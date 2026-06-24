import httpx
from app.notifications.base import (
    NotifierChannel, NotificationPayload, SendResult, ChannelStatus,
)


class BarkChannel(NotifierChannel):
    """Bark 推送（spec §8.1）：iOS 原生推送，配置 = key + URL。

    POST {url}/{key}，body={"title":..., "body":...}（Bark 官方 API）。
    成功判据 = HTTP 200 且响应体 code == 200：Bark 对 key 失效/参数错误常返回
    HTTP 200 + body {"code":400,...}，仅判 HTTP 状态会把失败推送静默判成功（spec §10）。
    """
    type = "bark"

    def __init__(self, transport: httpx.BaseTransport | None = None):
        # transport 注入仅用于测试（MockTransport）；生产留空走默认 HTTP。
        self._client = httpx.Client(transport=transport, timeout=10.0)

    def send(self, payload: NotificationPayload, config: dict) -> SendResult:
        try:
            url = config["url"].rstrip("/") + f"/{config['key']}"
            r = self._client.post(url, json={"title": payload.title, "body": payload.body})
            if r.status_code != 200:
                return SendResult(ChannelStatus.FAILED, error=f"bark HTTP {r.status_code}")
            # Bark 业务码：200=成功，其余（400 等）=失败，须带原因便于日志/告警定位。
            body = r.json()
            if body.get("code") != 200:
                return SendResult(
                    ChannelStatus.FAILED,
                    error=f"bark code {body.get('code')} msg={body.get('message', '')}",
                )
            return SendResult(ChannelStatus.SENT)
        except Exception as e:
            return SendResult(ChannelStatus.FAILED, error=str(e))

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
