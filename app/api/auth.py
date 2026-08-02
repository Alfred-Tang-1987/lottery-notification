"""Plan 05 / T4：auth API（register/login/logout/csrf/me）。

Spec §4.3 / §6.1 / §6.2：
- httpOnly cookie + SameSite=Lax；CSRF token 经 GET /auth/csrf 取得（SPA 读不到
  httpOnly cookie，靠非 httpOnly 的 csrf_token cookie + X-CSRF-Token header 做
  double-submit）。已登录用户的 state-changing 路由（/auth/logout）挂
  ``app.api.deps.verify_csrf`` 强制校验 header 与 cookie 一致；匿名进入端点
  （/auth/register /auth/login）豁免——首次请求尚无 csrf cookie。
- 注册必须持有效邀请码（§6.2 单次/有效期/尝试锁定）。
- 密码 >= 8 字符（pydantic 校验）。

静默失败纪律（CLAUDE.md）：注册路径"建用户 + 占用邀请码"在同一事务内原子完成——
先建用户 flush 取 id，再用 InviteService.consume(code, user_id=...) 的
UPDATE...RETURNING 原子占用，失败即回滚（不建半截用户、不漏占码）。
"""

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.api.deps import current_user, get_session_dep, verify_csrf
from app.api.security import (
    COOKIE_NAME,
    create_session_token,
    generate_csrf_token,
    hash_password,
    verify_password,
)
from app.config import get_cors_origins, get_settings
from app.infrastructure.crypto import CryptoService
from app.models import User
from app.services.invite_service import InviteService
from app.services.password_reset_service import (
    PasswordResetService,
    RateLimited,
    RateLimiter,
    ResetRejected,
)

router = APIRouter(prefix='/auth', tags=['auth'])

# session cookie 7 天（与 create_session_token 默认 exp 对齐）。
_SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 7

# 注册用户的默认角色（与 User.role 字段注释 'user | admin' 对齐，避免魔法字符串
# 在多处重复——未来 admin 创建路径复用同一常量）。
_ROLE_USER = 'user'


class RegisterIn(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=128)
    invite_code: str = Field(min_length=6, max_length=6)


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


@router.get('/csrf')
def csrf(response: Response) -> dict[str, str]:
    """签发 CSRF token：返回体 + 非 httpOnly cookie（供 SPA 读取做 double-submit）。"""
    token = generate_csrf_token()
    # samesite=lax：跨站 POST 不带 cookie，配合 CSRF token 防 CSRF。
    # secure 从配置（生产 True 仅 HTTPS 传输；开发 http 关闭）——安全审查 #2。
    response.set_cookie(
        'csrf_token', token, httponly=False, samesite='lax', secure=get_settings().cookie_secure,
    )
    return {'csrf_token': token}


@router.post('/register', status_code=status.HTTP_201_CREATED)
def register(
    body: RegisterIn,
    session: Session = Depends(get_session_dep),
) -> dict[str, object]:
    """注册：校验邀请码 + 用户名唯一，同事务原子占用邀请码（§6.2 单次使用）。

    顺序约束（防 silent-failure）：
    1. 先判用户名冲突（早失败，不浪费 invite 尝试计数）。
    2. 建用户 flush 取 id，再 InviteService.consume(code, user_id=...) 原子占用
       （UPDATE...RETURNING，单次使用，避免 TOCTOU）。
    3. consume 失败（码不存在/已用/过期/锁定）：必须回滚丢弃半截用户（否则
       被拒注册仍落库用户行，占住 username → 后续合法注册误判冲突）；但防爆破
       的 attempts 计数不得丢——在独立 session 内重放一次"仅校验"consume
       （user_id=None）来累计 attempts/locked_at，再持久化。
    """
    # 用户名冲突先判（早失败，不浪费一次 invite consume 尝试计数）。
    if session.exec(select(User).where(User.username == body.username)).first():
        raise HTTPException(status.HTTP_409_CONFLICT, '用户名已存在')

    # 建用户并 flush 取 id。
    user = User(
        username=body.username,
        password_hash=hash_password(body.password),
        role=_ROLE_USER,
        invite_code=body.invite_code,
    )
    session.add(user)
    try:
        session.flush()
    except IntegrityError:
        # 并发：另一请求抢先建了同名用户（通过了上面的 select 预检）。回滚防
        # session 中毒（PendingRollback），返回 409 而非裸 500。
        session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, '用户名已存在') from None

    engine = session.get_bind()
    invite = InviteService(engine)
    claimed = invite.consume(body.invite_code, user_id=user.id, session=session)
    if claimed is None:
        # 回滚丢弃半截用户（防 username 占位 silent-failure）。
        session.rollback()
        # 在独立事务内累计防爆破 attempts（consume(user_id=None) 只校验+计数）。
        # 码不存在时 consume 立即返回 None 不写行，无需额外处理。
        _record_failed_invite_attempt(engine, body.invite_code, invite)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, '邀请码无效或已使用')

    session.commit()
    session.refresh(user)
    return {'id': user.id, 'username': user.username}


def _record_failed_invite_attempt(engine: Engine, code: str, invite: InviteService) -> None:
    """对真实存在的码累计一次失败尝试（防爆破），独立事务持久化。

    注册流程在主 session 回滚后调用：用全新 session 重放 consume(user_id=None)，
    让 attempts 递增 / 超限锁定并 commit。码不存在时 consume 立即返回 None 不写行，
    此处无副作用。
    """
    with Session(engine) as s:
        invite.consume(code, user_id=None, session=s)
        s.commit()


@router.post('/login')
def login(
    body: LoginIn,
    response: Response,
    session: Session = Depends(get_session_dep),
    origin: str | None = Header(default=None, alias='Origin'),
) -> dict[str, object]:
    """登录：校验密码，签发 JWT 并写入 httpOnly session cookie。

    Login CSRF 缓解（安全审查 #3）：浏览器跨站请求必带 Origin header——有 Origin 则
    须在 cors_origins allow-list，否则 403（防 forced-login 到攻击者账号）。无 Origin
    放行（同源浏览器 fetch 默认带 Origin；缺 Origin 是同源工具/TestClient）。
    """
    if origin and origin not in get_cors_origins():
        raise HTTPException(status.HTTP_403_FORBIDDEN, '跨站登录被拒')
    user = session.exec(select(User).where(User.username == body.username)).first()
    # 用户不存在与密码错误统一返回 401（避免用户名枚举）。
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, '用户名或密码错误')
    token = create_session_token(user_id=user.id, role=user.role)
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        samesite='lax',
        secure=get_settings().cookie_secure,
        max_age=_SESSION_MAX_AGE_SECONDS,
    )
    return {'id': user.id, 'username': user.username, 'role': user.role}


@router.post('/logout')
def logout(
    response: Response,
    _csrf_ok: None = Depends(verify_csrf),
) -> dict[str, bool]:
    """登出：删除 session cookie（JWT 无服务端状态，删 cookie 即失效）。

    已登录 state-changing 路由——挂 verify_csrf 强制 double-submit（spec §4.3），
    防 CSRF 伪造登出。/auth/register /auth/login 豁免（匿名进入端点，首次无 csrf cookie）。
    """
    response.delete_cookie(COOKIE_NAME)
    return {'ok': True}


@router.get('/me')
def me(user: User = Depends(current_user)) -> dict[str, object]:
    """返回当前登录用户信息（未登录 → current_user 抛 401）。"""
    return {'id': user.id, 'username': user.username, 'role': user.role}


# ---- Plan 08：忘记密码 ----

_FORGOT_UNIFORM_MSG = '若账号存在，验证码已发送至你的邮箱'
_RESET_UNIFORM_ERR = '验证码错误或已过期'


class ForgotPasswordIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)


class ResetPasswordIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    code: str = Field(min_length=6, max_length=6, pattern=r'^\d{6}$')
    new_password: str = Field(min_length=8, max_length=128)


def _reset_service(request: Request, engine: Engine) -> PasswordResetService:
    """从 app.state 取已构造 channels/crypto 组装 service（autoplan A3：不 new 渠道）。

    限流器挂 app.state（进程级单例，跨请求累计）；首次访问惰性创建。
    """
    channels = getattr(request.app.state, 'channels', None) or {}
    crypto = getattr(request.app.state, 'crypto', None)
    if crypto is None:  # 防御：测试/非常规启动路径下 app.state 未接线
        settings = get_settings()
        crypto = CryptoService(settings.crypto_keys, settings.current_key_version)
    limiter = getattr(request.app.state, 'password_reset_limiter', None)
    if limiter is None:
        limiter = RateLimiter(max_per_minute=3)
        request.app.state.password_reset_limiter = limiter
    admin_alert = _build_admin_alert()
    return PasswordResetService(
        engine,
        email_channel=channels.get('email'),
        crypto=crypto,
        rate_limiter=limiter,
        admin_alert=admin_alert,
    )


def _build_admin_alert():
    """admin Bark 告警（autoplan C1）：复用 ADMIN_BARK_KEY，未配则 None。"""
    key = get_settings().admin_bark_key
    if not key:
        return None
    from app.notifications.bark import BarkChannel
    from app.notifications.base import NotificationPayload
    bark = BarkChannel()
    config = {'key': key, 'url': 'https://api.day.app'}

    def _alert(title: str, body: str) -> None:
        bark.send(NotificationPayload(title=title, body=body), config)

    return _alert


@router.post('/forgot-password')
def forgot_password(
    body: ForgotPasswordIn,
    request: Request,
    session: Session = Depends(get_session_dep),
) -> dict[str, object]:
    """忘记密码第一步：发验证码到用户 email 渠道。

    统一话术防枚举（autoplan A1）：用户不存在/无 email 渠道/SMTP 未配/60s 内
    重发/send 失败——全部返回逐字相同的 200。仅 IP 超限 429。
    匿名进入端点，豁免 CSRF（同 register/login）。
    """
    client_ip = request.client.host if request.client else 'unknown'
    svc = _reset_service(request, session.get_bind())
    try:
        svc.request_reset(body.username, client_ip=client_ip, session=session)
    except RateLimited:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, '请求过于频繁，请 1 分钟后再试'
        ) from None
    return {'ok': True, 'message': _FORGOT_UNIFORM_MSG}


@router.post('/reset-password')
def reset_password(
    body: ResetPasswordIn,
    request: Request,
    session: Session = Depends(get_session_dep),
    origin: str | None = Header(default=None, alias='Origin'),
) -> dict[str, object]:
    """忘记密码第二步：验证码 + 新密码。

    Origin 校验（autoplan A5，对齐 login）：reset 是匿名 state-changing 端点，
    跨站 Origin 拒 403——阻断「CSRF + 验证码泄露 → 接管账号」链路。
    失败统一 400 文案（码错/过期/超 attempts/用户不存在——防枚举）。
    """
    if origin and origin not in get_cors_origins():
        raise HTTPException(status.HTTP_403_FORBIDDEN, '跨站请求被拒')
    svc = _reset_service(request, session.get_bind())
    try:
        svc.verify_and_reset(
            body.username, body.code, body.new_password, session=session
        )
    except ResetRejected:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, _RESET_UNIFORM_ERR) from None
    return {'ok': True}
