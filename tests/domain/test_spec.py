import pytest
from app.domain.spec import NumberRange, PositionalDigits, LotterySpec


def test_number_range_rejects_duplicates():
    """D7: NumberRange 集合语义——count 不能超过可用号码数（否则无法去重）。"""
    with pytest.raises(ValueError, match="count"):
        NumberRange(min=1, max=6, count=10)  # 1-6 只有 6 个，选 10 不可能


def test_number_range_valid():
    r = NumberRange(min=1, max=33, count=6)
    assert r.count == 6


def test_positional_digits_allows_duplicate_positions():
    """D7: PositionalDigits 允许跨位重复（七星彩前区 1,1,2,3,4,5 合法）。"""
    d = PositionalDigits(min=0, max=9, length=6)
    assert d.length == 6


def test_positional_digits_validates_range():
    d = PositionalDigits(min=0, max=9, length=3)
    assert d.max == 9


def test_number_range_count_exceeds_pool():
    with pytest.raises(ValueError):
        NumberRange(min=1, max=16, count=20)


def _ssq_spec_dict():
    return {
        "code": "ssq", "name": "双色球", "category": "welfare", "number_style": "partition",
        "front": {"min": 1, "max": 33, "count": 6},
        "back": {"min": 1, "max": 16, "count": 1},
        "draw_days": [1, 3, 6], "play_types": ["single", "fushi", "dantuo"],
        "welfare_rate": 36, "price_per_bet": 200,
    }


def test_lottery_spec_from_dict_partition():
    spec = LotterySpec.from_dict(_ssq_spec_dict())
    assert spec.code == "ssq"
    assert isinstance(spec.front, NumberRange)
    assert spec.welfare_rate == 36


def test_lottery_spec_from_dict_positional():
    spec = LotterySpec.from_dict({
        "code": "fc3d", "name": "福彩3D", "category": "welfare", "number_style": "positional",
        "front": {"min": 0, "max": 9, "length": 3}, "back": None,
        "draw_days": [0, 1, 2, 3, 4, 5, 6], "play_types": ["danxuan"],
        "welfare_rate": 34, "price_per_bet": 200,
    })
    assert isinstance(spec.front, PositionalDigits)
    assert spec.back is None


def test_lottery_spec_hybrid_qxc():
    spec = LotterySpec.from_dict({
        "code": "qxc", "name": "七星彩", "category": "sport", "number_style": "hybrid",
        "front": {"min": 0, "max": 9, "length": 6},
        "back": {"min": 0, "max": 14, "count": 1},
        "draw_days": [1, 4, 6], "play_types": ["single"],
        "welfare_rate": 37, "price_per_bet": 200,
    })
    assert isinstance(spec.front, PositionalDigits)  # 前区按位
    assert isinstance(spec.back, NumberRange)  # 后区单值 0-14
    assert spec.back.max == 14


def test_lottery_spec_validates_welfare_rate():
    d = _ssq_spec_dict()
    d["welfare_rate"] = 150
    with pytest.raises(ValueError, match="welfare_rate"):
        LotterySpec.from_dict(d)


def test_number_range_min_greater_than_max():
    with pytest.raises(ValueError, match="min"):
        NumberRange(min=10, max=5, count=1)


def test_number_range_count_less_than_one():
    with pytest.raises(ValueError, match="count"):
        NumberRange(min=1, max=33, count=0)


def test_positional_digits_min_greater_than_max():
    with pytest.raises(ValueError, match="min"):
        PositionalDigits(min=10, max=5, length=3)


def test_positional_digits_length_less_than_one():
    with pytest.raises(ValueError, match="length"):
        PositionalDigits(min=0, max=9, length=0)


def test_lottery_spec_invalid_number_style():
    d = _ssq_spec_dict()
    d["number_style"] = "invalid"
    with pytest.raises(ValueError, match="number_style"):
        LotterySpec.from_dict(d)


def test_lottery_spec_invalid_price_per_bet():
    d = _ssq_spec_dict()
    d["price_per_bet"] = 0
    with pytest.raises(ValueError, match="price_per_bet"):
        LotterySpec.from_dict(d)


def test_lottery_spec_invalid_draw_days():
    d = _ssq_spec_dict()
    d["draw_days"] = [7]
    with pytest.raises(ValueError, match="draw_days"):
        LotterySpec.from_dict(d)


def test_lottery_spec_empty_draw_days():
    d = _ssq_spec_dict()
    d["draw_days"] = []
    with pytest.raises(ValueError, match="draw_days"):
        LotterySpec.from_dict(d)
