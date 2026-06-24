import httpx
from app.notifications.base import NotificationPayload, SendResult, ChannelStatus
from app.notifications.bark import BarkChannel
from app.notifications.feishu import FeishuChannel


def _payload():
    """路径 A 即时简讯样例 payload（渠道只关心 title/body，其余字段透传）。"""
    return NotificationPayload(title="🎉 恭喜中奖！双色球 二等奖", body="第062期命中二等奖",
                               user_id=1, lottery_code="ssq", draw_no="062")


# --- Bark -----------------------------------------------------------------

def test_bark_channel_send_ok():
    """Bark 200 + code 200 → SENT，POST 到 {url}/{key} 携带 title/body。"""
    captured = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["url"] = str(req.url)
        captured["json"] = req.read().decode()
        return httpx.Response(200, json={"code": 200})

    ch = BarkChannel(transport=httpx.MockTransport(handler))
    r = ch.send(_payload(), config={"key": "abc", "url": "https://api.day.app"})
    assert r.status == ChannelStatus.SENT and r.error is None
    # URL 拼装约定（spec §8.1：Bark 配置 key+URL）
    assert captured["url"] == "https://api.day.app/abc"
    assert "恭喜中奖" in captured["json"]


def test_bark_channel_send_fail():
    """Bark 5xx → FAILED 且带 error。"""
    def handler(req): return httpx.Response(500)

    ch = BarkChannel(transport=httpx.MockTransport(handler))
    r = ch.send(_payload(), config={"key": "abc", "url": "https://api.day.app"})
    assert r.status == ChannelStatus.FAILED and r.error


def test_bark_channel_send_http_ok_body_code_nonzero_is_failed():
    """Bark HTTP 200 但响应体 code≠200（如 key 失效）→ FAILED，绝不静默判成功。

    spec §10「推送失败」可靠性要求 + 核心纪律「中奖永不静默漏通知」：
    Bark API 对参数/key 错误常返回 HTTP 200 + body {"code":400,...}，
    仅判 HTTP 状态会把失败推送误报为 SENT → 降级/告警/重试全失效。
    """
    def handler(req): return httpx.Response(200, json={"code": 400, "message": "device token not found"})

    ch = BarkChannel(transport=httpx.MockTransport(handler))
    r = ch.send(_payload(), config={"key": "bad", "url": "https://api.day.app"})
    assert r.status == ChannelStatus.FAILED
    assert r.error  # 必须带原因，便于日志/告警定位


def test_bark_channel_missing_config_key_returns_failed_not_raises():
    """config 缺 key/url（解密损坏/脏数据）→ 返回 FAILED，不抛 KeyError 出 send。

    渠道契约：send 永远返回 SendResult，异常由内部吞掉转 FAILED；
    抛出会击穿 Notifier 编排（spec §10 推送失败应降级/告警，不崩调度）。
    """
    ch = BarkChannel(transport=httpx.MockTransport(lambda req: httpx.Response(200)))
    r = ch.send(_payload(), config={"url": "https://api.day.app"})  # 缺 key
    assert r.status == ChannelStatus.FAILED and r.error


def test_bark_channel_send_network_error():
    """Bark 网络异常（transport 抛错）→ FAILED 不冒泡，error 记录原因。"""
    def handler(req): raise httpx.ConnectError("boom")

    ch = BarkChannel(transport=httpx.MockTransport(handler))
    r = ch.send(_payload(), config={"key": "abc", "url": "https://api.day.app"})
    assert r.status == ChannelStatus.FAILED
    assert "boom" in (r.error or "")


# --- 飞书 -----------------------------------------------------------------

def test_feishu_channel_send_ok():
    """飞书 webhook 200 + StatusCode 0 → SENT，payload 为 text 卡片。"""
    captured = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["url"] = str(req.url)
        captured["body"] = req.read().decode()
        return httpx.Response(200, json={"StatusCode": 0})

    ch = FeishuChannel(transport=httpx.MockTransport(handler))
    r = ch.send(_payload(), config={"webhook": "https://open.feishu.cn/bot/v2/hook/x"})
    assert r.status == ChannelStatus.SENT
    assert captured["url"] == "https://open.feishu.cn/bot/v2/hook/x"
    # 飞书 text 卡片结构（不绑定 httpx JSON 分隔符空白）
    assert '"msg_type"' in captured["body"] and '"text"' in captured["body"]


def test_feishu_channel_send_fail():
    """飞书返回非 0 StatusCode → FAILED。"""
    def handler(req): return httpx.Response(200, json={"StatusCode": 19021})

    ch = FeishuChannel(transport=httpx.MockTransport(handler))
    r = ch.send(_payload(), config={"webhook": "https://open.feishu.cn/bot/v2/hook/x"})
    assert r.status == ChannelStatus.FAILED and r.error


def test_feishu_channel_missing_statuscode_field_is_failed_not_silent_sent():
    """飞书 200 但响应体无 StatusCode 字段 → FAILED，绝不静默判成功。

    静默失败纪律 + spec §10「推送失败」：飞书成功响应必带 StatusCode(=0)，
    缺失该字段属异常响应（网关拦截/接口变更/错误页 200）。旧实现用
    `.get("StatusCode", 0) == 0` 默认 0 → 缺字段即判 SENT，把异常响应误报为
    成功 → 降级/重试/告警全失效 → 中奖永不静默漏通知被破坏。必须显式要求
    StatusCode 字段存在且 == 0，与 BarkChannel 严格 `code == 200` 对齐。
    """
    def handler(req): return httpx.Response(200, json={"msg": "ok"})  # 无 StatusCode

    ch = FeishuChannel(transport=httpx.MockTransport(handler))
    r = ch.send(_payload(), config={"webhook": "https://open.feishu.cn/bot/v2/hook/x"})
    assert r.status == ChannelStatus.FAILED and r.error


def test_feishu_channel_send_network_error():
    """飞书网络异常（transport 抛错）→ FAILED 不冒泡，error 记录原因。

    与 test_bark_channel_send_network_error 对称（quality reviewer 指出测试不对称）：
    飞书与 Bark 同为 httpx 渠道，网络层故障（DNS/连接拒绝/超时）经 send 的 try/except
    须转 FAILED，绝不击穿 Notifier 编排（spec §10 推送失败降级/告警，不崩调度）。
    """
    def handler(req): raise httpx.ConnectError("boom")

    ch = FeishuChannel(transport=httpx.MockTransport(handler))
    r = ch.send(_payload(), config={"webhook": "https://open.feishu.cn/bot/v2/hook/x"})
    assert r.status == ChannelStatus.FAILED
    assert "boom" in (r.error or "")


def test_feishu_channel_missing_webhook_config_returns_failed_not_raises():
    """config 缺 webhook（解密损坏/脏数据）→ 返回 FAILED，不抛 KeyError。

    与 BarkChannel（缺 key/url）、EmailChannel（缺 address）对称：渠道契约
    要求 send 永远返回 SendResult，异常须内部吞掉转 FAILED，不击穿 Notifier。
    """
    ch = FeishuChannel(transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"StatusCode": 0})))
    r = ch.send(_payload(), config={"url": "https://x"})  # 缺 webhook
    assert r.status == ChannelStatus.FAILED and r.error


# --- 邮箱 -----------------------------------------------------------------

def test_email_channel_send(monkeypatch):
    """邮箱系统统一发件（spec §8.1）：用户只填收件地址，SMTP 运维方配置。"""
    from app.notifications.email_channel import EmailChannel

    sent = {}

    class FakeSMTP:
        def __init__(self, *a, **k): pass

        def __enter__(self): return self

        def __exit__(self, *a): pass

        def login(self, *a): pass

        def sendmail(self, frm, to, msg):
            sent["from"] = frm
            sent["to"] = to
            sent["msg"] = msg

    import smtplib
    monkeypatch.setattr(smtplib, "SMTP_SSL", lambda *a, **k: FakeSMTP())

    ch = EmailChannel(smtp_host="smtp.qq.com", smtp_port=465, smtp_user="u",
                      smtp_pass="p", smtp_from="lottery@example.com")
    r = ch.send(_payload(), config={"address": "user@example.com"})
    assert r.status == ChannelStatus.SENT and r.error is None
    # 统一发件地址 + 收件地址（用户只填 address）
    assert sent["from"] == "lottery@example.com"
    assert sent["to"] == ["user@example.com"]
    # MIME 头落位（body 经 utf-8/base64 编码，不验明文中文）
    assert "Subject:" in sent["msg"]
    assert "From: lottery@example.com" in sent["msg"]
    assert "To: user@example.com" in sent["msg"]


def test_email_channel_send_fail(monkeypatch):
    """SMTP sendmail 抛错 → FAILED 不冒泡。"""
    from app.notifications.email_channel import EmailChannel

    class FakeSMTP:
        def __init__(self, *a, **k): pass

        def __enter__(self): return self

        def __exit__(self, *a): pass

        def login(self, *a): pass

        def sendmail(self, *a): raise smtplib.SMTPException("auth failed")

    import smtplib
    monkeypatch.setattr(smtplib, "SMTP_SSL", lambda *a, **k: FakeSMTP())

    ch = EmailChannel(smtp_host="smtp.qq.com", smtp_port=465, smtp_user="u",
                      smtp_pass="p", smtp_from="lottery@example.com")
    r = ch.send(_payload(), config={"address": "user@example.com"})
    assert r.status == ChannelStatus.FAILED
    assert "auth failed" in (r.error or "")


def test_email_channel_missing_address_returns_failed_not_raises():
    """config 缺 address（解密损坏/脏数据）→ 返回 FAILED，不抛 KeyError。"""
    from app.notifications.email_channel import EmailChannel

    ch = EmailChannel(smtp_host="smtp.qq.com", smtp_port=465, smtp_user="u",
                      smtp_pass="p", smtp_from="lottery@example.com")
    r = ch.send(_payload(), config={})  # 缺 address
    assert r.status == ChannelStatus.FAILED and r.error


# --- 渠道生命周期（httpx Client 资源释放）------------------------------

def test_httpx_channels_close_cleanly():
    """Bark/飞书持有 httpx.Client，须可 close 释放连接池，支持 with 语法。

    渠道在应用启动时构造一次、长期存活；测试反复构造会泄漏连接。
    close()/上下文管理器是 httpx.Client 的标准生命周期约定。
    """
    ch = BarkChannel(transport=httpx.MockTransport(lambda req: httpx.Response(200)))
    with ch as ctx:
        assert ctx is ch  # __enter__ 返回自身
    # 退出 with 后 client 已关闭；再次 close 幂等不报错
    ch.close()
    ch.close()

    # 飞书同样持有 httpx.Client，须支持 close/with
    fch = FeishuChannel(transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"StatusCode": 0})))
    with fch:
        pass
    fch.close()  # 幂等
