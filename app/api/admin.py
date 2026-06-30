"""Plan 05 / T6：admin 后台 API。

提供用户列表、开奖结果 force-verify、系统健康、推送日志查询。
所有端点要求 admin 角色。
"""

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from app.api.deps import get_session_dep, require_admin
from app.models import User

router = APIRouter(prefix='/admin', tags=['admin'], dependencies=[Depends(require_admin)])


@router.get('/users')
def list_users(session: Session = Depends(get_session_dep)):
    return [
        {'id': u.id, 'username': u.username, 'role': u.role, 'enabled': u.enabled}
        for u in session.exec(select(User)).all()
    ]
