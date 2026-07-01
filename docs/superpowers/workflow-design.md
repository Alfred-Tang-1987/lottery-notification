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
- loader subagent 校验 enum，unknown → fail loud（不静默降级）

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
     return await agent(buildPrompt('finalReport', ctx),
                        {schema: SCHEMAS.finalReport, label: 'final-report:default'})
   }
   ```
   halt() 调用 finalReportWithFallback。**确保至少 haiku（最便宜、限额最宽）或环境默认能保存进度**。fallback 链**只此一处用途**——runTask 的主 agent 调用**不**用 fallback（不降级继续开发）。
4. **runTask 限额捕获**：implementor/review/commit/gate 的 agent() 包 try/catch。捕获错误若含限额关键词 → 返回 `{halted:true, reason:'model_unavailable', diag:{model, error}}` → halt → finalReportWithFallback 保存。非限额错误 → 正常错误处理（retry/halt）。
5. **resume 走 §13h**：额度恢复后 `resumeFromRunId`，native resume 重跑中断 task（此时额度可用）。manifest 的 `quota_exhausted` 字段提醒用户确认恢复再续跑。

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
  "status": "ok" | "failed" | "blocked" | "needs_context" | "model_unavailable",
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
// orchestrator in-memory state（每个 plan 执行开始时重置）
let state = {
  current_plan: null,
  current_task: null,
  tasks_completed: [],      // from bootstrap or commit subagent returns
  task_status: {},          // {taskId: 'pending'|'in_progress'|'committed'|'blocked'}
  review_round: 0,          // 当前 task 的 review round 计数
  simplify_invoked: false,  // 当前 task simplify 是否已触发
  files_touched_per_round: [], // [[file1,file2], [file2,file3], ...] 用于振荡检测
}

// 路由逻辑
switch (agentReturn.status) {
  case 'ok':
    // review round 全 ✅ → next phase (simplify/commit/next-task)
    break
  case 'blocked':
    if (state.upgraded_to_opus) halt()  // opus 仍 blocked → halt
    else retry_with_opus()              // 升级
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

// 振荡检测（§9/§13g）
if (state.files_touched_per_round.length >= 2) {
  const prev = state.files_touched_per_round.at(-2)
  const curr = state.files_touched_per_round.at(-1)
  if (same_files_reversing(prev, curr)) {
    mark_oscillating()
    halt_and_surface()
  }
}
```

**orchestrator 不"理解"返回值语义**——它只做模式匹配（status enum → 路由）。诊断（为什么 blocked、是否振荡）由 agent 在 `diagnostics` 字段预计算，orchestrator 据此路由。

## 5. Task 执行流程（bounded review rounds）

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
│ review rounds (max 3):                                   │
│   round N: spec ‖ quality ‖ hunter 并行（同 snapshot）    │
│     全 ✅ → break                                        │
│     任一 ❌ → implementor 修复 → round N+1（针对新 tree） │
│     任一 review 异常/空响应 → halt（reviewHaltReason:    │
│       agent_error > model_unavailable > review_empty）   │
│     任一 review failed 但 0 findings → halt              │
│       （reviewHaltForEmptyFailed: review_failed_no_...）  │
│   max 3 耗尽仍有 ❌ → BLOCKED                            │
└──────────────────────┬──────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────┐
│ simplify? (orchestrator 计数, max 1)                      │
│   → 无条件触发 review round（不信任 simplify 自报是否改） │
└──────────────────────┬──────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────┐
│ commit subagent: git status check → test → commit        │
│   → 返回 {commit_sha}（review 全绿是必要非充分条件）      │
└──────────────────────┬──────────────────────────────────┘
                       ▼
              mark task committed → 写 manifest → 下一个 task
```

### 5.1 为什么 review 是 rounds 而非独立循环（Eng C1）

review 不是对静态 artifact 的独立观察，是对**当前 working tree** 的 verdict。若 spec 触发修复，tree 变，并行的 quality/hunter 的 verdict 立刻过时。"每 reviewer 独立循环直到✅"自相矛盾。正确形态：**round 内并行（同 snapshot）、round 间串行（任一 ❌ 触发新 round）、max 3**。

### 5.2 为什么 simplify 后无条件重跑 review（Eng M10）

orchestrator 无法 diff，不能信任 simplify 自报"是否改了代码"。simplify 后无条件触发 review round（若没改，3 个 review 快速 ✅；若改了，重新验证）。max 1 由 orchestrator JS 计数强制。

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

崩溃在 implementor 完成但 commit 未执行时，working tree 有半成品。**native resume 会重跑未完成的 implementor**，TDD 重写自然覆盖半成品，无需显式 `git reset`。兜底：bootstrap 检测 `dirty_tree` 且该 task 无对应 commit → 视为半提交，`git reset --hard HEAD` 清理后由 native resume 重派。**commit 是状态原子转换**：commit subagent 返回 `{commit_sha}` 即 git commit 已落盘；崩在此点之后 → git log 有 commit → 视为 completed 跳过。

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
  manifest.json    # workflow 结束时由 final-report subagent 一次性写：{run_id, plans, per_task:{id:{status,model,review_rounds,files_touched_per_round,commit_sha,blocked_info}}, result}
  log.ndjson       # 关键 agent() 返回后 append 一行 {ts, agent_type, task_id, status, summary}（ts 由 subagent 调 Bash date 获得）
```

- **写入策略**：orchestrator 在 in-memory 累积 per-task 状态（§4.4），**仅在 workflow 结束（done/halted）时** dispatch final-report subagent 一次性写盘。砍掉逐事件的 state-updater dispatch（避免 agent 调用膨胀，§13h）。
- 崩溃中途：manifest 可能未写 → 靠 `/workflows` 实时面板 + native resume + git log，不依赖盘上 manifest
- 振荡检测（DX6）：in-memory `files_touched_per_round` 实时算（§13g），OSCILLATING 时 orchestrator `log()` + surface，不依赖盘上 manifest
- final report = manifest digest（final-report subagent 读 orchestrator 传入的 in-memory 累积值写盘并输出）

## 10. Post-Plan 阶段（全自动模式）

**全自动**：workflow 全程无人工介入，`requesting-code-review` 由用户结束后主动发起。

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
  "schema_tool": "alembic",
  "model_paths": ["app/models/"],
  "migration_paths": ["alembic/versions/"]
}
```

启动 config smoke：`test_command --collect-only`（fail loud，2 秒发现 typo 而非 20 分钟）。

> **可选字段**：`extra_lint_commands`（架构纪律 lint，如 domain 层纯度护栏）/ `reference_paths`（spec 外权威文档）/ `silent_failure_context`（项目特定静默失败纪律，hunter 优先核查）/ `lessons_path`（跨任务失败知识库，bootstrap 按 task 关键词匹配注入 implementor，halt 时 finalReport 自动追加新 lesson）/ `schema_tool` + `model_paths` + `migration_paths`（schema 迁移一致性检查三件套，gate 用 `git diff --name-only HEAD~1..HEAD` 查 model 有变更但无迁移文件 → `migration_missing=true` 触发 gate failed）均可选，不配即对应 prompt 段消失（条件渲染）。通用性原则：项目特有内容只走 config，不写进 prompt。

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

**产物**：`.claude/workflows/run-plans.js`（单文件，~300 行）。通过 `Workflow({scriptPath, args})` 触发，`resumeFromRunId` 续跑（§13h）。

**顶层结构**：

```
meta{}             // name/description/phases（纯字面量）
SCHEMAS{}          // 每个 agent 的 evidence schema（JS 对象常量，喂 agent({schema})）
PROMPTS{}          // 每个 role 的 prompt 模板字符串（内联，因 orchestrator 无 fs）
state{}            // §4.4 in-memory 状态
detectOscillation(filesTouchedPerRound)   // §13g，copy
buildPrompt(role, ctx)                    // 用 ctx 填充 PROMPTS[role]
leafTasks(plan)                           // §13e 叶子优先规则
main()
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

1. **implementor**（`model = task.model || 'sonnet'`）；`status='blocked'` → §2.3 升级链（sonnet→opus→halt，带上限）
2. **review rounds（max 3）**：每轮 `spec ‖ quality ‖ hunter` 并行（同 tree snapshot）；收集各 `diagnostics.files_touched` → 振荡检测（§13g）；全绿 break，任一 ❌ → implementor 修复（`collectReviewFindings` 归一化三类 review 的不同 diagnostics key + `formatFindings` 序列化为可读反馈；`fetchedContext` 独立占位符传参考上下文，不混入 fixIssues）→ 下一轮；max 3 耗尽 → halt。**review 异常/空响应 → halt**：`reviewHaltReason` 扫 status，sentinel 优先级 `agent_error > model_unavailable > review_empty`——`review_empty` 守 status 缺失/为空/非法（含 thinking-only 空响应：agent() 静默返回 null/空对象，无异常），防 hunter/quality/spec 哑火（旧逻辑漏过 → 不 halt → implementor 跑空修复 → max rounds 误 halt）。**第二道守卫 `reviewHaltForEmptyFailed`**（在 `reviewHaltReason` 之后、fix-round 之前）：任一 review `status==='failed'` 但该 review 的 findings 产出 0 项（`issues`/`silent_failures` 空）→ halt `review_failed_no_findings`。堵「合法 failed + 空诊断」漏过 `reviewHaltReason`（status 合法不 halt）→ `collectReviewFindings` 空 → implementor 收「0 项发现」跑空修复 → max rounds 误 halt 的同类洞；与 `review_empty` 区分（后者 status 缺失，本守卫 status 合法但无发现）。**schema items 约束**：quality/hunter 的 `issues`/`silent_failures` 元素强制对象 `{title, fix}`（specReview 保持字符串模板走 `reviewSchema`，qualityReviewer 拆出 `qualityReviewSchema`）——防 LLM 返回纯字符串/缺 fix/用错字段名 → `collectReviewFindings` 的 `it.title||String(it)` 兜底为 `[object Object]`。
3. **simplify（max 1，§5.2）**：无条件触发一轮 review（不信任自报 `changed`）；该轮 ❌ → 标记 `simplify_failed`，**回退委托 commit subagent**（orchestrator 无 fs，commit subagent 在 commit 前按 simplify 的 `files_changed` 先 `git checkout` 回退，再走正常 commit）；simplify 视为 no-op，用 simplify 前的 review 全绿状态继续
4. **commit**：status check → test → `git commit -m "feat(plan-X/T-Y): ..."`；返回 `commit_sha` → `state.task_status[task.id]='committed'`

**halt(plan, task, r)**（终止 helper）：累积 `blocked_info`（task/category/last_error/suggested_fix，来自 `r.diag`）到 state → dispatch `finalReport`（halted 模式）写 manifest + `.workflow/blocked.md`（§8.2）+ `log()` surface → return。收敛后无 state-updater，**中途终止也走 finalReport 写盘**。

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
| `bootstrap` | 读 config（§11.1）+ plan files（§13e 生成 frontmatter、叶子优先解析）+ git log（completed）+ dirty_tree | sonnet | config, plans[], completed[], dirty_tree |
| `implementor` | TDD（RED→GREEN→REFACTOR），跑 `test_command`，self-review；BLOCKED 时填 diagnostics | task.model\|\|sonnet | tests_exit_code, files_changed[], pytest_summary |
| `specReviewer` | 代码 vs spec（`spec_path`）逐行比对，记 files_touched | opus | status, issues[] |
| `qualityReviewer` | 质量/架构/边界/类型/不可变性，记 files_touched | opus | status, issues[] |
| `hunter` | 静默失败/吞错/bad fallback（ECC silent-failure-hunter 语义），记 files_touched。**只读审查**：禁止跑 pytest/ruff/build（那是 implementor/gate 职责）；项目特定静默失败纪律经 `silent_failure_context` config 注入，hunter 优先核查 | sonnet | status, silent_failures[] |
| `simplify` | 精简代码（ECC simplify 语义），**如实报 `changed(bool)`** | sonnet | changed, files_changed[] |
| `commit` | status check → test → `git commit -m "feat(plan-X/T-Y): ..."`，返回 commit_sha | sonnet | commit_sha, committed_files[], tests_at_commit |
| `contextFetcher` | NEEDS_CONTEXT 兑现（grep/glob/LSP/读 spec/Context7/WebSearch） | sonnet | context |
| `gate` | committed SHA 上 `git checkout <sha>` + 跑 `full_test_command` + **`git checkout -` 回原 HEAD**，真实 exit code（§3 独立 gate） | sonnet | tests_exit_code, pytest_summary |
| `finalReport` | 读 orchestrator 传入的 in-memory state，写 `runs/<run-id>/manifest.json`（§13d），输出 digest | sonnet | — |

> 收敛后原 `state-updater` / `manifest-writer` 已并入 `finalReport`（§13h 砍逐事件写盘）。

**agentType 映射**（实现时核对实际可用 subagent_type）：

- `hunter` → `agentType: 'silent-failure-hunter'`（ECC 存在）
- `simplify` → default workflow subagent + prompt（simplify 是 skill 非 agent type）
- `specReviewer` / `qualityReviewer` → default + prompt + `model: 'opus'`（upstream 角色语义，无专门 agent type）
- 其余（bootstrap/implementor/commit/contextFetcher/gate/finalReport）→ default workflow subagent + prompt

**prompt 共同结构**：每个 prompt 编码 ① 角色职责边界 ② 输入 ctx 字段说明 ③ **必填 evidence 字段**（gate 据此，§4.1，绝不叙述替代 evidence）④ Red Flag 提醒（绝不跳 review / 绝不模糊通过 / BLOCKED 必填 diagnostics）。

### 13c. Agent Boundary Protocol — evidence schema per agent

每个 agent 类型的必填 `evidence` + `diagnostics` 字段：

| agent | evidence 必填 | diagnostics 必填 |
|---|---|---|
| **implementor** | `tests_exit_code`, `files_changed`, `pytest_summary` | — |
| **spec-reviewer** | `status`（ok/failed） | `issues[]`（spec 不符列表） |
| **quality-reviewer** | `status`（ok/failed） | `issues[]`（质量问题列表） |
| **silent-failure-hunter** | `status`（ok/failed） | `silent_failures[]`（静默失败列表） |
| **simplify** | `changed`（bool）, `files_changed[]` | — |
| **commit** | `commit_sha`, `committed_files[]`, `tests_at_commit` | — |
| **gate**（plan 级） | `tests_exit_code`, `pytest_summary` | — |
| **bootstrap** | `config`, `plans[]`, `completed[]`, `in_progress`, `dirty_tree` | — |
| **context-fetcher** | — | `context`（补充的上下文文本） |
| **state-updater** | — | —（manifest 写入确认由 orchestrator 校验） |

**evidence vs diagnostics**：evidence 是 gate 决策依据（硬数据），diagnostics 是诊断辅助（软信息，供 final report 和振荡检测）。

- [ ] **13f. workflow init / validate-plans 命令实现**（实现期）

### 13d. run manifest 写入策略（收敛后）

**决策（§13h）**：manifest 是观测日志，不参与 resume。砍掉逐事件 state-updater dispatch，改为 orchestrator in-memory 累积 + workflow 结束时一次性写盘。

```
runs/<run-id>/
  manifest.json     # 仅 workflow 结束时写：{run_id, plans, per_task:{id:{status,model,review_rounds,files_touched_per_round,commit_sha,blocked_info}}, result}
  log.ndjson        # 关键 agent() 返回后 append 一行（ts 由 subagent 调 Bash date）
```

**写入时机（收敛后）**：

| 事件 | 写入者 | 内容 |
|---|---|---|
| 每个 agent() 返回 | orchestrator（in-memory 累积） | 更新 state（§4.4）；无需 dispatch |
| 关键节点（committed/blocked/oscillating） | orchestrator `log()` + 累积 | in-memory，不写盘 |
| workflow 结束（done/halted） | final-report subagent（唯一写盘者） | 读 orchestrator 传入的 in-memory 累积值，写 manifest.json + 输出 digest |

**砍掉的 dispatch**（原设计的 state-updater 逐事件写盘）：task 开始/round 结束/simplify 完成等不再单独 dispatch 写盘 agent。这些状态都在 orchestrator in-memory，结束时一次写。

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

**files_touched 的来源**：review agent 的返回值 `diagnostics.files_touched` 由 orchestrator 追加到 in-memory `filesTouchedPerRound[]`。review agent 在检查 diff 时顺带记录变更文件列表。

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
