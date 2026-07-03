"""Plan 06 / T6：比对记录 API（中奖记录）。

返回当前用户的全部比对结果（可选仅中奖），含开奖信息、注单信息、兑奖状态。
"""

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import desc
from sqlmodel import Session, select

from app.api.deps import current_user, get_session_dep
from app.models import Comparison, DrawResult, LotteryType, PrizeClaim, Ticket, User

router = APIRouter(prefix='/api/comparisons', tags=['comparisons'])


class ComparisonOut(BaseModel):
    id: int
    lottery_code: str
    lottery_name: str
    draw_no: str
    draw_date: datetime
    numbers_json: str
    ticket_label: str | None
    hits_json: str
    prize_tier: int | None
    prize_amount: int | None
    is_win: bool
    created_at: datetime
    claim_status: str | None
    claim_id: int | None
    deadline: datetime | None


_MAX_LIMIT = 200


@router.get('', response_model=list[ComparisonOut])
def list_comparisons(
    win_only: bool = Query(False),
    lottery_code: str | None = Query(None),
    limit: int = Query(100, ge=1, le=_MAX_LIMIT),
    user: User = Depends(current_user),
    session: Session = Depends(get_session_dep),
) -> list[ComparisonOut]:
    """返回当前用户的比对记录，按创建时间降序。

    win_only=true 时仅返回中奖记录，用于「中奖记录」页面。
    lottery_code 可按彩种筛选。
    """
    stmt = select(Comparison, DrawResult, LotteryType, Ticket).where(
        Comparison.user_id == user.id
    ).join(
        DrawResult, Comparison.draw_result_id == DrawResult.id
    ).join(
        LotteryType, DrawResult.lottery_code == LotteryType.code
    ).join(
        Ticket, Comparison.ticket_id == Ticket.id
    ).order_by(desc(Comparison.created_at)).limit(limit)

    if win_only:
        stmt = stmt.where(Comparison.is_win == True)  # noqa: E712

    if lottery_code:
        stmt = stmt.where(DrawResult.lottery_code == lottery_code)

    rows = session.exec(stmt).all()

    comparison_ids = [cmp.id for cmp, _dr, _lt, _t in rows]
    claims: dict[int, PrizeClaim] = {}
    if comparison_ids:
        claim_rows = session.exec(
            select(PrizeClaim).where(PrizeClaim.comparison_id.in_(comparison_ids))
        ).all()
        claims = {c.comparison_id: c for c in claim_rows}

    result = []
    for cmp, draw, lottery, ticket in rows:
        claim = claims.get(cmp.id)
        result.append(
            ComparisonOut(
                id=cmp.id,
                lottery_code=lottery.code,
                lottery_name=lottery.name,
                draw_no=draw.draw_no,
                draw_date=draw.draw_date,
                numbers_json=ticket.numbers_json,
                ticket_label=ticket.label,
                hits_json=cmp.hits_json,
                prize_tier=cmp.prize_tier,
                prize_amount=cmp.prize_amount,
                is_win=cmp.is_win,
                created_at=cmp.created_at,
                claim_status=claim.status if claim else None,
                claim_id=claim.id if claim else None,
                deadline=claim.deadline if claim else None,
            )
        )
    return result
