"""管理员后台重置密码测试（Plan 08 / T5，spec §3.7）。

未配 email 渠道用户的兜底路径。对齐 test_admin.py 已验证 pattern：
seed 独立 Session → admin client（cookie + CSRF header）→ HTTP → 新 Session 验证。
"""

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.api.deps import get_session_dep
from app.api.security import (
    COOKIE_NAME,
    CSRF_HEADER,
    create_session_token,
    generate_csrf_token,
    hash_password,
    verify_password,
)
from app.config import reset_settings_cache
from app.main import app
from app.models import AdminAuditLog, User


def _set_env(monkeypatch):
    monkeypatch.setenv('JWT_SECRET', 'a' * 32)
    monkeypatch.setenv('CRYPTO_KEY_V1', Fernet.generate_key().decode())
    reset_settings_cache()


def _admin_client(db_engine, monkeypatch):
    _set_env(monkeypatch)
    with Session(db_engine) as s:
        s.add(User(username='admin', password_hash='x', role='admin', invite_code='A'))
        s.commit()
        uid = s.exec(select(User).where(User.username == 'admin')).first().id
    app.dependency_overrides[get_session_dep] = lambda: (yield Session(db_engine))
    client = TestClient(app)
    client.cookies.set(COOKIE_NAME, create_session_token(user_id=uid, role='admin'))
    csrf = generate_csrf_token()
    client.cookies.set('csrf_token', csrf)
    client.headers[CSRF_HEADER] = csrf
    return client


def _seed_user(db_engine, username='bob'):
    with Session(db_engine) as s:
        s.add(User(username=username, password_hash=hash_password('oldpass123'),
                   role='user', invite_code=username))
        s.commit()
        return s.exec(select(User).where(User.username == username)).first().id


def test_admin_reset_password_success(db_engine, monkeypatch):
    uid = _seed_user(db_engine)
    client = _admin_client(db_engine, monkeypatch)
    try:
        r = client.post(f'/admin/users/{uid}/reset-password',
                        json={'new_password': 'newpass456'})
        assert r.status_code == 200
        assert r.json()['username'] == 'bob'
        with Session(db_engine) as s:
            assert verify_password('newpass456', s.get(User, uid).password_hash)
            log = s.exec(
                select(AdminAuditLog).where(AdminAuditLog.action == 'reset_password')
            ).first()
            assert log is not None
            assert log.target_id == str(uid)
            assert 'newpass456' not in (log.new_values or '')  # 密码不入审计
    finally:
        app.dependency_overrides.clear()


def test_admin_reset_password_user_not_found(db_engine, monkeypatch):
    client = _admin_client(db_engine, monkeypatch)
    try:
        r = client.post('/admin/users/9999/reset-password',
                        json={'new_password': 'newpass456'})
        assert r.status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_admin_reset_password_without_csrf_403(db_engine, monkeypatch):
    uid = _seed_user(db_engine)
    _set_env(monkeypatch)
    with Session(db_engine) as s:
        s.add(User(username='admin', password_hash='x', role='admin', invite_code='A'))
        s.commit()
        aid = s.exec(select(User).where(User.username == 'admin')).first().id
    app.dependency_overrides[get_session_dep] = lambda: (yield Session(db_engine))
    client = TestClient(app)
    client.cookies.set(COOKIE_NAME, create_session_token(user_id=aid, role='admin'))
    try:
        r = client.post(f'/admin/users/{uid}/reset-password',
                        json={'new_password': 'newpass456'})
        assert r.status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_admin_reset_password_non_admin_403(db_engine, monkeypatch):
    uid = _seed_user(db_engine)
    _set_env(monkeypatch)
    app.dependency_overrides[get_session_dep] = lambda: (yield Session(db_engine))
    client = TestClient(app)
    client.cookies.set(COOKIE_NAME, create_session_token(user_id=uid, role='user'))
    csrf = generate_csrf_token()
    client.cookies.set('csrf_token', csrf)
    client.headers[CSRF_HEADER] = csrf
    try:
        r = client.post(f'/admin/users/{uid}/reset-password',
                        json={'new_password': 'newpass456'})
        assert r.status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_admin_reset_password_short_422(db_engine, monkeypatch):
    uid = _seed_user(db_engine)
    client = _admin_client(db_engine, monkeypatch)
    try:
        r = client.post(f'/admin/users/{uid}/reset-password',
                        json={'new_password': 'short'})
        assert r.status_code == 422
    finally:
        app.dependency_overrides.clear()
