# 彩票开奖自动核对与通知系统 — 设计文档（Spec）

> **日期**: 2026-06-16
> **状态**: Draft（待用户审阅）
> **技术栈**: Python 3.12 + FastAPI + SQLite + Vue3/ECharts + APScheduler
> **部署**: NAS Docker（FnOS, <NAS_IP>），端口 8280

---

## 1. 背景与目标

> **产品名**：兑奖了吗？（副标题：开奖自动核对与中奖推送）｜未来 iOS App 复用此品牌名

### 1.1 问题
用户是彩票爱好者，有"追号"习惯（固定号码长期投注），但每期开奖后需自行去彩票店人工核对、兑奖，极不便。希望开奖后**自动比对**个人号码并将结果**推送**给自己，并扩展到福利彩票与体育彩票的主要品种。

### 1.2 目标
构建一个**多用户**的彩票开奖自动核对与通知系统：
- 覆盖福彩+体彩 **7 大主流彩种**；
- 用户维护固定号码池，系统每期自动比对；
- 比对结果按用户配置的渠道和策略推送；
- 提供中奖历史、盈亏、命中率等**事后统计**；
- 当前为小圈子（家人朋友）共享，**架构预留大规模公开扩展**。

### 1.3 产品定位（合规红线）
**「事后核对 + 个人号码管理工具」**。系统只做开奖结果的事后核对、展示与统计，**绝不包含**任何号码预测、走势推荐、冷热号选号、购彩代购或支付功能。详见 [§9 合规边界](#9-合规边界)。

---

## 2. 用户与场景

| 维度 | 说明 |
|---|---|
| 用户规模 | 当前小圈子共享（自用 + 家人朋友，几个人）；邀请制加入 |
| 认证 | 账号密码 + 邀请码；预留未来更强认证（OAuth/JWT/限流） |
| 数据隔离 | 用户私有数据严格按 user_id 隔离；开奖结果为全局共享数据 |
| 扩展性 | 当前 SQLite + 单容器；预留迁移 PostgreSQL、水平扩展、分布式调度 |
| 终端 | 当前 Web UI（响应式，iOS Safari 友好）；未来优先 iOS 原生 App（API-first 复用） |

---

## 3. 需求全景

### 3.1 功能模块（MVP）

| 模块 | 内容 |
|---|---|
| **用户体系** | 邀请制注册、账号密码认证、数据隔离、角色(user/admin) |
| **号码管理** | 固定号码池、按彩种分组、CRUD、号码盘点击选号（按玩法约束）、批量导入、合法性校验、命名备注、玩法标记 |
| **倍投** | 每注 2–99 倍（影响投入与中奖金额，按注设置） |
| **追加投注** | 仅大乐透（基本 2 元 + 追加 1 元，追加仅参与一二等奖 80%） |
| **玩法** | MVP 单式先行；架构支持复式/胆拖/组选（Phase 2） |
| **彩种** | 7 大主流（双色球、大乐透、七乐彩、七星彩、福彩3D、排列3、排列5），策略模式插件化 |
| **开奖获取** | 双源容灾（MXNZP 主 + 聚合数据备）、定时 + 手动、多源交叉校验 |
| **中奖比对** | 奖级自研判定（规则表）、固定档奖金内置、浮动标"待官方派奖" |
| **通知推送** | Bark / 飞书 / 邮箱（可插拔、按用户配置、无主备；邮箱系统统一发件）；每用户×每彩种策略可配；分层时机（大奖即时 + 次日汇总）；中奖高优先级 |
| **Web UI** | 号码管理、配置、开奖查询、历史、统计、走势、设置、管理后台 |
| **统计** | 中奖历史、累计盈亏（按时段：全部/本月/本年）、号码命中率、中奖等级分布 |
| **提醒** | 开奖信息提醒（"今晚 X 开奖"）、兑奖期限提醒（60 天倒计时）、中奖税务提示（超 1 万缴 20%） |
| **信息日历** | 开奖时间表、历史开奖查询（纯查询） |
| **走势图（合规版）** | 纯历史连线 + 近 N 期号码出现频次（纯数字，不排序/不标冷热/不推荐）+ 显著随机性声明 |
| **运维管理** | 健康状态面板、管理员后台、数据导出（CSV/JSON）、推送日志 |
| **周/月报** | 定期盈亏汇总推送 |
| **理性提示** | 全系统常驻"理性购彩 量力而行" |
| **深色模式** | 自动（18:00–次日 6:00）+ 手动切换 + 记住偏好（localStorage） |
| **兑奖操作** | 待兑奖可标记"已领取"（更新 `prize_claims.status`） |
| **用户身份区** | 右上角当前用户头像 / 切换账号 / 登出（多用户入口） |

### 3.2 明确不做（YAGNI / 合规）
- 号码预测 / AI 推荐 / 必中；冷热号排序推荐
- 购彩、代购、支付；高频彩、私彩
- iCal 导出/订阅；购彩行为提醒（仅保留开奖信息提醒）
- 预算管理、盈亏趋势曲线图、年度报告（本轮未选，可未来迭代）
- PWA / 推送多渠道冗余 / 静默时段（本轮未选）

> 注：开奖日历提供开奖时间表与历史查询；**iCal 订阅已取消**，不在任何页面提供。

---

## 4. 架构设计

### 4.1 分层架构

```
┌──────────────────────────────────────────────────────┐
│  客户端层（API-first，为 iOS App 铺路）                 │
│   Web UI (Vue3 响应式)  ←─ 未来：iOS 原生 App          │
└────────────────────────┬─────────────────────────────┘
                         │ REST/JSON
┌────────────────────────▼─────────────────────────────┐
│  接口层  FastAPI：认证 / 用户 / 号码 / 查询 / 管理 API   │
└────────────────────────┬─────────────────────────────┘
┌────────────────────────▼─────────────────────────────┐
│  应用服务层                                            │
│  调度器 │ 开奖获取(双源) │ 比对引擎 │ 通知服务(插件化) │
│  统计 │ 提醒 │ 走势 │ 兑奖 │ 周月报                    │
└────────────────────────┬─────────────────────────────┘
┌────────────────────────▼─────────────────────────────┐
│  领域层（纯逻辑、无 IO、可单测——核心资产）              │
│  彩种规则引擎(策略模式) │ 号码/玩法模型 │ 奖级规则表    │
└─────────┬────────────────────────────┬───────────────┘
┌─────────▼──────────┐        ┌────────▼──────────────┐
│  适配层（数据源）    │        │  基础设施              │
│  MXNZP / 聚合数据   │        │  SQLite(→可迁PG)       │
│  双源容灾+交叉校验  │        │  配置/日志/缓存        │
└────────────────────┘        └───────────────────────┘
```

### 4.2 关键设计原则
- **领域层零 IO**：彩种规则、奖级判定、号码比对是纯函数，不碰数据库/网络，可 100% 单测。这是多彩种兼容的核心落点。
- **策略模式彩种引擎**：新增彩种 = 加一份 `LotterySpec` 配置 + 若需要则加一个 `CompareStrategy`，核心逻辑不动。
- **通知/数据源插件化**：渠道和数据源均为可插拔接口。
- **编码纪律（非过度设计）**：MVP 单容器跑全部；坚持"领域层无 IO"的分层纪律，使未来真要拆分（worker 独立、DB 迁 PG、推送走队列）时是"移动代码"而非"重写"。当前不投入拆分基建。

### 4.3 部署
- **单容器**：FastAPI（Web + API）+ APScheduler（进程内定时）同进程；SQLite 持久化卷挂载到 NAS。
- **端口 8280**（已核实空闲，避开 NAS 已占用端口）。
- **`restart: always`**（FnOS 关机会 `docker stop` 所有容器，`unless-stopped` 不会自启——见 NAS 维护记录）。
- **SQLite 配置**：`journal_mode=WAL` + `synchronous=NORMAL` + `busy_timeout=5000`；**单写连接**（写 pool_size=1）防 `database is locked`。APScheduler 的 `SQLAlchemyJobStore` **必须共享同一 engine**（让 WAL/busy_timeout 生效），且调度器线程用**独立连接实例**，不进 FastAPI 的 pool_size=1 池（否则请求持有连接时调度器死锁）。
- **APScheduler 配置**：`coalesce=True` + `max_instances=1`（防 misfire 重复执行）；**全局时区 `Asia/Shanghai`**（所有 job 创建时 tz-aware，避免 tz-naive 静默偏移 8 小时）；`misfire_grace_time=600s`；启动 backfill 同时补 outbox 与遗漏抓取。
- **Schema 迁移**：使用 Alembic 管理迁移；**初始迁移包含 APScheduler 的 `apscheduler_jobs` 表**（不让 jobstore 首次运行 auto-create，避免与 Alembic 鸡生蛋导致 schema drift）。
- **备份**：每日用 SQLite backup API 进程内备份（或宿主 `sqlite3 .backup` CLI + WAL checkpoint）到独立目录，保留 30 天；`admin_audit_logs`/`notification_logs`（90 天）有归档/清理任务。
- **认证/CORS**：httpOnly cookie + SameSite + CSRF token（SPA 经 `/auth/csrf` GET 拿 token，因读不到 httpOnly cookie）；CORS `allow_credentials=True` + 显式 origins（生产同源托管 SPA；开发期 Vite 5173→FastAPI 8000 需显式白名单，禁用通配符）。
- **启动校验**：启动时检查 `JWT_SECRET`/`CRYPTO_KEY`、系统时区 Asia/Shanghai、必要 SMTP 配置（若启用 email）；若启用 email 则强制 admin Bark fallback 已配。
- **Docker healthcheck**：提供 `/health` 端点。
- **预留拆分**（未来，非 MVP）：worker 容器独立、SQLite→PostgreSQL、推送走消息队列——均不需改领域层。

---

## 5. 领域模型（纯逻辑，核心资产）

四个核心概念：

### 5.1 彩种规格 `LotterySpec`（配置驱动）
```python
# 分区型：集合语义，号码去重、无序（红球 6 个不同号）
NumberRange(min=1, max=33, count=6)            # 用于 partition 彩种

# 按位型：有序、每位独立、允许跨位重复（如七星彩前区 1,1,2,3,4,5）
PositionalDigits(min=0, max=9, length=6)       # 用于 positional/hybrid 彩种

LotterySpec(
  code="ssq", name="双色球", category="welfare",
  front=NumberRange(min=1, max=33, count=6),   # 红球（分区，去重）
  back =NumberRange(min=1, max=16, count=1),   # 蓝球
  draw_days=[周二, 周四, 周日],
  play_types=[single, fushi, dantuo],
  number_style="partition",                     # partition(分区) | positional(按位) | hybrid(七星彩)
  welfare_rate=36,                              # 公益金比例（占购彩额 %）
  price_per_bet=200                             # 单注金额（分），便于投入/公益统计
)
```

> **类型表达不变式**：`NumberRange`（集合、去重、无序）用于分区型彩种；`PositionalDigits`（有序、每位独立、**允许跨位重复**）用于按位型/混合型。两种语义不能共用一个类型——否则七星彩前区 `1,1,2,3,4,5`（合法）会被分区校验误拒。校验逻辑落在类型层，不靠运行时 if 区分。

**7 大彩种规则表**：

| 彩种 | code | 前区(范围×个数) | 后区(范围×个数) | 开奖日 | 号码风格 | 公益率(%) | 单注(分) |
|---|---|---|---|---|---|---|---|
| 双色球 | ssq | 1-33 × 6 | 1-16 × 1 | 二/四/日 | partition | 36 | 200 |
| 大乐透 | dlt | 1-35 × 5 | 1-12 × 2 | 一/三/六 | partition | 36 | 200 |
| 七乐彩 | qlc | 1-30 × 7 | 1-30 × 1(特别号，无放回) | 一/三/五 | partition | 36 | 200 |
| 福彩3D | fc3d | 0-9 × 3 | — | 每日 | positional | 34 | 200 |
| 七星彩 | qxc | 前区 0-9 × 6（按位）| 后区 0-14 × 1 | 二/五/日 | **hybrid** | 37 | 200 |
| 排列3 | pl3 | 0-9 × 3 | — | 每日 | positional | 34 | 200 |
| 排列5 | pl5 | 0-9 × 5 | — | 每日 | positional | 34 | 200 |

> 七乐彩后区为"特别号"（从**剩余 23 个号码中无放回**抽取，参与奖级判定）。福彩3D/排列3/排列5 为按位数字（每位 0-9，顺序敏感）。**七星彩 2020-10-13 改版**后为"前区 6 位（0-9）+ 后区 1 位（0-14）"混合型，号码风格为 `hybrid`。完整规则见 [`docs/reference/lottery-rules.md`](../reference/lottery-rules.md)。公益率按官方返奖+公益金+发行费拆分给出，统计时以 `LotterySpec.welfare_rate` 为准。

### 5.2 号码/玩法模型 `Entry`
- `play_type` 按彩种配套：分区型（双色球/大乐透/七乐彩/七星彩）= `single`(单式)/`fushi`(复式)/`dantuo`(胆拖)；按位型（福彩3D/排列3/排列5）= `danxuan`(单选,3D)/`zhixuan`(直选,排列)/`zuxuan3`(组选三)/`zuxuan6`(组选六)。注：**福彩3D 官方共 12 种玩法**（另有 1D/2D/通选/和数/包选/猜大小/猜三同/拖拉机/猜奇偶），MVP 取主要 3 种、其余 Phase 2 补；**排列5 仅 `zhixuan`**（含定位复式/组合复式，无组选）。
- 复式/胆拖存储原始选择。**展开逻辑 `expand(entry) -> list[SingleCombo]` 单点在领域层**：创建/导入注单时**立即调用一次**算出真实 `cost`（= 单注价 × 组合数 × 倍投 × 追加系数）并校验 `MAX_COMBINATIONS` 上限；比对时复用该展开结果（带按 entry 内容 hash 的内存缓存，注单变更则失效）。
- **展开上限**：单注展开后的单式组合数不得超过 `MAX_COMBINATIONS`（默认 10,000）。超限在 create/导入时拒绝。
- MVP 仅实现 `single`（+ 按位型的 `zhixuan`）；其余玩法 Phase 2。

### 5.3 奖级规则 `PrizeTier`（命中条件 → 奖级 → 奖金类型）
```python
PrizeTier(lottery="ssq", tier=5, condition="front_hit==4 and back_hit==0",
          amount=10, amount_type="fixed")   # fixed 固定档 / float 浮动

# 大乐透追加示例：追加后一二等奖为原浮动金额的 1.8 倍
PrizeTier(lottery="dlt", tier=1, condition="front_hit==5 and back_hit==2",
          amount=None, amount_type="float", append_multiplier=1.8)
```
- **固定档奖金可配置**（政策会调，不硬编码代码）。
- **浮动档（一二等奖）** 标 `amount_type="float"`，当晚给"待官方派奖"。
- **大乐透追加**参与一二等奖：`append_multiplier=1.8`（基本 2 元 + 追加 1 元，追加部分按 80% 计入，即总奖金 = 原浮动额 × 1.8）。其他彩种 `append_multiplier=1.0` 或省略。compare strategy 读取 `Entry.append` 决定是否乘上该倍数。

**双色球奖级示例**（完整规则表在领域层实现）：
| 奖级 | 命中条件 | 奖金类型 |
|---|---|---|
| 一等奖 | 6红+1蓝 | float（浮动） |
| 二等奖 | 6红+0蓝 | float（浮动） |
| 三等奖 | 5红+1蓝 | fixed 3000 |
| 四等奖 | 5红+0蓝 / 4红+1蓝 | fixed 200 |
| 五等奖 | 4红+0蓝 / 3红+1蓝 | fixed 10 |
| 六等奖 | 2红+1蓝 / 1红+1蓝 / 0红+1蓝 | fixed 5 |

### 5.4 比对策略 `CompareStrategy`（策略模式）
```
接口: compare(spec, draw_numbers, entry) -> HitResult(hits, tier, amount, is_win)
实现:
  - PartitionCompare : 双色球/大乐透/七乐彩（集合匹配红/蓝球个数；七乐彩后区特别号单独计命中）
  - PositionalCompare: 福彩3D/排列3/排列5（逐位比对；单选/直选全对、组选看数字集合）
  - QxcHybridCompare: 七星彩（前区 6 位按位全对/部分对，后区单值 0-14 命中；奖级判定结合前区连续命中位数 + 后区命中）
```

**七星彩奖级简化规则（MVP）**：以"前区连续命中位数"+"后区命中"共同判定。例如：
- 一等奖：前区 6 位全中且后区中；
- 二等奖：前区 6 位全中但后区不中；
- 三至六等奖：前区连续命中位数递减 + 后区命中/不中的组合（具体表在领域层实现，严格对照 `docs/reference/lottery-rules.md`）。

> 注：七乐彩后区特别号从剩余 23 个号码中无放回抽取，因此 `PartitionCompare` 的前区命中与后区命中**不是独立概率事件**，但奖级判定只需按开奖结果做命中计数即可，无需模拟抽取过程。

---

## 6. 数据模型（SQLite，类型选可迁 PG 的）

### 6.1 数据分类（关键）
| 类型 | 表 | user_id | 说明 |
|---|---|---|---|
| **全局共享** | `lottery_types`、`draw_results` | 无 | 开奖结果是公共事实，所有用户看同一期 |
| **用户私有** | `tickets`、`comparisons`、`prize_claims`、`notification_*` | **有** | 严格 user_id 隔离 |

### 6.2 表结构

| 表 | 关键字段 | 说明 |
|---|---|---|
| `users` | id, username, password_hash, role(user/admin), invite_code, created_at | 邀请制；邀请码**单次使用 + 有效期 + 失败尝试锁定**，仅 admin 生成，无默认 bootstrap 码 |
| `lottery_types` | code, name, category, spec_json, draw_schedule_json, enabled | 彩种配置（种子内置 7 大彩种） |
| `tickets` | id, **user_id**, lottery_code, play_type, numbers_json, tuo_json(胆拖拖码), label, multiplier(倍投1-99), append(追加), cost(分), enabled, created_at | 号码池（`cost` = 该注真实投入，含倍投/追加/复式影响） |
| `draw_results` | id, lottery_code, draw_no(期号), draw_date, numbers_json, source, fetched_at, verified, version(默认1, 修正后递增) | 唯一约束 (lottery_code+draw_no) 保证幂等；`version` 支持官方更正 |
| `draw_corrections` | id, draw_result_id, old_numbers_json, new_numbers_json, corrected_at, reason | 开奖结果官方更正记录 |
| `comparisons` | id, **user_id**, draw_result_id, ticket_id, hits_json, prize_tier, prize_amount, is_win, created_at, corrected_at(可空) | 比对结果；**唯一约束 (draw_result_id, ticket_id)** 保证同注同期只一行（更正时**原地更新** hits/tier/amount + 写 `corrected_at`，避免 stats 双算/中奖记录重复；历史走 `draw_corrections` 追溯） |
| `prize_claims` | id, **comparison_id**, status(pending/claimed/expired), deadline, claimed_at | 兑奖台账 |
| `pending_comparisons` | id, draw_result_id, created_at, processed_at | 比对触发 outbox；worker 用 `UPDATE ... SET processed_at=now WHERE id=? AND processed_at IS NULL RETURNING id` 原子认领（影响行数=1 才比对），保证"比对一次" + 崩溃可补 |
| `notification_channels` | id, **user_id**, type(bark/feishu/email), config_json, enabled, key_version | 每用户渠道配置（加密存储；`key_version` 支持 Fernet 轮换） |
| `notification_rules` | id, **user_id**, lottery_code, strategy(every/win_only), timing | 每用户×每彩种推送策略 |
| `notification_logs` | id, **user_id**, type, payload, status, sent_at, error | 推送日志（保留 90 天） |
| `api_source_health` | source, last_success_at, status, error | 数据源健康面板数据源 |
| `admin_audit_logs` | id, admin_id, action, target_type, target_id, old_values, new_values, created_at | 管理员操作审计 |

### 6.3 用户隔离机制（不只靠自觉）
- FastAPI 依赖注入从认证 token 解析 `current_user`；
- 所有 Repository/Service 方法**强制接收 `user_id`**，查询一律 `WHERE user_id = ?`；
- 用户 A 的请求链路拿不到用户 B 的 user_id，物理上无法越权。

---

## 7. 核心数据流（开奖 → 比对 → 推送）

> **范围三层（重要）**：① 开奖获取是**全局**的——所有启用彩种都拉取开奖结果（公共数据）；② **比对**只针对"追了该开奖彩种"的用户的"该彩种启用注"（号码池决定范围）；③ **推送**只推送用户追投彩种的结果。**用户没追的彩种，不比对、不推送。**

### 7.1 关键设计：比对只做一次，两条推送路径复用结果
```
[1] 开奖获取 Fetch（双源 + 交叉校验）→ 写 draw_results（verified=true）
            │
            ▼
[1.5] 写 pending_comparisons outbox（驱动后续比对）
            │
            ▼
[2] APScheduler 轮询 pending_comparisons → 比对 Compare（唯一比对点 · 幂等）
     仅比对"追了该开奖彩种"的用户的"该彩种启用注"（号码池决定范围）→ 写 comparisons
     用户号码池无该彩种注 → 标记 processed 但不生成 comparisons
            │
            ├──── 路径 A「开奖当晚」: 读 comparisons → 命中一二等奖 → 推【即时简讯】
            │
            └──── 路径 B「次日 07:00」: 读 comparisons → 按用户×彩种策略 → 推【详情汇总】
```

- **比对触发时机**：`draw_results` 首次 `verified=true` 入库后，写 `pending_comparisons` 一行；APScheduler 短周期轮询该 outbox，用 claim SQL（`UPDATE ... SET processed_at=now WHERE id=? AND processed_at IS NULL RETURNING id`）原子认领，影响行数=1 才比对。崩溃可补、并发不重比。
- **路径 A（异步）**：比对事务**提交后**，把"命中一二等奖需推即时简讯"的任务交给独立推送 worker / 异步任务，**绝不阻塞比对事务或持有 DB 连接**（否则慢 SMTP 会卡死单写连接，连带 Path B/抓取 stall）。文案"恭喜，双色球第 X 期命中二等奖，详情见次日汇总"。
- **路径 B**：次日 07:00 **不再比对**，直接读已有 `comparisons`，按策略推详情汇总（含未中奖的"本期核对完毕"）。
- **`comparisons` 为单一数据源**，两条推送路径只是"同一份数据、不同时机、不同文案"。
- **官方开奖结果更正**：官方事后更正号码 → 写 `draw_corrections` + `draw_results.version` 递增 + 重新生成 `pending_comparisons` 行 → 重新比对时**原地更新**对应 `comparisons`（hits/tier/amount）并写 `corrected_at`（**不新增行**，唯一约束 (draw_result_id, ticket_id) 兜底），避免 stats 盈亏双算与中奖记录重复。更正后若用户已收到旧结果，推送一条更正简讯。
- **浮动奖回填**：一二等奖首次比对 `prize_amount=null`（"待官方派奖"）。APScheduler 次日起每日轮询官方数据源回填 `comparisons.prize_amount`；**回填有上限**（默认追踪 7 天，超期标 `unresolved` 不再查）；**回填成功后补推**一条"实际奖金已更新：{金额}"给该用户（大奖金额是用户最关心的，默认不推是产品缺陷）。
- **`verified=false` 恢复**：双源不一致拒入库后，21:30→01:00 窗口会反复重抓重不匹配；设**单期重抓上限**（默认 6 次），超限停止该期自动重抓，admin 在后台可 **force-verify**（人工核对后标记 verified + 写 `admin_audit_logs`）或忽略该期。

### 7.2 开奖获取（双源容灾）
```
主源 MXNZP ──失败──▶ 备源 聚合数据 ──失败──▶ 用本地缓存最近一期 + 告警
双源都成功 → 交叉校验号码一致才入库(verified=true)
              不一致 → 拒绝入库(verified=false) + 告警 + 人工介入
一源成功一源空 → 等待 grace window(默认5分钟) 后重试；仍只有一源 → 单源入库,
                  verified=true, single_source=true, 健康面板标黄, 文案提示"单源校验"
```

- **"空结果" 的语义**：若返回为空但 HTTP 成功，视为"该期尚未开奖"，标记 `not_drawn`，停止本轮，等下次轮询；不是错误。
- **部分源**：一源已有号码、另一源暂无（时间差）时，给 5 分钟 grace window，超时仍只有一源则按单源入库，避免整夜 stall。单源结果在 UI 用黄色"单源校验"标签提示。
- **期号语义映射**：MXNZP 与聚合数据的 `draw_no` 命名可能不同（如 `2026062` vs `062`）或一方滞后一期；适配层先做**归一化期号映射**再交叉校验号码，否则"号码一致"无意义。
- **抓取退避/抖动**：每轮失败（timeout/429）用指数退避 + 随机抖动重试，单期最多 ~6 次；避免 15min 固定节奏把限流源一夜锤 ~100 次（7 彩种）触发封禁。

### 7.3 调度
- 开奖时间表存 `lottery_types.draw_schedule_json`，APScheduler 动态注册任务。
- **路径 A 轮询**：开奖日 21:30 起每 15 分钟轮询直到拿到结果（最晚至次日 01:00）。
- **路径 B 汇总**：次日 07:00 一次性汇总（时间可配置）。
- **浮动奖回填轮询**：次日 08:00 起每日一次，检查官方是否公布一二等奖具体金额。
- **兑奖过期扫描**：每日 07:30 扫描 `prize_claims`，把超过 60 天未领取的标记为 `expired`。
- **APScheduler 配置**：详见 §4.3（`SQLAlchemyJobStore` 共享 engine、全局 Asia/Shanghai、`coalesce=True`/`max_instances=1`、`misfire_grace_time=600s`）。
- **DND 顺延触发**：路径 B/周月报在免打扰时段被抑制时，**登记延后任务**（在 DND 结束时刻调度一次），而非依赖下次常规 tick 撞上——避免与常规 07:00 job 碰撞重复推送。
- **宕机补抓**：启动 backfill 不仅补未处理的 `pending_comparisons`，也检查宕机窗口内**应开奖但未抓取**的彩种补抓（抓取与比对两路都补）。
- 全程 Asia/Shanghai 时区。

### 7.4 推送决策（按用户、仅追投彩种）
- **推送彩种范围 = 该用户号码池里有启用注的彩种**。用户没追投的彩种即使开奖也不推送（避免噪音）。
- 对每个追投彩种，查 `notification_rules(user_id, lottery_code)` 的策略：`every` → 每期推；`win_only` → 仅 `is_win` 推。
- 时机：路径 A（大奖即时）/ 路径 B（次日汇总）。
- 执行：Notifier 渠道插件 → Bark/飞书/邮箱 → 写 `notification_logs`。

---

## 8. 通知推送设计

### 8.1 渠道（可插拔，按用户配置，无主备）
| 渠道 | 说明 |
|---|---|
| **Bark** | iOS 原生推送，配置最简（key + URL），契合 iOS 优先 |
| 飞书 | 群机器人 webhook（复用用户现有飞书基础设施） |
| **邮箱** | 系统统一发件：用户只填**收件地址**，发件 SMTP 由运维方在后台配置（家庭 NAS 小圈子场景，受邀用户免折腾 SMTP）；邮件从统一发件地址发出 |
| 未来 iOS App | APNs app 内推送（后续项目） |

每用户在 `notification_channels` 配置自己的渠道（Bark/飞书存 webhook/key，邮箱只存收件地址）；可配多个，无主备关系。渠道配置（webhook/key/收件地址）**加密存储**（Fernet，key 来自 `CRYPTO_KEY`），不明文落库或入日志。`key_version` 字段支持密钥轮换时逐条 re-encrypt。

**多版本密钥（轮换）**：env 形如 `CRYPTO_KEY_V1=...`（解密旧数据）+ `CRYPTO_KEY_V2=...`（新写入用），轮换时后台任务逐条 `V1→V2` re-encrypt。`admin_audit_logs` 的 old_values/new_values 若含渠道配置字段须**脱敏**（不存明文密钥/webhook）。

**管理员告警兜底**：当邮件渠道不可用时，admin 告警须走 Bark 推送到管理员手机，避免"告警本身依赖坏掉的邮件"这一循环依赖。系统在启动时校验：若启用 email 渠道，则必须同时配置 admin 的 Bark fallback。

### 8.2 策略与时机
- **推送范围**：只推送该用户追投（号码池里有启用注）的彩种，未追投的彩种不推送。
- **策略**：每用户 × 每彩种独立配置（`every` 每期推 / `win_only` 仅中奖推）。
- **时机（分层）**：
  - 路径 A：开奖当晚，仅"追投该彩种且命中一二等奖"的用户 → 即时简讯（高优先级推送）。
  - 路径 B：次日 07:00（可配）→ 按策略推详情汇总（仅追投彩种）。
  - 周/月报：每周日 09:00 盈亏汇总、每月 1 日 09:00 月度汇总（仅追投彩种；若该周期无活动则静默跳过，不推空消息）。
- **免打扰（DND）**：用户可配置时段（默认 22:00–07:00）。DND 期间：路径 B 和周月报**顺延**到 DND 结束；路径 A（命中一二等奖）**可破例**立即推送（大奖不容耽搁）。

### 8.3 推送内容与模板
两路径各有模板（在设置页「推送模板预览」确认）：

- **路径 A 大奖即时简讯**：标题 `🎉 恭喜中奖！{彩种} {奖级}`，正文「第 {期号} 期开奖，你追投的号码命中 {奖级}，奖金 {金额}。请在 60 天内兑奖；单注 ≥1 万元将代扣 20% 偶然所得税。」
- **路径 B 次日汇总**：标题 `兑奖了吗 · {日期} 核对汇总`，正文「本期共核对 {N} 个追投彩种，中奖 {M} 笔：{逐笔 彩种 奖级 金额}；其余 {X} 个未中奖。点击查看明细。」

通用要素：期号、彩种、开奖号码（红/蓝色彩点缀）、我的号码、命中情况（红/蓝球数）、奖级、固定档奖金（浮动标"待官方派奖"）、数据来源 + 获取时间、"以官方开奖为准"声明、"理性购彩"提示。

---

## 9. 合规边界（红线）

### 9.1 法律依据
中国彩票为国家特许发行（《彩票管理条例》）。三条红线：① 擅自网售/代购/收付资金；② 以预测、分析、推荐、走势等方式引导、诱使购彩；③ 对随机事件做"预测"涉嫌虚假宣传（《广告法》）。

### 9.2 系统遵守
- **坚决不做**：号码预测、AI 推荐、必中、走势图选号、冷热号推荐。
- **走势图（合规版，常规功能）**：仅历史号码综合分布（开出标圆 + 遗漏次数）+ 近 N 期频次（纯数字、不排序、不标冷热、不推荐）+ 显著常驻声明"彩票为独立随机事件，历史不代表未来，仅供历史回顾"。走势作为常规 tab，不再区分公开版。
- **提醒**：仅开奖信息提醒（"今晚 X 开奖"，纯中性信息）；**不含**购彩行为提醒。
- **代销点查询**：「附近代销点」为便民工具（用户主动定位查询官方代销点、区分福彩/体彩、点击打开地图导航），不代购/不预测/不引导购彩；措辞中性，不作系统购彩推送。
- **理性提示**：全系统常驻"理性购彩 量力而行"。
- **数据声明**：所有结果标注"以官方开奖为准"+ 数据来源 + 获取时间。

### 9.3 残余风险
走势图（即使合规版）仍存在被认定"引导购彩"的可能。规避方式：走势页**默认不展示选号面板**，仅历史回顾 + 强随机性声明常驻 + 不做冷热/排序/选号推荐；用户若需选号，须点击"我要选号"按钮并在弹窗/抽屉中确认"我知道历史走势不影响中奖概率，仅基于个人意愿自选"后，才展开选号面板。选号面板不基于走势做任何推荐。

---

## 10. 错误处理与可靠性

| 场景 | 策略 |
|---|---|
| 主数据源失败 | 自动切备源；都失败 → 用本地缓存最近一期 + 健康面板标红 + 告警 admin |
| 双源数据不一致 | **拒绝入库**（verified=false）+ 告警 + 人工介入——宁可推迟推送也不推错号码 |
| 一源有一源无（时间差） | grace window 5min → 仍只有一源则单源入库 `single_source=true` + 健康面板标黄 |
| 重复触发/幂等 | `draw_results` 唯一约束(lottery_code+draw_no)；`pending_comparisons` 唯一约束保证只比对一次；推送日志防重发 |
| 开奖结果官方更正 | 写 `draw_corrections`，`draw_results.version` 递增，重新触发比对，保留历史 comparisons |
| 浮动奖金额未公布 | 首次比对标 `prize_amount=null`/"待官方派奖"；后续每日轮询回填 |
| 坏注单/格式异常 | 隔离该注，不影响其他注的比对；记录错误日志 |
| 推送失败 | 指数退避重试 3 次；用户配多渠道则降级到其他渠道；全失败告警 |
| 邮件渠道不可用 | 用户侧静默失败 + 健康面板标红；admin 告警走 Bark fallback |
| DB 锁定/并发 | SQLite WAL 模式 + 连接重试；APScheduler 持久化 job store |
| 时区 | 全程 Asia/Shanghai；启动时校验系统时区 |
| 密钥安全 | API Key 仅环境变量/加密配置，不进库不进日志；用户渠道配置加密存储并支持 key_version 轮换 |

**可靠性原则**：开奖号码是核心数据，**准确性优先于及时性**——拿不准就不推、不入库、告警等人工。

---

## 11. 测试策略

| 层级 | 重点 | 方法 |
|---|---|---|
| **领域层单测（最高优先）** | 7 彩种 × 各玩法比对逻辑、每个奖级至少一个命中用例、边界(全中/全不中/复式展开上限)、QXC hybrid、DLT append | 用**真实历史开奖数据**构造用例；覆盖率 95%+ |
| 策略注册 | 新彩种/玩法正确路由到对应 CompareStrategy；`number_style` 覆盖 partition/positional/hybrid | 表驱动测试 |
| 数据源适配 | mock MXNZP/聚合数据响应，测双源切换、交叉校验、失败降级、部分源 grace window | 不打真实 API |
| 核心闭环 | outbox 触发比对、幂等、浮奖回填、结果更正重比对、坏注隔离 | 集成测试 |
| 推送 | mock 渠道 HTTP，测策略决策、时机、重试、多渠道降级、DND 顺延/破例 | |
| 调度幂等 | 同期重复触发不重复比对/推送；APScheduler 重启后 job 不丢失 | 集成测试 |
| API/Web | 关键流程（登录/注册限流、号码 CRUD、查询、设置）、admin 操作审计 | FastAPI TestClient + E2E |
| 安全 | 邀请码防爆破、IDOR（prize_claims）、JWT/Crypto key rotation、CSV 导入安全 | 单元 + 集成 |

**TDD 落点**：比对逻辑（领域层）必须先写测试再实现——最该被测试保护，也最易用真实开奖核对验证。

---

## 12. 前端设计（Prototype 先行 via Open Design）

### 12.1 流程
1. 用 NAS 上的 **Open Design**（`http://<NAS_IP>:7457`）先设计 prototype；
2. 选 Skill（仪表盘/号码/统计用 `dashboard`，移动端用 `mobile-app`）+ 选 Design System（用户在 OD 内预览自选）；
3. 逐屏生成 HTML 原型，迭代定稿；
4. prototype 定稿后，实现阶段照原型写 Vue3 + ECharts。

### 12.2 页面清单（信息架构）
| # | 页面 | 核心内容 |
|---|---|---|
| 1 | 登录/注册 | 邀请码注册（6 位码·MVP）+ 账号密码登录（校验：邀请码格式/密码 ≥8 位/确认一致）+ 第三方登录占位（Apple/微信·「公开版可用」·架构预留 `oauth_provider`/`oauth_id`·OAuth 待公开版启用）+ 主题切换 + 理性购彩提示 |
| 2 | 仪表盘 | 开奖概览（各彩种期号+号码+日期）、**开奖日历**（按我的号码启用彩种过滤·开奖前预告提醒）、**附近代销点**（📍 定位·区分福彩/体彩·按追投类型过滤·点击打开地图导航·便民查询；数据源需后续接入高德/百度地图或官方 POI，MVP 可 mock）、我的命中（多彩种混合表）、待兑奖（"已领取"绿色突出按钮）、盈亏速览（按时段筛选·含公益贡献卡：按各彩种 `welfare_rate` 计算）、右上角用户身份区、理性提示常驻 |
| 3 | 我的号码 | 号码盘点击选号（按玩法：单式/复式/胆拖/单选/直选/组选）、机选一注（随机）、批量导入、每注倍投（2-99）、大乐透追加、左右布局（号码池为主） |
| 4 | 开奖查询 | 彩种切换（7 种 pill）+ 期号选择（下拉/前后翻页/「双源校验通过」徽章）、开奖号码按规则渲染（分区红蓝球｜按位数字方块带位标签｜七星彩混合型前区6位0-9+后区1位0-14）、奖池与销售额 meta、我的比对详情（仅比对追投彩种·命中标绿·奖级奖金·未追投空状态）、各奖级中奖情况（注数+单注奖金·浮动奖标注） |
| 5 | 中奖记录 | 全局筛选条（时段本月/本年/全部/自定义·彩种·兑奖状态）+ 4 卡统计概览（累计/待兑/已领/过期·金额与笔数·随筛选联动）、中奖记录卡片（奖级·金额·我的号码·兑奖状态徽章·有效期倒计时·已领取操作）、临近过期标红（≤15天）、大额税务提示（≥1万元含实得金额）、兑奖状态与仪表盘待兑奖联动（同一 prize_claims）、底部精简兑奖须知、金额用分存储 |
| 6 | 我的统计 | 全局筛选（时段本月/本年/全部/自定义·彩种，默认本月）、盈亏总览（投入/中奖/净盈亏/中奖率/公益贡献·随筛选联动；投入按 `tickets.cost` 计算，公益按各彩种 `welfare_rate` 计算；**浮动奖未回填时显示"待官方派奖"，不把 null 计成 0**——否则用户看到巨额虚假亏损）、中奖等级双饼图（笔数+金额占比）、月度投入与中奖双柱图（柱顶标注金额·投入按自身波动·独立全期不随筛选）、金额用分存储 |
| 7 | 开奖走势 | 综合分布图（期从远到近·号码按列·开出标圆·遗漏次数同区独立·表头sticky·cell自适应·彩种筛选默认双色球·期数30/50/100+自定义）+ **选号面板默认折叠**（用户点击"我要选号"并在弹窗/抽屉确认"我知道历史走势不影响中奖概率，仅基于个人意愿自选"后展开；玩法选择·号码盘按玩法·机选一注/机选自定义复式胆拖红蓝数·多注队列·队列内倍投·批量推送号码池）+ 出现频次（升序·非频次排序）+ 随机性强声明（历史回顾·不影响概率·不构成选号建议） |
| 8 | 设置 | 推送渠道（Bark/飞书/邮箱·邮箱统一发件用户只填收件·渠道加密存储）、每彩种推送策略（每期/仅中奖）、推送时机（总开关+大奖即时简讯+次日汇总时间+免打扰时段；大奖即时可破例免打扰）、推送模板预览（大奖即时/次日汇总文案可确认）、偏好（外观浅/自动/深联动·新号码默认启用） |
| 9 | 后台管理 | SMTP 发件（服务商下拉 QQ/网易/Gmail/自定义·选中自动填服务器/端口/加密·只填账号+授权码·提供"发送测试邮件"）、用户管理（邀请码注册 MVP + 商业化预留手机号/开放注册·备注列·角色/启用）、彩种配置（启用/开奖日/双源）、系统健康（数据源双源容灾状态 + 比对/推送健康 + 调度任务**结构化可配**：开奖后 N 分钟 / 每日 HH:MM / 固定触发 + 告警）、推送日志（日期列 + 6 维筛选：日期/用户/彩种/渠道/类型/状态，按列序；保留 90 天）、**管理员操作审计** |

### 12.3 移动端 / iOS 优先
- 当前 Web 响应式（iOS Safari 友好）；未来 iOS 原生 App 复用 REST API。
- Bark 推送天然契合 iOS。
- **响应式断点**：320 / 375 / 768 / 1024 / 1440。≤768 为移动布局。
- **移动端导航（≤768px）**：底部 tab bar（4 高频：仪表盘 / 我的号码 / 开奖查询 / 我的）+ 其余页（统计/走势/设置/后台）入"更多"抽屉。桌面 ≥1024px 保留 240px sidebar。
- Prototype 阶段可用 `mobile-app` Skill 出 iOS 外框原型。

### 12.4 设计规范（A11y / 状态 / 优先级 / 确认门）

> 完整 token 见 [`docs/designs/DESIGN.md`](../designs/DESIGN.md)（色板/字号/间距/圆角/动效单一源，从 9 页 prototype 反向提取）。

**交互状态系统（统一 `<State type=loading|empty|error>` 组件）**：

| 页面 | LOADING | EMPTY | ERROR | PARTIAL |
|---|---|---|---|---|
| dashboard | 骨架屏 | 日历空"添加追投彩种" | 拉取失败+重试 | 单源标黄/待派奖 |
| my-numbers | spinner | "号码池为空，去选一注"+CTA | 保存失败+重试 | — |
| draw-query | 骨架 | "该彩种未追投" | 拉取失败+重试 | 单源/校验中 |
| win-records | 骨架 | "本期暂无中奖，理性购彩" | — | — |
| my-stats | 骨架 | "先添加追投" | — | 浮奖待派奖(非0) |
| settings | inline | — | SMTP 测试失败 | — |
| admin | inline | 空列表 | 拉取失败+重试 | — |
| trend | 骨架 | "该彩种暂无历史" | 拉取失败+重试 | — |
| login | 按钮态 | — | 邀请码/密码错误 | — |

空状态要素：温暖文案 + 主操作 CTA + 上下文（**禁止** "No data"/"暂无数据"裸文案）。

**A11y 基线（D9 强制纳入 MVP）**：
- ARIA landmark：每页 `<header><nav><main><aside>`（读屏可跳转）
- 交互元素一律 `<button>`/`<a>`，**禁 `div onclick`**（键盘/读屏可达）
- 图标按钮 `aria-label`；表格/列表用语义标签
- 系统焦点环（`:focus-visible`，不禁用）；Tab 顺序合理
- 触控靶 ≥44×44px；对比度 AA（正文 ≥4.5:1）
- 理性/随机性声明不依赖纯颜色区分（加图标/文字）

**走势确认门视觉（D8/D4）**：走势页选号默认折叠；用户点"我要选号"→**右侧抽屉（drawer）**滑出，顶部确认句"我知道历史走势不影响中奖概率，仅基于个人意愿自选"，确认后展开选号面板；抽屉契合移动 thumb reach 且保留走势上下文。

**dashboard 首屏优先级（D5）**：待兑奖 > 我的命中 > 盈亏速览 > 开奖概览 > 开奖日历/附近代销点（次屏）。用户来 dashboard 最关心"我中奖了吗/欠兑奖"，置顶。

**新增组件**须对齐 [`docs/designs/DESIGN.md`](../designs/DESIGN.md) 的 token 与组件词汇，不引入新色/新字号/新圆角。

---

## 13. 分阶段交付计划

### Phase 1.0 — 基础设施 bootstrap（严格依赖顺序）

> 实现必须按此顺序，避免 Alembic/jobstore 鸡生蛋、crypto 后于 channels 写入等坑：

1. **Alembic 初始化**（迁移基建先行）
2. **Schema 迁移 #1**（所有表含 `apscheduler_jobs`，**不让 jobstore 运行时 auto-create**，否则与 Alembic stamp 冲突致 schema drift）
3. **Crypto 服务**（多版本 key env + `key_version`）
4. **种子 `lottery_types`**（7 彩种 `spec_json`，hydration 时 pydantic 校验）
5. **领域层**（LotterySpec / NumberRange / PositionalDigits / Entry / PrizeTier / CompareStrategy / `expand()` — 零依赖，可并行单测）
6. **Repository**（构造函数注入 user_id，IDOR-safe）
7. **比对引擎 + outbox claim**（claim SQL + comparisons 唯一约束）
8. **调度器**（`SQLAlchemyJobStore` 共享 engine、全局 Asia/Shanghai、`coalesce`/`max_instances`、启动双路 backfill）
9. **抓取**（双源 + 退避抖动 + 期号映射 + verified 恢复）
10. **推送**（路径 A **异步**、渠道插件、Bark admin fallback、DND defer）
11. **认证**（httpOnly cookie + `/auth/csrf` + CORS）
12. **Web UI**（按 prototype + A11y + 响应式断点 + 空/错误状态）
13. **冒烟**：`python -m app.cli ssq` 端到端跑通

### Phase 1 — MVP（核心闭环 + 单式）
- 用户体系（邀请制、认证、隔离、邀请码防爆破）
- 7 大彩种领域层（LotterySpec + 单式比对策略 + 奖级规则；QXC hybrid；DLT append）
- 号码管理（单式 CRUD、批量导入、复式展开上限校验）
- 开奖获取（双源 + 交叉校验 + 部分源 grace window + 官方更正）
- 比对引擎 + comparisons（outbox 触发、比对一次、浮奖回填）
- 通知推送（Bark / 飞书 / 邮箱，分层时机，DND，按用户×彩种策略，Bark admin fallback）
- 统计（中奖历史、盈亏按真实 cost、命中率定义清晰、等级分布、公益贡献按彩种 welfare_rate）
- 提醒（开奖信息、兑奖期限 60 天倒计时、税务）
- 信息日历、走势图（合规版 + 选号确认门）
- 运维管理（健康面板含比对/推送、admin 后台、admin 审计、导出、推送日志保留 90 天）
- 周月报、理性提示
- Web UI（按 prototype 实现，含 A11y 基线、响应式断点、空状态）
- 部署：Alembic、SQLite WAL、APScheduler job store、每日备份、启动校验、healthcheck

### Phase 2 — 玩法扩展
- 复式 / 胆拖（展开比对）
- 组选（组三/组六）

### Phase 3 — 扩展与优化
- 奖池余额（若数据源支持）
- 规模化准备（DB 迁 PG、worker 拆分、异步推送）按需
- iOS 原生 App（独立项目）

---

## 14. 风险与注意事项

| 风险 | 应对 |
|---|---|
| API 服务中断/变更 | 双源 + 本地缓存 + 版本化适配器 + 变更告警；单源时标黄提示 |
| 个人维护 API（MXNZP）停服 | 聚合数据商业级备源 |
| 数据延迟/错误 | 次日汇总 + 多源交叉校验 + 单源 grace window + 准确性优先 |
| 合规（走势图/代销点） | 历史回顾 + 强随机性声明 + 不排序/不冷热/不推荐；走势选号默认折叠+确认门；代销点仅便民查询不引导购彩 |
| 数据准确性 | 自研奖级判定 + 固定档配置 + "以官方为准"声明 + 官方更正流程 |
| 数据丢失 | SQLite WAL + 每日备份 + 30 天保留 |

---

## 15. 未决 / 待 prototype 确认
- 各页面 prototype 已定的，实现阶段细化交互细节（loading/empty/error 状态、响应式断点）。
- 奖池余额功能依赖数据源是否提供，Phase 1 视情况实现或后置。
- 附近代销点 POI 数据源（高德/百度地图 API 或官方数据）待接入，MVP 可用 mock。

---

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 1 | clean | HOLD SCOPE; 11 sections; 23 tasks; all decisions resolved |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | skipped | Codex CLI not installed; Claude subagent used (CEO + ENG) |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | clean | FULL_REVIEW; 4 sections; 21 tasks; outside voice 25 findings folded; 0 critical gaps after fixes |
| Design Review | `/plan-design-review` | UI/UX gaps | 1 | clean | 7 passes; 6→9/10; DESIGN.md + 状态系统 + A11y 基线 + 确认门 + 移动导航; 8 tasks |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | not run |

- **VERDICT**: CEO + ENG + DESIGN CLEARED — ready to implement.
- **Unresolved decisions status**: `NO UNRESOLVED DECISIONS`
