"""Plan 06 / T6e：notification rules + DND + 模板预览 API 测试（RED）。"""

import json

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.api.deps import get_session_dep
from app.api.security import COOKIE_NAME, CSRF_HEADER, create_session_token
from app.config import reset_settings_cache
from app.main import app
from app.models import LotteryType, NotificationRule, User


@pytest.fixture(autouse=True)
def _mock_env(monkeypatch):
    reset_settings_cache()
    monkeypatch.setenv('JWT_SECRET', 't' * 32)
    monkeypatch.setenv('CRYPTO_KEY_V1', Fernet.generate_key().decode())
    monkeypatch.setenv('SCHEDULER_ENABLED', 'false')


@pytest.fixture
def db_engine(tmp_path):
    import app.models  # noqa: F401
    from app.db.engine import apply_sqlite_pragmas, build_engine

    eng = build_engine(f'sqlite:///{tmp_path}/test.db')
    apply_sqlite_pragmas(eng)
    from sqlmodel import SQLModel

    SQLModel.metadata.create_all(eng)
    # Seed one lottery type
    with Session(eng) as s:
        s.add(LotteryType(code='ssq', name='双色球', category='welfare', spec_json='{}', draw_schedule_json='{}'))
        s.commit()
    return eng


@pytest.fixture
def client(db_engine):
    def _override():
        with Session(db_engine) as s:
            yield s

    app.dependency_overrides[get_session_dep] = _override
    return TestClient(app)


@pytest.fixture
def auth_client(client, db_engine):
    with Session(db_engine) as s:
        u = User(username='alice', password_hash='x', role='user', invite_code='inv')
        s.add(u)
        s.commit()
        s.refresh(u)
        uid = u.id
    client.cookies.set(COOKIE_NAME, create_session_token(user_id=uid, role='user'))
    client.cookies.set('csrf_token', 'tok')
    client.headers[CSRF_HEADER] = 'tok'
    return client


# ---------------------------------------------------------------------------
# Notification rules
# ---------------------------------------------------------------------------


def test_list_rules_empty(auth_client):
    """GET /channels/rules：无规则时返回空数组。"""
    r = auth_client.get('/channels/rules')
    assert r.status_code == 200, r.text
    assert r.json() == []


def test_save_rule_creates_row(auth_client, db_engine):
    """PUT /channels/rules：写入每彩种策略 + 推送时机。"""
    r = auth_client.put(
        '/channels/rules',
        json={'lottery_code': 'ssq', 'strategy': 'win_only', 'timing': '21:30'},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body['lottery_code'] == 'ssq'
    assert body['strategy'] == 'win_only'
    assert body['timing'] == '21:30'

    with Session(db_engine) as s:
        rows = s.exec(select(NotificationRule)).all()
        assert len(rows) == 1
        assert rows[0].strategy == 'win_only'
        assert rows[0].timing == '21:30'


def test_save_rule_rejects_unknown_strategy(auth_client):
    """策略必须是 every 或 win_only。"""
    r = auth_client.put(
        '/channels/rules',
        json={'lottery_code': 'ssq', 'strategy': 'sometimes'},
    )
    # FastAPI pydantic validation 返回 422（非法 body）。
    assert r.status_code == 422, r.text


def test_update_rule_overwrites_existing(auth_client, db_engine):
    """同一 (user, lottery) 重复 PUT 应更新而非重复。"""
    auth_client.put('/channels/rules', json={'lottery_code': 'ssq', 'strategy': 'every'})
    r = auth_client.put('/channels/rules', json={'lottery_code': 'ssq', 'strategy': 'win_only'})
    assert r.status_code == 200, r.text

    with Session(db_engine) as s:
        rows = s.exec(select(NotificationRule)).all()
        assert len(rows) == 1
        assert rows[0].strategy == 'win_only'


def test_delete_rule(auth_client, db_engine):
    """DELETE /channels/rules/{id}：删除当前用户的规则。"""
    r = auth_client.put('/channels/rules', json={'lottery_code': 'ssq', 'strategy': 'every'})
    rid = r.json()['id']

    dr = auth_client.delete(f'/channels/rules/{rid}')
    assert dr.status_code == 200, dr.text

    with Session(db_engine) as s:
        assert s.get(NotificationRule, rid) is None


# ---------------------------------------------------------------------------
# DND
# ---------------------------------------------------------------------------


def test_dnd_roundtrip(auth_client, db_engine):
    """POST /channels/dnd → GET /channels/dnd 持久化往返。"""
    r = auth_client.post(
        '/channels/dnd',
        json={'enabled': True, 'start': '23:00', 'end': '07:00'},
    )
    assert r.status_code == 200, r.text

    g = auth_client.get('/channels/dnd')
    assert g.status_code == 200, g.text
    body = g.json()
    assert body['enabled'] is True
    assert body['start'] == '23:00'
    assert body['end'] == '07:00'

    with Session(db_engine) as s:
        u = s.get(User, 1)
        dnd = json.loads(u.dnd_json)
        assert dnd['enabled'] is True


def test_dnd_rejects_invalid_time(auth_client):
    """时间格式错误应 422（pydantic 校验失败）。"""
    r = auth_client.post('/channels/dnd', json={'enabled': True, 'start': '25:00', 'end': '07:00'})
    assert r.status_code == 422, r.text


# ---------------------------------------------------------------------------
# Template preview
# ---------------------------------------------------------------------------


def test_template_preview_returns_path_a_and_b(auth_client):
    """GET /channels/templates 返回路径 A/B 示例文案。"""
    r = auth_client.get('/channels/templates')
    assert r.status_code == 200, r.text
    body = r.json()
    assert 'path_a' in body
    assert 'path_b' in body
    assert '恭喜中奖' in body['path_a']['title']
