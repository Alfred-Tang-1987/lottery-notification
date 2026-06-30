"""Plan 05 / T3：FastAPI 依赖——current_user / require_admin / verify_csrf。"""

from fastapi import Cookie, Depends, Header, HTTPException, status
from sqlmodel import Session

from app.api.security import COOKIE_NAME, CSRF_HEADER, csrf_tokens_match, decode_session_token
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


def verify_csrf(
    csrf_cookie: str | None = Cookie(default=None, alias='csrf_token'),
    csrf_header: str | None = Header(default=None, alias=CSRF_HEADER),
) -> None:
    """CSRF double-submit 校验（spec §4.3）：X-CSRF-Token header 须与 csrf_token
    cookie 一致且非空，否则 403。

    挂载到已登录用户的 state-changing 路由（如 /auth/logout）。匿名进入端点
    （register/login）豁免——首次请求时尚无 csrf cookie，强制校验会形成鸡生蛋死锁。
    """
    if not csrf_tokens_match(csrf_cookie, csrf_header):
        raise HTTPException(status.HTTP_403_FORBIDDEN, 'CSRF token 无效')
