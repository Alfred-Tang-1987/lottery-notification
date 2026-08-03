from datetime import datetime

from sqlalchemy import UniqueConstraint
from sqlmodel import Field

from app.models._base import TimestampMixin


class DrawCost(TimestampMixin, table=True):
    """期次成本（spec §4 成本按开奖日记账）：每用户每彩种每期一行，cost=该期所有
    enabled 追投注 cost 之和（分）。draw_date 取自 DrawResult.draw_date（aware CST，
    与 DrawResult 同表示），dashboard 按本列归期，使投入与中奖同属开奖日。

    唯一约束 (user_id, lottery_code, draw_no) 兜底：比对 outbox 认领幂等，更正重比
    原地更新（与 comparisons uq 同构），不重复记账。
    """

    __tablename__ = 'draw_costs'
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key='users.id', index=True)
    lottery_code: str = Field(foreign_key='lottery_types.code', index=True, max_length=8)
    draw_no: str = Field(index=True, max_length=16)
    cost: int = Field(default=0, ge=0, sa_column_kwargs={'server_default': '0'})  # 分
    draw_date: datetime

    __table_args__ = (
        UniqueConstraint('user_id', 'lottery_code', 'draw_no', name='uq_draw_cost_user_lottery_no'),
    )
