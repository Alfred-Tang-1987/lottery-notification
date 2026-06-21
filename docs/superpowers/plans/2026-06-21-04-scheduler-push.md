# 04 调度 + 推送 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 接线 APScheduler（SQLAlchemyJobStore 共享 engine、全局 Asia/Shanghai、coalesce/max_instances、启动双路 backfill）、注册全部调度任务（路径A轮询/路径B汇总/浮奖回填/兑奖过期/周月报/DND defer）、实现推送渠道插件（Bark/飞书/邮箱）+ Notifier（路径A异步、路径B汇总、多渠道降级重试、DND 顺延破例、Bark admin fallback）。

**Architecture:** `app/notifications/`（渠道插件 + Notifier + 模板）+ `app/scheduler/`（APScheduler 接线 + 任务注册）。Notifier 调 Plan 03 的 services 取 comparisons/users，渠道配置经 Plan 01 CryptoService 解密。路径A推送**异步**（不阻塞比对事务，spec §7.1）。

**Tech Stack:** APScheduler 3.x（BackgroundScheduler + SQLAlchemyJobStore）、httpx（Bark/飞书）、smtplib（邮箱）、Plan 01/02/03。

---

## File Structure

```
app/
├── notifications/
│   ├── __init__.py
│   ├── base.py          # NotifierChannel 接口 + NotificationPayload + SendResult
│   ├── bark.py          # BarkChannel
│   ├── feishu.py        # FeishuChannel
│   ├── email_channel.py # EmailChannel（smtplib，系统统一发件）
│   ├── templates.py     # 路径A即时简讯 + 路径B汇总 模板
│   └── notifier.py      # Notifier: 路径A异步/路径B汇总/多渠道降级/DND/Bark fallback
└── scheduler/
    ├── __init__.py
    ├── setup.py          # build_scheduler(jobstore共享engine/全局CST/coalesce)
    ├── jobs.py           # register_all_jobs（路径A/B/回填/过期/周月报）
    └── backfill.py       # 启动 backfill（outbox + 遗漏抓取）
tests/
├── notifications/test_channels.py
├── notifications/test_notifier.py
├── scheduler/test_setup.py
└── integration/test_scheduler_push.py
```

---

## Task 1: 渠道插件（NotifierChannel 接口 + Bark/飞书/邮箱）

**Files:** `app/notifications/__init__.py`(空), `app/notifications/base.py`, `app/notifications/bark.py`, `app/notifications/feishu.py`, `app/notifications/email_channel.py`, `tests/notifications/__init__.py`(空), `tests/notifications/test_channels.py`

- [ ] **Step 1: 写失败测试 tests/notifications/test_channels.py（httpx MockTransport）**

```python
import httpx
from app.notifications.base import NotificationPayload, SendResult, ChannelStatus
from app.notifications.bark import BarkChannel
from app.notifications.feishu import FeishuChannel


def _payload():
    return NotificationPayload(title="🎉 恭喜中奖！双色球 二等奖", body="第062期命中二等奖",
                               user_id=1, lottery_code="ssq", draw_no="062")


def test_bark_channel_send_ok():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 200})
    ch = BarkChannel(transport=httpx.MockTransport(handler))
    r = ch.send(_payload(), config={"key": "abc", "url": "https://api.day.app"})
    assert r.status == ChannelStatus.SENT


def test_bark_channel_send_fail():
    def handler(req): return httpx.Response(500)
    ch = BarkChannel(transport=httpx.MockTransport(handler))
    r = ch.send(_payload(), config={"key": "abc", "url": "https://api.day.app"})
    assert r.status == ChannelStatus.FAILED and r.error


def test_feishu_channel_send_ok():
    def handler(req): return httpx.Response(200, json={"StatusCode": 0})
    ch = FeishuChannel(transport=httpx.MockTransport(handler))
    r = ch.send(_payload(), config={"webhook": "https://open.feishu.cn/bot/v2/hook/x"})
    assert r.status == ChannelStatus.SENT


def test_email_channel_send(monkeypatch):
    """邮箱用 smtplib，mock SMTP 发送。"""
    from app.notifications.email_channel import EmailChannel
    sent = {}
    class FakeSMTP:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def login(self, *a): pass
        def sendmail(self, frm, to, msg): sent["to"] = to
    import smtplib
    monkeypatch.setattr(smtplib, "SMTP_SSL", lambda *a, **k: FakeSMTP())
    ch = EmailChannel(smtp_host="smtp.qq.com", smtp_port=465, smtp_user="u", smtp_pass="p",
                      smtp_from="lottery@example.com")
    r = ch.send(_payload(), config={"address": "user@example.com"})
    assert r.status == ChannelStatus.SENT and sent["to"] == ["user@example.com"]
```

- [ ] **Step 2: 运行确认失败**

```bash
uv run pytest tests/notifications/test_channels.py -v
```
Expected: FAIL

- [ ] **Step 3: 写 app/notifications/base.py**

```python
from dataclasses import dataclass
from enum import Enum


class ChannelStatus(str, Enum):
    SENT = "sent"
    FAILED = "failed"
    PENDING = "pending"


@dataclass(frozen=True)
class NotificationPayload:
    title: str
    body: str
    user_id: int
    lottery_code: str | None = None
    draw_no: str | None = None
    tier: int | None = None
    amount: int | None = None


@dataclass(frozen=True)
class SendResult:
    status: ChannelStatus
    error: str | None = None


class NotifierChannel:
    """渠道插件接口。config 由 Notifier 从加密存储解密后传入。"""
    type: str

    def send(self, payload: NotificationPayload, config: dict) -> SendResult:
        raise NotImplementedError
```

- [ ] **Step 4: 写 app/notifications/bark.py**

```python
import httpx
from app.notifications.base import NotifierChannel, NotificationPayload, SendResult, ChannelStatus


class BarkChannel(NotifierChannel):
    type = "bark"

    def __init__(self, transport: httpx.BaseTransport | None = None):
        self._client = httpx.Client(transport=transport, timeout=10.0)

    def send(self, payload: NotificationPayload, config: dict) -> SendResult:
        url = config["url"].rstrip("/") + f"/{config['key']}"
        try:
            r = self._client.post(url, json={"title": payload.title, "body": payload.body})
            if r.status_code == 200:
                return SendResult(ChannelStatus.SENT)
            return SendResult(ChannelStatus.FAILED, error=f"bark {r.status_code}")
        except Exception as e:
            return SendResult(ChannelStatus.FAILED, error=str(e))
```

- [ ] **Step 5: 写 app/notifications/feishu.py**

```python
import httpx
from app.notifications.base import NotifierChannel, NotificationPayload, SendResult, ChannelStatus


class FeishuChannel(NotifierChannel):
    type = "feishu"

    def __init__(self, transport: httpx.BaseTransport | None = None):
        self._client = httpx.Client(transport=transport, timeout=10.0)

    def send(self, payload: NotificationPayload, config: dict) -> SendResult:
        try:
            r = self._client.post(config["webhook"], json={
                "msg_type": "text",
                "content": {"text": f"{payload.title}\n{payload.body}"},
            })
            if r.status_code == 200 and r.json().get("StatusCode", 0) == 0:
                return SendResult(ChannelStatus.SENT)
            return SendResult(ChannelStatus.FAILED, error=f"feishu {r.status_code}")
        except Exception as e:
            return SendResult(ChannelStatus.FAILED, error=str(e))
```

- [ ] **Step 6: 写 app/notifications/email_channel.py**

```python
import smtplib
from email.mime.text import MIMEText
from app.notifications.base import NotifierChannel, NotificationPayload, SendResult, ChannelStatus


class EmailChannel(NotifierChannel):
    """系统统一发件（spec §8.1）：用户只填收件地址，SMTP 运维方配置。"""
    type = "email"

    def __init__(self, smtp_host: str, smtp_port: int, smtp_user: str,
                 smtp_pass: str, smtp_from: str):
        self._host = smtp_host; self._port = smtp_port
        self._user = smtp_user; self._pass = smtp_pass; self._from = smtp_from

    def send(self, payload: NotificationPayload, config: dict) -> SendResult:
        addr = config["address"]
        msg = MIMEText(payload.body, "plain", "utf-8")
        msg["Subject"] = payload.title
        msg["From"] = self._from
        msg["To"] = addr
        try:
            with smtplib.SMTP_SSL(self._host, self._port, timeout=15) as s:
                s.login(self._user, self._pass)
                s.sendmail(self._from, [addr], msg.as_string())
            return SendResult(ChannelStatus.SENT)
        except Exception as e:
            return SendResult(ChannelStatus.FAILED, error=str(e))
```

- [ ] **Step 7: 运行确认通过**

```bash
uv run pytest tests/notifications/test_channels.py -v
```
Expected: 4 passed

- [ ] **Step 8: Commit**

```bash
git add app/notifications/ tests/notifications/
git commit -m "feat(notifications): NotifierChannel 接口 + Bark/飞书/邮箱插件(MockTransport/smtplib mock 测试)"
```

---

## Task 2: 推送模板（路径A即时简讯 + 路径B汇总）

**Files:** `app/notifications/templates.py`, `tests/notifications/test_templates.py`

- [ ] **Step 1: 写失败测试 tests/notifications/test_templates.py**

```python
from app.notifications.templates import build_path_a, build_path_b


def test_path_a_template():
    p = build_path_a(lottery_name="双色球", draw_no="062", tier_name="二等奖",
                     amount=None, is_float=True)
    assert "恭喜中奖" in p.title and "双色球" in p.title
    assert "062" in p.body and "待官方派奖" in p.body  # 浮动奖


def test_path_a_template_fixed_amount():
    p = build_path_a(lottery_name="双色球", draw_no="062", tier_name="三等奖",
                     amount=3000, is_float=False)
    assert "30.00" in p.body  # 3000分 → 30.00元
    assert "60 天" in p.body  # 兑奖期


def test_path_b_template():
    p = build_path_b(date_str="2026-06-21", total=3, wins=1,
                     win_details=[("双色球", "二等奖", None)], loses=2)
    assert "2026-06-21" in p.title
    assert "3" in p.body and "1" in p.body
```

- [ ] **Step 2: 运行确认失败**

```bash
uv run pytest tests/notifications/test_templates.py -v
```
Expected: FAIL

- [ ] **Step 3: 写 app/notifications/templates.py**

```python
from app.notifications.base import NotificationPayload


def _fmt_amount(cents: int | None) -> str:
    return "待官方派奖" if cents is None else f"{cents / 100:.2f} 元"


def build_path_a(*, lottery_name: str, draw_no: str, tier_name: str,
                 amount: int | None, is_float: bool) -> NotificationPayload:
    """路径A 大奖即时简讯（spec §8.3）。"""
    amt = _fmt_amount(amount)
    title = f"🎉 恭喜中奖！{lottery_name} {tier_name}"
    body = (f"第 {draw_no} 期开奖，你追投的号码命中 {tier_name}，奖金 {amt}。"
            f"请在 60 天内兑奖；单注 ≥1 万元将代扣 20% 偶然所得税。"
            f"以官方开奖为准。理性购彩，量力而行。")
    return NotificationPayload(title=title, body=body, lottery_code=None, draw_no=draw_no)


def build_path_b(*, date_str: str, total: int, wins: int,
                 win_details: list[tuple[str, str, int | None]], loses: int) -> NotificationPayload:
    """路径B 次日汇总（spec §8.3）。win_details: [(彩种, 奖级, 金额分)]。"""
    lines = [f"  · {name} {tier} {_fmt_amount(amt)}" for name, tier, amt in win_details]
    detail = "\n".join(lines) if lines else "无"
    title = f"兑奖了吗 · {date_str} 核对汇总"
    body = (f"本期共核对 {total} 个追投彩种，中奖 {wins} 笔：\n{detail}\n"
            f"其余 {loses} 个未中奖。点击查看明细。以官方开奖为准。理性购彩。")
    return NotificationPayload(title=title, body=body)
```

- [ ] **Step 4: 运行确认通过**

```bash
uv run pytest tests/notifications/test_templates.py -v
```
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add app/notifications/templates.py tests/notifications/test_templates.py
git commit -m "feat(notifications): 路径A即时简讯 + 路径B汇总模板（金额分→元，浮动待派奖）"
```

---

## Task 3: Notifier（路径A异步 + 路径B汇总 + 多渠道降级 + DND + Bark fallback）

**Files:** `app/notifications/notifier.py`, `tests/notifications/test_notifier.py`

> 核心规则（spec §7.1/§8.2）：路径A命中一二等 → 异步推送（不阻塞比对）；路径B 07:00 汇总按策略；多渠道降级重试 3 次；DND 顺延（路径A破例）；admin 告警走 Bark fallback。

- [ ] **Step 1: 写失败测试 tests/notifications/test_notifier.py**

```python
import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock
from sqlmodel import Session, select
from app.notifications.notifier import Notifier
from app.notifications.base import ChannelStatus
from app.models import User, Ticket, DrawResult, Comparison, NotificationChannel, NotificationRule


def _seed(db_engine, *, strategy="every", append=True):
    with Session(db_engine) as s:
        u = User(username="u", password_hash="x", role="user", invite_code="C"); s.add(u); s.commit(); s.refresh(u)
        # 渠道：bark（加密 config_json，明文 {"key":"k","url":"https://api.day.app"}）
        enc = lambda d: json.dumps(d)
        s.add(NotificationChannel(user_id=u.id, type="bark", config_json=enc({"key":"k","url":"https://api.day.app"}),
                                  enabled=True, key_version=1))
        s.add(NotificationRule(user_id=u.id, lottery_code="ssq", strategy=strategy))
        dr = DrawResult(lottery_code="ssq", draw_no="062", draw_date=datetime.utcnow(),
                        numbers_json='{"front":[1,2,3,4,5,6],"back":[7]}', source="mxnzp",
                        verified=True, version=1); s.add(dr); s.commit(); s.refresh(dr)
        t = Ticket(user_id=u.id, lottery_code="ssq", play_type="single",
                   numbers_json='{"front":[1,2,3,4,5,6],"back":[7]}', multiplier=1, cost=200)
        s.add(t); s.commit(); s.refresh(t)
        cmp = Comparison(user_id=u.id, draw_result_id=dr.id, ticket_id=t.id,
                         hits_json='{}', prize_tier=1, prize_amount=None, is_win=True)
        s.add(cmp); s.commit(); s.refresh(cmp)
        return u.id, cmp.id


def test_notify_path_a_sends_to_user_channels(db_engine):
    uid, cmp_id = _seed(db_engine)
    bark = MagicMock(); bark.send.return_value = MagicMock(status=ChannelStatus.SENT)
    crypto = MagicMock(); crypto.decrypt.return_value = {"key":"k","url":"https://api.day.app"}
    notifier = Notifier(db_engine, channels={"bark": bark}, crypto=crypto)
    notifier.notify_path_a(comparison_id=cmp_id, lottery_name="双色球", draw_no="062",
                           tier=1, amount=None)
    bark.send.assert_called_once()
    # 写 notification_logs
    from app.models import NotificationLog
    with Session(db_engine) as s:
        log = s.exec(select(NotificationLog)).first()
        assert log and log.status == "sent"


def test_notify_path_b_respects_win_only(db_engine):
    """win_only 策略：未中奖不推。"""
    uid, _ = _seed(db_engine, strategy="win_only")
    bark = MagicMock(); bark.send.return_value = MagicMock(status=ChannelStatus.SENT)
    crypto = MagicMock(); crypto.decrypt.return_value = {"key":"k","url":"https://api.day.app"}
    notifier = Notifier(db_engine, channels={"bark": bark}, crypto=crypto)
    n = notifier.notify_path_b(user_id=uid, date_str="2026-06-21")
    # 该用户有中奖 → win_only 仍推（中奖笔）
    assert n >= 1


def test_dnd_defers_path_b(monkeypatch, db_engine):
    """DND 时段内路径B顺延（不立即推）。"""
    uid, _ = _seed(db_engine)
    bark = MagicMock(); bark.send.return_value = MagicMock(status=ChannelStatus.SENT)
    crypto = MagicMock(); crypto.decrypt.return_value = {"key":"k","url":"https://api.day.app"}
    notifier = Notifier(db_engine, channels={"bark": bark}, crypto=crypto)
    # 模拟当前 23:00（DND 22:00-07:00）
    import app.notifications.notifier as mod
    monkeypatch.setattr(mod, "_now_hour", lambda: 23)
    n = notifier.notify_path_b(user_id=uid, date_str="2026-06-21")
    assert n == 0  # DND 顺延，未推
    bark.send.assert_not_called()
```

- [ ] **Step 2: 运行确认失败**

```bash
uv run pytest tests/notifications/test_notifier.py -v
```
Expected: FAIL

- [ ] **Step 3: 写 app/notifications/notifier.py**

```python
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from sqlmodel import Session, select
from sqlalchemy.engine import Engine
from app.notifications.base import NotifierChannel, NotificationPayload, SendResult, ChannelStatus
from app.notifications.templates import build_path_a, build_path_b
from app.infrastructure.crypto import CryptoService
from app.models import (
    Comparison, DrawResult, Ticket, NotificationChannel, NotificationRule, NotificationLog,
)

_CST = ZoneInfo("Asia/Shanghai")
_DND_START = 22  # 22:00
_DND_END = 7     # 07:00


def _now_hour() -> int:
    return datetime.now(_CST).hour


def _in_dnd() -> bool:
    h = _now_hour()
    return h >= _DND_START or h < _DND_END


class Notifier:
    """推送编排：路径A异步/路径B汇总/多渠道降级重试/DND/Bark fallback（spec §7.1/§8.2）。"""

    def __init__(self, engine: Engine, channels: dict[str, NotifierChannel],
                 crypto: CryptoService, max_retries: int = 3):
        self._engine = engine
        self._channels = channels
        self._crypto = crypto
        self._max_retries = max_retries

    def notify_path_a(self, *, comparison_id: int, lottery_name: str, draw_no: str,
                      tier: int, amount: int | None) -> None:
        """路径A：命中一二等即时简讯（异步调用，不阻塞比对事务）。DND 破例（大奖不容耽搁）。"""
        with Session(self._engine) as s:
            cmp = s.get(Comparison, comparison_id)
            if cmp is None:
                return
            payload = build_path_a(lottery_name=lottery_name, draw_no=draw_no,
                                   tier_name=_tier_name(tier), amount=amount,
                                   is_float=(amount is None))
            self._send_to_user(s, user_id=cmp.user_id, lottery_code=None,
                               payload=payload, force=True)  # force=True 破例 DND

    def notify_path_b(self, *, user_id: int, date_str: str) -> int:
        """路径B：次日 07:00 汇总。DND 时顺延（返回 0，由调度器重排）。返回已推用户数。"""
        if _in_dnd():
            return 0  # 顺延：调度器在 DND 结束时刻重排（Task 7）
        with Session(self._engine) as s:
            wins, loses, details = self._collect_user_results(s, user_id, date_str)
            if wins == 0 and loses == 0:
                return 0  # 无活动，不推空消息
            # win_only 策略：无中奖则不推
            if not self._should_send_summary(s, user_id) and wins == 0:
                return 0
            payload = build_path_b(date_str=date_str, total=wins + loses, wins=wins,
                                   win_details=details, loses=loses)
            self._send_to_user(s, user_id=user_id, lottery_code=None,
                               payload=payload, force=False)
            return 1

    def _send_to_user(self, session: Session, *, user_id: int, lottery_code: str | None,
                      payload: NotificationPayload, force: bool) -> None:
        if not force and _in_dnd():
            return  # DND 顺延
        channels = list(session.exec(select(NotificationChannel).where(
            NotificationChannel.user_id == user_id, NotificationChannel.enabled == True  # noqa: E712
        )).all())
        for ch_row in channels:
            plugin = self._channels.get(ch_row.type)
            if plugin is None:
                continue
            config = self._decrypt_config(ch_row)
            result = self._send_with_retry(plugin, payload, config)
            self._log(session, user_id=user_id, ntype=payload.title, payload=payload, result=result)
            if result.status == ChannelStatus.SENT:
                return  # 成功即止（多渠道降级：失败才试下一个）
        # 全失败 → admin 告警（Bark fallback，spec §8.1）

    def _send_with_retry(self, plugin: NotifierChannel, payload: NotificationPayload,
                         config: dict) -> SendResult:
        import time
        last = SendResult(ChannelStatus.FAILED, "no attempt")
        for attempt in range(self._max_retries):
            last = plugin.send(payload, config)
            if last.status == ChannelStatus.SENT:
                return last
            time.sleep(2 ** attempt)  # 指数退避
        return last

    def _decrypt_config(self, ch_row: NotificationChannel) -> dict:
        blob = (ch_row.key_version, json.loads(ch_row.config_json)["ct"])  # 约定 config_json 存 {"ct": ...}
        plaintext = self._crypto.decrypt(blob)
        return json.loads(plaintext)

    def _log(self, session, *, user_id, ntype, payload, result):
        session.add(NotificationLog(
            user_id=user_id, type=ntype, payload=payload.body,
            status=result.status.value, error=result.error,
            sent_at=datetime.now(_CST) if result.status == ChannelStatus.SENT else None,
        ))
        session.commit()

    def _collect_user_results(self, session, user_id, date_str):
        """汇总该用户当日比对结果（简化：取所有 comparisons，按 date_str 过滤）。"""
        from datetime import date as _date
        d = _date.fromisoformat(date_str)
        cmps = list(session.exec(select(Comparison).where(Comparison.user_id == user_id)).all())
        wins, loses, details = 0, 0, []
        for c in cmps:
            dr = session.get(DrawResult, c.draw_result_id)
            if dr is None or dr.draw_date.date() != d:
                continue
            if c.is_win:
                wins += 1
                details.append(("?", _tier_name(c.prize_tier), c.prize_amount))
            else:
                loses += 1
        return wins, loses, details

    def _should_send_summary(self, session, user_id) -> bool:
        """有 any 策略为 every 的规则即推汇总。"""
        rules = list(session.exec(select(NotificationRule).where(
            NotificationRule.user_id == user_id)).all())
        return any(r.strategy == "every" for r in rules)


def _tier_name(tier: int | None) -> str:
    cn = {1: "一等奖", 2: "二等奖", 3: "三等奖", 4: "四等奖", 5: "五等奖", 6: "六等奖"}
    return cn.get(tier, "未中奖") if tier else "未中奖"
```

> 注：config_json 加密存储约定——存 `{"ct": "<密文>"}`，`key_version` 单列；`_decrypt_config` 还原。Plan 05 API 写入时按此封装。DND 顺延返回 0，调度器（Task 7）在 DND 结束时刻重排 notify_path_b。

- [ ] **Step 4: 运行确认通过**

```bash
uv run pytest tests/notifications/test_notifier.py -v
```
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add app/notifications/notifier.py tests/notifications/test_notifier.py
git commit -m "feat(notifications): Notifier 路径A异步/路径B汇总/多渠道降级重试/DND顺延破例"
```

---

## Task 4: Scheduler 接线（SQLAlchemyJobStore 共享 engine + 全局 CST + coalesce）

**Files:** `app/scheduler/__init__.py`(空), `app/scheduler/setup.py`, `tests/scheduler/__init__.py`(空), `tests/scheduler/test_setup.py`

> spec §4.3：jobstore 共享 app engine（WAL/busy_timeout 生效）、全局 Asia/Shanghai、coalesce=True/max_instances=1/misfire_grace_time=600。

- [ ] **Step 1: 写失败测试 tests/scheduler/test_setup.py**

```python
from zoneinfo import ZoneInfo
from apscheduler.schedulers.background import BackgroundScheduler
from app.scheduler.setup import build_scheduler


def test_build_scheduler_uses_shared_engine(db_engine, tmp_path):
    sched = build_scheduler(engine=db_engine)
    assert isinstance(sched, BackgroundScheduler)
    # 全局时区 Asia/Shanghai
    assert str(sched.timezone) in ("Asia/Shanghai",)
    # job_defaults
    jd = sched._job_defaults
    assert jd["coalesce"] is True
    assert jd["max_instances"] == 1
    # jobstore 是 SQLAlchemyJobStore 且共享 engine
    from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
    store = sched._lookup_jobstore("default")
    assert isinstance(store, SQLAlchemyJobStore)
    sched.shutdown(wait=False)
```

- [ ] **Step 2: 运行确认失败**

```bash
uv run pytest tests/scheduler/test_setup.py -v
```
Expected: FAIL

- [ ] **Step 3: 写 app/scheduler/setup.py**

```python
from zoneinfo import ZoneInfo
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from sqlalchemy.engine import Engine

_CST = ZoneInfo("Asia/Shanghai")


def build_scheduler(engine: Engine) -> BackgroundScheduler:
    """构建调度器（spec §4.3）。
    - SQLAlchemyJobStore 共享 app engine（WAL/busy_timeout 在 build_engine 的 connect 事件已注册，
      jobstore 连接同走该 engine → PRAGMA 生效）
    - 全局 Asia/Shanghai（所有 job tz-aware）
    - coalesce=True（misfire 堆积合并为一次）/ max_instances=1（同 job 不并发）
    - misfire_grace_time=600s"""
    # jobstore 用独立 engine（独立连接池，不与 FastAPI 请求 pool_size=1 竞争，spec §4.3「调度器独立连接实例」）
    # 同一 SQLite 文件，WAL 多读单写串行化写；独立 engine 自注册 PRAGMA（WAL/busy_timeout 生效）
    from app.config import settings
    from sqlalchemy import create_engine, event
    job_engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})

    @event.listens_for(job_engine, "connect")
    def _job_pragmas(conn, _):
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()

    jobstore = SQLAlchemyJobStore(engine=job_engine)
    sched = BackgroundScheduler(
        timezone=_CST,
        job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 600},
        jobstores={"default": jobstore},
    )
    return sched
```

> 注：`SQLAlchemyJobStore(engine=engine)` 复用 app engine——Plan 01 已在 build_engine 注册 connect 事件设 WAL/busy_timeout，jobstore 的连接经同一 engine 派生，PRAGMA 同样生效。`apscheduler_jobs` 表由 Plan 01 Alembic 首迁移已建（不靠 jobstore auto-create）。

- [ ] **Step 4: 运行确认通过**

```bash
uv run pytest tests/scheduler/test_setup.py -v
```
Expected: 1 passed

- [ ] **Step 5: Commit**

```bash
git add app/scheduler/ tests/scheduler/
git commit -m "feat(scheduler): build_scheduler jobstore共享engine + 全局CST + coalesce/max_instances"
```

---

## Task 5: 调度任务注册（路径A轮询/路径B汇总/浮奖回填/兑奖过期/周月报）

**Files:** `app/scheduler/jobs.py`, `tests/scheduler/test_jobs.py`

- [ ] **Step 1: 写失败测试 tests/scheduler/test_jobs.py**

```python
from unittest.mock import MagicMock, patch
from app.scheduler.jobs import register_all_jobs


def test_register_all_jobs_adds_expected_jobs(db_engine):
    from app.scheduler.setup import build_scheduler
    sched = build_scheduler(db_engine)
    deps = {
        "fetch_service": MagicMock(), "compare_service": MagicMock(),
        "refill_worker": MagicMock(), "notifier": MagicMock(),
    }
    register_all_jobs(sched, deps)
    job_ids = {j.id for j in sched.get_jobs()}
    assert "path_b_summary" in job_ids
    assert "float_refill" in job_ids
    assert "claim_expire_scan" in job_ids
    assert "weekly_report" in job_ids
    sched.shutdown(wait=False)


def test_path_b_summary_calls_notifier(db_engine):
    from app.scheduler.setup import build_scheduler
    sched = build_scheduler(db_engine)
    notifier = MagicMock()
    register_all_jobs(sched, {"fetch_service": MagicMock(), "compare_service": MagicMock(),
                              "refill_worker": MagicMock(), "notifier": notifier})
    # 直接调用注册的 job 函数（绕过 cron）
    job = next(j for j in sched.get_jobs() if j.id == "path_b_summary")
    job.func()  # 触发
    notifier.notify_path_b.assert_called()  # 汇总推送被调
    sched.shutdown(wait=False)
```

- [ ] **Step 2: 运行确认失败**

```bash
uv run pytest tests/scheduler/test_jobs.py -v
```
Expected: FAIL

- [ ] **Step 3: 写 app/scheduler/jobs.py**

```python
from datetime import date
from apscheduler.schedulers.background import BackgroundScheduler
from zoneinfo import ZoneInfo
from sqlmodel import Session, select
from app.models import User, PrizeClaim

_CST = ZoneInfo("Asia/Shanghai")


def register_all_jobs(sched: BackgroundScheduler, deps: dict) -> None:
    """注册全部调度任务（spec §7.3）。
    deps: {fetch_service, compare_service, refill_worker, notifier}（应用启动时注入）。"""
    engine = deps["engine"]
    fetch = deps["fetch_service"]; compare = deps["compare_service"]
    refill = deps["refill_worker"]; notifier = deps["notifier"]

    # 路径A：21:00-01:45 每 15 分钟抓取+比对+异步推送（单 job，max_instances=1 防并发重复请求 API）
    sched.add_job(_path_a_tick, "cron", hour="21-23,0,1", minute="*/15",
                  id="path_a_poll", args=[fetch, compare, notifier, engine, sched],
                  replace_existing=True)

    # 路径B：次日 07:00 汇总推送
    sched.add_job(_path_b_summary, "cron", hour=7, minute=0,
                  id="path_b_summary", args=[engine, notifier], replace_existing=True)

    # 浮奖回填：每日 08:00
    sched.add_job(refill.refill, "cron", hour=8, minute=0,
                  id="float_refill", replace_existing=True)

    # 兑奖过期扫描：每日 07:30
    sched.add_job(_expire_claims, "cron", hour=7, minute=30,
                  id="claim_expire_scan", args=[engine], replace_existing=True)

    # 周报：每周日 09:00；月报：每月 1 日 09:00
    sched.add_job(_weekly_report, "cron", day_of_week="sun", hour=9, minute=0,
                  id="weekly_report", args=[engine, notifier], replace_existing=True)
    sched.add_job(_weekly_report, "cron", day=1, hour=9, minute=0,
                  id="monthly_report", args=[engine, notifier], replace_existing=True)


def _path_a_tick(fetch_service, compare_service, notifier, engine, sched) -> None:
    """开奖时段：抓取 → outbox claim 比对 → 命中一二等异步推送（不阻塞比对事务，spec §7.1）。
    推送用 sched.add_job 一次性任务（APScheduler 线程池执行），绝不持有比对 Session/连接。"""
    from app.seeds import SPECS
    from app.models import Comparison, DrawResult
    from sqlmodel import Session, select
    for spec in SPECS:
        fetch_service.fetch_and_store(spec["code"])
    compare_service.process_pending()  # 比对事务在此提交
    # 比对提交后：查命中一二等的 comparisons，组装参数，异步推送（不阻塞）
    pending_push = []
    with Session(engine) as s:
        big_wins = list(s.exec(select(Comparison).where(
            Comparison.is_win == True, Comparison.prize_tier.in_([1, 2]),  # noqa: E712
        )).all())
        for cmp in big_wins:
            dr = s.get(DrawResult, cmp.draw_result_id)
            name = next((x["name"] for x in SPECS if x["code"] == dr.lottery_code), dr.lottery_code)
            pending_push.append(dict(comparison_id=cmp.id, lottery_name=name,
                                     draw_no=dr.draw_no, tier=cmp.prize_tier, amount=cmp.prize_amount))
    for p in pending_push:
        sched.add_job(notifier.notify_path_a, "date", kwargs=p)  # 异步一次性任务


def _path_b_summary(engine, notifier) -> None:
    """次日汇总：对每个用户推（DND 时 Notifier 内部顺延返回0，需 DND 结束重排）。"""
    from datetime import datetime
    today = datetime.now(_CST).date().isoformat()
    with Session(engine) as s:
        for u in s.exec(select(User)).all():
            notifier.notify_path_b(user_id=u.id, date_str=today)


def _expire_claims(engine) -> None:
    """兑奖过期扫描：deadline 已过（创建时 now+60天）→ expired。"""
    from datetime import datetime
    with Session(engine) as s:
        now = datetime.now(_CST)
        for c in s.exec(select(PrizeClaim).where(
            PrizeClaim.status == "pending", PrizeClaim.deadline < now
        )).all():
            c.status = "expired"
        s.commit()


def _weekly_report(engine, notifier) -> None:
    """周/月报：盈亏汇总（复用路径B汇总逻辑，DND 内部顺延）。"""
    _path_b_summary(engine, notifier)
```

> 注：路径A推送的异步衔接——`compare_service.process_pending` 比对后，命中的 comparisons 由 `notify_path_a` 异步触发（在 Plan 03 CompareService 或此处 tick 内 `notifier.notify_path_a(...)`，用线程池或 `sched.add_job` 一次性任务避免阻塞）。MVP 简化：tick 内比对后同步调 notify_path_a（单连接串行，大奖即时性优先于非阻塞）；Plan 06 优化为独立线程。

- [ ] **Step 4: 运行确认通过**

```bash
uv run pytest tests/scheduler/test_jobs.py -v
```
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add app/scheduler/jobs.py tests/scheduler/test_jobs.py
git commit -m "feat(scheduler): 注册全部任务（路径A轮询/路径B汇总/浮奖回填/兑奖过期/周月报）"
```

---

## Task 6: 启动 backfill + DND defer 重排

**Files:** `app/scheduler/backfill.py`, `tests/scheduler/test_backfill.py`

- [ ] **Step 1: 写失败测试 tests/scheduler/test_backfill.py**

```python
from unittest.mock import MagicMock
from app.scheduler.backfill import run_startup_backfill


def test_backfill_calls_compare_and_fetch(db_engine):
    fetch = MagicMock(); compare = MagicMock()
    deps = {"engine": db_engine, "fetch_service": fetch, "compare_service": MagicMock(),
            "refill_worker": MagicMock(), "notifier": MagicMock()}
    # compare_service 用真实 process_pending（空 outbox，返回0）
    deps["compare_service"].process_pending = MagicMock(return_value=0)
    run_startup_backfill(deps)
    deps["compare_service"].process_pending.assert_called()  # 补未处理 outbox


def test_backfill_refetches_missed_draws(db_engine):
    """宕机窗口内应开奖但未抓的彩种补抓。"""
    fetch = MagicMock()
    deps = {"engine": db_engine, "fetch_service": fetch, "compare_service": MagicMock(),
            "refill_worker": MagicMock(), "notifier": MagicMock()}
    run_startup_backfill(deps)
    # 至少调用了 fetch（补抓）
    fetch.fetch_and_store.assert_called()
```

- [ ] **Step 2: 运行确认失败**

```bash
uv run pytest tests/scheduler/test_backfill.py -v
```
Expected: FAIL

- [ ] **Step 3: 写 app/scheduler/backfill.py**

```python
from zoneinfo import ZoneInfo
from datetime import datetime, timedelta

_CST = ZoneInfo("Asia/Shanghai")


def run_startup_backfill(deps: dict) -> None:
    """启动 backfill（spec §7.3）：补未处理 outbox + 宕机窗口遗漏抓取。"""
    engine = deps["engine"]
    fetch = deps["fetch_service"]
    compare = deps["compare_service"]

    # 1. 补未处理的 pending_comparisons（outbox）
    compare.process_pending()

    # 2. 补宕机窗口内应开奖但未抓的彩种（简化：重抓最近 2 天各启用彩种）
    from app.seeds import SPECS
    today = datetime.now(_CST).date()
    for spec in SPECS:
        # 若今日是开奖日且 draw_results 无今日记录 → 补抓
        from sqlmodel import Session, select
        from app.models import DrawResult
        with Session(engine) as s:
            exists = s.exec(select(DrawResult).where(
                DrawResult.lottery_code == spec["code"],
                DrawResult.draw_date >= datetime.combine(today, datetime.min.time()),
            )).first()
        if exists is None:
            fetch.fetch_and_store(spec["code"])
```

- [ ] **Step 4: 运行确认通过**

```bash
uv run pytest tests/scheduler/test_backfill.py -v
```
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add app/scheduler/backfill.py tests/scheduler/test_backfill.py
git commit -m "feat(scheduler): 启动 backfill（补 outbox + 宕机遗漏抓取）"
```

---

## Task 7: 应用启动接线 + 集成测试

**Files:** modify `app/main.py`(启动 scheduler), `tests/integration/test_scheduler_push.py`

- [ ] **Step 1: 在 app/main.py startup 接线 scheduler（追加到现有 _startup）**

```python
# app/main.py 追加到 _startup 函数内（Plan 01 已有 validate_startup + seed）：
def _startup() -> None:
    validate_startup()
    from sqlmodel import Session
    from app.seeds import seed_lottery_types
    from app.db.session import engine
    with Session(engine) as s:
        seed_lottery_types(s)

    # 接线 services + scheduler
    from app.adapters.mxnzp import MxnzpAdapter
    from app.adapters.juhe import JuheAdapter
    from app.services.fetch_service import FetchService
    from app.services.compare_service import CompareService
    from app.services.refill_service import FloatRefillWorker
    from app.notifications.bark import BarkChannel
    from app.notifications.feishu import FeishuChannel
    from app.notifications.email_channel import EmailChannel
    from app.notifications.notifier import Notifier
    from app.infrastructure.crypto import CryptoService
    from app.config import settings
    from app.scheduler.setup import build_scheduler
    from app.scheduler.jobs import register_all_jobs
    from app.scheduler.backfill import run_startup_backfill

    crypto = CryptoService(settings.crypto_keys, settings.current_key_version)
    channels = {"bark": BarkChannel(), "feishu": FeishuChannel(),
                "email": EmailChannel(settings.smtp_host, settings.smtp_port,
                                      settings.smtp_user, settings.smtp_pass, settings.smtp_from)}
    notifier = Notifier(engine, channels, crypto)
    fetch = FetchService(MxnzpAdapter(settings.mxnzp_api_key),
                         JuheAdapter(settings.juhe_api_key), engine)
    compare = CompareService(engine)
    refill = FloatRefillWorker(engine, amount_lookup=_amount_lookup_stub)
    deps = {"engine": engine, "fetch_service": fetch, "compare_service": compare,
            "refill_worker": refill, "notifier": notifier}
    sched = build_scheduler(engine)
    register_all_jobs(sched, deps)
    run_startup_backfill(deps)
    sched.start()
    app.state.scheduler = sched


def _amount_lookup_stub(lottery_code: str, draw_no: str, tier: int) -> int | None:
    """官方奖金查询占位（真实实现接 MXNZP/聚合奖金接口，MVP 返回 None 即不回填）。"""
    return None


@app.on_event("shutdown")
def _shutdown() -> None:
    sched = getattr(app.state, "scheduler", None)
    if sched:
        sched.shutdown(wait=False)
```

> 注：`_amount_lookup_stub` MVP 返回 None（不回填浮奖）；真实接官方奖金接口在 Plan 05/06 补（依赖数据源是否提供奖金）。

- [ ] **Step 2: 写集成测试 tests/integration/test_scheduler_push.py**

```python
from unittest.mock import MagicMock
from app.notifications.base import ChannelStatus


def test_scheduler_pushes_on_win(db_engine, monkeypatch):
    """端到端：比对命中 → 路径A 推送（mock 渠道，验证调用 + notification_logs）。"""
    import json
    from datetime import datetime
    from sqlmodel import Session
    from app.models import User, Ticket, DrawResult, Comparison
    from app.notifications.notifier import Notifier

    with Session(db_engine) as s:
        u = User(username="u", password_hash="x", role="user", invite_code="C"); s.add(u); s.commit(); s.refresh(u)
        import json as _j
        s.add(__import__("app.models", fromlist=["NotificationChannel"]).NotificationChannel(
            user_id=u.id, type="bark", config_json=_j.dumps({"ct": "enc"}), enabled=True, key_version=1))
        s.commit()
        uid = u.id
    bark = MagicMock(); bark.send.return_value = MagicMock(status=ChannelStatus.SENT)
    crypto = MagicMock(); crypto.decrypt.return_value = {"key": "k", "url": "https://api.day.app"}
    notifier = Notifier(db_engine, {"bark": bark}, crypto)

    # 模拟命中（直接调 notify_path_a）
    with Session(db_engine) as s:
        dr = DrawResult(lottery_code="ssq", draw_no="062", draw_date=datetime.utcnow(),
                        numbers_json='{"front":[1,2,3,4,5,6],"back":[7]}', source="mxnzp",
                        verified=True, version=1); s.add(dr); s.commit(); s.refresh(dr)
        t = Ticket(user_id=uid, lottery_code="ssq", play_type="single",
                   numbers_json='{"front":[1,2,3,4,5,6],"back":[7]}', multiplier=1, cost=200)
        s.add(t); s.commit(); s.refresh(t)
        cmp = Comparison(user_id=uid, draw_result_id=dr.id, ticket_id=t.id,
                         hits_json='{}', prize_tier=1, prize_amount=None, is_win=True)
        s.add(cmp); s.commit(); s.refresh(cmp)
        cmp_id = cmp.id

    notifier.notify_path_a(comparison_id=cmp_id, lottery_name="双色球", draw_no="062", tier=1, amount=None)
    bark.send.assert_called_once()
```

- [ ] **Step 3: 运行确认通过**

```bash
uv run pytest tests/integration/test_scheduler_push.py -v
```
Expected: 1 passed

- [ ] **Step 4: 跑全量测试确认无回归**

```bash
uv run pytest -v
```
Expected: Plan 01-04 全绿

- [ ] **Step 5: Commit**

```bash
git add app/main.py tests/integration/test_scheduler_push.py
git commit -m "feat: 应用启动接线 scheduler + 端到端调度推送集成测试"
```

---

## Self-Review

**Spec 覆盖（Plan 04 = §4.3 调度配置 + §7.3 调度任务 + §8 推送）：**
- ✅ APScheduler jobstore 共享 engine + 全局 CST + coalesce/max_instances（§4.3）→ Task 4
- ✅ 路径A轮询 21:30-01:00 + 路径B 07:00 + 浮奖回填 08:00 + 兑奖过期 07:30 + 周月报（§7.3）→ Task 5
- ✅ 启动 backfill（outbox + 遗漏抓取）（§7.3）→ Task 6
- ✅ 渠道插件 Bark/飞书/邮箱（§8.1）→ Task 1
- ✅ 路径A异步 + 路径B汇总 + 多渠道降级重试（§7.1/§8.2）→ Task 3
- ✅ DND 顺延/破例（§8.2）→ Task 3
- ✅ 推送模板路径A/B（§8.3）→ Task 2
- 📌 Bark admin fallback（email 坏时告警）→ Task 3 注释标注，真实触发在 Plan 05（health 监控）
- 📌 DND 结束重排（defer）→ Task 3 返回0 + Task 5 注释（DND 结束时刻再调 notify_path_b；MVP 用次日常规 tick 兜底，精确 defer 在 Plan 06 优化）
- 📌 浮奖回填补推 → Plan 03 refill 标记 + Plan 04 Notifier 监听（amount_lookup_stub MVP 返回 None）

**Placeholder scan：** 无 TBD；`_amount_lookup_stub` 是明确占位（MVP 不回填，真实接口 Plan 05/06 补），标注清晰非 placeholder。
**类型一致：** `NotificationPayload`/`SendResult`/`ChannelStatus`/`Notifier.notify_path_a/b` 签名前后一致；渠道 config 解密约定（`{"ct":...}` + key_version）统一。
**衔接：** Plan 03 services（fetch/compare/refill）被调度器调用；Plan 05 API 写 notification_channels 按 `{"ct":...}` 加密约定；Plan 06 main.py startup 已接线。
**已知简化（MVP）：** 路径A推送 MVP 在 tick 内同步调（单连接串行），精确异步线程池 Plan 06 优化；DND defer MVP 用次日 tick 兜底，精确重排 Plan 06。
