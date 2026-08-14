"""QxcHybridCompare 测试（Plan 10 / T3 重写）。

依据：7星彩 2020-10-13 新规——「任意 N 位对位一致」（按位对号、不要求连续），
非旧版「连续 N 位」。固定档 3000/500/30/5 元（lottery.gov.cn 规则第二十二条，
2026-08-14 核对；旧版 1800/300/100/10 已作废）。
"""

from app.domain.compare import QxcHybridCompare


def test_qxc_first_prize():
    """前区 6 位全对 + 后区对 = 一等（浮动）。"""
    r = QxcHybridCompare.compare(
        lottery='qxc',
        draw_front=(1, 2, 3, 4, 5, 6),
        draw_back=(7,),
        combo_front=(1, 2, 3, 4, 5, 6),
        combo_back=(7,),
    )
    assert r.is_win and r.tier == 1 and r.amount is None


def test_qxc_second_prize():
    r = QxcHybridCompare.compare('qxc', (1, 2, 3, 4, 5, 6), (7,), (1, 2, 3, 4, 5, 6), (8,))
    assert r.is_win and r.tier == 2 and r.amount is None


def test_qxc_third_prize_any_5_plus_back():
    """前区任意 5 位对位 + 后区对 = 三等 3000 元。对位不要求连续：
    第 2 位错、其余 5 位对，照样三等。"""
    r = QxcHybridCompare.compare('qxc', (1, 2, 3, 4, 5, 6), (7,), (1, 9, 3, 4, 5, 6), (7,))
    assert r.is_win and r.tier == 3 and r.amount == 300000


def test_qxc_fourth_prize_any_5():
    """任意 5 位对位（含 4 前+后区）= 四等 500 元。"""
    assert QxcHybridCompare.compare('qxc', (1, 2, 3, 4, 5, 6), (7,), (1, 9, 3, 4, 5, 6), (8,)).amount == 50000  # 5+0
    assert QxcHybridCompare.compare('qxc', (1, 2, 3, 4, 5, 6), (7,), (1, 9, 3, 9, 5, 6), (7,)).amount == 50000  # 4+1


def test_qxc_fifth_prize_any_4():
    """任意 4 位对位 = 五等 30 元。"""
    # 4+0：第 0/2/3/4 位命中，后区不中
    assert QxcHybridCompare.compare('qxc', (1, 2, 3, 4, 5, 6), (7,), (1, 9, 3, 4, 5, 9), (8,)).amount == 3000
    # 3+1：第 0/2/4 位命中 + 后区中
    assert QxcHybridCompare.compare('qxc', (1, 2, 3, 4, 5, 6), (7,), (1, 9, 3, 9, 5, 9), (7,)).amount == 3000


def test_qxc_sixth_prize_all_forms():
    """六等 5 元：3+0 / 2+1 / 1+1 / 0+1（旧实现漏判后三种——静默漏中奖）。"""
    draw_f, draw_b = (1, 2, 3, 4, 5, 6), (7,)
    # 3+0：第 0/2/4 位命中，后区不中
    assert QxcHybridCompare.compare('qxc', draw_f, draw_b, (1, 9, 3, 9, 5, 9), (8,)).amount == 500
    # 2+1：第 0/5 位命中 + 后区中
    assert QxcHybridCompare.compare('qxc', draw_f, draw_b, (1, 9, 9, 9, 9, 6), (7,)).amount == 500
    # 1+1：仅第 0 位命中 + 后区中
    assert QxcHybridCompare.compare('qxc', draw_f, draw_b, (1, 9, 9, 9, 9, 9), (7,)).amount == 500
    # 0+1：仅后区中
    r = QxcHybridCompare.compare('qxc', draw_f, draw_b, (9, 9, 9, 9, 9, 9), (7,))
    assert r.is_win and r.tier == 6 and r.amount == 500


def test_qxc_no_win():
    # 0+0：逐位全错、后区不中
    r = QxcHybridCompare.compare('qxc', (1, 2, 3, 4, 5, 6), (7,), (9, 8, 7, 9, 9, 9), (8,))
    assert not r.is_win
    # 2+0 / 1+0 不中奖
    assert not QxcHybridCompare.compare('qxc', (1, 2, 3, 4, 5, 6), (7,), (1, 2, 9, 9, 9, 9), (8,)).is_win
