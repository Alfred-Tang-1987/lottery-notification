from datetime import datetime, timedelta

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.api.deps import get_session_dep
from app.api.security import COOKIE_NAME, CSRF_HEADER, create_session_token, generate_csrf_token
from app.config import reset_settings_cache
from app.main import app
from app.models import AdminAuditLog, LotteryType, NotificationLog, User


def _set_required_env(monkeypatch):
    monkeypatch.setenv('JWT_SECRET', 'a' * 32)
    monkeypatch.setenv('CRYPTO_KEY_V1', Fernet.generate_key().decode())
    reset_settings_cache()


def _admin_client(session, monkeypatch):
    """使用一个已经打开的 Session 作为请求依赖，避免 pool_size=1 的死锁。

    db_engine fixture 给的是 pool_size=1 的 engine。测试内再开新的 Session 验证
    结果时，必须保证请求用的 Session 已关闭归还连接。因此这里用单个 Session 贯穿
    整个测试：setup -> 请求 -> 验证，然后关闭。
    """
    _set_required_env(monkeypatch)
    u = User(username='admin', password_hash='x', role='admin', invite_code='A')
    session.add(u)
    session.commit()
    session.refresh(u)
    uid = u.id

    app.dependency_overrides[get_session_dep] = lambda: (yield session)
    client = TestClient(app)
    client.cookies.set(COOKIE_NAME, create_session_token(user_id=uid, role='admin'))
    csrf = generate_csrf_token()
    client.cookies.set('csrf_token', csrf)
    client.headers[CSRF_HEADER] = csrf
    return client, uid


# ---------------------------------------------------------------------------
# SMTP config
# ---------------------------------------------------------------------------


def test_admin_smtp_config_returns_current_env(db_engine, monkeypatch):
    _set_required_env(monkeypatch)
    monkeypatch.setenv('SMTP_HOST', 'smtp.qq.com')
    monkeypatch.setenv('SMTP_PORT', '465')
    monkeypatch.setenv('SMTP_ENCRYPTION', 'SSL/TLS')
    monkeypatch.setenv('SMTP_USER', 'a@qq.com')
    monkeypatch.setenv('SMTP_FROM', 'a@qq.com')
    # smtp_pass 设置为任意值，确保 configured=true
    monkeypatch.setenv('SMTP_PASS', 'secret')
    reset_settings_cache()

    with Session(db_engine) as session:
        client, _ = _admin_client(session, monkeypatch)
        r = client.get('/admin/smtp-config')
        assert r.status_code == 200
        data = r.json()
        assert data['smtp_host'] == 'smtp.qq.com'
        assert data['smtp_port'] == 465
        assert data['configured'] is True
        # 密码不回显
        assert 'smtp_pass' not in data


# ---------------------------------------------------------------------------
# 邀请码
# ---------------------------------------------------------------------------


def test_admin_create_invite_code(db_engine, monkeypatch):
    with Session(db_engine) as session:
        client, _ = _admin_client(session, monkeypatch)
        r = client.post('/admin/invite-codes')
        assert r.status_code == 201
        data = r.json()
        assert len(data['code']) == 6
        assert data['code'].isdigit()
        assert data['created_by'] > 0
        assert data['used_by'] is None
        assert data['expires_at'] is not None

        log = session.exec(
            select(AdminAuditLog).where(AdminAuditLog.action == 'create_invite_code')
        ).first()
        assert log is not None
        assert log.target_id == data['code']


def test_admin_list_invite_codes(db_engine, monkeypatch):
    with Session(db_engine) as session:
        client, _ = _admin_client(session, monkeypatch)
        client.post('/admin/invite-codes')
        client.post('/admin/invite-codes')
        r = client.get('/admin/invite-codes')
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 2
        # 按创建时间倒序
        assert data[0]['created_at'] >= data[1]['created_at']


# ---------------------------------------------------------------------------
# 彩种启用/停用
# ---------------------------------------------------------------------------


def test_admin_toggle_lottery(db_engine, monkeypatch):
    with Session(db_engine) as session:
        session.add(LotteryType(
            code='ssq',
            name='双色球',
            category='welfare',
            spec_json='{"welfare_rate":36}',
            draw_schedule_json='{}',
            enabled=True,
        ))
        session.commit()

        client, _ = _admin_client(session, monkeypatch)
        r = client.patch('/admin/lotteries/ssq/enabled?enabled=false')
        assert r.status_code == 200
        data = r.json()
        assert data['code'] == 'ssq'
        assert data['enabled'] is False

        log = session.exec(
            select(AdminAuditLog).where(AdminAuditLog.action == 'toggle_lottery')
        ).first()
        assert log is not None
        assert log.target_id == 'ssq'


def test_admin_toggle_lottery_not_found(db_engine, monkeypatch):
    with Session(db_engine) as session:
        client, _ = _admin_client(session, monkeypatch)
        r = client.patch('/admin/lotteries/xxx/enabled?enabled=false')
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# 审计日志
# ---------------------------------------------------------------------------


def test_admin_audit_logs_pagination(db_engine, monkeypatch):
    with Session(db_engine) as session:
        client, _ = _admin_client(session, monkeypatch)
        # 产生一条审计日志
        client.post('/admin/invite-codes')
        r = client.get('/admin/audit-logs?page=1&page_size=10')
        assert r.status_code == 200
        data = r.json()
        assert data['total'] >= 1
        assert data['page'] == 1
        assert data['page_size'] == 10
        assert len(data['items']) >= 1
        item = data['items'][0]
        assert item['admin_username'] == 'admin'
        assert item['action'] is not None


# ---------------------------------------------------------------------------
# 推送日志 6 维筛选 + 分页
# ---------------------------------------------------------------------------


def _seed_push_logs(session, n=5):
    for i in range(n):
        session.add(NotificationLog(
            user_id=1,
            type='bark',
            payload='{"tier": "大奖即时"}',
            status='sent' if i % 2 == 0 else 'failed',
            created_at=datetime.utcnow() - timedelta(days=i),
        ))
    session.add(User(username='u1', password_hash='x', role='user', invite_code='B'))
    session.commit()


def test_admin_push_logs_filtered_by_status(db_engine, monkeypatch):
    with Session(db_engine) as session:
        _seed_push_logs(session, n=4)
        client, _ = _admin_client(session, monkeypatch)
        r = client.get('/admin/push-logs?status=sent')
        assert r.status_code == 200
        data = r.json()
        assert all(item['status'] == 'sent' for item in data['items'])


def test_admin_push_logs_filtered_by_user_id(db_engine, monkeypatch):
    with Session(db_engine) as session:
        _seed_push_logs(session, n=3)
        client, _ = _admin_client(session, monkeypatch)
        r = client.get('/admin/push-logs?user_id=1')
        assert r.status_code == 200
        data = r.json()
        assert all(item['user_id'] == 1 for item in data['items'])
        assert data['items'][0]['username'] == 'u1'


def test_admin_push_logs_pagination(db_engine, monkeypatch):
    with Session(db_engine) as session:
        _seed_push_logs(session, n=10)
        client, _ = _admin_client(session, monkeypatch)
        r = client.get('/admin/push-logs?page=1&page_size=3')
        assert r.status_code == 200
        data = r.json()
        assert data['total'] == 10
        assert len(data['items']) == 3
        assert data['page'] == 1
        assert data['page_size'] == 3


def test_admin_push_logs_date_range(db_engine, monkeypatch):
    with Session(db_engine) as session:
        _seed_push_logs(session, n=5)
        today = datetime.utcnow().strftime('%Y-%m-%d')
        client, _ = _admin_client(session, monkeypatch)
        r = client.get(f'/admin/push-logs?date_from={today}&date_to={today}')
        assert r.status_code == 200
        data = r.json()
        # 今天创建的第一条应被包含
        assert len(data['items']) >= 1


# ---------------------------------------------------------------------------
# 受保护端点须 admin + CSRF
# ---------------------------------------------------------------------------


def test_admin_invite_code_without_csrf_rejected(db_engine, monkeypatch):
    _set_required_env(monkeypatch)
    # pool_size=1：外层 session setup 后必须关闭归还连接，请求时新开 Session 才有连接可用。
    with Session(db_engine) as session:
        u = User(username='admin', password_hash='x', role='admin', invite_code='A')
        session.add(u)
        session.commit()
        session.refresh(u)
        uid = u.id
    app.dependency_overrides[get_session_dep] = lambda: (yield Session(db_engine))
    client = TestClient(app)
    client.cookies.set(COOKIE_NAME, create_session_token(user_id=uid, role='admin'))
    r = client.post('/admin/invite-codes')
    assert r.status_code == 403


def test_admin_non_admin_forbidden_on_new_endpoints(db_engine, monkeypatch):
    _set_required_env(monkeypatch)
    with Session(db_engine) as session:
        u = User(username='u', password_hash='x', role='user', invite_code='C')
        session.add(u)
        session.commit()
        session.refresh(u)
        uid = u.id
    app.dependency_overrides[get_session_dep] = lambda: (yield Session(db_engine))
    client = TestClient(app)
    client.cookies.set(COOKIE_NAME, create_session_token(user_id=uid, role='user'))
    csrf = generate_csrf_token()
    client.cookies.set('csrf_token', csrf)
    client.headers[CSRF_HEADER] = csrf
    r = client.get('/admin/invite-codes')
    assert r.status_code == 403
