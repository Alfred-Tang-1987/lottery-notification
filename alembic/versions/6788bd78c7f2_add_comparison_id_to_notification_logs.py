"""add comparison_id to notification_logs

Revision ID: 6788bd78c7f2
Revises: b6a04a178e56
Create Date: 2026-06-29 02:06:47.236390

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '6788bd78c7f2'
down_revision: str | Sequence[str] | None = 'b6a04a178e56'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # SQLite 不支持 ALTER TABLE ADD FOREIGN KEY，使用 batch 模式重建表。
    with op.batch_alter_table('notification_logs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('comparison_id', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_notification_logs_comparison_id'), ['comparison_id'], unique=False)
        batch_op.create_foreign_key('fk_notification_logs_comparison_id', 'comparisons', ['comparison_id'], ['id'])


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('notification_logs', schema=None) as batch_op:
        batch_op.drop_constraint('fk_notification_logs_comparison_id', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_notification_logs_comparison_id'))
        batch_op.drop_column('comparison_id')
