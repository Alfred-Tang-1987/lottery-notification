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
| silent-failure-hunter | — | 静默失败/吞错/bad fallback | 每 review round |
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
  "status": "ok" | "failed" | "blocked" | "needs_context",
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

runtime 无持久化。状态靠 **git log + run manifest**（subagent 写盘）。

### 6.1 启动 / Resume 流程

```
bootstrap subagent:
  读 project config（§11）→ {test_command, spec_path, ...}
  读 plan files frontmatter → {plans: [{id, tasks:[{id, model}]}]}
  读 git log → completed_task_ids（via commit convention feat(plan-X/T-Y)）
  读 run manifest → in_progress task（若有）
  返回 {config, plans, completed, in_progress, dirty_tree}
                       ▼
orchestrator:
  completed task → 跳过
  in_progress task（崩溃在 implementor-return 和 commit 间）:
    git reset --hard <pre_task_sha>（清理半提交）→ 重派
  committed task → 永不重跑
  强制重跑某 task → workflow reset --task T4（删 manifest 行）
```

### 6.2 半提交状态清理（DX5）

崩溃在 implementor 完成但 commit 未执行时，working tree 有半成品。resume 时 orchestrator 读 per-task 状态（`in_progress` + `pre_task_sha`），`git reset --hard <pre_task_sha>` 清理后重派。**commit 是状态原子转换**：manifest 写 committed 行 + git commit 在同一 orchestrator turn；turn 崩则 manifest 无行 → 重跑。

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

## 9. 可观测性（run manifest）

orchestrator JS 只有 `log()`。强制 run manifest 让崩溃后可取证（DX2）：

```
runs/<run-id>/
  manifest.json    # {current_plan, current_task, task_status, review_round, last_commit_sha}
  log.ndjson       # 每 agent() 调用一行 {ts, agent, status, summary}
  per-task/<id>.json  # {status, review_rounds, files_touched_per_round, status_history}
```

- 崩溃后：`cat runs/latest/manifest.json` 看状态，非翻 transcript
- 振荡检测（DX6）：同文件连续 round 反转 diff → 标 OSCILLATING + surface
- final report 是 manifest 的 digest

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
  "language": "python"
}
```

启动 config smoke：`test_command --collect-only`（fail loud，2 秒发现 typo 而非 20 分钟）。

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

- [ ] **13a. workflow script JS 骨架**（实现期）：bootstrap → serial plan/task → review rounds → commit → gate → post-plan
- [ ] **13b. subagent prompt 模板**（实现期）：继承 subagent-driven-development 的 implementer/spec/quality prompt + 新增 bootstrap/commit/context-fetcher/gate/manifest-writer prompt

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

### 13d. run manifest 读写时机

```
runs/<run-id>/
  manifest.json           # {run_id, started_at, plans:[], status:'running'|'done'|'halted'}
  per-task/<task-id>.json  # {task_id, plan_id, status, model, review_rounds, files_touched_per_round, commit_sha, blocked_info}
  log.ndjson              # 每 agent 调用一行 {ts, agent_type, task_id, status, summary}
```

**写入时机与写入者**：

| 事件 | 写入者 | 写入内容 |
|---|---|---|
| workflow 启动 | bootstrap subagent | 创建 manifest.json（新 run-id）；resume 时读已有 manifest |
| task 开始 | state-updater subagent | 写 per-task `{status: in_progress, model, plan_id}` |
| review round 结束 | review agent（在返回值里） | orchestrator 更新 in-memory `files_touched_per_round`；state-updater 写 per-task round 信息 |
| simplify 完成 | state-updater subagent | 更新 per-task simplify 状态 |
| commit 成功 | commit subagent | 更新 per-task `{status: committed, commit_sha}` |
| BLOCKED | state-updater subagent | 更新 per-task `{status: blocked, blocked_info}` |
| workflow 结束 | final-report subagent | 更新 manifest `{status: done/halted}`，输出 digest |

**为什么不让 orchestrator JS 写**：orchestrator 无 fs（runtime 约束）。所有盘上写入必须由 subagent 执行。orchestrator 通过 prompt 指令告诉 subagent 写什么（把 manifest 路径和目标内容编码进 agent prompt）。

**resume 读取**：bootstrap subagent 读 manifest + git log，交叉验证（manifest 说 committed 但 git log 无对应 commit → 以 git log 为准）。

### 13e. writing-plans frontmatter 增强

**推荐方案：workflow 启动时自动生成（bootstrap subagent），不修改 writing-plans 技能。**

理由：writing-plans 是 upstream superpowers 技能，修改它侵入性大且需要维护 fork。workflow 框架应对现有 plan 格式自适应。

**bootstrap subagent 的 frontmatter 生成逻辑**：

```
对每个 plan 文件：
  如果已有 YAML frontmatter（--- 开头）→ 直接读取
  如果没有 → 生成：
    1. 提取 `## Task N` / `### Task N` headers → task 列表
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

**触发动作**：振荡 → orchestrator 不自动 halt（避免丢失 in-progress work），而是写 per-task `{status: OSCILLATING, oscillation_info}` + surface 用户决定（继续/跳过/干预）。

**files_touched 的来源**：review agent 的返回值 `diagnostics.files_touched` 由 orchestrator 追加到 in-memory `filesTouchedPerRound[]`。review agent 在检查 diff 时顺带记录变更文件列表。
