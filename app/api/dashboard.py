"""Plan 06 / T6：Dashboard 聚合 API。

Spec §12.2：仪表盘首屏需要「待兑奖 / 我的命中 / 盈亏速览 / 开奖概览」的聚合快照。
/api/dashboard 在一次请求内返回当前用户的全部首屏数据，减少前端多次请求。
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, func
from sqlmodel import Session, select

from app.api.deps import current_user, get_session_dep
from app.config import get_settings
from app.models import Comparison, DrawResult, LotteryType, PrizeClaim, Ticket, User

_CST = ZoneInfo('Asia/Shanghai')
logger = logging.getLogger(__name__)

# 高德 POI HTTP 客户端（模块级单例，与 adapter 模式一致：D1 决策，独立 httpx.Client）。
# 测试通过替换此属性注入 MockTransport。
_amap_client = httpx.Client(timeout=5.0)


def _build_time_filter(period: str, date_from: str | None = None, date_to: str | None = None):
    """Build SQLAlchemy filter for time period.

    Returns a callable that takes a datetime column and returns a filter expression,
    or None for 'all' period.

    IMPORTANT: All datetime comparisons use naive UTC to match the project convention
    (TimestampMixin.created_at = default_factory=datetime.utcnow = naive UTC).
    SQLite stores datetimes as strings without timezone info, so CST and UTC values
    would compare incorrectly if mixed.
    """
    if period == 'all':
        return None

    # Use naive UTC to match created_at column convention (datetime.utcnow)
    utc_now = datetime.utcnow()

    if period == 'month':
        # Current month: from 1st day of current month to end of current month
        start_of_month = utc_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if utc_now.month == 12:
            end_of_month = utc_now.replace(year=utc_now.year + 1, month=1, day=1)
        else:
            end_of_month = utc_now.replace(month=utc_now.month + 1, day=1)
        return lambda col: and_(col >= start_of_month, col < end_of_month)

    elif period == 'year':
        # Current year: from Jan 1 to Dec 31
        start_of_year = utc_now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        end_of_year = utc_now.replace(year=utc_now.year + 1, month=1, day=1)
        return lambda col: and_(col >= start_of_year, col < end_of_year)

    elif period == 'custom':
        # Custom date range: date_from and date_to (YYYY-MM-DD format)
        if date_from and date_to:
            try:
                start_date = datetime.strptime(date_from, '%Y-%m-%d')
                end_date = datetime.strptime(date_to, '%Y-%m-%d')
                # Make inclusive: end_date should include the whole day
                end_date = end_date.replace(hour=23, minute=59, second=59)
                return lambda col: and_(col >= start_date, col <= end_date)
            except ValueError as e:
                # Invalid date format, raise error for 422
                raise ValueError(f'Invalid date format: {e}') from e

    # Unknown period defaults to all
    return None

router = APIRouter(prefix='/api/dashboard', tags=['dashboard'])


class LatestDrawOut(BaseModel):
    lottery_code: str
    lottery_name: str
    draw_no: str
    draw_date: datetime
    numbers_json: str
    verified: bool
    single_source: bool


class PendingClaimOut(BaseModel):
    id: int
    comparison_id: int
    lottery_code: str
    lottery_name: str
    draw_no: str
    prize_tier: int | None
    prize_amount: int | None
    deadline: datetime
    status: str
    days_left: int


class SummaryOut(BaseModel):
    total_cost: int
    total_prize: int
    pending_amount: int = Field(description='Count of winning comparisons with NULL prize_amount (floating prizes awaiting backfill). Named "amount" for backward compatibility but holds a count, not monetary value.')
    net: int
    win_count: int
    ticket_count: int
    win_rate: float = Field(description='中奖率 (winning comparisons / total tickets), 0.0–1.0')
    welfare_contribution: int = Field(description='公益贡献（分）：按各彩种 welfare_rate × 投入金额累加')


class DashboardOut(BaseModel):
    latest_draws: list[LatestDrawOut]
    pending_claims: list[PendingClaimOut]
    recent_hits: list[dict[str, Any]]
    summary: SummaryOut


def _latest_draws(session: Session) -> list[LatestDrawOut]:
    """每个启用彩种最新一期开奖结果（子查询 MAX(draw_date) GROUP BY lottery_code）。"""
    lottery_rows = session.exec(select(LotteryType).where(LotteryType.enabled == True)).all()  # noqa: E712
    lotteries = {lt.code: lt for lt in lottery_rows}
    if not lotteries:
        return []

    # 单次查询：每彩种最新 draw_date 子查询 JOIN 回 DrawResult
    subq = (
        select(
            DrawResult.lottery_code,
            func.max(DrawResult.draw_date).label('max_date'),
        )
        .where(DrawResult.lottery_code.in_(lotteries.keys()))
        .group_by(DrawResult.lottery_code)
    ).subquery()

    latest_draws = session.exec(
        select(DrawResult)
        .join(
            subq,
            and_(
                DrawResult.lottery_code == subq.c.lottery_code,
                DrawResult.draw_date == subq.c.max_date,
            ),
        )
    ).all()

    result = []
    for d in sorted(latest_draws, key=lambda d: d.lottery_code):
        lt = lotteries.get(d.lottery_code)
        if lt is None:
            continue
        result.append(
            LatestDrawOut(
                lottery_code=d.lottery_code,
                lottery_name=lt.name,
                draw_no=d.draw_no,
                draw_date=d.draw_date,
                numbers_json=d.numbers_json,
                verified=d.verified,
                single_source=d.single_source,
            )
        )
    return result


def _pending_claims(session: Session, user_id: int, period: str = 'month', lottery_code: str | None = None, date_from: str | None = None, date_to: str | None = None) -> list[PendingClaimOut]:
    """当前用户待兑奖记录，按截止日升序。支持 period 和 lottery_code 过滤。"""
    # Build filter conditions
    conds = [
        Comparison.user_id == user_id,
        PrizeClaim.status == 'pending',
    ]
    if lottery_code:
        conds.append(DrawResult.lottery_code == lottery_code)
    time_filter = _build_time_filter(period, date_from=date_from, date_to=date_to)
    if time_filter:
        conds.append(time_filter(Comparison.created_at))

    rows = session.exec(
        select(PrizeClaim, Comparison, DrawResult, LotteryType)
        .join(Comparison, PrizeClaim.comparison_id == Comparison.id)
        .join(DrawResult, Comparison.draw_result_id == DrawResult.id)
        .join(LotteryType, DrawResult.lottery_code == LotteryType.code)
        .where(and_(*conds))
        .order_by(PrizeClaim.deadline.asc())
    ).all()

    # Use naive CST to match deadline values (compare_service._now() writes aware CST,
    # SQLite strips tzinfo → stored as naive CST).  Matching timezone avoids 8h off-by-one.
    today = datetime.now(_CST).replace(tzinfo=None)
    result = []
    for claim, comparison, draw, lottery in rows:
        delta = claim.deadline - today
        days_left = max(0, delta.days)
        result.append(
            PendingClaimOut(
                id=claim.id,
                comparison_id=comparison.id,
                lottery_code=lottery.code,
                lottery_name=lottery.name,
                draw_no=draw.draw_no,
                prize_tier=comparison.prize_tier,
                prize_amount=comparison.prize_amount,
                deadline=claim.deadline,
                status=claim.status,
                days_left=days_left,
            )
        )
    return result


def _summary(session: Session, user_id: int, period: str = 'month', lottery_code: str | None = None, date_from: str | None = None, date_to: str | None = None) -> SummaryOut:
    """盈亏摘要：投入按 tickets.cost；中奖按 comparisons.prize_amount。
    pending_amount 统计 prize_amount IS NULL 的中奖笔数（浮动奖未回填，无金额可计）。
    win_rate = win_count / ticket_count（ticket_count=0 时返回 0.0）。
    welfare_contribution 按每票(lottery_type.welfare_rate × cost)累加。

    Filters:
    - period: 'month' (current month), 'year' (current year), 'all', 'custom' (with date_from/date_to)
    - lottery_code: filter by specific lottery type
    - date_from/date_to: required when period='custom' (YYYY-MM-DD format)
    """
    # Build time filter for Ticket.created_at and Comparison.created_at
    time_filter = _build_time_filter(period, date_from=date_from, date_to=date_to)

    # Ticket conditions
    ticket_conds = [Ticket.user_id == user_id, Ticket.enabled == True]  # noqa: E712
    if lottery_code:
        ticket_conds.append(Ticket.lottery_code == lottery_code)
    if time_filter:
        ticket_conds.append(time_filter(Ticket.created_at))

    cost_row = session.exec(
        select(func.coalesce(func.sum(Ticket.cost), 0)).where(and_(*ticket_conds))
    ).first()
    total_cost = int(cost_row or 0)

    ticket_count_row = session.exec(
        select(func.count(Ticket.id)).where(and_(*ticket_conds))
    ).first()
    ticket_count = int(ticket_count_row or 0)

    # Comparison conditions
    comp_conds = [Comparison.user_id == user_id]
    if lottery_code:
        # Join through DrawResult to filter by lottery_code
        comp_conds.append(Comparison.draw_result_id == DrawResult.id)
        comp_conds.append(DrawResult.lottery_code == lottery_code)
    if time_filter:
        comp_conds.append(time_filter(Comparison.created_at))

    win_row = session.exec(
        select(
            func.coalesce(func.sum(Comparison.prize_amount), 0),
            func.count(Comparison.id),
        ).where(and_(*comp_conds,
            Comparison.is_win == True,  # noqa: E712
            Comparison.prize_amount != None,  # noqa: E711
        ))
    ).first()
    total_prize = int(win_row[0] if win_row else 0)
    win_count = int(win_row[1] if win_row else 0)

    # Pending claims where prize_amount IS NULL (floating prizes not yet backfilled).
    pending_count_row = session.exec(
        select(func.count(Comparison.id)).where(and_(*comp_conds,
            Comparison.is_win == True,  # noqa: E712
            Comparison.prize_amount == None,  # noqa: E711
        ))
    ).first()
    pending_amount = int(pending_count_row or 0)

    # 中奖率：win_count / ticket_count
    win_rate = (win_count / ticket_count) if ticket_count > 0 else 0.0

    # 公益贡献：按每票的 lottery_type.welfare_rate × cost 累加
    welfare_contribution = 0
    if total_cost > 0:
        # Preload lottery types for welfare_rate lookup
        lt_rows = session.exec(select(LotteryType)).all()
        lt_map = {lt.code: lt for lt in lt_rows}

        # Build ticket cost aggregation with filters
        ticket_cost_stmt = (
            select(Ticket.lottery_code, func.sum(Ticket.cost))
            .where(and_(*ticket_conds))
            .group_by(Ticket.lottery_code)
        )
        tickets_with_lottery = session.exec(ticket_cost_stmt).all()
        for lt_code, cost_sum in tickets_with_lottery:
            lt = lt_map.get(lt_code)
            if lt is not None:
                try:
                    spec = json.loads(lt.spec_json)
                    rate = spec.get('welfare_rate', 0)
                except json.JSONDecodeError:
                    rate = 0
                welfare_contribution += int(cost_sum * rate / 100)

    return SummaryOut(
        total_cost=total_cost,
        total_prize=total_prize,
        pending_amount=pending_amount,
        net=total_prize - total_cost,
        win_count=win_count,
        ticket_count=ticket_count,
        win_rate=win_rate,
        welfare_contribution=welfare_contribution,
    )


_MAX_RECENT_HITS = 20


def _recent_hits(session: Session, user_id: int, period: str = 'month', lottery_code: str | None = None, date_from: str | None = None, date_to: str | None = None) -> list[dict[str, Any]]:
    """最近中奖记录（多彩种混合，按 created_at 倒序）。
    批量查 PrizeClaim（避免 N+1），按 comparison_id → latest_claim 索引。
    支持 period 和 lottery_code 过滤。"""
    # Build filter conditions
    conds = [
        Comparison.user_id == user_id,
        Comparison.is_win == True,  # noqa: E712
    ]
    if lottery_code:
        conds.append(DrawResult.lottery_code == lottery_code)
    time_filter = _build_time_filter(period, date_from=date_from, date_to=date_to)
    if time_filter:
        conds.append(time_filter(Comparison.created_at))

    rows = session.exec(
        select(Comparison, DrawResult, LotteryType)
        .join(DrawResult, Comparison.draw_result_id == DrawResult.id)
        .join(LotteryType, DrawResult.lottery_code == LotteryType.code)
        .where(and_(*conds))
        .order_by(Comparison.created_at.desc())
        .limit(_MAX_RECENT_HITS)
    ).all()

    if not rows:
        return []

    # Single batched query for all PrizeClaim rows (max 20 comparisons → 1 query)
    comp_ids = [comp.id for comp, _, _ in rows]
    claims = session.exec(
        select(PrizeClaim)
        .where(PrizeClaim.comparison_id.in_(comp_ids))
        .order_by(PrizeClaim.comparison_id, PrizeClaim.id.desc())
    ).all()

    # Build dict {comparison_id: latest_claim}
    claim_by_comp: dict[int, PrizeClaim] = {}
    for c in claims:
        if c.comparison_id not in claim_by_comp:
            claim_by_comp[c.comparison_id] = c  # first = latest (desc order)

    hits = []
    for comp, draw, lottery in rows:
        claim = claim_by_comp.get(comp.id)
        hits.append({
            'id': comp.id,
            'lottery_code': lottery.code,
            'lottery_name': lottery.name,
            'draw_no': draw.draw_no,
            'prize_tier': comp.prize_tier,
            'prize_amount': comp.prize_amount,
            'is_win': comp.is_win,
            'claim_status': claim.status if claim else None,
            'created_at': comp.created_at.isoformat() if comp.created_at else None,
        })
    return hits


class CalendarItemOut(BaseModel):
    """开奖日历单条（spec §12.2 row 2）：彩种开奖日程 + 下一期预告。"""

    lottery_code: str
    lottery_name: str
    category: str
    draw_days: list[int] = Field(description='开奖日（Python weekday: 0=周一…6=周日）')
    next_draw_date: str | None = Field(
        default=None,
        description='下一期开奖日（YYYY-MM-DD），从今天起最近的 draw_day；无日程则为 null',
    )


class AgencyOut(BaseModel):
    """附近代销点（spec §12.2 row 2 / §5.4）：高德 POI 真实数据或 mock 回退。"""

    name: str
    address: str
    category: str = Field(description='welfare=福彩 / sport=体彩')
    lat: float
    lng: float
    distance_m: int | None = Field(default=None, description='距用户距离（米）；高德 POI 不返回距离时为空')


# Mock 代销点回退数据（用户未授权定位 / 无 AMAP_API_KEY / API 故障时使用）。
# 坐标用北京参考点；前端拿到后可直接调起地图导航。
_MOCK_AGENCIES: list[AgencyOut] = [
    AgencyOut(
        name='中国福利彩票（朝阳路销售厅）',
        address='北京市朝阳区朝阳路 XX 号',
        category='welfare',
        lat=39.9242,
        lng=116.4987,
        distance_m=320,
    ),
    AgencyOut(
        name='中国福利彩票（双井店）',
        address='北京市朝阳区双井桥东路南',
        category='welfare',
        lat=39.8963,
        lng=116.4647,
        distance_m=850,
    ),
    AgencyOut(
        name='中国体育彩票（建国路网点）',
        address='北京市朝阳区建国路 XX 号',
        category='sport',
        lat=39.9082,
        lng=116.4870,
        distance_m=540,
    ),
    AgencyOut(
        name='中国体育彩票（大望路店）',
        address='北京市朝阳区大望路 XX 号',
        category='sport',
        lat=39.9098,
        lng=116.5074,
        distance_m=1100,
    ),
]


def _classify_agency_category(name: str) -> str:
    """根据代销点名称判断福彩/体彩。

    高德 POI typecode 无彩票细分类型，靠 name 关键词判断：
    - 含「福利彩票」或「福彩」→ welfare
    - 含「体育彩票」或「体彩」→ sport
    - 无法判断 → welfare（默认，福彩代销点数量更多）
    """
    if '体育彩票' in name or '体彩' in name:
        return 'sport'
    return 'welfare'


def _query_amap_pois(lat: float, lng: float) -> list[AgencyOut]:
    """调用高德 /place/around 搜索附近彩票代销点。

    高德 Web 服务 API location 参数格式：lng,lat（经度在前，与 WGS84 lat,lng 相反）。
    搜索关键词「彩票」覆盖福彩+体彩代销点。
    失败时抛异常，由调用方 catch 回退 mock。
    """
    settings = get_settings()
    if not settings.amap_api_key:
        raise ValueError('AMAP_API_KEY not configured')

    r = _amap_client.get(
        'https://restapi.amap.com/v3/place/around',
        params={
            'key': settings.amap_api_key,
            'location': f'{lng},{lat}',  # 高德格式：经度,纬度
            'keywords': '彩票',
            'radius': 3000,  # 3km 搜索半径
            'offset': 20,
            'page': 1,
            'extensions': 'base',
        },
    )
    r.raise_for_status()
    body = r.json()
    if body.get('status') != '1':
        raise ValueError(f"amap error: {body.get('info', 'unknown')}")

    pois = body.get('pois') or []
    result: list[AgencyOut] = []
    for poi in pois:
        name = poi.get('name', '')
        if not name:
            continue
        address = poi.get('address') or ''
        # address 空时用 pname+cityname+adname 拼接
        if not address:
            parts = [poi.get('pname', ''), poi.get('cityname', ''), poi.get('adname', '')]
            address = ''.join(p for p in parts if p)
        location = poi.get('location', '')
        if ',' not in location:
            continue
        lng_str, lat_str = location.split(',', 1)
        try:
            poi_lng = float(lng_str)
            poi_lat = float(lat_str)
        except ValueError:
            continue
        result.append(AgencyOut(
            name=name,
            address=address or '地址未知',
            category=_classify_agency_category(name),
            lat=poi_lat,
            lng=poi_lng,
            distance_m=None,  # 高德 /place/around base 扩展不返回距离
        ))
    return result


def _compute_next_draw_date(draw_days: list[int], today: datetime) -> str | None:
    """从今天起最近一个匹配的 draw_day（Python weekday），返回 ISO date 字符串。

    draw_days 为空 → None。today 取 naive CST date 对齐用户视角。
    """
    valid = sorted({d for d in draw_days if isinstance(d, int) and 0 <= d <= 6})
    if not valid:
        return None
    today_date = today.replace(hour=0, minute=0, second=0, microsecond=0)
    for offset in range(0, 8):  # 一周内必中
        candidate = today_date + timedelta(days=offset)
        if candidate.weekday() in valid:
            return candidate.strftime('%Y-%m-%d')
    return None  # 不可达；兜底


def _parse_draw_days(draw_schedule_json: str | None) -> list[int]:
    """从 draw_schedule_json 提取 draw_days；非法/空 JSON → []。"""
    if not draw_schedule_json:
        return []
    try:
        data = json.loads(draw_schedule_json)
    except (json.JSONDecodeError, TypeError):
        return []
    days = data.get('draw_days') if isinstance(data, dict) else None
    if not isinstance(days, list):
        return []
    return [d for d in days if isinstance(d, int)]


@router.get('/calendar', response_model=list[CalendarItemOut])
def dashboard_calendar(
    user: User = Depends(current_user),
    session: Session = Depends(get_session_dep),
) -> list[CalendarItemOut]:
    """开奖日历：返回**启用**彩种的开奖日程 + 下一期预告日（spec §12.2 row 2）。

    仅返回 enabled=True 的彩种（按启用彩种过滤）。draw_days 从 draw_schedule_json 解析；
    缺失/非法的彩种仍返回（draw_days=[]，next_draw_date=null），不阻断整体响应。
    next_draw_date 取 naive CST 视角的"今天起最近一个 draw_day"。
    """
    rows = session.exec(
        select(LotteryType).where(LotteryType.enabled == True)  # noqa: E712
    ).all()

    today = datetime.now(_CST).replace(tzinfo=None)
    items: list[CalendarItemOut] = []
    for lt in rows:
        days = _parse_draw_days(lt.draw_schedule_json)
        items.append(
            CalendarItemOut(
                lottery_code=lt.code,
                lottery_name=lt.name,
                category=lt.category,
                draw_days=days,
                next_draw_date=_compute_next_draw_date(days, today),
            )
        )
    # 按 next_draw_date 升序排列（最近的在前），无日程（None）的排末尾。
    # 用户关心「下一期最近开奖是哪个彩种」，按日期排序比按 code 字母序更有意义。
    items.sort(key=lambda x: x.next_draw_date or '9999-12-31')
    return items


@router.get('/agencies', response_model=list[AgencyOut])
def dashboard_agencies(
    category: str | None = Query(None, pattern='^(welfare|sport)$'),
    lat: float | None = Query(None, ge=-90, le=90),
    lng: float | None = Query(None, ge=-180, le=180),
    user: User = Depends(current_user),
) -> list[AgencyOut]:
    """附近代销点（spec §12.2 row 2 / §5.4）：高德 POI 真实数据 + mock 回退。

    - 有 lat/lng + AMAP_API_KEY → 调用高德 /place/around 搜索附近彩票代销点
    - 无 lat/lng（用户未授权定位）/ 无 API key / API 故障 → 回退 mock 数据
    - category=welfare → 仅福彩；category=sport → 仅体彩
    - 非法 category → 422（Query pattern）

    高德 Web 服务 location 参数格式：lng,lat（经度在前）。
    """
    agencies: list[AgencyOut]
    if lat is not None and lng is not None:
        try:
            agencies = _query_amap_pois(lat, lng)
        except Exception:
            # 高德 API 故障不得阻断 dashboard（silent-failure 纪律：外部依赖降级）。
            logger.warning('amap_poi_failed lat=%s lng=%s', lat, lng, exc_info=True)
            agencies = list(_MOCK_AGENCIES)
    else:
        agencies = list(_MOCK_AGENCIES)

    if category is not None:
        agencies = [a for a in agencies if a.category == category]
    return agencies


class MonthlyPointOut(BaseModel):
    month: str  # "2026-01"
    cost: int
    prize: int


@router.get('/monthly', response_model=list[MonthlyPointOut])
def dashboard_monthly(
    user: User = Depends(current_user),
    session: Session = Depends(get_session_dep),
) -> list[MonthlyPointOut]:
    """返回当前用户的月度投入/中奖数据（最近12个月），供 MyStats 月柱图使用。"""
    # Monthly cost aggregation
    cost_rows = session.exec(
        select(
            func.strftime('%Y-%m', Ticket.created_at).label('month'),
            func.sum(Ticket.cost).label('cost'),
        )
        .where(Ticket.user_id == user.id, Ticket.enabled == True)  # noqa: E712
        .group_by('month')
        .order_by('month')
    ).all()

    # Monthly prize aggregation
    prize_rows = session.exec(
        select(
            func.strftime('%Y-%m', Comparison.created_at).label('month'),
            func.coalesce(func.sum(Comparison.prize_amount), 0).label('prize'),
        )
        .where(Comparison.user_id == user.id, Comparison.is_win == True)  # noqa: E712
        .group_by('month')
        .order_by('month')
    ).all()

    cost_map: dict[str, int] = {row.month: int(row.cost) for row in cost_rows}
    prize_map: dict[str, int] = {row.month: int(row.prize) for row in prize_rows}

    # Merge into unified monthly list (last 12 months)
    now = datetime.now(_CST)
    months = []
    for i in range(11, -1, -1):
        m = now.month - i
        y = now.year
        while m <= 0:
            m += 12
            y -= 1
        month_str = f'{y}-{m:02d}'
        months.append(MonthlyPointOut(
            month=month_str,
            cost=cost_map.get(month_str, 0),
            prize=prize_map.get(month_str, 0),
        ))

    return months


@router.get('', response_model=DashboardOut)
def dashboard(
    period: str = Query('month', pattern='^(month|year|all|custom)$'),
    lottery_code: str | None = Query(None),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    user: User = Depends(current_user),
    session: Session = Depends(get_session_dep),
) -> DashboardOut:
    """返回当前登录用户的首屏聚合数据。

    Filters:
    - period: 'month' (current month, default), 'year' (current year), 'all', 'custom'
    - lottery_code: filter by specific lottery type (optional)
    - date_from/date_to: custom date range (YYYY-MM-DD), required when period='custom'
    """
    latest = _latest_draws(session)
    pending = _pending_claims(session, user.id, period=period, lottery_code=lottery_code, date_from=date_from, date_to=date_to)
    summary = _summary(session, user.id, period=period, lottery_code=lottery_code, date_from=date_from, date_to=date_to)
    hits = _recent_hits(session, user.id, period=period, lottery_code=lottery_code, date_from=date_from, date_to=date_to)
    return DashboardOut(
        latest_draws=latest,
        pending_claims=pending,
        recent_hits=hits,
        summary=summary,
    )
