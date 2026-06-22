from fastapi.testclient import TestClient
from app.main import app
import pytest


@pytest.fixture(autouse=True)
def mock_env(monkeypatch):
    """所有 health 测试强制注入环境变量，避免 lifespan 初始化 Settings 时缺少 JWT/CRYPTO。"""
    monkeypatch.setenv("JWT_SECRET", "x" * 32)
    monkeypatch.setenv("CRYPTO_KEY_V1", "k" * 32)


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
