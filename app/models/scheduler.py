"""APScheduler SQLAlchemyJobStore 期望的 apscheduler_jobs 表结构。

显式定义以便 Alembic 迁移纳入（不让 jobstore 运行时 auto-create，避免 schema drift）。
字段名/列严格对齐 APScheduler 3.x jobstore：仅 (id, next_run_time, job_state)。

⚠️ 不得继承 TimestampMixin：APScheduler 的 SQLAlchemyJobStore.insert 只写
(id, next_run_time, job_state)，不写 created_at——若列存在且 NOT NULL，插入即
IntegrityError → 调度器无法持久化任何 job → sched.start() 报错 → 抓取/比对/推送
任务永不触发 → 中奖静默漏通知（spec §4.3 表结构须对齐 jobstore）。
"""

from datetime import datetime

from sqlmodel import Field, SQLModel


class ApschedulerJob(SQLModel, table=True):
    __tablename__ = 'apscheduler_jobs'
    id: str = Field(primary_key=True, max_length=191)
    next_run_time: datetime | None = Field(default=None, index=True)
    job_state: bytes  # pickled blob（LargeBinary，与 APScheduler jobstore 一致）
