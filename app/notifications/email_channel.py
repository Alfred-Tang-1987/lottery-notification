import contextlib
import smtplib
from email.mime.text import MIMEText

from app.config import SmtpEncryption
from app.notifications.base import (
    ChannelStatus,
    NotificationPayload,
    NotifierChannel,
    SendResult,
)


class _StarttlsSmtp:
    """明文 SMTP 的 context manager，可选 STARTTLS 升级（防 fd 泄漏）。

    hunter finding：早期实现 `ctx = smtplib.SMTP(...)` 在 with 外构造 + `ctx.starttls()`
    在 with 外调用，starttls() 抛异常时 ctx 未被 __exit__ 捕获 → 套接字描述符泄漏。
    本类把 SMTP 构造 + starttls 都包进 __enter__，确保任何异常路径都走 __exit__ 关闭。
    """

    def __init__(self, host: str, port: int, use_starttls: bool, timeout: float = 15):
        self._host = host
        self._port = port
        self._use_starttls = use_starttls
        self._timeout = timeout
        self._smtp: smtplib.SMTP | None = None

    def __enter__(self) -> smtplib.SMTP:
        self._smtp = smtplib.SMTP(self._host, self._port, timeout=self._timeout)
        if self._use_starttls:
            # starttls 失败时 Python 的 with 不会调 __exit__（__enter__ 抛异常 = 未进入 with 块），
            # 必须在此显式关闭套接字，否则 fd 泄漏（hunter finding）。
            try:
                self._smtp.starttls()
            except Exception:
                self._close_smtp()
                raise
        return self._smtp

    def __exit__(self, *exc):
        self._close_smtp()
        return False

    def _close_smtp(self) -> None:
        if self._smtp is not None:
            with contextlib.suppress(Exception):
                self._smtp.quit()


class EmailChannel(NotifierChannel):
    """邮箱渠道（spec §8.1 系统统一发件）。

    用户只填收件地址（config["address"]），发件 SMTP 由运维方在后台配置
    （家庭 NAS 小圈子场景，受邀用户免折腾 SMTP）；邮件从统一发件地址发出。
    渠道配置中的收件地址同样经 Fernet 加密存储，由 Notifier 解密后传入。

    smtp_encryption 决定连接方式（spec §12.2 row 9 + lesson L-20260706T010500Z）：
    - 'SSL/TLS' → smtplib.SMTP_SSL（端口通常 465）
    - 'STARTTLS' → smtplib.SMTP + s.starttls()（端口通常 587）
    - 'none' → smtplib.SMTP（明文，仅测试用）
    早期实现硬编码 SMTP_SSL 忽略 encryption，导致 Gmail/STARTTLS preset 是 no-op。
    """

    type = 'email'

    def __init__(
        self,
        smtp_host: str,
        smtp_port: int,
        smtp_user: str,
        smtp_pass: str,
        smtp_from: str,
        smtp_encryption: SmtpEncryption = 'SSL/TLS',
    ):
        self._host = smtp_host
        self._port = smtp_port
        self._user = smtp_user
        self._pass = smtp_pass
        self._from = smtp_from
        self._encryption = smtp_encryption

    def send(self, payload: NotificationPayload, config: dict) -> SendResult:
        try:
            addr = config['address']
            msg = MIMEText(payload.body, 'plain', 'utf-8')
            msg['Subject'] = payload.title
            msg['From'] = self._from
            msg['To'] = addr
            with self._open_smtp() as s:
                s.login(self._user, self._pass)
                s.sendmail(self._from, [addr], msg.as_string())
            return SendResult(ChannelStatus.SENT)
        except Exception as e:
            return SendResult(ChannelStatus.FAILED, error=str(e))

    def _open_smtp(self):
        """按 encryption 切换连接方式（lesson L-20260706T010500Z：preset 须真实生效）。

        返回一个 context manager，调用方用 with 进入。STARTTLS 的 starttls() 在 with
        块内调用，确保 starttls() 抛异常时 SMTP 套接字仍被 __exit__ 关闭（防 fd 泄漏）。
        """
        if self._encryption == 'SSL/TLS':
            return smtplib.SMTP_SSL(self._host, self._port, timeout=15)
        # STARTTLS / none 都先建明文 SMTP，STARTTLS 再升级加密
        return _StarttlsSmtp(self._host, self._port, self._encryption == 'STARTTLS')
