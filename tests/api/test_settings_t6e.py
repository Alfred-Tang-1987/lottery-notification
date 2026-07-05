"""Plan 06 / T6e：notification rules + DND + preferences + 模板预览 API 测试（RED）。"""

import json

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.api.deps import get_session_dep
from app.api.security import COOKIE_NAME, CSRF_HEADER, create_session_token
from app.config import reset_settings_cache
from app.infrastructure.crypto import CryptoService
from app.main import app
from app.models import LotteryType, NotificationRule, NotificationChannel, NotificationSettings, User


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
    yield eng
    # Explicit cleanup: dispose engine so tmp_path can be freed reliably.
    eng.dispose()


@pytest.fixture
def client(db_engine):
    def _override():
        with Session(db_engine) as s:
            yield s

    app.dependency_overrides[get_session_dep] = _override
    yield TestClient(app)
    # Clear dependency overrides so this fixture cannot leak into later tests.
    app.dependency_overrides = {}


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


@pytest.fixture
def other_user_client(client, db_engine):
    """A second authenticated user to test IDOR boundaries."""
    with Session(db_engine) as s:
        u = User(username='bob', password_hash='x', role='user', invite_code='inv2')
        s.add(u)
        s.commit()
        s.refresh(u)
        uid = u.id
    other = TestClient(app)
    other.cookies.set(COOKIE_NAME, create_session_token(user_id=uid, role='user'))
    other.cookies.set('csrf_token', 'tok')
    other.headers[CSRF_HEADER] = 'tok'
    return other


# ---------------------------------------------------------------------------
# Notification rules
# ---------------------------------------------------------------------------


def test_list_rules_empty(auth_client):
    """GET /channels/rules：无规则时返回空数组。"""
    r = auth_client.get('/channels/rules')
    assert r.status_code == 200, r.text
    assert r.json() == []


def test_save_rule_creates_row(auth_client, db_engine):
    """PUT /channels/rules：写入每彩种策略。全局开关已在 notification_settings 中。"""
    r = auth_client.put(
        '/channels/rules',
        json={
            'lottery_code': 'ssq',
            'strategy': 'win_only',
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body['lottery_code'] == 'ssq'
    assert body['strategy'] == 'win_only'

    with Session(db_engine) as s:
        rows = s.exec(select(NotificationRule)).all()
        assert len(rows) == 1
        assert rows[0].strategy == 'win_only'


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
    r = auth_client.put(
        '/channels/rules',
        json={'lottery_code': 'ssq', 'strategy': 'win_only'},
    )
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


def test_delete_rule_other_user_returns_404(other_user_client, auth_client, db_engine):
    """用户不能删除其他用户的规则。"""
    r = auth_client.put('/channels/rules', json={'lottery_code': 'ssq', 'strategy': 'every'})
    rid = r.json()['id']

    dr = other_user_client.delete(f'/channels/rules/{rid}')
    assert dr.status_code == 404, dr.text

    with Session(db_engine) as s:
        assert s.get(NotificationRule, rid) is not None


def test_delete_rule_missing_returns_404(auth_client):
    """删除不存在的规则返回 404。"""
    r = auth_client.delete('/channels/rules/9999')
    assert r.status_code == 404, r.text


# ---------------------------------------------------------------------------
# Global notification settings
# ---------------------------------------------------------------------------


def test_notification_settings_roundtrip(auth_client, db_engine):
    """GET/PUT /channels/settings：读写 per-user 全局通知设置。"""
    r = auth_client.put(
        '/channels/settings',
        json={
            'master_enable': False,
            'path_a_enable': False,
            'summary_time': '21:30',
            'new_numbers_default_enabled': False,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body['master_enable'] is False
    assert body['path_a_enable'] is False
    assert body['summary_time'] == '21:30'
    assert body['new_numbers_default_enabled'] is False

    g = auth_client.get('/channels/settings')
    assert g.status_code == 200, g.text
    assert g.json() == body

    with Session(db_engine) as s:
        u = s.get(User, 1)
        settings = s.get(NotificationSettings, u.id)
        assert settings is not None
        assert settings.master_enable is False
        assert settings.summary_time == '21:30'


def test_notification_settings_rejects_bad_summary_time(auth_client):
    """summary_time 必须是 HH:MM 或空。"""
    r = auth_client.put('/channels/settings', json={'summary_time': '25:00'})
    assert r.status_code == 422, r.text

    r2 = auth_client.put('/channels/settings', json={'summary_time': 'not-a-time'})
    assert r2.status_code == 422, r2.text

    r3 = auth_client.put('/channels/settings', json={'summary_time': ''})
    assert r3.status_code == 200, r3.text
    assert r3.json()['summary_time'] is None


def test_notification_settings_isolated_between_users(auth_client, other_user_client):
    """用户 A 的设置不影响用户 B。"""
    auth_client.put(
        '/channels/settings',
        json={'master_enable': False, 'summary_time': '09:00'},
    )
    r = other_user_client.get('/channels/settings')
    assert r.status_code == 200, r.text
    body = r.json()
    assert body['master_enable'] is True
    assert body['summary_time'] is None


def test_notification_settings_idempotent_defaults(auth_client):
    """未设置过时应返回合理默认值。"""
    r = auth_client.get('/channels/settings')
    assert r.status_code == 200, r.text
    body = r.json()
    assert body['master_enable'] is True
    assert body['path_a_enable'] is True
    assert body['summary_time'] is None
    assert body['new_numbers_default_enabled'] is True


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


def test_dnd_corrupt_json_returns_default(auth_client, db_engine):
    """损坏的 dnd_json 应回退默认并打日志，不抛异常。"""
    with Session(db_engine) as s:
        u = s.get(User, 1)
        u.dnd_json = 'not-json'
        s.add(u)
        s.commit()

    g = auth_client.get('/channels/dnd')
    assert g.status_code == 200, g.text
    body = g.json()
    assert body['enabled'] is False
    assert body['start'] == '22:00'
    assert body['end'] == '07:00'


def test_dnd_wrong_value_types_returns_default(auth_client, db_engine):
    """_parse_dnd 对损坏 dict 的值做类型校验，失败回退默认。"""
    with Session(db_engine) as s:
        u = s.get(User, 1)
        u.dnd_json = json.dumps({'enabled': 'yes', 'start': None, 'end': 99})
        s.add(u)
        s.commit()

    g = auth_client.get('/channels/dnd')
    assert g.status_code == 200, g.text
    body = g.json()
    assert body['enabled'] is False
    assert body['start'] == '22:00'
    assert body['end'] == '07:00'


def test_dnd_invalid_time_string_returns_default(auth_client, db_engine):
    """DND 时间字符串不是 HH:MM 时回退默认。"""
    with Session(db_engine) as s:
        u = s.get(User, 1)
        u.dnd_json = json.dumps({'enabled': True, 'start': '25:00', 'end': '07:00'})
        s.add(u)
        s.commit()

    g = auth_client.get('/channels/dnd')
    assert g.status_code == 200, g.text
    body = g.json()
    assert body['enabled'] is False
    assert body['start'] == '22:00'
    assert body['end'] == '07:00'


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------


def test_preferences_roundtrip(auth_client, db_engine):
    """POST /channels/preferences → GET /channels/preferences 持久化往返。"""
    r = auth_client.post(
        '/channels/preferences',
        json={'theme': 'dark'},
    )
    assert r.status_code == 200, r.text
    assert r.json()['theme'] == 'dark'

    g = auth_client.get('/channels/preferences')
    assert g.status_code == 200, g.text
    assert g.json() == {'theme': 'dark'}

    with Session(db_engine) as s:
        u = s.get(User, 1)
        prefs = json.loads(u.preferences_json)
        assert prefs['theme'] == 'dark'


def test_preferences_rejects_bad_theme(auth_client):
    """theme 必须是 light/dark/auto 之一。"""
    r = auth_client.post('/channels/preferences', json={'theme': 'neon'})
    assert r.status_code == 422, r.text


def test_preferences_string_false_returns_default(auth_client, db_engine):
    """字符串 'false' 在 preferences 中不再控制新号码默认启用；移入全局设置。"""
    with Session(db_engine) as s:
        u = s.get(User, 1)
        u.preferences_json = json.dumps({'new_numbers_default_enabled': 'false'})
        s.add(u)
        s.commit()

    g = auth_client.get('/channels/preferences')
    assert g.status_code == 200, g.text
    body = g.json()
    assert body['theme'] == 'auto'


def test_preferences_corrupt_returns_default(auth_client, db_engine):
    """损坏的 preferences_json 回退默认主题。"""
    with Session(db_engine) as s:
        u = s.get(User, 1)
        u.preferences_json = 'not-json'
        s.add(u)
        s.commit()

    g = auth_client.get('/channels/preferences')
    assert g.status_code == 200, g.text
    body = g.json()
    assert body['theme'] == 'auto'


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
    assert 'title' in body['path_a']
    assert 'body' in body['path_a']


# ---------------------------------------------------------------------------
# Channel list decrypt failures
# ---------------------------------------------------------------------------


def test_list_channels_decrypt_failure_exposes_header(auth_client, db_engine, monkeypatch):
    """渠道解密失败应通过响应头暴露失败 id，而不是静默跳过。"""
    from app.config import get_settings

    crypto = CryptoService(get_settings().crypto_keys, get_settings().current_key_version)
    plaintext = json.dumps({'key': 'abc'})
    blob = crypto.encrypt(plaintext)
    stored = json.dumps({'ct': blob.ciphertext})

    with Session(db_engine) as s:
        u = s.get(User, 1)
        ch = NotificationChannel(
            user_id=u.id,
            type='bark',
            config_json=stored,
            enabled=True,
            key_version=blob.version,
        )
        s.add(ch)
        s.commit()
        ch_id = ch.id

    new_key = Fernet.generate_key().decode()
    monkeypatch.setenv('CRYPTO_KEY_V1', new_key)
    reset_settings_cache()

    r = auth_client.get('/channels')
    assert r.status_code == 200, r.text
    assert r.headers.get('X-Channel-Decrypt-Failed') == str(ch_id)
