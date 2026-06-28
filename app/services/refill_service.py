import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from app.models import Comparison, DrawResult

logger = logging.getLogger(__name__)


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
        amount_lookup: Callable[[str, str, int], int | None],
        max_age_days: int = 7,
    ):
        self._engine = engine
        self._lookup = amount_lookup  # amount_lookup(lottery_code, draw_no, tier) -> 分 | None
        self._max_age = max_age_days

    def refill(self) -> int:
        cutoff = _cutoff_naive_utc(self._max_age)
        refilled = 0
        with Session(self._engine) as s:
            # 显式限定 prize_tier IN (1,2) —— 仅浮动档（spec §7.1 明文「一二等奖」）
            pending = list(
                s.exec(
                    select(Comparison).where(
                        Comparison.is_win == True,  # noqa: E712
                        Comparison.prize_tier.in_((1, 2)),
                        Comparison.prize_amount.is_(None),
                        Comparison.unresolved == False,  # noqa: E712
                        Comparison.created_at >= cutoff,
                    )
                ).all()
            )
            # 预载 draw_result 映射（拿 lottery_code/draw_no 查官方奖金）
            dr_ids = {c.draw_result_id for c in pending}
            drs = {dr.id: dr for dr in s.exec(select(DrawResult).where(DrawResult.id.in_(dr_ids))).all()}
        # 每行回填独立：单行 lookup 抛异常（源 5xx/超时/解析错）只隔离该行，不阻断后续行
        # （silent-failure C1：旧版无 try/except，一行 raise 中断整批 → 后续行静默丢失）。
        for cmp in pending:
            dr = drs.get(cmp.draw_result_id)
            if dr is None or cmp.prize_tier is None:
                continue
            try:
                amount = self._lookup(dr.lottery_code, dr.draw_no, cmp.prize_tier)
            except Exception:
                # 源故障隔离到该行：记日志（含 traceback），不阻断其他行回填
                logger.warning(
                    'refill_skip_lookup_failed comparison_id=%s lottery=%s draw_no=%s tier=%s',
                    cmp.id,
                    dr.lottery_code,
                    dr.draw_no,
                    cmp.prize_tier,
                    exc_info=True,
                )
                continue
            if amount is not None:
                with Session(self._engine) as s:
                    c = s.get(Comparison, cmp.id)
                    c.prize_amount = amount
                    s.commit()
                refilled += 1
                # 补推：回填后金额变更，由 Plan 04 Notifier 监听 prize_amount 变更事件推送
                # （本 plan 仅回填数据；推送在 Plan 04 接线，避免循环依赖与跨 plan 耦合）

        # 超期未回填的标 unresolved（spec §7.1 line 276「超期标 unresolved 不再查」）。
        # 必须无条件执行（即便上方回填循环某行 raise 被隔离）——否则超期行永不标记、
        # 每轮重查、永不 resolve，T5 的 terminal-state 契约被破坏（silent-failure C1）。
        self._mark_expired_unresolved(cutoff)

        return refilled

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
