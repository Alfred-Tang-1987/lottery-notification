"""plan-08: password_reset_codes 表（忘记密码验证码）.

Revision ID: p8_password_reset_codes
Revises: t6f_user_note
Create Date: 2026-08-02 21:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'p8_password_reset_codes'
down_revision: str | Sequence[str] | None = 't6f_user_note'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'password_reset_codes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('code_hash', sa.String(length=64), nullable=False),
        sa.Column('channel_type', sa.String(length=8), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('attempts', sa.Integer(), server_default='0', nullable=False),
        sa.Column('used_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_password_reset_codes_user_id', 'password_reset_codes', ['user_id']
    )


def downgrade() -> None:
    op.drop_index('ix_password_reset_codes_user_id', table_name='password_reset_codes')
    op.drop_table('password_reset_codes')
