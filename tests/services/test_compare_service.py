"""CompareService 测试：outbox 原子认领 + domain.compare 接线 + 写 comparisons/prize_claims。

覆盖 spec §7.1 核心数据流：开奖结果 verified 入库后 outbox(pending_comparisons)
被原子认领 → 取追投 tickets → 领域 compare() → 写 comparisons（唯一约束兜底）+
中奖写 prize_claims(pending)。
"""
import json
from datetime import datetime

from sqlmodel import Session, select

from app.models import (
    User, Ticket, DrawResult, PendingComparison, Comparison, PrizeClaim,
)
from app.services.compare_service import CompareService


def _make_user(session, username="u"):
    u = User(username=username, password_hash="x", role="user", invite_code="C")
    session.add(u); session.commit(); session.refresh(u)
    return u


def _seed_draw(session, code="ssq", front=(1, 2, 3, 4, 5, 6), back=(7,)):
    dr = DrawResult(
        lottery_code=code, draw_no="062", draw_date=datetime.utcnow(),
        numbers_json=json.dumps({"front": list(front), "back": list(back)}),
        source="mxnzp", verified=True, version=1,
    )
    session.add(dr); session.commit(); session.refresh(dr)
    pc = PendingComparison(draw_result_id=dr.id)
    session.add(pc); session.commit()
    return dr


def _seed_ticket(session, user_id, front=(1, 2, 3, 4, 5, 6), back=(7,)):
    t = Ticket(
        user_id=user_id, lottery_code="ssq", play_type="single",
        numbers_json=json.dumps({"front": list(front), "back": list(back)}),
        multiplier=1, append=False, cost=200, enabled=True,
    )
    session.add(t); session.commit(); session.refresh(t)
    return t


def test_compare_writes_comparison_first_prize(db_engine):
    """一等奖（双色球 6红+1蓝）→ 写 comparison(is_win, tier=1) + prize_claim(pending)。"""
    with Session(db_engine) as s:
        u = _make_user(s)
        _seed_draw(s)
        _seed_ticket(s, u.id)
    svc = CompareService(db_engine)
    n = svc.process_pending()  # 处理所有 outbox
    assert n == 1
    with Session(db_engine) as s:
        cmp = s.exec(select(Comparison)).first()
        assert cmp.is_win and cmp.prize_tier == 1
        # 一等奖应生成 prize_claims(pending)
        claim = s.exec(select(PrizeClaim)).first()
        assert claim and claim.status == "pending"


def test_compare_no_ticket_no_comparison(db_engine):
    """号码池无该彩种注 → outbox 标 processed 但不生成 comparisons。"""
    with Session(db_engine) as s:
        _seed_draw(s)  # 无 ticket
    svc = CompareService(db_engine)
    svc.process_pending()
    with Session(db_engine) as s:
        assert s.exec(select(Comparison)).first() is None
        pc = s.exec(select(PendingComparison)).first()
        assert pc.processed_at is not None  # 标 processed


def test_outbox_claim_idempotent(db_engine):
    """重复 process 不重复比对（已认领的 pending 不再处理）。"""
    with Session(db_engine) as s:
        u = _make_user(s)
        _seed_draw(s)
        _seed_ticket(s, u.id)
    svc = CompareService(db_engine)
    svc.process_pending()
    svc.process_pending()  # 第二次：无新 pending
    with Session(db_engine) as s:
        assert len(s.exec(select(Comparison)).all()) == 1  # 不重复


def test_compare_fixed_prize_applies_multiplier(db_engine):
    """倍投放大固定奖金额（lottery-rules §倍投：中奖金额 × 倍数）。

    双色球三等奖（5红+1蓝 = 3000 分）× 3 倍 = 9000 分。
    回归保护：旧版直接存 hit.amount（单注 3000），漏乘 multiplier。
    """
    with Session(db_engine) as s:
        u = _make_user(s)
        _seed_draw(s, front=(1, 2, 3, 4, 5, 6), back=(7,))
        # 5 红 + 1 蓝 → 三等奖；倍投 3 倍
        t = Ticket(
            user_id=u.id, lottery_code="ssq", play_type="single",
            numbers_json=json.dumps({"front": [1, 2, 3, 4, 5, 33], "back": [7]}),
            multiplier=3, append=False, cost=600, enabled=True,
        )
        s.add(t); s.commit(); s.refresh(t)
    svc = CompareService(db_engine)
    svc.process_pending()
    with Session(db_engine) as s:
        cmp = s.exec(select(Comparison)).first()
        assert cmp is not None and cmp.prize_tier == 3
        assert cmp.prize_amount == 9000  # 3000 × 3 倍


def test_compare_float_prize_amount_stays_null_with_multiplier(db_engine):
    """浮动奖（一二等奖）amount 即使有倍投也保持 null——金额未知，倍投在回填（T5）应用。

    防止误把倍投乘到 None 上（None * 3 会崩或变 0）。"""
    with Session(db_engine) as s:
        u = _make_user(s)
        uid = u.id
        _seed_draw(s, front=(1, 2, 3, 4, 5, 6), back=(7,))
        # 一等奖（6红+1蓝，浮动），倍投 5 倍
        s.add(Ticket(
            user_id=uid, lottery_code="ssq", play_type="single",
            numbers_json=json.dumps({"front": [1, 2, 3, 4, 5, 6], "back": [7]}),
            multiplier=5, append=False, cost=1000, enabled=True,
        ))
        s.commit()
    svc = CompareService(db_engine)
    svc.process_pending()
    with Session(db_engine) as s:
        cmps = s.exec(select(Comparison).order_by(Comparison.id)).all()
        assert len(cmps) == 1
        cmp = cmps[0]
        assert cmp.prize_tier == 1
        assert cmp.prize_amount is None  # 浮动奖不乘倍投，回填时应用
