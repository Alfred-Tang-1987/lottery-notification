# Workflow Script 设计文档

> **薄 delta on `superpowers:subagent-driven-development`**：多 plan 串行执行 + review 并行 + modelHint + runtime 契约。
> 继承 upstream 不重复（三角色 implementor/spec-reviewer/quality-reviewer、两阶段 review、TDD、DONE/NEEDS_CONTEXT/BLOCKED 状态机、Red Flags、least-powerful-model 原则）。此处**仅列增量与 runtime 契约**。
> 运行于 Claude Code `Workflow` 工具（JS orchestrator，约束见 §4）。

## 1. 目标与通用性范围

自动化执行多份 implementation plan：每个 task 派发 subagent（implementor → review chain），遵循 TDD，按 plan 顺序推进，全量测试门禁。

**通用性范围 A**：同工具链（Claude Code + ECC + superpowers）下，**不同项目**复用。
- review agent **硬编码** ECC（spec-reviewer/quality-reviewer/silent-failure-hunter/simplify）——假设 ECC 已安装
- **项目特定项**（test/build/lint 命令、spec 路径）通过项目配置注入（§11）——这是通用性的真正瓶颈
- 不追求跨工具链（vanilla CC / Cursor）可移植

## 2. 角色与模型策略

### 2.1 角色（继承 upstream + ECC review chain）

| 角色 | 模型 | 职责 | 介入 |
|---|---|---|---|
| orchestrator | session 主模型 | 调度、状态管理、resume | 全程（JS） |
| implementor | modelHint \|\| sonnet | TDD 实现 + self-review | 每 task |
| spec-reviewer | opus | 代码 vs spec 逐行比对 | 每 review round |
| quality-reviewer | opus | 质量/架构/边界/类型 | 每 review round |
| silent-failure-hunter | sonnet | 静默失败/吞错/bad fallback | 每 review round |
| simplify | — | 精简代码（可选，max 1） | review 全绿后 |
| bootstrap | — | 读 config/plan/git log，返回结构化状态 | 启动 + resume |
| commit | — | status check + test + git commit | review 全绿后 |
| pr-test-analyzer | — | 测试覆盖质量 | post-plan |

### 2.2 模型选择（砍评分，用 modelHint）

**不评分**。模型选择来自 plan frontmatter 的 `model` 字段（typed enum `sonnet|opus`，§11）：

```
model = task.model || 'sonnet'   // 未标注默认 sonnet
```

- modelHint 是**唯一确定性来源**（workflow JS 无法执行 LLM 打分）
- model enum 校验：当前 bootstrap agent 直接读 `task.model` 不做 enum 校验（**wontfix，第 6 轮，低风险**）。invalid 值（如 `claude-3.5`）会透传到 `dispatchImpl` → `agent()` 调用失败 → halt `agent_error`。fail loud 语义已达成（不静默降级），只是错误路径稍晚（agent dispatch 时 vs bootstrap 校验时）。详见 §14.2 #6 wontfix 决策记录。

**来源链**（依赖 writing-plans 增强，§13e）：
```
workflow 读 plan frontmatter model 字段（§11.2）
  ← plan frontmatter 由 writing-plans 技能增强产出（§13e 待实现）
    ← writing-plans agent 用判断标准标注：
      - 安全/加密/认证相关 → opus
      - 纯算法 + 边界条件 → opus
      - 多模块集成/接口设计 → opus
      - 其余（配置/声明/CRUD/模板）→ 不标注（默认 sonnet）
```

**降级路径**：writing-plans 增强未完成时，所有 task 默认 sonnet；BLOCKED → opus 兜底。非理想但可用。

### 2.3 BLOCKED 升级链（带上限）

```
sonnet BLOCKED → opus 重派 → opus BLOCKED → halt + 写 .workflow/blocked.md + surface 用户
```

**不无限升级**。opus 仍 BLOCKED 则停止整个 workflow（serial 下不阻塞下游，而是 halt 等人工）。

### 2.4 Model 限额容错（halt + fallback 链保存 + resume）

**问题**：opus/sonnet 额度不一，限额耗尽 → agent 调用失败。§2.3 BLOCKED 升级链为「任务太难」设计，限额是「资源不可用」，走升级链无意义（opus 限额耗尽时升级 opus 无用）。

**核心原则**：限额耗尽 → **halt（不降级继续开发）** → fallback 链**仅用于保存进度** → 额度恢复后 resume。**fallback_model 绝不用于继续开发**（避免弱 model 产出低质量代码污染进度）。

```
agent(model=opus/sonnet) 调用
  → 限额耗尽（quota / rate-limit / 429）
    → agent 抛错 或 返回 status:'model_unavailable'
      → workflow 识别（双重：自报 + 捕获抛错，错误含 quota/rate-limit/429/overloaded）
        → halt（不进 §2.3 升级链，不降级）
          → halt 的 finalReport 按 fallback 链 [opus, sonnet, haiku] 逐一尝试
            → 第一个可用 model 写 manifest（quota_exhausted + completed + current_task + resume_point）
              → 全链失败 → 环境默认 model 兜底（不指定 model）
                → surface：「X model 限额耗尽，进度已保存，额度恢复后 resumeFromRunId」
                  → [额度恢复] resumeFromRunId 续跑（§13h）
```

**关键设计**：

1. **`model_unavailable` 新 status**（§4.1/§4.4）：agent 遇 quota/rate-limit → 返回 `status:'model_unavailable'`（非 blocked/failed）。workflow 路由：`model_unavailable → halt`（不升级、不降级）。
2. **双重限额检测**：agent 自报 `model_unavailable` + workflow 捕获 agent() 抛错（限额常致 agent 直接抛错而非返回 status）。workflow 判断错误关键词（quota / rate-limit / 429 / overloaded / **中文 router 限额**：`使用上限|限额|额度|超出.*限制`，本机 router 返回中文错误，旧 `isQuotaError` 正则不认 → 不归类 → 顶层 uncaught crash）。
   - **`dispatchImpl` null guard**（2026-07-01）：router 限额中文错误常被 agent runtime 吞为空响应（`agent()` 返回 null 而非 throw）。dispatchImpl 不处理 null → 返回 null → 顶层 `boot.halted`/`impl.halted` crash。修：`agent()` 返回 null → 视作 `model_unavailable` halt（覆盖 bootstrap + 所有 task dispatch），不依赖 isQuotaError 认中文（双保险）。
   - **`dispatchImpl` retryModel 机制**（2026-07-03）：null 响应不总是限额——也可能是**模型能力不足**（如 qwen3.7-plus 跑 bootstrap 被 router "Repetitive tool calls" 400 中断）。旧逻辑一律 halt → 弱模型永远无法完成复杂任务。修：`dispatchImpl(prompt, opts, model, retryModel = null)` 新增第 4 参数——`agent()` 返回 null 时若 `retryModel` 非空且 ≠ `model`，用 `retryModel` **重试一次**（仅一次，不循环）；quota 错误仍在第一层 catch halt（不浪费更强模型额度）。bootstrap 调用传 `'opus'` 作 retryModel（sonnet→opus 升级路径）。测试：`docs/superpowers/workflows/tests/dispatchImpl-retry.test.js`（8 个场景覆盖）。日志：重试时 `log()` 打 `⚠ label: model returned null (capability failure likely), retry with retryModel`。
   - **`normalizeCompleted` 分隔符兼容**（2026-07-01）：bootstrap agent 返回 completed id 偶用连字符（`01-T1`），旧正则只认斜杠 `/` → 漏过 normalize → 与 taskKey `plan-01/T1` 不等 → 已完成 task 误判 pending → 重做 → 脏工作树 + OSCILLATING。修：正则 `[\/\-]+` 兼容两种分隔符。识别侧 `extractTaskKey` 不变（commit convention 固定斜杠）。
3. **fallback 链 [opus, sonnet, haiku] 仅用于 halt/finalReport 保存**（逐一尝试）：
   ```javascript
   async function finalReportWithFallback(ctx) {
     for (const m of ['opus', 'sonnet', 'haiku']) {
       try {
         return await agent(buildPrompt('finalReport', ctx),
                            {schema: SCHEMAS.finalReport, model: m, label: `final-report:${m}`})
       } catch (e) { log(`finalReport ${m} 不可用: ${errStr(e)}, 试下一个`) }
     }
     log('fallback 链全失败，用环境默认 model 保存')
     try {
       return await agent(buildPrompt('finalReport', ctx),
                          {schema: SCHEMAS.finalReport, label: 'final-report:default'})
     } catch (e) {
       log(`✗ 环境默认 model 也失败，finalReport 无法保存: ${errStr(e)}`)
       return null  // QC2: 全链失败返回 null，调用方检查并 log 致命错误
     }
   }
   ```
   halt()/done 调用 finalReportWithFallback，须检查返回值（null 时 log 致命错误，manifest 未写入）。**确保至少 haiku（最便宜、限额最宽）或环境默认能保存进度**。fallback 链**只此一处用途**——runTask 的主 agent 调用**不**用 fallback（不降级继续开发）。
4. **runTask 限额捕获 + agent_error 分类**（P0-3/P0-4，第 6 轮）：implementor/review/commit/gate 的 agent() 包 try/catch。捕获错误经 `classifyThrown(e)`（`isQuotaError` ? `model_unavailable` : `agent_error`）分流：
   - **限额错误** → 返回 `{halted:true, reason:'model_unavailable', diag:{model, error}}` → halt → finalReportWithFallback 保存。
   - **非限额错误**（TypeError/ReferenceError 等真实 bug）→ 返回 `{halted:true, reason:'agent_error', diag:{model, error}}` → halt。**`agent_error` 语义**：orchestrator 代码或 agent runtime 异常（非限额、非任务难度），用户 resume 无效（相同 bug 会复现），须人工修代码。旧代码一律标 `model_unavailable` → 误导用户无效 resume。
   - **`dispatchImpl` 非 quota 异常须封装 agent_error 返回（不 throw）**（P0-4）：旧代码 `throw e` → 顶层 catch 一律标 `model_unavailable`（误判）。改：dispatchImpl catch 块按 `isQuotaError` 分流，封装 `{halted:true, reason:'agent_error'/'model_unavailable'}` 返回；顶层 catch 仅兜底 runtime 异常（同样按 quota 分流）。retry 路径同样封装 agent_error。
5. **resume 走 §13h**：额度恢复后 `resumeFromRunId`，native resume 重跑中断 task（此时额度可用）。manifest 的 `quota_exhausted` 字段提醒用户确认恢复再续跑。`agent_error` halt 的 task 不建议直接 resume（须先修 bug）。

**blocked_info + 工作树脏状态**：`halt()` 给 `blocked_info` 填 `likely_source`（基于 reason 的确定性映射：`implementor changes` / `gate restored` / `bootstrap frontmatter` / `unknown`——纯字符串映射，**非 dirty 推断**）。finalReport 写 `blocked.md` 时跑 `git status --porcelain` + `git diff --stat`（ground truth，best-effort 失败不阻塞 manifest），结果写 Working Tree 段 + 接手指引。`likely_source`（语义线索）与 git status（真实状态）并存：用户既有定位线索，也有真实脏状态。orchestrator 无 shell，git 探查委托 finalReport agent（与 gate/commit/bootstrap 跑命令同路径）。

**UX 附带**（解决长任务中断根因）：bootstrap/implementor 跑长命令（uv sync/build）前 `log()` 打「预计 N 分钟」；`workflow.config.json` 可选加 `task_timeout`（超时 surface）。

## 3. 测试策略

| 层级 | 时机 | 命令来源 | 说明 |
|---|---|---|---|
| task 级 | implementor 内 TDD | project config `test_command` + 具体文件 | 秒级反馈 |
| plan 级 | plan 全部 task 完成后 | project config `full_test_command` | **独立 gate**：由独立 subagent 在最新 commit SHA 上重跑（非信任 implementor 自报） |
| 跨 plan | 全部 plan 完成后 | `full_test_command --cov` | 含覆盖率 |

**plan 级独立 gate 的必要性**（DX3）：orchestrator(JS) 无法独立验证 implementor 的 `tests_exit_code` 声称。plan 级 gate 由独立 subagent 在 committed SHA 上重跑 pytest，Bash 真实 exit code 是 ground truth。

## 4. Agent Boundary Protocol（核心 runtime 契约）

orchestrator 是 JS sandbox：**无 fs、无 subprocess、无 Date.now/Math.random**。所有 IO（git/test/diff/读文件）必须委托 subagent。orchestrator 只能通过 `agent()` 返回值感知世界。

### 4.1 返回值契约

**每个** `agent()` 调用必须返回 JSON：

```json
{
  "status": "ok" | "failed" | "blocked" | "needs_context" | "model_unavailable" | "done_with_concerns",
  "evidence": {
    "tests_exit_code": 0,
    "commit_sha": "abc1234",
    "files_changed": ["app/domain/ssq.py"],
    "pytest_summary": "=== 12 passed, 0 failed in 0.3s ==="
  },
  "diagnostics": {
    "blocked_category": "interface" | "file" | "spec" | "dependency" | "external" | null,
    "last_error": "app/domain/ssq.py does not exist",
    "suggested_fix": "check plan ordering — Plan 02 creates this module",
    "files_touched": ["app/domain/ssq.py", "tests/test_ssq.py"]
  },
  "summary": "实现了双色球分区比对，12 测试全绿"
}
```

- orchestrator 的所有 gate（测试通过/review 绿/commit 成功）基于 `evidence`，**非叙述**
- `tests_exit_code` 由 subagent 实际执行测试命令获得（真实 exit code）
- `commit_sha` 让 resume 和验证可追溯
- `diagnostics` 供 orchestrator 路由 + final report 可操作性（§8/§9）；`null` 表示不适用

### 4.2 IO 委托边界

| 操作 | 谁执行 | orchestrator 如何感知 |
|---|---|---|
| 写代码/跑测试 | implementor subagent | 返回 `{status, evidence.tests_exit_code}` |
| 读 diff/spec | review subagent | 返回 `{status: ok/failed, issues:[...]}` |
| git commit | commit subagent | 返回 `{status, evidence.commit_sha}` |
| 读 config/plan/git log | bootstrap subagent | 返回结构化 `{config, plans, completed}` |
| 跑全量测试 | gate subagent | 返回 `{status, evidence.pytest_summary}` |
| grep/glob/LSP（NEEDS_CONTEXT 兑现） | context-fetcher subagent | 返回 `{status:ok, context:"..."}` |

**orchestrator 永不直接 grep/glob/read/git**——原 §2.2 NEEDS_CONTEXT 表中"orchestrator 用 grep 搜索"在 runtime 下不可行，全改为 context-fetcher subagent dispatch（§8）。

### 4.3 Workflow runtime 约束清单

| 约束 | 设计如何尊重 |
|---|---|
| 无 fs | 所有读操作委托 subagent（§4.2） |
| 无 Date.now/Math.random | 不做时间戳/随机；run manifest 的时间由 subagent 写入 |
| agent() 上限 min(16,cpu-2) | serial task → 同时仅 1 task in flight；每 task 最多 implementor + 3 review 并行 + simplify + commit ≈ 6 agent，远低于上限 |
| 无持久化 | 状态靠 git log + run manifest（subagent 写盘，§6/§9） |

**代码分层（实现约束）**：纯函数/SCHEMAS/PROMPTS 的真源是 `docs/superpowers/workflows/lib.js`（ES module，`node --test` 单测）；`run-plans.js` inline 复制它们（runtime 禁模块 import），`sync.test.js` 守护字节一致。**纯决策**（如 `collectReviewFindings`/`classifyThrown`/`reviewHaltReason`/`reviewHaltForEmptyFailed`，不调 `agent()`）进 lib.js 可测；**runtime 胶水**（`safeAgent`/`dispatchImpl`，调 `agent()`）只能留 run-plans.js（lib.js 是纯模块不能调 runtime 全局）。`agent_error`（agent() 抛非 quota 异常，catch 块构造）/ `review_empty`（agent() 静默空返回，如 thinking-only 空响应——模型在 thinking 块里"以为"调了 StructuredOutput 实际无 tool_use 块；瞬态模型 hiccup）/ `review_failed_no_findings`（agent 明确判 failed 但 `issues`/`silent_failures` 空——`reviewHaltForEmptyFailed` 构造，第二道守卫）是 orchestrator-internal sentinel，绕过 schema 校验，不入 review schema enum。`reviewHaltReason` 扫三类 review 的 status：`agent_error > model_unavailable > review_empty`，全合法非 sentinel → null；`reviewHaltForEmptyFailed` 随后查 failed-but-0-findings → `review_failed_no_findings`。两道守卫防 review 哑火（旧逻辑漏过 null status / 空诊断 → 不 halt → implementor 跑空修复 → max rounds 误 halt）。

### 4.4 orchestrator 状态管理

orchestrator 维护 in-memory 状态（JS 变量），从 agent 返回值更新，据此做路由：

```javascript
// orchestrator in-memory state（perTask 嵌套结构，task 开始时初始化）
let state = {
  currentTask: null,
  completed: [],              // 已完成 task key（plan-XX/TY）from bootstrap or commit
  plans: [],                  // bootstrap 解析的 plan 列表（P0-2，§13a main 流程 `state.plans = boot.evidence.plans`，finalReport stateJson 须含 plans 保 manifest 完整性）
  perTask: {},                // {taskKey: {planId, status, model, review_rounds, files_touched_per_round,
                              //   review_history, commit_sha, simplify_reverted, simplify_review_findings,
                              //   destructive_review_failed, destructive_review_findings, concerns, blocked_info}}
                              //   taskKey = `plan-{seq}/{task.id}`（plan-scoped，防跨 plan 同名 task 覆盖）
  failedApproaches: {},       // {taskKey: [{reason, error}]} 跨 session 失败方案（bootstrap 扫 runs/ 提取，key 已 plan-scoped）
  taskLessons: {},            // {taskKey: [lesson]} bootstrap 按 task 标题关键词匹配（S3：存储用 plan-scoped key）
  taskWriteFiles: {},         // {taskKey: [file]} bootstrap 提取的 plan declared write_files（S3：存储用 plan-scoped key）
  config: null,               // workflow.config.json（test_command/spec_path/...）
  runTs: null,                // 运行时间戳（get-ts agent 生成，非 string 降级 'unknown-ts'，S2）
}
// perTask[taskKey] 初始化（runTask 顶部，ensurePerTaskDefaults helper，Q5 DRY）：status='in_progress'，所有持久化字段初始化为默认值
// （false/[]/null），确保 manifest JSON 序列化时字段完整、schema 稳定。

// 路由逻辑（伪代码，实现见 run-plans.js runTask）
switch (agentReturn.status) {
  case 'ok' | 'done_with_concerns':
    // done_with_concerns: 记 concerns，继续进 review（不 halt）
    // ok: review round 全 ✅ → next phase (simplify/commit/next-task)
    break
  case 'blocked':
    if (model === 'opus') halt()  // opus 仍 blocked → halt（model 是局部变量，非 state 字段）
    else retry_with_opus()        // 升级
    break
  case 'needs_context':
    dispatch_context_fetcher(agentReturn.diagnostics)
    retry_with_more_context()
    break
  case 'failed':
    // commit failed / test failed → retry once → BLOCKED
    break
  case 'model_unavailable':
    // 限额耗尽（§2.4）→ halt（不升级不降级）→ fallback 链 [opus,sonnet,haiku] 仅保存进度
    halt_via_fallback()
    break
}

// 振荡检测（§13g 实际算法，非伪代码）：
// detectOscillation(filesTouchedPerRound) —— length<3 → 不振荡；
// 规则 1：同文件出现在 ≥3 个 round → 振荡；规则 2：连续 2 round files 完全重叠且 ≥2 → 振荡。
// 详见 §13g。allGreen break 必须在 detectOscillation 之前（防收敛误报，REGRESSION 测试守护）。
```

**orchestrator 不"理解"返回值语义**——它只做模式匹配（status enum → 路由）。诊断（为什么 blocked、是否振荡）由 agent 在 `diagnostics` 字段预计算，orchestrator 据此路由。

**review_history（可观测性，2026-07-01）**：每轮 review 后 `summarizeReviewRound(round, spec, qual, hunt)` 把三类 reviewer 的 `{status, findings:[{title,severity}]}` 摘要 push 进 `state.perTask[task].review_history`（复用 `findingsOf` 归一化，丢 fix/file/source 控体积），随 manifest 持久化。**动机**：OSCILLATING / 收敛 halt 后需精确定位振荡点（哪个 reviewer 在哪点 flip-flop）；旧 `review_rounds` 只存 int 计数，T3 halt 时无法还原分歧点、需考古推断。摘要只留 title+severity（诊断定位所需），完整 findings（含 fix/file）走 implementor 反馈管道不持久化。`summarizeReviewRound` 是 lib.js 纯函数（`node --test` 单测）+ run-plans.js inline（sync.test 守护）。

**halt 自动提炼 lesson（2026-07-03，§5.4）**：旧 finalReport step5 在 halt 时自动追加 `lessons_path` 条目（`title=halt reason, detail=last_error, status=active`）——但 halt reason（如 `OSCILLATING`）是事件标识、非可复用知识，自动写入产生废条目（title/detail 均为 `OSCILLATING`）污染知识库、被 bootstrap 匹配注入 implementor 噪音。2026-07-01 修复为"完全不自动写、靠人工复盘"。2026-07-03 再改为"halt 时调 `lessonDistiller` agent 自动提炼可复用根因"：distiller 过滤瞬态事件（review_empty/model_unavailable）+ 语义去重对比现有 lessons → append/update/skip 决策 → distiller 自读写 `lessonsPath`（§5.4 写盘契约，不经过 finalReport）。distiller 失败/限额 → best-effort 跳过，不阻塞 manifest 写入。`lessons_auto_distill` config 开关控制（默认 true）。详见 §5.4。

## 5. Task 执行流程（bounded review rounds，max 可配）

```
┌─────────────────────────────────────────────────────────┐
│ orchestrator: 派发前写 per-task 状态 {in_progress, pre_task_sha} │
│ 派发 implementor(model = task.model || 'sonnet')         │
└──────────────────────┬──────────────────────────────────┘
                       ▼
              status == BLOCKED?
              ┌─── yes ──→ 升级 opus 重派 → 仍 BLOCKED → halt（§2.3）
              no
              ▼
┌─────────────────────────────────────────────────────────┐
│ review rounds (max N，默认 4，可配 0=无限):              │
│   round N: spec ‖ quality ‖ hunter 并行（同 snapshot）    │
│     全 ✅ → break                                        │
│     任一 ❌ → implementor 修复 → round N+1（针对新 tree） │
│       最后 1 轮 fix（有限模式 round===maxRounds-1 /      │
│       无限模式 round>=4）强制 opus（§5.3）               │
│     任一 review 异常/空响应 → halt（reviewHaltReason:    │
│       agent_error > model_unavailable > review_empty）   │
│     任一 review failed 但 0 findings → halt              │
│       （reviewHaltForEmptyFailed: review_failed_no_...）  │
│   max N 耗尽仍有 ❌ → BLOCKED（maxRounds=0 无此 halt，   │
│     仅靠 detectOscillation 同文件 ≥3 round halt）        │
└──────────────────────┬──────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────┐
│ commit subagent: git status check → test → commit        │
│   → 返回 {commit_sha}（review 全绿是必要非充分条件）      │
│   （§5.2 方案 C：commit 提前到 simplify 前）              │
└──────────────────────┬──────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────┐
│ simplify? (orchestrator 计数, max 1)                      │
│   → git status --porcelain 独立验证是否动代码（不信任自报）│
│     有改动 → 触发 review round（spec‖qual‖hunt 并行）    │
│       全绿 → git commit --amend（合并 simplify 改动）    │
│       失败 → git reset --hard HEAD（回退 simplify 改动） │
│     无改动 → 跳过 review（省成本）                       │
└──────────────────────┬──────────────────────────────────┘
                       ▼
              mark task committed → 写 manifest → 下一个 task
```

### 5.1 为什么 review 是 rounds 而非独立循环（Eng C1）

review 不是对静态 artifact 的独立观察，是对**当前 working tree** 的 verdict。若 spec 触发修复，tree 变，并行的 quality/hunter 的 verdict 立刻过时。"每 reviewer 独立循环直到✅"自相矛盾。正确形态：**round 内并行（同 snapshot）、round 间串行（任一 ❌ 触发新 round）、max N（默认 4，可配）**。

### 5.2 simplify 流程：方案 C（commit 提前 + git status 触发 review + amend/checkout 回退，2026-07-03）

**旧设计（无条件 review）问题**：orchestrator 无法 diff，不能信任 simplify 自报"是否改了代码"，故无条件触发 review round——但 simplify 多数情况只改注释/格式，3 个 review 全绿空跑，成本浪费。

**方案 C 新流程**（commit 提前到 simplify 前，git status --porcelain 独立验证触发 review）：

```
review 全绿 → COMMIT → SIMPLIFY → git status --porcelain subagent 检查有无改动（staged+unstaged）
                                         ├─ 有改动 → re-review (spec‖qual‖hunt, runReviewRound helper)
                                         │            ├─ 全绿 → git commit --amend + git rev-parse HEAD（validateAmendResult 校验 40 位 hex）
                                         │            └─ 失败 → git reset --hard HEAD && git clean -fd（validateCheckoutResult 兜底验证 porcelain 空）
                                         │                          └─ checkout 失败/工作树仍脏 → halt 'simplify checkout failed'
                                         └─ 无改动 → 跳过 review（省成本）
   diff subagent 失败 → halt 'simplify diff check failed'；amend 失败/SHA 格式错 → halt 'simplify amend failed'
```

**关键设计决策**：

1. **commit 提前**：旧流程 `simplify → commit` 让 commit 兜底 simplify 失败；新流程 `commit → simplify` 让 simplify 改动可被 git status 精确观测。commit 后工作树本应 clean，simplify 若动代码 → git status --porcelain 非空 → 触发 review。
2. **git status --porcelain 独立验证**（不信任 `simp.evidence.changed` 自报）：用 subagent 跑 `git status --porcelain`，返回 `{changed, files}`。同时检测 staged + unstaged 改动（防 simplify 误 `git add` 后 staged 改动被 `git diff --stat` 漏检）。runtime 禁止 orchestrator 直接调 shell，故走 subagent。三个 subagent（diff/amend/checkout）均用 `safeAgent` 包装 + 返回值验证 + 失败 halt（§5.2 健壮化）。
3. **amend/checkout 回退**：
   - review 全绿 → `git add -A && git commit --amend --no-edit` 合并 simplify 改动到 HEAD（保持单 commit 原子性，避免 HEAD 多一个 simplify commit 污染 git log）；amend 后用 `git rev-parse HEAD` 独立获取新 SHA + `validateAmendResult` 纯函数校验 40 位 hex（不信任 agent 自报字符串）
   - review 失败 → `git reset --hard HEAD && git clean -fd` 丢弃 simplify 改动（同时清理 tracked 修改、staged changes、untracked 新文件，HEAD 不变，task 仍 committed 在原 SHA）；checkout 后再跑 `git status --porcelain` 兜底验证工作树真 clean（`validateCheckoutResult` 纯函数校验 porcelain 空，防 ok:true 谎报）。**Q11（第 4 轮）**：须用 `git reset --hard HEAD`（非旧 `git checkout -- .`）——同时清理 staged changes（simplify 误 `git add` 时 staged 区域残留，旧 git-checkout 只回退 tracked 工作区修改不清理 staged）。
   - review 失败时 simplify review findings 持久化到 `perTask.simplify_review_findings`（不丢，用户无需考古 transcript）
4. **省成本**：simplify 没动代码则跳过 review（旧设计无条件 review 的成本浪费被消除）。
5. **max 1** 由线性流程强制（simplify 在 runTask 中单次调用，非计数器；旧 `simplify_invoked` 字段已删除）。

**为什么不用 simplify 自报 changed 触发 review**：simplify agent 的 `evidence.changed` 是 LLM 自报，存在误报风险（模型可能说没改但实际改了，或反之）。git status 是 ground truth，确定性触发。

**状态机守卫不变**：simplify review 轮同样接 `reviewHaltReason` + `reviewHaltForEmptyFailed` 双守卫（同主 review 轮，复用 `runReviewRound` helper）。

### 5.2.1 Destructive Change Detection（commit 后额外 review round）

commit agent 在 step 2.6 用 `git diff HEAD --numstat` 检测 `deleted_code` / `file_deletion` / `signature_change`，写入 `diagnostics.destructive_changes`。非空 → 触发 spec+quality+hunter 并行额外 review（`runReviewRound` helper，`:destructive` label）。

> **S4（第 4 轮）**: 须用 `git diff HEAD`（非 `git diff --cached`）——文件未 `git add` 时 `--cached` 永远为空，destructive review 永不触发。`git diff HEAD` 对比工作树与 HEAD，无需暂存即可检测改动。

**与主 review 轮的区别**：
- 不计入 `review_rounds` 限额（destructive 是增强保护，非主流程）
- 失败/异常**不 halt**，记录 `destructive_review_failed` + `destructive_review_findings` 到 perTask 继续
- 全绿则正常继续下一 task

**设计动机**：destructive change（删除代码/文件/改签名）风险高于普通改动，值得独立 review。但不阻断流程——若 review 发现问题，记录到 manifest 供用户事后审计；若 review 异常（model 限额等），不应阻塞已 committed 的 task。

### 5.3 review max rounds 可配 + 最后 1 轮 fix 强制 opus（2026-07-03）

**配置**：`workflow.config.json` 的 `review_max_rounds` 字段，由 `resolveMaxRounds(config)` 解析：

| 配置值 | maxRounds | 行为 |
|---|---|---|
| 未配 / null / 非数字 | 4（默认） | 4 轮后 halt |
| 正整数 N | N | N 轮后 halt |
| 0 / 负数 | 0（无限模式） | 永不因轮数 halt，仅靠 detectOscillation（同文件 ≥3 round）halt |

**最后 1 轮 fix 强制 opus**（`fixModelForRound(round, baseModel, maxRounds)`）：

- **有限模式**（maxRounds > 0）：`round === maxRounds - 1` 是最后 1 轮 fix，强制 opus。默认 maxRounds=4 → round=3 的 fix 升级 opus。
- **无限模式**（maxRounds=0）：前 3 轮用 baseModel 给 sonnet 充分尝试；从第 4 轮起强制 opus（前 3 轮没修好说明问题复杂，后续用 opus 提升修复质量，直到 detectOscillation halt 或全绿）。
- **向后兼容**：maxRounds 未传 → 默认 3（旧行为：round=2 升级 opus）。
- 已是 opus 的 task 返回 'opus'（语义等价，不重复升级）。是升级而非降级，与 §2.4「限额 halt 不降级」纪律一致。

**设计理由**：难度递增——round 1 没修好说明问题比预期复杂，最后 1 轮用最强 model 降低 halt 概率（halt 后人工介入成本 >> opus 调用）。无限模式前 3 轮给 sonnet 充分机会，避免过早烧 opus；第 4 轮起问题已证明复杂，持续用 opus 直到振荡 halt。

**无限模式的防线**：maxRounds=0 时永不因轮数 halt，仅靠 `detectOscillation`（§13g，同文件 ≥3 round 或连续两轮完全重叠 ≥2 文件）halt。这是独立于 maxRounds 的防线，保证无限模式不会真的无限循环。

### 5.4 lesson 自动提炼（distiller agent，2026-07-03）

**问题**：旧版"halt 时自动写 lesson"产生废条目（title/detail 均为 halt reason 如 `OSCILLATING`），污染知识库；2026-07-01 改为"完全不自动写、靠人工复盘"，但人工复盘成本高、可复用知识流失。

**新机制**：halt 时调 `lessonDistiller` agent（opus，单次 `agent()` 调用，非 fallback 链——§2.4 fallback 链仅用于 finalReport 保存进度）自动提炼可复用根因，distiller 自读写 `lessonsPath`（best-effort，失败/限额跳过不阻塞 finalReport）。

**配置**：`workflow.config.json` 的 `lessons_auto_distill` 字段，由 `resolveLessonsAutoDistill(config)` 解析：

| 配置值 | 行为 |
|---|---|
| 未配 / true / 非布尔 | true（默认启用） |
| 显式 false | 关闭（旧行为：不自动提炼） |

**distiller agent 设计**：
- **模型**：opus（提炼需推理，非模式匹配）
- **触发**：仅 halt 且 `lessons_auto_distill=true` 且 `lessons_path` 非空（done 不触发）
- **输入**（`distillLessonInput` 构造）：`halt_info` + `review_history` + `failed_approaches` + 现有 lessons（distiller 自己读 `lessonsPath` 解析）
- **任务**：
  1. 识别可复用根因（silent-failure / dependency / convention / test-strategy / other）
  2. 过滤瞬态事件（review_empty / model_unavailable / 单次 hiccup → skip）
  3. 语义去重对比现有 lessons：重叠 → `update`（补证据）；全新 → `append`；无价值 → `skip`
- **输出**：`decisions: [{action, id, title, detail, source?, category?, update_target_id?}]`
- **质量门**：RED FLAG 给正反例（❌ "OSCILLATING" 事件标签 / ✅ "DB split-commit 必须单事务" 可复用根因）

**写盘契约**（2026-07-03 修正，SH2 后）：**distiller agent 自己读写 `lessonsPath`**（有 fs 访问），orchestrator 不参与写盘。distiller 在 prompt 中明确告知"Apply decisions to lessonsPath yourself"，agent 内部完成 append/update/skip 决策并直接覆写文件。orchestrator 不再调用 `formatLessonsForDistill` / `applyLessonDecisions` / `renderLessonEntry`——这些纯函数保留在 lib.js 仅作向后兼容与单元测试，但 run-plans.js 中已无 inline 副本（死代码已清理，sync.test helper 列表已同步精简）。

**为什么 distiller 自读写盘而非走 finalReport**：finalReport 是写 manifest/blocked.md 的 agent，让它再调 `applyLessonDecisions` 写 lessons.md 会把决策与应用耦合——finalReport 失败时 lesson 也丢。distiller 自读写盘让 lesson 提炼成为独立 best-effort 通道：distiller 失败/限额 → best-effort 跳过 lesson 更新，不阻塞 finalReport 写 manifest/blocked.md。

**lessons.md schema 增强**（向后兼容）：
```markdown
## L-<ts>
title: <可复用知识>
detail: <根因+场景+修法>
source: <plan-X/T-Y@<run_ts>>  # 新字段（可选）
category: <silent-failure|dependency|convention|test-strategy|other>  # 新字段（可选）
status: active
```
- bootstrap 关键词匹配逻辑不变（按 title/detail）
- `source` / `category` 缺失时 bootstrap 解析兼容

**纯函数测试**（lib.js，`node --test`）：`distillLessonInput` / `formatLessonsForDistill` / `applyLessonDecisions` 共 13 个测试覆盖全分支（lib.js 真源保留测试，确保函数契约可追溯）；sync.test.js 守护 inline 副本一致性 + finalReport prompt 含 distiller 调用断言。

## 6. Bootstrap & Resume（崩溃恢复）

**Resume 主路径 = Workflow 原生 `resumeFromRunId`**（journal 缓存命中：完成的 agent() 秒回，首个改动的调用及之后重跑）。run manifest 降级为**仅供人读的观测日志**，不参与 resume 决策（决策依据见 §13h）。

### 6.1 启动 / Resume 流程

```
[首启动] bootstrap subagent:
  读 project config（§11）→ {test_command, spec_path, ...}
  读 plan files → 生成/读取 frontmatter（§13e，含叶子 task 解析规则）→ {plans: [{id, tasks:[{id, model}]}]}
  读 git log → completed_task_ids（via commit convention feat(plan-X/T-Y)）
  检查 dirty_tree（兜底，见 6.2）
  返回 {config, plans, completed, dirty_tree}
                       ▼
orchestrator 路由:
  committed task（git log 有对应 commit）→ 跳过
  未完成 task → 正常派发

[崩溃后 resume] Workflow({scriptPath, resumeFromRunId: <runId>}):
  - 未改动的 agent()（同 prompt+opts）→ journal 秒回缓存
  - 首个未完成 agent → 重跑（崩在 implementor 后/commit 前会自动重跑 implementor，覆盖半成品）
  - bootstrap 重新读 git log 确认 completed（git log 是 ground truth，不读 manifest）
```

> **提交 scope 区分（infra vs business）**：bootstrap 的 `extractTaskKey` 仅识别 `feat(plan-XX/TY):` 前缀作为"已完成业务 task"（§6.1 识别约定）。workflow 自身基建改动（lib.js / run-plans.js / tests / workflow.config.json）**不得**用 `feat(plan-` 前缀——否则会被误识别为同号业务 plan 的已完成 task → bootstrap 跳过 → 漏做。基建提交约定用 `chore(workflow-NN/TN):`：`extractTaskKey` 对此前缀返回 `null` → 对 bootstrap 不可见，零碰撞风险。此约定是 `extractTaskKey` 单一识别源的对称面（emission ↔ recognition）。

### 6.2 半提交状态清理（DX5）

崩溃在 implementor 完成但 commit 未执行时，working tree 有半成品。**native resume 会重跑未完成的 implementor**，TDD 重写自然覆盖半成品，无需显式 `git reset`。兜底：bootstrap agent 检测 `dirty_tree=true` → **自愈**（orchestrator 无 shell，bootstrap agent 有 Bash 访问，执行 `git reset --hard HEAD` 清理后 re-run `git status --porcelain` 确认 clean，设 `dirty_tree=false`）；git reset 失败则保留 `dirty_tree=true` 并在 summary 记录错误。**commit 是状态原子转换**：commit subagent 返回 `{commit_sha}` 即 git commit 已落盘；崩在此点之后 → git log 有 commit → 视为 completed 跳过。

## 7. 串行调度

**plan 间串行 + plan 内 task 串行**（砍依赖图/worktree/3-slot implementor 并行）：

```
plan 列表（按声明顺序）
  → plan 01（串行 task）→ 全量测试 gate
  → plan 02（串行 task）→ 全量测试 gate
  → ...（后 plan 依赖前 plan，共享 app/ 代码库，不能并行）
```

### 7.1 为什么砍依赖图（CEO）

依赖图为**并行调度**服务。既然 implementor 串行，依赖图失去调度用途。串行执行靠 plan 作者声明顺序（writing-plans 约定），顺序错则 implementor 自然 BLOCKED 兜底。review 并行是固定 3 agent，无需图。

### 7.2 为什么砍 worktree isolation（CEO）

全盘开发场景下 worktree 合并复杂度爆炸（task 间隐式耦合、review 链断裂、commit 时序混乱）。upstream Red Flag L242 禁止并行 implementor 正是此权衡。唯一安全的并行是 review（只读同 diff），无需 worktree。

## 8. 错误恢复

### 8.1 NEEDS_CONTEXT（全改 agent dispatch）

orchestrator 无法 grep/glob/read（runtime 约束）。NEEDS_CONTEXT 的 5 类缺失全委托 context-fetcher subagent：

| 缺失类型 | context-fetcher 动作 |
|---|---|
| 文件/路径 | grep/glob 搜索工作区，返回路径 |
| 接口签名 | LSP 或正则提取函数/类签名 |
| spec/文档 | 读 plan 引用的文档路径，提取段落 |
| 依赖状态 | 读前序 task 代码，提取关键实现 |
| 外部知识 | Context7/WebSearch 查询 |

orchestrator 判断何时升级 BLOCKED（无硬重试上限，保留 5 分类作判断脚手架）。

### 8.2 BLOCKED

```
sonnet BLOCKED → opus 重派 → opus BLOCKED → halt
  → 写 .workflow/blocked.md（含 task/category/last_error/suggested_fix）
  → surface 用户（skip / 修改 plan / 终止）
```

## 9. 可观测性（run manifest，观测用途）

orchestrator JS 只有 `log()`。run manifest 角色 = **正常结束后的人读观测日志 + final report 数据源**，不参与 resume（resume 走 native，§6/§13h）。

```
runs/<run-id>/
  manifest.json    # workflow 结束时由 final-report subagent 一次性写：{run_ts, mode, plans, per_task:{id:{status,model,review_rounds,files_touched_per_round,review_history,commit_sha,simplify_reverted,simplify_review_findings,destructive_review_failed,destructive_review_findings,concerns,blocked_info}}, result}
  log.ndjson       # 关键 agent() 返回后 append 一行 {ts, agent_type, task_id, status, summary}（ts 由 subagent 调 Bash date 获得）
```

- **写入策略**：orchestrator 在 in-memory 累积 per-task 状态（§4.4），**仅在 workflow 结束（done/halted）时** dispatch final-report subagent 一次性写盘。砍掉逐事件的 state-updater dispatch（避免 agent 调用膨胀，§13h）。
- 崩溃中途：manifest 可能未写 → 靠 `/workflows` 实时面板 + native resume + git log，不依赖盘上 manifest
- 振荡检测（DX6）：in-memory `files_touched_per_round` 实时算（§13g），OSCILLATING 时 orchestrator `log()` + surface，不依赖盘上 manifest
- final report = manifest digest（final-report subagent 读 orchestrator 传入的 in-memory 累积值写盘并输出）

## 10. Post-Plan 阶段（全自动模式）

**全自动**：workflow 全程无人工介入，`requesting-code-review` 由用户结束后主动发起。

> **TODO（未实现，第 6 轮 wontfix）**：当前 run-plans.js 降级为 `finalReport` only（写 manifest + digest），下方 pr-test-analyzer / verification-before-completion / finishing-a-development-branch 流程尚未实现。跨 plan 全量测试由每个 plan 末尾的 plan gate（§3）部分覆盖。实现优先级低（plan gate 已覆盖核心测试门禁），待后续按需补全。详见 §14 wontfix 决策记录。

```
所有 plan 完成
  → 跨 plan 全量测试（含覆盖率）
  → pr-test-analyzer（测试覆盖质量 + 断言质量）
  → verification-before-completion（确认无遗留）
  → finishing-a-development-branch（清理合并）
  → 最终报告（manifest digest）

[workflow 结束后，用户可选]
  → requesting-code-review → receiving-code-review
```

## 11. 项目配置契约

### 11.1 project config（workflow.config.json）

```json
{
  "test_command": "uv run pytest",
  "full_test_command": "uv run pytest -v",
  "build_command": "uv build",
  "lint_command": "uv run ruff check .",
  "spec_path": "docs/superpowers/specs/2026-06-16-lottery-notification-design.md",
  "language": "python",
  "extra_lint_commands": ["uv run lint-imports"],
  "reference_paths": ["docs/reference/lottery-rules.md"],
  "silent_failure_context": ["<项目特定静默失败纪律数组>"],
  "lessons_path": "docs/superpowers/lessons.md",
  "lessons_auto_distill": true,
  "review_max_rounds": 4,
  "schema_tool": "alembic",
  "model_paths": ["app/models/"],
  "migration_paths": ["alembic/versions/"]
}
```

启动 config smoke：`test_command --collect-only`（fail loud，2 秒发现 typo 而非 20 分钟）。

> **可选字段**：`extra_lint_commands`（架构纪律 lint，如 domain 层纯度护栏）/ `reference_paths`（spec 外权威文档）/ `silent_failure_context`（项目特定静默失败纪律，hunter 优先核查）/ `lessons_path`（跨任务失败知识库，bootstrap 按 task 关键词匹配注入 implementor，halt 时调 lessonDistiller agent 自读写 `lessonsPath` 提炼可复用根因，§5.4 写盘契约）/ `lessons_auto_distill`（控制 halt 时自动提炼 lesson，默认 true，§5.4）/ `review_max_rounds`（review 最大轮数，默认 4，0=无限模式靠 detectOscillation 防线 halt，§5.3）/ `schema_tool` + `model_paths` + `migration_paths`（schema 迁移一致性检查三件套，gate 用 `git diff --name-only HEAD~1..HEAD` 查 model 有变更但无迁移文件 → `migration_missing=true` 触发 gate failed）均可选，不配即对应 prompt 段消失（条件渲染）。通用性原则：项目特有内容只走 config，不写进 prompt。

> **跨 session 失败方案追踪（failed_approaches）**：bootstrap agent 扫 `runs/*/manifest.json` 提取历史 halt task 的 `failed_approach`（reason + error），注入 implementor prompt 的 `failedApproaches` 占位符。此机制让新 run 的 implementor 知晓历史失败方案、避免重蹈覆辙。`state.failedApproaches` 由 bootstrap 填充，runTask 通过 `formatFailedApproaches` 序列化注入。

### 11.2 plan frontmatter（YAML，writing-plans 产出约定）

```yaml
---
id: plan-01
tasks:
  - id: T1
    model: sonnet       # enum: sonnet|opus，optional，未标注默认 sonnet
  - id: T6
    model: opus         # 高风险 task（安全/算法/集成）标注 opus
---
# Plan 01 正文（人读 Markdown，原 writing-plans 输出）
```

frontmatter 是机器读，正文是人读，单文件不分离。

### 11.3 validator + onboarding

> **TODO（未实现，第 6 轮 wontfix）**：`workflow validate-plans` 与 `workflow init` 命令尚未实现。当前 bootstrap agent 在启动时做 frontmatter schema 校验（fail loud），但无独立 CLI 命令。实现优先级低（bootstrap 已覆盖核心校验路径），待后续按需补 CLI。详见 §14 wontfix 决策记录。

- `workflow validate-plans docs/superpowers/plans/`：校验 frontmatter schema + task header 匹配 + ID 唯一。启动前必跑，schema 错 fail loud。
- `workflow init`：读现有 plan 目录，emit config 模板 + 跑 validator + 打印 next steps。TTHW 目标 15-20 分钟。

## 12. Red Flags（继承 upstream + 新增）

继承 upstream（绝不跳过 review / spec 未过不启动 quality / 不带问题推进 / 不模糊通过 / 不自审替代正式 review / 不忽视 BLOCKED），新增：

- **绝不**信任 subagent 叙述替代 evidence（gate 基于 `evidence` 字段）
- **绝不**让 simplify 改代码后跳过重跑 review
- **绝不**并行派发多个 implementor（serial，尊重 upstream Red Flag L242）
- **绝不**静默降级 modelHint（unknown → fail loud）
- **绝不**无限 BLOCKED 升级（opus 仍 BLOCKED → halt）

## 13. 待细化

### 13a. workflow script JS 骨架

**产物**：`.claude/workflows/run-plans.js`（单文件，~1300 行，第 7 轮实际行数）。通过 `Workflow({scriptPath, args})` 触发，`resumeFromRunId` 续跑（§13h）。

**顶层结构**：

```
meta{}             // name/description/phases（纯字面量）
SCHEMAS{}          // 每个 agent 的 evidence schema（JS 对象常量，喂 agent({schema})）
PROMPTS{}          // 每个 role 的 prompt 模板字符串（内联，因 orchestrator 无 fs）
state{}            // §4.4 in-memory 状态
detectOscillation(filesTouchedPerRound)   // §13g，copy
buildPrompt(role, ctx)                    // 用 ctx 填充 PROMPTS[role]
runReviewRound(taskId, cfg, plan, fc, concernsHint, labelSuffix, phaseLabel)  // 并行 spec‖qual‖hunt + 双守卫（主轮/simplify/destructive 共用）
validateAmendResult(result)               // amend subagent 返回值校验纯函数（Q8）
validateCheckoutResult(result)            // checkout subagent 返回值校验纯函数（Q4/Q8）
main()                                    // leafTasks(plan) 由 bootstrap agent 完成（§13e），runtime 不 inline
```

**主流程（main）**：

```javascript
phase('Bootstrap')
const boot = await agent(buildPrompt('bootstrap', {configPath, plansDir}),
                         {schema: SCHEMAS.bootstrap, label: 'bootstrap'})
Object.assign(state, {config: boot.config, plans: boot.plans})

for (const plan of boot.plans) {
  phase(`Plan ${plan.id}`)
  for (const task of leafTasks(plan)) {
    if (boot.completed.includes(task.id)) { log(`skip ${task.id}`); continue }
    const r = await runTask(plan, task)
    if (r.halted) return halt(plan, task, r)         // 写 .workflow/blocked.md + surface（§8.2）
  }
  // plan 级独立 gate（§3）：committed SHA 上重跑 full_test_command
  const gate = await agent(buildPrompt('gate', {plan}),
                           {schema: SCHEMAS.gate, label: `gate:${plan.id}`})
  if (gate.evidence.tests_exit_code !== 0) return halt(plan, null, {reason: 'plan gate failed', gate})
}

phase('Finalize')
await agent(buildPrompt('finalReport', {state}), {schema: SCHEMAS.finalReport, label: 'final-report'})
return {result: 'done', state}
```

**runTask(plan, task) 控制流（要点）**：

> **wontfix（第 6 轮，风险高）**：runTask 函数较长（~180 行）但拆分风险高——控制流涉及 5 阶段（implementor → review rounds → commit → simplify → destructive review）+ 多异常分支（halt/agent_error/model_unavailable/review_empty/OSCILLATING），拆分易引入状态转移 bug。已通过抽出 helper（`runReviewRound`/`ensurePerTaskDefaults`/`dispatchImpl`/`safeAgent`/`classifyThrown`/`reviewHaltReason`/`reviewHaltForEmptyFailed`/`haltLikelySource`/`formatFindings`/`formatConcernsHint`）降低单函数复杂度，主控制流保留单函数保证状态转移可读性。详见 §14 wontfix 决策记录。

1. **implementor**（`model = task.model || 'sonnet'`）；`status='blocked'` → §2.3 升级链（sonnet→opus→halt，带上限）
2. **review rounds（max N，默认 4，可配 0=无限；§5.3）**：每轮 `spec ‖ quality ‖ hunter` 并行（同 tree snapshot）；收集各 `diagnostics.files_touched` → 振荡检测（§13g）；全绿 break，任一 ❌ → implementor 修复（`collectReviewFindings` 归一化三类 review 的不同 diagnostics key + `formatFindings` 序列化为可读反馈；`fetchedContext` 独立占位符传参考上下文，不混入 fixIssues）→ 下一轮；最后 1 轮 fix 强制 opus（有限模式 `round===maxRounds-1` / 无限模式 `round>=4`，§5.3）；max N 耗尽 → halt（maxRounds=0 无此 halt，仅靠 detectOscillation）。**review 异常/空响应 → halt**：`reviewHaltReason` 扫 status，sentinel 优先级 `agent_error > model_unavailable > review_empty`——`review_empty` 守 status 缺失/为空/非法（含 thinking-only 空响应：agent() 静默返回 null/空对象，无异常），防 hunter/quality/spec 哑火（旧逻辑漏过 → 不 halt → implementor 跑空修复 → max rounds 误 halt）。**第二道守卫 `reviewHaltForEmptyFailed`**（在 `reviewHaltReason` 之后、fix-round 之前）：任一 review `status==='failed'` 但该 review 的 findings 产出 0 项（`issues`/`silent_failures` 空）→ halt `review_failed_no_findings`。堵「合法 failed + 空诊断」漏过 `reviewHaltReason`（status 合法不 halt）→ `collectReviewFindings` 空 → implementor 收「0 项发现」跑空修复 → max rounds 误 halt 的同类洞；与 `review_empty` 区分（后者 status 缺失，本守卫 status 合法但无发现）。**schema items 约束**：quality/hunter 的 `issues`/`silent_failures` 元素强制对象 `{title, fix}`（specReview 保持字符串模板走 `reviewSchema`，qualityReviewer 拆出 `qualityReviewSchema`）——防 LLM 返回纯字符串/缺 fix/用错字段名 → `collectReviewFindings` 的 `it.title||String(it)` 兜底为 `[object Object]`。
3. **commit（§5.2 方案 C：commit 提前到 simplify 前）**：status check → test → `git commit -m "feat(plan-X/T-Y): ..."`；返回 `commit_sha` → `state.perTask[taskKey].status='committed'`（中间态）
4. **simplify（max 1，§5.2 方案 C）**：commit 后工作树 clean → simplify agent → `git status --porcelain` subagent 独立验证是否动代码（不信任 `simp.evidence.changed` 自报）；有改动 → 触发 review round（`runReviewRound` helper，spec‖qual‖hunt 并行 + 双守卫）；全绿 → `git commit --amend`（合并 simplify 改动到 HEAD，`git rev-parse HEAD` 独立获取新 SHA + 正则校验 40 位 hex）；失败 → `git reset --hard HEAD && git clean -fd` 回退 simplify 改动（HEAD 不变）；无改动 → 跳过 review（省成本）。三个 subagent（diff/amend/checkout）均用 `safeAgent` 包装 + 返回值验证 + 失败 halt（Q1-Q6 健壮化，Q11 用 git reset --hard HEAD 同时清理 staged changes）。
5. **destructive review（§5.2.1）+ 终态**：commit 返回 `destructive_changes` 非空 → 触发额外 review round（`runReviewRound` helper）；失败/异常**不 halt**，记录 `destructive_review_failed` + `destructive_review_findings`（统一 shape `{source, severity, title, fix}`，Q14）到 perTask 继续。runTask 全流程完成（commit + simplify + destructive review）→ `state.perTask[taskKey].status='done'`（Q8 终态，区分"已提交但 simplify/destructive 未完成"与"全流程完成"）。

**halt(plan, task, r)**（终止 helper）：累积 `blocked_info`（task/category/last_error/suggested_fix，来自 `r.diag`）到 state → 若 `lessons_auto_distill=true` 且 `lessons_path` 非空，调 `lessonDistiller` agent（distiller 自读写 `lessonsPath`，best-effort，§5.4）→ dispatch `finalReport`（halted 模式）写 manifest + `.workflow/blocked.md`（§8.2）+ `log()` surface → return。收敛后无 state-updater，**中途终止也走 finalReport 写盘**。

**3 个关键约束的落地**：

- **prompt 内联**（`PROMPTS{}` + `buildPrompt`）：orchestrator 无 fs（§4.3），prompt 不能读外部 .md，只能内联字符串常量。
- **schema 内联**（`SCHEMAS{}`）：每个 agent 的 evidence schema 作 JS 对象，`agent(p, {schema})` 在 tool-call 层校验（不信任叙述）。
- **resume 不自建**：靠 Workflow 原生 `resumeFromRunId`（§13h）。骨架不读/写 manifest 做 resume；manifest 仅 final-report 结束写（§13d）。

**验证节奏（首次端到端）**：

1. T1 only（`args:{plan:'01', tasks:['T1']}`）→ 验证单 task 闭环（bootstrap→implementor→review→commit→gate）
2. 全 Plan 01（7 叶子 task：T1/T2/T3/T4a/T4b/T4c/T4d）→ plan gate 全绿 + manifest 写出
3. 中途 kill → `resumeFromRunId` 续跑验证

### 13b. subagent prompt 模板

**载体**：`PROMPTS{role: 模板字符串}` 内联在 run-plans.js，`buildPrompt(role, ctx)` 用 ctx 填充。**不外置 .md**（orchestrator 无 fs）。

**继承 upstream**（subagent-driven-development）：implementor / spec-reviewer / quality-reviewer 三角色 prompt 骨架沿用（TDD 纪律、逐行 spec 比对、质量门禁、Red Flags）。

**收敛后 10 类 agent 的 prompt 职责 + evidence 契约**：

| role | prompt 核心职责 | model | evidence 必填（§13c） |
|---|---|---|---|
| `bootstrap` | 读 config（§11.1）+ plan files（§13e 生成 frontmatter、叶子优先解析）+ git log（completed）+ dirty_tree（自愈 `git reset --hard HEAD`，§6.2）+ in_progress（扫 `runs/*/manifest.json`，§13c） | sonnet | config, plans[], completed[], dirty_tree, in_progress, failed_approaches[], task_lessons[], task_write_files[] |
| `implementor` | TDD（RED→GREEN→REFACTOR），跑 `test_command`，self-review；**`build_command` 非空时 GREEN 前跑构建验证可构建性**（P1-11，§11.1 build_command 字段）；BLOCKED 时填 diagnostics；done_with_concerns 时填 concerns[] | task.model\|\|sonnet | tests_exit_code, files_changed[], pytest_summary |
| `specReviewer` | 代码 vs spec（`spec_path`）逐行比对，记 files_touched | opus | status, issues[] |
| `qualityReviewer` | 质量/架构/边界/类型/不可变性，记 files_touched | opus | status, issues[] |
| `hunter` | 静默失败/吞错/bad fallback（ECC silent-failure-hunter 语义），记 files_touched。**只读审查**：禁止跑 pytest/ruff/build（那是 implementor/gate 职责）；项目特定静默失败纪律经 `silent_failure_context` config 注入，hunter 优先核查 | sonnet | status, silent_failures[] |
| `simplify` | 精简代码（ECC simplify 语义），**如实报 `changed(bool)`** | **sonnet**（硬编码，非 task model，P1-5） | changed, files_changed[] |
| `commit` | status check → test → `git commit -m "feat(plan-X/T-Y): ..."`，返回 commit_sha；检测 out_of_scope / destructive_changes | **sonnet**（硬编码，非 task model，P1-5） | commit_sha, committed_files[], tests_at_commit |
| `contextFetcher` | NEEDS_CONTEXT 兑现（grep/glob/LSP/读 spec/Context7/WebSearch） | **sonnet**（硬编码，非 task model，P1-5） | context |
| `gate` | committed SHA 上 `git checkout <sha>` + 跑 `full_test_command` + **`git checkout -` 回原 HEAD**，真实 exit code（§3 独立 gate） | sonnet | tests_exit_code, pytest_summary |
| `finalReport` | 读 orchestrator 传入的 in-memory state，写 `runs/<run-id>/manifest.json`（§13d），输出 digest | sonnet | — |

> 收敛后原 `state-updater` / `manifest-writer` 已并入 `finalReport`（§13h 砍逐事件写盘）。

**agentType 映射**（实现时核对实际可用 subagent_type）：

- `hunter` → default workflow subagent + prompt（**wontfix，第 6 轮**：runtime 限制——Workflow 工具的 subagent dispatch 不支持自定义 `agentType: 'silent-failure-hunter'`，ECC skill-based agent 无法在 workflow runtime 直接调用。改用 default subagent + hunter prompt 模拟 silent-failure-hunter 语义。详见 §14 wontfix 决策记录。）
- `simplify` → default workflow subagent + prompt（simplify 是 skill 非 agent type）
- `specReviewer` / `qualityReviewer` → default + prompt + `model: 'opus'`（upstream 角色语义，无专门 agent type）
- 其余（bootstrap/implementor/commit/contextFetcher/gate/finalReport）→ default workflow subagent + prompt

**prompt 共同结构**：每个 prompt 编码 ① 角色职责边界 ② 输入 ctx 字段说明 ③ **必填 evidence 字段**（gate 据此，§4.1，绝不叙述替代 evidence）④ Red Flag 提醒（绝不跳 review / 绝不模糊通过 / BLOCKED 必填 diagnostics）。

### 13c. Agent Boundary Protocol — evidence schema per agent

每个 agent 类型的必填 `evidence` + `diagnostics` 字段：

| agent | evidence 必填 | diagnostics 必填 |
|---|---|---|
| **implementor** | `tests_exit_code`, `files_changed`, `pytest_summary` | `concerns[]`（done_with_concerns 时填，§4.4） |
| **spec-reviewer** | `status`（ok/failed） | `issues[]`（spec 不符列表） |
| **quality-reviewer** | `status`（ok/failed） | `issues[]`（质量问题列表） |
| **silent-failure-hunter** | `status`（ok/failed） | `silent_failures[]`（静默失败列表） |
| **simplify** | `changed`（bool）, `files_changed[]` | — |
| **commit** | `commit_sha`, `committed_files[]`, `tests_at_commit` | `out_of_scope[]`, `destructive_changes[]`（§5.2.1） |
| **gate**（plan 级） | `tests_exit_code`, `pytest_summary`, `lint_results[]`（每条 `{command, exit_code}`，§13b gate prompt） | — |
| **bootstrap** | `config`, `plans[]`, `completed[]`, `in_progress`, `dirty_tree`, `failed_approaches[]`, `task_lessons[]`, `task_write_files[]` | — |
| **context-fetcher** | — | `context`（补充的上下文文本） |
| **state-updater** | — | —（manifest 写入确认由 orchestrator 校验） |

**evidence vs diagnostics**：evidence 是 gate 决策依据（硬数据），diagnostics 是诊断辅助（软信息，供 final report 和振荡检测）。

- [ ] **13f. workflow init / validate-plans 命令实现**（实现期）

### 13d. run manifest 写入策略（收敛后）

**决策（§13h）**：manifest 是观测日志，不参与 resume。砍掉逐事件 state-updater dispatch，改为 orchestrator in-memory 累积 + workflow 结束时一次性写盘。

```
runs/<run-id>/
  manifest.json     # 仅 workflow 结束时写：{run_ts, mode, plans, per_task:{id:{status,model,review_rounds,files_touched_per_round,review_history,commit_sha,simplify_reverted,simplify_review_findings,destructive_review_failed,destructive_review_findings,concerns,blocked_info}}, result}
  log.ndjson        # TODO（未实现，第 6 轮 wontfix）：关键 agent() 返回后 append 一行（ts 由 subagent 调 Bash date）。当前 orchestrator 仅 `log()`（in-memory console 输出），无 ndjson 文件写盘。实现需额外 subagent dispatch 写盘（成本 vs 观测价值不划算），且 `/workflows` 面板已提供实时观测。详见 §14 wontfix 决策记录。
```

**写入时机（收敛后）**：

| 事件 | 写入者 | 内容 |
|---|---|---|
| 每个 agent() 返回 | orchestrator（in-memory 累积） | 更新 state（§4.4）；无需 dispatch |
| 关键节点（committed/blocked/oscillating） | orchestrator `log()` + 累积 | in-memory，不写盘 |
| workflow 结束（done/halted） | final-report subagent（唯一写盘者） | 读 orchestrator 传入的 in-memory 累积值，写 manifest.json + 输出 digest |

**砍掉的 dispatch**（原设计的 state-updater 逐事件写盘）：task 开始/round 结束/simplify 完成等不再单独 dispatch 写盘 agent。这些状态都在 orchestrator in-memory，结束时一次写。

**manifest 字段说明**（第 7 轮 spec 对齐代码）：
- `run_ts`：运行时间戳（get-ts agent 生成，作 `runs/<run-id>/` 目录名基础；非 string 降级 `'unknown-ts'`，S2）
- `mode`：`done` | `halted`（区分正常结束与中断，finalReport prompt 据此决定是否写 blocked.md）
- `result`：digest 摘要（counts: done/blocked, total tasks, per-plan gate result）
- `plans` / `per_task`：state.plans / state.perTask 序列化（§4.4）

**为什么能砍**：resume 走 Workflow native `resumeFromRunId`（journal 缓存），不依赖盘上 manifest 的 in_progress 字段。盘上 manifest 唯一消费者是"人读"和"final report"。

**resume 时 manifest 的角色**：不读。bootstrap 只读 git log 确认 completed（git log 是 ground truth）。manifest 崩溃中途可能未写，不影响 resume。

> orchestrator JS 写盘的通用约束仍成立：无 fs（§4.3），所有盘上写入由 subagent 执行。收敛后唯一写盘 subagent = final-report。

### 13e. writing-plans frontmatter 增强

**推荐方案：workflow 启动时自动生成（bootstrap subagent），不修改 writing-plans 技能。**

理由：writing-plans 是 upstream superpowers 技能，修改它侵入性大且需要维护 fork。workflow 框架应对现有 plan 格式自适应。

**bootstrap subagent 的 frontmatter 生成逻辑**：

```
对每个 plan 文件：
  如果已有 YAML frontmatter（--- 开头）→ 直接读取
  如果没有 → 生成：
    1. 提取 task 列表（**叶子优先规则**，处理 Plan 01 式嵌套）：
       - 扫描 `## Task N` 与 `### Task NX` headers
       - 若某 `## Task N` 下存在 `### Task NX` 子 task → 只入子 task（NX），父 Task N 视为容器不入列
       - 若某 `## Task N` 下无子 task → 入 Task N 本身
       - ID 规范化：`plan-01/T1`、`plan-01/T4a`（保留字母后缀），全局唯一
    2. 对每个 task，判断 modelHint：
       - 标题/描述含"安全/加密/认证/JWT/CSRF/Fernet" → opus
       - 标题/描述含"算法/比对/策略/边界" → opus
       - 标题/描述含"集成/多模块/接口设计" → opus
       - 其余 → 不标注（默认 sonnet）
    3. 生成 YAML frontmatter 写回 plan 文件头部
    4. 返回 {plans: [{id, tasks:[{id, model}]}]}
```

**frontmatter 写回是幂等的**：已有 frontmatter 的 plan 不重写。resume 时 bootstrap 直接读已有 frontmatter（不重新生成）。

**判断标准的一致性**：§2.2 来源链的判断标准与这里一致（安全/算法/集成 → opus）。标准硬编码在 bootstrap prompt 里，不是配置文件（足够简单，不需要可配置）。

### 13g. 振荡检测算法

orchestrator JS 在每个 review round 结束后调用，纯数组操作（无 fs）：

```javascript
function detectOscillation(filesTouchedPerRound) {
  // filesTouchedPerRound = [['f1','f2'], ['f2','f3'], ['f1','f2']]
  if (filesTouchedPerRound.length < 3) return { oscillating: false }

  // 规则 1：同文件出现在 ≥3 个 round → 振荡
  const fileRoundCount = {}
  for (const [i, files] of filesTouchedPerRound.entries()) {
    for (const f of files) {
      if (!fileRoundCount[f]) fileRoundCount[f] = []
      fileRoundCount[f].push(i)
    }
  }
  for (const [file, rounds] of Object.entries(fileRoundCount)) {
    if (rounds.length >= 3) {
      return { oscillating: true, reason: `${file} touched in ${rounds.length} rounds`, file, rounds }
    }
  }

  // 规则 2：连续 2 个 round 的 files 高度重叠（≥2 文件且完全重叠）→ 可能振荡
  for (let i = 1; i < filesTouchedPerRound.length; i++) {
    const prev = new Set(filesTouchedPerRound[i - 1])
    const curr = new Set(filesTouchedPerRound[i])
    const overlap = [...curr].filter(f => prev.has(f))
    if (overlap.length >= 2 && overlap.length === curr.size) {
      return { oscillating: true, reason: `consecutive rounds fix same files: ${overlap.join(',')}`, files: overlap }
    }
  }

  return { oscillating: false }
}
```

**触发动作**：振荡 → orchestrator halt（写 per-task `{status: OSCILLATING, blocked_info}` + finalReport 写盘 manifest/blocked.md + `log()` surface）。

**⚠️ allGreen 必须在 detectOscillation 之前**（收敛误报根治，2026-07-01）：run-plans.js review rounds 循环里 `if (allGreen(spec, qual, hunt)) break` 在 `detectOscillation` 检查之前调用。否则 r3 三 reviewer 全 ok 时，先被「核心文件被审 ≥3 轮」OSCILLATING 截胡、allGreen break 永远轮不到 → 收敛误报。Plan 05 跑 T2/T5 时反复踩此（r3 全 ok 仍 halt）。提前后：
- **收敛**（r3 全 ok）→ allGreen break 放行，不进 OSCILLATING；
- **真矛盾**（reviewer 持续分歧，如 T7 claims 时区：quality 要 CST「雷 2 同行同时区」vs hunter 要 naive UTC「主流惯例」，CLAUDE.md 两规则冲突）→ 永不全绿 → 落进 detectOscillation halt，让人介入打破规则冲突（手动选一侧 + commit + 全新跑续）。

**files_touched 的来源**：review agent 的返回值 `diagnostics.files_touched` 由 orchestrator 追加到 in-memory `filesTouchedPerRound[]`。review agent 在检查 diff 时顺带记录变更文件列表。注：files_touched 记录的是「review 审查的文件」，核心文件每轮都被审查是正常的——故 detectOscillation 的「≥3 轮」规则只对「真矛盾/反复修同一文件不收敛」有意义，对「收敛」由前置 allGreen 兜底放行。

### 13h. Resume 机制收敛决策

**问题**：原设计（§6/§9/§13d）用自建 run manifest + bootstrap 重读做 resume，与 Workflow 工具原生 `resumeFromRunId`（journal 缓存）重叠冲突。逐事件 state-updater 写盘还让每 task agent 调用数从 §4.3 的 ~6 膨胀到 ~10+。

**决策**：
1. **Resume 主路径 = Workflow 原生 `resumeFromRunId`**。完成的 agent()（同 prompt+opts）秒回缓存，首个改动的调用及之后重跑。
2. **Manifest 降级为观测日志**：仅供人读 + final report 数据源，不参与 resume 决策。
3. **砍掉逐事件 state-updater dispatch**：orchestrator in-memory 累积状态（§4.4），仅 workflow 结束时 final-report subagent 一次写盘（§13d）。
4. **半提交清理（§6.2）**：native resume 重跑未完成 implementor 自然覆盖半成品，无需显式 git reset；bootstrap 仅做 dirty_tree 兜底检查。

**影响**：
- §4.3 的"每 task ≈6 agent"估算成立（无 state-updater 膨胀）
- §4.4 in-memory state 是执行期间的事实来源；native resume 重跑 bootstrap 重建之
- §6/§9/§13d 已据此收敛

**代价**：崩溃中途无盘上 manifest（靠 `/workflows` 面板 + native resume + git log）。

**Resume 能力边界（与 Claude Code 官方规范对齐）**：native `resumeFromRunId` **仅在同一个 Claude Code session 内有效**——退出 CC 后再启动会 fresh start（规范原文："Resume works within the same Claude Code session. If you exit Claude Code while a workflow is running, the next session starts the workflow fresh."）。故跨 session 重启时 journal 缓存失效，但 **bootstrap 以 git log 为 ground truth 识别已完成 task**（§6.1），fresh start 也能正确跳过已 commit 的 task，韧性不依赖 resume。manifest 仅供人读观测，不参与 resume 决策——这与规范的"runtime tracks each agent's result"不冲突，因为我们不读 manifest 做 resume。

## 14. Wontfix / TODO 决策记录（第 6 轮 review 收敛）

本节记录第 6 轮 Spec/Quality Review 后的 wontfix 与未实现决策，含决策理由 + 复盘触发条件。每个条目标注 inline 引用位置（§章节号）便于追溯。

### 14.1 未实现功能（标 TODO，待后续按需补全）

| # | 项目 | inline 位置 | 决策理由 | 复盘触发 |
|---|---|---|---|---|
| 1 | `log.ndjson` 逐事件写盘 | §13d | 当前 orchestrator 仅 `log()`（in-memory console 输出），无 ndjson 文件写盘。实现需额外 subagent dispatch 写盘（成本 vs 观测价值不划算），且 `/workflows` 面板已提供实时观测。 | 用户需要离线分析 agent 调用链 / `/workflows` 面板不可用 |
| 2 | `workflow validate-plans` / `workflow init` CLI | §11.3 | 当前 bootstrap agent 在启动时做 frontmatter schema 校验（fail loud），无独立 CLI 命令。bootstrap 已覆盖核心校验路径。 | 多人协作时需 plan 作者独立校验 / 新项目 onboarding 需 init 模板 |
| 3 | post-plan 全流程（pr-test-analyzer / verification-before-completion / finishing-a-development-branch） | §10 | 当前 run-plans.js 降级为 `finalReport` only。跨 plan 全量测试由每个 plan 末尾的 plan gate（§3）部分覆盖。 | 需要测试覆盖质量分析 / 自动清理开发分支 / 跨 plan 覆盖率报告 |

### 14.2 Wontfix（设计决策，不修）

| # | 项目 | inline 位置 | 决策理由 | 复盘触发 |
|---|---|---|---|---|
| 4 | `hunter` agentType 用 default subagent + prompt（非 ECC `silent-failure-hunter`） | §13b | runtime 限制——Workflow 工具的 subagent dispatch 不支持自定义 `agentType: 'silent-failure-hunter'`，ECC skill-based agent 无法在 workflow runtime 直接调用。default subagent + hunter prompt 模拟 silent-failure-hunter 语义已足够（hunter schema 强制 `silent_failures[]` items 结构）。 | Workflow 工具支持自定义 agentType / ECC 提供 workflow-native agent type |
| 5 | `runTask` 不拆分（保留单函数 ~180 行） | §13a | 控制流涉及 5 阶段 + 多异常分支（halt/agent_error/model_unavailable/review_empty/OSCILLATING），拆分易引入状态转移 bug。已通过抽出 10+ helper（`runReviewRound`/`ensurePerTaskDefaults`/`dispatchImpl`/`safeAgent`/`classifyThrown`/`reviewHaltReason`/`reviewHaltForEmptyFailed`/`haltLikelySource`/`formatFindings`/`formatConcernsHint`）降低单函数复杂度。主控制流保留单函数保证状态转移可读性。 | runTask 行数继续膨胀（新增阶段/异常分支）/ 状态转移 bug 频发 |
| 6 | `task.model` enum 校验不做（bootstrap 直接读） | §2.2 | invalid 值（如 `claude-3.5`）会透传到 `dispatchImpl` → `agent()` 调用失败 → halt `agent_error`。fail loud 语义已达成（不静默降级），只是错误路径稍晚（agent dispatch 时 vs bootstrap 校验时）。低风险。 | invalid model 值导致难以定位的 halt 频发 / bootstrap 阶段需提前 fail |
| 7 | `contextFetcher` 硬编码 sonnet 无 BLOCKED 升级链 | §13b | contextFetcher 是 NEEDS_CONTEXT 兑现（grep/glob/LSP/读 spec/Context7/WebSearch），查询通常简单，sonnet 足够。复杂查询失败直接 halt 是设计选择——NEEDS_CONTEXT 兑现不应过度工程，且 halt 后用户可手动补上下文再续跑。implementor 有升级链因任务难度递增（最后 1 轮 fix 强制 opus），contextFetcher 是辅助查询不需要同路径。 | contextFetcher 复杂查询频繁 halt / 实际需要 opus 级推理的 NEEDS_CONTEXT 场景增多 |

### 14.3 误报（review 判断有误，不修）

第 6 轮 review 中以下发现经核查为误报，不修：

- **`fixModelForRound` 无限模式 round>=4 升级 opus**：review 认为"无限模式不应强制 opus，应保持 baseModel"。但设计意图是"前 3 轮没修好说明问题复杂，后续用 opus 提升修复质量"（§5.3 设计理由），且与 §2.4「限额 halt 不降级」纪律一致（是升级而非降级）。spec §5.3 已有明确说明，不修。
- **`haltLikelySource` 用 Set 替代大正则**：review 认为"Set 查找比正则慢"。但 Set.has 是 O(1) 且代码可读性远优于大正则，P1-8 修复正是为了"防误匹配 + 易维护"。性能差异在 halt 路径（非热路径）可忽略，不修。
- **`buildPrompt` undefined/null 渲染为空串**：review 认为"应保留 `{{k}}` 占位符以便 debug"。但实现已区分：key 缺失（`k in ctx=false`）保留 `{{k}}` 占位符（debug 用）；key 存在但值为 undefined → 空串（防 `"undefined"` 污染 prompt）。两者并存，不冲突。
