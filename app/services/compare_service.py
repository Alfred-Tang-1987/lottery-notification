"""CompareService：比对引擎（spec §7.1 核心数据流）。

outbox 原子认领（pending_comparisons）→ 取追投 tickets → 领域 compare() →
写 comparisons（唯一约束 draw_result_id+ticket_id 兜底，更正重比原地更新）+
中奖写 prize_claims(pending)。比对只做一次（spec §4 line99：路径 A/B 复用 comparisons）。
"""

import json
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, text
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from app.domain import compare as domain_compare
from app.domain.entry import Entry
from app.domain.prize import AmountType
from app.domain.spec import LotterySpec
from app.models import (
    Comparison,
    DrawCost,
    DrawResult,
    PendingComparison,
    PrizeClaim,
    Ticket,
)
from app.seeds import SPECS

logger = logging.getLogger(__name__)

# 全程 Asia/Shanghai（spec §4.3）。aware datetime，与 FetchService 统一。
_CST = ZoneInfo('Asia/Shanghai')


def _now() -> datetime:
    return datetime.now(_CST)


# LotterySpec hydrate 缓存（SPECS 静态、LotterySpec 不可变语义 → 进程级安全复用）。
_SPEC_CACHE: dict[str, LotterySpec] = {}


def _spec_for(code: str) -> LotterySpec:
    if code not in _SPEC_CACHE:
        spec_dict = next(s for s in SPECS if s['code'] == code)
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
            pending = list(s.exec(select(PendingComparison).where(PendingComparison.processed_at.is_(None))).all())
        processed = 0
        for pc in pending:
            if not self._claim(pc.id):
                continue
            processed += 1
            try:
                self._compare_one(pc.draw_result_id)
            except Exception:
                # 期级失败（如 spec 缺失/开奖号损坏）：不阻断后续期，但必须留痕——
                # claim 已提交，此期不再自动重试（避免配置错无限重试），靠日志告警人工介入。
                # 期级失败多为编程/配置错，含 traceback 便于定位（I2）。
                logger.error(
                    'compare_failed draw_result_id=%s pending_id=%s',
                    pc.draw_result_id,
                    pc.id,
                    exc_info=True,
                )
        return processed

    def _claim(self, pending_id: int) -> bool:
        """原子认领：UPDATE ... WHERE processed_at IS NULL RETURNING。
        影响 0 行 = 已被认领（并发或重复调用），返回 False。"""
        with self._engine.begin() as conn:
            row = conn.execute(
                text(
                    'UPDATE pending_comparisons SET processed_at = :now '
                    'WHERE id = :id AND processed_at IS NULL RETURNING id'
                ),
                {'now': _now(), 'id': pending_id},
            ).first()
            return row is not None

    def _compare_one(self, draw_result_id: int) -> None:
        with Session(self._engine) as s:
            dr = s.get(DrawResult, draw_result_id)
            # 未开奖 / 未 verified 不比对（spec §7.1：仅 verified 入库触发比对）
            if dr is None or not dr.verified:
                return
            dn = json.loads(dr.numbers_json)
            draw_front = tuple(dn['front'])
            draw_back = tuple(dn['back']) if dn.get('back') else None
            spec = _spec_for(dr.lottery_code)

            # 仅追投该彩种的启用注（spec §4 line99：比对范围由号码池决定，没追的不比对）
            tickets = list(
                s.exec(
                    select(Ticket).where(
                        Ticket.lottery_code == dr.lottery_code,
                        Ticket.enabled == True,  # noqa: E712
                    )
                ).all()
            )

            for t in tickets:
                # per-ticket 隔离（spec §10 line375：坏注单/格式异常 → 隔离该注，不影响
                # 其他注的比对；记录错误日志）。
                #
                # ⚠️ 必须 savepoint（s.begin_nested()）：坏注失败可能发生在 DB 写入阶段
                # （_upsert_comparison 内的 session.add/flush/exec/delete——如 flush 时
                # IntegrityError「database is locked」/ NOT NULL 违反 / uq 竞态 / schema
                # 不匹配），不是仅纯 Python 阶段。无 savepoint 时 bare except 吞掉异常，
                # 但 session 已进入 PendingRollback 态：后续好注的 session.exec 撞
                # PendingRollbackError（也被吞，误记为坏注）→ 末尾 s.commit() 在毒化 session
                # 上变 rollback → 好注已 flush 的 comparison 全被抹 → claim 已提交
                # processed_at → 该期永久无比对、中奖静默漏通知（C1 实测复现）。
                # savepoint 让坏注失败只回滚该注，外层 session 保持干净，好注照常 commit。
                try:
                    with s.begin_nested():  # SAVEPOINT：失败只回滚该注
                        tn = json.loads(t.numbers_json)
                        entry = Entry(
                            lottery_code=t.lottery_code,
                            play_type=t.play_type,
                            front=tuple(tn['front']),
                            back=tuple(tn['back']) if tn.get('back') else None,
                            multiplier=t.multiplier,
                            append=t.append,
                        )
                        results = domain_compare(
                            spec,
                            draw_front=draw_front,
                            draw_back=draw_back,
                            entry=entry,
                            draw_date=dr.draw_date,  # 规则版本门：历史期更正重比按当时规则判定
                        )
                        # single/zhixuan 玩法展开为 1 注；复式/胆拖 Phase 2 扩展后逐注比对写行
                        for hit in results:
                            self._upsert_comparison(
                                s,
                                user_id=t.user_id,
                                draw_result_id=dr.id,
                                ticket_id=t.id,
                                hit=hit,
                                multiplier=t.multiplier,
                            )
                except Exception:
                    # 隔离该注：savepoint 已回滚该注的写，跳过 + 记录错误日志（含 traceback，
                    # 便于排查 DB 错根因——I2），继续比对同期的其他注（§10）。
                    logger.warning(
                        'compare_skip_bad_ticket ticket_id=%s user_id=%s lottery=%s draw_result_id=%s',
                        t.id,
                        t.user_id,
                        t.lottery_code,
                        dr.id,
                        exc_info=True,
                    )
                    continue

            # 期次成本记账（spec §4：成本按开奖日记账；只要有 enabled 追投注就记）。
            # 与 comparisons 同事务 commit（silent-failure：状态变更单事务一次 commit）。
            # per-user 聚合 cost（坏注被 savepoint 隔离跳过，但仍计入成本--成本由「是否
            # 追投该期」决定，与比对是否成功无关；号码格式异常是数据问题，投入的钱已花）。
            # 重比走 upsert（uq 兜底），不重复记账。
            _upsert_draw_costs(s, dr)
            s.commit()

    def _upsert_comparison(
        self,
        session: Session,
        *,
        user_id,
        draw_result_id,
        ticket_id,
        hit,
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
        hits_json = json.dumps({'front_hit': hit.front_hit, 'back_hit': hit.back_hit})
        amount = hit.amount * multiplier if hit.amount is not None else None
        existing = session.exec(
            select(Comparison).where(
                Comparison.draw_result_id == draw_result_id,
                Comparison.ticket_id == ticket_id,
            )
        ).first()
        if existing:
            was_win = existing.is_win
            existing.hits_json = hits_json
            existing.prize_tier = hit.tier
            existing.prize_amount = amount
            existing.is_win = hit.is_win
            existing.corrected_at = _now()
            # 官方更正重比命中同一注：若该行曾被标 unresolved（浮奖超期未回填），更正后
            # prize_amount 可能重置回 None（待派奖），须重置 unresolved=False 让它重回
            # FloatRefillWorker 回填管线——否则永久卡死（refill 排除 unresolved=True），
            # 中奖金额永远 null（spec §7.1 浮奖回填契约被破坏，quality review I2）。
            if existing.unresolved:
                existing.unresolved = False
            _sync_claim(session, existing, is_win_now=hit.is_win, was_win=was_win)
        else:
            cmp = Comparison(
                user_id=user_id,
                draw_result_id=draw_result_id,
                ticket_id=ticket_id,
                hits_json=hits_json,
                prize_tier=hit.tier,
                prize_amount=amount,
                is_win=hit.is_win,
            )
            session.add(cmp)
            session.flush()  # 拿 cmp.id 给 PrizeClaim FK
            if hit.is_win:
                _create_claim(session, cmp.id)


def _upsert_draw_costs(session: Session, draw_result: DrawResult) -> None:
    """期次成本记账（spec §4：成本按开奖日记账；只要有 enabled 追投注就记一行 per user）。

    成本聚合基准：所有 enabled 追投注（与比对范围一致，spec §4 line99），坏注（号码格式
    异常被 savepoint 隔离）仍计入--成本由「是否追投该期」决定，与比对是否成功无关，投入
    的钱已花。per-user group by 一次查全，逐 user upsert（uq (user_id,lottery_code,draw_no)
    兜底幂等：更正重比原地更新 cost，不重复记账）。

    draw_date 取 draw_result.draw_date（aware CST，与 DrawResult 同表示，dashboard 按本列归期）。
    无追投注时跳过（不记 0 成本期--该期对该用户无投入）。
    """
    rows = session.exec(
        select(Ticket.user_id, func.coalesce(func.sum(Ticket.cost), 0))
        .where(
            Ticket.lottery_code == draw_result.lottery_code,
            Ticket.enabled == True,  # noqa: E712
        )
        .group_by(Ticket.user_id)
    ).all()
    for user_id, cost_sum in rows:
        cost = int(cost_sum or 0)
        existing = session.exec(
            select(DrawCost).where(
                DrawCost.user_id == user_id,
                DrawCost.lottery_code == draw_result.lottery_code,
                DrawCost.draw_no == draw_result.draw_no,
            )
        ).first()
        if existing:
            existing.cost = cost
            existing.draw_date = draw_result.draw_date
        else:
            session.add(
                DrawCost(
                    user_id=user_id,
                    lottery_code=draw_result.lottery_code,
                    draw_no=draw_result.draw_no,
                    cost=cost,
                    draw_date=draw_result.draw_date,
                )
            )


def backfill_draw_costs(engine: Engine) -> int:
    """历史期次成本回填（spec §4）：遍历所有 DrawResult，per-user 聚合 enabled 追投注
    cost 写 DrawCost（upsert 幂等）。

    用于迁移后补历史数据：迁移前已有 DrawResult+comparisons 但无 DrawCost（成本口径
    切换前的历史断层）。CLI backfill-draw-costs 调用此函数一次性补齐。

    返回回填的 DrawCost 行数（含 upsert 更新的既有行）。无追投注的 DrawResult 跳过
    （无投入不记账）。
    """
    count = 0
    with Session(engine) as s:
        draws = list(s.exec(select(DrawResult)).all())
        for dr in draws:
            _upsert_draw_costs(s, dr)
            count += 1
        s.commit()
    return count


def _create_claim(session: Session, comparison_id: int) -> None:
    """中奖 → 写 prize_claim(pending)，兑奖截止 60 天（以官方为准，可配置）。"""
    session.add(
        PrizeClaim(
            comparison_id=comparison_id,
            status='pending',
            deadline=_now() + timedelta(days=60),
        )
    )


def _sync_claim(
    session: Session,
    comparison: Comparison,
    *,
    is_win_now: bool,
    was_win: bool,
) -> None:
    """更正重比后 prize_claims 同步：win→lose 删 claim；lose→win 建 claim；win→win 不变。"""
    if was_win and not is_win_now:
        for c in session.exec(select(PrizeClaim).where(PrizeClaim.comparison_id == comparison.id)).all():
            session.delete(c)
    elif not was_win and is_win_now:
        _create_claim(session, comparison.id)


# ────────── recompare_all（Plan 10 / T6：奖级表修正后按新表重算存量比对行）──────────


def recompare_all(engine: Engine, lottery_code: str | None = None, dry_run: bool = False) -> dict:
    """按现行领域规则（含 T1 版本门）重算全部存量比对行（Plan 10 / T6）。

    为每个 verified DrawResult 重入 CompareService._compare_one（幂等 upsert：
    uq_cmp_draw_ticket 原地更新 hits/tier/amount/is_win + corrected_at）。
    用途：奖级表修正后清理旧表写出的错误行（eng-review 外部声音发现 1）；
    未来任何规则修正的一键重算。per-draw 失败隔离（try/except + log，不中断整批）。
    dry_run=True：只统计会变更的行数，不写库。

    浮动档金额保护（eng-review 主复核发现 1，HIGH）：_upsert_comparison 对浮动档
    置 prize_amount=None，会抹掉已回填金额；FloatRefillWorker 又有 7 天 created_at
    窗口，超期老行不会再回填 → 静默永久丢失。故：① 重比前快照「旧规则下浮动档、
    金额非空」行，重比后对 tier 未变者写回；② 收尾对「浮动档、金额 None」行绕过
    窗口强制回填（max_age_days=None）。

    返回 {'draws': 重比期数, 'rows': 处理行数（约等于快照行数，下限 1/期）,
    'changed': 实际变更行数}。
    """
    svc = CompareService(engine)
    stats = {'draws': 0, 'rows': 0, 'changed': 0}
    with Session(engine) as s:
        q = select(DrawResult).where(DrawResult.verified == True)  # noqa: E712
        if lottery_code:
            q = q.where(DrawResult.lottery_code == lottery_code)
        dr_ids = [dr.id for dr in s.exec(q).all()]
    for dr_id in dr_ids:
        if dry_run:
            # 与实跑同一比对逻辑，仅比对内存结果统计差异（不 commit，只读会话）
            stats['draws'] += 1
            stats['changed'] += _count_changed(engine, dr_id)
        else:
            # 快照 + 重比都纳入 per-draw 隔离：坏 draw（如未知彩种，get_tiers/_spec_for
            # KeyError）只跳过该期，不中断整批（spec §10 期级隔离纪律）。
            try:
                before = _snapshot_rows(engine, dr_id)
                preserved = _snapshot_refilled_float_amounts(engine, dr_id)  # 发现 1 保护①
                svc._compare_one(dr_id)
            except Exception:
                logger.warning('recompare_skip_draw draw_result_id=%s', dr_id, exc_info=True)
                continue
            _restore_float_amounts(engine, dr_id, preserved)  # 发现 1 保护①
            stats['draws'] += 1
            stats['rows'] += max(len(before), 1)
            stats['changed'] += _diff_rows(before, _snapshot_rows(engine, dr_id))
    if not dry_run:
        _force_refill_float_rows(engine)  # 发现 1 保护②：绕过 7 天窗口强制回填
    return stats


def _snapshot_rows(engine: Engine, dr_id: int) -> set[tuple]:
    """抽取该期 comparisons 的 (ticket_id, tier, amount, is_win) 元组集（重比前后对比）。

    ticket_id 是 (draw_result_id, ticket_id) 唯一键的定位键——同一票重比前后同键，
    值变更即为该行变化。"""
    with Session(engine) as s:
        return {
            (c.ticket_id, c.prize_tier, c.prize_amount, c.is_win)
            for c in s.exec(select(Comparison).where(Comparison.draw_result_id == dr_id)).all()
        }


def _diff_rows(before: set[tuple], after: set[tuple]) -> int:
    """统计重比前后变化的行数。after 中不在 before 的元组 = 值变更或新增行
    （_compare_one 只 upsert 不删行，after ⊇ before 的 ticket 键）。"""
    return sum(1 for row in after if row not in before)


def _count_changed(engine: Engine, dr_id: int) -> int:
    """dry-run 专用：内存比对统计该期会有多少行变更（不写库，只读会话）。

    与 _compare_one 同一比对逻辑（spec + Entry + domain_compare + 倍投乘数），
    仅把当前 DB 行与内存预期结果对比计数；坏注 per-ticket 隔离（同实跑契约）。"""
    with Session(engine) as s:
        dr = s.get(DrawResult, dr_id)
        if dr is None or not dr.verified:
            return 0
        current = {
            c.ticket_id: (c.prize_tier, c.prize_amount, c.is_win)
            for c in s.exec(select(Comparison).where(Comparison.draw_result_id == dr_id)).all()
        }
        dn = json.loads(dr.numbers_json)
        draw_front = tuple(dn['front'])
        draw_back = tuple(dn['back']) if dn.get('back') else None
        try:
            spec = _spec_for(dr.lottery_code)
        except Exception:
            logger.warning('recompare_dry_run_skip_draw draw_result_id=%s', dr_id, exc_info=True)
            return 0
        tickets = list(
            s.exec(
                select(Ticket).where(
                    Ticket.lottery_code == dr.lottery_code,
                    Ticket.enabled == True,  # noqa: E712
                )
            ).all()
        )
    changed = 0
    for t in tickets:
        try:
            tn = json.loads(t.numbers_json)
            entry = Entry(
                lottery_code=t.lottery_code,
                play_type=t.play_type,
                front=tuple(tn['front']),
                back=tuple(tn['back']) if tn.get('back') else None,
                multiplier=t.multiplier,
                append=t.append,
            )
            for hit in domain_compare(
                spec,
                draw_front=draw_front,
                draw_back=draw_back,
                entry=entry,
                draw_date=dr.draw_date,  # 规则版本门：与实跑同源
            ):
                amount = hit.amount * t.multiplier if hit.amount is not None else None
                if current.get(t.id) != (hit.tier, amount, hit.is_win):
                    changed += 1
        except Exception:
            logger.warning('recompare_dry_run_skip_ticket ticket_id=%s', t.id, exc_info=True)
            continue
    return changed


def _snapshot_refilled_float_amounts(engine: Engine, dr_id: int) -> dict[int, tuple[int, int]]:
    """发现 1 保护①（快照）：重比前快照「旧规则下为浮动档、is_win、prize_amount 非空」
    的行 → {ticket_id: (tier, prize_amount)}。

    旧规则经 T1 版本门 get_tiers(code, dr.draw_date) 判定——历史期按当时生效的奖级表
    判断是否浮动档（dlt/ssq/qxc 一二等 + qlc 一二三等）。重比后写回防已回填金额被抹。"""
    from app.domain.prize_tables import get_tiers

    with Session(engine) as s:
        dr = s.get(DrawResult, dr_id)
        if dr is None:
            return {}
        float_tiers = {
            t.tier for t in get_tiers(dr.lottery_code, dr.draw_date)
            if t.amount_type == AmountType.FLOAT
        }
        rows = s.exec(
            select(Comparison).where(
                Comparison.draw_result_id == dr_id,
                Comparison.is_win == True,  # noqa: E712
                Comparison.prize_amount.is_not(None),
            )
        ).all()
        return {
            c.ticket_id: (c.prize_tier, c.prize_amount)
            for c in rows
            if c.prize_tier in float_tiers and c.prize_amount is not None
        }


def _restore_float_amounts(
    engine: Engine, dr_id: int, preserved: dict[int, tuple[int, int]]
) -> None:
    """发现 1 保护①（写回）：重比后对「tier 未变、仍 is_win、金额被重置为 None」的
    浮动档行写回快照金额——保住已回填的 dlt/ssq/qxc 一二等金额（7 天窗口外永丢失）。"""
    if not preserved:
        return
    with Session(engine) as s:
        for c in s.exec(select(Comparison).where(Comparison.draw_result_id == dr_id)).all():
            snap = preserved.get(c.ticket_id)
            if snap is not None and c.prize_tier == snap[0] and c.is_win and c.prize_amount is None:
                c.prize_amount = snap[1]
        s.commit()


def _force_refill_float_rows(engine: Engine) -> None:
    """发现 1 保护②：重比后「浮动档、is_win、金额 None、unresolved=False」的行
    （含 qlc 三等固定→浮动、历史期未回填浮动行）绕过 7 天 created_at 窗口强制回填
    （FloatRefillWorker max_age_days=None=不限窗口）。

    amount_lookup 复用 app.main._build_amount_lookup（构造 cwl/sporttery 适配器与
    app/main.py 相同），不另写新 lookup。防御性包装：回填失败只记日志，不阻断重比主流程。
    """
    try:
        from app.adapters.cwl_prize import CwlPrizeSource
        from app.adapters.sporttery_prize import SportteryPrizeSource
        from app.main import _build_amount_lookup
        from app.services.refill_service import FloatRefillWorker

        cwl = CwlPrizeSource()
        sporttery = SportteryPrizeSource()
        amount_lookup = _build_amount_lookup(cwl, sporttery)
        FloatRefillWorker(engine, amount_lookup=amount_lookup, max_age_days=None).refill()
    except Exception:
        logger.warning('recompare_force_refill_failed', exc_info=True)
