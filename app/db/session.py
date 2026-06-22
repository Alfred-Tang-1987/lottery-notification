"""全局 engine 单例（应用启动时构建）。Plan 后续 main.py 注入。"""
from app.config import settings
from app.db.engine import build_engine, apply_sqlite_pragmas

engine = build_engine(settings.database_url)
apply_sqlite_pragmas(engine)
