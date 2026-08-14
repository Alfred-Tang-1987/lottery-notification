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
        assert r.json()['db'] == 'down'
        assert any(rec.levelno >= logging.WARNING for rec in caplog.records), (
            '/health db 故障应 logger.warning，不该静默吞'
        )
    finally:
        app.dependency_overrides.clear()


def test_health_returns_503_when_db_down():
    """[critical review-fix]：DB down 必须返回 HTTP 503，不能 200 + status=degraded。

    容器编排层（Docker HEALTHCHECK / k8s liveness）依赖 HTTP 状态码判断健康——
    返回 200 会让 Docker 把不健康容器标为 healthy，DB 长期故障时漏抓开奖/比对/推送，
    违反「中奖永不静默漏通知」核心纪律。
    """
    bad = MagicMock()
    bad.connect.side_effect = OSError('connection refused')
    app.dependency_overrides[get_db_for_health] = lambda: bad
    try:
        r = TestClient(app).get('/health')
        assert r.status_code == 503, (
            'DB down 必须返回 503（degraded 是运行时状态，但编排层依赖 HTTP 状态码）'
        )
        assert r.json()['db'] == 'down'
    finally:
        app.dependency_overrides.clear()


# —— Plan 09 / T8：数据源 key 缺失告警（spec D4）——


def _set_source_keys(monkeypatch, *, mxnzp_id='', mxnzp_secret='', juhe=''):
    reset_settings_cache()
    monkeypatch.setenv('JWT_SECRET', 'x' * 32)
    monkeypatch.setenv('CRYPTO_KEY_V1', Fernet.generate_key().decode())
    monkeypatch.setenv('MXNZP_API_KEY', mxnzp_id)
    monkeypatch.setenv('MXNZP_APP_SECRET', mxnzp_secret)
    monkeypatch.setenv('JUHE_API_KEY', juhe)


def test_health_data_sources_missing(monkeypatch, db_engine):
    """key 全空 → data_sources=missing，但 HTTP 仍 200（缺 key ≠ 容器不健康，
    否则首次安装未配 key 就被 HEALTHCHECK 重启循环）。"""
    _set_source_keys(monkeypatch)
    app.dependency_overrides[get_db_for_health] = lambda: db_engine
    try:
        r = TestClient(app).get('/health')
        assert r.status_code == 200
        assert r.json()['data_sources'] == 'missing'
    finally:
        app.dependency_overrides.clear()


def test_health_data_sources_single_source(monkeypatch, db_engine):
    _set_source_keys(monkeypatch, mxnzp_id='id', mxnzp_secret='secret')
    app.dependency_overrides[get_db_for_health] = lambda: db_engine
    try:
        r = TestClient(app).get('/health')
        assert r.json()['data_sources'] == 'single_source'
    finally:
        app.dependency_overrides.clear()


def test_health_data_sources_dual(monkeypatch, db_engine):
    _set_source_keys(monkeypatch, mxnzp_id='id', mxnzp_secret='secret', juhe='key')
    app.dependency_overrides[get_db_for_health] = lambda: db_engine
    try:
        r = TestClient(app).get('/health')
        assert r.json()['data_sources'] == 'dual'
    finally:
        app.dependency_overrides.clear()


def test_validate_startup_warns_when_all_source_keys_empty(monkeypatch, caplog):
    """silent-failure 纪律：数据源 key 全空必须 WARNING 显眼告警——否则 dashboard
    永远空、用户无从得知（D4）。"""
    _set_source_keys(monkeypatch)
    with caplog.at_level(logging.WARNING, logger='app.startup'):
        validate_startup()
    assert any(
        '数据源' in rec.message and rec.levelno >= logging.WARNING
        for rec in caplog.records
    ), 'key 全空应 WARNING 告警'
