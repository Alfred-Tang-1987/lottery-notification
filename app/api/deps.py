"""Plan 05 / T3：FastAPI 依赖——current_user / require_admin。"""

from fastapi import Cookie, Depends, HTTPException, status
from sqlmodel import Session

from app.api.security import COOKIE_NAME, decode_session_token
from app.db.session import get_engine
from app.models import User


async def get_session_dep() -> Session:
    """FastAPI 依赖：每请求一个 SQLModel Session，请求结束自动关闭。"""
    with Session(get_engine()) as session:
        yield session


def current_user(
    session: Session = Depends(get_session_dep),
    token: str | None = Cookie(default=None, alias=COOKIE_NAME),
) -> User:
    """从 httpOnly session cookie 解析 JWT，并加载启用的用户。"""
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, '未登录')
    payload = decode_session_token(token)
    if payload is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, '会话失效')
    user = session.get(User, int(payload['sub']))
    if user is None or not user.enabled:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, '用户无效')
    return user


def require_admin(user: User = Depends(current_user)) -> User:
    """要求当前用户 role == admin。"""
    if user.role != 'admin':
        raise HTTPException(status.HTTP_403_FORBIDDEN, '需要管理员权限')
    return user
