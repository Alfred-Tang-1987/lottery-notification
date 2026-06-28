# Workflow Orchestrator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 Claude Code `Workflow` 工具上的多 plan 自动执行 orchestrator（`.claude/workflows/run-plans.js`），用 TDD 夯实纯函数，用 Plan 01 端到端验证编排。

**Architecture:** 纯函数（plan 解析 / 振荡检测 / prompt 填充 / 辅助）独立可测模块 `docs/superpowers/workflows/lib.js`（ES module，`node --test` 单测）。编排逻辑 `run-plans.js` 顶层 await（Workflow runtime 注入 `agent()`/`parallel()`/`phase()`/`log()` 全局）+ **inline 复制** lib.js 纯函数（Workflow 脚本不可 import 外部模块）。编排正确性靠 Plan 01 端到端（非单测）。设计依据 `docs/superpowers/workflow-design.md` §4/§5/§13。

**Tech Stack:** JavaScript（ES module）、`node:test`（Node 18+ 内置，纯函数单测）、Claude Code `Workflow` 工具（编排 runtime）。

> **演进记录**（2026-06-25）：[10 bug fix commits] 修复 review 反馈管道信息丢失（qualityReviewer 结构化 findings → `[object Object]`、hunter `silent_failures` 完全丢弃等）——详见 `docs/superpowers/workflows/lib.js` 新增 `collectReviewFindings`/`formatFindings`/`matchesPlanFilter` 及其测试。hunter 模型显式固定 `model:'sonnet'`。implementor prompt 新增 `{{fetchedContext}}` 独立占位符。
>
> **演进记录**（2026-06-28）：[去重重构] runTask 178→126 行。新增纯决策函数 `classifyThrown`/`reviewHaltReason`（进 lib.js，node:test 覆盖）+ runtime 胶水 `safeAgent`/`dispatchImpl`（留 run-plans.js，统一 10 处 implementor 派发 + 6 处 review lambda 的 try/catch）。分层原则：纯决策进 lib.js 可测，runtime 胶水（调 `agent()`）留 run-plans.js（lib.js 是纯模块不能调 runtime 全局）。`agent_error` 文档化为 orchestrator-internal sentinel（不入 schema enum）。
>
> **演进记录**（2026-06-28）：[halt 工作树脏状态] `halt()` 填 `blocked_info.likely_source`（`haltLikelySource(reason)` 纯函数，基于 reason 的确定性来源映射，非 dirty 推断）。finalReport prompt halt 时跑 `git status --porcelain` + `git diff --stat`（ground truth，best-effort），写 `blocked.md` Working Tree 段 + 接手指引。likely_source（语义线索）+ git status（真实状态）并存。

**约束（来自 workflow-design.md §4.3）：** orchestrator JS 无 fs / 无 subprocess / 无 `Date.now`/`Math.random`。所有 IO 委托 subagent；时间戳由 subagent 调 `date` 命令获得。

---

## File Structure

```
docs/superpowers/workflows/
├── package.json              # {type:module}，启用 node:test
├── lib.js                    # 纯函数真源（export，可 node 测）：
│                             #   leafTasks / detectOscillation / buildPrompt /
│                             #   allGreen / unionFiles / issuesFromReviews / SCHEMAS / PROMPTS
├── tests/
│   ├── leafTasks.test.js
│   ├── detectOscillation.test.js
│   ├── buildPrompt.test.js
│   ├── helpers.test.js
│   └── schemas.test.js
└── VALIDATION-T1.md          # Task 8 端到端记录
.claude/workflows/
└── run-plans.js              # 顶层编排 + inline 纯函数（自 lib.js 复制）+ meta/state/runTask/halt
workflow.config.json          # 项目根（§11.1）
```

**lib.js ↔ run-plans.js 关系**：lib.js 是可测真源；run-plans.js 顶部 inline 复制纯函数/SCHEMAS/PROMPTS。**改一处必须同步另一处**（run-plans.js 顶部注释标注来源行号）。

---

## Task 1: 测试基建 + 项目配置

**Files:**
- Create: `docs/superpowers/workflows/package.json`
- Create: `workflow.config.json`
- Create: `docs/superpowers/workflows/lib.js`
- Create: `docs/superpowers/workflows/tests/leafTasks.test.js`（占位，Task 2 填）

- [ ] **Step 1: 写 package.json**

```json
{
  "name": "lottery-workflow-lib",
  "version": "0.1.0",
  "type": "module",
  "private": true
}
```

- [ ] **Step 2: 写 workflow.config.json**（§11.1，命令用 uv，对齐 CLAUDE.md 环境现实）

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

- [ ] **Step 3: 写 lib.js 骨架**（export 占位，后续 task 填实现）

```javascript
// lottery-workflow-lib —— workflow orchestrator 纯函数真源
// 此文件被 node --test 测试；run-plans.js inline 复制其中的函数。

export function leafTasks(markdown) {
  throw new Error('not implemented') // Task 2
}

export function detectOscillation(filesTouchedPerRound) {
  throw new Error('not implemented') // Task 3
}

export function buildPrompt(role, ctx) {
  throw new Error('not implemented') // Task 4
}

export function allGreen(...reviews) {
  throw new Error('not implemented') // Task 4
}

export function unionFiles(...reviews) {
  throw new Error('not implemented') // Task 4
}

export function issuesFromReviews(...reviews) {
  throw new Error('not implemented') // Task 4
}

export const SCHEMAS = {} // Task 5
export const PROMPTS = {} // Task 6
```

- [ ] **Step 4: 写占位测试（验证 node --test 可跑）**

`docs/superpowers/workflows/tests/leafTasks.test.js`：
```javascript
import { test } from 'node:test'
import assert from 'node:assert/strict'

test('node:test harness works', () => {
  assert.equal(1 + 1, 2)
})
```

- [ ] **Step 5: 跑测试验证基建**

Run: `node --test docs/superpowers/workflows/tests/`
Expected: PASS（1 test）

- [ ] **Step 6: config smoke（验证 test_command 有效）**

Run: `uv run pytest --collect-only 2>&1 | tail -5`
Expected: 能 collect（或报告 no tests collected，但不报 command-not-found / config 错误）。若 pytest 未安装，先 `uv sync` 或记录阻塞。

- [ ] **Step 7: Commit**

```bash
git add docs/superpowers/workflows/ workflow.config.json
git commit -m "feat(workflow): 测试基建 + workflow.config.json + lib.js 骨架"
```

---

## Task 2: leafTasks（§13e 叶子优先解析，TDD）

**Files:**
- Modify: `docs/superpowers/workflows/tests/leafTasks.test.js`
- Modify: `docs/superpowers/workflows/lib.js`

- [ ] **Step 1: 写失败测试**

`docs/superpowers/workflows/tests/leafTasks.test.js`：
```javascript
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { leafTasks } from '../lib.js'

test('parent task with children yields only children (Plan 01 式嵌套)', () => {
  const md = [
    '## Task 1: uv 初始化',
    'body',
    '## Task 4: SQLModel schema',
    '### Task 4a: 基类',
    '### Task 4b: 号码',
    '### Task 4c: 通知',
    '## Task 5: 种子',
  ].join('\n')
  assert.deepEqual(leafTasks(md), ['T1', 'T4a', 'T4b', 'T4c', 'T5'])
})

test('task without children yields itself', () => {
  assert.deepEqual(leafTasks('## Task 3: DB engine'), ['T3'])
})

test('multi-digit task numbers', () => {
  assert.deepEqual(leafTasks('## Task 12: foo'), ['T12'])
})

test('ignores non-task headings', () => {
  const md = '## File Structure\n## Task 1: a\n### Subsection\n## Task 2: b'
  assert.deepEqual(leafTasks(md), ['T1', 'T2'])
})
```

- [ ] **Step 2: 跑测试验证失败**

Run: `node --test docs/superpowers/workflows/tests/leafTasks.test.js`
Expected: FAIL（`not implemented`）

- [ ] **Step 3: 实现 leafTasks**

替换 `lib.js` 中的 `leafTasks`：
```javascript
// 从 plan markdown 提取叶子 task ID（§13e 叶子优先规则）。
// 规则：## Task N 下若有 ### Task NX 子 task → 只取子 task；否则取 Task N 本身。
export function leafTasks(markdown) {
  const tops = []          // {id, children:[]}
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

- [ ] **Step 4: 跑测试验证通过**

Run: `node --test docs/superpowers/workflows/tests/leafTasks.test.js`
Expected: PASS（4 tests）

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/workflows/tests/leafTasks.test.js docs/superpowers/workflows/lib.js
git commit -m "feat(workflow): leafTasks 叶子优先 plan 解析（§13e）"
```

---

## Task 3: detectOscillation（§13g，TDD）

**Files:**
- Create: `docs/superpowers/workflows/tests/detectOscillation.test.js`
- Modify: `docs/superpowers/workflows/lib.js`

- [ ] **Step 1: 写失败测试**

`docs/superpowers/workflows/tests/detectOscillation.test.js`：
```javascript
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { detectOscillation } from '../lib.js'

test('fewer than 3 rounds → not oscillating', () => {
  assert.equal(detectOscillation([['f1'], ['f1']]).oscillating, false)
  assert.equal(detectOscillation([]).oscillating, false)
})

test('same file in >=3 rounds → oscillating', () => {
  const r = detectOscillation([['f1', 'f2'], ['f2', 'f3'], ['f1', 'f3']])
  assert.equal(r.oscillating, true)
  assert.match(r.reason, /f[123]/)
})

test('consecutive rounds fix same files completely → oscillating', () => {
  const r = detectOscillation([['a', 'b', 'c'], ['a', 'b', 'c', 'd'], ['x']])
  // a,b 出现在连续 2 round 且第 2 round 全部重叠
  assert.equal(r.oscillating, true)
})

test('healthy progression → not oscillating', () => {
  assert.equal(detectOscillation([['f1'], ['f2'], ['f3']]).oscillating, false)
})
```

- [ ] **Step 2: 跑测试验证失败**

Run: `node --test docs/superpowers/workflows/tests/detectOscillation.test.js`
Expected: FAIL（`not implemented`）

- [ ] **Step 3: 实现 detectOscillation**（自 §13g copy）

替换 `lib.js` 中的 `detectOscillation`：
```javascript
// 振荡检测（§13g）。纯数组操作，无 fs。
export function detectOscillation(filesTouchedPerRound) {
  if (filesTouchedPerRound.length < 3) return { oscillating: false }

  // 规则 1：同文件出现在 >=3 个 round → 振荡
  const fileRoundCount = {}
  for (const [i, files] of filesTouchedPerRound.entries()) {
    for (const f of files) {
      (fileRoundCount[f] ||= []).push(i)
    }
  }
  for (const [file, rounds] of Object.entries(fileRoundCount)) {
    if (rounds.length >= 3) {
      return { oscillating: true, reason: `${file} touched in ${rounds.length} rounds`, file, rounds }
    }
  }

  // 规则 2：连续 2 round 的 files 高度重叠（>=2 且完全重叠）→ 振荡
  for (let i = 1; i < filesTouchedPerRound.length; i++) {
    const prev = new Set(filesTouchedPerRound[i - 1])
    const curr = filesTouchedPerRound[i]
    const overlap = curr.filter(f => prev.has(f))
    if (overlap.length >= 2 && overlap.length === curr.length) {
      return { oscillating: true, reason: `consecutive rounds fix same files: ${overlap.join(',')}`, files: overlap }
    }
  }
  return { oscillating: false }
}
```

- [ ] **Step 4: 跑测试验证通过**

Run: `node --test docs/superpowers/workflows/tests/detectOscillation.test.js`
Expected: PASS（4 tests）

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/workflows/tests/detectOscillation.test.js docs/superpowers/workflows/lib.js
git commit -m "feat(workflow): detectOscillation 振荡检测（§13g）"
```

---

## Task 4: buildPrompt 框架 + review 辅助函数（TDD）

**Files:**
- Create: `docs/superpowers/workflows/tests/helpers.test.js`
- Create: `docs/superpowers/workflows/tests/buildPrompt.test.js`
- Modify: `docs/superpowers/workflows/lib.js`

> 注：`PROMPTS` 的完整文本在 Task 6 写。本 task 先建 `buildPrompt` 框架（占位 PROMPTS 字典 + 模板填充），让它可测；Task 6 填充实际 prompt 字符串后，buildPrompt 自动生效。

- [ ] **Step 1: 写辅助函数失败测试**

`docs/superpowers/workflows/tests/helpers.test.js`：
```javascript
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { allGreen, unionFiles, issuesFromReviews } from '../lib.js'

const ok = { status: 'ok', diagnostics: { files_touched: ['a.py'] } }
const ok2 = { status: 'ok', diagnostics: { files_touched: ['b.py'] } }
const bad = { status: 'failed', diagnostics: { files_touched: ['a.py'], issues: ['bug'] } }

test('allGreen true only if every review ok', () => {
  assert.equal(allGreen(ok, ok, ok), true)
  assert.equal(allGreen(ok, bad, ok), false)
})

test('unionFiles dedupes across reviews', () => {
  assert.deepEqual(unionFiles(ok, ok2, bad).sort(), ['a.py', 'b.py'])
})

test('issuesFromReviews collects issues from failed reviews', () => {
  assert.deepEqual(issuesFromReviews(ok, bad, ok2), ['bug'])
})
```

- [ ] **Step 2: 跑验证失败**

Run: `node --test docs/superpowers/workflows/tests/helpers.test.js`
Expected: FAIL（`not implemented`）

- [ ] **Step 3: 实现辅助函数**

替换 `lib.js` 的 `allGreen` / `unionFiles` / `issuesFromReviews`：
```javascript
// 3 个 review 全 ok 才算绿（§5）
export function allGreen(...reviews) {
  return reviews.every(r => r && r.status === 'ok')
}

// 合并所有 review 的 files_touched（去重），供振荡检测
export function unionFiles(...reviews) {
  const set = new Set()
  for (const r of reviews) for (const f of (r?.diagnostics?.files_touched || [])) set.add(f)
  return [...set]
}

// 收集 failed review 的 issues，供 implementor 修复 prompt
export function issuesFromReviews(...reviews) {
  const out = []
  for (const r of reviews) if (r && r.status === 'failed') out.push(...(r.diagnostics?.issues || []))
  return out
}
```

- [ ] **Step 4: 跑验证通过**

Run: `node --test docs/superpowers/workflows/tests/helpers.test.js`
Expected: PASS（3 tests）

- [ ] **Step 5: 写 buildPrompt 框架失败测试**

`docs/superpowers/workflows/tests/buildPrompt.test.js`：
```javascript
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { buildPrompt, PROMPTS } from '../lib.js'

test('buildPrompt fills ctx placeholders', () => {
  // 用一个临时 prompt 验证填充逻辑（PROMPTS 实际内容由 Task 6 填）
  const original = PROMPTS.bootstrap
  PROMPTS.bootstrap = 'config={{configPath}} plans={{plansDir}} task={{taskId}}'
  try {
    const out = buildPrompt('bootstrap', { configPath: 'c.json', plansDir: 'p/', taskId: 'T1' })
  assert.equal(out, 'config=c.json plans=p/ task=T1')
  } finally {
    PROMPTS.bootstrap = original
  }
})

test('buildPrompt throws on unknown role', () => {
  assert.throws(() => buildPrompt('nope', {}), /unknown role/)
})
```

- [ ] **Step 6: 跑验证失败**

Run: `node --test docs/superpowers/workflows/tests/buildPrompt.test.js`
Expected: FAIL（`not implemented`）

- [ ] **Step 7: 实现 buildPrompt 框架**

替换 `lib.js` 的 `buildPrompt`：
```javascript
// 用 ctx 填充 PROMPTS[role] 的 {{key}} 占位符（§13b）。
export function buildPrompt(role, ctx = {}) {
  const tpl = PROMPTS[role]
  if (!tpl) throw new Error(`unknown role: ${role}`)
  return tpl.replace(/\{\{(\w+)\}\}/g, (_, k) => (k in ctx ? String(ctx[k]) : `{{${k}}}`))
}
```

- [ ] **Step 8: 跑验证通过**

Run: `node --test docs/superpowers/workflows/tests/buildPrompt.test.js`
Expected: PASS（2 tests）

- [ ] **Step 9: 全量回归**

Run: `node --test docs/superpowers/workflows/tests/`
Expected: PASS（全部）

- [ ] **Step 10: Commit**

```bash
git add docs/superpowers/workflows/tests/helpers.test.js docs/superpowers/workflows/tests/buildPrompt.test.js docs/superpowers/workflows/lib.js
git commit -m "feat(workflow): buildPrompt 框架 + review 辅助函数（allGreen/unionFiles/issues）"
```

---

## Task 5: SCHEMAS（10 类 evidence schema，§13c）

**Files:**
- Create: `docs/superpowers/workflows/tests/schemas.test.js`
- Modify: `docs/superpowers/workflows/lib.js`

> 不引入 ajv（项目无 node deps）。测试只断言"每个 schema 定义了 required evidence 字段"；Workflow runtime 在 `agent({schema})` 时做真正的 JSON Schema 校验。

- [ ] **Step 1: 写失败测试**

`docs/superpowers/workflows/tests/schemas.test.js`：
```javascript
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { SCHEMAS } from '../lib.js'

const ROLES = ['bootstrap', 'implementor', 'specReview', 'qualityReviewer',
  'hunter', 'simplify', 'commit', 'contextFetcher', 'gate', 'finalReport']

test('every role has a schema', () => {
  for (const r of ROLES) assert.ok(SCHEMAS[r], `missing schema: ${r}`)
})

test('evidence-bearing roles require evidence field', () => {
  for (const r of ['bootstrap', 'implementor', 'commit', 'gate']) {
    assert.ok(SCHEMAS[r].properties.evidence, `${r} needs evidence prop`)
  }
})

test('review schemas require status enum', () => {
  for (const r of ['specReview', 'qualityReviewer', 'hunter']) {
    const s = SCHEMAS[r].properties.status
    assert.deepEqual(s.enum.sort(), ['failed', 'ok'])
  }
})

test('implementor evidence requires tests_exit_code + files_changed', () => {
  const req = SCHEMAS.implementor.properties.evidence.required
  for (const f of ['tests_exit_code', 'files_changed', 'pytest_summary']) {
    assert.ok(req.includes(f), `implementor missing ${f}`)
  }
})
```

- [ ] **Step 2: 跑验证失败**

Run: `node --test docs/superpowers/workflows/tests/schemas.test.js`
Expected: FAIL（SCHEMAS 为空）

- [ ] **Step 3: 实现 SCHEMAS**

替换 `lib.js` 的 `export const SCHEMAS = {}`：
```javascript
// 10 类 agent 的 evidence schema（§13c）。agent({schema}) 在 tool-call 层校验。
export const SCHEMAS = {
  bootstrap: {
    type: 'object', required: ['status', 'evidence'], additionalProperties: true,
    properties: {
      status: { type: 'string', enum: ['ok', 'failed', 'blocked'] },
      evidence: { type: 'object', required: ['config', 'plans', 'completed', 'dirty_tree'],
        properties: { config: { type: 'object' }, plans: { type: 'array' }, completed: { type: 'array' }, dirty_tree: { type: 'boolean' } } },
      diagnostics: { type: 'object' }, summary: { type: 'string' },
    },
  },
  implementor: {
    type: 'object', required: ['status', 'evidence'], additionalProperties: true,
    properties: {
      status: { type: 'string', enum: ['ok', 'failed', 'blocked', 'needs_context'] },
      evidence: { type: 'object', required: ['tests_exit_code', 'files_changed', 'pytest_summary'],
        properties: { tests_exit_code: { type: 'integer' }, files_changed: { type: 'array' }, pytest_summary: { type: 'string' } } },
      diagnostics: { type: 'object', properties: { blocked_category: { type: 'string' }, last_error: { type: 'string' }, suggested_fix: { type: 'string' } } },
      summary: { type: 'string' },
    },
  },
  specReview: reviewSchema(),
  qualityReviewer: reviewSchema(),
  hunter: { type: 'object', required: ['status'], additionalProperties: true,
    properties: { status: { type: 'string', enum: ['ok', 'failed'] },
      diagnostics: { type: 'object', properties: { files_touched: { type: 'array' }, silent_failures: { type: 'array' } } },
      summary: { type: 'string' } } },
  simplify: { type: 'object', required: ['evidence'], additionalProperties: true,
    properties: { evidence: { type: 'object', required: ['changed', 'files_changed'],
      properties: { changed: { type: 'boolean' }, files_changed: { type: 'array' } } }, summary: { type: 'string' } } },
  commit: { type: 'object', required: ['status', 'evidence'], additionalProperties: true,
    properties: { status: { type: 'string', enum: ['ok', 'failed'] },
      evidence: { type: 'object', required: ['commit_sha', 'committed_files'],
        properties: { commit_sha: { type: 'string' }, committed_files: { type: 'array' }, tests_at_commit: { type: 'integer' } } },
      diagnostics: { type: 'object' }, summary: { type: 'string' } } },
  contextFetcher: { type: 'object', required: ['diagnostics'], additionalProperties: true,
    properties: { diagnostics: { type: 'object', required: ['context'], properties: { context: { type: 'string' } } }, summary: { type: 'string' } } },
  gate: { type: 'object', required: ['status', 'evidence'], additionalProperties: true,
    properties: { status: { type: 'string', enum: ['ok', 'failed'] },
      evidence: { type: 'object', required: ['tests_exit_code', 'pytest_summary'],
        properties: { tests_exit_code: { type: 'integer' }, pytest_summary: { type: 'string' } } }, summary: { type: 'string' } } },
  finalReport: { type: 'object', required: ['summary'], additionalProperties: true,
    properties: { evidence: { type: 'object', properties: { manifest_path: { type: 'string' } } }, summary: { type: 'string' } } },
}

function reviewSchema() {
  return { type: 'object', required: ['status'], additionalProperties: true,
    properties: {
      status: { type: 'string', enum: ['ok', 'failed'] },
      diagnostics: { type: 'object', properties: { files_touched: { type: 'array' }, issues: { type: 'array' } } },
      summary: { type: 'string' },
    } }
}
```

> 注：`reviewSchema()` helper 必须在 `SCHEMAS` 引用前定义（function 声明提升，放 `SCHEMAS` 后亦可）。实现时置于 `SCHEMAS` 常量之前更清晰——若 lint 报 use-before-def，上移。

- [ ] **Step 4: 跑验证通过**

Run: `node --test docs/superpowers/workflows/tests/schemas.test.js`
Expected: PASS（4 tests）

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/workflows/tests/schemas.test.js docs/superpowers/workflows/lib.js
git commit -m "feat(workflow): 10 类 agent evidence schema（§13c）"
```

---

## Task 6: PROMPTS（10 类完整 prompt 模板，§13b）

**Files:**
- Modify: `docs/superpowers/workflows/lib.js`

> prompt 是自然语言产物，非 TDD（无断言可写）。本 task 填充 Task 4 建的 `PROMPTS = {}`，每个 prompt 编码：角色职责 + ctx 字段 + 必填 evidence + Red Flag。措辞是可工作初版，Task 8 端到端验证时迭代。

- [ ] **Step 1: 实现 PROMPTS**

替换 `lib.js` 的 `export const PROMPTS = {}`：
```javascript
// 10 类 agent prompt 模板（§13b）。{{key}} 由 buildPrompt(role, ctx) 填充。
export const PROMPTS = {
  bootstrap: `You are the BOOTSTRAP agent for the lottery-notification workflow orchestrator. Read project state and return structured data. You MAY write YAML frontmatter to plan files that lack it (idempotent). Modify no other files.

Inputs: configPath={{configPath}} plansDir={{plansDir}} runTs={{runTs}}

Steps:
1. Read {{configPath}} → {test_command, full_test_command, build_command, lint_command, spec_path, language}.
2. Config smoke: run test_command with --collect-only. If command-not-found or config typo → status=failed with error.
3. For each {{plansDir}}/*.md: if frontmatter (starts with ---) read task models; else generate — extract LEAF ids (## Task N with ### Task NX children → only NX; else N), modelHint (title contains 安全|加密|认证|JWT|CSRF|Fernet|算法|比对|策略|边界|集成|接口 → opus, else omit), write frontmatter at file top. Idempotent.
4. git log → completed task ids via convention feat(plan-X/T-Y).
5. git status --porcelain → dirty_tree.
6. For each leaf task return its model (sonnet|opus|undefined→sonnet).

Return {status, evidence:{config, plans:[{id, tasks:[{id, model}]}], completed:[...], dirty_tree}, summary}.
RED FLAG: evidence 必须是真实读取结果，绝不编造。`,

  implementor: `You are the IMPLEMENTOR for {{taskId}} (plan {{planId}}). TDD strict (RED→GREEN→REFACTOR). {{retryNote}}

Inputs: specPath={{specPath}} testCommand={{testCommand}} planFile={{planFilePath}} taskId={{taskId}} fixIssues={{fixIssues}}

Steps:
1. Read {{planFilePath}}, locate {{taskId}} section: files to create/modify, tests to write.
2. Read {{specPath}} relevant section; implement to spec.
3. RED: write failing test; run {{testCommand}}; confirm fail.
4. GREEN: minimal impl passing.
5. REFACTOR: clean; tests still green.
6. Self-review vs spec.
7. Run {{testCommand}}; record pytest summary + exit code. If fixIssues non-empty, this round fixes them.

Return {status, evidence:{tests_exit_code, files_changed:[...], pytest_summary}, diagnostics:{blocked_category, last_error, suggested_fix} (only if blocked), summary}.
- status=ok: done, tests_exit_code=0. - status=blocked: 障碍 (interface|file|spec|dependency|external) → fill diagnostics.
RED FLAG: tests_exit_code 必须真实，绝不编造 0。绝不跳过测试。遇障碍宁可 blocked 也不要伪造通过。`,

  specReview: `You are the SPEC-REVIEWER (model opus). Compare implementor's code against spec line-by-line. Verdict on CURRENT working tree (HEAD or staged).

Inputs: specPath={{specPath}} taskId={{taskId}} planFile={{planFilePath}} changedHint={{filesChanged}}

Steps:
1. git diff (or read changed files) for this task.
2. Read {{specPath}} section governing {{taskId}}.
3. For each spec requirement, verify code implements it. Record mismatches.
4. Record files_touched (files in the diff).

Return {status (ok|failed), diagnostics:{files_touched:[...], issues:[<spec requirement>: <code gap>]}, summary}.
RED FLAG: ok 仅当逐条 spec 全符合。绝不模糊通过。issues 要具体（哪条 spec + 代码哪里不符）。`,

  qualityReviewer: `You are the QUALITY-REVIEWER (model opus). Review code quality: architecture, boundaries, types, immutability, error handling, naming. Verdict on CURRENT tree.

Inputs: taskId={{taskId}} changedHint={{filesChanged}}

Steps:
1. Read changed files.
2. Check: 函数 <50 行, 文件 <800 行, 无深层嵌套 (>4), 错误显式处理, 无 mutation, 无硬编码值, 命名清晰.
3. Check domain-layer-zero-IO discipline (app/domain 无 DB/network import) if applicable.
4. Record files_touched.

Return {status (ok|failed), diagnostics:{files_touched:[...], issues:[...]}, summary}.
RED FLAG: ok 仅当无 HIGH 级问题。架构/安全/正确性问题必须 failed。`,

  hunter: `You are the SILENT-FAILURE-HUNTER. Hunt swallowed errors, bad fallbacks, missing error propagation, swallowed exceptions, except:pass, broad except hiding bugs, default values masking failures. Verdict on CURRENT tree.

Inputs: taskId={{taskId}} changedHint={{filesChanged}}

Steps:
1. Read changed files.
2. Find: try/except that pass or log-only, bare except, fallback returning wrong-type default, unhandled None, ignored return values, missing await, fire-and-forget without error path.
3. Record files_touched.

Return {status (ok|failed), diagnostics:{files_touched:[...], silent_failures:[...]}, summary}.
RED FLAG: 只报真正的静默失败（会导致 bug 被隐藏），不报刻意的优雅降级（有日志+合理 fallback）。`,

  simplify: `You are SIMPLIFY. Reduce code: dedupe, remove dead code, tighten naming, lower complexity. Behavior MUST be preserved (tests still pass). Be honest about whether you changed anything.

Inputs: taskId={{taskId}} filesChanged={{filesChanged}} simplifyFailed={{simplifyFailed}}

Steps:
1. Read changed files.
2. Apply only safe simplifications (behavior-preserving).
3. Run tests mentally or note you cannot (orchestrator will re-run review).
4. HONESTLY report changed (bool) + files_changed.

Return {evidence:{changed, files_changed:[...]}, summary}.
RED FLAG: changed 必须如实。orchestrator 不信任自报，会无条件重跑 review。若 simplifyFailed=true，跳过（orchestrator 已回退你的上一轮）。`,

  commit: `You are COMMIT. Create one atomic commit for task {{taskId}}. {{simplifyRevertNote}}

Inputs: taskId={{taskId}} planId={{planId}} testCommand={{testCommand}} simplifyFailed={{simplifyFailed}} simplifyFiles={{simplifyFiles}}

Steps:
1. If simplifyFailed=true: first git checkout -- each file in simplifyFiles (revert bad simplify), then proceed.
2. git status --porcelain → see staged/unstaged.
3. Run {{testCommand}} on current tree; confirm exit 0. If fail → status=failed (do NOT commit).
4. git add -A; git commit -m "feat({{planIdShort}}/{{taskId}}): {{taskTitle}}" (planIdShort = plan-01 etc).
5. git rev-parse HEAD → commit_sha.

Return {status (ok|failed), evidence:{commit_sha, committed_files:[...], tests_at_commit}, summary}.
RED FLAG: tests exit != 0 时绝不 commit（status=failed）。commit_sha 必须真实。`,

  contextFetcher: `You are CONTEXT-FETCHER. The implementor requested context (NEEDS_CONTEXT). Find and return it. Read-only.

Inputs: needType={{needType}} query={{query}} specPath={{specPath}} workdir={{workdir}}

Steps by needType:
- file/path: grep/glob workdir for query, return paths.
- interface: LSP or regex extract function/class signatures.
- spec/doc: read {{specPath}} or named doc, extract relevant section.
- dependency: read prior task code, extract key impl.
- external: Context7 or WebSearch query.

Return {diagnostics:{context: <findings text>}, summary}.
RED FLAG: context 必须是真实查到的，绝不编造。查不到 → context="not found: <query>"。`,

  gate: `You are PLAN-GATE. Independently re-run the full test suite on the committed SHA (do NOT trust implementor self-report). Then restore HEAD.

Inputs: sha={{sha}} fullTestCommand={{fullTestCommand}}

Steps:
1. git checkout {{sha}}.
2. Run {{fullTestCommand}}. Record REAL exit code + summary.
3. git checkout - (restore previous HEAD). CRITICAL: must restore or downstream tasks break.
4. If step 3 fails, git checkout <previous-branch> explicitly.

Return {status (ok|failed), evidence:{tests_exit_code, pytest_summary}, summary}.
RED FLAG: tests_exit_code 必须真实（你在 committed SHA 上亲跑）。必须 checkout 回原 HEAD。exit != 0 → status=failed。`,

  finalReport: `You are FINAL-REPORT (mode={{mode}} done|halted). Write the run manifest (the ONLY on-disk write in this workflow) and emit a digest.

Inputs: mode={{mode}} state={{stateJson}} runsDir={{runsDir}} runTs={{runTs}}

Steps:
1. mkdir -p {{runsDir}}.
2. Write {{runsDir}}/manifest.json = {run_ts:{{runTs}}, mode:{{mode}}, plans:[...], per_task:{<taskId>:{status,model,review_rounds,files_touched_per_round,commit_sha,blocked_info}}, result}.
3. If mode=halted: also write {{runsDir}}/blocked.md (human-readable: which task, category, last_error, suggested_fix).
4. Print a digest summary (counts: done/blocked, total tasks, per-plan gate result).

Return {evidence:{manifest_path}, summary: <digest>}.
RED FLAG: manifest 必须真实写入磁盘（你 ls 确认）。stateJson 是 orchestrator 传入的完整状态，照实记录。`,
}
```

- [ ] **Step 2: 回归 buildPrompt 测试（验证 PROMPTS 填充后框架仍工作）**

Run: `node --test docs/superpowers/workflows/tests/`
Expected: PASS（全部；buildPrompt 测试用临时 PROMPTS.bootstrap，不受影响）

- [ ] **Step 3: 手工 spot-check 一个 prompt 填充**

Run:
```bash
node -e "import('./docs/superpowers/workflows/lib.js').then(m => console.log(m.buildPrompt('commit', {taskId:'T1', planId:'2026-06-21-01', planIdShort:'plan-01', taskTitle:'uv init', testCommand:'uv run pytest', simplifyFailed:'false', simplifyFiles:''}).slice(0,200)))"
```
Expected: 打印 commit prompt 前 200 字符，含 "feat(plan-01/T1): uv init"。若报错（ESM import 路径等），修正后重跑。

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/workflows/lib.js
git commit -m "feat(workflow): 10 类 agent prompt 模板（§13b）"
```

---

## Task 7: run-plans.js 编排主体（§13a，inline 纯函数 + 顶层编排）

**Files:**
- Create: `.claude/workflows/run-plans.js`
- Modify: `docs/superpowers/workflows/lib.js`（增强 bootstrap prompt，补 file/seq/title 字段）

> 编排逻辑（main/runTask/halt）无法单测（依赖 Workflow runtime 全局 `agent()`/`parallel()`），靠 Task 8-9 端到端验证。纯函数/SCHEMAS/PROMPTS **inline 复制**自 lib.js（Workflow 脚本不可 import 外部模块）。

- [ ] **Step 1: 增强 bootstrap prompt（补 file/seq/title，保证 type consistency）**

在 `lib.js` 的 `PROMPTS.bootstrap` 的 step 3/6 中，让返回的 plans 项结构为 `{id, file, seq, tasks:[{id, model, title}]}`：
- step 3 补："记录每个 plan 的 file（完整路径）和 seq（文件名末尾两位数字，如 01）"
- step 6 补："task 项含 title（Task header 的描述文本）"

具体：编辑 `PROMPTS.bootstrap`，把 `plans:[{id, tasks:[{id, model}]}]` 改为 `plans:[{id, file, seq, tasks:[{id, model, title}]}]`，并在 Steps 里说明 file=完整路径、seq=文件名末尾两位、title=header 描述。

Run: `node --test docs/superpowers/workflows/tests/` → 仍 PASS（prompt 文本变化不影响纯函数测试）。

- [ ] **Step 2: 写 run-plans.js**

`.claude/workflows/run-plans.js`：
```javascript
// workflow orchestrator —— 多 plan 自动执行（workflow-design.md §4/§5/§13）
// 纯函数/SCHEMAS/PROMPTS inline 自 docs/superpowers/workflows/lib.js —— 改 lib 必须同步改这里。
// 顶层 await = Workflow 入口；agent/parallel/phase/log/args/budget 为 Workflow runtime 注入的全局。

export const meta = {
  name: 'run-plans',
  description: '自动执行 implementation plans：每 task implementor→review chain→commit，plan 级独立 gate',
  phases: [
    { title: 'Bootstrap', detail: '读 config/plan/git log + 生成 frontmatter' },
    { title: 'Plan', detail: '串行 task + review rounds + simplify + commit + plan gate' },
    { title: 'Finalize', detail: '写 manifest + digest' },
  ],
}

// ===== 纯函数（inline 自 lib.js Task 2-4，逐字复制）=====
function leafTasks(markdown) {
  const tops = []; let current = null
  for (const line of markdown.split('\n')) {
    const m1 = line.match(/^##\s+Task\s+(\d+)\b/)
    if (m1) { current = { id: 'T' + m1[1], children: [] }; tops.push(current); continue }
    const m2 = line.match(/^###\s+Task\s+(\d+)([a-z])\b/)
    if (m2 && current) { current.children.push('T' + m2[1] + m2[2]) }
  }
  return tops.flatMap(t => t.children.length ? t.children : [t.id])
}
function detectOscillation(filesTouchedPerRound) {
  if (filesTouchedPerRound.length < 3) return { oscillating: false }
  const cnt = {}
  for (const [i, files] of filesTouchedPerRound.entries()) for (const f of files) (cnt[f] ||= []).push(i)
  for (const [file, rounds] of Object.entries(cnt)) if (rounds.length >= 3) return { oscillating: true, reason: `${file} touched in ${rounds.length} rounds`, file, rounds }
  for (let i = 1; i < filesTouchedPerRound.length; i++) {
    const prev = new Set(filesTouchedPerRound[i - 1]); const curr = filesTouchedPerRound[i]
    const overlap = curr.filter(f => prev.has(f))
    if (overlap.length >= 2 && overlap.length === curr.length) return { oscillating: true, reason: `consecutive rounds fix same files: ${overlap.join(',')}`, files: overlap }
  }
  return { oscillating: false }
}
function buildPrompt(role, ctx = {}) {
  const tpl = PROMPTS[role]; if (!tpl) throw new Error(`unknown role: ${role}`)
  return tpl.replace(/\{\{(\w+)\}\}/g, (_, k) => (k in ctx ? String(ctx[k]) : `{{${k}}}`))
}
function allGreen(...reviews) { return reviews.every(r => r && r.status === 'ok') }
function unionFiles(...reviews) {
  const set = new Set(); for (const r of reviews) for (const f of (r?.diagnostics?.files_touched || [])) set.add(f); return [...set]
}
function issuesFromReviews(...reviews) {
  const out = []; for (const r of reviews) if (r && r.status === 'failed') out.push(...(r.diagnostics?.issues || [])); return out
}

// ===== SCHEMAS / PROMPTS（复制自 lib.js Task 5 Step 3 / Task 6 Step 1，整段照搬）=====
// ⚠️ 实现时：把 lib.js 中 export const SCHEMAS = {...}（含 reviewSchema helper）整段复制到此。
//    把 lib.js 中 export const PROMPTS = {...} 整段复制到此。
//    （去 export 关键字，改为本地 const。内容与 lib.js 完全一致。）
const SCHEMAS = { /* 复制 lib.js SCHEMAS + reviewSchema */ }
const PROMPTS = { /* 复制 lib.js PROMPTS */ }

// ===== state（§4.4）=====
const state = {
  runTs: null, config: null, completed: [], currentPlan: null, currentTask: null,
  perTask: {},  // {taskId: {planId, status, model, review_rounds, files_touched_per_round, commit_sha, blocked_info}}
}

// ===== halt（§13a：累积 blocked_info → finalReport halted 模式写盘 + surface）=====
async function halt(plan, task, r) {
  const tid = task?.id || 'unknown'
  state.perTask[tid] = { ...(state.perTask[tid] || {}), status: 'blocked',
    blocked_info: { plan: plan?.id, task: tid, reason: r.reason, diag: r.diag || {} } }
  phase('Finalize')
  await agent(buildPrompt('finalReport', { mode: 'halted', stateJson: JSON.stringify(state), runsDir: 'runs', runTs: state.runTs }),
    { schema: SCHEMAS.finalReport, label: 'final-report:halted' })
  log(`✗ HALT: ${r.reason} (plan ${plan?.id}, task ${tid})`)
}

// ===== runTask（§13a：implementor + 升级链 + review rounds + simplify + commit）=====
async function runTask(plan, task) {
  state.currentTask = task.id
  const cfg = state.config
  const planIdShort = `plan-${plan.seq}`
  state.perTask[task.id] = { planId: plan.id, status: 'in_progress', model: task.model || 'sonnet', review_rounds: 0, files_touched_per_round: [], commit_sha: null, blocked_info: null }

  // —— implementor + BLOCKED 升级链（§2.3）——
  let model = task.model || 'sonnet'
  const implCtx = (fix, note) => ({ planId: plan.id, taskId: task.id, planFilePath: plan.file, specPath: cfg.spec_path, testCommand: cfg.test_command, fixIssues: fix, retryNote: note })
  let impl = await agent(buildPrompt('implementor', implCtx('', '')), { schema: SCHEMAS.implementor, model, label: `impl:${task.id}` })
  if (impl.status === 'blocked') {
    if (model === 'opus') return { halted: true, reason: 'opus BLOCKED', diag: impl.diagnostics }
    model = 'opus'
    impl = await agent(buildPrompt('implementor', implCtx('', '上一轮 sonnet BLOCKED，升级 opus 重试。')), { schema: SCHEMAS.implementor, model: 'opus', label: `impl:${task.id}:opus` })
    if (impl.status === 'blocked') return { halted: true, reason: 'opus BLOCKED', diag: impl.diagnostics }
  }
  let filesChanged = impl.evidence.files_changed || []

  // —— review rounds（max 3，§5）——
  for (let round = 1; round <= 3; round++) {
    state.perTask[task.id].review_rounds = round
    const fc = filesChanged.join(',')
    const [spec, qual, hunt] = await parallel([
      () => agent(buildPrompt('specReview', { taskId: task.id, specPath: cfg.spec_path, planFilePath: plan.file, filesChanged: fc }), { schema: SCHEMAS.specReview, model: 'opus', phase: `Plan ${plan.id}`, label: `spec:${task.id}:r${round}` }),
      () => agent(buildPrompt('qualityReviewer', { taskId: task.id, filesChanged: fc }), { schema: SCHEMAS.qualityReviewer, model: 'opus', label: `qual:${task.id}:r${round}` }),
      () => agent(buildPrompt('hunter', { taskId: task.id, filesChanged: fc }), { schema: SCHEMAS.hunter, label: `hunt:${task.id}:r${round}` }),
    ])
    state.perTask[task.id].files_touched_per_round.push(unionFiles(spec, qual, hunt))
    const osc = detectOscillation(state.perTask[task.id].files_touched_per_round)
    if (osc.oscillating) return { halted: true, reason: 'OSCILLATING', diag: osc }
    if (allGreen(spec, qual, hunt)) break
    if (round === 3) return { halted: true, reason: 'review max rounds', diag: { spec: spec.diagnostics, qual: qual.diagnostics, hunt: hunt.diagnostics } }
    impl = await agent(buildPrompt('implementor', implCtx(issuesFromReviews(spec, qual, hunt).join('; '), `修复 review round ${round} 问题。`)), { schema: SCHEMAS.implementor, model, label: `impl:${task.id}:fix${round}` })
    filesChanged = impl.evidence.files_changed || filesChanged
  }

  // —— simplify（max 1，§5.2：无条件重跑 review；失败则回退）——
  let simplifyFailed = false, simplifyFiles = []
  const simp = await agent(buildPrompt('simplify', { taskId: task.id, filesChanged: filesChanged.join(','), simplifyFailed: 'false' }), { schema: SCHEMAS.simplify, label: `simp:${task.id}` })
  if (simp.evidence.changed) {
    const fc = (simp.evidence.files_changed || []).join(',')
    const [spec2, qual2, hunt2] = await parallel([
      () => agent(buildPrompt('specReview', { taskId: task.id, specPath: cfg.spec_path, planFilePath: plan.file, filesChanged: fc }), { schema: SCHEMAS.specReview, model: 'opus', label: `spec:${task.id}:simp` }),
      () => agent(buildPrompt('qualityReviewer', { taskId: task.id, filesChanged: fc }), { schema: SCHEMAS.qualityReviewer, model: 'opus', label: `qual:${task.id}:simp` }),
      () => agent(buildPrompt('hunter', { taskId: task.id, filesChanged: fc }), { schema: SCHEMAS.hunter, label: `hunt:${task.id}:simp` }),
    ])
    if (!allGreen(spec2, qual2, hunt2)) { simplifyFailed = true; simplifyFiles = simp.evidence.files_changed || [] }
  }

  // —— commit（§5：状态原子转换；simplify 回退委托此 agent）——
  const commit = await agent(buildPrompt('commit', { taskId: task.id, planId: plan.id, planIdShort, taskTitle: task.title || task.id, testCommand: cfg.test_command, simplifyFailed: String(simplifyFailed), simplifyFiles: simplifyFiles.join(',') }), { schema: SCHEMAS.commit, label: `commit:${task.id}` })
  if (commit.status !== 'ok') return { halted: true, reason: 'commit failed', diag: commit.diagnostics }
  state.perTask[task.id].status = 'committed'
  state.perTask[task.id].commit_sha = commit.evidence.commit_sha
  log(`✓ ${task.id} committed @ ${commit.evidence.commit_sha}`)
  return { halted: false }
}

// ===== 顶层编排（Workflow 入口）=====
phase('Bootstrap')
const tsAgent = await agent('Run `date -u +%Y%m%dT%H%M%SZ` and return ONLY the timestamp string, nothing else.', { label: 'get-ts' })
state.runTs = typeof tsAgent === 'string' ? tsAgent.trim() : String(tsAgent)
const boot = await agent(buildPrompt('bootstrap', { configPath: args.configPath, plansDir: args.plansDir, runTs: state.runTs }), { schema: SCHEMAS.bootstrap, label: 'bootstrap' })
state.config = boot.evidence.config
state.completed = boot.evidence.completed

for (const plan of boot.evidence.plans) {
  if (args.plan && plan.id !== args.plan) continue
  state.currentPlan = plan.id
  phase(`Plan ${plan.id}`)
  const want = (args.tasks && args.tasks.length) ? new Set(args.tasks) : null
  const tasks = plan.tasks.filter(t => !want || want.has(t.id))
  for (const task of tasks) {
    if (state.completed.includes(task.id)) { log(`skip ${task.id} (already committed)`); continue }
    const r = await runTask(plan, task)
    if (r.halted) { await halt(plan, { id: task.id }, r); return { result: 'halted', reason: r.reason } }
  }
  // plan 级独立 gate（§3）：本 plan 最后 commit SHA 上重跑 full_test_command
  const lastSha = Object.values(state.perTask).filter(p => p.planId === plan.id && p.commit_sha).at(-1)?.commit_sha
  if (lastSha) {
    const gate = await agent(buildPrompt('gate', { sha: lastSha, fullTestCommand: state.config.full_test_command }), { schema: SCHEMAS.gate, label: `gate:${plan.id}`, phase: `Plan ${plan.id}` })
    if (gate.evidence.tests_exit_code !== 0) { await halt(plan, null, { reason: 'plan gate failed', diag: { sha: lastSha, summary: gate.evidence.pytest_summary } }); return { result: 'halted', reason: 'plan gate failed' } }
    log(`✓ plan ${plan.id} gate green @ ${lastSha}`)
  }
}

phase('Finalize')
await agent(buildPrompt('finalReport', { mode: 'done', stateJson: JSON.stringify(state), runsDir: 'runs', runTs: state.runTs }), { schema: SCHEMAS.finalReport, label: 'final-report' })
log('✓ workflow done')
return { result: 'done', perTask: state.perTask }
```

- [ ] **Step 3: 语法校验（node --check，不执行顶层 await）**

Run: `node --check .claude/workflows/run-plans.js`
Expected: 无输出（语法 OK）。若报语法错，修正。注：`node --check` 只校验语法，不执行（顶层 await 在 --check 下不跑），故不会因 `agent` 未定义报错。

- [ ] **Step 4: Commit**

```bash
git add .claude/workflows/run-plans.js docs/superpowers/workflows/lib.js
git commit -m "feat(workflow): run-plans.js 编排主体（§13a）+ bootstrap prompt 增强"
```

---

## Task 8: 端到端验证 —— Plan 01 T1 单 task 闭环

**Files:**
- Create: `docs/superpowers/workflows/VALIDATION-T1.md`

> 通过 `Workflow` 工具触发 run-plans.js，args 限定只跑 T1。验证 bootstrap→implementor→review×3→(simplify)→commit→gate 单 task 闭环。

- [ ] **Step 1: 跑 T1 only**

调用 Workflow 工具：
```
Workflow({
  scriptPath: '.claude/workflows/run-plans.js',
  args: {
    configPath: 'workflow.config.json',
    plansDir: 'docs/superpowers/plans',
    tasks: ['T1']            // 只跑 T1；plan 由 bootstrap 扫描，但 tasks 过滤只留 T1
  }
})
```
> 注：bootstrap 会扫描所有 plan 文件生成 frontmatter，但顶层 for 循环对每个 plan 的 tasks 用 `args.tasks` 过滤。Plan 01 的 T1 会被执行；其他 plan 的 T1 因 `want` 集合也匹配——需在 args 额外限定 plan，或接受「每个 plan 都跑它的 T1」。
> **修正**：若要严格只跑 Plan 01 的 T1，在 args 加 `plan: '2026-06-21-01'`，并在顶层循环加 `if (args.plan && plan.id !== args.plan) continue`。先在 run-plans.js 顶层循环开头加此过滤，再跑。

- [ ] **Step 2: 观察验证点**

Workflow 面板应显示：
1. Bootstrap phase：bootstrap agent 返回 config/plans/completed
2. Plan phase：`impl:T1` → 3 个 review 并行（spec/qual/hunt）→ 全绿或修复 → `commit:T1`
3. gate:01：在 T1 commit SHA 上重跑 pytest
4. Finalize：final-report 写 manifest

**通过标准**：
- T1 被 commit（git log 见 `feat(plan-01/T1)`）
- plan gate `tests_exit_code === 0`
- `runs/<runTs>/manifest.json` 存在，perTask.T1.status='committed'

- [ ] **Step 3: 记录结果**

`docs/superpowers/workflows/VALIDATION-T1.md`：记录实际表现、遇到的问题（schema 不匹配/agentType 未知/prompt 措辞等）、修复的迭代。失败则回到相关 Task 修 lib.js/run-plans.js 后重跑。

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/workflows/VALIDATION-T1.md .claude/workflows/run-plans.js
git commit -m "test(workflow): Plan 01 T1 端到端验证 + 修复迭代"
```

---

## Task 9: 端到端验证 —— 全 Plan 01 + resume

**Files:**
- Modify: `docs/superpowers/workflows/VALIDATION-T1.md`（追加全 plan + resume 记录）

- [ ] **Step 1: 跑全 Plan 01（7 叶子 task：T1/T2/T3/T4a/T4b/T4c/T4d）**

```
Workflow({ scriptPath: '.claude/workflows/run-plans.js',
  args: { configPath: 'workflow.config.json', plansDir: 'docs/superpowers/plans', plan: '2026-06-21-01' } })
```
（不传 tasks = 跑该 plan 全部叶子 task）

**通过标准**：7 task 全 committed + plan gate 全绿 + manifest 完整。

- [ ] **Step 2: resume 验证**

在 Step 1 跑到中途（如 T3 执行中）kill workflow，记下 runId。然后：
```
Workflow({ scriptPath: '.claude/workflows/run-plans.js', resumeFromRunId: '<runId>',
  args: { configPath: 'workflow.config.json', plansDir: 'docs/superpowers/plans', plan: '2026-06-21-01' } })
```
**通过标准**：
- 已 committed 的 task（T1/T2）被 bootstrap 跳过（git log 识别）
- 崩在 implementor 后/commit 前的 task（T3）被重跑（native resume 重跑未完成 agent，覆盖半成品）
- workflow 继续到完成

- [ ] **Step 3: 记录 + Commit**

```bash
# 追加全 plan + resume 结果到 VALIDATION-T1.md
git add docs/superpowers/workflows/VALIDATION-T1.md
git commit -m "test(workflow): 全 Plan 01 + resume 端到端验证"
```

---

## Self-Review

**1. Spec 覆盖**（对照 workflow-design.md §13a/§13b）：
- §13a JS 骨架（bootstrap→serial→review→commit→gate→post-plan）→ Task 7 ✓
- §13a runTask（升级链/review rounds/simplify/commit/振荡）→ Task 7 runTask ✓
- §13a halt 复用 finalReport → Task 7 halt ✓
- §13a 3 个约束（prompt 内联/schema 内联/resume 不自建）→ Task 7（inline + 顶层用 resumeFromRunId）✓
- §13b 10 类 prompt → Task 6 ✓
- §13e 叶子优先 → Task 2 ✓
- §13g 振荡检测 → Task 3 ✓
- §13c evidence schema → Task 5 ✓
- §13d manifest（finalReport 一次写）→ Task 7 finalReport prompt ✓
- workflow.config.json（§11.1）→ Task 1 ✓
- **gap**：§13f（workflow init / validate-plans 命令）—— 未覆盖。决策：标为后置（首次验证用手动 Workflow 调用 + leafTasks 已隐含 validate）。若需 onboarding 命令，后续单开 plan。

**2. Placeholder scan**：
- run-plans.js 的 `const SCHEMAS = { /* 复制 lib.js SCHEMAS */ }` / `PROMPTS` —— 是明确的"复制 Task 5/6 整段"指令，非 placeholder（源已完整存在上文）。✓
- 所有 TDD task 有完整测试 + 实现代码。✓

**3. Type consistency**：
- `leafTasks` / `detectOscillation` / `buildPrompt` / `allGreen` / `unionFiles` / `issuesFromReviews`：lib.js 与 run-plans.js inline 签名一致（Task 7 注释要求逐字复制）✓
- bootstrap 返回结构 `{id, file, seq, tasks:[{id, model, title}]}`：Task 6 原写 `{id, tasks:[{id, model}]}`，Task 7 Step 1 已增强对齐 ✓
- `plan.seq` → `plan-${plan.seq}`（commit message）：bootstrap 返回 seq ✓
- `state.perTask` 字段（status/model/review_rounds/files_touched_per_round/commit_sha/blocked_info）在 runTask/halt/finalReport 三处一致 ✓
- `args.plan` 过滤：Task 8 Step 1 注明要在顶层循环加 `if (args.plan && plan.id !== args.plan) continue` —— **这是 Task 7 的遗漏**，应在 Task 7 Step 2 的顶层循环补此过滤行（否则 Task 8 跑全 plan 时无法限定单 plan）。

**Self-review 修复**：Task 7 Step 2 顶层 for 循环，在 `for (const plan of boot.evidence.plans) {` 之后立即加：
```javascript
  if (args.plan && plan.id !== args.plan) { continue }
```
实现 Task 7 时务必包含此行（Task 8 依赖）。

