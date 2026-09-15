"""数据源健康落表语义（plan-11 spec §1.2）。时间均 naive UTC。"""

from datetime import datetime, timedelta

from sqlmodel import Session

from app.models import ApiSourceHealth
from app.services.source_health import record_source_health


def _get(engine, source='mxnzp') -> ApiSourceHealth:
    with Session(engine) as s:
        return s.get(ApiSourceHealth, source)


def test_record_ok_sets_last_success_and_clears_down(db_engine):
    """ok → status=ok、last_success_at 刷新、down_since 清空、error 清空。"""
    t0 = datetime(2026, 9, 15, 4, 0, 0)
    record_source_health(db_engine, 'mxnzp', 'down', 'boom', now=lambda: t0)
    record_source_health(db_engine, 'mxnzp', 'ok', now=lambda: t0 + timedelta(minutes=5))
    h = _get(db_engine)
    assert h.status == 'ok'
    assert h.last_success_at == t0 + timedelta(minutes=5)
    assert h.down_since is None and h.error is None


def test_record_down_keeps_first_down_since(db_engine):
    """连续 down：down_since 记首次失败时间，不刷新（故障起点语义）。"""
    t0 = datetime(2026, 9, 15, 4, 0, 0)
    record_source_health(db_engine, 'mxnzp', 'down', 'e1', now=lambda: t0)
    record_source_health(db_engine, 'mxnzp', 'down', 'e2', now=lambda: t0 + timedelta(hours=1))
    h = _get(db_engine)
    assert h.status == 'down' and h.down_since == t0 and h.error == 'e2'


def test_record_permanent_sets_degraded(db_engine):
    """permanent（配置态）→ status=degraded、error 留痕、down_since 清零（dx-voice F11）。

    outcome→status 映射：ok→ok / down→down / permanent→degraded（F3 词表统一）。
    """
    t0 = datetime(2026, 9, 15, 4, 0, 0)
    record_source_health(db_engine, 'juhe', 'ok', now=lambda: t0)
    record_source_health(db_engine, 'juhe', 'permanent', 'juhe api_key not configured')
    h = _get(db_engine, 'juhe')
    assert h.status == 'degraded' and h.down_since is None and h.alerted == 'none'
    assert h.error == 'juhe api_key not configured'


def test_record_permanent_on_fresh_row_sets_degraded(db_engine):
    """全新行直接 permanent（部署首日 juhe 未配 key 的真实路径）→ degraded +
    error 留痕（design-voice D7：面板由此显示琥珀降级+原因，而非无解释的 unknown 灰砖）。"""
    record_source_health(db_engine, 'juhe', 'permanent', 'juhe api_key not configured')
    h = _get(db_engine, 'juhe')
    assert h.status == 'degraded' and h.down_since is None
    assert h.error == 'juhe api_key not configured'


def test_record_ok_on_alerted_transitions_recovering_keeps_down_since(db_engine):
    """已告警（alerted）的源恢复 → recovering 且保留 down_since（供恢复通知算时长）。"""
    t0 = datetime(2026, 9, 15, 4, 0, 0)
    record_source_health(db_engine, 'mxnzp', 'down', 'e', now=lambda: t0)
    with Session(db_engine) as s:
        s.get(ApiSourceHealth, 'mxnzp').alerted = 'alerted'
        s.commit()
    record_source_health(db_engine, 'mxnzp', 'ok', now=lambda: t0 + timedelta(hours=2))
    h = _get(db_engine)
    assert h.alerted == 'recovering'
    assert h.down_since == t0  # 保留，评估侧送达恢复通知后清除


def test_record_down_cancels_pending_recovery(db_engine):
    """抖动：recovering 期间再次失败 → 回 none + down_since 重置为新 episode（eng-voice M3a）。

    旧设计 recovering→down 翻回 alerted 是个谎言：M11 路径（告警从未送达）的行
    会显示「已通知」而无人收到。翻回 none 让评估器按**新故障 episode** 重新计时
    告警——恢复通知自然取消（发送要求 status=='ok'），重告警是否发由送达门控
    决定（duplicate > silence，silent-failure 纪律）。
    """
    t0 = datetime(2026, 9, 15, 4, 0, 0)
    with Session(db_engine) as s:
        s.add(ApiSourceHealth(source='mxnzp', status='ok', alerted='recovering',
                              down_since=t0))
        s.commit()
    record_source_health(db_engine, 'mxnzp', 'down', 'again', now=lambda: t0 + timedelta(minutes=1))
    h = _get(db_engine)
    assert h.alerted == 'none' and h.status == 'down'
    assert h.down_since == t0 + timedelta(minutes=1)  # 新 episode 起点


def test_record_permanent_clears_recovering(db_engine):
    """permanent（配置态）落到 recovering 行 → alerted 一并清 none（eng-voice M3b）。

    配置态转移终结本次运行故障 episode——不欠恢复通知；否则 degraded 行永远
    挂着「恢复待通知」（评估器恢复分支要求 status=='ok'，degraded 永不满足）。
    """
    t0 = datetime(2026, 9, 15, 4, 0, 0)
    with Session(db_engine) as s:
        s.add(ApiSourceHealth(source='juhe', status='ok', alerted='recovering',
                              down_since=t0))
        s.commit()
    record_source_health(db_engine, 'juhe', 'permanent', 'juhe api_key not configured',
                         now=lambda: t0 + timedelta(hours=1))
    h = _get(db_engine, 'juhe')
    assert h.status == 'degraded' and h.alerted == 'none' and h.down_since is None


def test_record_ok_after_long_outage_without_delivered_alert_goes_recovering(db_engine):
    """长故障期间告警从未送达（通道同挂，DNS 教训）→ 恢复时不得静默清零——

    置 recovering 补发恢复通知（autoplan M11：否则 9 天故障自愈 = 零通知）。
    """
    t0 = datetime(2026, 9, 1, 4, 0, 0)
    record_source_health(db_engine, 'mxnzp', 'down', 'e', now=lambda: t0)
    # 故障期每次评估尝试告警均失败（状态保持 none），第 9 天直接恢复
    record_source_health(db_engine, 'mxnzp', 'ok', now=lambda: t0 + timedelta(days=9))
    h = _get(db_engine)
    assert h.alerted == 'recovering' and h.down_since == t0


def test_record_ok_after_short_outage_clears_silently(db_engine):
    """短故障（<30min，从未达告警阈值）恢复 → 清 down_since，不留 recovering（M11 边界）。"""
    t0 = datetime(2026, 9, 15, 4, 0, 0)
    record_source_health(db_engine, 'mxnzp', 'down', 'e', now=lambda: t0)
    record_source_health(db_engine, 'mxnzp', 'ok', now=lambda: t0 + timedelta(minutes=10))
    h = _get(db_engine)
    assert h.alerted == 'none' and h.down_since is None


def test_record_error_redacts_secret_query_params(db_engine):
    """error 含 URL query 密钥（juhe key= 等）→ 落表前脱敏（autoplan M13）。

    juhe.py:27 把 api key 放 query，raise_for_status 异常消息含完整 URL；
    健康表 error 会进 admin 面板与 Bark 告警体（第三方服务器），密钥不得外泄。
    """
    record_source_health(
        db_engine, 'juhe', 'down',
        "Client error '403' for url 'https://v.juhe.cn/lottery/query?lottery_id=ssq&key=SECRETKEY123'",
    )
    h = _get(db_engine, 'juhe')
    assert 'SECRETKEY123' not in (h.error or '')
    assert 'key=[REDACTED]' in (h.error or '')


def test_threshold_falls_back_when_settings_unavailable(db_engine, caplog):
    """settings 读不到 → 阈值回退默认 30 分钟且不抛（复核修订 2026-09-15）。

    本套件跑在 conftest 的「无密钥环境」下（JWT_SECRET/CRYPTO_KEY_V1 被删，
    Settings 必填项缺失）——若 _down_alert_after() 直接抛 ValidationError，
    下面断言全不可达；生产中该异常还会被 _record_health 的 except 吞掉，
    健康表将静默停写（silent-failure 纪律不允许）。
    """
    import logging

    t0 = datetime(2026, 9, 15, 4, 0, 0)
    with caplog.at_level(logging.WARNING, logger='app.services.source_health'):
        record_source_health(db_engine, 'mxnzp', 'down', 'e', now=lambda: t0)
        # 10 分钟 < 回退阈值 30 分钟 → 短故障静默清除，不留 recovering
        record_source_health(db_engine, 'mxnzp', 'ok', now=lambda: t0 + timedelta(minutes=10))
    h = _get(db_engine)
    assert h.status == 'ok' and h.alerted == 'none' and h.down_since is None
    assert 'alert_threshold_settings_unavailable' in caplog.text


def test_fallback_threshold_matches_settings_default():
    """兜底常量必须与 settings 默认值一致（防漂移，复核修订 2026-09-15）。

    阈值真值源是 `settings.source_health_alert_after_minutes`（F19 逃生舱，可配可改）；
    `_DEFAULT_ALERT_AFTER_MINUTES` 只是「settings 读不到」时的兜底值。两处若漂移，
    故障场景下会静默使用错误阈值——用测试钉住。

    本用例只读类级字段（不实例化 Settings），故在无密钥环境下也成立。
    """
    from app.config import Settings
    from app.services.source_health import _DEFAULT_ALERT_AFTER_MINUTES

    assert (
        Settings.model_fields['source_health_alert_after_minutes'].default
    ) == _DEFAULT_ALERT_AFTER_MINUTES
