"""Plan 06 / T6：Dashboard API 聚合测试。

Spec §12.2：仪表盘首屏聚合「待兑奖 / 我的命中 / 盈亏速览 / 开奖概览」。
/api/dashboard 返回当前用户的数据快照，供 Dashboard.vue 消费。
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.api.deps import get_session_dep
from app.api.security import COOKIE_NAME, create_session_token
from app.config import reset_settings_cache
from app.main import app
from app.models import Comparison, DrawResult, LotteryType, PrizeClaim, Ticket, User

_CST = ZoneInfo('Asia/Shanghai')


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


def _auth_client(db_engine, uid: int) -> TestClient:
    """已登录（session cookie）的 TestClient；GET 无需 csrf。"""
    client = _client(db_engine)
    client.cookies.set(COOKIE_NAME, create_session_token(user_id=uid, role='user'))
    return client


def _seed_lottery(db_engine):
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


def test_dashboard_requires_auth(db_engine):
    """未登录 GET /api/dashboard → 401。"""
    client = _client(db_engine)
    r = client.get('/api/dashboard')
    assert r.status_code == 401


def test_dashboard_returns_aggregated_snapshot(db_engine):
    """GET /api/dashboard 返回当前用户的聚合数据快照。"""
    _seed_lottery(db_engine)
    uid = _make_user(db_engine, 'alice')

    # 用 CST naive 播种以匹配生产路径（compare_service._now() → aware CST → SQLite 剥离 tz）
    cst_now = datetime.now(_CST).replace(tzinfo=None)
    draw_date = cst_now - timedelta(days=1)
    deadline = cst_now + timedelta(days=59)

    with Session(db_engine) as s:
        draw = DrawResult(
            lottery_code='ssq',
            draw_no='2026062',
            draw_date=draw_date,
            numbers_json='{"front":[1,2,3,4,5,6],"back":[7]}',
            source='mxnzp',
            verified=True,
        )
        s.add(draw)
        s.flush()
        ticket = Ticket(
            user_id=uid,
            lottery_code='ssq',
            play_type='single',
            numbers_json='{"front":[1,2,3,4,5,6],"back":[7]}',
            cost=200,
        )
        s.add(ticket)
        s.flush()
        comparison = Comparison(
            user_id=uid,
            draw_result_id=draw.id,
            ticket_id=ticket.id,
            hits_json='{"front_hit":6,"back_hit":1}',
            prize_tier=1,
            prize_amount=None,
            is_win=True,
        )
        s.add(comparison)
        s.flush()
        claim = PrizeClaim(
            comparison_id=comparison.id,
            status='pending',
            deadline=deadline,
        )
        s.add(claim)
        s.commit()

    client = _auth_client(db_engine, uid)
    r = client.get('/api/dashboard')
    assert r.status_code == 200, r.text
    data = r.json()

    # 开奖概览
    assert 'latest_draws' in data
    draws = data['latest_draws']
    assert len(draws) >= 1
    assert draws[0]['lottery_code'] == 'ssq'
    assert draws[0]['draw_no'] == '2026062'

    # 待兑奖
    assert 'pending_claims' in data
    claims = data['pending_claims']
    assert len(claims) == 1
    assert claims[0]['status'] == 'pending'
    assert claims[0]['prize_tier'] == 1

    # 盈亏摘要
    assert 'summary' in data
    summary = data['summary']
    assert summary['total_cost'] == 200
    assert summary['ticket_count'] == 1
    assert summary['pending_amount'] == 1  # one winning comparison with NULL prize_amount = pending
    assert summary['win_count'] == 0  # prize_amount IS NULL → not counted as resolved win
    assert summary['win_rate'] == 0.0  # 0 resolved wins / 1 ticket
    assert isinstance(summary['welfare_contribution'], int)  # per-lottery welfare rate × cost

    # 我的命中
    assert 'recent_hits' in data
    assert len(data['recent_hits']) >= 1
    assert data['recent_hits'][0]['lottery_code'] == 'ssq'
    assert data['recent_hits'][0]['prize_tier'] == 1
    assert data['recent_hits'][0]['is_win'] is True


def test_pending_claims_days_left_cst(db_engine):
    """days_left 须基于 CST 时区——用 UTC 会 8h 偏差导致 off-by-one 误报。"""
    from app.api.dashboard import _pending_claims

    _seed_lottery(db_engine)
    uid = _make_user(db_engine, 'bob')

    # 生产 deadline 是 compare_service._now()+60days（aware CST，存为 naive CST 数值）。
    # 用 deadline=now(CST)+23h → days_left 应为 0；UTC-today 会算成 1。
    cst_now = datetime.now(_CST)
    deadline_23h_naive = (cst_now + timedelta(hours=23)).replace(tzinfo=None)

    with Session(db_engine) as s:
        draw = DrawResult(
            lottery_code='ssq', draw_no='2026100',
            draw_date=cst_now - timedelta(days=1),
            numbers_json='{"front":[1,2,3,4,5,6],"back":[7]}',
            source='mxnzp', verified=True,
        )
        s.add(draw)
        s.flush()
        ticket = Ticket(
            user_id=uid, lottery_code='ssq', play_type='single',
            numbers_json='{}', cost=200,
        )
        s.add(ticket)
        s.flush()
        comp = Comparison(
            user_id=uid, draw_result_id=draw.id, ticket_id=ticket.id,
            hits_json='{}', prize_tier=1, prize_amount=None, is_win=True,
        )
        s.add(comp)
        s.flush()
        claim = PrizeClaim(
            comparison_id=comp.id, status='pending',
            deadline=deadline_23h_naive,
        )
        s.add(claim)
        s.commit()

    with Session(db_engine) as s:
        claims = _pending_claims(s, uid)

    assert len(claims) == 1
    assert claims[0].days_left == 0, f'expected 0, got {claims[0].days_left} (23h from now should be 0 days left)'


def test_recent_hits_batched_claim_lookup(db_engine):
    """_recent_hits 对多个中奖记录批量查 PrizeClaim（非逐条 N+1）。
    验证 claim_status 正确解析：有 claim → claim.status，无 claim → None。"""
    from app.api.dashboard import _recent_hits

    _seed_lottery(db_engine)
    uid = _make_user(db_engine, 'carol')

    cst_now = datetime.now(_CST).replace(tzinfo=None)
    draw_date = cst_now - timedelta(days=2)

    with Session(db_engine) as s:
        draw = DrawResult(
            lottery_code='ssq', draw_no='2026064',
            draw_date=draw_date,
            numbers_json='{"front":[1,2,3,4,5,6],"back":[7]}',
            source='mxnzp', verified=True,
        )
        s.add(draw)
        s.flush()

        # Each comparison needs its own ticket (UNIQUE constraint on draw_result_id, ticket_id)
        t_a = Ticket(user_id=uid, lottery_code='ssq', play_type='single', numbers_json='{"front":[1]}', cost=200)
        t_b = Ticket(user_id=uid, lottery_code='ssq', play_type='single', numbers_json='{"front":[2]}', cost=200)
        t_c = Ticket(user_id=uid, lottery_code='ssq', play_type='single', numbers_json='{"front":[3]}', cost=200)
        s.add_all([t_a, t_b, t_c])
        s.flush()

        # Comparison A: has PrizeClaim (claimed)
        comp_a = Comparison(
            user_id=uid, draw_result_id=draw.id, ticket_id=t_a.id,
            hits_json='{}', prize_tier=2, prize_amount=50000, is_win=True,
        )
        s.add(comp_a)
        s.flush()
        s.add(PrizeClaim(comparison_id=comp_a.id, status='claimed', deadline=cst_now + timedelta(days=50)))

        # Comparison B: has PrizeClaim (pending)
        comp_b = Comparison(
            user_id=uid, draw_result_id=draw.id, ticket_id=t_b.id,
            hits_json='{}', prize_tier=1, prize_amount=None, is_win=True,
        )
        s.add(comp_b)
        s.flush()
        s.add(PrizeClaim(comparison_id=comp_b.id, status='pending', deadline=cst_now + timedelta(days=58)))

        # Comparison C: no PrizeClaim
        comp_c = Comparison(
            user_id=uid, draw_result_id=draw.id, ticket_id=t_c.id,
            hits_json='{}', prize_tier=3, prize_amount=3000, is_win=True,
        )
        s.add(comp_c)
        s.commit()

    with Session(db_engine) as s:
        hits = _recent_hits(s, uid)

    assert len(hits) >= 3, f'Expected at least 3 hit records, got {len(hits)}'

    # Build lookup by prize_tier for assertions
    by_tier = {h['prize_tier']: h for h in hits}

    assert by_tier[2]['claim_status'] == 'claimed'
    assert by_tier[1]['claim_status'] == 'pending'
    assert by_tier[3]['claim_status'] is None  # no PrizeClaim row


def test_dashboard_summary_filters_by_time_period(db_engine):
    """GET /api/dashboard?period=month filters summary to current month only."""
    _seed_lottery(db_engine)
    uid = _make_user(db_engine, 'filter_user')

    cst_now = datetime.now(_CST).replace(tzinfo=None)

    with Session(db_engine) as s:
        # Ticket from current month
        t_current = Ticket(
            user_id=uid, lottery_code='ssq', play_type='single',
            numbers_json='{}', cost=200,
        )
        s.add(t_current)
        s.flush()

        # Ticket from 2 months ago
        t_old = Ticket(
            user_id=uid, lottery_code='ssq', play_type='single',
            numbers_json='{}', cost=300,
        )
        # Manually set created_at to 2 months ago
        t_old.created_at = cst_now - timedelta(days=60)
        s.add(t_old)
        s.commit()

    client = _auth_client(db_engine, uid)

    # No filter: should include all tickets
    r = client.get('/api/dashboard')
    assert r.status_code == 200
    summary_all = r.json()['summary']
    assert summary_all['total_cost'] == 500  # 200 + 300

    # Filter by current month: should only include current month ticket
    r = client.get('/api/dashboard?period=month')
    assert r.status_code == 200
    summary_month = r.json()['summary']
    assert summary_month['total_cost'] == 200, f"Expected 200 (current month only), got {summary_month['total_cost']}"


def test_dashboard_summary_filters_by_lottery_code(db_engine):
    """GET /api/dashboard?lottery_code=ssq filters summary to specific lottery."""
    # Seed two lottery types
    with Session(db_engine) as s:
        s.add(LotteryType(code='ssq', name='双色球', category='welfare', spec_json='{}', draw_schedule_json='{}'))
        s.add(LotteryType(code='dlt', name='大乐透', category='sports', spec_json='{}', draw_schedule_json='{}'))
        s.commit()

    uid = _make_user(db_engine, 'lottery_filter_user')

    with Session(db_engine) as s:
        t_ssq = Ticket(
            user_id=uid, lottery_code='ssq', play_type='single',
            numbers_json='{}', cost=200,
        )
        t_dlt = Ticket(
            user_id=uid, lottery_code='dlt', play_type='single',
            numbers_json='{}', cost=400,
        )
        s.add_all([t_ssq, t_dlt])
        s.commit()

    client = _auth_client(db_engine, uid)

    # No filter: includes both lotteries
    r = client.get('/api/dashboard')
    assert r.status_code == 200
    summary_all = r.json()['summary']
    assert summary_all['total_cost'] == 600  # 200 + 400

    # Filter by ssq: only ssq tickets
    r = client.get('/api/dashboard?lottery_code=ssq')
    assert r.status_code == 200
    summary_ssq = r.json()['summary']
    assert summary_ssq['total_cost'] == 200, f"Expected 200 (ssq only), got {summary_ssq['total_cost']}"

    # Filter by dlt: only dlt tickets
    r = client.get('/api/dashboard?lottery_code=dlt')
    assert r.status_code == 200
    summary_dlt = r.json()['summary']
    assert summary_dlt['total_cost'] == 400, f"Expected 400 (dlt only), got {summary_dlt['total_cost']}"


def test_comparisons_filters_by_time_and_lottery(db_engine):
    """GET /api/comparisons supports period and lottery_code filters."""
    with Session(db_engine) as s:
        s.add(LotteryType(code='ssq', name='双色球', category='welfare', spec_json='{}', draw_schedule_json='{}'))
        s.add(LotteryType(code='dlt', name='大乐透', category='sports', spec_json='{}', draw_schedule_json='{}'))
        s.commit()

    uid = _make_user(db_engine, 'comp_filter_user')
    cst_now = datetime.now(_CST).replace(tzinfo=None)

    with Session(db_engine) as s:
        # SSQ draw + comparison (current month)
        draw_ssq = DrawResult(
            lottery_code='ssq', draw_no='2026100',
            draw_date=cst_now - timedelta(days=1),
            numbers_json='{"front":[1,2,3,4,5,6],"back":[7]}',
            source='mxnzp', verified=True,
        )
        s.add(draw_ssq)
        s.flush()
        t_ssq = Ticket(user_id=uid, lottery_code='ssq', play_type='single', numbers_json='{}', cost=200)
        s.add(t_ssq)
        s.flush()
        comp_ssq = Comparison(
            user_id=uid, draw_result_id=draw_ssq.id, ticket_id=t_ssq.id,
            hits_json='{}', prize_tier=5, prize_amount=1000, is_win=True,
        )
        s.add(comp_ssq)

        # DLT draw + comparison (current month)
        draw_dlt = DrawResult(
            lottery_code='dlt', draw_no='2026100',
            draw_date=cst_now - timedelta(days=1),
            numbers_json='{"front":[1,2,3,4,5],"back":[6,7]}',
            source='mxnzp', verified=True,
        )
        s.add(draw_dlt)
        s.flush()
        t_dlt = Ticket(user_id=uid, lottery_code='dlt', play_type='single', numbers_json='{}', cost=400)
        s.add(t_dlt)
        s.flush()
        comp_dlt = Comparison(
            user_id=uid, draw_result_id=draw_dlt.id, ticket_id=t_dlt.id,
            hits_json='{}', prize_tier=3, prize_amount=500, is_win=True,
        )
        s.add(comp_dlt)
        s.commit()

    client = _auth_client(db_engine, uid)

    # No filter: both comparisons
    r = client.get('/api/comparisons?win_only=true')
    assert r.status_code == 200
    comps_all = r.json()
    assert len(comps_all) == 2

    # Filter by ssq: only ssq comparison
    r = client.get('/api/comparisons?win_only=true&lottery_code=ssq')
    assert r.status_code == 200
    comps_ssq = r.json()
    assert len(comps_ssq) == 1
    assert comps_ssq[0]['lottery_code'] == 'ssq'

    # Filter by dlt: only dlt comparison
    r = client.get('/api/comparisons?win_only=true&lottery_code=dlt')
    assert r.status_code == 200
    comps_dlt = r.json()
    assert len(comps_dlt) == 1
    assert comps_dlt[0]['lottery_code'] == 'dlt'


def test_dashboard_monthly_returns_last_12_months(db_engine):
    """GET /api/dashboard/monthly 返回最近12个月的投入/中奖月数据。"""
    _seed_lottery(db_engine)
    uid = _make_user(db_engine, 'dave')

    cst_now = datetime.now(_CST).replace(tzinfo=None)

    with Session(db_engine) as s:
        t = Ticket(
            user_id=uid, lottery_code='ssq', play_type='single',
            numbers_json='{}', cost=200,
        )
        s.add(t)
        s.flush()

        draw = DrawResult(
            lottery_code='ssq', draw_no='2026100',
            draw_date=cst_now - timedelta(days=1),
            numbers_json='{"front":[1,2,3,4,5,6],"back":[7]}',
            source='mxnzp', verified=True,
        )
        s.add(draw)
        s.flush()

        comp = Comparison(
            user_id=uid, draw_result_id=draw.id, ticket_id=t.id,
            hits_json='{}', prize_tier=5, prize_amount=1000, is_win=True,
        )
        s.add(comp)
        s.commit()

    client = _auth_client(db_engine, uid)
    r = client.get('/api/dashboard/monthly')
    assert r.status_code == 200, r.text
    monthly = r.json()
    assert isinstance(monthly, list)
    assert len(monthly) == 12, f'Expected 12 months, got {len(monthly)}'

    # Each entry has month/cost/prize
    for entry in monthly:
        assert 'month' in entry
        assert 'cost' in entry
        assert 'prize' in entry

    # Last month should have our seeded data
    last_month = cst_now.strftime('%Y-%m')
    last_entry = monthly[-1]
    assert last_entry['month'] == last_month
    assert last_entry['cost'] == 200
    assert last_entry['prize'] == 1000
