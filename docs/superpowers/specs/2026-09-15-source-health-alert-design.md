# 数据源健康告警 + 启动回填 QPS 间隔 设计（plan-11）

日期：2026-09-15
状态：已与作者确认（brainstorming 会话）
事故背景：2026-09-15 NAS 生产事故——容器 DNS 上游被钉为不可达 IPv6，mxnzp 抓取连续
失败 9 天（每天 630 条 `source_fetch_failed` 日志）而**零用户可见告警**，开奖漏抓 6 期、
3 注中奖漏通知。同日重启容器触发 `run_startup_backfill` QPS 限流风暴（启动即白撞一轮
mxnzp code=101）。

## 目标

1. **fetch 持续失败 → admin Bark 告警**（30 分钟级），恢复 → 「已恢复」通知；告警状态
   持久化（容器重启不丢计时）；告警发送本身失败则下轮重试直到送达。
2. **`ApiSourceHealth` 表从「有读无写」变为实时写入**，admin 面板可见源健康。
3. **启动回填加彩种间间隔**，消除重启时的 QPS 限流风暴。

## 非目标

- 不做多源告警通道（email 等）——只用既有 `ADMIN_BARK_KEY` Bark。
- 不做适配器级全局限速器——QPS 已是 `TransientLookupError` + 退避 ≥1s 自愈（L-20260726），
  仅补启动回填的间隔缺口。
- 不做 per-lottery 粒度告警——源（source）粒度足够（单源部署下源挂 = 全彩种挂）。
- 不改 fetch 重试/退避策略本身。

## 一、数据源健康告警

### 1.1 数据模型（`app/models/health.py` + 1 条 Alembic 迁移）

`ApiSourceHealth` 现有：`source`(PK) / `last_success_at` / `status`(ok|degraded|down|unknown)
/ `error` / `created_at`。新增两列：

| 列 | 类型 | 语义 |
|---|---|---|
| `down_since` | datetime \| None | 本次故障起点（naive UTC，与 `last_success_at` 同表示）；恢复正常时清 NULL |
| `alerted` | str，默认 `'none'` | 告警状态机：`none`（未告警）→ `alerted`（故障告警已送达）→ `recovering`（恢复通知待送达）→ `none` |

### 1.2 写入语义（`FetchService.fetch_and_store`）

`fetch_and_store` 内已有 per-source 的 `(numbers, ok)` 结果（`_try_fetch` 返回值），
据此按源 upsert 健康表。**独立短事务**（健康写失败不得回滚抓取数据）：包 try/except
记日志，绝不阻断抓取主流程。

**「源健康」定义**：HTTP/API 层成功返回（含业务上的「未开奖」`None`）= 健康；抛异常
（网络/DNS/限流重试耗尽）= 故障。

**`PermanentLookupError` 不计入故障**：未配置 key（juhe 不可用是长期事实）与 schema
契约变更属配置态而非运行态——既不写 `down` 也不清 `ok`，仅更新 `error` 字段留痕。
否则单源部署下 juhe 会永久 `down` 且每天告警。

| 源抓取结果 | 健康表动作 |
|---|---|
| ok（含未开奖） | `status=ok`、`last_success_at=now`、`down_since=NULL`、`alerted` 非 `none` → `recovering` |
| 失败（Transient 层异常耗尽） | `status=down`、`error=摘要`、`down_since` 为空则置 `now`（非空保留原值——故障起点不刷新） |
| `PermanentLookupError` | 仅 `error=摘要`，其余不动 |

### 1.3 评估与告警状态机（新函数 `_evaluate_source_alerts(engine)`）

**评估挂载点**：`_path_a_tick` 尾部（21:30–01:00 每 15 分钟自然评估）+ `run_startup_backfill`
尾部（开机即评估，覆盖白天/停机场景）。

**触发条件**（「连续 2 个 tick 全失败」的时间窗实现）：`status=down` 且 `now - down_since
≥ 30 分钟`。与 tick 次数解耦：持久、不怕重启、白天停机后开机也能正确计时。

**状态机转移**（每次评估对每个源执行）：

| 当前 `alerted` | 条件 | 动作 |
|---|---|---|
| `none` | down ≥ 30min | 发 Bark「开奖抓取持续失败」（源 / 故障起点 / 已持续时长 / 最近 error 摘要）；**送达成功** → `alerted`；发送异常 → 保持 `none`（下轮重试） |
| `alerted` | 仍 down | 不发（防每 15 分钟轰炸） |
| `recovering` | （待送恢复通知） | 发 Bark「已恢复」（源 / 故障时长）；送达成功 → `none`；发送异常 → 保持 `recovering`（下轮重试） |
| `none` | 正常 | 无动作 |

注 1：`alerted → recovering` 的转移发生在**写入侧**（1.2：恢复成功的抓取把非 `none`
的 `alerted` 置为 `recovering`），评估侧只负责送出恢复通知后回 `none`。即「曾经告警过的
故障恢复后，必须成功送出一条恢复通知才回到初始态」——DNS 教训：故障期告警通道大概率
同时挂，恢复通知也必须重试到送达。

`ADMIN_BARK_KEY` 未配置：不发（`_build_admin_alert()` 返回 None 语义），仅写表 + error
日志；admin 面板仍可见 `down`。

### 1.4 告警通道共享（小重构）

`_build_admin_alert` 从 `app/api/auth.py:231` 挪到 `app/notifications/admin_alert.py`
（函数原样），auth 与 scheduler 共同引用。**不引入 Notifier/渠道表体系**——admin 告警
是运维兜底，不走用户通知管线（无 NotificationLog、无 DND）。

### 1.5 admin 面板小加分

`GET /admin/health`（`app/api/admin.py` system_health）响应从 `{source, status}` 扩展为
`{source, status, last_success_at, down_since}`，前端面板顺势展示（前端改动从简：仅展示
新字段，无新组件）。

### 1.6 时间语义纪律（CLAUDE.md datetime 对齐）

`last_success_at` / `down_since` / 评估用 `now` 全部 naive UTC（`datetime.now(timezone.utc)
.replace(tzinfo=None)`，与 `TimestampMixin.created_at` 同时区同数值），DB 内比较不做
naive/aware 混比。

## 二、启动回填 QPS 间隔

`app/scheduler/backfill.py` `run_startup_backfill` 第 4 步（missed-draw 检查循环，
backfill.py:52-56）：第二个彩种起抓取前 `time.sleep(_INTER_LOTTERY_INTERVAL)`——镜像
`_path_a_tick` 既有做法（jobs.py:177，L-20260726T013000Z 同源理由：mxnzp 免费账号
QPS=1，连续请求触发 code=101 白耗重试）。

- 常量在 backfill.py 本地定义 `_INTER_LOTTERY_INTERVAL = 1.2`（backfill 被 jobs import，
  反向 import 会循环依赖）；注释注明与 jobs.py 同源同值。
- 第 3 步冷启动历史回填已有间隔（backfill.py:108）✓ 不动。
- `_path_a_tick` 已有间隔 ✓ 不动。

## 三、测试策略

内存 SQLite + mock 源，无真实网络：

1. **写表语义**：`fetch_and_store` 成功 / 未开奖 / Transient 耗尽 / Permanent 四路对
   `ApiSourceHealth` 的写入断言（含「故障起点不刷新」——第二次失败保留首个 down_since）。
2. **状态机**（直接调 `_evaluate_source_alerts`，stub admin_alert）：
   - down 不足 30 分钟 → 不告警；
   - down ≥ 30 分钟 → 告警一次、`alerted`；继续 down 不重复告警；
   - 恢复 → `recovering`，评估送出恢复通知 → `none`；
   - **Bark 发送抛异常 → 状态不变，下轮重试后送达才转移**（DNS 教训回归用例）；
   - 未配 `ADMIN_BARK_KEY` → 不发送、状态照常流转（表仍有值）。
3. **评估挂载**：`_path_a_tick` / `run_startup_backfill` 尾部调用 `_evaluate_source_alerts`
   （monkeypatch spy 断言调用）。
4. **QPS 间隔**：startup backfill 多彩种场景，monkeypatch `time.sleep` 记录调用序列，
   断言 7 彩种共 6 次间隔（首彩种不 sleep）。
5. 全量回归：现有 fetch/scheduler 测试不受影响（健康写入包 try/except 不改抓取行为）。

## 四、部署注意

- 新增 1 条 Alembic 迁移（ApiSourceHealth 两列）；容器启动自动 `alembic upgrade head`。
- NAS 端 `docker-compose.override.yml`（dns 固定）已就位，升级按 docs/deploy.md 常规流程
  `git pull && docker compose up -d --build`。
- 上线后首个 tick 即开始写健康表，admin 面板 `/admin/health` 立即可见。
