import logging
from unittest.mock import MagicMock

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.config import reset_settings_cache
from app.main import app, get_db_for_health, validate_startup


@pytest.fixture(autouse=True)
def mock_env(monkeypatch):
    """所有 health 测试强制注入有效环境变量（真实 Fernet key），避免 lifespan 校验失败。

    禁用 scheduler（SCHEDULER_ENABLED=false）：health 测试只验证 /health 端点，不应触发
    lifespan 内的 run_startup_backfill（会真实抓取 MXNZP/聚合数据源，污染测试 + 可能挂起）。
    """
    reset_settings_cache()
    monkeypatch.setenv('JWT_SECRET', 'x' * 32)
    monkeypatch.setenv('CRYPTO_KEY_V1', Fernet.generate_key().decode())
    monkeypatch.setenv('SCHEDULER_ENABLED', 'false')


def test_health_ok(db_engine):
    # 注入隔离的测试 engine（与 test_health_includes_db_check 一致）。
    # 避免连默认 ./data/lottery.db——该目录被 .gitignore 排除，干净 checkout 上不存在，
    # 会导致 health 探活失败返回 degraded（测试不应依赖工作目录的文件系统状态）。
    app.dependency_overrides[get_db_for_health] = lambda: db_engine
    try:
        client = TestClient(app)
        r = client.get('/health')
        assert r.status_code == 200
        body = r.json()
        assert body['status'] == 'ok'
        assert 'tz' in body
        assert body['tz'] == 'Asia/Shanghai'
    finally:
        app.dependency_overrides.clear()


def test_health_includes_db_check(db_engine):
    # 注入测试 engine（try/finally 保证清理，避免污染后续测试）
    app.dependency_overrides[get_db_for_health] = lambda: db_engine
    try:
        client = TestClient(app)
        r = client.get('/health')
        assert r.status_code == 200
        assert r.json()['db'] == 'ok'
    finally:
        app.dependency_overrides.clear()


# [HIGH] validate_startup must prove the crypto key is usable (construct CryptoService)
def test_validate_startup_proves_crypto_key(monkeypatch):
    """Settings field_validator already rejects bad keys, but validate_startup must
    additionally instantiate CryptoService to prove the key works end-to-end before
    serving traffic (spec §124 mandates CRYPTO_KEY startup validation)."""
    reset_settings_cache()
    monkeypatch.setenv('JWT_SECRET', 'x' * 32)
    monkeypatch.setenv('CRYPTO_KEY_V1', Fernet.generate_key().decode())
    validate_startup()  # must not raise


def test_validate_startup_catches_invalid_crypto_key(monkeypatch):
    """If an invalid key somehow bypasses Settings (e.g. future refactor),
    validate_startup must still catch it rather than crashing on first encrypt."""
    reset_settings_cache()
    monkeypatch.setenv('JWT_SECRET', 'x' * 32)
    # Bypass Settings validator by constructing with a raw invalid key is not possible
    # since Settings now validates; instead verify validate_startup surfaces a clear
    # error when Settings itself rejects the key.
    monkeypatch.setenv('CRYPTO_KEY_V1', 'k' * 16)
    with pytest.raises(Exception):
        validate_startup()


def test_health_logs_when_db_down(caplog):
    """hunter：/health db 故障不得静默吞——须 logger.warning 让运维在日志察觉（否则只在
    HTTP 响应变 degraded，服务端无迹可寻，延误故障发现）。"""
    bad = MagicMock()
    bad.connect.side_effect = OSError('connection refused')
    app.dependency_overrides[get_db_for_health] = lambda: bad
    try:
        with caplog.at_level(logging.WARNING, logger='app.main'):
            r = TestClient(app).get('/health')
        assert r.status_code == 200
        assert r.json()['db'] == 'down'
        assert any(rec.levelno >= logging.WARNING for rec in caplog.records), (
            '/health db 故障应 logger.warning，不该静默吞'
        )
    finally:
        app.dependency_overrides.clear()
