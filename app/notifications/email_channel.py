import smtplib
from email.mime.text import MIMEText

from app.notifications.base import (
    ChannelStatus,
    NotificationPayload,
    NotifierChannel,
    SendResult,
)


class EmailChannel(NotifierChannel):
    """邮箱渠道（spec §8.1 系统统一发件）。

    用户只填收件地址（config["address"]），发件 SMTP 由运维方在后台配置
    （家庭 NAS 小圈子场景，受邀用户免折腾 SMTP）；邮件从统一发件地址发出。
    渠道配置中的收件地址同样经 Fernet 加密存储，由 Notifier 解密后传入。
    """

    type = 'email'

    def __init__(self, smtp_host: str, smtp_port: int, smtp_user: str, smtp_pass: str, smtp_from: str):
        self._host = smtp_host
        self._port = smtp_port
        self._user = smtp_user
        self._pass = smtp_pass
        self._from = smtp_from

    def send(self, payload: NotificationPayload, config: dict) -> SendResult:
        try:
            addr = config['address']
            msg = MIMEText(payload.body, 'plain', 'utf-8')
            msg['Subject'] = payload.title
            msg['From'] = self._from
            msg['To'] = addr
            with smtplib.SMTP_SSL(self._host, self._port, timeout=15) as s:
                s.login(self._user, self._pass)
                s.sendmail(self._from, [addr], msg.as_string())
            return SendResult(ChannelStatus.SENT)
        except Exception as e:
            return SendResult(ChannelStatus.FAILED, error=str(e))
