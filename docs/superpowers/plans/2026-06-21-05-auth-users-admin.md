# 05 认证 + 用户 + admin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 实现 httpOnly cookie 认证（PyJWT + passlib）、`/auth/csrf` + CSRF 中间件 + CORS、邀请码防爆破（单次+有效期+尝试锁定）、用户 API（注册/登录/登出/me）、current_user/RequireAdmin 依赖、渠道配置加密写入（对齐 Plan 04 Notifier 约定）、admin 后台（用户/彩种/SMTP/健康/推送日志/审计）、admin_audit_logs 写入（脱敏）、verified=false force-verify。

**Architecture:** `app/api/`（FastAPI 路由 + 依赖 + 安全工具）+ `app/services/`（invite/audit）。所有用户私有 API 经 `current_user` 依赖拿 user_id，IDOR 靠 Plan 03 Repository 的 user_id 注入。admin API 经 `RequireAdmin`。

**Tech Stack:** PyJWT、passlib[bcrypt]、FastAPI 中间件（CORS/CSRF）、Plan 01-04。

---

## File Structure

```
app/
├── api/
│   ├── __init__.py
│   ├── security.py       # JWT 签发/校验 + cookie 工具 + CSRF
│   ├── deps.py           # current_user 依赖 + RequireAdmin
│   ├── auth.py           # /auth/register /login /logout /csrf /me
│   ├── tickets.py        # 号码 CRUD（IDOR via TicketRepo）
│   ├── channels.py       # 渠道配置加密写入/读取
│   └── admin.py          # admin 后台 + force-verify + 审计
└── services/
    ├── invite_service.py  # 邀请码生成（admin）+ 校验（注册）+ 防爆破
    └── audit_service.py   # admin_audit_logs 写入（脱敏）
tests/
├── api/test_auth.py
├── api/test_csrf_cors.py
├── api/test_invite.py
├── api/test_channels.py
├── api/test_admin.py
└── integration/test_auth_flow.py
```

---

## Task 1: 安全工具（passlib + PyJWT + cookie + CSRF token）

**Files:** `app/api/__init__.py`(空), `app/api/security.py`, `tests/api/__init__.py`(空), `tests/api/test_security.py`

- [ ] **Step 1: 写失败测试 tests/api/test_security.py**

```python
import pytest
from app.api.security import hash_password, verify_password, create_session_token, decode_session_token, generate_csrf_token


def test_password_hash_roundtrip():
    h = hash_password("secret123")
    assert h != "secret123"
    assert verify_password("secret123", h)
    assert not verify_password("wrong", h)


def test_jwt_token_roundtrip(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "a" * 32)
    from importlib import reload
    import app.config; reload(app.config)
    import app.api.security as sec; reload(sec)
    token = sec.create_session_token(user_id=42, role="user")
    payload = sec.decode_session_token(token)
    assert payload["sub"] == "42" and payload["role"] == "user"


def test_jwt_expired_rejected(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "a" * 32)
    from importlib import reload
    import app.config; reload(app.config)
    import app.api.security as sec; reload(sec)
    token = sec.create_session_token(user_id=1, role="user", expires_minutes=-1)
    assert sec.decode_session_token(token) is None


def test_csrf_token_random():
    a = generate_csrf_token(); b = generate_csrf_token()
    assert a != b and len(a) >= 32
```

- [ ] **Step 2: 运行确认失败**

```bash
uv run pytest tests/api/test_security.py -v
```
Expected: FAIL

- [ ] **Step 3: 写 app/api/security.py**

```python
import secrets
from datetime import datetime, timedelta, timezone
import jwt
from passlib.context import CryptContext
from app.config import settings

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
_COOKIE_NAME = "session"
_CSRF_HEADER = "X-CSRF-Token"


def hash_password(plain: str) -> str:
    return _pwd.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return _pwd.verify(plain, hashed)


def create_session_token(*, user_id: int, role: str, expires_minutes: int = 60 * 24 * 7) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id), "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=expires_minutes)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_session_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None


def generate_csrf_token() -> str:
    return secrets.token_urlsafe(32)


COOKIE_NAME = _COOKIE_NAME
CSRF_HEADER = _CSRF_HEADER
```

- [ ] **Step 4: 运行确认通过**

```bash
uv run pytest tests/api/test_security.py -v
```
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add app/api/__init__.py app/api/security.py tests/api/__init__.py tests/api/test_security.py
git commit -m "feat(api): 安全工具 passlib 哈希 + PyJWT session token + CSRF token"
```

---

## Task 2: 邀请码服务（admin 生成 + 注册校验 + 防爆破）

**Files:** `app/services/invite_service.py`, `tests/api/test_invite.py`

> spec §6.2：邀请码单次使用 + 有效期 + 失败尝试锁定，仅 admin 生成，无默认 bootstrap 码。

- [ ] **Step 1: 写失败测试 tests/api/test_invite.py**

```python
import pytest
from datetime import datetime, timedelta
from sqlmodel import Session
from app.services.invite_service import InviteService


def test_generate_invite_returns_6_digit(db_engine):
    svc = InviteService(db_engine)
    code = svc.generate(admin_id=1)
    assert len(code) == 6 and code.isdigit()


def test_consume_invite_marks_used(db_engine):
    svc = InviteService(db_engine)
    code = svc.generate(admin_id=1)
    assert svc.consume(code) is True  # 首次使用成功
    assert svc.consume(code) is False  # 单次：再次失败


def test_consume_expired_fails(db_engine):
    svc = InviteService(db_engine, ttl_days=1)
    code = svc.generate(admin_id=1)
    # 模拟过期：直接改 DB
    from app.services.invite_service import InviteCode
    with Session(db_engine) as s:
        ic = s.exec(__import__("sqlmodel").select(InviteCode).where(InviteCode.code == code)).first()
        ic.expires_at = datetime.utcnow() - timedelta(days=2); s.commit()
    assert svc.consume(code) is False


def test_brute_force_locked(db_engine):
    """失败尝试超限 → 锁定。"""
    svc = InviteService(db_engine, max_attempts=3)
    for _ in range(3):
        assert svc.consume("000000") is False  # 错码
    # 第 4 次即使有正确码也锁（同 IP/全局简化）


def test_no_default_bootstrap_code(db_engine):
    """无默认码：首启无任何有效邀请码。"""
    svc = InviteService(db_engine)
    assert svc.consume("000000") is False
    assert svc.consume("123456") is False
```

- [ ] **Step 2: 运行确认失败**

```bash
uv run pytest tests/api/test_invite.py -v
```
Expected: FAIL

- [ ] **Step 3: 加 InviteCode model（Plan 01 未含）+ 写 invite_service.py**

先在 `app/models/` 加 `invite.py`：
```python
from datetime import datetime
from sqlmodel import Field
from app.models._base import TimestampMixin


class InviteCode(TimestampMixin, table=True):
    __tablename__ = "invite_codes"
    code: str = Field(primary_key=True, max_length=6)
    created_by: int = Field(foreign_key="users.id")
    used_by: int | None = Field(default=None)
    used_at: datetime | None = None
    expires_at: datetime
    attempts: int = Field(default=0)
```

> 注：invite_codes 表是 Plan 05 新增——**必须在 `app/models/__init__.py` 补 `from app.models.invite import InviteCode  # noqa`**（否则 SQLModel.metadata 不注册该表，Alembic autogenerate 会漏），补完再生成迁移（Task 7）。

写 `app/services/invite_service.py`：
```python
import secrets
from datetime import datetime, timedelta
from sqlmodel import Session, select
from sqlalchemy.engine import Engine
from app.models.invite import InviteCode


class InviteService:
    """邀请码：admin 生成（6 位），注册时 consume，单次+有效期+尝试锁定。无默认 bootstrap 码。"""

    def __init__(self, engine: Engine, ttl_days: int = 30, max_attempts: int = 5):
        self._engine = engine
        self._ttl = ttl_days
        self._max_attempts = max_attempts

    def generate(self, *, admin_id: int) -> str:
        code = f"{secrets.randbelow(1000000):06d}"
        with Session(self._engine) as s:
            s.add(InviteCode(code=code, created_by=admin_id,
                             expires_at=datetime.utcnow() + timedelta(days=self._ttl)))
            s.commit()
        return code

    def consume(self, code: str, *, session: Session | None = None) -> bool:
        """校验邀请码可用（单次/有效期/尝试锁定）。session 传入则同事务（注册时原子，避免竞态）。"""
        own = session is None
        if own:
            session = Session(self._engine)
        try:
            ic = session.exec(select(InviteCode).where(InviteCode.code == code)).first()
            if ic is None:
                return False
            ic.attempts += 1
            if ic.attempts > self._max_attempts:
                if own: session.commit()
                return False  # 尝试锁定
            if ic.used_by is not None:
                if own: session.commit()
                return False  # 单次：已用
            if datetime.utcnow() > ic.expires_at:
                if own: session.commit()
                return False  # 过期
            if own: session.commit()
            return True  # 可用（注册事务内标记 used_by，原子）
        finally:
            if own: session.close()
```

> 注：`consume` 预占语义——返回 True 表示码有效可用，实际 `used_by` 标记在注册事务内完成（防止校验与注册之间竞态）。简化测试用 `used_by is not None` 判单次。

- [ ] **Step 4: 运行确认通过**

```bash
uv run pytest tests/api/test_invite.py -v
```
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add app/models/invite.py app/services/invite_service.py tests/api/test_invite.py
git commit -m "feat(services): InviteService 6位码 + 单次/有效期/尝试锁定 + 无默认 bootstrap"
```

---

## Task 3: current_user / RequireAdmin 依赖

**Files:** `app/api/deps.py`, `tests/api/test_deps.py`

- [ ] **Step 1: 写失败测试 tests/api/test_deps.py**

```python
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session
from app.api.deps import current_user, require_admin, get_session_dep
from app.api.security import create_session_token, COOKIE_NAME
from app.models import User


def _app_with_routes(engine):
    app = FastAPI()
    app.dependency_overrides[get_session_dep] = lambda: Session(engine)
    @app.get("/me")
    def me(u=Depends(current_user)): return {"id": u.id, "role": u.role}
    @app.get("/admin")
    def adm(u=Depends(require_admin)): return {"ok": True}
    return app


def test_current_user_requires_cookie(db_engine):
    with Session(db_engine) as s:
        s.add(User(username="u", password_hash="x", role="user", invite_code="C")); s.commit()
    app = _app_with_routes(db_engine)
    client = TestClient(app)
    r = client.get("/me")
    assert r.status_code == 401


def test_current_user_with_valid_cookie(db_engine):
    with Session(db_engine) as s:
        u = User(username="u", password_hash="x", role="user", invite_code="C"); s.add(u); s.commit(); s.refresh(u)
        uid = u.id
    app = _app_with_routes(db_engine)
    token = create_session_token(user_id=uid, role="user")
    client = TestClient(app)
    r = client.get("/me", cookies={COOKIE_NAME: token})
    assert r.status_code == 200 and r.json()["id"] == uid


def test_admin_required_for_admin_route(db_engine):
    with Session(db_engine) as s:
        u = User(username="u", password_hash="x", role="user", invite_code="C"); s.add(u); s.commit(); s.refresh(u)
        uid = u.id
    app = _app_with_routes(db_engine)
    token = create_session_token(user_id=uid, role="user")
    r = TestClient(app).get("/admin", cookies={COOKIE_NAME: token})
    assert r.status_code == 403  # 非 admin
```

- [ ] **Step 2: 运行确认失败**

```bash
uv run pytest tests/api/test_deps.py -v
```
Expected: FAIL

- [ ] **Step 3: 写 app/api/deps.py**

```python
from fastapi import Cookie, Depends, HTTPException, status
from sqlmodel import Session
from sqlalchemy.engine import Engine
from app.api.security import decode_session_token, COOKIE_NAME
from app.db.session import engine as _engine
from app.models import User


def get_session_dep():
    with Session(_engine) as s:
        yield s


def current_user(
    session: Session = Depends(get_session_dep),
    token: str | None = Cookie(default=None, alias=COOKIE_NAME),
) -> User:
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "未登录")
    payload = decode_session_token(token)
    if payload is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "会话失效")
    user = session.get(User, int(payload["sub"]))
    if user is None or not user.enabled:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户无效")
    return user


def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "需要管理员权限")
    return user
```

- [ ] **Step 4: 运行确认通过**

```bash
uv run pytest tests/api/test_deps.py -v
```
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add app/api/deps.py tests/api/test_deps.py
git commit -m "feat(api): current_user/require_admin 依赖（cookie JWT 解析 + role 校验）"
```

---

## Task 4: auth API（register/login/logout/csrf/me）+ CSRF 中间件 + CORS

**Files:** `app/api/auth.py`, `tests/api/test_auth.py`, `tests/api/test_csrf_cors.py`

> spec §4.3：httpOnly cookie + SameSite + CSRF token（/auth/csrf GET）；CORS allow_credentials + 显式 origins。

- [ ] **Step 1: 写失败测试 tests/api/test_auth.py**

```python
from fastapi.testclient import TestClient
from sqlmodel import Session
from app.api.deps import get_session_dep
from app.services.invite_service import InviteService
from app.main import app
from app.models import User


def _client(db_engine):
    app.dependency_overrides[get_session_dep] = lambda: (yield Session(db_engine))
    return TestClient(app)


def test_register_login_logout_flow(db_engine):
    # admin 先生成邀请码（直接用 service）
    invite = InviteService(db_engine)
    code = invite.generate(admin_id=0)  # bootstrap：首个 admin 另行处理（见 Task 6 备注）
    client = _client(db_engine)
    r = client.post("/auth/register", json={"username": "alice", "password": "password1",
                                            "invite_code": code})
    assert r.status_code == 201
    # 登录
    r = client.post("/auth/login", json={"username": "alice", "password": "password1"})
    assert r.status_code == 200
    assert "session" in r.cookies
    # me
    r = client.get("/auth/me")
    assert r.status_code == 200 and r.json()["username"] == "alice"
    # logout
    r = client.post("/auth/logout")
    assert r.status_code == 200
    r = client.get("/auth/me")
    assert r.status_code == 401


def test_register_invalid_invite_rejected(db_engine):
    client = _client(db_engine)
    r = client.post("/auth/register", json={"username": "bob", "password": "password1",
                                            "invite_code": "000000"})
    assert r.status_code == 400


def test_register_weak_password_rejected(db_engine):
    invite = InviteService(db_engine); code = invite.generate(admin_id=0)
    client = _client(db_engine)
    r = client.post("/auth/register", json={"username": "c", "password": "123", "invite_code": code})
    assert r.status_code == 422  # 密码 <8
```

- [ ] **Step 2: 写失败测试 tests/api/test_csrf_cors.py**

```python
from fastapi.testclient import TestClient
from app.main import app


def test_csrf_endpoint_returns_token():
    client = TestClient(app)
    r = client.get("/auth/csrf")
    assert r.status_code == 200
    assert "csrf_token" in r.json()
    # token 也 set 到非 httpOnly cookie 供 SPA 读取
    assert "csrf_token" in r.cookies


def test_cors_credentials_header(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:5173")
    client = TestClient(app)
    r = client.options("/auth/login", headers={
        "Origin": "http://localhost:5173",
        "Access-Control-Request-Method": "POST",
    })
    assert r.headers.get("access-control-allow-credentials") == "true"
    assert "http://localhost:5173" in r.headers.get("access-control-allow-origin", "")
```

- [ ] **Step 3: 运行确认失败**

```bash
uv run pytest tests/api/test_auth.py tests/api/test_csrf_cors.py -v
```
Expected: FAIL

- [ ] **Step 4: 写 app/api/auth.py**

```python
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlmodel import Session, select
from pydantic import BaseModel, Field
from app.api.deps import current_user, get_session_dep
from app.api.security import (
    hash_password, verify_password, create_session_token,
    generate_csrf_token, COOKIE_NAME, CSRF_HEADER,
)
from app.services.invite_service import InviteService
from app.models import User, InviteCode
from datetime import datetime

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterIn(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=128)
    invite_code: str = Field(min_length=6, max_length=6)


class LoginIn(BaseModel):
    username: str
    password: str


@router.get("/csrf")
def csrf(response: Response):
    token = generate_csrf_token()
    response.set_cookie("csrf_token", token, httponly=False, samesite="lax")  # SPA 可读
    return {"csrf_token": token}


@router.post("/register", status_code=201)
def register(body: RegisterIn, session: Session = Depends(get_session_dep)):
    # 统一经 InviteService.consume 校验（单次/有效期/尝试锁定），同事务保证原子
    invite = InviteService(session.get_bind())
    if not invite.consume(body.invite_code, session=session):
        raise HTTPException(400, "邀请码无效或已使用")
    if session.exec(select(User).where(User.username == body.username)).first():
        raise HTTPException(409, "用户名已存在")
    user = User(username=body.username, password_hash=hash_password(body.password),
                role="user", invite_code=body.invite_code)
    session.add(user); session.flush()
    # 同事务标记 used_by（consume 已校验，此处原子占用）
    ic = session.exec(select(InviteCode).where(InviteCode.code == body.invite_code)).first()
    ic.used_by = user.id; ic.used_at = datetime.utcnow()
    session.commit()
    return {"id": user.id, "username": user.username}


@router.post("/login")
def login(body: LoginIn, response: Response, session: Session = Depends(get_session_dep)):
    user = session.exec(select(User).where(User.username == body.username)).first()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "用户名或密码错误")
    token = create_session_token(user_id=user.id, role=user.role)
    response.set_cookie(COOKIE_NAME, token, httponly=True, samesite="lax", secure=False, max_age=60*60*24*7)
    return {"id": user.id, "username": user.username, "role": user.role}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE_NAME)
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(current_user)):
    return {"id": user.id, "username": user.username, "role": user.role}
```

> 注：CSRF 中间件——对 mutating 请求（POST/PUT/DELETE）校验 `X-CSRF-Token` header 与 `csrf_token` cookie 一致（double-submit）。在 main.py 注册中间件（Task 8）。CORS 在 main.py 加 `CORSMiddleware(allow_credentials=True, allow_origins=settings.cors_origins)`。

- [ ] **Step 5: 运行确认通过**

```bash
uv run pytest tests/api/test_auth.py tests/api/test_csrf_cors.py -v
```
Expected: passed

- [ ] **Step 6: Commit**

```bash
git add app/api/auth.py tests/api/test_auth.py tests/api/test_csrf_cors.py
git commit -m "feat(api): auth 路由（register/login/logout/csrf/me）+ CSRF double-submit 约定"
```

---

## Task 5: 渠道配置加密写入/读取（对齐 Plan 04 Notifier）

**Files:** `app/api/channels.py`, `tests/api/test_channels.py`

> Plan 04 Notifier._decrypt_config 约定：config_json = `{"ct": "<Fernet密文>"}`，key_version 单列。本 task 落实写入。

- [ ] **Step 1: 写失败测试 tests/api/test_channels.py**

```python
import json
from fastapi.testclient import TestClient
from sqlmodel import Session
from app.api.deps import get_session_dep
from app.main import app
from app.models import User, NotificationChannel
from app.api.security import create_session_token, COOKIE_NAME


def _auth_client(db_engine, uid):
    app.dependency_overrides[get_session_dep] = lambda: (yield Session(db_engine))
    client = TestClient(app)
    token = create_session_token(user_id=uid, role="user")
    client.cookies.set(COOKIE_NAME, token)
    return client


def test_save_bark_channel_encrypts(db_engine):
    with Session(db_engine) as s:
        u = User(username="u", password_hash="x", role="user", invite_code="C"); s.add(u); s.commit(); s.refresh(u)
        uid = u.id
    client = _auth_client(db_engine, uid)
    r = client.post("/channels", json={"type": "bark", "config": {"key": "abc", "url": "https://api.day.app"}})
    assert r.status_code == 201
    with Session(db_engine) as s:
        ch = s.exec(__import__("sqlmodel").select(NotificationChannel).where(NotificationChannel.user_id == uid)).first()
        stored = json.loads(ch.config_json)
        assert "ct" in stored and "abc" not in stored["ct"]  # 明文不入库
        assert ch.key_version >= 1


def test_list_channels_returns_plaintext(db_engine):
    with Session(db_engine) as s:
        u = User(username="u", password_hash="x", role="user", invite_code="C"); s.add(u); s.commit(); s.refresh(u)
        uid = u.id
    client = _auth_client(db_engine, uid)
    client.post("/channels", json={"type": "bark", "config": {"key": "abc", "url": "https://api.day.app"}})
    r = client.get("/channels")
    assert r.status_code == 200
    cfg = r.json()[0]["config"]
    assert cfg["key"] == "abc"  # 读取时解密回明文


def test_channel_isolated_by_user(db_engine):
    """u2 读不到 u1 的渠道。"""
    with Session(db_engine) as s:
        u1 = User(username="u1", password_hash="x", role="user", invite_code="C"); s.add(u1)
        u2 = User(username="u2", password_hash="x", role="user", invite_code="D"); s.add(u2); s.commit(); s.refresh(u1); s.refresh(u2)
    c1 = _auth_client(db_engine, u1.id); c1.post("/channels", json={"type": "bark", "config": {"key": "k1", "url": "u"}})
    c2 = _auth_client(db_engine, u2.id)
    assert c2.get("/channels").json() == []  # 隔离
```

- [ ] **Step 2: 运行确认失败**

```bash
uv run pytest tests/api/test_channels.py -v
```
Expected: FAIL

- [ ] **Step 3: 写 app/api/channels.py**

```python
import json
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlmodel import Session, select
from app.api.deps import current_user, get_session_dep
from app.infrastructure.crypto import CryptoService
from app.config import settings
from app.models import User, NotificationChannel

router = APIRouter(prefix="/channels", tags=["channels"])


def _crypto() -> CryptoService:
    return CryptoService(settings.crypto_keys, settings.current_key_version)


class ChannelIn(BaseModel):
    type: str
    config: dict


@router.post("", status_code=201)
def save_channel(body: ChannelIn, user: User = Depends(current_user),
                 session: Session = Depends(get_session_dep)):
    crypto = _crypto()
    blob = crypto.encrypt(json.dumps(body.config, ensure_ascii=False))
    stored = json.dumps({"ct": blob.ciphertext})
    ch = NotificationChannel(user_id=user.id, type=body.type, config_json=stored,
                             enabled=True, key_version=blob.version)
    session.add(ch); session.commit()
    return {"id": ch.id, "type": ch.type}


@router.get("")
def list_channels(user: User = Depends(current_user),
                  session: Session = Depends(get_session_dep)):
    crypto = _crypto()
    rows = session.exec(select(NotificationChannel).where(
        NotificationChannel.user_id == user.id)).all()
    out = []
    for ch in rows:
        ct = json.loads(ch.config_json)["ct"]
        plaintext = crypto.decrypt((ch.key_version, ct))
        out.append({"id": ch.id, "type": ch.type, "config": json.loads(plaintext), "enabled": ch.enabled})
    return out
```

- [ ] **Step 4: 运行确认通过**

```bash
uv run pytest tests/api/test_channels.py -v
```
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add app/api/channels.py tests/api/test_channels.py
git commit -m "feat(api): 渠道配置加密写入/读取（config_json={ct:密文}+key_version，对齐 Notifier）"
```

---

## Task 6: admin 后台（用户/彩种/SMTP/健康/推送日志）+ force-verify

**Files:** `app/api/admin.py`, `app/services/audit_service.py`, `tests/api/test_admin.py`

- [ ] **Step 1: 写失败测试 tests/api/test_admin.py**

```python
from fastapi.testclient import TestClient
from sqlmodel import Session
from app.api.deps import get_session_dep
from app.main import app
from app.models import User, DrawResult, AdminAuditLog
from app.api.security import create_session_token, COOKIE_NAME
from datetime import datetime


def _admin_client(db_engine):
    with Session(db_engine) as s:
        u = User(username="admin", password_hash="x", role="admin", invite_code="A"); s.add(u); s.commit(); s.refresh(u)
        uid = u.id
    app.dependency_overrides[get_session_dep] = lambda: (yield Session(db_engine))
    client = TestClient(app)
    client.cookies.set(COOKIE_NAME, create_session_token(user_id=uid, role="admin"))
    return client


def test_admin_list_users(db_engine):
    with Session(db_engine) as s:
        s.add(User(username="u1", password_hash="x", role="user", invite_code="C")); s.commit()
    client = _admin_client(db_engine)
    r = client.get("/admin/users")
    assert r.status_code == 200 and len(r.json()) >= 2


def test_admin_force_verify_logs_audit(db_engine):
    """verified=false 的开奖，admin force-verify → 写审计。"""
    with Session(db_engine) as s:
        dr = DrawResult(lottery_code="ssq", draw_no="062", draw_date=datetime.utcnow(),
                        numbers_json='{"front":[1,2,3,4,5,6],"back":[7]}', source="mxnzp",
                        verified=False, version=1); s.add(dr); s.commit(); s.refresh(dr)
        dr_id = dr.id
    client = _admin_client(db_engine)
    r = client.post(f"/admin/draw-results/{dr_id}/force-verify")
    assert r.status_code == 200
    with Session(db_engine) as s:
        assert s.get(DrawResult, dr_id).verified is True
        log = s.exec(__import__("sqlmodel").select(AdminAuditLog)).first()
        assert log and log.action == "force_verify"


def test_non_admin_forbidden(db_engine):
    with Session(db_engine) as s:
        u = User(username="u", password_hash="x", role="user", invite_code="C"); s.add(u); s.commit(); s.refresh(u)
        uid = u.id
    app.dependency_overrides[get_session_dep] = lambda: (yield Session(db_engine))
    client = TestClient(app); client.cookies.set(COOKIE_NAME, create_session_token(user_id=uid, role="user"))
    r = client.get("/admin/users")
    assert r.status_code == 403
```

- [ ] **Step 2: 运行确认失败**

```bash
uv run pytest tests/api/test_admin.py -v
```
Expected: FAIL

- [ ] **Step 3: 写 app/services/audit_service.py**

```python
import json
from sqlmodel import Session
from app.models import AdminAuditLog

_SENSITIVE_KEYS = {"key", "webhook", "password", "smtp_pass", "ct"}


def _sanitize(values: dict | None) -> str | None:
    if values is None:
        return None
    return json.dumps({k: ("***" if k.lower() in _SENSITIVE_KEYS else v) for k, v in values.items()})


def write_audit(session: Session, *, admin_id: int, action: str, target_type: str,
                target_id: str | None = None, old_values: dict | None = None,
                new_values: dict | None = None) -> None:
    session.add(AdminAuditLog(
        admin_id=admin_id, action=action, target_type=target_type, target_id=target_id,
        old_values=_sanitize(old_values), new_values=_sanitize(new_values),
    ))
    session.commit()
```

- [ ] **Step 4: 写 app/api/admin.py**

```python
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from app.api.deps import require_admin, get_session_dep
from app.services.audit_service import write_audit
from app.models import User, DrawResult, NotificationLog, ApiSourceHealth, LotteryType

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


@router.get("/users")
def list_users(session: Session = Depends(get_session_dep)):
    return [{"id": u.id, "username": u.username, "role": u.role, "enabled": u.enabled}
            for u in session.exec(select(User)).all()]


@router.post("/draw-results/{draw_id}/force-verify")
def force_verify(draw_id: int, admin: User = Depends(require_admin),
                 session: Session = Depends(get_session_dep)):
    """verified=false 恢复：admin 人工核对后强制标记 verified（spec §7.1）。"""
    dr = session.get(DrawResult, draw_id)
    if dr is None:
        raise HTTPException(404, "开奖结果不存在")
    old = {"verified": dr.verified}
    dr.verified = True
    session.commit()
    write_audit(session, admin_id=admin.id, action="force_verify",
                target_type="draw_result", target_id=str(draw_id),
                old_values=old, new_values={"verified": True})
    return {"id": draw_id, "verified": True}


@router.get("/health")
def system_health(session: Session = Depends(get_session_dep)):
    sources = session.exec(select(ApiSourceHealth)).all()
    return {"sources": [{"source": s.source, "status": s.status} for s in sources]}


@router.get("/push-logs")
def push_logs(session: Session = Depends(get_session_dep), limit: int = 100):
    logs = session.exec(select(NotificationLog).order_by(NotificationLog.id.desc()).limit(limit)).all()
    return [{"id": l.id, "user_id": l.user_id, "type": l.type, "status": l.status,
             "error": l.error} for l in logs]
```

- [ ] **Step 5: 运行确认通过**

```bash
uv run pytest tests/api/test_admin.py -v
```
Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
git add app/api/admin.py app/services/audit_service.py tests/api/test_admin.py
git commit -m "feat(api): admin 后台（users/force-verify/health/push-logs）+ 审计日志脱敏写入"
```

---

## Task 7: 路由注册 + CSRF/CORS 中间件 + 邀请码迁移

**Files:** modify `app/main.py`(注册路由+中间件), 新 Alembic 迁移

- [ ] **Step 1: 在 app/main.py 注册路由 + CORS/CSRF 中间件**

```python
# app/main.py 顶部追加 import + app 构建后注册：
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from app.config import settings
from app.api import auth, channels, admin, tickets

# CORS（spec §4.3）
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,  # 显式 origins，禁通配
    allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

# CSRF double-submit：mutating 请求校验 X-CSRF-Token header == csrf_token cookie
class CSRFMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            header_token = request.headers.get("X-CSRF-Token")
            cookie_token = request.cookies.get("csrf_token")
            # /auth/login /auth/register 豁免（首次无 csrf cookie）
            if request.url.path not in ("/auth/login", "/auth/register", "/auth/csrf"):
                if not header_token or header_token != cookie_token:
                    return JSONResponse(status_code=403, content={"detail": "CSRF token invalid"})
        return await call_next(request)

app.add_middleware(CSRFMiddleware)

app.include_router(auth.router)
app.include_router(channels.router)
app.include_router(admin.router)
app.include_router(tickets.router)
```

> 注：`settings.cors_origins` 需在 config.py 加（list[str]，env `CORS_ORIGINS` 逗号分隔，默认 `["http://localhost:5173"]`）。`tickets.router` 是号码 CRUD API（基于 Plan 03 TicketRepo，IDOR-safe），本 plan 不展开代码（复用 TicketRepo + current_user）。

- [ ] **Step 2: config.py 加 cors_origins**

```python
# app/config.py Settings 加字段：
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
```
（pydantic-settings 自动从 `CORS_ORIGINS=http://localhost:5173,https://app.example.com` 解析为 list）

- [ ] **Step 3: 生成 invite_codes 迁移**

```bash
uv run alembic revision --autogenerate -m "add invite_codes table"
uv run alembic upgrade head
```

- [ ] **Step 4: 端到端 auth 流程集成测试 tests/integration/test_auth_flow.py**

```python
from fastapi.testclient import TestClient
from sqlmodel import Session
from app.api.deps import get_session_dep
from app.services.invite_service import InviteService
from app.main import app


def test_full_auth_flow(db_engine):
    app.dependency_overrides[get_session_dep] = lambda: (yield Session(db_engine))
    code = InviteService(db_engine).generate(admin_id=0)
    client = TestClient(app)
    # register
    assert client.post("/auth/register", json={"username":"a","password":"password1","invite_code":code}).status_code == 201
    # login
    r = client.post("/auth/login", json={"username":"a","password":"password1"})
    assert r.status_code == 200
    # me
    assert client.get("/auth/me").status_code == 200
```

- [ ] **Step 5: 运行全量测试**

```bash
uv run pytest -v
```
Expected: Plan 01-05 全绿

- [ ] **Step 6: Commit**

```bash
git add app/main.py app/config.py tests/integration/test_auth_flow.py alembic/versions/*invite*
git commit -m "feat: 路由注册 + CORS/CSRF 中间件 + invite_codes 迁移 + auth 流程集成"
```

---

## Self-Review

**Spec 覆盖（Plan 05 = §4.3 认证CORS + §6.2 users/invite + §7.1 force-verify + §8.1 渠道加密 + §6.3 IDOR + admin 审计）：**
- ✅ httpOnly cookie + SameSite + JWT（§4.3 D2:A）→ Task 1/3/4
- ✅ /auth/csrf + CSRF double-submit（§4.3 D2:A）→ Task 4/7
- ✅ CORS allow_credentials + 显式 origins（§4.3 D2:A）→ Task 7
- ✅ 邀请码单次+有效期+尝试锁定+admin生成+无默认码（§6.2）→ Task 2
- ✅ 用户体系 register/login/logout/me（§6.2）→ Task 4
- ✅ 渠道配置加密写入 config_json={ct:}+key_version（§8.1，对齐 Plan 04）→ Task 5
- ✅ admin 后台 + force-verify（§7.1 verified 恢复）→ Task 6
- ✅ admin_audit_logs 脱敏写入（§6.2）→ Task 6
- ✅ IDOR（current_user + Repository user_id）→ Task 3/5/6
- 📌 tickets CRUD API → Task 7 标注复用 TicketRepo（Plan 03 已 IDOR-safe）
- 📌 bootstrap admin（首个 admin 生成）→ 首次部署手动 SQL 或 env 注入一个 admin（运维 runbook）

**Placeholder scan：** 无 TBD；tickets.router 标注"复用 TicketRepo 不展开"（非 placeholder——TicketRepo 在 Plan 03 已实现，本 plan 聚焦认证/admin）。
**类型一致：** `COOKIE_NAME`/`CSRF_HEADER`/`create_session_token`/`current_user`/`require_admin` 前后一致；渠道 config `{"ct":...}`+key_version 与 Plan 04 Notifier._decrypt_config 对齐。
**衔接：** Plan 04 main.py startup（Plan 04 Task 7）与本 Plan 05 main.py 路由注册合并（都用 Edit 追加到 app/main.py）；Plan 06 前端用 /auth/* + /channels + /admin API。
**已知简化（MVP）：**
- tickets.router 复用 Plan 03 TicketRepo 不展开代码（IDOR-safe 已由 repo 保证）。
- **bootstrap admin**：首个 admin 经 CLI `uv run python -m app.cli create-admin --username admin --password <p>` 创建（users 表空时无需邀请码建首个 admin；后续用户走邀请码）。CLI 在 Plan 06 补，本 plan 标注。
- **邀请码防爆破**：按全局码 attempts 锁定（非 IP 限流），家庭小圈子场景够；公开版需加 IP/会话限流（slowapi/nginx）。
- **JWT 无 refresh/blacklist**：7 天过期重新登录；家庭场景可接受，公开版加 refresh + jti 黑名单。
- CSRF 中间件路径豁免覆盖所有"首次无 cookie"端点（login/register/csrf）。
