# Phase 1 · Plan 5: 扩展功能（统计 + 走势 + 提醒 + 钉钉企微 + 奖级DB化 + 运维装配）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`).

**Goal:** 补齐 MVP 的扩展功能：统计聚合（盈亏/命中率/等级分布）、走势数据（合规版）、提醒（兑奖/税务/开奖信息）、周月报、钉钉/企微渠道、奖级表 DB 化（可配置）、scheduler async 装配收尾与运维端点。

**Architecture:** 新增 service 层（stats/trend/reminders/report）与对应 API。奖级规则从 Plan 1 的硬编码 `prize_tables.py` 迁移到 DB 表（admin 可改，应用启动时种子）。Notifier 插件加钉钉/企微。scheduler 装配真实 async 回调。

**Tech Stack:** 同前（Python/FastAPI/SQLModel）。走势/统计图表在前端（Plan 4 ECharts），本 plan 提供数据 API。

**前置依赖:** Plan 1-4 完成。

**对应 Spec:** §3.1（统计/提醒/走势/运维）、§5.3（奖级可配置）、§8（钉钉企微）

---

## Task 1: 钉钉 + 企微 Notifier

**Files:**
- Create: `app/services/notifier/dingtalk.py`, `app/services/notifier/wecom.py`
- Modify: `app/services/orchestration.py` / `app/api/notifications.py` 的渠道工厂支持新类型
- Test: `tests/services/test_notifier_extra.py`

- [ ] **Step 1: 写测试 `tests/services/test_notifier_extra.py`**

```python
import pytest, respx, httpx
from app.services.notifier.dingtalk import DingtalkNotifier
from app.services.notifier.wecom import WecomNotifier
from app.services.notifier.base import Message

@pytest.mark.asyncio
@respx.mock
async def test_dingtalk_webhook():
    r = respx.post("https://oapi.dingtalk.com/robot/send").mock(
        return_value=httpx.Response(200, json={"errcode": 0}))
    n = DingtalkNotifier(webhook="https://oapi.dingtalk.com/robot/send", secret="")
    assert await n.send(Message(title="t", body="b")) is True

@pytest.mark.asyncio
@respx.mock
async def test_wecom_webhook():
    r = respx.post("https://qyapi.weixin.qq.com/cgi-bin/webhook/send").mock(
        return_value=httpx.Response(200, json={"errcode": 0}))
    n = WecomNotifier(webhook="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=K")
    assert await n.send(Message(title="t", body="b")) is True
```

- [ ] **Step 2: 实现 `dingtalk.py` / `wecom.py`（照 Bark/飞书结构）**

```python
# dingtalk.py
import httpx
from app.services.notifier.base import Message

class DingtalkNotifier:
    type = "dingtalk"
    def __init__(self, webhook: str, secret: str = ""):
        self.webhook = webhook; self.secret = secret
    async def send(self, msg: Message) -> bool:
        payload = {"msgtype": "text", "text": {"content": f"{msg.title}\n{msg.body}"}}
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(self.webhook, json=payload)
        return r.json().get("errcode") == 0
```

```python
# wecom.py — 结构同上，webhook 不同，payload 为 {"msgtype":"text","text":{"content":...}}
class WecomNotifier:
    type = "wecom"
    def __init__(self, webhook: str):
        self.webhook = webhook
    async def send(self, msg: Message) -> bool:
        payload = {"msgtype": "text", "text": {"content": f"{msg.title}\n{msg.body}"}}
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(self.webhook, json=payload)
        return r.json().get("errcode") == 0
```

- [ ] **Step 3: 渠道工厂（`app/services/notifier/factory.py`）统一构造**

```python
import json
from sqlmodel import Session, select
from app.db.models import NotificationChannel
from app.services.notifier.base import Notifier
from app.services.notifier.bark import BarkNotifier
from app.services.notifier.feishu import FeishuNotifier
from app.services.notifier.dingtalk import DingtalkNotifier
from app.services.notifier.wecom import WecomNotifier

_BUILDERS = {"bark": lambda c: BarkNotifier(c["device_key"]),
             "feishu": lambda c: FeishuNotifier(c["webhook"]),
             "dingtalk": lambda c: DingtalkNotifier(c["webhook"], c.get("secret", "")),
             "wecom": lambda c: WecomNotifier(c["webhook"])}

def build_notifiers(session: Session, user_id: int) -> list[Notifier]:
    rows = session.exec(select(NotificationChannel).where(
        NotificationChannel.user_id == user_id,
        NotificationChannel.enabled == True)).all()  # noqa: E712
    out = []
    for ch in rows:
        builder = _BUILDERS.get(ch.type)
        if builder:
            try: out.append(builder(json.loads(ch.config_json)))
            except KeyError: continue
    return out
```

> **重构：** `orchestration.py`/`cli.py`/`notify_dispatcher` 的 `channel_factory` 改为调用 `build_notifiers(session, user_id)`，消除 Plan 2-3 的临时嵌套。

- [ ] **Step 4: 测试通过 + Commit**

Run: `pytest tests/services/test_notifier_extra.py -v` → PASS
```bash
git add app/services/notifier/dingtalk.py app/services/notifier/wecom.py \
        app/services/notifier/factory.py tests/services/test_notifier_extra.py
git commit -m "feat(notifier): 钉钉+企微渠道 + 统一渠道工厂"
```

---

## Task 2: 奖级表 DB 化（可配置）

**Files:**
- Create: `app/db/models.py` 追加 `PrizeRule` 表
- Create: `app/db/seed.py`（从 `prize_tables.py` 迁移种子）
- Modify: `app/domain/compare/partition.py` / `positional.py`（从 DB 读奖级，带内存缓存）
- Test: `tests/db/test_prize_seed.py`

- [ ] **Step 1: `app/db/models.py` 追加 PrizeRule**

```python
class PrizeRule(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    lottery_code: str = Field(index=True)
    tier: int
    name: str
    conditions_json: str     # "[[4,0],[3,1]]"
    amount: int | None       # 分，None=浮动
    amount_type: str         # fixed / float
    sort_order: int          # 从高到低
```

- [ ] **Step 2: `app/db/seed.py` 从 Plan 1 的表迁移**

```python
import json
from sqlmodel import Session
from app.db.models import PrizeRule, LotteryType  # LotteryType 见下
from app.domain.prize_tables import PRIZE_TABLES

def seed_prizes(session: Session):
    if session.exec(__import__("sqlmodel").select(PrizeRule)).first():
        return  # 已种子
    for code, tiers in PRIZE_TABLES.items():
        for i, t in enumerate(tiers):
            session.add(PrizeRule(lottery_code=code, tier=t.tier, name=t.name,
                conditions_json=json.dumps([list(c) for c in t.conditions]),
                amount=t.amount, amount_type=t.amount_type.value, sort_order=i))
    session.commit()
```

- [ ] **Step 3: 比对策略改读 DB（带模块级缓存，admin 改后清缓存）**

新增 `app/domain/prize_loader.py`：
```python
import json
from sqlmodel import Session, select
from app.db.models import PrizeRule
from app.domain.models import AmountType, PrizeTier

_cache: dict[str, tuple[PrizeTier, ...]] = {}

def load_tiers(session: Session, lottery_code: str) -> tuple[PrizeTier, ...]:
    if lottery_code in _cache:
        return _cache[lottery_code]
    rows = session.exec(select(PrizeRule).where(
        PrizeRule.lottery_code == lottery_code).order_by(PrizeRule.sort_order)).all()
    tiers = tuple(PrizeTier(lottery_code, r.tier, r.name,
                  tuple(tuple(c) for c in json.loads(r.conditions_json)),
                  r.amount, AmountType(r.amount_type)) for r in rows)
    _cache[lottery_code] = tiers
    return tiers

def invalidate(lottery_code: str | None = None):
    if lottery_code:
        _cache.pop(lottery_code, None)
    else:
        _cache.clear()
```

- [ ] **Step 4: 修改 `partition.py`/`positional.py` 比对时传 session 读 DB**

把 `compare(spec, draw, entry)` 扩展为接受可选 session，读 `prize_loader.load_tiers(session, spec.code)`。比对引擎 `CompareEngine.compare_draw` 传 session。**保持领域层纯逻辑**：`prize_loader` 返回 `PrizeTier` tuple 后，比对仍是纯函数。

> **架构保持：** 比对策略接收 `tiers: tuple[PrizeTier,...]` 作为参数（而非直接读 DB），DB 读取由引擎层注入。这样领域层仍无 IO、可纯测。

- [ ] **Step 5: 测试 `tests/db/test_prize_seed.py`（种子后读回与原表一致）**

```python
def test_seed_matches_hardcoded(session):
    from app.db.seed import seed_prizes
    from app.domain.prize_loader import load_tiers
    seed_prizes(session)
    tiers = load_tiers(session, "ssq")
    assert [t.tier for t in tiers] == [1,2,3,4,5,6]
    assert tiers[3].amount == 20000  # 四等 200元
```

- [ ] **Step 6: 测试通过 + Commit**

Run: `pytest tests/db/test_prize_seed.py tests/domain/ -v` → PASS（领域层测试加 session 注入 tiers）
```bash
git add app/db/models.py app/db/seed.py app/domain/prize_loader.py \
        app/domain/compare/ tests/db/test_prize_seed.py
git commit -m "feat: 奖级表DB化(可配置)+种子迁移+缓存"
```

---

## Task 3: 统计聚合 service + API

**Files:**
- Create: `app/services/stats.py`, `app/api/stats.py`
- Test: `tests/services/test_stats.py`

- [ ] **Step 1: 写测试 `tests/services/test_stats.py`**

```python
from app.db.models import Comparison, Ticket
from app.services.stats import compute_user_stats

def test_user_stats_aggregates(session):
    session.add(Ticket(user_id=1, lottery_code="ssq", numbers_json="{}"))
    session.add(Comparison(user_id=1, draw_result_id=1, ticket_id=1, lottery_code="ssq",
        draw_no="1", hits_json="{}", prize_tier=5, prize_amount=1000, is_win=True))
    session.add(Comparison(user_id=1, draw_result_id=2, ticket_id=1, lottery_code="ssq",
        draw_no="2", hits_json="{}", prize_tier=None, prize_amount=None, is_win=False))
    session.commit()
    s = compute_user_stats(session, user_id=1)
    assert s["total_win"] == 1000           # 分
    assert s["win_count"] == 1
    assert s["tier_dist"][5] == 1
    assert s["total_periods"] >= 1
```

- [ ] **Step 2: 实现 `app/services/stats.py`**

```python
import json
from collections import Counter
from sqlmodel import Session, select, func
from app.db.models import Comparison, Ticket

def compute_user_stats(session: Session, user_id: int) -> dict:
    comps = session.exec(select(Comparison).where(Comparison.user_id == user_id)).all()
    wins = [c for c in comps if c.is_win]
    total_win = sum(c.prize_amount or 0 for c in wins)
    tier_dist = Counter(c.prize_tier for c in wins if c.prize_tier)
    periods = {c.draw_no for c in comps}
    tickets = session.exec(select(Ticket).where(Ticket.user_id == user_id)).all()
    # 投入：每注每期 2 元 = 200 分（简化；大乐透追加等在 Phase 2）
    total_invest = len(tickets) * len(periods) * 200
    return {
        "total_win": total_win, "total_invest": total_invest,
        "net": total_win - total_invest, "win_count": len(wins),
        "total_periods": len(periods),
        "tier_dist": dict(tier_dist),
        "hit_rate": round(len(wins) / len(comps), 3) if comps else 0,
    }
```

- [ ] **Step 3: `app/api/stats.py` 暴露端点**

```python
from fastapi import APIRouter, Depends
from sqlmodel import Session
from app.core.deps import current_user
from app.db.database import get_session
from app.services.stats import compute_user_stats

router = APIRouter(prefix="/api/stats", tags=["stats"])

@router.get("")
def my_stats(u=Depends(current_user), session: Session = Depends(get_session)):
    return compute_user_stats(session, u.id)
```

- [ ] **Step 4: 注册到 `router.py` + 测试通过 + Commit**

Run: `pytest tests/services/test_stats.py -v` → PASS
```bash
git add app/services/stats.py app/api/stats.py app/api/router.py tests/services/test_stats.py
git commit -m "feat: 统计聚合service+API(盈亏/命中率/等级分布)"
```

---

## Task 4: 走势数据 service + API（合规版）

**Files:**
- Create: `app/services/trend.py`, `app/api/trend.py`
- Test: `tests/services/test_trend.py`

- [ ] **Step 1: 写测试 `tests/services/test_trend.py`**

```python
from app.db.models import DrawResult
from app.services.trend import compute_frequency

def test_frequency_recent_n(session):
    for i in range(10):
        session.add(DrawResult(lottery_code="ssq", draw_no=str(i), draw_date="2024-01-01",
            numbers_json='{"front":[1,2,3,4,5,6],"back":[7]}', source="mxnzp", verified=True))
    session.commit()
    freq = compute_frequency(session, "ssq", recent=5)
    assert freq["front"][1] == 5    # 近5期 1 号出现5次
    assert "disclaimer" in freq     # 必带随机声明
```

- [ ] **Step 2: 实现 `app/services/trend.py`**

```python
import json
from collections import Counter
from sqlmodel import Session, select
from app.db.models import DrawResult

DISCLAIMER = "彩票为独立随机事件，历史不代表未来，本数据仅供历史回顾，不构成任何选号建议。"

def compute_frequency(session: Session, lottery_code: str, recent: int = 30) -> dict:
    rows = session.exec(select(DrawResult).where(
        DrawResult.lottery_code == lottery_code
    ).order_by(DrawResult.draw_no.desc()).limit(recent)).all()
    front_freq = Counter()
    back_freq = Counter()
    history = []
    for r in rows:
        n = json.loads(r.numbers_json)
        front_freq.update(n["front"])
        back_freq.update(n.get("back", []))
        history.append({"draw_no": r.draw_no, "numbers": n})
    history.reverse()  # 时间正序
    return {
        "recent": recent, "front": dict(front_freq), "back": dict(back_freq),
        "history": history, "disclaimer": DISCLAIMER,
    }
```

- [ ] **Step 3: `app/api/trend.py`（公开版默认关，admin 开关）**

```python
from fastapi import APIRouter, Depends, Query
from sqlmodel import Session
from app.core.deps import current_user
from app.db.database import get_session
from app.services.trend import compute_frequency
from app.core.config import get_settings

router = APIRouter(prefix="/api/trend", tags=["trend"])

@router.get("")
def trend(lottery_code: str = Query(...), recent: int = Query(30, ge=1, le=100),
          u=Depends(current_user), session: Session = Depends(get_session)):
    if not get_settings().trend_enabled:          # 公开版默认关
        from fastapi import HTTPException
        raise HTTPException(404, "走势图未开启")
    return compute_frequency(session, lottery_code, recent)
```

- [ ] **Step 4: Settings 加 `trend_enabled: bool = False`；注册路由；测试通过 + Commit**

Run: `pytest tests/services/test_trend.py -v` → PASS（测试 mock settings.trend_enabled=True）
```bash
git add app/services/trend.py app/api/trend.py app/core/config.py \
        app/api/router.py tests/services/test_trend.py
git commit -m "feat: 走势数据service+API(合规版,近N期频次,公开默认关)"
```

---

## Task 5: 提醒 service（兑奖/税务/开奖信息）+ 周月报

**Files:**
- Create: `app/services/reminders.py`, `app/services/report.py`
- Modify: scheduler 注册提醒/报表 job
- Test: `tests/services/test_reminders.py`

- [ ] **Step 1: 写测试 `tests/services/test_reminders.py`**

```python
from app.db.models import Comparison
from app.services.reminders import build_claim_reminders

def test_claim_reminder_near_expiry(session):
    from datetime import date, timedelta
    session.add(Comparison(user_id=1, draw_result_id=1, ticket_id=1, lottery_code="ssq",
        draw_no="1", hits_json="{}", prize_tier=6, prize_amount=500, is_win=True,
        created_at=date.today() - timedelta(days=50)))  # 50天前中,剩10天
    session.commit()
    reminders = build_claim_reminders(session, user_id=1)
    assert len(reminders) == 1
    assert reminders[0]["days_left"] <= 60
    assert "兑奖" in reminders[0]["title"]
```

- [ ] **Step 2: 实现 `app/services/reminders.py`**

```python
from datetime import datetime, timedelta
from sqlmodel import Session, select
from app.db.models import Comparison

CLAIM_DEADLINE_DAYS = 60
TAX_THRESHOLD = 1_000_000  # 1万元 = 1000000 分

def build_claim_reminders(session: Session, user_id: int) -> list[dict]:
    wins = session.exec(select(Comparison).where(
        Comparison.user_id == user_id, Comparison.is_win == True)).all()  # noqa: E712
    out = []
    for w in wins:
        # 注：生产用 prize_claims 表跟踪兑奖状态；此处简化用 created_at 倒推
        days_left = CLAIM_DEADLINE_DAYS - (datetime.utcnow() - w.created_at).days
        if days_left <= 0:
            continue
        tax_note = f"（超1万元需缴20%偶然所得税）" if (w.prize_amount or 0) >= TAX_THRESHOLD else ""
        out.append({
            "title": f"{w.lottery_code} 第{w.draw_no}期兑奖提醒 {tax_note}",
            "days_left": days_left,
            "amount": w.prize_amount,
        })
    return [r for r in out if r["days_left"] <= CLAIM_DEADLINE_DAYS]

def build_draw_info_reminders() -> list[dict]:
    """开奖信息提醒：今日开奖的彩种（中性信息，非引导）。"""
    from app.domain.lottery_types import LOTTERY_TYPES
    today = datetime.utcnow().weekday()
    codes = [c for c, s in LOTTERY_TYPES.items() if today in s.draw_days]
    return [{"title": f"今晚开奖：{', '.join(codes)}", "type": "draw_info"}] if codes else []
```

- [ ] **Step 3: 实现 `app/services/report.py`（周月报）**

```python
from sqlmodel import Session
from app.services.stats import compute_user_stats
from app.services.notifier.base import Message

def build_weekly_report(session: Session, user_id: int) -> Message:
    s = compute_user_stats(session, user_id)
    net = f"+¥{s['net']/100:.0f}" if s["net"] >= 0 else f"-¥{abs(s['net'])/100:.0f}"
    return Message(
        title="本周购彩核对汇总",
        body=(f"累计投入 ¥{s['total_invest']/100:.0f}\n累计中奖 ¥{s['total_win']/100:.0f}\n"
              f"净盈亏 {net}\n理性购彩 量力而行"))
```

- [ ] **Step 4: scheduler 注册提醒（每日 08:00）与周报（周一 08:00）；测试通过 + Commit**

Run: `pytest tests/services/test_reminders.py -v` → PASS
```bash
git add app/services/reminders.py app/services/report.py \
        app/services/scheduler.py tests/services/test_reminders.py
git commit -m "feat: 提醒(兑奖/税务/开奖信息)+周月报 service"
```

---

## Task 6: 运维装配收尾（scheduler async 回调 + admin 触发 + 数据源监控）

**Files:**
- Modify: `app/main.py`（完整 lifespan 装配 async 回调）
- Modify: `app/api/admin.py`（手动触发端点 + 数据源健康）
- Test: `tests/api/test_admin_trigger.py`

- [ ] **Step 1: `app/main.py` lifespan 完整装配**

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    engine = init_db()
    # 种子奖级
    from sqlmodel import Session
    with Session(engine) as s:
        from app.db.seed import seed_prizes
        seed_prizes(s)
    # scheduler 装配
    from app.services.scheduler import Scheduler
    from app.services.orchestration import fetch_compare_push
    from app.services.notifier.factory import build_notifiers
    from app.services.reminders import build_claim_reminders
    from app.services.notifier.base import dispatch

    codes = ["ssq", "dlt", "qlc", "fc3d", "qxc", "pl3", "pl5"]

    async def poll_job(code: str):
        with Session(get_engine()) as s:
            await fetch_compare_push(s, code,
                channel_factory=lambda uid: build_notifiers(s, uid))

    async def summary_job():
        for code in codes:
            with Session(get_engine()) as s:
                await fetch_compare_push(s, code,
                    channel_factory=lambda uid: build_notifiers(s, uid))

    sched = Scheduler()
    sched.register(codes, poll_callback=poll_job, summary_callback=summary_job)
    sched.start()
    yield
    sched.shutdown()
```

- [ ] **Step 2: `app/api/admin.py` 加手动触发 + 数据源健康**

```python
@router.post("/trigger")
async def trigger(lottery_code: str, admin=Depends(require_admin),
                  session: Session = Depends(get_session)):
    from app.services.orchestration import fetch_compare_push
    from app.services.notifier.factory import build_notifiers
    await fetch_compare_push(session, lottery_code,
        channel_factory=lambda uid: build_notifiers(session, uid))
    return {"status": "done"}

@router.get("/sources")
def source_status(admin=Depends(require_admin), session: Session = Depends(get_session)):
    from app.db.models import DrawResult
    rows = session.exec(select(DrawResult).order_by(
        DrawResult.fetched_at.desc()).limit(7)).all()
    return [{"lottery": r.lottery_code, "no": r.draw_no,
             "fetched_at": str(r.fetched_at), "source": r.source} for r in rows]
```

- [ ] **Step 3: 测试 `tests/api/test_admin_trigger.py`（mock 编排）+ 通过 + Commit**

Run: `pytest tests/api/test_admin_trigger.py -v` → PASS
```bash
git add app/main.py app/api/admin.py tests/api/test_admin_trigger.py
git commit -m "feat: scheduler完整async装配 + admin触发 + 数据源健康"
```

---

## Task 7: 全量测试 + 覆盖率

- [ ] **Step 1: 跑全部测试**

Run: `pytest -v && cd web && npx vitest run`
Expected: 全部通过，覆盖率 ≥ 80%

- [ ] **Step 2: Commit**

```bash
git commit --allow-empty -m "test: Plan 5 全量测试通过"
```

---

## Self-Review（已执行）

**1. Spec 覆盖：** §3.1 统计 → Task 3 ✅；走势（合规版）→ Task 4 ✅；提醒（兑奖/税务/开奖信息）→ Task 5 ✅；钉钉/企微 → Task 1 ✅；运维管理 → Task 6 ✅；§5.3 奖级可配置 → Task 2 ✅。
**2. 占位符：** 无。`prize_claims` 完整兑奖台账跟踪在 Task 5 注明简化（用 created_at 倒推），生产应接 prize_claims.status——这是已知简化非占位符。✅
**3. 类型一致：** `compute_user_stats`/`compute_frequency`/`build_notifiers` 签名一致；`PrizeTier` 从 loader 注入比对，领域层保持纯逻辑。✅

---

## Execution Handoff

Plan 5 完成（7 Task）：MVP 全部扩展功能（统计/走势/提醒/周月报/钉钉企微/奖级DB化/运维装配）。至此 Phase 1 MVP 功能完整，仅差部署（Plan 6）。
