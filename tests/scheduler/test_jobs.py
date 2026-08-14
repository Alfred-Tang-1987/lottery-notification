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


def test_register_all_jobs_adds_float_refill_night_at_22(db_engine):
    """开奖日当晚 22:00 应登记额外的浮奖回填轮（plan 07/T6，1C 决策）。

    现有 float_refill（每日 08:00）不变；新增 float_refill_night（每晚 22:00）
    在开奖后不久官方可能已公布浮动奖金额时再补一轮。本测试断言新 job 被注册、
    触发时刻为 22:00、复用 _run_float_refill，且与 08:00 的 float_refill 区分
    （不是同一个 job id）—— 防止「新增了但被 replace_existing 合并/未生效」的
    silent-success（L-20260706T010500Z）。
    """
    from app.scheduler.jobs import _run_float_refill, register_all_jobs

    sched = build_scheduler(db_engine)
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
    jobs_by_id = {j.id: j for j in sched.get_jobs()}
    assert 'float_refill_night' in jobs_by_id
    night_job = jobs_by_id['float_refill_night']
    # 复用现有 _run_float_refill（签名/行为不变），仅触发时刻不同。
    assert night_job.func is _run_float_refill
    # 触发器为 22:00 每日（CST 时区由 scheduler 统一配置）。
    trigger = night_job.trigger
    from apscheduler.triggers.cron import CronTrigger

    assert isinstance(trigger, CronTrigger)
    fields = {f.name: str(f) for f in trigger.fields}
    assert fields['hour'] == '22'
    assert fields['minute'] == '0'
    # 与 08:00 float_refill 互不覆盖（两个独立 job id）。
    assert 'float_refill' in jobs_by_id
    assert jobs_by_id['float_refill'].func is _run_float_refill
    morning_fields = {f.name: str(f) for f in jobs_by_id['float_refill'].trigger.fields}
    assert morning_fields['hour'] == '8'


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


def test_path_a_tick_paces_mxnzp_qps_with_inter_lottery_interval(db_engine, monkeypatch):
    """path_a_tick 须在彩种间 sleep 1.2s 避免 MXNZP 1 QPS 限流（L-20260726T013000Z）。

    根因：串行调 7 彩种触发 code=101，旧实现静默返回 None → 开奖静默漏抓。
    修复分两层：(1) adapter 把 code=101 抛 TransientLookupError；(2) path_a_tick 加间隔预防。
    本测试验证第 2 层：彩种间确实调用了 sleep。
    """
    from app.scheduler import jobs as jobs_mod
    from app.scheduler.jobs import register_all_jobs
    from app.seeds import SPECS

    # 测试环境强制 0 间隔避免拖慢，但本测试要验证真实间隔存在，恢复默认值
    monkeypatch.setattr(jobs_mod, '_INTER_LOTTERY_INTERVAL', 1.2)
    sleep_calls: list[float] = []
    monkeypatch.setattr(jobs_mod.time, 'sleep', lambda s: sleep_calls.append(s))

    sched = build_scheduler(db_engine)
    fetch = MagicMock()
    register_all_jobs(
        sched,
        {
            'engine': db_engine,
            'fetch_service': fetch,
            'compare_service': MagicMock(),
            'refill_worker': MagicMock(),
            'notifier': MagicMock(),
        },
    )
    _invoke_job(sched, 'path_a_poll_evening')

    # 7 彩种 → 6 个间隔（第一个不等待）
    assert fetch.fetch_and_store.call_count == len(SPECS)
    assert len(sleep_calls) == len(SPECS) - 1
    # 每个间隔是 1.2s
    for s in sleep_calls:
        assert s == 1.2


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


def test_path_b_summary_isolates_per_user_failure(db_engine):
    """路径B汇总：单个用户的 notify_path_b 抛异常不得阻断后续用户（silent-failure 纪律）。

    review round 1 important：_path_b_summary 的 per-user 循环此前无 try/except，user A
    的 notify_path_b 抛任何异常（transient DB 错 / 解密失败 / httpx 传输异常）都会冒泡
    中断 for 循环 → user B/C/D 收不到当日汇总（per-user 静默漏通知）。与 _path_a_tick 已
    正确隔离的 per-lottery fetch 循环同构（CLAUDE.md：批量循环里单行故障不得中断整批）。

    RED 断言：user A 抛异常后，user B 的 notify_path_b 仍被调用（call_count == 2，而非
    在 user A 处中断为 1）。用真实 side_effect 抛 RuntimeError 复现 user A 故障。
    """
    from sqlmodel import Session

    from app.models import User
    from app.scheduler.jobs import register_all_jobs

    sched = build_scheduler(db_engine)
    with Session(db_engine) as s:
        s.add(User(username='user_a', password_hash='x', role='user', invite_code='A1'))
        s.add(User(username='user_b', password_hash='x', role='user', invite_code='B1'))
        s.commit()

    notifier = MagicMock()
    notifier.is_dnd_active.return_value = False
    call_log = []

    def side_effect(*, user_id, date_str):
        call_log.append(user_id)
        if len(call_log) == 1:
            raise RuntimeError('user_a notifier outage (transient DB/decrypt/network)')

    notifier.notify_path_b.side_effect = side_effect
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
    # 两个用户都被尝试（user A 抛异常后循环继续到 user B），而非中断在 user A。
    assert len(call_log) == 2, f'user B 应被处理，实际 call_log={call_log}'


def test_period_summary_isolates_per_user_failure(db_engine):
    """周/月报汇总：单个用户的 notify_period_summary 抛异常不得阻断后续用户。

    review round 1 important：_push_period_summary 的 per-user 循环同样无 try/except。
    user A 抛异常 → user B/C/D 当期周报/月报全丢（per-user 静默漏通知）。
    """

    from sqlmodel import Session

    from app.models import User
    from app.scheduler.jobs import register_all_jobs

    sched = build_scheduler(db_engine)
    with Session(db_engine) as s:
        s.add(User(username='weekly_a', password_hash='x', role='user', invite_code='WA1'))
        s.add(User(username='weekly_b', password_hash='x', role='user', invite_code='WB1'))
        s.commit()

    notifier = MagicMock()
    notifier.is_dnd_active.return_value = False
    call_log = []

    def side_effect(*, user_id, start_date_str, end_date_str, period_label):
        call_log.append(user_id)
        if len(call_log) == 1:
            raise RuntimeError('weekly_a notifier outage')

    notifier.notify_period_summary.side_effect = side_effect
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
    assert len(call_log) == 2, f'user B 应被处理，实际 call_log={call_log}'


def test_path_a_tick_day_window_matches_aware_cst_draw_date(db_engine):
    """路径A 日窗口查询的 bounds 必须与 DrawResult.draw_date 同 tz 表示（critical）。

    review round 1 critical：fetch_service.py:229 写 DrawResult.draw_date 为 aware-CST
    （datetime.combine(d, min.time(), tzinfo=_CST)）。jobs.py 此前用 naive bounds
    （datetime.combine(today, min.time()) 无 tzinfo）过滤该列。CLAUDE.md 明文：「SQLite
    对 datetime 做字符串比较且存取会剥离 tzinfo……凡与其他 datetime 字段比较的写入值，
    须与 TimestampMixin.created_at（naive UTC）同时区同数值」——bounds 与列的 tz 表示
    必须一致，依赖 SQLAlchemy 方言内部 strip-tzinfo 是脆弱的（方言/版本漂移即静默漏比对
    当天大奖，违反「中奖永不静默漏通知」核心价值，spec §10）。

    本测试不强依赖方言 strip 行为：种一笔今日 aware-CST draw 命中大奖，断言 _path_a_tick
    查询命中并登记推送 job。同时用 runtime introspection 断言 day_start/day_end 的 tzinfo
    与列写入值同表示（aware CST），锁定「bounds 与列同 tz」这一防回归契约——若未来有人把
    bounds 改回 naive，本断言会先于生产差异报警。
    """
    import inspect

    from sqlmodel import Session

    from app.models import Comparison, DrawResult, LotteryType, Ticket, User
    from app.scheduler import jobs
    from app.scheduler.jobs import _push_big_win, register_all_jobs

    sched = build_scheduler(db_engine)
    with Session(db_engine) as s:
        s.add(
            LotteryType(
                code='ssq',
                name='双色球',
                category='welfare',
                spec_json='{"code": "ssq"}',
                draw_schedule_json='{"draw_days": [1, 3, 6]}',
                enabled=True,
            )
        )
        user = User(username='tz_bounds', password_hash='x', role='user', invite_code='TZ1')
        s.add(user)
        s.commit()
        s.refresh(user)
        # fetch_service 写 aware-CST；测试镜像该写入路径（非 naive）。
        from datetime import datetime, time
        from zoneinfo import ZoneInfo

        cst = ZoneInfo('Asia/Shanghai')
        dr = DrawResult(
            lottery_code='ssq',
            draw_no='062',
            draw_date=datetime.combine(datetime.now(cst).date(), time.min, tzinfo=cst),
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
    # aware-CST 列 + bounds 同 tz → 今日大奖被命中，登记推送 job。
    push_jobs = [j for j in sched.get_jobs() if j.func is _push_big_win]
    assert len(push_jobs) == 1, '今日 aware-CST 大奖应被日窗口命中并登记推送'

    # 防回归：源码级断言 day_start/day_end 以 aware-CST 构造（与列同 tz 表示）。
    src = inspect.getsource(jobs._path_a_tick)
    assert 'tzinfo=_CST' in src, (
        'day_start/day_end 必须以 tzinfo=_CST 构造，与 DrawResult.draw_date（aware CST）'
        '同 tz 表示——依赖方言 strip 是脆弱的，见 CLAUDE.md datetime 时区对齐纪律'
    )


# ---------------------------------------------------------------------------
# 回归：path_b / period_summary 嵌套 Session 死锁（2026-07-28 NAS 实测复现）
#
# _path_b_summary / _push_period_summary 在 `with Session(engine) as s` 循环里调
# notifier.notify_path_b / notify_period_summary，而这两个 notifier 方法内部又开
# `with Session(self._engine) as s`。两个 Session 共用 pool_size=1 的唯一 engine ->
# 内层借不到连接 -> 30s TimeoutError -> 被 per-user try/except 吞成 path_b_user_failed
# -> notification_logs 不写 -> 中奖静默漏通知。
#
# 用真实 Notifier（真实 engine + mock 渠道/crypto）让内层真的开 Session 复现死锁。
# 阈值 5s 远小于 30s 死锁超时，死锁时必超时失败。
# ---------------------------------------------------------------------------


def _real_notifier_with_mock_channel(db_engine):
    """真实 Notifier + mock bark（返回 SENT）+ mock crypto（解密返回明文配置）。

    复用 test_scheduler_push._make_notifier 模式，让 notify_path_b / notify_period_summary
    内部真的 `with Session(self._engine)` 开连接，复现嵌套 Session 死锁。
    """

    from app.notifications.base import ChannelStatus, SendResult
    from app.notifications.notifier import Notifier

    bark = MagicMock()
    bark.send.return_value = SendResult(status=ChannelStatus.SENT, error=None)
    bark.type = 'bark'
    crypto = MagicMock()
    crypto.decrypt.return_value = '{"key":"k","url":"https://api.day.app"}'
    return Notifier(db_engine, channels={'bark': bark}, crypto=crypto), bark


def _seed_yesterday_ssq_with_ticket(db_engine):
    """为「昨天」播一期 ssq 开奖 + 启用用户 + ssq ticket + 启用 bark 渠道。

    让 path_b 的 _collect_user_results 有活干（tracked_count > 0，有追投彩种当期比对），
    从而真的走到 notify_path_b 内部开 Session 的路径（而非 tracked_count=0 提前 return 0）。
    返回 (user_id, yesterday_iso)。
    """
    import json
    from datetime import date, timedelta

    from sqlmodel import Session

    from app.models import NotificationChannel, Ticket, User

    yesterday = date.today() - timedelta(days=1)
    with Session(db_engine) as s:
        u = User(username='deadlock_test', password_hash='x', role='user', invite_code='D1')
        s.add(u)
        s.commit()
        s.refresh(u)
        uid = u.id
        s.add(
            NotificationChannel(
                user_id=uid,
                type='bark',
                config_json=json.dumps({'ct': 'enc'}),
                enabled=True,
                key_version=1,
            )
        )
        s.add(
            Ticket(
                user_id=uid,
                lottery_code='ssq',
                play_type='single',
                numbers_json=json.dumps({'front': [1, 2, 3, 4, 5, 6], 'back': [7]}),
                multiplier=1,
                cost=200,
                enabled=True,
            )
        )
        s.commit()
    return uid, yesterday.isoformat()


def _fetch_and_compare_yesterday_ssq(db_engine, yesterday_iso):
    """经真实 FetchService(双源 mock) + CompareService 跑一期 ssq，比手 INSERT 更稳。"""
    from datetime import date
    from unittest.mock import MagicMock

    from app.adapters.base import DrawNumbers
    from app.services.compare_service import CompareService
    from app.services.fetch_service import FetchService

    dn = DrawNumbers(
        lottery_code='ssq',
        draw_no='062',
        draw_date=date.fromisoformat(yesterday_iso),
        front=(1, 2, 3, 4, 5, 6),
        back=(7,),
    )
    primary = MagicMock()
    primary.name = 'mxnzp'
    primary.fetch.return_value = dn
    backup = MagicMock()
    backup.name = 'juhe'
    backup.fetch.return_value = dn
    FetchService(primary, backup, db_engine, grace_seconds=0).fetch_and_store('ssq')
    CompareService(db_engine).process_pending()


def test_path_b_summary_does_not_deadlock_with_real_notifier(db_engine):
    """path_b 用真实 Notifier 时不得死锁（2026-07-28 NAS 回归）。

    修复前：_path_b_summary 外层 `with Session` 持有 pool_size=1 唯一连接，内层
    notify_path_b 再开 Session 借不到 -> 30s TimeoutError。本测试用真实 Notifier
    复现，断言 ≤ 5s 完成（死锁时 30s 必失败）。
    """
    import time

    from sqlmodel import Session, select

    from app.models import NotificationLog
    from app.scheduler.jobs import _path_b_summary, register_all_jobs
    from app.scheduler.setup import build_scheduler

    uid, yesterday_iso = _seed_yesterday_ssq_with_ticket(db_engine)
    _fetch_and_compare_yesterday_ssq(db_engine, yesterday_iso)

    notifier, bark = _real_notifier_with_mock_channel(db_engine)
    # 白天跑，走真实推送路径。注意：is_dnd_active() 只是 _in_dnd() 的薄包装；notify_path_b
    # 内部校验的是 self._in_dnd()（真实时钟）。只置 is_dnd_active 在夜间跑测试时 _in_dnd()
    # 仍返回 True → notify_path_b 提前 return 0 → bark.send 永不调用（2026-08-14 22:33
    # CST 实测：0.003s 返回 0、无 NotificationLog）。须一并置假，使测试与运行时刻解耦、
    # 确定性地走真实推送路径（否则夜间 CI 假红/白天假绿，且夜间根本不测死锁路径）。
    notifier.is_dnd_active = lambda: False
    notifier._in_dnd = lambda: False

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

    t0 = time.monotonic()
    _path_b_summary(str(db_engine.url))
    elapsed = time.monotonic() - t0

    # 死锁时 30s 才返回且无 log；修复后 < 1s 且有 log。5s 阈值留余量。
    assert elapsed < 5.0, f'path_b 死锁超时：{elapsed:.1f}s（应 < 5s，死锁 30s）'
    bark.send.assert_called_once()
    with Session(db_engine) as s:
        logs = s.exec(select(NotificationLog).where(NotificationLog.user_id == uid)).all()
        assert len(logs) >= 1, '修复后必须写出 NotificationLog（死锁时 0 条）'
        assert all(lg.status == 'sent' for lg in logs), [lg.status for lg in logs]


def test_period_summary_does_not_deadlock_with_real_notifier(db_engine):
    """周报/月报共用入口 _push_period_summary 用真实 Notifier 时不得死锁。

    同构覆盖 _push_period_summary（外层 `with Session` + 内层 notify_period_summary
    再开 Session）。死锁路径与 path_b 完全一致。
    """
    import time

    from sqlmodel import Session, select

    from app.models import NotificationLog
    from app.scheduler.jobs import _push_period_summary

    uid, yesterday_iso = _seed_yesterday_ssq_with_ticket(db_engine)
    _fetch_and_compare_yesterday_ssq(db_engine, yesterday_iso)

    notifier, bark = _real_notifier_with_mock_channel(db_engine)
    notifier.is_dnd_active = lambda: False

    from datetime import date

    d = date.fromisoformat(yesterday_iso)

    t0 = time.monotonic()
    _push_period_summary(db_engine, notifier, d, d)
    elapsed = time.monotonic() - t0

    assert elapsed < 5.0, f'period_summary 死锁超时：{elapsed:.1f}s（应 < 5s）'
    bark.send.assert_called_once()
    with Session(db_engine) as s:
        logs = s.exec(select(NotificationLog).where(NotificationLog.user_id == uid)).all()
        assert len(logs) >= 1, '修复后必须写出 NotificationLog（死锁时 0 条）'
