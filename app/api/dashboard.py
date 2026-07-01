"""Plan 06 / T6：Dashboard 聚合 API。

Spec §12.2：仪表盘首屏需要「待兑奖 / 我的命中 / 盈亏速览 / 开奖概览」的聚合快照。
/api/dashboard 在一次请求内返回当前用户的全部首屏数据，减少前端多次请求。
"""

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import and_, func
from sqlmodel import Session, select

from app.api.deps import current_user, get_session_dep
from app.models import Comparison, DrawResult, LotteryType, PrizeClaim, Ticket, User

_CST = ZoneInfo('Asia/Shanghai')

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


def _pending_claims(session: Session, user_id: int) -> list[PendingClaimOut]:
    """当前用户待兑奖记录，按截止日升序。"""
    rows = session.exec(
        select(PrizeClaim, Comparison, DrawResult, LotteryType)
        .join(Comparison, PrizeClaim.comparison_id == Comparison.id)
        .join(DrawResult, Comparison.draw_result_id == DrawResult.id)
        .join(LotteryType, DrawResult.lottery_code == LotteryType.code)
        .where(
            Comparison.user_id == user_id,
            PrizeClaim.status == 'pending',
        )
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


def _summary(session: Session, user_id: int) -> SummaryOut:
    """盈亏摘要：投入按 tickets.cost；中奖按 comparisons.prize_amount。
    pending_amount 统计 prize_amount IS NULL 的中奖笔数（浮动奖未回填，无金额可计）。
    win_rate = win_count / ticket_count（ticket_count=0 时返回 0.0）。
    welfare_contribution 按每票(lottery_type.welfare_rate × cost)累加。"""
    cost_row = session.exec(
        select(func.coalesce(func.sum(Ticket.cost), 0)).where(
            Ticket.user_id == user_id,
            Ticket.enabled == True,  # noqa: E712
        )
    ).first()
    total_cost = int(cost_row or 0)

    ticket_count_row = session.exec(
        select(func.count(Ticket.id)).where(
            Ticket.user_id == user_id,
            Ticket.enabled == True,  # noqa: E712
        )
    ).first()
    ticket_count = int(ticket_count_row or 0)

    win_row = session.exec(
        select(
            func.coalesce(func.sum(Comparison.prize_amount), 0),
            func.count(Comparison.id),
        ).where(
            Comparison.user_id == user_id,
            Comparison.is_win == True,  # noqa: E712
            Comparison.prize_amount != None,  # noqa: E711
        )
    ).first()
    total_prize = int(win_row[0] if win_row else 0)
    win_count = int(win_row[1] if win_row else 0)

    # Pending claims where prize_amount IS NULL (floating prizes not yet backfilled).
    # We count them rather than sum (SUM of all-NULL rows = NULL→0 is useless).
    pending_count_row = session.exec(
        select(func.count(Comparison.id)).where(
            Comparison.user_id == user_id,
            Comparison.is_win == True,  # noqa: E712
            Comparison.prize_amount == None,  # noqa: E711
        )
    ).first()
    pending_amount = int(pending_count_row or 0)

    # 中奖率：win_count / ticket_count
    win_rate = (win_count / ticket_count) if ticket_count > 0 else 0.0

    # 公益贡献：按每票的 lottery_type.welfare_rate × cost 累加
    welfare_contribution = 0
    if total_cost > 0:
        import json
        # Preload all lottery types for welfare_rate lookup
        lt_rows = session.exec(select(LotteryType)).all()
        lt_map = {lt.code: lt for lt in lt_rows}
        tickets_with_lottery = session.exec(
            select(Ticket.lottery_code, func.sum(Ticket.cost))
            .where(Ticket.user_id == user_id, Ticket.enabled == True)  # noqa: E712
            .group_by(Ticket.lottery_code)
        ).all()
        for lt_code, cost_sum in tickets_with_lottery:
            lt = lt_map.get(lt_code)
            if lt is not None:
                try:
                    spec = json.loads(lt.spec_json)
                    rate = spec.get('welfare_rate', 0)
                except (json.JSONDecodeError, KeyError):
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


def _recent_hits(session: Session, user_id: int) -> list[dict[str, Any]]:
    """最近中奖记录（多彩种混合，按 created_at 倒序）。
    批量查 PrizeClaim（避免 N+1），按 comparison_id → latest_claim 索引。"""
    rows = session.exec(
        select(Comparison, DrawResult, LotteryType)
        .join(DrawResult, Comparison.draw_result_id == DrawResult.id)
        .join(LotteryType, DrawResult.lottery_code == LotteryType.code)
        .where(
            Comparison.user_id == user_id,
            Comparison.is_win == True,  # noqa: E712
        )
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
    from datetime import datetime
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
    user: User = Depends(current_user),
    session: Session = Depends(get_session_dep),
) -> DashboardOut:
    """返回当前登录用户的首屏聚合数据。"""
    latest = _latest_draws(session)
    pending = _pending_claims(session, user.id)
    summary = _summary(session, user.id)
    hits = _recent_hits(session, user.id)
    return DashboardOut(
        latest_draws=latest,
        pending_claims=pending,
        recent_hits=hits,
        summary=summary,
    )
