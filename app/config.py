"""应用配置（pydantic-settings）。

Spec §124 启动校验要求：JWT_SECRET / CRYPTO_KEY 必须在启动时验证。
本模块用 field_validator 在 Settings 构造时即校验 CRYPTO_KEY 是真实可用的 Fernet key
（44 url-safe base64 字符，解码后 32 字节），并在 validate_startup() 中实例化
CryptoService 端到端证明密钥可用，避免运行时首次加解密才崩。
"""

import threading
from typing import Literal

from cryptography.fernet import Fernet
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Fernet key = 32 bytes, url-safe base64 编码后固定 44 字符（含 1 个 padding '='）。
_FERNET_KEY_LENGTH = 44
# JWT 用于 HMAC 签名，生产强度下限 32 字符。
_JWT_SECRET_MIN = 32

SmtpEncryption = Literal['SSL/TLS', 'STARTTLS', 'none']


def _validate_fernet_key(value: str, field_name: str) -> str:
    """校验 value 是真实可用的 Fernet key（构造 Fernet 实例，失败即 ValueError）。"""
    if not isinstance(value, str) or len(value) != _FERNET_KEY_LENGTH:
        raise ValueError(
            f'{field_name} 必须是 {_FERNET_KEY_LENGTH} 字符的 url-safe base64 Fernet key'
            f'（生成：python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"）'
        )
    try:
        Fernet(value.encode())
    except Exception as exc:
        raise ValueError(f'{field_name} 不是有效的 Fernet key：{exc}') from exc
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')

    # JWT_SECRET：HMAC 签名密钥，生产下限 32 字符（spec §124）
    jwt_secret: str = Field(min_length=_JWT_SECRET_MIN)
    # CRYPTO_KEY_V1/V2：Fernet 多版本密钥。min_length 设为真实 Fernet 长度 44，
    # 并由 _validate_fernet_key 进一步校验可构造 Fernet 实例。
    crypto_key_v1: str = Field(alias='CRYPTO_KEY_V1', min_length=_FERNET_KEY_LENGTH)
    crypto_key_v2: str | None = Field(default=None, alias='CRYPTO_KEY_V2')

    mxnzp_api_key: str = ''
    juhe_api_key: str = ''

    smtp_host: str | None = None
    smtp_port: int = 465
    smtp_encryption: SmtpEncryption = 'SSL/TLS'
    smtp_user: str | None = None
    smtp_pass: str | None = None
    smtp_from: str | None = None
    admin_bark_key: str | None = None

    database_url: str = 'sqlite:///./data/lottery.db'
    tz: str = 'Asia/Shanghai'
    cors_origins: list[str] = Field(default_factory=lambda: ['http://localhost:5173'])

    # 调度器启动开关（spec §4.3）：生产默认开启；运维排障/迁移时可关。
    # 测试通过 SCHEDULER_ENABLED=false 跳过 lifespan 内抓取/比对网络与后台线程。
    scheduler_enabled: bool = Field(default=True, alias='SCHEDULER_ENABLED')

    # Cookie secure 标志（安全审查 #2）：生产默认 True——session/CSRF cookie 仅经 HTTPS
    # 传输，防明文泄露 bearer。开发期（http://localhost）与测试经 COOKIE_SECURE=false 关闭。
    cookie_secure: bool = Field(default=True, alias='COOKIE_SECURE')

    @field_validator('crypto_key_v1')
    @classmethod
    def _check_crypto_key_v1(cls, v: str) -> str:
        return _validate_fernet_key(v, 'CRYPTO_KEY_V1')

    @field_validator('crypto_key_v2')
    @classmethod
    def _check_crypto_key_v2(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return _validate_fernet_key(v, 'CRYPTO_KEY_V2')

    @property
    def crypto_keys(self) -> dict[int, str]:
        keys = {1: self.crypto_key_v1}
        if self.crypto_key_v2:
            keys[2] = self.crypto_key_v2
        return keys

    @property
    def current_key_version(self) -> int:
        return max(self.crypto_keys)

    @property
    def email_enabled(self) -> bool:
        return bool(self.smtp_host and self.smtp_user and self.smtp_pass and self.smtp_from)

    def validate_email_bark_fallback(self) -> None:
        if self.email_enabled and not self.admin_bark_key:
            raise ValueError('启用 email 渠道时必须配置 ADMIN_BARK_KEY（Bark 兜底告警，避免邮件循环依赖）')


# ---------- 单例缓存（线程安全） ----------

_settings: Settings | None = None
_settings_lock = threading.Lock()


def get_settings() -> Settings:
    """返回全局 Settings 单例（线程安全，惰性初始化）。

    APScheduler 线程并发首次调用时不会重复实例化。测试用 reset_settings_cache()
    清除缓存以注入 monkeypatch 的环境变量。
    """
    global _settings
    if _settings is None:
        with _settings_lock:
            # double-checked locking：持锁后再检一次，避免多个线程同时通过外层检查
            if _settings is None:
                _settings = Settings()
    return _settings


def reset_settings_cache() -> None:
    """清除 Settings 单例缓存（测试用）。生产代码不应调用。"""
    global _settings
    with _settings_lock:
        _settings = None
