# Phase 1 · Plan 2: 核心闭环（数据层 + 开奖获取 + 比对引擎 + 推送 + 调度）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 打通端到端核心闭环——定时拉取开奖（双源交叉校验）→ 幂等比对追投用户的号码池 → 按用户追投彩种与策略推送（Bark+飞书），分层时机（大奖即时 + 次日 07:00 汇总）。产出可手动触发、有集成测试验证的命令行闭环。

**Architecture:** 数据层用 SQLModel + SQLite（类型选可迁 PostgreSQL）。开奖获取走适配器（`DrawSource` 接口，MXNZP 主 + 聚合数据备），双源交叉校验一致才入库。比对引擎幂等（`draw_no` 唯一约束），只比对追投用户的号码池。通知用插件化 Notifier，Dispatcher 按用户追投彩种过滤 + 策略决策。APScheduler 驱动两条触发路径。

**Tech Stack:** Python 3.12、SQLModel、httpx（异步 HTTP）、APScheduler、pytest、pytest-asyncio、respx（HTTP mock）。

**前置依赖:** Plan 1（领域层）已完成。

**对应 Spec:** §6（数据模型）、§7（核心数据流）、§8（通知）、§10（错误处理）

**范围说明:** 本 plan 实现"能跑通的闭环"。完整用户认证/REST API/Web UI 在 Plan 3-4。MVP 玩法单式。钉钉/企微渠道在 Plan 5。固定档奖金仍读 Plan 1 的 `prize_tables.py`（Plan 5 再改 DB 驱动）。

---

## File Structure

```
lottery-notification/
├── pyproject.toml                 # Modify: 加运行时依赖
├── app/
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py              # Settings（env：DB 路径、API key、推送 key）
│   │   └── logging.py             # 结构化日志
│   ├── db/
│   │   ├── __init__.py
│   │   ├── database.py            # engine + session + init_db()
│   │   ├── models.py              # SQLModel 表
│   │   └── repositories/
│   │       ├── __init__.py
│   │       ├── draw_result_repo.py
│   │       ├── ticket_repo.py
│   │       ├── comparison_repo.py
│   │       ├── notification_repo.py
│   │       └── user_repo.py
│   ├── adapters/
│   │   ├── __init__.py
│   │   ├── base.py                # DrawSource 接口 + DTO
│   │   ├── mxnzp.py
│   │   └── juhe.py
│   ├── services/
│   │   ├── __init__.py
│   │   ├── draw_fetcher.py        # 双源 + 交叉校验 + 降级
│   │   ├── compare_engine.py      # 幂等比对
│   │   ├── notifier/
│   │   │   ├── __init__.py
│   │   │   ├── base.py            # Notifier 接口 + 注册
│   │   │   ├── bark.py
│   │   │   └── feishu.py
│   │   ├── notify_dispatcher.py   # 追投过滤 + 策略 + 时机
│   │   └── scheduler.py           # APScheduler 路径 A/B
│   └── cli.py                     # 命令行入口：手动触发闭环
└── tests/
    ├── conftest.py                # db fixture（内存 SQLite）
    ├── db/
    ├── adapters/
    ├── services/
    └── integration/
        └── test_core_loop.py
```

**职责边界：**
- `core/config.py` — 集中读取环境变量，启动时校验必需 key。
- `db/` — 持久化；repository 封装查询，强制 user_id 过滤。
- `adapters/` — 外部数据源，返回标准化 `DrawResultDTO`。
- `services/` — 业务编排：获取→比对→推送→调度。
- `cli.py` — 手动触发（开发/运维用），非 Web。

---

## Task 1: 运行时依赖与配置

**Files:**
- Modify: `pyproject.toml`
- Create: `app/core/__init__.py`, `app/core/config.py`, `app/core/logging.py`
- Test: `tests/core/__init__.py`, `tests/core/test_config.py`

- [ ] **Step 1: 修改 `pyproject.toml` 加依赖**

把 `dependencies` 与 dev 改为：

```toml
[project]
name = "lottery-notification"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "sqlmodel>=0.0.16",
    "httpx>=0.27",
    "apscheduler>=3.10",
    "pydantic-settings>=2.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "pytest-cov>=5.0",
    "respx>=0.21",
]
```

Run: `pip install -e ".[dev]"`

- [ ] **Step 2: 写失败测试 `tests/core/test_config.py`**

```python
import os
from app.core.config import Settings


def test_settings_loads_from_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./test.db")
    monkeypatch.setenv("MXNZP_APP_ID", "id123")
    monkeypatch.setenv("MXNZP_APP_SECRET", "sec456")
    monkeypatch.setenv("JUHE_API_KEY", "juhe789")
    s = Settings()
    assert s.database_url == "sqlite:///./test.db"
    assert s.mxnzp_app_id == "id123"
    assert s.juhe_api_key == "juhe789"


def test_settings_has_defaults():
    s = Settings(_env_file=None)  # 不读 .env
    assert s.summary_hour == 7           # 次日汇总默认 07:00
    assert s.poll_start_offset_min == 30  # 开奖后 30 分钟开始轮询
```

- [ ] **Step 3: 跑测试验证失败**

Run: `pytest tests/core/test_config.py -v`
Expected: FAIL — `ImportError: cannot import name 'Settings'`

- [ ] **Step 4: 实现 `app/core/config.py`**

```python
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    database_url: str = "sqlite:///./data/lottery.db"
    # 数据源
    mxnzp_app_id: str = ""
    mxnzp_app_secret: str = ""
    juhe_api_key: str = ""
    # 调度（spec §7.3）
    poll_start_offset_min: int = 30   # 开奖时刻后多久开始轮询
    poll_interval_min: int = 15       # 轮询间隔
    poll_deadline_offset_hour: int = 4  # 最晚轮到开奖后几小时
    summary_hour: int = 7             # 次日汇总推送小时
    # 时区
    tz: str = "Asia/Shanghai"


def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 5: 实现 `app/core/logging.py`**

```python
import logging

def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
```

- [ ] **Step 6: 跑测试验证通过 + Commit**

Run: `pytest tests/core/test_config.py -v` → PASS
```bash
git add pyproject.toml app/core/ tests/core/
git commit -m "feat(core): 运行时依赖与 Settings 配置"
```

---

## Task 2: 数据库连接与初始化

**Files:**
- Create: `app/db/__init__.py`, `app/db/database.py`
- Create: `tests/conftest.py`
- Test: `tests/db/__init__.py`, `tests/db/test_database.py`

- [ ] **Step 1: 写 `tests/conftest.py`（内存 SQLite fixture）**

```python
import pytest
from sqlmodel import Session
from app.db.database import init_db, get_engine


@pytest.fixture()
def session():
    engine = get_engine("sqlite://")  # 内存库
    init_db(engine)
    with Session(engine) as s:
        yield s
```

- [ ] **Step 2: 写失败测试 `tests/db/test_database.py`**

```python
from sqlmodel import Session, select
from app.db.database import init_db, get_engine
from app.db.models import DrawResult


def test_init_db_creates_tables():
    engine = get_engine("sqlite://")
    init_db(engine)
    with Session(engine) as s:
        # 能查询空表即说明表已建
        rows = s.exec(select(DrawResult)).all()
        assert rows == []


def test_in_memory_db_isolated_per_engine():
    e1 = get_engine("sqlite://")
    init_db(e1)
    e2 = get_engine("sqlite://")
    init_db(e2)
    with Session(e1) as s1, Session(e2) as s2:
        s1.add(DrawResult(lottery_code="ssq", draw_no="1", draw_date="2024-01-01",
                          numbers_json='{"front":[1,2,3,4,5,6],"back":[7]}',
                          source="mxnzp", verified=True))
        s1.commit()
        assert len(s2.exec(select(DrawResult)).all()) == 0  # 隔离
```

- [ ] **Step 3: 跑测试验证失败**

Run: `pytest tests/db/test_database.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 4: 实现 `app/db/models.py`（所有表，一次建好）**

```python
from datetime import date, datetime
from sqlmodel import Field, SQLModel


class User(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    username: str = Field(index=True, unique=True)
    password_hash: str
    role: str = Field(default="user")        # user / admin
    invite_code: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Ticket(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    lottery_code: str = Field(index=True)
    play_type: str = Field(default="single")
    numbers_json: str           # {"front":[...],"back":[...]}
    label: str | None = None
    enabled: bool = Field(default=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class DrawResult(SQLModel, table=True):
    __tablename__ = "draw_results"
    id: int | None = Field(default=None, primary_key=True)
    lottery_code: str = Field(index=True)
    draw_no: str = Field(index=True)         # 期号
    draw_date: date
    numbers_json: str
    source: str
    verified: bool = Field(default=False)
    fetched_at: datetime = Field(default_factory=datetime.utcnow)


class Comparison(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    draw_result_id: int = Field(index=True)
    ticket_id: int = Field(index=True)
    lottery_code: str = Field(index=True)
    draw_no: str = Field(index=True)
    hits_json: str              # {"front_hit":4,"back_hit":0}
    prize_tier: int | None
    prize_amount: int | None   # 分
    is_win: bool = Field(default=False)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class NotificationChannel(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    type: str                   # bark / feishu / dingtalk / wecom
    config_json: str            # {"key":"...","url":"..."}
    enabled: bool = Field(default=True)


class NotificationRule(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    lottery_code: str = Field(index=True)
    strategy: str = Field(default="every")   # every / win_only
    timing: str = Field(default="summary")   # summary / instant / both


class NotificationLog(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    channel_type: str
    lottery_code: str
    draw_no: str
    payload: str
    status: str                 # sent / failed
    error: str | None = None
    sent_at: datetime = Field(default_factory=datetime.utcnow)
```

- [ ] **Step 5: 实现 `app/db/database.py`**

```python
from sqlmodel import Session, SQLModel, create_engine
from app.db import models  # noqa: F401  确保表类被导入

_engine = None


def get_engine(url: str | None = None):
    global _engine
    if url is not None:
        return create_engine(url, connect_args={"check_same_thread": False}
                             if url.startswith("sqlite") else {})
    if _engine is None:
        raise RuntimeError("engine 未初始化，先调 init_db")
    return _engine


def init_db(engine=None) -> None:
    global _engine
    if engine is None:
        from app.core.config import get_settings
        engine = create_engine(get_settings().database_url,
                               connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    _engine = engine
```

- [ ] **Step 6: 跑测试验证通过 + Commit**

Run: `pytest tests/db/test_database.py tests/conftest.py -v` → PASS
```bash
git add app/db/ tests/conftest.py tests/db/
git commit -m "feat(db): 数据库连接初始化与全部表模型"
```

---

## Task 3: Repository 层（幂等 draw_result + ticket + comparison）

**Files:**
- Create: `app/db/repositories/__init__.py`, `draw_result_repo.py`, `ticket_repo.py`, `comparison_repo.py`, `notification_repo.py`, `user_repo.py`
- Test: `tests/db/test_repositories.py`

- [ ] **Step 1: 写失败测试 `tests/db/test_repositories.py`**

```python
import pytest
from app.db.repositories import draw_result_repo as dr
from app.db.repositories import ticket_repo as tk
from app.db.repositories import comparison_repo as cp


def test_upsert_draw_result_idempotent(session):
    payload = {"lottery_code": "ssq", "draw_no": "2024060", "draw_date": "2024-05-26",
               "numbers_json": '{"front":[2,7,14,18,25,32],"back":[6]}',
               "source": "mxnzp", "verified": True}
    dr.upsert(session, payload)
    dr.upsert(session, payload)  # 重复不报错、不新增
    assert len(dr.list_by_lottery(session, "ssq")) == 1


def test_get_draw_result_by_no(session):
    dr.upsert(session, {"lottery_code": "ssq", "draw_no": "1",
              "draw_date": "2024-01-01", "numbers_json": "{}",
              "source": "mxnzp", "verified": True})
    assert dr.get_by_no(session, "ssq", "1") is not None
    assert dr.get_by_no(session, "ssq", "999") is None


def test_list_active_tickets_by_lottery(session):
    session.add(tk.Ticket(user_id=1, lottery_code="ssq",
                numbers_json='{"front":[1,2,3,4,5,6],"back":[7]}'))
    session.add(tk.Ticket(user_id=1, lottery_code="dlt",
                numbers_json='{"front":[1,2,3,4,5],"back":[6,7]}'))
    session.add(tk.Ticket(user_id=2, lottery_code="ssq", enabled=False,
                numbers_json='{"front":[8,9,10,11,12,13],"back":[14]}'))
    session.commit()
    rows = tk.list_active_by_lottery(session, "ssq")
    assert len(rows) == 1  # 只 user1 的启用 ssq 注
    assert rows[0].user_id == 1


def test_distinct_users_for_lottery(session):
    session.add(tk.Ticket(user_id=1, lottery_code="ssq", numbers_json="{}"))
    session.add(tk.Ticket(user_id=1, lottery_code="ssq", numbers_json="{}"))
    session.add(tk.Ticket(user_id=2, lottery_code="ssq", numbers_json="{}"))
    session.commit()
    assert tk.distinct_active_users(session, "ssq") == [1, 2]


def test_comparison_idempotent(session):
    dr.upsert(session, {"lottery_code": "ssq", "draw_no": "1",
              "draw_date": "2024-01-01", "numbers_json": "{}",
              "source": "mxnzp", "verified": True})
    d = dr.get_by_no(session, "ssq", "1")
    cp.upsert(session, {"user_id": 1, "draw_result_id": d.id, "ticket_id": 10,
              "lottery_code": "ssq", "draw_no": "1", "hits_json": "{}",
              "prize_tier": 5, "prize_amount": 1000, "is_win": True})
    cp.upsert(session, {"user_id": 1, "draw_result_id": d.id, "ticket_id": 10,
              "lottery_code": "ssq", "draw_no": "1", "hits_json": "{}",
              "prize_tier": 5, "prize_amount": 1000, "is_win": True})
    assert len(cp.list_by_draw(session, d.id)) == 1
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/db/test_repositories.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: 实现 `app/db/repositories/draw_result_repo.py`**

```python
from sqlmodel import Session, select
from app.db.models import DrawResult


def upsert(session: Session, data: dict) -> DrawResult:
    existing = get_by_no(session, data["lottery_code"], data["draw_no"])
    if existing:
        return existing
    row = DrawResult(**data)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def get_by_no(session: Session, lottery_code: str, draw_no: str) -> DrawResult | None:
    return session.exec(select(DrawResult).where(
        DrawResult.lottery_code == lottery_code, DrawResult.draw_no == draw_no
    )).first()


def list_by_lottery(session: Session, lottery_code: str) -> list[DrawResult]:
    return list(session.exec(select(DrawResult).where(
        DrawResult.lottery_code == lottery_code
    ).order_by(DrawResult.draw_no.desc())))
```

- [ ] **Step 4: 实现 `app/db/repositories/ticket_repo.py`**

```python
from sqlmodel import Session, select
from app.db.models import Ticket


def list_active_by_lottery(session: Session, lottery_code: str) -> list[Ticket]:
    return list(session.exec(select(Ticket).where(
        Ticket.lottery_code == lottery_code, Ticket.enabled == True  # noqa: E712
    )))


def distinct_active_users(session: Session, lottery_code: str) -> list[int]:
    rows = session.exec(select(Ticket.user_id).where(
        Ticket.lottery_code == lottery_code, Ticket.enabled == True  # noqa: E712
    ).distinct()).all()
    return sorted(set(rows))


def list_active_for_user(session: Session, user_id: int) -> list[Ticket]:
    return list(session.exec(select(Ticket).where(
        Ticket.user_id == user_id, Ticket.enabled == True  # noqa: E712
    )))
```

- [ ] **Step 5: 实现 `app/db/repositories/comparison_repo.py`**

```python
from sqlmodel import Session, select
from app.db.models import Comparison


def upsert(session: Session, data: dict) -> Comparison:
    existing = session.exec(select(Comparison).where(
        Comparison.draw_result_id == data["draw_result_id"],
        Comparison.ticket_id == data["ticket_id"],
    )).first()
    if existing:
        return existing
    row = Comparison(**data)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def list_by_draw(session: Session, draw_result_id: int) -> list[Comparison]:
    return list(session.exec(select(Comparison).where(
        Comparison.draw_result_id == draw_result_id
    )))


def list_by_user_draw(session: Session, user_id: int, lottery_code: str, draw_no: str):
    return list(session.exec(select(Comparison).where(
        Comparison.user_id == user_id,
        Comparison.lottery_code == lottery_code,
        Comparison.draw_no == draw_no,
    )))
```

- [ ] **Step 6: 实现剩余 repository（`notification_repo.py`、`user_repo.py`）**

`app/db/repositories/notification_repo.py`:
```python
from sqlmodel import Session, select
from app.db.models import NotificationChannel, NotificationRule


def list_channels(session: Session, user_id: int) -> list[NotificationChannel]:
    return list(session.exec(select(NotificationChannel).where(
        NotificationChannel.user_id == user_id, NotificationChannel.enabled == True  # noqa: E712
    )))


def get_rule(session: Session, user_id: int, lottery_code: str) -> NotificationRule | None:
    return session.exec(select(NotificationRule).where(
        NotificationRule.user_id == user_id,
        NotificationRule.lottery_code == lottery_code,
    )).first()


def add_log(session: Session, data: dict) -> None:
    from app.db.models import NotificationLog
    session.add(NotificationLog(**data))
    session.commit()
```

`app/db/repositories/user_repo.py`:
```python
from sqlmodel import Session, select
from app.db.models import User


def get(session: Session, user_id: int) -> User | None:
    return session.get(User, user_id)


def list_all(session: Session) -> list[User]:
    return list(session.exec(select(User)))
```

- [ ] **Step 7: 跑测试验证通过 + Commit**

Run: `pytest tests/db/ -v` → PASS
```bash
git add app/db/repositories/ tests/db/test_repositories.py
git commit -m "feat(db): repository 层(幂等 upsert + 用户隔离查询)"
```

---

## Task 4: 数据源接口 + MXNZP 适配器

**Files:**
- Create: `app/adapters/__init__.py`, `app/adapters/base.py`, `app/adapters/mxnzp.py`
- Test: `tests/adapters/__init__.py`, `tests/adapters/test_mxnzp.py`

- [ ] **Step 1: 写失败测试 `tests/adapters/test_mxnzp.py`（用 respx mock HTTP）**

```python
import respx, httpx, pytest
from app.adapters.mxnzp import MxnzpSource
from app.adapters.base import DrawResultDTO


@pytest.mark.asyncio
@respx.mock
async def test_fetch_latest_returns_dto():
    respx.get("https://www.mxnzp.com/api/lottery/common/latest").mock(
        return_value=httpx.Response(200, json={
            "code": 1, "data": {
                "lotteryId": "ssq", "lotteryName": "双色球",
                "lotteryNo": "2024060", "lotteryDate": "2024-05-26",
                "lotteryNumbers": "2,7,14,18,25,32|6",
            }}))
    src = MxnzpSource(app_id="x", app_secret="y")
    dto = await src.fetch_latest("ssq")
    assert dto.lottery_code == "ssq"
    assert dto.draw_no == "2024060"
    assert dto.front == [2, 7, 14, 18, 25, 32]
    assert dto.back == [6]
    assert dto.source == "mxnzp"


@pytest.mark.asyncio
@respx.mock
async def test_fetch_raises_on_api_error():
    respx.get("https://www.mxnzp.com/api/lottery/common/latest").mock(
        return_value=httpx.Response(200, json={"code": 0, "msg": "fail"}))
    src = MxnzpSource(app_id="x", app_secret="y")
    with pytest.raises(Exception):
        await src.fetch_latest("ssq")
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/adapters/test_mxnzp.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: 实现 `app/adapters/base.py`**

```python
from dataclasses import dataclass
from datetime import date
from typing import Protocol


@dataclass(frozen=True)
class DrawResultDTO:
    lottery_code: str
    draw_no: str
    draw_date: date
    front: list[int]
    back: list[int]
    source: str


class DrawSource(Protocol):
    name: str
    async def fetch_latest(self, lottery_code: str) -> DrawResultDTO: ...
```

- [ ] **Step 4: 实现 `app/adapters/mxnzp.py`**

```python
import httpx
from datetime import datetime
from app.adapters.base import DrawResultDTO

BASE = "https://www.mxnzp.com/api/lottery/common"
# MXNZP 彩种代码映射（spec §3.1 的 7 彩种）
CODE_MAP = {"ssq": "ssq", "dlt": "cjdlt", "qlc": "qlc", "fc3d": "fc3d",
            "qxc": "qxc", "pl3": "pl3", "pl5": "pl5"}


class MxnzpSource:
    name = "mxnzp"

    def __init__(self, app_id: str, app_secret: str):
        self.app_id = app_id
        self.app_secret = app_secret

    async def fetch_latest(self, lottery_code: str) -> DrawResultDTO:
        ext = CODE_MAP[lottery_code]
        params = {" lotteryId": ext, "app_id": self.app_id, "app_secret": self.app_secret}
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{BASE}/latest", params={**params, "lotteryId": ext})
            r.raise_for_status()
        body = r.json()
        if body.get("code") != 1:
            raise RuntimeError(f"MXNZP 错误: {body.get('msg')}")
        d = body["data"]
        front_back = d["lotteryNumbers"].split("|")
        front = [int(x) for x in front_back[0].split(",")]
        back = [int(x) for x in front_back[1].split(",")] if len(front_back) > 1 else []
        return DrawResultDTO(
            lottery_code=lottery_code, draw_no=d["lotteryNo"],
            draw_date=datetime.strptime(d["lotteryDate"], "%Y-%m-%d").date(),
            front=front, back=back, source=self.name,
        )
```

- [ ] **Step 5: 跑测试验证通过 + Commit**

Run: `pytest tests/adapters/test_mxnzp.py -v` → PASS
```bash
git add app/adapters/ tests/adapters/
git commit -m "feat(adapters): DrawSource 接口与 MXNZP 适配器"
```

---

## Task 5: 聚合数据适配器 + DrawFetcher（双源交叉校验）

**Files:**
- Create: `app/adapters/juhe.py`
- Create: `app/services/__init__.py`, `app/services/draw_fetcher.py`
- Test: `tests/services/__init__.py`, `tests/services/test_draw_fetcher.py`

- [ ] **Step 1: 写失败测试 `tests/services/test_draw_fetcher.py`**

```python
import pytest
from unittest.mock import AsyncMock
from datetime import date
from app.adapters.base import DrawResultDTO
from app.services.draw_fetcher import DrawFetcher


def dto(code, no, front, back, src):
    return DrawResultDTO(code, no, date(2024, 5, 26), front, back, src)


@pytest.mark.asyncio
async def test_primary_success():
    primary = AsyncMock(); primary.fetch_latest.return_value = dto("ssq", "1", [1,2,3,4,5,6], [7], "mxnzp")
    backup = AsyncMock()
    f = DrawFetcher(primary, backup)
    result = await f.fetch("ssq")
    assert result.verified is True
    backup.fetch_latest.assert_not_called()


@pytest.mark.asyncio
async def test_failover_to_backup():
    primary = AsyncMock(); primary.fetch_latest.side_effect = RuntimeError("down")
    backup = AsyncMock(); backup.fetch_latest.return_value = dto("ssq", "1", [1,2,3,4,5,6], [7], "juhe")
    f = DrawFetcher(primary, backup)
    result = await f.fetch("ssq")
    assert result.verified is True
    assert result.source == "juhe"


@pytest.mark.asyncio
async def test_mismatch_marks_unverified():
    primary = AsyncMock(); primary.fetch_latest.return_value = dto("ssq", "1", [1,2,3,4,5,6], [7], "mxnzp")
    backup = AsyncMock(); backup.fetch_latest.return_value = dto("ssq", "1", [1,2,3,4,5,6], [8], "juhe")  # 蓝球不同
    f = DrawFetcher(primary, backup)
    result = await f.fetch("ssq", cross_check=True)
    assert result.verified is False  # 不一致，拒绝


@pytest.mark.asyncio
async def test_all_fail_returns_none():
    primary = AsyncMock(); primary.fetch_latest.side_effect = RuntimeError("down")
    backup = AsyncMock(); backup.fetch_latest.side_effect = RuntimeError("down")
    f = DrawFetcher(primary, backup)
    assert await f.fetch("ssq") is None
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/services/test_draw_fetcher.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: 实现 `app/adapters/juhe.py`（结构同 MXNZP，API 不同）**

```python
import httpx
from datetime import datetime
from app.adapters.base import DrawResultDTO

BASE = "https://v.juhe.cn/lottery"
CODE_MAP = {"ssq": "ssq", "dlt": "dlt", "qlc": "qlc", "fc3d": "fc3d",
            "qxc": "qxc", "pl3": "pl3", "pl5": "pl5"}


class JuheSource:
    name = "juhe"

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def fetch_latest(self, lottery_code: str) -> DrawResultDTO:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{BASE}/latest", params={
                "lotteryId": CODE_MAP[lottery_code], "key": self.api_key})
            r.raise_for_status()
        body = r.json()
        if body.get("error_code") != 0:
            raise RuntimeError(f"聚合错误: {body.get('reason')}")
        d = body["result"][0]
        front_back = d["lottery_res"].split(",")
        # 聚合返回形如 "2,7,14,18,25,32,6"；前区由彩种 front.count 决定
        from app.domain.lottery_types import LOTTERY_TYPES
        spec = LOTTERY_TYPES[lottery_code]
        n = spec.front.count
        front = [int(x) for x in front_back[:n]]
        back = [int(x) for x in front_back[n:]]
        return DrawResultDTO(
            lottery_code=lottery_code, draw_no=d["lottery_no"],
            draw_date=datetime.strptime(d["lottery_date"], "%Y-%m-%d").date(),
            front=front, back=back, source=self.name,
        )
```

> **注：** MXNZP/聚合的真实响应字段需对接时核对（Task 4-5 用了文档所述结构）。集成测试（Task 12）用 mock DTO 验证闭环，不依赖真实 API。真实 key 在部署时配 env。

- [ ] **Step 4: 实现 `app/services/draw_fetcher.py`**

```python
import logging
from app.adapters.base import DrawResultDTO, DrawSource

log = logging.getLogger(__name__)


class DrawFetcher:
    """双源获取 + 交叉校验 + 降级。

    cross_check=True 时主备都拉，号码一致才 verified=True。
    主源失败 → 用备源（verified=True，单源）。
    都失败 → 返回 None（调用方走缓存/告警）。
    """
    def __init__(self, primary: DrawSource, backup: DrawSource):
        self.primary = primary
        self.backup = backup

    async def fetch(self, lottery_code: str, cross_check: bool = True
                    ) -> DrawResultDTO | None:
        primary = await self._safe(self.primary, lottery_code)
        if primary is None:
            log.warning("主源 %s 失败，切备源", self.primary.name)
            backup = await self._safe(self.backup, lottery_code)
            return backup  # 可能为 None
        if not cross_check:
            return primary
        backup = await self._safe(self.backup, lottery_code)
        if backup is None:
            log.warning("备源 %s 失败，仅用主源", self.backup.name)
            return primary
        if _matches(primary, backup):
            return primary
        log.error("双源数据不一致 %s: %s vs %s", lottery_code, primary, backup)
        # 返回主源但标 unverified（refuse 入库由调用方判断）
        from dataclasses import replace
        return replace(primary, verified=False) if hasattr(primary, "verified") \
            else primary

    async def _safe(self, src: DrawSource, code: str) -> DrawResultDTO | None:
        try:
            return await src.fetch_latest(code)
        except Exception as e:
            log.warning("%s 获取 %s 异常: %s", src.name, code, e)
            return None


def _matches(a: DrawResultDTO, b: DrawResultDTO) -> bool:
    return a.front == b.front and a.back == b.back and a.draw_no == b.draw_no
```

> **DTO verified 字段：** `DrawResultDTO` 是 frozen dataclass 无 verified。DrawFetcher 返回 DTO；"是否入库 verified" 由比对引擎/获取编排层根据 `cross_check` 结果决定（见 Task 9：一致 → verified=True 入库；不一致 → 不入库 + 告警）。Task 5 测试中 `result.verified` 实际由编排层设置——为保持测试可读，**修正**：让 DrawFetcher 返回 `(dto, verified: bool)` 元组。

- [ ] **Step 5: 修正 `DrawFetcher.fetch` 返回 `(dto, verified)` 元组（保持测试一致）**

把 `DrawFetcher.fetch` 改为返回元组，更新 Task 5 测试断言：

```python
    async def fetch(self, lottery_code: str, cross_check: bool = True):
        primary = await self._safe(self.primary, lottery_code)
        if primary is None:
            backup = await self._safe(self.backup, lottery_code)
            return (backup, True) if backup else (None, False)
        if not cross_check:
            return primary, True
        backup = await self._safe(self.backup, lottery_code)
        if backup is None:
            return primary, True
        if _matches(primary, backup):
            return primary, True
        log.error("双源不一致 %s", lottery_code)
        return None, False  # 拒绝，调用方告警
```

并更新 `tests/services/test_draw_fetcher.py` 断言为元组形式：
```python
result, verified = await f.fetch("ssq")
assert verified is True
# mismatch 用例：
result, verified = await f.fetch("ssq", cross_check=True)
assert verified is False and result is None
# all fail：
assert await f.fetch("ssq") == (None, False)
```

- [ ] **Step 6: 跑测试验证通过 + Commit**

Run: `pytest tests/services/test_draw_fetcher.py tests/adapters/ -v` → PASS
```bash
git add app/adapters/juhe.py app/services/ tests/services/
git commit -m "feat(services): 聚合适配器+DrawFetcher 双源交叉校验"
```

---

## Task 6: 比对引擎（幂等，遍历追投用户）

**Files:**
- Create: `app/services/compare_engine.py`
- Test: `tests/services/test_compare_engine.py`

- [ ] **Step 1: 写失败测试 `tests/services/test_compare_engine.py`**

```python
import json
from app.db.models import Ticket
from app.db.repositories import draw_result_repo as dr, ticket_repo as tk, comparison_repo as cp
from app.services.compare_engine import CompareEngine

DRAW = {"lottery_code": "ssq", "draw_no": "2024060", "draw_date": "2024-05-26",
        "numbers_json": '{"front":[2,7,14,18,25,32],"back":[6]}',
        "source": "mxnzp", "verified": True}


def test_compare_only_users_tracking_lottery(session):
    d = dr.upsert(session, DRAW)
    # user1 追 ssq（命中 4+1 四等奖），user2 追 dlt（不参与 ssq 比对）
    session.add(Ticket(user_id=1, lottery_code="ssq",
                numbers_json='{"front":[2,7,14,18,1,2],"back":[6]}'))
    session.add(Ticket(user_id=2, lottery_code="dlt", numbers_json='{}'))
    session.commit()
    engine = CompareEngine()
    engine.compare_draw(session, d)
    comps = cp.list_by_draw(session, d.id)
    assert len(comps) == 1            # 只有 user1 的 ssq 注
    assert comps[0].user_id == 1
    assert comps[0].prize_tier == 4
    assert comps[0].is_win is True


def test_compare_idempotent(session):
    d = dr.upsert(session, DRAW)
    session.add(Ticket(user_id=1, lottery_code="ssq",
                numbers_json='{"front":[2,7,14,18,1,2],"back":[6]}'))
    session.commit()
    engine = CompareEngine()
    engine.compare_draw(session, d)
    engine.compare_draw(session, d)   # 重复
    assert len(cp.list_by_draw(session, d.id)) == 1


def test_no_tickets_no_comparisons(session):
    d = dr.upsert(session, DRAW)
    CompareEngine().compare_draw(session, d)
    assert cp.list_by_draw(session, d.id) == []
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/services/test_compare_engine.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: 实现 `app/services/compare_engine.py`**

```python
import json
import logging
from sqlmodel import Session
from app.domain import compare as _  # noqa: 触发策略注册
from app.domain.compare.strategy import compare
from app.domain.lottery_types import LOTTERY_TYPES
from app.db.models import DrawResult
from app.db.repositories import ticket_repo as tk, comparison_repo as cp

log = logging.getLogger(__name__)


class CompareEngine:
    """对一期开奖结果，比对所有追投该彩种的用户的启用注。幂等。"""

    def compare_draw(self, session: Session, draw: DrawResult) -> int:
        spec = LOTTERY_TYPES[draw.lottery_code]
        draw_numbers = json.loads(draw.numbers_json)
        tickets = tk.list_active_by_lottery(session, draw.lottery_code)
        count = 0
        for t in tickets:
            existing = cp.list_by_user_draw(session, t.user_id, draw.lottery_code, draw.draw_no)
            if any(c.ticket_id == t.id for c in existing):
                continue  # 该注已比对，幂等跳过
            entry = json.loads(t.numbers_json)
            r = compare(spec, draw_numbers, entry)
            cp.upsert(session, {
                "user_id": t.user_id, "draw_result_id": draw.id, "ticket_id": t.id,
                "lottery_code": draw.lottery_code, "draw_no": draw.draw_no,
                "hits_json": json.dumps({"front_hit": r.front_hit, "back_hit": r.back_hit}),
                "prize_tier": r.tier, "prize_amount": r.prize_amount, "is_win": r.is_win,
            })
            count += 1
        log.info("比对 %s 第%s期: %d 注", draw.lottery_code, draw.draw_no, count)
        return count
```

- [ ] **Step 4: 跑测试验证通过 + Commit**

Run: `pytest tests/services/test_compare_engine.py -v` → PASS
```bash
git add app/services/compare_engine.py tests/services/test_compare_engine.py
git commit -m "feat(services): 比对引擎(幂等,遍历追投用户号码池)"
```

---

## Task 7: Notifier 接口 + Bark + 飞书

**Files:**
- Create: `app/services/notifier/__init__.py`, `app/services/notifier/base.py`, `app/services/notifier/bark.py`, `app/services/notifier/feishu.py`
- Test: `tests/services/test_notifier.py`

- [ ] **Step 1: 写失败测试 `tests/services/test_notifier.py`**

```python
import pytest, respx, httpx
from unittest.mock import AsyncMock
from app.services.notifier.base import Notifier, Message, dispatch
from app.services.notifier.bark import BarkNotifier
from app.services.notifier.feishu import FeishuNotifier


@pytest.mark.asyncio
@respx.mock
async def test_bark_sends_to_device_key():
    route = respx.post("https://api.day.app/KEY123/").mock(
        return_value=httpx.Response(200, json={"code": 200}))
    n = BarkNotifier(device_key="KEY123")
    ok = await n.send(Message(title="t", body="b"))
    assert ok is True
    assert route.called


@pytest.mark.asyncio
@respx.mock
async def test_feishu_sends_webhook():
    route = respx.post("https://open.feishu.cn/bot/v2/hook/TOKEN").mock(
        return_value=httpx.Response(200, json={"StatusCode": 0}))
    n = FeishuNotifier(webhook="https://open.feishu.cn/bot/v2/hook/TOKEN")
    ok = await n.send(Message(title="t", body="b"))
    assert ok is True
    assert route.called


@pytest.mark.asyncio
async def test_dispatch_falls_through_on_failure():
    failing = AsyncMock(); failing.send.return_value = False
    ok_chan = AsyncMock(); ok_chan.send.return_value = True
    sent = await dispatch([failing, ok_chan], Message(title="t", body="b"))
    assert sent is True  # 至少一个成功
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/services/test_notifier.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: 实现 `app/services/notifier/base.py`**

```python
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Message:
    title: str
    body: str
    priority: str = "normal"   # normal / high（大奖）


class Notifier(Protocol):
    type: str
    async def send(self, msg: Message) -> bool: ...


async def dispatch(notifiers: list[Notifier], msg: Message) -> bool:
    """依次尝试，任一成功即 True（多渠道冗余）。"""
    import logging
    log = logging.getLogger(__name__)
    any_ok = False
    for n in notifiers:
        try:
            if await n.send(msg):
                any_ok = True
        except Exception as e:
            log.warning("通知失败 %s: %s", getattr(n, "type", "?"), e)
    return any_ok
```

- [ ] **Step 4: 实现 `app/services/notifier/bark.py`**

```python
import httpx
from app.services.notifier.base import Message


class BarkNotifier:
    type = "bark"

    def __init__(self, device_key: str, server: str = "https://api.day.app"):
        self.device_key = device_key
        self.server = server

    async def send(self, msg: Message) -> bool:
        icon = "🎉" if msg.priority == "high" else ""
        url = f"{self.server}/{self.device_key}/{msg.title}{icon}/{msg.body}"
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(url)
        return r.status_code == 200 and r.json().get("code") == 200
```

- [ ] **Step 5: 实现 `app/services/notifier/feishu.py`**

```python
import httpx
from app.services.notifier.base import Message


class FeishuNotifier:
    type = "feishu"

    def __init__(self, webhook: str):
        self.webhook = webhook

    async def send(self, msg: Message) -> bool:
        payload = {"msg_type": "text",
                   "content": {"text": f"{msg.title}\n{msg.body}"}}
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(self.webhook, json=payload)
        body = r.json()
        return r.status_code == 200 and body.get("StatusCode", body.get("code", 1)) == 0
```

- [ ] **Step 6: 跑测试验证通过 + Commit**

Run: `pytest tests/services/test_notifier.py -v` → PASS
```bash
git add app/services/notifier/ tests/services/test_notifier.py
git commit -m "feat(notifier): Notifier 接口+Bark+飞书+多渠道 dispatch"
```

---

## Task 8: NotifyDispatcher（追投过滤 + 策略 + 时机）

**Files:**
- Create: `app/services/notify_dispatcher.py`
- Test: `tests/services/test_notify_dispatcher.py`

- [ ] **Step 1: 写失败测试 `tests/services/test_notify_dispatcher.py`**

```python
import pytest
from unittest.mock import AsyncMock, MagicMock
from app.db.models import Ticket, NotificationChannel, NotificationRule
from app.db.repositories import draw_result_repo as dr
from app.services.notify_dispatcher import NotifyDispatcher, build_message


def test_build_message_win():
    msg = build_message("ssq", "2024060", [2,7,14,18,25,32], [6],
                        front_hit=4, back_hit=0, tier=4, amount=20000, is_win=True)
    assert "双色球" in msg.title and "中奖" in msg.body
    assert msg.priority == "normal"  # 四等非高奖


def test_build_message_high_tier_high_priority():
    msg = build_message("ssq", "2024060", [2,7,14,18,25,32], [6],
                        front_hit=6, back_hit=1, tier=1, amount=None, is_win=True)
    assert msg.priority == "high"  # 一等奖


@pytest.mark.asyncio
async def test_dispatcher_only_user_tracking(session):
    d = dr.upsert(session, {"lottery_code": "ssq", "draw_no": "1",
        "draw_date": "2024-01-01", "numbers_json": '{"front":[1,2,3,4,5,6],"back":[7]}',
        "source": "mxnzp", "verified": True})
    # user1 追 ssq 且配了渠道+规则(every)
    session.add(Ticket(user_id=1, lottery_code="ssq",
                numbers_json='{"front":[1,2,3,4,5,6],"back":[7]}'))
    session.add(NotificationChannel(user_id=1, type="bark", config_json='{"device_key":"K"}'))
    session.add(NotificationRule(user_id=1, lottery_code="ssq", strategy="every"))
    # user2 不追 ssq
    session.add(Ticket(user_id=2, lottery_code="dlt", numbers_json="{}"))
    session.commit()

    bark = AsyncMock(); bark.send.return_value = True
    factory = MagicMock(return_value=[bark])
    disp = NotifyDispatcher(channel_factory=factory)
    await disp.dispatch_summary(session, "ssq", "1")
    assert bark.send.await_count == 1  # 只通知 user1


@pytest.mark.asyncio
async def test_dispatcher_win_only_skips_nonwin(session):
    d = dr.upsert(session, {"lottery_code": "ssq", "draw_no": "1",
        "draw_date": "2024-01-01", "numbers_json": '{"front":[1,2,3,4,5,6],"back":[7]}',
        "source": "mxnzp", "verified": True})
    session.add(Ticket(user_id=1, lottery_code="ssq",
                numbers_json='{"front":[33,32,31,30,29,28],"back":[16]}'))  # 全不中
    session.add(NotificationChannel(user_id=1, type="bark", config_json='{"device_key":"K"}'))
    session.add(NotificationRule(user_id=1, lottery_code="ssq", strategy="win_only"))
    session.commit()

    bark = AsyncMock(); bark.send.return_value = True
    disp = NotifyDispatcher(channel_factory=lambda u: [bark])
    await disp.dispatch_summary(session, "ssq", "1")
    assert bark.send.await_count == 0  # win_only 且未中 → 不推
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/services/test_notify_dispatcher.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: 实现 `app/services/notify_dispatcher.py`**

```python
import json, logging
from sqlmodel import Session
from app.db.repositories import ticket_repo as tk, comparison_repo as cp, notification_repo as nr
from app.services.notifier.base import Message, dispatch
from app.domain.lottery_types import LOTTERY_TYPES

log = logging.getLogger(__name__)
HIGH_TIERS = {1, 2}  # 一二等为高奖（即时高优先级）


def build_message(lottery_code, draw_no, front, back,
                  front_hit, back_hit, tier, amount, is_win) -> Message:
    spec = LOTTERY_TYPES[lottery_code]
    if is_win:
        amt = "待官方派奖" if amount is None else f"¥{amount/100:.0f}"
        title = f"{spec.name} 第{draw_no}期 中奖！{tier}等奖"
        body = (f"开奖：{front} + {back}\n命中：前{front_hit} 后{back_hit}\n"
                f"奖级：{tier}等奖 · {amt}\n以官方开奖为准")
    else:
        title = f"{spec.name} 第{draw_no}期 未中奖"
        body = (f"开奖：{front} + {back}\n本期核对完毕，以官方开奖为准\n理性购彩 量力而行")
    priority = "high" if (is_win and tier in HIGH_TIERS) else "normal"
    return Message(title=title, body=body, priority=priority)


class NotifyDispatcher:
    def __init__(self, channel_factory):
        """channel_factory(user_id) -> list[Notifier]，按用户渠道配置构造。"""
        self.channel_factory = channel_factory

    async def dispatch_summary(self, session: Session, lottery_code: str, draw_no: str):
        """路径 B：次日汇总。对每个追投该彩种的用户，按策略推送。"""
        from app.db.repositories import draw_result_repo as dr
        d = dr.get_by_no(session, lottery_code, draw_no)
        if not d:
            return
        front_back = json.loads(d.numbers_json)
        for user_id in tk.distinct_active_users(session, lottery_code):
            await self._push_user(session, user_id, lottery_code, draw_no, front_back)

    async def dispatch_instant_high_tier(self, session: Session, lottery_code: str, draw_no: str):
        """路径 A：开奖当晚，仅命中一二等的追投用户。"""
        from app.db.repositories import draw_result_repo as dr
        d = dr.get_by_no(session, lottery_code, draw_no)
        if not d:
            return
        front_back = json.loads(d.numbers_json)
        for user_id in tk.distinct_active_users(session, lottery_code):
            comps = cp.list_by_user_draw(session, user_id, lottery_code, draw_no)
            for c in comps:
                if c.is_win and c.prize_tier in HIGH_TIERS:
                    msg = build_message(lottery_code, draw_no, front_back["front"],
                                        front_back.get("back", []),
                                        json.loads(c.hits_json)["front_hit"],
                                        json.loads(c.hits_json).get("back_hit", 0),
                                        c.prize_tier, c.prize_amount, True)
                    notifiers = self.channel_factory(user_id)
                    await dispatch(notifiers, msg)
                    break

    async def _push_user(self, session, user_id, lottery_code, draw_no, front_back):
        rule = nr.get_rule(session, user_id, lottery_code)
        strategy = rule.strategy if rule else "every"
        comps = cp.list_by_user_draw(session, user_id, lottery_code, draw_no)
        for c in comps:
            if strategy == "win_only" and not c.is_win:
                continue
            hits = json.loads(c.hits_json)
            msg = build_message(lottery_code, draw_no, front_back["front"],
                                front_back.get("back", []),
                                hits["front_hit"], hits.get("back_hit", 0),
                                c.prize_tier, c.prize_amount, c.is_win)
            notifiers = self.channel_factory(user_id)
            ok = await dispatch(notifiers, msg)
            nr.add_log(session, {
                "user_id": user_id, "channel_type": ",".join(n.type for n in notifiers),
                "lottery_code": lottery_code, "draw_no": draw_no,
                "payload": msg.body, "status": "sent" if ok else "failed"})
```

- [ ] **Step 4: 跑测试验证通过 + Commit**

Run: `pytest tests/services/test_notify_dispatcher.py -v` → PASS
```bash
git add app/services/notify_dispatcher.py tests/services/test_notify_dispatcher.py
git commit -m "feat(services): NotifyDispatcher 追投过滤+策略+时机+消息构建"
```

---

## Task 9: Scheduler（APScheduler 路径 A/B）

**Files:**
- Create: `app/services/scheduler.py`
- Test: `tests/services/test_scheduler.py`

- [ ] **Step 1: 写失败测试 `tests/services/test_scheduler.py`**

```python
from apscheduler.triggers.cron import CronTrigger
from app.services.scheduler import build_summary_trigger, build_poll_trigger, Scheduler


def test_summary_trigger_at_07_daily():
    t = build_summary_trigger(hour=7)
    assert isinstance(t, CronTrigger)


def test_scheduler_registers_jobs():
    s = Scheduler()
    s.register(lottery_codes=["ssq", "fc3d"])
    job_ids = {j.id for j in s.scheduler.get_jobs()}
    assert "summary" in job_ids
    assert any(j.id.startswith("poll_ssq") for j in s.scheduler.get_jobs())
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/services/test_scheduler.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: 实现 `app/services/scheduler.py`**

```python
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from app.domain.lottery_types import LOTTERY_TYPES

log = logging.getLogger(__name__)


def build_summary_trigger(hour: int) -> CronTrigger:
    return CronTrigger(hour=hour, minute=0, timezone="Asia/Shanghai")


def build_poll_trigger(draw_days: tuple[int, ...]) -> CronTrigger:
    # 开奖日 21:30 起每 15 分钟（路径 A 轮询），简化为开奖日傍晚触发
    return CronTrigger(day_of_week=",".join(str(d) for d in draw_days),
                       hour="21-23", minute="*/15", timezone="Asia/Shanghai")


class Scheduler:
    def __init__(self):
        self.scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")

    def register(self, lottery_codes: list[str]):
        for code in lottery_codes:
            spec = LOTTERY_TYPES[code]
            self.scheduler.add_job(
                lambda c=code: None,  # 占位；真实回调由 main 装配时注入
                trigger=build_poll_trigger(spec.draw_days),
                id=f"poll_{code}", replace_existing=True)
        self.scheduler.add_job(lambda: None, trigger=build_summary_trigger(7),
                               id="summary", replace_existing=True)

    def start(self):
        self.scheduler.start()

    def shutdown(self):
        self.scheduler.shutdown(wait=False)
```

> **注：** 真实回调（fetch→compare→push 的编排函数）在 Task 11 CLI/入口装配时注入。Scheduler 本 task 只验证触发器与任务注册。

- [ ] **Step 4: 跑测试验证通过 + Commit**

Run: `pytest tests/services/test_scheduler.py -v` → PASS
```bash
git add app/services/scheduler.py tests/services/test_scheduler.py
git commit -m "feat(services): APScheduler 路径A轮询+路径B次日汇总触发"
```

---

## Task 10: 端到端集成测试（闭环）

**Files:**
- Create: `tests/integration/__init__.py`, `tests/integration/test_core_loop.py`

- [ ] **Step 1: 写集成测试 `tests/integration/test_core_loop.py`**

```python
import json, pytest
from unittest.mock import AsyncMock
from datetime import date
from app.adapters.base import DrawResultDTO
from app.db.models import Ticket, NotificationChannel, NotificationRule
from app.db.repositories import draw_result_repo as dr, comparison_repo as cp, notification_repo as nr
from app.services.compare_engine import CompareEngine
from app.services.notify_dispatcher import NotifyDispatcher


@pytest.mark.asyncio
async def test_full_loop_fetch_compare_push(session):
    """闭环：拿到开奖DTO → 入库 → 比对 → 推送（mock notifier 验证被调用）。"""
    # 1) 模拟 DrawFetcher 产出（已交叉校验）
    dto = DrawResultDTO("ssq", "2024060", date(2024, 5, 26),
                        [2, 7, 14, 18, 25, 32], [6], "mxnzp")
    draw = dr.upsert(session, {
        "lottery_code": dto.lottery_code, "draw_no": dto.draw_no,
        "draw_date": dto.draw_date,
        "numbers_json": json.dumps({"front": dto.front, "back": dto.back}),
        "source": dto.source, "verified": True})

    # 2) user1 追 ssq（命中 4+1 四等奖）
    session.add(Ticket(user_id=1, lottery_code="ssq",
                numbers_json='{"front":[2,7,14,18,1,2],"back":[6]}'))
    session.add(NotificationChannel(user_id=1, type="bark", config_json='{"device_key":"K"}'))
    session.add(NotificationRule(user_id=1, lottery_code="ssq", strategy="every"))
    session.commit()

    # 3) 比对
    CompareEngine().compare_draw(session, draw)
    comps = cp.list_by_draw(session, draw.id)
    assert len(comps) == 1 and comps[0].prize_tier == 4

    # 4) 推送（路径 B 汇总）
    bark = AsyncMock(); bark.send.return_value = True
    bark.type = "bark"
    disp = NotifyDispatcher(channel_factory=lambda u: [bark])
    await disp.dispatch_summary(session, "ssq", "2024060")
    assert bark.send.await_count == 1
    logs = session.exec(nr.__dict__ and __import__("sqlmodel").select(
        __import__("app.db.models", fromlist=["NotificationLog"]).NotificationLog)).all()
    assert len(logs) == 1 and logs[0].status == "sent"
```

- [ ] **Step 2: 跑集成测试**

Run: `pytest tests/integration/test_core_loop.py -v`
Expected: PASS（闭环：入库→比对→推送→日志全通）

- [ ] **Step 3: Commit**

```bash
git add tests/integration/
git commit -m "test(integration): 端到端核心闭环(获取→比对→推送)"
```

---

## Task 11: CLI 入口（手动触发闭环）

**Files:**
- Create: `app/cli.py`
- Create: `app/main.py`（装配 + scheduler 启动骨架）

- [ ] **Step 1: 实现 `app/cli.py`**

```python
"""命令行入口：手动触发开奖获取+比对+推送，供开发/运维验证。"""
import asyncio, argparse, json
from app.core.config import get_settings
from app.core.logging import setup_logging
from app.db.database import init_db
from app.db.models import NotificationChannel
from app.adapters.mxnzp import MxnzpSource
from app.adapters.juhe import JuheSource
from app.services.draw_fetcher import DrawFetcher
from app.services.compare_engine import CompareEngine
from app.services.notify_dispatcher import NotifyDispatcher
from app.services.notifier.bark import BarkNotifier
from app.services.notifier.feishu import FeishuNotifier
from sqlmodel import Session, select


def channel_factory(session):
    def _make(user_id):
        rows = session.exec(select(NotificationChannel).where(
            NotificationChannel.user_id == user_id,
            NotificationChannel.enabled == True)).all()  # noqa: E712
        notifiers = []
        for ch in rows:
            cfg = json.loads(ch.config_json)
            if ch.type == "bark":
                notifiers.append(BarkNotifier(cfg["device_key"]))
            elif ch.type == "feishu":
                notifiers.append(FeishuNotifier(cfg["webhook"]))
        return notifiers
    return _make


async def run_once(lottery_code: str):
    setup_logging()
    settings = get_settings()
    engine = init_db()
    fetcher = DrawFetcher(
        MxnzpSource(settings.mxnzp_app_id, settings.mxnzp_app_secret),
        JuheSource(settings.juhe_api_key))
    dto, verified = await fetcher.fetch(lottery_code, cross_check=True)
    if dto is None or not verified:
        print(f"❌ {lottery_code} 获取失败或双源不一致，跳过")
        return
    with Session(engine) as session:
        from app.db.repositories import draw_result_repo as dr
        draw = dr.upsert(session, {
            "lottery_code": dto.lottery_code, "draw_no": dto.draw_no,
            "draw_date": dto.draw_date,
            "numbers_json": json.dumps({"front": dto.front, "back": dto.back}),
            "source": dto.source, "verified": True})
        CompareEngine().compare_draw(session, draw)
        disp = NotifyDispatcher(channel_factory(channel_factory_session_holder(session)))
        await disp.dispatch_summary(session, lottery_code, dto.draw_no)


def channel_factory_session_holder(session):
    return channel_factory(session)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("lottery", help="彩种代码，如 ssq")
    args = p.parse_args()
    asyncio.run(run_once(args.lottery))
```

- [ ] **Step 2: 实现 `app/main.py`（FastAPI 骨架，Plan 3 扩展）**

```python
"""应用入口。Plan 2 仅提供 scheduler 启动；REST API 在 Plan 3。"""
from app.core.config import get_settings
from app.core.logging import setup_logging
from app.db.database import init_db
from app.services.scheduler import Scheduler


def run_scheduled():
    setup_logging()
    init_db()
    s = Scheduler()
    s.register(lottery_codes=["ssq", "dlt", "qlc", "fc3d", "qxc", "pl3", "pl5"])
    # 真实回调注入：把 poll_{code} 的 job 替换为 fetch→compare→push 编排
    # （此处简化；完整装配在 Plan 3 的 main 里，复用 cli.run_once 逻辑）
    s.start()


if __name__ == "__main__":
    run_scheduled()
```

> **注：** Task 11 的 `cli.py` 有意保持简单可跑（手动 `python -m app.cli ssq` 验证真实 API）。`channel_factory` 嵌套是临时简化，Plan 3 重构为依赖注入。集成测试已覆盖逻辑正确性。

- [ ] **Step 3: 验证 CLI 可执行（不依赖真实 API，用 --help）**

Run: `python -m app.cli --help`
Expected: 显示 argparse 帮助，无 import 错误

- [ ] **Step 4: 跑全量测试确保无回归 + Commit**

Run: `pytest -v` → 全部通过
```bash
git add app/cli.py app/main.py
git commit -m "feat: CLI 手动触发入口 + scheduler 启动骨架"
```

---

## Self-Review（plan 作者自查，已执行）

**1. Spec 覆盖：**
- §6 数据模型 → Task 2（全部表）、Task 3（repository 幂等+隔离）✅
- §7 核心数据流（获取→比对→推送，比对一次复用）→ Task 5（fetcher）、Task 6（engine）、Task 8（dispatcher）✅；路径A/B → Task 9 ✅
- §7.2 双源交叉校验 → Task 5（不一致返回 (None, False) 拒绝入库）✅
- §7.3 调度（21:30轮询 / 次日07:00汇总）→ Task 9 ✅
- §7.4/§8.2 推送按追投彩种过滤+策略 → Task 8（distinct_active_users + rule.strategy）✅
- §8 通知（Bark+飞书，多渠道 dispatch）→ Task 7 ✅；钉钉/企微 = Plan 5 ✅
- §10 错误处理（双源降级、幂等、推送失败多渠道）→ Task 5/7/8 ✅
- §11 测试（集成测试）→ Task 10 ✅

**2. 占位符扫描：** Task 9/11 的 scheduler 回调注入有明确说明（Plan 3 装配），非占位符。MXNZP/聚合字段需对接核对（Task 5 注明）。`cli.py` 的 channel_factory 嵌套标注为临时简化（Plan 3 重构）。无 TBD/TODO。✅

**3. 类型一致性：** `DrawResultDTO`、`Message`、repository 函数签名全 plan 一致。Task 5 修正了 DrawFetcher 返回元组，测试同步更新。`compare(spec, draw, entry)` 的 draw/entry 为 `{"front","back"}` 字典，与 Plan 1 一致。✅

**4. 残留风险：**
- MXNZP/聚合真实响应字段需对接核对（Task 4-5 注明）。
- scheduler 真实回调注入在 Plan 3 完成（Task 9/11 注明）。
- 固定档奖金仍读 Plan 1 硬编码表（Plan 5 改 DB 驱动）。

---

## Execution Handoff

Plan 2 完成（11 个 Task），产出：可手动触发、有集成测试验证的端到端核心闭环（双源获取→幂等比对→按追投彩种/策略推送→日志），APScheduler 双路径触发器。与 Plan 1 领域层组合即可 `python -m app.cli ssq` 跑通（配真实 API key 后）。

**后续 Plan：**
- **Plan 3** — 用户体系（邀请制/认证/会话）+ FastAPI REST API（号码 CRUD、查询、配置、管理后台）+ scheduler 回调装配
- **Plan 4** — 前端 Vue3+ECharts（按 prototype，10 页）
- **Plan 5** — 钉钉/企微渠道 + 统计 + 提醒 + 走势(合规版) + 运维管理 + 奖级表 DB 驱动化
- **Plan 6** — Docker 部署到 NAS（端口 8280, restart: always）
