from datetime import datetime
from typing import TYPE_CHECKING

from sqlmodel import Field, Relationship

from app.models._base import TimestampMixin

if TYPE_CHECKING:
    # 循环引用：User 定义在 user.py，运行时由 SQLModel 解析字符串注解，
    # 此处仅供类型检查器/ruff 识别（避免 F821 误报），不在运行时导入。
    from app.models.user import User


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


class NotificationSettings(TimestampMixin, table=True):
    """Per-user global notification settings.

    Spec §12.2 row 8: master_enable, path_a_enable, summary_time and
    new_numbers_default_enabled are global per-user items, not per-lottery.
    Storing them in a dedicated table removes denormalization drift from
    NotificationRule rows.
    """

    __tablename__ = 'notification_settings'
    user_id: int = Field(foreign_key='users.id', primary_key=True)
    master_enable: bool = Field(default=True)
    path_a_enable: bool = Field(default=True)
    summary_time: str | None = Field(default=None, max_length=5)  # "HH:MM"
    new_numbers_default_enabled: bool = Field(default=True)

    # Relationship is optional but clarifies the one-to-one nature.
    user: 'User' = Relationship(back_populates='notification_settings')


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
