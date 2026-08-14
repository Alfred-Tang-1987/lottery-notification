from app.domain.prize import AmountType
from app.domain.prize_tables import PRIZE_TABLES, get_tiers


def test_ssq_has_6_tiers():
    tiers = get_tiers('ssq')
    assert len(tiers) == 6
    assert tiers[0].tier == 1 and tiers[0].amount_type == AmountType.FLOAT  # 一等浮动
    assert tiers[2].amount == 300000  # 三等固定 3000 元 = 300000 分


def test_dlt_tier1_append_multiplier():
    tiers = get_tiers('dlt')
    assert tiers[0].append_multiplier == 1.8  # 一等追加


def test_all_7_lotteries_have_tables():
    for code in ('ssq', 'dlt', 'qlc', 'qxc', 'fc3d', 'pl3', 'pl5'):
        assert code in PRIZE_TABLES, f'缺 {code} 奖级表'
        assert len(get_tiers(code)) >= 1


def test_fixed_tiers_have_amount_float_tiers_none():
    for _code, tiers in PRIZE_TABLES.items():
        for t in tiers:
            if t.amount_type == AmountType.FIXED:
                assert t.amount is not None and t.amount > 0
            else:
                assert t.amount is None


# ---------------------------------------------------------------------------
# 固定档金额单位回归（2026-08-03 用户报告：周/月汇总中奖金额缩小 100 倍）
#
# 根因：prize_tables 固定档金额按「元」录入（spec §5.3 示例 amount=10 即 10 元），
# 但系统全程按「分」处理（Ticket.cost=200 分=2 元、dashboard「公益贡献（分）」、
# 前端 fmtMoney(cents/100)、浮动奖 refill adapter int(元)*100）。双色球六等奖
# 存 5（元）被当 5 分 -> 推送显示「0.05 元」（应为 5 元 = 500 分），与用户实测吻合。
#
# 修复：固定档金额一律改为「分」（官方元金额 × 100）。本测试锁定以分为单位的权威值，
# 防止元/分单位回退。官方元金额见 docs/reference/lottery-rules.md + spec §5.3。
# ---------------------------------------------------------------------------

# 权威固定档金额（分 = 元 × 100），依据官方规则：
#   ssq 三等3000 / 四等200 / 五等10 / 六等5 元
#   dlt 三等5000 / 四等300 / 五等150 / 六等15 / 七等5 元（2026-02-01 新规，财综〔2025〕51 号；2019 九档经 draw_date 版本门保留）
#   qlc 三等浮动（高等奖 20%）/ 四等200 / 五等50 / 六等10 / 七等5 元（2026-08-14 核对福彩官方）
#   qxc 三等1800 / 四等300 / 五等100 / 六等10 元
#   fc3d/pl3 单选/直选 1040 元
#   pl5 直选 100000 元
EXPECTED_FIXED_CENTS = {
    'ssq': {3: 300000, 4: 20000, 5: 1000, 6: 500},
    'dlt': {3: 500000, 4: 30000, 5: 15000, 6: 1500, 7: 500},
    'qlc': {4: 20000, 5: 5000, 6: 1000, 7: 500},
    'qxc': {3: 180000, 4: 30000, 5: 10000, 6: 1000},
    'fc3d': {1: 104000},
    'pl3': {1: 104000},
    'pl5': {1: 10000000},
}


def test_fixed_tiers_stored_in_cents_not_yuan():
    """所有固定档金额必须以「分」存储（元 × 100），而非「元」。

    旧值以元录入被当分处理 -> 显示缩小 100 倍（双色球六等 5 元显示 0.05 元）。
    """
    for code, expected in EXPECTED_FIXED_CENTS.items():
        tiers = {t.tier: t for t in get_tiers(code)}
        for tier, cents in expected.items():
            assert tiers[tier].amount == cents, (
                f'{code} {tier}等 金额应为 {cents} 分（{cents // 100} 元），'
                f'实得 {tiers[tier].amount}（疑似以元录入未 ×100）'
            )
