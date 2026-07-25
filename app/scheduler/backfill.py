import json
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from app.config import get_settings
from app.models import DrawResult, LotteryType
from app.scheduler import _JobDeps
from app.seeds import SPECS
from app.services.compare_service import CompareService
from app.services.fetch_service import FetchService

_CST = ZoneInfo('Asia/Shanghai')
logger = logging.getLogger(__name__)
_BACKFILL_LOOKBACK_DAYS = 2


def run_startup_backfill(deps: _JobDeps) -> None:
    """启动 backfill（spec §7.3）：补未处理 outbox + 宕机窗口遗漏抓取。"""
    engine: Engine = deps['engine']
    fetch_service: FetchService = deps['fetch_service']
    compare_service: CompareService = deps['compare_service']

    # 1. 补未处理的 pending_comparisons（outbox）。单期失败不得阻断遗漏抓取。
    try:
        compare_service.process_pending()
    except Exception:
        logger.error('startup_backfill_process_pending_failed', exc_info=True)

    # 2. pre-check：两个数据源 key 都未配置时整段跳过——否则 7 彩种 × 12 次退避
    #    重试注定全失败，阻塞应用启动数分钟（healthcheck 超时 → restart:always
    #    无限重启循环，2026-07-21 冒烟实测）。outbox 已在步骤 1 处理，不受影响。
    settings = get_settings()
    if not settings.mxnzp_api_key and not settings.juhe_api_key:
        logger.info('startup_backfill_skip_fetch reason=no_data_source_key')
        return

    # 3. 冷启动历史回填：对 DB 中无数据的彩种，抓取最近 50 期历史开奖，让走势页
    #    冷启动即有数据。放在 missed-draw backfill 之前——历史回填常含「今日开奖」
    #    （MXNZP /common/history 返回最新 N 期），后续 missed-draw 检查命中即跳过，
    #    避免重复抓取。
    _backfill_history(engine, fetch_service, settings)

    # 4. 补宕机窗口内应开奖但未抓的彩种。
    today = datetime.now(_CST).date()
    lookback_days = [today - timedelta(days=i) for i in range(_BACKFILL_LOOKBACK_DAYS)]

    for code, draw_days in _enabled_lotteries(engine):
        try:
            missed = any(d.weekday() in draw_days and not _has_draw_for_date(engine, code, d) for d in lookback_days)
            if missed:
                fetch_service.fetch_and_store(code)
        except Exception:
            # 单彩种源故障不得阻断其他彩种（silent-failure 纪律）。
            logger.error('startup_backfill_fetch_failed code=%s', code, exc_info=True)


def _enabled_lotteries(engine: Engine) -> list[tuple[str, list[int]]]:
    """返回启用彩种代码及其 draw_days。

    lottery_types 表未写入时（如全新测试库）回退到 SPECS，保证启动 backfill
    在 seed 前亦能执行。
    """
    with Session(engine) as s:
        rows = list(s.exec(select(LotteryType).where(LotteryType.enabled)).all())
    if rows:
        result: list[tuple[str, list[int]]] = []
        for lt in rows:
            try:
                draw_days = json.loads(lt.draw_schedule_json).get('draw_days', [])
            except json.JSONDecodeError:
                logger.error('startup_backfill_bad_schedule code=%s', lt.code, exc_info=True)
                continue
            result.append((lt.code, draw_days))
        return result
    return [(spec['code'], spec['draw_days']) for spec in SPECS]


def _backfill_history(engine: Engine, fetch_service: FetchService, settings) -> None:
    """冷启动历史回填：对 DB 中无数据的彩种，抓取最近 50 期历史开奖号码。

    走势页冷启动即有数据，避免空白页。仅 mxnzp 主源支持（仅 MxnzpAdapter 有
    fetch_history 方法；juhe 无历史接口）。
    - DB 已有该彩种数据 → 跳过（幂等，避免重复抓取）
    - mxnzp key 未配置 → 跳过（fetch_history 会抛 PermanentLookupError，无意义）
    - 单彩种失败不阻断其他彩种（silent-failure 纪律）
    """
    primary = fetch_service._primary
    # 仅 mxnzp 支持历史接口；juhe 无 fetch_history。检查 name 避免对 MagicMock
    # auto-attr 误判（hasattr 对 MagicMock 恒 True）。
    if getattr(primary, 'name', None) != 'mxnzp':
        return
    if not settings.mxnzp_api_key:
        return

    for code, _draw_days in _enabled_lotteries(engine):
        try:
            if _has_any_draw_for_lottery(engine, code):
                continue
            draws = primary.fetch_history(code, size=50)
            if not draws:
                continue
            _store_history_draws(engine, draws, source_name='mxnzp')
            logger.info('backfill_history_done code=%s count=%d', code, len(draws))
        except Exception:
            # 单彩种失败不阻断其他彩种（silent-failure 纪律）。
            logger.error('backfill_history_failed code=%s', code, exc_info=True)


def _has_any_draw_for_lottery(engine: Engine, code: str) -> bool:
    """检查指定彩种在 DB 中是否已有任何开奖结果（历史回填幂等判断）。"""
    with Session(engine) as s:
        existing = s.exec(
            select(DrawResult).where(DrawResult.lottery_code == code)
        ).first()
    return existing is not None


def _store_history_draws(engine: Engine, draws: list, source_name: str) -> None:
    """批量存储历史开奖号码（single_source=True, verified=True）。

    幂等：基于 (lottery_code, draw_no) 查重，已有行跳过（不升级 flag——历史回填
    是单源数据，双源升级走 FetchService._store 正常路径）。
    不创建 PendingComparison：历史回填是冷启动数据补充（走势页用），非增量抓取；
    用户历史票的比对由 ticket 创建时的 outbox 机制覆盖。
    """
    with Session(engine) as s:
        for dn in draws:
            existing = s.exec(
                select(DrawResult).where(
                    DrawResult.lottery_code == dn.lottery_code,
                    DrawResult.draw_no == dn.draw_no,
                )
            ).first()
            if existing:
                continue
            dr = DrawResult(
                lottery_code=dn.lottery_code,
                draw_no=dn.draw_no,
                draw_date=datetime.combine(dn.draw_date, datetime.min.time(), tzinfo=_CST),
                numbers_json=json.dumps({
                    'front': list(dn.front),
                    'back': list(dn.back) if dn.back else None,
                }),
                source=source_name,
                verified=True,
                single_source=True,
                version=1,
            )
            s.add(dr)
        s.commit()


def _has_draw_for_date(engine: Engine, code: str, d: datetime.date) -> bool:
    """检查指定彩种在指定 CST 日期是否已有开奖结果。

    draw_date 由 FetchService 以 aware-CST 写入；SQLite 存取会剥离 tzinfo，但为
    避免 naive/aware 比较在 Python 层或未来存储格式变更时断裂，查询窗口同样使用
    aware-CST，与写入端保持一致。
    """
    day_start = datetime.combine(d, datetime.min.time(), tzinfo=_CST)
    day_end = datetime.combine(d + timedelta(days=1), datetime.min.time(), tzinfo=_CST)
    with Session(engine) as s:
        existing = s.exec(
            select(DrawResult).where(
                DrawResult.lottery_code == code,
                DrawResult.draw_date >= day_start,
                DrawResult.draw_date < day_end,
            )
        ).first()
    return existing is not None
