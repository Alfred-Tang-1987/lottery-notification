from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    jwt_secret: str = Field(min_length=16)
    crypto_key_v1: str = Field(alias="CRYPTO_KEY_V1", min_length=16)
    crypto_key_v2: str | None = Field(default=None, alias="CRYPTO_KEY_V2")

    mxnzp_api_key: str = ""
    juhe_api_key: str = ""

    smtp_host: str | None = None
    smtp_port: int = 465
    smtp_encryption: str = "SSL/TLS"
    smtp_user: str | None = None
    smtp_pass: str | None = None
    smtp_from: str | None = None
    admin_bark_key: str | None = None

    database_url: str = "sqlite:///./data/lottery.db"
    tz: str = "Asia/Shanghai"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

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
        return bool(self.smtp_host and self.smtp_user and self.smtp_pass)

    def validate_email_bark_fallback(self) -> None:
        if self.email_enabled and not self.admin_bark_key:
            raise ValueError(
                "启用 email 渠道时必须配置 ADMIN_BARK_KEY（Bark 兜底告警，避免邮件循环依赖）"
            )


settings = None  # type: ignore


def get_settings() -> Settings:
    global settings
    if settings is None:
        settings = Settings()  # type: ignore[call-arg]
    return settings
