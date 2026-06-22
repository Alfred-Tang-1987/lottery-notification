from app.models._base import TimestampMixin
from sqlmodel import Field


class AdminAuditLog(TimestampMixin, table=True):
    __tablename__ = "admin_audit_logs"
    id: int | None = Field(default=None, primary_key=True)
    admin_id: int = Field(foreign_key="users.id", index=True)
    action: str = Field(max_length=32)
    target_type: str = Field(max_length=32)
    target_id: str | None = None
    old_values: str | None = None  # JSON（敏感字段脱敏）
    new_values: str | None = None
