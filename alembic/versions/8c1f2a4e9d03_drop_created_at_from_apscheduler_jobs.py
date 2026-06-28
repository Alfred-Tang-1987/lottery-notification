"""drop created_at from apscheduler_jobs

对齐 APScheduler SQLAlchemyJobStore 期望的表结构（spec §4.3）：jobstore.insert 只写
(id, next_run_time, job_state)，不写 created_at。原 0001 迁移误给 apscheduler_jobs 加了
TimestampMixin 的 created_at NOT NULL 列 → 插入 job 即 IntegrityError → 调度器无法
持久化任何任务 → sched.start() 失败 → 抓取/比对/推送任务永不触发 → 中奖静默漏通知。

本迁移删除该列，使表结构严格匹配 jobstore。

Revision ID: 8c1f2a4e9d03
Revises: 6788bd78c7f2
Create Date: 2026-06-29 07:25:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '8c1f2a4e9d03'
down_revision: str | Sequence[str] | None = '6788bd78c7f2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column('apscheduler_jobs', 'created_at')


def downgrade() -> None:
    op.add_column(
        'apscheduler_jobs',
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
