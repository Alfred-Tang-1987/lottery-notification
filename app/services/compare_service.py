"""CompareService：比对引擎（spec §7.1 核心数据流）。

outbox 原子认领（pending_comparisons）→ 取追投 tickets → 领域 compare() →
写 comparisons（唯一约束 draw_result_id+ticket_id 兜底，更正重比原地更新）+
中奖写 prize_claims(pending)。比对只做一次（spec §4 line99：路径 A/B 复用 comparisons）。
"""
import json
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from app.domain import compare as domain_compare
from app.domain.entry import Entry
from app.domain.spec import LotterySpec
from app.models import (
    Comparison, DrawResult, PendingComparison, PrizeClaim, Ticket,
)
from app.seeds import SPECS

logger = logging.getLogger(__name__)

# 全程 Asia/Shanghai（spec §4.3）。aware datetime，与 FetchService 统一。
_CST = ZoneInfo("Asia/Shanghai")


def _now() -> datetime:
    return datetime.now(_CST)


# LotterySpec hydrate 缓存（SPECS 静态、LotterySpec 不可变语义 → 进程级安全复用）。
_SPEC_CACHE: dict[str, LotterySpec] = {}


def _spec_for(code: str) -> LotterySpec:
    if code not in _SPEC_CACHE:
        spec_dict = next(s for s in SPECS if s["code"] == code)
        _SPEC_CACHE[code] = LotterySpec.from_dict(spec_dict)
    return _SPEC_CACHE[code]


class CompareService:
    """outbox 原子认领 → domain.compare → 写 comparisons + prize_claims（spec §7.1）。"""

    def __init__(self, engine: Engine):
        self._engine = engine

    def process_pending(self) -> int:
        """处理所有未认领 pending_comparisons。返回处理条数。

        先快照待处理列表（短事务读），再逐条原子认领+比对。认领用
        UPDATE...WHERE processed_at IS NULL RETURNING 防并发重复认领（单写连接下
        亦保证幂等：已认领的行 processed_at 非空，WHERE 命中 0 行）。

        per-draw 隔离（spec §10 + silent-failure 防护）：单期比对抛异常不得中断
        后续期——否则一期坏注/配置错会让整批 process_pending 崩溃，后续期永不比对
        （claim 已提交 processed_at → 永久丢失）。此处 catch+log+continue，把错误
        显式留痕（不静默），processed_at 保持已认领（不无限重试配置错）。
        """
        with Session(self._engine) as s:
            pending = list(s.exec(
                select(PendingComparison).where(PendingComparison.processed_at.is_(None))
            ).all())
        processed = 0
        for pc in pending:
            if not self._claim(pc.id):
                continue
            processed += 1
            try:
                self._compare_one(pc.draw_result_id)
            except Exception as exc:
                # 期级失败（如 spec 缺失/开奖号损坏）：不阻断后续期，但必须留痕——
                # claim 已提交，此期不再自动重试（避免配置错无限重试），靠日志告警人工介入。
                logger.error(
                    "compare_failed draw_result_id=%s pending_id=%s error=%s",
                    pc.draw_result_id, pc.id, exc,
                )
        return processed

    def _claim(self, pending_id: int) -> bool:
        """原子认领：UPDATE ... WHERE processed_at IS NULL RETURNING。
        影响 0 行 = 已被认领（并发或重复调用），返回 False。"""
        with self._engine.begin() as conn:
            row = conn.execute(text(
                "UPDATE pending_comparisons SET processed_at = :now "
                "WHERE id = :id AND processed_at IS NULL RETURNING id"
            ), {"now": _now(), "id": pending_id}).first()
            return row is not None

    def _compare_one(self, draw_result_id: int) -> None:
        with Session(self._engine) as s:
            dr = s.get(DrawResult, draw_result_id)
            # 未开奖 / 未 verified 不比对（spec §7.1：仅 verified 入库触发比对）
            if dr is None or not dr.verified:
                return
            dn = json.loads(dr.numbers_json)
            draw_front = tuple(dn["front"])
            draw_back = tuple(dn["back"]) if dn.get("back") else None
            spec = _spec_for(dr.lottery_code)

            # 仅追投该彩种的启用注（spec §4 line99：比对范围由号码池决定，没追的不比对）
            tickets = list(s.exec(select(Ticket).where(
                Ticket.lottery_code == dr.lottery_code, Ticket.enabled == True  # noqa: E712
            )).all())

            for t in tickets:
                # per-ticket 隔离（spec §10 line375：坏注单/格式异常 → 隔离该注，不影响
                # 其他注的比对；记录错误日志）。坏注的失败发生在纯 Python 阶段
                # （json.loads 损坏 JSON / fushi·dantuo Phase2 expand NotImplementedError /
                # 号码结构异常）——这些在到达 DB 写入前抛出，session 不受污染，好注照常
                # 在循环结束统一 commit。旧版无此隔离：一注坏 → 整个 _compare_one unwind
                # → 好注（未 commit 的 session）回滚丢失 + claim 已提交 processed_at
                # → 该期永久无比对、中奖静默漏通知。
                try:
                    tn = json.loads(t.numbers_json)
                    entry = Entry(
                        lottery_code=t.lottery_code, play_type=t.play_type,
                        front=tuple(tn["front"]),
                        back=tuple(tn["back"]) if tn.get("back") else None,
                        multiplier=t.multiplier, append=t.append,
                    )
                    results = domain_compare(
                        spec, draw_front=draw_front, draw_back=draw_back, entry=entry,
                    )
                    # single/zhixuan 玩法展开为 1 注；复式/胆拖 Phase 2 扩展后逐注比对写行
                    for hit in results:
                        self._upsert_comparison(
                            s, user_id=t.user_id, draw_result_id=dr.id,
                            ticket_id=t.id, hit=hit, multiplier=t.multiplier,
                        )
                except Exception as exc:
                    # 隔离该注：跳过 + 记录错误日志，继续比对同期的其他注（§10）。
                    logger.warning(
                        "compare_skip_bad_ticket ticket_id=%s user_id=%s "
                        "lottery=%s draw_result_id=%s error=%s",
                        t.id, t.user_id, t.lottery_code, dr.id, exc,
                    )
                    continue
            s.commit()

    def _upsert_comparison(
        self, session: Session, *, user_id, draw_result_id, ticket_id, hit,
        multiplier: int = 1,
    ) -> None:
        """唯一约束 (draw_result_id, ticket_id)：存在则原地更新（更正重比），否则新建。

        同步 prize_claims（spec §7.1）：win→lose 删 claim（避免孤儿/虚假待兑奖）；
        lose→win 建 claim(pending)；win→win 不变（金额可能在浮奖回填后变，T5 管）。

        奖金金额（lottery-rules §倍投）：
          - 固定档 amount 不为 None → amount × multiplier（倍投放大中奖金额）。
          - 浮动档（一二等奖）amount=None → 保持 None，倍投 + 追加在 T5 回填时应用
            （金额未知，不在此乘，避免 None*multiplier 误算）。
        """
        hits_json = json.dumps({"front_hit": hit.front_hit, "back_hit": hit.back_hit})
        amount = hit.amount * multiplier if hit.amount is not None else None
        existing = session.exec(select(Comparison).where(
            Comparison.draw_result_id == draw_result_id,
            Comparison.ticket_id == ticket_id,
        )).first()
        if existing:
            was_win = existing.is_win
            existing.hits_json = hits_json
            existing.prize_tier = hit.tier
            existing.prize_amount = amount
            existing.is_win = hit.is_win
            existing.corrected_at = _now()
            _sync_claim(session, existing, is_win_now=hit.is_win, was_win=was_win)
        else:
            cmp = Comparison(
                user_id=user_id, draw_result_id=draw_result_id, ticket_id=ticket_id,
                hits_json=hits_json, prize_tier=hit.tier, prize_amount=amount,
                is_win=hit.is_win,
            )
            session.add(cmp)
            session.flush()  # 拿 cmp.id 给 PrizeClaim FK
            if hit.is_win:
                _create_claim(session, cmp.id)


def _create_claim(session: Session, comparison_id: int) -> None:
    """中奖 → 写 prize_claim(pending)，兑奖截止 60 天（以官方为准，可配置）。"""
    session.add(PrizeClaim(
        comparison_id=comparison_id, status="pending",
        deadline=_now() + timedelta(days=60),
    ))


def _sync_claim(
    session: Session, comparison: Comparison, *, is_win_now: bool, was_win: bool,
) -> None:
    """更正重比后 prize_claims 同步：win→lose 删 claim；lose→win 建 claim；win→win 不变。"""
    if was_win and not is_win_now:
        for c in session.exec(select(PrizeClaim).where(
            PrizeClaim.comparison_id == comparison.id
        )).all():
            session.delete(c)
    elif not was_win and is_win_now:
        _create_claim(session, comparison.id)
