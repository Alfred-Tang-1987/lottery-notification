# 忘记密码（验证码自助重置）设计 spec

> 日期： 2026-08-02 | 状态： 已确认（brainstorming 三节设计逐节通过）
> 范围： 登录页「忘记密码」tab + 后端验证码重置闭环
> 前情： 上次同功能尝试因测试在 pool_size=1 SQLite 上同测试内多次 HTTP 调用产生连接死锁而整体回滚（git 历史无残留）。本 spec §4 测试策略以此教训为核心约束。

## 1. 背景与目标

系统为邀请制多用户部署（家庭 NAS）。用户忘密码目前只能线下找管理员。目标：登录页提供自助重置——用户输入用户名，系统把 6 位验证码经**该用户自己已配置的通知渠道**（email 优先）发给本人，凭验证码设新密码。

约束前提：
- 认证用用户名（`User` 无 email 字段）；email 地址存在于用户通知渠道 `NotificationChannel(type='email')` 的加密 config 中
- 系统 SMTP 已是部署必需项（`smtp_host/user/pass/from`），`EmailChannel` 插件就绪
- 渠道插件契约：`send(payload: NotificationPayload, config: dict) -> SendResult`，永不抛异常；config 由 Fernet 解密后明文传入
- 用户未配任何渠道 → 无法自助，统一话术不暴露，页面提示「未配置通知渠道，请联系管理员重置」

## 2. 已确认的关键决策

| 决策点 | 结论 |
|---|---|
| 重置机制 | 验证码经用户通知渠道（非邮件链接、非管理员手动） |
| 渠道选择 | email 优先，单渠道发送（无 email 则 bark/feishu 任一启用渠道） |
| 未配渠道 | 响应统一话术 + `no_channel: true` 信号（§3.5），前端据以提示联系管理员 |
| 验证码存储 | 新表 `password_reset_codes`（SHA-256 hash，不存明文），Alembic 迁移 0002 |
| 安全策略 | 统一话术防枚举 + IP 限流 + 同用户 60s 重发间隔 + attempts≥5 作废 |
| 前端形态 | Login.vue 扩为三 tab（登录/注册/忘记密码），forgot tab 内两步状态机 |
| 验证码参数 | 6 位数字、15 分钟有效、5 次尝试上限 |
| 发送语义 | 验证码 ≠ 中奖通知：无 DND 顺延、无指数退避重试、无 admin Bark 告警（不复用 Notifier 编排器，独立轻量直发） |

## 3. 架构与组件

### 3.1 新表 `password_reset_codes`（第 14 张表）

| 字段 | 类型 | 说明 |
|---|---|---|
| id | int PK | |
| user_id | int FK→users.id, index | |
| code_hash | str(64) | SHA-256(验证码) hex |
| channel_type | str(8) | 实际发送渠道 email/bark/feishu（审计） |
| expires_at | datetime | 创建 + 15min，naive UTC |
| attempts | int default 0 | 验证失败计数，≥5 作废 |
| used_at | datetime \| None | 非空即作废（成功重置 / 被新请求顶替 / send 失败） |
| created_at | TimestampMixin | naive UTC（`datetime.utcnow`） |

同一用户同时至多一条活跃码：新请求在事务内把该用户所有 `used_at IS NULL` 的码标作废，再插新行。

时区纪律（CLAUDE.md）：`expires_at`/`used_at` 及所有比较一律 `datetime.now(UTC).replace(tzinfo=None)`，与 `created_at` 同 naive UTC 同数值。

### 3.2 `app/services/password_reset_service.py`（新）

分层纪律：service 编排，复用 `app/notifications/` 插件层（EmailChannel/BarkChannel/FeishuChannel 直接实例化），**不**经过 `Notifier`（其 DND/重试/告警语义是中奖通知的，对验证码是错的；且 `_send_with_retry` 的 `time.sleep` 退避会阻塞 HTTP worker）。

```python
class PasswordResetService:
    def __init__(self, engine: Engine, *,
                 channels: dict[str, NotifierChannel] | None = None,  # 可注入假插件
                 rate_limiter: RateLimiter | None = None,             # 可注入，便于单测
                 code_ttl_minutes: int = 15,
                 max_attempts: int = 5,
                 resend_interval_seconds: int = 60): ...

    def request_reset(self, username: str, *, client_ip: str) -> None:
        """统一话术语义：用户不存在/无渠道/限流跳过/send 失败——全部静默，不抛错。
        仅 IP 超限抛 RateLimited（API 层转 429）。"""

    def verify_and_reset(self, username: str, code: str, new_password: str) -> None:
        """成功: 单事务改 password_hash + 码标 used_at。
        失败: attempts+1 独立小事务持久化（防爆破计数不丢，
        与 InviteService._record_failed_invite_attempt 同模式），抛 ResetRejected。"""
```

渠道选择：email 优先；无 email 则 bark > feishu 顺序取第一个 `enabled=True` 且解密成功的渠道。解密复用现有设施（`app/infrastructure/crypto.py` 的 CipherProvider，`{"ct": ...}` 格式，解密失败 WARNING + 跳过——同 `Notifier._decrypt_config` 纪律，明文 config 绝不入日志）。

### 3.3 限流器

内存滑动窗口（模块级 dict + threading.Lock）：
- IP 维度：`/auth/forgot-password` 每分钟 ≤3 次，超限 → 429
- 用户维度：同用户距上次发码 <60s → 静默跳过（仍 200 统一话术，不发新码）

进程重启清零可接受——DB 侧 attempts 上限 + 码 15min TTL 是硬兜底。NAS 单进程部署，无需 Redis。

### 3.4 API（`app/api/auth.py` 新增）

两个匿名端点，与 register/login 一样豁免 CSRF（首次请求无 csrf cookie）：

- `POST /auth/forgot-password` body `{username: str(1..64)}`
  → 始终 200 `{'ok': True, 'message': '若账号存在且已配置通知渠道，验证码已发送'}`
  → 用户存在且无渠道时响应体追加 `'no_channel': True`（§3.5）
  → IP 超限 429
- `POST /auth/reset-password` body `{username, code: str(6 位数字), new_password: str(8..128)}`
  → 成功 200 `{'ok': True}`；失败 400 `{'detail': '验证码错误或已过期'}`（码错/过期/超 attempts/用户不存在——同文案）

### 3.5 前端如何得知「未配置渠道」

统一话术是**对外默认值**；但「未配渠道」是用户本人可修复的合法状态，值得显式提示：

- `POST /auth/forgot-password` 响应体固定为统一话术，**不**区分用户存在与否
- 但用户**存在且无任何启用渠道**时，响应增加字段 `{'ok': True, 'message': <统一话术>, 'no_channel': True}`；其余情况无此字段
- 前端仅当 `no_channel === true` 时显示「该账号未配置通知渠道，请联系管理员重置」，否则一律显示统一话术
- 信息论上 `no_channel=True` 泄露「用户名存在」——接受此权衡：小圈子邀请制，用户名非秘密；提示价值远大于枚举风险（login 端点已有「用户名存在才能试密码」的同级别暴露面）

### 3.6 前端 Login.vue

tab: `login | register | forgot`。forgot tab 两步状态机（同 tab 内）：

1. **步骤 1**：用户名输入 +「发送验证码」按钮 → POST forgot → 显示统一话术提示 → 进入步骤 2。按钮 60s 倒计时禁用（与后端重发间隔对齐）。
   - 响应含 `no_channel: true`（§3.5）时，改显「该账号未配置通知渠道，请联系管理员重置」，不进入步骤 2。
2. **步骤 2**：验证码 + 新密码 + 确认新密码 → 本地校验（两次密码一致、码 6 位数字）→ POST reset → 成功：提示「密码已重置，请登录」+ 自动切回 login tab；失败：显示 400 文案。

`autocomplete`：验证码 `one-time-code`，新密码 `new-password`。

## 4. 数据流与错误处理

### 4.1 request_reset 时序

```
POST /auth/forgot-password {username}
 1. 限流器查 IP（超限 → 429）
 2. session（get_session_dep 注入）查用户 → 不存在 → 200 统一话术（INFO log）
 3. 查该用户启用渠道，email 优先 → 无渠道 → 200 统一话术 + no_channel=true（INFO log，不写码）
 4. 同用户 60s 内已有码 → 200 统一话术（不发新码）
 5. secrets 生成 6 位数字码
 6. 事务A（单 commit）: UPDATE 旧活跃码 used_at=now + INSERT 新码行 → commit
 7. 事务外: 解密渠道 config → 插件 send(NotificationPayload(
      title='【兑奖了吗】密码重置验证码',
      body='验证码 123456，15 分钟内有效。若非本人操作请忽略。'))
 8. send 失败 → 事务B: 码 used_at=now 标作废 + WARNING(exc_info) → 仍 200 统一话术
```

关键事务纪律：渠道 `send()` 在 DB 事务外调用（先 commit 码落库再 send）——HTTP 路径不拿写锁等 SMTP 网络 IO。代价：send 失败留一条作废记录（可审计，无害）。

### 4.2 错误矩阵

| 场景 | HTTP 响应 | 副作用 / 日志 |
|---|---|---|
| 用户名不存在（forgot） | 200 统一话术 | INFO |
| 用户名不存在（reset） | 400 统一文案 | 无码可查，直接拒绝 |
| 用户无渠道 | 200 统一话术 + `no_channel: true` | INFO，不写码 |
| 渠道 config 解密失败 | 视同 send 失败 | WARNING（不含明文），码作废 |
| 渠道 send 失败 | 200 统一话术 | WARNING(exc_info)，码作废 |
| 60s 内重复请求 | 200 统一话术 | 不发新码 |
| IP 超限（>3/min） | 429 | 不写码 |
| 码错误 / 过期 / attempts≥5 | 400 统一文案 | attempts+1 独立持久化 |
| 新密码 <8 位 / 码非 6 位数字 | 422（pydantic） | 无 |

防枚举回归要求：测试逐字断言「用户不存在」与「正常发送」的 forgot 响应体完全一致；「码错误」与「用户不存在」的 reset 响应体完全一致。

### 4.3 安全论证

- 6 位数字码 + 5 次尝试 + 15min TTL → 在线爆破需期望 50 万次尝试，远超上限
- 码只存 SHA-256 → DB 泄露不暴露有效码
- 统一话术 + login 已有的统一 401 纪律一致 → 用户名不可枚举
- code 生成用 `secrets`（非 `random`）
- 端点匿名但无状态变更泄露：forgot 唯一副作用是向**该用户自己的渠道**发码；reset 要求持有码（知识因子）

## 5. 测试策略（核心：pool_size=1 死锁规避）

### 5.1 铁律（上次回滚的根源教训 + test_admin.py 已验证 pattern）

1. **每个测试最多 1 次 HTTP 调用**
2. 准备数据用独立 `_seed_*` 辅助函数（`with Session` 开→写→commit→**关闭**），完成后才做 HTTP 调用
3. HTTP 调用后如需验证 DB 状态，**再开新的** `with Session`（此时请求已结束、连接已归还）
4. **绝不**在 HTTP 调用进行中/依赖注入 session 存活期内嵌套开 Session；**绝不**嵌套 `with Session`
5. 渠道 send 一律注入假插件，不打真实网络
6. 多次调用需求（如限流）下沉到 service 单元测试（直接 new service 调 N 次），不走 HTTP

### 5.2 fixture

```python
# tests/api/test_password_reset.py
def _seed_user(db_engine, username='alice', password='oldpass123',
               with_channel: str | None = 'email') -> int:
    """独立 Session 建用户（+可选渠道，config 真 Fernet 加密）。返回 user_id。"""

def _seed_code(db_engine, user_id: int, code: str = '123456', *,
               age_seconds: int = 0, expired: bool = False,
               attempts: int = 0, used: bool = False) -> int:
    """独立 Session 直接写码行（code_hash 算好）。返回 code_id。"""

def _client(db_engine, monkeypatch, fake_send):
    """TestClient + dependency_overrides[get_session_dep] +
    假渠道插件注入（fake_send 捕获 payload 供断言）。"""
```

### 5.3 后端用例（tests/api/test_password_reset.py，每条 ≤1 次 HTTP）

| # | 用例 | 要点 |
|---|---|---|
| 1 | forgot 成功发码 | 200 统一话术；fake_send 收 1 次且 body 含 6 位码；新 Session 断言码行（hash 非明文、expires≈15min） |
| 2 | forgot 用户不存在 | 200 **逐字同** #1 响应体；fake_send 未调；响应无 `no_channel` |
| 3 | forgot 用户无渠道 | 200 同话术 + `no_channel: true`；无码行 |
| 4 | forgot email 优先于 bark | 配双渠道；fake_send 只走 email 插件 |
| 5 | forgot 60s 内重发静默跳过 | _seed 预置 30s 前的码；fake_send 未调；仍只有 1 条码；响应无 `no_channel` |
| 6 | IP 限流 429 | service 单元测试直接调 4 次 request_reset（**不走 HTTP**）；HTTP 层只测一次超限响应（可选） |
| 7 | forgot send 失败码作废 | fake_send 返回 FAILED；200；新 Session 断言 used_at 非空 |
| 8 | reset 成功 | 200；新 Session 断言 password_hash 已变、码 used_at 非空 |
| 9 | reset 码错误 | 400；attempts=1、password_hash 未变 |
| 10 | reset attempts≥5 拒绝 | _seed attempts=5；正确码也 400 |
| 11 | reset 码过期拒绝 | _seed expired；400 |
| 12 | reset 用户不存在 | 400 **逐字同** #9 响应体 |
| 13 | reset 新密码太短 | 422 pydantic |
| 14 | reset 旧码被新码顶替后失效 | _seed 两条码（旧 used、新活跃）；旧码 400 |

### 5.4 前端用例（web vitest）

- forgot tab 渲染 + 两步状态机推进
- 发码后按钮 60s 倒计时禁用
- 确认密码不一致 → 本地报错不发请求
- mock apiPost：reset 成功 → 切回 login tab + 成功提示

### 5.5 验证命令

```bash
uv run pytest tests/api/test_password_reset.py -v   # 新增全绿
uv run pytest -v                                     # 回归 554+ 全绿
cd web && npm test                                   # 前端 vitest
```

## 6. 实现顺序（供 plan 参考）

| Task | 内容 | 测试 |
|---|---|---|
| T1 | 迁移 0002 + `PasswordResetCode` model | model 默认值/约束断言 |
| T2 | `PasswordResetService.request_reset` + 限流器 | 用例 1-7（service 级为主） |
| T3 | `PasswordResetService.verify_and_reset` | 用例 8-14 |
| T4 | API 端点接线（统一话术/CSRF 豁免/429/pydantic） | HTTP 层用例 |
| T5 | Login.vue forgot tab + vitest | §5.4 |

## 7. 明确不做（YAGNI）

- 邮件重置链接（需公开域名，NAS 内网部署不适用）
- 管理员后台重置按钮（本次不做；未配渠道用户线下找管理员，沿用现状）
- 验证码 Fernet 加密存储（短寿命码 SHA-256 已够）
- 多渠道同发 / 用户自选渠道
- Redis 限流（单进程内存滑窗足够）
- 重置后强制下线其他会话（JWT 无服务端状态，7 天 cookie 自然过期；小圈子风险可接受）
