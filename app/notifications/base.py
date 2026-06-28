from dataclasses import dataclass
from enum import StrEnum


class ChannelStatus(StrEnum):
    """单次推送的渠道投递结果（spec §8.2 多渠道降级 / §10 重试）。"""

    SENT = 'sent'
    FAILED = 'failed'
    PENDING = 'pending'


@dataclass(frozen=True)
class NotificationPayload:
    """推送内容（spec §8.3）。渠道插件只消费 title/body，其余字段供 Notifier 记日志/去重。"""

    title: str
    body: str
    user_id: int | None = None
    lottery_code: str | None = None
    draw_no: str | None = None
    tier: int | None = None
    amount: int | None = None  # 单位：分（None = 浮动奖待官方派奖）


@dataclass(frozen=True)
class SendResult:
    status: ChannelStatus
    error: str | None = None


class NotifierChannel:
    """渠道插件接口（spec §8.1）。

    config 由 Notifier 从加密存储（NotificationChannel.config_json + key_version，
    Fernet 解密）还原后以明文 dict 传入——渠道本身不感知加密，可独立单测。

    send 永远返回 SendResult，绝不向调用方抛异常——配置缺失/网络故障/业务码错误
    一律转 FAILED，由 Notifier 走降级/重试/告警（spec §10）。
    """

    type: str

    def send(self, payload: NotificationPayload, config: dict) -> SendResult:
        raise NotImplementedError

    def close(self) -> None:
        """释放渠道持有的资源（连接池等）。持有 httpx.Client 的子类须重写。

        默认空操作：无需释放资源（如 EmailChannel 用完即关的 smtplib）的子类继承即可。
        """
        return None
