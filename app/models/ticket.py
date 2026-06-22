from sqlmodel import Field
from app.models._base import TimestampMixin


class Ticket(TimestampMixin, table=True):
    __tablename__ = "tickets"
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    lottery_code: str = Field(foreign_key="lottery_types.code", index=True, max_length=8)
    play_type: str = Field(max_length=16)  # single/fushi/dantuo/danxuan/zhixuan/...
    numbers_json: str  # 原始选择
    tuo_json: str | None = None  # 胆拖拖码
    label: str | None = Field(default=None, max_length=32)
    multiplier: int = Field(
        default=1, ge=1, le=99, sa_column_kwargs={"server_default": "1"},
    )
    append: bool = Field(
        default=False, sa_column_kwargs={"server_default": "0"},
    )  # 仅大乐透
    cost: int = Field(default=0, ge=0, sa_column_kwargs={"server_default": "0"})  # 分
    enabled: bool = Field(default=True, sa_column_kwargs={"server_default": "1"})
