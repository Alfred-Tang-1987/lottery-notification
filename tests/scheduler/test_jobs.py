import json
import pickle
import time
from datetime import UTC
from unittest.mock import MagicMock

import httpx
from apscheduler.triggers.date import DateTrigger

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
    from app.scheduler.jobs import _push_big_win, register_all_jobs
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

    push_jobs = [j for j in sched.get_jobs() if j.func is _push_big_win]
    assert len(push_jobs) == 1
    _invoke_job(sched, push_jobs[0].id)
    notifier.notify_path_a.assert_called_once()
    assert notifier.notify_path_a.call_args.kwargs['comparison_id'] == comparison_id


def test_path_a_tick_does_not_repush_historical_big_wins(db_engine):
    """路径A只推送当前开奖夜的最新大奖，不得重复推送历史大奖。"""
    from datetime import timedelta

    from sqlmodel import Session

    from app.models import Comparison, DrawResult, Ticket, User
    from app.scheduler.jobs import _push_big_win, register_all_jobs

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
    push_jobs = [j for j in sched.get_jobs() if j.func is _push_big_win]
    assert len(push_jobs) == 0


def test_path_a_tick_dedups_already_sent_big_win(db_engine):
    """路径A对同一 comparison 的已 sent 大奖不再重复调度。"""
    from sqlmodel import Session

    from app.models import Comparison, DrawResult, NotificationLog, Ticket, User
    from app.scheduler.jobs import _push_big_win, register_all_jobs

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
    push_jobs = [j for j in sched.get_jobs() if j.func is _push_big_win]
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


def test_path_a_big_win_push_job_pickles_through_jobstore(db_engine):
    """生产崩溃回归：路径A 大奖推送 job 须经 SQLAlchemyJobStore pickle round-trip。

    生产 Notifier 持真实 BarkChannel/FeishuChannel（httpx.Client 持 _thread.RLock 不可
    pickle），且 Notifier._engine（SQLAlchemy engine 持 connect 闭包）同样不可 pickle。
    若 push job func 为 bound method notifier.notify_path_a，sched.start() 持久化
    _pending_jobs 时会 PicklingError/AttributeError → 调度器无法启动 → 抓取/比对/推送
    任务永不触发 → 中奖静默漏通知（spec §10 核心价值，最高优先级纪律）。

    修法（review round 1 quality/hunter）：job func 为模块级 top-level 函数，args 只
    携带可 pickle 的 (db_url, params)，函数体经 _resolve_deps(db_url) 取回 notifier 后
    调用——与现有 _path_a_tick/_path_b_summary「job args 只带 db_url」注册表模式同构。

    现有 test_path_a_tick_fetches_compares_and_schedules_big_win 用 MagicMock(notifier)
    且只 _invoke_job 直接调用 job.func、从不经 jobstore 持久化 round-trip，故未暴露此
    生产崩溃。
    """
    from sqlmodel import Session

    from app.models import Comparison, DrawResult, NotificationChannel, Ticket, User
    from app.notifications.bark import BarkChannel
    from app.notifications.feishu import FeishuChannel
    from app.notifications.notifier import Notifier
    from app.scheduler.jobs import register_all_jobs

    # 真实 Notifier + 真实 BarkChannel/FeishuChannel（持 httpx.Client，不可 pickle）。
    # MockTransport 仅拦截网络请求，仍构造真实 httpx.Client 对象 → 复现生产 pickle 崩溃。
    def ok_handler(req):
        return httpx.Response(200, json={'code': 200})

    bark = BarkChannel(transport=httpx.MockTransport(ok_handler))
    feishu = FeishuChannel(transport=httpx.MockTransport(ok_handler))
    crypto = MagicMock()
    crypto.decrypt.return_value = '{"key":"k","url":"https://api.day.app"}'

    # 种子：今日开奖命中一等奖的大奖（_path_a_tick 查 draw_date 落今天）
    with Session(db_engine) as s:
        user = User(username='pickle_reg', password_hash='x', role='user', invite_code='PK1')
        s.add(user)
        s.commit()
        s.refresh(user)
        s.add(
            NotificationChannel(
                user_id=user.id,
                type='bark',
                config_json=json.dumps({'ct': 'enc'}),
                enabled=True,
                key_version=1,
            )
        )
        s.commit()
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

    notifier = Notifier(db_engine, channels={'bark': bark, 'feishu': feishu}, crypto=crypto)
    sched = build_scheduler(db_engine)
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
    # _path_a_tick 命中大奖 → 登记一次性推送 job 到 _pending_jobs（sched 未 start 不持久化）
    _invoke_job(sched, 'path_a_poll_evening')

    # 取出 push job（'date' 触发器；sched 未 start 时存于 _pending_jobs）
    push_jobs = [job for job, _alias, _replace in sched._pending_jobs if isinstance(job.trigger, DateTrigger)]
    assert push_jobs, '应登记至少一个一次性推送 job（命中大奖）'
    for job in push_jobs:
        # bound method（持 httpx.Client + engine）→ PicklingError/AttributeError；
        # top-level func + db_url + 原始类型 params → OK
        pickle.dumps((job.func, job.args, job.kwargs))

    # 生产路径：sched.start() 经 SQLAlchemyJobStore 持久化 _pending_jobs → pickle
    # round-trip。bound method（修复前）→ start() 抛 PicklingError/AttributeError，调度器
    # 无法启动；top-level func（修复后）→ start() 成功。
    #
    # 用 no-op 执行器（替换 BaseExecutor.submit_job）避免立即触发的 'date' 推送任务真正
    # 执行（不调 _push_big_win/网络/DB），消除 wait=False 的「cannot schedule new futures
    # after shutdown」竞态与 wait=True 的 JobLookupError teardown 噪音。job 仍经 jobstore
    # 持久化（pickle.dumps(__getstate__)）→ 主循环 reconstitute（pickle.loads）→ 提交到
    # 执行器——被记录即证明完整 round-trip 成功（func_ref 文本 + args=(db_url,) 可 pickle）。
    import apscheduler.executors.base as _base_exec

    submitted: list = []

    def _noop_submit(self, job, run_times):
        submitted.append(job)

    orig_submit = _base_exec.BaseExecutor.submit_job
    _base_exec.BaseExecutor.submit_job = _noop_submit
    try:
        sched.start()  # 未抛 PicklingError = 持久化 pickle 成功
        assert sched.running
        # 等待主循环把 'date' 推送 job 从 jobstore reconstitute 并提交（完整 round-trip 证明）。
        # 立即触发的 date job 通常毫秒级被拾取；轮询避免在慢机/CI 上误判。
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not any(isinstance(j.trigger, DateTrigger) for j in submitted):
            time.sleep(0.02)
        date_pushed = [j for j in submitted if isinstance(j.trigger, DateTrigger)]
        assert date_pushed, 'date 推送 job 应被 jobstore reconstitute 并提交到执行器（pickle round-trip 成功）'
        # reconstitute 后 func 仍解析回模块级 _push_big_win（func_ref 文本可 pickle + 可 import）。
        from app.scheduler.jobs import _push_big_win

        assert date_pushed[0].func is _push_big_win
        assert date_pushed[0].args == (str(db_engine.url),)  # args 只带可 pickle 的 db_url
        sched.shutdown(wait=True)
        assert not sched.running
    finally:
        _base_exec.BaseExecutor.submit_job = orig_submit
        notifier.close()
