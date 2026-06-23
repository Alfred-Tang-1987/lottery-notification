from app.domain.prize_tables import PRIZE_TABLES, get_tiers
from app.domain.prize import AmountType


def test_ssq_has_6_tiers():
    tiers = get_tiers("ssq")
    assert len(tiers) == 6
    assert tiers[0].tier == 1 and tiers[0].amount_type == AmountType.FLOAT  # 一等浮动
    assert tiers[2].amount == 3000  # 三等固定


def test_dlt_tier1_append_multiplier():
    tiers = get_tiers("dlt")
    assert tiers[0].append_multiplier == 1.8  # 一等追加


def test_all_7_lotteries_have_tables():
    for code in ("ssq", "dlt", "qlc", "qxc", "fc3d", "pl3", "pl5"):
        assert code in PRIZE_TABLES, f"缺 {code} 奖级表"
        assert len(get_tiers(code)) >= 1


def test_fixed_tiers_have_amount_float_tiers_none():
    for code, tiers in PRIZE_TABLES.items():
        for t in tiers:
            if t.amount_type == AmountType.FIXED:
                assert t.amount is not None and t.amount > 0
            else:
                assert t.amount is None
