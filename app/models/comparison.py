from datetime import datetime
from sqlmodel import Field
from sqlalchemy import UniqueConstraint
from app.models._base import TimestampMixin


class Comparison(TimestampMixin, table=True):
    __tablename__ = "comparisons"
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    draw_result_id: int = Field(foreign_key="draw_results.id", index=True)
    ticket_id: int = Field(foreign_key="tickets.id", index=True)
    hits_json: str
    prize_tier: int | None = None
    prize_amount: int | None = None  # 分；null=浮动奖待派奖
    is_win: bool = Field(default=False)
    corrected_at: datetime | None = None

    __table_args__ = (
        UniqueConstraint("draw_result_id", "ticket_id", name="uq_cmp_draw_ticket"),
    )


class PrizeClaim(TimestampMixin, table=True):
    __tablename__ = "prize_claims"
    id: int | None = Field(default=None, primary_key=True)
    comparison_id: int = Field(foreign_key="comparisons.id", index=True)
    status: str = Field(default="pending", max_length=16)  # pending|claimed|expired
    deadline: datetime
    claimed_at: datetime | None = None
