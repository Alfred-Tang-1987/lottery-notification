"""t11_source_health_alert 迁移测试：api_source_health +down_since/alerted。"""

import os
import subprocess
import sys
from pathlib import Path

from cryptography.fernet import Fernet
from sqlalchemy import create_engine, inspect, text

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _env_for(db_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    env['JWT_SECRET'] = 'x' * 32
    env['CRYPTO_KEY_V1'] = Fernet.generate_key().decode()
    env['DATABASE_URL'] = f'sqlite:///{db_path}'
    return env


def _alembic_upgrade(db_path: Path, target: str = 'head') -> None:
    r = subprocess.run(
        [sys.executable, '-m', 'alembic', 'upgrade', target],
        cwd=PROJECT_ROOT, env=_env_for(db_path), capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(f'alembic upgrade {target} 失败:\n{r.stdout}\n{r.stderr}')


def test_migration_adds_alert_columns(tmp_path):
    db = tmp_path / 'mig.db'
    _alembic_upgrade(db)
    cols = {c['name']: c for c in inspect(create_engine(f'sqlite:///{db}'))
            .get_columns('api_source_health')}
    assert 'down_since' in cols and cols['down_since']['nullable']
    assert 'alerted' in cols
    # eng-voice H2：inspect().get_columns() 返回的是 DDL 带引号字面量 "'none'"，
    # 不是 'none'——`== 'none'` 与 `is None` 两个分支都永假（SQLite 实测）。
    # 断言 quoted 子串 + NOT NULL + 缺省插入回读（真正防回归的三件套）。
    assert cols['alerted']['nullable'] is False
    assert "'none'" in str(cols['alerted'].get('default') or '')
    eng = create_engine(f'sqlite:///{db}')
    with eng.begin() as c:
        # 裸 SQL 必须显式给 created_at / status：二者 nullable=False 且**无 server_default**
        # （0001_initial.py:28-35 建表、_base.py:6 的 default_factory 只走 ORM）。
        # 只插 source 会撞 IntegrityError: NOT NULL constraint failed（实测）。
        # alerted 故意不写——本用例要验的正是它的 server_default='none'。
        c.execute(text(
            "INSERT INTO api_source_health (source, created_at, status) "
            "VALUES ('mxnzp', '2026-09-15 04:00:00.000000', 'unknown')"
        ))
        row = c.execute(text("SELECT alerted FROM api_source_health WHERE source='mxnzp'")).one()
    assert row[0] == 'none'


def test_alembic_single_head():
    """eng-voice C1 守卫：迁移链必须单 head——双 head 时 Dockerfile:79 的

    `alembic upgrade head` 报 'Multiple head revisions' 直接起不来容器。
    本 plan 原稿 down_revision 写成 fix_prize_amount_cents（真实 head 是
    d1_draw_costs）就会产生双 head；此测试让该类错误 RED 在 CI 而非 NAS。
    """
    r = subprocess.run(
        [sys.executable, '-m', 'alembic', 'heads'],
        cwd=PROJECT_ROOT, env=_env_for(Path(PROJECT_ROOT) / 'unused.db'),
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    heads = [ln for ln in r.stdout.splitlines() if ln.strip()]
    assert len(heads) == 1, f'存在多个 alembic head: {heads}'
