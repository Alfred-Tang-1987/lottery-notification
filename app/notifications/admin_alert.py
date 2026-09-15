"""admin Bark 告警构造（plan-11：自 app/api/auth.py 迁出，auth 与 scheduler 共用）。

运维兜底通道：不走 Notifier/用户渠道体系（无 NotificationLog、无 DND）——
admin 告警的价值在「系统级故障时也能叫到人」，必须绕开业务通知管线。

送达契约（autoplan M2）：BarkChannel.send 吞掉一切失败返回 SendResult 而非抛异常
（bark.py:49-50）。若忽略返回值，调用方 try/except 永不触发，「送达成功才转移」
退化为「尝试即转移」——2026-09-15 DNS 事故（告警通道同挂）场景下告警永不重试。
故 status != SENT 时抛 RuntimeError，让评估器/调用方的 except 正确识别未送达。
"""

from collections.abc import Callable

from app.config import Settings
from app.notifications.bark import BarkChannel
from app.notifications.base import ChannelStatus, NotificationPayload


def admin_bark_config(settings: Settings) -> dict | None:
    """admin Bark 通道配置组装（单一真源）：Notifier 兜底通道（main.py）与
    build_admin_alert（健康告警）共用同一份 {'key', 'url'}——BarkChannel 的 config
    契约变化只改这里，两路消费不漂移（/simplify 收敛，此前两处各自组装）。

    ADMIN_BARK_KEY 未配 → None：调用方各自降级（main.py 不建兜底通道 /
    build_admin_alert 返回 None）。
    """
    if not settings.admin_bark_key:
        return None
    return {'key': settings.admin_bark_key, 'url': settings.admin_bark_url}


def build_admin_alert() -> Callable[[str, str], None] | None:
    """复用 ADMIN_BARK_KEY 构造告警函数；未配 key → None（调用方降级为只记日志）。

    返回的 callable：送达成功正常返回；未送达（HTTP 错 / 业务码非 200 / 传输异常）
    抛 RuntimeError——调用方据此保持状态、下轮重试。
    调用方发送后应调用其 close 属性（若存在）确定性释放 httpx.Client
    （bark close 一等纪律；evaluate_source_alerts 已如此）。
    """
    from app.config import get_settings

    config = admin_bark_config(get_settings())
    if config is None:
        return None
    bark = BarkChannel()

    def _alert(title: str, body: str) -> None:
        result = bark.send(NotificationPayload(title=title, body=body), config)
        if result.status != ChannelStatus.SENT:
            # dx-voice F8：error 可能为 None，别渲染成「未送达: None」。
            raise RuntimeError(f'admin bark 未送达: {result.error or "未知原因"}')

    # close 通道（/simplify，efficiency 审查 finding）：惰性工厂堵住了「无待发送
    # 不建 client」，但建出的 client 之前仍靠 GC 兜底回收。挂 close 供调用方
    # 发送后确定性释放；工厂签名不变（eng H1/M2 接口已冻结），测试注入的
    # 无 close 属性假 sender 自然兼容（消费点 getattr 探测）。
    _alert.close = bark.close  # type: ignore[attr-defined]

    return _alert
