import json
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.engine import Engine
from sqlmodel import Session, select

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

    # 2. 补宕机窗口内应开奖但未抓的彩种。
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
