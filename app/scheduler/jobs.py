from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import TypedDict
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from app.models import Comparison, DrawResult, NotificationLog, PrizeClaim, User
from app.notifications.notifier import Notifier
from app.services.compare_service import CompareService
from app.services.fetch_service import FetchService
from app.services.refill_service import FloatRefillWorker

_CST = ZoneInfo('Asia/Shanghai')

logger = logging.getLogger(__name__)


class _JobDeps(TypedDict):
    engine: Engine
    fetch_service: FetchService
    compare_service: CompareService
    refill_worker: FloatRefillWorker
    notifier: Notifier


def register_all_jobs(sched: BackgroundScheduler, deps: _JobDeps) -> None:
    """注册全部调度任务（spec §7.3）。"""
    engine: Engine = deps['engine']
    fetch_service: FetchService = deps['fetch_service']
    compare_service: CompareService = deps['compare_service']
    refill_worker: FloatRefillWorker = deps['refill_worker']
    notifier: Notifier = deps['notifier']

    # 路径A：开奖日 21:30-次日 01:00 每 15 分钟轮询（spec §7.3）。
    # 精确表达式拆三段：21:30/21:45；22:00-00:45 每 15 分；01:00。
    sched.add_job(
        _path_a_tick,
        'cron',
        hour='21',
        minute='30,45',
        id='path_a_poll_evening',
        args=[fetch_service, compare_service, notifier, engine, sched],
        replace_existing=True,
    )
    sched.add_job(
        _path_a_tick,
        'cron',
        hour='22-23,0',
        minute='*/15',
        id='path_a_poll_overnight',
        args=[fetch_service, compare_service, notifier, engine, sched],
        replace_existing=True,
    )
    sched.add_job(
        _path_a_tick,
        'cron',
        hour='1',
        minute='0',
        id='path_a_poll_end',
        args=[fetch_service, compare_service, notifier, engine, sched],
        replace_existing=True,
    )

    # 路径B：次日 07:00 汇总推送
    sched.add_job(
        _path_b_summary,
        'cron',
        hour=7,
        minute=0,
        id='path_b_summary',
        args=[engine, notifier, sched],
        replace_existing=True,
    )

    # 浮奖回填：每日 08:00
    sched.add_job(
        refill_worker.refill,
        'cron',
        hour=8,
        minute=0,
        id='float_refill',
        replace_existing=True,
    )

    # 兑奖过期扫描：每日 07:30
    sched.add_job(
        _expire_claims,
        'cron',
        hour=7,
        minute=30,
        id='claim_expire_scan',
        args=[engine],
        replace_existing=True,
    )

    # 周报：每周日 09:00；月报：每月 1 日 09:00
    sched.add_job(
        _weekly_report,
        'cron',
        day_of_week='sun',
        hour=9,
        minute=0,
        id='weekly_report',
        args=[engine, notifier, sched],
        replace_existing=True,
    )
    sched.add_job(
        _monthly_report,
        'cron',
        day=1,
        hour=9,
        minute=0,
        id='monthly_report',
        args=[engine, notifier, sched],
        replace_existing=True,
    )


def _path_a_tick(
    fetch_service: FetchService,
    compare_service: CompareService,
    notifier: Notifier,
    engine: Engine,
    sched: BackgroundScheduler,
) -> None:
    """开奖时段：抓取 → outbox claim 比对 → 命中一二等异步推送（不阻塞比对事务，spec §7.1）。"""
    from app.seeds import SPECS

    for spec in SPECS:
        try:
            fetch_service.fetch_and_store(spec['code'])
        except Exception:
            # 单彩种源故障不得阻断其他彩种的比对/推送（silent-failure 纪律）。
            logger.error('path_a_fetch_failed code=%s', spec['code'], exc_info=True)

    try:
        compare_service.process_pending()
    except Exception:
        # 比对整体失败仍应尝试推送已比对出的大奖（大奖不容耽搁），同时留痕。
        logger.error('path_a_compare_failed', exc_info=True)

    today = datetime.now(_CST).date()
    day_start = datetime.combine(today, datetime.min.time())
    day_end = datetime.combine(today + timedelta(days=1), datetime.min.time())
    code_to_name = {x['code']: x['name'] for x in SPECS}

    pending_push = []
    with Session(engine) as s:
        # 只查当前开奖夜的大奖：draw_date 落在今天。
        big_wins = list(
            s.exec(
                select(Comparison, DrawResult)
                .join(DrawResult, Comparison.draw_result_id == DrawResult.id)
                .where(
                    Comparison.is_win == True,  # noqa: E712
                    Comparison.prize_tier.in_((1, 2)),
                    DrawResult.draw_date >= day_start,
                    DrawResult.draw_date < day_end,
                )
            ).all()
        )
        # 防御性去重：该 comparison 已有 sent 的 NotificationLog 则不再推。
        for cmp, dr in big_wins:
            already_sent = s.exec(
                select(NotificationLog).where(
                    NotificationLog.comparison_id == cmp.id,
                    NotificationLog.status == 'sent',
                )
            ).first()
            if already_sent is not None:
                continue
            name = code_to_name.get(dr.lottery_code, dr.lottery_code)
            pending_push.append(
                {
                    'comparison_id': cmp.id,
                    'lottery_name': name,
                    'draw_no': dr.draw_no,
                    'tier': cmp.prize_tier,
                    'amount': cmp.prize_amount,
                }
            )

    for params in pending_push:
        sched.add_job(notifier.notify_path_a, 'date', kwargs=params)


def _path_b_summary(engine: Engine, notifier: Notifier, sched: BackgroundScheduler) -> None:
    """次日汇总：对每个用户推前一天的核对结果；DND 时登记顺延任务。"""
    if notifier.is_dnd_active():
        _defer_summary(sched, engine, notifier, _path_b_summary, 'path_b_summary')
        return
    yesterday = (datetime.now(_CST).date() - timedelta(days=1)).isoformat()
    with Session(engine) as s:
        for user in s.exec(select(User).where(User.enabled == True)).all():  # noqa: E712
            notifier.notify_path_b(user_id=user.id, date_str=yesterday)


def _weekly_report(engine: Engine, notifier: Notifier, sched: BackgroundScheduler) -> None:
    """周报：汇总上周一 00:00 至上周日 23:59 的核对结果。"""
    if notifier.is_dnd_active():
        _defer_summary(sched, engine, notifier, _weekly_report, 'weekly_report')
        return
    today = datetime.now(_CST).date()
    # 上周日 = today - weekday - 1（若 today 是周日，则上周日 = today - 7）
    days_since_sunday = (today.weekday() + 1) % 7
    last_sunday = today - timedelta(days=days_since_sunday)
    last_monday = last_sunday - timedelta(days=6)
    _push_period_summary(engine, notifier, last_monday, last_sunday)


def _monthly_report(engine: Engine, notifier: Notifier, sched: BackgroundScheduler) -> None:
    """月报：汇总上月 1 日至上月最后一天的核对结果。"""
    if notifier.is_dnd_active():
        _defer_summary(sched, engine, notifier, _monthly_report, 'monthly_report')
        return
    today = datetime.now(_CST).date()
    first_of_this_month = today.replace(day=1)
    last_of_prev_month = first_of_this_month - timedelta(days=1)
    first_of_prev_month = last_of_prev_month.replace(day=1)
    _push_period_summary(engine, notifier, first_of_prev_month, last_of_prev_month)


def _push_period_summary(engine: Engine, notifier: Notifier, start: date, end: date) -> None:
    """为每个启用用户推送 [start, end] 区间的汇总。"""
    label = f'{start.isoformat()} ~ {end.isoformat()}'
    with Session(engine) as s:
        for user in s.exec(select(User).where(User.enabled == True)).all():  # noqa: E712
            notifier.notify_period_summary(
                user_id=user.id,
                start_date_str=start.isoformat(),
                end_date_str=end.isoformat(),
                period_label=label,
            )


def _defer_summary(
    sched: BackgroundScheduler,
    engine: Engine,
    notifier: Notifier,
    func,
    job_id: str,
) -> None:
    """DND 期间被抑制时，在 DND 结束时刻（当天/次日 07:00）登记一次性顺延任务。

    spec §7.3：登记延后任务，而非依赖下次常规 tick 撞上。
    """
    now = datetime.now(_CST)
    dnd_end = now.replace(hour=7, minute=0, second=0, microsecond=0)
    if now >= dnd_end:
        dnd_end += timedelta(days=1)
    logger.info('dnd_defer_scheduled job=%s run_at=%s', job_id, dnd_end.isoformat())
    sched.add_job(
        func,
        'date',
        run_date=dnd_end,
        id=f'{job_id}_deferred',
        args=[engine, notifier, sched],
        replace_existing=True,
    )


def _expire_claims(engine: Engine) -> None:
    """兑奖过期扫描：deadline 已过 → expired。"""
    now = datetime.now(_CST)
    with Session(engine) as s:
        for claim in s.exec(
            select(PrizeClaim).where(
                PrizeClaim.status == 'pending',
                PrizeClaim.deadline < now,
            )
        ).all():
            claim.status = 'expired'
        s.commit()
