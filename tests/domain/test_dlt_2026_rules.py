# tests/domain/test_dlt_2026_rules.py
"""dlt 2026 新规（9 档并 7 档）+ 规则版本门测试（Plan 10 / T1）。

依据：财综〔2025〕51 号 + 体彩中心公告（2026-01-16），第 26014 期（2026-01-31）起执行。
合并规则：原(5+0)+(4+2)→新三等；原(4+0)+(3+2)→新五等；1+1/2+0/0+1 不中奖。
金额为奖池 <8 亿基础档；≥8 亿上浮档（6666/380/200/18/7）需奖池数据，B2 roadmap。
版本门：2026-01-31 之前的开奖日按 2019 九档表判定（eng-review Issue 3）。
"""

from datetime import date, datetime

import pytest

from app.domain.compare import PartitionCompare
from app.domain.prize_tables import get_tiers

DRAW_FRONT = (1, 2, 3, 4, 5)
DRAW_BACK = (6, 7)


def _dlt(front, back, append=False, draw_date=None):
    return PartitionCompare.compare(
        'dlt', DRAW_FRONT, DRAW_BACK, tuple(front), tuple(back),
        append=append, draw_date=draw_date,
    )


@pytest.mark.parametrize(
    ('front', 'back', 'tier', 'amount'),
    [
        ((1, 2, 3, 4, 5), (6, 7), 1, None),        # 5+2 一等浮动
        ((1, 2, 3, 4, 5), (6, 8), 2, None),        # 5+1 二等浮动
        ((1, 2, 3, 4, 5), (8, 9), 3, 500000),      # 5+0 → 新三等 5000 元
        ((1, 2, 3, 4, 9), (6, 7), 3, 500000),      # 4+2 → 新三等（合并档）
        ((1, 2, 3, 4, 9), (6, 8), 4, 30000),       # 4+1 → 四等 300 元
        ((1, 2, 3, 4, 9), (8, 9), 5, 15000),       # 4+0 → 新五等 150 元
        ((1, 2, 3, 9, 9), (6, 7), 5, 15000),       # 3+2 → 新五等（合并档）
        ((1, 2, 3, 9, 9), (6, 8), 6, 1500),        # 3+1 → 六等 15 元
        ((1, 2, 9, 9, 9), (6, 7), 6, 1500),        # 2+2 → 六等
        ((1, 2, 3, 9, 9), (8, 9), 7, 500),         # 3+0 → 七等 5 元
        ((1, 9, 9, 9, 9), (6, 7), 7, 500),         # 1+2 → 七等
        ((1, 2, 9, 9, 9), (6, 8), 7, 500),         # 2+1 → 七等
        ((9, 9, 9, 9, 9), (6, 7), 7, 500),         # 0+2 → 七等
    ],
)
def test_dlt_2026_tiers(front, back, tier, amount):
    """现行表（不传 draw_date = 最新版本）。"""
    r = _dlt(front, back)
    assert r.is_win, f'{front}+{back} 应中 {tier} 等'
    assert r.tier == tier and r.amount == amount


@pytest.mark.parametrize(
    ('front', 'back'),
    [
        ((1, 9, 9, 9, 9), (6, 8)),   # 1+1 不中奖（旧错误表曾误判七等 100 元）
        ((1, 2, 9, 9, 9), (8, 9)),   # 2+0 不中奖
        ((9, 9, 9, 9, 9), (6, 8)),   # 0+1 不中奖
        ((9, 9, 9, 9, 9), (8, 9)),   # 0+0
    ],
)
def test_dlt_2026_non_winning(front, back):
    assert not _dlt(front, back).is_win


def test_dlt_append_multiplier_only_on_float_tiers():
    """追加 1.8 仅一二等（浮动）；固定档 append_multiplier=1.0。"""
    tiers = {t.tier: t for t in get_tiers('dlt')}
    assert tiers[1].append_multiplier == 1.8
    assert tiers[2].append_multiplier == 1.8
    for n in range(3, 8):
        assert tiers[n].append_multiplier == 1.0, f'{n} 等不得有追加倍数'


# —— 规则版本门（eng-review Issue 3）：历史期按当时规则判定 ——


def test_dlt_version_boundary_old_draw_uses_2019_table():
    """2026-01-30 及之前 → 2019 九档：4+2 = 四等 3000 元（新规下同号组合是三等 5000）。"""
    r = _dlt((1, 2, 3, 4, 9), (6, 7), draw_date=date(2026, 1, 30))
    assert r.is_win and r.tier == 4 and r.amount == 300000


def test_dlt_version_boundary_new_draw_uses_2026_table():
    """2026-01-31（第 26014 期开售）起 → 七档：4+2 = 三等 5000 元。"""
    r = _dlt((1, 2, 3, 4, 9), (6, 7), draw_date=date(2026, 1, 31))
    assert r.is_win and r.tier == 3 and r.amount == 500000


def test_dlt_2019_tier8_tier9_exist():
    """2019 表八等（3+1/2+2=15 元）与九等（3+0/1+2/2+1/0+2=5 元）。"""
    r8 = _dlt((1, 2, 3, 9, 9), (6, 8), draw_date=date(2025, 6, 1))
    assert r8.is_win and r8.tier == 8 and r8.amount == 1500
    r9 = _dlt((9, 9, 9, 9, 9), (6, 7), draw_date=date(2025, 6, 1))
    assert r9.is_win and r9.tier == 9 and r9.amount == 500


def test_dlt_2019_five_plus_zero_is_tier3_10000():
    """2019 表三等 5+0 = 10000 元（与 2026 合并三等 5000 区分）。"""
    r = _dlt((1, 2, 3, 4, 5), (8, 9), draw_date=datetime(2026, 1, 15, 21, 30))
    assert r.is_win and r.tier == 3 and r.amount == 1000000
