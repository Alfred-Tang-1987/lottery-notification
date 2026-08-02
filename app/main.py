import logging
from contextlib import asynccontextmanager
from pathlib import Path

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
    from app.infrastructure.crypto import CryptoService
    from app.seeds import seed_lottery_types

    engine = get_engine()
    with Session(engine) as s:
        seed_lottery_types(s)

    # app.state 兜底：scheduler 关闭时（测试/开发）channels 为 {}，crypto 仍可用。
    # 端点在 scheduler 开启分支内会用 deps 里的真实 channels 覆盖。
    app.state.crypto = CryptoService(settings.crypto_keys, settings.current_key_version)
    app.state.channels = {}

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
        app.state.channels = deps['channels']  # 覆盖兜底：真实渠道注入（crypto 已在 if 外构造）
        app.state._deps = deps  # 供 lifespan teardown close 奖金查询适配器 client
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
    # close 奖金查询适配器 client（httpx 连接池），防泄漏。单 adapter 故障不阻断其余 close。
    deps = getattr(app.state, '_deps', None)
    if deps:
        for key in ('cwl_prize', 'sporttery_prize'):
            adapter = deps.get(key)
            if adapter is not None and hasattr(adapter, 'close'):
                try:
                    adapter.close()
                except Exception:
                    logger.warning('adapter_close_failed key=%s', key, exc_info=True)
        app.state._deps = None


def _build_amount_lookup(cwl, sporttery):
    """构建路由闭包：按彩种分发到对应 PrizeSource（spec §7.1 浮动奖回填）。

    ssq/qlc → cwl（中彩网）；dlt/qxc → sporttery（中国体彩网）；
    其他（fc3d/pl3/pl5 固定档）→ None（不查询）。

    闭包内嵌 code→adapter 集合：固定档直接返回 None，避免被错误地转发到任一 adapter
    产生 silent-success（L-20260706T010500Z：filter 必须真能区分行为）。
    """
    _CWL_CODES = frozenset({'ssq', 'qlc'})
    _SPORTTERY_CODES = frozenset({'dlt', 'qxc'})

    def amount_lookup(lottery_code: str, draw_no: str, draw_date, tier: int) -> int | None:
        if lottery_code in _CWL_CODES:
            return cwl.lookup_amount(lottery_code, draw_no, draw_date, tier)
        if lottery_code in _SPORTTERY_CODES:
            return sporttery.lookup_amount(lottery_code, draw_no, draw_date, tier)
        return None  # 固定档彩种不查询

    return amount_lookup


def _build_scheduler_and_deps(engine: Engine, settings: Settings):
    """构造 services + channels + notifier + scheduler + 注册全部任务（spec §4.3/§7.3）。

    返回 (sched, deps)。纯构造 + 注册：不抓取、不 backfill、不 start，无网络副作用——
    可单测断言服务类型与任务 id。run_startup_backfill / sched.start 由 lifespan 调用。

    register_all_jobs 经进程内注册表按 engine 解析 services，job args 只携带可 pickle 的
    engine——否则 SQLAlchemyJobStore 在 sched.start() 持久化时会 PicklingError
    （services 持 httpx.Client/engine 不可 pickle）→ 调度器无法启动 → 中奖静默漏通知。
    """
    from apscheduler.schedulers.background import BackgroundScheduler

    from app.adapters.cwl_prize import CwlPrizeSource
    from app.adapters.juhe import JuheAdapter
    from app.adapters.mxnzp import MxnzpAdapter
    from app.adapters.sporttery_prize import SportteryPrizeSource
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
        MxnzpAdapter(settings.mxnzp_api_key, settings.mxnzp_app_secret),
        JuheAdapter(settings.juhe_api_key),
        engine,
    )
    compare = CompareService(engine)
    # 奖金查询适配器（各建独立 httpx.Client，D1 决策）——供 lifespan teardown close。
    cwl = CwlPrizeSource()
    sporttery = SportteryPrizeSource()
    amount_lookup = _build_amount_lookup(cwl, sporttery)
    refill = FloatRefillWorker(engine, amount_lookup=amount_lookup)
    deps = {
        'engine': engine,
        'fetch_service': fetch,
        'compare_service': compare,
        'refill_worker': refill,
        'notifier': notifier,
        'cwl_prize': cwl,
        'sporttery_prize': sporttery,
        'channels': channels,
        'crypto': crypto,
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
    # 奖金查询 API 字段名冒烟验证（OV2/OV8）：启动时确认 API 可用且字段名匹配。
    # 不匹配则 log error 但不阻止启动——PDF 降级可能仍可用（spec §10/§11）。
    _smoke_check_prize_sources(settings, log)


def _smoke_check_prize_sources(settings: Settings, log: logging.Logger) -> None:
    """启动冒烟：验证 cwl + sporttery API 字段名匹配。

    非启动门禁——网络故障/字段漂移只 log error，不 raise。原因：
    1. PDF 降级（sporttery）可能仍可用；
    2. 启动期网络抖动不应阻塞整个服务拉起（spec §10 容错优先于立即失败）。
    字段名缺失 → log error 而非 info：让运维在日志察觉 schema 漂移（silent-failure
    纪律：schema 漂移若静默，下游 lookup 永远返回 None 被当「未公布」，奖金永久 null）。
    """
    import httpx

    # cwl 冒烟：ssq 任取一期，验证响应含 result[*].prizegrades（T2 lookup_amount 依赖）。
    try:
        r = httpx.get(
            'https://www.cwl.gov.cn/cwl_admin/front/cwlkj/search/kjxx/findDrawNotice',
            params={'name': 'ssq', 'code': '2026082'},
            headers={'User-Agent': 'Mozilla/5.0'},
            timeout=5.0,
        )
        # L-20260706T010500Z（review round 1 [minor]）：raise_for_status 让 5xx/4xx 落入
        # except 分支报 smoke_*_failed（transient），而非被当 JSON 误判为 field_mismatch
        # （schema drift）——保持 schema-drift 信号纯净，避免运维误诊 lookup None 成因。
        r.raise_for_status()
        body = r.json()
        if 'result' not in body or 'state' not in body:
            log.error('smoke_cwl_field_mismatch: missing result/state in response')
        elif not body.get('result'):
            # L-20260706T010500Z（review round 1 [important]）：空 result 是「未查到该期」，
            # 非验证而非 schema-OK。旧实现 else 分支对空 result 直接报 ok，导致上游返回
            # 空时冒烟被当成功——schema 漂移永远沉默（本函数 docstring 自警的 trap）。
            log.warning('smoke_cwl_no_data_to_verify: empty result for code=2026082')
        elif 'prizegrades' not in body['result'][0]:
            log.error('smoke_cwl_field_mismatch: missing prizegrades in result[0]')
        else:
            log.info('smoke_cwl_ok')
    except Exception as exc:
        log.error('smoke_cwl_failed: %s', exc)

    # sporttery 冒烟：dlt（gameNo=85）任取一期，验证 list[*] 含 prizeLevelList（T3 依赖）。
    try:
        r = httpx.get(
            'https://webapi.sporttery.cn/gateway/lottery/getHistoryPageListV1.qry',
            params={
                'gameNo': '85', 'provinceId': '0', 'pageSize': '1',
                'isVerify': '1', 'pageNo': '1',
                # L-20260706T010500Z（review round 1 [important]）：传 term（期号）定位具体开奖，
                # 否则 API 返回摘要列表（无 prizeLevelList）——smoke 永不命中真实开奖详情页，
                # 沦为 silent-success。cwl 用 code 精确定位，sporttery 须用 term 对齐语义。
                'term': '2026099',
            },
            headers={'User-Agent': 'Mozilla/5.0'},
            timeout=5.0,
        )
        r.raise_for_status()  # 同 cwl：5xx/4xx 须分类为 failed 而非 field_mismatch
        body = r.json()
        data = body.get('data', {})
        items = data.get('list', [])
        if not items:
            # L-20260706T010500Z（review round 1 [important]）：空 list 是非验证而非 schema-OK。
            # 旧实现 `if items and ...` 对空 list 走 else 直接 ok，false-positive schema-OK。
            log.warning('smoke_sporttery_no_data_to_verify: empty list for term=2026099')
        elif 'prizeLevelList' not in items[0]:
            log.error('smoke_sporttery_field_mismatch: missing prizeLevelList in list[0]')
        else:
            log.info('smoke_sporttery_ok')
    except Exception as exc:
        log.error('smoke_sporttery_failed: %s', exc)


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
    """健康探活端点（spec §4.3 / Docker HEALTHCHECK）。

    DB 故障时返回 **HTTP 503**（非 200）——容器编排层（Docker HEALTHCHECK / k8s liveness）
    依赖 HTTP 状态码判断健康。返回 200 + status=degraded 会让 Docker 把不健康容器标为
    healthy，DB 长期故障时漏抓开奖/比对/推送，违反「中奖永不静默漏通知」核心纪律。
    响应体保留 `status=degraded` 供人类可读；HTTP 状态码供编排器判定（review-fix critical）。
    """
    try:
        with db.connect() as conn:
            conn.execute(text('SELECT 1'))
        db_ok = True
    except Exception as exc:
        # hunter：db 故障不得静默——只在 HTTP 响应变 degraded 会让运维无迹可寻，延误发现。
        logger.warning('/health db 探活失败: %s', exc)
        db_ok = False
    body = {
        'status': 'ok' if db_ok else 'degraded',
        'tz': get_settings().tz,
        'db': 'ok' if db_ok else 'down',
    }
    # review-fix：DB down → HTTP 503，让编排层（Docker/k8s）正确标 unhealthy。
    # silent-failure 设防（L-20260706T010500Z 自验：HTTP 503 真能改变 Docker HEALTHCHECK
    # 判定——否则 DB 故障被静默吞，违反「中奖永不静默漏通知」核心纪律）。
    if not db_ok:
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=503, content=body)
    return body


# —— FastAPI 静态托管 SPA（spec §12.3 / plan 06 T8）——
# STATIC_DIR 由前端构建产物（web/ → npm run build → ../static/）落盘。生产部署存在，
# 开发态/未构建时不存在——此时不应注册 catch_all（保留 FastAPI 默认 404，让 dev 用 Vite）。
# 注：plan T8 brief 的 Files 字段提到 app/cli.py（build 集成），但 brief 无 cli.py 代码块，
# cli 将在 T10 引入（plan §10）——本 task 不创建 cli.py，避免 over-build（L-20260706T123500Z）。
STATIC_DIR = Path(__file__).parent.parent / 'static'


def mount_spa(app: FastAPI, static_dir: Path) -> None:
    """注册 SPA 静态托管 + history catch-all（spec §12.3）。

    - ``/assets/*`` 走 StaticFiles（前端构建产物的 hash 命名资源）。
    - ``/{full_path:path}`` catch_all：非 API/静态路径回退 index.html，让 Vue Router
      history 模式刷新子路由时不 404。

    catch_all 注册时机：必须**晚于**所有 API router（app.include_router 在 main.py 顶部
    已完成）。FastAPI 按注册顺序匹配，API router 先注册 → 先命中；catch_all 仅在无人命中
    时兜底。因此 catch_all 的排除前缀列表（auth/admin/channels/health）是 belt-and-
    suspenders 防御性逻辑，列表不完整（缺 tickets/claims/api）不影响功能——顺序保证。

    抽成函数：模块级一次性调用 + 测试可显式调用并清理 routes（避免污染全局 app）。
    """
    from fastapi.responses import FileResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles

    assets_dir = static_dir / 'assets'
    if assets_dir.exists():
        app.mount('/assets', StaticFiles(directory=assets_dir), name='assets')

    index_html = static_dir / 'index.html'

    @app.get('/{full_path:path}', name='spa_catch_all')
    def spa_catch_all(full_path: str):
        """history 模式：非 API/静态路径回退 index.html（spec §12.3）。

        排除已知 API 前缀——防御性 belt-and-suspenders（路由顺序已保证 API 先命中）。
        """
        if full_path.startswith(('auth/', 'admin/', 'channels/', 'health')):
            return JSONResponse(status_code=404, content={'detail': 'not found'})
        return FileResponse(index_html)


# 模块级一次性注册（生产 static/ 存在时挂载，开发/未 build 时跳过）。
if STATIC_DIR.exists() and (STATIC_DIR / 'index.html').exists():
    mount_spa(app, STATIC_DIR)
