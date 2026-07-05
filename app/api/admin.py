"""Plan 05 / T6：admin 后台 API。

提供用户列表、开奖结果 force-verify、系统健康、推送日志查询。
所有端点要求 admin 角色；state-changing 端点强制 CSRF double-submit。
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.api.deps import current_user, get_session_dep, require_admin, verify_csrf
from app.models import ApiSourceHealth, DrawResult, PendingComparison, User
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
    # outbox 判重（quality review IMPORTANT）：重复 force-verify 不得插第二条 PendingComparison
    # ——否则 CompareService._claim 按 id 认领两行，第二行写 comparisons 撞 (draw_result_id,
    # ticket_id) unique 约束被 per-row 隔离吞掉，outbox 残留重复行破坏比对幂等。
    existing = session.exec(
        select(PendingComparison).where(
            PendingComparison.draw_result_id == dr.id,
            PendingComparison.processed_at.is_(None),
        )
    ).first()
    if existing is None:
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


# /push-logs 已迁移至 app/api/admin_ext.py（6 维筛选 + 分页 envelope PushLogPageOut，
# spec §12.2 row 9）。此处旧版（裸 list）已删除，避免同 prefix 路由遮蔽新版。

