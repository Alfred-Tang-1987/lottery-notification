"""t11: api_source_health +down_since/alerted（plan-11 数据源健康告警）。

Revision ID: t11_source_health_alert
Revises: d1_draw_costs
Create Date: 2026-09-15 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 't11_source_health_alert'
down_revision: str | Sequence[str] | None = 'd1_draw_costs'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema: api_source_health 增告警状态机两列。"""
    with op.batch_alter_table('api_source_health', schema=None) as batch_op:
        batch_op.add_column(sa.Column('down_since', sa.DateTime(), nullable=True))
        batch_op.add_column(
            sa.Column('alerted', sa.String(length=16), nullable=False, server_default='none')
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('api_source_health', schema=None) as batch_op:
        batch_op.drop_column('alerted')
        batch_op.drop_column('down_since')
