import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动：校验 + 种子幂等写入 + 接线 scheduler（spec §4.3/§7.3）；关闭：停调度器。

    scheduler 由 settings.scheduler_enabled 控制：生产默认开启；测试/排障可关。
    启动时执行 run_startup_backfill（补 outbox + 宕机遗漏抓取，spec §7.3）。
    """
    settings = get_settings()
    validate_startup()
    from sqlmodel import Session

    from app.db.session import get_engine
    from app.seeds import seed_lottery_types

    engine = get_engine()
    with Session(engine) as s:
        seed_lottery_types(s)

    if settings.scheduler_enabled:
        from app.scheduler.backfill import run_startup_backfill

        sched, deps = _build_scheduler_and_deps(engine, settings)
        # 启动 backfill：补未处理 outbox + 宕机窗口遗漏抓取（spec §7.3）。
        # 单彩种/单期故障已被 run_startup_backfill 内部 try/except 隔离，不会阻断启动。
        try:
            run_startup_backfill(deps)
        except Exception:
            logger.error('startup_backfill_failed', exc_info=True)
        sched.start()
        app.state.scheduler = sched
        app.state.notifier = deps['notifier']
    yield
    # 关闭：先停调度器（不再派发新 job），再释放渠道资源（httpx 连接池）。
    sched = getattr(app.state, 'scheduler', None)
    if sched is not None:
        sched.shutdown(wait=False)
        app.state.scheduler = None
    notifier = getattr(app.state, 'notifier', None)
    if notifier is not None:
        notifier.close()
        app.state.notifier = None


def _amount_lookup_stub(lottery_code: str, draw_no: str, tier: int) -> int | None:
    """官方浮动奖金查询占位（spec §7.1 浮动奖回填）。

    MVP 返回 None：FloatRefillWorker 查不到金额即不回填、不补推（待 Plan 05/06 接真实
    奖金接口）。真实实现接 MXNZP/聚合奖金接口后替换此函数。
    """
    return None


def _build_scheduler_and_deps(engine: Engine, settings: Settings):
    """构造 services + channels + notifier + scheduler + 注册全部任务（spec §4.3/§7.3）。

    返回 (sched, deps)。纯构造 + 注册：不抓取、不 backfill、不 start，无网络副作用——
    可单测断言服务类型与任务 id。run_startup_backfill / sched.start 由 lifespan 调用。

    register_all_jobs 经进程内注册表按 engine 解析 services，job args 只携带可 pickle 的
    engine——否则 SQLAlchemyJobStore 在 sched.start() 持久化时会 PicklingError
    （services 持 httpx.Client/engine 不可 pickle）→ 调度器无法启动 → 中奖静默漏通知。
    """
    from apscheduler.schedulers.background import BackgroundScheduler

    from app.adapters.juhe import JuheAdapter
    from app.adapters.mxnzp import MxnzpAdapter
    from app.infrastructure.crypto import CryptoService
    from app.notifications.bark import BarkChannel
    from app.notifications.email_channel import EmailChannel
    from app.notifications.feishu import FeishuChannel
    from app.notifications.notifier import Notifier
    from app.scheduler.jobs import register_all_jobs
    from app.scheduler.setup import build_scheduler
    from app.services.compare_service import CompareService
    from app.services.fetch_service import FetchService
    from app.services.refill_service import FloatRefillWorker

    crypto = CryptoService(settings.crypto_keys, settings.current_key_version)

    channels = {
        'bark': BarkChannel(),
        'feishu': FeishuChannel(),
    }
    # 邮箱渠道仅当运维方已配置 SMTP 时启用（spec §8.1 系统统一发件）。
    if settings.email_enabled:
        channels['email'] = EmailChannel(
            smtp_host=settings.smtp_host,
            smtp_port=settings.smtp_port,
            smtp_user=settings.smtp_user,
            smtp_pass=settings.smtp_pass,
            smtp_from=settings.smtp_from,
            smtp_encryption=settings.smtp_encryption,
        )
    # admin Bark fallback：启用 email 时 Settings.validate_email_bark_fallback 已强制
    # ADMIN_BARK_KEY 存在；未配 email 时 admin_bark_key 可选。
    admin_bark_config = None
    if settings.admin_bark_key:
        admin_bark_config = {'key': settings.admin_bark_key, 'url': 'https://api.day.app'}

    notifier = Notifier(
        engine,
        channels=channels,
        crypto=crypto,
        admin_bark_config=admin_bark_config,
    )

    fetch = FetchService(
        MxnzpAdapter(settings.mxnzp_api_key),
        JuheAdapter(settings.juhe_api_key),
        engine,
    )
    compare = CompareService(engine)
    refill = FloatRefillWorker(engine, amount_lookup=_amount_lookup_stub)
    deps = {
        'engine': engine,
        'fetch_service': fetch,
        'compare_service': compare,
        'refill_worker': refill,
        'notifier': notifier,
    }
    sched: BackgroundScheduler = build_scheduler(engine)
    register_all_jobs(sched, deps)
    return sched, deps


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

# CORS（spec §4.3）：allow_credentials=True + 显式 origins（禁用通配符）。
# 生产同源托管 SPA；开发期 Vite 5173 → FastAPI 8000 需显式白名单。origins 由
# get_cors_origins() 读 CORS_ORIGINS env（不触发完整 Settings 构造——后者要求
# JWT_SECRET/CRYPTO_KEY，缺密钥的测试 collection 期 import app.main 会崩）。中间件与
# login Origin 校验共用此函数为单一真源（防两处读不同源致生产登录被 403 误拒）。
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

# CORS origins 单一真源在 app.config.get_cors_origins（中间件与 login Origin 校验共用，
# 防两处读不同源导致生产登录被 403 误拒）。H1 warning 回退逻辑也在该函数内。
from app.config import get_cors_origins  # noqa: E402

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_cors_origins(),
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)

# auth 路由（Plan 05 / T4）
from app.api.auth import router as auth_router  # noqa: E402

app.include_router(auth_router)

# 渠道配置 API（Plan 05 / T5）：加密写入/读取，对齐 Notifier._decrypt_config。
from app.api.channels import router as channels_router  # noqa: E402

app.include_router(channels_router)

# admin 后台 API（Plan 05 / T6）
from app.api.admin import router as admin_router  # noqa: E402

app.include_router(admin_router)

# T6f admin 后台扩展 API（Plan 06 / T6f）
from app.api.admin_ext import router as admin_ext_router  # noqa: E402

app.include_router(admin_ext_router)

# 号码池 CRUD API（Plan 05 / T7）：复用 Plan 03 TicketRepo（IDOR-safe via user_id 注入）。
from app.api.tickets import router as tickets_router  # noqa: E402

app.include_router(tickets_router)

# 兑奖领取 API（Plan 05 / T7）：PrizeClaim pending→claimed，IDOR 经 comparison→user。
from app.api.claims import router as claims_router  # noqa: E402

app.include_router(claims_router)

# Dashboard 聚合 API（Plan 06 / T6）：首屏数据快照。
from app.api.dashboard import router as dashboard_router  # noqa: E402

app.include_router(dashboard_router)

# 开奖历史 API（Plan 06 / T6）：开奖查询 / 走势。
from app.api.draws import router as draws_router  # noqa: E402

app.include_router(draws_router)

# 比对记录 API（Plan 06 / T6）：中奖记录。
from app.api.comparisons import router as comparisons_router  # noqa: E402

app.include_router(comparisons_router)


@app.get('/health')
def health(db: Engine = Depends(get_db_for_health)):
    try:
        with db.connect() as conn:
            conn.execute(text('SELECT 1'))
        db_ok = True
    except Exception as exc:
        # hunter：db 故障不得静默——只在 HTTP 响应变 degraded 会让运维无迹可寻，延误发现。
        logger.warning('/health db 探活失败: %s', exc)
        db_ok = False
    return {
        'status': 'ok' if db_ok else 'degraded',
        'tz': get_settings().tz,
        'db': 'ok' if db_ok else 'down',
    }
