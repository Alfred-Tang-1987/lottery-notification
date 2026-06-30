"""Plan 05 / T4：auth API（register/login/logout/csrf/me）测试。

Spec §4.3 / §6.1 / §6.2：httpOnly cookie + SameSite + CSRF token（/auth/csrf GET）；
注册需有效邀请码（单次/有效期/锁定，§6.2）；密码 >= 8 字符。
"""

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.api.deps import get_session_dep
from app.config import reset_settings_cache
from app.main import app
from app.services.invite_service import InviteService


@pytest.fixture(autouse=True)
def _mock_env(monkeypatch):
    """注入有效 env + 关闭 scheduler，避免 app.main lifespan 校验/抓取副作用。

    与 tests/test_health.py 同源：app.main:app 的 lifespan 会 validate_startup
    （需要 JWT_SECRET/CRYPTO_KEY）并可能 run_startup_backfill（真实抓取）。
    """
    reset_settings_cache()
    monkeypatch.setenv('JWT_SECRET', 't' * 32)
    monkeypatch.setenv('CRYPTO_KEY_V1', Fernet.generate_key().decode())
    monkeypatch.setenv('SCHEDULER_ENABLED', 'false')


def _client(db_engine):
    """注入测试 engine 作为 get_session_dep 的依赖，返回 TestClient。"""

    def _override():
        with Session(db_engine) as s:
            yield s

    app.dependency_overrides[get_session_dep] = _override
    return TestClient(app)


def test_register_login_logout_flow(db_engine):
    # admin 先生成邀请码（直接用 service）
    invite = InviteService(db_engine)
    code = invite.generate(admin_id=1)
    client = _client(db_engine)
    try:
        r = client.post('/auth/register', json={'username': 'alice', 'password': 'password1', 'invite_code': code})
        assert r.status_code == 201
        # 登录
        r = client.post('/auth/login', json={'username': 'alice', 'password': 'password1'})
        assert r.status_code == 200
        assert 'session' in r.cookies
        # me
        r = client.get('/auth/me')
        assert r.status_code == 200
        assert r.json()['username'] == 'alice'
        # logout 是已登录 state-changing 路由——需先取 CSRF token 并以
        # X-CSRF-Token header 回传（spec §4.3 double-submit，模拟 SPA 行为）。
        r = client.get('/auth/csrf')
        csrf_token = r.json()['csrf_token']
        r = client.post('/auth/logout', headers={'X-CSRF-Token': csrf_token})
        assert r.status_code == 200
        r = client.get('/auth/me')
        assert r.status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_register_invalid_invite_rejected(db_engine):
    client = _client(db_engine)
    try:
        r = client.post('/auth/register', json={'username': 'bob', 'password': 'password1', 'invite_code': '000000'})
        assert r.status_code == 400
    finally:
        app.dependency_overrides.clear()


def test_register_invalid_invite_does_not_create_user(db_engine):
    """注册被拒（邀请码无效）不得落库半截用户（防 silent-failure / 用户名占位）。"""
    client = _client(db_engine)
    try:
        r = client.post('/auth/register', json={'username': 'bob', 'password': 'password1', 'invite_code': '000000'})
        assert r.status_code == 400
        # bob 不应被建出来——否则后续合法注册同 username 会误判冲突。
        from sqlmodel import select as _select

        from app.models import User as _User

        with Session(db_engine) as s:
            assert s.exec(_select(_User).where(_User.username == 'bob')).first() is None
    finally:
        app.dependency_overrides.clear()


def test_register_weak_password_rejected(db_engine):
    invite = InviteService(db_engine)
    code = invite.generate(admin_id=1)
    client = _client(db_engine)
    try:
        # 密码 < 8 → pydantic 422
        r = client.post('/auth/register', json={'username': 'carol', 'password': '123', 'invite_code': code})
        assert r.status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_register_consumes_invite_single_use(db_engine):
    """邀请码注册后被占用，第二次用同码注册应失败（§6.2 单次使用）。"""
    invite = InviteService(db_engine)
    code = invite.generate(admin_id=1)
    client = _client(db_engine)
    try:
        r = client.post('/auth/register', json={'username': 'dave', 'password': 'password1', 'invite_code': code})
        assert r.status_code == 201
        # 同码再注册另一用户 → 失败（码已用）
        r = client.post('/auth/register', json={'username': 'erin', 'password': 'password1', 'invite_code': code})
        assert r.status_code == 400
    finally:
        app.dependency_overrides.clear()


def test_register_duplicate_username_conflict(db_engine):
    """用户名已存在 → 409（即使持有有效未用邀请码）。"""
    invite = InviteService(db_engine)
    code1 = invite.generate(admin_id=1)
    code2 = invite.generate(admin_id=1)
    client = _client(db_engine)
    try:
        r = client.post('/auth/register', json={'username': 'frank', 'password': 'password1', 'invite_code': code1})
        assert r.status_code == 201
        r = client.post('/auth/register', json={'username': 'frank', 'password': 'password1', 'invite_code': code2})
        assert r.status_code == 409
    finally:
        app.dependency_overrides.clear()


def test_login_wrong_password_rejected(db_engine):
    invite = InviteService(db_engine)
    code = invite.generate(admin_id=1)
    client = _client(db_engine)
    try:
        client.post('/auth/register', json={'username': 'grace', 'password': 'password1', 'invite_code': code})
        r = client.post('/auth/login', json={'username': 'grace', 'password': 'wrongpass'})
        assert r.status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_me_without_cookie_unauthorized(db_engine):
    client = _client(db_engine)
    try:
        r = client.get('/auth/me')
        assert r.status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_csrf_endpoint_returns_token():
    """GET /auth/csrf 返回 token 且写入非 httpOnly cookie 供 SPA 读取（spec §4.3）。"""
    client = TestClient(app)
    r = client.get('/auth/csrf')
    assert r.status_code == 200
    assert 'csrf_token' in r.json()
    assert len(r.json()['csrf_token']) > 0
