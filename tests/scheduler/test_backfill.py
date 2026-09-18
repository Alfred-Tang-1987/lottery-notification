from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest
from sqlmodel import Session

from app.models import DrawResult
from app.scheduler.backfill import _CST, run_startup_backfill
from app.seeds import SPECS


def _make_deps(engine):
    return {
        'engine': engine,
        'fetch_service': MagicMock(),
        'compare_service': MagicMock(),
        'refill_worker': MagicMock(),
        'notifier': MagicMock(),
    }


def _mock_settings_with_keys():
    """构造「数据源 key 已配置」的 settings 对象，供 backfill pre-check 放行抓取路径。

    既有 backfill 测试默认走抓取路径；backfill 现在会 `get_settings()` 判 key，
    不 mock 会读真实环境（CI 无 .env → key 空 → 触发 skip → 抓取断言失败）。
    测试无 key 场景时自行 monkeypatch 覆盖。用 MagicMock 避免 Settings 的 alias/env 坑。
    """
    return MagicMock(mxnzp_api_key='test-key', juhe_api_key='test-key')


@pytest.fixture(autouse=True)
def _backfill_settings_with_keys(monkeypatch):
    """默认让 backfill 的 get_settings() 返回「有 key」配置，放行抓取路径。

    autouse：既有测试无需逐个改。测「无 key 跳过」场景的测试自行覆盖此 patch。
    """
    monkeypatch.setattr(
        'app.scheduler.backfill.get_settings', _mock_settings_with_keys
    )


def test_backfill_processes_pending_comparisons(db_engine):
    """启动 backfill 应处理未认领的 pending_comparisons。

    空库场景 missed 检查命中、触发补抓，步骤 5 会再跑一次 outbox 处理
    （次数语义由 test_backfill_reprocesses_outbox_after_missed_draw_fetch 锁定），
    此处只断言「被处理」这一核心不变量。
    """
    deps = _make_deps(db_engine)
    deps['compare_service'].process_pending = MagicMock(return_value=0)

    run_startup_backfill(deps)

    deps['compare_service'].process_pending.assert_called()


def test_enabled_lotteries_reads_draw_schedule_json(db_engine):
    """启用彩种的开奖日必须从 draw_schedule_json 读取（spec §7.3）。"""
    import json as _json

    from app.models import LotteryType
    from app.scheduler.backfill import _enabled_lotteries

    with Session(db_engine) as s:
        s.add(
            LotteryType(
                code='ssq',
                name='双色球',
                category='welfare',
                spec_json=_json.dumps({'code': 'ssq'}),
                draw_schedule_json=_json.dumps({'draw_days': [1, 3, 6]}),
                enabled=True,
            )
        )
        s.commit()

    codes_days = _enabled_lotteries(db_engine)
    assert ('ssq', [1, 3, 6]) in codes_days


def test_backfill_refetches_missed_draws(db_engine, monkeypatch):
    """宕机窗口内应开奖但未抓的彩种，启动时应补抓（固定到 ssq 开奖日，避免按周天波动）。"""
    deps = _make_deps(db_engine)
    fetch = deps['fetch_service']

    # 2026-06-28 是周日（ssq 开奖日），lookback [Sun, Sat] 包含开奖日。
    fixed_sunday = datetime(2026, 6, 28, 10, 0, 0, tzinfo=_CST)

    class _FixedNow(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_sunday if tz is None else fixed_sunday.astimezone(tz)

    monkeypatch.setattr('app.scheduler.backfill.datetime', _FixedNow)

    run_startup_backfill(deps)

    called_codes = {c.args[0] for c in fetch.fetch_and_store.call_args_list}
    assert 'ssq' in called_codes


def test_backfill_skips_lottery_with_existing_draw(db_engine):
    """当日已有开奖结果的彩种不再重复补抓。"""
    deps = _make_deps(db_engine)
    fetch = deps['fetch_service']

    today = datetime.now(_CST).date()
    for offset in (0, 1):
        day = today - timedelta(days=offset)
        day_start = datetime.combine(day, datetime.min.time())
        with Session(db_engine) as s:
            # 最近 2 天各 spec 都已有开奖结果
            for spec in SPECS:
                s.add(
                    DrawResult(
                        lottery_code=spec['code'],
                        draw_no=f'day_{offset}',
                        draw_date=day_start,
                        numbers_json='{}',
                        source='mxnzp',
                        verified=True,
                        version=1,
                    )
                )
            s.commit()

    run_startup_backfill(deps)

    # 最近 2 天结果均已存在，不应触发补抓
    fetch.fetch_and_store.assert_not_called()


def test_backfill_isolates_fetch_failure_per_lottery(db_engine):
    """单个彩种补抓异常不得阻断其他彩种与 outbox 处理。"""
    deps = _make_deps(db_engine)
    deps['compare_service'].process_pending = MagicMock(return_value=0)
    fetch = deps['fetch_service']

    def side_effect(code):
        if code == 'ssq':
            raise RuntimeError('ssq source outage')

    fetch.fetch_and_store.side_effect = side_effect

    run_startup_backfill(deps)

    # ssq 之外的彩种补抓成功（fetched>0）→ 步骤 5 再跑一次 outbox 处理也属预期；
    # 本测试锁定的核心不变量是「fetch 故障不阻断 outbox 处理」。
    deps['compare_service'].process_pending.assert_called()
    # 其余彩种仍被补抓
    assert fetch.fetch_and_store.call_count > 1


def test_backfill_isolates_bad_schedule_json(db_engine, monkeypatch):
    """一行坏 draw_schedule_json 不得阻断其余正常彩种补抓。"""
    import json as _json

    from app.models import LotteryType

    fixed_sunday = datetime(2026, 6, 28, 10, 0, 0, tzinfo=_CST)

    class _FixedNow(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_sunday if tz is None else fixed_sunday.astimezone(tz)

    monkeypatch.setattr('app.scheduler.backfill.datetime', _FixedNow)

    with Session(db_engine) as s:
        s.add(
            LotteryType(
                code='bad_lt',
                name='Bad Lottery',
                category='welfare',
                spec_json=_json.dumps({'code': 'bad_lt'}),
                draw_schedule_json='{bad',
                enabled=True,
            )
        )
        s.add(
            LotteryType(
                code='ssq',
                name='双色球',
                category='welfare',
                spec_json=_json.dumps({'code': 'ssq'}),
                draw_schedule_json=_json.dumps({'draw_days': [1, 3, 6]}),
                enabled=True,
            )
        )
        s.commit()

    deps = _make_deps(db_engine)
    fetch = deps['fetch_service']

    run_startup_backfill(deps)

    called_codes = {c.args[0] for c in fetch.fetch_and_store.call_args_list}
    assert 'ssq' in called_codes
    assert 'bad_lt' not in called_codes


# ---------------------------------------------------------------- 数据源未配置时跳过抓取
def test_backfill_skips_fetch_when_both_keys_empty(db_engine, monkeypatch):
    """两个数据源 key 都未配置时，backfill 不应触发任何抓取（spec §7.3）。

    回归点：无 key 时 fetch_and_store 注定全失败，触发 7 彩种 × 12 次退避重试，
    阻塞应用启动数分钟（healthcheck 超时 → restart:always 无限重启循环）。
    pre-check 判定无 key → 整段跳过抓取；outbox（process_pending）不受影响。
    """
    # 覆盖 autouse fixture：模拟「双 key 均空」
    monkeypatch.setattr(
        'app.scheduler.backfill.get_settings',
        lambda: MagicMock(mxnzp_api_key='', juhe_api_key=''),
    )

    deps = _make_deps(db_engine)
    fetch = deps['fetch_service']
    compare = deps['compare_service']

    run_startup_backfill(deps)

    # 抓取完全跳过（无意义重试）
    fetch.fetch_and_store.assert_not_called()
    # outbox 处理不受数据源配置影响
    compare.process_pending.assert_called_once()


def test_backfill_fetches_when_keys_configured(db_engine, monkeypatch):
    """数据源 key 已配置时，backfill 正常抓取遗漏彩种（防 pre-check 过度跳过）。"""
    # 覆盖 autouse fixture：仅 mxnzp 配了 key（备源空）也该放行
    monkeypatch.setattr(
        'app.scheduler.backfill.get_settings',
        lambda: MagicMock(mxnzp_api_key='configured-key', juhe_api_key=''),
    )
    # 2026-06-29 是周一（draw_days=[0,2,4] 命中），lookback=[周一,周日] 含周一 → 触发 missed。
    fixed_monday = datetime(2026, 6, 29, 10, 0, 0, tzinfo=_CST)

    class _FixedNow(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_monday if tz is None else fixed_monday.astimezone(tz)

    monkeypatch.setattr('app.scheduler.backfill.datetime', _FixedNow)
    from app.models import LotteryType

    with Session(db_engine) as s:
        s.add(LotteryType(code='ssq', name='双色球', category='welfare', spec_json='{}', draw_schedule_json='{"draw_days":[0,2,4]}', enabled=True))
        s.commit()

    deps = _make_deps(db_engine)
    fetch = deps['fetch_service']

    run_startup_backfill(deps)

    called_codes = {c.args[0] for c in fetch.fetch_and_store.call_args_list}
    assert 'ssq' in called_codes


# ──────────────────────────────────────────────
# 启动历史回填（backfill_history）：新库 draw_results 为空时抓 50 期历史
# ──────────────────────────────────────────────


def test_backfill_history_fills_when_db_empty(db_engine, monkeypatch):
    """启动时某彩种 draw_results 表为空 → 调用 fetch_history 抓 50 期并入库。

    回填数据标记 single_source=True（无聚合双源校验，单源降级语义）。
    """
    from datetime import date as _date

    from app.adapters.base import DrawNumbers
    from app.models import LotteryType

    # 种 1 个启用彩种
    with Session(db_engine) as s:
        s.add(LotteryType(code='ssq', name='双色球', category='welfare',
                          spec_json='{}', draw_schedule_json='{"draw_days":[0,2,4]}', enabled=True))
        s.commit()

    # mock MxnzpAdapter.fetch_history 返回 2 期数据
    fake_draws = [
        DrawNumbers(lottery_code='ssq', draw_no='062', draw_date=_date(2026, 6, 12),
                    front=(1, 2, 3, 4, 5, 6), back=(7,)),
        DrawNumbers(lottery_code='ssq', draw_no='061', draw_date=_date(2026, 6, 10),
                    front=(8, 11, 15, 22, 29, 33), back=(12,)),
    ]
    deps = _make_deps(db_engine)
    deps['fetch_service']._primary = MagicMock()
    deps['fetch_service']._primary.name = 'mxnzp'
    deps['fetch_service']._primary.fetch_history = MagicMock(return_value=fake_draws)

    run_startup_backfill(deps)

    deps['fetch_service']._primary.fetch_history.assert_called_once_with('ssq', size=50)
    with Session(db_engine) as s:
        from sqlmodel import select
        rows = list(s.exec(select(DrawResult).where(DrawResult.lottery_code == 'ssq')).all())
        assert len(rows) == 2
        assert all(r.single_source for r in rows)
        assert all(r.verified for r in rows)


def test_backfill_history_skips_when_db_has_data(db_engine):
    """DB 已有该彩种数据（非空） → 不触发历史回填（避免重复抓取）。"""
    from app.models import DrawResult, LotteryType

    with Session(db_engine) as s:
        s.add(LotteryType(code='ssq', name='双色球', category='welfare',
                          spec_json='{}', draw_schedule_json='{"draw_days":[0,2,4]}', enabled=True))
        s.add(DrawResult(lottery_code='ssq', draw_no='062',
                         draw_date=datetime(2026, 6, 12, tzinfo=_CST),
                         numbers_json='{"front":[1,2,3,4,5,6],"back":[7]}',
                         source='mxnzp', fetched_at=datetime.utcnow(),
                         verified=True, single_source=True, version=1))
        s.commit()

    deps = _make_deps(db_engine)
    deps['fetch_service']._primary = MagicMock()
    deps['fetch_service']._primary.name = 'mxnzp'
    deps['fetch_service']._primary.fetch_history = MagicMock()

    run_startup_backfill(deps)

    deps['fetch_service']._primary.fetch_history.assert_not_called()


def test_backfill_history_skips_when_no_mxnzp_key(db_engine, monkeypatch):
    """MXNZP key 未配置 → 跳过历史回填（fetch_history 会抛 PermanentLookupError）。"""
    from app.models import LotteryType

    with Session(db_engine) as s:
        s.add(LotteryType(code='ssq', name='双色球', category='welfare',
                          spec_json='{}', draw_schedule_json='{"draw_days":[0,2,4]}', enabled=True))
        s.commit()

    monkeypatch.setattr(
        'app.scheduler.backfill.get_settings',
        lambda: MagicMock(mxnzp_api_key='', juhe_api_key='test-key')
    )

    deps = _make_deps(db_engine)
    deps['fetch_service']._primary = MagicMock()
    deps['fetch_service']._primary.name = 'mxnzp'
    deps['fetch_service']._primary.fetch_history = MagicMock()

    run_startup_backfill(deps)

    deps['fetch_service']._primary.fetch_history.assert_not_called()


def test_backfill_history_isolates_per_lottery_failure(db_engine):
    """某彩种 fetch_history 失败不阻断其他彩种（silent-failure 纪律）。"""
    from app.adapters.base import PermanentLookupError
    from app.models import LotteryType

    with Session(db_engine) as s:
        s.add(LotteryType(code='ssq', name='双色球', category='welfare',
                          spec_json='{}', draw_schedule_json='{"draw_days":[0,2,4]}', enabled=True))
        s.add(LotteryType(code='dlt', name='大乐透', category='sports',
                          spec_json='{}', draw_schedule_json='{"draw_days":[0,2,4]}', enabled=True))
        s.commit()

    deps = _make_deps(db_engine)
    deps['fetch_service']._primary = MagicMock()
    deps['fetch_service']._primary.name = 'mxnzp'
    deps['fetch_service']._primary.fetch_history = MagicMock(
        side_effect=[PermanentLookupError('test'), []]
    )

    run_startup_backfill(deps)

    assert deps['fetch_service']._primary.fetch_history.call_count == 2


def test_backfill_history_idempotent_on_restart(db_engine):
    """重复调用 backfill 不会重复入库（幂等：唯一约束 + 已有数据跳过）。"""
    from datetime import date as _date

    from app.adapters.base import DrawNumbers
    from app.models import LotteryType

    with Session(db_engine) as s:
        s.add(LotteryType(code='ssq', name='双色球', category='welfare',
                          spec_json='{}', draw_schedule_json='{"draw_days":[0,2,4]}', enabled=True))
        s.commit()

    fake_draws = [
        DrawNumbers(lottery_code='ssq', draw_no='062', draw_date=_date(2026, 6, 12),
                    front=(1, 2, 3, 4, 5, 6), back=(7,)),
    ]
    deps = _make_deps(db_engine)
    deps['fetch_service']._primary = MagicMock()
    deps['fetch_service']._primary.name = 'mxnzp'
    deps['fetch_service']._primary.fetch_history = MagicMock(return_value=fake_draws)

    run_startup_backfill(deps)
    deps['fetch_service']._primary.fetch_history.reset_mock()
    run_startup_backfill(deps)
    deps['fetch_service']._primary.fetch_history.assert_not_called()

    with Session(db_engine) as s:
        from sqlmodel import select
        rows = list(s.exec(select(DrawResult).where(DrawResult.lottery_code == 'ssq')).all())
        assert len(rows) == 1


def test_startup_backfill_paces_fetches_with_interval(db_engine, monkeypatch):
    """启动回填对实际抓取的彩种加 QPS 间隔：第 2 个起每次 fetch 前 sleep（plan-11）。"""
    import app.scheduler.backfill as backfill_mod

    monkeypatch.setattr(backfill_mod.jobs_mod, '_INTER_LOTTERY_INTERVAL', 1.2)
    sleeps = []
    monkeypatch.setattr(backfill_mod.time, 'sleep', lambda s: sleeps.append(s))

    # 3 个彩种全部 missed（DB 无开奖 + draw_days 覆盖回看窗口）
    monkeypatch.setattr(
        backfill_mod, '_enabled_lotteries',
        lambda engine: [('a', [0, 1, 2, 3, 4, 5, 6]), ('b', [0, 1, 2, 3, 4, 5, 6]),
                        ('c', [0, 1, 2, 3, 4, 5, 6])],
    )
    monkeypatch.setattr(backfill_mod, '_has_draw_for_date', lambda engine, code, d: False)

    deps = _make_deps(db_engine)
    run_startup_backfill(deps)
    assert deps['fetch_service'].fetch_and_store.call_count == 3
    assert sleeps == [1.2, 1.2]  # 首个抓取不 sleep


def test_startup_backfill_evaluates_source_alerts(db_engine, monkeypatch):
    """启动 backfill 尾部评估健康告警（plan-11：开机即评估）。"""
    import app.scheduler.backfill as backfill_mod

    spy = MagicMock()
    monkeypatch.setattr(backfill_mod, 'evaluate_source_alerts', spy)
    run_startup_backfill(_make_deps(db_engine))
    assert spy.call_count == 1


def test_startup_backfill_evaluate_failure_isolated(db_engine, monkeypatch, caplog):
    """启动 backfill 尾部 evaluate_source_alerts 抛异常不得阻断/上抛（characterization 回归钉）。

    终审 deferred：评估失败隔离 try/except 位于 run_startup_backfill 函数尾部、独立于
    抓取循环的 try/except（backfill.py 尾部），行为已正确，本测试为 characterization
    测试（预期无 RED）——钉死「不传播 + 记录 source_alert_evaluate_failed」契约。
    """
    import logging

    import app.scheduler.backfill as backfill_mod

    def _boom(engine, factory):
        raise RuntimeError('evaluate outage (transient DB/alert channel)')

    monkeypatch.setattr(backfill_mod, 'evaluate_source_alerts', _boom)
    with caplog.at_level(logging.WARNING, logger='app.scheduler.backfill'):
        run_startup_backfill(_make_deps(db_engine))  # 不传播 = 调用正常完成
    assert 'source_alert_evaluate_failed' in caplog.text


def test_backfill_reprocesses_outbox_after_missed_draw_fetch(db_engine, monkeypatch):
    """步骤 4 补抓遗漏开奖后应再跑一次 process_pending。

    抓取落库同事务写 pending_comparisons outbox；步骤 1 的处理在抓取**之前**，
    只补抓不补比会让遗漏期的比对迟到当晚 21:30 tick（半天的中奖静默窗口）。
    """
    deps = _make_deps(db_engine)
    monkeypatch.setattr(
        'app.scheduler.backfill.push_win_catchups', MagicMock(return_value=0)
    )
    fixed_now = datetime(2026, 9, 15, 13, 0, 0, tzinfo=_CST)

    class _FixedNow(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now if tz is None else fixed_now.astimezone(tz)

    monkeypatch.setattr('app.scheduler.backfill.datetime', _FixedNow)
    # 已无 9-15 当期数据 → missed 检查命中 → 触发补抓
    run_startup_backfill(deps)
    assert deps['compare_service'].process_pending.call_count == 2


def test_backfill_runs_win_catchup_sweep(db_engine, monkeypatch):
    """启动 backfill 收尾应执行回填中奖补推（2026-09-15 事故：补抓+补比产生的
    隔期中奖无常规推送路径覆盖，须当场补推）。"""
    deps = _make_deps(db_engine)
    called = {}
    monkeypatch.setattr(
        'app.scheduler.backfill.push_win_catchups',
        lambda d: called.update(deps=d) or 2,
    )
    run_startup_backfill(deps)
    assert called.get('deps') is deps


def test_backfill_runs_win_catchup_sweep_even_when_keys_empty(db_engine, monkeypatch):
    """无数据源 key 提前 return 的路径也要跑补推扫描——outbox（步骤 1）已处理，
    其中产生的迟到中奖同样不能静默漏推。"""
    deps = _make_deps(db_engine)
    called = {}
    monkeypatch.setattr(
        'app.scheduler.backfill.get_settings',
        lambda: MagicMock(mxnzp_api_key='', juhe_api_key=''),
    )
    monkeypatch.setattr(
        'app.scheduler.backfill.push_win_catchups',
        lambda d: called.update(deps=d) or 0,
    )
    run_startup_backfill(deps)
    assert called.get('deps') is deps


def test_backfill_win_catchup_failure_does_not_block_startup(db_engine, monkeypatch):
    """补推扫描自身抛异常不得阻断启动（与 fetch/compare 失败隔离同构）。"""
    deps = _make_deps(db_engine)
    monkeypatch.setattr(
        'app.scheduler.backfill.push_win_catchups',
        MagicMock(side_effect=RuntimeError('boom')),
    )
    run_startup_backfill(deps)  # 不应抛出
