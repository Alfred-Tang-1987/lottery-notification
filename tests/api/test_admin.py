from datetime import datetime

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.api.deps import get_session_dep
from app.api.security import COOKIE_NAME, create_session_token
from app.config import reset_settings_cache
from app.main import app
from app.models import AdminAuditLog, ApiSourceHealth, DrawResult, NotificationLog, User


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


def test_admin_force_verify_logs_audit(db_engine, monkeypatch):
    """verified=false 的开奖，admin force-verify → verified=true 并写审计日志。"""
    with Session(db_engine) as s:
        dr = DrawResult(
            lottery_code='ssq',
            draw_no='062',
            draw_date=datetime.utcnow(),
            numbers_json='{"front":[1,2,3,4,5,6],"back":[7]}',
            source='mxnzp',
            verified=False,
            version=1,
        )
        s.add(dr)
        s.commit()
        s.refresh(dr)
        dr_id = dr.id
    client = _admin_client(db_engine, monkeypatch)
    r = client.post(f'/admin/draw-results/{dr_id}/force-verify')
    assert r.status_code == 200
    with Session(db_engine) as s:
        assert s.get(DrawResult, dr_id).verified is True
        log = s.exec(select(AdminAuditLog)).first()
        assert log and log.action == 'force_verify'


def test_non_admin_forbidden(db_engine, monkeypatch):
    _set_required_env(monkeypatch)
    with Session(db_engine) as s:
        u = User(username='u', password_hash='x', role='user', invite_code='C')
        s.add(u)
        s.commit()
        s.refresh(u)
        uid = u.id
    app.dependency_overrides[get_session_dep] = lambda: (yield Session(db_engine))
    client = TestClient(app)
    client.cookies.set(COOKIE_NAME, create_session_token(user_id=uid, role='user'))
    r = client.get('/admin/users')
    assert r.status_code == 403


def test_admin_system_health(db_engine, monkeypatch):
    with Session(db_engine) as s:
        s.add(ApiSourceHealth(source='mxnzp', status='ok'))
        s.add(ApiSourceHealth(source='juhe', status='degraded'))
        s.commit()
    client = _admin_client(db_engine, monkeypatch)
    r = client.get('/admin/health')
    assert r.status_code == 200
    data = r.json()
    assert len(data['sources']) == 2
    assert {s['source'] for s in data['sources']} == {'mxnzp', 'juhe'}


def test_admin_push_logs(db_engine, monkeypatch):
    with Session(db_engine) as s:
        s.add(NotificationLog(user_id=1, type='bark', payload='{}', status='sent'))
        s.commit()
    client = _admin_client(db_engine, monkeypatch)
    r = client.get('/admin/push-logs')
    assert r.status_code == 200
    assert len(r.json()) == 1
    assert r.json()[0]['status'] == 'sent'
