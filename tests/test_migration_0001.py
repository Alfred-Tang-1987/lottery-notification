"""T4b fix: Alembic 迁移 0001_initial 与模型 schema 一致性。

回归：模型 app/models/draw.py 已含 DrawCorrection.corrected_at（spec §6.2），
但 alembic/versions/0001_initial.py 的 create_table('draw_corrections') 缺该列，
导致 `alembic check` 检测到 schema drift：'Detected added column
draw_corrections.corrected_at'。部署后 DB 实际缺列，官方更正写表时无法记录更正时间。

本测试用 **alembic upgrade head**（而非 SQLModel.metadata.create_all）建库——
前者走迁移 DDL（生产路径），后者走模型 metadata（测试 conftest 路径）。
只有前者能暴露迁移 DDL 与模型定义的偏差。
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import inspect

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _env_for(db_path: Path) -> dict[str, str]:
    """构造运行 alembic 所需的最小环境变量（Settings 必填项）。"""
    env = os.environ.copy()
    env["JWT_SECRET"] = "x" * 32
    env["CRYPTO_KEY_V1"] = Fernet.generate_key().decode()
    env["DATABASE_URL"] = f"sqlite:///{db_path}"
    return env


def _alembic_upgrade(db_path: Path) -> None:
    """运行 alembic upgrade head，失败即抛出含 stderr 的 RuntimeError。"""
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=PROJECT_ROOT,
        env=_env_for(db_path),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"alembic upgrade head 失败 (rc={result.returncode}):\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )


@pytest.fixture
def migrated_engine(tmp_path):
    """通过 alembic upgrade head 在临时 SQLite 建库，返回连到该库的 engine。"""
    from sqlalchemy import create_engine

    db_path = tmp_path / "migrated.db"
    _alembic_upgrade(db_path)
    # db_path 由 alembic 创建；engine 直接连
    engine = create_engine(f"sqlite:///{db_path}")
    yield engine
    engine.dispose()


def test_migration_creates_draw_corrections_corrected_at(migrated_engine):
    """spec §6.2: draw_corrections.corrected_at 必须由迁移 DDL 创建。

    回归 fix：0001_initial.py 的 create_table('draw_corrections') 曾缺该列，
    `alembic check` 报 'Detected added column draw_corrections.corrected_at'。
    """
    cols = {c["name"] for c in inspect(migrated_engine).get_columns("draw_corrections")}
    assert "corrected_at" in cols, (
        f"迁移 0001 未创建 draw_corrections.corrected_at 列；实际列: {sorted(cols)}"
    )


def test_no_alembic_check_drift(tmp_path):
    """`alembic check` 在全新 upgrade-head 库上应无 drift（exit 0）。

    端到端回归：确保迁移 DDL 与 SQLModel.metadata 完全一致，无任何残留 drift。
    """
    db_path = tmp_path / "drift_check.db"
    _alembic_upgrade(db_path)

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "check"],
        cwd=PROJECT_ROOT,
        env=_env_for(db_path),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"alembic check 检测到 schema drift (rc={result.returncode}):\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
