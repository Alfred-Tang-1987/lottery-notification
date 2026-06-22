from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.config import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动：校验 + 种子幂等写入；关闭：无特殊清理。"""
    settings = get_settings()
    validate_startup()
    from sqlmodel import Session
    from app.seeds import seed_lottery_types
    from app.db.session import get_engine
    with Session(get_engine()) as s:
        seed_lottery_types(s)
    yield


def validate_startup() -> None:
    """启动校验：应用时区 + OS 时区软警告 + email/bark 兜底。JWT/CRYPTO 已由 Settings 强制。"""
    import logging
    import time as _time
    settings = get_settings()
    log = logging.getLogger("app.startup")
    if settings.tz != "Asia/Shanghai":
        raise RuntimeError(f"应用时区必须 Asia/Shanghai，当前配置 {settings.tz}")
    tzname = _time.tzname[0] if _time.tzname else None
    if tzname not in ("CST",):
        log.warning("OS 时区为 %s（非 CST），应用已用 Asia/Shanghai 锁定调度时区", tzname)
    settings.validate_email_bark_fallback()


def get_db_for_health() -> Engine:
    from app.db.session import get_engine
    return get_engine()


app = FastAPI(title="兑奖了吗？API", version="0.1.0", lifespan=lifespan)


@app.get("/health")
def health(db: Engine = Depends(get_db_for_health)):
    db_ok = False
    try:
        with db.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False
    return {
        "status": "ok" if db_ok else "degraded",
        "tz": get_settings().tz,
        "db": "ok" if db_ok else "down",
    }
