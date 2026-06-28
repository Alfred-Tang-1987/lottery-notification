from datetime import UTC
from unittest.mock import MagicMock

from app.scheduler.setup import build_scheduler


def _invoke_job(sched, job_id):
    job = next(j for j in sched.get_jobs() if j.id == job_id)
    return job.func(*job.args, **job.kwargs)


def test_register_all_jobs_adds_expected_jobs(db_engine):
    """register_all_jobs 应注册路径A/B/浮奖回填/过期扫描/周报月报等全部任务。"""
    from app.scheduler.jobs import register_all_jobs

    sched = build_scheduler(db_engine)
    deps = {
        'engine': db_engine,
        'fetch_service': MagicMock(),
        'compare_service': MagicMock(),
        'refill_worker': MagicMock(),
        'notifier': MagicMock(),
    }
    register_all_jobs(sched, deps)
    job_ids = {j.id for j in sched.get_jobs()}
    assert 'path_a_poll_evening' in job_ids
    assert 'path_a_poll_overnight' in job_ids
    assert 'path_a_poll_end' in job_ids
    assert 'path_b_summary' in job_ids
    assert 'float_refill' in job_ids
    assert 'claim_expire_scan' in job_ids
    assert 'weekly_report' in job_ids
    assert 'monthly_report' in job_ids


def test_path_b_summary_calls_notifier(db_engine):
    """路径B汇总任务应遍历用户并调用 notifier.notify_path_b。"""
    from sqlmodel import Session

    from app.models import User
    from app.scheduler.jobs import register_all_jobs

    sched = build_scheduler(db_engine)
    with Session(db_engine) as s:
        user = User(username='scheduler_test', password_hash='x', role='user', invite_code='S1')
        s.add(user)
        s.commit()
        s.refresh(user)
        user_id = user.id

    notifier = MagicMock()
    notifier.is_dnd_active.return_value = False
    register_all_jobs(
        sched,
        {
            'engine': db_engine,
            'fetch_service': MagicMock(),
            'compare_service': MagicMock(),
            'refill_worker': MagicMock(),
            'notifier': notifier,
        },
    )
    _invoke_job(sched, 'path_b_summary')
    notifier.notify_path_b.assert_called_once()
    args = notifier.notify_path_b.call_args
    assert args.kwargs['user_id'] == user_id
    # 路径B汇总应使用"昨天"的日期（前晚开奖）。
    from datetime import date, timedelta

    expected = (date.today() - timedelta(days=1)).isoformat()
    assert args.kwargs['date_str'] == expected


def _today_cst():
    from datetime import datetime as _dt
    from datetime import time
    from zoneinfo import ZoneInfo

    cst = ZoneInfo('Asia/Shanghai')
    return _dt.combine(_dt.now(cst).date(), time.min, tzinfo=cst)


def test_path_a_tick_fetches_compares_and_schedules_big_win_push(db_engine):
    """路径A轮询应抓取全部彩种、执行比对，并把命中一二等奖的推送登记为异步任务。"""
    from sqlmodel import Session

    from app.models import Comparison, DrawResult, Ticket, User
    from app.scheduler.jobs import register_all_jobs
    from app.seeds import SPECS

    sched = build_scheduler(db_engine)
    with Session(db_engine) as s:
        user = User(username='path_a_test', password_hash='x', role='user', invite_code='P1')
        s.add(user)
        s.commit()
        s.refresh(user)
        dr = DrawResult(
            lottery_code='ssq',
            draw_no='062',
            draw_date=_today_cst(),
            numbers_json='{}',
            source='mxnzp',
            verified=True,
            version=1,
        )
        s.add(dr)
        s.commit()
        s.refresh(dr)
        ticket = Ticket(
            user_id=user.id,
            lottery_code='ssq',
            play_type='single',
            numbers_json='{}',
            multiplier=1,
            cost=200,
        )
        s.add(ticket)
        s.commit()
        s.refresh(ticket)
        cmp = Comparison(
            user_id=user.id,
            draw_result_id=dr.id,
            ticket_id=ticket.id,
            hits_json='{}',
            prize_tier=1,
            prize_amount=None,
            is_win=True,
        )
        s.add(cmp)
        s.commit()
        s.refresh(cmp)
        comparison_id = cmp.id

    fetch = MagicMock()
    compare = MagicMock()
    notifier = MagicMock()
    register_all_jobs(
        sched,
        {
            'engine': db_engine,
            'fetch_service': fetch,
            'compare_service': compare,
            'refill_worker': MagicMock(),
            'notifier': notifier,
        },
    )
    _invoke_job(sched, 'path_a_poll_evening')

    assert fetch.fetch_and_store.call_count == len(SPECS)
    compare.process_pending.assert_called_once()

    push_jobs = [j for j in sched.get_jobs() if j.func is notifier.notify_path_a]
    assert len(push_jobs) == 1
    _invoke_job(sched, push_jobs[0].id)
    notifier.notify_path_a.assert_called_once()
    assert notifier.notify_path_a.call_args.kwargs['comparison_id'] == comparison_id


def test_path_a_tick_does_not_repush_historical_big_wins(db_engine):
    """路径A只推送当前开奖夜的最新大奖，不得重复推送历史大奖。"""
    from datetime import timedelta

    from sqlmodel import Session

    from app.models import Comparison, DrawResult, Ticket, User
    from app.scheduler.jobs import register_all_jobs

    sched = build_scheduler(db_engine)
    with Session(db_engine) as s:
        user = User(username='hist_test', password_hash='x', role='user', invite_code='H1')
        s.add(user)
        s.commit()
        s.refresh(user)
        dr = DrawResult(
            lottery_code='ssq',
            draw_no='001',
            draw_date=_today_cst() - timedelta(days=7),
            numbers_json='{}',
            source='mxnzp',
            verified=True,
            version=1,
        )
        s.add(dr)
        s.commit()
        s.refresh(dr)
        ticket = Ticket(
            user_id=user.id,
            lottery_code='ssq',
            play_type='single',
            numbers_json='{}',
            multiplier=1,
            cost=200,
        )
        s.add(ticket)
        s.commit()
        s.refresh(ticket)
        cmp = Comparison(
            user_id=user.id,
            draw_result_id=dr.id,
            ticket_id=ticket.id,
            hits_json='{}',
            prize_tier=1,
            prize_amount=None,
            is_win=True,
        )
        s.add(cmp)
        s.commit()

    notifier = MagicMock()
    register_all_jobs(
        sched,
        {
            'engine': db_engine,
            'fetch_service': MagicMock(),
            'compare_service': MagicMock(),
            'refill_worker': MagicMock(),
            'notifier': notifier,
        },
    )
    _invoke_job(sched, 'path_a_poll_evening')
    push_jobs = [j for j in sched.get_jobs() if j.func is notifier.notify_path_a]
    assert len(push_jobs) == 0


def test_path_a_tick_dedups_already_sent_big_win(db_engine):
    """路径A对同一 comparison 的已 sent 大奖不再重复调度。"""
    from sqlmodel import Session

    from app.models import Comparison, DrawResult, NotificationLog, Ticket, User
    from app.scheduler.jobs import register_all_jobs

    sched = build_scheduler(db_engine)
    with Session(db_engine) as s:
        user = User(username='dedup_test', password_hash='x', role='user', invite_code='D1')
        s.add(user)
        s.commit()
        s.refresh(user)
        dr = DrawResult(
            lottery_code='ssq',
            draw_no='062',
            draw_date=_today_cst(),
            numbers_json='{}',
            source='mxnzp',
            verified=True,
            version=1,
        )
        s.add(dr)
        s.commit()
        s.refresh(dr)
        ticket = Ticket(
            user_id=user.id,
            lottery_code='ssq',
            play_type='single',
            numbers_json='{}',
            multiplier=1,
            cost=200,
        )
        s.add(ticket)
        s.commit()
        s.refresh(ticket)
        cmp = Comparison(
            user_id=user.id,
            draw_result_id=dr.id,
            ticket_id=ticket.id,
            hits_json='{}',
            prize_tier=1,
            prize_amount=None,
            is_win=True,
        )
        s.add(cmp)
        s.commit()
        s.refresh(cmp)
        s.add(
            NotificationLog(
                user_id=user.id,
                comparison_id=cmp.id,
                type='path_a',
                payload='第 062 期',
                status='sent',
            )
        )
        s.commit()

    notifier = MagicMock()
    register_all_jobs(
        sched,
        {
            'engine': db_engine,
            'fetch_service': MagicMock(),
            'compare_service': MagicMock(),
            'refill_worker': MagicMock(),
            'notifier': notifier,
        },
    )
    _invoke_job(sched, 'path_a_poll_evening')
    push_jobs = [j for j in sched.get_jobs() if j.func is notifier.notify_path_a]
    assert len(push_jobs) == 0


def test_path_a_fetch_failure_isolates_per_lottery(db_engine):
    """路径A中某个彩种抓取异常不得阻断其他彩种抓取。"""
    from app.scheduler.jobs import register_all_jobs
    from app.seeds import SPECS

    sched = build_scheduler(db_engine)
    fetch = MagicMock()

    def side_effect(code):
        if code == 'ssq':
            raise RuntimeError('ssq outage')

    fetch.fetch_and_store.side_effect = side_effect
    notifier = MagicMock()
    register_all_jobs(
        sched,
        {
            'engine': db_engine,
            'fetch_service': fetch,
            'compare_service': MagicMock(),
            'refill_worker': MagicMock(),
            'notifier': notifier,
        },
    )
    _invoke_job(sched, 'path_a_poll_evening')
    assert fetch.fetch_and_store.call_count == len(SPECS)


def test_path_b_summary_defers_when_dnd(db_engine):
    """路径B在 DND 时段应登记顺延任务，而不是直接推送。"""
    from sqlmodel import Session

    from app.models import User
    from app.scheduler.jobs import register_all_jobs

    sched = build_scheduler(db_engine)
    with Session(db_engine) as s:
        user = User(username='dnd_test', password_hash='x', role='user', invite_code='N1')
        s.add(user)
        s.commit()

    notifier = MagicMock()
    notifier.is_dnd_active.return_value = True
    register_all_jobs(
        sched,
        {
            'engine': db_engine,
            'fetch_service': MagicMock(),
            'compare_service': MagicMock(),
            'refill_worker': MagicMock(),
            'notifier': notifier,
        },
    )
    _invoke_job(sched, 'path_b_summary')
    notifier.notify_path_b.assert_not_called()
    deferred = [j for j in sched.get_jobs() if j.id == 'path_b_summary_deferred']
    assert len(deferred) == 1


def test_weekly_report_aggregates_last_week(db_engine):
    """周报应汇总上周（周一至周日），不含本周数据。"""
    from datetime import date, timedelta

    from sqlmodel import Session

    from app.models import Comparison, DrawResult, Ticket, User
    from app.scheduler.jobs import register_all_jobs

    sched = build_scheduler(db_engine)
    with Session(db_engine) as s:
        user = User(username='weekly_test', password_hash='x', role='user', invite_code='W1')
        s.add(user)
        s.commit()
        s.refresh(user)

        def make_draw(day_offset, draw_no):
            from datetime import datetime, time

            d = date.today() + timedelta(days=day_offset)
            naive = datetime.combine(d, time.min)
            dr = DrawResult(
                lottery_code='ssq',
                draw_no=draw_no,
                draw_date=naive,
                numbers_json='{}',
                source='mxnzp',
                verified=True,
                version=1,
            )
            s.add(dr)
            s.commit()
            s.refresh(dr)
            return dr

        # 上周日 -7 应纳入
        dr_last = make_draw(-7, 'last')
        # 本周一 0 应排除
        dr_this = make_draw(0 if date.today().weekday() != 0 else 7, 'this')
        for dr in (dr_last, dr_this):
            ticket = Ticket(
                user_id=user.id,
                lottery_code='ssq',
                play_type='single',
                numbers_json='{}',
                multiplier=1,
                cost=200,
            )
            s.add(ticket)
            s.commit()
            s.refresh(ticket)
            cmp = Comparison(
                user_id=user.id,
                draw_result_id=dr.id,
                ticket_id=ticket.id,
                hits_json='{}',
                prize_tier=1,
                prize_amount=None,
                is_win=True,
            )
            s.add(cmp)
            s.commit()

    notifier = MagicMock()
    notifier.is_dnd_active.return_value = False
    register_all_jobs(
        sched,
        {
            'engine': db_engine,
            'fetch_service': MagicMock(),
            'compare_service': MagicMock(),
            'refill_worker': MagicMock(),
            'notifier': notifier,
        },
    )
    _invoke_job(sched, 'weekly_report')
    notifier.notify_period_summary.assert_called_once()
    kwargs = notifier.notify_period_summary.call_args.kwargs
    start = date.fromisoformat(kwargs['start_date_str'])
    end = date.fromisoformat(kwargs['end_date_str'])
    # 只验证区间长度是 6 天（上周一到上周日）
    assert (end - start).days == 6


def test_expire_claims_marks_overdue_as_expired(db_engine):
    """兑奖过期扫描应把 deadline 已过的 pending 兑奖标为 expired。"""
    from datetime import datetime, timedelta

    from sqlmodel import Session

    from app.models import Comparison, DrawResult, PrizeClaim, Ticket, User
    from app.scheduler.jobs import register_all_jobs

    sched = build_scheduler(db_engine)
    with Session(db_engine) as s:
        user = User(username='claim_test', password_hash='x', role='user', invite_code='C1')
        s.add(user)
        s.commit()
        s.refresh(user)
        dr = DrawResult(
            lottery_code='ssq',
            draw_no='062',
            draw_date=datetime.now(UTC).replace(tzinfo=None),
            numbers_json='{}',
            source='mxnzp',
            verified=True,
            version=1,
        )
        s.add(dr)
        s.commit()
        s.refresh(dr)
        ticket = Ticket(
            user_id=user.id,
            lottery_code='ssq',
            play_type='single',
            numbers_json='{}',
            multiplier=1,
            cost=200,
        )
        s.add(ticket)
        s.commit()
        s.refresh(ticket)
        cmp = Comparison(
            user_id=user.id,
            draw_result_id=dr.id,
            ticket_id=ticket.id,
            hits_json='{}',
            prize_tier=6,
            prize_amount=500,
            is_win=True,
        )
        s.add(cmp)
        s.commit()
        s.refresh(cmp)
        # compare_service 写入 deadline 使用 aware CST；测试对齐该约定。
        from zoneinfo import ZoneInfo

        cst = ZoneInfo('Asia/Shanghai')
        claim = PrizeClaim(
            comparison_id=cmp.id,
            status='pending',
            deadline=datetime.now(cst) - timedelta(days=1),
        )
        s.add(claim)
        s.commit()
        claim_id = claim.id

    register_all_jobs(
        sched,
        {
            'engine': db_engine,
            'fetch_service': MagicMock(),
            'compare_service': MagicMock(),
            'refill_worker': MagicMock(),
            'notifier': MagicMock(),
        },
    )
    _invoke_job(sched, 'claim_expire_scan')

    with Session(db_engine) as s:
        updated = s.get(PrizeClaim, claim_id)
        assert updated.status == 'expired'
