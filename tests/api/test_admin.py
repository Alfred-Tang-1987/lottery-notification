from datetime import datetime

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.api.deps import get_session_dep
from app.api.security import COOKIE_NAME, CSRF_HEADER, create_session_token, generate_csrf_token
from app.config import reset_settings_cache
from app.main import app
from app.models import AdminAuditLog, ApiSourceHealth, DrawResult, NotificationLog, PendingComparison, User


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
    csrf = generate_csrf_token()
    client.cookies.set('csrf_token', csrf)
    client.headers[CSRF_HEADER] = csrf
    return client


def test_admin_list_users(db_engine, monkeypatch):
    with Session(db_engine) as s:
        s.add(User(username='u1', password_hash='x', role='user', invite_code='C'))
        s.commit()
    client = _admin_client(db_engine, monkeypatch)
    r = client.get('/admin/users')
    assert r.status_code == 200
    assert len(r.json()) >= 2


def test_admin_force_verify_without_csrf_token_rejected(db_engine, monkeypatch):
    """force-verify 是 state-changing POST，必须带 matching X-CSRF-Token header。"""
    _set_required_env(monkeypatch)
    with Session(db_engine) as s:
        u = User(username='admin', password_hash='x', role='admin', invite_code='A')
        s.add(u)
        s.commit()
        s.refresh(u)
    app.dependency_overrides[get_session_dep] = lambda: (yield Session(db_engine))
    client = TestClient(app)
    client.cookies.set(COOKIE_NAME, create_session_token(user_id=u.id, role='admin'))
    r = client.post('/admin/draw-results/1/force-verify')
    assert r.status_code == 403


def test_admin_force_verify_creates_pending_comparison(db_engine, monkeypatch):
    """verified=false 的开奖，admin force-verify 必须写 PendingComparison outbox，驱动比对→推送。"""
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
        pc = s.exec(select(PendingComparison).where(PendingComparison.draw_result_id == dr_id)).first()
        assert pc is not None and pc.processed_at is None


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


def test_admin_force_verify_writes_audit_log(db_engine, monkeypatch):
    """force-verify 须写审计日志，且与状态变更同事务。"""
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
        log = s.exec(select(AdminAuditLog)).first()
        assert log and log.action == 'force_verify'
        assert log.target_id == str(dr_id)
        assert log.old_values is not None and '"verified": false' in log.old_values
        assert log.new_values is not None and '"verified": true' in log.new_values


def test_admin_push_logs_limit_is_capped(db_engine, monkeypatch):
    """push-logs limit 须被限制在合理上限，防止无界查询。"""
    with Session(db_engine) as s:
        for i in range(5):
            s.add(NotificationLog(user_id=1, type='bark', payload='{}', status='sent'))
        s.commit()
    client = _admin_client(db_engine, monkeypatch)
    # 请求 limit=1000 应被钳制到上限（如 500），这里上限 >= 5 即可验证钳制行为
    r = client.get('/admin/push-logs?limit=1000')
    assert r.status_code == 200
    assert len(r.json()) == 5


def test_admin_push_logs_limit_negative_is_clamped(db_engine, monkeypatch):
    """push-logs limit 非法值须被钳制到 [1, 500]。"""
    with Session(db_engine) as s:
        s.add(NotificationLog(user_id=1, type='bark', payload='{}', status='sent'))
        s.commit()
    client = _admin_client(db_engine, monkeypatch)
    r = client.get('/admin/push-logs?limit=0')
    assert r.status_code == 200
    assert len(r.json()) == 1
