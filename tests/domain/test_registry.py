import pytest

from app.domain.compare import REGISTRY, PartitionCompare, PositionalCompare, QxcHybridCompare, compare


def test_registry_routes_partition():
    assert REGISTRY['ssq'] is PartitionCompare
    assert REGISTRY['dlt'] is PartitionCompare
    assert REGISTRY['qlc'] is PartitionCompare


def test_registry_routes_positional():
    assert REGISTRY['fc3d'] is PositionalCompare
    assert REGISTRY['pl3'] is PositionalCompare
    assert REGISTRY['pl5'] is PositionalCompare


def test_registry_routes_hybrid():
    assert REGISTRY['qxc'] is QxcHybridCompare


def test_compare_entry_ssq_single():
    """入口：compare(spec, draw, entry) 自动展开 single 并比对。"""
    from app.domain.entry import Entry
    from app.domain.spec import LotterySpec

    spec = LotterySpec.from_dict(
        {
            'code': 'ssq',
            'name': '双色球',
            'category': 'welfare',
            'number_style': 'partition',
            'front': {'min': 1, 'max': 33, 'count': 6},
            'back': {'min': 1, 'max': 16, 'count': 1},
            'draw_days': [1, 3, 6],
            'play_types': ['single'],
            'welfare_rate': 36,
            'price_per_bet': 200,
        }
    )
    entry = Entry('ssq', 'single', (1, 2, 3, 4, 5, 6), (7,))
    results = compare(spec, draw_front=(1, 2, 3, 4, 5, 6), draw_back=(7,), entry=entry)
    assert len(results) == 1
    assert results[0].is_win and results[0].tier == 1


def test_compare_entry_positional_fc3d():
    """入口：按位型彩种（福彩3D）自动用 draw_front 当 draw 路由到 PositionalCompare。"""
    from app.domain.entry import Entry
    from app.domain.spec import LotterySpec

    spec = LotterySpec.from_dict(
        {
            'code': 'fc3d',
            'name': '福彩3D',
            'category': 'welfare',
            'number_style': 'positional',
            'front': {'min': 0, 'max': 9, 'length': 3},
            'back': None,
            'draw_days': [0, 1, 2, 3, 4, 5, 6],
            'play_types': ['danxuan'],
            'welfare_rate': 34,
            'price_per_bet': 200,
        }
    )
    entry = Entry('fc3d', 'single', (1, 2, 3), None)
    results = compare(spec, draw_front=(1, 2, 3), draw_back=None, entry=entry)
    assert len(results) == 1
    assert results[0].is_win and results[0].tier == 1 and results[0].amount == 1040


def test_compare_entry_hybrid_qxc():
    """入口：混合型彩种（七星彩）路由到 QxcHybridCompare。"""
    from app.domain.entry import Entry
    from app.domain.spec import LotterySpec

    spec = LotterySpec.from_dict(
        {
            'code': 'qxc',
            'name': '七星彩',
            'category': 'sport',
            'number_style': 'hybrid',
            'front': {'min': 0, 'max': 9, 'length': 6},
            'back': {'min': 0, 'max': 14, 'count': 1},
            'draw_days': [1, 4, 6],
            'play_types': ['single'],
            'welfare_rate': 37,
            'price_per_bet': 200,
        }
    )
    entry = Entry('qxc', 'single', (1, 2, 3, 4, 5, 6), (7,))
    results = compare(spec, draw_front=(1, 2, 3, 4, 5, 6), draw_back=(7,), entry=entry)
    assert len(results) == 1
    assert results[0].is_win and results[0].tier == 1


def test_compare_unknown_lottery_raises():
    """未知彩种代码不在 REGISTRY 中应明确报错（不静默 fallback）。"""
    from app.domain.entry import Entry
    from app.domain.spec import LotterySpec

    spec = LotterySpec.from_dict(
        {
            'code': 'ssq',
            'name': '双色球',
            'category': 'welfare',
            'number_style': 'partition',
            'front': {'min': 1, 'max': 33, 'count': 6},
            'back': {'min': 1, 'max': 16, 'count': 1},
            'draw_days': [1, 3, 6],
            'play_types': ['single'],
            'welfare_rate': 36,
            'price_per_bet': 200,
        }
    )
    spec.code = 'unknown_code'  # 造一个不在 REGISTRY 的 code
    entry = Entry('unknown_code', 'single', (1, 2, 3, 4, 5, 6), (7,))
    with pytest.raises(KeyError):
        compare(spec, draw_front=(1, 2, 3, 4, 5, 6), draw_back=(7,), entry=entry)
