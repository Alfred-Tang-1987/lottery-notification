from app.domain.compare import PositionalCompare


def test_fc3d_danxuan_all_match():
    """单选：3 位全对 = 中奖 1040 元 = 104000 分。"""
    r = PositionalCompare.compare(
        lottery='fc3d',
        draw=(1, 2, 3),
        combo=(1, 2, 3),
    )
    assert r.is_win and r.tier == 1 and r.amount == 104000


def test_fc3d_partial_no_win():
    """2 位对 = 未中奖（单选需全对）。"""
    r = PositionalCompare.compare('fc3d', (1, 2, 3), (1, 2, 9))
    assert not r.is_win


def test_pl5_all_match():
    """排列5 直选：5 位全对 = 10 万 元 = 10000000 分。"""
    r = PositionalCompare.compare('pl5', (1, 2, 3, 4, 5), (1, 2, 3, 4, 5))
    assert r.is_win and r.amount == 10000000


def test_pl3_order_matters():
    """直选顺序敏感：(1,2,3) ≠ (3,2,1)。"""
    r = PositionalCompare.compare('pl3', (1, 2, 3), (3, 2, 1))
    assert not r.is_win  # 顺序错=未中
