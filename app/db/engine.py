from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine


def build_engine(url: str) -> Engine:
    """单写连接（pool_size=1, max_overflow=0）+ SQLite + PRAGMA（spec §4.3）。
    写串行化防 database is locked；PRAGMA 在 engine 创建时一次性注册 connect 事件。"""
    eng = create_engine(
        url,
        connect_args={'check_same_thread': False},
        pool_size=1,
        max_overflow=0,  # 强制单连接复用，写串行化
    )

    @event.listens_for(eng, 'connect')
    def _set_pragmas(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute('PRAGMA journal_mode=WAL')
        cur.execute('PRAGMA synchronous=NORMAL')
        cur.execute('PRAGMA busy_timeout=5000')
        cur.close()

    return eng


def apply_sqlite_pragmas(eng: Engine) -> None:
    """兼容入口：PRAGMA 已在 build_engine 内注册。保留供测试/旧调用，内部 no-op
    （避免对同一 engine 重复注册 connect 事件）。"""
    return None
