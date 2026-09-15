# 数据源健康告警 + 启动回填 QPS 间隔 实施计划（plan-11）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** mxnzp 抓取持续失败 30 分钟 → admin Bark 告警（恢复再通知），`ApiSourceHealth` 从有读无写变为实时落表，启动回填补 QPS 间隔。

**Architecture:** 新增 `app/services/source_health.py`（健康写入 `record_source_health` + 告警评估 `evaluate_source_alerts`，状态机 `none→alerted→recovering→none`、送达才转移）；`FetchService._try_fetch` 扩为三态 outcome 并写表；评估挂在 `path_a_tick` 尾部与 `run_startup_backfill` 尾部；`build_admin_alert` 从 auth.py 迁至 `app/notifications/admin_alert.py` 共用。

**Tech Stack:** Python 3.12 / SQLModel + Alembic / APScheduler / httpx / pytest（后端）；Vue3 + vitest（前端仅展示两列）。

**Spec:** `docs/superpowers/specs/2026-09-15-source-health-alert-design.md`（含 2026-09-15 修订：抖动取消恢复通知、down_since 保留至恢复通知送达、间隔只加在真实请求间）。

## Global Constraints

- 时间纪律（CLAUDE.md）：`down_since` / `last_success_at` / 评估 now 全部 naive UTC（`datetime.now(timezone.utc).replace(tzinfo=None)`），与 `TimestampMixin.created_at` 同表示；DB 内不做 naive/aware 混比。
- 测试零真实网络：mock 数据源 / stub 告警发送。
- 每任务 TDD：先看测试失败（RED），再实现（GREEN），每任务一 commit。
- 注释风格与仓库一致（中文、讲 why 不讲 what）。
- 仓库命令均用 `uv run`；提交只 add 本任务涉及文件。

---

### Task 1: ApiSourceHealth 扩列 + Alembic 迁移

**Files:**
- Modify: `app/models/health.py`
- Create: `alembic/versions/t11_source_health_alert.py`
- Test: `tests/test_migration_t11_source_health.py`

**Interfaces:**
- Produces: `ApiSourceHealth.down_since: datetime | None`、`ApiSourceHealth.alerted: str`（default `'none'`）——后续所有任务的读写依赖。

- [ ] **Step 1: 扩展模型**

`app/models/health.py` 全文替换为：

```python
from datetime import datetime

from sqlmodel import Field

from app.models._base import TimestampMixin


class ApiSourceHealth(TimestampMixin, table=True):
    __tablename__ = 'api_source_health'
    source: str = Field(primary_key=True, max_length=16)
    last_success_at: datetime | None = None
    status: str = Field(default='unknown', max_length=16)  # ok | degraded | down | unknown
    error: str | None = None
    # plan-11（2026-09-15 DNS 事故）：告警状态机支撑列。时间均 naive UTC（CLAUDE.md 纪律）。
    # down_since：本次故障起点；恢复通知送达后清除（保留期间供计算故障时长）。
    down_since: datetime | None = None
    # alerted：none（未告警）→ alerted（故障告警已送达）→ recovering（恢复通知待送达）。
    alerted: str = Field(default='none', max_length=16)
```

- [ ] **Step 2: 写失败迁移测试（镜像 tests/test_migration_fix_prize_amount.py 范式）**

`tests/test_migration_t11_source_health.py`：

```python
"""t11_source_health_alert 迁移测试：api_source_health +down_since/alerted。"""

import os
import subprocess
import sys
from pathlib import Path

from cryptography.fernet import Fernet
from sqlalchemy import create_engine, inspect

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _env_for(db_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    env['JWT_SECRET'] = 'x' * 32
    env['CRYPTO_KEY_V1'] = Fernet.generate_key().decode()
    env['DATABASE_URL'] = f'sqlite:///{db_path}'
    return env


def _alembic_upgrade(db_path: Path, target: str = 'head') -> None:
    r = subprocess.run(
        [sys.executable, '-m', 'alembic', 'upgrade', target],
        cwd=PROJECT_ROOT, env=_env_for(db_path), capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(f'alembic upgrade {target} 失败:\n{r.stdout}\n{r.stderr}')


def test_migration_adds_alert_columns(tmp_path):
    db = tmp_path / 'mig.db'
    _alembic_upgrade(db)
    cols = {c['name']: c for c in inspect(create_engine(f'sqlite:///{db}'))
            .get_columns('api_source_health')}
    assert 'down_since' in cols and cols['down_since']['nullable']
    assert 'alerted' in cols
    assert cols['alerted'].get('default') is None or cols['alerted'].get('default') == 'none'
```

- [ ] **Step 3: 跑测试确认失败**

Run: `uv run pytest tests/test_migration_t11_source_health.py -q`
Expected: FAIL（alembic upgrade 到旧 head 成功，但 api_source_health 无 down_since/alerted 列 → AssertionError）。

- [ ] **Step 4: 写迁移**

`alembic/versions/t11_source_health_alert.py`（当前 head 是 `fix_prize_amount_cents`）：

```python
"""t11: api_source_health +down_since/alerted（plan-11 数据源健康告警）。

Revision ID: t11_source_health_alert
Revises: fix_prize_amount_cents
Create Date: 2026-09-15 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 't11_source_health_alert'
down_revision: str | Sequence[str] | None = 'fix_prize_amount_cents'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema: api_source_health 增告警状态机两列。"""
    with op.batch_alter_table('api_source_health', schema=None) as batch_op:
        batch_op.add_column(sa.Column('down_since', sa.DateTime(), nullable=True))
        batch_op.add_column(
            sa.Column('alerted', sa.String(length=16), nullable=False, server_default='none')
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('api_source_health', schema=None) as batch_op:
        batch_op.drop_column('alerted')
        batch_op.drop_column('down_since')
```

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run pytest tests/test_migration_t11_source_health.py -q`
Expected: 1 passed。

- [ ] **Step 6: 模型改动不破坏既有套件**

Run: `uv run pytest tests/api/test_admin.py -q`
Expected: 全部 PASS（test_admin_system_health 仍绿）。

- [ ] **Step 7: Commit（含迁移测试）**

```bash
git add app/models/health.py alembic/versions/t11_source_health_alert.py tests/test_migration_t11_source_health.py
git commit -m "feat(plan-11): ApiSourceHealth +down_since/alerted 告警状态机列（模型+迁移+迁移测试）"
```

---

### Task 2: build_admin_alert 共享模块

**Files:**
- Create: `app/notifications/admin_alert.py`
- Modify: `app/api/auth.py:220`（调用点）、`app/api/auth.py:230-245`（删除本地定义）
- Test: `tests/notifications/test_admin_alert.py`

**Interfaces:**
- Produces: `build_admin_alert() -> Callable[[str, str], None] | None`（title, body → Bark；`ADMIN_BARK_KEY` 未配返回 None）——Task 5 的 tick/backfill 接线依赖此签名。

- [ ] **Step 1: 写失败测试**

`tests/notifications/test_admin_alert.py`：

```python
"""build_admin_alert 共享模块测试（plan-11：从 auth.py 迁出供 scheduler 复用）。"""

from unittest.mock import MagicMock, patch

from app.config import reset_settings_cache


def test_build_admin_alert_none_without_key(monkeypatch):
    """ADMIN_BARK_KEY 未配 → 返回 None（调用方据此只写日志不发）。"""
    reset_settings_cache()
    monkeypatch.delenv('ADMIN_BARK_KEY', raising=False)
    monkeypatch.setenv('JWT_SECRET', 'x' * 32)
    monkeypatch.setenv('CRYPTO_KEY_V1', 'x' * 44)
    from app.notifications.admin_alert import build_admin_alert

    assert build_admin_alert() is None


def test_build_admin_alert_sends_bark_with_key(monkeypatch):
    """配 key → 返回 callable，调用时经 BarkChannel 发送 title/body。"""
    reset_settings_cache()
    monkeypatch.setenv('JWT_SECRET', 'x' * 32)
    monkeypatch.setenv('CRYPTO_KEY_V1', 'x' * 44)
    monkeypatch.setenv('ADMIN_BARK_KEY', 'test-key')
    from app.notifications import admin_alert as mod

    bark = MagicMock()
    with patch.object(mod, 'BarkChannel', return_value=bark):
        alert = mod.build_admin_alert()
        alert('标题', '正文')
    bark.send.assert_called_once()
    payload = bark.send.call_args.args[0]
    assert payload.title == '标题' and payload.body == '正文'
```

（若 conftest 已有 settings 环境注入 fixture，优先复用其模式；`CRYPTO_KEY_V1` 必须是合法 Fernet key 时改用 `Fernet.generate_key().decode()`——以本仓库其他测试（如 tests/api/test_health.py）的写法为准。）

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/notifications/test_admin_alert.py -q`
Expected: FAIL（`ModuleNotFoundError: app.notifications.admin_alert`）。

- [ ] **Step 3: 迁移实现**

`app/notifications/admin_alert.py`：

```python
"""admin Bark 告警构造（plan-11：自 app/api/auth.py 迁出，auth 与 scheduler 共用）。

运维兜底通道：不走 Notifier/用户渠道体系（无 NotificationLog、无 DND）——
admin 告警的价值在「系统级故障时也能叫到人」，必须绕开业务通知管线。
"""

from collections.abc import Callable

from app.notifications.bark import BarkChannel
from app.notifications.base import NotificationPayload


def build_admin_alert() -> Callable[[str, str], None] | None:
    """复用 ADMIN_BARK_KEY 构造告警函数；未配 key → None（调用方降级为只记日志）。"""
    from app.config import get_settings

    key = get_settings().admin_bark_key
    if not key:
        return None
    bark = BarkChannel()
    config = {'key': key, 'url': 'https://api.day.app'}

    def _alert(title: str, body: str) -> None:
        bark.send(NotificationPayload(title=title, body=body), config)

    return _alert
```

`app/api/auth.py`：删除 `_build_admin_alert` 定义（230-245 行），文件头部 import 区加
`from app.notifications.admin_alert import build_admin_alert`，调用点 220 行改为
`admin_alert = build_admin_alert()`。

注意：`BarkChannel`/`NotificationPayload` 若在 auth.py 已无其他使用，同步移除其 import（lint-imports 会查）。

- [ ] **Step 4: 新测试 + auth 回归**

Run: `uv run pytest tests/notifications/test_admin_alert.py tests/api/ -q`
Expected: 全部 PASS（auth 行为不变）。

- [ ] **Step 5: Commit**

```bash
git add app/notifications/admin_alert.py app/api/auth.py tests/notifications/test_admin_alert.py
git commit -m "refactor(plan-11): build_admin_alert 迁至 notifications 共享模块（auth 行为不变）"
```

---

### Task 3: source_health 写入 + FetchService 三态接线

**Files:**
- Create: `app/services/source_health.py`（本任务只写 `record_source_health` 部分）
- Modify: `app/services/fetch_service.py:111-121`（`_try_fetch` 三态）、`fetch_and_store` 头部、`_grace_refetch` 内 `_try_fetch` 解包处（约 195 行）
- Test: `tests/services/test_source_health.py`

**Interfaces:**
- Produces: `record_source_health(engine: Engine, source: str, outcome: str, error: str | None) -> None`，`outcome ∈ {'ok','down','permanent'}`——Task 4/5 依赖；`FetchService._try_fetch` 返回 `(DrawNumbers | None, str, str | None)`。

- [ ] **Step 1: 写失败测试**

`tests/services/test_source_health.py`：

```python
"""数据源健康落表语义（plan-11 spec §1.2）。时间均 naive UTC。"""

from datetime import datetime, timedelta

from sqlmodel import Session

from app.models import ApiSourceHealth
from app.services.source_health import now_naive_utc, record_source_health


def _get(engine, source='mxnzp') -> ApiSourceHealth:
    with Session(engine) as s:
        return s.get(ApiSourceHealth, source)


def test_record_ok_sets_last_success_and_clears_down(db_engine):
    """ok → status=ok、last_success_at 刷新、down_since 清空、error 清空。"""
    t0 = datetime(2026, 9, 15, 4, 0, 0)
    record_source_health(db_engine, 'mxnzp', 'down', 'boom', now=lambda: t0)
    record_source_health(db_engine, 'mxnzp', 'ok', now=lambda: t0 + timedelta(minutes=5))
    h = _get(db_engine)
    assert h.status == 'ok'
    assert h.last_success_at == t0 + timedelta(minutes=5)
    assert h.down_since is None and h.error is None


def test_record_down_keeps_first_down_since(db_engine):
    """连续 down：down_since 记首次失败时间，不刷新（故障起点语义）。"""
    t0 = datetime(2026, 9, 15, 4, 0, 0)
    record_source_health(db_engine, 'mxnzp', 'down', 'e1', now=lambda: t0)
    record_source_health(db_engine, 'mxnzp', 'down', 'e2', now=lambda: t0 + timedelta(hours=1))
    h = _get(db_engine)
    assert h.status == 'down' and h.down_since == t0 and h.error == 'e2'


def test_record_permanent_only_writes_error(db_engine):
    """permanent（未配 key 等）只记 error，status/down_since/alerted 全不动。"""
    t0 = datetime(2026, 9, 15, 4, 0, 0)
    record_source_health(db_engine, 'juhe', 'ok', now=lambda: t0)
    record_source_health(db_engine, 'juhe', 'permanent', 'juhe api_key not configured')
    h = _get(db_engine, 'juhe')
    assert h.status == 'ok' and h.down_since is None and h.alerted == 'none'
    assert h.error == 'juhe api_key not configured'


def test_record_ok_on_alerted_transitions_recovering_keeps_down_since(db_engine):
    """已告警（alerted）的源恢复 → recovering 且保留 down_since（供恢复通知算时长）。"""
    t0 = datetime(2026, 9, 15, 4, 0, 0)
    record_source_health(db_engine, 'mxnzp', 'down', 'e', now=lambda: t0)
    with Session(db_engine) as s:
        s.get(ApiSourceHealth, 'mxnzp').alerted = 'alerted'
        s.commit()
    record_source_health(db_engine, 'mxnzp', 'ok', now=lambda: t0 + timedelta(hours=2))
    h = _get(db_engine)
    assert h.alerted == 'recovering'
    assert h.down_since == t0  # 保留，评估侧送达恢复通知后清除


def test_record_down_cancels_pending_recovery(db_engine):
    """抖动：recovering 期间再次失败 → 回 alerted（取消待发的过时恢复通知）。"""
    t0 = datetime(2026, 9, 15, 4, 0, 0)
    with Session(db_engine) as s:
        s.add(ApiSourceHealth(source='mxnzp', status='ok', alerted='recovering',
                              down_since=t0))
        s.commit()
    record_source_health(db_engine, 'mxnzp', 'down', 'again', now=lambda: t0 + timedelta(minutes=1))
    h = _get(db_engine)
    assert h.alerted == 'alerted' and h.status == 'down'
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/services/test_source_health.py -q`
Expected: FAIL（`ModuleNotFoundError: app.services.source_health`）。

- [ ] **Step 3: 实现 record_source_health**

`app/services/source_health.py`：

```python
"""数据源健康记录与告警评估（plan-11 / 2026-09-15 DNS 事故跟进）。

ApiSourceHealth 长期「有读无写」（admin 面板空表）——本模块补写路径：fetch 按
源记录 ok/down；评估器按「down 持续 ≥30 分钟」发 admin Bark（送达才转移状态。
DNS 教训：故障期告警通道大概率同挂，未送达必须下轮重试）。

时间纪律（CLAUDE.md）：down_since/last_success_at 均 naive UTC，与
TimestampMixin.created_at 同表示，DB 内不做 naive/aware 混比。
"""

import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from app.models import ApiSourceHealth

logger = logging.getLogger(__name__)

# 「连续 2 个 tick 全失败」的时间窗实现（spec §1.3）：与 tick 次数解耦——
# 持久、不怕容器重启（2026-09-15 事故中容器恰在故障期重启，内存计数会清零）。
DOWN_ALERT_AFTER = timedelta(minutes=30)

# now 注入点：生产用默认；测试注入固定时钟，避免真实 sleep/时间竞争。
_NowFn = Callable[[], datetime]


def now_naive_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def record_source_health(
    engine: Engine,
    source: str,
    outcome: str,
    error: str | None = None,
    now: _NowFn = now_naive_utc,
) -> None:
    """按源 upsert 健康表（spec §1.2 语义表）。outcome: 'ok' | 'down' | 'permanent'。

    permanent（key 未配置等配置态）：仅记 error——单源部署下未配置的备源若计入
    down 会永久故障且天天告警（juhe 不可用是长期事实，非运行故障）。
    """
    t = now()
    with Session(engine) as s:
        h = s.get(ApiSourceHealth, source)
        if h is None:
            h = ApiSourceHealth(source=source)
            s.add(h)
        if outcome == 'ok':
            h.status = 'ok'
            h.last_success_at = t
            h.error = None
            if h.alerted == 'none':
                h.down_since = None
            else:
                # 已告警过 → 待恢复通知；保留 down_since 供评估侧算故障时长。
                h.alerted = 'recovering'
        elif outcome == 'down':
            h.status = 'down'
            if h.down_since is None:
                h.down_since = t
            if h.alerted == 'recovering':
                h.alerted = 'alerted'  # 抖动：取消待发的过时恢复通知
            h.error = error
        elif outcome == 'permanent':
            h.error = error
        else:
            raise ValueError(f'unknown outcome: {outcome}')
        s.commit()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/services/test_source_health.py -q`
Expected: 5 passed。

- [ ] **Step 5: FetchService 三态接线（回归保护既有语义）**

`app/services/fetch_service.py`：

改 `_try_fetch`（原 111-116 行）：

```python
    def _try_fetch(
        self, source: DrawSource, lottery_code: str
    ) -> tuple[DrawNumbers | None, str, str | None]:
        """返回 (numbers, outcome, error)。outcome: 'ok' | 'down' | 'permanent'。

        ok+None=未开奖（源健康）；down=运行故障（网络/限流重试耗尽）；
        permanent=配置态错误（key 未配置等）——健康表据此区分（plan-11 spec §1.2）。
        """
        try:
            return self._fetch_with_backoff(source, lottery_code), 'ok', None
        except Exception as exc:
            if isinstance(exc, PermanentLookupError):
                return None, 'permanent', str(exc)
            return None, 'down', str(exc)
```

改 `fetch_and_store` 头部（原 118-121 行）：

```python
    def fetch_and_store(self, lottery_code: str) -> FetchResult:
        primary, p_outcome, p_err = self._try_fetch(self._primary, lottery_code)
        backup, b_outcome, b_err = self._try_fetch(self._backup, lottery_code)
        # 数据源健康落表（plan-11）：写失败不得阻断抓取（spec §1.2 独立短事务）。
        self._record_health(self._primary.name, p_outcome, p_err)
        self._record_health(self._backup.name, b_outcome, b_err)
        p_ok = p_outcome == 'ok'
        b_ok = b_outcome == 'ok'
```

（后续 `if not p_ok and not b_ok:` 等分支逻辑不动——布尔语义与旧版一致。）

`_grace_refetch` 内（约 195 行）原 `m2, m2_ok = self._try_fetch(missing_source, lottery_code)` 改为：

```python
        m2, m2_outcome, _m2_err = self._try_fetch(missing_source, lottery_code)
        m2_ok = m2_outcome == 'ok'
```

`FetchService` 类内新增方法（放在 `_try_fetch` 之后）：

```python
    def _record_health(self, source_name, outcome: str, error: str | None) -> None:
        """写 ApiSourceHealth（plan-11）。独立短事务 + 吞异常：健康落表失败只记日志，
        绝不阻断抓取主流程（spec §1.2）。"""
        try:
            from app.services.source_health import record_source_health

            record_source_health(self._engine, str(source_name), outcome, error)
        except Exception:
            logger.warning(
                'source_health_write_failed source=%s outcome=%s', source_name, outcome,
                exc_info=True,
            )
```

（函数内 import：避免 fetch_service ↔ source_health 潜在环；模块顶部 import 亦可，以 lint-imports 通过为准。）

- [ ] **Step 6: 既有 fetch 套件回归**

Run: `uv run pytest tests/services/test_fetch_service.py tests/adapters/ tests/integration/ -q`
Expected: 全部 PASS（健康写入对既有断言无感——独立表、独立事务）。

- [ ] **Step 7: Commit**

```bash
git add app/services/source_health.py app/services/fetch_service.py tests/services/test_source_health.py
git commit -m "feat(plan-11): fetch 按源三态落 ApiSourceHealth（ok/down/permanent，故障起点不刷新）"
```

---

### Task 4: evaluate_source_alerts 告警状态机

**Files:**
- Modify: `app/services/source_health.py`（追加评估器）
- Test: `tests/services/test_source_health.py`（追加）

**Interfaces:**
- Consumes: Task 3 的 `record_source_health` / `DOWN_ALERT_AFTER`。
- Produces: `evaluate_source_alerts(engine: Engine, send_alert: Callable[[str, str], None] | None, now: _NowFn = now_naive_utc) -> None`——Task 5 接线依赖。

- [ ] **Step 1: 写失败测试（追加到 test_source_health.py）**

```python
# ---------- evaluate_source_alerts 状态机（spec §1.3） ----------

import logging

from app.services.source_health import evaluate_source_alerts


class _Recorder:
    """记 send_alert 调用；可控抛异常模拟「告警通道也挂了」（DNS 教训）。"""

    def __init__(self, fail_first=0):
        self.calls = []
        self.fail_first = fail_first

    def __call__(self, title, body):
        if self.fail_first > 0:
            self.fail_first -= 1
            raise ConnectionError('bark down')
        self.calls.append((title, body))


def _seed_down(engine, down_since, alerted='none', status='down'):
    with Session(engine) as s:
        s.add(ApiSourceHealth(source='mxnzp', status=status, alerted=alerted,
                              down_since=down_since, last_success_at=None))
        s.commit()


def test_evaluate_no_alert_before_threshold(db_engine):
    """down 不足 30 分钟 → 不告警。"""
    t = datetime(2026, 9, 15, 13, 0, 0)
    _seed_down(db_engine, down_since=t - timedelta(minutes=29))
    rec = _Recorder()
    evaluate_source_alerts(db_engine, rec, now=lambda: t)
    assert rec.calls == []
    assert _get(db_engine).alerted == 'none'


def test_evaluate_alerts_once_after_threshold(db_engine):
    """down ≥30 分钟 → 告警一次并置 alerted；继续 down 不重复告警。"""
    t = datetime(2026, 9, 15, 13, 0, 0)
    _seed_down(db_engine, down_since=t - timedelta(minutes=31))
    rec = _Recorder()
    evaluate_source_alerts(db_engine, rec, now=lambda: t)
    evaluate_source_alerts(db_engine, rec, now=lambda: t + timedelta(minutes=15))
    assert len(rec.calls) == 1 and '持续失败' in rec.calls[0][0]
    assert _get(db_engine).alerted == 'alerted'


def test_evaluate_send_failure_keeps_state_and_retries(db_engine):
    """发送异常 → 状态保持 none，下轮重试；送达成功才转移（DNS 教训回归）。"""
    t = datetime(2026, 9, 15, 13, 0, 0)
    _seed_down(db_engine, down_since=t - timedelta(minutes=40))
    rec = _Recorder(fail_first=1)
    evaluate_source_alerts(db_engine, rec, now=lambda: t)
    assert rec.calls == [] and _get(db_engine).alerted == 'none'
    evaluate_source_alerts(db_engine, rec, now=lambda: t + timedelta(minutes=15))
    assert len(rec.calls) == 1 and _get(db_engine).alerted == 'alerted'


def test_recovery_notice_sent_then_reset(db_engine):
    """alerted → 抓取恢复（写入侧置 recovering）→ 评估送出恢复通知 → none+清 down_since。"""
    t = datetime(2026, 9, 15, 13, 0, 0)
    _seed_down(db_engine, down_since=t - timedelta(hours=2), alerted='alerted')
    record_source_health(db_engine, 'mxnzp', 'ok', now=lambda: t)  # → recovering
    rec = _Recorder()
    evaluate_source_alerts(db_engine, rec, now=lambda: t)
    assert len(rec.calls) == 1 and '恢复' in rec.calls[0][0]
    h = _get(db_engine)
    assert h.alerted == 'none' and h.down_since is None


def test_recovery_send_failure_retries(db_engine):
    """恢复通知发送失败 → 保持 recovering，下轮送达才回 none。"""
    t = datetime(2026, 9, 15, 13, 0, 0)
    _seed_down(db_engine, down_since=t - timedelta(hours=2), alerted='alerted')
    record_source_health(db_engine, 'mxnzp', 'ok', now=lambda: t)
    rec = _Recorder(fail_first=1)
    evaluate_source_alerts(db_engine, rec, now=lambda: t)
    assert _get(db_engine).alerted == 'recovering'
    evaluate_source_alerts(db_engine, rec, now=lambda: t + timedelta(minutes=15))
    assert _get(db_engine).alerted == 'none'


def test_no_sender_still_transitions(db_engine):
    """ADMIN_BARK_KEY 未配（send_alert=None）→ 不发送但状态照常流转（表可见 down）。"""
    t = datetime(2026, 9, 15, 13, 0, 0)
    _seed_down(db_engine, down_since=t - timedelta(minutes=40))
    evaluate_source_alerts(db_engine, None, now=lambda: t)
    assert _get(db_engine).alerted == 'alerted'
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/services/test_source_health.py -q -k evaluate`
Expected: FAIL（ImportError: evaluate_source_alerts）。

- [ ] **Step 3: 实现评估器（追加到 source_health.py）**

```python
def evaluate_source_alerts(
    engine: Engine,
    send_alert: Callable[[str, str], None] | None,
    now: _NowFn = now_naive_utc,
) -> None:
    """评估健康表驱动告警状态机（spec §1.3；挂载于 path_a tick 尾 + 启动 backfill 尾）。

    send_alert=None（ADMIN_BARK_KEY 未配）→ 只做状态转移不发送，admin 面板仍可见。
    任何发送异常保持原状态（continue 不 commit 该行变更），下轮重试直到送达——
    2026-09-15 DNS 事故教训：故障期告警通道大概率同时挂。
    """
    t = now()
    with Session(engine) as s:
        for h in s.exec(select(ApiSourceHealth)).all():
            if (
                h.status == 'down'
                and h.alerted == 'none'
                and h.down_since is not None
                and t - h.down_since >= DOWN_ALERT_AFTER
            ):
                minutes = int((t - h.down_since).total_seconds() // 60)
                if send_alert is not None:
                    try:
                        send_alert(
                            '开奖抓取持续失败',
                            f'数据源 {h.source} 已持续失败约 {minutes} 分钟'
                            f'（自 {h.down_since} 起），最近错误：{(h.error or "")[:200]}',
                        )
                    except Exception:
                        logger.warning(
                            'source_alert_send_failed source=%s', h.source, exc_info=True
                        )
                        continue  # 未送达不转移，下轮重试
                h.alerted = 'alerted'
            elif h.alerted == 'recovering' and h.status == 'ok':
                duration = t - h.down_since if h.down_since else timedelta(0)
                minutes = int(duration.total_seconds() // 60)
                if send_alert is not None:
                    try:
                        send_alert(
                            '开奖抓取已恢复',
                            f'数据源 {h.source} 已恢复抓取（故障持续约 {minutes} 分钟）',
                        )
                    except Exception:
                        logger.warning(
                            'source_recovery_send_failed source=%s', h.source, exc_info=True
                        )
                        continue
                h.alerted = 'none'
                h.down_since = None
        s.commit()
```

（注意：`continue` 跳过的是该行的状态转移；Session 内其他行继续评估。）

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/services/test_source_health.py -q`
Expected: 11 passed。

- [ ] **Step 5: Commit**

```bash
git add app/services/source_health.py tests/services/test_source_health.py
git commit -m "feat(plan-11): evaluate_source_alerts 告警状态机（down≥30min 告警、恢复通知、送达才转移）"
```

---

### Task 5: 调度接线 + 启动回填 QPS 间隔

**Files:**
- Modify: `app/scheduler/jobs.py`（`_path_a_tick` 末尾 + import）
- Modify: `app/scheduler/backfill.py`（第 4 步间隔 + 函数尾评估 + 常量）
- Modify: `tests/conftest.py:14-26`（autouse fixture 补 backfill 常量置 0）
- Test: `tests/scheduler/test_jobs.py`（追加）、`tests/scheduler/test_backfill.py`（追加）

**Interfaces:**
- Consumes: Task 4 `evaluate_source_alerts`、Task 2 `build_admin_alert`。
- Produces: `_path_a_tick` 与 `run_startup_backfill` 尾部各调用一次评估；`backfill._INTER_LOTTERY_INTERVAL = 1.2`。

- [ ] **Step 1: 写失败测试（test_jobs.py 追加，用仓库既有 `_invoke_job` 辅助——见文件内其他 tick 测试）**

```python
def test_path_a_tick_evaluates_source_alerts(db_engine, monkeypatch):
    """path_a_tick 尾部必须评估数据源健康告警（plan-11：tick 即评估点）。"""
    from unittest.mock import MagicMock

    import app.scheduler.jobs as jobs_mod
    from app.scheduler.setup import build_scheduler

    spy = MagicMock()
    monkeypatch.setattr(jobs_mod, 'evaluate_source_alerts', spy)
    sched = build_scheduler(db_engine)
    register_all_jobs(
        sched,
        {
            'engine': db_engine,
            'fetch_service': MagicMock(),
            'compare_service': MagicMock(),
            'refill_worker': MagicMock(),
            'notifier': MagicMock(),
        },
    )
    _invoke_job(sched, 'path_a_poll_evening')
    assert spy.call_count == 1
    # engine 为第一参数，sender 来自 build_admin_alert（测试无 key → None）
    assert spy.call_args.args[0] is db_engine
```

- [ ] **Step 2: 写失败测试（test_backfill.py 追加）**

```python
def test_startup_backfill_paces_fetches_with_interval(db_engine, monkeypatch):
    """启动回填对实际抓取的彩种加 QPS 间隔：第 2 个起每次 fetch 前 sleep（plan-11）。"""
    import app.scheduler.backfill as backfill_mod
    from app.models import LotteryType

    monkeypatch.setattr(backfill_mod, '_INTER_LOTTERY_INTERVAL', 1.2)
    sleeps = []
    monkeypatch.setattr(backfill_mod.time, 'sleep', lambda s: sleeps.append(s))

    # 3 个彩种全部 missed（DB 无开奖 + draw_days 覆盖回看窗口）
    monkeypatch.setattr(
        backfill_mod, '_enabled_lotteries',
        lambda engine: [('a', [0, 1, 2, 3, 4, 5, 6]), ('b', [0, 1, 2, 3, 4, 5, 6]),
                        ('c', [0, 1, 2, 3, 4, 5, 6])],
    )
    monkeypatch.setattr(backfill_mod, '_has_draw_for_date', lambda engine, code, d: False)

    deps = _make_deps(db_engine)
    run_startup_backfill(deps)
    assert deps['fetch_service'].fetch_and_store.call_count == 3
    assert sleeps == [1.2, 1.2]  # 首个抓取不 sleep


def test_startup_backfill_evaluates_source_alerts(db_engine, monkeypatch):
    """启动 backfill 尾部评估健康告警（plan-11：开机即评估）。"""
    import app.scheduler.backfill as backfill_mod

    spy = MagicMock()
    monkeypatch.setattr(backfill_mod, 'evaluate_source_alerts', spy)
    run_startup_backfill(_make_deps(db_engine))
    assert spy.call_count == 1
```

- [ ] **Step 3: 跑两个新测试确认失败**

Run: `uv run pytest tests/scheduler/test_jobs.py::test_path_a_tick_evaluates_source_alerts tests/scheduler/test_backfill.py::test_startup_backfill_paces_fetches_with_interval tests/scheduler/test_backfill.py::test_startup_backfill_evaluates_source_alerts -q`
Expected: 3 FAIL（AttributeError: evaluate_source_alerts / _INTER_LOTTERY_INTERVAL）。

- [ ] **Step 4: 实现 jobs.py 接线**

`app/scheduler/jobs.py` import 区加：

```python
from app.notifications.admin_alert import build_admin_alert
from app.services.source_health import evaluate_source_alerts
```

`_path_a_tick` 函数末尾（`sched.add_job(_push_big_win, ...)` 循环之后、函数体结束前）追加：

```python
    # 数据源健康评估（plan-11）：tick 尾部评估告警状态机（down≥30min → admin bark；
    # 未配 ADMIN_BARK_KEY 则 sender=None 只转移状态）。评估失败不阻断本 tick 收尾。
    try:
        evaluate_source_alerts(engine, build_admin_alert())
    except Exception:
        logger.error('source_alert_evaluate_failed', exc_info=True)
```

- [ ] **Step 5: 实现 backfill.py 间隔 + 尾部评估**

`app/scheduler/backfill.py`：模块级（`_BACKFILL_LOOKBACK_DAYS = 2` 旁）加：

```python
# 彩种间抓取间隔（秒）。与 jobs._INTER_LOTTERY_INTERVAL 同源同值（MXNZP 免费 1 QPS，
# 连续请求触发 code=101 白耗重试）；本地定义避免 backfill↔jobs 循环 import。
# 测试经 conftest autouse fixture 置 0。
_INTER_LOTTERY_INTERVAL = 1.2
```

import 区加 `import time`（若未有）与 `from app.notifications.admin_alert import build_admin_alert`、`from app.services.source_health import evaluate_source_alerts`。

第 4 步循环（`for code, draw_days in _enabled_lotteries(engine):`）改为：

```python
    fetched = 0
    for code, draw_days in _enabled_lotteries(engine):
        try:
            missed = any(d.weekday() in draw_days and not _has_draw_for_date(engine, code, d) for d in lookback_days)
            if missed:
                # QPS 间隔只加在真实请求之间（missed 检查跳过的彩种不白等）；
                # 首个抓取不等待（plan-11，镜像 jobs._path_a_tick 的 L-20260726 语义）。
                if fetched > 0:
                    time.sleep(_INTER_LOTTERY_INTERVAL)
                fetch_service.fetch_and_store(code)
                fetched += 1
        except Exception:
            # 单彩种源故障不得阻断其他彩种（silent-failure 纪律）。
            logger.error('startup_backfill_fetch_failed code=%s', code, exc_info=True)

    # 数据源健康评估（plan-11）：开机即评估一次（覆盖白天故障/停机后恢复场景）。
    try:
        evaluate_source_alerts(engine, build_admin_alert())
    except Exception:
        logger.error('source_alert_evaluate_failed', exc_info=True)
```

（原循环体的 try/except 与 missed 判断保持原样，仅包入 fetched 计数与 sleep。）

- [ ] **Step 6: conftest 补置 0**

`tests/conftest.py` 的 `_disable_inter_lottery_interval` fixture 内追加：

```python
    from app.scheduler import backfill as backfill_mod

    monkeypatch.setattr(backfill_mod, '_INTER_LOTTERY_INTERVAL', 0)
```

（fixture docstring 同步提及 backfill。）

- [ ] **Step 7: 新测试通过 + 调度器套件回归**

Run: `uv run pytest tests/scheduler/ -q`
Expected: 全部 PASS（含既有 `test_path_a_tick_paces_mxnzp_qps_with_inter_lottery_interval` 与全部 backfill 测试）。

- [ ] **Step 8: Commit**

```bash
git add app/scheduler/jobs.py app/scheduler/backfill.py tests/conftest.py tests/scheduler/test_jobs.py tests/scheduler/test_backfill.py
git commit -m "feat(plan-11): tick/backfill 尾部评估源告警；启动回填对实际抓取加 QPS 间隔"
```

---

### Task 6: /admin/health 扩展 + Admin.vue 展示

**Files:**
- Modify: `app/api/admin.py:79-82`（system_health 响应）
- Modify: `web/src/pages/Admin.vue:19-22`（HealthSource）、`web/src/pages/Admin.vue:644-648`（模板）
- Test: `tests/api/test_admin.py:128-138`（扩展既有断言）

**Interfaces:**
- Consumes: Task 1 的 `down_since` 列。
- Produces: `/admin/health` 每源多返回 `last_success_at` / `down_since`（ISO 字符串或 null）。

- [ ] **Step 1: 扩展既有测试（RED）**

`tests/api/test_admin.py` 的 `test_admin_system_health` 改为：

```python
def test_admin_system_health(db_engine, monkeypatch):
    with Session(db_engine) as s:
        s.add(ApiSourceHealth(source='mxnzp', status='ok'))
        s.add(ApiSourceHealth(source='juhe', status='degraded'))
        s.commit()
    client = _admin_client(db_engine, monkeypatch)
    r = client.get('/admin/health')
    assert r.status_code == 200
    data = r.json()
    assert len(data['sources']) == 2
    assert {s['source'] for s in data['sources']} == {'mxnzp', 'juhe'}
    # plan-11：每源返回 last_success_at/down_since（null 安全——空表行也要有键）
    assert all({'last_success_at', 'down_since'} <= set(s) for s in data['sources'])
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/api/test_admin.py::test_admin_system_health -q`
Expected: FAIL（响应无 last_success_at 键）。

- [ ] **Step 3: 实现响应扩展**

`app/api/admin.py` system_health 改为：

```python
@router.get('/health')
def system_health(session: Session = Depends(get_session_dep)):
    sources = session.exec(select(ApiSourceHealth)).all()
    return {
        'sources': [
            {
                'source': s.source,
                'status': s.status,
                'last_success_at': s.last_success_at.isoformat() if s.last_success_at else None,
                'down_since': s.down_since.isoformat() if s.down_since else None,
            }
            for s in sources
        ]
    }
```

- [ ] **Step 4: Admin.vue 展示**

`HealthSource` 接口改为：

```typescript
interface HealthSource {
  source: string;
  status: string;
  last_success_at: string | null;
  down_since: string | null;
}
```

模板（644-648 行）`source-item` 内追加一段（放在 status 之后）：

```html
            <div v-for="s in health" :key="s.source" class="source-item">
              <span class="source-name">{{ s.source }}</span>
              <span class="source-meta">
                {{ s.down_since ? `故障自 ${s.down_since.slice(0, 16).replace('T', ' ')}` : (s.last_success_at ? `最后成功 ${s.last_success_at.slice(0, 16).replace('T', ' ')}` : '—') }}
              </span>
              <span class="source-status" :class="s.status">{{ s.status }}</span>
            </div>
```

（`.source-meta` 样式：沿用卡内次要文字的既有 class；若无，在组件 style 尾部加
`.source-meta { color: var(--vt-c-text-2, #888); font-size: 0.8rem; }`，以文件内既有变量为准。）

- [ ] **Step 5: 前后端验证**

Run: `uv run pytest tests/api/test_admin.py -q && npm --prefix web run build && npm --prefix web run test`
Expected: pytest PASS；vue-tsc + vite build exit 0；vitest PASS（Admin.test.ts 默认 mock `{sources: []}` 不受影响）。

- [ ] **Step 6: Commit**

```bash
git add app/api/admin.py web/src/pages/Admin.vue tests/api/test_admin.py
git commit -m "feat(plan-11): /admin/health 返回 last_success_at/down_since，面板展示故障起点"
```

---

### Task 7: 全量回归收尾

**Files:** 无新改动（只验证；若前序遗漏由本任务兜底发现）。

- [ ] **Step 1: 后端全量**

Run: `uv run pytest -q`
Expected: 全部 PASS（基线 754+1 skipped 之上加本计划新增用例；无既有测试因健康落表/评估接线失败）。

- [ ] **Step 2: Lint**

Run: `uv run ruff check . && uv run lint-imports`
Expected: All checks passed / Contracts kept。

- [ ] **Step 3: 状态确认**

Run: `git status --short && git log --oneline -7`
Expected: 工作区干净（.mimosa/ 等本地产物除外）；6 个功能 commit。

---

## Self-Review 记录

- Spec 覆盖：§1.1→Task 1；§1.2→Task 3；§1.3→Task 4+5；§1.4→Task 2；§1.5→Task 6；§1.6（naive UTC）→ Global Constraints + 各 now 参数；§二→Task 5；§三→各任务测试步骤一一对应；§四→Task 1 迁移 + 部署提醒（执行完成后人工步骤，不属代码任务）。
- TDD 顺序：每任务均为「失败测试 → RED 确认 → 实现 → GREEN 确认」；Task 1 的迁移文件在 RED（Step 3）之后（Step 4）。
- 类型一致性：`record_source_health(engine, source, outcome, error, now)` 与 `evaluate_source_alerts(engine, send_alert, now)` 的签名在 Task 3/4/5 间一致；`_try_fetch` 三元组在 fetch_and_store 与 _grace_refetch 两处解包一致。
