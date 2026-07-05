"""t6f: add note column to users (spec §12.2 row 9 备注列).

Revision ID: t6f_user_note
Revises: t6e_notification_settings
Create Date: 2026-07-06 00:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 't6f_user_note'
down_revision: str | Sequence[str] | None = 't6e_notification_settings'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema: users +note (管理员备注，spec §12.2 row 9 备注列)。"""
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('note', sa.String(length=255), nullable=False, server_default='')
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('note')
