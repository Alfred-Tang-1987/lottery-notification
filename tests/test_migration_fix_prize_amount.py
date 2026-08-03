"""数据迁移 fix_prize_amount_cents 测试。

回归 2026-08-03：prize_tables 固定档金额曾以「元」录入被当「分」处理，
历史 comparisons.prize_amount 存的是「元值 × 倍投」而非「分值 × 倍投」，
所有固定档中奖金额显示缩小 100 倍。本迁移把元值纠正为分值（×100）。

测试覆盖：
  1. 元值行（含倍投）被 ×100 纠正
  2. 已是分值的行不被二次 ×100（幂等）
  3. 浮动档（一二等，prize_amount 经 refill 回填为分值）绝不被触碰
  4. downgrade 可逆
"""

import os
import subprocess
import sys
from pathlib import Path

from cryptography.fernet import Fernet
from sqlalchemy import create_engine, text

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _env_for(db_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    env['JWT_SECRET'] = 'x' * 32
    env['CRYPTO_KEY_V1'] = Fernet.generate_key().decode()
    env['DATABASE_URL'] = f'sqlite:///{db_path}'
    return env


def _alembic(db_path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, '-m', 'alembic', *args],
        cwd=PROJECT_ROOT,
        env=_env_for(db_path),
        capture_output=True,
        text=True,
    )


def _alembic_upgrade(db_path: Path, target: str = 'head') -> None:
    r = _alembic(db_path, 'upgrade', target)
    if r.returncode != 0:
        raise RuntimeError(f'alembic upgrade {target} 失败:\n{r.stdout}\n{r.stderr}')


def _seed_fixtures_all_yuan(db_path: Path) -> None:
    """生产真实场景：所有固定档行都是元值（bug 影响全部历史写入），无「本就分值」行。

    用于验证 upgrade/downgrade 完全对称可逆。
    """
    engine = create_engine(f'sqlite:///{db_path}')
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO users (id, username, password_hash, role, invite_code, enabled, created_at) "
            "VALUES (1, 'u', 'x', 'user', 'C1', 1, '2026-01-01 00:00:00')"
        ))
        for code, name, cat in (('ssq', '双色球', 'fucai'), ('dlt', '大乐透', 'ticai')):
            conn.execute(text(
                "INSERT INTO lottery_types (code, name, category, spec_json, draw_schedule_json, "
                "enabled, schema_version, created_at) "
                "VALUES (:c, :n, :cat, :spec, '[]', 1, 1, '2026-01-01 00:00:00')"
            ), {'c': code, 'n': name, 'cat': cat, 'spec': '{"price_per_bet":200}'})
        for did, code, dno in ((1, 'ssq', '062'), (2, 'dlt', '063')):
            conn.execute(text(
                "INSERT INTO draw_results (id, lottery_code, draw_no, draw_date, numbers_json, "
                "source, fetched_at, verified, single_source, version, created_at) "
                "VALUES (:id, :c, :n, '2026-07-25 00:00:00', '{}', 'mxnzp', "
                "'2026-07-25 00:00:00', 1, 0, 1, '2026-07-25 00:00:00')"
            ), {'id': did, 'c': code, 'n': dno})
        # tickets：倍投分别 1 / 3 / 1 / 1
        for tid, uid, code, mult in ((1, 1, 'ssq', 1), (2, 1, 'ssq', 3),
                                     (3, 1, 'dlt', 1), (4, 1, 'ssq', 1)):
            conn.execute(text(
                "INSERT INTO tickets (id, user_id, lottery_code, play_type, numbers_json, "
                "multiplier, append, cost, enabled, created_at) "
                "VALUES (:id, :uid, :c, 'single', '{}', :m, 0, 200, 1, '2026-01-01 00:00:00')"
            ), {'id': tid, 'uid': uid, 'c': code, 'm': mult})
        # 全元值行（生产真实状态）：ssq 六等5 / ssq 三等9000(×3) / dlt 三等10000 / ssq 一等浮动5000000
        rows = [
            (1, 1, 1, 6, 5, 1),          # ssq 六等 元值 5（元）
            (2, 1, 2, 3, 9000, 1),        # ssq 三等 元值 9000（=3000×3）
            (3, 2, 3, 3, 10000, 1),       # dlt 三等 元值 10000（元）
            (4, 1, 4, 1, 5000000, 1),     # ssq 一等 浮动档分值（refill 回填）
        ]
        for cid, drid, tid, tier, amt, win in rows:
            conn.execute(text(
                "INSERT INTO comparisons (id, user_id, draw_result_id, ticket_id, hits_json, "
                "prize_tier, prize_amount, is_win, unresolved, created_at) "
                "VALUES (:id, 1, :drid, :tid, '{}', :tier, :amt, :win, 0, '2026-07-25 00:00:00')"
            ), {'id': cid, 'drid': drid, 'tid': tid, 'tier': tier, 'amt': amt, 'win': win})
    engine.dispose()


def _amounts(db_path: Path) -> dict[int, int | None]:
    engine = create_engine(f'sqlite:///{db_path}')
    with engine.connect() as conn:
        rows = conn.execute(text('SELECT id, prize_amount FROM comparisons ORDER BY id')).all()
    engine.dispose()
    return {r[0]: r[1] for r in rows}


def test_migration_corrects_yuan_rows_and_leaves_float_untouched(tmp_path):
    """upgrade 纠正所有元值行（×100），浮动档不动。"""
    db_path = tmp_path / 'fix.db'
    _alembic_upgrade(db_path, 'p8_password_reset_codes')  # 停在数据迁移之前
    _seed_fixtures_all_yuan(db_path)
    _alembic_upgrade(db_path, 'head')  # 跑 fix_prize_amount_cents

    amounts = _amounts(db_path)
    assert amounts[1] == 500, f'ssq 六等元值 5 应纠正为 500，实得 {amounts[1]}'
    assert amounts[2] == 900000, f'ssq 三等元值 9000（×3倍）应纠正为 900000，实得 {amounts[2]}'
    assert amounts[3] == 1000000, f'dlt 三等元值 10000 应纠正为 1000000，实得 {amounts[3]}'
    assert amounts[4] == 5000000, f'ssq 一等浮动档不应被改，实得 {amounts[4]}'


def test_migration_upgrade_downgrade_roundtrip_is_symmetric(tmp_path):
    """生产全元值场景下，upgrade -> downgrade 完全对称可逆（无累积、无丢失）。

    bug 影响所有历史固定档行（一律元值），故 upgrade 全转分、downgrade 全转回元，
    数值精确还原。幂等性由 alembic 版本机制 + 精确判别共同保证。
    """
    db_path = tmp_path / 'roundtrip.db'
    _alembic_upgrade(db_path, 'p8_password_reset_codes')
    _seed_fixtures_all_yuan(db_path)
    original = _amounts(db_path)

    _alembic_upgrade(db_path, 'head')
    upgraded = _amounts(db_path)
    # 所有固定档行 ×100，浮动档不变
    assert upgraded[1] == 500 and upgraded[2] == 900000 and upgraded[3] == 1000000
    assert upgraded[4] == 5000000

    _alembic(db_path, 'downgrade', 'p8_password_reset_codes')
    reverted = _amounts(db_path)
    assert reverted == original, f'downgrade 后应还原 upgrade 前原值，原={original}, 还原={reverted}'


def test_migration_downgrade_is_best_effort_on_mixed_data(tmp_path):
    """混合数据下 downgrade 的已知限制（文档化行为）。

    若 DB 混有「本就分值」的固定档行（非 bug 产生），downgrade 无法将其与
    「被 upgrade 转过的行」区分，会一并 ÷100。故混合状态不应盲目 downgrade。
    本测试锁定该已知行为，防止未来误以为 downgrade 完美可逆。
    """
    db_path = tmp_path / 'mixed.db'
    _alembic_upgrade(db_path, 'p8_password_reset_codes')
    _seed_fixtures_all_yuan(db_path)
    # 额外插一行「本就分值」的 dlt 三等（multiplier=1，分值 1000000）
    engine = create_engine(f'sqlite:///{db_path}')
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO tickets (id, user_id, lottery_code, play_type, numbers_json, "
            "multiplier, append, cost, enabled, created_at) "
            "VALUES (5, 1, 'dlt', 'single', '{}', 1, 0, 200, 1, '2026-01-01 00:00:00')"
        ))
        conn.execute(text(
            "INSERT INTO comparisons (id, user_id, draw_result_id, ticket_id, hits_json, "
            "prize_tier, prize_amount, is_win, unresolved, created_at) "
            "VALUES (5, 1, 2, 5, '{}', 3, 1000000, 1, 0, '2026-07-25 00:00:00')"
        ))
    engine.dispose()

    _alembic_upgrade(db_path, 'head')
    # row 5 本就分值，upgrade 不改（精确判别：1000000*100 != 1000000*1）
    assert _amounts(db_path)[5] == 1000000

    _alembic(db_path, 'downgrade', 'p8_password_reset_codes')
    # downgrade 无法区分，row 5 被一并 ÷100 -> 已知限制
    assert _amounts(db_path)[5] == 10000, 'downgrade 已知限制：本就分值的行被一并 ÷100'
