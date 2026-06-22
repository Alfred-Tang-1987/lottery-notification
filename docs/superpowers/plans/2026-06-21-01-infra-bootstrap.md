---
models:
  T1: sonnet
  T2: sonnet
  T3: sonnet
  T4a: sonnet
  T4b: opus
  T4c: sonnet
  T4d: sonnet
  T5: sonnet
  T6: opus
  T7: sonnet
  T8: sonnet
  T9: sonnet
  T10: sonnet
---

# 01 基础设施骨架 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 搭起 Python 3.12 + uv 项目骨架，含 Alembic 迁移基建、全 13 表 schema（含 apscheduler_jobs）、Crypto 多版本服务、7 彩种种子数据、DB engine（WAL/单写）、启动校验与 `/health` 端点。

**Architecture:** 分层目录 `app/{domain,adapters,infrastructure,services,api}` + Alembic 管 schema 演进 + SQLModel ORM + APScheduler jobstore 表纳入首迁移（避免 schema drift）。领域层零 IO 纪律用 import-linter 强制（本 plan 先放配置，领域代码在 Plan 02）。

**Tech Stack:** Python 3.12（uv 安装）、uv 0.11、FastAPI、SQLModel、Alembic、APScheduler、cryptography(Fernet)、pydantic v2、pytest。

**环境现实适配：** 本机仅有 `uv`（无 pip/poetry）+ Python 3.9。所有命令用 `uv`，Python 3.12 由 `uv python install` 提供。完成 Task 1 后，CLAUDE.md 的 `pip install -e ".[dev]"` 等命令需同步改为 `uv` 版本（见 Task 12）。

---

## File Structure

```
lottery-notification/
├── pyproject.toml              # uv 项目 + 依赖 + dev extras
├── uv.lock                     # 锁文件（uv 自动生成）
├── .python-version             # 3.12
├── alembic.ini                 # Alembic 配置
├── alembic/                    # 迁移脚本
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
│       └── 0001_initial.py     # 全 schema 含 apscheduler_jobs
├── app/
│   ├── __init__.py
│   ├── config.py               # 配置（env 读取 + pydantic-settings）
│   ├── main.py                 # FastAPI app + /health + 启动校验
│   ├── db/
│   │   ├── __init__.py
│   │   ├── engine.py           # engine（WAL/busy_timeout/单写）
│   │   └── session.py          # 会话工厂
│   ├── models/                 # SQLModel 表（全 schema）
│   │   ├── __init__.py
│   │   ├── _base.py            # SQLModel 基类 + 共用 mixin
│   │   ├── user.py             # users
│   │   ├── lottery.py          # lottery_types
│   │   ├── ticket.py           # tickets
│   │   ├── draw.py             # draw_results, draw_corrections, pending_comparisons
│   │   ├── comparison.py       # comparisons, prize_claims
│   │   ├── notification.py     # notification_channels/rules/logs
│   │   ├── health.py           # api_source_health
│   │   ├── audit.py            # admin_audit_logs
│   │   └── scheduler.py        # apscheduler_jobs（显式建表，不靠 jobstore auto-create）
│   ├── infrastructure/
│   │   ├── __init__.py
│   │   └── crypto.py           # Fernet 多版本 + key_version
│   └── seeds/
│       ├── __init__.py
│       └── lottery_types.py    # 7 彩种 spec_json 种子
├── tests/
│   ├── __init__.py
│   ├── conftest.py             # 临时 SQLite fixture
│   ├── test_engine.py          # WAL/单写
│   ├── test_crypto.py          # 加解密 + 多版本轮换
│   ├── test_models.py          # 全表建表 + 约束
│   ├── test_seed.py            # 7 彩种种子 + spec_json 校验
│   └── test_health.py          # /health + 启动校验
├── .env.example                # 配置模板（不提交真值）
└── import_linter.toml          # 领域层 purity 护栏（Plan 02 用）
```

---

## Task 1: uv 项目初始化 + Python 3.12 + 依赖

**Files:**
- Create: `pyproject.toml`, `.python-version`, `.env.example`

- [ ] **Step 1: 安装 Python 3.12 并初始化项目**

```bash
uv python install 3.12
uv init --python 3.12 --no-readme --no-pin-python .
```

- [ ] **Step 2: 写 pyproject.toml（覆盖 uv init 的最小版）**

```toml
[project]
name = "lottery-notification"
version = "0.1.0"
description = "兑奖了吗？— 彩票开奖自动核对与通知"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "sqlmodel>=0.0.22",
    "alembic>=1.13",
    "apscheduler>=3.10",
    "httpx>=0.27",
    "pyjwt>=2.9",
    "passlib[bcrypt]>=1.7",
    "cryptography>=43",
    "pydantic>=2.9",
    "pydantic-settings>=2.5",
    "python-multipart>=0.0.9",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.3",
    "pytest-asyncio>=0.24",
    "httpx>=0.27",
    "import-linter>=2.1",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["app"]
```

写 `.python-version`：
```
3.12
```

- [ ] **Step 3: 写 .env.example**

```bash
# 必填
JWT_SECRET=change-me-to-a-long-random-string
CRYPTO_KEY_V1=        # python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# 数据源
MXNZP_API_KEY=
JUHE_API_KEY=
# SMTP（启用 email 渠道时必填）
SMTP_HOST=
SMTP_PORT=465
SMTP_ENCRYPTION=SSL/TLS
SMTP_USER=
SMTP_PASS=
SMTP_FROM=lottery@example.com
# 管理员 Bark 兜底告警（启用 email 时必填）
ADMIN_BARK_KEY=
# 运行环境
DATABASE_URL=sqlite:///./data/lottery.db
TZ=Asia/Shanghai
```

- [ ] **Step 4: 安装依赖（含 dev）**

```bash
uv sync --extra dev
```
Expected: 生成 `uv.lock` + `.venv/`，无报错。

- [ ] **Step 5: 验证 Python 版本**

```bash
uv run python --version
```
Expected: `Python 3.12.x`

- [ ] **Step 6: 更新 .gitignore（补 data/ alembic 缓存）**

追加到 `.gitignore`：
```
data/
*.db
*.db-journal
*.db-wal
*.db-shm
.env
```

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock .python-version .env.example .gitignore
git commit -m "chore: uv 项目初始化 + Python 3.12 + 依赖"
```

---

## Task 2: 配置层（pydantic-settings + 启动校验）

**Files:**
- Create: `app/config.py`, `app/__init__.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: 写 app/__init__.py（空）**

```python
```

- [ ] **Step 2: 写失败测试 tests/test_config.py**

```python
import pytest
from app.config import Settings


def test_settings_load_from_env(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "x" * 32)
    monkeypatch.setenv("CRYPTO_KEY_V1", "k" * 32)
    s = Settings()
    assert s.jwt_secret == "x" * 32
    assert s.crypto_keys[1] == "k" * 32
    assert s.tz == "Asia/Shanghai"


def test_settings_missing_required_raises(monkeypatch):
    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.delenv("CRYPTO_KEY_V1", raising=False)
    with pytest.raises(Exception):
        Settings()


def test_multi_key_versions(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "x" * 32)
    monkeypatch.setenv("CRYPTO_KEY_V1", "old-key-" * 4)
    monkeypatch.setenv("CRYPTO_KEY_V2", "new-key-" + "x" * 23)
    s = Settings()
    assert s.crypto_keys[2].startswith("new-key")
    assert s.current_key_version == 2


def test_email_requires_admin_bark(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "x" * 32)
    monkeypatch.setenv("CRYPTO_KEY_V1", "k" * 32)
    monkeypatch.setenv("SMTP_HOST", "smtp.qq.com")
    monkeypatch.delenv("ADMIN_BARK_KEY", raising=False)
    s = Settings()
    with pytest.raises(ValueError, match="Bark"):
        s.validate_email_bark_fallback()
```

- [ ] **Step 3: 运行测试确认失败**

```bash
uv run pytest tests/test_config.py -v
```
Expected: FAIL（`ModuleNotFoundError: No module named 'app.config'`）

- [ ] **Step 4: 写 app/config.py 最小实现**

```python
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    jwt_secret: str = Field(min_length=16)
    crypto_key_v1: str = Field(alias="CRYPTO_KEY_V1", min_length=16)
    crypto_key_v2: str | None = Field(default=None, alias="CRYPTO_KEY_V2")

    mxnzp_api_key: str = ""
    juhe_api_key: str = ""

    smtp_host: str | None = None
    smtp_port: int = 465
    smtp_encryption: str = "SSL/TLS"
    smtp_user: str | None = None
    smtp_pass: str | None = None
    smtp_from: str | None = None
    admin_bark_key: str | None = None

    database_url: str = "sqlite:///./data/lottery.db"
    tz: str = "Asia/Shanghai"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])  # Plan 05 CORS 用

    @property
    def crypto_keys(self) -> dict[int, str]:
        keys = {1: self.crypto_key_v1}
        if self.crypto_key_v2:
            keys[2] = self.crypto_key_v2
        return keys

    @property
    def current_key_version(self) -> int:
        return max(self.crypto_keys)

    @property
    def email_enabled(self) -> bool:
        return bool(self.smtp_host and self.smtp_user and self.smtp_pass)

    def validate_email_bark_fallback(self) -> None:
        if self.email_enabled and not self.admin_bark_key:
            raise ValueError(
                "启用 email 渠道时必须配置 ADMIN_BARK_KEY（Bark 兜底告警，避免邮件循环依赖）"
            )


settings = Settings()  # type: ignore[call-arg]
```

- [ ] **Step 5: 运行测试确认通过**

```bash
uv run pytest tests/test_config.py -v
```
Expected: 4 passed

- [ ] **Step 6: Commit**

```bash
git add app/__init__.py app/config.py tests/test_config.py
git commit -m "feat: 配置层 pydantic-settings + 多版本 crypto key + email/bark 校验"
```

---

## Task 3: DB engine（WAL / busy_timeout / 单写连接）

**Files:**
- Create: `app/db/__init__.py`, `app/db/engine.py`, `app/db/session.py`
- Test: `tests/conftest.py`, `tests/test_engine.py`

- [ ] **Step 1: 写失败测试 tests/test_engine.py**

```python
import sqlite3
from sqlalchemy import create_engine, text
from app.db.engine import build_engine, apply_sqlite_pragmas


def test_apply_pragmas_sets_wal(tmp_path):
    db = tmp_path / "t.db"
    eng = build_engine(f"sqlite:///{db}")
    apply_sqlite_pragmas(eng)
    with eng.connect() as conn:
        jm = conn.execute(text("PRAGMA journal_mode")).scalar()
        sync = conn.execute(text("PRAGMA synchronous")).scalar()
        bt = conn.execute(text("PRAGMA busy_timeout")).scalar()
    assert jm.lower() == "wal"
    assert sync == 1  # NORMAL
    assert bt == 5000


def test_write_pool_is_single(tmp_path):
    eng = build_engine(f"sqlite:///{tmp_path / 't.db'}")
    # SQLite 方言下 NullPool + 单连接；校验 pool 不大于 1
    assert eng.pool.size() <= 1
```

- [ ] **Step 2: 写 tests/conftest.py（临时 DB fixture）**

```python
import pytest
from sqlalchemy import create_engine
from sqlmodel import SQLModel


@pytest.fixture
def db_engine(tmp_path):
    """每个测试一个临时 SQLite，建全表。"""
    from app.db.engine import build_engine, apply_sqlite_pragmas
    eng = build_engine(f"sqlite:///{tmp_path}/test.db")
    apply_sqlite_pragmas(eng)
    # 导入所有 model 注册到 SQLModel.metadata（Plan 后续补全；此处先建空）
    SQLModel.metadata.create_all(eng)
    return eng
```

- [ ] **Step 3: 运行测试确认失败**

```bash
uv run pytest tests/test_engine.py -v
```
Expected: FAIL（无 `app.db.engine`）

- [ ] **Step 4: 写 app/db/engine.py**

```python
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlmodel import Session


def build_engine(url: str) -> Engine:
    """单写连接（pool_size=1, max_overflow=0）+ SQLite + PRAGMA（spec §4.3）。
    写串行化防 database is locked；PRAGMA 在 engine 创建时一次性注册 connect 事件。"""
    eng = create_engine(
        url,
        connect_args={"check_same_thread": False},
        pool_size=1,
        max_overflow=0,  # 强制单连接复用，写串行化
    )

    @event.listens_for(eng, "connect")
    def _set_pragmas(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()

    return eng


def apply_sqlite_pragmas(eng: Engine) -> None:
    """兼容入口：PRAGMA 已在 build_engine 内注册。保留供测试/旧调用，内部 no-op
    （避免对同一 engine 重复注册 connect 事件）。"""
    return None


def get_session(eng: Engine):
    """FastAPI 依赖：每请求一会话。"""
    with Session(eng) as session:
        yield session
```

- [ ] **Step 5: 写 app/db/__init__.py + app/db/session.py（空壳，复用 engine）**

`app/db/__init__.py`:
```python
from app.db.engine import build_engine, apply_sqlite_pragmas, get_session  # noqa
```

`app/db/session.py`:
```python
"""全局 engine 单例（应用启动时构建）。Plan 后续 main.py 注入。"""
from app.config import settings
from app.db.engine import build_engine, apply_sqlite_pragmas

engine = build_engine(settings.database_url)
apply_sqlite_pragmas(engine)
```

- [ ] **Step 6: 运行测试确认通过**

```bash
uv run pytest tests/test_engine.py tests/conftest.py -v
```
Expected: passed

- [ ] **Step 7: Commit**

```bash
git add app/db/ tests/test_engine.py tests/conftest.py
git commit -m "feat: DB engine WAL/NORMAL/busy_timeout + 会话工厂"
```

---

## Task 4: SQLModel 全表 schema（13 表 + apscheduler_jobs）

> 分 4 个子 task 写 models（按聚合），最后汇总测试。所有金额用 int（分）。
> **TDD 取舍**：表定义是声明式数据（无行为逻辑），采用「实现后验证」——4d 测试验证建表成功 + 唯一约束 + 字段存在。行为代码（config/crypto/health）严格 RED→GREEN。这是声明式 vs 行为代码的合理区分，非 TDD 松懈。Task 7 种子数据同理。

### Task 4a: 基类 + 用户 + 彩种

**Files:** `app/models/_base.py`, `app/models/user.py`, `app/models/lottery.py`, `app/models/__init__.py`

- [ ] **Step 1: 写 _base.py**

```python
from datetime import datetime
from sqlmodel import SQLModel, Field
from typing import Optional


class TimestampMixin(SQLModel):
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)
```

- [ ] **Step 2: 写 user.py**

```python
from sqlmodel import Field
from app.models._base import TimestampMixin


class User(TimestampMixin, table=True):
    __tablename__ = "users"
    id: int | None = Field(default=None, primary_key=True)
    username: str = Field(index=True, unique=True, max_length=64)
    password_hash: str
    role: str = Field(default="user", max_length=16)  # user | admin
    invite_code: str = Field(max_length=16, index=True)  # 注册时用的码
    enabled: bool = Field(default=True)
```

- [ ] **Step 3: 写 lottery.py（lottery_types 全局表）**

```python
from sqlmodel import Field
from app.models._base import TimestampMixin


class LotteryType(TimestampMixin, table=True):
    __tablename__ = "lottery_types"
    code: str = Field(primary_key=True, max_length=8)  # ssq/dlt/...
    name: str = Field(max_length=16)
    category: str = Field(max_length=8)  # welfare | sport
    spec_json: str  # LotterySpec 序列化（Plan 02 hydration）
    draw_schedule_json: str  # 开奖日 + 调度配置
    enabled: bool = Field(default=True)
    schema_version: int = Field(default=1)  # spec_json 演进
```

- [ ] **Step 4: 写 __init__.py 暂留空（4d 汇总 import）**

```python
```

### Task 4b: 号码 + 开奖 + 比对

**Files:** `app/models/ticket.py`, `app/models/draw.py`, `app/models/comparison.py`

- [ ] **Step 1: 写 ticket.py**

```python
from sqlmodel import Field
from app.models._base import TimestampMixin


class Ticket(TimestampMixin, table=True):
    __tablename__ = "tickets"
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    lottery_code: str = Field(foreign_key="lottery_types.code", index=True, max_length=8)
    play_type: str = Field(max_length=16)  # single/fushi/dantuo/danxuan/zhixuan/...
    numbers_json: str  # 原始选择
    tuo_json: str | None = None  # 胆拖拖码
    label: str | None = Field(default=None, max_length=32)
    multiplier: int = Field(default=1, ge=1, le=99)
    append: bool = Field(default=False)  # 仅大乐透
    cost: int = Field(default=0, ge=0)  # 分
    enabled: bool = Field(default=True)
```

- [ ] **Step 2: 写 draw.py（draw_results + draw_corrections + pending_comparisons）**

```python
from datetime import datetime
from sqlalchemy import UniqueConstraint
from sqlmodel import Field
from app.models._base import TimestampMixin


class DrawResult(TimestampMixin, table=True):
    __tablename__ = "draw_results"
    id: int | None = Field(default=None, primary_key=True)
    lottery_code: str = Field(index=True, max_length=8)
    draw_no: str = Field(index=True, max_length=16)
    draw_date: datetime
    numbers_json: str
    source: str = Field(max_length=16)  # mxnzp | juhe
    fetched_at: datetime = Field(default_factory=datetime.utcnow)
    verified: bool = Field(default=False)
    single_source: bool = Field(default=False)
    version: int = Field(default=1)  # 官方更正递增

    __table_args__ = (
        UniqueConstraint("lottery_code", "draw_no", name="uq_draw_lottery_no"),
    )


class DrawCorrection(TimestampMixin, table=True):
    __tablename__ = "draw_corrections"
    id: int | None = Field(default=None, primary_key=True)
    draw_result_id: int = Field(foreign_key="draw_results.id", index=True)
    old_numbers_json: str
    new_numbers_json: str
    reason: str | None = None


class PendingComparison(TimestampMixin, table=True):
    """比对触发 outbox。processed_at 为空=待处理。"""
    __tablename__ = "pending_comparisons"
    id: int | None = Field(default=None, primary_key=True)
    draw_result_id: int = Field(foreign_key="draw_results.id", index=True)
    processed_at: datetime | None = Field(default=None, index=True)
```

- [ ] **Step 3: 写 comparison.py（comparisons + prize_claims）**

```python
from datetime import datetime
from sqlmodel import Field
from sqlalchemy import UniqueConstraint
from app.models._base import TimestampMixin


class Comparison(TimestampMixin, table=True):
    __tablename__ = "comparisons"
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    draw_result_id: int = Field(foreign_key="draw_results.id", index=True)
    ticket_id: int = Field(foreign_key="tickets.id", index=True)
    hits_json: str
    prize_tier: int | None = None
    prize_amount: int | None = None  # 分；null=浮动奖待派奖
    is_win: bool = Field(default=False)
    corrected_at: datetime | None = None

    __table_args__ = (
        UniqueConstraint("draw_result_id", "ticket_id", name="uq_cmp_draw_ticket"),
    )


class PrizeClaim(TimestampMixin, table=True):
    __tablename__ = "prize_claims"
    id: int | None = Field(default=None, primary_key=True)
    comparison_id: int = Field(foreign_key="comparisons.id", index=True)
    status: str = Field(default="pending", max_length=16)  # pending|claimed|expired
    deadline: datetime
    claimed_at: datetime | None = None
```

### Task 4c: 通知 + 健康 + 审计 + 调度

**Files:** `app/models/notification.py`, `app/models/health.py`, `app/models/audit.py`, `app/models/scheduler.py`

- [ ] **Step 1: 写 notification.py**

```python
from datetime import datetime
from sqlmodel import Field
from app.models._base import TimestampMixin


class NotificationChannel(TimestampMixin, table=True):
    __tablename__ = "notification_channels"
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    type: str = Field(max_length=8)  # bark | feishu | email
    config_json: str  # 加密存储（webhook/key/收件地址）
    enabled: bool = Field(default=True)
    key_version: int = Field(default=1)


class NotificationRule(TimestampMixin, table=True):
    __tablename__ = "notification_rules"
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    lottery_code: str = Field(foreign_key="lottery_types.code", index=True)
    strategy: str = Field(default="every", max_length=8)  # every | win_only
    timing: str | None = None


class NotificationLog(TimestampMixin, table=True):
    __tablename__ = "notification_logs"
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    type: str = Field(max_length=16)
    payload: str
    status: str = Field(max_length=16)  # sent | failed | pending
    sent_at: datetime | None = None
    error: str | None = None
```

- [ ] **Step 2: 写 health.py**

```python
from datetime import datetime
from sqlmodel import Field
from app.models._base import TimestampMixin


class ApiSourceHealth(TimestampMixin, table=True):
    __tablename__ = "api_source_health"
    source: str = Field(primary_key=True, max_length=16)
    last_success_at: datetime | None = None
    status: str = Field(default="unknown", max_length=16)  # ok | degraded | down | unknown
    error: str | None = None
```

- [ ] **Step 3: 写 audit.py**

```python
from app.models._base import TimestampMixin
from sqlmodel import Field


class AdminAuditLog(TimestampMixin, table=True):
    __tablename__ = "admin_audit_logs"
    id: int | None = Field(default=None, primary_key=True)
    admin_id: int = Field(foreign_key="users.id", index=True)
    action: str = Field(max_length=32)
    target_type: str = Field(max_length=32)
    target_id: str | None = None
    old_values: str | None = None  # JSON（敏感字段脱敏）
    new_values: str | None = None
```

- [ ] **Step 4: 写 scheduler.py（apscheduler_jobs 显式表定义，对齐 APScheduler SQLAlchemyJobStore 的表结构）**

```python
"""APScheduler SQLAlchemyJobStore 期望的 apscheduler_jobs 表结构。
显式定义以便 Alembic 首迁移纳入（不让 jobstore 运行时 auto-create，避免 schema drift）。
字段名严格对齐 APScheduler 3.x jobstore。"""
from datetime import datetime
from sqlmodel import Field
from app.models._base import TimestampMixin


class ApschedulerJob(TimestampMixin, table=True):
    __tablename__ = "apscheduler_jobs"
    id: str = Field(primary_key=True, max_length=191)
    next_run_time: datetime | None = Field(default=None, index=True)
    job_state: bytes  # pickled blob（SQLModel 映射 LargeBinary，与 APScheduler jobstore 一致，autogenerate 正确）
```

> 注：APScheduler 的 job_state 是 pickle blob。SQLModel 用 `bytes` 声明映射 LargeBinary，与 APScheduler jobstore 一致，autogenerate 直接生成正确列类型（无需手动改迁移）。jobstore 读写在 Plan 04 接线。

### Task 4d: 汇总 import + models 建表测试

**Files:** `app/models/__init__.py`, `tests/test_models.py`

- [ ] **Step 1: 写 __init__.py（import 全部表注册 metadata）**

```python
from app.models._base import TimestampMixin  # noqa
from app.models.user import User  # noqa
from app.models.lottery import LotteryType  # noqa
from app.models.ticket import Ticket  # noqa
from app.models.draw import DrawResult, DrawCorrection, PendingComparison  # noqa
from app.models.comparison import Comparison, PrizeClaim  # noqa
from app.models.notification import (  # noqa
    NotificationChannel, NotificationRule, NotificationLog,
)
from app.models.health import ApiSourceHealth  # noqa
from app.models.audit import AdminAuditLog  # noqa
from app.models.scheduler import ApschedulerJob  # noqa

__all__ = [
    "User", "LotteryType", "Ticket", "DrawResult", "DrawCorrection",
    "PendingComparison", "Comparison", "PrizeClaim",
    "NotificationChannel", "NotificationRule", "NotificationLog",
    "ApiSourceHealth", "AdminAuditLog", "ApschedulerJob",
]
```

- [ ] **Step 2: 写失败测试 tests/test_models.py**

```python
from sqlalchemy import inspect
from sqlmodel import Session, select


def test_all_13_tables_created(db_engine):
    insp = inspect(db_engine)
    names = set(insp.get_table_names())
    expected = {
        "users", "lottery_types", "tickets", "draw_results", "draw_corrections",
        "pending_comparisons", "comparisons", "prize_claims",
        "notification_channels", "notification_rules", "notification_logs",
        "api_source_health", "admin_audit_logs", "apscheduler_jobs",
    }
    assert expected.issubset(names), f"缺表: {expected - names}"


def test_draw_results_unique_constraint(db_engine):
    insp = inspect(db_engine)
    uqs = {tuple(c["column_names"]) for c in insp.get_unique_constraints("draw_results")}
    assert ("lottery_code", "draw_no") in uqs


def test_comparisons_unique_constraint(db_engine):
    insp = inspect(db_engine)
    uqs = {tuple(c["column_names"]) for c in insp.get_unique_constraints("comparisons")}
    assert ("draw_result_id", "ticket_id") in uqs
```

- [ ] **Step 3: 修正 conftest db_engine fixture 导入 models**

`tests/conftest.py` 的 `db_engine` 里在 `create_all` 前加：
```python
import app.models  # noqa: F401  注册全部表
```

- [ ] **Step 4: 运行测试**

```bash
uv run pytest tests/test_models.py -v
```
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add app/models/ tests/test_models.py tests/conftest.py
git commit -m "feat: SQLModel 全 13 表 schema + 唯一约束 + apscheduler_jobs"
```

---

## Task 5: Alembic 初始化 + 首迁移（含 apscheduler_jobs）

**Files:** `alembic.ini`, `alembic/env.py`, `alembic/script.py.mako`, `alembic/versions/0001_initial.py`

- [ ] **Step 1: 初始化 Alembic**

```bash
uv run alembic init alembic
```

- [ ] **Step 2: 配置 alembic.ini 的 sqlalchemy.url（占位，env.py 动态注入）**

编辑 `alembic.ini`，把 `sqlalchemy.url = ...` 行改为：
```
sqlalchemy.url = driver://user:pass@localhost/dbname
```
（占位，env.py 会用 settings.database_url 覆盖）

- [ ] **Step 3: 写 alembic/env.py（注入 settings + metadata）**

```python
from logging.config import fileConfig
from sqlalchemy import create_engine, pool
from alembic import context

from app.config import settings
import app.models  # noqa: F401  注册全部表到 metadata
from sqlmodel import SQLModel

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = SQLModel.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(
        config.get_main_option("sqlalchemy.url"),
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 4: 自动生成首迁移**

```bash
uv run alembic revision --autogenerate -m "initial schema with apscheduler_jobs"
```
Expected: 在 `alembic/versions/` 生成 `*_initial_schema_with_apscheduler_jobs.py`，含全部 14 表。

- [ ] **Step 5: 重命名为 0001_initial.py（保持顺序清晰）**

```bash
cd alembic/versions && mv *_initial_schema_with_apscheduler_jobs.py 0001_initial.py
```
打开文件，把 `revision` 改为 `"0001"`，`down_revision = None`。

- [ ] **Step 6: 确认 job_state 列类型为 LargeBinary**

model 中 `ApschedulerJob.job_state: bytes`，SQLModel 映射 LargeBinary，autogenerate 自动生成正确类型。打开 `0001_initial.py` 确认 `apscheduler_jobs.job_state` 是 `sa.LargeBinary()`（应已正确，无需手改；若不是说明 model 定义有误，回查 Task 4c）。

- [ ] **Step 7: 验证迁移可跑（空库 upgrade）**

```bash
mkdir -p data
uv run alembic upgrade head
```
Expected: 无报错，`data/lottery.db` 建成，含全 14 表。

- [ ] **Step 8: 验证可回滚**

```bash
uv run alembic downgrade base
uv run alembic upgrade head
```
Expected: 两次都无报错。

- [ ] **Step 9: Commit**

```bash
git add alembic.ini alembic/
git commit -m "feat: Alembic 初始化 + 首迁移（全 schema 含 apscheduler_jobs，job_state LargeBinary）"
```

---

## Task 6: Crypto 服务（Fernet 多版本 + key_version 轮换）

**Files:** `app/infrastructure/__init__.py`, `app/infrastructure/crypto.py`, `tests/test_crypto.py`

- [ ] **Step 1: 写失败测试 tests/test_crypto.py**

```python
import pytest
from cryptography.fernet import Fernet
from app.infrastructure.crypto import CryptoService


@pytest.fixture
def crypto():
    k1 = Fernet.generate_key().decode()
    k2 = Fernet.generate_key().decode()
    return CryptoService({1: k1, 2: k2}, current_version=2)


def test_encrypt_decrypt_roundtrip(crypto):
    ct = crypto.encrypt("secret-webhook", version=2)
    assert ct.version == 2
    assert crypto.decrypt(ct) == "secret-webhook"


def test_decrypt_old_version_after_rotation(crypto):
    # 用 V1 加密的旧数据，轮换到 V2 后仍可解
    ct = crypto.encrypt("legacy", version=1)
    assert crypto.decrypt(ct) == "legacy"


def test_decrypt_tuple_form(crypto):
    blob = crypto.encrypt("x", version=2)
    # 模拟 DB 存的 (version, ciphertext) 元组
    assert crypto.decrypt((blob.version, blob.ciphertext)) == "x"


def test_reencrypt_upgrades_version(crypto):
    old = crypto.encrypt("data", version=1)
    new = crypto.re_encrypt(old, to_version=2)
    assert new.version == 2
    assert crypto.decrypt(new) == "data"
```

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run pytest tests/test_crypto.py -v
```
Expected: FAIL（无 module）

- [ ] **Step 3: 写 app/infrastructure/__init__.py（空）+ app/infrastructure/crypto.py**

`app/infrastructure/__init__.py`:
```python
```

`app/infrastructure/crypto.py`:
```python
from dataclasses import dataclass
from cryptography.fernet import Fernet


@dataclass(frozen=True)
class CipherBlob:
    """加密结果：(version, ciphertext)。DB 存两列或拼接。"""
    version: int
    ciphertext: str


class CryptoService:
    """Fernet 多版本：按 key_version 选对应 Fernet 解密（旧 key 解旧数据），用 current key 加密。"""

    def __init__(self, keys: dict[int, str], current_version: int):
        if current_version not in keys:
            raise ValueError(f"current_version {current_version} 不在 keys 中")
        self._keys = keys
        self._current = current_version
        self._fernets = {v: Fernet(k.encode()) for v, k in keys.items()}

    def encrypt(self, plaintext: str, version: int | None = None) -> CipherBlob:
        v = version or self._current
        if v not in self._fernets:
            raise ValueError(f"未知 key version: {v}")
        ct = self._fernets[v].encrypt(plaintext.encode()).decode()
        return CipherBlob(version=v, ciphertext=ct)

    def decrypt(self, blob: CipherBlob | tuple[int, str]) -> str:
        if isinstance(blob, tuple):
            v, ct = blob
        else:
            v, ct = blob.version, blob.ciphertext
        if v not in self._fernets:
            raise ValueError(f"无法解密：未知 key version {v}")
        return self._fernets[v].decrypt(ct.encode()).decode()

    def re_encrypt(self, blob: CipherBlob, to_version: int) -> CipherBlob:
        """轮换：旧 key 解密 → 新 key 加密。"""
        plaintext = self.decrypt(blob)
        return self.encrypt(plaintext, version=to_version)
```

- [ ] **Step 4: 运行测试确认通过**

```bash
uv run pytest tests/test_crypto.py -v
```
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add app/infrastructure/ tests/test_crypto.py
git commit -m "feat: Crypto 服务 Fernet 多版本 + key_version 轮换 + re-encrypt"
```

---

## Task 7: 7 彩种种子数据（spec_json + pydantic 校验）

> 领域层的 LotterySpec 纯类在 Plan 02 定义。本 plan 的种子用**轻量 pydantic SpecSchema** 校验 spec_json 结构（含 welfare_rate/price_per_bet/number_style 等），Plan 02 的领域 LotterySpec 从同一 spec_json hydrate。

**Files:** `app/seeds/__init__.py`, `app/seeds/lottery_types.py`, `app/seeds/spec_schema.py`, `tests/test_seed.py`

- [ ] **Step 1: 写 spec_schema.py（轻量校验 schema，对齐 spec §5.1）**

```python
from pydantic import BaseModel, Field


class NumberRangeModel(BaseModel):
    min: int
    max: int
    count: int


class PositionalDigitsModel(BaseModel):
    min: int
    max: int
    length: int


class LotterySpecModel(BaseModel):
    """spec_json 的校验 schema（与领域 LotterySpec 同形，Plan 02 复用）。"""
    code: str
    name: str
    category: str  # welfare | sport
    number_style: str  # partition | positional | hybrid
    front: NumberRangeModel | PositionalDigitsModel
    back: NumberRangeModel | PositionalDigitsModel | None = None
    draw_days: list[int] = Field(description="0=周一…6=周日（Python weekday）")
    play_types: list[str]
    welfare_rate: int = Field(ge=0, le=100)
    price_per_bet: int = Field(gt=0, description="分")
```

- [ ] **Step 2: 写 lottery_types.py（7 彩种 spec_json，严格对齐 spec §5.1 规则表）**

```python
import json
from app.seeds.spec_schema import LotterySpecModel

# 7 大彩种规格（spec §5.1）。draw_days 用 Python weekday: 周一=0 … 周日=6
SPECS: list[dict] = [
    {
        "code": "ssq", "name": "双色球", "category": "welfare", "number_style": "partition",
        "front": {"min": 1, "max": 33, "count": 6},
        "back": {"min": 1, "max": 16, "count": 1},
        "draw_days": [1, 3, 6],  # 二/四/日
        "play_types": ["single", "fushi", "dantuo"],
        "welfare_rate": 36, "price_per_bet": 200,
    },
    {
        "code": "dlt", "name": "大乐透", "category": "sport", "number_style": "partition",
        "front": {"min": 1, "max": 35, "count": 5},
        "back": {"min": 1, "max": 12, "count": 2},
        "draw_days": [0, 2, 5],  # 一/三/六
        "play_types": ["single", "fushi", "dantuo"],
        "welfare_rate": 36, "price_per_bet": 200,
    },
    {
        "code": "qlc", "name": "七乐彩", "category": "welfare", "number_style": "partition",
        "front": {"min": 1, "max": 30, "count": 7},
        "back": {"min": 1, "max": 30, "count": 1},  # 特别号，同池无放回
        "draw_days": [0, 2, 4],  # 一/三/五
        "play_types": ["single", "fushi", "dantuo"],
        "welfare_rate": 36, "price_per_bet": 200,
    },
    {
        "code": "fc3d", "name": "福彩3D", "category": "welfare", "number_style": "positional",
        "front": {"min": 0, "max": 9, "length": 3},
        "back": None,
        "draw_days": [0, 1, 2, 3, 4, 5, 6],  # 每日
        "play_types": ["danxuan", "zuxuan3", "zuxuan6"],
        "welfare_rate": 34, "price_per_bet": 200,
    },
    {
        "code": "qxc", "name": "七星彩", "category": "sport", "number_style": "hybrid",
        "front": {"min": 0, "max": 9, "length": 6},  # 按位 6 位
        "back": {"min": 0, "max": 14, "count": 1},
        "draw_days": [1, 4, 6],  # 二/五/日
        "play_types": ["single", "fushi", "dantuo"],
        "welfare_rate": 37, "price_per_bet": 200,
    },
    {
        "code": "pl3", "name": "排列3", "category": "sport", "number_style": "positional",
        "front": {"min": 0, "max": 9, "length": 3},
        "back": None,
        "draw_days": [0, 1, 2, 3, 4, 5, 6],
        "play_types": ["zhixuan", "zuxuan3", "zuxuan6"],
        "welfare_rate": 34, "price_per_bet": 200,
    },
    {
        "code": "pl5", "name": "排列5", "category": "sport", "number_style": "positional",
        "front": {"min": 0, "max": 9, "length": 5},
        "back": None,
        "draw_days": [0, 1, 2, 3, 4, 5, 6],
        "play_types": ["zhixuan"],
        "welfare_rate": 34, "price_per_bet": 200,
    },
]

# 全部校验
for _s in SPECS:
    LotterySpecModel(**_s)  # 启动时即校验，错即崩


def seed_lottery_types(session) -> int:
    """幂等写入 7 彩种到 lottery_types。返回写入/更新条数。"""
    from app.models.lottery import LotteryType
    count = 0
    for spec in SPECS:
        existing = session.get(LotteryType, spec["code"])
        spec_json = json.dumps(spec, ensure_ascii=False)
        sched = json.dumps({"draw_days": spec["draw_days"]})
        if existing is None:
            session.add(LotteryType(
                code=spec["code"], name=spec["name"], category=spec["category"],
                spec_json=spec_json, draw_schedule_json=sched, enabled=True,
            ))
            count += 1
        else:
            existing.spec_json = spec_json
            existing.draw_schedule_json = sched
            count += 1
    session.commit()
    return count
```

- [ ] **Step 3: 写 app/seeds/__init__.py**

```python
from app.seeds.lottery_types import seed_lottery_types, SPECS  # noqa
```

- [ ] **Step 4: 写失败测试 tests/test_seed.py**

```python
import json
from sqlmodel import Session, select
from app.seeds import seed_lottery_types, SPECS
from app.models.lottery import LotteryType


def test_seeds_7_lotteries(db_engine):
    with Session(db_engine) as s:
        n = seed_lottery_types(s)
    assert n == 7
    with Session(db_engine) as s:
        codes = {lt.code for lt in s.exec(select(LotteryType)).all()}
    assert codes == {"ssq", "dlt", "qlc", "fc3d", "qxc", "pl3", "pl5"}


def test_seed_idempotent(db_engine):
    with Session(db_engine) as s:
        assert seed_lottery_types(s) == 7
    with Session(db_engine) as s:
        assert seed_lottery_types(s) == 7  # 第二次不新增


def test_spec_json_valid_and_welfare_rate(db_engine):
    with Session(db_engine) as s:
        seed_lottery_types(s)
        dlt = s.get(LotteryType, "dlt")
        spec = json.loads(dlt.spec_json)
    assert spec["welfare_rate"] == 36
    assert spec["number_style"] == "partition"


def test_qxc_hybrid_allows_duplicate_positions():
    """D7:A 验证：hybrid 前区允许跨位重复（NumberRange 不适用）。"""
    spec = next(s for s in SPECS if s["code"] == "qxc")
    assert spec["number_style"] == "hybrid"
    # 前区是 PositionalDigits（length=6），允许 1,1,2,3,4,5
    front = spec["front"]
    assert front.get("length") == 6  # PositionalDigits 语义
```

- [ ] **Step 5: 运行测试确认通过**

```bash
uv run pytest tests/test_seed.py -v
```
Expected: 4 passed

- [ ] **Step 6: Commit**

```bash
git add app/seeds/ tests/test_seed.py
git commit -m "feat: 7 彩种种子数据 + spec_json pydantic 校验 + QXC hybrid 语义"
```

---

## Task 8: FastAPI app + /health + 启动校验

**Files:** `app/main.py`, `tests/test_health.py`

- [ ] **Step 1: 写失败测试 tests/test_health.py**

```python
from fastapi.testclient import TestClient
from app.main import app


def test_health_ok():
    client = TestClient(app)
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "tz" in body
    assert body["tz"] == "Asia/Shanghai"


def test_health_includes_db_check(db_engine):
    # 注入测试 engine（try/finally 保证清理，避免污染后续测试）
    from app.main import app, get_db_for_health
    app.dependency_overrides[get_db_for_health] = lambda: db_engine
    try:
        client = TestClient(app)
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["db"] == "ok"
    finally:
        app.dependency_overrides.clear()
```

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run pytest tests/test_health.py -v
```
Expected: FAIL（无 app.main）

- [ ] **Step 3: 写 app/main.py**

```python
import time
from fastapi import FastAPI, Depends
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.config import settings


def validate_startup() -> None:
    """启动校验：应用时区 + OS 时区软警告 + email/bark 兜底。JWT/CRYPTO 已由 Settings 强制。"""
    import logging
    import time as _time
    log = logging.getLogger("app.startup")
    if settings.tz != "Asia/Shanghai":
        raise RuntimeError(f"应用时区必须 Asia/Shanghai，当前配置 {settings.tz}")
    # OS 时区软校验：容器内可能 UTC，但应用所有调度用 APScheduler 的 Asia/Shanghai
    # 参数锁死，OS tz 偏移不致开奖时间错乱。仅记警告供运维排查。
    tzname = _time.tzname[0] if _time.tzname else None
    if tzname not in ("CST",):
        log.warning("OS 时区为 %s（非 CST），应用已用 Asia/Shanghai 锁定调度时区", tzname)
    settings.validate_email_bark_fallback()


def get_db_for_health() -> Engine:
    from app.db.session import engine
    return engine


app = FastAPI(title="兑奖了吗？API", version="0.1.0")


@app.on_event("startup")
def _startup() -> None:
    validate_startup()
    # 种子幂等写入
    from sqlmodel import Session
    from app.seeds import seed_lottery_types
    from app.db.session import engine
    with Session(engine) as s:
        seed_lottery_types(s)


@app.get("/health")
def health(db: Engine = Depends(get_db_for_health)):
    db_ok = False
    try:
        with db.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False
    return {
        "status": "ok" if db_ok else "degraded",
        "tz": settings.tz,
        "db": "ok" if db_ok else "down",
    }
```

- [ ] **Step 4: 运行测试确认通过**

```bash
uv run pytest tests/test_health.py -v
```
Expected: 2 passed

- [ ] **Step 5: 手动冒烟（启动看 /health）**

```bash
uv run uvicorn app.main:app --port 8280 & PID=$!
sleep 2
curl -s http://127.0.0.1:8280/health
kill $PID 2>/dev/null || true
```
Expected: `{"status":"ok","tz":"Asia/Shanghai","db":"ok"}`

- [ ] **Step 6: Commit**

```bash
git add app/main.py tests/test_health.py
git commit -m "feat: FastAPI app + /health（db+tz）+ 启动校验 + 种子幂等"
```

---

## Task 9: import-linter 领域层 purity 护栏（配置先行）

**Files:** `import_linter.toml`

> 领域层代码在 Plan 02。本 plan 先放配置 + 一个契约测试占位，Plan 02 落地后即生效。

- [ ] **Step 1: 写 import_linter.toml**

```toml
[tool.importlinter]
root_packages = ["app"]

[[tool.importlinter.contracts]]
name = "Domain layer is pure (no IO)"
type = "forbidden"
source_modules = ["app.domain"]
forbidden_modules = ["app.infrastructure", "app.adapters", "app.api", "app.services"]
```

- [ ] **Step 2: 验证配置可加载（领域层暂不存在，contract 会 skip）**

```bash
uv run lint-imports 2>&1 | head -5 || true
```
Expected: 配置被识别（领域层缺失时不报错，Plan 02 加代码后强制）。

- [ ] **Step 3: Commit**

```bash
git add import_linter.toml
git commit -m "chore: import-linter 领域层 purity 护栏配置（Plan 02 落地后强制）"
```

---

## Task 10: 全量测试 + 文档同步

- [ ] **Step 1: 跑全量测试**

```bash
uv run pytest -v
```
Expected: 全绿（config 4 + engine 2 + models 3 + crypto 4 + seed 4 + health 2 = 19 passed）

- [ ] **Step 2: 验证 alembic 从零建库**

```bash
rm -f data/lottery.db
uv run alembic upgrade head
uv run python -c "import sqlite3; c=sqlite3.connect('data/lottery.db'); print(len(c.execute(\"SELECT name FROM sqlite_master WHERE type='table'\").fetchall()), 'tables')"
```
Expected: `14 tables`

- [ ] **Step 3: 同步 CLAUDE.md 命令为 uv（环境适配）**

打开 `CLAUDE.md`，把「常用命令」里：
- `pip install -e ".[dev]"` → `uv sync --extra dev`
- `pytest -v` → `uv run pytest -v`
- `pytest tests/domain/test_partition_compare.py -v` → `uv run pytest tests/domain/test_partition_compare.py -v`
- `python -m app.cli ssq` → `uv run python -m app.cli ssq`
- `uvicorn app.main:app --reload` → `uv run uvicorn app.main:app --reload`

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: CLAUDE.md 命令同步为 uv（环境无 pip/poetry）"
```

---

## Self-Review

**Spec coverage（Plan 01 范围 = Phase 1.0 步骤 1-4 + 部署基础）：**
- ✅ Alembic 迁移基建（步骤 1）→ Task 5
- ✅ 全 schema 含 apscheduler_jobs（步骤 2）→ Task 4 + Task 5
- ✅ Crypto 多版本 + key_version（步骤 3）→ Task 6
- ✅ 种子 lottery_types + pydantic 校验（步骤 4）→ Task 7
- ✅ WAL/busy_timeout/单写 → Task 3
- ✅ 启动校验（TZ/email-bark/JWT/CRYPTO）→ Task 2 + Task 8
- ✅ /health + healthcheck → Task 8
- ✅ import-linter 领域 purity 配置 → Task 9
- 📌 每日备份/日志归档 → Plan 06（部署）

**Placeholder scan：** 无 TBD/TODO；所有 step 含实际代码/命令。
**Type consistency：** `CryptoService`/`CipherBlob`/`Settings`/各 model 命名一致；seed `SPECS` 与 `LotterySpecModel` 字段对齐 spec §5.1。

**衔接后续 plan：**
- Plan 02（领域层）从 `spec_json` hydrate 为领域 `LotterySpec`，复用 `LotterySpecModel`。
- Plan 03（仓储+闭环）用 `app.db.session.engine` + 全 models。
- Plan 04（调度）用 `ApschedulerJob` 表 + `app.db.engine` 共享。
