from datetime import datetime

from sqlmodel import Field

from app.models._base import TimestampMixin


class InviteCode(TimestampMixin, table=True):
    __tablename__ = 'invite_codes'
    code: str = Field(primary_key=True, max_length=6)
    created_by: int = Field(foreign_key='users.id')
    used_by: int | None = Field(default=None, foreign_key='users.id')
    used_at: datetime | None = None
    expires_at: datetime
    attempts: int = Field(default=0, sa_column_kwargs={'server_default': '0'})
    locked_at: datetime | None = None
