"""fix historical comparisons.prize_amount: yuan misread as cents (×100)

Revision ID: fix_prize_amount_cents
Revises: p8_password_reset_codes
Create Date: 2026-08-03 08:30:00.000000

背景（2026-08-03 用户报告 + 修复）:
  prize_tables 固定档金额原本按官方「元」值录入（如 ssq 六等=5、三等=3000），
  但系统全程按「分」处理，导致 comparisons.prize_amount 存的是「元值 × 倍投」
  而非「分值 × 倍投」，所有固定档中奖金额显示缩小 100 倍
  （双色球六等 5 元显示「0.05 元」）。

  应用层修复已在 app/domain/prize_tables.py 把固定档金额改为分（×100）。
  本迁移纠正历史已落库的 comparisons.prize_amount：元值 -> 分值（×100）。

  浮动档（一二等奖，prize_tier IN (1,2)）由 refill_service 用 int(元)*100 回填，
  本来就是分值，**不在本迁移范围**，绝不触碰。

幂等性 / 安全:
  - alembic 版本机制保证 upgrade 只跑一次；单事务内全部 UPDATE，失败整体回滚。
  - 精确判别，不盲乘：仅当 `prize_amount * 100 == 期望分值 × ticket.multiplier`
    时才认定该行为「元值当分」并 ×100。已是分值（含倍投）的行不匹配，不动--
    即便 DB 被部分修复过或迁移被异常中断后重跑，也不会二次 ×100。
  - 期望分值表内嵌于此脚本（自包含，不 import 应用层，避免迁移对领域层耦合）。
"""

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'fix_prize_amount_cents'
down_revision: str | Sequence[str] | None = 'p8_password_reset_codes'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# 固定档期望分值（= 官方元金额 × 100），与 app/domain/prize_tables.py 修复后一致。
# 仅固定档（浮动档一二等不在此表，本迁移不动它们）。
# 来源：docs/reference/lottery-rules.md + spec §5.3 + 官方公告。
EXPECTED_CENTS: dict[str, dict[int, int]] = {
    'ssq': {3: 300000, 4: 20000, 5: 1000, 6: 500},
    'dlt': {3: 1000000, 4: 300000, 5: 30000, 6: 20000, 7: 10000},
    'qlc': {3: 304500, 4: 30000, 5: 5000, 6: 1000, 7: 500},
    'qxc': {3: 180000, 4: 30000, 5: 10000, 6: 1000},
    'fc3d': {1: 104000},
    'pl3': {1: 104000},
    'pl5': {1: 10000000},
}


def upgrade() -> None:
    """把历史固定档 prize_amount 从「元值×倍投」纠正为「分值×倍投」（×100）。

    判别：prize_amount * 100 == EXPECTED_CENTS[lottery][tier] * ticket.multiplier
    即「当前值恰好是期望分值的 1/100」-> 是元值当分 -> ×100。
    """
    bind = op.get_bind()

    for lottery_code, tiers in EXPECTED_CENTS.items():
        for tier, expected_cents in tiers.items():
            # join draw_results 取 lottery_code、tickets 取 multiplier。
            # 条件 prize_amount * 100 = expected_cents * multiplier 精确锁定「元值」行：
            #   错误行 prize_amount = (expected_cents/100) * m  ->  prize_amount*100 = expected_cents*m  ✓
            #   正确行 prize_amount = expected_cents * m        ->  prize_amount*100 = expected_cents*m*100 ✗
            # 故已是分值的行不会被匹配，避免二次 ×100。
            #
            # 防御 NULL multiplier：tickets.multiplier NOT NULL（schema 强制），无需额外处理。
            # 防御 ticket 缺失：comparison.ticket_id NOT NULL + FK，行必有对应 ticket。
            result = bind.execute(
                text(
                    """
                    UPDATE comparisons
                       SET prize_amount = prize_amount * 100
                      FROM draw_results dr, tickets t
                     WHERE comparisons.draw_result_id = dr.id
                       AND comparisons.ticket_id = t.id
                       AND dr.lottery_code = :lottery_code
                       AND comparisons.prize_tier = :tier
                       AND comparisons.prize_amount IS NOT NULL
                       AND comparisons.prize_amount * 100 = :expected_cents * t.multiplier
                    """
                ),
                {'lottery_code': lottery_code, 'tier': tier, 'expected_cents': expected_cents},
            )
            if result.rowcount:
                print(
                    f'  fix_prize_amount_cents: {lottery_code} tier={tier} '
                    f'corrected {result.rowcount} row(s) yuan->cents'
                )


def downgrade() -> None:
    """反向：把分值改回元值（÷100）。仅在需要回退 schema 版本时使用。

    判别：prize_amount == EXPECTED_CENTS[lottery][tier] * ticket.multiplier
    即「当前值恰好等于期望分值」-> 是分值 -> ÷100。

    ⚠️ 数据语义说明：本迁移纠正的 bug 影响所有历史固定档行（一律元值当分），
    故生产 DB 中 upgrade 前所有固定档行都是元值，upgrade 全部转为分值，
    downgrade 全部转回--完全对称可逆。

    若 DB 中混有「本就是分值」的固定档行（如迁移已跑过、或人工补过分值数据），
    downgrade 无法将其与「被 upgrade 转过的行」区分，会一并 ÷100。
    因此 downgrade 仅用于干净回退；混合状态下不应盲目 downgrade，应从备份恢复。
    """
    bind = op.get_bind()

    for lottery_code, tiers in EXPECTED_CENTS.items():
        for tier, expected_cents in tiers.items():
            result = bind.execute(
                text(
                    """
                    UPDATE comparisons
                       SET prize_amount = prize_amount / 100
                      FROM draw_results dr, tickets t
                     WHERE comparisons.draw_result_id = dr.id
                       AND comparisons.ticket_id = t.id
                       AND dr.lottery_code = :lottery_code
                       AND comparisons.prize_tier = :tier
                       AND comparisons.prize_amount IS NOT NULL
                       AND comparisons.prize_amount = :expected_cents * t.multiplier
                    """
                ),
                {'lottery_code': lottery_code, 'tier': tier, 'expected_cents': expected_cents},
            )
            if result.rowcount:
                print(
                    f'  fix_prize_amount_cents: {lottery_code} tier={tier} '
                    f'reverted {result.rowcount} row(s) cents->yuan'
                )
