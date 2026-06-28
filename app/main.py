from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.config import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动：校验 + 种子幂等写入；关闭：无特殊清理。"""
    validate_startup()
    from sqlmodel import Session

    from app.db.session import get_engine
    from app.seeds import seed_lottery_types

    with Session(get_engine()) as s:
        seed_lottery_types(s)
    yield


def validate_startup() -> None:
    """启动校验（spec §124）：
    - JWT_SECRET / CRYPTO_KEY：Settings field_validator 已强制；此处再实例化 CryptoService
      端到端证明密钥可用（构造 Fernet + 加解密冒烟），避免运行时首次加解密才崩。
    - 应用时区必须 Asia/Shanghai；OS 时区软警告。
    - email/bark 兜底：启用 email 时强制 ADMIN_BARK_KEY。
    """
    import logging
    import time as _time

    from app.infrastructure.crypto import CryptoService

    settings = get_settings()
    log = logging.getLogger('app.startup')
    if settings.tz != 'Asia/Shanghai':
        raise RuntimeError(f'应用时区必须 Asia/Shanghai，当前配置 {settings.tz}')
    tzname = _time.tzname[0] if _time.tzname else None
    if tzname not in ('CST',):
        log.warning('OS 时区为 %s（非 CST），应用已用 Asia/Shanghai 锁定调度时区', tzname)
    # 端到端证明 crypto key 可用：构造 CryptoService 并做一次加解密冒烟。
    # 即使未来 Settings 校验被绕过，此处仍会捕获无效密钥，fail-fast 而非运行时崩。
    crypto = CryptoService(settings.crypto_keys, settings.current_key_version)
    _smoke = crypto.encrypt('__startup_probe__', version=settings.current_key_version)
    crypto.decrypt(_smoke)
    settings.validate_email_bark_fallback()


def get_db_for_health() -> Engine:
    from app.db.session import get_engine

    return get_engine()


app = FastAPI(title='兑奖了吗？API', version='0.1.0', lifespan=lifespan)


@app.get('/health')
def health(db: Engine = Depends(get_db_for_health)):
    try:
        with db.connect() as conn:
            conn.execute(text('SELECT 1'))
        db_ok = True
    except Exception:
        db_ok = False
    return {
        'status': 'ok' if db_ok else 'degraded',
        'tz': get_settings().tz,
        'db': 'ok' if db_ok else 'down',
    }
