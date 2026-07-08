# Consolidated Workflow Implementation Plan — run-plans 全量实现回顾

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 lottery-notification 仓库 `run-plans` workflow 的当前完整实现状态汇编为一份可复现的 consolidated plan。覆盖 6 个改进批次：核心 workflow（Plan 01-06）、07 优化（2026-06-30）、cross-reviewer surfacing（2026-07-05）、v3 improvements（2026-07-06）、W1 port（2026-07-07）、W1 三维复核 fixes（2026-07-07）。本 plan 为**回顾性文档**——代码已实现，文档化供新人复现整个 workflow。

**Architecture:** 两文件模式：`docs/superpowers/workflows/lib.js`（纯函数真源，node:test 可测）+ `.claude/workflows/run-plans.js`（inline 副本 + runtime 胶水，sync.test 字节守护）。Orchestrator 是 JS sandbox（无 fs、无 `Date.now()`/`Math.random()`、无 subprocess），所有 IO 委托 subagent。12 个 agent role：bootstrap / implementor / specReview / qualityReviewer / hunter / simplify / commit / contextFetcher / gate / headVerifier / finalReport / lessonDistiller。

**Tech Stack:** JavaScript (Workflow runtime sandbox), node:test, Claude Code Workflow agent dispatch (`agent()` / `parallel()` / `phase()` / `log()` / `args` 为 runtime 注入全局)

**Spec / 设计依据:**
- `docs/superpowers/workflow-design.md` §1-§14（架构 / roles / agent boundary / state / task execution / simplify / lessons / bootstrap / gate / halt / wontfix）
- `docs/superpowers/workflows/USAGE.md` §1-§13（用户文档，须与实现同步）

**Runtime constraints:**
- Orchestrator 是 JS sandbox：无 fs、无 `Date.now()`/`Math.random()`、无 subprocess
- `agent()` 可能返回 null（thinking-only 空响应）——所有调用须 null-guard
- 纯函数 → lib.js（`node --test` 可测）；runtime 胶水（调 `agent()`）→ run-plans.js
- lib.js 改了的 helper 必须同步 inline 复制到 run-plans.js（sync.test.js 字节守护）
- 所有 datetime 用 naive UTC（`datetime.now(timezone.utc).replace(tzinfo=None)`，本项目 Python 侧）；orchestrator 侧用 `agent('date -u ...')` 子代理获取时间戳（无 Date API）
- 业务逻辑注释用中文，技术术语用英文（项目约定）

---

## Task 1: 核心脚手架 — lib.js + run-plans.js 文件结构 + sync.test.js 守护机制

**目标:** 建立 two-file 模式的骨架：lib.js 纯函数真源 + run-plans.js inline 副本 + sync.test.js 字节守护。

### Step 1.1 — lib.js 文件头与导出结构

`docs/superpowers/workflows/lib.js` 是纯函数真源，被 `node --test` 测试：

- [ ] 创建 `docs/superpowers/workflows/lib.js`，文件头声明此文件为真源，run-plans.js inline 复制：

```javascript
// lottery-workflow-lib —— workflow orchestrator 纯函数真源
// 此文件被 node --test 测试；run-plans.js inline 复制其中的函数。

// 从 plan markdown 提取叶子 task ID（§13e 叶子优先规则）。
export function leafTasks(markdown) {
  const tops = []
  let current = null
  for (const line of markdown.split('\n')) {
    const m1 = line.match(/^##\s+Task\s+(\d+)\b/)
    if (m1) { current = { id: 'T' + m1[1], children: [] }; tops.push(current); continue }
    const m2 = line.match(/^###\s+Task\s+(\d+)([a-z])\b/)
    if (m2 && current) { current.children.push('T' + m2[1] + m2[2]) }
  }
  return tops.flatMap(t => t.children.length ? t.children : [t.id])
}
```

- [ ] 导出清单（完整 40+ 纯函数）：`leafTasks`, `detectOscillation`, `shouldEscalateOnOscillation`, `isFlipFlop`, `buildPrompt`, `allGreen`, `unionFiles`, `normalizeFilePath`, `findingsOf`, `collectReviewFindings`, `formatConcernsHint`, `reviewHaltForEmptyFailed`, `formatFindings`, `summarizeFinding`, `summarizeReviewRound`, `isQuotaError`, `errStr`, `classifyThrown`, `reviewHaltReason`, `haltLikelySource`, `validateAmendResult`, `validateCheckoutResult`, `fixModelForRound`, `resolveMaxRounds`, `resolveLessonsAutoDistill`, `distillLessonInput`, `formatLessonsForDistill`, `applyLessonDecisions`, `commitSubject`, `extractTaskKey`, `extractCompletedFromSubjects`, `normalizeCompleted`, `bareTaskId`, `dropParentTasks`, `matchesPlanFilter`, `formatReferencePaths`, `formatSilentFailureContext`, `formatFailedApproaches`, `formatLessons`, `formatUniversalLessons`, `formatDomainLessons`, `updateFindingsHistory`, `formatFindingsHistory`, `hasRegressed`, `resolveReviewBudget`, `formatWriteFilesScope`, `formatSchemaCheck`, `LANGUAGE_CHECKLISTS`, `languageChecklist`, `gateCommands`, `groupFindingsByFile`, `formatCrossReviewerNote`, `SCHEMAS`, `PROMPTS`

### Step 1.2 — run-plans.js 文件头与 inline 复制模式

`.claude/workflows/run-plans.js` 是 orchestrator 主文件：

- [ ] 创建 `.claude/workflows/run-plans.js`，文件头声明 inline 复制契约 + meta 导出 + 顶层 async IIFE：

```javascript
// workflow orchestrator —— 多 plan 自动执行（workflow-design.md §4/§5/§13）
// 纯函数/SCHEMAS/PROMPTS inline 自 docs/superpowers/workflows/lib.js —— 改 lib 必须同步改这里。
// 顶层 await = Workflow 入口；agent/parallel/phase/log/args 为 Workflow runtime 注入的全局。
// 分层：纯决策进 lib.js 可 node:test 测；runtime 胶水（safeAgent/dispatchImpl，调 agent()）只能留此文件。

export const meta = {
  name: 'run-plans',
  description: '自动执行 implementation plans：每 task implementor→review chain→commit，plan 级独立 gate',
  phases: [
    { title: 'Bootstrap', detail: '读 config/plan/git log + 生成 frontmatter' },
    { title: 'Plan', detail: '串行 task + review rounds + simplify + commit + plan gate' },
    { title: 'Finalize', detail: '写 manifest + digest' },
  ],
}

await (async () => {

// ===== 纯函数（inline 自 lib.js Task 2-4，逐字复制）=====
// ... 所有纯函数 inline 副本 ...
// ===== SCHEMAS =====
// ===== PROMPTS =====
// ===== state + runtime 胶水 =====
// ===== halt / runTask =====
// ===== 顶层编排 =====
})()
```

### Step 1.3 — state 初始化（§4.4）

- [ ] 在 run-plans.js 中初始化 state 对象（plan-scoped taskKey 贯穿全流程）：

```javascript
const state = {
  runTs: null, config: null, completed: [], plans: [], currentPlan: null, currentTask: null,
  perTask: {},  // {taskKey: {planId, status, model, review_rounds, files_touched_per_round, commit_sha, blocked_info}}
  failedApproaches: {},  // {taskKey: [{task_id, reason, error}]}
  taskWriteFiles: {},  // {taskKey: [files]} — write_files 边界控制
  taskLessons: {},  // {taskKey: [{id, title, detail}]} — LESSONS.md 跨任务失败知识库
  allLessons: [],  // v3: bootstrap 解析的全量 lessons（含 category），供两层注入
}
```

### Step 1.4 — sync.test.js 守护机制

- [ ] 创建 `docs/superpowers/workflows/tests/sync.test.js`，建立三层守护：

```javascript
import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const libSrc = fs.readFileSync(path.resolve(__dirname, '../lib.js'), 'utf8')
const runSrc = fs.readFileSync(path.resolve(__dirname, '../../../../.claude/workflows/run-plans.js'), 'utf8')

// 提取 PROMPTS[role] 的模板字面量正文
function promptBody(src, role) {
  const re = new RegExp(`  ${role}: \\\`([\\s\\S]*?)\\\`,`)
  const m = src.match(re)
  assert.ok(m, `role ${role} template not found`)
  return m[1]
}

const ROLES = ['bootstrap', 'implementor', 'specReview', 'qualityReviewer', 'hunter', 'simplify', 'commit', 'contextFetcher', 'gate', 'headVerifier', 'finalReport', 'lessonDistiller']

for (const role of ROLES) {
  test(`PROMPTS.${role} identical between lib.js and run-plans.js`, () => {
    assert.equal(promptBody(runSrc, role), promptBody(libSrc, role),
      `PROMPTS.${role} drifted — lib.js 改了 prompt 必须同步 run-plans.js`)
  })
}
```

- [ ] QC-4 字节比较 helper（支持 top-level function + 箭头函数 + 嵌套函数）：

```javascript
function extractFunctionBody(src, fnName) {
  const needle = `function ${fnName}(`
  const fnStart = src.indexOf(needle)
  if (fnStart === -1) return null
  const afterFn = src.slice(fnStart)
  const closeMatch = afterFn.match(/\n\}/)
  if (!closeMatch) return null
  return afterFn.slice(0, closeMatch.index + 2).trim()
}

test('QC-4: 关键 helper 函数体 lib.js ↔ run-plans.js 字节一致', () => {
  const fns = [
    'fixModelForRound', 'resolveMaxRounds', 'haltLikelySource', 'reviewHaltReason',
    'reviewHaltForEmptyFailed', 'detectOscillation', 'classifyThrown',
    'isQuotaError', 'commitSubject', 'normalizeCompleted', 'matchesPlanFilter',
    'collectReviewFindings', 'summarizeReviewRound', 'formatFindings',
    'resolveLessonsAutoDistill', 'distillLessonInput',
    'formatUniversalLessons', 'formatDomainLessons',
    'validateAmendResult', 'validateCheckoutResult',
    'findingsOf', 'summarizeFinding',
    'groupFindingsByFile', 'formatCrossReviewerNote',
    'bareTaskId', 'dropParentTasks', 'extractCompletedFromSubjects',
    'shouldEscalateOnOscillation', 'isFlipFlop',
    'updateFindingsHistory', 'formatFindingsHistory', 'hasRegressed', 'resolveReviewBudget',
    'normalizeFilePath', 'unionFiles',
  ]
  for (const fn of fns) {
    const libBody = extractFunctionBody(libSrc, fn)
    const runBody = extractFunctionBody(runSrc, fn)
    assert.ok(libBody, `lib.js 中找不到函数 ${fn}`)
    assert.ok(runBody, `run-plans.js 中找不到函数 ${fn}`)
    assert.equal(runBody, libBody, `helper ${fn} 函数体字节不一致`)
  }
})
```

### Verify

- [ ] `node --test docs/superpowers/workflows/tests/sync.test.js` 全绿
- [ ] `node --test docs/superpowers/workflows/tests/helpers.test.js` 全绿
- [ ] run-plans.js 文件头注释声明 inline 复制契约
- [ ] lib.js 所有导出函数在 run-plans.js 有 inline 副本

---

## Task 2: Bootstrap agent — config 读取、plan 解析、git log、dirty_tree 分类自愈、all_lessons 解析

**目标:** Bootstrap agent 读 config/plan/git log，分类处理 dirty_tree（W1-1/W1-4），解析 all_lessons，返回 evidence 供 orchestrator 初始化 state。

### Step 2.1 — bootstrap prompt（lib.js PROMPTS.bootstrap）

- [ ] bootstrap prompt 须要求 agent 执行 6 步：读 config → 解析 plan frontmatter → git log subjects → 分类处理 dirty_tree → 解析 lessons.md → 返回 evidence

```
bootstrap prompt 核心步骤（PROMPTS.bootstrap）：
Step 1: Read workflow.config.json → config (spec_path, test_command, review_max_rounds, review_budget, lessons_path...)
Step 2: Parse all plan .md files in plansDir → plans[{id, seq, file, tasks[{id, title, model, lesson_categories}]}]
Step 3: Run `git log --oneline -200` → git_log_subjects[] (feat/fix/refactor commits)
Step 4: Extract completed task keys from subjects (feat(plan-XX/TY): pattern)
Step 5: classify and handle each change in dirty working tree:
  5a: auto-commit lessons.md from interrupted run → `git commit <lessons_path> -m "chore: auto-commit lessons.md from interrupted run"`
  5b: discard runs/ and .workflow/ changes → `git checkout -- runs/ .workflow/`
  5c: git reset --hard HEAD for other unexpected changes
  5d: if any classification step fails → leave dirty_tree=true (orchestrator will halt)
Step 6: Parse lessons.md → all_lessons[{id, title, detail, category}]
Return evidence: {config, plans, completed, git_log_subjects, dirty_tree, failed_approaches, task_write_files, task_lessons, all_lessons}
```

### Step 2.2 — orchestrator bootstrap dispatch + state 初始化

- [ ] 顶层编排中 dispatch bootstrap（sonnet + retryModel opus），校验返回值，初始化 state：

```javascript
// 顶层编排（Workflow 入口）
phase('Bootstrap')
if (!args) throw new Error('args must be a non-null object (Workflow runtime contract)')
if (typeof args === 'string') {
  try { args = JSON.parse(args) } catch (parseErr) {
    throw new Error(`args was a string but failed JSON.parse: ${parseErr.message}`)
  }
}
if (typeof args.configPath !== 'string' || !args.configPath.trim()) {
  throw new Error('args.configPath must be a non-empty string (workflow.config.json path)')
}
if (typeof args.plansDir !== 'string' || !args.plansDir.trim()) {
  throw new Error('args.plansDir must be a non-empty string (plans directory path)')
}

// get-ts: 用 agent 获取时间戳（orchestrator 无 Date API，§4.3 硬约束）
let tsAgent
try {
  tsAgent = await agent('Run `date -u +%Y%m%dT%H%M%SZ` and return ONLY the timestamp string.', { label: 'get-ts' })
} catch (e) {
  log(`⚠ get-ts agent 抛错，降级用 'unknown-ts' 占位符: ${errStr(e)}`)
  tsAgent = 'unknown-ts'
}
if (typeof tsAgent !== 'string' || !tsAgent.trim()) tsAgent = 'unknown-ts'
state.runTs = tsAgent.trim()

let boot
try {
  boot = await dispatchImpl(buildPrompt('bootstrap', { configPath: args.configPath, plansDir: args.plansDir, runTs: state.runTs }), { schema: SCHEMAS.bootstrap, label: 'bootstrap' }, 'sonnet', 'opus')
} catch (e) {
  return await halt(null, null, { reason: 'agent_error', diag: { model: 'sonnet', error: errStr(e) } })
}
if (boot.halted) { return await halt(null, null, { reason: boot.reason, diag: boot.diag }) }
if (boot.status !== 'ok') { return await halt(null, null, { reason: `bootstrap ${boot.status}`, diag: boot.diagnostics }) }

// H-F1: bootstrap step 5d 分类处理失败时 dirty_tree=true，orchestrator 须 halt
if (boot.evidence?.dirty_tree) {
  return await halt(null, null, { reason: 'bootstrap dirty_tree cleanup failed', diag: { summary: boot.summary || 'dirty_tree=true after bootstrap step 5 classification' } })
}
```

### Step 2.3 — task_id 归一化 + leaf-guard + completed 提取

- [ ] bootstrap 返回的 task_id 可能含 `plan-XX/` 前缀，须 strip；过滤非叶子父 task；completed 用正则+LLM 并集：

```javascript
// P1-bootstrap-sanitize: strip plan-XX/ 前缀（防 taskKey 双重前缀 → 重跑）
for (const p of (boot.evidence.plans || [])) {
  if (Array.isArray(p.tasks)) {
    for (const t of p.tasks) t.id = bareTaskId(t.id)
    p.tasks = dropParentTasks(p.tasks)  // 过滤非叶子父 task（T6+T6b 共存 → drop T6）
  }
}
for (const fa of (boot.evidence.failed_approaches || [])) fa.task_id = bareTaskId(fa.task_id)
for (const twf of (boot.evidence.task_write_files || [])) twf.task_id = bareTaskId(twf.task_id)
for (const tl of (boot.evidence.task_lessons || [])) tl.task_id = bareTaskId(tl.task_id)

state.config = boot.evidence.config
state.plans = boot.evidence.plans

// P3-deterministic-completed: 正则提取（主源）+ LLM completed（补漏）取并集
const _regexCompleted = (Array.isArray(boot.evidence.git_log_subjects) && boot.evidence.git_log_subjects.length
  ? extractCompletedFromSubjects(boot.evidence.git_log_subjects) : [])
const _llmCompleted = (Array.isArray(boot.evidence.completed) ? boot.evidence.completed : [])
const _rawCompleted = (Array.isArray(args.completed) && args.completed.length
  ? args.completed : [...new Set([..._regexCompleted, ..._llmCompleted])]) || []
state.completed = normalizeCompleted(_rawCompleted)
```

### Step 2.4 — failed_approaches / task_write_files / task_lessons / all_lessons 归一化

- [ ] 按 plan-scoped taskKey 索引存入 state（防跨 plan 同名 task 覆盖）：

```javascript
if (Array.isArray(boot.evidence.failed_approaches)) {
  for (const fa of boot.evidence.failed_approaches) {
    const faKey = fa.task_id.includes('/') ? fa.task_id : `plan-${String(fa.plan_seq).padStart(2, '0')}/${fa.task_id}`
    if (!state.failedApproaches[faKey]) state.failedApproaches[faKey] = []
    state.failedApproaches[faKey].push(fa)
  }
}
if (Array.isArray(boot.evidence.task_write_files)) {
  for (const twf of boot.evidence.task_write_files) {
    state.taskWriteFiles[`plan-${String(twf.plan_seq).padStart(2, '0')}/${twf.task_id}`] = twf.files || []
  }
}
if (Array.isArray(boot.evidence.task_lessons)) {
  for (const tl of boot.evidence.task_lessons) {
    state.taskLessons[`plan-${String(tl.plan_seq).padStart(2, '0')}/${tl.task_id}`] = tl.lessons || []
  }
}
if (Array.isArray(boot.evidence.all_lessons)) {
  state.allLessons = boot.evidence.all_lessons  // v3: 全量 lessons（含 category）
}
```

### Verify

- [ ] `sync.test.js` 断言 bootstrap prompt 含 `classify and handle each change`（W1-1/W1-4）
- [ ] `sync.test.js` 断言 bootstrap prompt 含 `auto-commit lessons.md`（W1-1）
- [ ] `sync.test.js` 断言 bootstrap prompt 含 `git checkout -- runs/ .workflow/`（W1-1）
- [ ] orchestrator 检查 `boot.evidence?.dirty_tree` 并 halt（H-F1）
- [ ] `helpers.test.js` 测 `bareTaskId` / `dropParentTasks` / `extractCompletedFromSubjects` / `normalizeCompleted`

---

## Task 3: Implementor agent — TDD、Discipline 禁提交、6 维自审、lessons 两层注入接线

**目标:** implementor agent prompt 包含 TDD 纪律、Discipline 段落禁 git commit/add（W1-2）、6 维自审、build_command、lessons 两层注入（v3 A+B）、failed approaches 注入。

### Step 3.1 — implementor prompt Discipline 段落（W1-2）

- [ ] implementor prompt 须有 `## Discipline (HARD REQUIREMENTS)` 段落，禁止 `git commit` / `git add`：

```
## Discipline (HARD REQUIREMENTS)
- DO NOT run `git commit` or `git add` — only write code and tests.
- Leave your changes in the working tree uncommitted. The commit agent will commit after review.
- Violating this creates dirty state that corrupts the commit/simplify/gate flow.
```

### Step 3.2 — implementor prompt 6 维自审 + build_command + L-xxx 编号指引（Q-F2）

- [ ] implementor prompt 须要求 GREEN 前跑 build_command（P1-11），加固代码时标 L-xxx 编号（Q-F2）：

```
## Before declaring done (6-dimension self-review)
1. Cognitive Overload: 单函数 >80 行？职责过多？拆分。
2. Change Propagation: 改动是否影响调用方？需同步更新？
3. Knowledge Duplication: 重复逻辑？抽 helper？
4. Accidental Complexity: 过度设计？删冗余抽象。
5. Dependency Disorder: 循环依赖？层级混乱？
6. Domain Distortion: 业务概念与代码命名对齐？

## Build verification
Run `{buildCommand}` before declaring done. If build fails, fix and retry.

## Lesson hardening (Q-F2)
When hardening code per a lesson, annotate with the lesson id:
  // L-<timestamp>: guard null per lesson
This lets reviewers locate hardening via the Exemption paragraph.
```

### Step 3.3 — implCtx 接线：lessons 两层注入 + failed approaches + build_command

- [ ] runTask 中 implCtx 构造，注入 formatUniversalLessons + formatDomainLessons（v3 A+B），移除旧 formatLessons（S3 修复）：

```javascript
// v3: lesson_categories 来自 plan frontmatter（可选）；未声明则 domain lessons 回退到 title 关键词匹配
// S3 修复：移除旧 formatLessons 调用——它与 formatDomainLessons 的 title keyword fallback 同源，
//   保留会导致非 silent-failure 且 keyword 重叠的 lesson 被注入两次。
const taskCategories = task.lesson_categories || []
const lessonsText = formatUniversalLessons(state.allLessons || []) + formatDomainLessons(state.allLessons || [], taskCategories, planIdShort, task.title || '')
const implCtx = (fix, note, ctx = '') => ({
  planId: plan.id, taskId: task.id, planFilePath: plan.file, specPath: cfg.spec_path,
  testCommand: cfg.test_command, buildCommand: cfg.build_command || '',
  fixIssues: fix, retryNote: note, fetchedContext: ctx,
  referencePaths: formatReferencePaths(cfg.reference_paths),
  failedApproaches: formatFailedApproaches(state.failedApproaches?.[taskKey] || []),
  lessons: lessonsText,
})
```

### Step 3.4 — implementor dispatch + blocked 升级链 + needs_context + failed retry

- [ ] 初始 dispatch 用 task.model + retryModel='opus'；blocked 升级 opus；needs_context 调 contextFetcher；failed 重试一次：

```javascript
let model = task.model || 'sonnet'
let impl
impl = await dispatchImpl(buildPrompt('implementor', implCtx('', '')), { schema: SCHEMAS.implementor, model, label: `impl:${task.id}` }, model, 'opus')
if (impl.halted) return impl

// blocked 升级链：sonnet→opus→halt
if (impl.status === 'blocked') {
  if (model === 'opus') return { halted: true, reason: 'opus BLOCKED', diag: impl.diagnostics }
  model = 'opus'
  impl = await dispatchImpl(buildPrompt('implementor', implCtx('', '上一轮 sonnet BLOCKED，升级 opus 重试。')), { schema: SCHEMAS.implementor, model: 'opus', label: `impl:${task.id}:opus` }, 'opus')
  if (impl.halted) return impl
  if (impl.status === 'blocked') return { halted: true, reason: 'opus BLOCKED', diag: impl.diagnostics }
}

// needs_context: dispatch contextFetcher, retry implementor with context
if (impl.status === 'needs_context') {
  let ctxr = await dispatchImpl(buildPrompt('contextFetcher', {
    needType: impl.diagnostics?.blocked_category || 'file',
    query: impl.diagnostics?.last_error || impl.diagnostics?.suggested_fix || '',
    specPath: cfg.spec_path, workdir: '.',
  }), { schema: SCHEMAS.contextFetcher, label: `ctx:${task.id}` }, 'sonnet')
  if (ctxr.halted) return ctxr
  const fetchedCtx = ctxr.diagnostics?.context || ''
  impl = await dispatchImpl(buildPrompt('implementor', implCtx('', `补充上下文后重试。`, fetchedCtx)), { schema: SCHEMAS.implementor, model, label: `impl:${task.id}:ctx` }, model, 'opus')
  if (impl.halted) return impl
  if (impl.status === 'blocked') {
    if (model === 'opus') return { halted: true, reason: 'opus BLOCKED after context-fetch', diag: impl.diagnostics }
    model = 'opus'
    impl = await dispatchImpl(buildPrompt('implementor', implCtx('', '上下文补充后 sonnet 仍 BLOCKED，升级 opus 重试。', fetchedCtx)), { schema: SCHEMAS.implementor, model: 'opus', label: `impl:${task.id}:ctx:opus` }, 'opus')
    if (impl.halted) return impl
    if (impl.status === 'blocked') return { halted: true, reason: 'opus BLOCKED after context-fetch', diag: impl.diagnostics }
  }
  if (impl.status === 'failed') {
    impl = await dispatchImpl(buildPrompt('implementor', implCtx('', '上下文补充后仍 failed，重试一次。', fetchedCtx)), { schema: SCHEMAS.implementor, model, label: `impl:${task.id}:ctx:retry` }, model, 'opus')
    if (impl.halted) return impl
    if (impl.status !== 'ok' && impl.status !== 'done_with_concerns') return { halted: true, reason: `implementor ${impl.status} after context-fetch retry`, diag: impl.diagnostics }
  }
  if (impl.status !== 'ok' && impl.status !== 'done_with_concerns') return { halted: true, reason: `implementor ${impl.status} after context-fetch`, diag: impl.diagnostics }
}

// failed: retry once → halt
if (impl.status === 'failed') {
  impl = await dispatchImpl(buildPrompt('implementor', implCtx('', '上次 failed，重试一次。')), { schema: SCHEMAS.implementor, model, label: `impl:${task.id}:retry` }, model, 'opus')
  if (impl.halted) return impl
  if (impl.status !== 'ok' && impl.status !== 'done_with_concerns') return { halted: true, reason: `implementor ${impl.status} after retry`, diag: impl.diagnostics }
}

// done_with_concerns: 记录疑虑，继续进 review（不 halt）
let concerns = []
if (impl.status === 'done_with_concerns') {
  concerns = impl.diagnostics?.concerns || []
  state.perTask[taskKey].concerns = concerns
  log(`⚠ ${task.id} done_with_concerns: ${concerns.join('; ') || '(no detail)'}`)
}
let concernsHint = formatConcernsHint(concerns)
let filesChanged = impl.evidence.files_changed || []
```

### Verify

- [ ] `sync.test.js` 断言 implementor prompt 含 `## Discipline (HARD REQUIREMENTS)`（W1-2）
- [ ] `sync.test.js` 断言 implementor prompt 含 `DO NOT run \`git commit\` or \`git add\``（W1-2）
- [ ] `sync.test.js` 断言 implementor prompt 含 L-xxx 编号指引（Q-F2）
- [ ] `helpers.test.js` 测 `formatUniversalLessons` + `formatDomainLessons`（S3: 不调旧 formatLessons）
- [ ] `sync.test.js` 断言 `lessonsText` 不含 `formatLessons(` 调用（S3 修复）

---

## Task 4: Lessons 两层注入纯函数 — formatUniversalLessons + formatDomainLessons

**目标:** v3 A+B 两层注入纯函数：Tier 1 silent-failure 始终注入 + Tier 2 按 category 匹配（cap 5，same-plan 优先）。

### Step 4.1 — formatUniversalLessons（Tier 1: silent-failure 始终注入）

- [ ] lib.js 中 formatUniversalLessons，容错 silent-failure 变体（正则 `/^(silent[-_]?failure)$/i`）：

```javascript
// v3 Tier 1: silent-failure 类 lesson 始终注入（所有 task 都需防静默失败）
// 容错 silent-failure 变体：silent-failure / silent_failure / silentFailure
export function formatUniversalLessons(allLessons) {
  const re = /^(silent[-_]?failure)$/i
  const universal = (allLessons || []).filter(l => re.test(l.category || l.title || ''))
  if (universal.length === 0) return ''
  let out = '\n## Universal Lessons (silent-failure prevention — applies to ALL tasks)\n'
  for (const l of universal) {
    out += `\n### ${l.id}: ${l.title}\n${l.detail || ''}\n`
  }
  return out
}
```

### Step 4.2 — formatDomainLessons（Tier 2: 按 category 匹配，cap 5，same-plan 优先）

- [ ] lib.js 中 formatDomainLessons，category 精确匹配 + title keyword fallback（覆盖 legacy 无 category 场景）：

```javascript
// v3 Tier 2: 按 task.lesson_categories 匹配 domain lessons（cap 5，same-plan 优先）
// taskCategories 来自 plan frontmatter（可选）；未声明则回退到 title 关键词匹配
export function formatDomainLessons(allLessons, taskCategories, currentPlanSeq, taskTitle) {
  if (!Array.isArray(allLessons) || allLessons.length === 0) return ''
  const cats = Array.isArray(taskCategories) ? taskCategories : []
  let matched = []
  if (cats.length > 0) {
    // category 精确匹配（case-insensitive）
    matched = allLessons.filter(l => {
      const lc = (l.category || '').toLowerCase()
      return cats.some(c => c.toLowerCase() === lc)
    })
  } else if (taskTitle) {
    // title keyword fallback（legacy 无 category 场景）
    const tokens = taskTitle.toLowerCase().split(/\s+/).filter(t => t.length > 2)
    matched = allLessons.filter(l => {
      const lt = (l.title + ' ' + (l.detail || '')).toLowerCase()
      return tokens.some(t => lt.includes(t))
    })
  }
  if (matched.length === 0) return ''
  // same-plan 优先：currentPlanSeq 匹配的排前面
  const samePlan = matched.filter(l => l.plan_seq === currentPlanSeq)
  const others = matched.filter(l => l.plan_seq !== currentPlanSeq)
  const ordered = [...samePlan, ...others].slice(0, 5)  // cap 5
  let out = '\n## Domain Lessons (category-matched, cap 5, same-plan priority)\n'
  for (const l of ordered) {
    out += `\n### ${l.id}: ${l.title}\n${l.detail || ''}\n`
  }
  return out
}
```

### Verify

- [ ] `helpers.test.js` 测 formatUniversalLessons 容错 `silent-failure` / `silent_failure` / `silentFailure` 变体
- [ ] `helpers.test.js` 测 formatDomainLessons category 匹配 + title keyword fallback + cap 5 + same-plan 优先
- [ ] `sync.test.js` QC-4 字节比较 formatUniversalLessons + formatDomainLessons
- [ ] `sync.test.js` H4 断言 formatUniversalLessons inline 副本与 lib.js 字节一致

---

## Task 5: Review chain + runReviewRound — spec/quality/hunter 并行、Task Scope Boundary、Lessons Learned Exemption

**目标:** 抽 runReviewRound helper（Q10，主轮/simplify 轮/destructive 轮共用），spec/quality/hunter 并行 review，含 Task Scope Boundary（W1-5a）+ Lessons Learned Exemption（W1-5e + H-F4）。

### Step 5.1 — runReviewRound helper（Q10 抽取）

- [ ] 抽取三处重复的 review 调用模式为 helper，只抽"并行调用 + reviewHaltReason + reviewHaltForEmptyFailed"：

```javascript
// 跑一轮 spec‖qual‖hunt 并行 review + 双守卫（Q10 抽取，主轮/simplify 轮/destructive 轮共用）
// concernsHint：主轮传 implementor 疑虑，simplify/destructive 传空串
// labelSuffix：区分轮次（`:r${round}` / `:simp` / `:destructive`）
// phaseLabel：主轮+destructive 传 `Plan ${plan.id}`（Workflow UI 显示阶段），simplify 传空
// 返回 { spec, qual, hunt, haltReason, emptyFailed }
async function runReviewRound(taskId, cfg, plan, fc, concernsHint, labelSuffix, phaseLabel) {
  const commonOpts = phaseLabel ? { phase: phaseLabel } : {}
  const [spec, qual, hunt] = await parallel([
    async () => safeAgent(buildPrompt('specReview', {
      taskId, specPath: cfg.spec_path, planFilePath: plan.file, filesChanged: fc,
      concernsHint, referencePaths: formatReferencePaths(cfg.reference_paths),
      lessonsPath: cfg.lessons_path || '',
    }), { schema: SCHEMAS.specReview, model: 'opus', ...commonOpts, label: `spec:${taskId}${labelSuffix}` }),
    async () => safeAgent(buildPrompt('qualityReviewer', {
      taskId, filesChanged: fc, languageChecklist: languageChecklist(cfg.language),
      lessonsPath: cfg.lessons_path || '',
    }), { schema: SCHEMAS.qualityReviewer, model: 'opus', ...commonOpts, label: `qual:${taskId}${labelSuffix}` }),
    async () => safeAgent(buildPrompt('hunter', {
      taskId, filesChanged: fc,
      silentFailureContext: formatSilentFailureContext(cfg.silent_failure_context, cfg.silent_failure_intro),
    }), { schema: SCHEMAS.hunter, model: 'sonnet', ...commonOpts, label: `hunt:${taskId}${labelSuffix}` }),
  ])
  const haltReason = reviewHaltReason(spec, qual, hunt)
  const emptyFailed = haltReason ? null : reviewHaltForEmptyFailed(spec, qual, hunt)
  return { spec, qual, hunt, haltReason, emptyFailed }
}
```

### Step 5.2 — specReview prompt Task Scope Boundary（W1-5a）

- [ ] specReview prompt 须有 `## Task Scope Boundary` 段落，明确 future task 不算 MISSING：

```
## Task Scope Boundary (critical for multi-task plans)
- Each task implements ONLY its plan section's requirements.
- Methods/interfaces/fields needed by FUTURE tasks are NOT missing — they will be implemented in their own tasks.
- Do not flag them as MISSING unless the CURRENT task's plan section explicitly requires them.
```

### Step 5.3 — specReview + qualityReviewer Lessons Learned Exemption（W1-5e + Q-F1 + H-F4）

- [ ] specReview + qualityReviewer prompt 须有 `## Lessons Learned Exemption` 段落，编号格式 L-<timestamp>（Q-F1），lessonsPath 注入（H-F4 空 guard）：

```
## Lessons Learned Exemption (防 reviewer ↔ implementor 振荡)
- Implementor may harden code per lessons in {lessonsPath}.
- Hardening annotated with L-<timestamp> id (e.g. // L-20260701T103320Z: guard null per lesson).
- If a finding is about code that has an L-<timestamp> annotation, check lessons.md for that id.
- If the annotation matches a lesson, EXEMPT that finding (do not flag it).
- Exemption only applies to the lesson's specific concern; other issues in the same code are still flagged.

## Quality Reviewer Exemption (限定维度硬性豁免)
- 不豁免的维度: 命名清晰度、类型注解、错误处理。
- Other dimensions (per lesson annotation) may be exempted per the Exemption paragraph above.
```

### Step 5.4 — review loop 主轮调用 + history 更新 + halt 检查顺序

- [ ] review loop 中 push 须在 halt 检查之前（Q15），findings_history 更新在 review_history.push 之后、halt 检查之前：

```javascript
for (let round = 1; maxRounds === 0 ? true : round <= maxRounds; round++) {
  state.perTask[taskKey].review_rounds = round
  const fc = filesChanged.join('\n')
  const { spec, qual, hunt, haltReason: reviewReason, emptyFailed: emptyFailedReason } =
    await runReviewRound(task.id, cfg, plan, fc, concernsHint, `:r${round}`, `Plan ${plan.id}`)

  // Q15: push 须在 halt 检查之前——halt 轮的 review_history 也须持久化
  state.perTask[taskKey].files_touched_per_round.push(unionFiles(spec, qual, hunt))
  state.perTask[taskKey].review_history.push(summarizeReviewRound(round, spec, qual, hunt))

  // v3: findings 状态机更新（在 halt 检查之前）
  const currentFindings = collectReviewFindings(spec, qual, hunt)
  state.perTask[taskKey].findings_history = updateFindingsHistory(
    state.perTask[taskKey].findings_history, currentFindings, round
  )

  if (reviewReason) return { halted: true, reason: reviewReason, diag: { spec: spec?.diagnostics, qual: qual?.diagnostics, hunt: hunt?.diagnostics } }
  if (emptyFailedReason) return { halted: true, reason: emptyFailedReason, diag: { spec: spec?.diagnostics, qual: qual?.diagnostics, hunt: hunt?.diagnostics } }

  // allGreen 必须在 detectOscillation 之前（防收敛误报 OSCILLATING）
  if (allGreen(spec, qual, hunt)) break
  // ... OSCILLATING 检测见 Task 8 ...
}
```

### Verify

- [ ] `sync.test.js` 断言 run-plans.js 须有 `function runReviewRound`（Q10）
- [ ] `sync.test.js` 断言 runReviewRound 内部须调 `reviewHaltForEmptyFailed(spec, qual, hunt)`
- [ ] `sync.test.js` 断言 specReview prompt 含 `## Task Scope Boundary`（W1-5a）
- [ ] `sync.test.js` 断言 specReview + qualityReviewer prompt 含 `## Lessons Learned Exemption`（W1-5e）
- [ ] `sync.test.js` 断言 specReview + qualityReviewer buildPrompt 传 `lessonsPath`（W1-5e）
- [ ] `sync.test.js` REGRESSION 断言 allGreen break 在 detectOscillation 之前
- [ ] `sync.test.js` v3 wiring 断言 findings_history 更新在 review_history.push 之后、halt 检查之前

---

## Task 6: Cross-reviewer 纯函数 — groupFindingsByFile + formatCrossReviewerNote + normalizeFilePath

**目标:** cross-reviewer surfacing 纯函数（2026-07-05），按文件分组 findings，≥2 个不同 reviewer 标记同一文件 → 注入 implementor prompt。normalizeFilePath 路径归一（W1-5b + Q-F3/H-F5/H-F6）。

### Step 6.1 — normalizeFilePath（W1-5b 路径归一）

- [ ] lib.js 中 normalizeFilePath，白名单扩展覆盖 JS/Go/Python 项目常见目录（Q-F3/H-F5）：

```javascript
// W1-5b 路径归一：防 reviewer 返回不同路径格式致 cross-reviewer 重叠检测漏报
// 白名单覆盖 src|tests|docs|data|logs|lib|app|internal|cmd|.claude + JS/Go/Python 常见目录
export function normalizeFilePath(p) {
  if (typeof p !== 'string' || !p) return ''  // H-F6: typeof 防御非 string
  // 去 ./ 前缀 + 取相对路径部分
  let s = p.replace(/^\.\//, '').replace(/\\/g, '/')
  // 匹配白名单目录开头的相对路径
  const m = s.match(/(?:^|\/)((?:src|tests|docs|data|logs|lib|app|internal|cmd|\.claude|scripts|bin|tools|config|public|static|templates|utils|api|server|client|web|\.github)\/.+)/)
  return m ? m[1] : s
}
```

### Step 6.2 — groupFindingsByFile（按文件分组 findings）

- [ ] lib.js 中 groupFindingsByFile，纯函数不依赖映射表：

```javascript
// 跨 reviewer 文件重叠检测：按 file 分组 findings → 组中有 ≥2 个不同 source → 标记
export function groupFindingsByFile(findings) {
  const groups = {}
  for (const f of findings) {
    if (!f.file) continue
    const norm = normalizeFilePath(f.file)
    if (!norm) continue
    if (!groups[norm]) groups[norm] = { file: norm, sources: new Set(), findings: [] }
    groups[norm].sources.add(f.source)
    groups[norm].findings.push(f)
  }
  return Object.values(groups)
}
```

### Step 6.3 — formatCrossReviewerNote（格式化跨 reviewer 重叠提示）

- [ ] lib.js 中 formatCrossReviewerNote，仅当某文件有 ≥2 个不同 reviewer 标记时才输出：

```javascript
// 格式化跨 reviewer 文件重叠为 implementor 可读的注入文本
export function formatCrossReviewerNote(findings) {
  const groups = groupFindingsByFile(findings).filter(g => g.sources.size >= 2)
  if (groups.length === 0) return ''
  let out = '\n## ⚠ Cross-Reviewer Overlap (≥2 reviewers flagged same file — check for conflicts)\n'
  for (const g of groups) {
    const srcs = [...g.sources].join('/')
    out += `\n### ${g.file} (flagged by: ${srcs})\n`
    for (const f of g.findings) {
      out += `- [${f.source}${f.severity ? '|' + f.severity : ''}] ${f.title}${f.fix ? ' — fix: ' + f.fix : ''}\n`
    }
  }
  return out
}
```

### Step 6.4 — fixIssues 注入 cross-reviewer note

- [ ] review loop 中 fixIssues 追加 formatCrossReviewerNote（D1: history 主导单源，不再单独注入 formatFindings(本轮)）：

```javascript
const findings = collectReviewFindings(spec, qual, hunt)
const crossReviewerNote = formatCrossReviewerNote(findings)
// D1: history 主导单源——formatFindingsHistory 已含本轮 [OPEN]（标★本轮新增）
const findingsHistoryText = formatFindingsHistory(state.perTask[taskKey].findings_history || [], round)
const fullFixIssues = findingsHistoryText ? `${findingsHistoryText}\n${crossReviewerNote}` : crossReviewerNote
```

### Verify

- [ ] `helpers.test.js` 测 groupFindingsByFile 覆盖 0/1/多组 + 同 source 多 finding + 无 file 的 finding
- [ ] `helpers.test.js` 测 formatCrossReviewerNote 覆盖 0/1/多重叠 + 输出格式
- [ ] `helpers.test.js` 测 normalizeFilePath 白名单目录 + 非 string 防御（H-F6）+ 非贪婪匹配（Q-F5）
- [ ] `sync.test.js` QC-4 字节比较 groupFindingsByFile + formatCrossReviewerNote + normalizeFilePath + unionFiles

---

## Task 7: Findings 状态机纯函数 — updateFindingsHistory + formatFindingsHistory + hasRegressed

**目标:** v3 E' findings 状态机纯函数：open→fixed→regressed，[REGRESSED] 触发 halt，[FIXED] 注入防回归。

### Step 7.1 — updateFindingsHistory（状态机更新）

- [ ] lib.js 中 updateFindingsHistory，状态转换 open→fixed→regressed→fixed（二次修好）：

```javascript
// v3 E' findings 状态机：open→fixed→regressed→fixed（二次修好，防状态机死锁）
// history: [{key, title, source, severity, status, first_round, fixed_round, regressed_round}]
// currentFindings: 本轮 collectReviewFindings 输出
// round: 当前轮次
export function updateFindingsHistory(history, currentFindings, round) {
  const prev = Array.isArray(history) ? history : []
  // 按 title+source 建 key（容错 title 变体）
  const findByKey = (title, source) => prev.find(h =>
    h.title === title && h.source === source
  )
  // 本轮发现的 finding → open（新）或 fixed→regressed（回归）或 regressed→fixed（二次修好）
  const currentKeys = new Set()
  for (const f of currentFindings) {
    const key = `${f.source}::${f.title}`
    currentKeys.add(key)
    const existing = findByKey(f.title, f.source)
    if (!existing) {
      // 新 finding → open
      prev.push({ key, title: f.title, source: f.source, severity: f.severity, status: 'open', first_round: round })
    } else if (existing.status === 'fixed') {
      // 已修好但本轮又出现 → regressed
      existing.status = 'regressed'
      existing.regressed_round = round
    } else if (existing.status === 'regressed') {
      // regressed 后本轮又消失 → fixed（二次修好，§5.5 E' 补 regressed→fixed 转换）
      existing.status = 'fixed'
      existing.fixed_round = round
    } else if (existing.status === 'open') {
      // 仍 open，无变化
    }
  }
  // 本轮未出现的 open finding → fixed
  for (const h of prev) {
    const key = `${h.source}::${h.title}`
    if (h.status === 'open' && !currentKeys.has(key)) {
      h.status = 'fixed'
      h.fixed_round = round
    }
  }
  return prev
}
```

### Step 7.2 — hasRegressed（检测回归）

- [ ] lib.js 中 hasRegressed，检查 history 中是否有 status='regressed' 的 finding：

```javascript
// v3: 任一 finding 回归（fixed→regressed）→ true，触发 OSCILLATING halt
export function hasRegressed(history) {
  if (!Array.isArray(history)) return false
  return history.some(h => h.status === 'regressed')
}
```

### Step 7.3 — formatFindingsHistory（历史主导单源，★ 标本轮新增）

- [ ] lib.js 中 formatFindingsHistory，签名含 currentRound（D1: 区分本轮新增标记紧急度）：

```javascript
// v3 D1: history 主导单源——formatFindingsHistory 已含本轮 [OPEN]（★ 标本轮新增）
// 签名: formatFindingsHistory(history, currentRound)
export function formatFindingsHistory(history, currentRound) {
  if (!Array.isArray(history) || history.length === 0) return ''
  let out = '\n## Findings History (状态机: [OPEN]→[FIXED]→[REGRESSED])\n'
  // 按状态分组：regressed 优先 > open > fixed
  const regressed = history.filter(h => h.status === 'regressed')
  const open = history.filter(h => h.status === 'open')
  const fixed = history.filter(h => h.status === 'fixed')
  if (regressed.length) {
    out += '\n### [REGRESSED] — fixed 后又回归，必须立即修复\n'
    for (const h of regressed) out += `- ⚠ [${h.source}] ${h.title} (first: r${h.first_round}, fixed: r${h.fixed_round}, regressed: r${h.regressed_round})\n`
  }
  if (open.length) {
    out += '\n### [OPEN] — 未修复\n'
    for (const h of open) {
      const isNew = h.first_round === currentRound
      out += `- ${isNew ? '★ ' : ''}[${h.source}] ${h.title} (since r${h.first_round})\n`
    }
  }
  if (fixed.length) {
    out += '\n### [FIXED] — 已修复（不要回退这些修改）\n'
    for (const h of fixed) out += `- [${h.source}] ${h.title} (fixed: r${h.fixed_round})\n`
  }
  return out
}
```

### Verify

- [ ] `helpers.test.js` 测 updateFindingsHistory 状态转换 open→fixed→regressed→fixed
- [ ] `helpers.test.js` 测 hasRegressed true/false 边界
- [ ] `helpers.test.js` 测 formatFindingsHistory 输出格式 + ★ 本轮新增标记
- [ ] `sync.test.js` QC-4 字节比较 updateFindingsHistory + formatFindingsHistory + hasRegressed
- [ ] `sync.test.js` v3 wiring 断言 `formatFindingsHistory(state.perTask[taskKey].findings_history || [], round)`

---

## Task 8: OSCILLATING halt 逻辑 — flipFlop/regressed 驱动 + budget guard

**目标:** v3 OSC 改进：flipFlop/regressed 驱动 halt + budget guard 5（review_not_converging），替代纯计数。shouldEscalateOnOscillation 仅判断升级，halt 决策上移。

### Step 8.1 — detectOscillation（文件级振荡检测）

- [ ] lib.js 中 detectOscillation（核心文件被审 ≥3 轮 → oscillating=true）：

```javascript
export function detectOscillation(filesTouchedPerRound) {
  if (filesTouchedPerRound.length < 3) return { oscillating: false }
  // 核心文件（被审 ≥3 轮）→ 振荡
  const fileRounds = {}
  for (let r = 0; r < filesTouchedPerRound.length; r++) {
    for (const f of (filesTouchedPerRound[r] || [])) {
      if (!fileRounds[f]) fileRounds[f] = []
      fileRounds[f].push(r + 1)
    }
  }
  const core = Object.entries(fileRounds).filter(([, rounds]) => rounds.length >= 3)
  if (core.length === 0) return { oscillating: false }
  return { oscillating: true, coreFiles: core.map(([f, rounds]) => ({ file: f, rounds })) }
}
```

### Step 8.2 — isFlipFlop（区分 flip-flop vs 补充）

- [ ] lib.js 中 isFlipFlop（同 title 跨轮反复出现 → 真振荡）：

```javascript
// 改进2: 区分 flip-flop（reviewer 反向分歧）vs 补充（每轮新 findings = 在推进）
export function isFlipFlop(reviewHistory) {
  if (!Array.isArray(reviewHistory) || reviewHistory.length < 2) return false
  // 收集所有轮次的 finding titles（跨 reviewer）
  const allTitles = []
  for (const r of reviewHistory) {
    const spec = r?.spec?.findings || []
    const qual = r?.quality?.findings || []
    const hunt = r?.hunter?.findings || []
    for (const f of [...spec, ...qual, ...hunt]) {
      if (f?.title) allTitles.push(f.title)
    }
  }
  // 同 title 出现 ≥2 次（跨轮）→ flip-flop
  const counts = {}
  for (const t of allTitles) counts[t] = (counts[t] || 0) + 1
  return Object.values(counts).some(c => c >= 2)
}
```

### Step 8.3 — shouldEscalateOnOscillation（仅判断升级，halt 上移）

- [ ] lib.js 中 shouldEscalateOnOscillation（v3: 仅返回升级决策，halt 移到 OSC 分支）：

```javascript
// v3: 仅判断"是否升级 opus"，halt 决策上移到 OSC 分支
export function shouldEscalateOnOscillation(currentModel, alreadyEscalated) {
  if (currentModel === 'opus') return false  // 已是 opus，无需升级
  if (alreadyEscalated) return false  // 已升过，不重复升级
  return true
}
```

### Step 8.4 — resolveReviewBudget（budget guard 默认 5）

- [ ] lib.js 中 resolveReviewBudget（D4 决策：默认 5，0/负数/NaN/非数字 → 5）：

```javascript
// D4: budget guard 默认 5（无限模式 maxRounds=0 时兜底）
export function resolveReviewBudget(config) {
  const b = config?.review_budget
  if (typeof b !== 'number' || !Number.isFinite(b) || b <= 0) return 5
  return Math.floor(b)
}
```

### Step 8.5 — OSC 分支控制流（regressed + flipFlop + escalate + budget）

- [ ] runTask review loop 中 OSC 分支：regressed→halt；flipFlop→halt；flipFlop=false→escalate/budget：

```javascript
const osc = detectOscillation(state.perTask[taskKey].files_touched_per_round)
const flipFlop = isFlipFlop(state.perTask[taskKey].review_history || [])
const regressed = hasRegressed(state.perTask[taskKey].findings_history || [])

// v3 (§5.5): 任一 finding 回归（fixed→regressed）→ 立即 halt（独立于文件振荡）
if (regressed) {
  log(`⚠ ${task.id}: r${round} OSCILLATING halt — regressed finding(s) reappeared after being fixed (v3)`)
  return {
    halted: true, reason: 'OSCILLATING',
    diag: { ...osc, flipFlop, regressed,
      regressedFindings: state.perTask[taskKey].findings_history.filter(h => h.status === 'regressed'),
      model },
  }
}

if (osc.oscillating) {
  // v3: flipFlop → 真振荡（reviewer 反向分歧）→ halt
  if (flipFlop) {
    log(`⚠ ${task.id}: r${round} OSCILLATING halt — reviewer flip-flop detected (v3)`)
    return { halted: true, reason: 'OSCILLATING', diag: { ...osc, flipFlop, regressed, model } }
  }
  // flipFlop=false 且无 regressed（每轮新 findings = 在推进）
  if (shouldEscalateOnOscillation(model, state.perTask[taskKey].opus_escalated)) {
    state.perTask[taskKey].opus_escalated = true
    state.perTask[taskKey].oscillation_escalated_at_round = round
    model = 'opus'
    log(`⚠ ${task.id}: r${round} OSCILLATING (new-findings 补充, flipFlop=false) — escalate to opus, continue (v3)`)
  } else {
    log(`⚠ ${task.id}: r${round} OSCILLATING (flipFlop=false, opus already escalated) — continue until budget guard (v3)`)
  }
}

// v3: 无限模式（maxRounds=0）budget guard——flipFlop=false 持续推进的兜底
if (maxRounds === 0) {
  const budget = resolveReviewBudget(cfg)
  if (round >= budget) {
    // D4: halt reason 改可操作——blocked.md 建议拆 task
    return { halted: true, reason: 'review_not_converging',
      diag: { round, budget, findings_history: state.perTask[taskKey].findings_history,
        spec: spec.diagnostics, qual: qual.diagnostics, hunt: hunt.diagnostics } }
  }
} else if (round === maxRounds) {
  return { halted: true, reason: 'review max rounds',
    diag: { round, findings_history: state.perTask[taskKey].findings_history,
      spec: spec.diagnostics, qual: qual.diagnostics, hunt: hunt.diagnostics } }
}
```

### Step 8.6 — fix-round model 升级（fixModelForRound + opus 升级 prompt 强化）

- [ ] 最后 1 轮 fix 强制 opus（有限模式 round===maxRounds-1 / 无限模式 round>=4）：

```javascript
const fixModel = fixModelForRound(round, model, maxRounds)
impl = await dispatchImpl(buildPrompt('implementor', implCtx(fullFixIssues, retryNote)),
  { schema: SCHEMAS.implementor, model: fixModel, label: `impl:${task.id}:fix${round}` }, fixModel, 'opus')
```

- [ ] opus 升级轮 retryNote 强化（v3 F）：

```javascript
const oscEscRound = state.perTask[taskKey].oscillation_escalated_at_round
const retryNote = oscEscRound === round
  ? `## 升级到 opus，本轮必须修完所有 [OPEN]\n- 逐条核对 [OPEN]，每条要么修完，要么说明不修的原因（★ 标本轮新增的优先修）\n- 修完后，核对 [FIXED] 列表的 fix 在你的改动后仍然存在；若 [OPEN] 与 [FIXED] 同文件，只动 [OPEN] 描述的代码，不要回退 [FIXED] 对应的修改\n- 不要留到下一轮，下一轮不再有升级空间\n- 截至 r${round} review 累计未修 findings 如上`
  : `修复 review round ${round} 问题（${findings.length} 项发现；★ 标本轮新增）。`
```

### Verify

- [ ] `helpers.test.js` 测 detectOscillation / isFlipFlop / shouldEscalateOnOscillation / resolveReviewBudget
- [ ] `sync.test.js` v3 wiring 断言 `if (regressed)` 独立 halt 分支 + `regressedFindings`
- [ ] `sync.test.js` 断言 `reason: 'review_not_converging'`（budget guard）
- [ ] `sync.test.js` 断言 `formatFindingsHistory(state.perTask[taskKey].findings_history || [], round)` 在 fix-round 注入

---

## Task 9: Commit + Simplify（方案 C）— commit 提前 + git diff 触发 review + amend/checkout

**目标:** 方案 C simplify 流程：commit 提前到 simplify 前 → simplify → git status --porcelain 独立验证 → 有改动则 review → 全绿 amend / 失败 checkout。destructive review 不 halt。

### Step 9.1 — commit（提前到 simplify 前，sonnet 硬编码）

- [ ] commit agent 用 sonnet（P1-5: 不跟随 task model 升级），校验 out_of_scope + failed：

```javascript
// commit（提前到 simplify 前；§5 状态原子转换）
let commit
commit = await dispatchImpl(buildPrompt('commit', {
  taskId: task.id, planId: plan.id, planIdShort,
  commitMsg: commitSubject(plan.seq, task.id, task.title || task.id),
  testCommand: cfg.test_command,
  writeFilesScope: formatWriteFilesScope(state.taskWriteFiles?.[taskKey] || []),
}), { schema: SCHEMAS.commit, label: `commit:${task.id}` }, 'sonnet')
if (commit.halted) return commit
if (commit.status === 'failed' && Array.isArray(commit.diagnostics?.out_of_scope) && commit.diagnostics.out_of_scope.length) {
  return { halted: true, reason: 'commit out_of_scope', diag: commit.diagnostics }
}
if (commit.status !== 'ok') return { halted: true, reason: 'commit failed', diag: commit.diagnostics }
state.perTask[taskKey].status = 'committed'
state.perTask[taskKey].commit_sha = commit.evidence.commit_sha
log(`✓ ${task.id} committed @ ${commit.evidence.commit_sha}`)
```

### Step 9.2 — simplify + git status --porcelain 独立验证

- [ ] simplify 用 sonnet，git status --porcelain 独立验证是否动代码（不信任 simp.evidence.changed 自报）：

```javascript
let simp = await dispatchImpl(buildPrompt('simplify', { taskId: task.id, filesChanged: filesChanged.join('\n') }),
  { schema: SCHEMAS.simplify, label: `simp:${task.id}` }, 'sonnet')
if (simp.halted) return simp

// commit 后工作树 clean → git status --porcelain 非空即 simplify 动了代码
const diffSchema = { type: 'object', required: ['changed', 'files'], properties: {
  changed: { type: 'boolean' }, files: { type: 'array', items: { type: 'string' } } } }
const diffResult = await safeAgent('Run `git status --porcelain` in the current working directory. If output is empty, return {"changed": false, "files": []}. Otherwise return {"changed": true, "files": [<list of file paths from porcelain output>]}.', { schema: diffSchema, label: `diff:${task.id}` })

// Q4/Q8: diff 返回 null/异常/格式错 → halt；changed=true 时 files 须为 array
if (!diffResult || typeof diffResult !== 'object' || typeof diffResult.changed !== 'boolean' ||
    (diffResult.changed === true && !Array.isArray(diffResult.files))) {
  return { halted: true, reason: 'simplify diff check failed', diag: { task: task.id, diffResult: diffResult || null } }
}
const simpChanged = diffResult.changed === true
const simpFiles = Array.isArray(diffResult.files) ? diffResult.files : []
```

### Step 9.3 — simplify review + amend（全绿） / checkout（失败）

- [ ] simpChanged=true 时跑 runReviewRound；全绿 amend（validateAmendResult），失败 checkout（validateCheckoutResult）：

```javascript
if (simpChanged) {
  const fc = simpFiles.join('\n')
  const { spec: spec2, qual: qual2, hunt: hunt2, haltReason: simpReviewReason, emptyFailed: simpEmptyFailed } =
    await runReviewRound(task.id, cfg, plan, fc, '', ':simp', '')
  if (simpReviewReason) {
    return { halted: true, reason: simpReviewReason, diag: { spec2: spec2?.diagnostics, qual2: qual2?.diagnostics, hunt2: hunt2?.diagnostics } }
  }
  if (simpEmptyFailed) {
    return { halted: true, reason: simpEmptyFailed, diag: { spec2: spec2?.diagnostics, qual2: qual2?.diagnostics, hunt2: hunt2?.diagnostics } }
  }
  if (allGreen(spec2, qual2, hunt2)) {
    // review 全绿 → amend commit（合并 simplify 改动到 HEAD）
    const amendSchema = { type: 'object', required: ['ok'], properties: {
      ok: { type: 'boolean' }, sha: { type: 'string' }, error: { type: 'string' } } }
    const amendResult = await safeAgent('Run `git add -A && git commit --amend --no-edit`. Then run `git rev-parse HEAD` and return JSON {"ok": true, "sha": "<40-char-hex>"}. If amend failed, return {"ok": false, "sha": "", "error": "<message>"}.', { schema: amendSchema, label: `amend:${task.id}` })
    const amendCheck = validateAmendResult(amendResult)
    if (!amendCheck.valid) {
      return { halted: true, reason: 'simplify amend failed', diag: { task: task.id, amendError: amendCheck.error, commitSha: commit.evidence.commit_sha } }
    }
    state.perTask[taskKey].commit_sha = amendCheck.sha
    log(`✓ ${task.id} simplify review green — amended commit @ ${amendCheck.sha}`)
  } else {
    // review 失败 → git reset --hard HEAD + git clean -fd 回退 simplify 改动
    const checkoutSchema = { type: 'object', required: ['ok'], properties: {
      ok: { type: 'boolean' }, porcelain: { type: 'string' }, error: { type: 'string' } } }
    const checkoutResult = await safeAgent('Run `git reset --hard HEAD && git clean -fd` to discard simplify changes. Then run `git status --porcelain` to verify the working tree is clean. Return JSON {"ok": true, "porcelain": "<porcelain output>"} or {"ok": false, "porcelain": "<output>", "error": "<message>"}.', { schema: checkoutSchema, label: `checkout:${task.id}` })
    const checkoutCheck = validateCheckoutResult(checkoutResult)
    if (!checkoutCheck.valid) {
      return { halted: true, reason: 'simplify checkout failed', diag: { task: task.id, checkoutError: checkoutCheck.error, commitSha: commit.evidence.commit_sha } }
    }
    log(`⚠ ${task.id} simplify review NOT green — reverted simplify changes (HEAD unchanged @ ${commit.evidence.commit_sha})`)
    state.perTask[taskKey].simplify_reverted = true
    state.perTask[taskKey].simplify_review_findings = collectReviewFindings(spec2, qual2, hunt2)
  }
}
```

### Step 9.4 — Destructive change detection 额外 review（不 halt）

- [ ] commit.diagnostics.destructive_changes 非空 → 触发额外 review round，失败/异常不 halt，记录 destructive_review_failed + findings：

```javascript
const destructive = commit.diagnostics?.destructive_changes
if (Array.isArray(destructive) && destructive.length) {
  log(`⚠ ${task.id} destructive_changes detected (${destructive.length}) — 触发额外 review round`)
  const fc = (commit.evidence.committed_files || []).join('\n')  // Q3: 换行分隔（非逗号）
  const { spec: dSpec, qual: dQual, hunt: dHunt, haltReason: dReason, emptyFailed: dEmptyFailed } =
    await runReviewRound(task.id, cfg, plan, fc, '', ':destructive', `Plan ${plan.id}`)
  if (dReason || dEmptyFailed) {
    state.perTask[taskKey].destructive_review_failed = true
    state.perTask[taskKey].destructive_review_findings = [{ source: 'destructive-review', severity: 'critical', title: dReason || dEmptyFailed, fix: 'investigate review agent failure' }]
    log(`⚠ ${task.id} destructive review 异常 — 记录并继续`)
  } else if (!allGreen(dSpec, dQual, dHunt)) {
    state.perTask[taskKey].destructive_review_failed = true
    state.perTask[taskKey].destructive_review_findings = collectReviewFindings(dSpec, dQual, dHunt)
    log(`⚠ ${task.id} destructive review NOT green — 记录 findings 并继续（不 halt）`)
  } else {
    log(`✓ ${task.id} destructive review green — 继续正常流程`)
  }
}

state.perTask[taskKey].status = 'done'
return { halted: false }
```

### Verify

- [ ] `helpers.test.js` 测 validateAmendResult / validateCheckoutResult 边界条件
- [ ] `sync.test.js` 断言 run-plans.js 须有 `reason: 'simplify amend failed'` / `'simplify checkout failed'` / `'simplify diff check failed'`
- [ ] `sync.test.js` 断言 finalReport prompt per_task 含 `simplify_reverted` + `destructive_review_failed` 字段
- [ ] `sync.test.js` SCHEMAS 断言 gate evidence required 含 `lint_results` + `restored_head`

---

## Task 10: Gate — 独立验证、restored_head、lint loop、extra_lint_commands

**目标:** plan 级独立 gate：在 lastSha 上重跑 test + lint_command + extra_lint_commands + schema migration check，headVerifier 独立验证 HEAD 恢复。

### Step 10.1 — gateCommands（config → 命令列表）

- [ ] lib.js 中 gateCommands，从 config 提取 test + lint + extra_lint 命令：

```javascript
export function gateCommands(config) {
  const cmds = []
  if (config?.test_command) cmds.push(config.test_command)
  if (config?.lint_command) cmds.push(config.lint_command)
  if (Array.isArray(config?.extra_lint_commands)) {
    for (const c of config.extra_lint_commands) if (typeof c === 'string' && c.trim()) cmds.push(c)
  }
  return cmds
}
```

### Step 10.2 — lastSha 反向查找 + gate dispatch

- [ ] plan 级 gate：按 plan.tasks 反向查找 lastSha（非 Object.values 插入顺序），dispatch gate（sonnet）：

```javascript
// plan 级独立 gate（§3）：本 plan 最后 commit SHA 上重跑 test + lint
let lastSha = null
for (let i = plan.tasks.length - 1; i >= 0; i--) {
  const tk = `plan-${String(plan.seq).padStart(2, '0')}/${plan.tasks[i].id}`
  if (state.perTask[tk]?.commit_sha) { lastSha = state.perTask[tk].commit_sha; break }
}
if (lastSha) {
  const cmds = gateCommands(state.config)
  let gate
  try {
    gate = await dispatchImpl(buildPrompt('gate', {
      sha: lastSha, gateCommands: JSON.stringify(cmds),
      schemaCheck: formatSchemaCheck(state.config?.schema_tool || '', state.config?.model_paths || [], state.config?.migration_paths || []),
    }), { schema: SCHEMAS.gate, label: `gate:${plan.id}`, phase: `Plan ${plan.id}` }, 'sonnet')
  } catch (e) {
    return await halt(plan, null, { reason: 'agent_error', diag: { model: 'sonnet', error: errStr(e) } })
  }
  if (gate.halted) { return await halt(plan, null, { reason: gate.reason, diag: gate.diag }) }
  if (gate.status !== 'ok' || gate.evidence?.migration_missing) {
    return await halt(plan, null, { reason: 'plan gate failed', diag: {
      sha: lastSha, tests_exit_code: gate.evidence?.tests_exit_code,
      summary: gate.evidence?.pytest_summary, lint_results: gate.evidence?.lint_results,
      migration_missing: gate.evidence?.migration_missing } })
  }
```

### Step 10.3 — headVerifier 独立验证 HEAD 恢复

- [ ] gate agent 自称恢复 HEAD 后，orchestrator 独立验证当前 HEAD 与 restored_head 一致：

```javascript
  const headVerify = await dispatchImpl(buildPrompt('headVerifier', {}), {
    schema: { type: 'object', required: ['status', 'evidence'], additionalProperties: true,
      properties: { status: { type: 'string', enum: ['ok'] },
        evidence: { type: 'object', required: ['head'], properties: { head: { type: 'string' } } },
        summary: { type: 'string' } } },
    label: `head-verify:${plan.id}`, phase: `Plan ${plan.id}` }, 'sonnet')
  if (headVerify.halted || headVerify.status !== 'ok' || headVerify.evidence?.head !== gate.evidence?.restored_head) {
    return await halt(plan, null, { reason: 'gate head restore verification failed', diag: {
      expected: gate.evidence?.restored_head, actual: headVerify.evidence?.head, sha: lastSha } })
  }
  log(`✓ plan ${plan.id} gate green @ ${lastSha} (${cmds.length} cmd${cmds.length === 1 ? '' : 's'})`)
} else {
  log(`plan ${plan.id}: no new commits, gate skipped`)
}
```

### Verify

- [ ] `helpers.test.js` 测 gateCommands 从 config 提取命令
- [ ] `sync.test.js` SCHEMAS 断言 gate evidence required 含 `['tests_exit_code', 'pytest_summary', 'lint_results', 'restored_head']`
- [ ] `sync.test.js` 断言 run-plans.js 须有 headVerifier dispatch + `gate head restore verification failed` halt

---

## Task 11: FinalReport — manifest、blocked.md、lessons.md commit、cross-reviewer 分组段

**目标:** finalReport agent 写 manifest + blocked.md（halt 模式），halt 后 commit lessons.md（W1-1 + H-F3/H-F7），cross-reviewer 分组段。

### Step 11.1 — agentWithFallback（fallback 链 [opus,sonnet,haiku,default]）

- [ ] finalReport / lessonDistiller fallback 链抽象为 agentWithFallback helper（仅用于保存进度，不降级继续开发）：

```javascript
// opus→sonnet→haiku 逐一尝试，全链失败用环境默认 model，再失败返回 null
async function agentWithFallback(role, ctx, labelPrefix) {
  for (const m of ['opus', 'sonnet', 'haiku']) {
    try {
      return await agent(buildPrompt(role, ctx), { schema: SCHEMAS[role], model: m, label: `${labelPrefix}:${m}` })
    } catch (e) { log(`${labelPrefix} ${m} 不可用: ${errStr(e)}, 试下一个`) }
  }
  log('fallback 链全失败，用环境默认 model 保存')
  try {
    return await agent(buildPrompt(role, ctx), { schema: SCHEMAS[role], label: `${labelPrefix}:default` })
  } catch (e) {
    log(`✗ 环境默认 model 也失败，${labelPrefix} 无法保存: ${errStr(e)}`)
    return null
  }
}
```

### Step 11.2 — halt() 累积 blocked_info + distiller + finalReport halted 模式

- [ ] halt() 函数：ensurePerTaskDefaults 初始化 → distiller best-effort → finalReport halted 模式：

```javascript
async function halt(plan, task, r) {
  const tid = (plan && task?.id) ? `plan-${String(plan.seq).padStart(2, '0')}/${task.id}` : (task?.id || 'unknown')
  state.perTask[tid] = ensurePerTaskDefaults({ ...(state.perTask[tid] || {}), status: 'blocked',
    blocked_info: {
      plan: plan?.id, task: tid, reason: r.reason,
      category: r.diag?.blocked_category || r.diag?.file || r.diag?.reason || null,
      last_error: r.diag?.last_error || r.diag?.summary || r.reason,
      suggested_fix: r.diag?.suggested_fix || null,
      quota_exhausted: r.reason === 'model_unavailable',
      likely_source: haltLikelySource(r.reason),
      failed_approach: { task_id: tid, reason: r.reason, error: r.diag?.last_error || r.reason },
      raw: r.diag || {},
    }
  })
  phase('Finalize')
  const blockedInfo = JSON.stringify(state.perTask[tid].blocked_info)

  // distiller best-effort（§5.4）：opus 单次调用，失败跳过
  const lessonsAutoDistill = resolveLessonsAutoDistill(state.config)
  const lessonsPath = state.config?.lessons_path || ''
  if (lessonsAutoDistill && lessonsPath) {  // H-F3: 空 lessonsPath 跳过
    const haltInfo = state.perTask[tid].blocked_info
    const reviewHistory = state.perTask[tid]?.review_history || []
    const failedApproaches = state.failedApproaches[tid] || []
    const distillInput = JSON.stringify(distillLessonInput('halted', haltInfo, reviewHistory, failedApproaches))
    try {
      const distillResult = await agent(buildPrompt('lessonDistiller', { distillInput, lessonsPath }),
        { schema: SCHEMAS.lessonDistiller, model: 'opus', label: 'lesson-distiller' })
      if (distillResult?.decisions) {
        const applied = distillResult.decisions.filter(d => d.action !== 'skip').length
        log(`📋 lesson distiller: ${applied} 条 lesson 已更新（append/update）`)
      }
    } catch (e) { log(`⚠ lesson distiller 失败（best-effort 跳过）: ${errStr(e)}`) }
  }

  const fr = await agentWithFallback('finalReport', {
    mode: 'halted', stateJson: JSON.stringify(state), blockedInfo,
    runsDir: `runs/${state.runTs}`, runTs: state.runTs, lessonsPath,
    lessonsAutoDistill: String(lessonsAutoDistill),
  }, 'final-report')
  if (!fr) log('✗✗ 致命：finalReport 全链失败，manifest 未写入！请手动检查 runs/ 目录')
  log(`✗ HALT: ${r.reason} (plan ${plan?.id}, task ${tid})`)
  return { result: 'halted', reason: r.reason }
}
```

### Step 11.3 — finalReport prompt Step 6 commit lessons.md（W1-1 + H-F3/H-F7）

- [ ] finalReport prompt 须有 Step 6 commit lessons.md（halt 后），空 lessonsPath 跳过（H-F3），lessons_committed 字段（H-F7）：

```
finalReport prompt Step 6 (W1-1):
If {lessonsPath} is non-empty AND this is halted mode:
  Run `git commit {lessonsPath} -m "chore: auto-commit lessons.md from run {runTs}"`
  If commit succeeds → lessons_committed: true
  If commit fails → lessons_committed: false (do not block manifest write)
If {lessonsPath} is empty → lessons_committed: false (skip commit)
```

### Step 11.4 — finalReport prompt per_task 清单 + cross-reviewer 分组段

- [ ] finalReport prompt per_task 清单须含 opus_escalated（H3 防重复升级），halt 模式含 cross-reviewer 分组段：

```
finalReport prompt per_task structure (H3):
per_task:{<taskKey>:{status,model,review_rounds,files_touched_per_round,review_history,findings_history,
  oscillation_escalated_at_round,opus_escalated,commit_sha,simplify_reverted,simplify_review_findings,
  destructive_review_failed,destructive_review_findings,concerns,blocked_info}}

Cross-Reviewer Findings section (halt 模式, review round halt):
If halt was due to failed review round: include "## Cross-Reviewer Findings (per file)" section
grouping the halted task's blocked_info diagnostics by file, highlighting files where ≥2 reviewers reported.
```

### Step 11.5 — done 模式 finalReport

- [ ] workflow 正常完成时 dispatch finalReport done 模式：

```javascript
phase('Finalize')
const frDone = await agentWithFallback('finalReport', {
  mode: 'done', stateJson: JSON.stringify(state), blockedInfo: '',
  runsDir: `runs/${state.runTs}`, runTs: state.runTs,
  lessonsPath: state.config?.lessons_path || '',
  lessonsAutoDistill: String(resolveLessonsAutoDistill(state.config)),
}, 'final-report')
if (!frDone) log('✗✗ 致命：finalReport 全链失败，manifest 未写入！请手动检查 runs/ 目录')
log('✓ workflow done')
return { result: 'done', perTask: state.perTask }
```

### Verify

- [ ] `sync.test.js` 断言 finalReport prompt 含 `Commit lessons.md (W1-1, 2026-07-07)` Step 6
- [ ] `sync.test.js` 断言 finalReport prompt 含 `auto-commit lessons.md from run {runTs}`
- [ ] `sync.test.js` H3 断言 finalReport prompt per_task 清单含 `opus_escalated`
- [ ] `sync.test.js` 断言 finalReport prompt per_task 用 `{<taskKey>}` 占位符（非 `{<taskId>}`）

---

## Task 12: Lessons distiller — auto-distill、opus、自读写盘、distillLessonInput

**目标:** halt 时 lessonDistiller agent（opus）自读写 lessons.md，过滤瞬态事件后提炼可复用根因。distillLessonInput 纯函数构造输入。

### Step 12.1 — resolveLessonsAutoDistill（config → bool）

- [ ] lib.js 中 resolveLessonsAutoDistill（默认 true）：

```javascript
export function resolveLessonsAutoDistill(config) {
  const v = config?.lessons_auto_distill
  if (v === false) return false  // 显式关闭
  return true  // 默认 true + 显式 true
}
```

### Step 12.2 — distillLessonInput（构造 distiller 输入）

- [ ] lib.js 中 distillLessonInput，mode 枚举 'halted'|'done'：

```javascript
// mode: 'halted' | 'done'（spec §5.4）
export function distillLessonInput(mode, haltInfo, reviewHistory, failedApproaches) {
  return {
    mode,
    halt_reason: haltInfo?.reason || null,
    task_id: haltInfo?.task || null,
    review_history: Array.isArray(reviewHistory) ? reviewHistory : [],
    failed_approaches: Array.isArray(failedApproaches) ? failedApproaches : [],
    // 过滤瞬态事件提示：distiller 须排除 model_unavailable/agent_error 等瞬态原因
    filter_transient: ['model_unavailable', 'agent_error', 'review_empty', 'review_failed_no_findings'],
  }
}
```

### Step 12.3 — lessonDistiller prompt（opus，自读写盘）

- [ ] lessonDistiller prompt 须要求 agent 自己读 lessonsPath + 自己写回，过滤瞬态事件，编号格式 L-<timestamp>：

```
lessonDistiller prompt:
You are a lesson distiller. Read {lessonsPath} (existing lessons) and analyze the halt info.

INPUT (distillInput):
{distillInput}

RULES:
1. Filter transient events (model_unavailable, agent_error, review_empty, review_failed_no_findings) — these are NOT reusable root causes.
2. For non-transient halt reasons, distill a reusable root cause (not the raw halt reason as title).
3. Check existing lessons for semantic duplicates — if a similar lesson exists, UPDATE it (action: 'update', update_target_id: <existing id>).
4. If no similar lesson exists, APPEND a new one (action: 'append').
5. New lesson id format: L-<timestamp> (e.g. L-20260701T103320Z) — run `date -u +%Y%m%dT%H%M%SZ` to get timestamp.
6. Skip if no reusable root cause can be distilled (action: 'skip').

Write your decisions back to {lessonsPath} directly (you have fs access).
Return JSON: { decisions: [{ action: 'append'|'update'|'skip', id?, title?, detail?, category?, update_target_id? }] }
```

### Step 12.4 — halt() 中 distiller 调用（best-effort）

- [ ] halt() 中 distiller 用 opus 单次调用（非 fallback 链），失败 catch 跳过（见 Task 11.2 代码）。

### Verify

- [ ] `helpers.test.js` 测 resolveLessonsAutoDistill + distillLessonInput
- [ ] `sync.test.js` QC-4 字节比较 resolveLessonsAutoDistill + distillLessonInput
- [ ] `sync.test.js` 断言 distillLessonInput mode 枚举含 'halted'（非 'halt'）
- [ ] `sync.test.js` 断言 lessonDistiller prompt 含 `L-<timestamp>` 编号格式

---

## Task 13: Write files 边界 + Destructive 检测 + Schema 迁移

**目标:** formatWriteFilesScope（plan frontmatter 声明 + commit 边界检查）+ formatSchemaCheck（gate 迁移校验）+ commit destructive_changes 检测。

### Step 13.1 — formatWriteFilesScope（plan frontmatter → commit 边界）

- [ ] lib.js 中 formatWriteFilesScope，从 task_write_files 生成 commit agent 边界提示：

```javascript
export function formatWriteFilesScope(files) {
  if (!Array.isArray(files) || files.length === 0) return '(no write_files declaration — all files allowed)'
  return `Allowed files (from plan frontmatter write_files):\n${files.map(f => `- ${f}`).join('\n')}\nDo NOT commit files outside this list. If you need to write outside scope, return out_of_scope in diagnostics.`
}
```

### Step 13.2 — formatSchemaCheck（gate 迁移校验提示）

- [ ] lib.js 中 formatSchemaCheck，从 config 生成 gate agent schema 迁移校验提示：

```javascript
export function formatSchemaCheck(schemaTool, modelPaths, migrationPaths) {
  if (!schemaTool) return '(no schema_tool configured — skip migration check)'
  let out = `Schema migration check:\n- Tool: ${schemaTool}\n- Model paths: ${(modelPaths || []).join(', ') || '(none)'}\n- Migration paths: ${(migrationPaths || []).join(', ') || '(none)'}\n`
  out += 'Verify all migration files referenced by model_paths exist in migration_paths. Return migration_missing: true if any missing.'
  return out
}
```

### Step 13.3 — commit agent destructive_changes 检测

- [ ] commit prompt 须要求 agent 检测 deleted_code / file_deletion / signature_change，写入 diagnostics.destructive_changes，用 `git diff HEAD --numstat`（非 --cached）：

```
commit prompt destructive detection:
Run `git diff HEAD --numstat` (NOT --cached — files may not be git add-ed yet).
Detect:
- deleted_code: lines with deletion (numstat first column > 0)
- file_deletion: files deleted (numstat shows 0 0 <path> with D status)
- signature_change: function/method signature changes (analyze diff hunks)
Write to diagnostics.destructive_changes: [{type, file, detail}]
```

### Verify

- [ ] `helpers.test.js` 测 formatWriteFilesScope + formatSchemaCheck
- [ ] `sync.test.js` QC-4 字节比较 formatWriteFilesScope + formatSchemaCheck
- [ ] `sync.test.js` 断言 commit prompt 用 `git diff HEAD --numstat`（非 --cached）

---

## Task 14: 限额容错 + dispatchImpl retryModel — model_unavailable halt、agent_error 分类、retryModel 升级链

**目标:** dispatchImpl 统一 agent 派发 + retryModel 升级链 + quota/agent_error 分类。safeAgent 包装 review 调用。

### Step 14.1 — safeAgent（review agent 包装）

- [ ] safeAgent catch 异常 → classifyThrown 归类为 sentinel 返回：

```javascript
async function safeAgent(prompt, opts) {
  try { return await agent(prompt, opts) }
  catch (e) { return { status: classifyThrown(e), diagnostics: { error: errStr(e) } } }
}
```

### Step 14.2 — classifyThrown + isQuotaError（中文 router 限额识别）

- [ ] lib.js 中 isQuotaError 识别中英文限额关键词，classifyThrown 分流 quota→model_unavailable / 其余→agent_error：

```javascript
export function isQuotaError(e) {
  const msg = errStr(e).toLowerCase()
  // 英文: rate limit / quota / 429 / insufficient balance / model overloaded
  // 中文: 已达到/额度不足/超出调用限制（router 中文错误）
  return /rate limit|quota|429|insufficient balance|model overloaded|已达到|额度不足|超出调用限制/.test(msg)
}

export function classifyThrown(e) {
  return isQuotaError(e) ? 'model_unavailable' : 'agent_error'
}
```

### Step 14.3 — dispatchImpl（统一派发 + retryModel + null guard）

- [ ] dispatchImpl：首次 agent() + quota→halt + model_unavailable→halt + null→retryModel + retry null→halt：

```javascript
// retryModel：agent 返回 null（能力不足/限额耗尽被 runtime 吞为空响应）时用更强模型重试一次
async function dispatchImpl(prompt, opts, model, retryModel = null) {
  let impl
  try { impl = await agent(prompt, { ...opts, model }) }
  catch (e) {
    // P0-4: 非 quota 异常须封装 agent_error 返回（不 throw）
    if (isQuotaError(e)) return { halted: true, reason: 'model_unavailable', diag: { model, error: errStr(e) } }
    return { halted: true, reason: 'agent_error', diag: { model, error: errStr(e) } }
  }
  if (impl?.status === 'model_unavailable') return { halted: true, reason: 'model_unavailable', diag: impl.diagnostics }
  // agent() 返回 null：限额耗尽/thinking-only/能力不足
  if (impl == null) {
    if (retryModel && retryModel !== model) {
      log(`⚠ ${opts?.label || 'unknown'}: ${model} returned null, retry with ${retryModel}`)
      try {
        impl = await agent(prompt, { ...opts, model: retryModel })
        if (impl != null) {
          if (impl?.status === 'model_unavailable') return { halted: true, reason: 'model_unavailable', diag: impl.diagnostics }
          return impl
        }
      } catch (e) {
        if (isQuotaError(e)) return { halted: true, reason: 'model_unavailable', diag: { model: retryModel, error: errStr(e) } }
        return { halted: true, reason: 'agent_error', diag: { model: retryModel, error: errStr(e) } }
      }
    }
    const nullErr = retryModel ? `agent returned null (retry with ${retryModel} also exhausted)` : 'agent returned null (quota exhausted or capability failure)'
    return { halted: true, reason: 'model_unavailable', diag: { model, error: nullErr } }
  }
  return impl
}
```

### Step 14.4 — dispatchImpl 调用点（5 处 implementor + bootstrap + commit + simplify + contextFetcher + gate + headVerifier）

- [ ] 所有带 status 的 agent 调用走 dispatchImpl；review 三 agent 走 safeAgent（并行）：

| 调用点 | model | retryModel |
|--------|-------|------------|
| bootstrap | 'sonnet' | 'opus' |
| implementor 初始 | task.model | 'opus' |
| implementor blocked 升级 | 'opus' | - |
| implementor needs_context | task.model | 'opus' |
| implementor ctx blocked 升级 | 'opus' | - |
| implementor ctx retry | task.model | 'opus' |
| implementor failed retry | task.model | 'opus' |
| implementor fix-round | fixModel | 'opus' |
| contextFetcher | 'sonnet' | - |
| commit | 'sonnet' | - |
| simplify | 'sonnet' | - |
| gate | 'sonnet' | - |
| headVerifier | 'sonnet' | - |
| specReview/qualityReviewer | 'opus'（safeAgent） | - |
| hunter | 'sonnet'（safeAgent） | - |

### Step 14.5 — 顶层 catch 分流（quota vs agent_error）

- [ ] runTask 顶层 catch 须按 isQuotaError 分流，非一律 model_unavailable：

```javascript
try {
  r = await runTask(plan, task)
} catch (e) {
  const reason = isQuotaError(e) ? 'model_unavailable' : 'agent_error'
  r = { halted: true, reason, diag: { model: task.model || 'sonnet', error: errStr(e) } }
}
if (r.halted) { return await halt(plan, { id: task.id }, r) }
```

### Verify

- [ ] `helpers.test.js` 测 isQuotaError 识别中文 router 限额（`已达到 5 小时的使用上限` / `额度不足` / `超出调用限制`）
- [ ] `helpers.test.js` 测 classifyThrown quota→model_unavailable / 其余→agent_error
- [ ] `sync.test.js` QC-4 字节比较 isQuotaError + classifyThrown
- [ ] `sync.test.js` 断言 dispatchImpl retry 路径检查 `status==='model_unavailable'`（Q1）

---

## Task 15: 全量验证 + 文档同步

**目标:** 307 测试全绿（helpers.test + sync.test），spec §5.6 + USAGE.md 同步实现状态。

### Step 15.1 — 全量测试

- [ ] 运行全量测试（Node v26 用 glob 模式）：

```bash
node --test docs/superpowers/workflows/tests/*.test.js
```

- [ ] 验证测试数量 ≥ 297（含 v3 + W1 + 三维复核新增测试）
- [ ] 验证 sync.test.js 所有断言全绿（PROMPTS 字节一致 + QC-4 函数体字节一致 + W1 移植断言 + Q-F/H-F 修复断言）
- [ ] 验证 helpers.test.js 所有纯函数测试全绿

### Step 15.2 — spec §5.6 W1 port 同步

- [ ] `docs/superpowers/workflow-design.md` §5.6 须记录 W1 port 改进：
  - W1-1: bootstrap dirty_tree 分类 + finalReport commit lessons.md
  - W1-2: implementor Discipline 禁 git commit/add
  - W1-4: bootstrap 分类处理 dirty_tree（非一律 reset --hard）
  - W1-5a: specReview Task Scope Boundary
  - W1-5b: normalizeFilePath 路径归一
  - W1-5e: Lessons Learned Exemption 段落 + lessonsPath 注入

### Step 15.3 — spec §5.5 v3 improvements 同步

- [ ] spec §5.5 须记录 v3 改进：
  - findings 状态机 open→fixed→regressed→fixed（§5.5 E' 补 regressed→fixed 转换）
  - formatFindingsHistory 签名含 currentRound
  - OSCILLATING halt 改 flipFlop/regressed 驱动 + budget guard 5
  - shouldEscalateOnOscillation 仅判断升级，halt 上移
  - lessons 两层注入（Tier 1 universal + Tier 2 domain）

### Step 15.4 — USAGE.md 同步

- [ ] `docs/superpowers/workflows/USAGE.md` 须同步：
  - §2 config 字段：`review_budget`（默认 5）、`review_max_rounds`（默认 4，0=无限）、`lessons_auto_distill`（默认 true）、`lessons_path`、`build_command`、`schema_tool`/`model_paths`/`migration_paths`、`silent_failure_context`/`silent_failure_intro`
  - §4 args：`configPath`（required）、`plansDir`（required）、`plan`、`tasks`、`completed`
  - §7.1 fresh run（非 resume，git log driven）
  - §7.2 retryModel 升级链
  - §12 调试：OSCILLATING halt 诊断 + dirty_tree halt + simplify_reverted

### Step 15.5 — 旧 plan 归档

- [ ] 以下 4 个旧 plan 文件保留作历史参考（consolidated plan 已汇编其内容）：
  - `docs/superpowers/workflow-plans/2026-06-30-07-workflow-optimization.md`
  - `docs/superpowers/workflow-plans/2026-07-05-cross-reviewer-surfacing-plan.md`
  - `docs/superpowers/workflow-plans/2026-07-05-reviewer-consensus-matrix.md`
  - `docs/superpowers/workflow-plans/2026-07-06-review-v3-oscillation-findings-lessons.md`

### Verify

- [ ] `node --test docs/superpowers/workflows/tests/*.test.js` 全绿（≥297 tests）
- [ ] spec §5.5 + §5.6 与代码实现一致
- [ ] USAGE.md §2/§4/§7/§12 与代码实现一致
- [ ] consolidated plan 覆盖所有 6 个改进批次的核心改动

---

## Decision Audit Trail

### D1 (Eng+DX high): findings history 主导单源（v3）

**决策:** formatFindingsHistory(history, currentRound) 作为 fix-round findings 注入的唯一来源，★ 标本轮新增，不再单独注入 formatFindings(本轮)。

**理由:** 旧设计同时注入 formatFindings(本轮) + formatFindingsHistory(history)，导致本轮新 findings 重复出现两次（一次在"本轮"段，一次在 history [OPEN] 段）。D1 统一为 history 主导单源，★ 标记区分紧急度。

**替代方案否决:** 保留 formatFindings(本轮) + history 双源 — 重复注入，implementor 困惑。

### D2 (DX): ensurePerTaskDefaults + finalReport 字段同步（v3）

**决策:** 抽 ensurePerTaskDefaults helper（halt() + runTask 共用），finalReport prompt per_task 清单显式列出所有字段（含 opus_escalated）。

**理由:** halt() 在 tid='unknown'（bootstrap/gate halt）时 spread 空对象 → perTask 缺字段 → manifest schema 不稳定。ensurePerTaskDefaults 统一初始化 10+ 默认字段。finalReport per_task 清单含 opus_escalated 防 manifest strip 导致重复升级。

**替代方案否决:** halt() 内联字段初始化 — 与 runTask 重复，字段增删需两处同步易漂移。

### D3 (CEO): single plan release vs split（v3）

**决策:** v3 改进（OSC + findings 状态机 + lessons 两层注入）作为单个 plan 发布，非拆 3 个 plan。

**理由:** 5 个改进联动（A+B lessons → E' 状态机 → OSC flipFlop → F opus prompt），拆 plan 需定义中间状态兼容性，增加复杂度。单 plan 一次到位，TDD 保证每步可测。

**替代方案否决:** 拆 3 个 plan（lessons / findings / OSC）— 中间状态半成品，需额外兼容代码。

### D4 (CEO): review_budget 默认 5（v3）

**决策:** review_budget 默认 5（非 8），halt reason `review_not_converging`（非 `review max rounds`）。

**理由:** 无限模式（maxRounds=0）下 flipFlop=false 持续推进需 budget guard 兜底防同义变体漏报。5 轮足够判断收敛趋势，8 轮浪费 agent 资源。halt reason 改可操作——blocked.md 建议拆 task（非 "max rounds" 不可操作）。

**替代方案否决:** 默认 8 — 过多轮次，agent 资源浪费；halt reason `review max rounds` — 不可操作，用户不知如何接手。

---

## Self-Review

### Spec 覆盖检查

| spec 章节 | 本 plan 覆盖 Task | 一致性 |
|-----------|------------------|--------|
| §1 goals | Task 1（架构） | ✓ |
| §2 roles/model strategy | Task 2/3/5/9/10/11/12 | ✓ |
| §2.4 model_unavailable halt | Task 14 | ✓ |
| §4 agent boundary | Task 1/14 | ✓ |
| §4.4 state management | Task 1/2 | ✓ |
| §5 task execution | Task 3/5/8 | ✓ |
| §5.2 simplify Plan C | Task 9 | ✓ |
| §5.2.1 destructive review | Task 9 | ✓ |
| §5.3 max rounds | Task 8 | ✓ |
| §5.4 lessons distiller | Task 12 | ✓ |
| §5.5 v3 improvements | Task 4/7/8 | ✓ |
| §5.6 W1 port | Task 2/3/5/6/11 | ✓ |
| §6 bootstrap/resume | Task 2 | ✓ |
| §13g oscillation | Task 8 | ✓ |
| §13j cross-reviewer | Task 6 | ✓ |
| §13k bootstrap three-layer | Task 2 | ✓ |

### Placeholder 扫描

- [ ] 无 `{TODO}` / `{FIXME}` / `{TBD}` 占位符
- [ ] 所有代码块为完整实现（非伪代码）
- [ ] 所有 Verify section 有可执行断言

### 类型一致性

- [ ] taskKey 统一为 `plan-{seq}/{task.id}`（plan-scoped，2 位 padStart）
- [ ] perTask 字段统一（ensurePerTaskDefaults 10+ 字段）
- [ ] findings shape 统一 `[{source, severity, title, fix, file}]`
- [ ] lessons id 格式统一 `L-<timestamp>`（非 L-YYYYMMDD-NN）
- [ ] mode 枚举统一 `'halted'|'done'`（非 'halt'）

### W1 十项 P0/P1 修复覆盖

| 修复 ID | 内容 | 覆盖 Task |
|---------|------|-----------|
| Q-F1 | Exemption 编号 L-<timestamp> | Task 5 |
| Q-F2 | implementor 标 L-xxx 编号 | Task 3 |
| Q-F3 | normalizeFilePath 白名单扩展 | Task 6 |
| Q-F5 | normalizeFilePath 非贪婪匹配 | Task 6 |
| H-F1 | dirty_tree halt | Task 2 |
| H-F2 | git commit <path> | Task 2/11 |
| H-F3 | 空 lessonsPath 防御 | Task 11/12 |
| H-F4 | reviewer 空 guard | Task 5 |
| H-F5 | whitelist 扩展 | Task 6 |
| H-F6 | typeof 非 string 防御 | Task 6 |
| H-F7 | lessons_committed 字段 | Task 11 |

---

---

## 附录 A：历史批次与归档来源（2026-07-08 整合）

本 plan 是**回顾性 consolidated 文档**，汇编 6 个改进批次。2026-07-07/08 又新增两个批次（simplification TDD fix、AUDIT 阶段）——两者为**前瞻性实施 plan**，已实施完成（代码已落地、测试已绿），其完整 Task 步骤保留在归档原文中。本附录给出批次概览 + 关键 Task 映射 + 归档指针，便于追溯；**实施细节的权威来源是已归档的完整 plan + 当前代码**，本附录不重复逐步骤。

### A.1 run-plans.js 简化与一致性 TDD 修复（16 Task，2026-07-07）

**原文件**：`docs/superpowers/workflow-plans/2026-07-07-simplification-tdd-fix.md`（已归档 `archive/`）
**设计依据**：`docs/superpowers/specs/2026-07-07-simplification-tdd-fix-design.md`（已归档 `archive/`）
**审计依据**：`docs/superpowers/workflows/research/run-plans-simplification-audit-2026-07-07.md`（research 目录，保留）

**三批次 16 Task 概览**（严格串行 Batch 1→2→3，307→346 tests）：

| 批次 | 风险 | Task | 内容 | 累计测试 |
|---|---|---|---|---|
| Batch 1 | 低（安全网+文档） | T1-T3 | HIGH-1 lesson_categories 补链 + MEDIUM-1 trip-wire 守护 + B1-10 通用性守护 + LOW-1/2/3/4 + S11/S12/S14 清理 | 307→313 |
| Batch 2 | 中（纯函数抽取） | T4-T11 | S10 taskKey / S4 REVIEW_SOURCES / S9 makeHalt / S6 formatFindingItem / S1 checkImplStatus / S5 formatBulletSection / S7 QUOTA_HALT_NOTE / S8 STATIC_READONLY_NOTE | 313→332 |
| Batch 3 | 高（runtime 循环拆分） | T12-T16 | S2 recordReviewRound / decideReviewOutcome(10 action) / runFixRound / S3 simplify 三 helper + 主流程集成 | 332→346 |

**与本 plan 的映射**（本 plan Task 覆盖的核心改动 + 本批次复述的位置）：
- 本 plan **Task 1**（sync.test 骨架）→ 本批次强化 QC-4 `extractFunctionBody`（S9 makeHalt 等）
- 本 plan **Task 3**（implementor Discipline）→ 本批次 T10/T11（QUOTA_HALT_NOTE / STATIC_READONLY_NOTE prompt 去重）
- 本 plan **Task 5**（runReviewRound）→ 本批次 T12/T13（recordReviewRound / decideReviewOutcome 抽取，10 action 分支）
- 本 plan **Task 9**（simplify 方案 C）→ 本批次 T15/T16（checkSimplifyChanges / amendSimplifyCommit / revertSimplifyChanges 三 helper + 主流程集成）

**全量回归命令**：`node --test docs/superpowers/workflows/tests/*.test.js`
**CRLF 约定**（本项目特定）：commit 前 `perl -i -pe 's/(?<!\r)\n/\r\n/g' <file>`

### A.2 Refactor 类 Task AUDIT 阶段（5 Task，2026-07-08）

**原文件**：`docs/superpowers/workflow-plans/2026-07-08-refactor-audit-stage.md`（已归档 `archive/`）
**设计依据**：`docs/superpowers/specs/2026-07-08-refactor-audit-stage-design.md`（已归档 `archive/`）

**5 Task 概览**（350→360 tests）：

| Task | 内容 | 新增测试 |
|---|---|---|
| T1 | `AUDIT_DIRECTIVE` + `AUDIT_REFACTOR_KEYWORDS` 常量 + haltLikelySource 注释 | +3 |
| T2 | SCHEMAS `needs_audit_fix` + `audit_reason` + dispatchImpl halt + blocked.md 分类诊断 | +2 |
| T3 | implementor PROMPTS `{{auditDirective}}` 占位 + buildPrompt defaults 空串 | +2 |
| T4 | bootstrap `audit_required` 双层 guard + runTask 初始 dispatch 注入 + runtime fallback | +3 |
| T5 | 文档 §13l + §5 + §13b + §4.4 + `.gitignore` 加 `.audit/` | +0 |

**依赖图**：T1/T2/T3 可并行（独立）→ T4 依赖 T1+T3 → T5 最后。串行 T1→2→3→4→5。

**落地确认**（代码已验证）：
- `.claude/workflows/run-plans.js:486/503` dispatchImpl 双路径（首层 + retry）对称检查 `needs_audit_fix`
- `.claude/workflows/run-plans.js:848` SCHEMAS.implementor 含 `taskKey` 字段
- `.claude/workflows/run-plans.js:212` buildPrompt defaults `auditDirective: ''`
- `.claude/workflows/run-plans.js:1503` runTask 初始 dispatch 注入 `auditDirective: auditRequired ? AUDIT_DIRECTIVE : ''`

### A.3 三维审计修复（7 Task，2026-07-08）

**原文件**：`docs/superpowers/workflow-plans/2026-07-08-audit-feature-fixes.md`（已归档 `archive/`）
**审计依据**：`docs/superpowers/workflows/research/audit-feature-review-2026-07-08.md`（research 目录，保留）

**7 Task 概览**（361→368 tests，2 P0 + 5 P1）：

| Task | 优先级 | 内容 |
|---|---|---|
| T1 | **P0-1** | halt() blocked_info 加顶层 `diag: r.diag \|\| {}`（修 AUDIT 分类诊断渲染断裂） |
| T2 | **P0-2** | dispatchImpl retry 路径补 `needs_audit_fix` 检查（与首层对称，防 AUDIT halt 被 retry 吞） |
| T3 | P1-4 | SCHEMAS.implementor 加 `taskKey` 字段（needs_audit_fix 定位 `.audit/` 报告闭环） |
| T4 | **P1-5** | decideReviewOutcome budget/maxRounds halt 用 `?.` 防 diagnostics 缺失 TypeError（与 P0-2 同源——被吞的真实 halt reason） |
| T5 | P1-6 | spec §1/§4.2 软化"两层独立 guard"措辞补诚实边界 |
| T6 | P1-2 | USAGE.md 同步 AUDIT 阶段（已落地：§7 AUDIT halt 段 + §13 `.audit/`） |
| T7 | P1-3 | spec §6.1 消除 A3 报告格式测试与免责声明矛盾 |

**落地确认**（代码已验证，全 7 Task 已 commit）：
- `.claude/workflows/run-plans.js:1341` halt blocked_info 含 `diag: r.diag || {}`（P0-1 ✓）
- `.claude/workflows/run-plans.js:486/503` 双路径 `needs_audit_fix`（P0-2 ✓）
- `.claude/workflows/run-plans.js:848` taskKey schema（P1-4 ✓）
- git log: `4b72bed`(P1-6) / `55fa26a`(P1-2) / `b454271`(P1-3) 已 commit

**裁定不修**（复核 §8.2）：Hunter P1-5（revert diag 漏 commitSha）不成立——:1457 已有；Hunter P1-3（fix-round 不传 auditDirective）不成立——AUDIT 设计上只首轮跑；Spec P1-2（基线 350）不成立——feature 前确为 350。

### A.4 W3 hunter status 判定硬规则（1 Task，2026-07-07，移植自 OTC-Fund-SIP-Strategy）

**原文件**：OTC-Fund-SIP-Strategy 仓库 commit `0552195`（移植）
**问题**：hunter 报了 `silent_failures` 却 `status=ok` → `allGreen()` 误判通过 → 静默失败逃逸。

**根因**：
1. prompt 仅说 `status (ok|failed)` 未明确判定标准 → hunter 自行把 important 当作不阻塞
2. RED FLAG "有日志+合理 fallback" AND 关系不清晰 → hunter 把无日志的 `return 1.0` 误判为优雅降级

**修复（prompt 侧）**：
- **STATUS DETERMINATION 硬规则**：`silent_failures` 数组非空 → `status=failed`；为空 → `status=ok`。severity 不影响 status 判定——critical/important/minor 任一 finding 都触发 failed。禁止"报了 finding 却 status=ok"的矛盾输出。
- **优雅降级判定标准**：刻意的优雅降级须同时满足：① 有显式日志（`log.warning`/`error`，非注释/print 到 stdout）；② fallback 值类型正确且对调用方有意义。两者缺一即为静默失败。例如 `if not mapping: return 1.0` 无日志 → 静默失败（非优雅降级）。

**Task 概览**（368→368 tests，无新 test() 块，+2 assert.match 守护断言并入既有 W1 测试块）：

| Task | 内容 |
|---|---|
| W3 | hunter prompt 加 STATUS DETERMINATION 硬规则 + 优雅降级判定标准（lib.js + run-plans.js 双副本）+ sync.test 2 守护断言 + workflow-design.md §5.7 |

**落地确认**（代码已验证，已 commit）：
- `docs/superpowers/workflows/lib.js:1220-1222` hunter prompt 含 STATUS DETERMINATION + 优雅降级判定
- `.claude/workflows/run-plans.js:1137-1139` inline 副本字节一致
- `docs/superpowers/workflows/tests/sync.test.js:355-358` 2 个 W3 守护断言
- `docs/superpowers/workflow-design.md` §5.7 W3 移植记录
- git log: `ff5b99f` 已 commit

**测试**：368/368 node --test 全绿（W3 守护断言并入既有 W1 测试块，无新 test() 块）。

---

**END OF PLAN**
