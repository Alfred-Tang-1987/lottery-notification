import json
import pytest
from datetime import datetime
from sqlmodel import Session, select
from app.services.correct_service import DrawCorrectService
from app.services.compare_service import CompareService
from app.models import User, Ticket, DrawResult, PendingComparison, Comparison, DrawCorrection


def _seed_user_and_ticket(session, front=(1, 2, 3, 4, 5, 6), back=(7,)):
    u = User(username="u", password_hash="x", role="user", invite_code="C")
    session.add(u)
    session.commit()
    session.refresh(u)
    dr = DrawResult(
        lottery_code="ssq", draw_no="062", draw_date=datetime.utcnow(),
        numbers_json=json.dumps({"front": list(front), "back": list(back)}),
        source="mxnzp", verified=True, version=1,
    )
    session.add(dr)
    session.commit()
    session.refresh(dr)
    pc = PendingComparison(draw_result_id=dr.id)
    session.add(pc)
    session.commit()
    t = Ticket(
        user_id=u.id, lottery_code="ssq", play_type="single",
        numbers_json=json.dumps({"front": list(front), "back": list(back)}),
        multiplier=1, cost=200,
    )
    session.add(t)
    session.commit()
    return dr.id


def test_correct_increments_version_and_recompares(db_engine):
    """官方更正：version++、写 draw_corrections、重比后原地更新 comparison。"""
    with Session(db_engine) as s:
        dr_id = _seed_user_and_ticket(s)

    # 首次比对：6+1 = 一等奖
    CompareService(db_engine).process_pending()
    with Session(db_engine) as s:
        cmp = s.exec(select(Comparison)).first()
        assert cmp.is_win and cmp.prize_tier == 1

    # 更正：蓝球 7→8，变成 6+0 = 二等奖（仍是 win，tier 变 2）
    DrawCorrectService(db_engine).correct(
        draw_result_id=dr_id,
        new_front=(1, 2, 3, 4, 5, 6),
        new_back=(8,),
        reason="官方更正",
    )
    CompareService(db_engine).process_pending()

    with Session(db_engine) as s:
        dr = s.get(DrawResult, dr_id)
        assert dr.version == 2
        corr = s.exec(select(DrawCorrection)).first()
        assert corr is not None
        cmps = s.exec(select(Comparison)).all()
        assert len(cmps) == 1  # 原地更新，不新增行
        assert cmps[0].is_win is True  # 6+0 仍是二等奖
        assert cmps[0].prize_tier == 2
        assert cmps[0].corrected_at is not None


def test_correct_true_lose_changes_is_win(db_engine):
    """更正后 truly lose：front 全变 → 0 命中 → is_win=False，且 claim 被删除。"""
    with Session(db_engine) as s:
        dr_id = _seed_user_and_ticket(s)

    CompareService(db_engine).process_pending()
    with Session(db_engine) as s:
        assert s.exec(select(Comparison)).first().is_win is True
        assert s.exec(select(DrawCorrection)).first() is None

    # 更正：front 全换，用户 0 命中
    DrawCorrectService(db_engine).correct(
        draw_result_id=dr_id,
        new_front=(10, 11, 12, 13, 14, 15),
        new_back=(8,),
        reason="官方更正",
    )
    CompareService(db_engine).process_pending()

    with Session(db_engine) as s:
        dr = s.get(DrawResult, dr_id)
        assert dr.version == 2
        cmp = s.exec(select(Comparison)).first()
        assert cmp.is_win is False
        assert cmp.prize_tier is None
        assert cmp.corrected_at is not None


def test_correct_draw_result_not_found_raises(db_engine):
    svc = DrawCorrectService(db_engine)
    with pytest.raises(ValueError, match="draw_result 999 不存在"):
        svc.correct(draw_result_id=999, new_front=(1, 2, 3, 4, 5, 6), new_back=(7,), reason="x")
