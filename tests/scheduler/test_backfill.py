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
    """启动 backfill 应处理未认领的 pending_comparisons。"""
    deps = _make_deps(db_engine)
    deps['compare_service'].process_pending = MagicMock(return_value=0)

    run_startup_backfill(deps)

    deps['compare_service'].process_pending.assert_called_once()


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

    deps['compare_service'].process_pending.assert_called_once()
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
