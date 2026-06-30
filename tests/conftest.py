import pytest
from sqlmodel import SQLModel


@pytest.fixture(autouse=True)
def _cookie_secure_off(monkeypatch):
    """全局：测试环境关 cookie secure（TestClient 走 http，secure=True 会导致 cookie 不回传）。

    生产默认 cookie_secure=True（config.py），测试统一经此 fixture 降到 false。
    """
    monkeypatch.setenv('COOKIE_SECURE', 'false')


@pytest.fixture
def db_engine(tmp_path):
    """每个测试一个临时 SQLite，建全表。"""
    import app.models  # noqa: F401  注册全部表
    from app.db.engine import apply_sqlite_pragmas, build_engine

    eng = build_engine(f'sqlite:///{tmp_path}/test.db')
    apply_sqlite_pragmas(eng)
    SQLModel.metadata.create_all(eng)
    return eng
