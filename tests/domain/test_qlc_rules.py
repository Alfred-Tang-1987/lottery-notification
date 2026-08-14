# tests/domain/test_qlc_rules.py
"""qlc 奖级修正测试（Plan 10 / T2）。

依据：福彩七乐彩官方规则——一二三等皆浮动（高等奖 70%/10%/20%）；
四~七等固定 200/50/10/5 元；七等仅 4+0（3+1 不中奖）。2026-08-14 核对。
"""

from app.domain.compare import PartitionCompare
from app.domain.prize_tables import get_tiers

DRAW_FRONT = (1, 2, 3, 4, 5, 6, 7)
SPECIAL = (8,)  # 特别号（同源 01-30 池）


def _qlc(front, back):
    return PartitionCompare.compare('qlc', DRAW_FRONT, SPECIAL, tuple(front), tuple(back), append=False)


def test_qlc_third_prize_is_float():
    """三等 6+0 是浮动奖（旧表误录固定 3045 元）。"""
    r = _qlc((1, 2, 3, 4, 5, 6, 9), (9,))
    assert r.is_win and r.tier == 3 and r.amount is None


def test_qlc_fourth_prize_200():
    """四等 5+1 = 200 元（旧表误录 300 元）。"""
    r = _qlc((1, 2, 3, 4, 5, 9, 9), (8,))
    assert r.is_win and r.tier == 4 and r.amount == 20000


def test_qlc_seventh_prize_only_4_plus_0():
    """七等仅 4+0 = 5 元；3+1 不中奖（旧表误含）。"""
    r = _qlc((1, 2, 3, 4, 9, 9, 9), (9,))
    assert r.is_win and r.tier == 7 and r.amount == 500
    r_31 = _qlc((1, 2, 3, 9, 9, 9, 9), (8,))
    assert not r_31.is_win, '3+1 在七乐彩不中奖'


def test_qlc_float_tiers_have_no_amount():
    tiers = {t.tier: t for t in get_tiers('qlc')}
    for n in (1, 2, 3):
        assert tiers[n].amount is None and tiers[n].amount_type == 'float'
