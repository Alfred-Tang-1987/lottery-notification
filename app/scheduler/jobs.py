from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from app.models import Comparison, DrawResult, NotificationLog, PrizeClaim, User
from app.notifications.notifier import Notifier
from app.scheduler import _JobDeps
from app.services.compare_service import CompareService
from app.services.fetch_service import FetchService
from app.services.refill_service import FloatRefillWorker

_CST = ZoneInfo('Asia/Shanghai')

logger = logging.getLogger(__name__)

# path_a_tick 串行抓取彩种间的间隔（秒）。MXNZP 免费 1 QPS，串行调 7 彩种会触发
# code=101 限流 → adapter 旧实现静默返回 None → 开奖静默漏抓（L-20260726T013000Z）。
# 1.2s 留 20% 安全余量；测试环境经 conftest autouse fixture 降为 0 避免拖慢。
_INTER_LOTTERY_INTERVAL = 1.2


# ---------------------------------------------------------------------------
# 依赖解析：调度任务通过 SQLAlchemyJobStore 持久化时会对 job args 做 pickle。
# engine（含 connect 闭包）/ services（持 httpx.Client）**均不可 pickle**——若作为 job
# args 直接传递，sched.start() 持久化即 PicklingError → 调度器无法启动 → 抓取/比对/推送
# 任务永不触发 → 中奖静默漏通知（spec §10 核心价值）。
#
# 解法：job args 只携带可 pickle 的 **database URL 字符串**；services + engine 经进程内
# 注册表按 URL 解析。register_all_jobs 在注册前按 URL 存 deps（并注入 _sched）；
# job 函数运行时按 URL 取回。注册表本身从不被 pickle（只 job args 序列化）。
#
# 跨进程恢复：jobstore 重启恢复 job（同进程内）时 register_all_jobs 已重新填充注册表，
# 故按 URL 取回一致；即使 jobstore 在新进程恢复，新进程启动必先 register_all_jobs 再
# sched.start，URL 键同样命中。
# ---------------------------------------------------------------------------
_DEPS_REGISTRY: dict[str, _JobDeps] = {}


def _resolve_deps(db_url: str) -> _JobDeps:
    """按 database URL 取回 register_all_jobs 注册的 deps。"""
    deps = _DEPS_REGISTRY.get(db_url)
    if deps is None:
        raise RuntimeError(f'scheduler deps 未注册（db_url={db_url}）；请先调用 register_all_jobs')
    return deps


def register_all_jobs(sched: BackgroundScheduler, deps: _JobDeps) -> None:
    """注册全部调度任务（spec §7.3）。

    deps 先按 database URL 入进程内注册表（job 运行时按 URL 解析，避免 pickle
    engine/services）；job args 只携带可 pickle 的 URL 字符串。
    """
    engine: Engine = deps['engine']
    db_url = str(engine.url)
    # 注入调度器实例，供 job 运行时 add_job（路径A 异步推送 / DND 顺延）使用。
    deps['_sched'] = sched
    _DEPS_REGISTRY[db_url] = deps

    # 路径A：开奖日 21:30-次日 01:00 每 15 分钟轮询（spec §7.3）。
    # 精确表达式拆三段：21:30/21:45；22:00-00:45 每 15 分；01:00。
    sched.add_job(
        _path_a_tick,
        'cron',
        hour='21',
        minute='30,45',
        id='path_a_poll_evening',
        args=[db_url],
        replace_existing=True,
    )
    sched.add_job(
        _path_a_tick,
        'cron',
        hour='22-23,0',
        minute='*/15',
        id='path_a_poll_overnight',
        args=[db_url],
        replace_existing=True,
    )
    sched.add_job(
        _path_a_tick,
        'cron',
        hour='1',
        minute='0',
        id='path_a_poll_end',
        args=[db_url],
        replace_existing=True,
    )

    # 路径B：次日 07:00 汇总推送
    sched.add_job(
        _path_b_summary,
        'cron',
        hour=7,
        minute=0,
        id='path_b_summary',
        args=[db_url],
        replace_existing=True,
    )

    # 浮奖回填：每日 08:00
    sched.add_job(
        _run_float_refill,
        'cron',
        hour=8,
        minute=0,
        id='float_refill',
        args=[db_url],
        replace_existing=True,
    )

    # 浮奖回填（开奖日当晚补充轮）：每晚 22:00。
    # 1C 决策——开奖后不久官方可能已公布浮动奖金额，补一轮回填提升时效性。
    # 复用 _run_float_refill（内部按 verified + draw_date 过滤，仅在确有未回填的
    # 开奖结果时才发起请求），故 22:00 这一轮对非开奖日为天然 no-op，不会过度拉取。
    sched.add_job(
        _run_float_refill,
        'cron',
        hour=22,
        minute=0,
        id='float_refill_night',
        args=[db_url],
        replace_existing=True,
    )

    # 兑奖过期扫描：每日 07:30
    sched.add_job(
        _expire_claims,
        'cron',
        hour=7,
        minute=30,
        id='claim_expire_scan',
        args=[db_url],
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
        args=[db_url],
        replace_existing=True,
    )
    sched.add_job(
        _monthly_report,
        'cron',
        day=1,
        hour=9,
        minute=0,
        id='monthly_report',
        args=[db_url],
        replace_existing=True,
    )


def _path_a_tick(db_url: str) -> None:
    """开奖时段：抓取 → outbox claim 比对 → 命中一二等异步推送（不阻塞比对事务，spec §7.1）。"""
    deps = _resolve_deps(db_url)
    engine: Engine = deps['engine']
    fetch_service: FetchService = deps['fetch_service']
    compare_service: CompareService = deps['compare_service']
    sched: BackgroundScheduler = deps['_sched']

    from app.seeds import SPECS

    for i, spec in enumerate(SPECS):
        # 第二个彩种起抓取前 sleep _INTER_LOTTERY_INTERVAL，避免 MXNZP 1 QPS 限流
        # 触发 code=101 → 静默漏抓（L-20260726T013000Z）。第一彩种无需等待。
        if i > 0:
            time.sleep(_INTER_LOTTERY_INTERVAL)
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
    # L-20260725T064200Z: day-window bounds 必须与 DrawResult.draw_date 同 tz 表示。
    # fetch_service.py:229 写 draw_date 为 aware-CST（datetime.combine(d, min.time(), tzinfo=_CST)）。
    # 若 bounds 用 naive（旧实现），SQLite 字符串比较依赖方言 strip-tzinfo 才侥幸一致——
    # 方言/版本漂移即静默漏比对当日大奖（违反「中奖永不静默漏通知」核心价值，spec §10；
    # CLAUDE.md datetime 时区对齐纪律：bounds 与列须同时区同数值）。
    day_start = datetime.combine(today, datetime.min.time(), tzinfo=_CST)
    day_end = datetime.combine(today + timedelta(days=1), datetime.min.time(), tzinfo=_CST)
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
        # 注册模块级 top-level 函数为 job func：job args 只携带可 pickle 的 db_url 字符串
        # + 原始类型 params（comparison_id/lottery_name/draw_no/tier/amount）。绝不可直接注册
        # bound method notifier.notify_path_a——SQLAlchemyJobStore 持久化 job 时会 pickle
        # job func + args + kwargs，bound method 会连带序列化 Notifier（持真实 BarkChannel/
        # FeishuChannel 的 httpx.Client [_thread.RLock 不可 pickle] + Notifier._engine 的
        # SQLAlchemy connect 闭包）→ PicklingError/AttributeError → sched.start() 崩溃 →
        # 调度器无法启动 → 中奖静默漏通知（spec §10）。运行时经 _resolve_deps(db_url) 取回
        # notifier 后调用，与 _path_a_tick/_path_b_summary 同构（见 _push_big_win）。
        sched.add_job(_push_big_win, 'date', args=[db_url], kwargs=params)


def _push_big_win(db_url: str, **params) -> None:
    """路径A 大奖异步推送 job 入口（spec §7.1）。

    **模块级 top-level 函数**（非 bound method），经进程内注册表按 db_url 解析 notifier
    后调用 notify_path_a——避免 SQLAlchemyJobStore 持久化时 pickle Notifier（httpx.Client
    + engine 闭包不可 pickle）导致 sched.start() 崩溃。job args 只带可 pickle 的
    (db_url, params)；注册表本身从不被 pickle（只 job args 序列化）。
    """
    deps = _resolve_deps(db_url)
    notifier: Notifier = deps['notifier']
    notifier.notify_path_a(**params)


def _path_b_summary(db_url: str) -> None:
    """次日汇总：对每个用户推前一天的核对结果；DND 时登记顺延任务。"""
    deps = _resolve_deps(db_url)
    engine: Engine = deps['engine']
    notifier: Notifier = deps['notifier']
    sched: BackgroundScheduler = deps['_sched']
    if notifier.is_dnd_active():
        _defer_summary(sched, db_url, _path_b_summary, 'path_b_summary')
        return
    yesterday = (datetime.now(_CST).date() - timedelta(days=1)).isoformat()
    with Session(engine) as s:
        for user in s.exec(select(User).where(User.enabled == True)).all():  # noqa: E712
            # L-20260725T064200Z: per-user 隔离——单个用户的 notify_path_b 抛任何异常
            # （transient DB 错 / 渠道配置解密失败 / httpx 传输异常 / admin bark 误抛）
            # 不得冒泡中断 for 循环，否则后续用户当日汇总静默漏通知（CLAUDE.md：批量循环里
            # 单行故障不得中断整批）。与 _path_a_tick 已隔离的 per-lottery fetch 循环同构。
            try:
                notifier.notify_path_b(user_id=user.id, date_str=yesterday)
            except Exception:
                logger.error('path_b_user_failed user_id=%s', user.id, exc_info=True)


def _weekly_report(db_url: str) -> None:
    """周报：汇总上周一 00:00 至上周日 23:59 的核对结果。"""
    deps = _resolve_deps(db_url)
    engine: Engine = deps['engine']
    notifier: Notifier = deps['notifier']
    sched: BackgroundScheduler = deps['_sched']
    if notifier.is_dnd_active():
        _defer_summary(sched, db_url, _weekly_report, 'weekly_report')
        return
    today = datetime.now(_CST).date()
    # 上周日 = today - weekday - 1（若 today 是周日，则上周日 = today - 7）
    days_since_sunday = (today.weekday() + 1) % 7
    last_sunday = today - timedelta(days=days_since_sunday)
    last_monday = last_sunday - timedelta(days=6)
    _push_period_summary(engine, notifier, last_monday, last_sunday)


def _monthly_report(db_url: str) -> None:
    """月报：汇总上月 1 日至上月最后一天的核对结果。"""
    deps = _resolve_deps(db_url)
    engine: Engine = deps['engine']
    notifier: Notifier = deps['notifier']
    sched: BackgroundScheduler = deps['_sched']
    if notifier.is_dnd_active():
        _defer_summary(sched, db_url, _monthly_report, 'monthly_report')
        return
    today = datetime.now(_CST).date()
    first_of_this_month = today.replace(day=1)
    last_of_prev_month = first_of_this_month - timedelta(days=1)
    first_of_prev_month = last_of_prev_month.replace(day=1)
    _push_period_summary(engine, notifier, first_of_prev_month, last_of_prev_month)


def _run_float_refill(db_url: str) -> None:
    """浮奖回填（每日 08:00）。经注册表解析 worker，避免 pickle。"""
    deps = _resolve_deps(db_url)
    refill_worker: FloatRefillWorker = deps['refill_worker']
    try:
        refill_worker.refill()
    except Exception:
        # 回填整体故障不得拖垮调度器线程（后续任务仍须执行）。
        logger.error('float_refill_job_failed', exc_info=True)


def _push_period_summary(engine: Engine, notifier: Notifier, start: date, end: date) -> None:
    """为每个启用用户推送 [start, end] 区间的汇总。"""
    label = f'{start.isoformat()} ~ {end.isoformat()}'
    with Session(engine) as s:
        for user in s.exec(select(User).where(User.enabled == True)).all():  # noqa: E712
            # L-20260725T064200Z: per-user 隔离——与 _path_b_summary 同构。单用户抛异常不得
            # 中断后续用户的周报/月报推送（per-user 静默漏通知）。
            try:
                notifier.notify_period_summary(
                    user_id=user.id,
                    start_date_str=start.isoformat(),
                    end_date_str=end.isoformat(),
                    period_label=label,
                )
            except Exception:
                logger.error(
                    'period_summary_user_failed user_id=%s period=%s',
                    user.id,
                    label,
                    exc_info=True,
                )


def _defer_summary(
    sched: BackgroundScheduler,
    db_url: str,
    func,
    job_id: str,
) -> None:
    """DND 期间被抑制时，在 DND 结束时刻（当天/次日 07:00）登记一次性顺延任务。

    spec §7.3：登记延后任务，而非依赖下次常规 tick 撞上。
    顺延任务 args 同样只携带可 pickle 的 db_url。
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
        args=[db_url],
        replace_existing=True,
    )


def _expire_claims(db_url: str) -> None:
    """兑奖过期扫描：deadline 已过 → expired。"""
    deps = _resolve_deps(db_url)
    engine: Engine = deps['engine']
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
