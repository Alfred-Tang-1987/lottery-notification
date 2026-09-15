"""数据源健康落表语义（plan-11 spec §1.2）。时间均 naive UTC。"""

import logging
from datetime import datetime, timedelta

from sqlmodel import Session

from app.models import ApiSourceHealth
from app.services.source_health import evaluate_source_alerts, record_source_health


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


# ---------- evaluate_source_alerts 状态机（spec §1.3） ----------


class _Recorder:
    """记 send_alert 调用；可控抛异常模拟「告警通道也挂了」（DNS 教训）。"""

    def __init__(self, fail_first=0):
        self.calls = []
        self.fail_first = fail_first

    def __call__(self, title, body):
        if self.fail_first > 0:
            self.fail_first -= 1
            raise ConnectionError('bark down')
        self.calls.append((title, body))


def _seed_down(engine, down_since, alerted='none', status='down'):
    with Session(engine) as s:
        s.add(ApiSourceHealth(source='mxnzp', status=status, alerted=alerted,
                              down_since=down_since, last_success_at=None))
        s.commit()


def test_evaluate_no_alert_before_threshold(db_engine):
    """down 不足 30 分钟 → 不告警。"""
    t = datetime(2026, 9, 15, 13, 0, 0)
    _seed_down(db_engine, down_since=t - timedelta(minutes=29))
    rec = _Recorder()
    evaluate_source_alerts(db_engine, lambda: rec, now=lambda: t)
    assert rec.calls == []
    assert _get(db_engine).alerted == 'none'


def test_evaluate_alerts_once_after_threshold(db_engine):
    """down ≥30 分钟 → 告警一次并置 alerted；继续 down 不重复告警。"""
    t = datetime(2026, 9, 15, 13, 0, 0)
    _seed_down(db_engine, down_since=t - timedelta(minutes=31))
    rec = _Recorder()
    evaluate_source_alerts(db_engine, lambda: rec, now=lambda: t)
    evaluate_source_alerts(db_engine, lambda: rec, now=lambda: t + timedelta(minutes=15))
    assert len(rec.calls) == 1 and '持续失败' in rec.calls[0][0]
    assert _get(db_engine).alerted == 'alerted'


def test_evaluate_send_failure_keeps_state_and_retries(db_engine):
    """发送异常 → 状态保持 none，下轮重试；送达成功才转移（DNS 教训回归）。"""
    t = datetime(2026, 9, 15, 13, 0, 0)
    _seed_down(db_engine, down_since=t - timedelta(minutes=40))
    rec = _Recorder(fail_first=1)
    evaluate_source_alerts(db_engine, lambda: rec, now=lambda: t)
    assert rec.calls == [] and _get(db_engine).alerted == 'none'
    evaluate_source_alerts(db_engine, lambda: rec, now=lambda: t + timedelta(minutes=15))
    assert len(rec.calls) == 1 and _get(db_engine).alerted == 'alerted'


def test_recovery_notice_sent_then_reset(db_engine):
    """alerted → 抓取恢复（写入侧置 recovering）→ 评估送出恢复通知 → none+清 down_since。"""
    t = datetime(2026, 9, 15, 13, 0, 0)
    _seed_down(db_engine, down_since=t - timedelta(hours=2), alerted='alerted')
    record_source_health(db_engine, 'mxnzp', 'ok', now=lambda: t)  # → recovering
    rec = _Recorder()
    evaluate_source_alerts(db_engine, lambda: rec, now=lambda: t)
    assert len(rec.calls) == 1 and '恢复' in rec.calls[0][0]
    h = _get(db_engine)
    assert h.alerted == 'none' and h.down_since is None


def test_recovery_send_failure_retries(db_engine):
    """恢复通知发送失败 → 保持 recovering，下轮送达才回 none。"""
    t = datetime(2026, 9, 15, 13, 0, 0)
    _seed_down(db_engine, down_since=t - timedelta(hours=2), alerted='alerted')
    record_source_health(db_engine, 'mxnzp', 'ok', now=lambda: t)
    rec = _Recorder(fail_first=1)
    evaluate_source_alerts(db_engine, lambda: rec, now=lambda: t)
    assert _get(db_engine).alerted == 'recovering'
    evaluate_source_alerts(db_engine, lambda: rec, now=lambda: t + timedelta(minutes=15))
    assert _get(db_engine).alerted == 'none'


def test_no_sender_keeps_state_and_logs(db_engine, caplog):
    """sender_factory 返回 None（未配 key / 开关关闭）→ 不发送也**不转移**（dx-voice F6：

    alerted 的语义是「故障告警已送达」（spec §1.1），未送达标 alerted 是状态说谎——
    面板会显示「已通知」而无人收到。保持 none + 每次评估 warning 一次；
    配好 key 后下一轮自然补发。
    """
    t = datetime(2026, 9, 15, 13, 0, 0)
    _seed_down(db_engine, down_since=t - timedelta(minutes=40))
    with caplog.at_level(logging.WARNING, logger='app.services.source_health'):
        evaluate_source_alerts(db_engine, lambda: None, now=lambda: t)
    assert _get(db_engine).alerted == 'none'
    assert 'source_alerts_disabled' in caplog.text


def test_record_down_then_permanent_stops_alerting(db_engine):
    """down（down_since 已置）之后接 permanent（运行中 key 被删）→ 清 down_since
    置 degraded，评估器不再按运行故障告警（dx-voice F11：否则「持续失败 1440 分钟」
    误报，与「配置态不告警」立意冲突）。"""
    t0 = datetime(2026, 9, 15, 4, 0, 0)
    record_source_health(db_engine, 'juhe', 'down', 'boom', now=lambda: t0)
    record_source_health(db_engine, 'juhe', 'permanent', 'juhe api_key not configured',
                         now=lambda: t0 + timedelta(hours=1))
    h = _get(db_engine, 'juhe')
    assert h.status == 'degraded' and h.down_since is None
    rec = _Recorder()
    evaluate_source_alerts(db_engine, lambda: rec, now=lambda: t0 + timedelta(hours=2))
    assert rec.calls == []


def test_evaluate_sends_outside_db_session(db_engine):
    """M1 回归：send_alert 调用时评估器不得持有 DB 连接（pool_size=1 纪律）。

    若评估器在 session 内发送（plan 原稿），DNS 故障下 Bark 挂 10s 超时期间
    唯一连接被占，其他 job/请求撞 busy_timeout——jobs.py:276-278 两次事故同型。
    """
    t = datetime(2026, 9, 15, 13, 0, 0)
    _seed_down(db_engine, down_since=t - timedelta(minutes=40))
    checked_out_during_send = []

    def _sender(title, body):
        checked_out_during_send.append(db_engine.pool.checkedout())

    evaluate_source_alerts(db_engine, lambda: _sender, now=lambda: t)
    assert checked_out_during_send == [0]


def test_alert_body_includes_peer_source_status(db_engine):
    """告警体必须含备源状态（design-voice D5）：备源 ok 时明说「开奖未受影响」，
    避免单源故障的告警读起来像已经漏开奖（告警疲劳最快路径）。"""
    t = datetime(2026, 9, 15, 13, 0, 0)
    _seed_down(db_engine, down_since=t - timedelta(minutes=40))
    with Session(db_engine) as s:
        s.add(ApiSourceHealth(source='juhe', status='ok'))
        s.commit()
    rec = _Recorder()
    evaluate_source_alerts(db_engine, lambda: rec, now=lambda: t)
    assert len(rec.calls) == 1
    assert '备用源正常' in rec.calls[0][1] and '开奖未受影响' in rec.calls[0][1]
    assert '（自' not in rec.calls[0][1]  # D3：不带 UTC 绝对时间（与同句分钟数矛盾）


def test_factory_not_called_when_nothing_pending(db_engine):
    """eng-voice M2 回归：无待发送项时 sender 工厂不得被调用——

    工厂背后是 build_admin_alert()（新建 BarkChannel/httpx.Client）。若每 tick
    无条件构造，pool_size=1 单连接进程每天泄漏 ~96 个未关闭 client（bark.py:35-37
    client 仅经 close() 释放；notifier.py:271-290 把 close 当一等纪律）。
    """
    factory_spy = _Recorder()  # 复用 calls 记录；作为工厂被调即留痕
    with Session(db_engine) as s:
        s.add(ApiSourceHealth(source='mxnzp', status='ok',
                              last_success_at=datetime(2026, 9, 15, 12, 0, 0)))
        s.commit()
    evaluate_source_alerts(db_engine, factory_spy, now=lambda: datetime(2026, 9, 15, 13, 0, 0))
    assert factory_spy.calls == []


def test_alert_body_when_both_sources_down(db_engine):
    """eng-voice M8.4：双源同时故障 → 告警体如实声明（不得复用「备用源正常」文案）。"""
    t = datetime(2026, 9, 15, 13, 0, 0)
    _seed_down(db_engine, down_since=t - timedelta(minutes=40))
    with Session(db_engine) as s:
        s.add(ApiSourceHealth(source='juhe', status='down',
                              down_since=t - timedelta(minutes=35)))
        s.commit()
    rec = _Recorder()
    evaluate_source_alerts(db_engine, lambda: rec, now=lambda: t)
    bodies = [b for _, b in rec.calls]
    assert any('双源同时故障' in b for b in bodies)


def test_alert_body_when_peer_degraded(db_engine):
    """eng-voice M8.4：备源 degraded（如 juhe 未配 key）→ 明说单点风险，不伪装双源在线。"""
    t = datetime(2026, 9, 15, 13, 0, 0)
    _seed_down(db_engine, down_since=t - timedelta(minutes=40))
    with Session(db_engine) as s:
        s.add(ApiSourceHealth(source='juhe', status='degraded',
                              error='juhe api_key not configured'))
        s.commit()
    rec = _Recorder()
    evaluate_source_alerts(db_engine, lambda: rec, now=lambda: t)
    assert len(rec.calls) == 1
    assert '备用源非健康' in rec.calls[0][1] and 'juhe=degraded' in rec.calls[0][1]
    assert '备用源正常' not in rec.calls[0][1]


def test_redown_after_recovering_realerts(db_engine):
    """eng-voice M3a 评估侧：recovering 期间再 down（写入侧翻 none + 重置 episode）→

    达阈值后重新告警。翻回 none 是诚实状态（本次故障尚未送达），评估器据此重新决策。
    """
    t0 = datetime(2026, 9, 15, 4, 0, 0)
    with Session(db_engine) as s:
        s.add(ApiSourceHealth(source='mxnzp', status='ok', alerted='recovering',
                              down_since=t0))
        s.commit()
    t1 = t0 + timedelta(minutes=5)
    record_source_health(db_engine, 'mxnzp', 'down', 'again', now=lambda: t1)
    rec = _Recorder()
    # 新 episode 不足阈值 → 不告警
    evaluate_source_alerts(db_engine, lambda: rec, now=lambda: t1 + timedelta(minutes=10))
    assert rec.calls == []
    # 新 episode 达阈值 → 重新告警
    evaluate_source_alerts(db_engine, lambda: rec, now=lambda: t1 + timedelta(minutes=31))
    assert len(rec.calls) == 1 and '持续失败' in rec.calls[0][0]
