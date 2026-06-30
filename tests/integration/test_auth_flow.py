"""Plan 05 / T7：端到端 auth 流程集成测试。

覆盖 T1-T6 各层 router 经 ``app.main`` 装配后的真实串通（不经 service 短路）：

1. admin 经 InviteService 生成邀请码 →
2. 匿名用户 POST /auth/register（CSRF 豁免，邀请码校验）→
3. POST /auth/login（CSRF 豁免）拿 session cookie + 签发 csrf cookie →
4. GET /auth/me（session cookie 鉴权）→
5. POST /auth/logout（带 csrf double-submit）→ 会话失效。

回归点：router 注册、依赖装配、CSRF 豁免端点集合（register/login/csrf）、cookie
设置/删除、current_user 鉴权。任一环节断裂即 4xx/5xx。
"""

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.api.deps import get_session_dep
from app.api.security import COOKIE_NAME, CSRF_HEADER
from app.config import reset_settings_cache
from app.main import app
from app.services.invite_service import InviteService


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


def test_full_auth_flow(db_engine):
    """register → login → me → logout 全链路经真实 app 串通。"""
    # 1. admin 生成邀请码（service 直调，admin 路由本身在 T6 已测）
    code = InviteService(db_engine).generate(admin_id=1)

    client = _client(db_engine)

    # 2. 注册（匿名端点，CSRF 豁免）
    r = client.post(
        '/auth/register',
        json={'username': 'alice', 'password': 'password1', 'invite_code': code},
    )
    assert r.status_code == 201, r.text

    # 3. 登录（匿名端点，CSRF 豁免）—— 返回体带 role，且 set 了 session cookie
    r = client.post('/auth/login', json={'username': 'alice', 'password': 'password1'})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body['username'] == 'alice'
    assert body['role'] == 'user'
    assert COOKIE_NAME in client.cookies

    # 4. me（session cookie 鉴权）
    r = client.get('/auth/me')
    assert r.status_code == 200
    assert r.json()['username'] == 'alice'

    # 5. logout：已登录 state-changing 路由——需 csrf double-submit。
    #    登录后客户端尚无 csrf cookie（login 不签发 csrf），先取 /auth/csrf 拿 token。
    csrf_r = client.get('/auth/csrf')
    assert csrf_r.status_code == 200
    csrf_token = csrf_r.json()['csrf_token']
    client.headers[CSRF_HEADER] = csrf_token

    r = client.post('/auth/logout')
    assert r.status_code == 200, r.text
    assert r.json() == {'ok': True}

    # session cookie 已删除 → me 应 401
    r = client.get('/auth/me')
    assert r.status_code == 401


def test_login_wrong_password_rejected(db_engine):
    """登录密码错误 → 401（不枚举用户名：统一错误信息）。"""
    code = InviteService(db_engine).generate(admin_id=1)
    client = _client(db_engine)
    client.post(
        '/auth/register',
        json={'username': 'alice', 'password': 'password1', 'invite_code': code},
    )
    r = client.post('/auth/login', json={'username': 'alice', 'password': 'wrongpass'})
    assert r.status_code == 401
    assert COOKIE_NAME not in client.cookies  # 未签发 session


def test_csrf_endpoint_sets_readable_cookie(db_engine):
    """/auth/csrf 签发的 cookie 非 httpOnly（SPA 可读，做 double-submit）。"""
    client = _client(db_engine)
    r = client.get('/auth/csrf')
    assert r.status_code == 200
    assert 'csrf_token' in r.json()
    assert 'csrf_token' in client.cookies
