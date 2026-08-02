"""端到端调度推送集成测试（spec §7.1 路径A / §8 推送 / §7.3 调度启动）。

Plan 04 各层（services / scheduler / notifications）已分别单测覆盖；本文件覆盖两层
集成回归点：

1. **跨层推送链路**（test_full_loop_fetch_compare_then_push）：真实 FetchService(双源
   MagicMock) → CompareService → 命中一等奖 → Notifier.notify_path_a → 渠道被调 +
   NotificationLog.status='sent'。回归「比对生成的 comparison 能被推送正确消费」，
   不依赖手工 INSERT 的字段一致性（test_core_loop 只到 comparison 为止，不含推送）。

2. **应用启动接线**（test_build_scheduler_and_deps_* / test_lifespan_*）：T7 新代码——
   `app/main.py` 在 lifespan 内构建 services + scheduler + 注册任务 + 启动/关闭。
   `_build_scheduler_and_deps` 是纯构造（无网络/无 start），直接断言任务与服务类型；
   lifespan 启停用 TestClient 验证 app.state.scheduler 生命周期（monkeypatch 构造函数
   返回真实 scheduler + mock deps，避免真实抓取网络，测试的是 lifespan 装配逻辑本身）。
"""

import json
from datetime import datetime
from unittest.mock import MagicMock

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.config import reset_settings_cache
from app.models import (
    Comparison,
    NotificationChannel,
    NotificationLog,
    Ticket,
    User,
)
from app.notifications.base import ChannelStatus, SendResult
from app.notifications.notifier import Notifier

# ---------- 跨层推送链路回归 ----------


def _make_notifier(db_engine):
    """真实 Notifier + mock bark 渠道（返回 SENT）+ mock crypto（解密返回明文 bark 配置）。"""
    bark = MagicMock()
    bark.send.return_value = SendResult(status=ChannelStatus.SENT, error=None)
    bark.type = 'bark'
    crypto = MagicMock()
    crypto.decrypt.return_value = '{"key":"k","url":"https://api.day.app"}'
    return Notifier(db_engine, channels={'bark': bark}, crypto=crypto), bark


def test_full_loop_fetch_compare_then_push(db_engine):
    """全链路：真实 FetchService(双源 MagicMock) → CompareService → 命中 → 路径A 推送。

    比 test_core_loop 多走一步「推送」：验证比对生成的 comparison 能被 notify_path_a
    正确消费（comparison_id 衔接无误），NotificationLog 状态从 pending 流转为 sent。
    """
    from app.adapters.base import DrawNumbers
    from app.services.compare_service import CompareService
    from app.services.fetch_service import FetchService

    with Session(db_engine) as s:
        u = User(username='loop_user', password_hash='x', role='user', invite_code='L')
        s.add(u)
        s.commit()
        s.refresh(u)
        s.add(
            NotificationChannel(
                user_id=u.id,
                type='bark',
                config_json=json.dumps({'ct': 'enc'}),
                enabled=True,
                key_version=1,
            )
        )
        s.add(
            Ticket(
                user_id=u.id,
                lottery_code='ssq',
                play_type='single',
                numbers_json=json.dumps({'front': [1, 2, 3, 4, 5, 6], 'back': [7]}),
                multiplier=1,
                cost=200,
                enabled=True,
            )
        )
        s.commit()
        uid = u.id

    dn = DrawNumbers(
        lottery_code='ssq',
        draw_no='062',
        draw_date=datetime(2026, 6, 21).date(),
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

    notifier, bark = _make_notifier(db_engine)
    with Session(db_engine) as s:
        cmp = s.exec(
            select(Comparison).where(Comparison.user_id == uid, Comparison.is_win == True)  # noqa: E712
        ).first()
        assert cmp is not None and cmp.prize_tier == 1  # 6红+1蓝 = 一等奖
        cmp_id = cmp.id

    notifier.notify_path_a(
        comparison_id=cmp_id, lottery_name='双色球', draw_no='062', tier=1, amount=None
    )
    bark.send.assert_called_once()
    with Session(db_engine) as s:
        log = s.exec(
            select(NotificationLog).where(NotificationLog.comparison_id == cmp_id)
        ).first()
        assert log is not None and log.status == 'sent'


# ---------- T7 新代码：应用启动接线 ----------


def _valid_env(monkeypatch):
    """注入有效启动环境变量（真实 Fernet key），与 test_health 一致。"""
    reset_settings_cache()
    monkeypatch.setenv('JWT_SECRET', 'x' * 32)
    monkeypatch.setenv('CRYPTO_KEY_V1', Fernet.generate_key().decode())
    monkeypatch.setenv('SCHEDULER_ENABLED', 'false')  # 默认禁用，避免 lifespan 抓取网络


def test_build_scheduler_and_deps_registers_jobs_and_wires_services(db_engine, monkeypatch):
    """`_build_scheduler_and_deps` 构造 services + scheduler + 注册全部任务，返回 (sched, deps)。

    回归点：T7 启动接线必须把 Plan 03 的 services + Plan 04 的 scheduler/notifier 正确
    组装为 _JobDeps，并注册 §7.3 全部任务（路径A/B/回填/过期/周月报）。该函数纯构造——
    不抓取、不 start、不 backfill，故无网络副作用，可断言服务类型与任务 id。
    """
    from app.config import get_settings
    from app.main import _build_scheduler_and_deps
    from app.notifications.notifier import Notifier
    from app.services.compare_service import CompareService
    from app.services.fetch_service import FetchService
    from app.services.refill_service import FloatRefillWorker

    _valid_env(monkeypatch)
    settings = get_settings()

    sched, deps = _build_scheduler_and_deps(db_engine, settings)

    # deps 类型正确（不是 MagicMock / dict 残缺）
    assert isinstance(deps['engine'], type(db_engine))
    assert isinstance(deps['fetch_service'], FetchService)
    assert isinstance(deps['compare_service'], CompareService)
    assert isinstance(deps['refill_worker'], FloatRefillWorker)
    assert isinstance(deps['notifier'], Notifier)
    # 注册了 §7.3 全部任务
    job_ids = {j.id for j in sched.get_jobs()}
    for expected in (
        'path_a_poll_evening',
        'path_a_poll_overnight',
        'path_a_poll_end',
        'path_b_summary',
        'float_refill',
        'claim_expire_scan',
        'weekly_report',
        'monthly_report',
    ):
        assert expected in job_ids, f'缺少调度任务 {expected}'

    # 关键生产保证：sched.start() 经 SQLAlchemyJobStore 持久化 job 时会对 args 做 pickle。
    # job args 必须可 pickle（db_url 字符串）——若误传 engine/services 会 PicklingError
    # → 调度器无法启动 → 抓取/比对/推送任务永不触发 → 中奖静默漏通知（spec §10）。
    sched.start()
    assert sched.running
    # start 后任务仍在（持久化成功未被丢弃）
    assert {j.id for j in sched.get_jobs()} >= job_ids
    sched.shutdown(wait=False)
    # 释放渠道持有的 httpx 连接池（BarkChannel/FeishuChannel）避免泄漏。
    deps['notifier'].close()


def test_lifespan_starts_and_stops_scheduler(monkeypatch, db_engine):
    """启用 scheduler 时 lifespan 启动调度器并挂 app.state；退出时关闭。

    通过 monkeypatch `_build_scheduler_and_deps` 返回真实 build_scheduler(db_engine) +
    mock deps（process_pending/fetch_and_store/refill 无副作用），隔离真实抓取网络，
    专注验证 lifespan 的「启动→挂载→关闭」装配逻辑（spec §7.3 启动 backfill + 调度）。
    """
    import app.main as main_mod
    from app.scheduler.setup import build_scheduler

    _valid_env(monkeypatch)
    monkeypatch.setenv('SCHEDULER_ENABLED', 'true')  # 本测试要验证启用路径

    real_sched = build_scheduler(db_engine)
    mock_deps = {
        'engine': db_engine,
        'fetch_service': MagicMock(),
        'compare_service': MagicMock(),
        'refill_worker': MagicMock(),
        'notifier': MagicMock(),
        'channels': {},
    }
    monkeypatch.setattr(
        main_mod, '_build_scheduler_and_deps', lambda engine, settings: (real_sched, mock_deps)
    )

    client = TestClient(main_mod.app)
    with client:
        # lifespan 启动后 scheduler 已挂载并 running
        sched = getattr(main_mod.app.state, 'scheduler', None)
        assert sched is real_sched
        assert sched.running
    # 退出 lifespan 后 scheduler 已关闭
    assert not real_sched.running


def test_lifespan_skips_scheduler_when_disabled(monkeypatch, db_engine):
    """禁用 scheduler 时 lifespan 不启动调度器（运维开关，spec §4.3 单容器可关调度排障）。"""
    import app.main as main_mod

    _valid_env(monkeypatch)  # SCHEDULER_ENABLED=false

    called = {'build': False}

    def _should_not_run(engine, settings):
        called['build'] = True
        return build_scheduler_placeholder()

    monkeypatch.setattr(main_mod, '_build_scheduler_and_deps', _should_not_run)
    # 让 health 探活用隔离 engine，避免连默认 ./data/lottery.db
    main_mod.app.dependency_overrides[main_mod.get_db_for_health] = lambda: db_engine
    try:
        client = TestClient(main_mod.app)
        with client:
            r = client.get('/health')
            assert r.status_code == 200
            assert not hasattr(main_mod.app.state, 'scheduler') or getattr(
                main_mod.app.state, 'scheduler', None
            ) is None
    finally:
        main_mod.app.dependency_overrides.clear()
    assert not called['build'], '禁用 scheduler 时不应构建调度器'


def build_scheduler_placeholder():
    """仅占位，禁用路径不应被调用（被调用即测试失败）。"""
    raise AssertionError('scheduler 不应在禁用时被构建')
