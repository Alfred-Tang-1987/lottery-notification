## Implementation plan
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
    # eng-voice H2：inspect().get_columns() 返回的是 DDL 带引号字面量 "'none'"，
    # 不是 'none'——`== 'none'` 与 `is None` 两个分支都永假（SQLite 实测）。
    # 断言 quoted 子串 + NOT NULL + 缺省插入回读（真正防回归的三件套）。
    assert cols['alerted']['nullable'] is False
    assert "'none'" in str(cols['alerted'].get('default') or '')
    eng = create_engine(f'sqlite:///{db}')
    with eng.begin() as c:
        # 裸 SQL 必须显式给 created_at / status：二者 nullable=False 且**无 server_default**
        # （0001_initial.py:28-35 建表、_base.py:6 的 default_factory 只走 ORM）。
        # 只插 source 会撞 IntegrityError: NOT NULL constraint failed（实测）。
        # alerted 故意不写——本用例要验的正是它的 server_default='none'。
        c.execute(text(
            "INSERT INTO api_source_health (source, created_at, status) "
            "VALUES ('mxnzp', '2026-09-15 04:00:00.000000', 'unknown')"
        ))
        row = c.execute(text("SELECT alerted FROM api_source_health WHERE source='mxnzp'")).one()
    assert row[0] == 'none'


def test_alembic_single_head():
    """eng-voice C1 守卫：迁移链必须单 head——双 head 时 Dockerfile:79 的

    `alembic upgrade head` 报 'Multiple head revisions' 直接起不来容器。
    本 plan 原稿 down_revision 写成 fix_prize_amount_cents（真实 head 是
    d1_draw_costs）就会产生双 head；此测试让该类错误 RED 在 CI 而非 NAS。
    """
    r = subprocess.run(
        [sys.executable, '-m', 'alembic', 'heads'],
        cwd=PROJECT_ROOT, env=_env_for(Path(PROJECT_ROOT) / 'unused.db'),
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    heads = [ln for ln in r.stdout.splitlines() if ln.strip()]
    assert len(heads) == 1, f'存在多个 alembic head: {heads}'
```

（文件头部 import 区补 `from sqlalchemy import text`。）

- [ ] **Step 3: 跑测试确认失败**

Run: `uv run pytest tests/test_migration_t11_source_health.py -q`
Expected: FAIL（alembic upgrade 到旧 head 成功，但 api_source_health 无 down_since/alerted 列 → AssertionError）。

- [ ] **Step 4: 写迁移**

`alembic/versions/t11_source_health_alert.py`（当前 head 是 `d1_draw_costs`——
eng-voice C1：plan 原稿写 `fix_prize_amount_cents`，那是 2026-08 DrawCost 迁移
落地前的旧 head；down_revision 指错会产生**双 head**，Dockerfile:79 的
`alembic upgrade head` 报 'Multiple head revisions' → 容器启动即失败）：

```python
"""t11: api_source_health +down_since/alerted（plan-11 数据源健康告警）。

Revision ID: t11_source_health_alert
Revises: d1_draw_costs
Create Date: 2026-09-15 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 't11_source_health_alert'
down_revision: str | Sequence[str] | None = 'd1_draw_costs'
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
Expected: 2 passed（列断言 + 单 head 守卫）。

- [ ] **Step 6: 模型改动不破坏既有套件**

Run: `uv run pytest tests/api/test_admin.py -q`
Expected: 全部 PASS（test_admin_system_health 仍绿）。

- [ ] **Step 7: Commit（含迁移测试）**

```bash
git add app/models/health.py alembic/versions/t11_source_health_alert.py tests/test_migration_t11_source_health.py
git commit -m "feat(plan-11): ApiSourceHealth +down_since/alerted 告警状态机列（模型+迁移+迁移测试）"
```

---

### Task 2: settings 告警配置三字段 + build_admin_alert 共享模块

> 本任务含两段：先落 settings 三字段（Step 1-4，`build_admin_alert` 依赖
> `admin_bark_url`，故配置必须先于模块落地），再迁 `build_admin_alert`（Step 5-9）。
> 上一版 plan 把配置三字段放在 Task 5 Step 5.5，导致本任务实现 `settings.admin_bark_url`
> 时该字段尚不存在（AttributeError），已前移（2026-09-15 复核修订）。

**Files:**
- Modify: `app/config.py:62`（`admin_bark_key` 旁追加三字段）
- Modify: `app/main.py:145`（url 改读 settings，与告警同源）
- Create: `app/notifications/admin_alert.py`
- Modify: `app/api/auth.py:220`（调用点）、`app/api/auth.py:230-245`（删除本地定义）
- Test: `tests/test_config.py`、`tests/notifications/test_admin_alert.py`

**Interfaces:**
- Produces: `Settings.admin_bark_url: str`（默认 `'https://api.day.app'`）、
  `Settings.source_health_alerts_enabled: bool`（默认 `True`）、
  `Settings.source_health_alert_after_minutes: int`（默认 `30`）——Task 5 接线与
  Task 4 阈值依赖。
- Produces: `build_admin_alert() -> Callable[[str, str], None] | None`（title, body → Bark；`ADMIN_BARK_KEY` 未配返回 None）——Task 5 的 tick/backfill 接线依赖此签名。

- [ ] **Step 1: 写失败测试（config 三字段默认值）**

`tests/test_config.py` 追加：

```python
def test_source_health_alert_settings_defaults(monkeypatch):
    """plan-11 健康告警逃生舱默认值（F18/F19/F20）。"""
    monkeypatch.setenv('JWT_SECRET', 'x' * 32)
    from cryptography.fernet import Fernet
    monkeypatch.setenv('CRYPTO_KEY_V1', Fernet.generate_key().decode())
    from app.config import get_settings, reset_settings_cache
    reset_settings_cache()
    s = get_settings()
    assert s.source_health_alerts_enabled is True
    assert s.source_health_alert_after_minutes == 30
    assert s.admin_bark_url == 'https://api.day.app'
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_config.py::test_source_health_alert_settings_defaults -q`
Expected: FAIL（`AttributeError: 'Settings' object has no attribute 'source_health_alerts_enabled'`）。

- [ ] **Step 3: 实现三个 settings 字段（dx-voice F18/F19/F20 逃生舱）**

`app/config.py` `admin_bark_key`（第 62 行）旁追加：

```python
    admin_bark_key: str | None = None
    # Bark 服务端 URL（dx-voice F20：自建 Bark 是 NAS 真实场景）；默认官方域名，
    # build_admin_alert 与 main.py admin_bark_config 同源（单一真源，此前两处硬编码）。
    admin_bark_url: str = 'https://api.day.app'
    # 数据源健康告警独立开关（dx-voice F18：与密码重置告警解耦——运维被健康告警
    # 吵到时能只关它，不误伤密码重置 admin 通知）。
    source_health_alerts_enabled: bool = True
    # 告警阈值（分钟）（dx-voice F19：面向人的告警阈值必须可调，无需改代码重建镜像）。
    source_health_alert_after_minutes: int = 30
```

`app/main.py:145` `admin_bark_config = {'key': ..., 'url': 'https://api.day.app'}` 的
url 改为 `settings.admin_bark_url`（同源）。

- [ ] **Step 4: 跑测试确认通过 + 回归**

Run: `uv run pytest tests/test_config.py -q`
Expected: 全部 PASS（含新用例）。

- [ ] **Step 5: 写失败测试（build_admin_alert）**

`tests/notifications/test_admin_alert.py`：

```python
"""build_admin_alert 共享模块测试（plan-11：从 auth.py 迁出供 scheduler 复用）。"""

from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet

from app.config import reset_settings_cache


def test_build_admin_alert_none_without_key(monkeypatch):
    """ADMIN_BARK_KEY 未配 → 返回 None（调用方据此只写日志不发）。"""
    reset_settings_cache()
    monkeypatch.delenv('ADMIN_BARK_KEY', raising=False)
    monkeypatch.setenv('JWT_SECRET', 'x' * 32)
    # CRYPTO_KEY_V1 必须合法 Fernet key（autoplan M3：'x'*44 解码 33 字节非法；
    # 仓库范式见 tests/api/test_admin.py:16）。
    monkeypatch.setenv('CRYPTO_KEY_V1', Fernet.generate_key().decode())
    from app.notifications.admin_alert import build_admin_alert

    assert build_admin_alert() is None


def test_build_admin_alert_sends_bark_with_key(monkeypatch):
    """配 key → 返回 callable，调用时经 BarkChannel 发送 title/body。"""
    reset_settings_cache()
    monkeypatch.setenv('JWT_SECRET', 'x' * 32)
    monkeypatch.setenv('CRYPTO_KEY_V1', Fernet.generate_key().decode())
    monkeypatch.setenv('ADMIN_BARK_KEY', 'test-key')
    from app.notifications import admin_alert as mod
    from app.notifications.base import ChannelStatus, SendResult

    bark = MagicMock()
    bark.send.return_value = SendResult(ChannelStatus.SENT)
    with patch.object(mod, 'BarkChannel', return_value=bark):
        alert = mod.build_admin_alert()
        alert('标题', '正文')
    bark.send.assert_called_once()
    payload = bark.send.call_args.args[0]
    assert payload.title == '标题' and payload.body == '正文'


def test_build_admin_alert_raises_on_failed_send(monkeypatch):
    """bark 返回 FAILED（非抛异常）也必须 raise（autoplan M2 送达契约）——

    BarkChannel.send 吞掉一切失败返回 SendResult（bark.py:49-50），若忽略返回值，
    评估器「送达成功才转移」退化为「尝试即转移」，DNS 事故场景告警永不重试。
    """
    reset_settings_cache()
    monkeypatch.setenv('JWT_SECRET', 'x' * 32)
    monkeypatch.setenv('CRYPTO_KEY_V1', Fernet.generate_key().decode())
    monkeypatch.setenv('ADMIN_BARK_KEY', 'test-key')
    from app.notifications import admin_alert as mod
    from app.notifications.base import ChannelStatus, SendResult

    bark = MagicMock()
    bark.send.return_value = SendResult(ChannelStatus.FAILED, error='bark code 400')
    with patch.object(mod, 'BarkChannel', return_value=bark):
        alert = mod.build_admin_alert()
        with pytest.raises(RuntimeError):
            alert('标题', '正文')


def test_build_admin_alert_uses_settings_url(monkeypatch):
    """eng-voice M8.6：Bark config 的 url 必须来自 settings.admin_bark_url——

    只断言默认值存在管不住「退回硬编码 https://api.day.app」的回归（F20 同源初衷）。
    """
    reset_settings_cache()
    monkeypatch.setenv('JWT_SECRET', 'x' * 32)
    monkeypatch.setenv('CRYPTO_KEY_V1', Fernet.generate_key().decode())
    monkeypatch.setenv('ADMIN_BARK_KEY', 'test-key')
    monkeypatch.setenv('ADMIN_BARK_URL', 'https://bark.nas.local:8443')
    from app.notifications import admin_alert as mod
    from app.notifications.base import ChannelStatus, SendResult

    bark = MagicMock()
    bark.send.return_value = SendResult(ChannelStatus.SENT)
    with patch.object(mod, 'BarkChannel', return_value=bark):
        mod.build_admin_alert()('t', 'b')
    config = bark.send.call_args.args[1]
    assert config['url'] == 'https://bark.nas.local:8443'
```

（conftest 已有 `_reset_settings_and_env` autouse fixture 清环境；`CRYPTO_KEY_V1` 一律用 `Fernet.generate_key().decode()`（autoplan M3），与 tests/api/test_admin.py:16 一致。）

- [ ] **Step 6: 跑测试确认失败**

Run: `uv run pytest tests/notifications/test_admin_alert.py -q`
Expected: FAIL（`ModuleNotFoundError: app.notifications.admin_alert`）。

- [ ] **Step 7: 迁移实现（含 autoplan M2 送达契约）**

`app/notifications/admin_alert.py`：

```python
"""admin Bark 告警构造（plan-11：自 app/api/auth.py 迁出，auth 与 scheduler 共用）。

运维兜底通道：不走 Notifier/用户渠道体系（无 NotificationLog、无 DND）——
admin 告警的价值在「系统级故障时也能叫到人」，必须绕开业务通知管线。

送达契约（autoplan M2）：BarkChannel.send 吞掉一切失败返回 SendResult 而非抛异常
（bark.py:49-50）。若忽略返回值，调用方 try/except 永不触发，「送达成功才转移」
退化为「尝试即转移」——2026-09-15 DNS 事故（告警通道同挂）场景下告警永不重试。
故 status != SENT 时抛 RuntimeError，让评估器/调用方的 except 正确识别未送达。
"""

from collections.abc import Callable

from app.notifications.bark import BarkChannel
from app.notifications.base import ChannelStatus, NotificationPayload


def build_admin_alert() -> Callable[[str, str], None] | None:
    """复用 ADMIN_BARK_KEY 构造告警函数；未配 key → None（调用方降级为只记日志）。

    返回的 callable：送达成功正常返回；未送达（HTTP 错 / 业务码非 200 / 传输异常）
    抛 RuntimeError——调用方据此保持状态、下轮重试。
    """
    from app.config import get_settings

    settings = get_settings()
    if not settings.admin_bark_key:
        return None
    bark = BarkChannel()
    # url 走 settings（dx-voice F20：自建 Bark 服务端是 NAS 真实场景；
    # 与 main.py admin_bark_config 同源——单一默认真源）。
    config = {'key': settings.admin_bark_key, 'url': settings.admin_bark_url}

    def _alert(title: str, body: str) -> None:
        result = bark.send(NotificationPayload(title=title, body=body), config)
        if result.status != ChannelStatus.SENT:
            # dx-voice F8：error 可能为 None，别渲染成「未送达: None」。
            raise RuntimeError(f'admin bark 未送达: {result.error or "未知原因"}')

    return _alert
```

`app/api/auth.py`：删除 `_build_admin_alert` 定义（230-243 行），文件头部 import 区加
`from app.notifications.admin_alert import build_admin_alert`，调用点 220 行改为
`admin_alert = build_admin_alert()`。

注意：`BarkChannel`/`NotificationPayload` 在 auth.py 是 `_build_admin_alert` 内函数级 import，随定义一并删除即可（lint-imports 会查）。

M2 行为变化说明：password_reset_service.py:248-255 对 admin_alert 已有 try/except
（失败记 `password_reset_admin_alert_failed` 日志）——新版 raise 被该 except 兼容，
效果从「失败静默」变为「失败留痕」，是纯改进；既有测试（tests/services/
test_password_reset_service.py:163 用 stub callable）不受影响。

- [ ] **Step 8: 新测试 + auth 回归**

Run: `uv run pytest tests/notifications/test_admin_alert.py tests/api/ -q`
Expected: 全部 PASS（auth 行为不变）。

- [ ] **Step 9: Commit**

```bash
git add app/config.py app/main.py app/notifications/admin_alert.py app/api/auth.py tests/test_config.py tests/notifications/test_admin_alert.py
git commit -m "feat(plan-11): settings 告警三字段（开关/阈值/Bark URL）+ build_admin_alert 迁至 notifications 共享模块"
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


def test_record_permanent_sets_degraded(db_engine):
    """permanent（配置态）→ status=degraded、error 留痕、down_since 清零（dx-voice F11）。

    outcome→status 映射：ok→ok / down→down / permanent→degraded（F3 词表统一）。
    """
    t0 = datetime(2026, 9, 15, 4, 0, 0)
    record_source_health(db_engine, 'juhe', 'ok', now=lambda: t0)
    record_source_health(db_engine, 'juhe', 'permanent', 'juhe api_key not configured')
    h = _get(db_engine, 'juhe')
    assert h.status == 'degraded' and h.down_since is None and h.alerted == 'none'
    assert h.error == 'juhe api_key not configured'


def test_record_permanent_on_fresh_row_sets_degraded(db_engine):
    """全新行直接 permanent（部署首日 juhe 未配 key 的真实路径）→ degraded +
    error 留痕（design-voice D7：面板由此显示琥珀降级+原因，而非无解释的 unknown 灰砖）。"""
    record_source_health(db_engine, 'juhe', 'permanent', 'juhe api_key not configured')
    h = _get(db_engine, 'juhe')
    assert h.status == 'degraded' and h.down_since is None
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
    """抖动：recovering 期间再次失败 → 回 none + down_since 重置为新 episode（eng-voice M3a）。

    旧设计 recovering→down 翻回 alerted 是个谎言：M11 路径（告警从未送达）的行
    会显示「已通知」而无人收到。翻回 none 让评估器按**新故障 episode** 重新计时
    告警——恢复通知自然取消（发送要求 status=='ok'），重告警是否发由送达门控
    决定（duplicate > silence，silent-failure 纪律）。
    """
    t0 = datetime(2026, 9, 15, 4, 0, 0)
    with Session(db_engine) as s:
        s.add(ApiSourceHealth(source='mxnzp', status='ok', alerted='recovering',
                              down_since=t0))
        s.commit()
    record_source_health(db_engine, 'mxnzp', 'down', 'again', now=lambda: t0 + timedelta(minutes=1))
    h = _get(db_engine)
    assert h.alerted == 'none' and h.status == 'down'
    assert h.down_since == t0 + timedelta(minutes=1)  # 新 episode 起点


def test_record_permanent_clears_recovering(db_engine):
    """permanent（配置态）落到 recovering 行 → alerted 一并清 none（eng-voice M3b）。

    配置态转移终结本次运行故障 episode——不欠恢复通知；否则 degraded 行永远
    挂着「恢复待通知」（评估器恢复分支要求 status=='ok'，degraded 永不满足）。
    """
    t0 = datetime(2026, 9, 15, 4, 0, 0)
    with Session(db_engine) as s:
        s.add(ApiSourceHealth(source='juhe', status='ok', alerted='recovering',
                              down_since=t0))
        s.commit()
    record_source_health(db_engine, 'juhe', 'permanent', 'juhe api_key not configured',
                         now=lambda: t0 + timedelta(hours=1))
    h = _get(db_engine, 'juhe')
    assert h.status == 'degraded' and h.alerted == 'none' and h.down_since is None


def test_record_ok_after_long_outage_without_delivered_alert_goes_recovering(db_engine):
    """长故障期间告警从未送达（通道同挂，DNS 教训）→ 恢复时不得静默清零——

    置 recovering 补发恢复通知（autoplan M11：否则 9 天故障自愈 = 零通知）。
    """
    t0 = datetime(2026, 9, 1, 4, 0, 0)
    record_source_health(db_engine, 'mxnzp', 'down', 'e', now=lambda: t0)
    # 故障期每次评估尝试告警均失败（状态保持 none），第 9 天直接恢复
    record_source_health(db_engine, 'mxnzp', 'ok', now=lambda: t0 + timedelta(days=9))
    h = _get(db_engine)
    assert h.alerted == 'recovering' and h.down_since == t0


def test_record_ok_after_short_outage_clears_silently(db_engine):
    """短故障（<30min，从未达告警阈值）恢复 → 清 down_since，不留 recovering（M11 边界）。"""
    t0 = datetime(2026, 9, 15, 4, 0, 0)
    record_source_health(db_engine, 'mxnzp', 'down', 'e', now=lambda: t0)
    record_source_health(db_engine, 'mxnzp', 'ok', now=lambda: t0 + timedelta(minutes=10))
    h = _get(db_engine)
    assert h.alerted == 'none' and h.down_since is None


def test_record_error_redacts_secret_query_params(db_engine):
    """error 含 URL query 密钥（juhe key= 等）→ 落表前脱敏（autoplan M13）。

    juhe.py:27 把 api key 放 query，raise_for_status 异常消息含完整 URL；
    健康表 error 会进 admin 面板与 Bark 告警体（第三方服务器），密钥不得外泄。
    """
    record_source_health(
        db_engine, 'juhe', 'down',
        "Client error '403' for url 'https://v.juhe.cn/lottery/query?lottery_id=ssq&key=SECRETKEY123'",
    )
    h = _get(db_engine, 'juhe')
    assert 'SECRETKEY123' not in (h.error or '')
    assert 'key=[REDACTED]' in (h.error or '')


def test_threshold_falls_back_when_settings_unavailable(db_engine, caplog):
    """settings 读不到 → 阈值回退默认 30 分钟且不抛（复核修订 2026-09-15）。

    本套件跑在 conftest 的「无密钥环境」下（JWT_SECRET/CRYPTO_KEY_V1 被删，
    Settings 必填项缺失）——若 _down_alert_after() 直接抛 ValidationError，
    下面断言全不可达；生产中该异常还会被 _record_health 的 except 吞掉，
    健康表将静默停写（silent-failure 纪律不允许）。
    """
    import logging

    t0 = datetime(2026, 9, 15, 4, 0, 0)
    with caplog.at_level(logging.WARNING, logger='app.services.source_health'):
        record_source_health(db_engine, 'mxnzp', 'down', 'e', now=lambda: t0)
        # 10 分钟 < 回退阈值 30 分钟 → 短故障静默清除，不留 recovering
        record_source_health(db_engine, 'mxnzp', 'ok', now=lambda: t0 + timedelta(minutes=10))
    h = _get(db_engine)
    assert h.status == 'ok' and h.alerted == 'none' and h.down_since is None
    assert 'alert_threshold_settings_unavailable' in caplog.text
```


def test_fallback_threshold_matches_settings_default():
    """兜底常量必须与 settings 默认值一致（防漂移，复核修订 2026-09-15）。

    阈值真值源是 `settings.source_health_alert_after_minutes`（F19 逃生舱，可配可改）；
    `_DEFAULT_ALERT_AFTER_MINUTES` 只是「settings 读不到」时的兜底值。两处若漂移，
    故障场景下会静默使用错误阈值——用测试钉住。

    本用例只读类级字段（不实例化 Settings），故在无密钥环境下也成立。
    """
    from app.config import Settings

    from app.services.source_health import _DEFAULT_ALERT_AFTER_MINUTES

    assert _DEFAULT_ALERT_AFTER_MINUTES == (
        Settings.model_fields['source_health_alert_after_minutes'].default
    )
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/services/test_source_health.py -q`
Expected: FAIL（`ModuleNotFoundError: app.services.source_health`）。

- [ ] **Step 3: 实现 record_source_health（含 autoplan M11/M13）**

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
import re
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Literal

from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from app.models import ApiSourceHealth

logger = logging.getLogger(__name__)

# outcome（抓取结果三态）→ status（面板四态）映射（dx-voice F3 词表统一）：
#   ok        → ok        健康（含业务「未开奖」）
#   down      → down      运行故障（网络/DNS/限流重试耗尽）
#   permanent → degraded  配置态（key 未配置/schema 契约变更）——置 degraded 并清
#                         down_since：dx-voice F11，down 之后接 permanent 不得继续
#                         按运行故障告警（key 被删是配置事实，不该「持续失败 1440 分钟」）。
Outcome = Literal['ok', 'down', 'permanent']

# 告警阈值由 settings.source_health_alert_after_minutes 供给（dx-voice F19 逃生舱：
# 面向人的告警阈值必须可调，默认 30 分钟；时间窗实现与 tick 次数解耦——
# 持久、不怕容器重启，2026-09-15 事故中容器恰在故障期重启，内存计数会清零）。
_DEFAULT_ALERT_AFTER_MINUTES = 30


def _down_alert_after() -> timedelta:
    """阈值（分钟）→ timedelta；settings 读不到时回退默认值（复核修订 2026-09-15）。

    为什么必须容错：写入侧 `record_source_health` 的 ok 分支与评估侧触发条件都要读它，
    而设置读取失败（如测试环境 conftest 清空 JWT_SECRET/CRYPTO_KEY_V1 → Settings
    ValidationError，实测）会沿调用链上抛；写入侧那个异常还会被 `_record_health` 的
    `except Exception` 吞掉——健康表从此**静默停写**（silent-failure 纪律不允许）。
    回退到 30 分钟 + warning 留痕：告警阈值退化，但健康观测链路继续工作。
    **真值源仍是 settings**（F19 逃生舱：正常路径一律读配置，可随环境调整）；
    本常量仅兜底，并由 `test_fallback_threshold_matches_settings_default` 钉住不漂移。
    """
    try:
        from app.config import get_settings

        return timedelta(minutes=get_settings().source_health_alert_after_minutes)
    except Exception:
        logger.warning(
            'alert_threshold_settings_unavailable fallback_minutes=%d',
            _DEFAULT_ALERT_AFTER_MINUTES, exc_info=True,
        )
        return timedelta(minutes=_DEFAULT_ALERT_AFTER_MINUTES)

# error 脱敏（autoplan M13）：juhe 把 api key 放 query（juhe.py:27），
# raise_for_status 异常消息含完整 URL；健康表 error 会进 admin 面板与 Bark
# 告警体（第三方服务器），密钥参数值落表前一律替换 [REDACTED]。
# eng-voice M4：公开命名（去下划线，F21 一致性）——fetch_service 日志路径复用。
_SENSITIVE_QUERY_RE = re.compile(r'([?&](?:key|app_id|app_secret|token)=)[^&\s]+')

# now 注入点：生产用默认；测试注入固定时钟，避免真实 sleep/时间竞争。
NowFn = Callable[[], datetime]  # dx-voice F21：公开签名不用私有名


def now_naive_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def sanitize_error(error: str | None) -> str | None:
    """剥离 error 文本中的敏感 query 参数值（M13），保留 URL 其余部分供排障。

    两个消费点：record_source_health 落表前；FetchService._fetch_with_backoff
    打日志前（eng-voice M4——容器日志同在家族 NAS 上，key 不该落日志文件）。
    """
    if error is None:
        return None
    return _SENSITIVE_QUERY_RE.sub(r'\1[REDACTED]', error)


def record_source_health(
    engine: Engine,
    source: str,
    outcome: Outcome,
    error: str | None = None,
    now: NowFn = now_naive_utc,
) -> None:
    """按源 upsert 健康表（spec §1.2 语义表）。outcome→status 映射见模块头注释。

    ok + alerted=='none' + 故障时长 ≥ 阈值（autoplan M11）：转 recovering 补发
    恢复通知——故障期告警通道同挂（DNS 教训）导致告警从未送达，恢复时若静默
    清零，长故障将零通知（9 天事故复现路径）。
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
                if h.down_since is not None and t - h.down_since >= _down_alert_after():
                    h.alerted = 'recovering'  # M11：长故障零送达 → 补发恢复通知
                else:
                    h.down_since = None  # 短故障：静默恢复（未达告警阈值，无通知义务）
            else:
                # 已告警过 → 待恢复通知；保留 down_since 供评估侧算故障时长。
                h.alerted = 'recovering'
        elif outcome == 'down':
            h.status = 'down'
            if h.alerted == 'recovering':
                # 抖动/再故障（eng-voice M3a）：翻回 none 而非 alerted——alerted 的
                # 语义是「本次故障的告警已送达」（spec §1.1），M11 路径（告警从未
                # 送达）的行翻 alerted 会让面板显示「已通知」而无人收到。
                # 按新 episode 重新计时：恢复通知自然取消（发送要求 status=='ok'），
                # 是否重告警由评估器送达门控决定（duplicate > silence）。
                h.alerted = 'none'
                h.down_since = t
            elif h.down_since is None:
                h.down_since = t
            h.error = sanitize_error(error)
        elif outcome == 'permanent':
            # 配置态（dx-voice F11）：置 degraded 并清 down_since——不再按运行故障
            # 计时/告警；degraded 由此成为真实写路径（此前列注释枚举但无写入方）。
            # eng-voice M3b：alerted 一并清 none——配置态转移终结运行故障 episode，
            # 不欠恢复通知（否则 recovering 卡在 degraded 行上永远无法清偿）。
            h.status = 'degraded'
            h.down_since = None
            h.alerted = 'none'
            h.error = sanitize_error(error)
        else:
            raise ValueError(f'unknown outcome: {outcome}')
        s.commit()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/services/test_source_health.py -q`
Expected: 12 passed（原 5 + M11×2 + M13×1 + D7×1 + eng M3b×1 + 阈值回退×1 + 兜底常量防漂移×1；M3a 改写 1 个既有用例期望）。

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

eng-voice M4（日志路径脱敏闭环）：`_fetch_with_backoff` 的 `logger.warning(...)` 
（约 95-102 行）的 `exc` 参数改为 `sanitize_error(str(exc))`——M13 只堵住落表路径，
但 juhe `raise_for_status` 异常消息含完整 URL（key 在 query）会先落进容器日志文件
（同在家族 NAS 上）。import 区加 `from app.services.source_health import sanitize_error`
（顶部 import，无环：source_health 只依赖 models/config）。

eng-voice M8.1 回归测试（`tests/services/test_fetch_service.py` 追加，复用该文件既有
`_src(fetch_return, name)` helper——文件 30-36 行——与 `FetchService(primary, backup,
engine, ...)` 构造惯例）：

```python
def test_health_write_failure_does_not_break_fetch(db_engine, monkeypatch, caplog):
    """eng-voice M8.1：健康落表失败绝不阻断抓取主流程（plan-11 的核心纪律，必须有测试）。

    _record_health 的 except Exception + logger.warning 是本 plan 唯一保证「写健康表
    坏了不影响中奖比对」的代码——没有测试，一次重构就能悄悄移除它。
    （复核修订 2026-09-15：原稿 monkeypatch 整个 `_record_health` 会连带替换掉它要守的
    except/日志两行，且类属性替换会变 bound method 引发 TypeError——实测复现。）
    """
    import logging

    def _boom(*args, **kwargs):
        raise RuntimeError('db gone')

    # 注入真正的失败点：_record_health 内部 `from app.services.source_health import
    # record_source_health` 是调用时解析模块属性 → patch 该属性即生效，
    # 同时保留 _record_health 自身的 except + logger.warning（本用例要守的正是这两行）。
    monkeypatch.setattr('app.services.source_health.record_source_health', _boom)

    primary = _src(None, name='mxnzp')
    primary.fetch.side_effect = RuntimeError('timeout')
    backup = _src(None, name='juhe')
    backup.fetch.side_effect = RuntimeError('timeout')
    svc = FetchService(
        primary, backup, db_engine,
        max_attempts=1, backoff_base=0, sleep=lambda *_: None,
    )
    with caplog.at_level(logging.WARNING, logger='app.services.fetch_service'):
        result = svc.fetch_and_store('ssq')
    assert result is not None  # 抓取照常完成（未被健康写失败打断）
    assert 'source_health_write_failed' in caplog.text


def test_fetch_error_logged_with_redacted_secret(db_engine, caplog):
    """eng-voice M4 回归：juhe key 出现在异常 URL 时，日志输出必须脱敏。"""
    import logging

    primary = _src(None, name='mxnzp')
    primary.fetch.side_effect = RuntimeError(
        "Client error '403' for url "
        "'https://v.juhe.cn/lottery/query?lottery_id=ssq&key=SECRETKEY123'"
    )
    backup = _src(None, name='juhe')
    backup.fetch.side_effect = RuntimeError('timeout')
    # max_attempts=1 + sleep 置空：否则默认 6 次退避会真睡 ~31s（该文件既有失败用例同法）
    svc = FetchService(
        primary, backup, db_engine,
        max_attempts=1, backoff_base=0, sleep=lambda *_: None,
    )
    with caplog.at_level(logging.WARNING, logger='app.services.fetch_service'):
        svc.fetch_and_store('ssq')
    assert 'SECRETKEY123' not in caplog.text
    assert 'key=[REDACTED]' in caplog.text
```

（两处新增用例的 import：`FetchService` 与 `_src` 均为该文件既有模块级成员，无需新增 import。）

`_grace_refetch` 内（约 195 行）原 `m2, m2_ok = self._try_fetch(missing_source, lottery_code)` 改为：

```python
        m2, m2_outcome, m2_err = self._try_fetch(missing_source, lottery_code)
        # grace 重抓结果同样落健康表（autoplan M7）：否则 grace 内恢复的源要等下个
        # 抓取周期才转 ok，恢复通知无谓延迟一整轮（15 分钟）。
        self._record_health(missing_source.name, m2_outcome, m2_err)
        m2_ok = m2_outcome == 'ok'
```

`FetchService` 类内新增方法（放在 `_try_fetch` 之后）：

```python
    def _record_health(self, source_name: str, outcome: str, error: str | None) -> None:
        """写 ApiSourceHealth（plan-11）。独立短事务 + 吞异常：健康落表失败只记日志，
        绝不阻断抓取主流程（spec §1.2）。"""
        try:
            from app.services.source_health import record_source_health

            record_source_health(self._engine, source_name, outcome, error)
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
- Consumes: Task 3 的 `record_source_health` / `_down_alert_after()`（settings 阈值）。
- Produces: `evaluate_source_alerts(engine: Engine, sender_factory: Callable[[], Callable[[str, str], None] | None], now: NowFn = now_naive_utc) -> None`（eng-voice H1/M2：sender 改惰性工厂，只在有待发送项时调用）——Task 5 接线依赖。

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
    evaluate_source_alerts(db_engine, lambda: rec, now=lambda: t)
    assert rec.calls == []
    assert _get(db_engine).alerted == 'none'


def test_evaluate_alerts_once_after_threshold(db_engine):
    """down ≥30 分钟 → 告警一次并置 alerted；继续 down 不重复告警。"""
    t = datetime(2026, 9, 15, 13, 0, 0)
    _seed_down(db_engine, down_since=t - timedelta(minutes=31))
    rec = _Recorder()
    evaluate_source_alerts(db_engine, lambda: rec, now=lambda: t)
    evaluate_source_alerts(db_engine, lambda: rec, now=lambda: t + timedelta(minutes=15))
    assert len(rec.calls) == 1 and '持续失败' in rec.calls[0][0]
    assert _get(db_engine).alerted == 'alerted'


def test_evaluate_send_failure_keeps_state_and_retries(db_engine):
    """发送异常 → 状态保持 none，下轮重试；送达成功才转移（DNS 教训回归）。"""
    t = datetime(2026, 9, 15, 13, 0, 0)
    _seed_down(db_engine, down_since=t - timedelta(minutes=40))
    rec = _Recorder(fail_first=1)
    evaluate_source_alerts(db_engine, lambda: rec, now=lambda: t)
    assert rec.calls == [] and _get(db_engine).alerted == 'none'
    evaluate_source_alerts(db_engine, lambda: rec, now=lambda: t + timedelta(minutes=15))
    assert len(rec.calls) == 1 and _get(db_engine).alerted == 'alerted'


def test_recovery_notice_sent_then_reset(db_engine):
    """alerted → 抓取恢复（写入侧置 recovering）→ 评估送出恢复通知 → none+清 down_since。"""
    t = datetime(2026, 9, 15, 13, 0, 0)
    _seed_down(db_engine, down_since=t - timedelta(hours=2), alerted='alerted')
    record_source_health(db_engine, 'mxnzp', 'ok', now=lambda: t)  # → recovering
    rec = _Recorder()
    evaluate_source_alerts(db_engine, lambda: rec, now=lambda: t)
    assert len(rec.calls) == 1 and '恢复' in rec.calls[0][0]
    h = _get(db_engine)
    assert h.alerted == 'none' and h.down_since is None


def test_recovery_send_failure_retries(db_engine):
    """恢复通知发送失败 → 保持 recovering，下轮送达才回 none。"""
    t = datetime(2026, 9, 15, 13, 0, 0)
    _seed_down(db_engine, down_since=t - timedelta(hours=2), alerted='alerted')
    record_source_health(db_engine, 'mxnzp', 'ok', now=lambda: t)
    rec = _Recorder(fail_first=1)
    evaluate_source_alerts(db_engine, lambda: rec, now=lambda: t)
    assert _get(db_engine).alerted == 'recovering'
    evaluate_source_alerts(db_engine, lambda: rec, now=lambda: t + timedelta(minutes=15))
    assert _get(db_engine).alerted == 'none'


def test_no_sender_keeps_state_and_logs(db_engine, caplog):
    """sender_factory 返回 None（未配 key / 开关关闭）→ 不发送也**不转移**（dx-voice F6：

    alerted 的语义是「故障告警已送达」（spec §1.1），未送达标 alerted 是状态说谎——
    面板会显示「已通知」而无人收到。保持 none + 每次评估 warning 一次；
    配好 key 后下一轮自然补发。
    """
    t = datetime(2026, 9, 15, 13, 0, 0)
    _seed_down(db_engine, down_since=t - timedelta(minutes=40))
    with caplog.at_level(logging.WARNING, logger='app.services.source_health'):
        evaluate_source_alerts(db_engine, lambda: None, now=lambda: t)
    assert _get(db_engine).alerted == 'none'
    assert 'source_alerts_disabled' in caplog.text


def test_record_down_then_permanent_stops_alerting(db_engine):
    """down（down_since 已置）之后接 permanent（运行中 key 被删）→ 清 down_since
    置 degraded，评估器不再按运行故障告警（dx-voice F11：否则「持续失败 1440 分钟」
    误报，与「配置态不告警」立意冲突）。"""
    t0 = datetime(2026, 9, 15, 4, 0, 0)
    record_source_health(db_engine, 'juhe', 'down', 'boom', now=lambda: t0)
    record_source_health(db_engine, 'juhe', 'permanent', 'juhe api_key not configured',
                         now=lambda: t0 + timedelta(hours=1))
    h = _get(db_engine, 'juhe')
    assert h.status == 'degraded' and h.down_since is None
    rec = _Recorder()
    evaluate_source_alerts(db_engine, lambda: rec, now=lambda: t0 + timedelta(hours=2))
    assert rec.calls == []


def test_evaluate_sends_outside_db_session(db_engine):
    """M1 回归：send_alert 调用时评估器不得持有 DB 连接（pool_size=1 纪律）。

    若评估器在 session 内发送（plan 原稿），DNS 故障下 Bark 挂 10s 超时期间
    唯一连接被占，其他 job/请求撞 busy_timeout——jobs.py:276-278 两次事故同型。
    """
    t = datetime(2026, 9, 15, 13, 0, 0)
    _seed_down(db_engine, down_since=t - timedelta(minutes=40))
    checked_out_during_send = []

    def _sender(title, body):
        checked_out_during_send.append(db_engine.pool.checkedout())

    evaluate_source_alerts(db_engine, lambda: _sender, now=lambda: t)
    assert checked_out_during_send == [0]


def test_alert_body_includes_peer_source_status(db_engine):
    """告警体必须含备源状态（design-voice D5）：备源 ok 时明说「开奖未受影响」，
    避免单源故障的告警读起来像已经漏开奖（告警疲劳最快路径）。"""
    t = datetime(2026, 9, 15, 13, 0, 0)
    _seed_down(db_engine, down_since=t - timedelta(minutes=40))
    with Session(db_engine) as s:
        s.add(ApiSourceHealth(source='juhe', status='ok'))
        s.commit()
    rec = _Recorder()
    evaluate_source_alerts(db_engine, lambda: rec, now=lambda: t)
    assert len(rec.calls) == 1
    assert '备用源正常' in rec.calls[0][1] and '开奖未受影响' in rec.calls[0][1]
    assert '（自' not in rec.calls[0][1]  # D3：不带 UTC 绝对时间（与同句分钟数矛盾）


def test_factory_not_called_when_nothing_pending(db_engine):
    """eng-voice M2 回归：无待发送项时 sender 工厂不得被调用——

    工厂背后是 build_admin_alert()（新建 BarkChannel/httpx.Client）。若每 tick
    无条件构造，pool_size=1 单连接进程每天泄漏 ~96 个未关闭 client（bark.py:35-37
    client 仅经 close() 释放；notifier.py:271-290 把 close 当一等纪律）。
    """
    factory_spy = _Recorder()  # 复用 calls 记录；作为工厂被调即留痕
    with Session(db_engine) as s:
        s.add(ApiSourceHealth(source='mxnzp', status='ok',
                              last_success_at=datetime(2026, 9, 15, 12, 0, 0)))
        s.commit()
    evaluate_source_alerts(db_engine, factory_spy, now=lambda: datetime(2026, 9, 15, 13, 0, 0))
    assert factory_spy.calls == []


def test_alert_body_when_both_sources_down(db_engine):
    """eng-voice M8.4：双源同时故障 → 告警体如实声明（不得复用「备用源正常」文案）。"""
    t = datetime(2026, 9, 15, 13, 0, 0)
    _seed_down(db_engine, down_since=t - timedelta(minutes=40))
    with Session(db_engine) as s:
        s.add(ApiSourceHealth(source='juhe', status='down',
                              down_since=t - timedelta(minutes=35)))
        s.commit()
    rec = _Recorder()
    evaluate_source_alerts(db_engine, lambda: rec, now=lambda: t)
    bodies = [b for _, b in rec.calls]
    assert any('双源同时故障' in b for b in bodies)


def test_alert_body_when_peer_degraded(db_engine):
    """eng-voice M8.4：备源 degraded（如 juhe 未配 key）→ 明说单点风险，不伪装双源在线。"""
    t = datetime(2026, 9, 15, 13, 0, 0)
    _seed_down(db_engine, down_since=t - timedelta(minutes=40))
    with Session(db_engine) as s:
        s.add(ApiSourceHealth(source='juhe', status='degraded',
                              error='juhe api_key not configured'))
        s.commit()
    rec = _Recorder()
    evaluate_source_alerts(db_engine, lambda: rec, now=lambda: t)
    assert len(rec.calls) == 1
    assert '备用源非健康' in rec.calls[0][1] and 'juhe=degraded' in rec.calls[0][1]
    assert '备用源正常' not in rec.calls[0][1]


def test_redown_after_recovering_realerts(db_engine):
    """eng-voice M3a 评估侧：recovering 期间再 down（写入侧翻 none + 重置 episode）→

    达阈值后重新告警。翻回 none 是诚实状态（本次故障尚未送达），评估器据此重新决策。
    """
    t0 = datetime(2026, 9, 15, 4, 0, 0)
    with Session(db_engine) as s:
        s.add(ApiSourceHealth(source='mxnzp', status='ok', alerted='recovering',
                              down_since=t0))
        s.commit()
    t1 = t0 + timedelta(minutes=5)
    record_source_health(db_engine, 'mxnzp', 'down', 'again', now=lambda: t1)
    rec = _Recorder()
    # 新 episode 不足阈值 → 不告警
    evaluate_source_alerts(db_engine, lambda: rec, now=lambda: t1 + timedelta(minutes=10))
    assert rec.calls == []
    # 新 episode 达阈值 → 重新告警
    evaluate_source_alerts(db_engine, lambda: rec, now=lambda: t1 + timedelta(minutes=31))
    assert len(rec.calls) == 1 and '持续失败' in rec.calls[0][0]

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/services/test_source_health.py -q -k evaluate`
Expected: FAIL（ImportError: evaluate_source_alerts）。

- [ ] **Step 3: 实现评估器（追加到 source_health.py，autoplan M1 两阶段）**

```python
def evaluate_source_alerts(
    engine: Engine,
    sender_factory: Callable[[], Callable[[str, str], None] | None],
    now: NowFn = now_naive_utc,
) -> None:
    """评估健康表驱动告警状态机（spec §1.3；挂载于 path_a tick 尾 + 启动 backfill 尾）。

    sender_factory（eng-voice H1/M2）：惰性构造 sender 的工厂——只在确有告警/恢复
    待发送时才调用。无待发送项时绝不调用：不新建未关闭的 BarkChannel/httpx.Client
    （bark.py:35-37，client 仅经 close() 释放），也不在健康表全 ok 时触碰
    get_settings()（H1：调度测试环境 conftest 删 JWT_SECRET/CRYPTO_KEY_V1，
    无条件调 get_settings() 会 ValidationError 被外层 except 吞掉，评估静默不执行）。
    工厂返回 None（ADMIN_BARK_KEY 未配 / SOURCE_HEALTH_ALERTS_ENABLED=false）→
    不发送也**不转移**（dx-voice F6：alerted 的语义是「故障告警已送达」（spec §1.1），
    未送达就标 alerted 是状态说谎——面板会显示「已通知」而实际无人收到；保持 none
    并每次评估 warning 一次，配好 key 后下一轮评估自然补发）。
    发送异常不转移状态（下轮重试直到送达）——2026-09-15 DNS 事故教训：故障期
    告警通道大概率同时挂。

    两阶段（autoplan M1，pool_size=1 纪律）：短 session 读+决策后关闭 → session
    外发 HTTP 告警 → 短 session 守卫重读后落转移。绝不在持有唯一连接的 session
    内做 httpx 调用——DNS 故障（本 plan 目标场景）下 Bark 挂到 10s 超时，同期
    其他 job/请求借不到连接撞 busy_timeout，告警机制反而制造它要防的漏通知
    （jobs.py:276-278/339、password_reset_service.py:131/204 两次实测事故同型）。
    落转移前重读校验状态未变：读与落之间若有 fetch 写入（如故障恰好恢复），
    放弃本轮转移下轮重评，不覆盖并发写入（eng-voice L2 已知取舍：此时已送达的
    告警下一轮可能因状态未落而重复发送一次——duplicate > silence，运维看到
    重复告警比漏告警安全；deploy.md 写明避免误当 bug 追查）。
    """
    t = now()
    # 阶段 1：短 session 读 + 决策（快照出 session，不留 ORM 对象跨 session）
    with Session(engine) as s:
        rows = [
            (h.source, h.status, h.alerted, h.down_since, h.error)
            for h in s.exec(select(ApiSourceHealth)).all()
        ]
    # 备源状态速查（design-voice D5：告警体必须回答「开奖是否受影响」——
    # 单源 down 但备源正常时，告警文案若暗示漏开奖是最快的静音之路）。
    peer_status = {source: status for source, status, *_ in rows}

    # 阶段 1.5：先决策出全部待发送项；没有就直接返回——工厂不被调用（eng M2）。
    pending: list[tuple[str, str, str, str]] = []  # (source, 目标 alerted, title, body)
    for source, status, alerted, down_since, error in rows:
        if (
            status == 'down'
            and alerted == 'none'
            and down_since is not None
            and t - down_since >= _down_alert_after()
        ):
            minutes = int((t - down_since).total_seconds() // 60)
            peers = {p: ps for p, ps in peer_status.items() if p != source}
            if not peers:
                impact = '仅此一个数据源，开奖可能延迟入库'
            elif all(ps == 'ok' for ps in peers.values()):
                impact = f'备用源正常（{"、".join(peers)}），开奖未受影响'
            elif any(ps == 'down' for ps in peers.values()):
                impact = '双源同时故障，开奖可能延迟入库'
            else:
                # 备源 unknown/degraded（如 juhe 未配置）——不是「双源故障」，
                # 但主源 down 时实际上只剩单点，延迟风险同样要说清。
                impact = (
                    f'备用源非健康（{"、".join(f"{p}={ps}" for p, ps in peers.items())}），'
                    f'开奖可能延迟入库'
                )
            # 文案不带绝对时间（design-voice D3：UTC 括号与同句的分钟数
            # 相差 8h 自相矛盾；时长已足够定位）。fix 指引（dx-voice F10：
            # problem+cause+fix——半夜收到的人需要知道下一步做什么）。
            pending.append((
                source,
                'alerted',
                '开奖抓取持续失败',
                f'数据源 {source} 已持续失败约 {minutes} 分钟。{impact}。'
                f'最近错误：{(error or "")[:200]}。'
                f'处理：检查 NAS 网络/DNS 与上游状态；面板 /admin/health 查看；'
                f'详见 docs/deploy.md「数据源健康告警」。',
            ))
        elif alerted == 'recovering' and status == 'ok':
            duration = t - down_since if down_since else timedelta(0)
            minutes = int(duration.total_seconds() // 60)
            # 诚实声明缺口（design-voice D6：闭环不能只到「恢复」——
            # 故障窗口的开奖是否补回，admin 必须知道要不要人工介入）。
            pending.append((
                source,
                'none',
                '开奖抓取已恢复',
                f'数据源 {source} 已恢复抓取（故障持续约 {minutes} 分钟）。'
                f'故障期间的开奖缺失将随今晚 path_a 轮询与启动回填'
                f'（最近 2 天）覆盖；更长缺口请人工确认是否需要补抓。',
            ))
    if not pending:
        return

    # 阶段 2：惰性取 sender（此刻才碰 settings/BarkChannel，eng H1/M2）；
    # F6：取不到就不发送不转移，每次评估 warning 一次。
    send_alert = sender_factory()
    if send_alert is None:
        logger.warning(
            'source_alerts_disabled reason=no_sender '
            '(ADMIN_BARK_KEY 未配或 SOURCE_HEALTH_ALERTS_ENABLED=false)：'
            '健康告警只落表不发送'
        )
        return
    # session 外发送；送达成功的进待落清单
    delivered: list[tuple[str, str]] = []  # (source, 目标 alerted 状态)
    for source, target, title, body in pending:
        try:
            send_alert(title, body)
        except Exception:
            # error 级（dx-voice F12：告警链路本身挂了是重大运维事件，
            # 不是普通 warning；重试语义不变——下轮继续尝试直到送达）。
            logger.error('source_alert_send_failed source=%s', source, exc_info=True)
            continue  # 未送达不转移，下轮重试
        delivered.append((source, target))
    if not delivered:
        return
    # 阶段 3：短 session 守卫重读后落转移（recovering 完成时清 down_since）
    with Session(engine) as s:
        for source, target in delivered:
            h = s.get(ApiSourceHealth, source)
            if h is None:
                continue
            if target == 'alerted' and h.alerted == 'none' and h.status == 'down':
                h.alerted = 'alerted'
            elif target == 'none' and h.alerted == 'recovering' and h.status == 'ok':
                h.alerted = 'none'
                h.down_since = None
        s.commit()
```

（注：单源发送失败只跳过该源的状态转移，其他源继续评估与落库——两行数据源互不影响。）

同模块追加 sender 工厂（eng-voice H1/M2，jobs.py/backfill.py 共用）：

```python
def admin_alert_sender_factory() -> Callable[[str, str], None] | None:
    """健康告警 sender 工厂（dx-voice F18 独立开关 + eng-voice H1/M2 惰性构造）。

    SOURCE_HEALTH_ALERTS_ENABLED=false → None（健康告警与密码重置告警不共用
    一个总开关）；开关开但 ADMIN_BARK_KEY 未配 → build_admin_alert() 自身返回 None。
    评估器只在确有待发送项时才调用本工厂——无待发送项不碰 settings/httpx。
    """
    from app.config import get_settings
    from app.notifications.admin_alert import build_admin_alert

    if not get_settings().source_health_alerts_enabled:
        return None
    return build_admin_alert()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/services/test_source_health.py -q`
Expected: 25 passed（Task 3 的 12 + 本任务 13：原 9 + eng M2 工厂惰性回归 + M8.4 影响分支×2 + M3a 重告警）。

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
- Consumes: Task 4 `evaluate_source_alerts` 与 `admin_alert_sender_factory`、Task 2 的 settings 三字段（`source_health_alerts_enabled` 由工厂内部读取）。
- Produces: `_path_a_tick` 与 `run_startup_backfill` 尾部各调用一次评估；backfill 复用 `jobs._INTER_LOTTERY_INTERVAL`（M6 单一真值源）。

- [ ] **Step 1: 写失败测试（test_jobs.py 追加，用仓库既有 `_invoke_job` 辅助）**

（dx-voice F17：片段自给自足——实测 `tests/scheduler/test_jobs.py` 顶部只有
`build_scheduler` / `_invoke_job`（后者 :13），`register_all_jobs` 是在测试函数内 import 的；
下面的片段已自带 `from app.scheduler.jobs import register_all_jobs`，照抄不撞 NameError。）

```python
def test_path_a_tick_evaluates_source_alerts(db_engine, monkeypatch):
    """path_a_tick 尾部必须评估数据源健康告警（plan-11：tick 即评估点）。"""
    from unittest.mock import MagicMock

    import app.scheduler.jobs as jobs_mod
    from app.scheduler.jobs import register_all_jobs
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
    # engine 为第一参数；第二参数是 sender 工厂（eng H1/M2 惰性设计：本测试健康表
    # 为空、无待发送项，真实评估器根本不会调用工厂——无需注入 JWT_SECRET 等 env）。
    assert spy.call_args.args[0] is db_engine


def test_path_a_tick_sender_none_when_alerts_disabled(db_engine, monkeypatch):
    """SOURCE_HEALTH_ALERTS_ENABLED=false → sender 工厂返回 None（dx-voice F18 独立开关：

    配了 ADMIN_BARK_KEY 也不发——健康告警与密码重置告警不共用一个总开关）。
    eng-voice H1：直接调用工厂验证开关语义，需合法 JWT_SECRET/CRYPTO_KEY_V1 env
    （conftest autouse 会删，test_admin.py:15-18 范式补回）。
    """
    from unittest.mock import MagicMock

    import app.scheduler.jobs as jobs_mod
    from app.scheduler.jobs import register_all_jobs
    from app.scheduler.setup import build_scheduler

    monkeypatch.setenv('JWT_SECRET', 'x' * 32)
    from cryptography.fernet import Fernet
    monkeypatch.setenv('CRYPTO_KEY_V1', Fernet.generate_key().decode())
    monkeypatch.setenv('ADMIN_BARK_KEY', 'test-key')
    monkeypatch.setenv('SOURCE_HEALTH_ALERTS_ENABLED', 'false')
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
    factory = spy.call_args.args[1]
    assert factory() is None  # 开关关闭 → 工厂拒绝构造 sender（F6 由此触发）
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

- [ ] **Step 3: 跑新测试确认失败**

Run: `uv run pytest tests/scheduler/test_jobs.py::test_path_a_tick_evaluates_source_alerts tests/scheduler/test_jobs.py::test_path_a_tick_sender_none_when_alerts_disabled tests/scheduler/test_backfill.py::test_startup_backfill_paces_fetches_with_interval tests/scheduler/test_backfill.py::test_startup_backfill_evaluates_source_alerts -q`
Expected: 4 FAIL（ImportError/AttributeError: `admin_alert_sender_factory` / `evaluate_source_alerts` 尚未接线、backfill 无 `_INTER_LOTTERY_INTERVAL`）。

（复核修订 2026-09-15：原命令含 `tests/test_config.py::test_source_health_alert_settings_defaults`，
但该用例已在 Task 2 Step 1 落地——此处再点它既非 RED 也无意义；pytest 对不存在的 node id
是 exit 4「no tests ran」，与「5 FAIL」的预期不符。）

- [ ] **Step 4: 实现 jobs.py 接线（含 dx-voice F18 独立开关）**

`app/scheduler/jobs.py` import 区加：

```python
from app.services.source_health import admin_alert_sender_factory, evaluate_source_alerts
```

（eng H1/M2 后不再需要 `from app.config import get_settings` 与
`from app.notifications.admin_alert import build_admin_alert`——开关判断与 sender
构造都收入工厂；工厂定义在 source_health.py，与评估器同模块。）

`_path_a_tick` 函数末尾（`sched.add_job(_push_big_win, ...)` 循环之后、函数体结束前）追加：

```python
    # 数据源健康评估（plan-11）：tick 尾部评估告警状态机（down≥阈值 → admin bark）。
    # sender 经工厂惰性构造（eng H1/M2）：无待发送项时不碰 get_settings/httpx；
    # 评估失败不阻断本 tick 收尾。
    try:
        evaluate_source_alerts(engine, admin_alert_sender_factory)
    except Exception:
        logger.error('source_alert_evaluate_failed', exc_info=True)
```

- [ ] **Step 5: 实现 backfill.py 间隔 + 尾部评估**

`app/scheduler/backfill.py`：import 区加（eng-voice M7 核实：`import time` 在第 3 行、
`from app.config import get_settings` 在第 10 行均已存在，无需再加）：

```python
from app.scheduler.jobs import _INTER_LOTTERY_INTERVAL
from app.services.source_health import admin_alert_sender_factory, evaluate_source_alerts
```

M6 说明：plan 原稿在 backfill 本地复制 `_INTER_LOTTERY_INTERVAL = 1.2`（注释称「反向
import 会循环依赖」）——已核实 jobs.py 不 import backfill，无环，复制只会漂移
（MXNZP QPS 限额调整时需改两处）。单一真值源：backfill 直接 import jobs 常量；
conftest 置 0 与间隔测试 monkeypatch `backfill_mod._INTER_LOTTERY_INTERVAL`
（模块内 import 引用）依然有效，测试写法不变。

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
    # sender 工厂惰性构造（eng H1/M2，同 jobs.py 尾部）。
    try:
        evaluate_source_alerts(engine, admin_alert_sender_factory)
    except Exception:
        logger.error('source_alert_evaluate_failed', exc_info=True)
```

（原循环体的 try/except 与 missed 判断保持原样，仅包入 fetched 计数与 sleep。）

eng-voice M7（M6 单一真值源补完）：`_backfill_history` 内残留的 `time.sleep(1.2)`
（backfill.py:109）同步改为 `time.sleep(_INTER_LOTTERY_INTERVAL)`——同一个 MXNZP
QPS 限额常量不得有两份拷贝。

- [ ] **Step 5.5: （已前移，此处无动作）**

config 三字段（`admin_bark_url` / `source_health_alerts_enabled` /
`source_health_alert_after_minutes`）与 `app/main.py` url 同源、`tests/test_config.py`
默认值用例，已全部前移至 **Task 2 Step 1-4**（本任务接线依赖它们，若留在此处，
Task 2 实现 `settings.admin_bark_url` 时该字段尚不存在——复核修订 2026-09-15）。
本步保留编号只为不改动后续步号与既有交叉引用，**无需执行**。

- [ ] **Step 6: conftest 补置 0**

`tests/conftest.py` 的 `_disable_inter_lottery_interval` fixture 内追加：

```python
    from app.scheduler import backfill as backfill_mod

    monkeypatch.setattr(backfill_mod, '_INTER_LOTTERY_INTERVAL', 0)
```

（fixture docstring 同步提及 backfill。）

- [ ] **Step 7: 新测试通过 + 调度器套件回归**

Run: `uv run pytest tests/scheduler/ tests/test_config.py -q`
Expected: 全部 PASS（含既有 `test_path_a_tick_paces_mxnzp_qps_with_inter_lottery_interval` 与全部 backfill 测试）。

- [ ] **Step 8: Commit**

```bash
git add app/scheduler/jobs.py app/scheduler/backfill.py tests/conftest.py tests/scheduler/test_jobs.py tests/scheduler/test_backfill.py
git commit -m "feat(plan-11): tick/backfill 尾部评估源告警；启动回填对实际抓取加 QPS 间隔"
```

（`app/config.py` / `app/main.py` / `tests/test_config.py` 已由 Task 2 Step 9 提交，
本步不再重复 add。）

---

### Task 6: /admin/health 扩展 + Admin.vue 展示

**Files:**
- Modify: `app/api/admin.py:79-82`（system_health 响应）
- Modify: `web/src/pages/Admin.vue:19-22`（HealthSource）、`web/src/pages/Admin.vue:644-648`（模板）
- Modify: `web/src/pages/Admin.test.ts`（Step 5 追加 4 个健康卡用例；须插在唯一的
  `describe("Admin.vue (T6f)")` 块内，`mount`/`host` 是该块级作用域成员）
- Test: `tests/api/test_admin.py:128-138`（扩展既有断言）

**Interfaces:**
- Consumes: Task 1 的 `down_since` 列。
- Produces: `/admin/health` 每源返回 `status` / `alerted` / `error`（全文；前端截断——eng M6）/ `last_success_at` / `down_since`（后两个为显式 UTC ISO 或 null）。

- [ ] **Step 1: 扩展既有测试（RED，含 autoplan D-3 时区断言）**

`tests/api/test_admin.py` 的 `test_admin_system_health` 改为：

```python
def test_admin_system_health(db_engine, monkeypatch):
    with Session(db_engine) as s:
        s.add(ApiSourceHealth(source='mxnzp', status='ok',
                              last_success_at=datetime(2026, 9, 15, 4, 0, 0)))
        s.add(ApiSourceHealth(source='juhe', status='degraded',
                              down_since=datetime(2026, 9, 14, 20, 0, 0)))
        s.commit()
    client = _admin_client(db_engine, monkeypatch)
    r = client.get('/admin/health')
    assert r.status_code == 200
    data = r.json()
    assert len(data['sources']) == 2
    assert {s['source'] for s in data['sources']} == {'mxnzp', 'juhe'}
    # plan-11：每源返回 last_success_at/down_since（null 安全——空表行也要有键）
    # design-voice D4：alerted（状态机输出）与 error（故障原因）同返——面板必须
    # 回答「叫过人没有」「为什么挂」，只给时间是次有用的信息。
    assert all({'last_success_at', 'down_since', 'alerted', 'error'} <= set(s) for s in data['sources'])
    # eng-voice L1：按 source 索引而非数组下标——SQLite 返回序不契约化，
    # 下标断言会把「顺序变了」误报成「值错了」。
    by = {s['source']: s for s in data['sources']}
    # autoplan D-3：naive UTC 落库值必须以 'Z' 显式标注 UTC——否则前端
    # new Date() 按本地时区解析，面板故障起点显示偏差 8 小时（全程 Asia/Shanghai 纪律）。
    assert by['mxnzp']['last_success_at'].endswith('Z')
    assert by['juhe']['down_since'].endswith('Z')


def test_admin_system_health_null_and_long_error(db_engine, monkeypatch):
    """eng-voice M8.7/L3：空值与长 error 的边界——

    _iso_utc(None) → None（不抛）；error 全文返回不截断（eng M6：截断移到前端，
    后端截断会让 title 悬浮也只看到同一份截断文本，D17「全文可见」失效）。
    """
    long_error = 'x' * 300
    with Session(db_engine) as s:
        s.add(ApiSourceHealth(source='mxnzp', status='down', error=long_error))
        s.commit()
    client = _admin_client(db_engine, monkeypatch)
    data = client.get('/admin/health').json()
    src = {s['source']: s for s in data['sources']}['mxnzp']
    assert src['last_success_at'] is None and src['down_since'] is None
    assert src['error'] == long_error  # 后端不截断
```

（文件头部 import 区补 `from datetime import datetime`，若未有。）

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/api/test_admin.py::test_admin_system_health -q`
Expected: FAIL（响应无 last_success_at 键）。

- [ ] **Step 3: 实现响应扩展（含 autoplan D-3 显式 UTC）**

`app/api/admin.py` system_health 改为：

```python
def _iso_utc(dt) -> str | None:
    """naive UTC 落库值 → 显式 UTC ISO（追加 'Z'）。

    裸 isoformat() 无时区标记，前端 new Date() 会按浏览器本地时区解析，
    面板时间显示偏差 8 小时（autoplan D-3；全程 Asia/Shanghai 纪律）。
    eng-voice L3：兼容 aware 输入（先归一到 naive UTC 再加 'Z'，
    否则 aware 值会得到 '…+00:00Z' 的双重标记）。
    """
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.isoformat() + 'Z'


@router.get('/health')
def system_health(session: Session = Depends(get_session_dep)):
    sources = session.exec(select(ApiSourceHealth)).all()
    return {
        'sources': [
            {
                'source': s.source,
                'status': s.status,
                'alerted': s.alerted,
                # eng-voice M6：后端不再截断——截断移到前端 fmtError，
                # 否则 title 悬浮也只能看到同一份截断文本（D17 全文可见失效）。
                'error': s.error,
                'last_success_at': _iso_utc(s.last_success_at),
                'down_since': _iso_utc(s.down_since),
            }
            for s in sources
        ]
    }
```

（import 区补 `from datetime import timezone`，若未有；删除 plan 原稿的 `_truncate`——
eng M6 决定后端返回全文。）

- [ ] **Step 4: Admin.vue 展示（含 autoplan D-1/D-2/D-4 + design-voice D3/D4/D17）**

`HealthSource` 接口改为：

```typescript
interface HealthSource {
  source: string;
  status: string;
  alerted: string;
  error: string | null;
  last_success_at: string | null;
  down_since: string | null;
}
```

script 区加时长 helper（design-voice D3：时长为主、时区免疫——面板要回答的是
「多久了」，不是「几点几分」；后端已返回显式 UTC（'Z'），前端算差值即可，
不引入第二个日期格式——页面既有 formatDate 管绝对时间，这里管时长）：

```typescript
// 由显式 UTC（'Z' 后缀）算到当前的时长文本：「X 分钟 / X 小时 N 分 / X 天 N 小时」。
function fmtDuration(sinceIso: string): string {
  const ms = Date.now() - new Date(sinceIso).getTime();
  const minutes = Math.max(0, Math.floor(ms / 60000));
  if (minutes < 60) return `${minutes} 分钟`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} 小时${minutes % 60 ? ` ${minutes % 60} 分` : ''}`;
  const days = Math.floor(hours / 24);
  return `${days} 天${hours % 24 ? ` ${hours % 24} 小时` : ''}`;
}

// eng-voice M6：error 内联截断（80 字符 + '…'，截断必须可见），全文经 title 悬浮可见。
// 后端返回全文（不在后端截断——否则 title 也只有截断版，D17 全文可见失效）。
function fmtError(error: string): string {
  return error.length > 80 ? `${error.slice(0, 80)}…` : error;
}
```

模板（design-voice D2：这是**在既有 `source-item` div 内插入**两个 span——
644 行 `v-if="health.length > 0"` 与 650 行 `v-else class="empty-tip"` 保持
逐字节不动；下列片段仅示意插入位置，不是整段替换。插入点一：source-name 与
source-status 之间放 meta（时长 + error 摘要）；插入点二：source-status 前放
alerted 标签（区分「挂了」与「挂了且已叫人」）。
eng-voice M3a：时长文案按 `status !== 'ok'` 门控——recovering 行（恢复待通知、
down_since 保留中）status 已是 ok，若不看 status 会在绿色 ok pill 旁永远显示
「已故障 N 天」（状态说谎，与 F6 同类））：

```html
            <div v-for="s in health" :key="s.source" class="source-item">
              <span class="source-name">{{ s.source }}</span>
              <span class="source-meta">
                {{ s.status !== 'ok' && s.down_since ? `已故障 ${fmtDuration(s.down_since)}` : (s.last_success_at ? `最后成功 ${fmtDuration(s.last_success_at)}前` : '—') }}
                <span v-if="s.error" class="source-error" :title="s.error">{{ fmtError(s.error) }}</span>
              </span>
              <span v-if="s.alerted === 'alerted'" class="source-alert-tag">已通知</span>
              <span v-else-if="s.alerted === 'recovering'" class="source-alert-tag recovering">恢复待通知</span>
              <span class="source-status" :class="s.status">{{ s.status }}</span>
            </div>
```

style 区（autoplan D-1/D-2/D-4 + design-voice D4）：

```css
/* D-4：新增 meta/tag 后行内容变长，375px 下允许换行防挤压 */
.source-item {
  flex-wrap: wrap;
  row-gap: 4px;
}

/* D-2：次要信息用既有 --muted token（tokens.css 含 dark 变体），
   不引入 --vt-c-text-2（VitePress 变量，本项目不存在）或硬编码 #888 */
.source-meta {
  color: var(--muted);
  font-size: var(--text-xs);
}

/* D4：error 摘要与 meta 同行但更可忽略；全文经 title 悬浮可见（已后端截断 + …） */
.source-error {
  margin-left: 6px;
  opacity: 0.85;
}

/* D4：告警状态标签（沿用 status-badge 的 pill 模式与 DESIGN.md token） */
.source-alert-tag {
  padding: 3px 10px;
  border-radius: 20px;
  font-size: var(--text-xs);
  font-weight: 600;
  background: var(--surface-2);
  color: var(--muted);
}

.source-alert-tag.recovering {
  background: #fef3c7;
  color: var(--warning);
}

/* D-1：健康状态色补齐——plan-11 起 down 真实落表，未定义 class 的状态会裸奔。
   文字色用 DESIGN.md 语义 token（--danger/--warning/--muted），底色沿用既有 pill 风格 */
.source-status.down {
  background: #fee2e2;
  color: var(--danger);
}

.source-status.degraded {
  background: #fef3c7;
  color: var(--warning);
}

.source-status.unknown {
  background: var(--surface-2);
  color: var(--muted);
}
```

- [ ] **Step 5: 前端健康卡测试（design-voice D10：现有 Admin.test.ts 对健康卡零断言）**

`web/src/pages/Admin.test.ts` 追加（stubApi 已支持 `overrides.health`，见该文件 35-36 行。
eng-voice M5：本仓库**没有** @vue/test-utils——用文件既有 `mount(overrides)` helper
（返回宿主 div，见 66-90 行）+ `querySelectorAll`/`classList`/`textContent` 断言，
不新增依赖）：

```typescript
it('健康卡渲染 down 状态：时长、状态色 class、告警标签、error 摘要', async () => {
  const downSince = new Date(Date.now() - 40 * 60000).toISOString();
  await mount({
    health: {
      sources: [
        { source: 'mxnzp', status: 'down', alerted: 'alerted',
          error: 'dns boom', last_success_at: null, down_since: downSince },
        { source: 'juhe', status: 'ok', alerted: 'none',
          error: null, last_success_at: new Date().toISOString(), down_since: null },
      ],
    },
  });
  const items = host.querySelectorAll('.source-item');
  expect(items).toHaveLength(2);
  const down = items[0];
  expect(down.querySelector('.source-status')!.classList.contains('down')).toBe(true);
  expect(down.querySelector('.source-meta')!.textContent).toContain('已故障');
  expect(down.querySelector('.source-meta')!.textContent).toContain('分钟');
  expect(down.querySelector('.source-alert-tag')!.textContent).toBe('已通知');
  expect(down.querySelector('.source-error')!.textContent).toContain('dns boom');
});

it('健康卡空态保留（v-else 不被模板改动删除）', async () => {
  await mount({ health: { sources: [] } });
  expect(host.querySelector('.empty-tip')).not.toBeNull();
});

it('eng M3a：recovering 的 ok 行显示「最后成功」而非「已故障」（面板不说谎）', async () => {
  await mount({
    health: {
      sources: [
        { source: 'mxnzp', status: 'ok', alerted: 'recovering',
          error: null, last_success_at: new Date().toISOString(),
          down_since: new Date(Date.now() - 2 * 3600000).toISOString() },
      ],
    },
  });
  const meta = host.querySelector('.source-item .source-meta')!;
  expect(meta.textContent).toContain('最后成功');
  expect(meta.textContent).not.toContain('已故障');
  expect(host.querySelector('.source-alert-tag')!.textContent).toBe('恢复待通知');
});

it('eng M6：长 error 内联截断 + 省略号可见，title 悬浮为全文', async () => {
  const longError = 'e'.repeat(300);
  await mount({
    health: {
      sources: [
        { source: 'mxnzp', status: 'down', alerted: 'none',
          error: longError, last_success_at: null, down_since: null },
      ],
    },
  });
  const err = host.querySelector('.source-error')!;
  expect(err.textContent).toHaveLength(81); // 80 + '…'
  expect(err.textContent!.endsWith('…')).toBe(true);
  expect(err.getAttribute('title')).toBe(longError);
});
```

（挂载辅助为该文件既有 `mount(overrides)`（返回宿主元素并 awaited flush）；
`host` 为 describe 块级共享变量——与该文件既有用例同型。）

- [ ] **Step 6: 前后端验证**

Run: `uv run pytest tests/api/test_admin.py -q && npm --prefix web run build && npm --prefix web run test`
Expected: pytest PASS；vue-tsc + vite build exit 0；vitest PASS（含新增 4 个健康卡用例：down 全要素 / 空态回归 / M3a ok 行门控 / M6 截断+title）。

- [ ] **Step 7: Commit**

```bash
git add app/api/admin.py web/src/pages/Admin.vue web/src/pages/Admin.test.ts tests/api/test_admin.py
git commit -m "feat(plan-11): /admin/health 返回 alerted/error/时间戳，面板展示故障时长与通知状态"
```

---

### Task 7: 运维文档 + 全量回归收尾

**Files:**
- Modify: `docs/deploy.md`（新增「数据源健康告警」小节，dx-voice F15/F16）
- Modify: `CLAUDE.md`（「关键约定」补一行告警机制指针，F15）
- 其余只验证（若前序遗漏由本任务兜底发现）。

**Interfaces:**
- Consumes: Task 1-6 全部产出（本任务只写文档 + 跑全量回归，不新增接口）。
- Produces: 无代码接口；运维入口为 `docs/deploy.md`「数据源健康告警」小节。

- [ ] **Step 1: docs/deploy.md 新增「数据源健康告警」小节（dx-voice F15/F16）**

spec 是开发文档，不是半夜被叫醒的运维会打开的东西——运维事实必须落在 deploy.md。
小节内容大纲（照此撰写，含可复制命令）：

```markdown
## 数据源健康告警

- 机制：fetch 按源三态（ok/down/permanent）落 `api_source_health` 表；
  `down` 持续 ≥ `SOURCE_HEALTH_ALERT_AFTER_MINUTES`（默认 30 分钟）→ admin Bark；
  恢复 → 「已恢复」通知（故障时长）。送达失败下轮重试直到送达。
  sender 惰性构造：无待发送项时不建 BarkChannel/httpx.Client（eng M2）。
- 已知取舍（eng L2）：告警送达成功但落状态前恰好被并发 fetch 改写时，下一轮会
  重发一次同样的告警（duplicate > silence）——看到重复告警不是 bug，不必追查。
- 检测时机：path_a tick 尾部（开奖日 21:30–01:00 每 15 分钟）+ 每次启动 backfill 尾部。
  **白天无抓取，故障最早在当晚 21:30 窗口发现**——「30 分钟」指进入抓取窗口后的计时。
- 升级注意：旧版本升级的存量行 down_since 为 NULL——部署后需经过一个完整抓取
  周期才开始记录故障起点（dx-voice F2）。
- 面板：/admin/health（admin）返回 status/alerted/error/last_success_at/down_since
  （时间为显式 UTC 'Z' 后缀）。示例：
  `curl -b cookies.txt http://localhost:8280/admin/health | jq .sources`
- 配置：ADMIN_BARK_KEY（通道）、SOURCE_HEALTH_ALERTS_ENABLED（独立开关，默认 true，
  不影响密码重置 admin 通知）、SOURCE_HEALTH_ALERT_AFTER_MINUTES（阈值，默认 30）、
  ADMIN_BARK_URL（自建 Bark 服务端时覆盖）。
- 手工冒烟（dx-voice F1：不等真实故障，直接验证告警链路）：
  SOURCE_HEALTH_ALERT_AFTER_MINUTES=0 重启后执行——
  docker compose exec app uv run python -c "
from app.db.engine import build_engine
from app.config import get_settings
from app.services.source_health import (
    admin_alert_sender_factory, evaluate_source_alerts, record_source_health,
)
eng = build_engine(get_settings().database_url)
record_source_health(eng, 'mxnzp', 'down', 'manual smoke')
evaluate_source_alerts(eng, admin_alert_sender_factory)
print('check your Bark now')"
  预期：手机收到「开奖抓取持续失败」；随后
  record_source_health(eng, 'mxnzp', 'ok') 再评估一次应收到「已恢复」，
  且 /admin/health 该源回到 ok（alerted=none）。
```

- [ ] **Step 2: CLAUDE.md「关键约定」补一行**

在「推送时机」一行后追加：

```markdown
- 数据源健康：fetch 三态落 `api_source_health`（ok/down/permanent→degraded）；down ≥ `SOURCE_HEALTH_ALERT_AFTER_MINUTES`（默认 30min，path_a 窗口内检测）→ admin Bark，恢复再通知；面板 `/admin/health`
```

- [ ] **Step 3: 后端全量**

Run: `uv run pytest -q`
Expected: 全部 PASS（基线 757+1 skipped 之上加本计划新增用例——eng L4 实测
`pytest --collect-only` 为 757（plan-10 落地后的真实基线，plan 原稿 754 过期）；
无既有测试因健康落表/评估接线失败）。

- [ ] **Step 4: Lint**

Run: `uv run ruff check . && uv run lint-imports`
Expected: All checks passed / Contracts kept。

- [ ] **Step 5: 状态确认 + Commit**

Run: `git status --short && git log --oneline -8`
Expected: 工作区干净（.mimosa/ 等本地产物除外）；7 个功能 commit。

```bash
git add docs/deploy.md CLAUDE.md
git commit -m "docs(plan-11): deploy.md 数据源健康告警小节（机制/检测时机/配置/冒烟）+ CLAUDE.md 约定行"
```

---

## Self-Review 记录

- Spec 覆盖：§1.1→Task 1；§1.2→Task 3；§1.3→Task 4+5；§1.4→Task 2；§1.5→Task 6；§1.6（naive UTC）→ Global Constraints + 各 now 参数；§二→Task 5；§三→各任务测试步骤一一对应；§四→Task 1 迁移 + 部署提醒（执行完成后人工步骤，不属代码任务）。
- TDD 顺序：每任务均为「失败测试 → RED 确认 → 实现 → GREEN 确认」；Task 1 的迁移文件在 RED（Step 3）之后（Step 4）。
- 类型一致性：`record_source_health(engine, source, outcome, error, now)` 与 `evaluate_source_alerts(engine, sender_factory, now)`（eng E2 后为惰性工厂签名）在 Task 3/4/5 间一致；`_try_fetch` 三元组在 fetch_and_store 与 _grace_refetch 两处解包一致。

<!-- autoplan-accepted:ceo -->
- M1：Task 4 评估器改两阶段——短 session 读+决策关闭 → session 外 send_alert → 短 session 守卫重读后落转移；绝不在持有 pool_size=1 唯一连接的 session 内做 httpx 调用。验证：Task 4 原 6 用例全绿（接口不变）。
- M2：build_admin_alert 的 `_alert` 在 `result.status != ChannelStatus.SENT` 时 raise RuntimeError（送达契约）；Task 2 补「send 返回 FAILED（非抛异常）→ raise」测试；auth 回归（password_reset_service.py:248-255 既有 try/except 兼容）。
- M3：Task 2 测试 CRYPTO_KEY_V1 用 `Fernet.generate_key().decode()`（test_admin.py:16 范式；'x'*44=33 字节非法）。
- M6：backfill.py 不复制 1.2 常量，`from app.scheduler.jobs import _INTER_LOTTERY_INTERVAL`（已核实无循环 import）；conftest 置 0 同步 patch backfill 模块引用。
- M7：`_grace_refetch` 的 m2 outcome 也写健康表（grace 内恢复不等下周期）。
- M11：`record_source_health` ok 分支——alerted=='none' 且 down 时长 ≥ DOWN_ALERT_AFTER 时置 recovering（保留 down_since）补发恢复通知，不得静默清零；补「长故障零送达→recovering」「短故障→清除」两测试。
- M13：`record_source_health` 落表前对 error 脱敏（key=/app_id=/app_secret=/token= query 参数值替换 [REDACTED]）；补脱敏测试。
- M4/M5（spec 修订，收尾执行）：写明真实告警 SLA（下一抓取窗口 + 30min；白天无抓取不检测）+ 30min 阈值推导；同步 §1.2（M7/M11/M13）、§1.3（两阶段）、§1.4（送达契约）、§二（M6 单一真值源）。
- Taste（呈 Phase 4 门，未并入）：T1 恢复补抓（荐 defer 11b）、T2 结果口径告警（荐 defer 11b）、T3 白天探针（荐不做）、T4 单源可见性（荐面板+README）、T5 告警疲劳管理（荐 TODOS P2）、T6 /health 实时数据+外部探针（荐 TODOS P2）。
<!-- /autoplan-accepted:ceo -->

<!-- autoplan-accepted:design -->
- D-1：Admin.vue 补 `.source-status.down`（--danger 文字 + 既有红底）、`.degraded`（--warning + 琥珀底）、`.unknown`（--muted + surface-2 底）三类 pill——健康四态不再有裸奔状态；文字色一律 DESIGN.md 语义 token。
- D-2：`.source-meta { color: var(--muted); font-size: var(--text-xs); }`——用 tokens.css 既有 --muted（含 dark 变体），禁止 --vt-c-text-2（不存在）与硬编码 #888。
- D-3：后端 `_iso_utc()` 对 naive UTC 追加 'Z'（显式 UTC）；前端 `fmtDuration()` 展示**时长**（已故障 40 分钟 / 最后成功 3 小时前，时区免疫，兼作新鲜度信号）；推送体删除 UTC 括号（与同句分钟数矛盾）。test_admin_system_health 补 endswith('Z') 断言。验证：tests/api/test_admin.py 全绿。
- D-4：`.source-item` 加 `flex-wrap: wrap; row-gap: 4px`（375px 防挤压）。验证：npm run build + vitest 全绿。
- D-5（voice D5）：告警体含备源状态——全 ok→「备用源正常，开奖未受影响」；有 down→「双源同时故障，开奖可能延迟入库」；unknown/degraded→如实列状态+「可能延迟入库」；备源测试断言「开奖未受影响」与无「（自」。
- D-6（voice D6）：恢复通知体诚实声明缺口——path_a 轮询 + 启动回填（最近 2 天）覆盖范围，更长缺口请人工确认。
- D-7（voice D7）：Task 3 补 `test_record_permanent_on_fresh_row_keeps_unknown`（部署首日 juhe 未配 key 的真实路径）。
- D-8（voice D4/D17）：/admin/health 同返 `alerted` + `error`（后端 `_truncate` 截断 200 + '…'）；面板 alerted 渲染 已通知/恢复待通知 pill，error 进 meta（title 悬浮全文）。
- D-9（voice D2/D10）：Task 6 模板改插入式表述（v-if/v-else 逐字节不动）；Admin.test.ts 补 2 用例（down 行全要素断言 + 空态回归）。
- D-13（voice D13，taste 呈门）：非 ok 时健康状态置顶 global-error banner——荐 defer（理由：admin 已熟知卡片位置，banner 属增强）。
- Mockups 跳过：UI delta 为既有组件内信息 span/tag，无新布局/组件；比较板交互与 autoplan 单门纪律冲突（审计 #13）。
<!-- /autoplan-accepted:design -->

<!-- autoplan-accepted:dx -->
- DX-1（F1+F19+F2）：Task 7 新增 deploy.md「数据源健康告警」小节——机制/检测时机（含白天不检测的真实 SLA）/升级冷启动说明（存量行 down_since NULL 需一个抓取周期）/配置四项/面板 curl 示例/手工冒烟 runbook（阈值置 0 → seed down → evaluate → 收 Bark → ok 恢复 → 面板回 ok）；CLAUDE.md 关键约定补一行。验证：文档含可复制命令且与实现一致（runbook 函数签名 = evaluate_source_alerts(engine, send_alert)）。
- DX-2（F3+F11+F9）：`Outcome = Literal['ok','down','permanent']` + 模块注释 outcome→status 映射表；permanent 分支置 `status='degraded'` 且清 `down_since`（不再是「其余不动」——spec §1.2 随 M4/M5 同步改）；测试改写 permanent×2（degraded 语义）+ 新增 down→permanent 不告警回归（放 Task 4 测试段，依赖 _Recorder）。
- DX-3（F6）：`evaluate_source_alerts` 在 send_alert=None 时不发送也**不转移**（两分支同），每次评估 `source_alerts_disabled` warning 一次——spec §1.1 alerted=「已送达」语义自洽；`test_no_sender_keeps_state_and_logs` 取代 `test_no_sender_still_transitions`。
- DX-4（F18/F20）：config.py 增 `source_health_alerts_enabled: bool = True`、`admin_bark_url: str = 'https://api.day.app'`、`source_health_alert_after_minutes: int = 30`；jobs/backfill 接线按开关传 sender=None；build_admin_alert 与 main.py:145 的 url 同源 settings.admin_bark_url；test_config.py 补默认值测试。
- DX-5（F10+F12 轻量）：告警体补 fix 指引（检查 NAS 网络/DNS 与上游 → /admin/health → docs/deploy.md 小节）；send 失败日志 warning→error（重试语义不变）。
- DX-6（F7/F8/F21/F17）：`_record_health(self, source_name: str, ...)` 去双重 str()；`_alert` raise 用 `result.error or '未知原因'`；`_NowFn`→`NowFn` 导出；Task 5 测试片段显式 import 说明（register_all_jobs/_invoke_job/_make_deps）。
- 拒绝：F4（alerted→alert_state 改名，7 处 churn 换品味级收益）。
- 先前轮次覆盖声明（本 block 替代关系）：ceo M11 的 `DOWN_ALERT_AFTER` 常量由 settings 驱动的 `_down_alert_after()` 取代（F19）；design D-3 已关闭 dx F14（推送体无 UTC 绝对时间）；design D-8/D-17 已关闭 dx F5/F13。
<!-- /autoplan-accepted:dx -->

<!-- autoplan-accepted:eng -->
- E1（eng C1 必修）：迁移 `down_revision='d1_draw_costs'`（真实 head；plan 原稿 fix_prize_amount_cents 会产生双 head → Dockerfile:79 `alembic upgrade head` 报 Multiple head revisions → 容器启动失败）；新增 `test_alembic_single_head` 守卫（alembic heads 单 head 断言入迁移测试文件）；plan 文本 head 声明同步修正。验证：`uv run alembic heads` 单 head + 迁移测试 2 passed。
- E2（eng H1）：评估器签名 `send_alert` → `sender_factory` 惰性工厂——无待发送项时绝不调用（不碰 get_settings/httpx）；三个调度测试不再因 conftest 删 JWT_SECRET/CRYPTO_KEY_V1 而 ValidationError 吞评估；需直调工厂的测试按 test_admin.py:15-18 范式补 env。验证：tests/scheduler/ 套件全绿。
- E3（eng H2）：迁移测试断言 quoted 字面量（"'none'" in str(default)）+ nullable=False + 缺省插入回读——inspect() 返回 DDL 带引号文本 "'none'"，`== 'none'` 两个分支永假（SQLite 实测）。
- E4（eng M2，取代 CEO 轮 M12 LOW 不处理裁决）：sender 工厂模式消除每 tick 新建未关闭 httpx.Client——无 singleton、无 close 生命周期、无 conftest 耦合；`test_factory_not_called_when_nothing_pending` 回归。
- E5（eng M3a）：模板时长文案按 `status !== 'ok'` 门控（recovering 的 ok 行不再显示「已故障 N 天」）；recovering→down 翻 `alerted='none'` + down_since 重置为新 episode（修 M11 路径「已通知」谎言；重告警由送达门控决定，duplicate > silence）；改写 test_record_down_cancels_pending_recovery 期望 + 补 test_redown_after_recovering_realerts。
- E6（eng M3b）：permanent 分支同时 `alerted='none'`（配置态终结运行故障 episode，不欠恢复通知；degraded 行不再卡「恢复待通知」）；补 test_record_permanent_clears_recovering。
- E7（eng M4）：`_sanitize_error` → 公开 `sanitize_error`；`_fetch_with_backoff` 日志经脱敏（juhe key 不进容器日志——M13 只堵了落表路径，日志是同 NAS 上的同级泄露面）；补 caplog 脱敏回归测试。
- E8（eng M5）：Task 6 vitest 用仓库既有 `mount(overrides)` → host 惯例（querySelectorAll/classList/textContent），不引入 @vue/test-utils（非本仓库依赖）。
- E9（eng M6）：弃后端 `_truncate`——API 返回完整 error，前端 `fmtError(80)` 截断+'…' 内联、title 悬浮全文（后端截断会让 title 也只有截断版，D17「全文可见」失效）；补 300 字符穿透 + title 全文 + `_iso_utc(None)` 边界测试。
- E10（eng M7）：`_backfill_history` 的 `time.sleep(1.2)` → `_INTER_LOTTERY_INTERVAL`（M6 单一真值源补完最后一份拷贝）；修正 backfill import 说明（time/get_settings 已在文件头第 3/10 行）。
- E11（eng M8）：补测试——健康写失败不阻断抓取（M8.1）、双源同时故障/备源 degraded 告警体分支（M8.4）、admin_bark_url 注入 Bark config（M8.6）。
- E12（eng L1/L3/L4）：test_admin_system_health 按 source 索引（不依赖 SQLite 返回序）；_iso_utc 兼容 aware 输入（先归一 naive UTC 再加 'Z'）；Task 7 基线 754→757（collect-only 实测）；CEO 轮锚点清单「alembic head=fix_prize_amount_cents」声明已过期，以 eng 核实（d1_draw_costs）为准。
- E13（eng L2）：评估器 docstring + deploy.md 写明「送达后落转移前状态被并发改写 → 下轮重发一次同样告警（duplicate > silence）」，避免运维误当 bug 追查。
- 覆盖声明（本 block 对前轮次结论的替代关系）：CEO 轮 M12（httpx.Client 每 tick 新建，LOW 不处理）由 E4 工厂设计取代；design D-8「后端 _truncate 截断 200+'…'」由 E9 前端截断取代；dx DX-1 runbook 签名 `evaluate_source_alerts(engine, send_alert)` 由 E2 工厂签名取代。
<!-- /autoplan-accepted:eng -->
## Review record

### Phase 1: CEO Review（模式：SELECTIVE EXPANSION，2026-09-15，[subagent-only]）

> 声音覆盖：Claude subagent 完成（17 项发现 F1-F17）；Codex 不可用（not_installed）→ 共识列 N/A。
> 主审查已对子代理全部关键锚点逐一代码核实（fetch_service.py:110-200、jobs.py:26/66-94/166-288、backfill.py:19/52-59、auth.py:220-243、admin.py:79-82、engine.py:10-20、setup.py:23-37、bark.py:30/49-50、juhe.py:27-29、main.py:181-196/379-403、conftest.py:14-26、Admin.vue:19-22/645-648、Admin.test.ts:36、Dockerfile:60、alembic head=fix_prize_amount_cents）。锚点全部属实（行号 ±2 漂移）。

#### 系统审计
- main 领先 origin/main 2 commits（spec + 本 plan，均 docs）。无 TODOS.md（本次创建）、无 stash。
- 既有可复用：`_build_admin_alert`（auth.py:230-243）；`_INTER_LOTTERY_INTERVAL=1.2`（jobs.py:26）；`_BACKFILL_LOOKBACK_DAYS=2`（backfill.py:19）；`_data_source_state` + `/health`（main.py:181-196/379-403）；Docker HEALTHCHECK（Dockerfile:60）。
- 本仓库两次 pool_size=1 嵌套 session 实测事故（jobs.py:276-278/339、password_reset_service.py:131/204）→ 直接命中本 plan Task 4（见 M1）。
- 学习库命中 `design-doc-contradiction-after-review`：审查决策改变设计假设后须回扫 spec → M4/M5/M11/M13 全部落 spec 修订（收尾任务执行）。

#### 0A 前提质询
| 前提 | 裁决 |
|---|---|
| 「持续失败 30 分钟 → admin 告警」是正确问题 | 成立，但只覆盖事故一半：只修「发现」未修「补回」——9 天故障恢复后 `_BACKFILL_LOOKBACK_DAYS=2` 只补 2 天，其余 7 天永不比对（F2，3 注漏通知复现路径）→ T1 呈门 |
| 30 分钟阈值 | 数值合理但 spec 无推导（对齐晚间窗口：21:30 首败 → 22:00 告警，早于 01:00 末轮与 07:00 汇总，留有反应时间）→ M5 spec 补推导 |
| 评估挂载点足够 | **部分错误**：抓取只在 21:30-01:00 发生（jobs.py:66-94 cron），白天故障要到当晚 21:30 才首次写 down_since——「30 分钟级」仅晚间成立，最坏检测延迟 ~21h（F4 属实）→ M4 spec 写明真实 SLA；白天探针 → T3 呈门 |
| juhe 不可用是长期事实（permanent 不告警） | 本部署成立，但 MIT 开源后多数自部署者单源，「交叉校验未启用」永不可见（F5）→ T4 呈门 |
| Task 2「函数原样迁移」 | **错误（plan 内部矛盾）**：`BarkChannel.send` 吞掉一切失败返回 SendResult 而非抛异常（bark.py:49-50），原样迁移使 Task 4「送达成功才转移」退化为「尝试即转移」——DNS 事故（告警通道同挂）场景下告警永不重试 → **M2 必修** |
| backfill 复制 1.2 常量（「循环依赖」） | **理由不成立**：已核实 jobs.py 不 import backfill，无环 → **M6** 单一真值源 |

#### 0B 既有代码复用图
| 子问题 | 既有实现 | plan 处置 |
|---|---|---|
| admin 告警通道 | `_build_admin_alert`（auth.py:230-243） | 迁出共用 ✓（M2 修送达契约） |
| QPS 间隔 | `jobs._INTER_LOTTERY_INTERVAL`（jobs.py:26） | M6：直接 import，不复制 |
| 单源可见性 | `_data_source_state` + `/health.data_sources`（main.py:181/403） | T4 复用（呈门） |
| 外部探活 | `/health` + HEALTHCHECK（main.py:379、Dockerfile:60） | T6 defer |
| 恢复补抓 | `_backfill_history`/`_has_draw_for_date`/recompare CLI | T1 defer plan-11b |

#### 0C 梦想态
```
CURRENT                       THIS PLAN                      12-MONTH IDEAL
源故障零告警；健康表有读无写； → 传输故障 ≤1 窗口+30min 告警    → 结果口径告警（任何漏抓成因）
恢复只补 2 天；启动回填撞 QPS   （送达才转移）；健康表实时；     + 恢复自动补抓比对全窗口
                                面板可见；回填限流               + 用户侧「未能核对」可见
                                                                + 外部黑盒探针兜底整机故障
```
Dream state delta：本 plan 补上「传输层发现」与「限流」两块短板；「补回」「非传输成因发现」「用户可见性」「整机故障兜底」四块留给 11b/TODOS（见 T1/T2/F3/T6）。

#### 0C-bis 实现路线
- **APPROACH A（plan 现状 + 机械修正）**：传输健康告警 + 面板 + 回填间隔。Effort S / Risk Low。Pros：今天可上、直击事故两成因。Cons：非传输漏抓成因（交叉校验不一致/休市）无告警；恢复不补抓。Completeness 7/10。
- **APPROACH B（A + 结果口径评估 + 恢复补抓）**：Effort M-L / Risk Med。Pros：覆盖全部漏抓成因，事故两半都修。Cons：适配器需按日期抓取能力、休市误报需设计、plan 膨胀 2-3 倍。Completeness 10/10。
- **APPROACH C（仅外部黑盒监控）**：Effort S / Risk Low。Pros：覆盖整机故障。Cons：不覆盖应用内漏抓；依赖外部服务。Completeness 4/10。
- **裁决：A 现在合入（含 M1/M2/M11/M13 机械修正）；B 作为 plan-11b 紧随其后（T1/T2 呈门）；C 记 TODOS（T6）。** 依据 P1+P2：A 的完整版先落地，B 不稀释本 plan 的可发布性。

#### 0D 范围决策（SELECTIVE EXPANSION）
- 接受（机械，爆炸半径内 <1d）：M1 M2 M3 M6 M7 M11 M13 + spec 修订 M4/M5。
- 呈门 taste：T1 恢复补抓（荐 defer 11b）、T2 结果口径告警（荐 defer 11b）、T3 白天探针（荐不做）、T4 单源可见性（荐面板+README）、T5 告警疲劳管理（荐 TODOS）、T6 /health 实时数据+外部探针（荐 TODOS）。
- User Challenge：无（Codex 不可用，不存在双模型一致反对用户方向的信号）。

#### 0E 时序质询
- H1（迁移）：`server_default='none'` 覆盖存量行 ✓；迁移测试镜像 fix_prize_amount 范式（含 `Fernet.generate_key()`）✓。
- H2-3（写表/状态机）：实现者必撞两个暗坑——bark.send 不抛异常（M2 已修+补测试）、session 内发 HTTP 撞 pool_size=1（M1 已修为两阶段）。
- H4-5（接线）：conftest 置 0 只需处理 jobs 常量 + backfill 的 import 引用（M6）；grace 重抓补写健康（M7）。
- H6+（回归）：既有 fetch/scheduler/admin 套件不受影响（独立表、独立短事务、spy 断言）。

#### 11 节深审
1. **架构**：新模块 services/source_health 符合分层（domain 零 IO 不受影响，lint-imports 兜底）。状态机 none→alerted→recovering→none 设计良好，M11 补上「none + 超时故障恢复 → recovering」缺失边。**发现 M1**（评估器 session 内做 HTTP，撞 pool_size=1 纪律，同型事故两次）。其余无问题。
2. **错误与救援**：registry 见下表。**发现 M2**（SendResult 吞咽 → 送达契约失效）、**M13**（error 含 juhe key 外流路径）。plan 的 `_record_health` / tick 尾评估两处 `except Exception` 属有意兜底（exc_info 留痕、护主流程），符合 silent-failure 纪律，接受。
3. **安全**：**发现 M13**：juhe.py:27 key 在 query string，`raise_for_status` 异常消息含完整 URL → str(exc) 经 `_try_fetch` → 健康表 error → 面板 + Bark 告警体（第三方服务器）。plan 新建了 key 外泄路径。/admin/health 维持 admin-only ✓；无新端点；告警体截断 200 ✓。（既有日志同源泄露为存量问题，单独 flag。）
4. **数据流/边界**：record 四路径——首行 upsert ✓（有测试）、error=None ✓、DB 写失败吞+日志 ✓、并发（启动 backfill 与 21:30 tick 不重叠 + 单进程单连接串行）✓。状态机抖动边（recovering 再 down → alerted）✓ 有测试。**发现 M7**：grace 重抓恢复不落表，恢复通知延迟一整轮。面板空态/null ISO ✓。
5. **代码质量**：**发现 M6**（常量复制，「循环依赖」理由不成立）。命名/注释与仓库一致 ✓。
6. **测试**：矩阵良好（写表 5 + 状态机 6 + 接线 3 + 迁移 1 + admin 1）。**缺口**：M2 的「send 返回 FAILED（非异常）不转移」（生产真实失败形态）、M11 的「长故障零送达 → 恢复补发」、M13 的脱敏用例——已随机械修订补入 Task 2/3。
7. **性能**：每 tick 每源一次短写、评估全表 ≤4 行，可忽略。M12（每 tick 新建 httpx.Client 不 close）LOW——GC 兜底，与 auth.py 存量同型，不处理。
8. **可观测**：本 plan 即观测性本体；结构化日志（write_failed/send_failed/evaluate_failed）齐备。F7（健康写入自监控）→ TODOS P3：递归层级过深，现有 warning 日志已留痕，边际价值低。
9. **部署**：迁移加列 nullable + server_default，向后兼容 ✓；容器启动 auto upgrade ✓；回滚 = revert + downgrade 可用 ✓。部署后首个 tick 即写表，面板立即可见 ✓。
10. **长期**：可逆性 4/5。债务已书面化（TODOS.md：F1/F2 恢复补抓与结果口径、F9/F10 疲劳管理、F13 外部探针、F3 用户侧可见性、F15 多通道、F7 自监控、F16 指标化）。1 年后新工程师凭 spec 事故背景可懂 ✓。
11. **设计/UX**（UI scope）：面板加「故障自/最后成功」时间戳是诚实展示，F11 的「status 长期 ok 骗人」被 last_success_at 部分对冲；源名→元信息→状态的既有行模式不乱 IA；空态/loading 既有 ✓。Phase 2 设计审查照跑。

#### Error & Rescue Registry
| 方法/路径 | 可能故障 | 异常类 | 救援 | 用户/运维看到 |
|---|---|---|---|---|
| `_try_fetch` | 网络/DNS/限流耗尽 | httpx.* → 归 outcome='down' | 健康表落 down + 日志 | 面板 down；≥30min Bark |
| `_try_fetch` | key 未配/schema 变更 | PermanentLookupError → 'permanent' | 仅记 error（不告警） | 面板 error 列 |
| `_record_health` | DB 写失败 | Exception → 吞 + warning(exc_info) | 不阻断抓取 | 日志 |
| `evaluate_source_alerts` | Bark 发送失败 | RuntimeError(M2)/Exception → 不转移 | 下轮重试至送达 | 日志；面板状态不变 |
| `evaluate_source_alerts`（M1 两阶段后） | 读-落间并发状态变化 | —（守卫重读） | 放弃本轮转移，下轮重评 | 无 |
| `send_alert`（M2 后） | bark HTTP 错/业务码非 200/传输异常 | RuntimeError | 调用方 except 捕获 | 告警下轮重试 |
| tick 尾评估 | 评估整体异常 | Exception → 吞 + error(exc_info) | 不阻断 tick 收尾 | 日志 |
| backfill 单彩种 | fetch 异常 | Exception → 吞 + error(exc_info) | 不阻断其他彩种 | 日志 |

#### Failure Modes Registry
| 路径 | 故障模式 | 救援? | 测试? | 用户看到 | 日志? |
|---|---|---|---|---|---|
| 健康落表 | 写库失败 | Y(吞+日志) | N（低价值，F7→TODOS） | 面板停旧值 | Y |
| 故障告警 | 发送失败 | Y(不转移+重试) | Y（M2 补 FAILED 用例） | 延迟收到 | Y |
| 恢复通知 | 发送失败 | Y(保持 recovering) | Y | 延迟收到 | Y |
| 长故障+通道同挂（M11 前） | 恢复时静默清零 | **N→Y（M11 修）** | Y（M11 补） | 9 天事故零通知 → 恢复补发 | Y |
| 状态转移 | 并发读写竞争 | Y（M1 守卫） | 间接（接口不变，原 6 用例） | 无 | Y |
| error 落表 | 含 juhe key | **N→Y（M13 脱敏）** | Y（M13 补） | 密钥经 Bark 外泄 → 阻断 | — |
| grace 重抓 | 恢复不落表（M7 前） | **N→Y（M7 修）** | 既有 grace 套件回归 | 恢复通知延迟 15min → 消除 | — |

CRITICAL GAPS（修订前 3，全部已随机械修订闭环）：M1 session 内 HTTP、M2 送达契约、M11 静默清零、M13 密钥外泄（4 项）。

#### NOT in scope（已书面化）
- T1 恢复补抓 [down_since, now] → 荐 plan-11b（需适配器按日期抓取 + 休市处理，超爆炸半径）—呈门
- T2 结果口径告警（应开奖日无 verified DrawResult）→ 荐 plan-11b 与 T1 同做 —呈门
- T3 白天探测性抓取 → 荐不做（配额消耗，晚间才是承诺兑现期；SLA 已写明 M4）—呈门
- T4 单源可见性（面板标识 + README）→ 荐接受小版 —呈门
- T5 告警疲劳管理（冷却 + 24h 升级重发，F9/F10）→ TODOS P2（随 11b 设计）—呈门
- T6 /health 实时源健康 + 外部探针（F13）→ TODOS P2（未鉴权端点暴露面 + 部署层决策）—呈门
- F3/F14 用户侧「未能核对」可见性 → TODOS P2（改通知语义，大设计）
- F15 多通道 admin 告警 → TODOS P3（spec 非目标，单通道 + 送达重试已满足当下）
- F7 健康写入自监控 → TODOS P3（递归层级过深，日志已留痕）
- F16 源稳定性指标化 → TODOS P3
- F8 30min 阈值推导 → 不做任务，M5 已写进 spec
- F17 护城河论（可验证可信结果是差异点）→ 战略备注，无代码行动

#### What already exists
见 0B 复用图——admin 告警构造、QPS 间隔常量、单源三态判定、/health 探活端点、历史回填与 recompare CLI 均已存在并复用，无重复造轮子（M6 修正了唯一一处复制）。

#### CEO DUAL VOICES — CONSENSUS TABLE
```
═══════════════════════════════════════════════════════════════
  Dimension                            Claude(sub) Codex  Consensus
  ──────────────────────────────────── ─────────── ─────── ─────────
  1. Premises valid?                   挑战(F4-6)   N/A     N/A（外部不可用）
  2. Right problem to solve?           是(但半截F1/F2) N/A  N/A
  3. Scope calibration correct?        低估补回半   N/A     N/A
  4. Alternatives sufficiently explored? 否(F13)    N/A     N/A
  5. Competitive/market risks covered? F17          N/A     N/A
  6. 6-month trajectory sound?         F9/F10 风险  N/A     N/A
═══════════════════════════════════════════════════════════════
```
外部声音不可用（Codex not_installed）→ 六格 N/A，非 CONFIRMED。subagent 与主审查一致：F6（M1）、F4（M4）、F12（M6 同旨）。分歧（→ 呈门）：F1/F2 处置（subagent 主并入本 plan；主审查荐 defer 11b）；F5 处置（subagent 启动通知；主审查荐面板+README）。主审查独有：M2、M11、M13（子代理未发现的送达契约/静默清零/密钥外泄三坑）。单声 critical 已逐一代码核实。

#### Completion Summary
```
+====================================================================+
|            MEGA PLAN REVIEW — COMPLETION SUMMARY                   |
+====================================================================+
| Mode selected        | SELECTIVE EXPANSION                         |
| System Audit         | 锚点全核实；两次 pool_size=1 事故命中本 plan |
| Step 0               | 路线 A 现做 + B=plan-11b；6 taste 呈门       |
| Section 1  (Arch)    | 1 issue（M1）                                |
| Section 2  (Errors)  | 8 错误路径映射，3 GAPS（M2/M11/M13）         |
| Section 3  (Security)| 1 issue（M13，Med 密钥外泄路径）             |
| Section 4  (Data/UX) | 4 路径追踪，1 未处理（M7）                   |
| Section 5  (Quality) | 1 issue（M6 DRY）                            |
| Section 6  (Tests)   | 图已产，3 缺口（随 M2/M11/M13 补入）         |
| Section 7  (Perf)    | 0 issue（M12 LOW 不处理）                    |
| Section 8  (Observ)  | 0 gap（F7→TODOS P3）                         |
| Section 9  (Deploy)  | 0 risk                                       |
| Section 10 (Future)  | 可逆性 4/5，债务 7 项已书面化                |
| Section 11 (Design)  | 1 issue（F11 部分对冲，不阻断）              |
+--------------------------------------------------------------------+
| NOT in scope         | written（11 项）                             |
| What already exists  | written（0B 复用图）                         |
| Dream state delta    | written                                      |
| Error/rescue registry| 8 方法，4 CRITICAL GAPS（已闭环）            |
| Failure modes        | 7 项，4 CRITICAL（已闭环）                   |
| TODOS.md updates     | 7 项（新建 TODOS.md）                        |
| Scope proposals      | 6 proposed, 0 auto-accepted (taste→gate)     |
| CEO plan             | written（ceo-plans/2026-09-15-source-health-alert.md）|
| Outside voice        | codex: unavailable；claude subagent: completed(17 findings) |
| Lake Score           | 8/8 机械修订均选完整版（无捷径采纳）         |
| Diagrams produced    | dream-state、状态机（含 M11 边）、两阶段评估 |
| Stale diagrams found | 0                                            |
| Unresolved decisions | 6（全部 taste，呈 Phase 4 门）               |
+====================================================================+
```

<!-- autoplan-accepted:ceo -->
- M1：Task 4 评估器改两阶段——短 session 读+决策关闭 → session 外 send_alert → 短 session 守卫重读后落转移；绝不在持有 pool_size=1 唯一连接的 session 内做 httpx 调用。验证：Task 4 原 6 用例全绿（接口不变）。
- M2：build_admin_alert 的 `_alert` 在 `result.status != ChannelStatus.SENT` 时 raise RuntimeError（送达契约）；Task 2 补「send 返回 FAILED（非抛异常）→ raise」测试；auth 回归（password_reset_service.py:248-255 既有 try/except 兼容）。
- M3：Task 2 测试 CRYPTO_KEY_V1 用 `Fernet.generate_key().decode()`（test_admin.py:16 范式；'x'*44=33 字节非法）。
- M6：backfill.py 不复制 1.2 常量，`from app.scheduler.jobs import _INTER_LOTTERY_INTERVAL`（已核实无循环 import）；conftest 置 0 同步 patch backfill 模块引用。
- M7：`_grace_refetch` 的 m2 outcome 也写健康表（grace 内恢复不等下周期）。
- M11：`record_source_health` ok 分支——alerted=='none' 且 down 时长 ≥ DOWN_ALERT_AFTER 时置 recovering（保留 down_since）补发恢复通知，不得静默清零；补「长故障零送达→recovering」「短故障→清除」两测试。
- M13：`record_source_health` 落表前对 error 脱敏（key=/app_id=/app_secret=/token= query 参数值替换 [REDACTED]）；补脱敏测试。
- M4/M5（spec 修订，收尾执行）：写明真实告警 SLA（下一抓取窗口 + 30min；白天无抓取不检测）+ 30min 阈值推导；同步 §1.2（M7/M11/M13）、§1.3（两阶段）、§1.4（送达契约）、§二（M6 单一真值源）。
- Taste（呈 Phase 4 门，未并入）：T1 恢复补抓（荐 defer 11b）、T2 结果口径告警（荐 defer 11b）、T3 白天探针（荐不做）、T4 单源可见性（荐面板+README）、T5 告警疲劳管理（荐 TODOS P2）、T6 /health 实时数据+外部探针（荐 TODOS P2）。
<!-- /autoplan-accepted:ceo -->

<!-- AUTONOMOUS DECISION LOG -->
## Decision Audit Trail

| # | Phase | Decision | Classification | Principle | Rationale | Rejected |
|---|-------|----------|-----------|-----------|----------|
| 1 | CEO | 模式=SELECTIVE EXPANSION | Mechanical | 覆盖层默认（功能迭代于既有系统） | 事故跟进的可靠性补丁，hold 基线 + 逐项 cherry-pick | — |
| 2 | CEO | 路线 A 现做 + B=plan-11b（0C-bis） | Mechanical | P1 完整 + P2 湖泊 | A 完整版今天可发布；B 需适配器按日期抓取设计，并入会使 plan 膨胀 2-3 倍 | B 并入本 plan（膨胀）、C 单独（不覆盖应用内漏抓） |
| 3 | CEO | M1 评估器两阶段（send 移出 session） | Mechanical | P5 显式 + 仓库纪律 | pool_size=1 同型事故两次实测（jobs.py:276-278）；告警机制不得制造它要防的漏通知 | plan 原稿（session 内发送） |
| 4 | CEO | M2 `_alert` 未送达 raise | Mechanical | 满足 spec 明示要求 | bark.send 永不抛异常（bark.py:49-50），不 raise 则「送达才转移」退化为「尝试即转移」 | 原样迁移（plan 内部矛盾） |
| 5 | CEO | M3 测试用合法 Fernet key | Mechanical | 仓库既有范式 | test_admin.py:16 同型；'x'*44 解码 33 字节非法 | 'x'*44 |
| 6 | CEO | M6 常量单一真值源 | Mechanical | P4 DRY | jobs.py 不 import backfill 已核实，plan「循环依赖」理由不成立；复制必漂移 | 复制 + 注释约束 |
| 7 | CEO | M7 grace 重抓落健康 | Mechanical | P1 完整性 | grace 内恢复的源否则等下周期才 ok，恢复通知延迟 15min | 不写（plan 原稿） |
| 8 | CEO | M11 长故障零送达→recovering 补发 | Mechanical | spec DNS 教训直接推论 | 故障期通道同挂 → 恢复时静默清零 → 9 天事故零通知，状态机缺边 | 静默清零（spec/plan 原语义） |
| 9 | CEO | M13 error 落表前脱敏 | Mechanical | 安全底线 | juhe key 在 query（juhe.py:27）+ raise_for_status 含 URL → plan 新建 key→Bark 外泄路径 | 原样落表 |
| 10 | CEO | M4/M5 spec 写真实 SLA + 阈值推导 | Mechanical | 诚实文档 | 「30 分钟级」仅晚间成立（jobs.py:66-94 cron 已核实）；阈值对齐晚间窗口有推导 | 模糊表述 |
| 11 | CEO | F7/F15/F16/F3 记 TODOS（P2/P3） | Mechanical | P3 务实 + YAGNI | 递归自监控层级过深；多通道/指标化/用户侧可见性超出本 plan 承诺 | 并入本 plan |
| 12 | CEO | T1/T2/T3/T4/T5/T6 六项 taste 呈门 | Taste | 呈门规则（近 approach/边界范围） | 用户有模型没有的上下文（休市安排、外部监控现状、面板使用频率） | 静默自决 |

### Phase 2: Design Review（UI scope：Admin.vue 数据源健康卡，2026-09-15，[subagent-only，迟延对账完成]）

> 声音覆盖：Codex 不可用（not_installed）。Claude design subagent 派发后迟延回传（初判失控按 unavailable 关闭首轮；报告后经 SendMessage 送达，INPUT 哈希匹配 eaa1d79f ✓，17 项发现 D1-D17）——**迟延对账已完成**：重合项（voice D1/D8/D9 ↔ 主审查 D-1/D-2/placement）已闭环；新发现 8 项并入 Task 3/4/6（见下）；D13 呈门；D14/D15/D16 低值不处理。
> Mockups：DESIGN_READY 但**跳过生成**——UI delta 是既有已上线组件（source-item 行）内加信息 span/tag，零新组件；比较板 + 反馈环要求中途打断用户，与 autoplan 单门纪律冲突（决策记录见审计 #13）。
> 主审查已核对实物：Admin.vue:19-22/378-380/395/644-650/948-990、Admin.test.ts:35-36（stubApi 支持 overrides.health）、tokens.css:8/59（--muted 双主题）、docs/designs/DESIGN.md（设计系统单一事实源，105 行）。

#### Step 0: Design Scope
- 初始评分 **4/10**：UI 说明只有一段模板片段 + 「样式从简」，时间戳直接切 ISO 字符串展示（时区错误），状态色只有 ok/error 两类而健康状态机有四态。
- 10/10 的样子：四态各有 token 对齐的视觉、显式 UTC→CST 的正确时间展示、次要信息用 --muted、窄屏不挤压。
- 分类器：**OPERATE（App UI）**——admin 运维面板，冷静表面层级、工具语言。
- DESIGN.md 存在（docs/designs/DESIGN.md，9 页 prototype 反向提取的单一事实源）→ 全部设计裁决按其 token 校准。

#### 7 Passes（含 design-voice 迟延对账）
1. **信息架构 7→9**：行内层级 源名 → meta（时长+error，--muted）→ 告警标签 → 状态 pill。voice D13（卡片埋在第 4 位/建议非 ok 时置顶 global-error banner）→ **呈门**（荐 defer：admin 已熟知卡片位置，banner 提升属增强非信任必需）。
2. **交互状态 5→9**：D-1 状态色补齐（voice D1 同）。**voice D2**（代码块未含 v-if/v-else 上下文，粘贴即删空态）→ 模板改插入式表述 + 空态回归测试。**voice D7**（permanent 新行 → unknown 灰砖无解释）→ Task 3 补新行测试 + error 进面板（D4）解释原因。
3. **用户旅程 7→9**：3am 被叫 → 开面板 → 读行。**voice D3**（面板 UTC 切片 + 推送体裸 datetime，同句两事实差 8h）→ 时长相对化（`已故障 40 分钟`，时区免疫，且自愈 D11 陈旧度问题——时长本身即新鲜度信号）；推送体删 UTC 括号。**voice D5**（单源故障告警读起来像已漏开奖）→ 告警体加备源状态（正常→「开奖未受影响」；同挂→「双源同时故障」；非健康→如实列状态）。**voice D6**（恢复通知闭环不到缺口）→ 恢复体加诚实声明（path_a+启动回填覆盖范围 + 更长缺口人工确认）。
4. **AI Slop 9**：OPERATE 表面、零装饰；无 blacklist 命中。
5. **设计系统对齐 4→9**：D-2（--muted token，voice D8 同）；D-3 后端 'Z' 显式 UTC。**voice D4**（面板只加最次有用的两列，漏 alerted/error——M13 注释说 error 进面板而 Task 6 没给）→ 响应补 alerted + error（截断+…，voice D17）；alerted 渲染 已通知/恢复待通知。
6. **响应式/无障碍 5→8**：D-4 flex-wrap；状态文字+颜色双编码 ✓；--muted ≥4.5:1 ✓。**voice D10**（健康卡零断言）→ Task 6 Step 5 补 2 个 vitest 用例（down 行全要素 + 空态回归）。
7. **未决设计决策**：voice D13 呈门（荐 defer）；D14（uppercase 源名 vs 小写 DB/push——既有 pattern）、D15（英文 enum vs 中文页——ops 惯例与日志/Bark 文案一致）、D16（源排序——2 行数据无足轻重）低值不处理。

#### DESIGN OUTSIDE VOICES — LITMUS SCORECARD
```
═══════════════════════════════════════════════════════════════
  Check                                    Claude  Codex  Consensus
  ─────────────────────────────────────── ─────── ─────── ─────────
  1. Brand unmistakable in first screen?   N/A*    N/A    N/A
  2. One strong visual anchor?             N/A*    N/A    N/A
  3. Scannable by headlines only?          NO→fix  N/A    N/A
  4. Each section has one job?             YES     N/A    N/A
  5. Cards actually necessary?             YES     N/A    N/A
  6. Motion improves hierarchy?            N/A*    N/A    N/A
  7. Premium without decorative shadows?   N/A*    N/A    N/A
  ─────────────────────────────────────── ─────── ─────── ─────────
  Hard rejections triggered:               0       N/A    N/A
═══════════════════════════════════════════════════════════════
*N/A* = 与单 span/tag 信息增量不相关，声音未评估。
```
Codex 不可用 → Consensus 全 N/A。Claude subagent（迟延对账后）：硬拒绝 0；litmus 3（扫描性）初判 NO——down 无样式 + 时间错时区，经 D-1/D-3/D4 修复后转为 YES（声音 Gate 建议「D1-D3 必修、D4/D5 决定 3am 可信度」已全部并入）。

#### NOT in scope（设计）
- 全页 loading 态骨架/闪烁治理（9 页既有 onMounted 模式，独立改进，非本 plan 爆炸半径）。
- 状态 pill 的 dark 主题底色（既有 .ok/.error 同为 light-only 硬编码——统一治理属全文件 refactor；新类文字色已用 token，不新增债）。
- 健康卡趋势图/历史（F16 指标化的 UI 面，TODOS P3）。

#### What already exists（设计）
- `docs/designs/DESIGN.md`：--muted/--danger/--warning/--success/--surface-2/--text-xs 全量 token（本次全部复用，零新 token）。
- `.source-item`/`.source-status` pill 模式、`.empty-tip` 空态、`.card-body` 卡片模式（Admin.vue:948-990）。

#### Completion Summary
```
+====================================================================+
|         DESIGN PLAN REVIEW — COMPLETION SUMMARY                    |
+====================================================================+
| System Audit         | DESIGN.md 存在（105 行）；UI scope=1 卡片行  |
| Step 0               | 初始 4/10；全 7 维；OPERATE 分类             |
| Pass 1  (Info Arch)  | 7/10 → 9/10                                |
| Pass 2  (States)     | 5/10 → 9/10（D-1 状态色补齐）              |
| Pass 3  (Journey)    | 8/10 → 9/10（voice D3/D5/D6 文案与时长）   |
| Pass 4  (AI Slop)    | 9/10 → 9/10                                |
| Pass 5  (Design Sys) | 4/10 → 9/10（D-2 token + D-3 时区诚实）    |
| Pass 6  (Responsive) | 5/10 → 8/10（D-4 wrap + D10 测试）         |
| Pass 7  (Decisions)  | 0 unresolved, 1 呈门（voice D13 荐 defer） |
+--------------------------------------------------------------------+
| NOT in scope         | written（3 项 + D14/D15/D16）                |
| What already exists  | written                                     |
| TODOS.md updates     | 0 项（设计债无新增）                        |
| Approved Mockups     | 0 generated（跳过，理由见审计 #13）          |
| Decisions made       | 10（D-1..D-4 + voice D2/D3/D4/D5/D6/D7/D10）|
| Decisions deferred   | 1（voice D13 呈门）                         |
| Overall design score | 4/10 → 8/10                                 |
+====================================================================+
```

<!-- autoplan-accepted:design -->
- D-1：Admin.vue 补 `.source-status.down`（--danger 文字 + 既有红底）、`.degraded`（--warning + 琥珀底）、`.unknown`（--muted + surface-2 底）三类 pill——健康四态不再有裸奔状态；文字色一律 DESIGN.md 语义 token。
- D-2：`.source-meta { color: var(--muted); font-size: var(--text-xs); }`——用 tokens.css 既有 --muted（含 dark 变体），禁止 --vt-c-text-2（不存在）与硬编码 #888。
- D-3：后端 `_iso_utc()` 对 naive UTC 追加 'Z'（显式 UTC）；前端 `fmtDuration()` 展示**时长**（已故障 40 分钟 / 最后成功 3 小时前，时区免疫，兼作新鲜度信号）；推送体删除 UTC 括号（与同句分钟数矛盾）。test_admin_system_health 补 endswith('Z') 断言。验证：tests/api/test_admin.py 全绿。
- D-4：`.source-item` 加 `flex-wrap: wrap; row-gap: 4px`（375px 防挤压）。验证：npm run build + vitest 全绿。
- D-5（voice D5）：告警体含备源状态——全 ok→「备用源正常，开奖未受影响」；有 down→「双源同时故障，开奖可能延迟入库」；unknown/degraded→如实列状态+「可能延迟入库」；备源测试断言「开奖未受影响」与无「（自」。
- D-6（voice D6）：恢复通知体诚实声明缺口——path_a 轮询 + 启动回填（最近 2 天）覆盖范围，更长缺口请人工确认。
- D-7（voice D7）：Task 3 补 `test_record_permanent_on_fresh_row_keeps_unknown`（部署首日 juhe 未配 key 的真实路径）。
- D-8（voice D4/D17）：/admin/health 同返 `alerted` + `error`（后端 `_truncate` 截断 200 + '…'）；面板 alerted 渲染 已通知/恢复待通知 pill，error 进 meta（title 悬浮全文）。
- D-9（voice D2/D10）：Task 6 模板改插入式表述（v-if/v-else 逐字节不动）；Admin.test.ts 补 2 用例（down 行全要素断言 + 空态回归）。
- D-13（voice D13，taste 呈门）：非 ok 时健康状态置顶 global-error banner——荐 defer（理由：admin 已熟知卡片位置，banner 属增强）。
- Mockups 跳过：UI delta 为既有组件内信息 span/tag，无新布局/组件；比较板交互与 autoplan 单门纪律冲突（审计 #13）。
<!-- /autoplan-accepted:design -->

| # | Phase | Decision | Classification | Principle | Rationale | Rejected |
|---|-------|----------|-----------|-----------|----------|
| 13 | Design | 跳过 mockup 生成 | Mechanical | P3 务实 + autoplan 单门 | 单 span 信息增量无新布局；比较板反馈环需中途打断用户，与 one-gate 冲突 | 生成 3 变体+比较板 |
| 14 | Design | D-1 四态状态色补齐 | Mechanical | P5 显式 + DESIGN.md 对齐 | down 真实落表后无样式状态用户可见；token 化文字色 | 沿用 ok/error 两类 |
| 15 | Design | D-2 --muted token | Mechanical | P5 + 设计系统单一事实源 | --vt-c-text-2 在本项目不存在；DESIGN.md 明确定义 --muted | plan 原稿兜底 |
| 16 | Design | D-3 显式 UTC + CST 展示 | Mechanical | 时区纪律（全程 Asia/Shanghai） | naive UTC 裸展示用户误读 8h；JS 无标记 ISO 按本地解析 | slice(0,16) 直显 |
| 17 | Design | D-4 flex-wrap 防挤压 | Mechanical | P1 完整（375px 边界） | 三子元素长文本在既有 space-between 双元素布局下挤压 | 不动布局 |
| 18 | Design | voice 迟延对账：D1/D8/D9 重合项确认闭环 | Mechanical | 对账纪律 | 声音迟延送达不改已落地修复的正确性；INPUT 哈希匹配后按完成声纳 reconciliation | 丢弃迟延报告 |
| 19 | Design | voice D2/D10 插入式模板 + 前端健康卡测试×2 | Mechanical | P5 显式 + 测试纪律 | 粘贴式片段会误删 v-if/v-else 空态且零断言兜底 | 整段替换片段 |
| 20 | Design | voice D3 时长相对化 + 推送体删 UTC 括号 | Mechanical | 诚实展示 + 时区纪律 | 同句两事实差 8h；时长兼作新鲜度信号（顺带解 D11） | 绝对 CST 展示（首轮流派，被声音更优解取代） |
| 21 | Design | voice D4/D17 面板补 alerted/error + 截断省略号 | Mechanical | P1 完整 + M13 注释自洽 | 面板必须回答「叫过人没有/为什么挂」；M13 注释本就说 error 进面板 | 仅时间两列 |
| 22 | Design | voice D5/D6 告警体备源状态 + 恢复诚实声明 | Mechanical | 告警可信度 | 单源故障误读为已漏开奖 = 静音最快路径；闭环须到缺口 | 纯状态文案 |
| 23 | Design | voice D7 permanent 新行测试 | Mechanical | 测试真实路径 | 部署首日即是新行 permanent，既有用例只覆盖 ok 后 | 仅 ok 后路径 |
| 24 | Design | voice D13 呈门（荐 defer）；D14/D15/D16 低值不处理 | Taste | P3 务实 | banner 提升属增强非信任必需；uppercase/enum/排序为既有 pattern 或噪音 | 全部并入本 plan |

### Phase 2.5: DX Review（模式：DX POLISH，2026-09-15，[subagent-only]）

> 声音覆盖：Codex 不可用（not_installed）；Claude DX subagent 完成（21 项发现 F1-F21，high×4）。
> 输入绑定说明：DX 声音按快照 a9887d11（design 第一轮块 + DX 修订前任务）审查并核销；因 design 声音迟延对账（第二轮）改写了 implementation 中的 design 块，DX 快照已按当前 plan 刷新重建（6cfb856c）后再 amend——两阶段编辑分别由 design 第二轮 marker 与本节 DX-1..DX-6 记录，无未记录改写。
> 对账：voice F5/F13/F14 与 design 轮已落地修复重合（D8 面板 error / D17 省略号 / D3 推送删 UTC 括号）→ 关闭；F4 拒绝（见下）；其余 17 项全部裁决。
> 产品类型：自托管私有部署服务（API/Service + 运维 runbook）。主 persona：**自托管运维**（兼唯一 admin；被 Bark 叫醒的人；容忍度高但要求「能查、能关、能验证」）。

#### Step 0 调查
- **TTHW 评估**：本 plan 的「hello world」= 亲手看到一次告警送达。原 plan 下不可执行（需等真实故障 30 分钟）→ **∞ → < 5 分钟**（F1 手工冒烟 + F19 阈值可配置为 0，见 Task 7 Step 1 runbook）。
- **竞品基准**：对标对象是「同类自部署监控」（uptime-kuma 等）：它们的共同点是「不重建容器就能验证告警链路」。本 plan 经 F1/F19 后达到同一水位。Competitive tier（2-5 min）。
- **Magical moment**：手机收到第一条「开奖抓取持续失败（冒烟）」——交付物 = Task 7 的可粘贴冒烟命令（最低成本载体，P5）。
- **journey 摩擦点**：Discover（deploy.md 无此功能文档 F15）→ Install（env 三个新配置 F18/F19/F20 已入 .env 语义）→ Hello World（冒烟 runbook F1）→ Real Usage（面板字段 F5✓/文案 F10）→ Debug（error 进面板 F5✓ + send 失败 error 级 F12）→ Upgrade（F2 冷启动说明）。

#### 8 Passes（voice 发现逐条裁决）
1. **Getting Started 3→8**：**F1（high，接受）** Task 7 增手工冒烟 runbook（seed down → evaluate → 看手机）；**F19（接受）** 阈值入 settings 支持置 0 冒烟；**F2（接受）** deploy.md 写冷启动说明（存量行 down_since NULL，需一个完整抓取周期）。
2. **API/CLI 6→9**：**F3（high，接受）** outcome→status 映射表入模块注释 + `Outcome = Literal[...]`；**F11（接受）** permanent → status='degraded' + 清 down_since（消「配置态被报成运行故障 1440 分钟」+ degraded 成为真实写路径 + design D7 灰砖同步解）；**F7/F21（接受）** `_record_health(source_name: str, ...)` 去双重 str()、`NowFn` 导出。**F4（拒绝）** `alerted` 改名 `alert_state`：迁移未上线确实是改名最便宜的时点，但收益是品味级（列注释 + 状态机测试已自证语义），改动横跨 spec/plan/模型/迁移/API/前端/测试 7 处——churn 超过价值，保留 spec 定义名。
3. **Errors 5→9**：**F6（接受）** send_alert=None → 不发送不转移（spec §1.1 alerted=「已送达」语义自洽；面板 down+未通知=告警链路故障可见，顺带覆盖 F12 的核心）+ 每次评估 warning 一次；**F9（接受）** Literal 类型表达封闭集（保留运行时 raise 兜底）；**F10（接受）** 告警体补 fix 指引（检查 DNS/上游 → 面板 → deploy.md 小节）；**F12（接受轻量版）** send 失败日志升 error 级（告警链路挂=重大运维事件）；计数列不做（YAGNI，F6+D8 已使该状态面板可见）。
4. **Documentation 4→9**：**F15（接受）** Task 7 新增 deploy.md「数据源健康告警」小节（机制/检测时机/配置/面板 curl/冒烟）+ CLAUDE.md 约定行；**F16（接受）** curl 示例 + runbook 并入该小节；**F17（接受）** Task 5 测试片段显式 import 说明。
5. **Upgrade 7→8**：迁移可 downgrade ✓；**F2** 冷启动语义已书面化 ✓。
6. **Dev Environment 6→8**：**F18（high，接受）** `SOURCE_HEALTH_ALERTS_ENABLED` 独立开关（健康告警与密码重置 admin 通知解耦）；**F20（接受轻量版）** `ADMIN_BARK_URL` 入 settings（自建 Bark 场景；build_admin_alert 与 main.py:145 admin_bark_config 同源——消灭两处硬编码真源）；多渠道通道不做（evaluate 已接受任意 callable，TODOS P3 有项）。
7. **Community 6（不变）**：plan-09 已交付 CONTRIBUTING/SECURITY/issue 模板；本 plan 无社区面。
8. **Measurement 5→7**：健康表 + 面板 + 结构化日志（write_failed/send_failed/evaluate_failed/alerts_disabled）构成可测面；F12 轻量版落地。

#### DX DUAL VOICES — CONSENSUS TABLE
```
═══════════════════════════════════════════════════════════════
  Dimension                           Claude  Codex  Consensus
  ──────────────────────────────────── ─────── ─────── ─────────
  1. Getting started < 5 min?          NO→fix  N/A     N/A
  2. API/CLI naming guessable?         NO→fix  N/A     N/A
  3. Error messages actionable?        NO→fix  N/A     N/A
  4. Docs findable & complete?         NO→fix  N/A     N/A
  5. Upgrade path safe?                YES     N/A     N/A
  6. Dev environment friction-free?    NO→fix  N/A     N/A
═══════════════════════════════════════════════════════════════
```
Codex 不可用 → Consensus 全 N/A。subagent 的 5 个 NO 维全部经上述修复转 YES（见其自身「优先修复顺序」5 条：F14 已由 design D3 关闭、F18/F1+F19/F3+F11/F5+F12 全部接受）。单声 high 已逐一代码核实（main.py:145、config.py 布局、test_config.py 存在、_NowFn/_record_health 现状均属实）。

#### NOT in scope（DX）
- F4 `alerted`→`alert_state` 改名（拒绝：7 处 churn 换品味级收益；列注释+测试已自证）。
- 多渠道 admin 告警通道（F20 的 SOURCE_ALERT_CHANNEL 半）——TODOS P3 已有项；`ADMIN_BARK_URL` 已覆盖自建 Bark。
- send 失败计数列（F12 完整版）——F6+D8 使该状态面板可见，error 级日志留痕，计数属过度设计。
- TODOS 新增：无（F2 已书面化进 deploy.md；F4 拒绝非延期）。

#### What already exists（DX）
- plan 自身骨架（Task→Files/Interfaces/Steps + Expected）被 voice 点名表扬为「真正的 DX 增益，建议留作后续 plan 模板」——保留。
- `_invoke_job`/`_make_deps`/`stubApi(overrides.health)`/conftest autouse 置 0/`Fernet.generate_key()` 范式——全部复用。
- `evaluate_source_alerts(engine, send_alert, now)` 纯函数形态——voice 指出「一行 REPL 就能驱动」——冒烟 runbook 直接利用。

#### DX Scorecard
```
+====================================================================+
|              DX PLAN REVIEW — SCORECARD                             |
+====================================================================+
| Dimension            | Before | After  |
|----------------------|--------|--------|
| Getting Started      | 3/10   | 8/10   |
| API/CLI/SDK          | 6/10   | 9/10   |
| Error Messages       | 5/10   | 9/10   |
| Documentation        | 4/10   | 9/10   |
| Upgrade Path         | 7/10   | 8/10   |
| Dev Environment      | 6/10   | 8/10   |
| Community            | 6/10   | 6/10（本 plan 无社区面）|
| DX Measurement       | 5/10   | 7/10   |
+--------------------------------------------------------------------+
| TTHW（验证告警链路） | ∞（不可执行）| < 5 min（F1 冒烟 + F19 阈值置 0）|
| Competitive Rank     | Needs Work → Competitive                    |
| Magical Moment       | 手机收到冒烟告警 via 可粘贴命令（Task 7）     |
| Product Type         | 自托管私有部署服务（API/Service + runbook）  |
| Mode                 | DX POLISH                                   |
| Overall DX           | 3/10 → 8/10（min 口径：Community 6 为非本    |
|                      | plan 维度；主体维度全 8+）                    |
+====================================================================+
```

<!-- autoplan-accepted:dx -->
- DX-1（F1+F19+F2）：Task 7 新增 deploy.md「数据源健康告警」小节——机制/检测时机（含白天不检测的真实 SLA）/升级冷启动说明（存量行 down_since NULL 需一个抓取周期）/配置四项/面板 curl 示例/手工冒烟 runbook（阈值置 0 → seed down → evaluate → 收 Bark → ok 恢复 → 面板回 ok）；CLAUDE.md 关键约定补一行。验证：文档含可复制命令且与实现一致（runbook 函数签名 = evaluate_source_alerts(engine, send_alert)）。
- DX-2（F3+F11+F9）：`Outcome = Literal['ok','down','permanent']` + 模块注释 outcome→status 映射表；permanent 分支置 `status='degraded'` 且清 `down_since`（不再是「其余不动」——spec §1.2 随 M4/M5 同步改）；测试改写 permanent×2（degraded 语义）+ 新增 down→permanent 不告警回归（放 Task 4 测试段，依赖 _Recorder）。
- DX-3（F6）：`evaluate_source_alerts` 在 send_alert=None 时不发送也**不转移**（两分支同），每次评估 `source_alerts_disabled` warning 一次——spec §1.1 alerted=「已送达」语义自洽；`test_no_sender_keeps_state_and_logs` 取代 `test_no_sender_still_transitions`。
- DX-4（F18/F20）：config.py 增 `source_health_alerts_enabled: bool = True`、`admin_bark_url: str = 'https://api.day.app'`、`source_health_alert_after_minutes: int = 30`；jobs/backfill 接线按开关传 sender=None；build_admin_alert 与 main.py:145 的 url 同源 settings.admin_bark_url；test_config.py 补默认值测试。
- DX-5（F10+F12 轻量）：告警体补 fix 指引（检查 NAS 网络/DNS 与上游 → /admin/health → docs/deploy.md 小节）；send 失败日志 warning→error（重试语义不变）。
- DX-6（F7/F8/F21/F17）：`_record_health(self, source_name: str, ...)` 去双重 str()；`_alert` raise 用 `result.error or '未知原因'`；`_NowFn`→`NowFn` 导出；Task 5 测试片段显式 import 说明（register_all_jobs/_invoke_job/_make_deps）。
- 拒绝：F4（alerted→alert_state 改名，7 处 churn 换品味级收益）。
- 先前轮次覆盖声明（本 block 替代关系）：ceo M11 的 `DOWN_ALERT_AFTER` 常量由 settings 驱动的 `_down_alert_after()` 取代（F19）；design D-3 已关闭 dx F14（推送体无 UTC 绝对时间）；design D-8/D-17 已关闭 dx F5/F13。
<!-- /autoplan-accepted:dx -->

| # | Phase | Decision | Classification | Principle | Rationale | Rejected |
|---|-------|----------|-----------|-----------|----------|
| 25 | DX | 模式=DX POLISH | Mechanical | 覆盖层默认 | 既有产品的可靠性增强，不做 DX 扩张 | — |
| 26 | DX | F1+F19+F2 冒烟 runbook + 阈值可配 + 冷启动文档 | Mechanical | DX 第一原则 1（T0 零摩擦）+ P4 逃生舱 | 「看不见功能工作」= 不可验证的功能；阈值置 0 是冒烟前提 | 等真实故障验证 |
| 27 | DX | F3+F11 outcome→status 映射 + permanent→degraded | Mechanical | P5 显式 + 状态诚实 | 「配置态被报成运行故障 1440 分钟」与立意冲突；degraded 枚举此前无写入方 | permanent 只记 error（spec 原义，down→permanent 场景出错） |
| 28 | DX | F6 send_alert=None 不发送不转移 | Mechanical | spec §1.1 语义自洽 | alerted=「已送达」；未送达标 alerted 是状态说谎（顺带覆盖 F12 核心） | 原 plan 契约（转移不发送） |
| 29 | DX | F18 独立开关 + F20 ADMIN_BARK_URL + F19 阈值入 settings | Mechanical | DX 第一原则 4（decide for me, let me override） | ADMIN_BARK_KEY 被两类通知复用作总开关；自建 Bark 是 NAS 真实场景；阈值改需重建镜像不可接受 | 第二 Bark key（YAGNI）、多通道（TODOS P3） |
| 30 | DX | F10 fix 指引 + F12 error 级日志 | Mechanical | DX 原则 5（fight uncertainty） | 半夜收到告警必须知道下一步；告警链路挂=重大事件非普通 warning | F12 计数列（过度设计） |
| 31 | DX | F15/F16/F17 deploy.md 小节 + curl + import 显式 | Mechanical | P1 完整（文档即功能） | 运维事实四条（阈值/检测窗口/通道/面板字段）此前零文档 | 只写 spec（开发文档） |
| 32 | DX | F4 alerted→alert_state 改名 | Mechanical（拒绝） | P3 务实 | 7 处 churn 换品味级收益；列注释+状态机测试已自证语义 | 改名 |
| 33 | DX | F7/F8/F21 微观一致性 | Mechanical | P5 显式 | 类型注解一致/「未送达： None」/私有名入公开签名 | 不修 |

### Phase 3: Eng Review（最终门，2026-09-15，[subagent-only]）

> 声音覆盖：Claude subagent（eng-voice）完成（1 CRITICAL + 2 HIGH + 7 MEDIUM + 4 LOW + 7 测试缺口）；Codex 不可用（not_installed）→ 共识列 N/A。
> 主审查对子代理全部关键论断逐一代码核实：C1（alembic heads 实测 d1_draw_costs 单 head + Dockerfile:79 CMD 确认）、H1（conftest.py:44-55 autouse 删 JWT_SECRET/CRYPTO_KEY_V1 + config.py:45-49 无默认）、H2（SQLite inspect 实测 default="'none'" 带引号）、M2（bark.py:30/52-53 client 仅经 close 释放 + notifier.py close 纪律）、M3（plan Task 3/4 状态机逐路径推演 + Task 6 模板无 status 门控）、M4（fetch_service.py:95-102 原始异常日志 + juhe.py:27 key 在 query）、M5（web/package.json 无 @vue/test-utils + Admin.test.ts:81-90 mount→host 惯例）、M7（backfill.py:3/10 import 已存在 + :109 time.sleep(1.2) 残留）、L4（collect-only 实测 757）。全部属实。

#### Step 0 范围挑战（代码核实）
- 触及 12 文件 + 新模块 2 个——复杂度检查触发线（2+ 新服务）边缘；但两模块均为单一职责小模块，且各收回一份既有复制（auth._build_admin_alert、backfill 1.2 常量），净复杂度下降。**范围不缩减（P2：scope challenge never reduce）。**
- 复用审计：_build_admin_alert（auth.py:230-243）迁出 ✓；_INTER_LOTTERY_INTERVAL ✓；notifier close 纪律 → 经 E4 惰性构造规避同型问题。
- 基线漂移两项（L4 测试计数、C1 迁移 head）——plan 文本写于 plan-10 合入前，均已随修订校正。
- TODOS.md 交叉核对：7 项延期无阻塞本 plan；本阶段 0 新增 TODO。

#### Section 1 架构（ASCII 依赖图）
```
scheduler/jobs.py ──tick 尾──┐
scheduler/backfill.py ──尾───┤
                             ▼
             services/source_health.py
             ├─ record_source_health（fetch 三态写入）
             ├─ evaluate_source_alerts（两阶段状态机，sender 工厂）
             └─ admin_alert_sender_factory ──▶ notifications/admin_alert.py
                     │                              │（送达契约 raise，M2/F8）
                     ▼                              ▼
services/fetch_service.py ──三态写入──▶ models/health.py（+down_since/alerted）
        └──日志脱敏 sanitize_error◀── source_health（M4/M13 同一脱敏器）
api/admin.py /admin/health ──读──▶ Admin.vue 健康卡（status 门控时长，eng M3a）
```
耦合：scheduler→services→models 分层不变；notifications 无反向依赖；唯一 import-linter 契约（domain 零 IO）不受影响。单点：Bark 是 admin 唯一告警出口——送达重试+不转移已兜底，多通道记 TODOS P3 #5。

#### Section 2 代码质量
- eng M2（client 泄漏）与 CEO M12（LOW 不处理）裁决冲突 → E4 工厂设计双边解决（无生命周期管理负担）。
- eng M6 后端截断自毁（title 只见截断版）→ E9 前后端职责重排。eng M7 常量残留 → E10。
- DRY：sanitize_error 单点服务落表+日志两路径；_INTER_LOTTERY_INTERVAL 单一真值源补齐。

#### Section 3 测试审查（测试图）
```
CODE PATHS                                              覆盖
[+] services/source_health.py
  ├── record_source_health
  │   ├── ok 首行/短故障静默清除                          [★★★ 既有]
  │   ├── ok 长故障零送达 → recovering（M11）             [★★★ 既有]
  │   ├── down 连续（起点不刷新）                          [★★★ 既有]
  │   ├── recovering→down（M3a：翻 none + 新 episode）     [★★★ 改写+新增]
  │   ├── permanent→degraded 清 down_since+alerted（M3b）  [★★★ 新增]
  │   └── error 脱敏（M13）                               [★★★ 既有]
  ├── evaluate_source_alerts
  │   ├── 阈值前不告警/阈值后一次/失败重试                  [★★★ 既有]
  │   ├── 恢复通知送达/失败保持 recovering                 [★★★ 既有]
  │   ├── sender 工厂 None 不转移（F6）                    [★★★ 既有]
  │   ├── 发送时不持 DB 连接（M1，pool_size=1 实测断言）    [★★★ 既有]
  │   ├── 工厂惰性：无待发送不调用（M2）                    [★★★ 新增]
  │   ├── 备源 ok/degraded/双 down 影响文案（D5+M8.4）     [★★★ 既有1+新增2]
  │   └── recovering→down 达阈值重告警（M3a 评估侧）       [★★★ 新增]
  └── admin_alert_sender_factory 开关语义                 [★★ Task 5 接线]
[+] services/fetch_service.py
  ├── 健康写失败不阻断抓取（M8.1）                         [★★★ 新增]
  ├── 日志脱敏（M4）                                      [★★★ 新增]
  └── 双源落表 + grace 落表（M7）                          [★★ 接线断言]
[+] notifications/admin_alert.py                          [★★★ 4 用例：None/SENT/FAILED raise/url 注入]
[+] scheduler 接线（jobs/backfill 尾部 + QPS 间隔）         [★★★ 5 用例]
[+] api/admin.py + Admin.vue                               [★★★ 2 API + 4 vitest]
[+] alembic 迁移 + 单 head 守卫（C1/H2）                    [★★★ 2 用例]
USER FLOWS（手工冒烟 runbook，deploy.md）
  ├── [★★ 文档化] 阈值置 0 → seed down → 收 Bark → ok → 收恢复通知
  └── [GAP→TODOS] 整机故障（NAS 宕机）无进程内告警 → F13 外部探针
```
COVERAGE：新代码路径单元/集成全覆盖；E2E 级 = 手工冒烟 runbook；EVAL 不适用（无 LLM）。回归：test_record_down_cancels_pending_recovery 语义翻转（M3a）属行为修正，非回归破坏。
测试计划 artifact：~/.gstack/projects/gitea-lottery-notification/alfred-main-eng-review-test-plan-20260915-165800.md

#### Section 4 性能
- 每 tick 每源一次短写 + 全表 ≤4 行评估：可忽略。M2 client 泄漏由工厂设计消除（无待发送零构造）。
- 无 N+1（单表全量读快照出 session）；无新缓存需求；QPS 间隔只加真实请求间（既有语义）。

#### 失败模式注册表（修订后终态）
| 路径 | 故障模式 | 救援 | 测试 | 用户/运维看到 | 日志 |
|---|---|---|---|---|---|
| 迁移 | down_revision 指错→双 head | E1 守卫测试 | Y | CI RED 而非容器启动失败 | — |
| 健康落表 | 写库失败 | 吞+warning，不阻断抓取 | Y（M8.1） | 面板停旧值 | Y |
| 故障告警 | 发送失败 | 不转移+下轮重试 | Y | 延迟收到 | Y |
| 恢复通知 | 发送失败 | 保持 recovering | Y | 延迟收到 | Y |
| sender 构造 | settings 缺 key | 工厂 None→不转移（F6） | Y（F18 测试） | 面板诚实（无「已通知」） | Y |
| 状态转移 | 读-落间并发改写 | 守卫重读放弃本轮 | 间接 | 或重复告警一次（L2 已文档化） | Y |
| error 文本 | 含 juhe key | 落表+日志双路脱敏 | Y×2 | 阻断外泄 | Y |
| 面板展示 | recovering 滞留说谎 | status 门控 + permanent 清 alerted | Y×2 | 不说谎 | — |
CRITICAL GAPS：0（C1/H1/H2/M3 全部随修订闭环）。

#### ENG DUAL VOICES — CONSENSUS TABLE
```
  Dimension                           Claude(subagent+主审核实)  Codex        Consensus
  ──────────────────────────────────── ──────────────────────── ──────────── ─────────
  1. Architecture sound?              YES（工厂修订后）           N/A          N/A（单声音）
  2. Test coverage sufficient?        YES（M8 缺口补齐后）        N/A          N/A
  3. Performance risks addressed?     YES（M2→E4）               N/A          N/A
  4. Security threats covered?        YES（M4 日志路径补齐）      N/A          N/A
  5. Error paths handled?             YES                        N/A          N/A
  6. Deployment risk manageable?      YES（C1 修复+守卫后）       N/A          N/A
```
单声音标记：C1/H1/H2 为子代理单声音 CRITICAL/HIGH 发现，但主审查已逐一独立复现核实（双读者确认，非盲信）。

#### NOT in scope（本阶段考虑后仍延期）
- 多通道 admin 告警（email/feishu）——TODOS P3 #5
- sender 单例化/close 生命周期管理——E4 工厂设计使其不必要
- 外部黑盒探针——TODOS P2 #3
- （前轮 T1-T6/D13 呈门项不变，呈 Phase 4。）

#### What already exists
- auth.py:_build_admin_alert → 迁出共用（Task 2）；password_reset_service.py:248-255 既有 try/except 兼容 raise 语义
- jobs._INTER_LOTTERY_INTERVAL → 唯一真值源（M6+E10）
- notifier.py:271-290 close 纪律 → 本 plan 经惰性构造规避同生命周期问题
- tests/api/test_admin.py:15-18 Fernet/env 范式 → 全部新测试复用
- Admin.test.ts mount(overrides)→host 惯例 → vitest 新用例复用（不引 @vue/test-utils）

#### Completion Summary
- Step 0: Scope Challenge — 范围接受（复杂度触发线边缘，但净复杂度下降）
- Architecture Review: 1 issue（M2 client 生命周期→E4）
- Code Quality Review: 2 issues（M6→E9、M7→E10）
- Test Review: diagram produced, 9 gaps（H1×3 测试不可过、H2、M5、M8.1/M8.4×2/M8.6/M8.7）→ 全部补入 plan
- Performance Review: 1 issue（M2，与架构并案）
- NOT in scope: written
- What already exists: written
- TODOS.md updates: 0 新增
- Failure modes: 0 critical gaps（修订后）
- Outside voice: Codex not_installed（unavailable）；Claude subagent completed（1C/2H/7M/4L + 7 测试缺口）
- Parallelization: Sequential implementation, no parallelization opportunity（全部任务共享 services/scheduler 模块链）
- Lake Score: N/A（无 complete-vs-shortcut 抉择——全部为正确性必修项）
- Unresolved decisions: 0

#### Implementation Tasks（eng）
E1-E13 已全部直接落入 Task 1-7 的 TDD 步骤文本（测试先行），无独立待办任务。
<!-- autoplan-accepted:eng -->
- E1（eng C1 必修）：迁移 `down_revision='d1_draw_costs'`（真实 head；plan 原稿 fix_prize_amount_cents 会产生双 head → Dockerfile:79 `alembic upgrade head` 报 Multiple head revisions → 容器启动失败）；新增 `test_alembic_single_head` 守卫（alembic heads 单 head 断言入迁移测试文件）；plan 文本 head 声明同步修正。验证：`uv run alembic heads` 单 head + 迁移测试 2 passed。
- E2（eng H1）：评估器签名 `send_alert` → `sender_factory` 惰性工厂——无待发送项时绝不调用（不碰 get_settings/httpx）；三个调度测试不再因 conftest 删 JWT_SECRET/CRYPTO_KEY_V1 而 ValidationError 吞评估；需直调工厂的测试按 test_admin.py:15-18 范式补 env。验证：tests/scheduler/ 套件全绿。
- E3（eng H2）：迁移测试断言 quoted 字面量（"'none'" in str(default)）+ nullable=False + 缺省插入回读——inspect() 返回 DDL 带引号文本 "'none'"，`== 'none'` 两个分支永假（SQLite 实测）。
- E4（eng M2，取代 CEO 轮 M12 LOW 不处理裁决）：sender 工厂模式消除每 tick 新建未关闭 httpx.Client——无 singleton、无 close 生命周期、无 conftest 耦合；`test_factory_not_called_when_nothing_pending` 回归。
- E5（eng M3a）：模板时长文案按 `status !== 'ok'` 门控（recovering 的 ok 行不再显示「已故障 N 天」）；recovering→down 翻 `alerted='none'` + down_since 重置为新 episode（修 M11 路径「已通知」谎言；重告警由送达门控决定，duplicate > silence）；改写 test_record_down_cancels_pending_recovery 期望 + 补 test_redown_after_recovering_realerts。
- E6（eng M3b）：permanent 分支同时 `alerted='none'`（配置态终结运行故障 episode，不欠恢复通知；degraded 行不再卡「恢复待通知」）；补 test_record_permanent_clears_recovering。
- E7（eng M4）：`_sanitize_error` → 公开 `sanitize_error`；`_fetch_with_backoff` 日志经脱敏（juhe key 不进容器日志——M13 只堵了落表路径，日志是同 NAS 上的同级泄露面）；补 caplog 脱敏回归测试。
- E8（eng M5）：Task 6 vitest 用仓库既有 `mount(overrides)` → host 惯例（querySelectorAll/classList/textContent），不引入 @vue/test-utils（非本仓库依赖）。
- E9（eng M6）：弃后端 `_truncate`——API 返回完整 error，前端 `fmtError(80)` 截断+'…' 内联、title 悬浮全文（后端截断会让 title 也只有截断版，D17「全文可见」失效）；补 300 字符穿透 + title 全文 + `_iso_utc(None)` 边界测试。
- E10（eng M7）：`_backfill_history` 的 `time.sleep(1.2)` → `_INTER_LOTTERY_INTERVAL`（M6 单一真值源补完最后一份拷贝）；修正 backfill import 说明（time/get_settings 已在文件头第 3/10 行）。
- E11（eng M8）：补测试——健康写失败不阻断抓取（M8.1）、双源同时故障/备源 degraded 告警体分支（M8.4）、admin_bark_url 注入 Bark config（M8.6）。
- E12（eng L1/L3/L4）：test_admin_system_health 按 source 索引（不依赖 SQLite 返回序）；_iso_utc 兼容 aware 输入（先归一 naive UTC 再加 'Z'）；Task 7 基线 754→757（collect-only 实测）；CEO 轮锚点清单「alembic head=fix_prize_amount_cents」声明已过期，以 eng 核实（d1_draw_costs）为准。
- E13（eng L2）：评估器 docstring + deploy.md 写明「送达后落转移前状态被并发改写 → 下轮重发一次同样告警（duplicate > silence）」，避免运维误当 bug 追查。
- 覆盖声明（本 block 对前轮次结论的替代关系）：CEO 轮 M12（httpx.Client 每 tick 新建，LOW 不处理）由 E4 工厂设计取代；design D-8「后端 _truncate 截断 200+'…'」由 E9 前端截断取代；dx DX-1 runbook 签名 `evaluate_source_alerts(engine, send_alert)` 由 E2 工厂签名取代。
<!-- /autoplan-accepted:eng -->

---

> **autoplan baseline-edits 记录已移出正文**：本轮审查三阶段的 baseline-edit marker
> （ceo / design / eng，合计 282KB 内嵌审查前旧文本）移至
> `docs/superpowers/reviews/2026-09-15-source-health-alert.autoplan-baseline.md`——
> **不放在 plans/ 内**：CLAUDE.md 声明内部引擎按 `docs/superpowers/plans/*.md` glob 取计划，
> 放在该目录会被当计划读走（文件内含旧任务文本）；那些旧文本含已被后续审查推翻的
> 实现（如 `DOWN_ALERT_AFTER` 常量、`_sanitize_error` 私有名、recovering 再故障「回
> alerted」语义），留在正文会让按符号检索的实现者读到与现行 plan 相反的代码。移出后
> `gstack-autoplan-snapshot check/amend eng` 不再通过校验（本轮审查已结束、不再 amend）；
> 如需重跑 autoplan，直接 `create` 新快照。

## 批准后复核修订（2026-09-15，按 superpowers writing-plans 标准核验）

Phase 4 批准后，另派子代理按 `writing-plans` 技能标准（含其 plan-document-reviewer 模板）
核验本计划**是否真的可直接实施**，结论「Issues Found」；主审查对每条实测复现后确认属实，
经用户确认全部修复（修复前的原稿可从 `docs/superpowers/reviews/
2026-09-15-source-health-alert.autoplan-baseline.md` 中 design/eng 阶段 marker 的 `newText` 还原）：

| # | 缺陷（实测复现） | 修复 |
|---|---|---|
| R1 | `_down_alert_after()` 直接调 `get_settings()`：测试环境 conftest 删必填密钥 → `ValidationError` 沿调用链上抛，写入侧还被 `_record_health` 的 except 吞掉（**健康表静默停写**）；Task 3/4 大面积用例不可达。上轮 E2 只修了 sender 侧，漏了阈值侧 | 阈值读取加安全回退（默认 30 分钟 + warning），新增 `test_threshold_falls_back_when_settings_unavailable`；settings 仍为唯一真值源（F19 逃生舱未被架空），兜底常量由 `test_fallback_threshold_matches_settings_default` 钉住与 settings 默认值一致 |
| R2 | Task 2 实现用 `settings.admin_bark_url`，该字段原在 Task 5 Step 5.5 才创建 → `AttributeError`，Task 2 全挂 | config 三字段 + `main.py` 同源 + 默认值测试**前移**为 Task 2 Step 1-4；Task 5 对应步改为指针（保留编号以免破坏交叉引用） |
| R3 | 迁移测试裸 SQL `INSERT (source)` 漏 `created_at`/`status`（二者 NOT NULL 且无 server_default）→ `IntegrityError`，正确实现也失败 | INSERT 补齐两列；对真实迁移库实测通过（含 t11 加列后 `alerted` 缺省回读 = `none`） |
| R4 | M8.1 守卫用例 `monkeypatch.setattr(FetchService, '_record_health', _boom)`：类属性替换变 bound method → `TypeError`；且连带替换掉它要守的 except/日志两行，断言永假 | 改为 patch 模块属性 `app.services.source_health.record_source_health`（`_record_health` 函数内 import → 调用时解析，实测生效），构造按该测试文件既有 `_src` + `FetchService(...)` 惯例写出 |
| R5 | 引用仓库不存在的 helper（`_make_service_with_mock_sources` 等）并把构造方式甩给实现者（No Placeholders 违规） | 直接写出完整片段（`_src` + `fetch.side_effect` + `max_attempts=1/backoff_base=0/sleep=lambda`），删掉「以既有写法为准」兜底；顺带修掉原片段默认退避会真睡 ~31s 的问题 |
| R6 | Task 5 Step 3 命令点了尚未编写的 `test_source_health_alert_settings_defaults` → pytest exit 4，非「5 FAIL」 | 从命令移除；该用例已随 R2 前移并在 Task 2 走完整 RED→GREEN |
| R7 | 可读性：三个 baseline-edit marker 单行内嵌审查前旧文本共 282KB（占文件 64%），含**已被推翻的旧实现**（`DOWN_ALERT_AFTER`、`_sanitize_error`、recovering「回 alerted」），grep 型实现者会读到反的代码 | marker 移出为 `docs/superpowers/reviews/…autoplan-baseline.md`（逐字节保留；**不放 plans/**——该目录会被引擎按 `*.md` glob 当计划读），正文留指针；代价：snapshot `check/amend eng` 不再通过（审查已结束，不再 amend） |

附带修正：Task 5 测试片段补 `register_all_jobs` 显式 import（实测该文件顶部无此 import）、
Task 6 Files 补 `Admin.test.ts` 及其 describe 作用域说明、Self-Review 的陈旧签名
（`send_alert` → `sender_factory`）、Task 7 补 Interfaces 段。

核验结论小节：核验方判定「任务切分、TDD 步序、接口一致性、spec 覆盖」四项合格，
阻塞项集中在**测试可执行性**（R1-R6 全部是「照计划操作必然报错」而非设计缺陷）。
