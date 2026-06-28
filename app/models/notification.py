from datetime import datetime

from sqlmodel import Field

from app.models._base import TimestampMixin


class NotificationChannel(TimestampMixin, table=True):
    __tablename__ = 'notification_channels'
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key='users.id', index=True)
    type: str = Field(max_length=8)  # bark | feishu | email
    config_json: str  # 加密存储（webhook/key/收件地址）
    enabled: bool = Field(default=True)
    key_version: int = Field(default=1)


class NotificationRule(TimestampMixin, table=True):
    __tablename__ = 'notification_rules'
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key='users.id', index=True)
    lottery_code: str = Field(foreign_key='lottery_types.code', index=True)
    strategy: str = Field(default='every', max_length=8)  # every | win_only
    timing: str | None = None


class NotificationLog(TimestampMixin, table=True):
    __tablename__ = 'notification_logs'
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key='users.id', index=True)
    comparison_id: int | None = Field(
        default=None, foreign_key='comparisons.id', index=True
    )  # 路径A大奖推送关联的 comparison，用于去重
    type: str = Field(max_length=16)
    payload: str
    status: str = Field(max_length=16)  # sent | failed | pending
    sent_at: datetime | None = None
    error: str | None = None
