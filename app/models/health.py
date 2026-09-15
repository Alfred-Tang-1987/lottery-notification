from datetime import datetime

from sqlmodel import Field

from app.models._base import TimestampMixin


class ApiSourceHealth(TimestampMixin, table=True):
    __tablename__ = 'api_source_health'
    source: str = Field(primary_key=True, max_length=16)
    last_success_at: datetime | None = None
    status: str = Field(default='unknown', max_length=16)  # ok | degraded | down | unknown
    error: str | None = None
    # plan-11（2026-09-15 DNS 事故）：告警状态机支撑列。时间均 naive UTC（CLAUDE.md 纪律）。
    # down_since：本次故障起点；恢复通知送达后清除（保留期间供计算故障时长）。
    down_since: datetime | None = None
    # alerted：none（未告警）→ alerted（故障告警已送达）→ recovering（恢复通知待送达）。
    alerted: str = Field(default='none', max_length=16)
