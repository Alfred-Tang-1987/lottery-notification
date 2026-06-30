"""Plan 05 / T3：current_user / require_admin 依赖测试。"""

from cryptography.fernet import Fernet
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.api.deps import current_user, get_session_dep, require_admin
from app.api.security import COOKIE_NAME, create_session_token
from app.config import reset_settings_cache
from app.models import User


def _set_required_env(monkeypatch):
    monkeypatch.setenv('JWT_SECRET', 'a' * 32)
    monkeypatch.setenv('CRYPTO_KEY_V1', Fernet.generate_key().decode())
    reset_settings_cache()


def _app_with_routes(engine):
    app = FastAPI()
    app.dependency_overrides[get_session_dep] = lambda: Session(engine)

    @app.get('/me')
    def me(u=Depends(current_user)):
        return {'id': u.id, 'role': u.role}

    @app.get('/admin')
    def adm(_u=Depends(require_admin)):
        return {'ok': True}

    return app


def test_current_user_requires_cookie(db_engine, monkeypatch):
    _set_required_env(monkeypatch)
    with Session(db_engine) as s:
        s.add(User(username='u', password_hash='x', role='user', invite_code='C'))
        s.commit()
    app = _app_with_routes(db_engine)
    client = TestClient(app)
    r = client.get('/me')
    assert r.status_code == 401


def test_current_user_with_valid_cookie(db_engine, monkeypatch):
    _set_required_env(monkeypatch)
    with Session(db_engine) as s:
        u = User(username='u', password_hash='x', role='user', invite_code='C')
        s.add(u)
        s.commit()
        s.refresh(u)
        uid = u.id
    app = _app_with_routes(db_engine)
    token = create_session_token(user_id=uid, role='user')
    client = TestClient(app)
    r = client.get('/me', cookies={COOKIE_NAME: token})
    assert r.status_code == 200
    assert r.json()['id'] == uid


def test_admin_required_for_admin_route(db_engine, monkeypatch):
    _set_required_env(monkeypatch)
    with Session(db_engine) as s:
        u = User(username='u', password_hash='x', role='user', invite_code='C')
        s.add(u)
        s.commit()
        s.refresh(u)
        uid = u.id
    app = _app_with_routes(db_engine)
    token = create_session_token(user_id=uid, role='user')
    r = TestClient(app).get('/admin', cookies={COOKIE_NAME: token})
    assert r.status_code == 403


def test_disabled_user_is_rejected(db_engine, monkeypatch):
    _set_required_env(monkeypatch)
    with Session(db_engine) as s:
        u = User(username='u', password_hash='x', role='user', invite_code='C', enabled=False)
        s.add(u)
        s.commit()
        s.refresh(u)
        uid = u.id
    app = _app_with_routes(db_engine)
    token = create_session_token(user_id=uid, role='user')
    r = TestClient(app).get('/me', cookies={COOKIE_NAME: token})
    assert r.status_code == 401


def test_admin_user_can_access_admin_route(db_engine, monkeypatch):
    _set_required_env(monkeypatch)
    with Session(db_engine) as s:
        u = User(username='admin', password_hash='x', role='admin', invite_code='A')
        s.add(u)
        s.commit()
        s.refresh(u)
        uid = u.id
    app = _app_with_routes(db_engine)
    token = create_session_token(user_id=uid, role='admin')
    r = TestClient(app).get('/admin', cookies={COOKIE_NAME: token})
    assert r.status_code == 200
