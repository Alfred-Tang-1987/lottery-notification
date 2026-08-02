<!-- /autoplan restore point: /Users/alfred/.gstack/projects/gitea-lottery-notification/main-autoplan-restore-20260802-203436.md -->
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

---

## GSTACK REVIEW REPORT（/autoplan，2026-08-02）

> Codex CLI 不可用（本机未安装）→ 所有 dual-voice 降级为 `[codex-unavailable]`，仅 Claude subagent 单声。结论标注 `[subagent-only]`。

### 事实性瑕疵（spec 描述与代码现状不符）

| # | 位置 | spec 描述 | 代码现状 | 修正 |
|---|---|---|---|---|
| F1 | §3.2 / §6 T1 | "Alembic 迁移 0002"（隐含首迁移为 0001 含全 schema） | alembic 已有 **9 个迁移**，当前 head = `t6f_user_note` | 新迁移 `down_revision='t6f_user_note'`，非接 0001 |
| F2 | CLAUDE.md | "首迁移 0001 含全 schema" | 0001 仅初始，后续 8 个迁移增量改表 | 与本 spec 无关，但实现时勿被误导 |

### Design 维度（UI，主审独立——subagent 聚焦后端，此处补前端）

Login.vue 现状：`tab: 'login'|'register'` 单 `submit()` 二分支，已有 `error`/`loading` 态，用 CSS 变量（`--surface`/`--accent`/`--danger`/`--radius`）。spec §3.6 "扩为三 tab" 与现有结构兼容，但：

- **D1（medium）forgot 是多步状态机，非第三分支**：forgot 流程（输用户名→发码→输码+新密码→提交）与 login/register 的"单表单单提交"不同。若硬塞进同一 `submit()`，函数变三分支×多步，复杂度激增且难测。建议 forgot 拆独立 `<ForgotFlow>` 子组件（P5 explicit over clever）。
- **D2（medium）交互态规约缺失**：spec 未规定"验证码已发"成功反馈（60s 重发倒计时按钮禁用）、新密码二次确认输入框、密码强度/长度即时提示。三步流程缺这些会割裂。应在 spec §3.6 补：步骤指示器（1/2/3）、重发倒计时、密码二次确认。
- **D3（low）类型与 a11y**：`tab` 类型需扩 `'forgot'`；多步流程需 `aria-current="step"` 步骤指示；忘记密码入口对屏幕阅读器应可达。

### DX 维度（轻量——本功能面向最终用户，非开发者工具）

- 仅 3 处 DX 术语（API/error message），主面向最终用户。DX 维度无重大问题。
- **DX1（low）错误消息质量**：spec §4.2 错误码（`INVALID_CODE`/`NO_CHANNEL` 等）应保证 message 含"问题+原因+修复"三要素（项目规范），尤其 `RATE_LIMITED` 须告知用户等多久。

### 架构依赖图（新增组件 vs 现有）

```
                         ┌─────────────────────────────────────┐
   FastAPI /auth/        │   app/api/auth.py (现有)             │
   forgot-password 路由  │   + POST /auth/forgot/request (新)   │
   (新端点，挂 routers)  │   + POST /auth/forgot/reset   (新)   │
                         └───────────────┬─────────────────────┘
                                         │ Depends(get_session_dep)
                                         ▼
                         ┌─────────────────────────────────────┐
                         │ PasswordResetService (新, services/) │
                         │ - request_reset(user) → 写码+send    │
                         │ - verify_and_reset(code,new_pw)      │
                         │ - 内置 RateLimiter (内存滑窗, 新)     │
                         └──┬───────────────┬────────────────────┘
                            │               │
              ┌─────────────▼──┐      ┌─────▼──────────────────────┐
              │ 复用（直接实例化）│      │ CipherProvider/解密 (复用)  │
              │ EmailChannel   │      │ 抽公共函数 from             │
              │ BarkChannel    │      │ Notifier._decrypt_config    │
              │ FeishuChannel  │      │ (现为私有, 需重构提取)       │
              └────────────────┘      └────────────────────────────┘
                     │ new 第二组实例
                     ▼ ⚠ 耦合点: main.py lifespan 已构造一组 channels
                   与 Notifier 的 channels 实例分离 → 配置漂移/Client 生命周期风险
```

**新增表**：`password_reset_codes`（第 14 张表）— `down_revision='t6f_user_note'`（非 0001）。

**关键耦合**：PasswordResetService **绕过 Notifier**（spec §3.2 决策，因 DND/退避/admin-告警语义对验证码是错的），直接 new 渠道实例。这带来：①第二组 EmailChannel/BarkChannel/FeishuChannel 实例（httpx.Client 生命周期）；②Notifier._decrypt_config 私有方法需抽公共函数。

---

## Dual-Voice 共识表（Codex 不可用 → subagent-only）

CEO subagent 实读：spec + CLAUDE.md + config.py。
Eng subagent 实读：spec + 11 个代码文件 + tests/conftest + Dockerfile。

### CEO 共识（subagent-only）

| 维度 | 发现 | 严重 |
|---|---|---|
| 前提被证伪 | spec §1 称 SMTP 为"必需项"，config.py:56-107 全 Optional；未配 SMTP 时验证码落到 Bark/Feishu 明文信道（锁屏预览即可泄露码） | **CRITICAL** |
| 自相矛盾 | §3.5 `no_channel` 字段泄露用户名存在性，引用"login 已同级别暴露"是事实错误（auth.py:149-150 login 统一 401）；§3.5 与 §4.2 自相矛盾 | **HIGH** |
| 备选否决不充分 | §7 否决"管理员后台重置"理由是"未配渠道用户线下找管理员，沿用现状"——但这正是该功能的需求陈述，逻辑自相矛盾 | **MEDIUM** |
| ROI | 邀请制家庭 NAS 下自助重置 ROI 偏低；更高优先级是 JWT 无服务端状态的"无法下线会话"安全债 | INFO |
| 限流边界 | LAN 威胁模型下 IP 限流几乎失效，DB attempts 才是硬兜底，spec 未声明作用边界 | MEDIUM |

### Eng 共识（subagent-only）

| # | 发现 | 严重 |
|---|---|---|
| C1 | 事务B（send 失败作废码）失败留"幽灵活码"：spec 自问自答留白，未做 DoS 分析（攻击者可持续消耗用户 attempts 配额） | **CRITICAL** |
| C2 | §5.1 测试铁律 #1"每测试最多 1 次 HTTP 调用"**误诊根因**：test_auth.py:46-67 单测 6 次 HTTP 全绿。真根因是"HTTP 存活期内嵌套 Session" | **CRITICAL** |
| H1 | §3.2 渠道实例在 service 里 new → httpx.Client 泄漏 + 配置漂移；应从 app.state 注入；_decrypt_config 私有需抽公共函数 | **HIGH** |
| H2 | §3.2 attempts+1"独立小事务同 InviteService 模式"——引用的模式不存在。InviteService 是主 session 计数，仅回滚时才补救。应主 session 单事务 | **HIGH** |
| H3 | reset 端点 state-changing 但豁免 CSRF；§3.4 论证浅。Bark key 泄露 + CSRF 可接管账号。须加 Origin 校验（auth.py:138-146 现成模式） | **HIGH** |
| H4 | Bark key 泄露场景下，验证码退化为"知用户名即可重置"（码发到攻击者设备）。spec §4.3 安全论证未覆盖 | **HIGH** |
| M1 | 事务A/B 间 send（15s SMTP）与 APScheduler jobstore 写的 pool_size=1 抢锁边缘场景 | MEDIUM |
| M2 | rate_limiter 模块级 dict；--reload 双进程下限流失效（生产单 worker 不受影响） | MEDIUM |
| M3 | §3.2 渠道优先级"email>bark>feishu"与 Notifier 无序（DB 自然序）不一致 | MEDIUM |
| M4 | rate_limiter 模块级全局 → 测试间状态泄漏；应做成实例属性注入 | MEDIUM |

### 跨阶段主题（CEO + Eng 独立命中）

**主题 1：渠道配置作为认证凭证的安全模型**（CEO CRITICAL + Eng H4）。两声独立指出：spec 的安全论证隐含假设"渠道配置只有用户能访问"，但 Bark key 泄露是现实场景。这是高置信信号——**核心安全前提需要重审**。
**主题 2：spec 自相矛盾**（CEO no_channel + Eng C2 铁律）。两处都是 spec 内部逻辑不一致。

## 决策审计 trail

| # | 阶段 | 决策 | 分类 | 原则 | 处置 |
|---|---|---|---|---|---|
| 1 | CEO | 渠道白名单（仅 email）vs 全渠道 | 用户挑战 | P1 安全 | **呈交用户** |
| 2 | CEO | 删除 no_channel 字段 | 机械 | P5 一致性 | 自动采纳 |
| 3 | CEO | 重审"管理员后台重置"备选 | 用户挑战 | P3 | **呈交用户** |
| 4 | Eng | 测试铁律 #1 重写 | 机械 | P5 | 自动采纳 |
| 5 | Eng | 渠道实例从 app.state 注入 | 机械 | P5/DRY | 自动采纳 |
| 6 | Eng | attempts+1 改主 session 单事务 | 机械 | P5/silent-failure | 自动采纳 |
| 7 | Eng | reset 端点加 Origin 校验 | 机械 | 安全 | 自动采纳 |
| 8 | Eng | 幽灵活码 DoS 处理策略 | taste | P5 | **呈交用户** |

---

## 最终决议（用户 Approval with overrides，2026-08-02）

用户已批准，以下为最终决策。**spec 实现时须据此修订原文。**

### 三个 User Challenge 的决议

1. **渠道白名单 = 仅 email**（Challenge 1，采纳推荐）
   - 验证码**仅走 email 渠道**。用户未配 email 渠道时，forgot 流程提示"请联系管理员重置"，不发码。
   - 消解：CEO CRITICAL（渠道白名单）、Eng H4（Bark key 泄露场景）。
   - spec §3.2 须改：删除"无 email 则 bark > feishu"分支，改为"无 enabled email 渠道 → no_channel 提示找管理员"。
   - 副作用：`no_channel` 不再泄露用户名存在性（因仅在用户确实存在但无 email 时返回，且建议改为统一话术）——CEO HIGH 一并消解。

2. **自助重置 + 管理员后台重置 都做**（Challenge 2，选"两者都做"）
   - 自助路径：仅 email 的验证码流程（本 spec 主体）。
   - 管理员后台重置：新增 admin 端点（如 `POST /admin/users/{id}/reset-password`，admin 设新密码或生成临时密码），作为未配 email 用户的兜底。
   - spec §7 须改：从"明确不做"中移除"管理员后台重置"，改为"Plan 08 子任务"。
   - 合规：管理员重置须记 audit log（对齐现有 admin 操作审计 pattern）。

3. **幽灵活码 = 重试 + 告警**（Challenge 3，采纳推荐）
   - send 成功但事务B（作废码）失败时：本地短退避重试若干次（非 Notifier 指数退避，2-3 次秒级）；仍失败则 ERROR 级日志 + admin Bark 告警，让运维介入。
   - **不回滚事务A**（保护"HTTP 路径不持写锁等 SMTP 网络 IO"的核心设计前提）。
   - spec §4.1 时序第 8 步须补：事务B 失败的重试 + 告警策略。
   - spec §4.3 须补"幽灵活码 DoS"分析：send 失败的码不消耗用户 attempts 配额（attempts 计数与"是否曾成功发送"解耦——但本场景 send 已成功，此条主要约束 send 失败路径）。

### 五个自动决策（机械，已应用）

| # | 决策 | spec 修订点 |
|---|---|---|
| A1 | 删 `no_channel` 字段泄露（统一话术） | §3.5、§4.2：no_channel 不差异化响应，与"用户不存在"统一 |
| A2 | 重写测试铁律 #1 | §5.1：从"每测试最多 1 次 HTTP"改为"HTTP 存活期内不嵌套 Session；HTTP 间串行即可" |
| A3 | 渠道实例从 app.state 注入 | §3.2：PasswordResetService 不 new 渠道，从 app.state 取已构造的 email channel；解密抽公共函数 `decrypt_channel_config` |
| A4 | attempts+1 改主 session 单事务 | §3.2：删除"独立小事务同 InviteService 模式"，改"码错时 attempts+1 与码判定同事务单 commit"（对齐 InviteService.consume 主 session 计数） |
| A5 | reset 端点加 Origin 校验 | §3.4：对齐 auth.py:138-146 login Origin 检查，有 Origin 须在 CORS allow-list |

### 四个 MEDIUM 项处置（实现注意，须在 spec 修订时落地）

Eng subagent 的 4 个 MEDIUM 发现此前仅在共识表登记为"仅记录"，未给修订点。补充如下：

| # | 决策 | spec 修订点 |
|---|---|---|
| M1 | APScheduler 抢锁边缘场景（事务A/B 间 send 与 jobstore 写在 pool_size=1 下潜在互斥） | §4.1 补一句：send 期间不持 DB 连接（与 Notifier 路径A/B 一致），与 APScheduler jobstore 写不互斥（pool_size=1 + busy_timeout=5000ms 兜底）；send 用 `httpx timeout=10`（bark/feishu 已是 10s）避免 EmailChannel 15s 长持 |
| M2 | rate_limiter 单进程假设（`--reload` 双进程 / 未来多 worker 失效） | §3.3 加显式约束：此限流器依赖 uvicorn 单 worker（Dockerfile CMD 无 `--workers`，已核 Dockerfile）；多 worker 部署须迁 Redis；`--reload` 开发模式下限流窗口翻倍（已知，测试需注意） |
| M3 | 渠道优先级"email>bark>feishu"与 Notifier 无序不一致 | **被 Challenge 1（仅 email）消解**：仅走 email 后无多渠道选择问题。spec §3.2 删除优先级排序逻辑 |
| M4 | rate_limiter 模块级 dict → 测试间状态泄漏 | RateLimiter 做成实例属性注入（非模块级全局）；spec §5.2 加 autouse fixture `_reset_rate_limiter` 清理状态。实例化对齐 spec §3.2 已有 `rate_limiter: RateLimiter \| None = None` 注入参数 |

### Plan 08 任务清单（spec 修订后）

1. **spec 修订**（T0）：按上述 3+5 决策更新 spec 各节
2. 新建 `password_reset_codes` 表（迁移 down_revision=`t6f_user_note`）
3. PasswordResetService（request_reset / verify_and_reset，仅 email，注入 channel + 公共解密）
4. 管理员后台重置端点 + audit log
5. forgot API 端点（/auth/forgot/request、/auth/forgot/reset，后者加 Origin 校验）
6. Login.vue forgot tab（拆独立 `<ForgotFlow>` 多步组件）
7. 测试（按修订后铁律 + 测试计划 artifact 的 critical gaps）

### 状态
- **APPROVED with overrides**。
- spec 须先按本节修订，再进入 Plan 08 实现。
- Codex 全程不可用，dual-voice 为 subagent-only；结论置信度高（CEO+Eng 独立命中渠道安全前提）。

---

## 附录 A：Eng 对抗性评审详情（subagent-only，2026-08-02）

> 来源：独立 Eng subagent 实读 spec + 11 个代码文件 + tests/conftest + Dockerfile。本附录为完整证据保留，每条标注 spec 修订点与决议映射，供 Plan 08 实现者直接对照。

### 事实确认（已核代码）
- **Dockerfile CMD = `uvicorn app.main:app --host 0.0.0.0 --port 8280`，无 `--workers`，默认单 worker** → spec §3.3"NAS 单进程部署"断言成立。
- **`tests/api/test_auth.py:46-67` `test_register_login_logout_flow` 单测做 6 次 HTTP（register/login/me/csrf/logout/me），全部通过** → 实证 spec §5.1 铁律 #1"每测试最多 1 次 HTTP"与现有实践矛盾。
- **`app/services/invite_service.py:94` `ic.attempts += 1` 在主 session 内** → spec §3.2"独立小事务同 InviteService 模式"引用错误。

### CRITICAL

**C1. 幽灵活码 DoS** — spec:128 时序第 8 步"send 失败 → 事务B 标 used_at"，但 spec 自问"事务B 失败怎么办"留白。
高危场景：事务B 失败后，活码在 15min TTL 内继续接收 reset 尝试计数。攻击者知用户名即可持续打 5 次错码锁掉，用户每次都得赶在 5 次错码前提交正确码——这是 **DoS 不是爆破**，spec §4.3 只算了爆破期望次数。
**决议**：Challenge 3 = 重试 + 告警（本地短退避 2-3 次，仍失败 ERROR + admin Bark 告警，不回滚事务A）。

**C2. 测试铁律 #1 误诊根因** — spec §5.1:161"每测试最多 1 次 HTTP"称是"上次回滚根源教训"。实证：test_auth.py:46-67 单测 6 次 HTTP 全绿。真根因是 **"HTTP 存活期内（依赖注入的 Session 还活着）嵌套开 `with Session`"**，test_auth.py:90 的模式才正确（HTTP 完成后才开 Session 验证）。
**决议**：自动决策 A2 = 重写铁律 #1 为"HTTP 存活期内不嵌套 Session；HTTP 间串行即可"。

### HIGH

**H1. 渠道实例泄漏 + 配置漂移** — main.py:120-133 lifespan 构造 channels（含 httpx.Client），teardown 调 notifier.close()。spec §3.2 让 PasswordResetService"直接实例化"渠道 → new 第二组 client，永不被 close（service 无生命周期钩子），NAS 常驻进程下累积泄漏。且 `_decrypt_config`（notifier.py:260）是私有方法，依赖 self._crypto。
**决议**：自动决策 A3 = 渠道从 app.state 注入（不 new）；抽公共 `decrypt_channel_config(ch_row, crypto)` 函数，Notifier 改 1 处调用，无行为变化。

**H2. attempts+1 引用了不存在的"同模式"** — spec §3.2:69-70 称"独立小事务同 InviteService._record_failed_invite_attempt 模式"。实证 InviteService.consume（invite_service.py:94）是**主 session 内计数**，`_record_failed_invite_attempt` 仅在主 session 已回滚后补救。spec 让 verify_and_reset 的 attempts+1 **总是**走独立小事务 → 分两次 commit，小事务失败则计数丢失（违反 CLAUDE.md silent-failure 纪律）。
**决议**：自动决策 A4 = attempts+1 改主 session 单事务（码错时主 session 唯一写就是 attempts+1，无需独立小事务）。

**H3. reset 端点 CSRF 豁免论证浅** — spec §3.4:85-86"匿名豁免 CSRF 同 register/login"。但 reset 是 state-changing（改 password_hash）。攻击：攻击者知用户名 + 拿到验证码（Bark key 泄露场景）→ 诱导受害者点跨站链接 POST reset → 密码改成攻击者已知值 → 接管账号。
**决议**：自动决策 A5 = reset 端点加 Origin 校验（对齐 auth.py:138-146 login 现成模式）。

**H4. Bark key 泄露场景验证码退化** — spec §4.3:150 爆破期望次数论证只在"码发到仅用户可见渠道"时成立。Bark key 泄露（用户常贴到自动化平台）→ 攻击者调 forgot 拿到码，无需爆破，整个机制退化为"知用户名即可重置"。
**决议**：Challenge 1 = 仅走 email（消解——email 地址泄露概率远低于 Bark key）。

### MEDIUM（见"四个 MEDIUM 项处置"表）

- **M1** APScheduler 抢锁：send 期间不持 DB 连接（与 Notifier 路径A/B 一致），send 用 timeout=10。
- **M2** rate_limiter 单进程：spec §3.3 加约束"依赖 uvicorn 单 worker"。
- **M3** 渠道优先级：被 Challenge 1（仅 email）消解。
- **M4** rate_limiter 测试泄漏：RateLimiter 做实例属性注入 + autouse fixture 清理。

### LOW

- **L1** `channel_type str(8)` magic number，但 notification.py:18 已 max_length=8 对齐，历史约定沿用。
- **L2** T2 实现顺序合理，仅确认。

### 总评
spec 整体设计正确（双事务分离 send 在事务外、统一话术防枚举、SHA-256 hash 不存明文、60s 重发、attempts≥5 锁定）。阻断性：C1/C2/H1/H2（实现会走偏或留坑）。安全论证缺失：H3/H4（已在决议补齐）。实现注意：M1-M4。spec 按"最终决议"段修订后可进 Plan 08。

