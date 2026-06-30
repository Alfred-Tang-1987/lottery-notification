"""Plan 05 / T6：admin 后台 + force-verify + 审计日志测试。"""

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.api.deps import get_session_dep
from app.api.security import COOKIE_NAME, create_session_token
from app.config import reset_settings_cache
from app.main import app
from app.models import User


def _set_required_env(monkeypatch):
    monkeypatch.setenv('JWT_SECRET', 'a' * 32)
    monkeypatch.setenv('CRYPTO_KEY_V1', Fernet.generate_key().decode())
    reset_settings_cache()


def _admin_client(db_engine, monkeypatch):
    _set_required_env(monkeypatch)
    with Session(db_engine) as s:
        u = User(username='admin', password_hash='x', role='admin', invite_code='A')
        s.add(u)
        s.commit()
        s.refresh(u)
        uid = u.id
    app.dependency_overrides[get_session_dep] = lambda: (yield Session(db_engine))
    client = TestClient(app)
    client.cookies.set(COOKIE_NAME, create_session_token(user_id=uid, role='admin'))
    return client


def test_admin_list_users(db_engine, monkeypatch):
    with Session(db_engine) as s:
        s.add(User(username='u1', password_hash='x', role='user', invite_code='C'))
        s.commit()
    client = _admin_client(db_engine, monkeypatch)
    r = client.get('/admin/users')
    assert r.status_code == 200
    assert len(r.json()) >= 2
