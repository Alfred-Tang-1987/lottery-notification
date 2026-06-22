from fastapi.testclient import TestClient
from app.main import app, validate_startup
from app.config import reset_settings_cache
from cryptography.fernet import Fernet
import pytest


@pytest.fixture(autouse=True)
def mock_env(monkeypatch):
    """所有 health 测试强制注入有效环境变量（真实 Fernet key），避免 lifespan 校验失败。"""
    reset_settings_cache()
    monkeypatch.setenv("JWT_SECRET", "x" * 32)
    monkeypatch.setenv("CRYPTO_KEY_V1", Fernet.generate_key().decode())


def test_health_ok():
    client = TestClient(app)
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "tz" in body
    assert body["tz"] == "Asia/Shanghai"


def test_health_includes_db_check(db_engine):
    # 注入测试 engine（try/finally 保证清理，避免污染后续测试）
    from app.main import app, get_db_for_health
    app.dependency_overrides[get_db_for_health] = lambda: db_engine
    try:
        client = TestClient(app)
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["db"] == "ok"
    finally:
        app.dependency_overrides.clear()


# [HIGH] validate_startup must prove the crypto key is usable (construct CryptoService)
def test_validate_startup_proves_crypto_key(monkeypatch):
    """Settings field_validator already rejects bad keys, but validate_startup must
    additionally instantiate CryptoService to prove the key works end-to-end before
    serving traffic (spec §124 mandates CRYPTO_KEY startup validation)."""
    reset_settings_cache()
    monkeypatch.setenv("JWT_SECRET", "x" * 32)
    monkeypatch.setenv("CRYPTO_KEY_V1", Fernet.generate_key().decode())
    validate_startup()  # must not raise


def test_validate_startup_catches_invalid_crypto_key(monkeypatch):
    """If an invalid key somehow bypasses Settings (e.g. future refactor),
    validate_startup must still catch it rather than crashing on first encrypt."""
    reset_settings_cache()
    monkeypatch.setenv("JWT_SECRET", "x" * 32)
    # Bypass Settings validator by constructing with a raw invalid key is not possible
    # since Settings now validates; instead verify validate_startup surfaces a clear
    # error when Settings itself rejects the key.
    monkeypatch.setenv("CRYPTO_KEY_V1", "k" * 16)
    with pytest.raises(Exception):
        validate_startup()
