"""APScheduler SQLAlchemyJobStore 期望的 apscheduler_jobs 表结构。
显式定义以便 Alembic 首迁移纳入（不让 jobstore 运行时 auto-create，避免 schema drift）。
字段名严格对齐 APScheduler 3.x jobstore。"""
from datetime import datetime
from sqlmodel import Field
from app.models._base import TimestampMixin


class ApschedulerJob(TimestampMixin, table=True):
    __tablename__ = "apscheduler_jobs"
    id: str = Field(primary_key=True, max_length=191)
    next_run_time: datetime | None = Field(default=None, index=True)
    job_state: bytes  # pickled blob（SQLModel 映射 LargeBinary，与 APScheduler jobstore 一致，autogenerate 正确）
