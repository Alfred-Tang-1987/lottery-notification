"""Plan 06 / T6：开奖历史查询 API。

提供已核验开奖结果的按彩种历史查询，供「开奖查询」与「开奖走势」页面消费。
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import desc
from sqlmodel import Session, select

from app.api.deps import current_user, get_session_dep
from app.models import DrawResult, LotteryType, User

router = APIRouter(prefix='/api/draws', tags=['draws'])


class DrawOut(BaseModel):
    id: int
    lottery_code: str
    lottery_name: str
    draw_no: str
    draw_date: datetime
    numbers_json: str
    verified: bool
    single_source: bool
    version: int


_MAX_LIMIT = 200


@router.get('', response_model=list[DrawOut])
def list_draws(
    lottery_code: str = Query(..., min_length=1, max_length=8),
    limit: int = Query(50, ge=1, le=_MAX_LIMIT),
    user: User = Depends(current_user),
    session: Session = Depends(get_session_dep),
) -> list[DrawOut]:
    """返回指定彩种的开奖历史（按期号降序）。

    仅返回 verified 结果——未核验或单源数据不应进入历史查询/走势统计，避免误导用户。
    """
    lottery = session.get(LotteryType, lottery_code)
    if lottery is None:
        raise HTTPException(404, '彩种不存在')

    rows = session.exec(
        select(DrawResult)
        .where(
            DrawResult.lottery_code == lottery_code,
            DrawResult.verified == True,  # noqa: E712
        )
        .order_by(desc(DrawResult.draw_no))
        .limit(limit)
    ).all()

    return [
        DrawOut(
            id=d.id,
            lottery_code=d.lottery_code,
            lottery_name=lottery.name,
            draw_no=d.draw_no,
            draw_date=d.draw_date,
            numbers_json=d.numbers_json,
            verified=d.verified,
            single_source=d.single_source,
            version=d.version,
        )
        for d in rows
    ]
