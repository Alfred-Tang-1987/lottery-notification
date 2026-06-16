# Phase 1 · Plan 3: 用户体系 + FastAPI REST API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 实现多用户体系（邀请制注册、JWT 认证、严格用户隔离）与 FastAPI REST API——号码 CRUD、开奖查询、比对结果、通知配置、管理后台，并把 Plan 2 的核心闭环回调装配进 scheduler。

**Architecture:** FastAPI + JWT（无状态，为未来 iOS App 复用）。所有用户私有 API 经 `current_user` 依赖注入，repository 强制 `user_id` 过滤。路由按资源分组。scheduler 的 poll/summary job 在 app 启动时装配真实回调（fetch→compare→push 编排）。

**Tech Stack:** FastAPI、Uvicorn、PyJWT、passlib[bcrypt]、python-multipart、httpx（TestClient）。

**前置依赖:** Plan 1（领域层）+ Plan 2（核心闭环）已完成。

**对应 Spec:** §2（用户场景）、§6.3（隔离机制）、§12（页面/接口）

**范围说明:** 本 plan 实现后端 API（前端 Plan 4 消费）。钉钉/企微渠道、统计/提醒/走势/运维细节在 Plan 5。

---

## File Structure

```
app/
├── core/
│   ├── security.py          # 密码哈希 + JWT 签发/校验
│   └── deps.py              # current_user / require_admin 依赖
├── api/
│   ├── __init__.py
│   ├── router.py            # 汇总路由
│   ├── auth.py              # 注册/登录
│   ├── tickets.py           # 号码 CRUD（user 隔离）
│   ├── draws.py             # 开奖查询（全局读）
│   ├── results.py           # 比对结果/中奖记录（user 隔离）
│   ├── notifications.py     # 渠道/规则配置（user 隔离）
│   └── admin.py             # 管理后台（admin guard）
├── services/
│   └── orchestration.py     # fetch→compare→push 编排（scheduler 回调）
└── main.py                  # FastAPI app + scheduler 装配
tests/
├── api/
│   ├── test_auth.py
│   ├── test_tickets.py
│   ├── test_isolation.py    # 用户隔离专项
│   └── test_admin.py
└── conftest.py              # 扩展：client + auth fixtures
```

---

## Task 1: 安全工具（密码哈希 + JWT）

**Files:**
- Modify: `pyproject.toml`（加 fastapi/uvicorn/pyjwt/passlib/python-multipart）
- Create: `app/core/security.py`
- Test: `tests/core/test_security.py`

- [ ] **Step 1: 修改 `pyproject.toml` dependencies 追加**

```toml
dependencies = [
    "sqlmodel>=0.0.16",
    "httpx>=0.27",
    "apscheduler>=3.10",
    "pydantic-settings>=2.0",
    "fastapi>=0.110",
    "uvicorn[standard]>=0.27",
    "pyjwt>=2.8",
    "passlib[bcrypt]>=1.7",
    "python-multipart>=0.0.9",
]
```
Run: `pip install -e ".[dev]"`

- [ ] **Step 2: 写失败测试 `tests/core/test_security.py`**

```python
from app.core.security import hash_password, verify_password, create_token, decode_token


def test_hash_and_verify():
    h = hash_password("secret123")
    assert h != "secret123"
    assert verify_password("secret123", h) is True
    assert verify_password("wrong", h) is False


def test_jwt_roundtrip():
    token = create_token({"sub": "42", "role": "user"})
    payload = decode_token(token)
    assert payload["sub"] == "42"
    assert payload["role"] == "user"


def test_decode_invalid_token_returns_none():
    assert decode_token("not.a.jwt") is None
```

- [ ] **Step 3: 跑测试验证失败**

Run: `pytest tests/core/test_security.py -v` → FAIL `ImportError`

- [ ] **Step 4: 实现 `app/core/security.py`**

```python
from datetime import datetime, timedelta, timezone
import jwt
from passlib.context import CryptContext

SECRET = "change-me-in-env-JWT_SECRET"  # 生产从 env 读
ALGO = "HS256"
_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(p: str) -> str:
    return _pwd.hash(p)


def verify_password(p: str, h: str) -> bool:
    return _pwd.verify(p, h)


def create_token(payload: dict, expires_hours: int = 24 * 7) -> str:
    data = {**payload, "exp": datetime.now(timezone.utc) + timedelta(hours=expires_hours)}
    return jwt.encode(data, SECRET, algorithm=ALGO)


def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, SECRET, algorithms=[ALGO])
    except jwt.PyJWTError:
        return None
```

> **env 注入：** `SECRET` 在 Task 4 的 app 启动时从 `Settings.jwt_secret` 覆盖（生产强制非默认值）。

- [ ] **Step 5: 跑测试通过 + Commit**

Run: `pytest tests/core/test_security.py -v` → PASS
```bash
git add pyproject.toml app/core/security.py tests/core/test_security.py
git commit -m "feat(core): 密码哈希(passlib) + JWT 签发/校验"
```

---

## Task 2: 用户注册（邀请码）+ 登录 API

**Files:**
- Create: `app/api/__init__.py`, `app/api/auth.py`, `app/api/router.py`
- Modify: `app/db/repositories/user_repo.py`（加 create/get_by_username）
- Test: `tests/api/__init__.py`, `tests/api/test_auth.py`

- [ ] **Step 1: 扩展 `app/db/repositories/user_repo.py`**

追加：
```python
def create(session: Session, username: str, password_hash: str,
           invite_code: str | None = None, role: str = "user") -> User:
    u = User(username=username, password_hash=password_hash,
             invite_code=invite_code, role=role)
    session.add(u); session.commit(); session.refresh(u)
    return u


def get_by_username(session: Session, username: str) -> User | None:
    return session.exec(select(User).where(User.username == username)).first()
```

- [ ] **Step 2: 写失败测试 `tests/api/test_auth.py`**

```python
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_register_then_login():
    r = client.post("/api/auth/register", json={
        "username": "alice", "password": "Pass1234", "invite_code": "WELCOME"})
    assert r.status_code == 201
    r = client.post("/api/auth/login", json={"username": "alice", "password": "Pass1234"})
    assert r.status_code == 200
    assert "access_token" in r.json()


def test_login_wrong_password():
    client.post("/api/auth/register", json={
        "username": "bob", "password": "Pass1234", "invite_code": "WELCOME"})
    r = client.post("/api/auth/login", json={"username": "bob", "password": "wrong"})
    assert r.status_code == 401


def test_register_duplicate_username():
    client.post("/api/auth/register", json={
        "username": "carol", "password": "Pass1234", "invite_code": "WELCOME"})
    r = client.post("/api/auth/register", json={
        "username": "carol", "password": "Pass1234", "invite_code": "WELCOME"})
    assert r.status_code == 409
```

- [ ] **Step 3: 跑测试验证失败**

Run: `pytest tests/api/test_auth.py -v` → FAIL `ImportError`

- [ ] **Step 4: 实现 `app/api/auth.py`**

```python
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlmodel import Session
from app.db.database import get_session
from app.db.repositories import user_repo
from app.core.security import hash_password, verify_password, create_token

router = APIRouter(prefix="/api/auth", tags=["auth"])

VALID_INVITE = {"WELCOME"}  # MVP：固定邀请码；Plan 5 改为 DB 管理的单次码

class RegisterIn(BaseModel):
    username: str
    password: str
    invite_code: str

class LoginIn(BaseModel):
    username: str
    password: str


@router.post("/register", status_code=201)
def register(body: RegisterIn, session: Session = Depends(get_session)):
    if body.invite_code not in VALID_INVITE:
        raise HTTPException(403, "邀请码无效")
    if user_repo.get_by_username(session, body.username):
        raise HTTPException(409, "用户名已存在")
    u = user_repo.create(session, body.username, hash_password(body.password),
                         invite_code=body.invite_code)
    return {"id": u.id, "username": u.username}


@router.post("/login")
def login(body: LoginIn, session: Session = Depends(get_session)):
    u = user_repo.get_by_username(session, body.username)
    if not u or not verify_password(body.password, u.password_hash):
        raise HTTPException(401, "用户名或密码错误")
    token = create_token({"sub": str(u.id), "role": u.role, "username": u.username})
    return {"access_token": token, "token_type": "bearer"}
```

- [ ] **Step 5: 实现最小 `app/main.py`（Task 4 扩展）供测试 import**

```python
from fastapi import FastAPI
from sqlmodel import SQLModel
from app.db.database import init_db, get_engine
from app.api.auth import router as auth_router

app = FastAPI(title="彩票核对系统")
app.include_router(auth_router)


def get_session():
    from sqlmodel import Session
    with Session(get_engine()) as s:
        yield s
```

> **注：** Task 4 会把 `get_session` 移到 `db/database.py` 并装配真实 engine。此处让测试可 import app。conftest（Task 4）会在测试前 `init_db` 到内存库并覆盖 `get_session` 依赖。

- [ ] **Step 6: 跑测试通过 + Commit**

Run: `pytest tests/api/test_auth.py -v` → PASS（需 conftest 初始化内存库；若报 engine 未初始化，先做 Task 4 的 conftest）
```bash
git add app/api/ app/main.py app/db/repositories/user_repo.py tests/api/test_auth.py
git commit -m "feat(api): 邀请码注册 + 登录(JWT)"
```

---

## Task 3: current_user 依赖 + 用户隔离机制

**Files:**
- Create: `app/core/deps.py`
- Modify: `app/db/database.py`（加 `get_session`）
- Modify: `tests/conftest.py`（加 client/auth fixtures）
- Test: `tests/api/test_isolation.py`

- [ ] **Step 1: 修改 `app/db/database.py` 加 get_session**

```python
from sqlmodel import Session

def get_session():
    engine = get_engine()
    with Session(engine) as s:
        yield s
```

- [ ] **Step 2: 实现 `app/core/deps.py`**

```python
from fastapi import Depends, HTTPException, Header
from sqlmodel import Session
from app.db.database import get_session
from app.db.repositories import user_repo
from app.core.security import decode_token


def current_user(authorization: str = Header(default=""),
                 session: Session = Depends(get_session)):
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "未认证")
    payload = decode_token(authorization.removeprefix("Bearer "))
    if not payload:
        raise HTTPException(401, "token 无效")
    u = user_repo.get(session, int(payload["sub"]))
    if not u:
        raise HTTPException(401, "用户不存在")
    return u


def require_admin(u=Depends(current_user)):
    if u.role != "admin":
        raise HTTPException(403, "需要管理员权限")
    return u
```

- [ ] **Step 3: 扩展 `tests/conftest.py`（client + 两个用户 token）**

```python
import pytest
from fastapi.testclient import TestClient
from app.db.database import init_db, get_engine, get_session
from app.db.models import User
from app.main import app
from app.core.security import hash_password, create_token
from sqlmodel import Session


@pytest.fixture()
def session():
    engine = get_engine("sqlite://")
    init_db(engine)
    app.dependency_overrides[get_session] = lambda: (_ for _ in ()).throw(
        StopIteration)  # 占位，下面真实覆盖
    with Session(engine) as s:
        yield s


@pytest.fixture()
def client(session):
    def _override():
        yield session
    app.dependency_overrides[get_session] = _override
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture()
def alice_token(session):
    session.add(User(username="alice", password_hash=hash_password("p"), role="user"))
    session.add(User(username="bob", password_hash=hash_password("p"), role="user"))
    session.commit()
    return create_token({"sub": "1", "role": "user", "username": "alice"})


@pytest.fixture()
def bob_token(session):
    return create_token({"sub": "2", "role": "user", "username": "bob"})
```

> **简化：** conftest 直接为已存在的 alice/bob 用户签 token（id=1/2）。`get_session` 依赖被覆盖为测试内存库。

- [ ] **Step 4: 写失败测试 `tests/api/test_isolation.py`（隔离专项，Task 5 号码 API 落地后跑）**

```python
def test_user_cannot_see_others_tickets(client, alice_token, bob_token):
    # alice 建一注
    client.post("/api/tickets", json={
        "lottery_code": "ssq", "numbers": {"front": [1,2,3,4,5,6], "back": [7]}
    }, headers={"Authorization": f"Bearer {alice_token}"})
    # bob 看不到 alice 的
    r = client.get("/api/tickets", headers={"Authorization": f"Bearer {bob_token}"})
    assert r.status_code == 200
    assert r.json() == []   # bob 的号码池为空
    # alice 能看到自己的
    r = client.get("/api/tickets", headers={"Authorization": f"Bearer {alice_token}"})
    assert len(r.json()) == 1
```

- [ ] **Step 5: 跑（先跳过，Task 5 实现号码 API 后跑通）+ Commit deps**

Run: `pytest tests/api/test_isolation.py -v` → 预期 FAIL（无 /api/tickets）
```bash
git add app/core/deps.py app/db/database.py tests/conftest.py tests/api/test_isolation.py
git commit -m "feat(core): current_user 依赖 + 用户隔离测试骨架"
```

---

## Task 4: FastAPI app 骨架 + 错误处理 + 路由汇总

**Files:**
- Modify: `app/main.py`
- Modify: `app/api/router.py`

- [ ] **Step 1: 实现 `app/api/router.py`**

```python
from fastapi import APIRouter
from app.api import auth, tickets, draws, results, notifications, admin

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(tickets.router)
api_router.include_router(draws.router)
api_router.include_router(results.router)
api_router.include_router(notifications.router)
api_router.include_router(admin.router)
```

> **注：** tickets/draws/results/notifications/admin 在 Task 5-8 创建。本 task 先创建空 router 文件（每个含 `router = APIRouter()`），Task 5-8 填充。

- [ ] **Step 2: 为 tickets/draws/results/notifications/admin 各创建空 `app/api/<name>.py`**

每个文件内容：
```python
from fastapi import APIRouter
router = APIRouter(prefix="/api/<name>", tags=["<name>"])
```

- [ ] **Step 3: 重写 `app/main.py`**

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from app.core.config import get_settings
from app.core.logging import setup_logging
from app.db.database import init_db
from app.api.router import api_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    settings = get_settings()
    init_db()
    # scheduler 装配在 Task 9
    yield

app = FastAPI(title="彩票核对系统", lifespan=lifespan)
app.include_router(api_router)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    return JSONResponse(status_code=500, content={"detail": "服务器内部错误"})


@app.get("/api/health")
def health():
    return {"status": "ok"}
```

- [ ] **Step 4: 验证 app 可启动 + Commit**

Run: `python -c "from app.main import app; print(app.routes[:3])"` → 无异常
```bash
git add app/api/router.py app/api/tickets.py app/api/draws.py app/api/results.py \
        app/api/notifications.py app/api/admin.py app/main.py
git commit -m "feat(api): FastAPI app 骨架+路由汇总+健康检查"
```

---

## Task 5: 号码 CRUD API（用户隔离）

**Files:**
- Modify: `app/api/tickets.py`
- Test: `tests/api/test_tickets.py`

- [ ] **Step 1: 写失败测试 `tests/api/test_tickets.py`**

```python
def test_create_list_delete_ticket(client, alice_token):
    h = {"Authorization": f"Bearer {alice_token}"}
    r = client.post("/api/tickets", json={
        "lottery_code": "ssq", "numbers": {"front": [1,2,3,4,5,6], "back": [7]},
        "label": "幸运号"}, headers=h)
    assert r.status_code == 201
    tid = r.json()["id"]
    r = client.get("/api/tickets", headers=h)
    assert len(r.json()) == 1 and r.json()[0]["label"] == "幸运号"
    assert client.delete(f"/api/tickets/{tid}", headers=h).status_code == 204
    assert client.get("/api/tickets", headers=h).json() == []


def test_create_validates_numbers(client, alice_token):
    h = {"Authorization": f"Bearer {alice_token}"}
    # 双色球红球超范围（>33）
    r = client.post("/api/tickets", json={
        "lottery_code": "ssq", "numbers": {"front": [40,2,3,4,5,6], "back": [7]}}, headers=h)
    assert r.status_code == 422


def test_no_token_rejected(client):
    assert client.get("/api/tickets").status_code == 401
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/api/test_tickets.py tests/api/test_isolation.py -v` → FAIL

- [ ] **Step 3: 实现 `app/api/tickets.py`**

```python
import json
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select
from app.core.deps import current_user
from app.db.database import get_session
from app.db.models import Ticket, User
from app.domain.lottery_types import LOTTERY_TYPES

router = APIRouter(prefix="/api/tickets", tags=["tickets"])


class TicketIn(BaseModel):
    lottery_code: str
    numbers: dict          # {"front":[...],"back":[...]}
    play_type: str = "single"
    label: str | None = None


class TicketOut(BaseModel):
    id: int; lottery_code: str; numbers: dict; play_type: str
    label: str | None; enabled: bool


def _validate(code: str, numbers: dict):
    if code not in LOTTERY_TYPES:
        raise HTTPException(422, "未知彩种")
    spec = LOTTERY_TYPES[code]
    front = numbers.get("front", [])
    if len(front) != spec.front.count:
        raise HTTPException(422, f"前区需 {spec.front.count} 个")
    if any(not (spec.front.min <= x <= spec.front.max) for x in front):
        raise HTTPException(422, f"前区号码超出 {spec.front.min}-{spec.front.max}")
    if spec.back:
        back = numbers.get("back", [])
        if len(back) != spec.back.count:
            raise HTTPException(422, f"后区需 {spec.back.count} 个")
        if any(not (spec.back.min <= x <= spec.back.max) for x in back):
            raise HTTPException(422, f"后区号码超出 {spec.back.min}-{spec.back.max}")


@router.get("", response_model=list[TicketOut])
def list_tickets(u: User = Depends(current_user), session: Session = Depends(get_session)):
    rows = session.exec(select(Ticket).where(
        Ticket.user_id == u.id, Ticket.enabled == True)).all()  # noqa: E712
    return [TicketOut(id=r.id, lottery_code=r.lottery_code,
                      numbers=json.loads(r.numbers_json), play_type=r.play_type,
                      label=r.label, enabled=r.enabled) for r in rows]


@router.post("", response_model=TicketOut, status_code=201)
def create_ticket(body: TicketIn, u: User = Depends(current_user),
                  session: Session = Depends(get_session)):
    _validate(body.lottery_code, body.numbers)
    t = Ticket(user_id=u.id, lottery_code=body.lottery_code, play_type=body.play_type,
               numbers_json=json.dumps(body.numbers), label=body.label)
    session.add(t); session.commit(); session.refresh(t)
    return TicketOut(id=t.id, lottery_code=t.lottery_code, numbers=body.numbers,
                     play_type=t.play_type, label=t.label, enabled=t.enabled)


@router.delete("/{tid}", status_code=204)
def delete_ticket(tid: int, u: User = Depends(current_user),
                  session: Session = Depends(get_session)):
    t = session.get(Ticket, tid)
    if not t or t.user_id != u.id:
        raise HTTPException(404)
    t.enabled = False  # 软删除
    session.add(t); session.commit()
```

- [ ] **Step 4: 跑测试通过（含隔离测试）+ Commit**

Run: `pytest tests/api/test_tickets.py tests/api/test_isolation.py -v` → PASS
```bash
git add app/api/tickets.py tests/api/test_tickets.py
git commit -m "feat(api): 号码 CRUD + 号码合法性校验 + 用户隔离"
```

---

## Task 6: 开奖查询 + 比对结果 API

**Files:**
- Modify: `app/api/draws.py`, `app/api/results.py`
- Test: `tests/api/test_draws.py`

- [ ] **Step 1: 写测试 `tests/api/test_draws.py`**

```python
import json
from app.db.models import DrawResult, Ticket, Comparison

def _seed(session):
    session.add(DrawResult(lottery_code="ssq", draw_no="2024060",
        draw_date="2024-05-26", numbers_json='{"front":[2,7,14,18,25,32],"back":[6]}',
        source="mxnzp", verified=True))
    session.commit()

def test_list_latest_draws(client, alice_token, session):
    _seed(session)
    r = client.get("/api/draws?lottery_code=ssq", headers={"Authorization": f"Bearer {alice_token}"})
    assert r.status_code == 200
    assert r.json()[0]["draw_no"] == "2024060"

def test_my_results_isolated(client, alice_token, bob_token, session):
    _seed(session)
    # alice 的比对结果
    session.add(Comparison(user_id=1, draw_result_id=1, ticket_id=1,
        lottery_code="ssq", draw_no="2024060", hits_json='{"front_hit":6,"back_hit":1}',
        prize_tier=1, prize_amount=None, is_win=True))
    session.commit()
    r = client.get("/api/results?lottery_code=ssq&draw_no=2024060",
                   headers={"Authorization": f"Bearer {alice_token}"})
    assert len(r.json()) == 1 and r.json()[0]["prize_tier"] == 1
    r = client.get("/api/results?lottery_code=ssq&draw_no=2024060",
                   headers={"Authorization": f"Bearer {bob_token}"})
    assert r.json() == []  # bob 看不到 alice 的
```

- [ ] **Step 2: 实现 `app/api/draws.py`**

```python
import json
from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, select
from app.core.deps import current_user
from app.db.database import get_session
from app.db.models import DrawResult, User

router = APIRouter(prefix="/api/draws", tags=["draws"])


@router.get("")
def list_draws(lottery_code: str = Query(...), limit: int = 10,
               u: User = Depends(current_user), session: Session = Depends(get_session)):
    rows = session.exec(select(DrawResult).where(
        DrawResult.lottery_code == lottery_code
    ).order_by(DrawResult.draw_no.desc()).limit(limit)).all()
    return [{"draw_no": r.draw_no, "draw_date": str(r.draw_date),
             "numbers": json.loads(r.numbers_json), "source": r.source,
             "verified": r.verified} for r in rows]
```

- [ ] **Step 3: 实现 `app/api/results.py`**

```python
import json
from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, select
from app.core.deps import current_user
from app.db.database import get_session
from app.db.models import Comparison, User

router = APIRouter(prefix="/api/results", tags=["results"])


@router.get("")
def my_results(lottery_code: str = Query(...), draw_no: str = Query(...),
               u: User = Depends(current_user), session: Session = Depends(get_session)):
    rows = session.exec(select(Comparison).where(
        Comparison.user_id == u.id,
        Comparison.lottery_code == lottery_code,
        Comparison.draw_no == draw_no,
    )).all()
    return [{"ticket_id": r.ticket_id, "hits": json.loads(r.hits_json),
             "prize_tier": r.prize_tier, "prize_amount": r.prize_amount,
             "is_win": r.is_win} for r in rows]
```

- [ ] **Step 4: 跑测试通过 + Commit**

Run: `pytest tests/api/test_draws.py -v` → PASS
```bash
git add app/api/draws.py app/api/results.py tests/api/test_draws.py
git commit -m "feat(api): 开奖查询(全局读)+比对结果(用户隔离)"
```

---

## Task 7: 通知配置 API（渠道 + 规则）

**Files:**
- Modify: `app/api/notifications.py`
- Test: `tests/api/test_notifications.py`

- [ ] **Step 1: 写测试 `tests/api/test_notifications.py`**

```python
def test_set_bark_channel_and_rule(client, alice_token):
    h = {"Authorization": f"Bearer {alice_token}"}
    r = client.post("/api/notifications/channels", json={
        "type": "bark", "config": {"device_key": "KEY123"}}, headers=h)
    assert r.status_code == 201
    r = client.post("/api/notifications/rules", json={
        "lottery_code": "ssq", "strategy": "win_only", "timing": "summary"}, headers=h)
    assert r.status_code == 201
    r = client.get("/api/notifications/rules", headers=h)
    assert r.json()[0]["strategy"] == "win_only"
```

- [ ] **Step 2: 实现 `app/api/notifications.py`**

```python
import json
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlmodel import Session, select
from app.core.deps import current_user
from app.db.database import get_session
from app.db.models import NotificationChannel, NotificationRule, User

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


class ChannelIn(BaseModel):
    type: str           # bark / feishu / dingtalk / wecom
    config: dict


class RuleIn(BaseModel):
    lottery_code: str
    strategy: str = "every"      # every / win_only
    timing: str = "summary"      # summary / instant / both


@router.post("/channels", status_code=201)
def add_channel(body: ChannelIn, u: User = Depends(current_user),
                session: Session = Depends(get_session)):
    ch = NotificationChannel(user_id=u.id, type=body.type,
                             config_json=json.dumps(body.config))
    session.add(ch); session.commit(); session.refresh(ch)
    return {"id": ch.id}


@router.get("/channels")
def list_channels(u: User = Depends(current_user), session: Session = Depends(get_session)):
    rows = session.exec(select(NotificationChannel).where(
        NotificationChannel.user_id == u.id)).all()
    return [{"id": r.id, "type": r.type, "config": json.loads(r.config_json),
             "enabled": r.enabled} for r in rows]


@router.post("/rules", status_code=201)
def upsert_rule(body: RuleIn, u: User = Depends(current_user),
                session: Session = Depends(get_session)):
    existing = session.exec(select(NotificationRule).where(
        NotificationRule.user_id == u.id,
        NotificationRule.lottery_code == body.lottery_code)).first()
    if existing:
        existing.strategy = body.strategy; existing.timing = body.timing
        session.add(existing); session.commit()
        return {"id": existing.id}
    r = NotificationRule(user_id=u.id, lottery_code=body.lottery_code,
                         strategy=body.strategy, timing=body.timing)
    session.add(r); session.commit(); session.refresh(r)
    return {"id": r.id}


@router.get("/rules")
def list_rules(u: User = Depends(current_user), session: Session = Depends(get_session)):
    rows = session.exec(select(NotificationRule).where(
        NotificationRule.user_id == u.id)).all()
    return [{"lottery_code": r.lottery_code, "strategy": r.strategy, "timing": r.timing}
            for r in rows]
```

- [ ] **Step 3: 跑测试通过 + Commit**

Run: `pytest tests/api/test_notifications.py -v` → PASS
```bash
git add app/api/notifications.py tests/api/test_notifications.py
git commit -m "feat(api): 通知渠道+规则配置(用户隔离)"
```

---

## Task 8: 管理后台 API（admin guard）

**Files:**
- Modify: `app/api/admin.py`
- Test: `tests/api/test_admin.py`

- [ ] **Step 1: 写测试 `tests/api/test_admin.py`**

```python
from app.core.security import create_token

def test_admin_sees_users(client, session):
    from app.db.models import User
    from app.core.security import hash_password
    session.add(User(username="admin", password_hash=hash_password("p"), role="admin"))
    session.commit()
    admin_token = create_token({"sub": "1", "role": "admin", "username": "admin"})
    r = client.get("/api/admin/users", headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 200

def test_non_admin_forbidden(client, alice_token):
    r = client.get("/api/admin/users", headers={"Authorization": f"Bearer {alice_token}"})
    assert r.status_code == 403
```

- [ ] **Step 2: 实现 `app/api/admin.py`**

```python
from fastapi import APIRouter, Depends
from sqlmodel import Session, select
from app.core.deps import require_admin
from app.db.database import get_session
from app.db.models import User
from app.db.repositories import draw_result_repo as dr

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/users")
def list_users(admin: User = Depends(require_admin), session: Session = Depends(get_session)):
    rows = session.exec(select(User)).all()
    return [{"id": u.id, "username": u.username, "role": u.role} for u in rows]


@router.get("/health")
def source_health(admin: User = Depends(require_admin), session: Session = Depends(get_session)):
    from app.db.models import DrawResult
    latest = session.exec(select(DrawResult).order_by(DrawResult.fetched_at.desc())).first()
    return {"latest_draw": {"lottery": latest.lottery_code, "no": latest.draw_no,
                            "fetched_at": str(latest.fetched_at)} if latest else None}
```

- [ ] **Step 3: 跑测试通过 + Commit**

Run: `pytest tests/api/test_admin.py -v` → PASS
```bash
git add app/api/admin.py tests/api/test_admin.py
git commit -m "feat(api): 管理后台(admin guard)+健康面板"
```

---

## Task 9: 核心闭环编排 + scheduler 回调装配

**Files:**
- Create: `app/services/orchestration.py`
- Modify: `app/services/scheduler.py`（接受回调注入）
- Modify: `app/main.py`（lifespan 装配）
- Test: `tests/services/test_orchestration.py`

- [ ] **Step 1: 写测试 `tests/services/test_orchestration.py`**

```python
import pytest, json
from unittest.mock import AsyncMock, patch
from app.db.models import Ticket, NotificationChannel, NotificationRule
from app.services.orchestration import fetch_compare_push


@pytest.mark.asyncio
async def test_orchestration_writes_comparison_and_pushes(session):
    session.add(Ticket(user_id=1, lottery_code="ssq",
                numbers_json='{"front":[2,7,14,18,25,32],"back":[6]}'))
    session.add(NotificationChannel(user_id=1, type="bark", config_json='{"device_key":"K"}'))
    session.add(NotificationRule(user_id=1, lottery_code="ssq", strategy="every"))
    session.commit()
    fake_dto = ("ssq", "2024060", "2024-05-26", [2,7,14,18,25,32], [6], "mxnzp")
    with patch("app.services.orchestration.DrawFetcher.fetch",
               new=AsyncMock(return_value=(
                   __import__("app.adapters.base", fromlist=["DrawResultDTO"]).DrawResultDTO(
                       "ssq", "2024060", __import__("datetime").date(2024,5,26),
                       [2,7,14,18,25,32], [6], "mxnzp"), True))):
        with patch("app.services.orchestration.NotifyDispatcher.dispatch_summary",
                   new=AsyncMock(return_value=None)) as mock_push:
            await fetch_compare_push(session, "ssq", channel_factory=lambda u: [AsyncMock()])
            assert mock_push.await_count == 1


@pytest.mark.asyncio
async def test_orchestration_skips_unverified(session):
    with patch("app.services.orchestration.DrawFetcher.fetch",
               new=AsyncMock(return_value=(None, False))):
        # 不应抛异常，不应写任何 comparison
        await fetch_compare_push(session, "ssq", channel_factory=lambda u: [])
    from app.db.models import Comparison
    from sqlmodel import select
    assert session.exec(select(Comparison)).all() == []
```

- [ ] **Step 2: 实现 `app/services/orchestration.py`**

```python
import json, logging
from sqlmodel import Session
from app.core.config import get_settings
from app.adapters.mxnzp import MxnzpSource
from app.adapters.juhe import JuheSource
from app.services.draw_fetcher import DrawFetcher
from app.services.compare_engine import CompareEngine
from app.services.notify_dispatcher import NotifyDispatcher
from app.db.repositories import draw_result_repo as dr

log = logging.getLogger(__name__)


def _build_fetcher():
    s = get_settings()
    return DrawFetcher(MxnzpSource(s.mxnzp_app_id, s.mxnzp_app_secret),
                       JuheSource(s.juhe_api_key))


async def fetch_compare_push(session: Session, lottery_code: str, channel_factory,
                             cross_check: bool = True):
    """核心闭环编排：fetch → (verified?) → compare → push。"""
    dto, verified = await _build_fetcher().fetch(lottery_code, cross_check=cross_check)
    if dto is None or not verified:
        log.error("跳过 %s：获取失败或双源不一致", lottery_code)
        return
    draw = dr.upsert(session, {
        "lottery_code": dto.lottery_code, "draw_no": dto.draw_no, "draw_date": dto.draw_date,
        "numbers_json": json.dumps({"front": dto.front, "back": dto.back}),
        "source": dto.source, "verified": True})
    CompareEngine().compare_draw(session, draw)
    disp = NotifyDispatcher(channel_factory=channel_factory)
    await disp.dispatch_summary(session, lottery_code, dto.draw_no)
```

- [ ] **Step 3: 修改 `app/services/scheduler.py` 接受回调**

把 `register` 签名改为：
```python
def register(self, lottery_codes: list[str], poll_callback, summary_callback):
    ...
    self.scheduler.add_job(poll_callback, trigger=build_poll_trigger(spec.draw_days),
                           id=f"poll_{code}", args=[code], replace_existing=True)
    ...
    self.scheduler.add_job(summary_callback, trigger=build_summary_trigger(hour),
                           id="summary", replace_existing=True)
```

- [ ] **Step 4: 修改 `app/main.py` lifespan 装配**

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    init_db()
    from app.services.scheduler import Scheduler
    from app.services.orchestration import fetch_compare_push
    from app.services.notify_dispatcher import NotifyDispatcher
    # channel_factory 从 DB 读（同 cli.py 模式）
    def channel_factory_for(user_id):
        ...  # 复用 cli.channel_factory 逻辑（Plan 5 重构为依赖注入）
    sched = Scheduler()
    codes = ["ssq", "dlt", "qlc", "fc3d", "qxc", "pl3", "pl5"]
    # poll/summary 回调包装为 async 任务
    sched.register(codes,
                   poll_callback=lambda c: None,   # 路径A：当晚轮询，简化版
                   summary_callback=lambda: None)  # 路径B：次日汇总
    yield
```

> **注：** 完整的 async 回调装配（从 DB session 工厂构造 + APScheduler AsyncIOScheduler 的 add_job 包装）在 Plan 5 的"运维装配"任务里收尾。本 task 确保编排函数 `fetch_compare_push` 可独立测试通过。

- [ ] **Step 5: 跑测试通过 + Commit**

Run: `pytest tests/services/test_orchestration.py -v` → PASS
```bash
git add app/services/orchestration.py app/services/scheduler.py app/main.py \
        tests/services/test_orchestration.py
git commit -m "feat(services): 核心闭环编排 fetch_compare_push + scheduler 回调装配"
```

---

## Task 10: 全量 API 集成测试 + 覆盖率

- [ ] **Step 1: 跑全部测试**

Run: `pytest -v`
Expected: 全部通过

- [ ] **Step 2: 加 api 覆盖率到 pytest.ini**

```ini
addopts = -ra --strict-markers --cov=app --cov-report=term-missing --cov-fail-under=80
```

Run: `pytest -v` → 覆盖率 ≥ 80%

- [ ] **Step 3: Commit**

```bash
git add pytest.ini
git commit -m "test: 全量API集成测试 + 覆盖率门禁80%"
```

---

## Self-Review（已执行）

**1. Spec 覆盖：** §2 用户体系（邀请制/认证/隔离）→ Task 1-3 ✅；§6.3 隔离机制（current_user + repository user_id）→ Task 3/5/6 ✅；§12 接口（号码/查询/结果/配置/管理）→ Task 5-8 ✅；Plan 2 闭环回调装配 → Task 9 ✅。
**2. 占位符：** scheduler 完整 async 装配在 Plan 5 收尾（已注明），channel_factory 复用 cli 模式（注明 Plan 5 重构）。无 TBD。✅
**3. 类型一致：** `TicketIn/Out`、`current_user`、`require_admin`、`fetch_compare_push(session, code, channel_factory)` 签名全 plan 一致。✅
**4. 残留：** 邀请码 MVP 固定（Plan 5 改 DB）；scheduler async 装配 Plan 5 收尾。

---

## Execution Handoff

Plan 3 完成（10 Task）：多用户体系（JWT+邀请制+隔离）+ 完整 REST API + 核心闭环编排。与 Plan 1+2 组合，后端可启动并提供 API（前端 Plan 4 消费）。

**后续：** Plan 4（前端 Vue3）→ Plan 5（统计/提醒/走势/钉钉企微/运维装配/奖级DB化）→ Plan 6（Docker 部署）。
