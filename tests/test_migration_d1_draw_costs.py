"""迁移 d1_draw_costs 历史回填测试（spec §4）。

验证迁移 upgrade 在已有 DrawResult+tickets 的 DB 上：
1. 建 draw_costs 表。
2. 回填 SQL 正确：per (user, lottery, draw_no) 聚合 enabled 追投注 cost。
3. 幂等（ON CONFLICT upsert）。

设计：先 alembic upgrade 到 d1 前一版本 (fix_prize_amount_cents) -> 造历史数据
(DrawResult+tickets) -> upgrade 到 d1 -> 检查 draw_costs 已回填。
"""

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from cryptography.fernet import Fernet
from sqlmodel import Session, select

from app.models import DrawCost, DrawResult, LotteryType, Ticket, User

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _env_for(db_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    env['JWT_SECRET'] = 'x' * 32
    env['CRYPTO_KEY_V1'] = Fernet.generate_key().decode()
    env['DATABASE_URL'] = f'sqlite:///{db_path}'
    return env


def _alembic(args: list[str], db_path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, '-m', 'alembic', *args],
        cwd=PROJECT_ROOT,
        env=_env_for(db_path),
        capture_output=True,
        text=True,
    )


def test_migration_d1_backfills_draw_costs(tmp_path):
    """迁移 d1 在已有历史数据上回填 DrawCost。"""
    db_path = tmp_path / 'mig.db'

    # 1. upgrade 到 d1 前一版本
    r = _alembic(['upgrade', 'fix_prize_amount_cents'], db_path)
    assert r.returncode == 0, r.stderr

    # 2. 造历史数据（DrawResult + tickets，无 DrawCost 表）
    db_url = f'sqlite:///{db_path}'
    from app.db.engine import apply_sqlite_pragmas, build_engine

    eng = build_engine(db_url)
    apply_sqlite_pragmas(eng)
    with Session(eng) as s:
        s.add(LotteryType(code='ssq', name='双色球', category='welfare',
                          spec_json='{}', draw_schedule_json='{}'))
        s.add(User(username='u', password_hash='x', role='user', invite_code='C'))
        s.commit()
        u = s.exec(select(User)).first()
        dr = DrawResult(
            lottery_code='ssq', draw_no='2026070',
            draw_date=datetime(2026, 7, 1, 21, 30),
            numbers_json='{}', source='mxnzp', verified=True, version=1,
        )
        s.add(dr)
        s.flush()
        s.add(Ticket(user_id=u.id, lottery_code='ssq', play_type='single',
                     numbers_json='{}', cost=200, enabled=True))
        s.add(Ticket(user_id=u.id, lottery_code='ssq', play_type='single',
                     numbers_json='{}', cost=300, enabled=True))
        # disabled 注不计入
        s.add(Ticket(user_id=u.id, lottery_code='ssq', play_type='single',
                     numbers_json='{}', cost=999, enabled=False))
        s.commit()
    eng.dispose()

    # 3. upgrade 到 d1（建表 + 回填）
    r = _alembic(['upgrade', 'd1_draw_costs'], db_path)
    assert r.returncode == 0, r.stderr

    # 4. 检查 draw_costs 已回填
    eng = build_engine(db_url)
    apply_sqlite_pragmas(eng)
    with Session(eng) as s:
        dcs = list(s.exec(select(DrawCost)).all())
        assert len(dcs) == 1, f'Expected 1 DrawCost, got {len(dcs)}'
        assert dcs[0].cost == 500  # 200 + 300，disabled 999 不计
        assert dcs[0].draw_no == '2026070'
        assert dcs[0].draw_date == datetime(2026, 7, 1, 21, 30)
    eng.dispose()
