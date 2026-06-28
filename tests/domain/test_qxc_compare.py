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
    """前区 6 位全对 + 后区不对 = 二等（浮动）。"""
    r = QxcHybridCompare.compare(
        'qxc',
        (1, 2, 3, 4, 5, 6),
        (7,),
        (1, 2, 3, 4, 5, 6),
        (8,),
    )
    assert r.is_win and r.tier == 2


def test_qxc_partial_front():
    """前区连续 5 位对 + 后区对 = 三等。"""
    r = QxcHybridCompare.compare(
        'qxc',
        (1, 2, 3, 4, 5, 9),
        (7,),
        (1, 2, 3, 4, 5, 6),
        (7,),
    )
    assert r.is_win and r.tier == 3


def test_qxc_no_win():
    r = QxcHybridCompare.compare(
        'qxc',
        (1, 2, 3, 4, 5, 6),
        (7,),
        (9, 8, 7, 6, 5, 4),
        (8,),
    )
    assert not r.is_win
