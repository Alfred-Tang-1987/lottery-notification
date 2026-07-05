import secrets
from datetime import UTC, datetime, timedelta

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.api.deps import get_session_dep
from app.api.security import COOKIE_NAME, CSRF_HEADER, create_session_token, generate_csrf_token
from app.config import reset_settings_cache
from app.main import app
from app.models import AdminAuditLog, InviteCode, LotteryType, NotificationLog, User


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
    """邀请码创建必须 commit 入库 + 审计日志必须持久化（不能只在 pending 事务里）。

    静默失败防护：write_audit(commit=False) 后必须 session.commit()，否则审计行
    只在 pending 事务里，请求结束 session 关闭后被静默丢弃。验证用全新 Session
    查（模拟请求结束后另起连接的运维查询），与 _admin_client 复用 session 的
    假绿测试区分。
    """
    _set_required_env(monkeypatch)
    with Session(db_engine) as session:
        u = User(username='admin', password_hash='x', role='admin', invite_code='A')
        session.add(u)
        session.commit()
        session.refresh(u)
        uid = u.id

    def _dep():
        s = Session(db_engine)
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_session_dep] = _dep
    try:
        client = TestClient(app)
        client.cookies.set(COOKIE_NAME, create_session_token(user_id=uid, role='admin'))
        csrf = generate_csrf_token()
        client.cookies.set('csrf_token', csrf)
        client.headers[CSRF_HEADER] = csrf
        r = client.post('/admin/invite-codes')
        assert r.status_code == 201
        data = r.json()
        assert len(data['code']) == 6
        assert data['code'].isdigit()
        assert data['created_by'] > 0
        assert data['used_by'] is None
        assert data['expires_at'] is not None
    finally:
        app.dependency_overrides.clear()

    # 用全新 Session 验证审计日志已持久化（非 pending 假绿）
    with Session(db_engine) as verify:
        log = verify.exec(
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


def test_admin_create_invite_code_collision_retries_via_savepoint(db_engine, monkeypatch):
    """邀请码 code 唯一冲突走 savepoint 回滚，外层事务不毒化，下一轮重试成功。

    hunter finding（lesson L-20260706T010400Z）：早期实现用裸 try/except + session.rollback()
    处理冲突，rollback 会回滚整个外层事务（PendingRollback 状态），后续审计 insert 失败。
    savepoint（begin_nested）只回滚 savepoint，外层事务保持干净，审计+邀请码最终单次
    commit 原子落库。本测试 mock 第一次 randbelow 返回已存在 code，验证：
    (1) 第一次冲突触发 savepoint 回滚但不毒化 session；
    (2) 第二次成功生成 + 审计同事务落库；
    (3) 邀请码 + 审计在全新 Session 都查得到（原子性）。
    """
    _set_required_env(monkeypatch)
    with Session(db_engine) as session:
        u = User(username='admin2', password_hash='x', role='admin', invite_code='B')
        session.add(u)
        session.commit()
        session.refresh(u)
        uid = u.id
        # 预置一个已存在的邀请码（用于制造第一次冲突）
        existing = InviteCode(
            code='123456',
            created_by=uid,
            expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(days=30),
        )
        session.add(existing)
        session.commit()

    # mock secrets.randbelow：第一次返回 123456（冲突），第二次返回 654321（成功）
    call_count = {'n': 0}
    real_randbelow = secrets.randbelow

    def fake_randbelow(n):
        call_count['n'] += 1
        if call_count['n'] == 1:
            return 123456  # 冲突
        return real_randbelow(n)

    monkeypatch.setattr('app.api.admin_ext.secrets.randbelow', fake_randbelow)

    def _dep():
        s = Session(db_engine)
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_session_dep] = _dep
    try:
        client = TestClient(app)
        client.cookies.set(COOKIE_NAME, create_session_token(user_id=uid, role='admin'))
        csrf = generate_csrf_token()
        client.cookies.set('csrf_token', csrf)
        client.headers[CSRF_HEADER] = csrf
        r = client.post('/admin/invite-codes')
        assert r.status_code == 201, f'冲突重试后应成功，got {r.status_code}: {r.text}'
        assert call_count['n'] >= 2, '第一次冲突应触发第二次重试'
    finally:
        app.dependency_overrides.clear()

    # 邀请码 + 审计在全新 Session 都查得到（原子性验证）
    with Session(db_engine) as verify:
        new_code = verify.exec(select(InviteCode).where(InviteCode.code != '123456')).first()
        assert new_code is not None, '重试生成的邀请码应已落库'
        log = verify.exec(
            select(AdminAuditLog).where(AdminAuditLog.target_id == new_code.code)
        ).first()
        assert log is not None, '审计行应与邀请码同事务原子落库（savepoint 不毒化外层）'


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
# 用户管理：备注列（spec §12.2 row 9）
# ---------------------------------------------------------------------------


def test_admin_list_users_includes_note(db_engine, monkeypatch):
    """spec §12.2 row 9 要求用户管理含「备注列」。

    /admin/users 响应必须含 note 字段（默认空字符串），admin 可读到备注信息。
    """
    with Session(db_engine) as session:
        client, _ = _admin_client(session, monkeypatch)
        # 添加一个普通用户（admin 已在 _admin_client 里创建）
        session.add(User(username='alice', password_hash='x', role='user', invite_code='B',
                         note='家庭用户·张三'))
        session.commit()
        r = client.get('/admin/users')
        assert r.status_code == 200
        data = r.json()
        alice = next(u for u in data if u['username'] == 'alice')
        assert alice['note'] == '家庭用户·张三'


def test_admin_list_users_note_defaults_empty(db_engine, monkeypatch):
    """note 字段缺省为空字符串（向后兼容未设备注的旧用户）。"""
    with Session(db_engine) as session:
        client, _ = _admin_client(session, monkeypatch)
        r = client.get('/admin/users')
        data = r.json()
        # admin 用户未设 note
        admin = next(u for u in data if u['username'] == 'admin')
        assert admin['note'] == ''


# ---------------------------------------------------------------------------
# 彩种配置：GET /admin/lotteries（spec §12.2 row 9：启用/开奖日/双源）
# ---------------------------------------------------------------------------


def test_admin_list_lotteries_returns_enabled_and_draw_days(db_engine, monkeypatch):
    """spec §12.2 row 9 彩种配置要求「启用/开奖日」三要素。

    GET /admin/lotteries 返回每个彩种的 code/name/enabled/draw_days，
    供 Admin.vue 渲染真实启用状态而非硬编码 true。
    """
    with Session(db_engine) as session:
        # 种子一个彩种
        session.add(LotteryType(
            code='ssq',
            name='双色球',
            category='welfare',
            spec_json='{"welfare_rate":36}',
            draw_schedule_json='{"draw_days":[1,3,6]}',
            enabled=True,
        ))
        session.add(LotteryType(
            code='dlt',
            name='大乐透',
            category='sport',
            spec_json='{"welfare_rate":36}',
            draw_schedule_json='{"draw_days":[0,2,5]}',
            enabled=False,
        ))
        session.commit()
        client, _ = _admin_client(session, monkeypatch)
        r = client.get('/admin/lotteries')
        assert r.status_code == 200
        data = r.json()
        codes = {item['code']: item for item in data}
        assert 'ssq' in codes
        assert codes['ssq']['enabled'] is True
        assert codes['ssq']['draw_days'] == [1, 3, 6]
        assert codes['dlt']['enabled'] is False
        assert codes['dlt']['draw_days'] == [0, 2, 5]


# ---------------------------------------------------------------------------
# SMTP 配置写入（spec §12.2 row 9：服务商下拉 + 账号+授权码 + 保存）
# ---------------------------------------------------------------------------


def test_admin_save_smtp_config_persists_provider_fields(db_engine, monkeypatch, tmp_path):
    """spec §12.2 row 9 要求 SMTP 发件 UI 为写入表单（服务商下拉 + 账号 + 授权码 + 保存）。

    POST /admin/smtp-config 接收 provider/account/auth_code（+ 可选 host/port/encryption 覆盖），
    持久化到 .env 配置缓存（运行时 settings 单例），后续 GET /admin/smtp-config 反映新值。
    """
    _set_required_env(monkeypatch)
    # 重定向 ENV_FILE 到 tmp_path，避免污染真实 .env
    env_tmp = tmp_path / 'test.env'
    monkeypatch.setenv('ENV_FILE', str(env_tmp))
    with Session(db_engine) as session:
        client, _ = _admin_client(session, monkeypatch)
        r = client.post('/admin/smtp-config', json={
            'provider': 'qq',
            'account': 'test@qq.com',
            'auth_code': 'secret123',
        })
        assert r.status_code == 200
        # 验证写入后 GET 反映新值
        r2 = client.get('/admin/smtp-config')
        data = r2.json()
        assert data['smtp_host'] == 'smtp.qq.com'
        assert data['smtp_port'] == 465
        assert data['smtp_encryption'] == 'SSL/TLS'
        assert data['smtp_user'] == 'test@qq.com'
        assert data['smtp_from'] == 'test@qq.com'
        assert data['configured'] is True
        # 授权码不回显
        assert 'smtp_pass' not in data


def test_admin_save_smtp_config_custom_provider(db_engine, monkeypatch, tmp_path):
    """自定义服务商需手动填 host/port/encryption。"""
    _set_required_env(monkeypatch)
    env_tmp = tmp_path / 'test.env'
    monkeypatch.setenv('ENV_FILE', str(env_tmp))
    with Session(db_engine) as session:
        client, _ = _admin_client(session, monkeypatch)
        r = client.post('/admin/smtp-config', json={
            'provider': 'custom',
            'host': 'mail.example.com',
            'port': 587,
            'encryption': 'STARTTLS',
            'account': 'user@example.com',
            'auth_code': 'pw',
            'from_address': 'user@example.com',
        })
        assert r.status_code == 200
        data = r.json()
        assert data['smtp_host'] == 'mail.example.com'
        assert data['smtp_port'] == 587
        assert data['smtp_encryption'] == 'STARTTLS'


def test_admin_save_smtp_config_rejects_missing_provider(db_engine, monkeypatch):
    """无 provider 字段返回 422。"""
    with Session(db_engine) as session:
        client, _ = _admin_client(session, monkeypatch)
        r = client.post('/admin/smtp-config', json={'account': 'x', 'auth_code': 'y'})
        assert r.status_code == 422


def test_admin_save_smtp_config_rejects_out_of_range_port(db_engine, monkeypatch):
    """port 越界（<1 或 >65535）返回 422——防写入非法端口号。"""
    with Session(db_engine) as session:
        client, _ = _admin_client(session, monkeypatch)
        # 自定义 provider 才会消费 port 字段
        for bad_port in (-1, 0, 65536, 99999):
            r = client.post(
                '/admin/smtp-config',
                json={
                    'provider': 'custom',
                    'account': 'x',
                    'auth_code': 'y',
                    'host': 'smtp.example.com',
                    'port': bad_port,
                    'encryption': 'SSL/TLS',
                },
            )
            assert r.status_code == 422, f'port={bad_port} 应被拒'


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
