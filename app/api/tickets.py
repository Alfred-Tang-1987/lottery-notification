"""Plan 05 / T7：号码池 CRUD API（tickets router）。

Spec §6.3 IDOR：所有号码读写经 ``current_user`` 拿 user_id，复用 Plan 03 ``TicketRepo``
（构造注入 ``user_id``，查询一律 ``WHERE user_id``），用户只能 CRUD 自己的票。

CSRF（spec §4.3）：POST/DELETE 是已登录 state-changing 路由——挂 ``verify_csrf``
double-submit，与 /channels、/auth/logout 模式一致；GET 只读不挂。
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.api.deps import current_user, get_session_dep, verify_csrf
from app.infrastructure.repositories import TicketRepo
from app.models import User

router = APIRouter(prefix='/tickets', tags=['tickets'])


class TicketIn(BaseModel):
    """建票入参（对齐 TicketRepo.create 关键字 + Ticket 字段约束）。

    ``numbers_json`` 为原始选择的 JSON 串（与 Ticket.numbers_json 一致），由领域层
    ``Entry.expand`` 在比对阶段展开；本 API 边界只做结构校验（非空），不重复领域规则。
    """

    lottery_code: str = Field(min_length=1, max_length=8)
    play_type: str = Field(min_length=1, max_length=16)
    numbers_json: str = Field(min_length=1)
    cost: int = Field(ge=0)
    tuo_json: str | None = None
    label: str | None = Field(default=None, max_length=32)
    multiplier: int = Field(default=1, ge=1, le=99)
    append: bool = False
    enabled: bool = True


class TicketOut(BaseModel):
    id: int
    lottery_code: str
    play_type: str
    label: str | None
    multiplier: int
    enabled: bool


@router.post('', response_model=TicketOut, status_code=status.HTTP_201_CREATED)
def create_ticket(
    body: TicketIn,
    user: User = Depends(current_user),
    session: Session = Depends(get_session_dep),
    _csrf_ok: None = Depends(verify_csrf),
) -> TicketOut:
    """新增号码：经 TicketRepo 建票（IDOR-safe，归属当前 user）。"""
    t = TicketRepo(session, user_id=user.id).create(
        lottery_code=body.lottery_code,
        play_type=body.play_type,
        numbers_json=body.numbers_json,
        cost=body.cost,
        tuo_json=body.tuo_json,
        label=body.label,
        multiplier=body.multiplier,
        append=body.append,
        enabled=body.enabled,
    )
    return TicketOut(
        id=t.id,
        lottery_code=t.lottery_code,
        play_type=t.play_type,
        label=t.label,
        multiplier=t.multiplier,
        enabled=t.enabled,
    )


@router.get('', response_model=list[TicketOut])
def list_tickets(
    user: User = Depends(current_user),
    session: Session = Depends(get_session_dep),
) -> list[TicketOut]:
    """列出当前用户的全部号码（IDOR：TicketRepo 已 WHERE user_id）。"""
    rows = TicketRepo(session, user_id=user.id).list_all()
    return [
        TicketOut(
            id=t.id,
            lottery_code=t.lottery_code,
            play_type=t.play_type,
            label=t.label,
            multiplier=t.multiplier,
            enabled=t.enabled,
        )
        for t in rows
    ]


@router.delete('/{ticket_id}')
def delete_ticket(
    ticket_id: int,
    user: User = Depends(current_user),
    session: Session = Depends(get_session_dep),
    _csrf_ok: None = Depends(verify_csrf),
) -> dict[str, bool]:
    """删除号码：IDOR-safe——非归属票 TicketRepo.delete 返回 False → 404。

    返回 404（而非 403）避免泄露「该 id 存在但不属于你」的信息（与资源不存在不可区分）。
    """
    deleted = TicketRepo(session, user_id=user.id).delete(ticket_id)
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, '号码不存在')
    return {'deleted': True}
