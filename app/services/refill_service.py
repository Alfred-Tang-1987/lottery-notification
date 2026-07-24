"""浮奖回填：一二等奖 prize_amount=null 的，轮询官方金额回填，max_age_days 上限。

金额公式（OV1/4A，Plan 07 T4）：
  base = amount_lookup(code, draw_no, draw_date, tier)  # 基础奖金（分）
  if ticket.append and tier_info and tier_info.append_multiplier:  # 追加 guard（4A）
      base = int(base * tier_info.append_multiplier)              # 追加 1.8x
  base *= ticket.multiplier                                        # 倍投（OV1）
  prize_amount = base
"""
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from app.adapters.base import PermanentLookupError
from app.domain.prize_tables import get_tiers
from app.models import Comparison, DrawResult, Ticket

logger = logging.getLogger(__name__)

# 同彩种（同源 host）连续 lookup 间隔——OV2 per-host 限流，防被官方接口 ban
_LOOKUP_INTERVAL_SECONDS = 0.5


def _cutoff_naive_utc(days: int) -> datetime:
    """回填窗口下限——**naive UTC**，刻意与 Comparison.created_at 同时区比较。

    ⚠️ created_at 存储形态是 naive UTC（TimestampMixin default_factory=datetime.utcnow）。
    若 cutoff 用 aware CST，SQLite 对 datetime 做**字符串比较**（非 tz-aware），aware-CST 串
    （如 '2026-06-17 23:15+08:00'）会排在 naive-UTC 串（'2026-06-17 15:15'）之后 →
    created_at < cutoff 误成立 → 恰好窗口边界（~8h，CST=UTC+8）的行被误判超期、标
    unresolved → 永久排除回填 → 浮动奖金额永久 null（spec §7.1 核心特性静默失效，
    quality re-review 实测复现）。故 cutoff 必须 naive UTC 与 created_at 对齐。

    用 datetime.now(UTC).replace(tzinfo=None) 而非弃用的 datetime.utcnow()。
    系统性根治（让 created_at 也 aware CST）需迁移规整旧行，超出 T5 范围，留后续。
    """
    return datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days)


class FloatRefillWorker:
    """浮奖回填：一二等奖 prize_amount=null 的，轮询官方金额回填，max_age_days 上限。"""

    def __init__(
        self,
        engine: Engine,
        amount_lookup: Callable[[str, str, datetime, int], int | None],
        max_age_days: int = 7,
    ):
        self._engine = engine
        # amount_lookup(lottery_code, draw_no, draw_date, tier) -> 分 | None
        # ⚠️ Plan 07 T4 (1A)：签名扩展 draw_date，下游适配器据此调官方按期号+日期的奖金接口
        self._lookup = amount_lookup
        self._max_age = max_age_days

    def _log_skip(self, event: str, cmp: Comparison, dr: DrawResult) -> None:
        """单行隔离日志：统一 4 字段上下文（comparison_id/lottery/draw_no/tier）+ exc_info。

        silent-failure：transient/permanent 故障都只隔离该行记日志不阻断后续，且必须带上
        定位所需的 draw 上下文（lottery_code/draw_no/tier）——两处 except 分支共用本格式。
        """
        logger.warning(
            '%s comparison_id=%s lottery=%s draw_no=%s tier=%s',
            event,
            cmp.id,
            dr.lottery_code,
            dr.draw_no,
            cmp.prize_tier,
            exc_info=True,
        )

    def refill(self) -> int:
        cutoff = _cutoff_naive_utc(self._max_age)
        refilled = 0
        with Session(self._engine) as s:
            # 显式限定 prize_tier IN (1,2) —— 仅浮动档（spec §7.1 明文「一二等奖」）
            # OV4 (Plan 07 T4): join DrawResult 过滤 verified=True——只回填已交叉校验
            # 入库的开奖结果，verified=False（双源不一致拒入库）的 comparison 不回填，
            # 避免基于错误号码计算奖金（准确性优先于及时性，spec §7.2）。
            pending = list(
                s.exec(
                    select(Comparison)
                    .join(DrawResult, Comparison.draw_result_id == DrawResult.id)
                    .where(
                        Comparison.is_win == True,  # noqa: E712
                        Comparison.prize_tier.in_((1, 2)),
                        Comparison.prize_amount.is_(None),
                        Comparison.unresolved == False,  # noqa: E712
                        Comparison.created_at >= cutoff,
                        DrawResult.verified == True,  # noqa: E712  # OV4
                    )
                ).all()
            )
            # 预载 draw_result 映射（拿 lottery_code/draw_no/draw_date 查官方奖金）
            dr_ids = {c.draw_result_id for c in pending}
            drs = {dr.id: dr for dr in s.exec(select(DrawResult).where(DrawResult.id.in_(dr_ids))).all()}
            # 预载 ticket 映射（拿 append/multiplier 应用金额公式，OV1/4A）
            ticket_ids = {c.ticket_id for c in pending if c.ticket_id is not None}
            tickets = {
                t.id: t for t in s.exec(select(Ticket).where(Ticket.id.in_(ticket_ids))).all()
            }

        # 按 lottery_code 分组（OV2：per-host 限流，同组内 sleep）
        # 同彩种共用一个官方 host，连续请求需间隔；不同彩种 host 不同可并行无 sleep。
        grouped: dict[str, list[tuple[Comparison, DrawResult, Ticket | None]]] = {}
        for cmp in pending:
            dr = drs.get(cmp.draw_result_id)
            if dr is None or cmp.prize_tier is None:
                continue
            ticket = tickets.get(cmp.ticket_id) if cmp.ticket_id is not None else None
            grouped.setdefault(dr.lottery_code, []).append((cmp, dr, ticket))

        # 每行回填独立：单行 lookup 抛异常（源 5xx/超时/解析错）只隔离该行，不阻断后续行
        # （silent-failure C1：旧版无 try/except，一行 raise 中断整批 → 后续行静默丢失）。
        # 批末 expired 兜底标记移入 finally：即便下方任一分支（成功 line ~108 / permanent
        # line ~82）的 s.commit() 抛 DB 异常（database is locked / disk full / constraint），
        # 超期行仍被标 unresolved——否则异常冒泡出 refill()，_mark_expired_unresolved 不可达，
        # 超期行永不标记、每轮重查、terminal-state 契约破坏（review round 3 important）。
        try:
            for lottery_code, rows in grouped.items():
                for i, (cmp, dr, ticket) in enumerate(rows):
                    try:
                        amount = self._lookup(
                            dr.lottery_code, dr.draw_no, dr.draw_date, cmp.prize_tier
                        )
                    except PermanentLookupError:
                        # 永久形状错误（如 typemoney 非数字）：立即标 unresolved，不再下轮重试
                        # （spec §7.1 line 276「超期标 unresolved 不再查」精神延伸到「永久错误」）。
                        # 旧实现无此分支 → PermanentLookupError 被下方通用 except 当 transient 隔离 →
                        # 下轮 pending 过滤仍命中 → 每轮重查 7 天，期间日志噪声大且定位困难，最终才由
                        # _mark_expired_unresolved 兜底标记——永久 schema bug 静默耗满 7 天窗口
                        # （review round 2 critical）。
                        # 单行 savepoint 隔离 + 单事务 commit：状态变更与日志（如未来加审计）同事务，
                        # 不 split-commit（L-20260706T010400Z）。
                        with Session(self._engine) as s:
                            c = s.get(Comparison, cmp.id)
                            c.unresolved = True
                            s.commit()
                        self._log_skip('refill_marked_unresolved_permanent_error', cmp, dr)
                        # 同组限流：本行已访问外部源（虽抛 permanent），下一行前需间隔
                        if i < len(rows) - 1:
                            time.sleep(_LOOKUP_INTERVAL_SECONDS)
                        continue
                    except Exception:
                        # transient 源故障（5xx/超时）：隔离到该行记日志，不阻断其他行回填，
                        # 不标 unresolved（区别于 PermanentLookupError）——下轮重试。
                        self._log_skip('refill_skip_lookup_failed', cmp, dr)
                        # 同组限流：transient 也已访问外部源，下一行前需间隔
                        if i < len(rows) - 1:
                            time.sleep(_LOOKUP_INTERVAL_SECONDS)
                        continue
                    if amount is not None:
                        # 金额公式（Plan 07 T4 OV1/4A）：
                        #   base → append_multiplier（仅大乐透一二等奖 + 追加投注）→ multiplier（倍投）
                        # guard: tier_info is None（未知彩种/未知 tier，如 tier=99）时跳过 append 乘法
                        # 仅乘 multiplier，避免 AttributeError（4A）。PrizeTier.append_multiplier
                        # 默认 1.0（truthy），故 ssq 等非追加彩种即使 ticket.append=True 也会乘 1.0
                        # （no-op）——这是数据一致性问题而非回填逻辑问题，由 Ticket.append 入库校验保证。
                        tier_info = self._find_tier(dr.lottery_code, cmp.prize_tier)
                        if ticket is not None and ticket.append and tier_info and tier_info.append_multiplier:
                            amount = int(amount * tier_info.append_multiplier)  # 追加 1.8x
                        if ticket is not None:
                            amount *= ticket.multiplier  # 倍投（OV1）
                        with Session(self._engine) as s:
                            c = s.get(Comparison, cmp.id)
                            c.prize_amount = amount
                            s.commit()
                        refilled += 1
                        # 补推：回填后金额变更，由 Plan 04 Notifier 监听 prize_amount 变更事件推送
                        # （本 plan 仅回填数据；推送在 Plan 04 接线，避免循环依赖与跨 plan 耦合）
                    # 同组内限流（OV3）：本行 lookup 完成后，若非本组最后一行，sleep 避免被 ban。
                    # 跨彩种（不同组）之间不 sleep——不同 host 无共享限流。
                    if i < len(rows) - 1:
                        time.sleep(_LOOKUP_INTERVAL_SECONDS)
        finally:
            # 超期未回填的标 unresolved（spec §7.1 line 276「超期标 unresolved 不再查」）。
            # 必须无条件执行（即便上方回填循环任一行 raise 或 commit 抛 DB 异常冒泡）——否则
            # 超期行永不标记、每轮重查、永不 resolve，T5 的 terminal-state 契约被破坏
            # （silent-failure C1 + review round 3 important：旧实现在循环外顺序代码路径，
            # commit 异常即跳过）。finally 自身再 try/except 兜底：marker 故障只记日志，
            # 不吞掉 try 体内正在传播的原异常（调用方须知晓回填/回填-commit 失败）。
            try:
                self._mark_expired_unresolved(cutoff)
            except Exception:
                logger.warning('refill_expired_marker_failed', exc_info=True)

        return refilled

    @staticmethod
    def _find_tier(lottery_code: str, tier: int):
        """从 prize_tables 查找指定奖级信息；未知彩种/tier 返回 None（guard 用，4A）。"""
        try:
            for t in get_tiers(lottery_code):
                if t.tier == tier:
                    return t
        except KeyError:
            # 未知彩种 code（不在 PRIZE_TABLES）——返回 None 让调用方 guard 跳过 append 乘法
            pass
        return None

    def _mark_expired_unresolved(self, cutoff: datetime) -> None:
        """超期未回填的浮动奖标 unresolved=True（spec §7.1 line 276）。独立事务，refill 主循环
        异常不影响本标记（独立方法 + 独立 session，process_pending 调用方也可单独兜底）。"""
        with Session(self._engine) as s:
            expired = list(
                s.exec(
                    select(Comparison).where(
                        Comparison.is_win == True,  # noqa: E712
                        Comparison.prize_tier.in_((1, 2)),
                        Comparison.prize_amount.is_(None),
                        Comparison.unresolved == False,  # noqa: E712
                        Comparison.created_at < cutoff,
                    )
                ).all()
            )
            for cmp in expired:
                cmp.unresolved = True
            if expired:
                s.commit()
