"""admin Bark 告警构造（plan-11：自 app/api/auth.py 迁出，auth 与 scheduler 共用）。

运维兜底通道：不走 Notifier/用户渠道体系（无 NotificationLog、无 DND）——
admin 告警的价值在「系统级故障时也能叫到人」，必须绕开业务通知管线。

送达契约（autoplan M2）：BarkChannel.send 吞掉一切失败返回 SendResult 而非抛异常
（bark.py:49-50）。若忽略返回值，调用方 try/except 永不触发，「送达成功才转移」
退化为「尝试即转移」——2026-09-15 DNS 事故（告警通道同挂）场景下告警永不重试。
故 status != SENT 时抛 RuntimeError，让评估器/调用方的 except 正确识别未送达。
"""

from collections.abc import Callable

from app.notifications.bark import BarkChannel
from app.notifications.base import ChannelStatus, NotificationPayload


def build_admin_alert() -> Callable[[str, str], None] | None:
    """复用 ADMIN_BARK_KEY 构造告警函数；未配 key → None（调用方降级为只记日志）。

    返回的 callable：送达成功正常返回；未送达（HTTP 错 / 业务码非 200 / 传输异常）
    抛 RuntimeError——调用方据此保持状态、下轮重试。
    """
    from app.config import get_settings

    settings = get_settings()
    if not settings.admin_bark_key:
        return None
    bark = BarkChannel()
    # url 走 settings（dx-voice F20：自建 Bark 服务端是 NAS 真实场景；
    # 与 main.py admin_bark_config 同源——单一默认真源）。
    config = {'key': settings.admin_bark_key, 'url': settings.admin_bark_url}

    def _alert(title: str, body: str) -> None:
        result = bark.send(NotificationPayload(title=title, body=body), config)
        if result.status != ChannelStatus.SENT:
            # dx-voice F8：error 可能为 None，别渲染成「未送达: None」。
            raise RuntimeError(f'admin bark 未送达: {result.error or "未知原因"}')

    return _alert
