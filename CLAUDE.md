# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目状态

**Plan 01–05 已完成（基础设施 / 领域层 / 仓储核心闭环 / 调度推送 / 认证用户管理），318 tests green。** Plan 06（Web UI/部署）待实现，plan 在 `docs/superpowers/plans/`。改代码前先读 spec + 对应 plan；实现通过 **workflow orchestrator**（见下）自动跑 plan。

**OSCILLATING 升级 opus + flip-flop 区分**（2026-07-05）：T6e OSCILLATING halt 暴露两个设计缺陷（用户背景：非专业、依赖自动化、没手动调 opus）：(1) 无限模式 `round>=4` 才 opus，但 OSCILLATING 在 round 3 触发 → 挡住 opus 升级路径；(2) `detectOscillation` 只看文件被改次数，不分 flip-flop（reviewer 真矛盾）vs 补充（implementor 没修完，更强 model 能解）。修复（TDD RED→GREEN→260 tests green）：`shouldEscalateOnOscillation(currentModel, alreadyEscalated)`——OSCILLATING 触发时若非 opus + 未升级过 → 升级 opus 跑一轮（不 halt）；已 opus / 已升级过 → halt。`isFlipFlop(reviewHistory)`——last 轮 findings title 在任意前轮出现过 → flip-flop；全新 title → 补充（跨 reviewer 也算）。runtime: review 循环 osc 分支接两纯函数 + `ensurePerTaskDefaults` 加 `opus_escalated` 字段（防重复升级）。halt 含义升级为「opus 也修不好」，看 `diag.flipFlop` 定位（true=规则冲突需裁定 / false=拆 task）。详见 `docs/superpowers/workflow-design.md §13g`。

**Bootstrap task 识别三层防御**（2026-07-05）：bootstrap agent (kimi-k2.7) 在 plan-06 跑时连续暴露 3 类 task 识别不稳定，每层用 runtime 确定性兜底（不依赖 LLM）——把"提取/判断"这类确定性可正则化的事从 LLM 手里拿走交给纯函数，LLM 只负责"读+复制"。(1) **task_id 双重前缀**（`0566230`）：bootstrap 偶返 plan-scoped task_id `"plan-06/T1"`（frontmatter 是裸 `T1`）→ taskKey/commitSubject 再拼一层 → `feat(plan-06/plan-06/T1)` + completed 比对 key 不匹配 → 已完成 task 误判 pending → 重跑。修：`bareTaskId` 纯函数 strip `plan-XX/` 前缀 + boot 后源头 sanitize 所有 task_id。(2) **非叶子父 task 当 leaf**（`e50a851`）：bootstrap 不遵循 leaf-first，把 `## Task 6`（"拆 6b-6g" 父说明段）当 leaf 返回 + 漏提 frontmatter 的 T10 → implementor 跑说明段混乱。修：`dropParentTasks`（`T{N}`+`T{N}{letter}` 共存 → drop `T{N}`，基于返回列表判定不读 plan 文件）+ prompt 强化（CRITICAL 含全部 frontmatter keys）。(3) **completed 漏识别 / deterministic-completed**（`0cd42c6`）：bootstrap `evidence.completed` 漏 plan-06/T6d（feat `b08e3e7` + fix `ac28750` 都在却漏）→ 已完成 task 被当 pending 重跑（用户发现「implement T6d 但 T6d 已 commit」）。修：bootstrap 返回 `git_log_subjects`（原始 commit subjects，原样复制），runtime 用 `extractCompletedFromSubjects` 正则提取（`feat|fix|refactor`），三层 fallback：`args.completed`（手动覆盖）> `extractCompletedFromSubjects(git_log_subjects)`（正则）> `boot.evidence.completed`（LLM fallback）。`extractTaskKey` 扩展认 fix|refactor。详见 `docs/superpowers/workflow-design.md §13k`。253 tests green（含 3 个 REGRESSION 用例）。

**Workflow orchestrator 修复**（2026-06-25）：修复了 10 个 CRITICAL/IMPORTANT bug——qualityReviewer 结构化 findings 被 `.join()` 序列化为 `[object Object]`、hunter `silent_failures` 完全丢弃、fix-round implementor 状态被忽略等。修复后的 review chain 基于 `collectReviewFindings` + `formatFindings` 传播完整的结构化反馈。

**改进 7.1: implementor retryModel='opus'**（2026-07-05）：T6f（admin 后台 7 endpoints）implementor prompt **262533 token** > sonnet 槽 kimi-k2.7 router limit **262144**（超 389）→ router fallback（glm-4.7）同 limit 也超 → `agent()` 返 null → 无 retryModel → `model_unavailable` halt。opus 槽 glm-5.2[1M] 有 1M context 能装下。修：implementor 全部 5 个 dispatch 点（initial/ctx-fetch/ctx-retry/initial-retry/fix-round）加 `retryModel='opus'`——null 时自动升级 opus 重试一次（仅一次）。已是 `'opus'` 的 dispatch 点（blocked-upgrade/ctx-opus）由 dispatchImpl 内 `retryModel !== model` 守护自动短路。TDD：`dispatchImpl-retry.test.js` 新增源码字面量断言（按行扫描所有 implementor dispatch 点，校验非 opus 调用点含 fixModel 都传 'opus'）。RED→GREEN，全 261 workflow tests green。详见 `docs/superpowers/workflows/research/t6f-halt-token-limit-2026-07-05.md` + `USAGE.md §7.2`。

**dispatchImpl retryModel 机制**（2026-07-03）：`agent()` 返回 `null` 不总是限额耗尽——也可能是**模型能力不足**（如 `qwen3.7-plus` 跑复杂 bootstrap 被 router "Repetitive tool calls" 400 中断）。旧逻辑一律视作 `model_unavailable` halt → 弱模型永远无法完成复杂任务。修：`dispatchImpl(prompt, opts, model, retryModel = null)` 新增第 4 参数——`agent()` 返回 null 时若 `retryModel` 非空且 ≠ `model`，用 `retryModel` **重试一次**（仅一次，不循环）；quota 错误仍在第一层 catch halt（不浪费更强模型额度）。bootstrap 调用传 `'opus'` 作 retryModel（sonnet→opus 升级路径）。**改进 7.1（2026-07-05）**：implementor 全部 5 个 dispatch 点也加 `retryModel='opus'`（防 token-limit halt，opus 1M context 能装下大 prompt）。测试：`docs/superpowers/workflows/tests/dispatchImpl-retry.test.js`（9 个场景，含改进 7.1 implementor 全覆盖）。日志：重试时 `log()` 打 `⚠ label: model returned null (capability failure likely), retry with retryModel`。详见 `docs/superpowers/workflows/USAGE.md §7.2` 和 `docs/superpowers/workflow-design.md §2.4`。

**Review agent 循环修复 + 职责硬边界**（2026-06-30）：hunter 子代理在 Plan 05/T1 全绿后陷入死循环——把 `pytest --cov` / `ruff + pytest -q` / `git status+find` 三件套重跑 406 次、47 分钟不退出（claude-mem 里 50+ 条 "Final Security Module Validation" 是其副产物）。根因：review agent PROMPT 没禁止跑测试，hunter 误把"验证代码行为"当职责、全绿后反复重跑。修复：(1) 三个 review agent（specReview/qualityReviewer/hunter）PROMPT 加职责硬边界——**STATIC READ-ONLY，禁止跑 pytest/ruff/lint/build**（那是 implementor/gate 职责），只允许 git diff/status/find/grep/Read；(2) 删掉最初加的软计数 STOP RULE（"最多 5-10 次命令"靠不住）。同时给 hunter 加 `silent_failure_context` config 注入入口（见下「通用性」），把 CLAUDE.md「静默失败纪律」5 类项目特定致命点经 config 驱动喂给 hunter 优先核查——通用框架 + config 特化，非硬编码。改 lib.js 的 PROMPTS/helpers 必须同步 run-plans.js inline 副本（sync.test 守护）。

**Review 空响应守卫 + 结构化 schema 约束**（2026-06-30）：两处 review chain 强化。(1) **P0 空响应防哑火**：hunter/quality/spec review agent 偶发 thinking-only 空响应（模型在 thinking 块里"以为"调了 StructuredOutput，实际无 tool_use 块 → `agent()` 静默返回 null/空对象）。旧 `reviewHaltReason` 只查 `agent_error`/`model_unavailable` 两个 sentinel，对 null/undefined status 返回 null（不 halt）→ `allGreen` 判 false → `collectReviewFindings` 全跳过 → implementor 收「0 项发现」空反馈跑空修复 → 浪费 3 轮后以错误原因（`review max rounds`）halt，`blocked.md` 误导接手。修复：`reviewHaltReason` 加 `REVIEW_VALID_STATUSES` 守卫——status 缺失/为空/非法 → 返回新 sentinel **`review_empty`**（区别于 `agent_error`：后者是 `agent()` 抛非 quota 异常，`review_empty` 是静默空返回 = 瞬态模型 hiccup），`blocked.md` 据此提示「全新跑续即可」，可操作性高于笼统 `agent_error`。halt 决策符合 spec §5「瞬态失败重试耗尽 → halt」——`agent()` 带 schema 时内部已重试 StructuredOutput，等 safeAgent 见空返回时多半已耗尽。(2) **P1 schema items 约束**：qualityReviewer/hunter 的 `issues`/`silent_failures` 加 `items: {required:['title','fix']}` 对象约束（specReview 保持字符串模板故走 `reviewSchema`，qualityReviewer 拆出独立 `qualityReviewSchema`）——防 LLM 返回纯字符串/缺 fix/用错字段名 → `collectReviewFindings` 的 `it.title||String(it)` 兜底为 `[object Object]`。lib.js + run-plans.js 同步（sync.test 守护新增 `REVIEW_VALID_STATUSES`/`qualityReviewSchema`/`review_empty` 断言）。(3) **第二道守卫 `reviewHaltForEmptyFailed`**（code-review HIGH 跟进）：`review_empty` 只堵 status 缺失，「合法 `failed` + 空 diagnostics」仍漏过（status 合法 → `reviewHaltReason` 不 halt → `collectReviewFindings` 空 → implementor 收「0 项发现」跑空修复 → max rounds 误 halt）。新增纯函数 `reviewHaltForEmptyFailed`（`collectReviewFindings` 抽 `findingsOf` helper 复用），在 `reviewHaltReason` 之后、fix-round 之前：任一 review `status==='failed'` 但 findings 0 项 → halt `review_failed_no_findings`（区别于 `review_empty`：后者 status 缺失，本守卫 status 合法但无发现）。主 review 轮 + simplify 轮两处接线。lib.js + run-plans.js 同步（sync.test 守护 `reviewHaltForEmptyFailed`/`review_failed_no_findings` 断言）；78 tests green。

**Plan 05 完成 + workflow 韧性/收敛根治**（2026-07-01）：Plan 05/T1–T7 全部 commit（安全工具 / 邀请码服务 / current_user·RequireAdmin / auth API+CSRF+CORS / 渠道配置加密 / admin 后台+force-verify / 路由注册+号码池·兑奖 router+auth flow），318 tests green。本周期跑 plan-05 暴露并修了 4 个 workflow 缺陷：(1) **OSCILLATING 收敛误报根治**——`detectOscillation`（核心文件被审 ≥3 轮即 halt）原在 `allGreen break` 之前，r3 三 reviewer 全 ok 时被先截胡、allGreen 永远轮不到。修：`allGreen break` 提前到 `detectOscillation` 之前——收敛（全 ok）即放行，真矛盾（reviewer 持续分歧如 T7 claims 时区：quality 要 CST「雷 2」vs hunter 要 naive UTC「主流惯例」）仍正确 halt 让人介入。sync.test 加顺序断言。(2) **`normalizeCompleted` 连字符 bug**——正则只认斜杠 `/`，bootstrap agent 偶返连字符格式 `01-T1` 时漏过 normalize → 与 taskKey `plan-01/T1` 不等 → 已完成 task 误判 pending → 重做 plan-01 → 脏工作树+OSCILLATING。修：`[\/\-]+` 兼容两种分隔符。(3) **`dispatchImpl` null guard**——router 限额中文错误「已达到 5 小时的使用上限」被 agent runtime 吞为 null 返回（非 throw），dispatchImpl 不处理 null → 顶层 `boot.halted` crash。修：null → `model_unavailable` halt（覆盖 bootstrap+所有 task）。(4) **`isQuotaError` 认中文限额**——正则加 `使用上限|限额|额度|超出.*限制`（本机 router 中文错误）。另：手动收敛多个 task 的 OSCILLATING halt（T2 invite / T4 auth / T5 channels / T7 claims 时区——均 r3 收敛但 guard 误报，或 CLAUDE.md 规则冲突需人工裁定）；丢弃过 2 次 OSCILLATING 混乱中越界产生的 plan-06 文件（tickets/claims，后确认 T7 合法含 tickets/claims router，重做）。**安全加固**（commit 安全审查跟进）：cookie_secure 配置化（默认 True 生产安全，conftest 测试环境 false）+ login Origin 校验（跨站 → 403 防 forced-login CSRF）+ CORS_ORIGINS 解析失败 logger.warning（H1 静默回退）。`.env.example` 加 COOKIE_SECURE/CORS_ORIGINS。

**Workflow orchestrator 去重重构**（2026-06-28）：runTask 178→126 行。抽 `dispatchImpl`/`safeAgent` 统一重复的 try/catch+quota 处理，纯决策（`classifyThrown`/`reviewHaltReason`）进 lib.js 可 node:test 测，runtime 胶水留 run-plans.js。分层原则：**改 lib.js 的纯函数/helper 必须同步 inline 副本到 run-plans.js**（sync.test 守护）；runtime 胶水（调 `agent()`）只在 run-plans.js。

## 项目是什么

"兑奖了吗？"——多用户中国彩票开奖**自动核对与通知**系统。覆盖福彩+体彩 7 大主流彩种。用户维护固定号码池，系统每期自动比对开奖结果并按用户配置的渠道/策略推送。部署在家庭 NAS（小圈子邀请制共享），架构预留大规模公开扩展。未来计划 iOS 原生 App（API-first 复用）。

## ⚠️ 合规红线（最高优先级，任何改动都要守住）

系统定位**「事后核对 + 个人号码管理」**，**绝不**包含：号码预测 / AI 推荐 / 必中 / 冷热号排序推荐 / 购彩代购支付 / 高频彩私彩。走势图作为**常规功能**（综合分布图 + 频次，不排序/不标冷热/不推荐 + 显著随机性声明）；选号仅用户自选/机选，系统不基于走势推荐。**遇到"选号辅助/预测"类需求必须拒绝**。详见 spec §9。

## 技术栈

- 后端：Python 3.12（**用 uv，不用 pip/poetry**——本机系统 Python 是 3.9，项目锁 3.12，`uv sync` 自动建 3.12 venv）、FastAPI、SQLModel + SQLite（类型选可迁 PostgreSQL）、Alembic、APScheduler、httpx、PyJWT、passlib、cryptography(Fernet)
- 前端：Vue3 + Vite + Vue Router + Pinia + UnoCSS + ECharts
- 部署：Docker（NAS FnOS）

## 架构（big picture）

分层，**领域层零 IO 是核心纪律**：

```
客户端(Vue3 SPA) → FastAPI(REST) → 应用服务(调度/获取/比对/推送/统计)
  → 领域层(纯逻辑: 彩种规格+比对策略+奖级, 无DB/网络) → 适配层(数据源) + 基础设施(SQLite)
```

关键设计（需读 spec §4/§5/§7 才能拼出的全貌）：
- **策略模式彩种引擎**：新增彩种 = 加 `LotterySpec` 配置 + 若需要加 `CompareStrategy`。领域层核心代码不动。
- **双源容灾**：MXNZP 主 + 聚合数据备，**交叉校验号码一致才入库**（`verified=true`），不一致拒绝入库+告警。准确性优先于及时性。
- **比对只做一次**：开奖结果入库触发一次比对写 `comparisons`；路径A（大奖当晚即时简讯）与路径B（次日 07:00 汇总）**复用** comparisons，不重复比对。
- **范围三层**：开奖获取=全局（所有彩种都拉）；比对/推送=仅用户追投的彩种（号码池决定，没追的不比对不推送）。
- **用户隔离**：所有用户私有表强制 `user_id`，repository 查询一律 `WHERE user_id=?`，FastAPI `current_user` 依赖注入；开奖结果 `draw_results` 是全局共享数据。
- **金额用分**（int）存储，展示层再除 100，避免浮点。

### 已实现分层（Plan 01–04）

```
app/
├── config.py          # pydantic-settings Settings；field_validator 强制 CRYPTO_KEY 是可用 Fernet key
├── db/
│   ├── engine.py      # build_engine(): SQLite + WAL/NORMAL/busy_timeout + pool_size=1（单写连接）
│   └── session.py     # get_engine() 惰性单例 / get_session()
├── domain/            # [Plan 02] 纯逻辑层，零 IO（import-linter 强制）
│   ├── spec.py        # LotterySpec（hydrate from spec_json + 校验）；NumberRange/PositionalDigits 类型不变式
│   ├── entry.py       # Entry + expand()（复式/胆拖展开，MAX_COMBINATIONS 上限）
│   ├── prize.py       # PrizeTier + HitResult（front_hit/back_hit/tier/amount/is_win）
│   ├── compare.py     # 策略模式：PartitionCompare/PositionalCompare/QxcHybridCompare + REGISTRY + compare() 入口
│   └── prize_tables.py# 7 彩种奖级表（可配置）
├── adapters/          # [Plan 03] 数据源适配器（httpx，MockTransport 测试友好）
│   ├── base.py        # DrawSource protocol + DrawNumbers dataclass + normalize_draw_no（期号归一化）
│   ├── mxnzp.py       # MXNZP 主源
│   └── juhe.py        # 聚合数据备源
├── infrastructure/
│   ├── crypto.py      # CryptoService: Fernet 多版本；CipherBlob=(version,ciphertext)
│   └── repositories.py# [Plan 03] Repository 基类 + TicketRepo/UserRepo（user_id 注入，IDOR-safe）
├── services/          # [Plan 03] 应用服务层（编排，调 domain 不反向）
│   ├── fetch_service.py    # FetchService: 双源交叉校验 + grace + 退避 + 幂等存储（spec §7.2/§10）
│   ├── compare_service.py  # CompareService: outbox 原子认领 + domain.compare + comparisons/prize_claims（per-ticket savepoint）
│   ├── refill_service.py   # FloatRefillWorker: 浮奖回填（7天上限 + unresolved 标记 + cutoff naive UTC）
│   └── correct_service.py  # [Plan 03 T6] DrawCorrectService: 官方更正（version++ + outbox 重比 + 原地更新）
├── notifications/     # [Plan 04 T1–T3] 推送层
│   ├── base.py             # NotifierChannel 接口 + NotificationPayload/SendResult（send 永不抛异常）
│   ├── bark.py/feishu.py   # httpx 渠道，HTTP 状态码 + 业务码双判（防静默成功，spec §10）
│   ├── email_channel.py    # 系统统一发件（用户只填收件地址）
│   ├── templates.py        # 路径A即时简讯 + 路径B汇总（spec §8.3）
│   └── notifier.py         # Notifier: 路径A/B 编排（session 内读+写log→关session→发网络→新session 更新log，spec §7.1）+ 多渠道降级重试 + DND + admin Bark fallback
├── models/            # SQLModel 全 13 表 + apscheduler_jobs；__init__.py 汇总 import（建表靠 SQLModel.metadata.create_all）
├── seeds/             # 7 彩种 LotteryType 种子（spec_json pydantic 校验，启动幂等写入）
└── main.py            # FastAPI app；lifespan 启动校验(validate_startup)+种子；GET /health（db+tz 探活）
alembic/               # 首迁移 0001 含全 schema + apscheduler_jobs；b6a04a1 加 comparisons.unresolved
import_linter 配置内联于 pyproject.toml [tool.importlinter]     # app.domain 禁 import infrastructure/adapters/api/services（裸 `uv run lint-imports` 即生效，workflow gate 也走此命令）
```

**SQLite 并发模型**：`pool_size=1` + WAL + `busy_timeout` —— 单写连接串行化，配合 APScheduler jobstore 独立 engine（见 Plan 04）避免写竞争。

### ⚠️ 静默失败纪律（silent-failure，最高优先级）

系统核心价值是「**中奖永不静默漏通知**」。services 层反复因 silent-failure 被 review 链拦下，实现时务必主动设防：

- **DB 写不得 split-commit**：一个逻辑操作（如 DrawResult + PendingComparison outbox）必须**单事务一次 commit**。分两次 commit 时，若第二次失败，重试走幂等分支会因「已存在」跳过、不补 outbox → 永不比对 → 漏通知。（FetchService._store 已修）
- **per-row 异常隔离用 savepoint**：循环里逐行处理 + 共享 session 时，单行失败必须 `with session.begin_nested():`（SAVEPOINT）隔离——否则 flush 时 DB 错毒化 session（PendingRollback），后续好行全丢 + 末尾 commit 变 rollback。**bare `except Exception` 不够**，必须 savepoint。（CompareService._compare_one 已修）
- **批量循环里单行故障不得中断整批**：per-row try/except + log（含 `exc_info=True`），不阻断后续行；批末的兜底标记（如 expired/unresolved）须无条件执行（独立方法/finally），不依赖循环不抛。
- **更正路径重置终态标记**：行被标 `unresolved=True` 后经官方更正重比，须在 upsert existing 分支重置 `unresolved=False`，否则永久卡死、永不再查官方金额。
- **datetime 时区对齐**（已两次踩同源雷：T5 refill + Plan 04/T3 notifier）：SQLite 对 datetime 做**字符串比较**（非 tz-aware），且**存取会剥离 tzinfo**。规则：模型里凡与其他 datetime 字段比较/排序的写入值，须与项目主流惯例（`TimestampMixin.created_at = default_factory=datetime.utcnow` = **naive UTC**）同时区**同数值**。用 `datetime.now(timezone.utc).replace(tzinfo=None)`（非弃用 `utcnow()`、非 aware CST）。
  - **雷 1（T5 refill）**：cutoff 用 aware CST 与 naive-UTC `created_at` 比 → 字符串排序错位 → 边界行误判超期、永久排除回填 → 浮奖金额永久 null。修：`_cutoff_naive_utc`。
  - **雷 2（T3 notifier `sent_at`）**：`sent_at = datetime.now(_CST)` 写 **CST 本地数值**（如 05:10），`created_at`(utcnow) 写 **UTC 数值**（如 21:10）——同一时刻 `sent_at` 比 `created_at` 数值小 8h，SQLite 字符串排序 `sent_at < created_at`，未来按时间过滤 log 的运维查询（清理过期/查 pending 超时/统计延迟）静默误判边界行。修：`sent_at = datetime.now(timezone.utc).replace(tzinfo=None)`。
  - **⚠️ 测试写法陷阱**：因 SQLite 存取剥离 tzinfo，断言 `log.sent_at.tzinfo is None` **抓不到**雷 2（aware CST 存进去取回也是 None，但**数值**仍是 CST）。须断言**数值落 UTC 窗口**：`before = now(UTC).naive; ...; assert before <= sent_at <= after`——CST 数值会早 8h 落窗口外才暴露。光查 tzinfo 会假绿。
  - 系统性根治（让 `created_at` 也 aware CST + 迁移规整旧行）待后续。
- **claim-before-compare 的 tradeoff**：`_claim` 在比对前提交 `processed_at`，故比对抛异常的期不再自动重试（靠 ERROR 日志人工介入）——避免配置错无限重试，但瞬态错误会丢该期，Plan 04 调度层决定重试策略。

## 彩种规则（⚠️ 必读，领域层正确性的前提）

**权威源：[`docs/reference/lottery-rules.md`](docs/reference/lottery-rules.md)**（子代理复核福彩/体彩官网，含号码规则/玩法/称呼/奖级/倍投/追加 + 来源链接）。实现 `LotterySpec`/`prize_tables`/号码校验前**务必对照该文档**，不要凭记忆。

易错点（已复核修正）：
- **七星彩 2020-10-13 改版**：现行为"**前区 6 位（0-9）+ 后区 1 位（0-14）**"混合型，**不是纯 7 位 0-9**、不是纯 positional。
- **玩法按彩种配套，禁止硬编码"单式/复式/胆拖"三件套**：分区型（双色球/大乐透/七乐彩/七星彩）= 单式/复式/胆拖；按位型（福彩3D/排列3/排列5）= 单选/直选/组选三/组选六。
- **福彩3D 现行叫"单选"**（旧称"直选"已废），共 12 种玩法；排列3/5 沿用"直选"。两套术语，勿混。
- **倍投**：所有彩种 2–99 倍（影响投入与中奖金额）。**追加投注仅大乐透**（基本 2 元 + 追加 1 元，追加仅参与一二等奖 80%）。
- 七乐彩特别号**同源于 01–30 池**（非独立分区）。

## 常用命令（已验证可用）

```bash
# 后端
uv sync --extra dev                                          # 装依赖（uv.lock 锁定；首次 uv python install 3.12）
uv run pytest -v                                             # 全量测试
uv run pytest tests/test_models_t4c.py -v                    # 单文件
uv run pytest tests/test_models_t4c.py::test_defaults -v     # 单测试
uv run uvicorn app.main:app --reload                         # 启动 API（需 .env，见下）
uv run python -m app.cli ssq                                 # [Plan 03 后] 手动触发一期闭环（获取→比对→推送）

# 数据库迁移
uv run alembic upgrade head                                  # 应用迁移
uv run alembic revision --autogenerate -m "msg"              # 改 model 后生成新迁移

# 领域层 purity 护栏
uv run lint-imports                                          # app.domain 不得 import infra/adapters/api/services

# workflow orchestrator 测试（修改 run-plans.js/lib.js 后必须跑）
cd docs/superpowers/workflows && node --test 'tests/*.test.js'

# 前端（web/，[Plan 06 后]）
cd web && npm install && npm run dev                         # 开发（代理 /api → :8000）；npm run build → ../static

# 部署（NAS Docker，端口 8280）
docker compose up -d --build
```

**密钥**（`.env`，不进库不进日志；模板 `.env.example`）：`JWT_SECRET`（≥32 字符）、`CRYPTO_KEY_V1`（44 字符 Fernet key，生成 `python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"`）、`MXNZP_API_KEY`/`JUHE_API_KEY`（数据源）、`SMTP_*`（email 渠道）、`ADMIN_BARK_KEY`。启动时 `validate_startup()` 会端到端冒烟验证 crypto key 可用——无效即拒绝启动。

## workflow orchestrator（执行 plan）

`.claude/workflows/run-plans.js` 自动执行 `docs/superpowers/plans/*.md`：每 task implementor(TDD RED→GREEN→REFACTOR) → review 三链并行(spec 逐行 ‖ quality 架构 ‖ silent-failure-hunter) → simplify → commit `feat(plan-X/T-Y)`，plan 级独立 gate（在 committed SHA 上重跑全量测试，不信 implementor 自报）。review 反馈管道：`collectReviewFindings` 归一化三类 review 的 diagnostics key（spec/quality 读 `issues`，hunter 读 `silent_failures`）+ `formatFindings` 序列化为自描述可读反馈。修复了 hunter findings 被丢弃、quality findings 变成 `[object Object]` 等 10 个 bug（2026-06-25）。详见 `docs/superpowers/workflows/USAGE.md`。

**进度以 git 为单一事实源**——bootstrap 读 git log 的 `feat(plan-X/T-Y)` convention 跳过已完成 task。跨机器/跨 session 续跑无需 manifest 或 runId：clone + 跑全新 workflow 即从未完成的 task 继续。

触发（Claude 调 Workflow 工具）：
```
Workflow({ scriptPath: '.claude/workflows/run-plans.js', args: { plan: '03' } })  # 单 plan
Workflow({ scriptPath: '...', args: {} })                                          # 所有 plan
```

**⚠️ 续跑用「全新跑」，不要用 `resumeFromRunId`**：resume 回放缓存的 bootstrap agent，其 `completed` 是 halt 当时的快照，看不到 halt 后 workflow 外手动提交的 task（如 review halt → 手动修完 commit）→ 直接回放旧 halt、0 token 0 agent 推进；且 resume 不传 `args` 会因 `args.configPath` 抛 undefined crash。halt/限额/断 session/手动修完后续跑，一律全新跑（bootstrap 重新读 git log 跳过已 commit 的 task）。review-halt 后推荐流程：看 `runs/<ts>/manifest.json` 的 `blocked_info` 定位缺陷 → 手动修 + commit `feat(plan-X/T-Y)` → （可选）派 spec/quality subagent 复核 → 全新跑续。详见 `docs/superpowers/workflows/USAGE.md` §7.1。

**§2.4 模型策略（务必遵守）**：开发用指定 opus/sonnet/haiku；一旦不可用——**含 429 落 router stderr、不在 `Error.message` 的情形**——一律视作 `model_unavailable` → halt + 保存进度（finalReport 依次试 opus/sonnet/haiku 写 manifest）→ **等用户发指令才 resume**。**绝不降级到可用 model（如 glm）继续开发**，`'uncaught error'` 等同于 `model_unavailable`。

**本机 model 现状（`~/.claude/settings.json`，无 Claude 订阅）**：三槽固定映射——`opus`→`anthropic/glm-5.2[1M]`、`sonnet`→`anthropic/kimi-k2.7`、`haiku`→`anthropic/deepseek-v4-pro`（router `192.168.8.167:4010`）。§2.4 禁的是**中途因限额降级**到更弱模型；这种**主动配置的稳定映射不属降级**，可正常开发——仅当 router 真正返回 429/quota 或发生 model 切换时才视作 `model_unavailable` → halt。⚠️ `haiku`→deepseek 槽不支持 ultracode `effort=xhigh`（直接 400），跑 workflow 前须 `/effort` 降到 high/medium。

## 关键约定

- 彩种代码：`ssq`(双色球)/`dlt`(大乐透)/`qlc`(七乐彩)/`fc3d`(福彩3D)/`qxc`(七星彩)/`pl3`(排列3)/`pl5`(排列5)
- 号码风格：partition（多区：红/蓝、前/后区、基本/特别号）/ positional（按位 0-9）
- 开奖日 `draw_days` 用 Python 0-based 周几（`date.weekday()`）：周一=0 … 周日=6
- 通知渠道：bark/feishu/email（邮箱系统统一发件，用户只填收件地址，发件 SMTP 由运维方在后台配置），可插拔，每用户配置，无主备
- 推送策略（每用户×每彩种）：`every`(每期推) / `win_only`(仅中奖推)
- 推送时机分层：大奖当晚即时简讯 + 次日 07:00 汇总（时间可配，默认 07:00）
- 全程时区 Asia/Shanghai

## Prototype 协作模式（前端页面）

9 页 prototype，**全部完成（收官）**：01-dashboard（含开奖日历+附近代销点）、02-my-numbers、03-draw-query、04-win-records、05-my-stats、06-settings、07-admin、08-trend、09-login（开奖日程已融入 dashboard，无独立页）。经验：
- **全部手写 HTML**（OD/Open Design 易截断/跑偏/换视觉体系/触发彩票安全审查污染），手写最稳、视觉统一。
- 所有页面**复用仪表盘的视觉系统**（CSS 变量 `--bg/--surface/--fg/--accent/--red-ball/--blue-ball`、`:root.dark` 深色、**8 项导航**、右上角用户区、红蓝球）。
- **走势页**（08-trend）：综合分布图仿福彩官网（期从远到近 · 号码按列 · 开出标圆 · 遗漏次数）；⚠️ 遗漏算法须**红/蓝区独立计数**（双色球红蓝 01–16 数值重叠，共享 miss 会互相干扰）；选号面板支持玩法切换、机选自定义（复式/胆拖红蓝数）、多注队列、倍投、批量推送号码池。
- **跨页联动**（prototype 用 localStorage 模拟共享态）：走势选号→`lottery_tickets`→我的号码池；中奖记录「已领取」↔ 仪表盘待兑奖经 `lottery_claimed` 共享。
- **验证**：chrome-devtools MCP `navigate file://` + `evaluate` 检查渲染/交互/数据；视觉截图直接 `Read`（模型具备视觉识图能力，不必走 analyze_image MCP 中转）。

## 文档导航（权威来源）

- `docs/superpowers/specs/2026-06-16-lottery-notification-design.md` — **设计 spec（15 节，需求/架构/数据/合规的单一事实源）**
- `docs/reference/lottery-rules.md` — **7 大彩种规则权威参考**（号码/玩法/称呼/奖级/倍投/追加 + 来源）
- `docs/superpowers/plans/` — implementation plan（6 份业务 plan：01 已完成，02–06 待实现）
- `docs/superpowers/workflows/USAGE.md` — workflow orchestrator 使用指南（触发/参数/限额容错/resume/调试）
- `docs/superpowers/workflows/lib.js` — workflow 纯函数真源（`collectReviewFindings`/`formatFindings`/`matchesPlanFilter` 等 helper + PROMPTS/SCHEMAS），`node:test` 单测
- `docs/superpowers/workflows/tests/` — helper 单测 + sync 护栏（sync.test.js 强制 run-plans.js inline 副本与 lib.js 一致）
- `docs/superpowers/workflow-design.md` — workflow orchestrator 设计文档
- `docs/superpowers/prototypes/` — 页面 prototype（视觉基准，9 页）

## NAS 部署约束

- 端口 **8280**（已核实空闲，避开 NAS 已占用端口）
- **`restart: always`**（FnOS 关机会 `docker stop` 所有容器，`unless-stopped` 策略不会自启——这是该 NAS 的已知坑）
- 部署目录：`/vol1/1000/Docker/lottery-notification/`
- 密钥（数据源 API key、`JWT_SECRET`、`CRYPTO_KEY`）从 `.env` 注入，不进库不进日志
