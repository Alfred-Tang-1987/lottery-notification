from datetime import datetime

from sqlalchemy import UniqueConstraint
from sqlmodel import Field

from app.models._base import TimestampMixin


class DrawResult(TimestampMixin, table=True):
    __tablename__ = 'draw_results'
    id: int | None = Field(default=None, primary_key=True)
    lottery_code: str = Field(index=True, max_length=8)
    draw_no: str = Field(index=True, max_length=16)
    draw_date: datetime
    numbers_json: str
    source: str = Field(max_length=16)  # mxnzp | juhe
    fetched_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column_kwargs={'server_default': 'CURRENT_TIMESTAMP'},
    )
    verified: bool = Field(default=False, sa_column_kwargs={'server_default': '0'})
    single_source: bool = Field(
        default=False,
        sa_column_kwargs={'server_default': '0'},
    )
    version: int = Field(
        default=1,
        sa_column_kwargs={'server_default': '1'},
    )  # 官方更正递增

    __table_args__ = (UniqueConstraint('lottery_code', 'draw_no', name='uq_draw_lottery_no'),)


class DrawCorrection(TimestampMixin, table=True):
    __tablename__ = 'draw_corrections'
    id: int | None = Field(default=None, primary_key=True)
    draw_result_id: int = Field(foreign_key='draw_results.id', index=True)
    old_numbers_json: str
    new_numbers_json: str
    corrected_at: datetime | None = Field(default=None, nullable=True)
    reason: str | None = None


class PendingComparison(TimestampMixin, table=True):
    """比对触发 outbox。processed_at 为空=待处理。"""

    __tablename__ = 'pending_comparisons'
    id: int | None = Field(default=None, primary_key=True)
    draw_result_id: int = Field(foreign_key='draw_results.id', index=True)
    processed_at: datetime | None = Field(default=None, index=True)
