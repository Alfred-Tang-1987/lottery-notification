"""全局 engine 单例（惰性初始化）。"""
from app.db.engine import build_engine, apply_sqlite_pragmas

_engine = None  # type: ignore


def get_engine():
    """惰性初始化 engine：首次调用时从 get_settings() 读取配置并构建。"""
    global _engine
    if _engine is None:
        from app.config import get_settings
        _engine = build_engine(get_settings().database_url)
        apply_sqlite_pragmas(_engine)
    return _engine
