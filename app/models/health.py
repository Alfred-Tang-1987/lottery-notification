from datetime import datetime

from sqlmodel import Field

from app.models._base import TimestampMixin


class ApiSourceHealth(TimestampMixin, table=True):
    __tablename__ = 'api_source_health'
    source: str = Field(primary_key=True, max_length=16)
    last_success_at: datetime | None = None
    status: str = Field(default='unknown', max_length=16)  # ok | degraded | down | unknown
    error: str | None = None
