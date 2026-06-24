from datetime import datetime, timedelta
from sqlmodel import Session, select
from app.services.refill_service import FloatRefillWorker
from app.models import User, Ticket, DrawResult, Comparison, PrizeClaim


def _seed_float_win(engine, days_ago=0, tier=1):
    with Session(engine) as s:
        u = User(username="u", password_hash="x", role="user", invite_code="C")
        s.add(u)
        s.commit()
        s.refresh(u)
        dr = DrawResult(
            lottery_code="ssq",
            draw_no="062",
            draw_date=datetime.utcnow() - timedelta(days=days_ago),
            numbers_json='{"front":[1,2,3,4,5,6],"back":[7]}',
            source="mxnzp",
            verified=True,
            version=1,
        )
        s.add(dr)
        s.commit()
        s.refresh(dr)
        t = Ticket(
            user_id=u.id,
            lottery_code="ssq",
            play_type="single",
            numbers_json='{"front":[1,2,3,4,5,6],"back":[7]}',
            multiplier=1,
            cost=200,
        )
        s.add(t)
        s.commit()
        s.refresh(t)
        cmp = Comparison(
            user_id=u.id,
            draw_result_id=dr.id,
            ticket_id=t.id,
            hits_json='{}',
            prize_tier=tier,
            prize_amount=None,
            is_win=True,
            created_at=datetime.utcnow() - timedelta(days=days_ago),
        )
        s.add(cmp)
        s.commit()
        s.refresh(cmp)
        return cmp.id


def test_refill_updates_null_amount(db_engine):
    cmp_id = _seed_float_win(db_engine, days_ago=1)
    from unittest.mock import MagicMock

    amount_lookup = MagicMock(return_value=5_000_000)  # 官方公布 5 万元（分）
    worker = FloatRefillWorker(db_engine, amount_lookup=amount_lookup, max_age_days=7)
    n = worker.refill()
    assert n == 1
    with Session(db_engine) as s:
        assert s.get(Comparison, cmp_id).prize_amount == 5_000_000


def test_refill_skips_after_max_age_and_marks_unresolved(db_engine):
    """超 7 天的浮奖不再查，并标记 unresolved=true。"""
    cmp_id = _seed_float_win(db_engine, days_ago=10)
    from unittest.mock import MagicMock

    worker = FloatRefillWorker(
        db_engine, amount_lookup=MagicMock(return_value=999), max_age_days=7
    )
    assert worker.refill() == 0  # 超期不查
    with Session(db_engine) as s:
        cmp = s.get(Comparison, cmp_id)
        assert cmp.prize_amount is None
        assert cmp.unresolved is True  # 必须标记 unresolved


def test_refill_explicit_tier_filter_only_float(db_engine):
    """仅 prize_tier IN (1,2) 的浮动档才进入回填；三等奖固定档 prize_amount 有值，不应被选中。"""
    cmp_id = _seed_float_win(db_engine, days_ago=1, tier=3)
    # 手动把 prize_amount 设为 None 模拟错误状态（固定档不应为 None）
    with Session(db_engine) as s:
        cmp = s.get(Comparison, cmp_id)
        cmp.prize_amount = None
        s.commit()
    from unittest.mock import MagicMock

    worker = FloatRefillWorker(
        db_engine, amount_lookup=MagicMock(return_value=999), max_age_days=7
    )
    assert worker.refill() == 0  # tier 3 不是浮动档，不应回填
    with Session(db_engine) as s:
        cmp = s.get(Comparison, cmp_id)
        assert cmp.prize_amount is None  # 未被修改
        assert cmp.unresolved is not True
