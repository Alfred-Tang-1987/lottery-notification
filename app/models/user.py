from sqlmodel import Field

from app.models._base import TimestampMixin


class User(TimestampMixin, table=True):
    __tablename__ = 'users'
    id: int | None = Field(default=None, primary_key=True)
    username: str = Field(index=True, unique=True, max_length=64)
    password_hash: str
    role: str = Field(default='user', max_length=16)  # user | admin
    invite_code: str = Field(max_length=16, index=True)  # 注册时用的码
    enabled: bool = Field(default=True)
