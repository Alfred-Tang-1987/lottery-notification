from datetime import datetime, timedelta
from typing import Callable
from sqlmodel import Session, select
from sqlalchemy.engine import Engine
from app.models import Comparison, DrawResult


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
        cutoff = datetime.utcnow() - timedelta(days=self._max_age)
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
        for cmp in pending:
            dr = drs.get(cmp.draw_result_id)
            if dr is None or cmp.prize_tier is None:
                continue
            amount = self._lookup(dr.lottery_code, dr.draw_no, cmp.prize_tier)
            if amount is not None:
                with Session(self._engine) as s:
                    c = s.get(Comparison, cmp.id)
                    c.prize_amount = amount
                    s.commit()
                refilled += 1
                # 补推：回填后金额变更，由 Plan 04 Notifier 监听 prize_amount 变更事件推送
                # （本 plan 仅回填数据；推送在 Plan 04 接线，避免循环依赖与跨 plan 耦合）

        # 超期未回填的标 unresolved（spec §7.1 line 276「超期标 unresolved 不再查」）
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

        return refilled
