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


def test_admin_force_verify_idempotent_no_duplicate_outbox(db_engine, monkeypatch):
    """quality review IMPORTANT：重复 force-verify 不得插重复 PendingComparison——否则
    CompareService._claim 按 id 认领两行，第二行写 comparisons 撞 (draw_result_id,ticket_id)
    unique 约束（被 per-row 隔离吞掉），outbox 残留重复行破坏比对幂等。"""
    with Session(db_engine) as s:
        dr = DrawResult(
            lottery_code='ssq',
            draw_no='063',
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
    assert client.post(f'/admin/draw-results/{dr_id}/force-verify').status_code == 200
    assert client.post(f'/admin/draw-results/{dr_id}/force-verify').status_code == 200
    with Session(db_engine) as s:
        pcs = s.exec(select(PendingComparison).where(PendingComparison.draw_result_id == dr_id)).all()
        assert len(pcs) == 1, f'重复 force-verify 不应插重复 outbox，实际 {len(pcs)} 行'


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
        s.add(ApiSourceHealth(source='mxnzp', status='ok',
                              last_success_at=datetime(2026, 9, 15, 4, 0, 0)))
        s.add(ApiSourceHealth(source='juhe', status='degraded',
                              down_since=datetime(2026, 9, 14, 20, 0, 0)))
        s.commit()
    client = _admin_client(db_engine, monkeypatch)
    r = client.get('/admin/health')
    assert r.status_code == 200
    data = r.json()
    assert len(data['sources']) == 2
    assert {s['source'] for s in data['sources']} == {'mxnzp', 'juhe'}
    # plan-11：每源返回 last_success_at/down_since（null 安全——空表行也要有键）
    # design-voice D4：alerted（状态机输出）与 error（故障原因）同返——面板必须
    # 回答「叫过人没有」「为什么挂」，只给时间是次有用的信息。
    assert all({'last_success_at', 'down_since', 'alerted', 'error'} <= set(s) for s in data['sources'])
    # eng-voice L1：按 source 索引而非数组下标——SQLite 返回序不契约化，
    # 下标断言会把「顺序变了」误报成「值错了」。
    by = {s['source']: s for s in data['sources']}
    # autoplan D-3：naive UTC 落库值必须以 'Z' 显式标注 UTC——否则前端
    # new Date() 按本地时区解析，面板故障起点显示偏差 8 小时（全程 Asia/Shanghai 纪律）。
    assert by['mxnzp']['last_success_at'].endswith('Z')
    assert by['juhe']['down_since'].endswith('Z')


def test_admin_system_health_null_and_long_error(db_engine, monkeypatch):
    """eng-voice M8.7/L3：空值与长 error 的边界——

    _iso_utc(None) → None（不抛）；error 全文返回不截断（eng M6：截断移到前端，
    后端截断会让 title 悬浮也只看到同一份截断文本，D17「全文可见」失效）。
    """
    long_error = 'x' * 300
    with Session(db_engine) as s:
        s.add(ApiSourceHealth(source='mxnzp', status='down', error=long_error))
        s.commit()
    client = _admin_client(db_engine, monkeypatch)
    data = client.get('/admin/health').json()
    src = {s['source']: s for s in data['sources']}['mxnzp']
    assert src['last_success_at'] is None and src['down_since'] is None
    assert src['error'] == long_error  # 后端不截断


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


def test_admin_push_logs_page_size_capped(db_engine, monkeypatch):
    """push-logs page_size 须被限制在合理上限，防止无界查询（spec §12.2 row 9）。

    旧版 /push-logs（裸 list + ?limit=）已迁移至 admin_ext.py（envelope + ?page_size=）。
    page_size 上限钳制到 [1, 100]（admin_ext Query(ge=1, le=100)）。
    """
    with Session(db_engine) as s:
        for _i in range(5):
            s.add(NotificationLog(user_id=1, type='bark', payload='{}', status='sent'))
        s.commit()
    client = _admin_client(db_engine, monkeypatch)
    # page_size=1000 超上限 → 422（Query le=100）
    r = client.get('/admin/push-logs?page_size=1000')
    assert r.status_code == 422


def test_admin_push_logs_envelope_shape(db_engine, monkeypatch):
    """push-logs 返回 {total, page, page_size, items} envelope（spec §12.2 row 9）。"""
    with Session(db_engine) as s:
        s.add(NotificationLog(user_id=1, type='bark', payload='{}', status='sent'))
        s.commit()
    client = _admin_client(db_engine, monkeypatch)
    r = client.get('/admin/push-logs?page=1&page_size=20')
    assert r.status_code == 200
    data = r.json()
    assert set(data.keys()) >= {'total', 'page', 'page_size', 'items'}
    assert data['total'] == 1
    assert len(data['items']) == 1
