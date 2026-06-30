"""Plan 05 / T7：兑奖领取 API 测试（claims router）。

Spec §6.3 IDOR：PrizeClaim 经 comparison 关联到 user，操作前必须校验
``comparison.user_id == current_user.id``，否则 403——否则用户可领取他人中奖。

CSRF（spec §4.3）：POST 是已登录 state-changing 路由——挂 verify_csrf。
"""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.api.deps import get_session_dep
from app.api.security import COOKIE_NAME, CSRF_HEADER, create_session_token
from app.config import reset_settings_cache
from app.main import app
from app.models import Comparison, PrizeClaim, User

_CSRF_TOKEN = 'csrf-double-submit-token'
# 与生产 compare_service._create_claim 的 deadline 写法同数值时区（aware CST），
# 避免 fixture 数据比生产早 8h 误导后续时区相关测试（CLAUDE.md「雷 2」同源）。
_CST = ZoneInfo('Asia/Shanghai')


@pytest.fixture(autouse=True)
def _mock_env(monkeypatch):
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
    client = _client(db_engine)
    client.cookies.set(COOKIE_NAME, create_session_token(user_id=uid, role='user'))
    client.cookies.set('csrf_token', _CSRF_TOKEN)
    client.headers[CSRF_HEADER] = _CSRF_TOKEN
    return client


def _make_user(db_engine, username: str) -> int:
    with Session(db_engine) as s:
        u = User(username=username, password_hash='x', role='user', invite_code=username)
        s.add(u)
        s.commit()
        s.refresh(u)
        return u.id


def _make_claim(db_engine, user_id: int, status: str = 'pending') -> int:
    """建一条 Comparison + PrizeClaim（comparison 归属 user_id），返回 claim_id。

    用最小合法字段构造 Comparison（hits_json / 外键指向占位 id；FK 仅在校验启用时强约束，
    SQLite 默认 PRAGMA foreign_keys=OFF 不阻拦）。
    """
    with Session(db_engine) as s:
        cmp = Comparison(
            user_id=user_id,
            draw_result_id=1,
            ticket_id=1,
            hits_json='{}',
            is_win=True,
            prize_tier=6,
            prize_amount=500,
        )
        s.add(cmp)
        s.commit()
        s.refresh(cmp)
        claim = PrizeClaim(
            comparison_id=cmp.id,
            status=status,
            deadline=datetime.now(_CST) + timedelta(days=60),
        )
        s.add(claim)
        s.commit()
        s.refresh(claim)
        return claim.id


def test_mark_claimed_sets_status(db_engine):
    """POST /claims/{id}/claim：pending → claimed，写 claimed_at。"""
    uid = _make_user(db_engine, 'alice')
    claim_id = _make_claim(db_engine, uid)
    client = _auth_csrf_client(db_engine, uid)
    r = client.post(f'/claims/{claim_id}/claim')
    assert r.status_code == 200, r.text
    assert r.json()['status'] == 'claimed'
    with Session(db_engine) as s:
        claim = s.get(PrizeClaim, claim_id)
        assert claim.status == 'claimed'
        assert claim.claimed_at is not None


def test_mark_claimed_written_as_naive_utc(db_engine):
    """claimed_at 遵循项目主流 naive-UTC 惯例（与 created_at 同数值时区）。

    同行 deadline 用 aware-CST 是 compare_service（Plan 03/04）的历史遗留偏差，不该让
    claimed_at 跟着用 CST。claimed_at 业务上不与 deadline 比较排序（scheduler 比 deadline
    vs now），但与 created_at 同属行级时间戳（未来算「领取延迟」会一起查），故对齐主流。

    断言数值落 naive-UTC 窗口（参照 CLAUDE.md 测试陷阱：光查 tzinfo is None 抓不到，
    CST 数值会早 8h 落窗口外才暴露）。
    """
    before_utc = datetime.now(UTC).replace(tzinfo=None)
    uid = _make_user(db_engine, 'alice')
    claim_id = _make_claim(db_engine, uid)
    client = _auth_csrf_client(db_engine, uid)
    r = client.post(f'/claims/{claim_id}/claim')
    after_utc = datetime.now(UTC).replace(tzinfo=None)
    assert r.status_code == 200, r.text
    with Session(db_engine) as s:
        claim = s.get(PrizeClaim, claim_id)
        assert claim.claimed_at is not None
        # SQLite 存取剥离 tzinfo；断言「数值」落 UTC 窗口（aware-CST 会早 8h 落窗口外）。
        claimed_val = claim.claimed_at
        claimed_naive = claimed_val.replace(tzinfo=None) if claimed_val.tzinfo else claimed_val
        assert before_utc <= claimed_naive <= after_utc, (
            f'claimed_at={claimed_naive} 不在 UTC 窗口 [{before_utc}, {after_utc}]'
            '——可能被写成 aware-CST（早 8h）'
        )


def test_mark_claimed_idor_forbidden(db_engine):
    """IDOR（spec §6.3）：u2 领取 u1 的 claim → 403。"""
    u1 = _make_user(db_engine, 'alice')
    u2 = _make_user(db_engine, 'bob')
    claim_id = _make_claim(db_engine, u1)
    client = _auth_csrf_client(db_engine, u2)
    r = client.post(f'/claims/{claim_id}/claim')
    assert r.status_code == 403
    # u1 的 claim 仍是 pending（未被改）
    with Session(db_engine) as s:
        assert s.get(PrizeClaim, claim_id).status == 'pending'


def test_mark_claimed_not_found(db_engine):
    """claim_id 不存在 → 404。"""
    uid = _make_user(db_engine, 'alice')
    client = _auth_csrf_client(db_engine, uid)
    r = client.post('/claims/99999/claim')
    assert r.status_code == 404


def test_mark_claimed_requires_auth(db_engine):
    """未登录 → 401。"""
    client = _client(db_engine)
    assert client.post('/claims/1/claim').status_code == 401


def test_mark_claimed_requires_csrf(db_engine):
    """无 csrf token → 403。"""
    uid = _make_user(db_engine, 'alice')
    claim_id = _make_claim(db_engine, uid)
    client = _client(db_engine)
    client.cookies.set(COOKIE_NAME, create_session_token(user_id=uid, role='user'))
    r = client.post(f'/claims/{claim_id}/claim')
    assert r.status_code == 403


def test_mark_claimed_rejects_already_claimed(db_engine):
    """状态机（spec §4 line 235/298）：已 claimed 的 claim 再次 POST → 409，状态不变。"""
    uid = _make_user(db_engine, 'alice')
    claim_id = _make_claim(db_engine, uid, status='claimed')
    client = _auth_csrf_client(db_engine, uid)
    r = client.post(f'/claims/{claim_id}/claim')
    assert r.status_code == 409, r.text
    with Session(db_engine) as s:
        assert s.get(PrizeClaim, claim_id).status == 'claimed'


def test_mark_claimed_rejects_expired(db_engine):
    """状态机：scheduler 已标 expired 的 claim（兑奖截止期过）→ 409，不可再领取，状态不变。

    违反此规则会把 expired 覆盖回 claimed，绕过兑奖截止业务规则（spec §4 line 298）。
    409（非 403/404）与 IDOR 的 403/404 区分：资源存在、有权、但状态机不允许。
    """
    uid = _make_user(db_engine, 'alice')
    claim_id = _make_claim(db_engine, uid, status='expired')
    client = _auth_csrf_client(db_engine, uid)
    r = client.post(f'/claims/{claim_id}/claim')
    assert r.status_code == 409, r.text
    with Session(db_engine) as s:
        assert s.get(PrizeClaim, claim_id).status == 'expired'
