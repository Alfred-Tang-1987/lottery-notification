import threading

import pytest
from cryptography.fernet import Fernet

from app.config import Settings, get_settings, reset_settings_cache


# ---------- helpers ----------

def _valid_fernet_key() -> str:
    return Fernet.generate_key().decode()


def _set_required_env(monkeypatch, *, jwt: str | None = None, v1: str | None = None):
    """Set required env vars to valid values (real Fernet keys, long JWT)."""
    monkeypatch.setenv("JWT_SECRET", jwt or ("x" * 32))
    monkeypatch.setenv("CRYPTO_KEY_V1", v1 or _valid_fernet_key())
    return monkeypatch


# ---------- existing behavior (updated to valid keys) ----------

def test_settings_load_from_env(monkeypatch):
    k1 = _valid_fernet_key()
    _set_required_env(monkeypatch, v1=k1)
    s = Settings()
    assert s.jwt_secret == "x" * 32
    assert s.crypto_keys[1] == k1
    assert s.tz == "Asia/Shanghai"


def test_settings_missing_required_raises(monkeypatch):
    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.delenv("CRYPTO_KEY_V1", raising=False)
    with pytest.raises(Exception):
        Settings()


def test_multi_key_versions(monkeypatch):
    k1, k2 = _valid_fernet_key(), _valid_fernet_key()
    _set_required_env(monkeypatch, v1=k1)
    monkeypatch.setenv("CRYPTO_KEY_V2", k2)
    s = Settings()
    assert s.crypto_keys[2] == k2
    assert s.current_key_version == 2


def test_email_requires_admin_bark(monkeypatch):
    _set_required_env(monkeypatch)
    monkeypatch.setenv("SMTP_HOST", "smtp.qq.com")
    monkeypatch.setenv("SMTP_USER", "user@qq.com")
    monkeypatch.setenv("SMTP_PASS", "pass")
    monkeypatch.setenv("SMTP_FROM", "lottery@example.com")
    monkeypatch.delenv("ADMIN_BARK_KEY", raising=False)
    s = Settings()
    with pytest.raises(ValueError, match="Bark"):
        s.validate_email_bark_fallback()


# ---------- review round 1 fixes ----------

# [HIGH] invalid Fernet key must be rejected at Settings() construction time
def test_invalid_fernet_key_v1_rejected(monkeypatch):
    """A 16-char string passes min_length but is NOT a valid Fernet key."""
    monkeypatch.setenv("JWT_SECRET", "x" * 32)
    monkeypatch.setenv("CRYPTO_KEY_V1", "k" * 16)  # passes old min_length=16
    with pytest.raises(Exception):
        Settings()


def test_invalid_fernet_key_v2_rejected(monkeypatch):
    _set_required_env(monkeypatch)
    monkeypatch.setenv("CRYPTO_KEY_V2", "not-a-fernet-key-at-all!!")
    with pytest.raises(Exception):
        Settings()


def test_valid_fernet_keys_accepted(monkeypatch):
    """Sanity: a real Fernet key round-trips through Settings validation."""
    k1, k2 = _valid_fernet_key(), _valid_fernet_key()
    _set_required_env(monkeypatch, v1=k1)
    monkeypatch.setenv("CRYPTO_KEY_V2", k2)
    s = Settings()  # must not raise
    assert s.crypto_key_v1 == k1
    assert s.crypto_key_v2 == k2


# [MEDIUM] jwt_secret floor raised to 32
def test_short_jwt_secret_rejected(monkeypatch):
    k1 = _valid_fernet_key()
    monkeypatch.setenv("JWT_SECRET", "short")  # < 32
    monkeypatch.setenv("CRYPTO_KEY_V1", k1)
    with pytest.raises(Exception):
        Settings()


# [LOW] smtp_encryption constrained to allowed set
def test_smtp_encryption_valid_accepted(monkeypatch):
    _set_required_env(monkeypatch)
    monkeypatch.setenv("SMTP_ENCRYPTION", "SSL/TLS")
    Settings()  # ok


def test_smtp_encryption_typo_rejected(monkeypatch):
    _set_required_env(monkeypatch)
    monkeypatch.setenv("SMTP_ENCRYPTION", "SSLTLS")  # typo
    with pytest.raises(Exception):
        Settings()


# [LOW] get_settings typed + cache reset helper for tests
def test_get_settings_caches_singleton(monkeypatch):
    reset_settings_cache()
    _set_required_env(monkeypatch)
    a = get_settings()
    b = get_settings()
    assert a is b


def test_reset_settings_cache_clears(monkeypatch):
    reset_settings_cache()
    _set_required_env(monkeypatch)
    a = get_settings()
    reset_settings_cache()
    _set_required_env(monkeypatch)
    b = get_settings()
    assert a is not b


# [LOW] get_settings thread-safe (no double-instantiate under concurrency)
def test_get_settings_thread_safe(monkeypatch):
    reset_settings_cache()
    _set_required_env(monkeypatch)
    instances: list[Settings] = []
    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()
        instances.append(get_settings())

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    first = instances[0]
    assert all(inst is first for inst in instances), "get_settings returned distinct instances"
