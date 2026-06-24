import httpx
from app.notifications.base import (
    NotifierChannel, NotificationPayload, SendResult, ChannelStatus,
)


class FeishuChannel(NotifierChannel):
    """飞书群机器人 webhook（spec §8.1）：复用用户现有飞书基础设施。

    POST {webhook}，text 文本消息；成功判据 = HTTP 200 且响应体显式含 StatusCode == 0
    （飞书 webhook 成功返回 {"StatusCode": 0}，失败返回业务码如 19021）。StatusCode
    字段缺失属异常响应，判 FAILED 不静默成功——与 BarkChannel 严格业务码校验对齐。
    """
    type = "feishu"

    def __init__(self, transport: httpx.BaseTransport | None = None):
        self._client = httpx.Client(transport=transport, timeout=10.0)

    def send(self, payload: NotificationPayload, config: dict) -> SendResult:
        try:
            r = self._client.post(config["webhook"], json={
                "msg_type": "text",
                "content": {"text": f"{payload.title}\n{payload.body}"},
            })
            if r.status_code != 200:
                return SendResult(
                    ChannelStatus.FAILED,
                    error=f"feishu HTTP {r.status_code} body={r.text[:120]}",
                )
            body = r.json()
            # 飞书成功响应必带 StatusCode=0；显式要求字段存在且 == 0，
            # 绝不用默认值兜底——缺字段属异常响应（网关拦截/接口变更/错误页 200），
            # 默认判 SENT 会静默吞掉失败、破坏「中奖永不静默漏通知」（spec §10）。
            # 与 BarkChannel 严格 `code == 200` 对齐。
            if "StatusCode" not in body or body["StatusCode"] != 0:
                return SendResult(
                    ChannelStatus.FAILED,
                    error=f"feishu StatusCode={body.get('StatusCode', '<missing>')} body={r.text[:120]}",
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
