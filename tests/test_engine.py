from sqlalchemy import text

from app.db.engine import apply_sqlite_pragmas, build_engine


def test_apply_pragmas_sets_wal(tmp_path):
    db = tmp_path / 't.db'
    eng = build_engine(f'sqlite:///{db}')
    apply_sqlite_pragmas(eng)
    with eng.connect() as conn:
        jm = conn.execute(text('PRAGMA journal_mode')).scalar()
        sync = conn.execute(text('PRAGMA synchronous')).scalar()
        bt = conn.execute(text('PRAGMA busy_timeout')).scalar()
    assert jm.lower() == 'wal'
    assert sync == 1  # NORMAL
    assert bt == 5000


def test_write_pool_is_single(tmp_path):
    eng = build_engine(f'sqlite:///{tmp_path / "t.db"}')
    # SQLite 方言下 NullPool + 单连接；校验 pool 不大于 1
    assert eng.pool.size() <= 1
