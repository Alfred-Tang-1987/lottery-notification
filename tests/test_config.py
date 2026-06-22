import pytest
from app.config import Settings


def test_settings_load_from_env(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "x" * 32)
    monkeypatch.setenv("CRYPTO_KEY_V1", "k" * 32)
    s = Settings()
    assert s.jwt_secret == "x" * 32
    assert s.crypto_keys[1] == "k" * 32
    assert s.tz == "Asia/Shanghai"


def test_settings_missing_required_raises(monkeypatch):
    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.delenv("CRYPTO_KEY_V1", raising=False)
    # 模块级 settings 已初始化，直接测试 Settings 构造不传参应失败
    with pytest.raises(Exception):
        Settings(jwt_secret="", crypto_key_v1="")  # 空值触发 min_length 校验


def test_multi_key_versions(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "x" * 32)
    monkeypatch.setenv("CRYPTO_KEY_V1", "old-key-" * 4)
    monkeypatch.setenv("CRYPTO_KEY_V2", "new-key-" + "x" * 23)
    s = Settings()
    assert s.crypto_keys[2].startswith("new-key")
    assert s.current_key_version == 2


def test_email_requires_admin_bark(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "x" * 32)
    monkeypatch.setenv("CRYPTO_KEY_V1", "k" * 32)
    monkeypatch.setenv("SMTP_HOST", "smtp.qq.com")
    monkeypatch.delenv("ADMIN_BARK_KEY", raising=False)
    s = Settings()
    with pytest.raises(ValueError, match="Bark"):
        s.validate_email_bark_fallback()
