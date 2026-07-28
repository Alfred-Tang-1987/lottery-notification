import httpx

from app.notifications.base import (
    ChannelStatus,
    NotificationPayload,
    NotifierChannel,
    SendResult,
)

# Bark 官方默认服务端（spec §8.1）。当用户 config 未显式提供 url 时兜底--
# API 层 _REQUIRED_CONFIG_KEYS['bark']={'key'} 明确 url 可选（channels.py:46），
# Channel 实现必须与此契约对齐，否则用户只填 key 时 send 在 config['url'] 处
# KeyError -> 被吞成 FAILED -> 全渠道失败 -> 推送静默丢失（2026-07-28 NAS 实测）。
# 与 main.py admin_bark_config 默认 url 同源，保持单一默认真源。
DEFAULT_BARK_URL = 'https://api.day.app'


class BarkChannel(NotifierChannel):
    """Bark 推送（spec §8.1）：iOS 原生推送，配置 = key + URL（url 可选，缺省走官方默认）。

    POST {url}/{key}，body={"title":..., "body":...}（Bark 官方 API）。
    成功判据 = HTTP 200 且响应体 code == 200：Bark 对 key 失效/参数错误常返回
    HTTP 200 + body {"code":400,...}，仅判 HTTP 状态会把失败推送静默判成功（spec §10）。
    """

    type = 'bark'

    def __init__(self, transport: httpx.BaseTransport | None = None):
        # transport 注入仅用于测试（MockTransport）；生产留空走默认 HTTP。
        self._client = httpx.Client(transport=transport, timeout=10.0)

    def send(self, payload: NotificationPayload, config: dict) -> SendResult:
        try:
            # url 可选：缺省走官方默认（API 契约 url 非必填，见模块 docstring）。
            # config.get 而非 config['url']--后者缺 url 即 KeyError 被吞成 FAILED。
            base = config.get('url', DEFAULT_BARK_URL).rstrip('/')
            url = f'{base}/{config["key"]}'
            r = self._client.post(url, json={'title': payload.title, 'body': payload.body})
            if r.status_code != 200:
                return SendResult(ChannelStatus.FAILED, error=f'bark HTTP {r.status_code}')
            # Bark 业务码：200=成功，其余（400 等）=失败，须带原因便于日志/告警定位。
            body = r.json()
            if body.get('code') != 200:
                return SendResult(
                    ChannelStatus.FAILED,
                    error=f'bark code {body.get("code")} msg={body.get("message", "")}',
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
