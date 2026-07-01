"""Plan 06 / T6：比对记录 API 测试（中奖记录）。"""

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.api.deps import get_session_dep
from app.api.security import COOKIE_NAME, create_session_token
from app.config import reset_settings_cache
from app.main import app
from app.models import Comparison, DrawResult, LotteryType, PrizeClaim, Ticket, User


@pytest.fixture(autouse=True)
def _mock_env(monkeypatch):
    reset_settings_cache()
    monkeypatch.setenv('JWT_SECRET', 't' * 32)
    monkeypatch.setenv('CRYPTO_KEY_V1', Fernet.generate_key().decode())
    monkeypatch.setenv('SCHEDULER_ENABLED', 'false')


def _client(db_engine):
    def _override():
        with Session(db_engine) as s:
            yield s

    app.dependency_overrides[get_session_dep] = _override
    return TestClient(app)


def _auth_client(db_engine, uid: int) -> TestClient:
    client = _client(db_engine)
    client.cookies.set(COOKIE_NAME, create_session_token(user_id=uid, role='user'))
    return client


def _seed_ssq(db_engine):
    with Session(db_engine) as s:
        s.add(
            LotteryType(
                code='ssq',
                name='双色球',
                category='welfare',
                spec_json='{}',
                draw_schedule_json='{}',
            )
        )
        s.commit()


def _make_user(db_engine, username: str) -> int:
    with Session(db_engine) as s:
        u = User(username=username, password_hash='x', role='user', invite_code=username)
        s.add(u)
        s.commit()
        s.refresh(u)
        return u.id


def test_comparisons_requires_auth(db_engine):
    """未登录 GET /api/comparisons → 401。"""
    _seed_ssq(db_engine)
    client = _client(db_engine)
    r = client.get('/api/comparisons')
    assert r.status_code == 401


def test_comparisons_returns_user_wins(db_engine):
    """GET /api/comparisons 返回当前用户的比对记录（含兑奖状态）。"""
    _seed_ssq(db_engine)
    from datetime import datetime, timedelta

    uid = _make_user(db_engine, 'alice')
    with Session(db_engine) as s:
        draw = DrawResult(
            lottery_code='ssq',
            draw_no='2026062',
            draw_date=datetime.utcnow() - timedelta(days=1),
            numbers_json='{"front":[1,2,3,4,5,6],"back":[7]}',
            source='mxnzp',
            verified=True,
        )
        s.add(draw)
        s.flush()
        ticket = Ticket(
            user_id=uid,
            lottery_code='ssq',
            play_type='single',
            numbers_json='{"front":[1,2,3,4,5,6],"back":[7]}',
            cost=200,
        )
        s.add(ticket)
        s.flush()
        comparison = Comparison(
            user_id=uid,
            draw_result_id=draw.id,
            ticket_id=ticket.id,
            hits_json='{"front_hit":6,"back_hit":1}',
            prize_tier=1,
            prize_amount=None,
            is_win=True,
        )
        s.add(comparison)
        s.flush()
        claim = PrizeClaim(
            comparison_id=comparison.id,
            status='pending',
            deadline=datetime.utcnow() + timedelta(days=59),
        )
        s.add(claim)
        s.commit()

    client = _auth_client(db_engine, uid)
    r = client.get('/api/comparisons')
    assert r.status_code == 200, r.text
    data = r.json()
    assert len(data) == 1
    assert data[0]['is_win'] is True
    assert data[0]['prize_tier'] == 1
    assert data[0]['claim_status'] == 'pending'
    assert data[0]['lottery_name'] == '双色球'
