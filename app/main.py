import time
from fastapi import FastAPI, Depends
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.config import get_settings

settings = get_settings()


def validate_startup() -> None:
    """启动校验：应用时区 + OS 时区软警告 + email/bark 兜底。JWT/CRYPTO 已由 Settings 强制。"""
    import logging
    import time as _time
    log = logging.getLogger("app.startup")
    if settings.tz != "Asia/Shanghai":
        raise RuntimeError(f"应用时区必须 Asia/Shanghai，当前配置 {settings.tz}")
    # OS 时区软校验：容器内可能 UTC，但应用所有调度用 APScheduler 的 Asia/Shanghai
    # 参数锁死，OS tz 偏移不致开奖时间错乱。仅记警告供运维排查。
    tzname = _time.tzname[0] if _time.tzname else None
    if tzname not in ("CST",):
        log.warning("OS 时区为 %s（非 CST），应用已用 Asia/Shanghai 锁定调度时区", tzname)
    settings.validate_email_bark_fallback()


def get_db_for_health() -> Engine:
    from app.db.session import engine
    return engine


app = FastAPI(title="兑奖了吗？API", version="0.1.0")


@app.on_event("startup")
def _startup() -> None:
    validate_startup()
    # 种子幂等写入
    from sqlmodel import Session
    from app.seeds import seed_lottery_types
    from app.db.session import engine
    with Session(engine) as s:
        seed_lottery_types(s)


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
        "tz": settings.tz,
        "db": "ok" if db_ok else "down",
    }
