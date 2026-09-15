# TODOS

> 由 /autoplan CEO 审查（2026-09-15，plan-11）创建的延期项清单。每项含动机、现状与切入点。

## P2

### 1. plan-11b：恢复补抓 + 结果口径告警（F1/F2）

- **What**: 源恢复后按 `[down_since, now]`（上限 30 天）补抓缺口期并触发比对；评估器增加「应开奖日已过但无 verified DrawResult → 告警」的结果口径。
- **Why**: plan-11 只修「发现」未修「补回」——9 天故障恢复后 `_BACKFILL_LOOKBACK_DAYS=2` 只补 2 天，其余 7 天开奖永不比对（2026-09-15 事故 3 注漏通知的复现路径）。结果口径覆盖非传输成因（交叉校验不一致、休市、存储失败）。
- **Pros**: 事故伤害两半都闭环；任何漏抓成因都有告警。
- **Cons**: 需适配器按日期/期号抓取能力（mxnzp /common/history 可复用但历史回填不写 outbox）；休市误报需设计（彩市休市期间 draw_days 仍命中）。
- **Context**: 切入点 `app/scheduler/backfill.py:19`（lookback）、`app/services/source_health.py`（评估器）、`app/services/fetch_service.py`（outbox 语义）；`recompare` CLI（plan-10）可作存量重算组件。
- **Effort**: L（human）→ M（CC）
- **Depends on**: plan-11 合入（down_since 持久化是免费输入）

### 2. 告警疲劳管理：冷却 + 升级重发（F9/F10）

- **What**: 同源 6h 内最多一对 alert/recovery；故障 <5min 不发恢复通知；down 状态每 24h 重发一次「仍故障 + 时长」。
- **Why**: 抖动源（40min 断/40min 通）会持续产出成对消息，几周内把管理员训练成静音 Bark——下次 9 天事故照旧。单条告警被划掉就再无第二次机会（原事故机理）。
- **Pros**: 告警长期可信；睡觉/划掉场景有升级路径。
- **Cons**: 需要新增冷却状态列（或复用 down_since + 评估侧计数）；语义变复杂。
- **Context**: `app/services/source_health.py` 评估器；`down_since` 已持久化可直接算时长。
- **Effort**: M（human）→ S（CC）
- **Depends on**: plan-11

### 3. 外部黑盒监控：/health 实时源数据 + 探针（F13）

- **What**: `/health`（未鉴权）暴露实时源健康摘要（或独立 `/healthz/sources`），配一条外部 uptime 探针。
- **Why**: 进程内 Bark 在整机/NAS 级故障（2026-09-15 正是 NAS DNS）时结构性无效；外部探针是唯一能覆盖该类的方案。仓库已有 `/health` + `_data_source_state` + Docker HEALTHCHECK。
- **Pros**: 整机故障也有告警层；部署成本极低。
- **Cons**: 未鉴权端点暴露运营细节需权衡（家庭 NAS 风险低）；依赖外部监控服务。
- **Context**: `app/main.py:181`（`_data_source_state`）、`app/main.py:379`（/health）、`Dockerfile:60`（HEALTHCHECK）。
- **Effort**: S
- **Depends on**: plan-11（实时健康表）

### 4. 用户侧「未能核对」可见性（F3/F14）

- **What**: path_b / dashboard 在开奖缺失时不静默返回（notifier.py:159 `tracked_count == 0 → return 0`），增加「本期未能核对」状态与提示。
- **Why**: 「中奖永不静默漏通知」目前只兑现给 admin（Bark），没兑现给受损方（用户）。
- **Pros**: 产品承诺直接兑现；用户对系统可信度有感知。
- **Cons**: 改通知语义（新消息类型 + DND 交互）；需设计措辞避免恐慌。
- **Context**: `app/notifications/notifier.py:147-165`；spec §非目标当前排除 per-user 告警，改方向需 spec 修订。
- **Effort**: M
- **Depends on**: plan-11b（结果口径数据是先决条件）

## P3

### 5. 多通道 admin 告警（F15）

- **What**: admin 告警除 Bark 外支持 email/feishu（复用既有渠道层与 SMTP 配置）。
- **Why**: 一个全部意义是「必须送达」的组件采用单通道无升级；但 plan-11 的送达重试已缓解。
- **Effort**: M | **Depends on**: plan-11

### 6. 健康写入自监控（F7）

- **What**: 评估器附带「最近健康写入距今 >24h（且在应抓取时段）→ 告警」。
- **Why**: 健康写入路径坏了则表停旧 ok、永不告警（与 9 天事故同型）。递归层级过深、现有 warning 日志已留痕，故 P3。
- **Effort**: S | **Depends on**: plan-11

### 7. 源稳定性指标化（F16）

- **What**: 记录源故障率/恢复时长趋势，回答「这个源多不稳、要不要换源」。
- **Why**: 6 个月后无法量化评估 mxnzp 免费层是否够用。
- **Effort**: M | **Depends on**: plan-11
