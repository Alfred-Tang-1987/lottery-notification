from app.domain.prize import AmountType, HitResult, PrizeTier


def test_prize_tier_fixed():
    t = PrizeTier(tier=5, condition='front_hit==4 and back_hit==0', amount=10, amount_type=AmountType.FIXED)
    assert t.amount == 10
    assert t.append_multiplier == 1.0


def test_prize_tier_float_append():
    t = PrizeTier(
        tier=1,
        condition='front_hit==5 and back_hit==2',
        amount=None,
        amount_type=AmountType.FLOAT,
        append_multiplier=1.8,
    )
    assert t.append_multiplier == 1.8


def test_hit_result_win():
    r = HitResult(front_hit=6, back_hit=1, tier=1, amount=None, is_win=True)
    assert r.is_win
    assert r.tier == 1
