import json
from datetime import datetime, date
from unittest.mock import MagicMock, call
from sqlmodel import Session, select

from app.notifications.notifier import Notifier, _tier_name
from app.notifications.base import ChannelStatus, SendResult
from app.models import (
    User, Ticket, DrawResult, Comparison,
    NotificationChannel, NotificationRule, NotificationLog,
    LotteryType,
)
from app.seeds.lottery_types import seed_lottery_types


def _seed(db_engine, *, strategy="every", draw_date=None, lottery_code="ssq",
          play_type="single", prize_tier=1, is_win=True, add_lottery_type=True):
    with Session(db_engine) as s:
        if add_lottery_type:
            seed_lottery_types(s)
        u = User(username="u", password_hash="x", role="user", invite_code="C")
        s.add(u)
        s.commit()
        s.refresh(u)
        # 渠道：bark（加密存储 {"ct":"enc"}）
        s.add(NotificationChannel(
            user_id=u.id, type="bark",
            config_json=json.dumps({"ct": "encrypted_bark"}),
            enabled=True, key_version=1,
        ))
        s.add(NotificationRule(
            user_id=u.id, lottery_code=lottery_code, strategy=strategy,
        ))
        dr = DrawResult(
            lottery_code=lottery_code, draw_no="062",
            draw_date=draw_date or datetime(2026, 6, 21, 12, 0, 0),
            numbers_json='{"front":[1,2,3,4,5,6],"back":[7]}',
            source="mxnzp", verified=True, version=1,
        )
        s.add(dr)
        s.commit()
        s.refresh(dr)
        t = Ticket(
            user_id=u.id, lottery_code=lottery_code, play_type=play_type,
            numbers_json='{"front":[1,2,3,4,5,6],"back":[7]}',
            multiplier=1, cost=200, enabled=True,
        )
        s.add(t)
        s.commit()
        s.refresh(t)
        cmp = Comparison(
            user_id=u.id, draw_result_id=dr.id, ticket_id=t.id,
            hits_json='{}', prize_tier=prize_tier, prize_amount=None, is_win=is_win,
        )
        s.add(cmp)
        s.commit()
        s.refresh(cmp)
        return u.id, cmp.id


def test_notify_path_a_sends_to_user_channels(db_engine):
    """路径A：命中后发送即时简讯，并记录 notification_logs。"""
    uid, cmp_id = _seed(db_engine)
    bark = MagicMock()
    bark.send.return_value = SendResult(status=ChannelStatus.SENT, error=None)
    crypto = MagicMock()
    crypto.decrypt.return_value = '{"key":"k","url":"https://api.day.app"}'
    notifier = Notifier(db_engine, channels={"bark": bark}, crypto=crypto)
    notifier.notify_path_a(comparison_id=cmp_id, lottery_name="双色球", draw_no="062",
                             tier=1, amount=None)
    bark.send.assert_called_once()
    # 写 notification_logs
    with Session(db_engine) as s:
        log = s.exec(select(NotificationLog)).first()
        assert log and log.status == "sent"


def test_notify_path_b_respects_win_only(monkeypatch, db_engine):
    """win_only 策略：未中奖不推；有中奖仍推。"""
    uid, _ = _seed(db_engine, strategy="win_only", draw_date=datetime(2026, 6, 21, 12, 0, 0))
    bark = MagicMock()
    bark.send.return_value = SendResult(status=ChannelStatus.SENT, error=None)
    crypto = MagicMock()
    crypto.decrypt.return_value = '{"key":"k","url":"https://api.day.app"}'
    notifier = Notifier(db_engine, channels={"bark": bark}, crypto=crypto)
    # 确保不在 DND 时段（mock _now_hour 返回 12）
    import app.notifications.notifier as mod
    monkeypatch.setattr(mod, "_now_hour", lambda: 12)
    n = notifier.notify_path_b(user_id=uid, date_str="2026-06-21")
    # 该用户有中奖 → win_only 仍推（中奖笔）
    assert n >= 1


def test_dnd_defers_path_b(monkeypatch, db_engine):
    """DND 时段内路径B顺延（不立即推）。"""
    uid, _ = _seed(db_engine, draw_date=datetime(2026, 6, 21, 12, 0, 0))
    bark = MagicMock()
    bark.send.return_value = SendResult(status=ChannelStatus.SENT, error=None)
    crypto = MagicMock()
    crypto.decrypt.return_value = '{"key":"k","url":"https://api.day.app"}'
    notifier = Notifier(db_engine, channels={"bark": bark}, crypto=crypto)
    # 模拟 DND（22:00-07:00，当前 23:00 在 DND 内）
    import app.notifications.notifier as mod
    monkeypatch.setattr(mod, "_now_hour", lambda: 23)
    n = notifier.notify_path_b(user_id=uid, date_str="2026-06-21")
    assert n == 0  # DND 顺延，未推
    bark.send.assert_not_called()


# ====== Review Round 1 Fixes ======


def test_path_a_does_not_hold_session_during_retry(monkeypatch, db_engine):
    """spec §7.1: 路径A异步——notify_path_a 关闭 Session 后才做重试/退避，绝不持有 DB 连接。

    N6（quality re-review）：旧测试用 once-flag（session_closed 置位后永远 True），无法
    抓「retry 间意外开 Session」。改实时 open_count——每次 send 时断言 0 个 Session 开启，
    覆盖全部 3 次重试，与路径B 测试同等严格。
    """
    uid, cmp_id = _seed(db_engine)
    bark = MagicMock()
    # 前两次失败，第三次成功
    results = [
        SendResult(status=ChannelStatus.FAILED, error="timeout"),
        SendResult(status=ChannelStatus.FAILED, error="timeout"),
        SendResult(status=ChannelStatus.SENT, error=None),
    ]
    crypto = MagicMock()
    crypto.decrypt.return_value = '{"key":"k","url":"https://api.day.app"}'

    send_session_open = []  # 每次 send 时的 Session 开启数

    class TrackingSession(Session):
        def __enter__(self):
            self.__class__._depth = getattr(self.__class__, "_depth", 0) + 1
            return super().__enter__()

        def __exit__(self, *a):
            self.__class__._depth = getattr(self.__class__, "_depth", 1) - 1
            return super().__exit__(*a)

        @classmethod
        def open_count(cls):
            return getattr(cls, "_depth", 0)

    def spy_send(payload, config):
        send_session_open.append(TrackingSession.open_count())
        return results[len(send_session_open) - 1]

    bark.send.side_effect = spy_send
    monkeypatch.setattr("app.notifications.notifier.Session", TrackingSession)

    notifier = Notifier(db_engine, channels={"bark": bark}, crypto=crypto)
    notifier.notify_path_a(comparison_id=cmp_id, lottery_name="双色球", draw_no="062",
                             tier=1, amount=None)

    assert len(send_session_open) == 3, "应触发 3 次重试 send"
    assert all(n == 0 for n in send_session_open), (
        f"路径A 每次 send 都须在 Session 关闭后（spec §7.1），实际各次 send 时 "
        f"Session 开启数={send_session_open}（retry 间不得持有 DB 连接）")


def test_admin_bark_fallback_on_all_channels_fail(db_engine):
    """spec §8.1/§10: 全用户渠道失败 → admin Bark fallback 告警。"""
    uid, cmp_id = _seed(db_engine)
    bark = MagicMock()
    bark.send.return_value = SendResult(status=ChannelStatus.FAILED, error="net err")
    crypto = MagicMock()
    crypto.decrypt.return_value = '{"key":"k","url":"https://api.day.app"}'

    admin_bark = MagicMock()
    admin_bark.send.return_value = SendResult(status=ChannelStatus.SENT, error=None)

    notifier = Notifier(
        db_engine,
        channels={"bark": bark},
        crypto=crypto,
        admin_bark_config={"key": "admin_key", "url": "https://admin.day.app"},
    )
    # 注入 admin_bark channel（用同一个 mock 但不同实例以区分调用）
    notifier._admin_bark_channel = admin_bark

    notifier.notify_path_a(comparison_id=cmp_id, lottery_name="双色球", draw_no="062",
                             tier=1, amount=None)

    # 用户渠道失败 3 次（重试）
    assert bark.send.call_count == 3
    # admin Bark 被调用一次
    admin_bark.send.assert_called_once()
    # admin 消息包含告警关键词
    args, _ = admin_bark.send.call_args
    payload = args[0]
    assert "告警" in payload.title or "推送失败" in payload.title


def test_path_b_only_includes_tracked_lotteries(monkeypatch, db_engine):
    """spec §7.4/§8.2: 推送范围 = 该用户号码池里有启用注的彩种，未追投的不推。"""
    with Session(db_engine) as s:
        seed_lottery_types(s)
        u = User(username="u", password_hash="x", role="user", invite_code="C")
        s.add(u); s.commit(); s.refresh(u)
        # 用户只追投 ssq，不追投 dlt
        s.add(NotificationChannel(
            user_id=u.id, type="bark",
            config_json=json.dumps({"ct": "enc"}), enabled=True, key_version=1,
        ))
        s.add(NotificationRule(user_id=u.id, lottery_code="ssq", strategy="every"))
        # ssq 开奖 + 比对（中奖）
        dr_ssq = DrawResult(lottery_code="ssq", draw_no="062",
                            draw_date=datetime(2026, 6, 21, 12, 0, 0),
                            numbers_json='{"front":[1,2,3,4,5,6],"back":[7]}',
                            source="mxnzp", verified=True, version=1)
        s.add(dr_ssq); s.commit(); s.refresh(dr_ssq)
        t_ssq = Ticket(user_id=u.id, lottery_code="ssq", play_type="single",
                       numbers_json='{"front":[1,2,3,4,5,6],"back":[7]}',
                       multiplier=1, cost=200, enabled=True)
        s.add(t_ssq); s.commit(); s.refresh(t_ssq)
        cmp_ssq = Comparison(user_id=u.id, draw_result_id=dr_ssq.id, ticket_id=t_ssq.id,
                             hits_json='{}', prize_tier=1, prize_amount=None, is_win=True)
        s.add(cmp_ssq); s.commit()
        # dlt 开奖 + 比对（但用户无 dlt  ticket，所以不应出现在汇总）
        dr_dlt = DrawResult(lottery_code="dlt", draw_no="063",
                            draw_date=datetime(2026, 6, 21, 12, 0, 0),
                            numbers_json='{"front":[1,2,3,4,5],"back":[6,7]}',
                            source="mxnzp", verified=True, version=1)
        s.add(dr_dlt); s.commit(); s.refresh(dr_dlt)
        # 没有 dlt ticket，所以不生成 comparison（符合实际流程）
        uid = u.id

    bark = MagicMock()
    bark.send.return_value = SendResult(status=ChannelStatus.SENT, error=None)
    crypto = MagicMock()
    crypto.decrypt.return_value = '{"key":"k","url":"https://api.day.app"}'
    notifier = Notifier(db_engine, channels={"bark": bark}, crypto=crypto)
    # 确保不在 DND 时段
    import app.notifications.notifier as mod
    monkeypatch.setattr(mod, "_now_hour", lambda: 12)
    notifier.notify_path_b(user_id=uid, date_str="2026-06-21")

    # 验证发送的 payload 中只有 ssq，没有 dlt
    args, _ = bark.send.call_args
    payload = args[0]
    assert "双色球" in payload.body
    assert "大乐透" not in payload.body


def test_path_b_respects_per_lottery_strategy(monkeypatch, db_engine):
    """spec §8.2: 每用户×每彩种策略 every|win_only；未中奖的 every 才推，win_only 不推。"""
    with Session(db_engine) as s:
        seed_lottery_types(s)
        u = User(username="u", password_hash="x", role="user", invite_code="C")
        s.add(u); s.commit(); s.refresh(u)
        s.add(NotificationChannel(
            user_id=u.id, type="bark",
            config_json=json.dumps({"ct": "enc"}), enabled=True, key_version=1,
        ))
        # ssq: win_only（未中奖不推）；dlt: every（每期推）
        s.add(NotificationRule(user_id=u.id, lottery_code="ssq", strategy="win_only"))
        s.add(NotificationRule(user_id=u.id, lottery_code="dlt", strategy="every"))
        # ssq 未中奖
        dr_ssq = DrawResult(lottery_code="ssq", draw_no="062",
                            draw_date=datetime(2026, 6, 21, 12, 0, 0),
                            numbers_json='{"front":[1,2,3,4,5,6],"back":[7]}',
                            source="mxnzp", verified=True, version=1)
        s.add(dr_ssq); s.commit(); s.refresh(dr_ssq)
        t_ssq = Ticket(user_id=u.id, lottery_code="ssq", play_type="single",
                       numbers_json='{"front":[10,11,12,13,14,15],"back":[1]}',
                       multiplier=1, cost=200, enabled=True)
        s.add(t_ssq); s.commit(); s.refresh(t_ssq)
        cmp_ssq = Comparison(user_id=u.id, draw_result_id=dr_ssq.id, ticket_id=t_ssq.id,
                             hits_json='{}', prize_tier=None, prize_amount=None, is_win=False)
        s.add(cmp_ssq); s.commit()
        # dlt 未中奖
        dr_dlt = DrawResult(lottery_code="dlt", draw_no="063",
                            draw_date=datetime(2026, 6, 21, 12, 0, 0),
                            numbers_json='{"front":[1,2,3,4,5],"back":[6,7]}',
                            source="mxnzp", verified=True, version=1)
        s.add(dr_dlt); s.commit(); s.refresh(dr_dlt)
        t_dlt = Ticket(user_id=u.id, lottery_code="dlt", play_type="single",
                       numbers_json='{"front":[10,11,12,13,14],"back":[1,2]}',
                       multiplier=1, cost=200, enabled=True)
        s.add(t_dlt); s.commit(); s.refresh(t_dlt)
        cmp_dlt = Comparison(user_id=u.id, draw_result_id=dr_dlt.id, ticket_id=t_dlt.id,
                             hits_json='{}', prize_tier=None, prize_amount=None, is_win=False)
        s.add(cmp_dlt); s.commit()
        uid = u.id

    bark = MagicMock()
    bark.send.return_value = SendResult(status=ChannelStatus.SENT, error=None)
    crypto = MagicMock()
    crypto.decrypt.return_value = '{"key":"k","url":"https://api.day.app"}'
    notifier = Notifier(db_engine, channels={"bark": bark}, crypto=crypto)
    # 确保不在 DND 时段
    import app.notifications.notifier as mod
    monkeypatch.setattr(mod, "_now_hour", lambda: 12)
    notifier.notify_path_b(user_id=uid, date_str="2026-06-21")

    # 只推 dlt（every），不推 ssq（win_only 且未中奖）
    args, _ = bark.send.call_args
    payload = args[0]
    # every 策略即使未中奖也会推汇总（"本期核对完毕"）
    assert "核对" in payload.body
    assert "双色球" not in payload.body


def test_path_b_resolves_lottery_name(monkeypatch, db_engine):
    """spec §8.3: 路径B模板中彩种名须从 LotteryType 解析，不能是 '?'。"""
    uid, _ = _seed(db_engine, draw_date=datetime(2026, 6, 21, 12, 0, 0))
    bark = MagicMock()
    bark.send.return_value = SendResult(status=ChannelStatus.SENT, error=None)
    crypto = MagicMock()
    crypto.decrypt.return_value = '{"key":"k","url":"https://api.day.app"}'
    notifier = Notifier(db_engine, channels={"bark": bark}, crypto=crypto)
    # 确保不在 DND 时段
    import app.notifications.notifier as mod
    monkeypatch.setattr(mod, "_now_hour", lambda: 12)
    notifier.notify_path_b(user_id=uid, date_str="2026-06-21")

    args, _ = bark.send.call_args
    payload = args[0]
    assert "?" not in payload.body
    assert "双色球" in payload.body


def test_tier_name_per_lottery_type():
    """lottery-rules.md: 不同彩种奖级称呼不同。
    partition: 一等奖/二等奖/...；qxc: 一等奖/二等奖/...；
    positional(fc3d): 单选/组选三/组选六；positional(pl3/pl5): 直选/组选三/组选六。
    """
    # partition
    assert _tier_name("ssq", 1) == "一等奖"
    assert _tier_name("ssq", 6) == "六等奖"
    assert _tier_name("dlt", 1) == "一等奖"
    assert _tier_name("qlc", 2) == "二等奖"
    # qxc
    assert _tier_name("qxc", 1) == "一等奖"
    assert _tier_name("qxc", 6) == "六等奖"
    # positional - fc3d
    assert _tier_name("fc3d", 1) == "单选"
    assert _tier_name("fc3d", 2) == "组选三"
    assert _tier_name("fc3d", 3) == "组选六"
    # positional - pl3/pl5
    assert _tier_name("pl3", 1) == "直选"
    assert _tier_name("pl3", 2) == "组选三"
    assert _tier_name("pl5", 1) == "直选"
    # unknown
    assert _tier_name("ssq", 99) == "未中奖"
    assert _tier_name("unknown", 1) == "未中奖"


def test_dnd_configurable(monkeypatch, db_engine):
    """spec §8.2: DND 窗口须可注入配置，不能硬编码。"""
    uid, _ = _seed(db_engine, draw_date=datetime(2026, 6, 21, 12, 0, 0))
    bark = MagicMock()
    bark.send.return_value = SendResult(status=ChannelStatus.SENT, error=None)
    crypto = MagicMock()
    crypto.decrypt.return_value = '{"key":"k","url":"https://api.day.app"}'
    # 构造 Notifier，DND 设为 10:00-14:00（当前 12:00 在 DND 内）
    notifier = Notifier(
        db_engine, channels={"bark": bark}, crypto=crypto,
        dnd_start=10, dnd_end=14,
    )
    # 模拟当前 12:00
    import app.notifications.notifier as mod
    monkeypatch.setattr(mod, "_now_hour", lambda: 12)
    n = notifier.notify_path_b(user_id=uid, date_str="2026-06-21")
    assert n == 0  # 在自定义 DND 内，顺延
    bark.send.assert_not_called()

    # 模拟当前 15:00（DND 外）
    monkeypatch.setattr(mod, "_now_hour", lambda: 15)
    n = notifier.notify_path_b(user_id=uid, date_str="2026-06-21")
    assert n == 1  # 正常推送
    bark.send.assert_called()


def test_decrypt_config_rejects_plaintext(db_engine):
    """spec §8.1: 渠道配置加密存储，生产环境绝不接受明文。"""
    with Session(db_engine) as s:
        seed_lottery_types(s)
        u = User(username="u", password_hash="x", role="user", invite_code="C")
        s.add(u); s.commit(); s.refresh(u)
        # 明文 config_json（无 "ct"）
        s.add(NotificationChannel(
            user_id=u.id, type="bark",
            config_json=json.dumps({"key": "k", "url": "https://api.day.app"}),
            enabled=True, key_version=1,
        ))
        s.add(NotificationRule(user_id=u.id, lottery_code="ssq", strategy="every"))
        dr = DrawResult(lottery_code="ssq", draw_no="062",
                        draw_date=datetime(2026, 6, 21, 12, 0, 0),
                        numbers_json='{"front":[1,2,3,4,5,6],"back":[7]}',
                        source="mxnzp", verified=True, version=1)
        s.add(dr); s.commit(); s.refresh(dr)
        t = Ticket(user_id=u.id, lottery_code="ssq", play_type="single",
                   numbers_json='{"front":[1,2,3,4,5,6],"back":[7]}',
                   multiplier=1, cost=200, enabled=True)
        s.add(t); s.commit(); s.refresh(t)
        cmp = Comparison(user_id=u.id, draw_result_id=dr.id, ticket_id=t.id,
                         hits_json='{}', prize_tier=1, prize_amount=None, is_win=True)
        s.add(cmp); s.commit(); s.refresh(cmp)
        uid = u.id

    bark = MagicMock()
    bark.send.return_value = SendResult(status=ChannelStatus.SENT, error=None)
    crypto = MagicMock()
    notifier = Notifier(db_engine, channels={"bark": bark}, crypto=crypto)
    notifier.notify_path_a(comparison_id=cmp.id, lottery_name="双色球", draw_no="062",
                             tier=1, amount=None)
    # 明文配置被拒绝 → 渠道不被调用
    bark.send.assert_not_called()


# ====== Review Round 2 Fixes (quality re-review, observation 4145) ======


def test_path_b_does_not_hold_session_during_send(monkeypatch, db_engine):
    """I1（quality review Important）：路径B 与路径A 对齐——Session 关闭后才做网络发送
    + 退避，绝不持有 DB 连接（spec §7.1 + SQLite pool_size=1 单写连接）。

    _send_to_user 旧版在传入的 Session 内直接 _send_with_retry（含 sleep(2^n) 退避，
    3 次重试最多 ~7s）→ 整段持有单写连接 → 阻塞 APScheduler jobstore 写 + 其他写操作
    串行化卡顿。spec §7.1 明文「先读 DB 关 Session 再做网络发送」。

    正确：路径B 也在 Session 内只读数据 + 写 pending log，关 Session 后再发网络。
    """
    uid, _ = _seed(db_engine, draw_date=datetime(2026, 6, 21, 12, 0, 0))
    bark = MagicMock()
    bark.send.return_value = SendResult(status=ChannelStatus.SENT, error=None)
    crypto = MagicMock()
    crypto.decrypt.return_value = '{"key":"k","url":"https://api.day.app"}'

    import app.notifications.notifier as mod
    monkeypatch.setattr(mod, "_now_hour", lambda: 12)

    session_open_during_send = [0]

    class TrackingSession(mod.Session):
        def __enter__(self):
            self.__class__._depth = getattr(self.__class__, "_depth", 0) + 1
            return super().__enter__()

        def __exit__(self, *a):
            self.__class__._depth = getattr(self.__class__, "_depth", 1) - 1
            return super().__exit__(*a)

        @classmethod
        def open_count(cls):
            return getattr(cls, "_depth", 0)

    def spy_send(payload, config):
        session_open_during_send[0] = TrackingSession.open_count()
        return SendResult(status=ChannelStatus.SENT, error=None)

    bark.send.side_effect = spy_send
    monkeypatch.setattr("app.notifications.notifier.Session", TrackingSession)

    notifier = Notifier(db_engine, channels={"bark": bark}, crypto=crypto)
    notifier.notify_path_b(user_id=uid, date_str="2026-06-21")

    bark.send.assert_called_once()
    assert session_open_during_send[0] == 0, (
        f"路径B 网络发送必须发生在 Session 关闭后（spec §7.1），实际 send 时 "
        f"仍有 {session_open_during_send[0]} 个 Session 开启（旧版 _send_to_user "
        f"在 session 内发送+退避，长时间持有单写连接）")


def test_decrypt_config_logs_on_failure(db_engine, caplog):
    """I2（quality review Important）：_decrypt_config 解密失败不得静默——bare except 返回
    None 无 log 属 silent-failure（spec §10 + 项目「中奖永不静默漏通知」纪律）。

    解密失败意味着渠道配置损坏/key_version 失配/Fernet key 轮换错位——该用户该渠道将
    永不推送（每次解密失败都跳过）。无 log 则运维无法发现「某用户配置坏了」，中奖静默
    漏通知且无人知晓。须记 WARNING（含 user_id/channel type 便于定位），再返回 None。
    """
    import logging
    uid, cmp_id = _seed(db_engine)
    bark = MagicMock()
    bark.send.return_value = SendResult(status=ChannelStatus.SENT, error=None)
    crypto = MagicMock()
    # decrypt 抛异常（Fernet key 失配 / 密文损坏）
    crypto.decrypt.side_effect = ValueError("InvalidToken")

    notifier = Notifier(db_engine, channels={"bark": bark}, crypto=crypto)
    with caplog.at_level(logging.WARNING, logger="app.notifications.notifier"):
        notifier.notify_path_a(comparison_id=cmp_id, lottery_name="双色球", draw_no="062",
                               tier=1, amount=None)

    # 解密失败必须留痕（非静默吞掉）
    assert any("decrypt" in rec.message.lower() or "解密" in rec.message
               or "config" in rec.message.lower() for rec in caplog.records), (
        "_decrypt_config 解密失败须记 WARNING 日志（含 user_id/channel），不得 bare except 静默返回 None")
    bark.send.assert_not_called()  # 解密失败的渠道被跳过


# ====== Review Round 3 Fixes (quality re-review of 0d91602) ======


def test_sent_at_is_naive_utc_aligned_with_created_at(db_engine):
    """N1（quality re-review Important）：NotificationLog.sent_at 须 naive UTC，与
    created_at（TimestampMixin default_factory=datetime.utcnow，naive UTC）同时区且同数值。

    CLAUDE.md「silent-failure 纪律」明文：SQLite 对 datetime 做**字符串比较**（非 tz-aware），
    且存取会剥离 tzinfo。若 sent_at 用 datetime.now(CST)（aware CST），写入的是 CST 本地
    数值（如 05:10），而 created_at 用 utcnow() 写的是 UTC 数值（如 21:10）——同一时刻
    sent_at 比 created_at 数值小 8h，SQLite 字符串排序会让 sent_at < created_at，未来按时间
    过滤 log 的运维查询（清理过期 / 查 pending 超时 / 统计发送延迟）静默误判边界行。
    与 FloatRefillWorker _cutoff_naive_utc 同源雷，新代码不应重蹈。

    正确：sent_at = datetime.now(UTC).replace(tzinfo=None)（naive UTC 数值）。
    验证：sent_at 与「当前 UTC 时刻」差 < 5 分钟（非 CST 数值——CST 会差 8h）。
    """
    from datetime import timezone, datetime as _dt
    uid, cmp_id = _seed(db_engine)
    bark = MagicMock()
    bark.send.return_value = SendResult(status=ChannelStatus.SENT, error=None)
    crypto = MagicMock()
    crypto.decrypt.return_value = '{"key":"k","url":"https://api.day.app"}'
    notifier = Notifier(db_engine, channels={"bark": bark}, crypto=crypto)
    before = _dt.now(timezone.utc).replace(tzinfo=None)
    notifier.notify_path_a(comparison_id=cmp_id, lottery_name="双色球", draw_no="062",
                           tier=1, amount=None)
    after = _dt.now(timezone.utc).replace(tzinfo=None)

    with Session(db_engine) as s:
        log = s.exec(select(NotificationLog)).first()
        assert log and log.status == "sent"
        assert log.sent_at is not None
        # sent_at 须落在 [before, after] UTC 窗口内——若误用 CST 数值，会比 UTC 早 8h，
        # 落在 before 之前 → 断言失败（暴露时区错位）。
        assert before <= log.sent_at <= after, (
            f"sent_at={log.sent_at} 须 naive UTC（落 [{before}, {after}]），"
            f"实际落在窗口外——疑用 datetime.now(CST) 写了 CST 数值（比 UTC 早 8h），"
            "与 created_at(utcnow) 在 SQLite 字符串比较中错位，埋静默误判雷")


def test_admin_bark_alert_failure_is_logged(db_engine, caplog):
    """N2（quality re-review Minor→silent-failure）：_alert_admin 自身 send 返回 FAILED
    须记日志——否则「全渠道失败」时连兜底告警通道也挂了，运维却无感知（双重静默）。

    BarkChannel.send 永不抛异常（base.py 契约），但会返回 FAILED（admin key 失效 /
    Bark 服务挂）。_alert_admin 丢弃返回值则告警链路失败静默。须记 ERROR 含原因。
    """
    import logging
    uid, cmp_id = _seed(db_engine)
    bark = MagicMock()
    bark.send.return_value = SendResult(status=ChannelStatus.FAILED, error="net err")  # 用户渠道全失败
    crypto = MagicMock()
    crypto.decrypt.return_value = '{"key":"k","url":"https://api.day.app"}'

    admin_bark = MagicMock()
    admin_bark.send.return_value = SendResult(status=ChannelStatus.FAILED, error="admin key invalid")

    notifier = Notifier(
        db_engine, channels={"bark": bark}, crypto=crypto,
        admin_bark_config={"key": "admin_key", "url": "https://admin.day.app"},
    )
    notifier._admin_bark_channel = admin_bark

    with caplog.at_level(logging.ERROR, logger="app.notifications.notifier"):
        notifier.notify_path_a(comparison_id=cmp_id, lottery_name="双色球", draw_no="062",
                               tier=1, amount=None)

    admin_bark.send.assert_called_once()
    # admin Bark 自身失败必须留痕（非丢弃返回值静默）
    assert any("admin" in rec.message.lower() or "告警" in rec.message for rec in caplog.records), (
        "_alert_admin 的 send 返回 FAILED 须记 ERROR（含原因），不得丢弃返回值静默——"
        "否则全渠道失败时兜底告警也挂了却无人知晓（双重静默）")


def test_no_user_channels_does_not_trigger_admin_alert(monkeypatch, db_engine, caplog):
    """N4（quality re-review Minor→噪音）：用户未配/全禁用渠道（channels_data 为空）≠
    全渠道失败，不应触发 admin fallback——否则新用户每次路径B 有汇总内容都告警，噪音
    淹没真实「全渠道失败」告警。

    正确：空渠道单独记 WARNING（提示该用户无可用渠道），不调 _alert_admin。
    """
    import logging
    from app.models import User, DrawResult, Comparison, NotificationRule
    from app.seeds.lottery_types import seed_lottery_types
    # seed 一个无任何渠道的用户
    with Session(db_engine) as s:
        seed_lottery_types(s)
        u = User(username="noch", password_hash="x", role="user", invite_code="C")
        s.add(u); s.commit(); s.refresh(u)
        s.add(NotificationRule(user_id=u.id, lottery_code="ssq", strategy="every"))
        dr = DrawResult(lottery_code="ssq", draw_no="062",
                        draw_date=datetime(2026, 6, 21, 12, 0, 0),
                        numbers_json='{"front":[1,2,3,4,5,6],"back":[7]}',
                        source="mxnzp", verified=True, version=1)
        s.add(dr); s.commit(); s.refresh(dr)
        t = Ticket(user_id=u.id, lottery_code="ssq", play_type="single",
                   numbers_json='{"front":[1,2,3,4,5,6],"back":[7]}',
                   multiplier=1, cost=200, enabled=True)
        s.add(t); s.commit(); s.refresh(t)
        s.add(Comparison(user_id=u.id, draw_result_id=dr.id, ticket_id=t.id, hits_json='{}',
                         prize_tier=1, prize_amount=None, is_win=True)); s.commit()
        uid = u.id

    bark = MagicMock()
    crypto = MagicMock()
    import app.notifications.notifier as mod
    monkeypatch.setattr(mod, "_now_hour", lambda: 12)

    notifier = Notifier(
        db_engine, channels={"bark": bark}, crypto=crypto,
        admin_bark_config={"key": "admin_key", "url": "https://admin.day.app"},
    )
    admin_bark = MagicMock()
    notifier._admin_bark_channel = admin_bark

    with caplog.at_level(logging.WARNING, logger="app.notifications.notifier"):
        notifier.notify_path_b(user_id=uid, date_str="2026-06-21")

    bark.send.assert_not_called()  # 无渠道
    # 空渠道不应触发 admin fallback（不是「全渠道失败」）
    assert not admin_bark.send.called, "空渠道≠全渠道失败，不应触发 admin 告警（噪音淹没真实失败）"


def test_notifier_close_releases_admin_and_user_channels(db_engine):
    """N3（quality re-review Minor→资源泄漏）：Notifier.close() 须释放用户渠道 +
    懒加载的 admin Bark channel 的 httpx.Client（APScheduler 常驻进程下避免连接泄漏）。

    close() 须幂等（重复调用不报错），且关后 admin channel 置 None。
    """
    bark = MagicMock()
    crypto = MagicMock()
    notifier = Notifier(
        db_engine, channels={"bark": bark}, crypto=crypto,
        admin_bark_config={"key": "k", "url": "https://admin.day.app"},
    )
    admin_bark = MagicMock()
    notifier._admin_bark_channel = admin_bark

    notifier.close()

    bark.close.assert_called_once()  # 用户渠道释放
    admin_bark.close.assert_called_once()  # admin channel 释放
    assert notifier._admin_bark_channel is None  # 置空
    # 幂等：重复 close 不报错（admin 已 None 不再关；用户渠道 close 须自身幂等）
    notifier.close()
    assert admin_bark.close.call_count == 1  # admin 只关一次（已置 None）
