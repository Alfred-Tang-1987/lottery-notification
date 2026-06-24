import logging
from datetime import datetime, timedelta
from typing import Callable
from zoneinfo import ZoneInfo

from sqlmodel import Session, select
from sqlalchemy.engine import Engine
from app.models import Comparison, DrawResult

logger = logging.getLogger(__name__)

_CST = ZoneInfo("Asia/Shanghai")  # spec：全程 Asia/Shanghai


def _now() -> datetime:
    """aware CST now，与 FetchService/CompareService 统一（替代弃用的 datetime.utcnow）。"""
    return datetime.now(_CST)


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
        cutoff = _now() - timedelta(days=self._max_age)
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
            drs = {
                dr.id: dr
                for dr in s.exec(
                    select(DrawResult).where(DrawResult.id.in_(dr_ids))
                ).all()
            }
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
                    "refill_skip_lookup_failed comparison_id=%s lottery=%s draw_no=%s tier=%s",
                    cmp.id, dr.lottery_code, dr.draw_no, cmp.prize_tier, exc_info=True,
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

