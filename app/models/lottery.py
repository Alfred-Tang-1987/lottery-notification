from sqlmodel import Field

from app.models._base import TimestampMixin


class LotteryType(TimestampMixin, table=True):
    __tablename__ = 'lottery_types'
    code: str = Field(primary_key=True, max_length=8)  # ssq/dlt/...
    name: str = Field(max_length=16)
    category: str = Field(max_length=8)  # welfare | sport
    spec_json: str  # LotterySpec 序列化（Plan 02 hydration）
    draw_schedule_json: str  # 开奖日 + 调度配置
    enabled: bool = Field(default=True)
    schema_version: int = Field(default=1)  # spec_json 演进
