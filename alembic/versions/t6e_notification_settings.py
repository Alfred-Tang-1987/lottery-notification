"""t6e: user preferences + notification_settings table; drop notification_rules.timing.

合并 T6e round 3 的两个 migration（3c645973 + 5a1f2b8d9e04）成单一干净 migration。
旧 m3 错误把全局设置塞 per-lottery notification_rules（add master_enable/path_a_enable/
default_enabled），m5 又 drop 它们 + create 独立 notification_settings 表。
合并后直接最终态，无 add-then-drop（spec review round 3 finding「redundant migrations」）。

最终态：
- users +preferences_json（theme/new_numbers_default_enabled 等 JSON 缓存）
- notification_settings 新表（per-user 全局：master_enable/path_a_enable/summary_time/
  new_numbers_default_enabled）——独立表去 denormalization drift
- notification_rules -timing（时机从 per-lottery 移到 per-user NotificationSettings.summary_time）

Revision ID: t6e_notification_settings
Revises: 2024_add_user_dnd_json
Create Date: 2026-07-05 20:20:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 't6e_notification_settings'
down_revision: str | Sequence[str] | None = '2024_add_user_dnd_json'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # users.preferences_json: theme + new_numbers_default_enabled 等偏好 JSON 缓存
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('preferences_json', sa.String(), nullable=True))

    # notification_settings: per-user 全局设置独立表（去 denormalization drift）
    op.create_table(
        'notification_settings',
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('master_enable', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('path_a_enable', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('summary_time', sa.String(), nullable=True),
        sa.Column('new_numbers_default_enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.PrimaryKeyConstraint('user_id'),
    )

    # timing 从 per-lottery NotificationRule 移到 per-user NotificationSettings.summary_time
    with op.batch_alter_table('notification_rules', schema=None) as batch_op:
        batch_op.drop_column('timing')


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('notification_rules', schema=None) as batch_op:
        batch_op.add_column(sa.Column('timing', sa.String(), nullable=True))

    op.drop_table('notification_settings')

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('preferences_json')
