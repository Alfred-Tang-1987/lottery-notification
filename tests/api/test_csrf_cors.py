"""Plan 05 / T4：CSRF 端点 + CORS 中间件测试。

Spec §4.3：CORS allow_credentials=True + 显式 origins（禁用通配符）；
CSRF token 经 /auth/csrf GET 获取并 set 到非 httpOnly cookie 供 SPA 读取。

注意：CORSMiddleware 在 app.main 导入时即用 env CORS_ORIGINS 构造（origins 固定
为启动期快照，运行时改 env 不影响已构造的中间件——生产同源/反代场景 origins 来自
.env 启动注入）。故此处针对 import-time 默认白名单（含 localhost:5173）断言行为。
"""

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.config import reset_settings_cache
from app.main import app

# 默认开发白名单（与 app.main._read_cors_origins 缺省值一致）。
_DEV_ORIGIN = 'http://localhost:5173'


@pytest.fixture(autouse=True)
def _mock_env(monkeypatch):
    """注入有效 env + 关闭 scheduler，避免 app.main lifespan 副作用。"""
    reset_settings_cache()
    monkeypatch.setenv('JWT_SECRET', 't' * 32)
    monkeypatch.setenv('CRYPTO_KEY_V1', Fernet.generate_key().decode())
    monkeypatch.setenv('SCHEDULER_ENABLED', 'false')


def test_csrf_endpoint_sets_non_httponly_cookie():
    """CSRF token 必须写入非 httpOnly cookie（SPA 读不到 httpOnly session cookie，
    靠这个 cookie + X-CSRF-Token header 做 double-submit，spec §4.3）。"""
    client = TestClient(app)
    r = client.get('/auth/csrf')
    assert r.status_code == 200
    body = r.json()
    assert 'csrf_token' in body
    assert len(body['csrf_token']) > 0
    # cookie 也应被 set
    assert 'csrf_token' in r.cookies


def test_cors_credentials_header():
    """CORS 预检：白名单 origin 应得 allow_credentials=true 且被 echo（spec §4.3）。"""
    client = TestClient(app)
    r = client.options(
        '/auth/login',
        headers={
            'Origin': _DEV_ORIGIN,
            'Access-Control-Request-Method': 'POST',
        },
    )
    assert r.headers.get('access-control-allow-credentials') == 'true'
    assert _DEV_ORIGIN in r.headers.get('access-control-allow-origin', '')


def test_cors_rejects_unlisted_origin():
    """非白名单 origin 不应被 echo（spec §4.3 禁用通配符）。"""
    client = TestClient(app)
    r = client.options(
        '/auth/login',
        headers={
            'Origin': 'http://evil.example.com',
            'Access-Control-Request-Method': 'POST',
        },
    )
    allow_origin = r.headers.get('access-control-allow-origin', '')
    assert 'evil.example.com' not in allow_origin


# ---------------------------------------------------------------------------
# CSRF double-submit enforcement（spec §4.3）
#
# /auth/csrf 签发非 httpOnly csrf_token cookie；状态变更请求（POST/PUT/PATCH/DELETE）
# 必须携带 X-CSRF-Token header 且与 cookie 一致才放行——否则 /auth/csrf 只是装饰
# 性端点，SameSite=Lax 仅是部分缓解。/auth/login /auth/register 豁免：首次请求时
# 浏览器还没有 csrf cookie。
# ---------------------------------------------------------------------------
from app.api.security import COOKIE_NAME, CSRF_HEADER, create_session_token  # noqa: E402


def _logged_in_client(db_engine, uid: int) -> TestClient:
    """已登录的 TestClient（带 session cookie），用于测 state-changing 路由。"""
    client = TestClient(app)
    token = create_session_token(user_id=uid, role='user')
    client.cookies.set(COOKIE_NAME, token)
    return client


def test_logout_rejected_without_csrf_token(db_engine):
    """已登录用户的 state-changing 请求（/auth/logout）不带 CSRF token → 403。

    /auth/csrf 端点签发的 token 必须被实际校验，否则 double-submit 约定形同虚设。
    """
    client = _logged_in_client(db_engine, uid=1)
    r = client.post('/auth/logout')
    assert r.status_code == 403


def test_logout_rejected_when_csrf_token_mismatch(db_engine):
    """X-CSRF-Token header 与 csrf_token cookie 不一致 → 403（防伪造）。"""
    client = _logged_in_client(db_engine, uid=1)
    client.cookies.set('csrf_token', 'cookie-value')
    r = client.post('/auth/logout', headers={CSRF_HEADER: 'different-header-value'})
    assert r.status_code == 403


def test_logout_allowed_when_csrf_token_matches(db_engine):
    """X-CSRF-Token header 与 csrf_token cookie 一致 → 放行。"""
    client = _logged_in_client(db_engine, uid=1)
    client.cookies.set('csrf_token', 'matching-token')
    r = client.post('/auth/logout', headers={CSRF_HEADER: 'matching-token'})
    assert r.status_code == 200


def test_login_exempt_from_csrf_check(db_engine):
    """/auth/login 豁免 CSRF：首次登录尚无 csrf cookie（spec §4.3）。

    这是豁免的关键理由——register/login 是匿名进入端点，强制 CSRF 会形成鸡生蛋
    死锁。其余已登录 state-changing 路由均须校验。
    """
    client = TestClient(app)
    # 不带任何 csrf cookie/header 也能打到 401（用户名/密码错），而非 403（CSRF）
    r = client.post('/auth/login', json={'username': 'nobody', 'password': 'x' * 8})
    assert r.status_code != 403
