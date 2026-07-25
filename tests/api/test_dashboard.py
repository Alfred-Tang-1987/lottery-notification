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
from unittest.mock import MagicMock

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

    # Default (no period param) should be 'month' per spec §12.2 row 6
    r = client.get('/api/dashboard')
    assert r.status_code == 200
    summary_default = r.json()['summary']
    assert summary_default['total_cost'] == 200  # Default is 'month', only current month ticket

    # Explicit period=all: should include all tickets
    r = client.get('/api/dashboard?period=all')
    assert r.status_code == 200
    summary_all = r.json()['summary']
    assert summary_all['total_cost'] == 500  # 200 + 300

    # Filter by current month: should only include current month ticket
    r = client.get('/api/dashboard?period=month')
    assert r.status_code == 200
    summary_month = r.json()['summary']
    assert summary_month['total_cost'] == 200, f"Expected 200 (current month only), got {summary_month['total_cost']}"


def test_dashboard_custom_period_with_date_range(db_engine):
    """Spec §12.2 row 6: 自定义 — backend accepts period=custom + date_from/date_to filters."""
    _seed_lottery(db_engine)
    uid = _make_user(db_engine, 'custom_period_user')

    with Session(db_engine) as s:
        # Ticket from 2026-06-15
        t1 = Ticket(
            user_id=uid, lottery_code='ssq', play_type='single',
            numbers_json='{}', cost=200,
        )
        t1.created_at = datetime(2026, 6, 15, 10, 0, 0)  # naive CST
        s.add(t1)

        # Ticket from 2026-07-02
        t2 = Ticket(
            user_id=uid, lottery_code='ssq', play_type='single',
            numbers_json='{}', cost=300,
        )
        t2.created_at = datetime(2026, 7, 2, 10, 0, 0)
        s.add(t2)
        s.commit()

    client = _auth_client(db_engine, uid)

    # Custom range: 2026-07-01 to 2026-07-03 → only t2 matches
    r = client.get('/api/dashboard?period=custom&date_from=2026-07-01&date_to=2026-07-03')
    assert r.status_code == 200
    summary = r.json()['summary']
    assert summary['total_cost'] == 300


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


def test_dashboard_default_period_is_month(db_engine):
    """Spec §12.2 row 6: 默认本月 — backend API must default period to 'month', not 'all'."""
    _seed_lottery(db_engine)
    uid = _make_user(db_engine, 'default_period_user')

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
        t_old.created_at = cst_now - timedelta(days=60)
        s.add(t_old)
        s.commit()

    client = _auth_client(db_engine, uid)

    # No period param: should default to 'month' (current month only)
    r = client.get('/api/dashboard')
    assert r.status_code == 200
    summary = r.json()['summary']
    # Default should be 'month', so only current month ticket (200) included
    assert summary['total_cost'] == 200, f"Expected 200 (default=month), got {summary['total_cost']}"


def test_dashboard_custom_period_rolling_window(db_engine):
    """Spec §12.2 row 6: 自定义 — period=custom with date_from/date_to filters by custom range."""
    _seed_lottery(db_engine)
    uid = _make_user(db_engine, 'custom_period_user')

    cst_now = datetime.now(_CST).replace(tzinfo=None)

    with Session(db_engine) as s:
        # Ticket from 10 days ago
        t_recent = Ticket(
            user_id=uid, lottery_code='ssq', play_type='single',
            numbers_json='{}', cost=200,
        )
        t_recent.created_at = cst_now - timedelta(days=10)
        s.add(t_recent)
        s.flush()

        # Ticket from 40 days ago
        t_old = Ticket(
            user_id=uid, lottery_code='ssq', play_type='single',
            numbers_json='{}', cost=300,
        )
        t_old.created_at = cst_now - timedelta(days=40)
        s.add(t_old)
        s.commit()

    client = _auth_client(db_engine, uid)

    # Custom range: last 30 days only
    date_from = (cst_now - timedelta(days=30)).strftime('%Y-%m-%d')
    date_to = cst_now.strftime('%Y-%m-%d')
    r = client.get(f'/api/dashboard?period=custom&date_from={date_from}&date_to={date_to}')
    assert r.status_code == 200
    summary = r.json()['summary']
    # Only recent ticket (200) should be included
    assert summary['total_cost'] == 200, f"Expected 200 (custom range), got {summary['total_cost']}"


# -----------------------------
# T6g: 开奖日历 + 附近代销点（spec §12.2 row 2）
# -----------------------------


def test_calendar_requires_auth(db_engine):
    """未登录 GET /api/dashboard/calendar → 401。"""
    client = _client(db_engine)
    r = client.get('/api/dashboard/calendar')
    assert r.status_code == 401


def test_calendar_returns_enabled_lotteries_schedule(db_engine):
    """GET /api/dashboard/calendar 返回**启用**彩种的开奖日程 + 预告。

    draw_days 用 Python weekday（0=周一…6=周日）。返回字段须含：
    - lottery_code / lottery_name / category
    - draw_days（list[int]，0-6）
    - next_draw_date（ISO date 字符串， YYYY-MM-DD，从今天起最近一个匹配 weekday）

    禁用彩种（enabled=False）不应出现。
    """
    with Session(db_engine) as s:
        s.add(LotteryType(
            code='ssq', name='双色球', category='welfare',
            spec_json='{}', draw_schedule_json='{"draw_days": [1, 3, 6]}',
            enabled=True,
        ))
        s.add(LotteryType(
            code='dlt', name='大乐透', category='sport',
            spec_json='{}', draw_schedule_json='{"draw_days": [0, 2, 5]}',
            enabled=True,
        ))
        # Disabled lottery — must NOT appear in response
        s.add(LotteryType(
            code='qlc', name='七乐彩', category='welfare',
            spec_json='{}', draw_schedule_json='{"draw_days": [0, 2, 4]}',
            enabled=False,
        ))
        s.commit()

    uid = _make_user(db_engine, 'cal_user')
    client = _auth_client(db_engine, uid)
    r = client.get('/api/dashboard/calendar')
    assert r.status_code == 200, r.text
    items = r.json()
    assert isinstance(items, list)
    assert len(items) == 2, f'Expected 2 enabled lotteries, got {len(items)}'

    by_code = {it['lottery_code']: it for it in items}
    assert set(by_code.keys()) == {'ssq', 'dlt'}, 'disabled qlc must be filtered out'

    # Each item exposes schedule + next preview
    ssq = by_code['ssq']
    assert ssq['lottery_name'] == '双色球'
    assert ssq['category'] == 'welfare'
    assert ssq['draw_days'] == [1, 3, 6]
    # next_draw_date must be an ISO date today-or-future and fall on a declared weekday
    assert isinstance(ssq['next_draw_date'], str)
    next_dt = datetime.strptime(ssq['next_draw_date'], '%Y-%m-%d').date()
    today = datetime.now(_CST).date()
    assert next_dt >= today, f'next_draw_date {next_dt} should be today or future'
    # Python weekday: 0=Mon…6=Sun
    assert next_dt.weekday() in [1, 3, 6], (
        f'next_draw_date weekday {next_dt.weekday()} not in declared draw_days [1,3,6]'
    )


def test_calendar_handles_missing_draw_schedule(db_engine):
    """彩种 draw_schedule_json 缺失或非法 JSON 时该彩种仍返回（draw_days=[]），不整体 500。"""
    with Session(db_engine) as s:
        s.add(LotteryType(
            code='ssq', name='双色球', category='welfare',
            spec_json='{}', draw_schedule_json='',  # empty / missing
            enabled=True,
        ))
        s.commit()

    uid = _make_user(db_engine, 'cal_empty')
    client = _auth_client(db_engine, uid)
    r = client.get('/api/dashboard/calendar')
    assert r.status_code == 200, r.text
    items = r.json()
    assert len(items) == 1
    assert items[0]['draw_days'] == []
    assert items[0]['next_draw_date'] is None  # no schedule → no preview


def test_calendar_sorted_by_next_draw_date(db_engine):
    """开奖日历按 next_draw_date 升序排列（最近的在前），无日程的排末尾。

    回归点：旧实现按 lottery_code 字母序排列，用户看到 dlt → pl3 → pl5 → qlc → ...
    完全无日期含义，无法快速识别「下一期最近开奖是哪个彩种」。
    """
    # 用固定 draw_days 让 next_draw_date 可预测：
    # - ssq: draw_days=[2]（仅周二）→ next 是最近一个周二
    # - dlt: draw_days=[4]（仅周四）→ next 是最近一个周四
    # - qlc: draw_days=[]（无日程）→ next_draw_date=None，应排末尾
    with Session(db_engine) as s:
        s.add(LotteryType(code='ssq', name='双色球', category='welfare',
                          spec_json='{}', draw_schedule_json='{"draw_days": [2]}', enabled=True))
        s.add(LotteryType(code='dlt', name='大乐透', category='sport',
                          spec_json='{}', draw_schedule_json='{"draw_days": [4]}', enabled=True))
        s.add(LotteryType(code='qlc', name='七乐彩', category='welfare',
                          spec_json='{}', draw_schedule_json='{"draw_days": []}', enabled=True))
        s.commit()

    uid = _make_user(db_engine, 'cal_sort')
    client = _auth_client(db_engine, uid)
    r = client.get('/api/dashboard/calendar')
    assert r.status_code == 200, r.text
    items = r.json()
    assert len(items) == 3

    # 提取 next_draw_date 列表（None 视为最大，排末尾）
    dates = []
    for it in items:
        d = it['next_draw_date']
        dates.append(d if d is not None else '9999-12-31')

    # 验证升序：dates 必须是非递减的
    assert dates == sorted(dates), (
        f'calendar not sorted by next_draw_date asc: {dates}'
    )
    # 无日程的 qlc 必须排最后
    assert items[-1]['lottery_code'] == 'qlc'





def test_agencies_requires_auth(db_engine):
    """未登录 GET /api/dashboard/agencies → 401。"""
    client = _client(db_engine)
    r = client.get('/api/dashboard/agencies')
    assert r.status_code == 401


def test_agencies_returns_mock_list_with_category(db_engine):
    """GET /api/dashboard/agencies 返回 MVP mock 代销点列表。

    Spec §12.2 row 2 / §5.4：MVP 可 mock；按 category=welfare|sport 过滤；
    每条含 name / address / category / lat / lng / distance_m（导航/定位字段齐全）。
    无 category 参数 → 返回全部。
    """
    uid = _make_user(db_engine, 'agency_user')
    client = _auth_client(db_engine, uid)

    # No filter → all
    r = client.get('/api/dashboard/agencies')
    assert r.status_code == 200, r.text
    all_items = r.json()
    assert isinstance(all_items, list)
    assert len(all_items) >= 2, 'mock should return at least 2 agencies'
    # Each item has navigation/POI fields
    for it in all_items:
        assert {'name', 'address', 'category', 'lat', 'lng'} <= set(it.keys()), (
            f'agency missing required fields: {it}'
        )
        assert it['category'] in ('welfare', 'sport')

    categories_present = {it['category'] for it in all_items}
    assert categories_present == {'welfare', 'sport'}, (
        f'mock should cover both categories, got {categories_present}'
    )

    # Filter welfare
    r = client.get('/api/dashboard/agencies?category=welfare')
    assert r.status_code == 200
    welfare = r.json()
    assert len(welfare) >= 1
    assert all(it['category'] == 'welfare' for it in welfare), 'welfare filter broken'

    # Filter sport
    r = client.get('/api/dashboard/agencies?category=sport')
    assert r.status_code == 200
    sport = r.json()
    assert len(sport) >= 1
    assert all(it['category'] == 'sport' for it in sport), 'sport filter broken'


def test_agencies_rejects_invalid_category(db_engine):
    """非法 category 值 → 422（Query pattern validation）。"""
    uid = _make_user(db_engine, 'agency_bad')
    client = _auth_client(db_engine, uid)
    r = client.get('/api/dashboard/agencies?category=invalid')
    assert r.status_code == 422


# ──────────────────────────────────────────────
# 附近代销点 — 高德 POI 集成
# 前端传 lat/lng → 后端查高德 /place/around → 返回真实代销点
# 无 lat/lng / 无 API key / API 失败 → 回退 mock 数据
# ──────────────────────────────────────────────


def test_agencies_queries_amap_when_lat_lng_provided(db_engine, monkeypatch):
    """提供 lat/lng + AMAP_API_KEY 已配置 → 调用高德 POI API 返回真实代销点。

    高德 /place/around 返回 pois 列表，后端须解析为 AgencyOut 格式：
    - name → name
    - address → address（空则用 pname+cityname+address 拼接）
    - location "lng,lat" → lat/lng（注意高德 location 是 "经度,纬度" 顺序）
    - typecode/typecode 含 "彩票" → category（福彩/体彩），无法判断时默认 welfare
    - distance 可为 None（高德 /place/around 不返回距离，需另算或留空）
    """
    import httpx

    from app.api import dashboard as dash_mod

    # 配置 AMAP_API_KEY
    monkeypatch.setattr(dash_mod, 'get_settings', lambda: MagicMock(amap_api_key='test-amap-key'))

    # mock 高德 API 响应
    def amap_handler(req: httpx.Request) -> httpx.Response:
        # 验证请求参数
        assert 'restapi.amap.com/v3/place/around' in str(req.url)
        assert req.url.params['key'] == 'test-amap-key'
        # 高德 location 参数格式：lng,lat（经度在前）
        assert req.url.params['location'] == '116.4987,39.9242'
        assert '彩票' in req.url.params.get('keywords', '')
        return httpx.Response(200, json={
            'status': '1',
            'pois': [
                {
                    'name': '中国福利彩票（朝阳店）',
                    'address': '朝阳路100号',
                    'location': '116.4990,39.9250',
                    'typecode': '160500',
                },
                {
                    'name': '中国体育彩票（建国路）',
                    'address': '建国路50号',
                    'location': '116.4870,39.9082',
                    'typecode': '160500',
                },
            ],
        })

    # 替换 dashboard 模块内的 httpx.Client
    original_client = dash_mod._amap_client
    dash_mod._amap_client = httpx.Client(transport=httpx.MockTransport(amap_handler), timeout=5.0)
    try:
        uid = _make_user(db_engine, 'amap_user')
        client = _auth_client(db_engine, uid)
        r = client.get('/api/dashboard/agencies?lat=39.9242&lng=116.4987')
        assert r.status_code == 200, r.text
        items = r.json()
        assert len(items) == 2
        assert items[0]['name'] == '中国福利彩票（朝阳店）'
        assert items[0]['address'] == '朝阳路100号'
        # 高德 location "lng,lat" → lat/lng 正确拆分（lng 在前）
        assert items[0]['lng'] == 116.4990
        assert items[0]['lat'] == 39.9250
    finally:
        dash_mod._amap_client = original_client


def test_agencies_falls_back_to_mock_without_lat_lng(db_engine, monkeypatch):
    """无 lat/lng 参数 → 回退 mock 数据（用户未授权定位）。"""
    from app.api import dashboard as dash_mod

    monkeypatch.setattr(dash_mod, 'get_settings', lambda: MagicMock(amap_api_key='test-amap-key'))

    uid = _make_user(db_engine, 'amap_noloc')
    client = _auth_client(db_engine, uid)
    r = client.get('/api/dashboard/agencies')
    assert r.status_code == 200, r.text
    items = r.json()
    # 回退 mock（至少 2 条）
    assert len(items) >= 2
    # mock 数据有 distance_m（真实 POI 无）
    assert items[0]['distance_m'] is not None


def test_agencies_falls_back_to_mock_without_amap_key(db_engine, monkeypatch):
    """无 AMAP_API_KEY → 即使有 lat/lng 也回退 mock（不发无意义 HTTP 请求）。"""
    from app.api import dashboard as dash_mod

    monkeypatch.setattr(dash_mod, 'get_settings', lambda: MagicMock(amap_api_key=''))

    uid = _make_user(db_engine, 'amap_nokey')
    client = _auth_client(db_engine, uid)
    r = client.get('/api/dashboard/agencies?lat=39.9242&lng=116.4987')
    assert r.status_code == 200, r.text
    items = r.json()
    # 回退 mock
    assert len(items) >= 2


def test_agencies_falls_back_to_mock_on_amap_failure(db_engine, monkeypatch):
    """高德 API 返回错误/异常 → 回退 mock（不让外部 API 故障阻断 dashboard）。"""
    import httpx

    from app.api import dashboard as dash_mod

    monkeypatch.setattr(dash_mod, 'get_settings', lambda: MagicMock(amap_api_key='test-amap-key'))

    def amap_handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={'status': '0', 'info': 'INVALID_USER_KEY'})

    original_client = dash_mod._amap_client
    dash_mod._amap_client = httpx.Client(transport=httpx.MockTransport(amap_handler), timeout=5.0)
    try:
        uid = _make_user(db_engine, 'amap_fail')
        client = _auth_client(db_engine, uid)
        r = client.get('/api/dashboard/agencies?lat=39.9242&lng=116.4987')
        assert r.status_code == 200, r.text
        items = r.json()
        # 回退 mock
        assert len(items) >= 2
    finally:
        dash_mod._amap_client = original_client


def test_agencies_amap_supports_category_filter(db_engine, monkeypatch):
    """高德 POI 结果支持 category 过滤（welfare/sport）。

    通过 name 含「福利彩票」→ welfare、「体育彩票」→ sport 判断。
    """
    import httpx

    from app.api import dashboard as dash_mod

    monkeypatch.setattr(dash_mod, 'get_settings', lambda: MagicMock(amap_api_key='test-amap-key'))

    def amap_handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            'status': '1',
            'pois': [
                {'name': '中国福利彩票（朝阳店）', 'address': 'addr1', 'location': '116.4990,39.9250', 'typecode': '160500'},
                {'name': '中国体育彩票（建国路）', 'address': 'addr2', 'location': '116.4870,39.9082', 'typecode': '160500'},
            ],
        })

    original_client = dash_mod._amap_client
    dash_mod._amap_client = httpx.Client(transport=httpx.MockTransport(amap_handler), timeout=5.0)
    try:
        uid = _make_user(db_engine, 'amap_cat')
        client = _auth_client(db_engine, uid)
        # 过滤 welfare
        r = client.get('/api/dashboard/agencies?lat=39.9242&lng=116.4987&category=welfare')
        assert r.status_code == 200, r.text
        items = r.json()
        assert len(items) == 1
        assert items[0]['name'] == '中国福利彩票（朝阳店）'
        assert items[0]['category'] == 'welfare'
    finally:
        dash_mod._amap_client = original_client





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
