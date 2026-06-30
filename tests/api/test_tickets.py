"""Plan 05 / T7：号码池 CRUD API 测试（tickets router）。

Spec §6.3 IDOR：所有号码读写经 ``current_user`` 拿 user_id，复用 Plan 03 TicketRepo
（构造注入 user_id，查询一律 ``WHERE user_id``），用户只能 CRUD 自己的票。

CSRF（spec §4.3）：POST/DELETE 是已登录 state-changing 路由——挂 verify_csrf
double-submit，与 /channels、/auth/logout 模式一致。
"""

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.api.deps import get_session_dep
from app.api.security import COOKIE_NAME, CSRF_HEADER, create_session_token
from app.config import reset_settings_cache
from app.main import app
from app.models import LotteryType, Ticket, User

_CSRF_TOKEN = 'csrf-double-submit-token'


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


def _auth_csrf_client(db_engine, uid: int) -> TestClient:
    """已登录（session cookie）+ 已带 csrf cookie/header 的 TestClient。"""
    client = _client(db_engine)
    client.cookies.set(COOKIE_NAME, create_session_token(user_id=uid, role='user'))
    client.cookies.set('csrf_token', _CSRF_TOKEN)
    client.headers[CSRF_HEADER] = _CSRF_TOKEN
    return client


def _auth_client(db_engine, uid: int) -> TestClient:
    """已登录但无 csrf（用于验证 GET 不需 csrf / 隔离只读）。"""
    client = _client(db_engine)
    client.cookies.set(COOKIE_NAME, create_session_token(user_id=uid, role='user'))
    return client


def _seed_ssq(db_engine):
    """Ticket.lottery_code 是 FK→lottery_types.code，需先种一条彩种行（满足 NOT NULL）。"""
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


_VALID_TICKET = {
    'lottery_code': 'ssq',
    'play_type': 'single',
    'numbers_json': '{"front":[1,2,3,4,5,6],"back":[7]}',
    'cost': 200,
}


def test_create_ticket_persists_and_returns_id(db_engine):
    """POST /tickets：建票落库归属当前用户，返回 id。"""
    _seed_ssq(db_engine)
    uid = _make_user(db_engine, 'alice')
    client = _auth_csrf_client(db_engine, uid)
    r = client.post('/tickets', json=_VALID_TICKET)
    assert r.status_code == 201, r.text
    data = r.json()
    assert 'id' in data
    with Session(db_engine) as s:
        t = s.exec(select(Ticket).where(Ticket.user_id == uid)).first()
        assert t is not None
        assert t.lottery_code == 'ssq'
        assert t.play_type == 'single'


def test_list_tickets_returns_only_own(db_engine):
    """GET /tickets：仅返回当前用户的票（IDOR 隔离）。"""
    _seed_ssq(db_engine)
    u1 = _make_user(db_engine, 'alice')
    u2 = _make_user(db_engine, 'bob')
    c1 = _auth_csrf_client(db_engine, u1)
    c1.post('/tickets', json=_VALID_TICKET)
    # u2 建一张属于自己的票
    c2 = _auth_csrf_client(db_engine, u2)
    c2.post('/tickets', json=_VALID_TICKET)
    # u1 只看到自己的 1 张
    r1 = c1.get('/tickets')
    assert r1.status_code == 200
    assert len(r1.json()) == 1
    # u2 只看到自己的 1 张
    assert len(c2.get('/tickets').json()) == 1


def test_list_tickets_requires_auth(db_engine):
    """未登录访问 /tickets → 401。"""
    client = _client(db_engine)
    assert client.get('/tickets').status_code == 401


def test_delete_ticket_idor_safe(db_engine):
    """DELETE /tickets/{id}：用户删不掉他人的票（IDOR）。"""
    _seed_ssq(db_engine)
    u1 = _make_user(db_engine, 'alice')
    u2 = _make_user(db_engine, 'bob')
    c1 = _auth_csrf_client(db_engine, u1)
    r = c1.post('/tickets', json=_VALID_TICKET)
    tid = r.json()['id']
    # u2 试图删 u1 的票
    c2 = _auth_csrf_client(db_engine, u2)
    r2 = c2.delete(f'/tickets/{tid}')
    assert r2.status_code == 404  # TicketRepo.get 对非归属返回 None → 404
    # u1 的票仍在
    with Session(db_engine) as s:
        assert s.get(Ticket, tid) is not None


def test_delete_own_ticket(db_engine):
    """DELETE /tickets/{id}：删自己的票成功。"""
    _seed_ssq(db_engine)
    uid = _make_user(db_engine, 'alice')
    client = _auth_csrf_client(db_engine, uid)
    tid = client.post('/tickets', json=_VALID_TICKET).json()['id']
    r = client.delete(f'/tickets/{tid}')
    assert r.status_code == 200
    with Session(db_engine) as s:
        assert s.get(Ticket, tid) is None


def test_create_ticket_requires_csrf(db_engine):
    """POST /tickets 无 csrf token → 403（spec §4.3 double-submit）。"""
    _seed_ssq(db_engine)
    uid = _make_user(db_engine, 'alice')
    client = _auth_client(db_engine, uid)  # 已登录但无 csrf
    r = client.post('/tickets', json=_VALID_TICKET)
    assert r.status_code == 403
