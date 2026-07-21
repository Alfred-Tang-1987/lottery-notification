from typing import TYPE_CHECKING

from sqlmodel import Field, Relationship

from app.models._base import TimestampMixin

if TYPE_CHECKING:
    # 循环引用：NotificationSettings 定义在 notification.py，运行时由 SQLModel
    # 解析字符串注解，此处仅供类型检查器/ruff 识别（避免 F821 误报），不在运行时导入。
    from app.models.notification import NotificationSettings


class User(TimestampMixin, table=True):
    __tablename__ = 'users'
    id: int | None = Field(default=None, primary_key=True)
    username: str = Field(index=True, unique=True, max_length=64)
    password_hash: str
    role: str = Field(default='user', max_length=16)  # user | admin
    invite_code: str = Field(max_length=16, index=True)  # 注册时用的码
    enabled: bool = Field(default=True)
    note: str = Field(default='', max_length=255, sa_column_kwargs={'server_default': ''})  # 管理员备注（spec §12.2 row 9 备注列）
    dnd_json: str | None = Field(default=None)  # {"enabled":bool,"start":"HH:MM","end":"HH:MM"}
    preferences_json: str | None = Field(default=None)  # {"theme":"auto"} 主题偏好；new_numbers_default_enabled 在 NotificationSettings
    notification_settings: 'NotificationSettings' = Relationship(
        back_populates='user',
        sa_relationship_kwargs={'uselist': False},
    )
