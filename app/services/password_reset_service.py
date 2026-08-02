# app/services/password_reset_service.py
"""密码重置服务（Plan 08 / T2+T3）。

纪律（CLAUDE.md / spec）：
- 统一话术语义：request_reset 对用户不存在/无 email 渠道/SMTP 未配/60s 内重发/
  send 失败全部静默返回 None，仅 RateLimited 上抛（API 层转 429）。
- 事务外 send：事务A 落码 commit 后才调渠道 send；失败开事务B 标作废（重试+告警，
  不回滚事务A——HTTP 路径不持写锁等 SMTP 网络 IO）。
- session 由调用方（API 层 get_session_dep）注入，service 不在请求存活期内自建
  Session 做事务A——事务B/告警等请求外操作才用 self._engine 新开短 Session。
- 渠道白名单 = 仅 email（autoplan 决议）：不查 bark/feishu。
"""

import hashlib
import logging
import secrets
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from app.infrastructure.crypto import CryptoService
from app.models import NotificationChannel, PasswordResetCode, User
from app.notifications._decrypt import decrypt_channel_config
from app.notifications.base import ChannelStatus, NotificationPayload, NotifierChannel

logger = logging.getLogger(__name__)


class RateLimited(Exception):
    """IP 限流超限（API 层转 429）。"""


class ResetRejected(Exception):
    """验证码错误/过期/超 attempts/用户不存在（API 层转 400 统一文案）。"""


class RateLimiter:
    """内存滑动窗口限流（单进程语义，实例注入避免测试间状态泄漏）。

    依赖 uvicorn 单 worker（Dockerfile CMD 无 --workers）；多 worker 部署须迁 Redis。
    """

    def __init__(self, max_per_minute: int = 3):
        self._max = max_per_minute
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str) -> bool:
        """记录一次访问；窗口内超限返回 False（不记录该次）。"""
        now = time.monotonic()
        with self._lock:
            hits = [t for t in self._hits.get(key, []) if now - t < 60]
            if len(hits) >= self._max:
                self._hits[key] = hits
                return False
            hits.append(now)
            self._hits[key] = hits
            return True


def _now_naive_utc() -> datetime:
    """naive UTC，与 TimestampMixin.created_at 同时区同数值（CLAUDE.md 纪律）。"""
    return datetime.now(UTC).replace(tzinfo=None)


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


class PasswordResetService:
    def __init__(
        self,
        engine: Engine,
        *,
        email_channel: NotifierChannel | None,
        crypto: CryptoService,
        rate_limiter: RateLimiter | None = None,
        admin_alert: Callable[[str, str], None] | None = None,
        code_ttl_minutes: int = 15,
        max_attempts: int = 5,
        resend_interval_seconds: int = 60,
        send_retries: int = 2,
    ):
        self._engine = engine
        self._email_channel = email_channel
        self._crypto = crypto
        self._rate_limiter = rate_limiter or RateLimiter()
        self._admin_alert = admin_alert
        self._code_ttl = timedelta(minutes=code_ttl_minutes)
        self._max_attempts = max_attempts
        self._resend_interval = timedelta(seconds=resend_interval_seconds)
        self._send_retries = send_retries

    # ---- request_reset（T2） ----

    def request_reset(self, username: str, *, client_ip: str, session: Session) -> None:
        """统一话术语义：任何软失败静默返回；仅 IP 超限抛 RateLimited。

        时序（spec §4.1）：限流 → 查用户 → 查 email 渠道 → 60s 重发窗 →
        事务A（作废旧码+插新码，单 commit，用注入 session）→ 事务外 send →
        失败事务B（短退避重试标作废 + admin 告警，self._engine 短 Session）。

        pool_size=1 纪律：commit 前把 send 所需数据（uid / 解密后 config / code_id）
        全部取到局部变量；commit 后绝不触碰 session 挂接对象——过期行任一属性访问
        都会重新占用唯一连接，事务B 的短 Session 将拿不到连接（QueuePool deadlock）。
        故解密也在 commit 前做（纯 CPU，不涉网络），解密失败直接不写码静默返回。
        """
        if not self._rate_limiter.hit(client_ip):
            raise RateLimited(client_ip)

        user = session.exec(select(User).where(User.username == username)).first()
        if user is None:
            logger.info('password_reset_unknown_user username=%s', username)
            return
        uid = user.id
        if not user.enabled:
            logger.info('password_reset_disabled_user user_id=%s', uid)
            return

        ch_row = session.exec(
            select(NotificationChannel).where(
                NotificationChannel.user_id == uid,
                NotificationChannel.type == 'email',
                NotificationChannel.enabled == True,  # noqa: E712
            )
        ).first()
        if ch_row is None or self._email_channel is None:
            logger.info(
                'password_reset_no_email_channel user_id=%s smtp_configured=%s',
                uid, self._email_channel is not None,
            )
            return

        now = _now_naive_utc()
        latest = session.exec(
            select(PasswordResetCode)
            .where(
                PasswordResetCode.user_id == uid,
                PasswordResetCode.used_at.is_(None),
            )
            .order_by(PasswordResetCode.id.desc())
        ).first()
        if latest is not None and now - latest.created_at < self._resend_interval:
            logger.info('password_reset_resend_skipped user_id=%s', uid)
            return

        # 事务前解密（ch_row 仍挂接未过期；解密失败内部已 WARNING → 不写码静默返回，
        # 避免"写了再作废"的幽灵码路径，也不占事务B 作废次数）。
        config = decrypt_channel_config(ch_row, self._crypto)
        if config is None:
            return

        code = f'{secrets.randbelow(900000) + 100000:06d}'
        # 事务A：作废旧码 + 插新码，单 commit（注入 session，不嵌套）。
        for old in session.exec(
            select(PasswordResetCode).where(
                PasswordResetCode.user_id == uid,
                PasswordResetCode.used_at.is_(None),
            )
        ).all():
            old.used_at = now
            session.add(old)
        row = PasswordResetCode(
            user_id=uid,
            code_hash=_hash_code(code),
            channel_type='email',
            expires_at=now + self._code_ttl,
        )
        session.add(row)
        session.flush()  # PK 先生效，捕获 code_id
        code_id = row.id
        session.commit()  # 释放唯一连接（pool_size=1）→ 事务B 短 Session 才能拿到连接

        # 事务外：send（不持 DB 写锁等 SMTP）。只用局部变量，不再碰 session 对象。
        payload = NotificationPayload(
            title='【兑奖了吗】密码重置验证码',
            body=f'验证码 {code}，15 分钟内有效。若非本人操作请忽略。',
            user_id=uid,
        )
        result = self._email_channel.send(payload, config)
        if result.status != ChannelStatus.SENT:
            logger.warning(
                'password_reset_send_failed user_id=%s code_id=%s error=%s',
                uid, code_id, result.error,
            )
            self._invalidate_with_retry(code_id, reason='send_failed')

    def _invalidate_with_retry(self, code_id: int, *, reason: str) -> None:
        """事务B：标码作废，短退避重试；同时触发 admin 告警（autoplan C1）。

        告警与作废成败解耦：send 失败本身即需告警（测试铁律——send_retries=0 时
        作废首试必成功仍须告警）；仅当作废重试耗尽再补 ERROR 记幽灵活码风险。
        不回滚事务A（保护"HTTP 不持写锁等网络 IO"前提）。self._engine 新开短
        Session——调用点在注入 session commit 之后，无嵌套。
        """
        last_exc: Exception | None = None
        for attempt in range(self._send_retries + 1):
            try:
                with Session(self._engine) as s:
                    row = s.get(PasswordResetCode, code_id)
                    if row is not None and row.used_at is None:
                        row.used_at = _now_naive_utc()
                        s.add(row)
                        s.commit()
                break
            except Exception as exc:  # 重试须兜住一切 DB 故障
                last_exc = exc
                if attempt < self._send_retries:
                    time.sleep(1 + attempt)  # 秒级短退避（非 Notifier 指数退避）
        else:
            logger.error(
                'password_reset_invalidate_failed code_id=%s reason=%s（幽灵活码风险：'
                '该码已发用户但作废失败，TTL 内仍可被消耗 attempts）',
                code_id, reason, exc_info=last_exc,
            )
        if self._admin_alert is not None:
            try:
                self._admin_alert(
                    '【兑奖了吗】密码重置告警',
                    f'验证码发送/作废失败（code_id={code_id}, reason={reason}），请检查 SMTP 配置。',
                )
            except Exception:
                logger.error('password_reset_admin_alert_failed', exc_info=True)
