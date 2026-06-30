"""Plan 05 / T7：兑奖领取 API（claims router）。

Spec §6.3 IDOR：PrizeClaim 经 ``comparison`` 关联到 user——操作前必须校验
``comparison.user_id == current_user.id``，否则 403。否则用户可标记领取他人中奖。

CSRF（spec §4.3）：POST 是已登录 state-changing 路由——挂 ``verify_csrf`` double-submit。

静默失败纪律（CLAUDE.md）：claim 状态变更 + claimed_at 在单事务内 commit，原子。
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session

from app.api.deps import current_user, get_session_dep, verify_csrf
from app.models import Comparison, PrizeClaim, User

router = APIRouter(prefix='/claims', tags=['claims'])

# PrizeClaim.status 取值（对齐 PrizeClaim 字段注释 / spec §4 line 235）。
_STATUS_PENDING = 'pending'
_STATUS_CLAIMED = 'claimed'
_STATUS_EXPIRED = 'expired'

# claimed_at 遵循项目主流 naive-UTC 惯例（与 created_at 同数值时区，CLAUDE.md 雷区纪律）。
# 注：同行 deadline 用 aware-CST 是 compare_service（Plan 03/04）的历史遗留偏差
# （CLAUDE.md「系统性根治待后续」），不该让新字段 claimed_at 跟着用 CST。claimed_at
# 业务上不与 deadline 比较排序（scheduler 比 deadline vs now；claimed_at 是领取时间戳，
# 与 created_at 同属行级时间戳，未来算「领取延迟」会一起查），故对齐主流 created_at。


@router.post('/{claim_id}/claim')
def mark_claimed(
    claim_id: int,
    user: User = Depends(current_user),
    session: Session = Depends(get_session_dep),
    _csrf_ok: None = Depends(verify_csrf),
) -> dict[str, object]:
    """标记兑奖领取：pending → claimed，写 claimed_at。

    IDOR 校验顺序：先取 claim（404 不存在）→ 再取其 comparison 校验归属（403 越权）。
    404 先于 403：让「越权」与「不存在」对外不可区分会牺牲可观测性，但 spec §6.3
    要求 IDOR 防护——此处显式 403 以便前端区分（资源存在但无权）。
    """
    claim = session.get(PrizeClaim, claim_id)
    if claim is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, '兑奖记录不存在')
    comparison = session.get(Comparison, claim.comparison_id)
    if comparison is None or comparison.user_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, '无权操作')
    # 状态机守卫（spec §4 line 235/298）：仅 pending 可领取。
    # scheduler._expire_claims 会把过 deadline 的 pending 标 expired；此处不得把
    # expired 覆盖回 claimed（违反兑奖截止业务规则），已 claimed 的也不可重复领取。
    if claim.status == _STATUS_CLAIMED:
        raise HTTPException(status.HTTP_409_CONFLICT, '兑奖已领取，不可重复操作')
    if claim.status != _STATUS_PENDING:
        # 兑奖期已过（expired）或其他非 pending 终态——拒绝，状态不变。
        raise HTTPException(status.HTTP_409_CONFLICT, '兑奖期已过或状态不可领取')
    claim.status = _STATUS_CLAIMED
    claim.claimed_at = datetime.now(UTC).replace(tzinfo=None)
    session.add(claim)
    session.commit()
    return {'id': claim_id, 'status': _STATUS_CLAIMED}
