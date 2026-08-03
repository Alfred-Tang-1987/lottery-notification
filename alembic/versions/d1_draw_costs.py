"""draw_costs: 期次成本表（按 user+lottery+draw_no 记账，spec §4 成本按开奖日记账）.

Revision ID: d1_draw_costs
Revises: p8_password_reset_codes
Create Date: 2026-08-03 08:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd1_draw_costs'
down_revision: str | Sequence[str] | None = 'fix_prize_amount_cents'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema: 新建 draw_costs 表。

    历史回填在 T5 CLI backfill-draw-costs 完成（迁移只建结构，不在迁移内跑业务回填--
    避免迁移依赖 comparisons/tickets 的业务语义，保持迁移纯 schema 变更）。
    """
    op.create_table(
        'draw_costs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('lottery_code', sa.String(length=8), nullable=False),
        sa.Column('draw_no', sa.String(length=16), nullable=False),
        sa.Column('cost', sa.Integer(), server_default='0', nullable=False),
        sa.Column('draw_date', sa.DateTime(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.ForeignKeyConstraint(['lottery_code'], ['lottery_types.code']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'user_id', 'lottery_code', 'draw_no', name='uq_draw_cost_user_lottery_no'
        ),
    )
    op.create_index(
        op.f('ix_draw_costs_user_id'), 'draw_costs', ['user_id'], unique=False
    )
    op.create_index(
        op.f('ix_draw_costs_lottery_code'), 'draw_costs', ['lottery_code'], unique=False
    )
    op.create_index(
        op.f('ix_draw_costs_draw_no'), 'draw_costs', ['draw_no'], unique=False
    )

    # 历史回填（spec §4）：迁移前已有 DrawResult+tickets 但无 DrawCost（成本口径切换前的
    # 历史断层）。按 (user, lottery, draw_no) 聚合 enabled 追投注 cost 一次性补齐。
    # 与 compare_service._upsert_draw_costs 同语义：每张 enabled 追投注对该彩种每期都计成本。
    # ON CONFLICT 幂等（迁移重跑/部分完成安全）。
    op.execute(
        """
        INSERT INTO draw_costs (user_id, lottery_code, draw_no, cost, draw_date, created_at)
        SELECT t.user_id,
               t.lottery_code,
               dr.draw_no,
               COALESCE(SUM(t.cost), 0),
               dr.draw_date,
               CURRENT_TIMESTAMP
        FROM draw_results dr
        JOIN tickets t ON t.lottery_code = dr.lottery_code AND t.enabled = 1
        GROUP BY t.user_id, dr.id, dr.draw_no, dr.draw_date
        ON CONFLICT(user_id, lottery_code, draw_no) DO UPDATE SET
            cost = excluded.cost,
            draw_date = excluded.draw_date
        """
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_draw_costs_draw_no'), table_name='draw_costs')
    op.drop_index(op.f('ix_draw_costs_lottery_code'), table_name='draw_costs')
    op.drop_index(op.f('ix_draw_costs_user_id'), table_name='draw_costs')
    op.drop_table('draw_costs')
