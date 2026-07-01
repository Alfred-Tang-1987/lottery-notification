"""Plan 06 / T6：Dashboard 聚合 API。

Spec §12.2：仪表盘首屏需要「待兑奖 / 我的命中 / 盈亏速览 / 开奖概览」的聚合快照。
/api/dashboard 在一次请求内返回当前用户的全部首屏数据，减少前端多次请求。
"""

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func
from sqlmodel import Session, select

from app.api.deps import current_user, get_session_dep
from app.models import Comparison, DrawResult, LotteryType, PrizeClaim, Ticket, User

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
    pending_amount: int
    net: int
    win_count: int
    ticket_count: int


class DashboardOut(BaseModel):
    latest_draws: list[LatestDrawOut]
    pending_claims: list[PendingClaimOut]
    recent_hits: list[dict[str, Any]]
    summary: SummaryOut


def _latest_draws(session: Session) -> list[LatestDrawOut]:
    """每个启用彩种最新一期开奖结果。"""
    lottery_rows = session.exec(select(LotteryType).where(LotteryType.enabled == True)).all()  # noqa: E712
    lotteries = {lt.code: lt for lt in lottery_rows}
    if not lotteries:
        return []

    draws = session.exec(
        select(DrawResult).where(DrawResult.lottery_code.in_(lotteries.keys()))
    ).all()
    latest_by_code: dict[str, DrawResult] = {}
    for d in draws:
        existing = latest_by_code.get(d.lottery_code)
        if existing is None or d.draw_date > existing.draw_date:
            latest_by_code[d.lottery_code] = d

    result = []
    for code in sorted(lotteries):
        d = latest_by_code.get(code)
        if d is None:
            continue
        lt = lotteries[code]
        result.append(
            LatestDrawOut(
                lottery_code=code,
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

    today = datetime.now(timezone.utc).replace(tzinfo=None)
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
    """盈亏摘要：投入按 tickets.cost；中奖按 comparisons.prize_amount（null 计为待派奖）。"""
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

    pending_amount_row = session.exec(
        select(func.coalesce(func.sum(Comparison.prize_amount), 0)).where(
            Comparison.user_id == user_id,
            Comparison.is_win == True,  # noqa: E712
            Comparison.prize_amount == None,  # noqa: E711
        )
    ).first()
    pending_amount = int(pending_amount_row or 0)

    return SummaryOut(
        total_cost=total_cost,
        total_prize=total_prize,
        pending_amount=pending_amount,
        net=total_prize - total_cost,
        win_count=win_count,
        ticket_count=ticket_count,
    )


@router.get('', response_model=DashboardOut)
def dashboard(
    user: User = Depends(current_user),
    session: Session = Depends(get_session_dep),
) -> DashboardOut:
    """返回当前登录用户的首屏聚合数据。"""
    latest = _latest_draws(session)
    pending = _pending_claims(session, user.id)
    summary = _summary(session, user.id)
    return DashboardOut(
        latest_draws=latest,
        pending_claims=pending,
        recent_hits=[],
        summary=summary,
    )
