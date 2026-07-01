"""Plan 06 / T6：Dashboard API 聚合测试。

Spec §12.2：仪表盘首屏聚合「待兑奖 / 我的命中 / 盈亏速览 / 开奖概览」。
/api/dashboard 返回当前用户的数据快照，供 Dashboard.vue 消费。
"""

from datetime import datetime, timedelta

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
    """注入有效 env（CRYPTO_KEY_V1 须是真实 Fernet key）。"""
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
    """已登录（session cookie）的 TestClient；GET 无需 csrf。"""
    client = _client(db_engine)
    client.cookies.set(COOKIE_NAME, create_session_token(user_id=uid, role='user'))
    return client


def _seed_lottery(db_engine):
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


def test_dashboard_requires_auth(db_engine):
    """未登录 GET /api/dashboard → 401。"""
    client = _client(db_engine)
    r = client.get('/api/dashboard')
    assert r.status_code == 401


def test_dashboard_returns_aggregated_snapshot(db_engine):
    """GET /api/dashboard 返回当前用户的聚合数据快照。"""
    _seed_lottery(db_engine)
    uid = _make_user(db_engine, 'alice')

    draw_date = datetime.utcnow() - timedelta(days=1)
    deadline = datetime.utcnow() + timedelta(days=59)

    with Session(db_engine) as s:
        draw = DrawResult(
            lottery_code='ssq',
            draw_no='2026062',
            draw_date=draw_date,
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
            deadline=deadline,
        )
        s.add(claim)
        s.commit()

    client = _auth_client(db_engine, uid)
    r = client.get('/api/dashboard')
    assert r.status_code == 200, r.text
    data = r.json()

    # 开奖概览
    assert 'latest_draws' in data
    draws = data['latest_draws']
    assert len(draws) >= 1
    assert draws[0]['lottery_code'] == 'ssq'
    assert draws[0]['draw_no'] == '2026062'

    # 待兑奖
    assert 'pending_claims' in data
    claims = data['pending_claims']
    assert len(claims) == 1
    assert claims[0]['status'] == 'pending'
    assert claims[0]['prize_tier'] == 1

    # 盈亏摘要
    assert 'summary' in data
    summary = data['summary']
    assert summary['total_cost'] == 200
    assert summary['ticket_count'] == 1
