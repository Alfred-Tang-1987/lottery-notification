# 数据源健康告警 + 启动回填 QPS 间隔 设计（plan-11）

日期：2026-09-15
状态：已与作者确认（brainstorming 会话）；2026-09-15 autoplan 四轮审查（CEO/Design/DX/Eng）
修订同步——真实 SLA、送达契约、状态机诚实性、脱敏闭环、QPS 常量单一真值源（详见 plan
Review record 与各 accepted obligations）
事故背景：2026-09-15 NAS 生产事故——容器 DNS 上游被钉为不可达 IPv6，mxnzp 抓取连续
失败 9 天（每天 630 条 `source_fetch_failed` 日志）而**零用户可见告警**，开奖漏抓 6 期、
3 注中奖漏通知。同日重启容器触发 `run_startup_backfill` QPS 限流风暴（启动即白撞一轮
mxnzp code=101）。

## 目标

1. **fetch 持续失败 → admin Bark 告警**，恢复 → 「已恢复」通知；告警状态
   持久化（容器重启不丢计时）；告警发送本身失败则下轮重试直到送达。
2. **`ApiSourceHealth` 表从「有读无写」变为实时写入**，admin 面板可见源健康。
3. **启动回填加彩种间间隔**，消除重启时的 QPS 限流风暴。

**真实 SLA（autoplan M4）**：抓取只发生在开奖日 21:30–01:00 的 path_a 窗口——**白天无
抓取、不检测**。故障发现时机 = 下一抓取窗口 + `SOURCE_HEALTH_ALERT_AFTER_MINUTES`
（默认 30 分钟）；白天发生的故障最坏次日 21:30+30min 才发现。「30 分钟级告警」仅在
晚间窗口内成立。30min 阈值推导（M5）：21:30 首败 → 22:00 告警，早于 01:00 末轮与
次日 07:00 汇总，留有反应时间；阈值可调（settings），无需改代码重建镜像。

## 非目标

- 不做多源告警通道（email 等）——只用既有 `ADMIN_BARK_KEY` Bark（TODOS P3 候选）。
- 不做适配器级全局限速器——QPS 已是 `TransientLookupError` + 退避 ≥1s 自愈（L-20260726），
  仅补启动回填的间隔缺口。
- 不做 per-lottery 粒度告警——源（source）粒度足够（单源部署下源挂 = 全彩种挂）。
- 不改 fetch 重试/退避策略本身。
- 不做恢复补抓（`[down_since, now]` 缺口回补）与结果口径告警（应开奖日无 verified
  结果）——属 plan-11b（TODOS P2）；本 plan 只修「发现」不修「补回」。
- 不做白天探测性抓取（配额消耗；晚间才是承诺兑现期）。

## 一、数据源健康告警

### 1.1 数据模型（`app/models/health.py` + 1 条 Alembic 迁移）

`ApiSourceHealth` 现有：`source`(PK) / `last_success_at` / `status`(ok|degraded|down|unknown)
/ `error` / `created_at`。新增两列：

| 列 | 类型 | 语义 |
|---|---|---|
| `down_since` | datetime \| None | 本次故障 episode 起点（naive UTC，与 `last_success_at` 同表示）；恢复通知送达后清 NULL；recovering 期间再故障时**重置为新 episode 起点**（autoplan E5） |
| `alerted` | str，默认 `'none'` | 告警状态机：`none`（未告警/本次 episode 未送达）→ `alerted`（故障告警已送达）→ `recovering`（恢复通知待送达）→ `none`。**`alerted` 的语义是「已送达」——未送达不得标 alerted**（状态不说谎，F6/E5） |

迁移挂 `d1_draw_costs` 之后（真实 head）；迁移测试含单 head 守卫（双 head 会让容器
启动的 `alembic upgrade head` 直接失败，autoplan E1）。

### 1.2 写入语义（`FetchService.fetch_and_store` → `record_source_health`）

`fetch_and_store` 内 `_try_fetch` 返回三态 outcome（ok / down / permanent），据此按源
upsert 健康表；**grace 重抓结果同样落表**（M7：grace 内恢复的源不等下一周期才转 ok）。
**独立短事务**（健康写失败不得回滚抓取数据）：包 try/except 记日志，绝不阻断抓取主流程。

**「源健康」定义**：HTTP/API 层成功返回（含业务上的「未开奖」`None`）= 健康（ok）；抛异常
（网络/DNS/限流重试耗尽）= 运行故障（down）；`PermanentLookupError`（key 未配置 / schema
契约变更）= 配置态（permanent → `status=degraded`）。

| 源抓取结果 | 健康表动作 |
|---|---|
| ok（含未开奖） | `status=ok`、`last_success_at=now`、`error=None`；`alerted` 非 `none` → 转 `recovering` 且**保留 `down_since`**（供恢复通知算时长）；`alerted=='none'` 且故障时长 ≥ 阈值 → 同样转 `recovering` 补发恢复通知（M11：长故障期间告警从未送达——通道同挂，DNS 教训——恢复时不得静默清零，否则 9 天故障自愈 = 零通知）；`alerted=='none'` 且故障 < 阈值 → 清 `down_since`（未达告警阈值，无通知义务） |
| 失败（down） | `status=down`、`error=脱敏摘要`、`down_since` 为空则置 `now`（非空保留——故障起点不刷新）；`alerted=='recovering'` → 翻 `'none'` 且 **`down_since` 重置为 now**（E5：新 episode——翻回 `alerted` 会让 M11 路径的行显示「已通知」而无人收到；恢复通知自然取消，重告警由评估侧送达门控决定） |
| permanent（配置态） | `status=degraded`、`error=脱敏摘要`、清 `down_since`、清 `alerted='none'`（F11+E6：配置态终结运行故障 episode——不再按运行故障计时/告警，也不欠恢复通知；`degraded` 由此成为真实写路径） |

**error 脱敏（M13+E7）**：juhe 把 api key 放 query（`juhe.py:27`），`raise_for_status`
异常消息含完整 URL——落表前 `sanitize_error` 把 `key=/app_id=/app_secret=/token=`
参数值替换 `[REDACTED]`；**同一脱敏器也用于 `_fetch_with_backoff` 的日志输出**
（E7：容器日志同在家族 NAS 上，是与落表同级的泄露面）。

### 1.3 评估与告警状态机（`evaluate_source_alerts`，services/source_health.py）

**评估挂载点**：`_path_a_tick` 尾部（21:30–01:00 每 15 分钟自然评估）+ `run_startup_backfill`
尾部（开机即评估，覆盖白天/停机场景；两个 key 均未配时 backfill 整体提前返回，评估随之
跳过）。

**触发条件**：`status=down` 且 `now - down_since ≥ settings.source_health_alert_after_minutes`
（默认 30 分钟，F19 可调）。时间窗实现与 tick 次数解耦：持久、不怕重启、白天停机后开机
也能正确计时。

**sender 惰性工厂（E2/E4）**：签名 `evaluate_source_alerts(engine, sender_factory, now)`。
评估器先在短 session 内读出快照并决策出全部待发送项；**没有待发送项就直接返回，工厂
根本不被调用**——不新建 `BarkChannel`/`httpx.Client`（每 tick 新建未关闭 client 是慢
泄漏），也不碰 `get_settings()`。有待发送项时才调 `sender_factory()`（生产实现
`admin_alert_sender_factory`：`SOURCE_HEALTH_ALERTS_ENABLED=false` → None（独立开关，
F18：与密码重置 admin 告警不共用一个总开关）；开关开则 `build_admin_alert()`）。

**两阶段纪律（M1，pool_size=1）**：短 session 读+决策后关闭 → session 外发 HTTP →
短 session 守卫重读后落转移。绝不在持有唯一连接的 session 内做 httpx 调用（DNS 故障下
Bark 挂 10s 超时，同期其他 job/请求撞 busy_timeout——jobs.py:276-278 两次实测事故同型）。
落转移前重读校验状态未变；读与落之间被并发 fetch 改写则放弃本轮转移、下轮重评
（已知取舍 L2：此时已送达的告警下轮可能重发一次——duplicate > silence，deploy.md 写明）。

**状态机转移**（每次评估对每个源执行）：

| 当前 `alerted` | 条件 | 动作 |
|---|---|---|
| `none` | down ≥ 阈值 | 发 Bark「开奖抓取持续失败」（源 / 已持续时长 / 备源影响 / 最近 error 摘要 / 处理指引）；**送达成功** → `alerted`；发送异常 → 保持 `none`（下轮重试） |
| `alerted` | 仍 down | 不发（防每 15 分钟轰炸） |
| `recovering` | （待送恢复通知，status=ok） | 发 Bark「已恢复」（源 / 故障时长 / 缺口覆盖声明：path_a 轮询 + 启动回填最近 2 天，更长缺口请人工确认）；送达成功 → `none` 并清 `down_since`；发送异常 → 保持 `recovering`（下轮重试） |
| 任意 | sender 工厂返回 None | **不发送也不转移**（F6：未送达标 `alerted` 是状态说谎——面板会显示「已通知」而无人收到）；每次评估 warning 一次；配好 key/开关后下一轮自然补发 |

注 1：`alerted → recovering` 的转移发生在**写入侧**（1.2），评估侧只负责送出恢复通知后
回 `none`。即「告警过的故障恢复后，必须成功送出一条恢复通知才回到初始态」——DNS 教训：
故障期告警通道大概率同时挂，告警与恢复通知都必须重试到送达。

注 2：告警体必含备源影响（D5）：备源全 ok →「备用源正常，开奖未受影响」；有 down →
「双源同时故障」；unknown/degraded → 如实列状态 + 单点风险。单源故障的告警读起来不得
像已经漏开奖（告警疲劳最快路径）。

### 1.4 告警通道共享（小重构 + 送达契约）

`build_admin_alert` 从 `app/api/auth.py` 挪到 `app/notifications/admin_alert.py`，auth 与
scheduler 共同引用。**不引入 Notifier/渠道表体系**——admin 告警是运维兜底，不走用户
通知管线（无 NotificationLog、无 DND）。

**送达契约（M2/F8）**：`BarkChannel.send` 吞掉一切失败返回 `SendResult` 而非抛异常
（bark.py:49-50）——若忽略返回值，「送达成功才转移」退化为「尝试即转移」。故返回的
callable 在 `status != SENT` 时抛 `RuntimeError`（error 为 None 时兜底文案）。
`password_reset_service.py` 既有 try/except 兼容该 raise（失败从静默变留痕，纯改进）。

url 走 `settings.admin_bark_url`（默认 `https://api.day.app`，F20：自建 Bark 是 NAS 真实
场景；与 main.py `admin_bark_config` 同源——单一真源）。

### 1.5 admin 面板

`GET /admin/health` 响应扩展为 `{source, status, alerted, error, last_success_at, down_since}`：
- 时间戳为**显式 UTC**（naive UTC 落库值 + `'Z'` 后缀，D-3：否则前端按浏览器本地时区
  解析偏差 8 小时）；
- `error` **全文返回**（E9：截断在前端——后端截断会让 title 悬浮也只有截断版）；
- 前端展示**时长**为主（「已故障 X 分钟 / 最后成功 X 前」，时区免疫）+ 状态色 pill
  （down/degraded/unknown 补齐）+ 告警标签（已通知 / 恢复待通知）+ error 摘要（80 字符
  截断 + '…'，title 悬浮全文）；
- **时长文案按 `status !== 'ok'` 门控**（E5：recovering 行 status 已是 ok，不得显示
  「已故障」——面板不说谎）。

### 1.6 时间语义纪律（CLAUDE.md datetime 对齐）

`last_success_at` / `down_since` / 评估用 `now` 全部 naive UTC（`datetime.now(timezone.utc)
.replace(tzinfo=None)`，与 `TimestampMixin.created_at` 同时区同数值），DB 内比较不做
naive/aware 混比；API 出栈时加 `'Z'` 显式标注。

## 二、启动回填 QPS 间隔

`app/scheduler/backfill.py` `run_startup_backfill` 第 4 步（missed-draw 检查循环）：
**对实际发起抓取的彩种**，第二个起抓取前 `time.sleep(_INTER_LOTTERY_INTERVAL)`
（missed 检查命中的彩种才 fetch，间隔只加在真实请求之间，不为跳过的彩种白等）——
镜像 `_path_a_tick` 既有做法（L-20260726T013000Z 同源理由：mxnzp 免费账号 QPS=1，
连续请求触发 code=101 白耗重试）。

- 常量**单一真值源**（M6+E10）：backfill 直接 `from app.scheduler.jobs import
  _INTER_LOTTERY_INTERVAL`（无循环依赖——jobs 不 import backfill）；`_backfill_history`
  内残留的 `time.sleep(1.2)` 同步换用该常量。
- `_path_a_tick` 已有间隔 ✓ 不动。

## 三、测试策略

内存 SQLite + mock 源 + stub sender 工厂，无真实网络：

1. **迁移**：`alembic upgrade head` 后断言两列（`alerted` NOT NULL + DDL 带引号默认值
   `"'none'"` 子串 + 缺省插入回读）+ **单 head 守卫**（双 head 在 CI RED 而非容器启动失败）。
2. **写表语义**：ok/down/permanent 三路 + 故障起点不刷新 + 长故障零送达 → recovering
   补发（M11）+ recovering→down 翻 none 重置 episode（E5）+ permanent 清 alerted（E6）+
   error 脱敏（M13）。
3. **状态机**（直接调 `evaluate_source_alerts`，`_Recorder` stub 工厂）：
   - down 不足阈值 → 不告警（且**工厂不被调用**，E4）；
   - down ≥ 阈值 → 告警一次、`alerted`；继续 down 不重复告警；
   - 恢复 → `recovering`，评估送出恢复通知 → `none` 清 `down_since`；
   - **发送抛异常 → 状态不变，下轮重试后送达才转移**（DNS 教训回归）；
   - 工厂返回 None → 不发送**也不转移**（F6）+ warning 一次；
   - 发送期间不持有 DB 连接（pool_size=1，`checkedout()==0` 断言，M1）；
   - 告警体备源影响三分支（ok / degraded / 双 down）与恢复缺口声明；
   - recovering→down 达阈值重告警（E5 评估侧）。
4. **通道**：`build_admin_alert` 未配 key → None；FAILED（非异常）→ raise（M2）；
   url 来自 settings（F20 回归）。
5. **抓取接线**：健康写失败不阻断抓取（E11/M8.1）；日志脱敏（E7）；双源 + grace 落表（M7）。
6. **评估挂载**：`_path_a_tick` / `run_startup_backfill` 尾部调用评估（spy 断言）；
   `SOURCE_HEALTH_ALERTS_ENABLED=false` → 工厂返回 None。
7. **QPS 间隔**：startup backfill 多彩种场景 monkeypatch `time.sleep`，断言 N 彩种
   N-1 次间隔（首彩种不 sleep）。
8. **面板**：API 键齐 + `'Z'` 后缀 + 空值边界 + error 全文；vitest 健康卡（down 全要素 /
   空态回归 / ok 行不显示「已故障」/ 长 error 截断+title 全文）。
9. 全量回归：现有 fetch/scheduler/admin 套件不受影响（健康写入独立短事务不改抓取行为）。

## 四、部署注意

- 新增 1 条 Alembic 迁移（`t11_source_health_alert`，挂在 `d1_draw_costs` 之后）；容器
  启动自动 `alembic upgrade head`（单 head 守卫测试防错链）。
- **存量升级行 `down_since` 为 NULL**：部署后需经过一个完整抓取周期才开始记录故障起点
  （升级后第一天的故障计时从下一次抓取起算，属已知冷启动窗口）。
- 配置四项：`ADMIN_BARK_KEY`（通道）、`SOURCE_HEALTH_ALERTS_ENABLED`（独立开关，默认
  true，不影响密码重置 admin 通知）、`SOURCE_HEALTH_ALERT_AFTER_MINUTES`（阈值，默认
  30）、`ADMIN_BARK_URL`（自建 Bark 服务端时覆盖）。
- NAS 端 `docker-compose.override.yml`（dns 固定）已就位，升级按 docs/deploy.md 常规流程
  `git pull && docker compose up -d --build`；deploy.md「数据源健康告警」小节含机制 /
  检测时机 / 配置 / 面板 curl / 手工冒烟 runbook（阈值置 0 直接验证告警链路）。
- 上线后首个 tick 即开始写健康表，admin 面板 `/admin/health` 立即可见。
