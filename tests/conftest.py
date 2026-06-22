import pytest
from sqlalchemy import create_engine
from sqlmodel import SQLModel


@pytest.fixture
def db_engine(tmp_path):
    """每个测试一个临时 SQLite，建全表。"""
    from app.db.engine import build_engine, apply_sqlite_pragmas
    import app.models  # noqa: F401  注册全部表
    eng = build_engine(f"sqlite:///{tmp_path}/test.db")
    apply_sqlite_pragmas(eng)
    SQLModel.metadata.create_all(eng)
    return eng
