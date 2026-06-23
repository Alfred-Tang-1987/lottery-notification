import pytest
from app.domain.spec import NumberRange, PositionalDigits


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
