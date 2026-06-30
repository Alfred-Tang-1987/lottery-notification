"""Plan 05 / T6：admin 后台 API。

提供用户列表、开奖结果 force-verify、系统健康、推送日志查询。
所有端点要求 admin 角色；state-changing 端点强制 CSRF double-submit。
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.api.deps import current_user, get_session_dep, require_admin, verify_csrf
from app.models import ApiSourceHealth, DrawResult, NotificationLog, PendingComparison, User
from app.services.audit_service import write_audit

router = APIRouter(prefix='/admin', tags=['admin'], dependencies=[Depends(require_admin)])


@router.get('/users')
def list_users(session: Session = Depends(get_session_dep)):
    return [
        {'id': u.id, 'username': u.username, 'role': u.role, 'enabled': u.enabled}
        for u in session.exec(select(User)).all()
    ]


@router.post('/draw-results/{draw_id}/force-verify')
def force_verify(
    draw_id: int,
    admin: User = Depends(current_user),
    session: Session = Depends(get_session_dep),
    _csrf_ok: None = Depends(verify_csrf),
):
    """verified=false 恢复：admin 人工核对后强制标记 verified（spec §7.1）。

    静默失败纪律（CLAUDE.md）：
    - DrawResult.verified=True、PendingComparison outbox、AdminAuditLog 必须在同一事务内
      原子 commit（对齐 FetchService._store 的 verified 行 outbox 写入）。
    - admin force-verify 是 verified=false 拒入库行转 true 的唯一入口；若此处不写 outbox，
      CompareService._claim 永不认领 → 永不比对 → 永不推送，直接违反「中奖永不静默漏通知」。
    """
    dr = session.get(DrawResult, draw_id)
    if dr is None:
        raise HTTPException(404, '开奖结果不存在')
    old = {'verified': dr.verified}
    dr.verified = True
    session.add(PendingComparison(draw_result_id=dr.id))
    write_audit(
        session,
        admin_id=admin.id,
        action='force_verify',
        target_type='draw_result',
        target_id=str(draw_id),
        old_values=old,
        new_values={'verified': True},
        commit=False,
    )
    session.commit()
    return {'id': draw_id, 'verified': True}


@router.get('/health')
def system_health(session: Session = Depends(get_session_dep)):
    sources = session.exec(select(ApiSourceHealth)).all()
    return {'sources': [{'source': s.source, 'status': s.status} for s in sources]}


_MAX_PUSH_LOGS = 500


@router.get('/push-logs')
def push_logs(session: Session = Depends(get_session_dep), limit: int = 100):
    limit = min(max(limit, 1), _MAX_PUSH_LOGS)
    logs = session.exec(select(NotificationLog).order_by(NotificationLog.id.desc()).limit(limit)).all()
    return [
        {
            'id': log.id,
            'user_id': log.user_id,
            'type': log.type,
            'status': log.status,
            'error': log.error,
        }
        for log in logs
    ]
