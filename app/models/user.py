from sqlmodel import Field, Relationship

from app.models._base import TimestampMixin


class User(TimestampMixin, table=True):
    __tablename__ = 'users'
    id: int | None = Field(default=None, primary_key=True)
    username: str = Field(index=True, unique=True, max_length=64)
    password_hash: str
    role: str = Field(default='user', max_length=16)  # user | admin
    invite_code: str = Field(max_length=16, index=True)  # 注册时用的码
    enabled: bool = Field(default=True)
    dnd_json: str | None = Field(default=None)  # {"enabled":bool,"start":"HH:MM","end":"HH:MM"}
    preferences_json: str | None = Field(default=None)  # {"theme":"auto"} 主题偏好；new_numbers_default_enabled 在 NotificationSettings
    notification_settings: 'NotificationSettings' = Relationship(
        back_populates='user',
        sa_relationship_kwargs={'uselist': False},
    )
