# 03 仓储 + 核心闭环 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 实现应用服务层 + 适配层 + 仓储：Repository（user_id 注入、IDOR-safe）、双源抓取（交叉校验 + 部分源 grace + 期号映射 + 退避）、outbox claim 比对 worker（原子认领）、比对引擎（接线 domain.compare → 写 comparisons + prize_claims）、浮奖回填、结果更正。

**Architecture:** `app/adapters/`（数据源，httpx，mock 友好）+ `app/services/`（fetch/compare/refill/correct，业务编排）+ `app/infrastructure/repositories.py`（仓储，user_id 注入）。领域层（app/domain）保持纯，不 import 这些。比对引擎调 `app.domain.compare()`。

**Tech Stack:** httpx（+ MockTransport 测试）、SQLModel Session、APScheduler（Plan 04 接线调度，本 plan 写可被调用的 worker 函数）。

**前置（Plan 01/02 已完成）：** models、db engine、domain（compare/expand/HitResult）、seeds。

---

## File Structure

```
app/
├── adapters/
│   ├── __init__.py
│   ├── base.py          # DrawSource protocol + DrawNumbers dataclass + 期号归一化
│   ├── mxnzp.py         # MXNZP adapter
│   └── juhe.py          # 聚合数据 adapter
├── infrastructure/
│   ├── crypto.py        # (Plan 01)
│   └── repositories.py  # Repository 基类 + 各聚合 repo（user_id 注入）
└── services/
    ├── __init__.py
    ├── fetch_service.py    # FetchService: 双源 + 交叉校验 + grace + 退避 + verified 恢复
    ├── compare_service.py  # CompareService: outbox claim + 比对 + 写 comparisons/prize_claims
    ├── refill_service.py   # FloatRefillWorker: 浮奖回填
    └── correct_service.py  # DrawService.correct: 结果更正
tests/
├── adapters/test_draw_sources.py
├── infrastructure/test_repositories.py
├── services/test_fetch_service.py
├── services/test_compare_service.py
├── services/test_refill_service.py
├── services/test_correct_service.py
└── integration/test_core_loop.py
```

---

## Task 1: Repository 基类 + TicketRepo / UserRepo（user_id 注入，IDOR-safe）

**Files:** `app/infrastructure/repositories.py`, `tests/infrastructure/__init__.py`(空), `tests/infrastructure/test_repositories.py`

- [ ] **Step 1: 写失败测试 tests/infrastructure/test_repositories.py**

```python
import pytest
from sqlmodel import Session
from app.infrastructure.repositories import TicketRepo, UserRepository
from app.models import User, Ticket


def _make_user(session, username="u1", role="user"):
    u = User(username=username, password_hash="x", role=role, invite_code="ABC123")
    session.add(u); session.commit(); session.refresh(u)
    return u


def test_ticket_repo_scoped_by_user_id(db_engine):
    with Session(db_engine) as s:
        u1 = _make_user(s, "u1"); u2 = _make_user(s, "u2")
        repo1 = TicketRepo(s, user_id=u1.id)
        repo2 = TicketRepo(s, user_id=u2.id)
        repo1.create(lottery_code="ssq", play_type="single",
                     numbers_json='{"front":[1,2,3,4,5,6],"back":[7]}', cost=200)
        assert len(repo1.list_all()) == 1
        assert len(repo2.list_all()) == 0  # 隔离：u2 看不到 u1 的票


def test_ticket_repo_idor_safe(db_engine):
    """u2 不能通过 ticket_id 读 u1 的票。"""
    with Session(db_engine) as s:
        u1 = _make_user(s, "u1"); u2 = _make_user(s, "u2")
        repo1 = TicketRepo(s, user_id=u1.id)
        t = repo1.create(lottery_code="ssq", play_type="single",
                         numbers_json='{"front":[1,2,3,4,5,6],"back":[7]}', cost=200)
        repo2 = TicketRepo(s, user_id=u2.id)
        assert repo2.get(t.id) is None  # IDOR 防护
```

- [ ] **Step 2: 运行确认失败**

```bash
uv run pytest tests/infrastructure/test_repositories.py -v
```
Expected: FAIL

- [ ] **Step 3: 写 app/infrastructure/repositories.py**

```python
from sqlmodel import Session, select
from app.models import User, Ticket, Comparison, PrizeClaim


class TicketRepo:
    """号码池仓储。构造注入 session + user_id，所有查询 WHERE user_id。IDOR-safe。"""
    def __init__(self, session: Session, user_id: int):
        self._s = session
        self._uid = user_id

    def create(self, *, lottery_code, play_type, numbers_json, cost,
               tuo_json=None, label=None, multiplier=1, append=False, enabled=True) -> Ticket:
        t = Ticket(user_id=self._uid, lottery_code=lottery_code, play_type=play_type,
                   numbers_json=numbers_json, tuo_json=tuo_json, label=label,
                   multiplier=multiplier, append=append, cost=cost, enabled=enabled)
        self._s.add(t); self._s.commit(); self._s.refresh(t)
        return t

    def get(self, ticket_id: int) -> Ticket | None:
        """IDOR-safe：仅返回属于本 user 的票。"""
        return self._s.exec(
            select(Ticket).where(Ticket.id == ticket_id, Ticket.user_id == self._uid)
        ).first()

    def list_all(self) -> list[Ticket]:
        return list(self._s.exec(
            select(Ticket).where(Ticket.user_id == self._uid)
        ).all())

    def list_by_lottery(self, lottery_code: str, only_enabled=True) -> list[Ticket]:
        stmt = select(Ticket).where(Ticket.user_id == self._uid, Ticket.lottery_code == lottery_code)
        if only_enabled:
            stmt = stmt.where(Ticket.enabled == True)  # noqa: E712
        return list(self._s.exec(stmt).all())

    def update(self, ticket_id: int, **fields) -> Ticket | None:
        """IDOR-safe 更新：先 get 校验归属再改（不绕过 user_id）。"""
        t = self.get(ticket_id)
        if t is None:
            return None
        for k, v in fields.items():
            if hasattr(t, k):
                setattr(t, k, v)
        self._s.commit(); self._s.refresh(t)
        return t

    def delete(self, ticket_id: int) -> bool:
        """IDOR-safe 删除。"""
        t = self.get(ticket_id)
        if t is None:
            return False
        self._s.delete(t); self._s.commit()
        return True


class UserRepository:
    """全局用户仓储（注册/登录/角色，不经 user_id 隔离）。"""
    def __init__(self, session: Session):
        self._s = session

    def get_by_username(self, username: str) -> User | None:
        return self._s.exec(select(User).where(User.username == username)).first()

    def create(self, *, username, password_hash, role="user", invite_code) -> User:
        u = User(username=username, password_hash=password_hash, role=role, invite_code=invite_code)
        self._s.add(u); self._s.commit(); self._s.refresh(u)
        return u
```

> 其余 repo（DrawResultRepo/ComparisonRepo/NotificationRepo）按需在本 plan 后续 task 内联使用 SQLModel Session 直接查（避免过早抽象）；TicketRepo/UserRepo 是高频 + IDOR 敏感，先建。

- [ ] **Step 4: 运行确认通过**

```bash
uv run pytest tests/infrastructure/test_repositories.py -v
```
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add app/infrastructure/repositories.py tests/infrastructure/
git commit -m "feat(infra): TicketRepo(user_id 注入/IDOR-safe) + UserRepository"
```

---

## Task 2: 数据源适配器（DrawSource protocol + DrawNumbers + 期号归一化 + MXNZP/Juhe）

**Files:** `app/adapters/__init__.py`(空), `app/adapters/base.py`, `app/adapters/mxnzp.py`, `app/adapters/juhe.py`, `tests/adapters/__init__.py`(空), `tests/adapters/test_draw_sources.py`

- [ ] **Step 1: 写失败测试 tests/adapters/test_draw_sources.py（用 httpx MockTransport）**

```python
import httpx
from app.adapters.base import DrawNumbers, normalize_draw_no
from app.adapters.mxnzp import MxnzpAdapter
from app.adapters.juhe import JuheAdapter


def _mock_transport(handler):
    return httpx.MockTransport(handler)


def test_normalize_draw_no():
    """期号归一化：MXNZP '2026062' 与 聚合 '062' 统一为不带年份的 3 位。"""
    assert normalize_draw_no("2026062") == "062"
    assert normalize_draw_no("062") == "062"


def test_mxnzp_adapter_parses(monkeypatch):
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": "200", "data": {
            "issue": "2026062", "numbers": "01,02,03,04,05,06+07"}})
    adapter = MxnzpAdapter(api_key="k", transport=_mock_transport(handler))
    d = adapter.fetch("ssq")
    assert d is not None
    assert d.lottery_code == "ssq"
    assert d.draw_no == "062"  # 归一化
    assert d.front == (1, 2, 3, 4, 5, 6)
    assert d.back == (7,)


def test_mxnzp_adapter_empty_means_not_drawn():
    """HTTP 200 但 data 为空 = 该期未开奖（非错误）。"""
    def handler(req): return httpx.Response(200, json={"code": "200", "data": None})
    adapter = MxnzpAdapter(api_key="k", transport=_mock_transport(handler))
    assert adapter.fetch("ssq") is None


def test_juhe_adapter_parses():
    def handler(req): return httpx.Response(200, json={"error_code": 0, "result": {
        "lottery_no": "ssq", "lottery_date": "2026-06-21",
        "lottery_res": "01,02,03,04,05,06", "blue_no": "07", "period": "062"}})
    adapter = JuheAdapter(api_key="k", transport=_mock_transport(handler))
    d = adapter.fetch("ssq")
    assert d and d.back == (7,) and d.draw_no == "062"
```

- [ ] **Step 2: 运行确认失败**

```bash
uv run pytest tests/adapters/test_draw_sources.py -v
```
Expected: FAIL

- [ ] **Step 3: 写 app/adapters/base.py**

```python
from dataclasses import dataclass
from datetime import date
from typing import Protocol


@dataclass(frozen=True)
class DrawNumbers:
    """归一化开奖号码（adapter 输出）。"""
    lottery_code: str
    draw_no: str        # 归一化（去年份，如 '062'）
    draw_date: date
    front: tuple[int, ...]
    back: tuple[int, ...] | None


def normalize_draw_no(raw: str) -> str:
    """期号归一化：去前缀年份（2026062 → 062）。两源对齐才能交叉校验。"""
    s = raw.strip()
    return s[-3:] if len(s) > 3 else s


class DrawSource(Protocol):
    name: str
    def fetch(self, lottery_code: str) -> DrawNumbers | None:
        """返回归一化号码；None = 该期未开奖（HTTP 200 但无数据）。抛异常 = 网络/服务错误。"""
        ...
```

- [ ] **Step 4: 写 app/adapters/mxnzp.py**

```python
import httpx
from datetime import datetime
from app.adapters.base import DrawNumbers, normalize_draw_no, DrawSource


class MxnzpAdapter:
    name = "mxnzp"

    def __init__(self, api_key: str, transport: httpx.BaseTransport | None = None):
        self._key = api_key
        self._client = httpx.Client(transport=transport, timeout=10.0)

    def fetch(self, lottery_code: str) -> DrawNumbers | None:
        # MXNZP 接口（示例结构，真实字段以 MXNZP 文档为准）
        r = self._client.get(
            "https://www.mxnzp.com/api/lottery/common/result",
            params={"lottery_id": lottery_code, "app_id": self._key},
        )
        r.raise_for_status()
        body = r.json()
        data = body.get("data")
        if not data:
            return None  # 未开奖
        issue = data["issue"]  # '2026062'
        nums = data["numbers"]  # '01,02,03,04,05,06+07'
        front_str, _, back_str = nums.partition("+")
        front = tuple(int(x) for x in front_str.split(","))
        back = tuple(int(x) for x in back_str.split(",")) if back_str else None
        return DrawNumbers(
            lottery_code=lottery_code, draw_no=normalize_draw_no(issue),
            draw_date=datetime.utcnow().date(), front=front, back=back,
        )
```

> 注：MXNZP 真实字段名/参数以注册后文档为准（`lottery_id`、`numbers` 格式）。实现时核对 API 文档调整解析；上面的解析结构（issue/numbers/分割）是约定。

- [ ] **Step 5: 写 app/adapters/juhe.py**

```python
import httpx
from datetime import datetime, date
from app.adapters.base import DrawNumbers, normalize_draw_no


class JuheAdapter:
    name = "juhe"

    def __init__(self, api_key: str, transport: httpx.BaseTransport | None = None):
        self._key = api_key
        self._client = httpx.Client(transport=transport, timeout=10.0)

    def fetch(self, lottery_code: str) -> DrawNumbers | None:
        r = self._client.get(
            "https://v.juhe.cn/lottery/query",
            params={"lottery_id": lottery_code, "key": self._key},
        )
        r.raise_for_status()
        body = r.json()
        if body.get("error_code") != 0 or not body.get("result"):
            return None
        res = body["result"]
        front = tuple(int(x) for x in res["lottery_res"].split(","))
        back = tuple(int(res["blue_no"].split(",")[::1])) if res.get("blue_no") else None
        d: date
        try:
            d = date.fromisoformat(res["lottery_date"])
        except Exception:
            d = datetime.utcnow().date()
        return DrawNumbers(
            lottery_code=lottery_code, draw_no=normalize_draw_no(res.get("period", "")),
            draw_date=d, front=front, back=back,
        )
```

> 注：聚合数据字段（`lottery_res`/`blue_no`/`period`/`lottery_date`）以聚合 API 文档为准，实现时核对调整。

- [ ] **Step 6: 运行确认通过**

```bash
uv run pytest tests/adapters/test_draw_sources.py -v
```
Expected: 4 passed

- [ ] **Step 7: Commit**

```bash
git add app/adapters/ tests/adapters/
git commit -m "feat(adapters): DrawSource/DrawNumbers/期号归一化 + MXNZP/聚合 adapter(MockTransport 测试)"
```

---

## Task 3: FetchService（双源 + 交叉校验 + 部分源 grace + 退避 + verified 恢复）

**Files:** `app/services/__init__.py`(空), `app/services/fetch_service.py`, `tests/services/__init__.py`(空), `tests/services/test_fetch_service.py`

> 核心规则（spec §7.2/§10）：双源都成→交叉校验号码一致才 verified=true；不一致→verified=false+告警+重抓上限；一源有一源无→grace window→单源 verified=true single_source=true。

- [ ] **Step 1: 写失败测试 tests/services/test_fetch_service.py**

```python
import pytest
from unittest.mock import MagicMock
from sqlmodel import Session
from app.services.fetch_service import FetchService, FetchResult
from app.adapters.base import DrawNumbers
from datetime import date
from app.models import DrawResult


def _dn(code, no, front, back=None):
    return DrawNumbers(lottery_code=code, draw_no=no, draw_date=date(2026, 6, 21),
                       front=tuple(front), back=tuple(back) if back else None)


def _primary_ok(transport_unused): return None  # 占位


def test_fetch_cross_verify_match(db_engine):
    """双源一致 → verified=true。"""
    primary = MagicMock(); primary.name = "mxnzp"; primary.fetch.return_value = _dn("ssq", "062", [1,2,3,4,5,6], [7])
    backup = MagicMock(); backup.name = "juhe"; backup.fetch.return_value = _dn("ssq", "062", [1,2,3,4,5,6], [7])
    svc = FetchService(primary, backup, db_engine, max_attempts=6)
    result = svc.fetch_and_store("ssq")
    assert result.stored and result.verified
    with Session(db_engine) as s:
        dr = s.exec(__import__("sqlmodel").select(DrawResult)).first()
        assert dr.verified and not dr.single_source


def test_fetch_cross_verify_mismatch_rejected(db_engine):
    """双源不一致 → verified=false，不入库号码。"""
    primary = MagicMock(); primary.fetch.return_value = _dn("ssq","062",[1,2,3,4,5,6],[7])
    backup = MagicMock(); backup.fetch.return_value = _dn("ssq","062",[1,2,3,4,5,6],[8])  # 蓝球不同
    svc = FetchService(primary, backup, db_engine, max_attempts=6)
    r = svc.fetch_and_store("ssq")
    assert not r.verified  # 拒绝


def test_fetch_partial_source_single(db_engine):
    """主源有、备源无 → grace 后单源 verified=true single_source=true。"""
    primary = MagicMock(); primary.fetch.return_value = _dn("ssq","062",[1,2,3,4,5,6],[7])
    backup = MagicMock(); backup.fetch.return_value = None  # 未开奖/无
    svc = FetchService(primary, backup, db_engine, grace_seconds=0)
    r = svc.fetch_and_store("ssq")
    assert r.stored and r.verified and r.single_source


def test_fetch_not_drawn(db_engine):
    """双源都无 → 未开奖，不存。"""
    primary = MagicMock(); primary.fetch.return_value = None
    backup = MagicMock(); backup.fetch.return_value = None
    svc = FetchService(primary, backup, db_engine)
    r = svc.fetch_and_store("ssq")
    assert not r.stored and r.not_drawn
```

- [ ] **Step 2: 运行确认失败**

```bash
uv run pytest tests/services/test_fetch_service.py -v
```
Expected: FAIL

- [ ] **Step 3: 写 app/services/fetch_service.py**

```python
import time
import random
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo
from sqlmodel import Session, select
from sqlalchemy.engine import Engine
from app.adapters.base import DrawNumbers, DrawSource
from app.models import DrawResult

_CST = ZoneInfo("Asia/Shanghai")


def _now() -> datetime:
    """全程 Asia/Shanghai（spec §4.3）。aware datetime，应用层统一。"""
    return datetime.now(_CST)


@dataclass
class FetchResult:
    stored: bool
    verified: bool = False
    single_source: bool = False
    not_drawn: bool = False
    draw_result_id: int | None = None
    error: str | None = None


class FetchService:
    """双源抓取 + 交叉校验 + 部分源 grace + 退避（spec §7.2/§10）。
    源三态区分：返回 DrawNumbers=有；返回 None=未开奖(HTTP200无数据)；抛异常=源故障。"""

    def __init__(self, primary: DrawSource, backup: DrawSource, engine: Engine,
                 grace_seconds: int = 300, max_attempts: int = 6, backoff_base: float = 2.0):
        self._primary = primary
        self._backup = backup
        self._engine = engine
        self._grace = grace_seconds
        self._max_attempts = max_attempts
        self._backoff = backoff_base

    def _fetch_with_backoff(self, source: DrawSource, lottery_code: str) -> DrawNumbers | None:
        """指数退避 + 抖动重试，max_attempts 次。None=未开奖；异常=源故障（上抛）。"""
        for attempt in range(self._max_attempts):
            try:
                return source.fetch(lottery_code)
            except Exception:
                if attempt == self._max_attempts - 1:
                    raise
                time.sleep(self._backoff ** attempt + random.random())

    def _try_fetch(self, source: DrawSource, lottery_code: str) -> tuple[DrawNumbers | None, bool]:
        """返回 (numbers, ok)。ok=False=源故障（异常被捕获）；ok=True+None=未开奖。"""
        try:
            return self._fetch_with_backoff(source, lottery_code), True
        except Exception:
            return None, False

    def fetch_and_store(self, lottery_code: str) -> FetchResult:
        primary, p_ok = self._try_fetch(self._primary, lottery_code)
        backup, b_ok = self._try_fetch(self._backup, lottery_code)

        # 双源都故障 → 告警不存（spec §10）
        if not p_ok and not b_ok:
            return FetchResult(stored=False, error="all_sources_failed")

        # 仅取有效结果（故障源的 None 不算"未开奖"）
        p = primary if p_ok else None
        b = backup if b_ok else None

        # 双源都"未开奖"
        if p is None and b is None:
            return FetchResult(stored=False, not_drawn=True)

        # 双源都有 → 交叉校验
        if p is not None and b is not None:
            if _numbers_match(p, b):
                return self._store(p, verified=True, single_source=False, source_name=self._primary.name)
            return FetchResult(stored=False, verified=False, error="cross_verify_mismatch")  # admin force-verify（Plan 05）

        # 恰一源有效：grace 后重抓缺失源（spec §7.2 部分源 grace window）
        if self._grace > 0:
            time.sleep(self._grace)
            if p is None and b is not None:  # 缺主源
                p2, _ = self._try_fetch(self._primary, lottery_code)
                if p2 is not None and _numbers_match(p2, b):
                    return self._store(b, verified=True, single_source=False, source_name=self._primary.name)
            elif b is None and p is not None:  # 缺备源
                b2, _ = self._try_fetch(self._backup, lottery_code)
                if b2 is not None and _numbers_match(p, b2):
                    return self._store(p, verified=True, single_source=False, source_name=self._primary.name)

        # grace 后仍单源 → single_source 存（记录实际来源）
        only = p if p is not None else b
        src_name = self._primary.name if p is not None else self._backup.name
        return self._store(only, verified=True, single_source=True, source_name=src_name)

    def _store(self, dn: DrawNumbers, *, verified: bool, single_source: bool, source_name: str) -> FetchResult:
        import json
        with Session(self._engine) as s:
            existing = s.exec(select(DrawResult).where(
                DrawResult.lottery_code == dn.lottery_code, DrawResult.draw_no == dn.draw_no
            )).first()
            if existing:
                return FetchResult(stored=True, verified=existing.verified,
                                   single_source=existing.single_source,
                                   draw_result_id=existing.id)  # 幂等
            dr = DrawResult(
                lottery_code=dn.lottery_code, draw_no=dn.draw_no, draw_date=_now(),
                numbers_json=json.dumps({"front": list(dn.front), "back": list(dn.back) if dn.back else None}),
                source=source_name, verified=verified, single_source=single_source, version=1,
            )
            s.add(dr); s.commit(); s.refresh(dr)
            return FetchResult(stored=True, verified=verified, single_source=single_source,
                               draw_result_id=dr.id)


def _numbers_match(a: DrawNumbers, b: DrawNumbers) -> bool:
    """partition 无序→排序比；统一排序对 positional 也安全（内容相同即匹配）。"""
    if sorted(a.front) != sorted(b.front):
        return False
    if (a.back is None) != (b.back is None):
        return False
    if a.back is not None and sorted(a.back) != sorted(b.back):
        return False
    return True
```

> 注：`source` 字段在单源时记录实际来源（primary/backup）。`max_attempts`/`backoff` 防限流封禁（spec §7.2 退避抖动）。

- [ ] **Step 4: 运行确认通过**

```bash
uv run pytest tests/services/test_fetch_service.py -v
```
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add app/services/__init__.py app/services/fetch_service.py tests/services/__init__.py tests/services/test_fetch_service.py
git commit -m "feat(services): FetchService 双源交叉校验 + 部分源 grace + 退避 + 幂等存储"
```

---

## Task 4: 比对引擎 CompareService（outbox claim + domain.compare → 写 comparisons/prize_claims）

**Files:** `app/services/compare_service.py`, `tests/services/test_compare_service.py`

> spec §7.1：outbox claim（原子认领）→ 取追投 tickets → domain.compare() → 写 comparisons（唯一约束 draw_result_id+ticket_id）+ prize_claims（中奖 pending）。

- [ ] **Step 1: 写失败测试 tests/services/test_compare_service.py**

```python
import json
from datetime import datetime, timedelta
from sqlmodel import Session, select
from app.services.compare_service import CompareService
from app.models import User, Ticket, DrawResult, PendingComparison, Comparison, PrizeClaim


def _seed_draw(session, code="ssq", front=(1,2,3,4,5,6), back=(7,)):
    dr = DrawResult(lottery_code=code, draw_no="062", draw_date=datetime.utcnow(),
                    numbers_json=json.dumps({"front": list(front), "back": list(back)}),
                    source="mxnzp", verified=True, version=1)
    session.add(dr); session.commit(); session.refresh(dr)
    pc = PendingComparison(draw_result_id=dr.id)
    session.add(pc); session.commit()
    return dr


def _seed_ticket(session, user_id, front=(1,2,3,4,5,6), back=(7,)):
    t = Ticket(user_id=user_id, lottery_code="ssq", play_type="single",
               numbers_json=json.dumps({"front": list(front), "back": list(back)}),
               multiplier=1, append=False, cost=200, enabled=True)
    session.add(t); session.commit(); session.refresh(t)
    return t


def test_compare_writes_comparison_first_prize(db_engine):
    with Session(db_engine) as s:
        u = User(username="u", password_hash="x", role="user", invite_code="C"); s.add(u); s.commit(); s.refresh(u)
        _seed_draw(s)
        _seed_ticket(s, u.id)
    svc = CompareService(db_engine)
    n = svc.process_pending()  # 处理所有 outbox
    assert n == 1
    with Session(db_engine) as s:
        cmp = s.exec(select(Comparison)).first()
        assert cmp.is_win and cmp.prize_tier == 1
        # 一等奖应生成 prize_claims(pending)
        claim = s.exec(select(PrizeClaim)).first()
        assert claim and claim.status == "pending"


def test_compare_no_ticket_no_comparison(db_engine):
    """号码池无该彩种注 → outbox 标 processed 但不生成 comparisons。"""
    with Session(db_engine) as s:
        _seed_draw(s)  # 无 ticket
    svc = CompareService(db_engine)
    svc.process_pending()
    with Session(db_engine) as s:
        assert s.exec(select(Comparison)).first() is None
        pc = s.exec(select(PendingComparison)).first()
        assert pc.processed_at is not None  # 标 processed


def test_outbox_claim_idempotent(db_engine):
    """重复 process 不重复比对。"""
    with Session(db_engine) as s:
        u = User(username="u", password_hash="x", role="user", invite_code="C"); s.add(u); s.commit(); s.refresh(u)
        _seed_draw(s); _seed_ticket(s, u.id)
    svc = CompareService(db_engine)
    svc.process_pending()
    svc.process_pending()  # 第二次：无新 pending
    with Session(db_engine) as s:
        assert len(s.exec(select(Comparison)).all()) == 1  # 不重复
```

- [ ] **Step 2: 运行确认失败**

```bash
uv run pytest tests/services/test_compare_service.py -v
```
Expected: FAIL

- [ ] **Step 3: 写 app/services/compare_service.py**

```python
import json
from datetime import datetime, timedelta
from sqlmodel import Session, select
from sqlalchemy import text
from sqlalchemy.engine import Engine
from app.domain import compare as domain_compare
from app.domain.spec import LotterySpec
from app.seeds import SPECS
from app.models import (
    DrawResult, PendingComparison, Ticket, Comparison, PrizeClaim,
)


_SPEC_CACHE: dict[str, LotterySpec] = {}


def _spec_for(code: str) -> LotterySpec:
    if code not in _SPEC_CACHE:
        spec_dict = next(s for s in SPECS if s["code"] == code)
        _SPEC_CACHE[code] = LotterySpec.from_dict(spec_dict)
    return _SPEC_CACHE[code]


class CompareService:
    """比对引擎：outbox 原子认领 → domain.compare → 写 comparisons + prize_claims（spec §7.1）。"""

    def __init__(self, engine: Engine):
        self._engine = engine

    def process_pending(self) -> int:
        """处理所有未认领 pending_comparisons。返回处理条数。"""
        processed = 0
        with Session(self._engine) as s:
            pending = list(s.exec(
                select(PendingComparison).where(PendingComparison.processed_at.is_(None))
            ).all())
        for pc in pending:
            if self._claim(pc.id):
                self._compare_one(pc.draw_result_id)
                processed += 1
        return processed

    def _claim(self, pending_id: int) -> bool:
        """原子认领：UPDATE ... WHERE processed_at IS NULL RETURNING。影响 0=已被他人抢。"""
        with self._engine.begin() as conn:
            row = conn.execute(text(
                "UPDATE pending_comparisons SET processed_at = :now "
                "WHERE id = :id AND processed_at IS NULL RETURNING id"
            ), {"now": datetime.utcnow(), "id": pending_id}).first()
            return row is not None

    def _compare_one(self, draw_result_id: int) -> None:
        with Session(self._engine) as s:
            dr = s.get(DrawResult, draw_result_id)
            if dr is None or not dr.verified:
                return
            dn = json.loads(dr.numbers_json)
            draw_front = tuple(dn["front"])
            draw_back = tuple(dn["back"]) if dn.get("back") else None
            spec = _spec_for(dr.lottery_code)

            # 仅追投该彩种的启用注
            tickets = list(s.exec(select(Ticket).where(
                Ticket.lottery_code == dr.lottery_code, Ticket.enabled == True  # noqa: E712
            )).all())

            for t in tickets:
                from app.domain.entry import Entry
                tn = json.loads(t.numbers_json)
                entry = Entry(
                    lottery_code=t.lottery_code, play_type=t.play_type,
                    front=tuple(tn["front"]), back=tuple(tn["back"]) if tn.get("back") else None,
                    multiplier=t.multiplier, append=t.append,
                )
                results = domain_compare(spec=spec, draw_front=draw_front,
                                         draw_back=draw_back, entry=entry)
                # single: results 长度 1
                hit = results[0]
                self._upsert_comparison(s, user_id=t.user_id, draw_result_id=dr.id,
                                        ticket_id=t.id, hit=hit)
            s.commit()

    def _upsert_comparison(self, session: Session, *, user_id, draw_result_id, ticket_id, hit) -> None:
        """唯一约束 (draw_result_id, ticket_id)：存在则原地更新（更正重比），否则新建。
        同步 prize_claims：win→lose 删 claim（避免孤儿/虚假待兑奖）；lose→win 建 claim。"""
        import json as _json
        existing = session.exec(select(Comparison).where(
            Comparison.draw_result_id == draw_result_id, Comparison.ticket_id == ticket_id
        )).first()
        hits_json = _json.dumps({"front_hit": hit.front_hit, "back_hit": hit.back_hit})
        if existing:
            existing.hits_json = hits_json
            existing.prize_tier = hit.tier
            existing.prize_amount = hit.amount
            was_win = existing.is_win
            existing.is_win = hit.is_win
            existing.corrected_at = datetime.utcnow()
            _sync_claim(session, existing, is_win_now=hit.is_win, was_win=was_win)
        else:
            cmp = Comparison(
                user_id=user_id, draw_result_id=draw_result_id, ticket_id=ticket_id,
                hits_json=hits_json, prize_tier=hit.tier, prize_amount=hit.amount,
                is_win=hit.is_win,
            )
            session.add(cmp)
            session.flush()  # 拿 cmp.id
            if hit.is_win:
                _create_claim(session, cmp.id)


def _create_claim(session: Session, comparison_id: int) -> None:
    session.add(PrizeClaim(comparison_id=comparison_id, status="pending",
                           deadline=datetime.utcnow() + timedelta(days=60)))


def _sync_claim(session: Session, comparison: Comparison, *, is_win_now: bool, was_win: bool) -> None:
    """更正后 prize_claims 同步：win→lose 删 claim；lose→win 建 claim；win→win 不变。"""
    if was_win and not is_win_now:
        for c in session.exec(select(PrizeClaim).where(PrizeClaim.comparison_id == comparison.id)).all():
            session.delete(c)
    elif not was_win and is_win_now:
        _create_claim(session, comparison.id)
```

- [ ] **Step 4: 运行确认通过**

```bash
uv run pytest tests/services/test_compare_service.py -v
```
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add app/services/compare_service.py tests/services/test_compare_service.py
git commit -m "feat(services): CompareService outbox 原子认领 + domain.compare + comparisons/prize_claims 写入"
```

---

## Task 5: 浮奖回填 FloatRefillWorker

**Files:** `app/services/refill_service.py`, `tests/services/test_refill_service.py`

> spec §7.1：一二等奖 prize_amount=null（待派奖），次日轮询官方回填，7 天上限，回填成功补推标记（推送在 Plan 04）。

- [ ] **Step 1: 写失败测试 tests/services/test_refill_service.py**

```python
from datetime import datetime, timedelta
from sqlmodel import Session, select
from app.services.refill_service import FloatRefillWorker
from app.models import User, Ticket, DrawResult, Comparison, PrizeClaim


def _seed_float_win(engine, days_ago=0):
    with Session(engine) as s:
        u = User(username="u", password_hash="x", role="user", invite_code="C"); s.add(u); s.commit(); s.refresh(u)
        dr = DrawResult(lottery_code="ssq", draw_no="062", draw_date=datetime.utcnow()-timedelta(days=days_ago),
                        numbers_json='{"front":[1,2,3,4,5,6],"back":[7]}', source="mxnzp",
                        verified=True, version=1); s.add(dr); s.commit(); s.refresh(dr)
        t = Ticket(user_id=u.id, lottery_code="ssq", play_type="single",
                   numbers_json='{"front":[1,2,3,4,5,6],"back":[7]}', multiplier=1, cost=200); s.add(t); s.commit(); s.refresh(t)
        cmp = Comparison(user_id=u.id, draw_result_id=dr.id, ticket_id=t.id,
                         hits_json='{}', prize_tier=1, prize_amount=None, is_win=True,
                         created_at=datetime.utcnow()-timedelta(days=days_ago))
        s.add(cmp); s.commit(); s.refresh(cmp)
        return cmp.id


def test_refill_updates_null_amount(db_engine):
    cmp_id = _seed_float_win(db_engine, days_ago=1)
    from unittest.mock import MagicMock
    amount_lookup = MagicMock(return_value=5_000_000)  # 官方公布 5 万元（分）
    worker = FloatRefillWorker(db_engine, amount_lookup=amount_lookup, max_age_days=7)
    n = worker.refill()
    assert n == 1
    with Session(db_engine) as s:
        assert s.get(Comparison, cmp_id).prize_amount == 5_000_000


def test_refill_skips_after_max_age(db_engine):
    """超 7 天的浮奖不再查（unresolved）。"""
    cmp_id = _seed_float_win(db_engine, days_ago=10)
    from unittest.mock import MagicMock
    worker = FloatRefillWorker(db_engine, amount_lookup=MagicMock(return_value=999), max_age_days=7)
    assert worker.refill() == 0  # 超期不查
    with Session(db_engine) as s:
        assert s.get(Comparison, cmp_id).prize_amount is None
```

- [ ] **Step 2: 运行确认失败**

```bash
uv run pytest tests/services/test_refill_service.py -v
```
Expected: FAIL

- [ ] **Step 3: 写 app/services/refill_service.py**

```python
from datetime import datetime, timedelta
from typing import Callable
from sqlmodel import Session, select
from sqlalchemy.engine import Engine
from app.models import Comparison, DrawResult


class FloatRefillWorker:
    """浮奖回填：一二等奖 prize_amount=null 的，轮询官方金额回填，max_age_days 上限。"""

    def __init__(self, engine: Engine,
                 amount_lookup: Callable[[str, str, int], int | None],
                 max_age_days: int = 7):
        self._engine = engine
        self._lookup = amount_lookup  # amount_lookup(lottery_code, draw_no, tier) -> 分 | None
        self._max_age = max_age_days

    def refill(self) -> int:
        cutoff = datetime.utcnow() - timedelta(days=self._max_age)
        refilled = 0
        with Session(self._engine) as s:
            pending = list(s.exec(select(Comparison).where(
                Comparison.is_win == True,  # noqa: E712
                Comparison.prize_amount.is_(None),
                Comparison.created_at >= cutoff,
            )).all())
            # 预载 draw_result 映射（拿 lottery_code/draw_no 查官方奖金）
            dr_ids = {c.draw_result_id for c in pending}
            drs = {dr.id: dr for dr in s.exec(
                select(DrawResult).where(DrawResult.id.in_(dr_ids))
            ).all()}
        for cmp in pending:
            dr = drs.get(cmp.draw_result_id)
            if dr is None or cmp.prize_tier is None:
                continue
            amount = self._lookup(dr.lottery_code, dr.draw_no, cmp.prize_tier)
            if amount is not None:
                with Session(self._engine) as s:
                    c = s.get(Comparison, cmp.id)
                    c.prize_amount = amount
                    s.commit()
                refilled += 1
                # 补推：回填后金额变更，由 Plan 04 Notifier 监听 prize_amount 变更事件推送
                # （本 plan 仅回填数据；推送在 Plan 04 接线，避免循环依赖与跨 plan 耦合）
        return refilled
```

> 注：`amount_lookup` 真实实现查官方数据源（MXNZP/聚合的奖金接口，Plan 04/05 接线）；测试用 mock。补推在 Plan 04 Notifier 消费标记。

- [ ] **Step 4: 运行确认通过**

```bash
uv run pytest tests/services/test_refill_service.py -v
```
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add app/services/refill_service.py tests/services/test_refill_service.py
git commit -m "feat(services): FloatRefillWorker 浮奖回填（7天上限 + amount_lookup 注入）"
```

---

## Task 6: 结果更正 DrawCorrectService

**Files:** `app/services/correct_service.py`, `tests/services/test_correct_service.py`

> spec §7.1：官方更正 → draw_corrections 记录 + version++ + 重新 outbox → 重比时 comparisons 原地更新（唯一约束兜底，不新增行，避免 stats 双算）。

- [ ] **Step 1: 写失败测试 tests/services/test_correct_service.py**

```python
import json
from datetime import datetime
from sqlmodel import Session, select
from app.services.correct_service import DrawCorrectService
from app.services.compare_service import CompareService
from app.models import User, Ticket, DrawResult, PendingComparison, Comparison, DrawCorrection


def test_correct_increments_version_and_recompares(db_engine):
    with Session(db_engine) as s:
        u = User(username="u", password_hash="x", role="user", invite_code="C"); s.add(u); s.commit(); s.refresh(u)
        # 原开奖：6红+7蓝，用户中一等
        dr = DrawResult(lottery_code="ssq", draw_no="062", draw_date=datetime.utcnow(),
                        numbers_json='{"front":[1,2,3,4,5,6],"back":[7]}', source="mxnzp",
                        verified=True, version=1); s.add(dr); s.commit(); s.refresh(dr)
        pc = PendingComparison(draw_result_id=dr.id); s.add(pc); s.commit()
        t = Ticket(user_id=u.id, lottery_code="ssq", play_type="single",
                   numbers_json='{"front":[1,2,3,4,5,6],"back":[7]}', multiplier=1, cost=200)
        s.add(t); s.commit()
        dr_id = dr.id
    # 首次比对
    CompareService(db_engine).process_pending()
    # 官方更正：蓝球应为 8（用户从一等→未中）
    svc = DrawCorrectService(db_engine)
    svc.correct(draw_result_id=dr_id, new_front=(1,2,3,4,5,6), new_back=(8,), reason="官方更正")
    # 重新比对
    CompareService(db_engine).process_pending()
    with Session(db_engine) as s:
        dr = s.get(DrawResult, dr_id)
        assert dr.version == 2
        corr = s.exec(select(DrawCorrection)).first()
        assert corr is not None
        cmps = s.exec(select(Comparison)).all()
        assert len(cmps) == 1  # 原地更新，不新增
        assert cmps[0].is_win is False  # 更正后未中
        assert cmps[0].corrected_at is not None
```

- [ ] **Step 2: 运行确认失败**

```bash
uv run pytest tests/services/test_correct_service.py -v
```
Expected: FAIL

- [ ] **Step 3: 写 app/services/correct_service.py**

```python
import json
from datetime import datetime
from sqlmodel import Session
from sqlalchemy.engine import Engine
from app.models import DrawResult, DrawCorrection, PendingComparison


class DrawCorrectService:
    """开奖结果官方更正（spec §7.1）。"""

    def __init__(self, engine: Engine):
        self._engine = engine

    def correct(self, *, draw_result_id: int, new_front: tuple[int, ...],
                new_back: tuple[int, ...] | None, reason: str) -> None:
        with Session(self._engine) as s:
            dr = s.get(DrawResult, draw_result_id)
            if dr is None:
                raise ValueError(f"draw_result {draw_result_id} 不存在")
            old = json.loads(dr.numbers_json)
            new_json = {"front": list(new_front), "back": list(new_back) if new_back else None}
            # 记录更正历史
            s.add(DrawCorrection(
                draw_result_id=draw_result_id,
                old_numbers_json=dr.numbers_json, new_numbers_json=json.dumps(new_json),
                reason=reason,
            ))
            # 原地更新号码 + version++
            dr.numbers_json = json.dumps(new_json)
            dr.version += 1
            # 重新生成 outbox（触发重比，CompareService._upsert_comparison 原地更新）
            s.add(PendingComparison(draw_result_id=draw_result_id))
            s.commit()
```

- [ ] **Step 4: 运行确认通过**

```bash
uv run pytest tests/services/test_correct_service.py -v
```
Expected: 1 passed

- [ ] **Step 5: Commit**

```bash
git add app/services/correct_service.py tests/services/test_correct_service.py
git commit -m "feat(services): DrawCorrectService 官方更正（version++ + outbox 重比 + 原地更新 comparisons）"
```

---

## Task 7: 端到端集成测试（fetch → verify → compare → comparisons）

**Files:** `tests/integration/__init__.py`(空), `tests/integration/test_core_loop.py`

- [ ] **Step 1: 写 tests/integration/test_core_loop.py**

```python
import json
from datetime import datetime
from unittest.mock import MagicMock
from sqlmodel import Session, select
from app.adapters.base import DrawNumbers
from app.services.fetch_service import FetchService
from app.services.compare_service import CompareService
from app.models import User, Ticket, DrawResult, Comparison, PrizeClaim
from datetime import date


def _dn(front, back):
    return DrawNumbers(lottery_code="ssq", draw_no="062", draw_date=date(2026, 6, 21),
                       front=tuple(front), back=tuple(back))


def test_full_loop_win(db_engine):
    """完整闭环：抓取→校验→比对→comparisons+prize_claims。"""
    primary = MagicMock(); primary.name = "mxnzp"; primary.fetch.return_value = _dn([1,2,3,4,5,6],[7])
    backup = MagicMock(); backup.name = "juhe"; backup.fetch.return_value = _dn([1,2,3,4,5,6],[7])

    # 用户 + 追投票
    with Session(db_engine) as s:
        u = User(username="u", password_hash="x", role="user", invite_code="C"); s.add(u); s.commit(); s.refresh(u)
        s.add(Ticket(user_id=u.id, lottery_code="ssq", play_type="single",
                     numbers_json='{"front":[1,2,3,4,5,6],"back":[7]}', multiplier=1, cost=200))
        s.commit()

    fetch = FetchService(primary, backup, db_engine, grace_seconds=0)
    r = fetch.fetch_and_store("ssq")
    assert r.stored and r.verified

    CompareService(db_engine).process_pending()

    with Session(db_engine) as s:
        cmp = s.exec(select(Comparison)).first()
        assert cmp and cmp.is_win and cmp.prize_tier == 1
        claim = s.exec(select(PrizeClaim)).first()
        assert claim and claim.status == "pending"


def test_full_loop_lose(db_engine):
    """未追投的号码不中。"""
    primary = MagicMock(); primary.name="mxnzp"; primary.fetch.return_value = _dn([8,9,10,11,12,13],[14])
    backup = MagicMock(); backup.name="juhe"; backup.fetch.return_value = _dn([8,9,10,11,12,13],[14])
    with Session(db_engine) as s:
        u = User(username="u", password_hash="x", role="user", invite_code="C"); s.add(u); s.commit(); s.refresh(u)
        s.add(Ticket(user_id=u.id, lottery_code="ssq", play_type="single",
                     numbers_json='{"front":[1,2,3,4,5,6],"back":[7]}', multiplier=1, cost=200))
        s.commit()
    FetchService(primary, backup, db_engine, grace_seconds=0).fetch_and_store("ssq")
    CompareService(db_engine).process_pending()
    with Session(db_engine) as s:
        cmp = s.exec(select(Comparison)).first()
        assert cmp and not cmp.is_win
```

- [ ] **Step 2: 运行确认通过**

```bash
uv run pytest tests/integration/test_core_loop.py -v
```
Expected: 2 passed

- [ ] **Step 3: 跑全量测试确认无回归**

```bash
uv run pytest -v
```
Expected: Plan 01-03 全绿

- [ ] **Step 4: Commit**

```bash
git add tests/integration/
git commit -m "test(integration): 端到端核心闭环（fetch→verify→compare→comparisons+claims）"
```

---

## Self-Review

**Spec 覆盖（Plan 03 = §7 核心数据流 + §6.3 用户隔离 + §10 错误处理）：**
- ✅ Repository user_id 注入 + IDOR-safe（§6.3）→ Task 1
- ✅ 双源抓取 + 交叉校验 + 部分源 grace + 期号归一化（§7.2）→ Task 2-3
- ✅ 抓取退避抖动 + verified 恢复（§7.2/§10）→ Task 3（max_attempts/backoff；verified=false 返回，admin force-verify 在 Plan 05）
- ✅ outbox claim 原子认领 + 比对一次（§7.1）→ Task 4
- ✅ 比对引擎接线 domain.compare + 写 comparisons/prize_claims（§7.1）→ Task 4
- ✅ 浮奖回填（7 天上限）（§7.1）→ Task 5
- ✅ 结果更正（version++ + 原地更新 comparisons）（§7.1）→ Task 6
- ✅ 端到端闭环 → Task 7
- 📌 admin force-verify（verified=false 人工恢复）→ Plan 05（admin 后台）
- 📌 补推（浮奖回填后推送）→ Plan 04（Notifier 消费标记）
- 📌 调度接线（APScheduler 调 fetch/compare/refill worker）→ Plan 04

**Placeholder scan：** 无 TBD；adapter 字段名标注"以 API 文档为准，实现时核对调整"（非 placeholder——给了结构 + 解析约定，真实字段注册后核对）。
**类型一致：** `FetchResult`/`DrawNumbers`/`compare()` 入口签名前后一致；`CompareService._upsert_comparison` 与 Plan 02 `HitResult` 字段对齐。
**衔接：** Plan 04 调度器调 `FetchService.fetch_and_store` / `CompareService.process_pending` / `FloatRefillWorker.refill`；Plan 05 admin 调 `DrawCorrectService.correct` + force-verify；Plan 06 API 查 comparisons。
**领域纯净：** services/adapters/repositories 均 import domain（单向），domain 不反向 import——符合分层。
