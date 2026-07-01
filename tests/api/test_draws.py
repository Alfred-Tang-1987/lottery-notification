"""Plan 06 / T6：开奖历史查询 API 测试。"""

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.api.deps import get_session_dep
from app.api.security import COOKIE_NAME, create_session_token
from app.config import reset_settings_cache
from app.main import app
from app.models import DrawResult, LotteryType


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


def test_draws_requires_auth(db_engine):
    """未登录 GET /api/draws → 401。"""
    _seed_ssq(db_engine)
    client = _client(db_engine)
    r = client.get('/api/draws?lottery_code=ssq')
    assert r.status_code == 401


def test_draws_returns_history_ordered_by_draw_no_desc(db_engine):
    """GET /api/draws 返回指定彩种开奖历史，按期号降序。"""
    _seed_ssq(db_engine)
    from datetime import datetime

    with Session(db_engine) as s:
        s.add_all([
            DrawResult(
                lottery_code='ssq',
                draw_no='2026062',
                draw_date=datetime(2026, 6, 1),
                numbers_json='{"front":[1,2,3,4,5,6],"back":[7]}',
                source='mxnzp',
                verified=True,
            ),
            DrawResult(
                lottery_code='ssq',
                draw_no='2026061',
                draw_date=datetime(2026, 5, 29),
                numbers_json='{"front":[7,8,9,10,11,12],"back":[1]}',
                source='mxnzp',
                verified=True,
            ),
        ])
        s.commit()

    from tests.api.test_dashboard import _make_user
    uid = _make_user(db_engine, 'alice')
    client = _auth_client(db_engine, uid)
    r = client.get('/api/draws?lottery_code=ssq')
    assert r.status_code == 200, r.text
    data = r.json()
    assert len(data) == 2
    assert data[0]['draw_no'] == '2026062'
    assert data[1]['draw_no'] == '2026061'
    assert data[0]['lottery_name'] == '双色球'
