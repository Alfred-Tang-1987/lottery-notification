# Plan 08：忘记密码（验证码自助重置 + 管理员后台重置）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 登录页提供「忘记密码」自助重置（6 位验证码经用户 email 渠道发送）+ 管理员后台重置端点兜底（未配 email 用户）。

**Architecture:** 新表 `password_reset_codes`（SHA-256 存码）+ 独立 `PasswordResetService`（不复用 Notifier 编排器——验证码无 DND/退避/admin 告警语义；渠道实例从 `app.state.channels` 注入，解密抽公共函数）+ 两个匿名端点（forgot 统一话术防枚举；reset 加 Origin 校验）+ Login.vue 第三 tab 拆独立 `<ForgotFlow>` 多步组件。

**Tech Stack:** FastAPI / SQLModel + SQLite（pool_size=1 + WAL + busy_timeout）/ Alembic / passlib / Fernet / smtplib（EmailChannel）/ Vue3 + vitest。

**Spec:** `docs/superpowers/specs/2026-08-02-forgot-password-design.md`（含 GSTACK REVIEW REPORT 决议，本 plan 已全量落地）

## Global Constraints

- **时区纪律**：`expires_at`/`used_at` 及所有比较一律 `datetime.now(UTC).replace(tzinfo=None)`，与 `TimestampMixin.created_at`（`default_factory=datetime.utcnow`）同 naive UTC 同数值。SQLite 对 datetime 做字符串比较且存取剥离 tzinfo。
- **单事务纪律**：一个逻辑操作单事务一次 commit；禁止 split-commit。
- **事务外 send**：渠道 `send()` 绝不拿 DB 写锁调用——先 commit 码落库，再 send。
- **统一话术防枚举**：forgot 对「用户不存在 / 无 email 渠道 / SMTP 未配 / 60s 内重发 / send 失败」返回**逐字相同**的 200 响应体；reset 对「码错 / 过期 / attempts 超限 / 用户不存在」返回逐字相同的 400。删除一切 `no_channel` 差异化信号。
- **渠道白名单 = 仅 email**：不查 bark/feishu，不退回。无 enabled email 渠道或 SMTP 未配（`channels` dict 无 `'email'` 键）→ 走「无可用 email 渠道」分支（统一话术，不写码）。
- **测试铁律**（pool_size=1 死锁规避，实证根因是「HTTP 存活期内嵌套 Session」而非多次 HTTP）：
  1. HTTP 调用之间串行即可，无需拆测试
  2. 准备数据用独立 `_seed_*`（`with Session` 开→写→commit→关闭）后才做 HTTP
  3. HTTP 后验证 DB 状态再开新的 `with Session`
  4. **绝不**在 HTTP 请求存活期/依赖注入 session 存活期内嵌套开 Session；绝不嵌套 `with Session`
  5. 渠道 send 一律注入假插件，不打真实网络
  6. 多次调用需求（限流）下沉 service 单元测试
- **验证码参数**：6 位数字（`secrets.randbelow(900000) + 100000`）、15 分钟 TTL、attempts≥5 作废、同用户 60s 重发间隔、IP 每分钟 ≤3 次 forgot。
- **码只存 SHA-256 hex**，不存明文；明文 config 绝不入日志。
- **Alembic 迁移 `down_revision='t6f_user_note'`**（当前 head，非 0001）。
- **admin 鉴权依赖实际名是 `require_admin`**（`app/api/deps.py:33`，spec §3.7 的 `current_admin` 系笔误）。
- **EmailChannel 走 smtplib**（非 httpx），默认 timeout=15s——事务外调用不持锁，可接受，不改超时。
- 全程文案中文；代码注释遵循项目现有中文风格。

---

### Task 0: 抽公共 `decrypt_channel_config` 函数

**Files:**
- Create: `app/notifications/_decrypt.py`
- Modify: `app/notifications/notifier.py:260-290`（`_decrypt_config` 改调公共函数）
- Test: `tests/notifications/test_decrypt.py`（新建）

**Interfaces:**
- Consumes: `app.infrastructure.crypto.CryptoService.decrypt(blob: tuple[int, str]) -> str`；`app.models.NotificationChannel`（`config_json: str`、`key_version: int`、`user_id`、`id`、`type`）
- Produces: `decrypt_channel_config(ch_row: NotificationChannel, crypto: CryptoService) -> dict | None`——Task 2 的 `PasswordResetService` 依赖此函数解密用户 email 渠道 config。

- [ ] **Step 1: 写失败测试**

```python
# tests/notifications/test_decrypt.py
"""decrypt_channel_config 公共解密函数测试（Plan 08 / T0）。

从 Notifier._decrypt_config 抽出的公共实现：明文拒绝（INFO 级别 WARNING tag）、
密文损坏 WARNING + None、成功返回 dict。明文 config 绝不入日志。
"""

import json
import logging

from cryptography.fernet import Fernet

from app.infrastructure.crypto import CryptoService
from app.models import NotificationChannel
from app.notifications._decrypt import decrypt_channel_config

_KEY = Fernet.generate_key().decode()


def _crypto() -> CryptoService:
    return CryptoService({1: _KEY}, current_version=1)


def _ch_row(config_json: str, key_version: int = 1) -> NotificationChannel:
    return NotificationChannel(
        user_id=1, type='email', config_json=config_json, key_version=key_version,
    )


def test_decrypt_success_returns_dict():
    crypto = _crypto()
    ct = crypto.encrypt(json.dumps({'to': 'a@b.com'})).ciphertext
    row = _ch_row(json.dumps({'ct': ct}))
    assert decrypt_channel_config(row, crypto) == {'to': 'a@b.com'}


def test_decrypt_plaintext_rejected(caplog):
    row = _ch_row(json.dumps({'to': 'a@b.com'}))  # 无 'ct' 键 → 明文拒绝
    with caplog.at_level(logging.WARNING):
        assert decrypt_channel_config(row, _crypto()) is None
    assert 'notify_decrypt_skip_plaintext' in caplog.text
    assert 'a@b.com' not in caplog.text  # 明文 config 绝不入日志


def test_decrypt_broken_ciphertext_returns_none(caplog):
    row = _ch_row(json.dumps({'ct': 'not-a-valid-fernet!!!'}))
    with caplog.at_level(logging.WARNING):
        assert decrypt_channel_config(row, _crypto()) is None
    assert 'notify_decrypt_failed' in caplog.text


def test_decrypt_wrong_key_version_returns_none(caplog):
    crypto = _crypto()
    ct = crypto.encrypt(json.dumps({'to': 'a@b.com'})).ciphertext
    row = _ch_row(json.dumps({'ct': ct}), key_version=99)  # 未知版本
    with caplog.at_level(logging.WARNING):
        assert decrypt_channel_config(row, crypto) is None
    assert 'notify_decrypt_failed' in caplog.text
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/notifications/test_decrypt.py -v`
Expected: FAIL（`ModuleNotFoundError: app.notifications._decrypt`）

- [ ] **Step 3: 实现公共函数**

```python
# app/notifications/_decrypt.py
"""渠道 config 解密公共实现（Plan 08 / T0）。

从 Notifier._decrypt_config 抽出，供 Notifier 与 PasswordResetService 共用——
明文拒绝 / 解密失败 WARNING / key_version 失配处理只有一份实现。
契约：只接受 {"ct": ...} 格式；任何失败返回 None 并记 WARNING（绝不静默）；
明文 config 绝不入日志。
"""

import json
import logging

from app.infrastructure.crypto import CryptoService
from app.models import NotificationChannel

logger = logging.getLogger(__name__)


def decrypt_channel_config(ch_row: NotificationChannel, crypto: CryptoService) -> dict | None:
    """解密渠道配置。只接受 {"ct": ...} 格式，拒绝明文（spec §8.1）。

    解密失败（Fernet key 失配 / 密文损坏 / key_version 轮换错位）记 WARNING 返回 None。
    """
    raw = json.loads(ch_row.config_json)
    if 'ct' not in raw:
        logger.warning(
            'notify_decrypt_skip_plaintext user_id=%s channel_id=%s type=%s '
            '（spec §8.1 拒绝明文，疑似旧数据/手改）',
            ch_row.user_id,
            ch_row.id,
            ch_row.type,
        )
        return None
    try:
        blob = (ch_row.key_version, raw['ct'])
        plaintext = crypto.decrypt(blob)
        return json.loads(plaintext)
    except Exception:
        logger.warning(
            'notify_decrypt_failed user_id=%s channel_id=%s type=%s key_version=%s '
            '（密文损坏 / key_version 失配 / Fernet key 轮换错位，该渠道将跳过）',
            ch_row.user_id,
            ch_row.id,
            ch_row.type,
            ch_row.key_version,
            exc_info=True,
        )
        return None
```

- [ ] **Step 4: Notifier._decrypt_config 改调公共函数**

`app/notifications/notifier.py`：删除 `_decrypt_config` 方法体（260-290 行区域），改为委托；文件顶部 import 处加 `from app.notifications._decrypt import decrypt_channel_config`：

```python
    def _decrypt_config(self, ch_row: NotificationChannel) -> dict | None:
        """委托公共实现（Plan 08 / T0 抽出，PasswordResetService 共用）。"""
        return decrypt_channel_config(ch_row, self._crypto)
```

- [ ] **Step 5: 跑新测试 + Notifier 回归**

Run: `uv run pytest tests/notifications/ -v`
Expected: 新 4 条 PASS + 现有 notifier 测试全 PASS（无行为变化）

- [ ] **Step 6: Commit**

```bash
git add app/notifications/_decrypt.py app/notifications/notifier.py tests/notifications/test_decrypt.py
git commit -m "feat(plan-08/T0): 抽公共 decrypt_channel_config（Notifier 委托，供密码重置复用）"
```

---

### Task 1: `PasswordResetCode` model + Alembic 迁移

**Files:**
- Create: `app/models/password_reset.py`
- Modify: `app/models/__init__.py`（导入 + `__all__`）
- Create: `alembic/versions/p8_password_reset_codes.py`
- Test: `tests/test_password_reset_model.py`（新建）

**Interfaces:**
- Consumes: `app.models._base.TimestampMixin`；alembic 当前 head `t6f_user_note`
- Produces: `app.models.PasswordResetCode`（字段见下），Task 2/3 的 service 与 Task 4/5 的 API 测试 seed 均依赖。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_password_reset_model.py
"""PasswordResetCode model 测试（Plan 08 / T1）。"""

from datetime import UTC, datetime

from sqlmodel import Session, select

from app.models import PasswordResetCode, User


def test_defaults(db_engine):
    with Session(db_engine) as s:
        s.add(User(username='alice', password_hash='x', role='user', invite_code='C'))
        u = s.exec(select(User)).first()
        code = PasswordResetCode(
            user_id=u.id,
            code_hash='a' * 64,
            channel_type='email',
            expires_at=datetime.now(UTC).replace(tzinfo=None),
        )
        s.add(code)
        s.commit()
        s.refresh(code)
        assert code.id is not None
        assert code.attempts == 0
        assert code.used_at is None
        assert code.created_at is not None


def test_user_id_indexed_and_fk(db_engine):
    """user_id 有索引且是 users.id 外键（schema 断言）。"""
    from sqlalchemy import inspect as sa_inspect

    insp = sa_inspect(db_engine)
    cols = {c['name']: c for c in insp.get_columns('password_reset_codes')}
    assert 'user_id' in cols
    fks = insp.get_foreign_keys('password_reset_codes')
    assert any(fk['referred_table'] == 'users' for fk in fks)
    idxs = insp.get_indexes('password_reset_codes')
    assert any('user_id' in ix['column_names'] for ix in idxs)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_password_reset_model.py -v`
Expected: FAIL（`ImportError: cannot import name 'PasswordResetCode'`）

- [ ] **Step 3: 实现 model**

```python
# app/models/password_reset.py
"""密码重置验证码（Plan 08）。

同一用户同时至多一条活跃码（used_at IS NULL）：新请求事务内作废旧码再插新行。
code 只存 SHA-256 hex，不存明文。expires_at/used_at 一律 naive UTC
（datetime.now(UTC).replace(tzinfo=None)），与 TimestampMixin.created_at 同时区同数值。
"""

from datetime import datetime

from sqlmodel import Field

from app.models._base import TimestampMixin


class PasswordResetCode(TimestampMixin, table=True):
    __tablename__ = 'password_reset_codes'
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key='users.id', index=True)
    code_hash: str = Field(max_length=64)  # SHA-256(验证码) hex
    channel_type: str = Field(max_length=8)  # 实际发送渠道（审计），当前恒 'email'
    expires_at: datetime  # 创建 + 15min，naive UTC
    attempts: int = Field(default=0, sa_column_kwargs={'server_default': '0'})
    used_at: datetime | None = None  # 非空即作废（成功/被顶替/send 失败）
```

`app/models/__init__.py`：加 `from app.models.password_reset import PasswordResetCode`，`__all__` 加 `'PasswordResetCode'`（按字母序插入 PendingComparison 之后）。

- [ ] **Step 4: 写 Alembic 迁移**

```python
# alembic/versions/p8_password_reset_codes.py
"""plan-08: password_reset_codes 表（忘记密码验证码）.

Revision ID: p8_password_reset_codes
Revises: t6f_user_note
Create Date: 2026-08-02 21:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'p8_password_reset_codes'
down_revision: str | Sequence[str] | None = 't6f_user_note'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'password_reset_codes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('code_hash', sa.String(length=64), nullable=False),
        sa.Column('channel_type', sa.String(length=8), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('attempts', sa.Integer(), server_default='0', nullable=False),
        sa.Column('used_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_password_reset_codes_user_id', 'password_reset_codes', ['user_id']
    )


def downgrade() -> None:
    op.drop_index('ix_password_reset_codes_user_id', table_name='password_reset_codes')
    op.drop_table('password_reset_codes')
```

> 注：`created_at` 无 server_default——`TimestampMixin`（`app/models/_base.py`）是
> `Field(default_factory=datetime.utcnow, nullable=False)`，由 Python 侧填充，
> 迁移只建 `nullable=False` 列即可（上文已按此写）。

- [ ] **Step 5: 跑 model 测试 + 迁移测试**

Run: `uv run pytest tests/test_password_reset_model.py tests/test_migration_0001.py -v`
Expected: 全 PASS（`db_engine` fixture 走 `SQLModel.metadata.create_all`，迁移测试验证 alembic 链不破坏）

再跑迁移冒烟（**临时库，不碰 ./data 开发库**）：
```bash
tmpdir=$(mktemp -d)
DATABASE_URL="sqlite:///$tmpdir/mig.db" uv run alembic upgrade head
sqlite3 "$tmpdir/mig.db" ".tables" | grep password_reset_codes
```
Expected: 升级到 `p8_password_reset_codes` 无报错，表存在

- [ ] **Step 6: Commit**

```bash
git add app/models/password_reset.py app/models/__init__.py alembic/versions/p8_password_reset_codes.py tests/test_password_reset_model.py
git commit -m "feat(plan-08/T1): PasswordResetCode model + 迁移（down_revision=t6f_user_note）"
```

---

### Task 2: `PasswordResetService.request_reset` + RateLimiter

**Files:**
- Create: `app/services/password_reset_service.py`
- Test: `tests/services/test_password_reset_service.py`（新建）

**Interfaces:**
- Consumes: `decrypt_channel_config`（Task 0）；`PasswordResetCode`（Task 1）；`app.notifications.base.NotifierChannel / NotificationPayload / SendResult / ChannelStatus`；`app.infrastructure.crypto.CryptoService`
- Produces（Task 4 API 依赖的精确签名）:
  - `class RateLimited(Exception)` —— API 层转 429
  - `class RateLimiter(max_per_minute: int = 3)`，方法 `hit(key: str) -> bool`（超限返回 False）
  - `PasswordResetService(engine, *, email_channel, crypto, rate_limiter=None, admin_alert=None, code_ttl_minutes=15, max_attempts=5, resend_interval_seconds=60, send_retries=2)`
  - `request_reset(username: str, *, client_ip: str, session: Session) -> None` —— 统一话术语义全静默；仅 `RateLimited` 上抛。**session 由 API 层注入（用 get_session_dep 的同一 session），service 不在 HTTP 存活期内自建 Session**（测试铁律 #4 在实现侧的对应）

- [ ] **Step 1: 写失败测试（service 级，无 HTTP）**

```python
# tests/services/test_password_reset_service.py
"""PasswordResetService.request_reset 测试（Plan 08 / T2）。

纪律：所有 DB 操作用 with Session 串行（先 seed 关 → 调 service → 再开 Session 验证），
绝不嵌套。fake_send 假插件不打网络。
"""

import json
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.fernet import Fernet
from sqlmodel import Session, select

from app.infrastructure.crypto import CryptoService
from app.models import NotificationChannel, PasswordResetCode, User
from app.notifications.base import ChannelStatus, SendResult
from app.services.password_reset_service import (
    PasswordResetService,
    RateLimited,
    RateLimiter,
)

_KEY = Fernet.generate_key().decode()


class FakeEmailChannel:
    type = 'email'

    def __init__(self, fail: bool = False):
        self.calls = []
        self._fail = fail

    def send(self, payload, config):
        self.calls.append((payload, config))
        if self._fail:
            return SendResult(ChannelStatus.FAILED, 'smtp down')
        return SendResult(ChannelStatus.SENT)


def _crypto() -> CryptoService:
    return CryptoService({1: _KEY}, current_version=1)


def _seed_user(db_engine, username='alice', *, with_email=True) -> int:
    """独立 Session 建用户（+可选 email 渠道，真 Fernet 加密 config）。返回 user_id。"""
    crypto = _crypto()
    with Session(db_engine) as s:
        u = User(username=username, password_hash='x', role='user', invite_code=username)
        s.add(u)
        s.commit()
        s.refresh(u)
        if with_email:
            ct = crypto.encrypt(json.dumps({'to': f'{username}@example.com'})).ciphertext
            s.add(NotificationChannel(
                user_id=u.id, type='email',
                config_json=json.dumps({'ct': ct}), key_version=1,
            ))
            s.commit()
        return u.id


def _service(db_engine, fake, **kw) -> PasswordResetService:
    return PasswordResetService(
        db_engine, email_channel=fake, crypto=_crypto(), **kw,
    )


def _request(db_engine, svc, username='alice'):
    """模拟 API 层：注入 session 调 request_reset（Session 由调用方持有并关闭）。"""
    with Session(db_engine) as s:
        svc.request_reset(username, client_ip='10.0.0.1', session=s)


def test_request_sends_code_and_stores_hash(db_engine):
    uid = _seed_user(db_engine)
    fake = FakeEmailChannel()
    _request(db_engine, _service(db_engine, fake))
    assert len(fake.calls) == 1
    payload, config = fake.calls[0]
    assert config == {'to': 'alice@example.com'}
    import re
    m = re.search(r'验证码 (\d{6})', payload.body)
    assert m, f'body 应含 6 位验证码: {payload.body!r}'
    with Session(db_engine) as s:
        row = s.exec(select(PasswordResetCode)).first()
        assert row is not None and row.user_id == uid
        assert row.code_hash != m.group(1)  # hash 非明文
        import hashlib
        assert row.code_hash == hashlib.sha256(m.group(1).encode()).hexdigest()
        assert row.channel_type == 'email'
        assert row.used_at is None
        # expires ≈ created + 15min（naive UTC）
        delta = row.expires_at - row.created_at
        assert timedelta(minutes=14) < delta < timedelta(minutes=16)


def test_request_unknown_user_silent(db_engine):
    fake = FakeEmailChannel()
    _request(db_engine, _service(db_engine, fake), username='ghost')
    assert fake.calls == []
    with Session(db_engine) as s:
        assert s.exec(select(PasswordResetCode)).first() is None


def test_request_no_email_channel_silent(db_engine):
    _seed_user(db_engine, with_email=False)
    fake = FakeEmailChannel()
    _request(db_engine, _service(db_engine, fake))
    assert fake.calls == []
    with Session(db_engine) as s:
        assert s.exec(select(PasswordResetCode)).first() is None


def test_request_smtp_not_configured_silent(db_engine):
    """email_channel=None（SMTP 未配）→ 统一静默，不写码。"""
    _seed_user(db_engine)
    _request(db_engine, _service(db_engine, None))
    with Session(db_engine) as s:
        assert s.exec(select(PasswordResetCode)).first() is None


def test_request_resend_within_60s_skipped(db_engine):
    """同用户 60s 内已有活跃码 → 静默跳过（不发新码不顶废旧码）。

    用注入的宽松 rate_limiter 隔离 IP 限流干扰（默认 3 次/min 够 2 次调用，
    但显式注入避免与未来用例顺序耦合）。
    """
    _seed_user(db_engine)
    fake = FakeEmailChannel()
    svc = _service(db_engine, fake, rate_limiter=RateLimiter(max_per_minute=10))
    _request(db_engine, svc)
    _request(db_engine, svc)  # 60s 内第二次 → 静默跳过
    assert len(fake.calls) == 1
    with Session(db_engine) as s:
        assert len(s.exec(select(PasswordResetCode)).all()) == 1


def test_request_new_code_invalidates_old(db_engine):
    """超过重发窗（resend_interval=0）再请求：旧码作废，新码唯一活跃。"""
    _seed_user(db_engine)
    fake = FakeEmailChannel()
    svc = _service(db_engine, fake, resend_interval_seconds=0,
                   rate_limiter=RateLimiter(max_per_minute=10))
    _request(db_engine, svc)
    _request(db_engine, svc)
    assert len(fake.calls) == 2
    with Session(db_engine) as s:
        rows = s.exec(select(PasswordResetCode)).all()
        assert len(rows) == 2
        active = [r for r in rows if r.used_at is None]
        assert len(active) == 1


def test_request_send_failure_marks_code_used(db_engine):
    _seed_user(db_engine)
    fake = FakeEmailChannel(fail=True)
    alerts = []
    svc = _service(
        db_engine, fake, send_retries=0,
        admin_alert=lambda title, body: alerts.append((title, body)),
    )
    _request(db_engine, svc)
    assert len(fake.calls) == 1
    with Session(db_engine) as s:
        row = s.exec(select(PasswordResetCode)).first()
        assert row is not None and row.used_at is not None  # 码作废
    assert len(alerts) == 1  # send_retries=0 直接失败 → admin 告警


def test_rate_limiter_window():
    rl = RateLimiter(max_per_minute=3)
    assert rl.hit('1.1.1.1') is True
    assert rl.hit('1.1.1.1') is True
    assert rl.hit('1.1.1.1') is True
    assert rl.hit('1.1.1.1') is False  # 第 4 次超限
    assert rl.hit('2.2.2.2') is True   # 不同 key 互不影响


def test_request_ip_rate_limited_raises(db_engine):
    _seed_user(db_engine)
    fake = FakeEmailChannel()
    svc = _service(db_engine, fake, rate_limiter=RateLimiter(max_per_minute=2),
                   resend_interval_seconds=0)  # 关闭重发窗干扰，只验限流
    _request(db_engine, svc)
    _request(db_engine, svc)
    with pytest.raises(RateLimited):
        _request(db_engine, svc)  # 第 3 次超限（窗口内 2 次已记）
    assert len(fake.calls) == 2  # 超限次未发码
```

> 实现注意：上面用例语义依赖实现顺序——**先查限流器（抛 RateLimited）再查重发间隔**（spec §4.1 时序第 1 步即限流）。超限的调用不计入发码次数（`RateLimiter.hit` 超限返回 False 不记录该次）。

> 注：`verify_and_reset` 属 Task 3，此 task 先不实现（测试也不覆盖）——TDD 一次一个行为。

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/services/test_password_reset_service.py -v`
Expected: FAIL（`ModuleNotFoundError: app.services.password_reset_service`）

- [ ] **Step 3: 实现 service**

```python
# app/services/password_reset_service.py
"""密码重置服务（Plan 08 / T2+T3）。

纪律（CLAUDE.md / spec）：
- 统一话术语义：request_reset 对用户不存在/无 email 渠道/SMTP 未配/60s 内重发/
  send 失败全部静默返回 None，仅 RateLimited 上抛（API 层转 429）。
- 事务外 send：事务A 落码 commit 后才调渠道 send；失败开事务B 标作废（重试+告警，
  不回滚事务A——HTTP 路径不持写锁等 SMTP 网络 IO）。
- session 由调用方（API 层 get_session_dep）注入，service 不在请求存活期内自建
  Session 做事务A——事务B/告警等请求外操作才用 self._engine 新开短 Session。
- 渠道白名单 = 仅 email（autoplan 决议）：不查 bark/feishu。
"""

import hashlib
import logging
import secrets
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from app.infrastructure.crypto import CryptoService
from app.models import NotificationChannel, PasswordResetCode, User
from app.notifications._decrypt import decrypt_channel_config
from app.notifications.base import ChannelStatus, NotificationPayload, NotifierChannel

logger = logging.getLogger(__name__)


class RateLimited(Exception):
    """IP 限流超限（API 层转 429）。"""


class ResetRejected(Exception):
    """验证码错误/过期/超 attempts/用户不存在（API 层转 400 统一文案）。"""


class RateLimiter:
    """内存滑动窗口限流（单进程语义，实例注入避免测试间状态泄漏）。

    依赖 uvicorn 单 worker（Dockerfile CMD 无 --workers）；多 worker 部署须迁 Redis。
    """

    def __init__(self, max_per_minute: int = 3):
        self._max = max_per_minute
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str) -> bool:
        """记录一次访问；窗口内超限返回 False（不记录该次）。"""
        now = time.monotonic()
        with self._lock:
            hits = [t for t in self._hits.get(key, []) if now - t < 60]
            if len(hits) >= self._max:
                self._hits[key] = hits
                return False
            hits.append(now)
            self._hits[key] = hits
            return True


def _now_naive_utc() -> datetime:
    """naive UTC，与 TimestampMixin.created_at 同时区同数值（CLAUDE.md 纪律）。"""
    return datetime.now(UTC).replace(tzinfo=None)


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


class PasswordResetService:
    def __init__(
        self,
        engine: Engine,
        *,
        email_channel: NotifierChannel | None,
        crypto: CryptoService,
        rate_limiter: RateLimiter | None = None,
        admin_alert: Callable[[str, str], None] | None = None,
        code_ttl_minutes: int = 15,
        max_attempts: int = 5,
        resend_interval_seconds: int = 60,
        send_retries: int = 2,
    ):
        self._engine = engine
        self._email_channel = email_channel
        self._crypto = crypto
        self._rate_limiter = rate_limiter or RateLimiter()
        self._admin_alert = admin_alert
        self._code_ttl = timedelta(minutes=code_ttl_minutes)
        self._max_attempts = max_attempts
        self._resend_interval = timedelta(seconds=resend_interval_seconds)
        self._send_retries = send_retries

    # ---- request_reset（T2） ----

    def request_reset(self, username: str, *, client_ip: str, session: Session) -> None:
        """统一话术语义：任何软失败静默返回；仅 IP 超限抛 RateLimited。

        时序（spec §4.1）：限流 → 查用户 → 查 email 渠道 → 60s 重发窗 →
        事务A（作废旧码+插新码，单 commit，用注入 session）→ 事务外 send →
        失败事务B（短退避重试标作废 + admin 告警，self._engine 短 Session）。
        """
        if not self._rate_limiter.hit(client_ip):
            raise RateLimited(client_ip)

        user = session.exec(select(User).where(User.username == username)).first()
        if user is None:
            logger.info('password_reset_unknown_user username=%s', username)
            return
        if not user.enabled:
            logger.info('password_reset_disabled_user user_id=%s', user.id)
            return

        ch_row = session.exec(
            select(NotificationChannel).where(
                NotificationChannel.user_id == user.id,
                NotificationChannel.type == 'email',
                NotificationChannel.enabled == True,  # noqa: E712
            )
        ).first()
        if ch_row is None or self._email_channel is None:
            logger.info(
                'password_reset_no_email_channel user_id=%s smtp_configured=%s',
                user.id, self._email_channel is not None,
            )
            return

        now = _now_naive_utc()
        latest = session.exec(
            select(PasswordResetCode)
            .where(
                PasswordResetCode.user_id == user.id,
                PasswordResetCode.used_at.is_(None),
            )
            .order_by(PasswordResetCode.id.desc())
        ).first()
        if latest is not None and now - latest.created_at < self._resend_interval:
            logger.info('password_reset_resend_skipped user_id=%s', user.id)
            return

        code = f'{secrets.randbelow(900000) + 100000:06d}'
        # 事务A：作废旧码 + 插新码，单 commit（注入 session，不嵌套）。
        for old in session.exec(
            select(PasswordResetCode).where(
                PasswordResetCode.user_id == user.id,
                PasswordResetCode.used_at.is_(None),
            )
        ).all():
            old.used_at = now
            session.add(old)
        row = PasswordResetCode(
            user_id=user.id,
            code_hash=_hash_code(code),
            channel_type='email',
            expires_at=now + self._code_ttl,
        )
        session.add(row)
        session.commit()
        session.refresh(row)

        # 事务外：解密 + send（不持 DB 写锁等 SMTP）。
        config = decrypt_channel_config(ch_row, self._crypto)
        if config is None:
            self._invalidate_with_retry(row.id, reason='decrypt_failed')
            return
        payload = NotificationPayload(
            title='【兑奖了吗】密码重置验证码',
            body=f'验证码 {code}，15 分钟内有效。若非本人操作请忽略。',
            user_id=user.id,
        )
        result = self._email_channel.send(payload, config)
        if result.status != ChannelStatus.SENT:
            logger.warning(
                'password_reset_send_failed user_id=%s code_id=%s error=%s',
                user.id, row.id, result.error,
            )
            self._invalidate_with_retry(row.id, reason='send_failed')

    def _invalidate_with_retry(self, code_id: int, *, reason: str) -> None:
        """事务B：标码作废，短退避重试；仍失败 ERROR + admin 告警（autoplan C1）。

        不回滚事务A（保护"HTTP 不持写锁等网络 IO"前提）。self._engine 新开短
        Session——调用点在注入 session commit 之后，无嵌套。
        """
        last_exc: Exception | None = None
        for attempt in range(self._send_retries + 1):
            try:
                with Session(self._engine) as s:
                    row = s.get(PasswordResetCode, code_id)
                    if row is not None and row.used_at is None:
                        row.used_at = _now_naive_utc()
                        s.add(row)
                        s.commit()
                return
            except Exception as exc:  # noqa: BLE001 —— 重试须兜住一切 DB 故障
                last_exc = exc
                if attempt < self._send_retries:
                    time.sleep(1 + attempt)  # 秒级短退避（非 Notifier 指数退避）
        logger.error(
            'password_reset_invalidate_failed code_id=%s reason=%s（幽灵活码风险：'
            '该码已发用户但作废失败，TTL 内仍可被消耗 attempts）',
            code_id, reason, exc_info=last_exc,
        )
        if self._admin_alert is not None:
            try:
                self._admin_alert(
                    '【兑奖了吗】密码重置告警',
                    f'验证码发送/作废失败（code_id={code_id}, reason={reason}），请检查 SMTP 配置。',
                )
            except Exception:
                logger.error('password_reset_admin_alert_failed', exc_info=True)
```

> 注：`verify_and_reset` 属 Task 3，此 task 先不实现（测试也不覆盖）——TDD 一次一个行为。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/services/test_password_reset_service.py -v`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/password_reset_service.py tests/services/test_password_reset_service.py
git commit -m "feat(plan-08/T2): PasswordResetService.request_reset + RateLimiter（仅 email，事务外 send，作废重试+告警）"
```

---

### Task 3: `PasswordResetService.verify_and_reset`

**Files:**
- Modify: `app/services/password_reset_service.py`（追加方法）
- Test: `tests/services/test_password_reset_service.py`（追加用例）

**Interfaces:**
- Consumes: Task 1 model、Task 2 service 骨架；`app.api.security.hash_password`
- Produces: `verify_and_reset(username: str, code: str, new_password: str, *, session: Session) -> None`——成功改密+码作废（单事务单 commit）；失败 attempts+1 与码判定同事务（autoplan A4），抛 `ResetRejected`（Task 2 已定义）。Task 4 API 依赖。

- [ ] **Step 1: 追加失败测试**

```python
# tests/services/test_password_reset_service.py 追加
from app.api.security import hash_password, verify_password
from app.services.password_reset_service import ResetRejected


def _seed_code(db_engine, user_id: int, code='123456', *, expired=False,
               attempts=0, used=False) -> int:
    """独立 Session 直接写码行。返回 code_id。"""
    import hashlib as _hl
    now = datetime.now(UTC).replace(tzinfo=None)
    with Session(db_engine) as s:
        row = PasswordResetCode(
            user_id=user_id,
            code_hash=_hl.sha256(code.encode()).hexdigest(),
            channel_type='email',
            expires_at=now + timedelta(minutes=-1 if expired else 15),
            attempts=attempts,
            used_at=now if used else None,
        )
        s.add(row)
        s.commit()
        s.refresh(row)
        return row.id


def _verify(db_engine, svc, username='alice', code='123456', new_pw='newpass123'):
    with Session(db_engine) as s:
        svc.verify_and_reset(username, code, new_pw, session=s)


def _svc_no_send(db_engine) -> PasswordResetService:
    return PasswordResetService(
        db_engine, email_channel=FakeEmailChannel(), crypto=_crypto(),
    )


def test_reset_success_changes_password_consumes_code(db_engine):
    uid = _seed_user(db_engine)
    _seed_code(db_engine, uid)
    _verify(db_engine, _svc_no_send(db_engine))
    with Session(db_engine) as s:
        u = s.get(User, uid)
        assert verify_password('newpass123', u.password_hash)
        row = s.exec(select(PasswordResetCode)).first()
        assert row.used_at is not None  # 同事务作废


def test_reset_wrong_code_increments_attempts(db_engine):
    uid = _seed_user(db_engine)
    _seed_code(db_engine, uid)
    with pytest.raises(ResetRejected):
        _verify(db_engine, _svc_no_send(db_engine), code='000000')
    with Session(db_engine) as s:
        row = s.exec(select(PasswordResetCode)).first()
        assert row.attempts == 1        # attempts+1 主 session 单事务（A4）
        assert row.used_at is None
        u = s.get(User, uid)
        assert not verify_password('newpass123', u.password_hash)


def test_reset_attempts_exhausted_rejected(db_engine):
    uid = _seed_user(db_engine)
    _seed_code(db_engine, uid, attempts=5)
    with pytest.raises(ResetRejected):
        _verify(db_engine, _svc_no_send(db_engine))  # 正确码也拒
    with Session(db_engine) as s:
        u = s.get(User, uid)
        assert not verify_password('newpass123', u.password_hash)


def test_reset_expired_code_rejected(db_engine):
    uid = _seed_user(db_engine)
    _seed_code(db_engine, uid, expired=True)
    with pytest.raises(ResetRejected):
        _verify(db_engine, _svc_no_send(db_engine))


def test_reset_used_code_rejected(db_engine):
    uid = _seed_user(db_engine)
    _seed_code(db_engine, uid, used=True)
    with pytest.raises(ResetRejected):
        _verify(db_engine, _svc_no_send(db_engine))


def test_reset_unknown_user_rejected(db_engine):
    with pytest.raises(ResetRejected):
        _verify(db_engine, _svc_no_send(db_engine), username='ghost')


def test_reset_old_code_invalidated_after_new(db_engine):
    """旧码被新码顶替（used_at 已置）后不可用。"""
    uid = _seed_user(db_engine)
    _seed_code(db_engine, uid, code='111111', used=True)   # 旧码已作废
    _seed_code(db_engine, uid, code='222222')              # 新码活跃
    with pytest.raises(ResetRejected):
        _verify(db_engine, _svc_no_send(db_engine), code='111111')
    _verify(db_engine, _svc_no_send(db_engine), code='222222')  # 新码可用
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/services/test_password_reset_service.py -k reset_ -v`
Expected: FAIL（`AttributeError: 'PasswordResetService' object has no attribute 'verify_and_reset'`）

- [ ] **Step 3: 实现 verify_and_reset（追加到 service）**

```python
    # ---- verify_and_reset（T3） ----

    def verify_and_reset(
        self, username: str, code: str, new_password: str, *, session: Session
    ) -> None:
        """校验码并改密。

        成功：password_hash 更新 + 码 used_at，单事务单 commit（注入 session）。
        失败：attempts+1 与码判定同事务单 commit（autoplan A4，对齐
        InviteService.consume 主 session 计数模式），抛 ResetRejected——
        API 层转 400 统一文案「验证码错误或已过期」。
        """
        from app.api.security import hash_password  # 延迟 import 避免循环

        user = session.exec(select(User).where(User.username == username)).first()
        if user is None:
            raise ResetRejected(username)

        row = session.exec(
            select(PasswordResetCode)
            .where(
                PasswordResetCode.user_id == user.id,
                PasswordResetCode.used_at.is_(None),
            )
            .order_by(PasswordResetCode.id.desc())
        ).first()
        now = _now_naive_utc()
        if row is None or row.expires_at <= now or row.attempts >= self._max_attempts:
            raise ResetRejected(username)

        if row.code_hash != _hash_code(code):
            row.attempts += 1
            session.add(row)
            session.commit()  # 计数必须落库（防爆破），与判定同事务
            raise ResetRejected(username)

        user.password_hash = hash_password(new_password)
        row.used_at = now
        session.add(user)
        session.add(row)
        session.commit()  # 改密 + 作废单事务单 commit
        logger.info('password_reset_success user_id=%s', user.id)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/services/test_password_reset_service.py -v`
Expected: 全 PASS（T2 + T3 全部）

- [ ] **Step 5: Commit**

```bash
git add app/services/password_reset_service.py tests/services/test_password_reset_service.py
git commit -m "feat(plan-08/T3): verify_and_reset（改密+作废单事务，attempts 主 session 计数）"
```

---

### Task 4: forgot/reset API 端点 + app.state.channels 接线

**Files:**
- Modify: `app/api/auth.py`（追加两个端点）
- Modify: `app/main.py`（`_build_scheduler_and_deps` 的 deps dict 加 `'channels'` 和 `'crypto'`；lifespan 存 `app.state.channels` / `app.state.crypto`）
- Test: `tests/api/test_password_reset.py`（新建）

**Interfaces:**
- Consumes: Task 2/3 的 `PasswordResetService / RateLimited / ResetRejected / RateLimiter`；`app.api.deps.get_session_dep`；`app.config.get_cors_origins`
- Produces:
  - `POST /auth/forgot-password` body `{'username': str}` → 恒 200 `{'ok': True, 'message': '若账号存在，验证码已发送至你的邮箱'}`；429 超限
  - `POST /auth/reset-password` body `{'username', 'code': str(6 位数字), 'new_password': str(8..128)}` → 200 `{'ok': True}` / 400 `{'detail': '验证码错误或已过期'}`；跨站 Origin → 403
  - `app.state.channels: dict[str, NotifierChannel]`、`app.state.crypto: CryptoService`（Task 5 admin 端点也读 app.state）

- [ ] **Step 1: main.py 接线（先改，测试依赖 app.state）**

`app/main.py` `_build_scheduler_and_deps`：
- deps dict（约 158-166 行）加两项：`'channels': channels, 'crypto': crypto`
- lifespan（约 43-45 行 `app.state.notifier = deps['notifier']` 之后）加：
  ```python
  app.state.channels = deps['channels']
  app.state.crypto = deps['crypto']
  ```
- lifespan 的 `else` 分支（无 scheduler 时）对应加 `app.state.channels = None` 兼容？——查 lifespan 结构：`deps` 仅在 scheduler enabled 分支构造。**设计决策**：`channels`/`crypto` 的构造不依赖 scheduler，应把 `app.state.channels`/`app.state.crypto` 的赋值移到 `if` 外。最小改法：在 lifespan 开头（`validate_startup` 后）无条件构造 crypto 并从 deps 取 channels：
  ```python
  # app.state 兜底：scheduler 关闭时（测试/开发）channels 为 {}，crypto 仍可用。
  app.state.crypto = CryptoService(settings.crypto_keys, settings.current_key_version)
  app.state.channels = {}
  ```
  随后 if 分支内 `app.state.channels = deps['channels']` 覆盖。crypto 的重复构造代价可忽略（Fernet 对象创建），且保证 SCHEDULER_ENABLED=false 的测试环境端点不 500。

> 核对 lifespan 实际结构后按上述落地；关键是**端点代码对 `app.state.channels` 缺失/空 dict 都要安全**（`getattr(request.app.state, 'channels', None) or {}`）。

- [ ] **Step 2: 写失败测试**

```python
# tests/api/test_password_reset.py
"""忘记密码 API 测试（Plan 08 / T4）。

铁律：HTTP 间串行；seed 用独立 Session（关闭后才 HTTP）；HTTP 后验证开新 Session；
绝不嵌套 Session；渠道 send 注入假插件（monkeypatch app.state.channels）。
"""

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.api.deps import get_session_dep
from app.api.security import verify_password
from app.config import reset_settings_cache
from app.infrastructure.crypto import CryptoService
from app.main import app
from app.models import NotificationChannel, PasswordResetCode, User
from app.notifications.base import ChannelStatus, SendResult

_KEY = Fernet.generate_key().decode()
_UNIFORM_MSG = '若账号存在，验证码已发送至你的邮箱'


class FakeEmailChannel:
    type = 'email'

    def __init__(self, fail=False):
        self.calls = []
        self._fail = fail

    def send(self, payload, config):
        self.calls.append((payload, config))
        return SendResult(ChannelStatus.FAILED if self._fail else ChannelStatus.SENT,
                          'smtp down' if self._fail else None)


@pytest.fixture(autouse=True)
def _mock_env(monkeypatch):
    reset_settings_cache()
    monkeypatch.setenv('JWT_SECRET', 't' * 32)
    monkeypatch.setenv('CRYPTO_KEY_V1', _KEY)
    monkeypatch.setenv('SCHEDULER_ENABLED', 'false')


@pytest.fixture
def fake_channel():
    fake = FakeEmailChannel()
    app.state.channels = {'email': fake}
    app.state.crypto = CryptoService({1: _KEY}, current_version=1)
    yield fake
    app.state.channels = {}


def _client(db_engine):
    def _override():
        with Session(db_engine) as s:
            yield s
    app.dependency_overrides[get_session_dep] = _override
    return TestClient(app)


def _seed_user(db_engine, username='alice', password='oldpass123', *, with_email=True):
    from app.api.security import hash_password
    crypto = CryptoService({1: _KEY}, current_version=1)
    with Session(db_engine) as s:
        u = User(username=username, password_hash=hash_password(password),
                 role='user', invite_code=username)
        s.add(u)
        s.commit()
        s.refresh(u)
        if with_email:
            ct = crypto.encrypt(json.dumps({'to': f'{username}@example.com'})).ciphertext
            s.add(NotificationChannel(user_id=u.id, type='email',
                                      config_json=json.dumps({'ct': ct}), key_version=1))
            s.commit()
        return u.id


def _seed_code(db_engine, user_id, code='123456', *, expired=False, attempts=0):
    now = datetime.now(UTC).replace(tzinfo=None)
    with Session(db_engine) as s:
        row = PasswordResetCode(
            user_id=user_id, code_hash=hashlib.sha256(code.encode()).hexdigest(),
            channel_type='email',
            expires_at=now + timedelta(minutes=-1 if expired else 15),
            attempts=attempts,
        )
        s.add(row)
        s.commit()


def test_forgot_sends_code(db_engine, fake_channel):
    _seed_user(db_engine)
    client = _client(db_engine)
    try:
        r = client.post('/auth/forgot-password', json={'username': 'alice'})
        assert r.status_code == 200
        assert r.json() == {'ok': True, 'message': _UNIFORM_MSG}
        assert len(fake_channel.calls) == 1
        body = fake_channel.calls[0][0].body
        assert re.search(r'验证码 \d{6}', body)
        with Session(db_engine) as s:
            assert s.exec(select(PasswordResetCode)).first() is not None
    finally:
        app.dependency_overrides.clear()


def test_forgot_unknown_user_identical_response(db_engine, fake_channel):
    client = _client(db_engine)
    try:
        r = client.post('/auth/forgot-password', json={'username': 'ghost'})
        assert r.status_code == 200
        assert r.json() == {'ok': True, 'message': _UNIFORM_MSG}  # 逐字同成功响应
        assert fake_channel.calls == []
    finally:
        app.dependency_overrides.clear()


def test_forgot_no_email_channel_identical_response(db_engine, fake_channel):
    _seed_user(db_engine, with_email=False)
    client = _client(db_engine)
    try:
        r = client.post('/auth/forgot-password', json={'username': 'alice'})
        assert r.status_code == 200
        assert r.json() == {'ok': True, 'message': _UNIFORM_MSG}
        assert fake_channel.calls == []
    finally:
        app.dependency_overrides.clear()


def test_forgot_smtp_not_configured_identical_response(db_engine):
    _seed_user(db_engine)
    app.state.channels = {}  # SMTP 未配 → 无 email 键
    app.state.crypto = CryptoService({1: _KEY}, current_version=1)
    client = _client(db_engine)
    try:
        r = client.post('/auth/forgot-password', json={'username': 'alice'})
        assert r.status_code == 200
        assert r.json() == {'ok': True, 'message': _UNIFORM_MSG}
        with Session(db_engine) as s:
            assert s.exec(select(PasswordResetCode)).first() is None
    finally:
        app.dependency_overrides.clear()


def test_forgot_send_failure_still_uniform_200(db_engine):
    fake = FakeEmailChannel(fail=True)
    app.state.channels = {'email': fake}
    app.state.crypto = CryptoService({1: _KEY}, current_version=1)
    _seed_user(db_engine)
    client = _client(db_engine)
    try:
        r = client.post('/auth/forgot-password', json={'username': 'alice'})
        assert r.status_code == 200
        assert r.json() == {'ok': True, 'message': _UNIFORM_MSG}
        with Session(db_engine) as s:
            row = s.exec(select(PasswordResetCode)).first()
            assert row.used_at is not None  # 码已作废
    finally:
        app.dependency_overrides.clear()


def test_reset_success_then_login_with_new_password(db_engine, fake_channel):
    uid = _seed_user(db_engine)
    _seed_code(db_engine, uid)
    client = _client(db_engine)
    try:
        r = client.post('/auth/reset-password', json={
            'username': 'alice', 'code': '123456', 'new_password': 'newpass123'})
        assert r.status_code == 200 and r.json() == {'ok': True}
        # HTTP 后开新 Session 验证（铁律 #3）
        with Session(db_engine) as s:
            u = s.get(User, uid)
            assert verify_password('newpass123', u.password_hash)
            row = s.exec(select(PasswordResetCode)).first()
            assert row.used_at is not None
        # 串行第二次 HTTP：新密码可登录（铁律 #1：HTTP 间串行即可）
        r2 = client.post('/auth/login', json={'username': 'alice', 'password': 'newpass123'})
        assert r2.status_code == 200
    finally:
        app.dependency_overrides.clear()


def test_reset_wrong_code_400_and_attempts_increment(db_engine, fake_channel):
    uid = _seed_user(db_engine)
    _seed_code(db_engine, uid)
    client = _client(db_engine)
    try:
        r = client.post('/auth/reset-password', json={
            'username': 'alice', 'code': '000000', 'new_password': 'newpass123'})
        assert r.status_code == 400
        assert r.json() == {'detail': '验证码错误或已过期'}
        with Session(db_engine) as s:
            row = s.exec(select(PasswordResetCode)).first()
            assert row.attempts == 1
            assert verify_password('oldpass123', s.get(User, uid).password_hash)
    finally:
        app.dependency_overrides.clear()


def test_reset_unknown_user_identical_400(db_engine, fake_channel):
    client = _client(db_engine)
    try:
        r = client.post('/auth/reset-password', json={
            'username': 'ghost', 'code': '123456', 'new_password': 'newpass123'})
        assert r.status_code == 400
        assert r.json() == {'detail': '验证码错误或已过期'}  # 逐字同码错误响应
    finally:
        app.dependency_overrides.clear()


def test_reset_expired_code_400(db_engine, fake_channel):
    uid = _seed_user(db_engine)
    _seed_code(db_engine, uid, expired=True)
    client = _client(db_engine)
    try:
        r = client.post('/auth/reset-password', json={
            'username': 'alice', 'code': '123456', 'new_password': 'newpass123'})
        assert r.status_code == 400
    finally:
        app.dependency_overrides.clear()


def test_reset_attempts_exhausted_400(db_engine, fake_channel):
    uid = _seed_user(db_engine)
    _seed_code(db_engine, uid, attempts=5)
    client = _client(db_engine)
    try:
        r = client.post('/auth/reset-password', json={
            'username': 'alice', 'code': '123456', 'new_password': 'newpass123'})
        assert r.status_code == 400
    finally:
        app.dependency_overrides.clear()


def test_reset_cross_site_origin_403(db_engine, fake_channel):
    uid = _seed_user(db_engine)
    _seed_code(db_engine, uid)
    client = _client(db_engine)
    try:
        r = client.post(
            '/auth/reset-password',
            json={'username': 'alice', 'code': '123456', 'new_password': 'newpass123'},
            headers={'Origin': 'https://evil.example.com'},
        )
        assert r.status_code == 403
        with Session(db_engine) as s:
            assert verify_password('oldpass123', s.get(User, uid).password_hash)
    finally:
        app.dependency_overrides.clear()


def test_reset_short_password_422(db_engine, fake_channel):
    client = _client(db_engine)
    try:
        r = client.post('/auth/reset-password', json={
            'username': 'alice', 'code': '123456', 'new_password': 'short'})
        assert r.status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_reset_non_digit_code_422(db_engine, fake_channel):
    client = _client(db_engine)
    try:
        r = client.post('/auth/reset-password', json={
            'username': 'alice', 'code': 'abcdef', 'new_password': 'newpass123'})
        assert r.status_code == 422
    finally:
        app.dependency_overrides.clear()
```

- [ ] **Step 3: 跑测试确认失败**

Run: `uv run pytest tests/api/test_password_reset.py -v`
Expected: FAIL（404 —— 端点不存在）

- [ ] **Step 4: 实现端点**

`app/api/auth.py` 追加（import 归位：顶部 `from fastapi import ... Header, Request ...` 合并到现有 fastapi import；`from app.infrastructure.crypto import CryptoService`；其余如 BaseModel/Field/HTTPException/status 文件已有）：

```python
# ---- Plan 08：忘记密码 ----

from app.infrastructure.crypto import CryptoService  # 并入顶部 import 区
from app.services.password_reset_service import (
    PasswordResetService,
    RateLimited,
    RateLimiter,
    ResetRejected,
)

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
```

> 说明：crypto 为 None 的防御分支已并入 `_reset_service`（测试 fixture 总是注入、生产 lifespan 总是赋值，该分支纯防御）。

- [ ] **Step 5: 跑测试确认通过 + 回归**

Run: `uv run pytest tests/api/test_password_reset.py -v`
Expected: 全 PASS

Run: `uv run pytest tests/api/ -v`
Expected: auth/admin/channels/csrf_cors 等无回归

- [ ] **Step 6: Commit**

```bash
git add app/api/auth.py app/main.py tests/api/test_password_reset.py
git commit -m "feat(plan-08/T4): forgot/reset 端点（统一话术防枚举 + Origin 校验 + app.state 渠道注入）"
```

---

### Task 5: 管理员后台重置端点

**Files:**
- Modify: `app/api/admin.py`（追加端点）
- Test: `tests/api/test_admin_reset.py`（新建）

**Interfaces:**
- Consumes: `require_admin`（`app/api/deps.py:33`，router 级 dependencies 已挂）；`verify_csrf`；`write_audit`（`app/services/audit_service.py`，`commit=False` + 调用方统一 commit）；`app.api.security.hash_password`
- Produces: `POST /admin/users/{user_id}/reset-password` body `{'new_password': str(8..128)}` → 200 `{'id': int, 'username': str}`；404 用户不存在；非 admin 403（router 级已有）；无 CSRF 403

- [ ] **Step 1: 写失败测试**

```python
# tests/api/test_admin_reset.py
"""管理员后台重置密码测试（Plan 08 / T5，spec §3.7）。

未配 email 渠道用户的兜底路径。对齐 test_admin.py 已验证 pattern：
seed 独立 Session → admin client（cookie + CSRF header）→ HTTP → 新 Session 验证。
"""

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.api.deps import get_session_dep
from app.api.security import (
    COOKIE_NAME, CSRF_HEADER, create_session_token, generate_csrf_token,
    hash_password, verify_password,
)
from app.config import reset_settings_cache
from app.main import app
from app.models import AdminAuditLog, User


def _set_env(monkeypatch):
    monkeypatch.setenv('JWT_SECRET', 'a' * 32)
    monkeypatch.setenv('CRYPTO_KEY_V1', Fernet.generate_key().decode())
    reset_settings_cache()


def _admin_client(db_engine, monkeypatch):
    _set_env(monkeypatch)
    with Session(db_engine) as s:
        s.add(User(username='admin', password_hash='x', role='admin', invite_code='A'))
        s.commit()
        uid = s.exec(select(User).where(User.username == 'admin')).first().id
    app.dependency_overrides[get_session_dep] = lambda: (yield Session(db_engine))
    client = TestClient(app)
    client.cookies.set(COOKIE_NAME, create_session_token(user_id=uid, role='admin'))
    csrf = generate_csrf_token()
    client.cookies.set('csrf_token', csrf)
    client.headers[CSRF_HEADER] = csrf
    return client


def _seed_user(db_engine, username='bob'):
    with Session(db_engine) as s:
        s.add(User(username=username, password_hash=hash_password('oldpass123'),
                   role='user', invite_code=username))
        s.commit()
        return s.exec(select(User).where(User.username == username)).first().id


def test_admin_reset_password_success(db_engine, monkeypatch):
    uid = _seed_user(db_engine)
    client = _admin_client(db_engine, monkeypatch)
    try:
        r = client.post(f'/admin/users/{uid}/reset-password',
                        json={'new_password': 'newpass456'})
        assert r.status_code == 200
        assert r.json()['username'] == 'bob'
        with Session(db_engine) as s:
            assert verify_password('newpass456', s.get(User, uid).password_hash)
            log = s.exec(
                select(AdminAuditLog).where(AdminAuditLog.action == 'reset_password')
            ).first()
            assert log is not None
            assert log.target_id == str(uid)
            assert 'newpass456' not in (log.new_values or '')  # 密码不入审计
    finally:
        app.dependency_overrides.clear()


def test_admin_reset_password_user_not_found(db_engine, monkeypatch):
    client = _admin_client(db_engine, monkeypatch)
    try:
        r = client.post('/admin/users/9999/reset-password',
                        json={'new_password': 'newpass456'})
        assert r.status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_admin_reset_password_without_csrf_403(db_engine, monkeypatch):
    uid = _seed_user(db_engine)
    _set_env(monkeypatch)
    with Session(db_engine) as s:
        s.add(User(username='admin', password_hash='x', role='admin', invite_code='A'))
        s.commit()
        aid = s.exec(select(User).where(User.username == 'admin')).first().id
    app.dependency_overrides[get_session_dep] = lambda: (yield Session(db_engine))
    client = TestClient(app)
    client.cookies.set(COOKIE_NAME, create_session_token(user_id=aid, role='admin'))
    try:
        r = client.post(f'/admin/users/{uid}/reset-password',
                        json={'new_password': 'newpass456'})
        assert r.status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_admin_reset_password_non_admin_403(db_engine, monkeypatch):
    uid = _seed_user(db_engine)
    _set_env(monkeypatch)
    app.dependency_overrides[get_session_dep] = lambda: (yield Session(db_engine))
    client = TestClient(app)
    client.cookies.set(COOKIE_NAME, create_session_token(user_id=uid, role='user'))
    csrf = generate_csrf_token()
    client.cookies.set('csrf_token', csrf)
    client.headers[CSRF_HEADER] = csrf
    try:
        r = client.post(f'/admin/users/{uid}/reset-password',
                        json={'new_password': 'newpass456'})
        assert r.status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_admin_reset_password_short_422(db_engine, monkeypatch):
    uid = _seed_user(db_engine)
    client = _admin_client(db_engine, monkeypatch)
    try:
        r = client.post(f'/admin/users/{uid}/reset-password',
                        json={'new_password': 'short'})
        assert r.status_code == 422
    finally:
        app.dependency_overrides.clear()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/api/test_admin_reset.py -v`
Expected: FAIL（404/405 —— 端点不存在；注意 not_found 用例会假 PASS，以 success 用例为准）

- [ ] **Step 3: 实现端点**

`app/api/admin.py` 追加（文件已有 `write_audit / verify_csrf / User / hash_password?`——`hash_password` 需新 import `from app.api.security import hash_password`；`BaseModel/Field` 需 `from pydantic import BaseModel, Field`）：

```python
class AdminResetPasswordIn(BaseModel):
    new_password: str = Field(min_length=8, max_length=128)


@router.post('/users/{user_id}/reset-password')
def admin_reset_password(
    user_id: int,
    body: AdminResetPasswordIn,
    admin: User = Depends(current_user),
    session: Session = Depends(get_session_dep),
    _csrf_ok: None = Depends(verify_csrf),
):
    """管理员后台重置用户密码（Plan 08 / T5，spec §3.7）。

    未配 email 渠道用户无法自助重置时的兜底路径（邀请制场景，admin 线下告知新密码）。
    改密 + AdminAuditLog 单事务原子 commit（对齐 force_verify pattern）；
    审计 new_values 不含密码明文（write_audit 脱敏只对 dict key，此处干脆不传）。
    """
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(404, '用户不存在')
    user.password_hash = hash_password(body.new_password)
    session.add(user)
    write_audit(
        session,
        admin_id=admin.id,
        action='reset_password',
        target_type='user',
        target_id=str(user_id),
        new_values={'username': user.username, 'by': 'admin_reset'},
        commit=False,
    )
    session.commit()
    return {'id': user.id, 'username': user.username}
```

- [ ] **Step 4: 跑测试确认通过 + 回归**

Run: `uv run pytest tests/api/test_admin_reset.py tests/api/test_admin.py -v`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add app/api/admin.py tests/api/test_admin_reset.py
git commit -m "feat(plan-08/T5): 管理员后台重置密码端点 + 审计日志"
```

---

### Task 6: Login.vue forgot tab + `<ForgotFlow>` 组件

**Files:**
- Create: `web/src/components/ForgotFlow.vue`
- Modify: `web/src/pages/Login.vue`（tab 扩 `'forgot'` + 条件渲染 ForgotFlow）
- Test: `web/src/components/ForgotFlow.test.ts`（新建）

**Interfaces:**
- Consumes: `web/src/api/client.ts` 的 `apiPost<T>(path, body)`；Task 4 端点契约（`/auth/forgot-password` → `{ok, message}`；`/auth/reset-password` → `{ok}` / 400 `{detail}`）
- Produces: `<ForgotFlow @done="..." />`——重置成功 emit `done`，Login.vue 切回 login tab 并显示「密码已重置，请登录」。

- [ ] **Step 1: 写失败测试**

```ts
// web/src/components/ForgotFlow.test.ts
/** ForgotFlow 组件测试（Plan 08 / T6）：两步状态机 + 倒计时 + 本地校验。 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createApp, nextTick } from "vue";
import ForgotFlow from "./ForgotFlow.vue";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function stubApi(overrides: Record<string, unknown> = {}) {
  return vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const u = String(url);
    if (u === "/auth/csrf") return jsonResponse(200, { csrf_token: "tok" });
    if (method === "POST" && u === "/auth/forgot-password")
      return jsonResponse(200, overrides.forgot ?? { ok: true, message: "若账号存在，验证码已发送至你的邮箱" });
    if (method === "POST" && u === "/auth/reset-password") {
      if (overrides.resetFail)
        return jsonResponse(400, { detail: "验证码错误或已过期" });
      return jsonResponse(200, { ok: true });
    }
    throw new Error(`Unexpected fetch: ${method} ${url}`);
  });
}

describe("ForgotFlow.vue", () => {
  let host: HTMLDivElement;
  let app: ReturnType<typeof createApp> | null = null;
  let fetchMock: ReturnType<typeof vi.fn>;
  let emitted: string[] = [];

  beforeEach(() => {
    host = document.createElement("div");
    document.body.appendChild(host);
    emitted = [];
  });
  afterEach(() => {
    app?.unmount();
    host.remove();
    app = null;
    vi.restoreAllMocks();
  });

  async function mount(overrides: Record<string, unknown> = {}) {
    fetchMock = stubApi(overrides);
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    app = createApp(ForgotFlow, { onDone: () => emitted.push("done") });
    app.mount(host);
    await nextTick();
    return host;
  }

  function setInput(el: Element | null, value: string) {
    const input = el as HTMLInputElement;
    input.value = value;
    input.dispatchEvent(new Event("input"));
  }

  it("步骤 1 渲染用户名输入与发送按钮", async () => {
    await mount();
    expect(host.querySelector("[data-test='forgot-username']")).toBeTruthy();
    expect(host.querySelector("[data-test='forgot-send']")).toBeTruthy();
    expect(host.querySelector("[data-test='forgot-step1']")).toBeTruthy();
  });

  it("发送验证码后进入步骤 2 并显示统一话术", async () => {
    await mount();
    setInput(host.querySelector("[data-test='forgot-username']"), "alice");
    (host.querySelector("[data-test='forgot-send']") as HTMLButtonElement).click();
    await new Promise((r) => setTimeout(r, 0));
    await nextTick();
    expect(host.querySelector("[data-test='forgot-step2']")).toBeTruthy();
    expect(host.textContent).toContain("若账号存在");
  });

  it("发送后按钮进入 60s 倒计时禁用", async () => {
    vi.useFakeTimers();
    try {
      await mount();
      setInput(host.querySelector("[data-test='forgot-username']"), "alice");
      (host.querySelector("[data-test='forgot-send']") as HTMLButtonElement).click();
      await vi.advanceTimersByTimeAsync(0);
      await nextTick();
      const btn = host.querySelector("[data-test='forgot-send']") as HTMLButtonElement;
      expect(btn.disabled).toBe(true);
      expect(btn.textContent).toMatch(/60/);
      await vi.advanceTimersByTimeAsync(61_000);
      await nextTick();
      expect(btn.disabled).toBe(false);
    } finally {
      vi.useRealTimers();
    }
  });

  it("两次密码不一致 → 本地报错不发 reset 请求", async () => {
    await mount();
    setInput(host.querySelector("[data-test='forgot-username']"), "alice");
    (host.querySelector("[data-test='forgot-send']") as HTMLButtonElement).click();
    await new Promise((r) => setTimeout(r, 0));
    await nextTick();
    const resetCallsBefore = fetchMock.mock.calls.filter(
      (c) => String(c[0]) === "/auth/reset-password",
    ).length;
    setInput(host.querySelector("[data-test='forgot-code']"), "123456");
    setInput(host.querySelector("[data-test='forgot-newpass']"), "newpass123");
    setInput(host.querySelector("[data-test='forgot-confirm']"), "different1");
    (host.querySelector("[data-test='forgot-submit']") as HTMLButtonElement).click();
    await nextTick();
    expect(host.textContent).toContain("两次输入的密码不一致");
    const resetCallsAfter = fetchMock.mock.calls.filter(
      (c) => String(c[0]) === "/auth/reset-password",
    ).length;
    expect(resetCallsAfter).toBe(resetCallsBefore);
  });

  it("reset 成功 → emit done", async () => {
    await mount();
    setInput(host.querySelector("[data-test='forgot-username']"), "alice");
    (host.querySelector("[data-test='forgot-send']") as HTMLButtonElement).click();
    await new Promise((r) => setTimeout(r, 0));
    await nextTick();
    setInput(host.querySelector("[data-test='forgot-code']"), "123456");
    setInput(host.querySelector("[data-test='forgot-newpass']"), "newpass123");
    setInput(host.querySelector("[data-test='forgot-confirm']"), "newpass123");
    (host.querySelector("[data-test='forgot-submit']") as HTMLButtonElement).click();
    await new Promise((r) => setTimeout(r, 0));
    await nextTick();
    expect(emitted).toEqual(["done"]);
  });

  it("reset 失败 → 显示 400 文案", async () => {
    await mount({ resetFail: true });
    setInput(host.querySelector("[data-test='forgot-username']"), "alice");
    (host.querySelector("[data-test='forgot-send']") as HTMLButtonElement).click();
    await new Promise((r) => setTimeout(r, 0));
    await nextTick();
    setInput(host.querySelector("[data-test='forgot-code']"), "999999");
    setInput(host.querySelector("[data-test='forgot-newpass']"), "newpass123");
    setInput(host.querySelector("[data-test='forgot-confirm']"), "newpass123");
    (host.querySelector("[data-test='forgot-submit']") as HTMLButtonElement).click();
    await new Promise((r) => setTimeout(r, 0));
    await nextTick();
    expect(host.textContent).toContain("验证码错误或已过期");
    expect(emitted).toEqual([]);
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd web && npx vitest run src/components/ForgotFlow.test.ts`
Expected: FAIL（组件不存在）

- [ ] **Step 3: 实现 ForgotFlow.vue**

```vue
<!-- web/src/components/ForgotFlow.vue -->
<script setup lang="ts">
import { onUnmounted, ref } from 'vue';
import { apiPost } from '../api/client';

const emit = defineEmits<{ done: [] }>();

const step = ref<1 | 2>(1);
const username = ref('');
const code = ref('');
const newPassword = ref('');
const confirmPassword = ref('');
const info = ref('');
const err = ref('');
const loading = ref(false);
const countdown = ref(0);
let timer: ReturnType<typeof setInterval> | null = null;

function startCountdown() {
  countdown.value = 60;
  timer = setInterval(() => {
    countdown.value -= 1;
    if (countdown.value <= 0 && timer) {
      clearInterval(timer);
      timer = null;
    }
  }, 1000);
}

onUnmounted(() => {
  if (timer) clearInterval(timer);
});

async function sendCode() {
  err.value = '';
  info.value = '';
  loading.value = true;
  try {
    const r = await apiPost<{ ok: boolean; message: string }>('/auth/forgot-password', {
      username: username.value,
    });
    info.value = r.message;
    step.value = 2;
    startCountdown();
  } catch (e) {
    err.value = e instanceof Error ? e.message : '请求失败';
  } finally {
    loading.value = false;
  }
}

async function submitReset() {
  err.value = '';
  if (!/^\d{6}$/.test(code.value)) {
    err.value = '验证码为 6 位数字';
    return;
  }
  if (newPassword.value.length < 8) {
    err.value = '新密码至少 8 位';
    return;
  }
  if (newPassword.value !== confirmPassword.value) {
    err.value = '两次输入的密码不一致';
    return;
  }
  loading.value = true;
  try {
    await apiPost('/auth/reset-password', {
      username: username.value,
      code: code.value,
      new_password: newPassword.value,
    });
    emit('done');
  } catch (e) {
    err.value = e instanceof Error ? e.message : '请求失败';
  } finally {
    loading.value = false;
  }
}
</script>

<template>
  <div class="forgot-flow">
    <ol class="steps" aria-label="重置进度">
      <li :aria-current="step === 1 ? 'step' : undefined" :class="{ active: step === 1 }">1 发送验证码</li>
      <li :aria-current="step === 2 ? 'step' : undefined" :class="{ active: step === 2 }">2 设置新密码</li>
    </ol>

    <form v-if="step === 1" data-test="forgot-step1" @submit.prevent="sendCode">
      <label class="field">
        <span class="field-label">用户名</span>
        <input
          v-model="username"
          data-test="forgot-username"
          type="text"
          placeholder="用户名"
          required
          autocomplete="username"
        />
      </label>
      <p v-if="err" class="error" role="alert">{{ err }}</p>
      <button
        type="submit"
        class="submit"
        data-test="forgot-send"
        :disabled="loading || countdown > 0"
      >
        {{ countdown > 0 ? `重新发送（${countdown}s）` : (loading ? '请稍候…' : '发送验证码') }}
      </button>
      <p class="hint">验证码将发送至你配置的邮箱；未配置邮箱请联系管理员重置</p>
    </form>

    <form v-else data-test="forgot-step2" @submit.prevent="submitReset">
      <p v-if="info" class="info" role="status">{{ info }}</p>
      <label class="field">
        <span class="field-label">验证码</span>
        <input
          v-model="code"
          data-test="forgot-code"
          type="text"
          placeholder="6 位验证码"
          required
          minlength="6"
          maxlength="6"
          pattern="\d{6}"
          autocomplete="one-time-code"
          inputmode="numeric"
        />
      </label>
      <label class="field">
        <span class="field-label">新密码</span>
        <input
          v-model="newPassword"
          data-test="forgot-newpass"
          type="password"
          placeholder="新密码（≥8位）"
          required
          minlength="8"
          autocomplete="new-password"
        />
      </label>
      <label class="field">
        <span class="field-label">确认新密码</span>
        <input
          v-model="confirmPassword"
          data-test="forgot-confirm"
          type="password"
          placeholder="再次输入新密码"
          required
          minlength="8"
          autocomplete="new-password"
        />
      </label>
      <p v-if="err" class="error" role="alert">{{ err }}</p>
      <button type="submit" class="submit" data-test="forgot-submit" :disabled="loading">
        {{ loading ? '请稍候…' : '重置密码' }}
      </button>
      <button
        type="button"
        class="resend"
        data-test="forgot-resend"
        :disabled="countdown > 0"
        @click="sendCode"
      >
        {{ countdown > 0 ? `重新发送（${countdown}s）` : '重新发送验证码' }}
      </button>
    </form>
  </div>
</template>

<style scoped>
.steps {
  display: flex;
  gap: 12px;
  list-style: none;
  padding: 0;
  margin: 0 0 20px;
  font-size: var(--text-sm);
  color: var(--muted);
}
.steps li.active {
  color: var(--fg);
  font-weight: 600;
}
.field { display: block; margin-bottom: 16px; }
.field-label { display: block; font-size: var(--text-sm); color: var(--muted); margin-bottom: 6px; }
input {
  width: 100%;
  padding: 12px 14px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface);
  color: var(--fg);
  font-size: var(--text-md);
  min-height: 44px;
}
input:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.error {
  color: var(--danger);
  font-size: var(--text-sm);
  margin-bottom: 16px;
  padding: 10px 12px;
  background: #fef2f2;
  border-radius: var(--radius);
}
.info {
  color: var(--fg);
  font-size: var(--text-sm);
  margin-bottom: 16px;
  padding: 10px 12px;
  background: var(--surface-2);
  border-radius: var(--radius);
}
.submit {
  width: 100%;
  padding: 12px;
  border: none;
  border-radius: var(--radius);
  background: var(--accent);
  color: #fff;
  font-size: var(--text-md);
  font-weight: 600;
  cursor: pointer;
  min-height: 44px;
}
.submit:disabled { opacity: 0.7; cursor: not-allowed; }
.resend {
  width: 100%;
  margin-top: 10px;
  padding: 10px;
  border: none;
  background: transparent;
  color: var(--accent);
  font-size: var(--text-sm);
  cursor: pointer;
}
.resend:disabled { color: var(--muted); cursor: not-allowed; }
.hint { text-align: center; font-size: var(--text-xs); color: var(--muted); margin-top: 12px; }
</style>
```

- [ ] **Step 4: Login.vue 接线**

`web/src/pages/Login.vue`：
- script：`import ForgotFlow from '../components/ForgotFlow.vue';`，`tab` 类型改 `ref<'login' | 'register' | 'forgot'>('login')`，加：
  ```ts
  const resetDoneMsg = ref('');
  function onResetDone() {
    tab.value = 'login';
    resetDoneMsg.value = '密码已重置，请登录';
  }
  ```
  `submit()` 开头清 `resetDoneMsg.value = ''`（新登录尝试时清掉旧提示）。
- template：tabs 区加第三个按钮（`tab === 'forgot'`），form 外包条件：`v-if="tab !== 'forgot'"` 保留现有 form；`v-else` 渲染 `<ForgotFlow @done="onResetDone" />`；form 上方（或 err 同级）加 `<p v-if="resetDoneMsg" class="info" role="status">{{ resetDoneMsg }}</p>`。
- forgot tab 下不显示邀请码字段（现有 `v-if="tab === 'register'"` 已天然排除）。

- [ ] **Step 5: 跑测试确认通过 + 前端全量**

Run: `cd web && npx vitest run src/components/ForgotFlow.test.ts`
Expected: 6 条全 PASS

Run: `cd web && npm test`
Expected: 全量 PASS（Login 现有用例无回归——若 Login.test.ts 断言 tab 行为需同步更新）

- [ ] **Step 6: Commit**

```bash
git add web/src/components/ForgotFlow.vue web/src/components/ForgotFlow.test.ts web/src/pages/Login.vue
git commit -m "feat(plan-08/T6): 登录页忘记密码 tab（ForgotFlow 两步组件 + 倒计时）"
```

---

### Task 7: 全量验证

- [ ] **Step 1: 后端全量 + lint**

Run: `uv run pytest -v`
Expected: 554+ 全绿（新增约 30 条），无 skip 新增

Run: `uv run ruff check app tests alembic`
Expected: 0 error

Run: `uv run lint-imports`
Expected: 通过（domain 零 IO 不受影响——本 plan 未触 domain）

- [ ] **Step 2: 前端全量 + 构建**

Run: `cd web && npm test && npm run build`
Expected: vitest 全绿；产物到 `../static` 无报错

- [ ] **Step 3: 迁移链验证（临时库）**

```bash
tmpdir=$(mktemp -d)
DATABASE_URL="sqlite:///$tmpdir/mig.db" uv run alembic upgrade head
DATABASE_URL="sqlite:///$tmpdir/mig.db" uv run alembic downgrade -1
DATABASE_URL="sqlite:///$tmpdir/mig.db" uv run alembic upgrade head
```
Expected: 往返无报错

- [ ] **Step 4: Commit（如有遗漏文件）**

```bash
git add -A
git commit -m "feat(plan-08/T7): 全量验证" --allow-empty
```

---

## 自审记录（writing-plans checklist）

- **Spec 覆盖**：§3.1 表 → T1；§3.2 service + A3 注入/公共解密 → T0/T2；§3.3 限流（M2/M4 实例注入、单 worker 注释）→ T2；§3.4 端点 + A5 Origin → T4；§3.5 统一话术删 no_channel → T4（逐字断言用例）；§3.6 ForgotFlow（D1/D2 步骤指示器/倒计时/二次确认/aria-current）→ T6；§3.7 admin 兜底 → T5；§4.1 时序含 C1 事务B 重试+告警 → T2（`_invalidate_with_retry` + `admin_alert`，测试 7b 语义由 `test_request_send_failure_marks_code_used` 覆盖告警触发）；§4.3 安全论证 → 各 task 测试；§5 测试铁律 → Global Constraints + 各测试文件 docstring。
- **Placeholder 扫描**：无 TBD/TODO；所有步骤含完整代码或精确命令。
- **类型一致性**：`RateLimited / ResetRejected / RateLimiter.hit(key)->bool`（T2 定义 = T4 使用）；`PasswordResetService.request_reset(username, *, client_ip, session)` / `verify_and_reset(username, code, new_password, *, session)`（T2/T3 定义 = T4 调用）；`decrypt_channel_config(ch_row, crypto)`（T0 = T2）；`app.state.channels / app.state.crypto`（T4 定义 fixture 与 main.py 接线一致）；`ForgotFlow emit('done')`（T6 组件 = Login.vue `onResetDone`）。
- **已知取舍**：spec §4.1 的「send 用 httpx timeout=10」对 smtplib EmailChannel 不适用，按 15s smtplib 现实处理（Global Constraints 已声明）；spec §3.7 的 `current_admin` 笔误按实际 `require_admin`（router 级 dependencies 已挂，端点无需重复）。
