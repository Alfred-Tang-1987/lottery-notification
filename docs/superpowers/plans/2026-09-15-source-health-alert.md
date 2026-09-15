<!-- /autoplan restore point: "/Users/alfred/.gstack/projects/gitea-lottery-notification/main-autoplan-restore-20260915-133726.md" -->
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
```

（conftest 已有 `_reset_settings_and_env` autouse fixture 清环境；`CRYPTO_KEY_V1` 一律用 `Fernet.generate_key().decode()`（autoplan M3），与 tests/api/test_admin.py:16 一致。）

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/notifications/test_admin_alert.py -q`
Expected: FAIL（`ModuleNotFoundError: app.notifications.admin_alert`）。

- [ ] **Step 3: 迁移实现（含 autoplan M2 送达契约）**

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
    """抖动：recovering 期间再次失败 → 回 alerted（取消待发的过时恢复通知）。"""
    t0 = datetime(2026, 9, 15, 4, 0, 0)
    with Session(db_engine) as s:
        s.add(ApiSourceHealth(source='mxnzp', status='ok', alerted='recovering',
                              down_since=t0))
        s.commit()
    record_source_health(db_engine, 'mxnzp', 'down', 'again', now=lambda: t0 + timedelta(minutes=1))
    h = _get(db_engine)
    assert h.alerted == 'alerted' and h.status == 'down'


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
def _down_alert_after() -> timedelta:
    from app.config import get_settings

    return timedelta(minutes=get_settings().source_health_alert_after_minutes)

# error 脱敏（autoplan M13）：juhe 把 api key 放 query（juhe.py:27），
# raise_for_status 异常消息含完整 URL；健康表 error 会进 admin 面板与 Bark
# 告警体（第三方服务器），密钥参数值落表前一律替换 [REDACTED]。
_SENSITIVE_QUERY_RE = re.compile(r'([?&](?:key|app_id|app_secret|token)=)[^&\s]+')

# now 注入点：生产用默认；测试注入固定时钟，避免真实 sleep/时间竞争。
NowFn = Callable[[], datetime]  # dx-voice F21：公开签名不用私有名


def now_naive_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _sanitize_error(error: str | None) -> str | None:
    """剥离 error 文本中的敏感 query 参数值（M13），保留 URL 其余部分供排障。"""
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
            if h.down_since is None:
                h.down_since = t
            if h.alerted == 'recovering':
                h.alerted = 'alerted'  # 抖动：取消待发的过时恢复通知
            h.error = _sanitize_error(error)
        elif outcome == 'permanent':
            # 配置态（dx-voice F11）：置 degraded 并清 down_since——不再按运行故障
            # 计时/告警；degraded 由此成为真实写路径（此前列注释枚举但无写入方）。
            h.status = 'degraded'
            h.down_since = None
            h.error = _sanitize_error(error)
        else:
            raise ValueError(f'unknown outcome: {outcome}')
        s.commit()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/services/test_source_health.py -q`
Expected: 9 passed（原 5 + M11×2 + M13×1 + D7×1）。

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
- Produces: `evaluate_source_alerts(engine: Engine, send_alert: Callable[[str, str], None] | None, now: NowFn = now_naive_utc) -> None`——Task 5 接线依赖。

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


def test_no_sender_keeps_state_and_logs(db_engine, caplog):
    """send_alert=None（未配 key / 开关关闭）→ 不发送也**不转移**（dx-voice F6：

    alerted 的语义是「故障告警已送达」（spec §1.1），未送达标 alerted 是状态说谎——
    面板会显示「已通知」而无人收到。保持 none + 每次评估 warning 一次；
    配好 key 后下一轮自然补发。
    """
    t = datetime(2026, 9, 15, 13, 0, 0)
    _seed_down(db_engine, down_since=t - timedelta(minutes=40))
    with caplog.at_level(logging.WARNING, logger='app.services.source_health'):
        evaluate_source_alerts(db_engine, None, now=lambda: t)
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
    evaluate_source_alerts(db_engine, rec, now=lambda: t0 + timedelta(hours=2))
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

    evaluate_source_alerts(db_engine, _sender, now=lambda: t)
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
    evaluate_source_alerts(db_engine, rec, now=lambda: t)
    assert len(rec.calls) == 1
    assert '备用源正常' in rec.calls[0][1] and '开奖未受影响' in rec.calls[0][1]
    assert '（自' not in rec.calls[0][1]  # D3：不带 UTC 绝对时间（与同句分钟数矛盾）

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/services/test_source_health.py -q -k evaluate`
Expected: FAIL（ImportError: evaluate_source_alerts）。

- [ ] **Step 3: 实现评估器（追加到 source_health.py，autoplan M1 两阶段）**

```python
def evaluate_source_alerts(
    engine: Engine,
    send_alert: Callable[[str, str], None] | None,
    now: NowFn = now_naive_utc,
) -> None:
    """评估健康表驱动告警状态机（spec §1.3；挂载于 path_a tick 尾 + 启动 backfill 尾）。

    send_alert=None（ADMIN_BARK_KEY 未配 / SOURCE_HEALTH_ALERTS_ENABLED=false）→
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
    放弃本轮转移下轮重评，不覆盖并发写入。
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
    # 阶段 2：session 外发送；送达成功的进待落清单
    delivered: list[tuple[str, str]] = []  # (source, 目标 alerted 状态)
    disabled_logged = False
    for source, status, alerted, down_since, error in rows:
        if (
            status == 'down'
            and alerted == 'none'
            and down_since is not None
            and t - down_since >= _down_alert_after()
        ):
            minutes = int((t - down_since).total_seconds() // 60)
            if send_alert is None:
                # F6：不发送不转移（状态不说谎）；每次评估只 warning 一次。
                if not disabled_logged:
                    logger.warning(
                        'source_alerts_disabled reason=no_sender '
                        '(ADMIN_BARK_KEY 未配或 SOURCE_HEALTH_ALERTS_ENABLED=false)：'
                        '健康告警只落表不发送'
                    )
                    disabled_logged = True
                continue
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
            try:
                # 文案不带绝对时间（design-voice D3：UTC 括号与同句的分钟数
                # 相差 8h 自相矛盾；时长已足够定位）。fix 指引（dx-voice F10：
                # problem+cause+fix——半夜收到的人需要知道下一步做什么）。
                send_alert(
                    '开奖抓取持续失败',
                    f'数据源 {source} 已持续失败约 {minutes} 分钟。{impact}。'
                    f'最近错误：{(error or "")[:200]}。'
                    f'处理：检查 NAS 网络/DNS 与上游状态；面板 /admin/health 查看；'
                    f'详见 docs/deploy.md「数据源健康告警」。',
                )
            except Exception:
                # error 级（dx-voice F12：告警链路本身挂了是重大运维事件，
                # 不是普通 warning；重试语义不变——下轮继续尝试直到送达）。
                logger.error(
                    'source_alert_send_failed source=%s', source, exc_info=True
                )
                continue  # 未送达不转移，下轮重试
            delivered.append((source, 'alerted'))
        elif alerted == 'recovering' and status == 'ok':
            if send_alert is None:
                continue  # F6：同告警分支——未送达不转移（保持 recovering）
            duration = t - down_since if down_since else timedelta(0)
            minutes = int(duration.total_seconds() // 60)
            try:
                # 诚实声明缺口（design-voice D6：闭环不能只到「恢复」——
                # 故障窗口的开奖是否补回，admin 必须知道要不要人工介入）。
                send_alert(
                    '开奖抓取已恢复',
                    f'数据源 {source} 已恢复抓取（故障持续约 {minutes} 分钟）。'
                    f'故障期间的开奖缺失将随今晚 path_a 轮询与启动回填'
                    f'（最近 2 天）覆盖；更长缺口请人工确认是否需要补抓。',
                )
            except Exception:
                logger.error(
                    'source_recovery_send_failed source=%s', source, exc_info=True
                )
                continue
            delivered.append((source, 'none'))
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

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/services/test_source_health.py -q`
Expected: 18 passed（Task 3 的 9 + 本任务 9）。

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
- Produces: `_path_a_tick` 与 `run_startup_backfill` 尾部各调用一次评估；backfill 复用 `jobs._INTER_LOTTERY_INTERVAL`（M6 单一真值源）。

- [ ] **Step 1: 写失败测试（test_jobs.py 追加，用仓库既有 `_invoke_job` 辅助）**

（dx-voice F17：片段自给自足——`register_all_jobs` 经文件顶部既有
`from app.scheduler.jobs import register_all_jobs`（若无则显式补 import），
`_invoke_job` 为本文件既有 helper（test_jobs.py:13），照抄不撞 NameError。）

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


def test_path_a_tick_sender_none_when_alerts_disabled(db_engine, monkeypatch):
    """SOURCE_HEALTH_ALERTS_ENABLED=false → sender=None（dx-voice F18 独立开关：

    配了 ADMIN_BARK_KEY 也不发——健康告警与密码重置告警不共用一个总开关）。
    """
    from unittest.mock import MagicMock

    import app.scheduler.jobs as jobs_mod
    from app.scheduler.setup import build_scheduler

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
    assert spy.call_args.args[1] is None
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

Run: `uv run pytest tests/scheduler/test_jobs.py::test_path_a_tick_evaluates_source_alerts tests/scheduler/test_jobs.py::test_path_a_tick_sender_none_when_alerts_disabled tests/scheduler/test_backfill.py::test_startup_backfill_paces_fetches_with_interval tests/scheduler/test_backfill.py::test_startup_backfill_evaluates_source_alerts tests/test_config.py::test_source_health_alert_settings_defaults -q`
Expected: 5 FAIL（AttributeError: evaluate_source_alerts / _INTER_LOTTERY_INTERVAL / source_health_alerts_enabled）。

- [ ] **Step 4: 实现 jobs.py 接线（含 dx-voice F18 独立开关）**

`app/scheduler/jobs.py` import 区加：

```python
from app.config import get_settings
from app.notifications.admin_alert import build_admin_alert
from app.services.source_health import evaluate_source_alerts
```

`_path_a_tick` 函数末尾（`sched.add_job(_push_big_win, ...)` 循环之后、函数体结束前）追加：

```python
    # 数据源健康评估（plan-11）：tick 尾部评估告警状态机（down≥阈值 → admin bark）。
    # SOURCE_HEALTH_ALERTS_ENABLED=false → sender=None（dx-voice F18：健康告警与
    # 密码重置告警不共用 ADMIN_BARK_KEY 一个总开关；None 时不发送不转移，见 F6）。
    # 评估失败不阻断本 tick 收尾。
    try:
        sender = build_admin_alert() if get_settings().source_health_alerts_enabled else None
        evaluate_source_alerts(engine, sender)
    except Exception:
        logger.error('source_alert_evaluate_failed', exc_info=True)
```

- [ ] **Step 5: 实现 backfill.py 间隔 + 尾部评估**

`app/scheduler/backfill.py`：import 区加 `import time`（若未有）、`from app.notifications.admin_alert import build_admin_alert`、`from app.services.source_health import evaluate_source_alerts`，以及（autoplan M6）：

```python
from app.scheduler.jobs import _INTER_LOTTERY_INTERVAL
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
    try:
        sender = build_admin_alert() if get_settings().source_health_alerts_enabled else None
        evaluate_source_alerts(engine, sender)
    except Exception:
        logger.error('source_alert_evaluate_failed', exc_info=True)
```

（原循环体的 try/except 与 missed 判断保持原样，仅包入 fetched 计数与 sleep；
backfill import 区同时加 `from app.config import get_settings`。）

- [ ] **Step 5.5: config.py 三个 settings 字段（dx-voice F18/F19/F20 逃生舱）**

`app/config.py` `admin_bark_key` 旁追加：

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
git add app/scheduler/jobs.py app/scheduler/backfill.py app/config.py app/main.py tests/conftest.py tests/test_config.py tests/scheduler/test_jobs.py tests/scheduler/test_backfill.py
git commit -m "feat(plan-11): tick/backfill 尾部评估源告警；启动回填对实际抓取加 QPS 间隔；告警阈值/开关/Bark URL 入 settings"
```

---

### Task 6: /admin/health 扩展 + Admin.vue 展示

**Files:**
- Modify: `app/api/admin.py:79-82`（system_health 响应）
- Modify: `web/src/pages/Admin.vue:19-22`（HealthSource）、`web/src/pages/Admin.vue:644-648`（模板）
- Test: `tests/api/test_admin.py:128-138`（扩展既有断言）

**Interfaces:**
- Consumes: Task 1 的 `down_since` 列。
- Produces: `/admin/health` 每源返回 `status` / `alerted` / `error`（截断+…）/ `last_success_at` / `down_since`（后两个为显式 UTC ISO 或 null）。

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
    # autoplan D-3：naive UTC 落库值必须以 'Z' 显式标注 UTC——否则前端
    # new Date() 按本地时区解析，面板故障起点显示偏差 8 小时（全程 Asia/Shanghai 纪律）。
    assert data['sources'][0]['last_success_at'].endswith('Z')
    assert data['sources'][1]['down_since'].endswith('Z')
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
    """
    return dt.isoformat() + 'Z' if dt else None


def _truncate(text: str | None, limit: int = 200) -> str | None:
    """error 截断 + 省略号（design-voice D17：截断必须可见，否则运维以为看全了）。"""
    if text is None or len(text) <= limit:
        return text
    return text[:limit] + '…'


@router.get('/health')
def system_health(session: Session = Depends(get_session_dep)):
    sources = session.exec(select(ApiSourceHealth)).all()
    return {
        'sources': [
            {
                'source': s.source,
                'status': s.status,
                'alerted': s.alerted,
                'error': _truncate(s.error),
                'last_success_at': _iso_utc(s.last_success_at),
                'down_since': _iso_utc(s.down_since),
            }
            for s in sources
        ]
    }
```

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
```

模板（design-voice D2：这是**在既有 `source-item` div 内插入**两个 span——
644 行 `v-if="health.length > 0"` 与 650 行 `v-else class="empty-tip"` 保持
逐字节不动；下列片段仅示意插入位置，不是整段替换。插入点一：source-name 与
source-status 之间放 meta（时长 + error 摘要）；插入点二：source-status 前放
alerted 标签（区分「挂了」与「挂了且已叫人」））：

```html
            <div v-for="s in health" :key="s.source" class="source-item">
              <span class="source-name">{{ s.source }}</span>
              <span class="source-meta">
                {{ s.down_since ? `已故障 ${fmtDuration(s.down_since)}` : (s.last_success_at ? `最后成功 ${fmtDuration(s.last_success_at)}前` : '—') }}
                <span v-if="s.error" class="source-error" :title="s.error">{{ s.error }}</span>
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

`web/src/pages/Admin.test.ts` 追加（stubApi 已支持 `overrides.health`，见该文件 35-36 行）：

```typescript
it('健康卡渲染 down 状态：时长、状态色 class、告警标签、error 摘要', async () => {
  const downSince = new Date(Date.now() - 40 * 60000).toISOString();
  stubApi({
    health: {
      sources: [
        { source: 'mxnzp', status: 'down', alerted: 'alerted',
          error: 'dns boom', last_success_at: null, down_since: downSince },
        { source: 'juhe', status: 'ok', alerted: 'none',
          error: null, last_success_at: new Date().toISOString(), down_since: null },
      ],
    },
  });
  const wrapper = mountAdmin();
  await flushPromises();
  const items = wrapper.findAll('.source-item');
  expect(items).toHaveLength(2);
  const down = items[0];
  expect(down.find('.source-status').classes()).toContain('down');
  expect(down.find('.source-meta').text()).toContain('已故障');
  expect(down.find('.source-meta').text()).toContain('分钟');
  expect(down.find('.source-alert-tag').text()).toBe('已通知');
  expect(down.find('.source-error').text()).toContain('dns boom');
  // 空态回归（design-voice D2）：v-else 空态必须在 health 为空时仍渲染
});

it('健康卡空态保留（v-else 不被模板改动删除）', async () => {
  stubApi({ health: { sources: [] } });
  const wrapper = mountAdmin();
  await flushPromises();
  expect(wrapper.find('.empty-tip').exists()).toBe(true);
});
```

（挂载/flush 辅助以该文件既有写法为准——复用现有 mountAdmin/stubApi/flushPromises 模式；名字不同则对齐既有 helper。）

- [ ] **Step 6: 前后端验证**

Run: `uv run pytest tests/api/test_admin.py -q && npm --prefix web run build && npm --prefix web run test`
Expected: pytest PASS；vue-tsc + vite build exit 0；vitest PASS（含新增 2 个健康卡用例）。

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

- [ ] **Step 1: docs/deploy.md 新增「数据源健康告警」小节（dx-voice F15/F16）**

spec 是开发文档，不是半夜被叫醒的运维会打开的东西——运维事实必须落在 deploy.md。
小节内容大纲（照此撰写，含可复制命令）：

```markdown
## 数据源健康告警

- 机制：fetch 按源三态（ok/down/permanent）落 `api_source_health` 表；
  `down` 持续 ≥ `SOURCE_HEALTH_ALERT_AFTER_MINUTES`（默认 30 分钟）→ admin Bark；
  恢复 → 「已恢复」通知（故障时长）。送达失败下轮重试直到送达。
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
from app.services.source_health import record_source_health, evaluate_source_alerts
from app.notifications.admin_alert import build_admin_alert
eng = build_engine(get_settings().database_url)
record_source_health(eng, 'mxnzp', 'down', 'manual smoke')
evaluate_source_alerts(eng, build_admin_alert())
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
Expected: 全部 PASS（基线 754+1 skipped 之上加本计划新增用例；无既有测试因健康落表/评估接线失败）。

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
- 类型一致性：`record_source_health(engine, source, outcome, error, now)` 与 `evaluate_source_alerts(engine, send_alert, now)` 的签名在 Task 3/4/5 间一致；`_try_fetch` 三元组在 fetch_and_store 与 _grace_refetch 两处解包一致。

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

<!-- autoplan-baseline-edits:ceo {"sourceSha256":"67e868c2ffbac00d6e3f0d66e854c5040926ab50b528ae12e9c4e97958547c7f","replacements":[{"oldText":"from app.config import reset_settings_cache\n\n\ndef test_build_admin_alert_none_without_key(monkeypatch):\n    \"\"\"ADMIN_BARK_KEY 未配 → 返回 None（调用方据此只写日志不发）。\"\"\"\n    reset_settings_cache()\n    monkeypatch.delenv('ADMIN_BARK_KEY', raising=False)\n    monkeypatch.setenv('JWT_SECRET', 'x' * 32)\n    monkeypatch.setenv('CRYPTO_KEY_V1', 'x' * 44)\n    from app.notifications.admin_alert import build_admin_alert\n\n    assert build_admin_alert() is None\n\n\ndef test_build_admin_alert_sends_bark_with_key(monkeypatch):\n    \"\"\"配 key → 返回 callable，调用时经 BarkChannel 发送 title/body。\"\"\"\n    reset_settings_cache()\n    monkeypatch.setenv('JWT_SECRET', 'x' * 32)\n    monkeypatch.setenv('CRYPTO_KEY_V1', 'x' * 44)\n    monkeypatch.setenv('ADMIN_BARK_KEY', 'test-key')\n    from app.notifications import admin_alert as mod\n\n    bark = MagicMock()\n    with patch.object(mod, 'BarkChannel', return_value=bark):\n        alert = mod.build_admin_alert()\n        alert('标题', '正文')\n    bark.send.assert_called_once()\n    payload = bark.send.call_args.args[0]\n    assert payload.title == '标题' and payload.body == '正文'\n```\n\n（若 conftest 已有 settings 环境注入 fixture，优先复用其模式；`CRYPTO_KEY_V1` 必须是合法 Fernet key 时改用 `Fernet.generate_key().decode()`——以本仓库其他测试（如 tests/api/test_health.py）的写法为准。）\n\n- [ ] **Step 2: 跑测试确认失败**\n\nRun: `uv run pytest tests/notifications/test_admin_alert.py -q`\nExpected: FAIL（`ModuleNotFoundError: app.notifications.admin_alert`）。\n\n- [ ] **Step 3: 迁移实现**\n\n`app/notifications/admin_alert.py`：\n\n```python\n\"\"\"admin Bark 告警构造（plan-11：自 app/api/auth.py 迁出，auth 与 scheduler 共用）。\n\n运维兜底通道：不走 Notifier/用户渠道体系（无 NotificationLog、无 DND）——\nadmin 告警的价值在「系统级故障时也能叫到人」，必须绕开业务通知管线。\n\"\"\"\n\nfrom collections.abc import Callable\n\nfrom app.notifications.bark import BarkChannel\nfrom app.notifications.base import NotificationPayload\n\n\ndef build_admin_alert() -> Callable[[str, str], None] | None:\n    \"\"\"复用 ADMIN_BARK_KEY 构造告警函数；未配 key → None（调用方降级为只记日志）。\"\"\"\n    from app.config import get_settings\n\n    key = get_settings().admin_bark_key\n    if not key:\n        return None\n    bark = BarkChannel()\n    config = {'key': key, 'url': 'https://api.day.app'}\n\n    def _alert(title: str, body: str) -> None:\n        bark.send(NotificationPayload(title=title, body=body), config)\n\n    return _alert\n```\n\n`app/api/auth.py`：删除 `_build_admin_alert` 定义（230-245 行），文件头部 import 区加\n`from app.notifications.admin_alert import build_admin_alert`，调用点 220 行改为\n`admin_alert = build_admin_alert()`。\n\n注意：`BarkChannel`/`NotificationPayload` 若在 auth.py 已无其他使用，同步移除其 import（lint-imports 会查）。\n\n- [ ] **Step 4: 新测试 + auth 回归**\n\nRun: `uv run pytest tests/notifications/test_admin_alert.py tests/api/ -q`\nExpected: 全部 PASS（auth 行为不变）。\n\n- [ ] **Step 5: Commit**\n\n```bash\ngit add app/notifications/admin_alert.py app/api/auth.py tests/notifications/test_admin_alert.py\ngit commit -m \"refactor(plan-11): build_admin_alert 迁至 notifications 共享模块（auth 行为不变）\"\n```\n\n---\n\n### Task 3: source_health 写入 + FetchService 三态接线\n\n**Files:**\n- Create: `app/services/source_health.py`（本任务只写 `record_source_health` 部分）\n- Modify: `app/services/fetch_service.py:111-121`（`_try_fetch` 三态）、`fetch_and_store` 头部、`_grace_refetch` 内 `_try_fetch` 解包处（约 195 行）\n- Test: `tests/services/test_source_health.py`\n\n**Interfaces:**\n- Produces: `record_source_health(engine: Engine, source: str, outcome: str, error: str | None) -> None`，`outcome ∈ {'ok','down','permanent'}`——Task 4/5 依赖；`FetchService._try_fetch` 返回 `(DrawNumbers | None, str, str | None)`。\n\n- [ ] **Step 1: 写失败测试**\n\n`tests/services/test_source_health.py`：\n\n```python\n\"\"\"数据源健康落表语义（plan-11 spec §1.2）。时间均 naive UTC。\"\"\"\n\nfrom datetime import datetime, timedelta\n\nfrom sqlmodel import Session\n\nfrom app.models import ApiSourceHealth\nfrom app.services.source_health import now_naive_utc, record_source_health\n\n\ndef _get(engine, source='mxnzp') -> ApiSourceHealth:\n    with Session(engine) as s:\n        return s.get(ApiSourceHealth, source)\n\n\ndef test_record_ok_sets_last_success_and_clears_down(db_engine):\n    \"\"\"ok → status=ok、last_success_at 刷新、down_since 清空、error 清空。\"\"\"\n    t0 = datetime(2026, 9, 15, 4, 0, 0)\n    record_source_health(db_engine, 'mxnzp', 'down', 'boom', now=lambda: t0)\n    record_source_health(db_engine, 'mxnzp', 'ok', now=lambda: t0 + timedelta(minutes=5))\n    h = _get(db_engine)\n    assert h.status == 'ok'\n    assert h.last_success_at == t0 + timedelta(minutes=5)\n    assert h.down_since is None and h.error is None\n\n\ndef test_record_down_keeps_first_down_since(db_engine):\n    \"\"\"连续 down：down_since 记首次失败时间，不刷新（故障起点语义）。\"\"\"\n    t0 = datetime(2026, 9, 15, 4, 0, 0)\n    record_source_health(db_engine, 'mxnzp', 'down', 'e1', now=lambda: t0)\n    record_source_health(db_engine, 'mxnzp', 'down', 'e2', now=lambda: t0 + timedelta(hours=1))\n    h = _get(db_engine)\n    assert h.status == 'down' and h.down_since == t0 and h.error == 'e2'\n\n\ndef test_record_permanent_only_writes_error(db_engine):\n    \"\"\"permanent（未配 key 等）只记 error，status/down_since/alerted 全不动。\"\"\"\n    t0 = datetime(2026, 9, 15, 4, 0, 0)\n    record_source_health(db_engine, 'juhe', 'ok', now=lambda: t0)\n    record_source_health(db_engine, 'juhe', 'permanent', 'juhe api_key not configured')\n    h = _get(db_engine, 'juhe')\n    assert h.status == 'ok' and h.down_since is None and h.alerted == 'none'\n    assert h.error == 'juhe api_key not configured'\n\n\ndef test_record_ok_on_alerted_transitions_recovering_keeps_down_since(db_engine):\n    \"\"\"已告警（alerted）的源恢复 → recovering 且保留 down_since（供恢复通知算时长）。\"\"\"\n    t0 = datetime(2026, 9, 15, 4, 0, 0)\n    record_source_health(db_engine, 'mxnzp', 'down', 'e', now=lambda: t0)\n    with Session(db_engine) as s:\n        s.get(ApiSourceHealth, 'mxnzp').alerted = 'alerted'\n        s.commit()\n    record_source_health(db_engine, 'mxnzp', 'ok', now=lambda: t0 + timedelta(hours=2))\n    h = _get(db_engine)\n    assert h.alerted == 'recovering'\n    assert h.down_since == t0  # 保留，评估侧送达恢复通知后清除\n\n\ndef test_record_down_cancels_pending_recovery(db_engine):\n    \"\"\"抖动：recovering 期间再次失败 → 回 alerted（取消待发的过时恢复通知）。\"\"\"\n    t0 = datetime(2026, 9, 15, 4, 0, 0)\n    with Session(db_engine) as s:\n        s.add(ApiSourceHealth(source='mxnzp', status='ok', alerted='recovering',\n                              down_since=t0))\n        s.commit()\n    record_source_health(db_engine, 'mxnzp', 'down', 'again', now=lambda: t0 + timedelta(minutes=1))\n    h = _get(db_engine)\n    assert h.alerted == 'alerted' and h.status == 'down'\n```\n\n- [ ] **Step 2: 跑测试确认失败**\n\nRun: `uv run pytest tests/services/test_source_health.py -q`\nExpected: FAIL（`ModuleNotFoundError: app.services.source_health`）。\n\n- [ ] **Step 3: 实现 record_source_health**\n\n`app/services/source_health.py`：\n\n```python\n\"\"\"数据源健康记录与告警评估（plan-11 / 2026-09-15 DNS 事故跟进）。\n\nApiSourceHealth 长期「有读无写」（admin 面板空表）——本模块补写路径：fetch 按\n源记录 ok/down；评估器按「down 持续 ≥30 分钟」发 admin Bark（送达才转移状态。\nDNS 教训：故障期告警通道大概率同挂，未送达必须下轮重试）。\n\n时间纪律（CLAUDE.md）：down_since/last_success_at 均 naive UTC，与\nTimestampMixin.created_at 同表示，DB 内不做 naive/aware 混比。\n\"\"\"\n\nimport logging\nfrom collections.abc import Callable\nfrom datetime import datetime, timedelta, timezone\n\nfrom sqlalchemy.engine import Engine\nfrom sqlmodel import Session, select\n\nfrom app.models import ApiSourceHealth\n\nlogger = logging.getLogger(__name__)\n\n# 「连续 2 个 tick 全失败」的时间窗实现（spec §1.3）：与 tick 次数解耦——\n# 持久、不怕容器重启（2026-09-15 事故中容器恰在故障期重启，内存计数会清零）。\nDOWN_ALERT_AFTER = timedelta(minutes=30)\n\n# now 注入点：生产用默认；测试注入固定时钟，避免真实 sleep/时间竞争。\n_NowFn = Callable[[], datetime]\n\n\ndef now_naive_utc() -> datetime:\n    return datetime.now(timezone.utc).replace(tzinfo=None)\n\n\ndef record_source_health(\n    engine: Engine,\n    source: str,\n    outcome: str,\n    error: str | None = None,\n    now: _NowFn = now_naive_utc,\n) -> None:\n    \"\"\"按源 upsert 健康表（spec §1.2 语义表）。outcome: 'ok' | 'down' | 'permanent'。\n\n    permanent（key 未配置等配置态）：仅记 error——单源部署下未配置的备源若计入\n    down 会永久故障且天天告警（juhe 不可用是长期事实，非运行故障）。\n    \"\"\"\n    t = now()\n    with Session(engine) as s:\n        h = s.get(ApiSourceHealth, source)\n        if h is None:\n            h = ApiSourceHealth(source=source)\n            s.add(h)\n        if outcome == 'ok':\n            h.status = 'ok'\n            h.last_success_at = t\n            h.error = None\n            if h.alerted == 'none':\n                h.down_since = None\n            else:\n                # 已告警过 → 待恢复通知；保留 down_since 供评估侧算故障时长。\n                h.alerted = 'recovering'\n        elif outcome == 'down':\n            h.status = 'down'\n            if h.down_since is None:\n                h.down_since = t\n            if h.alerted == 'recovering':\n                h.alerted = 'alerted'  # 抖动：取消待发的过时恢复通知\n            h.error = error\n        elif outcome == 'permanent':\n            h.error = error\n        else:\n            raise ValueError(f'unknown outcome: {outcome}')\n        s.commit()\n```\n\n- [ ] **Step 4: 跑测试确认通过**\n\nRun: `uv run pytest tests/services/test_source_health.py -q`\nExpected: 5 passed。\n\n- [ ] **Step 5: FetchService 三态接线（回归保护既有语义）**\n\n`app/services/fetch_service.py`：\n\n改 `_try_fetch`（原 111-116 行）：\n\n```python\n    def _try_fetch(\n        self, source: DrawSource, lottery_code: str\n    ) -> tuple[DrawNumbers | None, str, str | None]:\n        \"\"\"返回 (numbers, outcome, error)。outcome: 'ok' | 'down' | 'permanent'。\n\n        ok+None=未开奖（源健康）；down=运行故障（网络/限流重试耗尽）；\n        permanent=配置态错误（key 未配置等）——健康表据此区分（plan-11 spec §1.2）。\n        \"\"\"\n        try:\n            return self._fetch_with_backoff(source, lottery_code), 'ok', None\n        except Exception as exc:\n            if isinstance(exc, PermanentLookupError):\n                return None, 'permanent', str(exc)\n            return None, 'down', str(exc)\n```\n\n改 `fetch_and_store` 头部（原 118-121 行）：\n\n```python\n    def fetch_and_store(self, lottery_code: str) -> FetchResult:\n        primary, p_outcome, p_err = self._try_fetch(self._primary, lottery_code)\n        backup, b_outcome, b_err = self._try_fetch(self._backup, lottery_code)\n        # 数据源健康落表（plan-11）：写失败不得阻断抓取（spec §1.2 独立短事务）。\n        self._record_health(self._primary.name, p_outcome, p_err)\n        self._record_health(self._backup.name, b_outcome, b_err)\n        p_ok = p_outcome == 'ok'\n        b_ok = b_outcome == 'ok'\n```\n\n（后续 `if not p_ok and not b_ok:` 等分支逻辑不动——布尔语义与旧版一致。）\n\n`_grace_refetch` 内（约 195 行）原 `m2, m2_ok = self._try_fetch(missing_source, lottery_code)` 改为：\n\n```python\n        m2, m2_outcome, _m2_err = self._try_fetch(missing_source, lottery_code)\n        m2_ok = m2_outcome == 'ok'\n```\n\n`FetchService` 类内新增方法（放在 `_try_fetch` 之后）：\n\n```python\n    def _record_health(self, source_name, outcome: str, error: str | None) -> None:\n        \"\"\"写 ApiSourceHealth（plan-11）。独立短事务 + 吞异常：健康落表失败只记日志，\n        绝不阻断抓取主流程（spec §1.2）。\"\"\"\n        try:\n            from app.services.source_health import record_source_health\n\n            record_source_health(self._engine, str(source_name), outcome, error)\n        except Exception:\n            logger.warning(\n                'source_health_write_failed source=%s outcome=%s', source_name, outcome,\n                exc_info=True,\n            )\n```\n\n（函数内 import：避免 fetch_service ↔ source_health 潜在环；模块顶部 import 亦可，以 lint-imports 通过为准。）\n\n- [ ] **Step 6: 既有 fetch 套件回归**\n\nRun: `uv run pytest tests/services/test_fetch_service.py tests/adapters/ tests/integration/ -q`\nExpected: 全部 PASS（健康写入对既有断言无感——独立表、独立事务）。\n\n- [ ] **Step 7: Commit**\n\n```bash\ngit add app/services/source_health.py app/services/fetch_service.py tests/services/test_source_health.py\ngit commit -m \"feat(plan-11): fetch 按源三态落 ApiSourceHealth（ok/down/permanent，故障起点不刷新）\"\n```\n\n---\n\n### Task 4: evaluate_source_alerts 告警状态机\n\n**Files:**\n- Modify: `app/services/source_health.py`（追加评估器）\n- Test: `tests/services/test_source_health.py`（追加）\n\n**Interfaces:**\n- Consumes: Task 3 的 `record_source_health` / `DOWN_ALERT_AFTER`。\n- Produces: `evaluate_source_alerts(engine: Engine, send_alert: Callable[[str, str], None] | None, now: _NowFn = now_naive_utc) -> None`——Task 5 接线依赖。\n\n- [ ] **Step 1: 写失败测试（追加到 test_source_health.py）**\n\n```python\n# ---------- evaluate_source_alerts 状态机（spec §1.3） ----------\n\nimport logging\n\nfrom app.services.source_health import evaluate_source_alerts\n\n\nclass _Recorder:\n    \"\"\"记 send_alert 调用；可控抛异常模拟「告警通道也挂了」（DNS 教训）。\"\"\"\n\n    def __init__(self, fail_first=0):\n        self.calls = []\n        self.fail_first = fail_first\n\n    def __call__(self, title, body):\n        if self.fail_first > 0:\n            self.fail_first -= 1\n            raise ConnectionError('bark down')\n        self.calls.append((title, body))\n\n\ndef _seed_down(engine, down_since, alerted='none', status='down'):\n    with Session(engine) as s:\n        s.add(ApiSourceHealth(source='mxnzp', status=status, alerted=alerted,\n                              down_since=down_since, last_success_at=None))\n        s.commit()\n\n\ndef test_evaluate_no_alert_before_threshold(db_engine):\n    \"\"\"down 不足 30 分钟 → 不告警。\"\"\"\n    t = datetime(2026, 9, 15, 13, 0, 0)\n    _seed_down(db_engine, down_since=t - timedelta(minutes=29))\n    rec = _Recorder()\n    evaluate_source_alerts(db_engine, rec, now=lambda: t)\n    assert rec.calls == []\n    assert _get(db_engine).alerted == 'none'\n\n\ndef test_evaluate_alerts_once_after_threshold(db_engine):\n    \"\"\"down ≥30 分钟 → 告警一次并置 alerted；继续 down 不重复告警。\"\"\"\n    t = datetime(2026, 9, 15, 13, 0, 0)\n    _seed_down(db_engine, down_since=t - timedelta(minutes=31))\n    rec = _Recorder()\n    evaluate_source_alerts(db_engine, rec, now=lambda: t)\n    evaluate_source_alerts(db_engine, rec, now=lambda: t + timedelta(minutes=15))\n    assert len(rec.calls) == 1 and '持续失败' in rec.calls[0][0]\n    assert _get(db_engine).alerted == 'alerted'\n\n\ndef test_evaluate_send_failure_keeps_state_and_retries(db_engine):\n    \"\"\"发送异常 → 状态保持 none，下轮重试；送达成功才转移（DNS 教训回归）。\"\"\"\n    t = datetime(2026, 9, 15, 13, 0, 0)\n    _seed_down(db_engine, down_since=t - timedelta(minutes=40))\n    rec = _Recorder(fail_first=1)\n    evaluate_source_alerts(db_engine, rec, now=lambda: t)\n    assert rec.calls == [] and _get(db_engine).alerted == 'none'\n    evaluate_source_alerts(db_engine, rec, now=lambda: t + timedelta(minutes=15))\n    assert len(rec.calls) == 1 and _get(db_engine).alerted == 'alerted'\n\n\ndef test_recovery_notice_sent_then_reset(db_engine):\n    \"\"\"alerted → 抓取恢复（写入侧置 recovering）→ 评估送出恢复通知 → none+清 down_since。\"\"\"\n    t = datetime(2026, 9, 15, 13, 0, 0)\n    _seed_down(db_engine, down_since=t - timedelta(hours=2), alerted='alerted')\n    record_source_health(db_engine, 'mxnzp', 'ok', now=lambda: t)  # → recovering\n    rec = _Recorder()\n    evaluate_source_alerts(db_engine, rec, now=lambda: t)\n    assert len(rec.calls) == 1 and '恢复' in rec.calls[0][0]\n    h = _get(db_engine)\n    assert h.alerted == 'none' and h.down_since is None\n\n\ndef test_recovery_send_failure_retries(db_engine):\n    \"\"\"恢复通知发送失败 → 保持 recovering，下轮送达才回 none。\"\"\"\n    t = datetime(2026, 9, 15, 13, 0, 0)\n    _seed_down(db_engine, down_since=t - timedelta(hours=2), alerted='alerted')\n    record_source_health(db_engine, 'mxnzp', 'ok', now=lambda: t)\n    rec = _Recorder(fail_first=1)\n    evaluate_source_alerts(db_engine, rec, now=lambda: t)\n    assert _get(db_engine).alerted == 'recovering'\n    evaluate_source_alerts(db_engine, rec, now=lambda: t + timedelta(minutes=15))\n    assert _get(db_engine).alerted == 'none'\n\n\ndef test_no_sender_still_transitions(db_engine):\n    \"\"\"ADMIN_BARK_KEY 未配（send_alert=None）→ 不发送但状态照常流转（表可见 down）。\"\"\"\n    t = datetime(2026, 9, 15, 13, 0, 0)\n    _seed_down(db_engine, down_since=t - timedelta(minutes=40))\n    evaluate_source_alerts(db_engine, None, now=lambda: t)\n    assert _get(db_engine).alerted == 'alerted'\n```\n\n- [ ] **Step 2: 跑测试确认失败**\n\nRun: `uv run pytest tests/services/test_source_health.py -q -k evaluate`\nExpected: FAIL（ImportError: evaluate_source_alerts）。\n\n- [ ] **Step 3: 实现评估器（追加到 source_health.py）**\n\n```python\ndef evaluate_source_alerts(\n    engine: Engine,\n    send_alert: Callable[[str, str], None] | None,\n    now: _NowFn = now_naive_utc,\n) -> None:\n    \"\"\"评估健康表驱动告警状态机（spec §1.3；挂载于 path_a tick 尾 + 启动 backfill 尾）。\n\n    send_alert=None（ADMIN_BARK_KEY 未配）→ 只做状态转移不发送，admin 面板仍可见。\n    任何发送异常保持原状态（continue 不 commit 该行变更），下轮重试直到送达——\n    2026-09-15 DNS 事故教训：故障期告警通道大概率同时挂。\n    \"\"\"\n    t = now()\n    with Session(engine) as s:\n        for h in s.exec(select(ApiSourceHealth)).all():\n            if (\n                h.status == 'down'\n                and h.alerted == 'none'\n                and h.down_since is not None\n                and t - h.down_since >= DOWN_ALERT_AFTER\n            ):\n                minutes = int((t - h.down_since).total_seconds() // 60)\n                if send_alert is not None:\n                    try:\n                        send_alert(\n                            '开奖抓取持续失败',\n                            f'数据源 {h.source} 已持续失败约 {minutes} 分钟'\n                            f'（自 {h.down_since} 起），最近错误：{(h.error or \"\")[:200]}',\n                        )\n                    except Exception:\n                        logger.warning(\n                            'source_alert_send_failed source=%s', h.source, exc_info=True\n                        )\n                        continue  # 未送达不转移，下轮重试\n                h.alerted = 'alerted'\n            elif h.alerted == 'recovering' and h.status == 'ok':\n                duration = t - h.down_since if h.down_since else timedelta(0)\n                minutes = int(duration.total_seconds() // 60)\n                if send_alert is not None:\n                    try:\n                        send_alert(\n                            '开奖抓取已恢复',\n                            f'数据源 {h.source} 已恢复抓取（故障持续约 {minutes} 分钟）',\n                        )\n                    except Exception:\n                        logger.warning(\n                            'source_recovery_send_failed source=%s', h.source, exc_info=True\n                        )\n                        continue\n                h.alerted = 'none'\n                h.down_since = None\n        s.commit()\n```\n\n（注意：`continue` 跳过的是该行的状态转移；Session 内其他行继续评估。）\n\n- [ ] **Step 4: 跑测试确认通过**\n\nRun: `uv run pytest tests/services/test_source_health.py -q`\nExpected: 11 passed。\n\n- [ ] **Step 5: Commit**\n\n```bash\ngit add app/services/source_health.py tests/services/test_source_health.py\ngit commit -m \"feat(plan-11): evaluate_source_alerts 告警状态机（down≥30min 告警、恢复通知、送达才转移）\"\n```\n\n---\n\n### Task 5: 调度接线 + 启动回填 QPS 间隔\n\n**Files:**\n- Modify: `app/scheduler/jobs.py`（`_path_a_tick` 末尾 + import）\n- Modify: `app/scheduler/backfill.py`（第 4 步间隔 + 函数尾评估 + 常量）\n- Modify: `tests/conftest.py:14-26`（autouse fixture 补 backfill 常量置 0）\n- Test: `tests/scheduler/test_jobs.py`（追加）、`tests/scheduler/test_backfill.py`（追加）\n\n**Interfaces:**\n- Consumes: Task 4 `evaluate_source_alerts`、Task 2 `build_admin_alert`。\n- Produces: `_path_a_tick` 与 `run_startup_backfill` 尾部各调用一次评估；`backfill._INTER_LOTTERY_INTERVAL = 1.2`。\n\n- [ ] **Step 1: 写失败测试（test_jobs.py 追加，用仓库既有 `_invoke_job` 辅助——见文件内其他 tick 测试）**\n\n```python\ndef test_path_a_tick_evaluates_source_alerts(db_engine, monkeypatch):\n    \"\"\"path_a_tick 尾部必须评估数据源健康告警（plan-11：tick 即评估点）。\"\"\"\n    from unittest.mock import MagicMock\n\n    import app.scheduler.jobs as jobs_mod\n    from app.scheduler.setup import build_scheduler\n\n    spy = MagicMock()\n    monkeypatch.setattr(jobs_mod, 'evaluate_source_alerts', spy)\n    sched = build_scheduler(db_engine)\n    register_all_jobs(\n        sched,\n        {\n            'engine': db_engine,\n            'fetch_service': MagicMock(),\n            'compare_service': MagicMock(),\n            'refill_worker': MagicMock(),\n            'notifier': MagicMock(),\n        },\n    )\n    _invoke_job(sched, 'path_a_poll_evening')\n    assert spy.call_count == 1\n    # engine 为第一参数，sender 来自 build_admin_alert（测试无 key → None）\n    assert spy.call_args.args[0] is db_engine\n```\n\n- [ ] **Step 2: 写失败测试（test_backfill.py 追加）**\n\n```python\ndef test_startup_backfill_paces_fetches_with_interval(db_engine, monkeypatch):\n    \"\"\"启动回填对实际抓取的彩种加 QPS 间隔：第 2 个起每次 fetch 前 sleep（plan-11）。\"\"\"\n    import app.scheduler.backfill as backfill_mod\n    from app.models import LotteryType\n\n    monkeypatch.setattr(backfill_mod, '_INTER_LOTTERY_INTERVAL', 1.2)\n    sleeps = []\n    monkeypatch.setattr(backfill_mod.time, 'sleep', lambda s: sleeps.append(s))\n\n    # 3 个彩种全部 missed（DB 无开奖 + draw_days 覆盖回看窗口）\n    monkeypatch.setattr(\n        backfill_mod, '_enabled_lotteries',\n        lambda engine: [('a', [0, 1, 2, 3, 4, 5, 6]), ('b', [0, 1, 2, 3, 4, 5, 6]),\n                        ('c', [0, 1, 2, 3, 4, 5, 6])],\n    )\n    monkeypatch.setattr(backfill_mod, '_has_draw_for_date', lambda engine, code, d: False)\n\n    deps = _make_deps(db_engine)\n    run_startup_backfill(deps)\n    assert deps['fetch_service'].fetch_and_store.call_count == 3\n    assert sleeps == [1.2, 1.2]  # 首个抓取不 sleep\n\n\ndef test_startup_backfill_evaluates_source_alerts(db_engine, monkeypatch):\n    \"\"\"启动 backfill 尾部评估健康告警（plan-11：开机即评估）。\"\"\"\n    import app.scheduler.backfill as backfill_mod\n\n    spy = MagicMock()\n    monkeypatch.setattr(backfill_mod, 'evaluate_source_alerts', spy)\n    run_startup_backfill(_make_deps(db_engine))\n    assert spy.call_count == 1\n```\n\n- [ ] **Step 3: 跑两个新测试确认失败**\n\nRun: `uv run pytest tests/scheduler/test_jobs.py::test_path_a_tick_evaluates_source_alerts tests/scheduler/test_backfill.py::test_startup_backfill_paces_fetches_with_interval tests/scheduler/test_backfill.py::test_startup_backfill_evaluates_source_alerts -q`\nExpected: 3 FAIL（AttributeError: evaluate_source_alerts / _INTER_LOTTERY_INTERVAL）。\n\n- [ ] **Step 4: 实现 jobs.py 接线**\n\n`app/scheduler/jobs.py` import 区加：\n\n```python\nfrom app.notifications.admin_alert import build_admin_alert\nfrom app.services.source_health import evaluate_source_alerts\n```\n\n`_path_a_tick` 函数末尾（`sched.add_job(_push_big_win, ...)` 循环之后、函数体结束前）追加：\n\n```python\n    # 数据源健康评估（plan-11）：tick 尾部评估告警状态机（down≥30min → admin bark；\n    # 未配 ADMIN_BARK_KEY 则 sender=None 只转移状态）。评估失败不阻断本 tick 收尾。\n    try:\n        evaluate_source_alerts(engine, build_admin_alert())\n    except Exception:\n        logger.error('source_alert_evaluate_failed', exc_info=True)\n```\n\n- [ ] **Step 5: 实现 backfill.py 间隔 + 尾部评估**\n\n`app/scheduler/backfill.py`：模块级（`_BACKFILL_LOOKBACK_DAYS = 2` 旁）加：\n\n```python\n# 彩种间抓取间隔（秒）。与 jobs._INTER_LOTTERY_INTERVAL 同源同值（MXNZP 免费 1 QPS，\n# 连续请求触发 code=101 白耗重试）；本地定义避免 backfill↔jobs 循环 import。\n# 测试经 conftest autouse fixture 置 0。\n_INTER_LOTTERY_INTERVAL = 1.2\n```\n\nimport 区加 `import time`（若未有）与 `from app.notifications.admin_alert import build_admin_alert`、`from app.services.source_health import evaluate_source_alerts`。","newText":"import pytest\nfrom cryptography.fernet import Fernet\n\nfrom app.config import reset_settings_cache\n\n\ndef test_build_admin_alert_none_without_key(monkeypatch):\n    \"\"\"ADMIN_BARK_KEY 未配 → 返回 None（调用方据此只写日志不发）。\"\"\"\n    reset_settings_cache()\n    monkeypatch.delenv('ADMIN_BARK_KEY', raising=False)\n    monkeypatch.setenv('JWT_SECRET', 'x' * 32)\n    # CRYPTO_KEY_V1 必须合法 Fernet key（autoplan M3：'x'*44 解码 33 字节非法；\n    # 仓库范式见 tests/api/test_admin.py:16）。\n    monkeypatch.setenv('CRYPTO_KEY_V1', Fernet.generate_key().decode())\n    from app.notifications.admin_alert import build_admin_alert\n\n    assert build_admin_alert() is None\n\n\ndef test_build_admin_alert_sends_bark_with_key(monkeypatch):\n    \"\"\"配 key → 返回 callable，调用时经 BarkChannel 发送 title/body。\"\"\"\n    reset_settings_cache()\n    monkeypatch.setenv('JWT_SECRET', 'x' * 32)\n    monkeypatch.setenv('CRYPTO_KEY_V1', Fernet.generate_key().decode())\n    monkeypatch.setenv('ADMIN_BARK_KEY', 'test-key')\n    from app.notifications import admin_alert as mod\n    from app.notifications.base import ChannelStatus, SendResult\n\n    bark = MagicMock()\n    bark.send.return_value = SendResult(ChannelStatus.SENT)\n    with patch.object(mod, 'BarkChannel', return_value=bark):\n        alert = mod.build_admin_alert()\n        alert('标题', '正文')\n    bark.send.assert_called_once()\n    payload = bark.send.call_args.args[0]\n    assert payload.title == '标题' and payload.body == '正文'\n\n\ndef test_build_admin_alert_raises_on_failed_send(monkeypatch):\n    \"\"\"bark 返回 FAILED（非抛异常）也必须 raise（autoplan M2 送达契约）——\n\n    BarkChannel.send 吞掉一切失败返回 SendResult（bark.py:49-50），若忽略返回值，\n    评估器「送达成功才转移」退化为「尝试即转移」，DNS 事故场景告警永不重试。\n    \"\"\"\n    reset_settings_cache()\n    monkeypatch.setenv('JWT_SECRET', 'x' * 32)\n    monkeypatch.setenv('CRYPTO_KEY_V1', Fernet.generate_key().decode())\n    monkeypatch.setenv('ADMIN_BARK_KEY', 'test-key')\n    from app.notifications import admin_alert as mod\n    from app.notifications.base import ChannelStatus, SendResult\n\n    bark = MagicMock()\n    bark.send.return_value = SendResult(ChannelStatus.FAILED, error='bark code 400')\n    with patch.object(mod, 'BarkChannel', return_value=bark):\n        alert = mod.build_admin_alert()\n        with pytest.raises(RuntimeError):\n            alert('标题', '正文')\n```\n\n（conftest 已有 `_reset_settings_and_env` autouse fixture 清环境；`CRYPTO_KEY_V1` 一律用 `Fernet.generate_key().decode()`（autoplan M3），与 tests/api/test_admin.py:16 一致。）\n\n- [ ] **Step 2: 跑测试确认失败**\n\nRun: `uv run pytest tests/notifications/test_admin_alert.py -q`\nExpected: FAIL（`ModuleNotFoundError: app.notifications.admin_alert`）。\n\n- [ ] **Step 3: 迁移实现（含 autoplan M2 送达契约）**\n\n`app/notifications/admin_alert.py`：\n\n```python\n\"\"\"admin Bark 告警构造（plan-11：自 app/api/auth.py 迁出，auth 与 scheduler 共用）。\n\n运维兜底通道：不走 Notifier/用户渠道体系（无 NotificationLog、无 DND）——\nadmin 告警的价值在「系统级故障时也能叫到人」，必须绕开业务通知管线。\n\n送达契约（autoplan M2）：BarkChannel.send 吞掉一切失败返回 SendResult 而非抛异常\n（bark.py:49-50）。若忽略返回值，调用方 try/except 永不触发，「送达成功才转移」\n退化为「尝试即转移」——2026-09-15 DNS 事故（告警通道同挂）场景下告警永不重试。\n故 status != SENT 时抛 RuntimeError，让评估器/调用方的 except 正确识别未送达。\n\"\"\"\n\nfrom collections.abc import Callable\n\nfrom app.notifications.bark import BarkChannel\nfrom app.notifications.base import ChannelStatus, NotificationPayload\n\n\ndef build_admin_alert() -> Callable[[str, str], None] | None:\n    \"\"\"复用 ADMIN_BARK_KEY 构造告警函数；未配 key → None（调用方降级为只记日志）。\n\n    返回的 callable：送达成功正常返回；未送达（HTTP 错 / 业务码非 200 / 传输异常）\n    抛 RuntimeError——调用方据此保持状态、下轮重试。\n    \"\"\"\n    from app.config import get_settings\n\n    key = get_settings().admin_bark_key\n    if not key:\n        return None\n    bark = BarkChannel()\n    config = {'key': key, 'url': 'https://api.day.app'}\n\n    def _alert(title: str, body: str) -> None:\n        result = bark.send(NotificationPayload(title=title, body=body), config)\n        if result.status != ChannelStatus.SENT:\n            raise RuntimeError(f'admin bark 未送达: {result.error}')\n\n    return _alert\n```\n\n`app/api/auth.py`：删除 `_build_admin_alert` 定义（230-243 行），文件头部 import 区加\n`from app.notifications.admin_alert import build_admin_alert`，调用点 220 行改为\n`admin_alert = build_admin_alert()`。\n\n注意：`BarkChannel`/`NotificationPayload` 在 auth.py 是 `_build_admin_alert` 内函数级 import，随定义一并删除即可（lint-imports 会查）。\n\nM2 行为变化说明：password_reset_service.py:248-255 对 admin_alert 已有 try/except\n（失败记 `password_reset_admin_alert_failed` 日志）——新版 raise 被该 except 兼容，\n效果从「失败静默」变为「失败留痕」，是纯改进；既有测试（tests/services/\ntest_password_reset_service.py:163 用 stub callable）不受影响。\n\n- [ ] **Step 4: 新测试 + auth 回归**\n\nRun: `uv run pytest tests/notifications/test_admin_alert.py tests/api/ -q`\nExpected: 全部 PASS（auth 行为不变）。\n\n- [ ] **Step 5: Commit**\n\n```bash\ngit add app/notifications/admin_alert.py app/api/auth.py tests/notifications/test_admin_alert.py\ngit commit -m \"refactor(plan-11): build_admin_alert 迁至 notifications 共享模块（auth 行为不变）\"\n```\n\n---\n\n### Task 3: source_health 写入 + FetchService 三态接线\n\n**Files:**\n- Create: `app/services/source_health.py`（本任务只写 `record_source_health` 部分）\n- Modify: `app/services/fetch_service.py:111-121`（`_try_fetch` 三态）、`fetch_and_store` 头部、`_grace_refetch` 内 `_try_fetch` 解包处（约 195 行）\n- Test: `tests/services/test_source_health.py`\n\n**Interfaces:**\n- Produces: `record_source_health(engine: Engine, source: str, outcome: str, error: str | None) -> None`，`outcome ∈ {'ok','down','permanent'}`——Task 4/5 依赖；`FetchService._try_fetch` 返回 `(DrawNumbers | None, str, str | None)`。\n\n- [ ] **Step 1: 写失败测试**\n\n`tests/services/test_source_health.py`：\n\n```python\n\"\"\"数据源健康落表语义（plan-11 spec §1.2）。时间均 naive UTC。\"\"\"\n\nfrom datetime import datetime, timedelta\n\nfrom sqlmodel import Session\n\nfrom app.models import ApiSourceHealth\nfrom app.services.source_health import now_naive_utc, record_source_health\n\n\ndef _get(engine, source='mxnzp') -> ApiSourceHealth:\n    with Session(engine) as s:\n        return s.get(ApiSourceHealth, source)\n\n\ndef test_record_ok_sets_last_success_and_clears_down(db_engine):\n    \"\"\"ok → status=ok、last_success_at 刷新、down_since 清空、error 清空。\"\"\"\n    t0 = datetime(2026, 9, 15, 4, 0, 0)\n    record_source_health(db_engine, 'mxnzp', 'down', 'boom', now=lambda: t0)\n    record_source_health(db_engine, 'mxnzp', 'ok', now=lambda: t0 + timedelta(minutes=5))\n    h = _get(db_engine)\n    assert h.status == 'ok'\n    assert h.last_success_at == t0 + timedelta(minutes=5)\n    assert h.down_since is None and h.error is None\n\n\ndef test_record_down_keeps_first_down_since(db_engine):\n    \"\"\"连续 down：down_since 记首次失败时间，不刷新（故障起点语义）。\"\"\"\n    t0 = datetime(2026, 9, 15, 4, 0, 0)\n    record_source_health(db_engine, 'mxnzp', 'down', 'e1', now=lambda: t0)\n    record_source_health(db_engine, 'mxnzp', 'down', 'e2', now=lambda: t0 + timedelta(hours=1))\n    h = _get(db_engine)\n    assert h.status == 'down' and h.down_since == t0 and h.error == 'e2'\n\n\ndef test_record_permanent_only_writes_error(db_engine):\n    \"\"\"permanent（未配 key 等）只记 error，status/down_since/alerted 全不动。\"\"\"\n    t0 = datetime(2026, 9, 15, 4, 0, 0)\n    record_source_health(db_engine, 'juhe', 'ok', now=lambda: t0)\n    record_source_health(db_engine, 'juhe', 'permanent', 'juhe api_key not configured')\n    h = _get(db_engine, 'juhe')\n    assert h.status == 'ok' and h.down_since is None and h.alerted == 'none'\n    assert h.error == 'juhe api_key not configured'\n\n\ndef test_record_ok_on_alerted_transitions_recovering_keeps_down_since(db_engine):\n    \"\"\"已告警（alerted）的源恢复 → recovering 且保留 down_since（供恢复通知算时长）。\"\"\"\n    t0 = datetime(2026, 9, 15, 4, 0, 0)\n    record_source_health(db_engine, 'mxnzp', 'down', 'e', now=lambda: t0)\n    with Session(db_engine) as s:\n        s.get(ApiSourceHealth, 'mxnzp').alerted = 'alerted'\n        s.commit()\n    record_source_health(db_engine, 'mxnzp', 'ok', now=lambda: t0 + timedelta(hours=2))\n    h = _get(db_engine)\n    assert h.alerted == 'recovering'\n    assert h.down_since == t0  # 保留，评估侧送达恢复通知后清除\n\n\ndef test_record_down_cancels_pending_recovery(db_engine):\n    \"\"\"抖动：recovering 期间再次失败 → 回 alerted（取消待发的过时恢复通知）。\"\"\"\n    t0 = datetime(2026, 9, 15, 4, 0, 0)\n    with Session(db_engine) as s:\n        s.add(ApiSourceHealth(source='mxnzp', status='ok', alerted='recovering',\n                              down_since=t0))\n        s.commit()\n    record_source_health(db_engine, 'mxnzp', 'down', 'again', now=lambda: t0 + timedelta(minutes=1))\n    h = _get(db_engine)\n    assert h.alerted == 'alerted' and h.status == 'down'\n\n\ndef test_record_ok_after_long_outage_without_delivered_alert_goes_recovering(db_engine):\n    \"\"\"长故障期间告警从未送达（通道同挂，DNS 教训）→ 恢复时不得静默清零——\n\n    置 recovering 补发恢复通知（autoplan M11：否则 9 天故障自愈 = 零通知）。\n    \"\"\"\n    t0 = datetime(2026, 9, 1, 4, 0, 0)\n    record_source_health(db_engine, 'mxnzp', 'down', 'e', now=lambda: t0)\n    # 故障期每次评估尝试告警均失败（状态保持 none），第 9 天直接恢复\n    record_source_health(db_engine, 'mxnzp', 'ok', now=lambda: t0 + timedelta(days=9))\n    h = _get(db_engine)\n    assert h.alerted == 'recovering' and h.down_since == t0\n\n\ndef test_record_ok_after_short_outage_clears_silently(db_engine):\n    \"\"\"短故障（<30min，从未达告警阈值）恢复 → 清 down_since，不留 recovering（M11 边界）。\"\"\"\n    t0 = datetime(2026, 9, 15, 4, 0, 0)\n    record_source_health(db_engine, 'mxnzp', 'down', 'e', now=lambda: t0)\n    record_source_health(db_engine, 'mxnzp', 'ok', now=lambda: t0 + timedelta(minutes=10))\n    h = _get(db_engine)\n    assert h.alerted == 'none' and h.down_since is None\n\n\ndef test_record_error_redacts_secret_query_params(db_engine):\n    \"\"\"error 含 URL query 密钥（juhe key= 等）→ 落表前脱敏（autoplan M13）。\n\n    juhe.py:27 把 api key 放 query，raise_for_status 异常消息含完整 URL；\n    健康表 error 会进 admin 面板与 Bark 告警体（第三方服务器），密钥不得外泄。\n    \"\"\"\n    record_source_health(\n        db_engine, 'juhe', 'down',\n        \"Client error '403' for url 'https://v.juhe.cn/lottery/query?lottery_id=ssq&key=SECRETKEY123'\",\n    )\n    h = _get(db_engine, 'juhe')\n    assert 'SECRETKEY123' not in (h.error or '')\n    assert 'key=[REDACTED]' in (h.error or '')\n```\n\n- [ ] **Step 2: 跑测试确认失败**\n\nRun: `uv run pytest tests/services/test_source_health.py -q`\nExpected: FAIL（`ModuleNotFoundError: app.services.source_health`）。\n\n- [ ] **Step 3: 实现 record_source_health（含 autoplan M11/M13）**\n\n`app/services/source_health.py`：\n\n```python\n\"\"\"数据源健康记录与告警评估（plan-11 / 2026-09-15 DNS 事故跟进）。\n\nApiSourceHealth 长期「有读无写」（admin 面板空表）——本模块补写路径：fetch 按\n源记录 ok/down；评估器按「down 持续 ≥30 分钟」发 admin Bark（送达才转移状态。\nDNS 教训：故障期告警通道大概率同挂，未送达必须下轮重试）。\n\n时间纪律（CLAUDE.md）：down_since/last_success_at 均 naive UTC，与\nTimestampMixin.created_at 同表示，DB 内不做 naive/aware 混比。\n\"\"\"\n\nimport logging\nimport re\nfrom collections.abc import Callable\nfrom datetime import datetime, timedelta, timezone\n\nfrom sqlalchemy.engine import Engine\nfrom sqlmodel import Session, select\n\nfrom app.models import ApiSourceHealth\n\nlogger = logging.getLogger(__name__)\n\n# 「连续 2 个 tick 全失败」的时间窗实现（spec §1.3）：与 tick 次数解耦——\n# 持久、不怕容器重启（2026-09-15 事故中容器恰在故障期重启，内存计数会清零）。\nDOWN_ALERT_AFTER = timedelta(minutes=30)\n\n# error 脱敏（autoplan M13）：juhe 把 api key 放 query（juhe.py:27），\n# raise_for_status 异常消息含完整 URL；健康表 error 会进 admin 面板与 Bark\n# 告警体（第三方服务器），密钥参数值落表前一律替换 [REDACTED]。\n_SENSITIVE_QUERY_RE = re.compile(r'([?&](?:key|app_id|app_secret|token)=)[^&\\s]+')\n\n# now 注入点：生产用默认；测试注入固定时钟，避免真实 sleep/时间竞争。\n_NowFn = Callable[[], datetime]\n\n\ndef now_naive_utc() -> datetime:\n    return datetime.now(timezone.utc).replace(tzinfo=None)\n\n\ndef _sanitize_error(error: str | None) -> str | None:\n    \"\"\"剥离 error 文本中的敏感 query 参数值（M13），保留 URL 其余部分供排障。\"\"\"\n    if error is None:\n        return None\n    return _SENSITIVE_QUERY_RE.sub(r'\\1[REDACTED]', error)\n\n\ndef record_source_health(\n    engine: Engine,\n    source: str,\n    outcome: str,\n    error: str | None = None,\n    now: _NowFn = now_naive_utc,\n) -> None:\n    \"\"\"按源 upsert 健康表（spec §1.2 语义表）。outcome: 'ok' | 'down' | 'permanent'。\n\n    permanent（key 未配置等配置态）：仅记 error——单源部署下未配置的备源若计入\n    down 会永久故障且天天告警（juhe 不可用是长期事实，非运行故障）。\n\n    ok + alerted=='none' + 故障时长 ≥ DOWN_ALERT_AFTER（autoplan M11）：转\n    recovering 补发恢复通知——故障期告警通道同挂（DNS 教训）导致告警从未送达，\n    恢复时若静默清零，长故障将零通知（9 天事故复现路径）。\n    \"\"\"\n    t = now()\n    with Session(engine) as s:\n        h = s.get(ApiSourceHealth, source)\n        if h is None:\n            h = ApiSourceHealth(source=source)\n            s.add(h)\n        if outcome == 'ok':\n            h.status = 'ok'\n            h.last_success_at = t\n            h.error = None\n            if h.alerted == 'none':\n                if h.down_since is not None and t - h.down_since >= DOWN_ALERT_AFTER:\n                    h.alerted = 'recovering'  # M11：长故障零送达 → 补发恢复通知\n                else:\n                    h.down_since = None  # 短故障：静默恢复（未达告警阈值，无通知义务）\n            else:\n                # 已告警过 → 待恢复通知；保留 down_since 供评估侧算故障时长。\n                h.alerted = 'recovering'\n        elif outcome == 'down':\n            h.status = 'down'\n            if h.down_since is None:\n                h.down_since = t\n            if h.alerted == 'recovering':\n                h.alerted = 'alerted'  # 抖动：取消待发的过时恢复通知\n            h.error = _sanitize_error(error)\n        elif outcome == 'permanent':\n            h.error = _sanitize_error(error)\n        else:\n            raise ValueError(f'unknown outcome: {outcome}')\n        s.commit()\n```\n\n- [ ] **Step 4: 跑测试确认通过**\n\nRun: `uv run pytest tests/services/test_source_health.py -q`\nExpected: 8 passed（原 5 + M11×2 + M13×1）。\n\n- [ ] **Step 5: FetchService 三态接线（回归保护既有语义）**\n\n`app/services/fetch_service.py`：\n\n改 `_try_fetch`（原 111-116 行）：\n\n```python\n    def _try_fetch(\n        self, source: DrawSource, lottery_code: str\n    ) -> tuple[DrawNumbers | None, str, str | None]:\n        \"\"\"返回 (numbers, outcome, error)。outcome: 'ok' | 'down' | 'permanent'。\n\n        ok+None=未开奖（源健康）；down=运行故障（网络/限流重试耗尽）；\n        permanent=配置态错误（key 未配置等）——健康表据此区分（plan-11 spec §1.2）。\n        \"\"\"\n        try:\n            return self._fetch_with_backoff(source, lottery_code), 'ok', None\n        except Exception as exc:\n            if isinstance(exc, PermanentLookupError):\n                return None, 'permanent', str(exc)\n            return None, 'down', str(exc)\n```\n\n改 `fetch_and_store` 头部（原 118-121 行）：\n\n```python\n    def fetch_and_store(self, lottery_code: str) -> FetchResult:\n        primary, p_outcome, p_err = self._try_fetch(self._primary, lottery_code)\n        backup, b_outcome, b_err = self._try_fetch(self._backup, lottery_code)\n        # 数据源健康落表（plan-11）：写失败不得阻断抓取（spec §1.2 独立短事务）。\n        self._record_health(self._primary.name, p_outcome, p_err)\n        self._record_health(self._backup.name, b_outcome, b_err)\n        p_ok = p_outcome == 'ok'\n        b_ok = b_outcome == 'ok'\n```\n\n（后续 `if not p_ok and not b_ok:` 等分支逻辑不动——布尔语义与旧版一致。）\n\n`_grace_refetch` 内（约 195 行）原 `m2, m2_ok = self._try_fetch(missing_source, lottery_code)` 改为：\n\n```python\n        m2, m2_outcome, m2_err = self._try_fetch(missing_source, lottery_code)\n        # grace 重抓结果同样落健康表（autoplan M7）：否则 grace 内恢复的源要等下个\n        # 抓取周期才转 ok，恢复通知无谓延迟一整轮（15 分钟）。\n        self._record_health(str(missing_source.name), m2_outcome, m2_err)\n        m2_ok = m2_outcome == 'ok'\n```\n\n`FetchService` 类内新增方法（放在 `_try_fetch` 之后）：\n\n```python\n    def _record_health(self, source_name, outcome: str, error: str | None) -> None:\n        \"\"\"写 ApiSourceHealth（plan-11）。独立短事务 + 吞异常：健康落表失败只记日志，\n        绝不阻断抓取主流程（spec §1.2）。\"\"\"\n        try:\n            from app.services.source_health import record_source_health\n\n            record_source_health(self._engine, str(source_name), outcome, error)\n        except Exception:\n            logger.warning(\n                'source_health_write_failed source=%s outcome=%s', source_name, outcome,\n                exc_info=True,\n            )\n```\n\n（函数内 import：避免 fetch_service ↔ source_health 潜在环；模块顶部 import 亦可，以 lint-imports 通过为准。）\n\n- [ ] **Step 6: 既有 fetch 套件回归**\n\nRun: `uv run pytest tests/services/test_fetch_service.py tests/adapters/ tests/integration/ -q`\nExpected: 全部 PASS（健康写入对既有断言无感——独立表、独立事务）。\n\n- [ ] **Step 7: Commit**\n\n```bash\ngit add app/services/source_health.py app/services/fetch_service.py tests/services/test_source_health.py\ngit commit -m \"feat(plan-11): fetch 按源三态落 ApiSourceHealth（ok/down/permanent，故障起点不刷新）\"\n```\n\n---\n\n### Task 4: evaluate_source_alerts 告警状态机\n\n**Files:**\n- Modify: `app/services/source_health.py`（追加评估器）\n- Test: `tests/services/test_source_health.py`（追加）\n\n**Interfaces:**\n- Consumes: Task 3 的 `record_source_health` / `DOWN_ALERT_AFTER`。\n- Produces: `evaluate_source_alerts(engine: Engine, send_alert: Callable[[str, str], None] | None, now: _NowFn = now_naive_utc) -> None`——Task 5 接线依赖。\n\n- [ ] **Step 1: 写失败测试（追加到 test_source_health.py）**\n\n```python\n# ---------- evaluate_source_alerts 状态机（spec §1.3） ----------\n\nimport logging\n\nfrom app.services.source_health import evaluate_source_alerts\n\n\nclass _Recorder:\n    \"\"\"记 send_alert 调用；可控抛异常模拟「告警通道也挂了」（DNS 教训）。\"\"\"\n\n    def __init__(self, fail_first=0):\n        self.calls = []\n        self.fail_first = fail_first\n\n    def __call__(self, title, body):\n        if self.fail_first > 0:\n            self.fail_first -= 1\n            raise ConnectionError('bark down')\n        self.calls.append((title, body))\n\n\ndef _seed_down(engine, down_since, alerted='none', status='down'):\n    with Session(engine) as s:\n        s.add(ApiSourceHealth(source='mxnzp', status=status, alerted=alerted,\n                              down_since=down_since, last_success_at=None))\n        s.commit()\n\n\ndef test_evaluate_no_alert_before_threshold(db_engine):\n    \"\"\"down 不足 30 分钟 → 不告警。\"\"\"\n    t = datetime(2026, 9, 15, 13, 0, 0)\n    _seed_down(db_engine, down_since=t - timedelta(minutes=29))\n    rec = _Recorder()\n    evaluate_source_alerts(db_engine, rec, now=lambda: t)\n    assert rec.calls == []\n    assert _get(db_engine).alerted == 'none'\n\n\ndef test_evaluate_alerts_once_after_threshold(db_engine):\n    \"\"\"down ≥30 分钟 → 告警一次并置 alerted；继续 down 不重复告警。\"\"\"\n    t = datetime(2026, 9, 15, 13, 0, 0)\n    _seed_down(db_engine, down_since=t - timedelta(minutes=31))\n    rec = _Recorder()\n    evaluate_source_alerts(db_engine, rec, now=lambda: t)\n    evaluate_source_alerts(db_engine, rec, now=lambda: t + timedelta(minutes=15))\n    assert len(rec.calls) == 1 and '持续失败' in rec.calls[0][0]\n    assert _get(db_engine).alerted == 'alerted'\n\n\ndef test_evaluate_send_failure_keeps_state_and_retries(db_engine):\n    \"\"\"发送异常 → 状态保持 none，下轮重试；送达成功才转移（DNS 教训回归）。\"\"\"\n    t = datetime(2026, 9, 15, 13, 0, 0)\n    _seed_down(db_engine, down_since=t - timedelta(minutes=40))\n    rec = _Recorder(fail_first=1)\n    evaluate_source_alerts(db_engine, rec, now=lambda: t)\n    assert rec.calls == [] and _get(db_engine).alerted == 'none'\n    evaluate_source_alerts(db_engine, rec, now=lambda: t + timedelta(minutes=15))\n    assert len(rec.calls) == 1 and _get(db_engine).alerted == 'alerted'\n\n\ndef test_recovery_notice_sent_then_reset(db_engine):\n    \"\"\"alerted → 抓取恢复（写入侧置 recovering）→ 评估送出恢复通知 → none+清 down_since。\"\"\"\n    t = datetime(2026, 9, 15, 13, 0, 0)\n    _seed_down(db_engine, down_since=t - timedelta(hours=2), alerted='alerted')\n    record_source_health(db_engine, 'mxnzp', 'ok', now=lambda: t)  # → recovering\n    rec = _Recorder()\n    evaluate_source_alerts(db_engine, rec, now=lambda: t)\n    assert len(rec.calls) == 1 and '恢复' in rec.calls[0][0]\n    h = _get(db_engine)\n    assert h.alerted == 'none' and h.down_since is None\n\n\ndef test_recovery_send_failure_retries(db_engine):\n    \"\"\"恢复通知发送失败 → 保持 recovering，下轮送达才回 none。\"\"\"\n    t = datetime(2026, 9, 15, 13, 0, 0)\n    _seed_down(db_engine, down_since=t - timedelta(hours=2), alerted='alerted')\n    record_source_health(db_engine, 'mxnzp', 'ok', now=lambda: t)\n    rec = _Recorder(fail_first=1)\n    evaluate_source_alerts(db_engine, rec, now=lambda: t)\n    assert _get(db_engine).alerted == 'recovering'\n    evaluate_source_alerts(db_engine, rec, now=lambda: t + timedelta(minutes=15))\n    assert _get(db_engine).alerted == 'none'\n\n\ndef test_no_sender_still_transitions(db_engine):\n    \"\"\"ADMIN_BARK_KEY 未配（send_alert=None）→ 不发送但状态照常流转（表可见 down）。\"\"\"\n    t = datetime(2026, 9, 15, 13, 0, 0)\n    _seed_down(db_engine, down_since=t - timedelta(minutes=40))\n    evaluate_source_alerts(db_engine, None, now=lambda: t)\n    assert _get(db_engine).alerted == 'alerted'\n\n\ndef test_evaluate_sends_outside_db_session(db_engine):\n    \"\"\"M1 回归：send_alert 调用时评估器不得持有 DB 连接（pool_size=1 纪律）。\n\n    若评估器在 session 内发送（plan 原稿），DNS 故障下 Bark 挂 10s 超时期间\n    唯一连接被占，其他 job/请求撞 busy_timeout——jobs.py:276-278 两次事故同型。\n    \"\"\"\n    t = datetime(2026, 9, 15, 13, 0, 0)\n    _seed_down(db_engine, down_since=t - timedelta(minutes=40))\n    checked_out_during_send = []\n\n    def _sender(title, body):\n        checked_out_during_send.append(db_engine.pool.checkedout())\n\n    evaluate_source_alerts(db_engine, _sender, now=lambda: t)\n    assert checked_out_during_send == [0]\n```\n\n- [ ] **Step 2: 跑测试确认失败**\n\nRun: `uv run pytest tests/services/test_source_health.py -q -k evaluate`\nExpected: FAIL（ImportError: evaluate_source_alerts）。\n\n- [ ] **Step 3: 实现评估器（追加到 source_health.py，autoplan M1 两阶段）**\n\n```python\ndef evaluate_source_alerts(\n    engine: Engine,\n    send_alert: Callable[[str, str], None] | None,\n    now: _NowFn = now_naive_utc,\n) -> None:\n    \"\"\"评估健康表驱动告警状态机（spec §1.3；挂载于 path_a tick 尾 + 启动 backfill 尾）。\n\n    send_alert=None（ADMIN_BARK_KEY 未配）→ 只做状态转移不发送，admin 面板仍可见。\n    发送异常不转移状态（下轮重试直到送达）——2026-09-15 DNS 事故教训：故障期\n    告警通道大概率同时挂。\n\n    两阶段（autoplan M1，pool_size=1 纪律）：短 session 读+决策后关闭 → session\n    外发 HTTP 告警 → 短 session 守卫重读后落转移。绝不在持有唯一连接的 session\n    内做 httpx 调用——DNS 故障（本 plan 目标场景）下 Bark 挂到 10s 超时，同期\n    其他 job/请求借不到连接撞 busy_timeout，告警机制反而制造它要防的漏通知\n    （jobs.py:276-278/339、password_reset_service.py:131/204 两次实测事故同型）。\n    落转移前重读校验状态未变：读与落之间若有 fetch 写入（如故障恰好恢复），\n    放弃本轮转移下轮重评，不覆盖并发写入。\n    \"\"\"\n    t = now()\n    # 阶段 1：短 session 读 + 决策（快照出 session，不留 ORM 对象跨 session）\n    with Session(engine) as s:\n        rows = [\n            (h.source, h.status, h.alerted, h.down_since, h.error)\n            for h in s.exec(select(ApiSourceHealth)).all()\n        ]\n    # 阶段 2：session 外发送；送达成功的进待落清单\n    delivered: list[tuple[str, str]] = []  # (source, 目标 alerted 状态)\n    for source, status, alerted, down_since, error in rows:\n        if (\n            status == 'down'\n            and alerted == 'none'\n            and down_since is not None\n            and t - down_since >= DOWN_ALERT_AFTER\n        ):\n            minutes = int((t - down_since).total_seconds() // 60)\n            if send_alert is not None:\n                try:\n                    send_alert(\n                        '开奖抓取持续失败',\n                        f'数据源 {source} 已持续失败约 {minutes} 分钟'\n                        f'（自 {down_since} 起），最近错误：{(error or \"\")[:200]}',\n                    )\n                except Exception:\n                    logger.warning(\n                        'source_alert_send_failed source=%s', source, exc_info=True\n                    )\n                    continue  # 未送达不转移，下轮重试\n            delivered.append((source, 'alerted'))\n        elif alerted == 'recovering' and status == 'ok':\n            duration = t - down_since if down_since else timedelta(0)\n            minutes = int(duration.total_seconds() // 60)\n            if send_alert is not None:\n                try:\n                    send_alert(\n                        '开奖抓取已恢复',\n                        f'数据源 {source} 已恢复抓取（故障持续约 {minutes} 分钟）',\n                    )\n                except Exception:\n                    logger.warning(\n                        'source_recovery_send_failed source=%s', source, exc_info=True\n                    )\n                    continue\n            delivered.append((source, 'none'))\n    if not delivered:\n        return\n    # 阶段 3：短 session 守卫重读后落转移（recovering 完成时清 down_since）\n    with Session(engine) as s:\n        for source, target in delivered:\n            h = s.get(ApiSourceHealth, source)\n            if h is None:\n                continue\n            if target == 'alerted' and h.alerted == 'none' and h.status == 'down':\n                h.alerted = 'alerted'\n            elif target == 'none' and h.alerted == 'recovering' and h.status == 'ok':\n                h.alerted = 'none'\n                h.down_since = None\n        s.commit()\n```\n\n（注：单源发送失败只跳过该源的状态转移，其他源继续评估与落库——两行数据源互不影响。）\n\n- [ ] **Step 4: 跑测试确认通过**\n\nRun: `uv run pytest tests/services/test_source_health.py -q`\nExpected: 15 passed（Task 3 的 8 + 本任务 7）。\n\n- [ ] **Step 5: Commit**\n\n```bash\ngit add app/services/source_health.py tests/services/test_source_health.py\ngit commit -m \"feat(plan-11): evaluate_source_alerts 告警状态机（down≥30min 告警、恢复通知、送达才转移）\"\n```\n\n---\n\n### Task 5: 调度接线 + 启动回填 QPS 间隔\n\n**Files:**\n- Modify: `app/scheduler/jobs.py`（`_path_a_tick` 末尾 + import）\n- Modify: `app/scheduler/backfill.py`（第 4 步间隔 + 函数尾评估 + 常量）\n- Modify: `tests/conftest.py:14-26`（autouse fixture 补 backfill 常量置 0）\n- Test: `tests/scheduler/test_jobs.py`（追加）、`tests/scheduler/test_backfill.py`（追加）\n\n**Interfaces:**\n- Consumes: Task 4 `evaluate_source_alerts`、Task 2 `build_admin_alert`。\n- Produces: `_path_a_tick` 与 `run_startup_backfill` 尾部各调用一次评估；backfill 复用 `jobs._INTER_LOTTERY_INTERVAL`（M6 单一真值源）。\n\n- [ ] **Step 1: 写失败测试（test_jobs.py 追加，用仓库既有 `_invoke_job` 辅助——见文件内其他 tick 测试）**\n\n```python\ndef test_path_a_tick_evaluates_source_alerts(db_engine, monkeypatch):\n    \"\"\"path_a_tick 尾部必须评估数据源健康告警（plan-11：tick 即评估点）。\"\"\"\n    from unittest.mock import MagicMock\n\n    import app.scheduler.jobs as jobs_mod\n    from app.scheduler.setup import build_scheduler\n\n    spy = MagicMock()\n    monkeypatch.setattr(jobs_mod, 'evaluate_source_alerts', spy)\n    sched = build_scheduler(db_engine)\n    register_all_jobs(\n        sched,\n        {\n            'engine': db_engine,\n            'fetch_service': MagicMock(),\n            'compare_service': MagicMock(),\n            'refill_worker': MagicMock(),\n            'notifier': MagicMock(),\n        },\n    )\n    _invoke_job(sched, 'path_a_poll_evening')\n    assert spy.call_count == 1\n    # engine 为第一参数，sender 来自 build_admin_alert（测试无 key → None）\n    assert spy.call_args.args[0] is db_engine\n```\n\n- [ ] **Step 2: 写失败测试（test_backfill.py 追加）**\n\n```python\ndef test_startup_backfill_paces_fetches_with_interval(db_engine, monkeypatch):\n    \"\"\"启动回填对实际抓取的彩种加 QPS 间隔：第 2 个起每次 fetch 前 sleep（plan-11）。\"\"\"\n    import app.scheduler.backfill as backfill_mod\n    from app.models import LotteryType\n\n    monkeypatch.setattr(backfill_mod, '_INTER_LOTTERY_INTERVAL', 1.2)\n    sleeps = []\n    monkeypatch.setattr(backfill_mod.time, 'sleep', lambda s: sleeps.append(s))\n\n    # 3 个彩种全部 missed（DB 无开奖 + draw_days 覆盖回看窗口）\n    monkeypatch.setattr(\n        backfill_mod, '_enabled_lotteries',\n        lambda engine: [('a', [0, 1, 2, 3, 4, 5, 6]), ('b', [0, 1, 2, 3, 4, 5, 6]),\n                        ('c', [0, 1, 2, 3, 4, 5, 6])],\n    )\n    monkeypatch.setattr(backfill_mod, '_has_draw_for_date', lambda engine, code, d: False)\n\n    deps = _make_deps(db_engine)\n    run_startup_backfill(deps)\n    assert deps['fetch_service'].fetch_and_store.call_count == 3\n    assert sleeps == [1.2, 1.2]  # 首个抓取不 sleep\n\n\ndef test_startup_backfill_evaluates_source_alerts(db_engine, monkeypatch):\n    \"\"\"启动 backfill 尾部评估健康告警（plan-11：开机即评估）。\"\"\"\n    import app.scheduler.backfill as backfill_mod\n\n    spy = MagicMock()\n    monkeypatch.setattr(backfill_mod, 'evaluate_source_alerts', spy)\n    run_startup_backfill(_make_deps(db_engine))\n    assert spy.call_count == 1\n```\n\n- [ ] **Step 3: 跑两个新测试确认失败**\n\nRun: `uv run pytest tests/scheduler/test_jobs.py::test_path_a_tick_evaluates_source_alerts tests/scheduler/test_backfill.py::test_startup_backfill_paces_fetches_with_interval tests/scheduler/test_backfill.py::test_startup_backfill_evaluates_source_alerts -q`\nExpected: 3 FAIL（AttributeError: evaluate_source_alerts / _INTER_LOTTERY_INTERVAL）。\n\n- [ ] **Step 4: 实现 jobs.py 接线**\n\n`app/scheduler/jobs.py` import 区加：\n\n```python\nfrom app.notifications.admin_alert import build_admin_alert\nfrom app.services.source_health import evaluate_source_alerts\n```\n\n`_path_a_tick` 函数末尾（`sched.add_job(_push_big_win, ...)` 循环之后、函数体结束前）追加：\n\n```python\n    # 数据源健康评估（plan-11）：tick 尾部评估告警状态机（down≥30min → admin bark；\n    # 未配 ADMIN_BARK_KEY 则 sender=None 只转移状态）。评估失败不阻断本 tick 收尾。\n    try:\n        evaluate_source_alerts(engine, build_admin_alert())\n    except Exception:\n        logger.error('source_alert_evaluate_failed', exc_info=True)\n```\n\n- [ ] **Step 5: 实现 backfill.py 间隔 + 尾部评估**\n\n`app/scheduler/backfill.py`：import 区加 `import time`（若未有）、`from app.notifications.admin_alert import build_admin_alert`、`from app.services.source_health import evaluate_source_alerts`，以及（autoplan M6）：\n\n```python\nfrom app.scheduler.jobs import _INTER_LOTTERY_INTERVAL\n```\n\nM6 说明：plan 原稿在 backfill 本地复制 `_INTER_LOTTERY_INTERVAL = 1.2`（注释称「反向\nimport 会循环依赖」）——已核实 jobs.py 不 import backfill，无环，复制只会漂移\n（MXNZP QPS 限额调整时需改两处）。单一真值源：backfill 直接 import jobs 常量；\nconftest 置 0 与间隔测试 monkeypatch `backfill_mod._INTER_LOTTERY_INTERVAL`\n（模块内 import 引用）依然有效，测试写法不变。"}]} -->
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

<!-- autoplan-baseline-edits:design {"sourceSha256":"8bf64b6c3b07a52929ed4de50e1d37eaf379fa7041559e0c49dd3c83306e1be7","replacements":[{"oldText":"def test_record_ok_on_alerted_transitions_recovering_keeps_down_since(db_engine):\n    \"\"\"已告警（alerted）的源恢复 → recovering 且保留 down_since（供恢复通知算时长）。\"\"\"\n    t0 = datetime(2026, 9, 15, 4, 0, 0)\n    record_source_health(db_engine, 'mxnzp', 'down', 'e', now=lambda: t0)\n    with Session(db_engine) as s:\n        s.get(ApiSourceHealth, 'mxnzp').alerted = 'alerted'\n        s.commit()\n    record_source_health(db_engine, 'mxnzp', 'ok', now=lambda: t0 + timedelta(hours=2))\n    h = _get(db_engine)\n    assert h.alerted == 'recovering'\n    assert h.down_since == t0  # 保留，评估侧送达恢复通知后清除\n\n\ndef test_record_down_cancels_pending_recovery(db_engine):\n    \"\"\"抖动：recovering 期间再次失败 → 回 alerted（取消待发的过时恢复通知）。\"\"\"\n    t0 = datetime(2026, 9, 15, 4, 0, 0)\n    with Session(db_engine) as s:\n        s.add(ApiSourceHealth(source='mxnzp', status='ok', alerted='recovering',\n                              down_since=t0))\n        s.commit()\n    record_source_health(db_engine, 'mxnzp', 'down', 'again', now=lambda: t0 + timedelta(minutes=1))\n    h = _get(db_engine)\n    assert h.alerted == 'alerted' and h.status == 'down'\n\n\ndef test_record_ok_after_long_outage_without_delivered_alert_goes_recovering(db_engine):\n    \"\"\"长故障期间告警从未送达（通道同挂，DNS 教训）→ 恢复时不得静默清零——\n\n    置 recovering 补发恢复通知（autoplan M11：否则 9 天故障自愈 = 零通知）。\n    \"\"\"\n    t0 = datetime(2026, 9, 1, 4, 0, 0)\n    record_source_health(db_engine, 'mxnzp', 'down', 'e', now=lambda: t0)\n    # 故障期每次评估尝试告警均失败（状态保持 none），第 9 天直接恢复\n    record_source_health(db_engine, 'mxnzp', 'ok', now=lambda: t0 + timedelta(days=9))\n    h = _get(db_engine)\n    assert h.alerted == 'recovering' and h.down_since == t0\n\n\ndef test_record_ok_after_short_outage_clears_silently(db_engine):\n    \"\"\"短故障（<30min，从未达告警阈值）恢复 → 清 down_since，不留 recovering（M11 边界）。\"\"\"\n    t0 = datetime(2026, 9, 15, 4, 0, 0)\n    record_source_health(db_engine, 'mxnzp', 'down', 'e', now=lambda: t0)\n    record_source_health(db_engine, 'mxnzp', 'ok', now=lambda: t0 + timedelta(minutes=10))\n    h = _get(db_engine)\n    assert h.alerted == 'none' and h.down_since is None\n\n\ndef test_record_error_redacts_secret_query_params(db_engine):\n    \"\"\"error 含 URL query 密钥（juhe key= 等）→ 落表前脱敏（autoplan M13）。\n\n    juhe.py:27 把 api key 放 query，raise_for_status 异常消息含完整 URL；\n    健康表 error 会进 admin 面板与 Bark 告警体（第三方服务器），密钥不得外泄。\n    \"\"\"\n    record_source_health(\n        db_engine, 'juhe', 'down',\n        \"Client error '403' for url 'https://v.juhe.cn/lottery/query?lottery_id=ssq&key=SECRETKEY123'\",\n    )\n    h = _get(db_engine, 'juhe')\n    assert 'SECRETKEY123' not in (h.error or '')\n    assert 'key=[REDACTED]' in (h.error or '')\n```\n\n- [ ] **Step 2: 跑测试确认失败**\n\nRun: `uv run pytest tests/services/test_source_health.py -q`\nExpected: FAIL（`ModuleNotFoundError: app.services.source_health`）。\n\n- [ ] **Step 3: 实现 record_source_health（含 autoplan M11/M13）**\n\n`app/services/source_health.py`：\n\n```python\n\"\"\"数据源健康记录与告警评估（plan-11 / 2026-09-15 DNS 事故跟进）。\n\nApiSourceHealth 长期「有读无写」（admin 面板空表）——本模块补写路径：fetch 按\n源记录 ok/down；评估器按「down 持续 ≥30 分钟」发 admin Bark（送达才转移状态。\nDNS 教训：故障期告警通道大概率同挂，未送达必须下轮重试）。\n\n时间纪律（CLAUDE.md）：down_since/last_success_at 均 naive UTC，与\nTimestampMixin.created_at 同表示，DB 内不做 naive/aware 混比。\n\"\"\"\n\nimport logging\nimport re\nfrom collections.abc import Callable\nfrom datetime import datetime, timedelta, timezone\n\nfrom sqlalchemy.engine import Engine\nfrom sqlmodel import Session, select\n\nfrom app.models import ApiSourceHealth\n\nlogger = logging.getLogger(__name__)\n\n# 「连续 2 个 tick 全失败」的时间窗实现（spec §1.3）：与 tick 次数解耦——\n# 持久、不怕容器重启（2026-09-15 事故中容器恰在故障期重启，内存计数会清零）。\nDOWN_ALERT_AFTER = timedelta(minutes=30)\n\n# error 脱敏（autoplan M13）：juhe 把 api key 放 query（juhe.py:27），\n# raise_for_status 异常消息含完整 URL；健康表 error 会进 admin 面板与 Bark\n# 告警体（第三方服务器），密钥参数值落表前一律替换 [REDACTED]。\n_SENSITIVE_QUERY_RE = re.compile(r'([?&](?:key|app_id|app_secret|token)=)[^&\\s]+')\n\n# now 注入点：生产用默认；测试注入固定时钟，避免真实 sleep/时间竞争。\n_NowFn = Callable[[], datetime]\n\n\ndef now_naive_utc() -> datetime:\n    return datetime.now(timezone.utc).replace(tzinfo=None)\n\n\ndef _sanitize_error(error: str | None) -> str | None:\n    \"\"\"剥离 error 文本中的敏感 query 参数值（M13），保留 URL 其余部分供排障。\"\"\"\n    if error is None:\n        return None\n    return _SENSITIVE_QUERY_RE.sub(r'\\1[REDACTED]', error)\n\n\ndef record_source_health(\n    engine: Engine,\n    source: str,\n    outcome: str,\n    error: str | None = None,\n    now: _NowFn = now_naive_utc,\n) -> None:\n    \"\"\"按源 upsert 健康表（spec §1.2 语义表）。outcome: 'ok' | 'down' | 'permanent'。\n\n    permanent（key 未配置等配置态）：仅记 error——单源部署下未配置的备源若计入\n    down 会永久故障且天天告警（juhe 不可用是长期事实，非运行故障）。\n\n    ok + alerted=='none' + 故障时长 ≥ DOWN_ALERT_AFTER（autoplan M11）：转\n    recovering 补发恢复通知——故障期告警通道同挂（DNS 教训）导致告警从未送达，\n    恢复时若静默清零，长故障将零通知（9 天事故复现路径）。\n    \"\"\"\n    t = now()\n    with Session(engine) as s:\n        h = s.get(ApiSourceHealth, source)\n        if h is None:\n            h = ApiSourceHealth(source=source)\n            s.add(h)\n        if outcome == 'ok':\n            h.status = 'ok'\n            h.last_success_at = t\n            h.error = None\n            if h.alerted == 'none':\n                if h.down_since is not None and t - h.down_since >= DOWN_ALERT_AFTER:\n                    h.alerted = 'recovering'  # M11：长故障零送达 → 补发恢复通知\n                else:\n                    h.down_since = None  # 短故障：静默恢复（未达告警阈值，无通知义务）\n            else:\n                # 已告警过 → 待恢复通知；保留 down_since 供评估侧算故障时长。\n                h.alerted = 'recovering'\n        elif outcome == 'down':\n            h.status = 'down'\n            if h.down_since is None:\n                h.down_since = t\n            if h.alerted == 'recovering':\n                h.alerted = 'alerted'  # 抖动：取消待发的过时恢复通知\n            h.error = _sanitize_error(error)\n        elif outcome == 'permanent':\n            h.error = _sanitize_error(error)\n        else:\n            raise ValueError(f'unknown outcome: {outcome}')\n        s.commit()\n```\n\n- [ ] **Step 4: 跑测试确认通过**\n\nRun: `uv run pytest tests/services/test_source_health.py -q`\nExpected: 8 passed（原 5 + M11×2 + M13×1）。\n\n- [ ] **Step 5: FetchService 三态接线（回归保护既有语义）**\n\n`app/services/fetch_service.py`：\n\n改 `_try_fetch`（原 111-116 行）：\n\n```python\n    def _try_fetch(\n        self, source: DrawSource, lottery_code: str\n    ) -> tuple[DrawNumbers | None, str, str | None]:\n        \"\"\"返回 (numbers, outcome, error)。outcome: 'ok' | 'down' | 'permanent'。\n\n        ok+None=未开奖（源健康）；down=运行故障（网络/限流重试耗尽）；\n        permanent=配置态错误（key 未配置等）——健康表据此区分（plan-11 spec §1.2）。\n        \"\"\"\n        try:\n            return self._fetch_with_backoff(source, lottery_code), 'ok', None\n        except Exception as exc:\n            if isinstance(exc, PermanentLookupError):\n                return None, 'permanent', str(exc)\n            return None, 'down', str(exc)\n```\n\n改 `fetch_and_store` 头部（原 118-121 行）：\n\n```python\n    def fetch_and_store(self, lottery_code: str) -> FetchResult:\n        primary, p_outcome, p_err = self._try_fetch(self._primary, lottery_code)\n        backup, b_outcome, b_err = self._try_fetch(self._backup, lottery_code)\n        # 数据源健康落表（plan-11）：写失败不得阻断抓取（spec §1.2 独立短事务）。\n        self._record_health(self._primary.name, p_outcome, p_err)\n        self._record_health(self._backup.name, b_outcome, b_err)\n        p_ok = p_outcome == 'ok'\n        b_ok = b_outcome == 'ok'\n```\n\n（后续 `if not p_ok and not b_ok:` 等分支逻辑不动——布尔语义与旧版一致。）\n\n`_grace_refetch` 内（约 195 行）原 `m2, m2_ok = self._try_fetch(missing_source, lottery_code)` 改为：\n\n```python\n        m2, m2_outcome, m2_err = self._try_fetch(missing_source, lottery_code)\n        # grace 重抓结果同样落健康表（autoplan M7）：否则 grace 内恢复的源要等下个\n        # 抓取周期才转 ok，恢复通知无谓延迟一整轮（15 分钟）。\n        self._record_health(str(missing_source.name), m2_outcome, m2_err)\n        m2_ok = m2_outcome == 'ok'\n```\n\n`FetchService` 类内新增方法（放在 `_try_fetch` 之后）：\n\n```python\n    def _record_health(self, source_name, outcome: str, error: str | None) -> None:\n        \"\"\"写 ApiSourceHealth（plan-11）。独立短事务 + 吞异常：健康落表失败只记日志，\n        绝不阻断抓取主流程（spec §1.2）。\"\"\"\n        try:\n            from app.services.source_health import record_source_health\n\n            record_source_health(self._engine, str(source_name), outcome, error)\n        except Exception:\n            logger.warning(\n                'source_health_write_failed source=%s outcome=%s', source_name, outcome,\n                exc_info=True,\n            )\n```\n\n（函数内 import：避免 fetch_service ↔ source_health 潜在环；模块顶部 import 亦可，以 lint-imports 通过为准。）\n\n- [ ] **Step 6: 既有 fetch 套件回归**\n\nRun: `uv run pytest tests/services/test_fetch_service.py tests/adapters/ tests/integration/ -q`\nExpected: 全部 PASS（健康写入对既有断言无感——独立表、独立事务）。\n\n- [ ] **Step 7: Commit**\n\n```bash\ngit add app/services/source_health.py app/services/fetch_service.py tests/services/test_source_health.py\ngit commit -m \"feat(plan-11): fetch 按源三态落 ApiSourceHealth（ok/down/permanent，故障起点不刷新）\"\n```\n\n---\n\n### Task 4: evaluate_source_alerts 告警状态机\n\n**Files:**\n- Modify: `app/services/source_health.py`（追加评估器）\n- Test: `tests/services/test_source_health.py`（追加）\n\n**Interfaces:**\n- Consumes: Task 3 的 `record_source_health` / `DOWN_ALERT_AFTER`。\n- Produces: `evaluate_source_alerts(engine: Engine, send_alert: Callable[[str, str], None] | None, now: _NowFn = now_naive_utc) -> None`——Task 5 接线依赖。\n\n- [ ] **Step 1: 写失败测试（追加到 test_source_health.py）**\n\n```python\n# ---------- evaluate_source_alerts 状态机（spec §1.3） ----------\n\nimport logging\n\nfrom app.services.source_health import evaluate_source_alerts\n\n\nclass _Recorder:\n    \"\"\"记 send_alert 调用；可控抛异常模拟「告警通道也挂了」（DNS 教训）。\"\"\"\n\n    def __init__(self, fail_first=0):\n        self.calls = []\n        self.fail_first = fail_first\n\n    def __call__(self, title, body):\n        if self.fail_first > 0:\n            self.fail_first -= 1\n            raise ConnectionError('bark down')\n        self.calls.append((title, body))\n\n\ndef _seed_down(engine, down_since, alerted='none', status='down'):\n    with Session(engine) as s:\n        s.add(ApiSourceHealth(source='mxnzp', status=status, alerted=alerted,\n                              down_since=down_since, last_success_at=None))\n        s.commit()\n\n\ndef test_evaluate_no_alert_before_threshold(db_engine):\n    \"\"\"down 不足 30 分钟 → 不告警。\"\"\"\n    t = datetime(2026, 9, 15, 13, 0, 0)\n    _seed_down(db_engine, down_since=t - timedelta(minutes=29))\n    rec = _Recorder()\n    evaluate_source_alerts(db_engine, rec, now=lambda: t)\n    assert rec.calls == []\n    assert _get(db_engine).alerted == 'none'\n\n\ndef test_evaluate_alerts_once_after_threshold(db_engine):\n    \"\"\"down ≥30 分钟 → 告警一次并置 alerted；继续 down 不重复告警。\"\"\"\n    t = datetime(2026, 9, 15, 13, 0, 0)\n    _seed_down(db_engine, down_since=t - timedelta(minutes=31))\n    rec = _Recorder()\n    evaluate_source_alerts(db_engine, rec, now=lambda: t)\n    evaluate_source_alerts(db_engine, rec, now=lambda: t + timedelta(minutes=15))\n    assert len(rec.calls) == 1 and '持续失败' in rec.calls[0][0]\n    assert _get(db_engine).alerted == 'alerted'\n\n\ndef test_evaluate_send_failure_keeps_state_and_retries(db_engine):\n    \"\"\"发送异常 → 状态保持 none，下轮重试；送达成功才转移（DNS 教训回归）。\"\"\"\n    t = datetime(2026, 9, 15, 13, 0, 0)\n    _seed_down(db_engine, down_since=t - timedelta(minutes=40))\n    rec = _Recorder(fail_first=1)\n    evaluate_source_alerts(db_engine, rec, now=lambda: t)\n    assert rec.calls == [] and _get(db_engine).alerted == 'none'\n    evaluate_source_alerts(db_engine, rec, now=lambda: t + timedelta(minutes=15))\n    assert len(rec.calls) == 1 and _get(db_engine).alerted == 'alerted'\n\n\ndef test_recovery_notice_sent_then_reset(db_engine):\n    \"\"\"alerted → 抓取恢复（写入侧置 recovering）→ 评估送出恢复通知 → none+清 down_since。\"\"\"\n    t = datetime(2026, 9, 15, 13, 0, 0)\n    _seed_down(db_engine, down_since=t - timedelta(hours=2), alerted='alerted')\n    record_source_health(db_engine, 'mxnzp', 'ok', now=lambda: t)  # → recovering\n    rec = _Recorder()\n    evaluate_source_alerts(db_engine, rec, now=lambda: t)\n    assert len(rec.calls) == 1 and '恢复' in rec.calls[0][0]\n    h = _get(db_engine)\n    assert h.alerted == 'none' and h.down_since is None\n\n\ndef test_recovery_send_failure_retries(db_engine):\n    \"\"\"恢复通知发送失败 → 保持 recovering，下轮送达才回 none。\"\"\"\n    t = datetime(2026, 9, 15, 13, 0, 0)\n    _seed_down(db_engine, down_since=t - timedelta(hours=2), alerted='alerted')\n    record_source_health(db_engine, 'mxnzp', 'ok', now=lambda: t)\n    rec = _Recorder(fail_first=1)\n    evaluate_source_alerts(db_engine, rec, now=lambda: t)\n    assert _get(db_engine).alerted == 'recovering'\n    evaluate_source_alerts(db_engine, rec, now=lambda: t + timedelta(minutes=15))\n    assert _get(db_engine).alerted == 'none'\n\n\ndef test_no_sender_still_transitions(db_engine):\n    \"\"\"ADMIN_BARK_KEY 未配（send_alert=None）→ 不发送但状态照常流转（表可见 down）。\"\"\"\n    t = datetime(2026, 9, 15, 13, 0, 0)\n    _seed_down(db_engine, down_since=t - timedelta(minutes=40))\n    evaluate_source_alerts(db_engine, None, now=lambda: t)\n    assert _get(db_engine).alerted == 'alerted'\n\n\ndef test_evaluate_sends_outside_db_session(db_engine):\n    \"\"\"M1 回归：send_alert 调用时评估器不得持有 DB 连接（pool_size=1 纪律）。\n\n    若评估器在 session 内发送（plan 原稿），DNS 故障下 Bark 挂 10s 超时期间\n    唯一连接被占，其他 job/请求撞 busy_timeout——jobs.py:276-278 两次事故同型。\n    \"\"\"\n    t = datetime(2026, 9, 15, 13, 0, 0)\n    _seed_down(db_engine, down_since=t - timedelta(minutes=40))\n    checked_out_during_send = []\n\n    def _sender(title, body):\n        checked_out_during_send.append(db_engine.pool.checkedout())\n\n    evaluate_source_alerts(db_engine, _sender, now=lambda: t)\n    assert checked_out_during_send == [0]\n```\n\n- [ ] **Step 2: 跑测试确认失败**\n\nRun: `uv run pytest tests/services/test_source_health.py -q -k evaluate`\nExpected: FAIL（ImportError: evaluate_source_alerts）。\n\n- [ ] **Step 3: 实现评估器（追加到 source_health.py，autoplan M1 两阶段）**\n\n```python\ndef evaluate_source_alerts(\n    engine: Engine,\n    send_alert: Callable[[str, str], None] | None,\n    now: _NowFn = now_naive_utc,\n) -> None:\n    \"\"\"评估健康表驱动告警状态机（spec §1.3；挂载于 path_a tick 尾 + 启动 backfill 尾）。\n\n    send_alert=None（ADMIN_BARK_KEY 未配）→ 只做状态转移不发送，admin 面板仍可见。\n    发送异常不转移状态（下轮重试直到送达）——2026-09-15 DNS 事故教训：故障期\n    告警通道大概率同时挂。\n\n    两阶段（autoplan M1，pool_size=1 纪律）：短 session 读+决策后关闭 → session\n    外发 HTTP 告警 → 短 session 守卫重读后落转移。绝不在持有唯一连接的 session\n    内做 httpx 调用——DNS 故障（本 plan 目标场景）下 Bark 挂到 10s 超时，同期\n    其他 job/请求借不到连接撞 busy_timeout，告警机制反而制造它要防的漏通知\n    （jobs.py:276-278/339、password_reset_service.py:131/204 两次实测事故同型）。\n    落转移前重读校验状态未变：读与落之间若有 fetch 写入（如故障恰好恢复），\n    放弃本轮转移下轮重评，不覆盖并发写入。\n    \"\"\"\n    t = now()\n    # 阶段 1：短 session 读 + 决策（快照出 session，不留 ORM 对象跨 session）\n    with Session(engine) as s:\n        rows = [\n            (h.source, h.status, h.alerted, h.down_since, h.error)\n            for h in s.exec(select(ApiSourceHealth)).all()\n        ]\n    # 阶段 2：session 外发送；送达成功的进待落清单\n    delivered: list[tuple[str, str]] = []  # (source, 目标 alerted 状态)\n    for source, status, alerted, down_since, error in rows:\n        if (\n            status == 'down'\n            and alerted == 'none'\n            and down_since is not None\n            and t - down_since >= DOWN_ALERT_AFTER\n        ):\n            minutes = int((t - down_since).total_seconds() // 60)\n            if send_alert is not None:\n                try:\n                    send_alert(\n                        '开奖抓取持续失败',\n                        f'数据源 {source} 已持续失败约 {minutes} 分钟'\n                        f'（自 {down_since} 起），最近错误：{(error or \"\")[:200]}',\n                    )\n                except Exception:\n                    logger.warning(\n                        'source_alert_send_failed source=%s', source, exc_info=True\n                    )\n                    continue  # 未送达不转移，下轮重试\n            delivered.append((source, 'alerted'))\n        elif alerted == 'recovering' and status == 'ok':\n            duration = t - down_since if down_since else timedelta(0)\n            minutes = int(duration.total_seconds() // 60)\n            if send_alert is not None:\n                try:\n                    send_alert(\n                        '开奖抓取已恢复',\n                        f'数据源 {source} 已恢复抓取（故障持续约 {minutes} 分钟）',\n                    )\n                except Exception:\n                    logger.warning(\n                        'source_recovery_send_failed source=%s', source, exc_info=True\n                    )\n                    continue\n            delivered.append((source, 'none'))\n    if not delivered:\n        return\n    # 阶段 3：短 session 守卫重读后落转移（recovering 完成时清 down_since）\n    with Session(engine) as s:\n        for source, target in delivered:\n            h = s.get(ApiSourceHealth, source)\n            if h is None:\n                continue\n            if target == 'alerted' and h.alerted == 'none' and h.status == 'down':\n                h.alerted = 'alerted'\n            elif target == 'none' and h.alerted == 'recovering' and h.status == 'ok':\n                h.alerted = 'none'\n                h.down_since = None\n        s.commit()\n```\n\n（注：单源发送失败只跳过该源的状态转移，其他源继续评估与落库——两行数据源互不影响。）\n\n- [ ] **Step 4: 跑测试确认通过**\n\nRun: `uv run pytest tests/services/test_source_health.py -q`\nExpected: 15 passed（Task 3 的 8 + 本任务 7）。\n\n- [ ] **Step 5: Commit**\n\n```bash\ngit add app/services/source_health.py tests/services/test_source_health.py\ngit commit -m \"feat(plan-11): evaluate_source_alerts 告警状态机（down≥30min 告警、恢复通知、送达才转移）\"\n```\n\n---\n\n### Task 5: 调度接线 + 启动回填 QPS 间隔\n\n**Files:**\n- Modify: `app/scheduler/jobs.py`（`_path_a_tick` 末尾 + import）\n- Modify: `app/scheduler/backfill.py`（第 4 步间隔 + 函数尾评估 + 常量）\n- Modify: `tests/conftest.py:14-26`（autouse fixture 补 backfill 常量置 0）\n- Test: `tests/scheduler/test_jobs.py`（追加）、`tests/scheduler/test_backfill.py`（追加）\n\n**Interfaces:**\n- Consumes: Task 4 `evaluate_source_alerts`、Task 2 `build_admin_alert`。\n- Produces: `_path_a_tick` 与 `run_startup_backfill` 尾部各调用一次评估；backfill 复用 `jobs._INTER_LOTTERY_INTERVAL`（M6 单一真值源）。\n\n- [ ] **Step 1: 写失败测试（test_jobs.py 追加，用仓库既有 `_invoke_job` 辅助——见文件内其他 tick 测试）**\n\n```python\ndef test_path_a_tick_evaluates_source_alerts(db_engine, monkeypatch):\n    \"\"\"path_a_tick 尾部必须评估数据源健康告警（plan-11：tick 即评估点）。\"\"\"\n    from unittest.mock import MagicMock\n\n    import app.scheduler.jobs as jobs_mod\n    from app.scheduler.setup import build_scheduler\n\n    spy = MagicMock()\n    monkeypatch.setattr(jobs_mod, 'evaluate_source_alerts', spy)\n    sched = build_scheduler(db_engine)\n    register_all_jobs(\n        sched,\n        {\n            'engine': db_engine,\n            'fetch_service': MagicMock(),\n            'compare_service': MagicMock(),\n            'refill_worker': MagicMock(),\n            'notifier': MagicMock(),\n        },\n    )\n    _invoke_job(sched, 'path_a_poll_evening')\n    assert spy.call_count == 1\n    # engine 为第一参数，sender 来自 build_admin_alert（测试无 key → None）\n    assert spy.call_args.args[0] is db_engine\n```\n\n- [ ] **Step 2: 写失败测试（test_backfill.py 追加）**\n\n```python\ndef test_startup_backfill_paces_fetches_with_interval(db_engine, monkeypatch):\n    \"\"\"启动回填对实际抓取的彩种加 QPS 间隔：第 2 个起每次 fetch 前 sleep（plan-11）。\"\"\"\n    import app.scheduler.backfill as backfill_mod\n    from app.models import LotteryType\n\n    monkeypatch.setattr(backfill_mod, '_INTER_LOTTERY_INTERVAL', 1.2)\n    sleeps = []\n    monkeypatch.setattr(backfill_mod.time, 'sleep', lambda s: sleeps.append(s))\n\n    # 3 个彩种全部 missed（DB 无开奖 + draw_days 覆盖回看窗口）\n    monkeypatch.setattr(\n        backfill_mod, '_enabled_lotteries',\n        lambda engine: [('a', [0, 1, 2, 3, 4, 5, 6]), ('b', [0, 1, 2, 3, 4, 5, 6]),\n                        ('c', [0, 1, 2, 3, 4, 5, 6])],\n    )\n    monkeypatch.setattr(backfill_mod, '_has_draw_for_date', lambda engine, code, d: False)\n\n    deps = _make_deps(db_engine)\n    run_startup_backfill(deps)\n    assert deps['fetch_service'].fetch_and_store.call_count == 3\n    assert sleeps == [1.2, 1.2]  # 首个抓取不 sleep\n\n\ndef test_startup_backfill_evaluates_source_alerts(db_engine, monkeypatch):\n    \"\"\"启动 backfill 尾部评估健康告警（plan-11：开机即评估）。\"\"\"\n    import app.scheduler.backfill as backfill_mod\n\n    spy = MagicMock()\n    monkeypatch.setattr(backfill_mod, 'evaluate_source_alerts', spy)\n    run_startup_backfill(_make_deps(db_engine))\n    assert spy.call_count == 1\n```\n\n- [ ] **Step 3: 跑两个新测试确认失败**\n\nRun: `uv run pytest tests/scheduler/test_jobs.py::test_path_a_tick_evaluates_source_alerts tests/scheduler/test_backfill.py::test_startup_backfill_paces_fetches_with_interval tests/scheduler/test_backfill.py::test_startup_backfill_evaluates_source_alerts -q`\nExpected: 3 FAIL（AttributeError: evaluate_source_alerts / _INTER_LOTTERY_INTERVAL）。\n\n- [ ] **Step 4: 实现 jobs.py 接线**\n\n`app/scheduler/jobs.py` import 区加：\n\n```python\nfrom app.notifications.admin_alert import build_admin_alert\nfrom app.services.source_health import evaluate_source_alerts\n```\n\n`_path_a_tick` 函数末尾（`sched.add_job(_push_big_win, ...)` 循环之后、函数体结束前）追加：\n\n```python\n    # 数据源健康评估（plan-11）：tick 尾部评估告警状态机（down≥30min → admin bark；\n    # 未配 ADMIN_BARK_KEY 则 sender=None 只转移状态）。评估失败不阻断本 tick 收尾。\n    try:\n        evaluate_source_alerts(engine, build_admin_alert())\n    except Exception:\n        logger.error('source_alert_evaluate_failed', exc_info=True)\n```\n\n- [ ] **Step 5: 实现 backfill.py 间隔 + 尾部评估**\n\n`app/scheduler/backfill.py`：import 区加 `import time`（若未有）、`from app.notifications.admin_alert import build_admin_alert`、`from app.services.source_health import evaluate_source_alerts`，以及（autoplan M6）：\n\n```python\nfrom app.scheduler.jobs import _INTER_LOTTERY_INTERVAL\n```\n\nM6 说明：plan 原稿在 backfill 本地复制 `_INTER_LOTTERY_INTERVAL = 1.2`（注释称「反向\nimport 会循环依赖」）——已核实 jobs.py 不 import backfill，无环，复制只会漂移\n（MXNZP QPS 限额调整时需改两处）。单一真值源：backfill 直接 import jobs 常量；\nconftest 置 0 与间隔测试 monkeypatch `backfill_mod._INTER_LOTTERY_INTERVAL`\n（模块内 import 引用）依然有效，测试写法不变。\n\n第 4 步循环（`for code, draw_days in _enabled_lotteries(engine):`）改为：\n\n```python\n    fetched = 0\n    for code, draw_days in _enabled_lotteries(engine):\n        try:\n            missed = any(d.weekday() in draw_days and not _has_draw_for_date(engine, code, d) for d in lookback_days)\n            if missed:\n                # QPS 间隔只加在真实请求之间（missed 检查跳过的彩种不白等）；\n                # 首个抓取不等待（plan-11，镜像 jobs._path_a_tick 的 L-20260726 语义）。\n                if fetched > 0:\n                    time.sleep(_INTER_LOTTERY_INTERVAL)\n                fetch_service.fetch_and_store(code)\n                fetched += 1\n        except Exception:\n            # 单彩种源故障不得阻断其他彩种（silent-failure 纪律）。\n            logger.error('startup_backfill_fetch_failed code=%s', code, exc_info=True)\n\n    # 数据源健康评估（plan-11）：开机即评估一次（覆盖白天故障/停机后恢复场景）。\n    try:\n        evaluate_source_alerts(engine, build_admin_alert())\n    except Exception:\n        logger.error('source_alert_evaluate_failed', exc_info=True)\n```\n\n（原循环体的 try/except 与 missed 判断保持原样，仅包入 fetched 计数与 sleep。）\n\n- [ ] **Step 6: conftest 补置 0**\n\n`tests/conftest.py` 的 `_disable_inter_lottery_interval` fixture 内追加：\n\n```python\n    from app.scheduler import backfill as backfill_mod\n\n    monkeypatch.setattr(backfill_mod, '_INTER_LOTTERY_INTERVAL', 0)\n```\n\n（fixture docstring 同步提及 backfill。）\n\n- [ ] **Step 7: 新测试通过 + 调度器套件回归**\n\nRun: `uv run pytest tests/scheduler/ -q`\nExpected: 全部 PASS（含既有 `test_path_a_tick_paces_mxnzp_qps_with_inter_lottery_interval` 与全部 backfill 测试）。\n\n- [ ] **Step 8: Commit**\n\n```bash\ngit add app/scheduler/jobs.py app/scheduler/backfill.py tests/conftest.py tests/scheduler/test_jobs.py tests/scheduler/test_backfill.py\ngit commit -m \"feat(plan-11): tick/backfill 尾部评估源告警；启动回填对实际抓取加 QPS 间隔\"\n```\n\n---\n\n### Task 6: /admin/health 扩展 + Admin.vue 展示\n\n**Files:**\n- Modify: `app/api/admin.py:79-82`（system_health 响应）\n- Modify: `web/src/pages/Admin.vue:19-22`（HealthSource）、`web/src/pages/Admin.vue:644-648`（模板）\n- Test: `tests/api/test_admin.py:128-138`（扩展既有断言）\n\n**Interfaces:**\n- Consumes: Task 1 的 `down_since` 列。\n- Produces: `/admin/health` 每源多返回 `last_success_at` / `down_since`（ISO 字符串或 null）。\n\n- [ ] **Step 1: 扩展既有测试（RED）**\n\n`tests/api/test_admin.py` 的 `test_admin_system_health` 改为：\n\n```python\ndef test_admin_system_health(db_engine, monkeypatch):\n    with Session(db_engine) as s:\n        s.add(ApiSourceHealth(source='mxnzp', status='ok'))\n        s.add(ApiSourceHealth(source='juhe', status='degraded'))\n        s.commit()\n    client = _admin_client(db_engine, monkeypatch)\n    r = client.get('/admin/health')\n    assert r.status_code == 200\n    data = r.json()\n    assert len(data['sources']) == 2\n    assert {s['source'] for s in data['sources']} == {'mxnzp', 'juhe'}\n    # plan-11：每源返回 last_success_at/down_since（null 安全——空表行也要有键）\n    assert all({'last_success_at', 'down_since'} <= set(s) for s in data['sources'])\n```\n\n- [ ] **Step 2: 跑测试确认失败**\n\nRun: `uv run pytest tests/api/test_admin.py::test_admin_system_health -q`\nExpected: FAIL（响应无 last_success_at 键）。\n\n- [ ] **Step 3: 实现响应扩展**\n\n`app/api/admin.py` system_health 改为：\n\n```python\n@router.get('/health')\ndef system_health(session: Session = Depends(get_session_dep)):\n    sources = session.exec(select(ApiSourceHealth)).all()\n    return {\n        'sources': [\n            {\n                'source': s.source,\n                'status': s.status,\n                'last_success_at': s.last_success_at.isoformat() if s.last_success_at else None,\n                'down_since': s.down_since.isoformat() if s.down_since else None,\n            }\n            for s in sources\n        ]\n    }\n```\n\n- [ ] **Step 4: Admin.vue 展示**\n\n`HealthSource` 接口改为：\n\n```typescript\ninterface HealthSource {\n  source: string;\n  status: string;\n  last_success_at: string | null;\n  down_since: string | null;\n}\n```\n\n模板（644-648 行）`source-item` 内追加一段（放在 status 之后）：\n\n```html\n            <div v-for=\"s in health\" :key=\"s.source\" class=\"source-item\">\n              <span class=\"source-name\">{{ s.source }}</span>\n              <span class=\"source-meta\">\n                {{ s.down_since ? `故障自 ${s.down_since.slice(0, 16).replace('T', ' ')}` : (s.last_success_at ? `最后成功 ${s.last_success_at.slice(0, 16).replace('T', ' ')}` : '—') }}\n              </span>\n              <span class=\"source-status\" :class=\"s.status\">{{ s.status }}</span>\n            </div>\n```\n\n（`.source-meta` 样式：沿用卡内次要文字的既有 class；若无，在组件 style 尾部加\n`.source-meta { color: var(--vt-c-text-2, #888); font-size: 0.8rem; }`，以文件内既有变量为准。）\n\n- [ ] **Step 5: 前后端验证**\n\nRun: `uv run pytest tests/api/test_admin.py -q && npm --prefix web run build && npm --prefix web run test`\nExpected: pytest PASS；vue-tsc + vite build exit 0；vitest PASS（Admin.test.ts 默认 mock `{sources: []}` 不受影响）。\n\n- [ ] **Step 6: Commit**\n\n```bash\ngit add app/api/admin.py web/src/pages/Admin.vue tests/api/test_admin.py\ngit commit -m \"feat(plan-11): /admin/health 返回 last_success_at/down_since，面板展示故障起点\"","newText":"def test_record_permanent_on_fresh_row_keeps_unknown(db_engine):\n    \"\"\"全新行直接 permanent（部署首日 juhe 未配 key 的真实路径）→ status 保持\n    unknown、error 留痕（design-voice D7：既有用例只覆盖 ok 之后的 permanent，\n    新行路径才是部署时实际发生的）。\"\"\"\n    record_source_health(db_engine, 'juhe', 'permanent', 'juhe api_key not configured')\n    h = _get(db_engine, 'juhe')\n    assert h.status == 'unknown' and h.down_since is None\n    assert h.error == 'juhe api_key not configured'\n\n\ndef test_record_ok_on_alerted_transitions_recovering_keeps_down_since(db_engine):\n    \"\"\"已告警（alerted）的源恢复 → recovering 且保留 down_since（供恢复通知算时长）。\"\"\"\n    t0 = datetime(2026, 9, 15, 4, 0, 0)\n    record_source_health(db_engine, 'mxnzp', 'down', 'e', now=lambda: t0)\n    with Session(db_engine) as s:\n        s.get(ApiSourceHealth, 'mxnzp').alerted = 'alerted'\n        s.commit()\n    record_source_health(db_engine, 'mxnzp', 'ok', now=lambda: t0 + timedelta(hours=2))\n    h = _get(db_engine)\n    assert h.alerted == 'recovering'\n    assert h.down_since == t0  # 保留，评估侧送达恢复通知后清除\n\n\ndef test_record_down_cancels_pending_recovery(db_engine):\n    \"\"\"抖动：recovering 期间再次失败 → 回 alerted（取消待发的过时恢复通知）。\"\"\"\n    t0 = datetime(2026, 9, 15, 4, 0, 0)\n    with Session(db_engine) as s:\n        s.add(ApiSourceHealth(source='mxnzp', status='ok', alerted='recovering',\n                              down_since=t0))\n        s.commit()\n    record_source_health(db_engine, 'mxnzp', 'down', 'again', now=lambda: t0 + timedelta(minutes=1))\n    h = _get(db_engine)\n    assert h.alerted == 'alerted' and h.status == 'down'\n\n\ndef test_record_ok_after_long_outage_without_delivered_alert_goes_recovering(db_engine):\n    \"\"\"长故障期间告警从未送达（通道同挂，DNS 教训）→ 恢复时不得静默清零——\n\n    置 recovering 补发恢复通知（autoplan M11：否则 9 天故障自愈 = 零通知）。\n    \"\"\"\n    t0 = datetime(2026, 9, 1, 4, 0, 0)\n    record_source_health(db_engine, 'mxnzp', 'down', 'e', now=lambda: t0)\n    # 故障期每次评估尝试告警均失败（状态保持 none），第 9 天直接恢复\n    record_source_health(db_engine, 'mxnzp', 'ok', now=lambda: t0 + timedelta(days=9))\n    h = _get(db_engine)\n    assert h.alerted == 'recovering' and h.down_since == t0\n\n\ndef test_record_ok_after_short_outage_clears_silently(db_engine):\n    \"\"\"短故障（<30min，从未达告警阈值）恢复 → 清 down_since，不留 recovering（M11 边界）。\"\"\"\n    t0 = datetime(2026, 9, 15, 4, 0, 0)\n    record_source_health(db_engine, 'mxnzp', 'down', 'e', now=lambda: t0)\n    record_source_health(db_engine, 'mxnzp', 'ok', now=lambda: t0 + timedelta(minutes=10))\n    h = _get(db_engine)\n    assert h.alerted == 'none' and h.down_since is None\n\n\ndef test_record_error_redacts_secret_query_params(db_engine):\n    \"\"\"error 含 URL query 密钥（juhe key= 等）→ 落表前脱敏（autoplan M13）。\n\n    juhe.py:27 把 api key 放 query，raise_for_status 异常消息含完整 URL；\n    健康表 error 会进 admin 面板与 Bark 告警体（第三方服务器），密钥不得外泄。\n    \"\"\"\n    record_source_health(\n        db_engine, 'juhe', 'down',\n        \"Client error '403' for url 'https://v.juhe.cn/lottery/query?lottery_id=ssq&key=SECRETKEY123'\",\n    )\n    h = _get(db_engine, 'juhe')\n    assert 'SECRETKEY123' not in (h.error or '')\n    assert 'key=[REDACTED]' in (h.error or '')\n```\n\n- [ ] **Step 2: 跑测试确认失败**\n\nRun: `uv run pytest tests/services/test_source_health.py -q`\nExpected: FAIL（`ModuleNotFoundError: app.services.source_health`）。\n\n- [ ] **Step 3: 实现 record_source_health（含 autoplan M11/M13）**\n\n`app/services/source_health.py`：\n\n```python\n\"\"\"数据源健康记录与告警评估（plan-11 / 2026-09-15 DNS 事故跟进）。\n\nApiSourceHealth 长期「有读无写」（admin 面板空表）——本模块补写路径：fetch 按\n源记录 ok/down；评估器按「down 持续 ≥30 分钟」发 admin Bark（送达才转移状态。\nDNS 教训：故障期告警通道大概率同挂，未送达必须下轮重试）。\n\n时间纪律（CLAUDE.md）：down_since/last_success_at 均 naive UTC，与\nTimestampMixin.created_at 同表示，DB 内不做 naive/aware 混比。\n\"\"\"\n\nimport logging\nimport re\nfrom collections.abc import Callable\nfrom datetime import datetime, timedelta, timezone\n\nfrom sqlalchemy.engine import Engine\nfrom sqlmodel import Session, select\n\nfrom app.models import ApiSourceHealth\n\nlogger = logging.getLogger(__name__)\n\n# 「连续 2 个 tick 全失败」的时间窗实现（spec §1.3）：与 tick 次数解耦——\n# 持久、不怕容器重启（2026-09-15 事故中容器恰在故障期重启，内存计数会清零）。\nDOWN_ALERT_AFTER = timedelta(minutes=30)\n\n# error 脱敏（autoplan M13）：juhe 把 api key 放 query（juhe.py:27），\n# raise_for_status 异常消息含完整 URL；健康表 error 会进 admin 面板与 Bark\n# 告警体（第三方服务器），密钥参数值落表前一律替换 [REDACTED]。\n_SENSITIVE_QUERY_RE = re.compile(r'([?&](?:key|app_id|app_secret|token)=)[^&\\s]+')\n\n# now 注入点：生产用默认；测试注入固定时钟，避免真实 sleep/时间竞争。\n_NowFn = Callable[[], datetime]\n\n\ndef now_naive_utc() -> datetime:\n    return datetime.now(timezone.utc).replace(tzinfo=None)\n\n\ndef _sanitize_error(error: str | None) -> str | None:\n    \"\"\"剥离 error 文本中的敏感 query 参数值（M13），保留 URL 其余部分供排障。\"\"\"\n    if error is None:\n        return None\n    return _SENSITIVE_QUERY_RE.sub(r'\\1[REDACTED]', error)\n\n\ndef record_source_health(\n    engine: Engine,\n    source: str,\n    outcome: str,\n    error: str | None = None,\n    now: _NowFn = now_naive_utc,\n) -> None:\n    \"\"\"按源 upsert 健康表（spec §1.2 语义表）。outcome: 'ok' | 'down' | 'permanent'。\n\n    permanent（key 未配置等配置态）：仅记 error——单源部署下未配置的备源若计入\n    down 会永久故障且天天告警（juhe 不可用是长期事实，非运行故障）。\n\n    ok + alerted=='none' + 故障时长 ≥ DOWN_ALERT_AFTER（autoplan M11）：转\n    recovering 补发恢复通知——故障期告警通道同挂（DNS 教训）导致告警从未送达，\n    恢复时若静默清零，长故障将零通知（9 天事故复现路径）。\n    \"\"\"\n    t = now()\n    with Session(engine) as s:\n        h = s.get(ApiSourceHealth, source)\n        if h is None:\n            h = ApiSourceHealth(source=source)\n            s.add(h)\n        if outcome == 'ok':\n            h.status = 'ok'\n            h.last_success_at = t\n            h.error = None\n            if h.alerted == 'none':\n                if h.down_since is not None and t - h.down_since >= DOWN_ALERT_AFTER:\n                    h.alerted = 'recovering'  # M11：长故障零送达 → 补发恢复通知\n                else:\n                    h.down_since = None  # 短故障：静默恢复（未达告警阈值，无通知义务）\n            else:\n                # 已告警过 → 待恢复通知；保留 down_since 供评估侧算故障时长。\n                h.alerted = 'recovering'\n        elif outcome == 'down':\n            h.status = 'down'\n            if h.down_since is None:\n                h.down_since = t\n            if h.alerted == 'recovering':\n                h.alerted = 'alerted'  # 抖动：取消待发的过时恢复通知\n            h.error = _sanitize_error(error)\n        elif outcome == 'permanent':\n            h.error = _sanitize_error(error)\n        else:\n            raise ValueError(f'unknown outcome: {outcome}')\n        s.commit()\n```\n\n- [ ] **Step 4: 跑测试确认通过**\n\nRun: `uv run pytest tests/services/test_source_health.py -q`\nExpected: 9 passed（原 5 + M11×2 + M13×1 + D7×1）。\n\n- [ ] **Step 5: FetchService 三态接线（回归保护既有语义）**\n\n`app/services/fetch_service.py`：\n\n改 `_try_fetch`（原 111-116 行）：\n\n```python\n    def _try_fetch(\n        self, source: DrawSource, lottery_code: str\n    ) -> tuple[DrawNumbers | None, str, str | None]:\n        \"\"\"返回 (numbers, outcome, error)。outcome: 'ok' | 'down' | 'permanent'。\n\n        ok+None=未开奖（源健康）；down=运行故障（网络/限流重试耗尽）；\n        permanent=配置态错误（key 未配置等）——健康表据此区分（plan-11 spec §1.2）。\n        \"\"\"\n        try:\n            return self._fetch_with_backoff(source, lottery_code), 'ok', None\n        except Exception as exc:\n            if isinstance(exc, PermanentLookupError):\n                return None, 'permanent', str(exc)\n            return None, 'down', str(exc)\n```\n\n改 `fetch_and_store` 头部（原 118-121 行）：\n\n```python\n    def fetch_and_store(self, lottery_code: str) -> FetchResult:\n        primary, p_outcome, p_err = self._try_fetch(self._primary, lottery_code)\n        backup, b_outcome, b_err = self._try_fetch(self._backup, lottery_code)\n        # 数据源健康落表（plan-11）：写失败不得阻断抓取（spec §1.2 独立短事务）。\n        self._record_health(self._primary.name, p_outcome, p_err)\n        self._record_health(self._backup.name, b_outcome, b_err)\n        p_ok = p_outcome == 'ok'\n        b_ok = b_outcome == 'ok'\n```\n\n（后续 `if not p_ok and not b_ok:` 等分支逻辑不动——布尔语义与旧版一致。）\n\n`_grace_refetch` 内（约 195 行）原 `m2, m2_ok = self._try_fetch(missing_source, lottery_code)` 改为：\n\n```python\n        m2, m2_outcome, m2_err = self._try_fetch(missing_source, lottery_code)\n        # grace 重抓结果同样落健康表（autoplan M7）：否则 grace 内恢复的源要等下个\n        # 抓取周期才转 ok，恢复通知无谓延迟一整轮（15 分钟）。\n        self._record_health(str(missing_source.name), m2_outcome, m2_err)\n        m2_ok = m2_outcome == 'ok'\n```\n\n`FetchService` 类内新增方法（放在 `_try_fetch` 之后）：\n\n```python\n    def _record_health(self, source_name, outcome: str, error: str | None) -> None:\n        \"\"\"写 ApiSourceHealth（plan-11）。独立短事务 + 吞异常：健康落表失败只记日志，\n        绝不阻断抓取主流程（spec §1.2）。\"\"\"\n        try:\n            from app.services.source_health import record_source_health\n\n            record_source_health(self._engine, str(source_name), outcome, error)\n        except Exception:\n            logger.warning(\n                'source_health_write_failed source=%s outcome=%s', source_name, outcome,\n                exc_info=True,\n            )\n```\n\n（函数内 import：避免 fetch_service ↔ source_health 潜在环；模块顶部 import 亦可，以 lint-imports 通过为准。）\n\n- [ ] **Step 6: 既有 fetch 套件回归**\n\nRun: `uv run pytest tests/services/test_fetch_service.py tests/adapters/ tests/integration/ -q`\nExpected: 全部 PASS（健康写入对既有断言无感——独立表、独立事务）。\n\n- [ ] **Step 7: Commit**\n\n```bash\ngit add app/services/source_health.py app/services/fetch_service.py tests/services/test_source_health.py\ngit commit -m \"feat(plan-11): fetch 按源三态落 ApiSourceHealth（ok/down/permanent，故障起点不刷新）\"\n```\n\n---\n\n### Task 4: evaluate_source_alerts 告警状态机\n\n**Files:**\n- Modify: `app/services/source_health.py`（追加评估器）\n- Test: `tests/services/test_source_health.py`（追加）\n\n**Interfaces:**\n- Consumes: Task 3 的 `record_source_health` / `DOWN_ALERT_AFTER`。\n- Produces: `evaluate_source_alerts(engine: Engine, send_alert: Callable[[str, str], None] | None, now: _NowFn = now_naive_utc) -> None`——Task 5 接线依赖。\n\n- [ ] **Step 1: 写失败测试（追加到 test_source_health.py）**\n\n```python\n# ---------- evaluate_source_alerts 状态机（spec §1.3） ----------\n\nimport logging\n\nfrom app.services.source_health import evaluate_source_alerts\n\n\nclass _Recorder:\n    \"\"\"记 send_alert 调用；可控抛异常模拟「告警通道也挂了」（DNS 教训）。\"\"\"\n\n    def __init__(self, fail_first=0):\n        self.calls = []\n        self.fail_first = fail_first\n\n    def __call__(self, title, body):\n        if self.fail_first > 0:\n            self.fail_first -= 1\n            raise ConnectionError('bark down')\n        self.calls.append((title, body))\n\n\ndef _seed_down(engine, down_since, alerted='none', status='down'):\n    with Session(engine) as s:\n        s.add(ApiSourceHealth(source='mxnzp', status=status, alerted=alerted,\n                              down_since=down_since, last_success_at=None))\n        s.commit()\n\n\ndef test_evaluate_no_alert_before_threshold(db_engine):\n    \"\"\"down 不足 30 分钟 → 不告警。\"\"\"\n    t = datetime(2026, 9, 15, 13, 0, 0)\n    _seed_down(db_engine, down_since=t - timedelta(minutes=29))\n    rec = _Recorder()\n    evaluate_source_alerts(db_engine, rec, now=lambda: t)\n    assert rec.calls == []\n    assert _get(db_engine).alerted == 'none'\n\n\ndef test_evaluate_alerts_once_after_threshold(db_engine):\n    \"\"\"down ≥30 分钟 → 告警一次并置 alerted；继续 down 不重复告警。\"\"\"\n    t = datetime(2026, 9, 15, 13, 0, 0)\n    _seed_down(db_engine, down_since=t - timedelta(minutes=31))\n    rec = _Recorder()\n    evaluate_source_alerts(db_engine, rec, now=lambda: t)\n    evaluate_source_alerts(db_engine, rec, now=lambda: t + timedelta(minutes=15))\n    assert len(rec.calls) == 1 and '持续失败' in rec.calls[0][0]\n    assert _get(db_engine).alerted == 'alerted'\n\n\ndef test_evaluate_send_failure_keeps_state_and_retries(db_engine):\n    \"\"\"发送异常 → 状态保持 none，下轮重试；送达成功才转移（DNS 教训回归）。\"\"\"\n    t = datetime(2026, 9, 15, 13, 0, 0)\n    _seed_down(db_engine, down_since=t - timedelta(minutes=40))\n    rec = _Recorder(fail_first=1)\n    evaluate_source_alerts(db_engine, rec, now=lambda: t)\n    assert rec.calls == [] and _get(db_engine).alerted == 'none'\n    evaluate_source_alerts(db_engine, rec, now=lambda: t + timedelta(minutes=15))\n    assert len(rec.calls) == 1 and _get(db_engine).alerted == 'alerted'\n\n\ndef test_recovery_notice_sent_then_reset(db_engine):\n    \"\"\"alerted → 抓取恢复（写入侧置 recovering）→ 评估送出恢复通知 → none+清 down_since。\"\"\"\n    t = datetime(2026, 9, 15, 13, 0, 0)\n    _seed_down(db_engine, down_since=t - timedelta(hours=2), alerted='alerted')\n    record_source_health(db_engine, 'mxnzp', 'ok', now=lambda: t)  # → recovering\n    rec = _Recorder()\n    evaluate_source_alerts(db_engine, rec, now=lambda: t)\n    assert len(rec.calls) == 1 and '恢复' in rec.calls[0][0]\n    h = _get(db_engine)\n    assert h.alerted == 'none' and h.down_since is None\n\n\ndef test_recovery_send_failure_retries(db_engine):\n    \"\"\"恢复通知发送失败 → 保持 recovering，下轮送达才回 none。\"\"\"\n    t = datetime(2026, 9, 15, 13, 0, 0)\n    _seed_down(db_engine, down_since=t - timedelta(hours=2), alerted='alerted')\n    record_source_health(db_engine, 'mxnzp', 'ok', now=lambda: t)\n    rec = _Recorder(fail_first=1)\n    evaluate_source_alerts(db_engine, rec, now=lambda: t)\n    assert _get(db_engine).alerted == 'recovering'\n    evaluate_source_alerts(db_engine, rec, now=lambda: t + timedelta(minutes=15))\n    assert _get(db_engine).alerted == 'none'\n\n\ndef test_no_sender_still_transitions(db_engine):\n    \"\"\"ADMIN_BARK_KEY 未配（send_alert=None）→ 不发送但状态照常流转（表可见 down）。\"\"\"\n    t = datetime(2026, 9, 15, 13, 0, 0)\n    _seed_down(db_engine, down_since=t - timedelta(minutes=40))\n    evaluate_source_alerts(db_engine, None, now=lambda: t)\n    assert _get(db_engine).alerted == 'alerted'\n\n\ndef test_evaluate_sends_outside_db_session(db_engine):\n    \"\"\"M1 回归：send_alert 调用时评估器不得持有 DB 连接（pool_size=1 纪律）。\n\n    若评估器在 session 内发送（plan 原稿），DNS 故障下 Bark 挂 10s 超时期间\n    唯一连接被占，其他 job/请求撞 busy_timeout——jobs.py:276-278 两次事故同型。\n    \"\"\"\n    t = datetime(2026, 9, 15, 13, 0, 0)\n    _seed_down(db_engine, down_since=t - timedelta(minutes=40))\n    checked_out_during_send = []\n\n    def _sender(title, body):\n        checked_out_during_send.append(db_engine.pool.checkedout())\n\n    evaluate_source_alerts(db_engine, _sender, now=lambda: t)\n    assert checked_out_during_send == [0]\n\n\ndef test_alert_body_includes_peer_source_status(db_engine):\n    \"\"\"告警体必须含备源状态（design-voice D5）：备源 ok 时明说「开奖未受影响」，\n    避免单源故障的告警读起来像已经漏开奖（告警疲劳最快路径）。\"\"\"\n    t = datetime(2026, 9, 15, 13, 0, 0)\n    _seed_down(db_engine, down_since=t - timedelta(minutes=40))\n    with Session(db_engine) as s:\n        s.add(ApiSourceHealth(source='juhe', status='ok'))\n        s.commit()\n    rec = _Recorder()\n    evaluate_source_alerts(db_engine, rec, now=lambda: t)\n    assert len(rec.calls) == 1\n    assert '备用源正常' in rec.calls[0][1] and '开奖未受影响' in rec.calls[0][1]\n    assert '（自' not in rec.calls[0][1]  # D3：不带 UTC 绝对时间（与同句分钟数矛盾）\n\n- [ ] **Step 2: 跑测试确认失败**\n\nRun: `uv run pytest tests/services/test_source_health.py -q -k evaluate`\nExpected: FAIL（ImportError: evaluate_source_alerts）。\n\n- [ ] **Step 3: 实现评估器（追加到 source_health.py，autoplan M1 两阶段）**\n\n```python\ndef evaluate_source_alerts(\n    engine: Engine,\n    send_alert: Callable[[str, str], None] | None,\n    now: _NowFn = now_naive_utc,\n) -> None:\n    \"\"\"评估健康表驱动告警状态机（spec §1.3；挂载于 path_a tick 尾 + 启动 backfill 尾）。\n\n    send_alert=None（ADMIN_BARK_KEY 未配）→ 只做状态转移不发送，admin 面板仍可见。\n    发送异常不转移状态（下轮重试直到送达）——2026-09-15 DNS 事故教训：故障期\n    告警通道大概率同时挂。\n\n    两阶段（autoplan M1，pool_size=1 纪律）：短 session 读+决策后关闭 → session\n    外发 HTTP 告警 → 短 session 守卫重读后落转移。绝不在持有唯一连接的 session\n    内做 httpx 调用——DNS 故障（本 plan 目标场景）下 Bark 挂到 10s 超时，同期\n    其他 job/请求借不到连接撞 busy_timeout，告警机制反而制造它要防的漏通知\n    （jobs.py:276-278/339、password_reset_service.py:131/204 两次实测事故同型）。\n    落转移前重读校验状态未变：读与落之间若有 fetch 写入（如故障恰好恢复），\n    放弃本轮转移下轮重评，不覆盖并发写入。\n    \"\"\"\n    t = now()\n    # 阶段 1：短 session 读 + 决策（快照出 session，不留 ORM 对象跨 session）\n    with Session(engine) as s:\n        rows = [\n            (h.source, h.status, h.alerted, h.down_since, h.error)\n            for h in s.exec(select(ApiSourceHealth)).all()\n        ]\n    # 备源状态速查（design-voice D5：告警体必须回答「开奖是否受影响」——\n    # 单源 down 但备源正常时，告警文案若暗示漏开奖是最快的静音之路）。\n    peer_status = {source: status for source, status, *_ in rows}\n    # 阶段 2：session 外发送；送达成功的进待落清单\n    delivered: list[tuple[str, str]] = []  # (source, 目标 alerted 状态)\n    for source, status, alerted, down_since, error in rows:\n        if (\n            status == 'down'\n            and alerted == 'none'\n            and down_since is not None\n            and t - down_since >= DOWN_ALERT_AFTER\n        ):\n            minutes = int((t - down_since).total_seconds() // 60)\n            if send_alert is not None:\n                peers = {p: ps for p, ps in peer_status.items() if p != source}\n                if not peers:\n                    impact = '仅此一个数据源，开奖可能延迟入库'\n                elif all(ps == 'ok' for ps in peers.values()):\n                    impact = f'备用源正常（{\"、\".join(peers)}），开奖未受影响'\n                elif any(ps == 'down' for ps in peers.values()):\n                    impact = '双源同时故障，开奖可能延迟入库'\n                else:\n                    # 备源 unknown/degraded（如 juhe 未配置）——不是「双源故障」，\n                    # 但主源 down 时实际上只剩单点，延迟风险同样要说清。\n                    impact = (\n                        f'备用源非健康（{\"、\".join(f\"{p}={ps}\" for p, ps in peers.items())}），'\n                        f'开奖可能延迟入库'\n                    )\n                try:\n                    # 文案不带绝对时间（design-voice D3：UTC 括号与同句的分钟数\n                    # 相差 8h 自相矛盾；时长已足够定位）。\n                    send_alert(\n                        '开奖抓取持续失败',\n                        f'数据源 {source} 已持续失败约 {minutes} 分钟。{impact}。'\n                        f'最近错误：{(error or \"\")[:200]}',\n                    )\n                except Exception:\n                    logger.warning(\n                        'source_alert_send_failed source=%s', source, exc_info=True\n                    )\n                    continue  # 未送达不转移，下轮重试\n            delivered.append((source, 'alerted'))\n        elif alerted == 'recovering' and status == 'ok':\n            duration = t - down_since if down_since else timedelta(0)\n            minutes = int(duration.total_seconds() // 60)\n            if send_alert is not None:\n                try:\n                    # 诚实声明缺口（design-voice D6：闭环不能只到「恢复」——\n                    # 故障窗口的开奖是否补回，admin 必须知道要不要人工介入）。\n                    send_alert(\n                        '开奖抓取已恢复',\n                        f'数据源 {source} 已恢复抓取（故障持续约 {minutes} 分钟）。'\n                        f'故障期间的开奖缺失将随今晚 path_a 轮询与启动回填'\n                        f'（最近 2 天）覆盖；更长缺口请人工确认是否需要补抓。',\n                    )\n                except Exception:\n                    logger.warning(\n                        'source_recovery_send_failed source=%s', source, exc_info=True\n                    )\n                    continue\n            delivered.append((source, 'none'))\n    if not delivered:\n        return\n    # 阶段 3：短 session 守卫重读后落转移（recovering 完成时清 down_since）\n    with Session(engine) as s:\n        for source, target in delivered:\n            h = s.get(ApiSourceHealth, source)\n            if h is None:\n                continue\n            if target == 'alerted' and h.alerted == 'none' and h.status == 'down':\n                h.alerted = 'alerted'\n            elif target == 'none' and h.alerted == 'recovering' and h.status == 'ok':\n                h.alerted = 'none'\n                h.down_since = None\n        s.commit()\n```\n\n（注：单源发送失败只跳过该源的状态转移，其他源继续评估与落库——两行数据源互不影响。）\n\n- [ ] **Step 4: 跑测试确认通过**\n\nRun: `uv run pytest tests/services/test_source_health.py -q`\nExpected: 17 passed（Task 3 的 9 + 本任务 8）。\n\n- [ ] **Step 5: Commit**\n\n```bash\ngit add app/services/source_health.py tests/services/test_source_health.py\ngit commit -m \"feat(plan-11): evaluate_source_alerts 告警状态机（down≥30min 告警、恢复通知、送达才转移）\"\n```\n\n---\n\n### Task 5: 调度接线 + 启动回填 QPS 间隔\n\n**Files:**\n- Modify: `app/scheduler/jobs.py`（`_path_a_tick` 末尾 + import）\n- Modify: `app/scheduler/backfill.py`（第 4 步间隔 + 函数尾评估 + 常量）\n- Modify: `tests/conftest.py:14-26`（autouse fixture 补 backfill 常量置 0）\n- Test: `tests/scheduler/test_jobs.py`（追加）、`tests/scheduler/test_backfill.py`（追加）\n\n**Interfaces:**\n- Consumes: Task 4 `evaluate_source_alerts`、Task 2 `build_admin_alert`。\n- Produces: `_path_a_tick` 与 `run_startup_backfill` 尾部各调用一次评估；backfill 复用 `jobs._INTER_LOTTERY_INTERVAL`（M6 单一真值源）。\n\n- [ ] **Step 1: 写失败测试（test_jobs.py 追加，用仓库既有 `_invoke_job` 辅助——见文件内其他 tick 测试）**\n\n```python\ndef test_path_a_tick_evaluates_source_alerts(db_engine, monkeypatch):\n    \"\"\"path_a_tick 尾部必须评估数据源健康告警（plan-11：tick 即评估点）。\"\"\"\n    from unittest.mock import MagicMock\n\n    import app.scheduler.jobs as jobs_mod\n    from app.scheduler.setup import build_scheduler\n\n    spy = MagicMock()\n    monkeypatch.setattr(jobs_mod, 'evaluate_source_alerts', spy)\n    sched = build_scheduler(db_engine)\n    register_all_jobs(\n        sched,\n        {\n            'engine': db_engine,\n            'fetch_service': MagicMock(),\n            'compare_service': MagicMock(),\n            'refill_worker': MagicMock(),\n            'notifier': MagicMock(),\n        },\n    )\n    _invoke_job(sched, 'path_a_poll_evening')\n    assert spy.call_count == 1\n    # engine 为第一参数，sender 来自 build_admin_alert（测试无 key → None）\n    assert spy.call_args.args[0] is db_engine\n```\n\n- [ ] **Step 2: 写失败测试（test_backfill.py 追加）**\n\n```python\ndef test_startup_backfill_paces_fetches_with_interval(db_engine, monkeypatch):\n    \"\"\"启动回填对实际抓取的彩种加 QPS 间隔：第 2 个起每次 fetch 前 sleep（plan-11）。\"\"\"\n    import app.scheduler.backfill as backfill_mod\n    from app.models import LotteryType\n\n    monkeypatch.setattr(backfill_mod, '_INTER_LOTTERY_INTERVAL', 1.2)\n    sleeps = []\n    monkeypatch.setattr(backfill_mod.time, 'sleep', lambda s: sleeps.append(s))\n\n    # 3 个彩种全部 missed（DB 无开奖 + draw_days 覆盖回看窗口）\n    monkeypatch.setattr(\n        backfill_mod, '_enabled_lotteries',\n        lambda engine: [('a', [0, 1, 2, 3, 4, 5, 6]), ('b', [0, 1, 2, 3, 4, 5, 6]),\n                        ('c', [0, 1, 2, 3, 4, 5, 6])],\n    )\n    monkeypatch.setattr(backfill_mod, '_has_draw_for_date', lambda engine, code, d: False)\n\n    deps = _make_deps(db_engine)\n    run_startup_backfill(deps)\n    assert deps['fetch_service'].fetch_and_store.call_count == 3\n    assert sleeps == [1.2, 1.2]  # 首个抓取不 sleep\n\n\ndef test_startup_backfill_evaluates_source_alerts(db_engine, monkeypatch):\n    \"\"\"启动 backfill 尾部评估健康告警（plan-11：开机即评估）。\"\"\"\n    import app.scheduler.backfill as backfill_mod\n\n    spy = MagicMock()\n    monkeypatch.setattr(backfill_mod, 'evaluate_source_alerts', spy)\n    run_startup_backfill(_make_deps(db_engine))\n    assert spy.call_count == 1\n```\n\n- [ ] **Step 3: 跑两个新测试确认失败**\n\nRun: `uv run pytest tests/scheduler/test_jobs.py::test_path_a_tick_evaluates_source_alerts tests/scheduler/test_backfill.py::test_startup_backfill_paces_fetches_with_interval tests/scheduler/test_backfill.py::test_startup_backfill_evaluates_source_alerts -q`\nExpected: 3 FAIL（AttributeError: evaluate_source_alerts / _INTER_LOTTERY_INTERVAL）。\n\n- [ ] **Step 4: 实现 jobs.py 接线**\n\n`app/scheduler/jobs.py` import 区加：\n\n```python\nfrom app.notifications.admin_alert import build_admin_alert\nfrom app.services.source_health import evaluate_source_alerts\n```\n\n`_path_a_tick` 函数末尾（`sched.add_job(_push_big_win, ...)` 循环之后、函数体结束前）追加：\n\n```python\n    # 数据源健康评估（plan-11）：tick 尾部评估告警状态机（down≥30min → admin bark；\n    # 未配 ADMIN_BARK_KEY 则 sender=None 只转移状态）。评估失败不阻断本 tick 收尾。\n    try:\n        evaluate_source_alerts(engine, build_admin_alert())\n    except Exception:\n        logger.error('source_alert_evaluate_failed', exc_info=True)\n```\n\n- [ ] **Step 5: 实现 backfill.py 间隔 + 尾部评估**\n\n`app/scheduler/backfill.py`：import 区加 `import time`（若未有）、`from app.notifications.admin_alert import build_admin_alert`、`from app.services.source_health import evaluate_source_alerts`，以及（autoplan M6）：\n\n```python\nfrom app.scheduler.jobs import _INTER_LOTTERY_INTERVAL\n```\n\nM6 说明：plan 原稿在 backfill 本地复制 `_INTER_LOTTERY_INTERVAL = 1.2`（注释称「反向\nimport 会循环依赖」）——已核实 jobs.py 不 import backfill，无环，复制只会漂移\n（MXNZP QPS 限额调整时需改两处）。单一真值源：backfill 直接 import jobs 常量；\nconftest 置 0 与间隔测试 monkeypatch `backfill_mod._INTER_LOTTERY_INTERVAL`\n（模块内 import 引用）依然有效，测试写法不变。\n\n第 4 步循环（`for code, draw_days in _enabled_lotteries(engine):`）改为：\n\n```python\n    fetched = 0\n    for code, draw_days in _enabled_lotteries(engine):\n        try:\n            missed = any(d.weekday() in draw_days and not _has_draw_for_date(engine, code, d) for d in lookback_days)\n            if missed:\n                # QPS 间隔只加在真实请求之间（missed 检查跳过的彩种不白等）；\n                # 首个抓取不等待（plan-11，镜像 jobs._path_a_tick 的 L-20260726 语义）。\n                if fetched > 0:\n                    time.sleep(_INTER_LOTTERY_INTERVAL)\n                fetch_service.fetch_and_store(code)\n                fetched += 1\n        except Exception:\n            # 单彩种源故障不得阻断其他彩种（silent-failure 纪律）。\n            logger.error('startup_backfill_fetch_failed code=%s', code, exc_info=True)\n\n    # 数据源健康评估（plan-11）：开机即评估一次（覆盖白天故障/停机后恢复场景）。\n    try:\n        evaluate_source_alerts(engine, build_admin_alert())\n    except Exception:\n        logger.error('source_alert_evaluate_failed', exc_info=True)\n```\n\n（原循环体的 try/except 与 missed 判断保持原样，仅包入 fetched 计数与 sleep。）\n\n- [ ] **Step 6: conftest 补置 0**\n\n`tests/conftest.py` 的 `_disable_inter_lottery_interval` fixture 内追加：\n\n```python\n    from app.scheduler import backfill as backfill_mod\n\n    monkeypatch.setattr(backfill_mod, '_INTER_LOTTERY_INTERVAL', 0)\n```\n\n（fixture docstring 同步提及 backfill。）\n\n- [ ] **Step 7: 新测试通过 + 调度器套件回归**\n\nRun: `uv run pytest tests/scheduler/ -q`\nExpected: 全部 PASS（含既有 `test_path_a_tick_paces_mxnzp_qps_with_inter_lottery_interval` 与全部 backfill 测试）。\n\n- [ ] **Step 8: Commit**\n\n```bash\ngit add app/scheduler/jobs.py app/scheduler/backfill.py tests/conftest.py tests/scheduler/test_jobs.py tests/scheduler/test_backfill.py\ngit commit -m \"feat(plan-11): tick/backfill 尾部评估源告警；启动回填对实际抓取加 QPS 间隔\"\n```\n\n---\n\n### Task 6: /admin/health 扩展 + Admin.vue 展示\n\n**Files:**\n- Modify: `app/api/admin.py:79-82`（system_health 响应）\n- Modify: `web/src/pages/Admin.vue:19-22`（HealthSource）、`web/src/pages/Admin.vue:644-648`（模板）\n- Test: `tests/api/test_admin.py:128-138`（扩展既有断言）\n\n**Interfaces:**\n- Consumes: Task 1 的 `down_since` 列。\n- Produces: `/admin/health` 每源返回 `status` / `alerted` / `error`（截断+…）/ `last_success_at` / `down_since`（后两个为显式 UTC ISO 或 null）。\n\n- [ ] **Step 1: 扩展既有测试（RED，含 autoplan D-3 时区断言）**\n\n`tests/api/test_admin.py` 的 `test_admin_system_health` 改为：\n\n```python\ndef test_admin_system_health(db_engine, monkeypatch):\n    with Session(db_engine) as s:\n        s.add(ApiSourceHealth(source='mxnzp', status='ok',\n                              last_success_at=datetime(2026, 9, 15, 4, 0, 0)))\n        s.add(ApiSourceHealth(source='juhe', status='degraded',\n                              down_since=datetime(2026, 9, 14, 20, 0, 0)))\n        s.commit()\n    client = _admin_client(db_engine, monkeypatch)\n    r = client.get('/admin/health')\n    assert r.status_code == 200\n    data = r.json()\n    assert len(data['sources']) == 2\n    assert {s['source'] for s in data['sources']} == {'mxnzp', 'juhe'}\n    # plan-11：每源返回 last_success_at/down_since（null 安全——空表行也要有键）\n    # design-voice D4：alerted（状态机输出）与 error（故障原因）同返——面板必须\n    # 回答「叫过人没有」「为什么挂」，只给时间是次有用的信息。\n    assert all({'last_success_at', 'down_since', 'alerted', 'error'} <= set(s) for s in data['sources'])\n    # autoplan D-3：naive UTC 落库值必须以 'Z' 显式标注 UTC——否则前端\n    # new Date() 按本地时区解析，面板故障起点显示偏差 8 小时（全程 Asia/Shanghai 纪律）。\n    assert data['sources'][0]['last_success_at'].endswith('Z')\n    assert data['sources'][1]['down_since'].endswith('Z')\n```\n\n（文件头部 import 区补 `from datetime import datetime`，若未有。）\n\n- [ ] **Step 2: 跑测试确认失败**\n\nRun: `uv run pytest tests/api/test_admin.py::test_admin_system_health -q`\nExpected: FAIL（响应无 last_success_at 键）。\n\n- [ ] **Step 3: 实现响应扩展（含 autoplan D-3 显式 UTC）**\n\n`app/api/admin.py` system_health 改为：\n\n```python\ndef _iso_utc(dt) -> str | None:\n    \"\"\"naive UTC 落库值 → 显式 UTC ISO（追加 'Z'）。\n\n    裸 isoformat() 无时区标记，前端 new Date() 会按浏览器本地时区解析，\n    面板时间显示偏差 8 小时（autoplan D-3；全程 Asia/Shanghai 纪律）。\n    \"\"\"\n    return dt.isoformat() + 'Z' if dt else None\n\n\ndef _truncate(text: str | None, limit: int = 200) -> str | None:\n    \"\"\"error 截断 + 省略号（design-voice D17：截断必须可见，否则运维以为看全了）。\"\"\"\n    if text is None or len(text) <= limit:\n        return text\n    return text[:limit] + '…'\n\n\n@router.get('/health')\ndef system_health(session: Session = Depends(get_session_dep)):\n    sources = session.exec(select(ApiSourceHealth)).all()\n    return {\n        'sources': [\n            {\n                'source': s.source,\n                'status': s.status,\n                'alerted': s.alerted,\n                'error': _truncate(s.error),\n                'last_success_at': _iso_utc(s.last_success_at),\n                'down_since': _iso_utc(s.down_since),\n            }\n            for s in sources\n        ]\n    }\n```\n\n- [ ] **Step 4: Admin.vue 展示（含 autoplan D-1/D-2/D-4 + design-voice D3/D4/D17）**\n\n`HealthSource` 接口改为：\n\n```typescript\ninterface HealthSource {\n  source: string;\n  status: string;\n  alerted: string;\n  error: string | null;\n  last_success_at: string | null;\n  down_since: string | null;\n}\n```\n\nscript 区加时长 helper（design-voice D3：时长为主、时区免疫——面板要回答的是\n「多久了」，不是「几点几分」；后端已返回显式 UTC（'Z'），前端算差值即可，\n不引入第二个日期格式——页面既有 formatDate 管绝对时间，这里管时长）：\n\n```typescript\n// 由显式 UTC（'Z' 后缀）算到当前的时长文本：「X 分钟 / X 小时 N 分 / X 天 N 小时」。\nfunction fmtDuration(sinceIso: string): string {\n  const ms = Date.now() - new Date(sinceIso).getTime();\n  const minutes = Math.max(0, Math.floor(ms / 60000));\n  if (minutes < 60) return `${minutes} 分钟`;\n  const hours = Math.floor(minutes / 60);\n  if (hours < 24) return `${hours} 小时${minutes % 60 ? ` ${minutes % 60} 分` : ''}`;\n  const days = Math.floor(hours / 24);\n  return `${days} 天${hours % 24 ? ` ${hours % 24} 小时` : ''}`;\n}\n```\n\n模板（design-voice D2：这是**在既有 `source-item` div 内插入**两个 span——\n644 行 `v-if=\"health.length > 0\"` 与 650 行 `v-else class=\"empty-tip\"` 保持\n逐字节不动；下列片段仅示意插入位置，不是整段替换。插入点一：source-name 与\nsource-status 之间放 meta（时长 + error 摘要）；插入点二：source-status 前放\nalerted 标签（区分「挂了」与「挂了且已叫人」））：\n\n```html\n            <div v-for=\"s in health\" :key=\"s.source\" class=\"source-item\">\n              <span class=\"source-name\">{{ s.source }}</span>\n              <span class=\"source-meta\">\n                {{ s.down_since ? `已故障 ${fmtDuration(s.down_since)}` : (s.last_success_at ? `最后成功 ${fmtDuration(s.last_success_at)}前` : '—') }}\n                <span v-if=\"s.error\" class=\"source-error\" :title=\"s.error\">{{ s.error }}</span>\n              </span>\n              <span v-if=\"s.alerted === 'alerted'\" class=\"source-alert-tag\">已通知</span>\n              <span v-else-if=\"s.alerted === 'recovering'\" class=\"source-alert-tag recovering\">恢复待通知</span>\n              <span class=\"source-status\" :class=\"s.status\">{{ s.status }}</span>\n            </div>\n```\n\nstyle 区（autoplan D-1/D-2/D-4 + design-voice D4）：\n\n```css\n/* D-4：新增 meta/tag 后行内容变长，375px 下允许换行防挤压 */\n.source-item {\n  flex-wrap: wrap;\n  row-gap: 4px;\n}\n\n/* D-2：次要信息用既有 --muted token（tokens.css 含 dark 变体），\n   不引入 --vt-c-text-2（VitePress 变量，本项目不存在）或硬编码 #888 */\n.source-meta {\n  color: var(--muted);\n  font-size: var(--text-xs);\n}\n\n/* D4：error 摘要与 meta 同行但更可忽略；全文经 title 悬浮可见（已后端截断 + …） */\n.source-error {\n  margin-left: 6px;\n  opacity: 0.85;\n}\n\n/* D4：告警状态标签（沿用 status-badge 的 pill 模式与 DESIGN.md token） */\n.source-alert-tag {\n  padding: 3px 10px;\n  border-radius: 20px;\n  font-size: var(--text-xs);\n  font-weight: 600;\n  background: var(--surface-2);\n  color: var(--muted);\n}\n\n.source-alert-tag.recovering {\n  background: #fef3c7;\n  color: var(--warning);\n}\n\n/* D-1：健康状态色补齐——plan-11 起 down 真实落表，未定义 class 的状态会裸奔。\n   文字色用 DESIGN.md 语义 token（--danger/--warning/--muted），底色沿用既有 pill 风格 */\n.source-status.down {\n  background: #fee2e2;\n  color: var(--danger);\n}\n\n.source-status.degraded {\n  background: #fef3c7;\n  color: var(--warning);\n}\n\n.source-status.unknown {\n  background: var(--surface-2);\n  color: var(--muted);\n}\n```\n\n- [ ] **Step 5: 前端健康卡测试（design-voice D10：现有 Admin.test.ts 对健康卡零断言）**\n\n`web/src/pages/Admin.test.ts` 追加（stubApi 已支持 `overrides.health`，见该文件 35-36 行）：\n\n```typescript\nit('健康卡渲染 down 状态：时长、状态色 class、告警标签、error 摘要', async () => {\n  const downSince = new Date(Date.now() - 40 * 60000).toISOString();\n  stubApi({\n    health: {\n      sources: [\n        { source: 'mxnzp', status: 'down', alerted: 'alerted',\n          error: 'dns boom', last_success_at: null, down_since: downSince },\n        { source: 'juhe', status: 'ok', alerted: 'none',\n          error: null, last_success_at: new Date().toISOString(), down_since: null },\n      ],\n    },\n  });\n  const wrapper = mountAdmin();\n  await flushPromises();\n  const items = wrapper.findAll('.source-item');\n  expect(items).toHaveLength(2);\n  const down = items[0];\n  expect(down.find('.source-status').classes()).toContain('down');\n  expect(down.find('.source-meta').text()).toContain('已故障');\n  expect(down.find('.source-meta').text()).toContain('分钟');\n  expect(down.find('.source-alert-tag').text()).toBe('已通知');\n  expect(down.find('.source-error').text()).toContain('dns boom');\n  // 空态回归（design-voice D2）：v-else 空态必须在 health 为空时仍渲染\n});\n\nit('健康卡空态保留（v-else 不被模板改动删除）', async () => {\n  stubApi({ health: { sources: [] } });\n  const wrapper = mountAdmin();\n  await flushPromises();\n  expect(wrapper.find('.empty-tip').exists()).toBe(true);\n});\n```\n\n（挂载/flush 辅助以该文件既有写法为准——复用现有 mountAdmin/stubApi/flushPromises 模式；名字不同则对齐既有 helper。）\n\n- [ ] **Step 6: 前后端验证**\n\nRun: `uv run pytest tests/api/test_admin.py -q && npm --prefix web run build && npm --prefix web run test`\nExpected: pytest PASS；vue-tsc + vite build exit 0；vitest PASS（含新增 2 个健康卡用例）。\n\n- [ ] **Step 7: Commit**\n\n```bash\ngit add app/api/admin.py web/src/pages/Admin.vue web/src/pages/Admin.test.ts tests/api/test_admin.py\ngit commit -m \"feat(plan-11): /admin/health 返回 alerted/error/时间戳，面板展示故障时长与通知状态\""}]} -->
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
> 产品类型：自部署 homelab 服务（API/Service + 运维 runbook）。主 persona：**自部署 homelab 运维**（兼唯一 admin；被 Bark 叫醒的人；容忍度高但要求「能查、能关、能验证」）。

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
| Product Type         | 自部署 homelab 服务（API/Service + runbook）  |
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
