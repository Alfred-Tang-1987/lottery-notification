from app.domain.compare import PartitionCompare


def test_ssq_first_prize_float():
    """6红+1蓝 = 一等奖（浮动）。"""
    r = PartitionCompare.compare(
        lottery='ssq',
        draw_front=(1, 2, 3, 4, 5, 6),
        draw_back=(7,),
        combo_front=(1, 2, 3, 4, 5, 6),
        combo_back=(7,),
        append=False,
    )
    assert r.is_win and r.tier == 1 and r.amount is None


def test_ssq_sixth_prize_fixed():
    """0红+1蓝 = 六等奖 5 元。"""
    r = PartitionCompare.compare(
        'ssq',
        (8, 9, 10, 11, 12, 13),
        (7,),
        (1, 2, 3, 4, 5, 6),
        (7,),
        append=False,
    )
    assert r.is_win and r.tier == 6 and r.amount == 5


def test_ssq_no_win():
    """0红+0蓝 = 未中奖。"""
    r = PartitionCompare.compare(
        'ssq',
        (8, 9, 10, 11, 12, 13),
        (14,),
        (1, 2, 3, 4, 5, 6),
        (7,),
        append=False,
    )
    assert not r.is_win and r.tier is None


def test_dlt_append_multiplier_applied():
    """大乐透追加：一等浮动，amount 仍 None（运行时回填），但标记 append 生效（tier1 命中即可）。"""
    r = PartitionCompare.compare(
        'dlt',
        (1, 2, 3, 4, 5),
        (6, 7),
        (1, 2, 3, 4, 5),
        (6, 7),
        append=True,
    )
    assert r.is_win and r.tier == 1  # amount=None（浮动），append 1.8 在回填时应用


def test_ssq_third_prize():
    """5红+1蓝 = 三等 3000。"""
    r = PartitionCompare.compare(
        'ssq',
        (1, 2, 3, 4, 5, 33),
        (7,),
        (1, 2, 3, 4, 5, 6),
        (7,),
        append=False,
    )
    assert r.is_win and r.tier == 3 and r.amount == 3000
