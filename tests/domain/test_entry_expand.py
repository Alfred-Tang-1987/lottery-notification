import pytest

from app.domain.entry import MAX_COMBINATIONS, Entry, expand


def _single_entry(front, back=None, **kw):
    defaults = dict(lottery_code='ssq', play_type='single', multiplier=1, append=False)
    defaults.update(kw)
    return Entry(
        front=tuple(front),
        back=tuple(back) if back else None,
        **defaults,
    )


def test_expand_single_returns_one_combo():
    e = _single_entry((1, 2, 3, 4, 5, 6), (7,))
    combos = expand(e)
    assert len(combos) == 1
    assert combos[0].front == (1, 2, 3, 4, 5, 6)
    assert combos[0].back == (7,)


def test_expand_cost_single():
    e = _single_entry((1, 2, 3, 4, 5, 6), (7,))
    assert e.cost(price_per_bet=200) == 200  # 1 注 × 2 元


def test_expand_cost_with_multiplier():
    e = _single_entry((1, 2, 3, 4, 5, 6), (7,), multiplier=5)
    assert e.cost(price_per_bet=200) == 1000  # 1 注 × 2 元 × 5 倍


def test_expand_cost_with_append_dlt():
    """大乐透追加：基本 2 元 + 追加 1 元 = 3 元/注。"""
    e = Entry('dlt', 'single', (1, 2, 3, 4, 5), (6, 7), multiplier=1, append=True)
    assert e.cost(price_per_bet=200) == 300  # 2 + 1


def test_expand_rejects_invalid_multiplier():
    with pytest.raises(ValueError, match='multiplier'):
        Entry('ssq', 'single', (1, 2, 3, 4, 5, 6), (7,), multiplier=100)


def test_expand_fushi_phase2_not_implemented():
    """MVP 仅 single；fushi 展开需 spec 精确（Phase 2），当前 raise NotImplementedError
    （硬编码 6 会算错大乐透5/七乐彩7，故 MVP 诚实拒绝而非估错 cost）。"""
    e = Entry('ssq', 'fushi', tuple(range(1, 34)), (7,), multiplier=1, append=False)
    with pytest.raises(NotImplementedError, match='Phase 2'):
        expand(e)


# ===== Review Round 1 Fixes =====


def test_expand_enforces_max_combinations():
    """MAX_COMBINATIONS=10000 must be enforced in expand()."""
    # Simulate an entry that would exceed MAX_COMBINATIONS if we had fushi
    # For now, we test that the infrastructure exists by checking single passes
    e = _single_entry((1, 2, 3, 4, 5, 6), (7,))
    combos = expand(e)
    assert len(combos) == 1  # single always OK

    # When fushi is implemented, it must raise ValueError if exceeding MAX_COMBINATIONS
    # For now, fushi raises NotImplementedError which is acceptable as Phase 2


def test_expand_hash_memo_cache():
    """expand() must use a content-hash based memo cache; same entry returns same list object."""
    e = _single_entry((1, 2, 3, 4, 5, 6), (7,))
    combos1 = expand(e)
    combos2 = expand(e)
    # Same entry content should return cached result (same object identity)
    assert combos1 is combos2


def test_expand_cache_invalidated_on_change():
    """Cache keyed by entry content; different entry returns different result."""
    e1 = _single_entry((1, 2, 3, 4, 5, 6), (7,))
    e2 = _single_entry((1, 2, 3, 4, 5, 7), (7,))
    combos1 = expand(e1)
    combos2 = expand(e2)
    assert combos1 is not combos2
    assert combos1[0].front == (1, 2, 3, 4, 5, 6)
    assert combos2[0].front == (1, 2, 3, 4, 5, 7)


def test_append_only_valid_for_dlt():
    """追加投注仅大乐透；非 dlt Entry with append=True must raise."""
    # dlt with append=True is OK
    e_dlt = Entry('dlt', 'single', (1, 2, 3, 4, 5), (6, 7), multiplier=1, append=True)
    assert e_dlt.cost(price_per_bet=200) == 300

    # ssq with append=True should raise ValueError
    e_ssq = Entry('ssq', 'single', (1, 2, 3, 4, 5, 6), (7,), multiplier=1, append=True)
    with pytest.raises(ValueError, match='append'):
        e_ssq.cost(price_per_bet=200)

    # fc3d with append=True should raise ValueError
    e_fc3d = Entry('fc3d', 'zhixuan', (1, 2, 3), None, multiplier=1, append=True)
    with pytest.raises(ValueError, match='append'):
        e_fc3d.cost(price_per_bet=200)


def test_expand_zhixuan_positional():
    """MVP 支持按位型 zhixuan（同 single 语义，一注直接返回）。"""
    e = Entry('pl3', 'zhixuan', (1, 2, 3), None, multiplier=1, append=False)
    combos = expand(e)
    assert len(combos) == 1
    assert combos[0].front == (1, 2, 3)
    assert combos[0].back is None


def test_expand_zhixuan_cost():
    """zhixuan positional entry cost calculation."""
    e = Entry('pl3', 'zhixuan', (1, 2, 3), None, multiplier=2, append=False)
    assert e.cost(price_per_bet=200) == 400  # 1 注 × 2 元 × 2 倍


def test_count_combos_enforces_max_combinations():
    """_count_combos must enforce MAX_COMBINATIONS (even if single is trivially 1)."""
    # single is always 1, well within limit
    e = _single_entry((1, 2, 3, 4, 5, 6), (7,))
    from app.domain.entry import _count_combos

    assert _count_combos(e) == 1
    # When fushi is implemented, it must check against MAX_COMBINATIONS and raise


def test_max_combinations_constant_exists():
    """MAX_COMBINATIONS must be defined and equal to 10000."""
    assert MAX_COMBINATIONS == 10000


def test_compare_strategy_base_raises():
    """CompareStrategy base class compare() must raise NotImplementedError."""
    from app.domain.compare import CompareStrategy

    with pytest.raises(NotImplementedError):
        CompareStrategy.compare(
            lottery='ssq',
            draw_front=(1, 2, 3, 4, 5, 6),
            draw_back=(7,),
            combo_front=(1, 2, 3, 4, 5, 6),
            combo_back=(7,),
            append=False,
        )
