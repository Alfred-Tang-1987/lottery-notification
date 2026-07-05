# Reviewer Consensus Matrix 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add reviewer consensus matrix to run-plans.js review loop — detect reviewer disagreements, resolve via haiku/opus arbitration, and update spec/plan to eliminate ambiguity root causes.

**Architecture:** 5 pure functions in lib.js (mapCategories, detectConflictsLoose, detectConflictStalemate, formatArbitratorInput, formatConflictSummary) + 1 new PROMPT/SCHEMA (arbitrator) → inline copies in run-plans.js → review loop integration in runTask → sync.test.js guard. Haiku strict-resolution inline agent; opus arbitrator via dispatchImpl.

**Tech Stack:** JavaScript (Workflow runtime sandbox — no fs/Date.now/Math.random), node:test, Claude Code Workflow agent dispatch (haiku for strict resolution, opus for arbitrator)

**Runtime constraints (commit 67a4164):**
- `args` may be JSON-stringified by Workflow runtime → all entry points defend with `typeof args === 'string' ? JSON.parse(args) : args`
- Orchestrator is JS sandbox: no fs, no Date.now/Math.random, no subprocess
- `agent()` may return null (thinking-only empty response) → all dispatch paths handle null
- Pure functions → lib.js (`node --test` testable); runtime glue → run-plans.js
- Inline copies guarded by sync.test.js byte comparison

**Spec:** `docs/superpowers/workflow-plans/2026-07-05-reviewer-consensus-matrix.md`

---

### Task 1: lib.js — mapCategories + detectConflictsLoose 纯函数 (TDD)

**Files:**
- Create: (none — modify existing)
- Modify: `docs/superpowers/workflows/lib.js` (append before `export const SCHEMAS`)
- Modify: `docs/superpowers/workflows/tests/helpers.test.js` (append test blocks)

- [ ] **Step 1: Write failing tests for mapCategories**

Add to `docs/superpowers/workflows/tests/helpers.test.js`:

```javascript
// —— mapCategories ——
import { mapCategories } from '../lib.js'

test('mapCategories maps spec missing → correctness', () => {
  const findings = [{ source: 'spec', category: 'missing', title: 'x', file: 'a.py' }]
  const out = mapCategories(findings)
  assert.equal(out[0].unifiedCategory, 'correctness')
})

test('mapCategories maps quality error-handling → error-handling', () => {
  const findings = [{ source: 'quality', category: 'error-handling', title: 'x', file: 'a.py' }]
  const out = mapCategories(findings)
  assert.equal(out[0].unifiedCategory, 'error-handling')
})

test('mapCategories maps hunter swallowed-errors → error-handling', () => {
  const findings = [{ source: 'hunter', category: 'swallowed-errors', title: 'x', file: 'a.py' }]
  const out = mapCategories(findings)
  assert.equal(out[0].unifiedCategory, 'error-handling')
})

test('mapCategories maps quality sql-injection → security', () => {
  const findings = [{ source: 'quality', category: 'sql-injection', title: 'x', file: 'a.py' }]
  const out = mapCategories(findings)
  assert.equal(out[0].unifiedCategory, 'security')
})

test('mapCategories maps hunter missing-timeout → resource-mgmt', () => {
  const findings = [{ source: 'hunter', category: 'missing-timeout', title: 'x', file: 'a.py' }]
  const out = mapCategories(findings)
  assert.equal(out[0].unifiedCategory, 'resource-mgmt')
})

test('mapCategories maps quality print-instead-of-logger → observability', () => {
  const findings = [{ source: 'quality', category: 'print-instead-of-logger', title: 'x', file: 'a.py' }]
  const out = mapCategories(findings)
  assert.equal(out[0].unifiedCategory, 'observability')
})

test('mapCategories maps hunter log-and-forget → observability', () => {
  const findings = [{ source: 'hunter', category: 'log-and-forget', title: 'x', file: 'a.py' }]
  const out = mapCategories(findings)
  assert.equal(out[0].unifiedCategory, 'observability')
})

test('mapCategories maps quality blocking-in-async → concurrency', () => {
  const findings = [{ source: 'quality', category: 'blocking-in-async', title: 'x', file: 'a.py' }]
  const out = mapCategories(findings)
  assert.equal(out[0].unifiedCategory, 'concurrency')
})

test('mapCategories maps hunter missing-await → concurrency', () => {
  const findings = [{ source: 'hunter', category: 'missing-await', title: 'x', file: 'a.py' }]
  const out = mapCategories(findings)
  assert.equal(out[0].unifiedCategory, 'concurrency')
})

test('mapCategories unmapped category → unifiedCategory=null (not in conflict detection)', () => {
  const findings = [{ source: 'quality', category: 'immutability', title: 'x', file: 'a.py' }]
  const out = mapCategories(findings)
  assert.equal(out[0].unifiedCategory, null)
})

test('mapCategories unmapped hunter category → null', () => {
  const findings = [{ source: 'hunter', category: 'transaction-safety', title: 'x', file: 'a.py' }]
  const out = mapCategories(findings)
  assert.equal(out[0].unifiedCategory, null)
})

test('mapCategories unknown source → null', () => {
  const findings = [{ source: 'unknown', category: 'whatever', title: 'x', file: 'a.py' }]
  const out = mapCategories(findings)
  assert.equal(out[0].unifiedCategory, null)
})

test('mapCategories preserves all original fields', () => {
  const findings = [{ source: 'spec', category: 'missing', severity: 'critical', title: 'test', file: 'a.py', fix: 'add it' }]
  const out = mapCategories(findings)
  assert.equal(out[0].source, 'spec')
  assert.equal(out[0].category, 'missing')
  assert.equal(out[0].severity, 'critical')
  assert.equal(out[0].title, 'test')
  assert.equal(out[0].file, 'a.py')
  assert.equal(out[0].fix, 'add it')
})

test('mapCategories empty array → empty array', () => {
  assert.deepEqual(mapCategories([]), [])
})

// —— detectConflictsLoose ——
import { detectConflictsLoose } from '../lib.js'

test('detectConflictsLoose detects one conflict (two sources, same file+category)', () => {
  const findings = [
    { source: 'spec', unifiedCategory: 'correctness', title: 'missing X', file: 'a.py' },
    { source: 'quality', unifiedCategory: 'correctness', title: 'wrong way', file: 'a.py' },
  ]
  const out = detectConflictsLoose(findings)
  assert.equal(out.length, 1)
  assert.equal(out[0].file, 'a.py')
  assert.equal(out[0].unifiedCategory, 'correctness')
  assert.deepEqual(out[0].sources.sort(), ['quality', 'spec'])
})

test('detectConflictsLoose no conflict when same source has multiple findings on same file+category', () => {
  const findings = [
    { source: 'quality', unifiedCategory: 'error-handling', title: 'bare except', file: 'a.py' },
    { source: 'quality', unifiedCategory: 'error-handling', title: 'swallowed exc', file: 'a.py' },
  ]
  const out = detectConflictsLoose(findings)
  assert.equal(out.length, 0)
})

test('detectConflictsLoose no conflict when findings on different files', () => {
  const findings = [
    { source: 'spec', unifiedCategory: 'correctness', title: 'x', file: 'a.py' },
    { source: 'quality', unifiedCategory: 'correctness', title: 'y', file: 'b.py' },
  ]
  const out = detectConflictsLoose(findings)
  assert.equal(out.length, 0)
})

test('detectConflictsLoose no conflict when findings have different unifiedCategories', () => {
  const findings = [
    { source: 'spec', unifiedCategory: 'correctness', title: 'x', file: 'a.py' },
    { source: 'hunter', unifiedCategory: 'error-handling', title: 'y', file: 'a.py' },
  ]
  const out = detectConflictsLoose(findings)
  assert.equal(out.length, 0)
})

test('detectConflictsLoose skips findings without unifiedCategory', () => {
  const findings = [
    { source: 'spec', unifiedCategory: null, title: 'x', file: 'a.py' },
    { source: 'quality', unifiedCategory: null, title: 'y', file: 'a.py' },
  ]
  const out = detectConflictsLoose(findings)
  assert.equal(out.length, 0)
})

test('detectConflictsLoose skips findings without file', () => {
  const findings = [
    { source: 'spec', unifiedCategory: 'correctness', title: 'x' },
    { source: 'quality', unifiedCategory: 'correctness', title: 'y' },
  ]
  const out = detectConflictsLoose(findings)
  assert.equal(out.length, 0)
})

test('detectConflictsLoose detects multiple conflicts across different file+category groups', () => {
  const findings = [
    { source: 'spec', unifiedCategory: 'correctness', title: 'x', file: 'a.py' },
    { source: 'quality', unifiedCategory: 'correctness', title: 'y', file: 'a.py' },
    { source: 'quality', unifiedCategory: 'error-handling', title: 'bare except', file: 'b.py' },
    { source: 'hunter', unifiedCategory: 'error-handling', title: 'swallowed', file: 'b.py' },
  ]
  const out = detectConflictsLoose(findings)
  assert.equal(out.length, 2)
})

test('detectConflictsLoose includes all conflicting findings in each group', () => {
  const findings = [
    { source: 'spec', unifiedCategory: 'correctness', title: 'missing X', file: 'a.py' },
    { source: 'quality', unifiedCategory: 'correctness', title: 'wrong way', file: 'a.py' },
    { source: 'hunter', unifiedCategory: 'correctness', title: 'bad fallback', file: 'a.py' },
  ]
  const out = detectConflictsLoose(findings)
  assert.equal(out.length, 1)
  assert.equal(out[0].findings.length, 3)
  assert.deepEqual(out[0].sources.sort(), ['hunter', 'quality', 'spec'])
})

test('detectConflictsLoose empty array → empty array', () => {
  assert.deepEqual(detectConflictsLoose([]), [])
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd docs/superpowers/workflows && node --test 'tests/helpers.test.js'`
Expected: FAIL — `mapCategories` and `detectConflictsLoose` not exported from lib.js

- [ ] **Step 3: Implement mapCategories in lib.js**

Add after the `languageChecklist` function and before `export const SCHEMAS` in `docs/superpowers/workflows/lib.js`:

```javascript
// 共识矩阵：把三类 reviewer 的原生类别映射为统一类别（仅映射可能产生跨 reviewer 矛盾的类别）。
// 无映射的类别 → unifiedCategory=null → 不参与冲突检测（只参与现有 collectReviewFindings 反馈管道）。
// spec §3.2 完整映射表。纯函数，node --test 可测。
export function mapCategories(findings) {
  const MAPPING = {
    spec: {
      missing: 'correctness', extra: 'correctness', misunderstanding: 'correctness',
      'over-build': 'architecture',
      'missing-security': 'security',
    },
    quality: {
      'error-handling': 'error-handling',
      'bare-except': 'error-handling',
      'swallowed-exceptions': 'error-handling',
      'mutable-args': 'error-handling',
      'value-is-none': 'error-handling',
      'missing-type-hints': 'error-handling',
      'sql-injection': 'security',
      'command-injection': 'security',
      'missing-with': 'resource-mgmt',
      'n-plus-one': 'resource-mgmt',
      'print-instead-of-logger': 'observability',
      'blocking-in-async': 'concurrency',
      architecture: 'architecture',
      // 以下只有一个 reviewer 覆盖，不参与冲突检测
      immutability: null,
      naming: null,
      'code-size': null,
      'shadowing-builtins': null,
    },
    hunter: {
      'swallowed-errors': 'error-handling',
      'bad-fallbacks': 'error-handling',
      'lost-stack-traces': 'error-handling',
      'missing-timeout': 'resource-mgmt',
      'log-and-forget': 'observability',
      'wrong-severity-logs': 'observability',
      'missing-await': 'concurrency',
      'fire-and-forget': 'concurrency',
      'swallowed-errors-security': 'security',
      // 以下只有一个 reviewer 覆盖
      'transaction-safety': null,
    },
  }

  return findings.map(f => ({
    ...f,
    unifiedCategory: (MAPPING[f.source] || {})[f.category] || null,
  }))
}
```

- [ ] **Step 4: Implement detectConflictsLoose in lib.js**

Add after `mapCategories`:

```javascript
// 共识矩阵宽松匹配：按 (file, unifiedCategory) 分组 → 组中有 ≥2 个不同 source → 候选冲突。
// 纯函数，node --test 可测。spec §4.1。
export function detectConflictsLoose(findings) {
  const groups = {}
  for (const f of findings) {
    if (!f.file || !f.unifiedCategory) continue
    const key = `${f.file}::${f.unifiedCategory}`
    if (!groups[key]) groups[key] = { file: f.file, unifiedCategory: f.unifiedCategory, sources: new Set(), findings: [] }
    groups[key].sources.add(f.source)
    groups[key].findings.push(f)
  }
  return Object.values(groups).filter(g => g.sources.size >= 2)
    .map(g => ({ ...g, sources: [...g.sources] }))
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd docs/superpowers/workflows && node --test 'tests/helpers.test.js'`
Expected: ALL PASS (existing tests + 25 new tests)

- [ ] **Step 6: Commit**

```bash
git add docs/superpowers/workflows/lib.js docs/superpowers/workflows/tests/helpers.test.js
git commit -m "feat(workflow-NN/T1): mapCategories + detectConflictsLoose 纯函数"
```

---

### Task 2: lib.js — detectConflictStalemate + formatConflictSummary 纯函数 (TDD)

**Files:**
- Modify: `docs/superpowers/workflows/lib.js` (append after detectConflictsLoose)
- Modify: `docs/superpowers/workflows/tests/helpers.test.js` (append test blocks)

- [ ] **Step 1: Write failing tests for detectConflictStalemate**

Add to `docs/superpowers/workflows/tests/helpers.test.js`:

```javascript
// —— detectConflictStalemate ——
import { detectConflictStalemate } from '../lib.js'

test('detectConflictStalemate returns repeat when same (file, category, sources) found in history', () => {
  const conflicts = [{ file: 'a.py', unifiedCategory: 'error-handling', sources: ['quality', 'hunter'], findings: [] }]
  const history = [{ round: 1, file: 'a.py', unifiedCategory: 'error-handling', sources: ['quality', 'hunter'] }]
  const out = detectConflictStalemate(conflicts, history)
  assert.ok(out)
  assert.equal(out.length, 1)
  assert.equal(out[0].current.file, 'a.py')
  assert.equal(out[0].previous.round, 1)
})

test('detectConflictStalemate returns null when file differs', () => {
  const conflicts = [{ file: 'b.py', unifiedCategory: 'error-handling', sources: ['quality', 'hunter'], findings: [] }]
  const history = [{ round: 1, file: 'a.py', unifiedCategory: 'error-handling', sources: ['quality', 'hunter'] }]
  assert.equal(detectConflictStalemate(conflicts, history), null)
})

test('detectConflictStalemate returns null when unifiedCategory differs', () => {
  const conflicts = [{ file: 'a.py', unifiedCategory: 'security', sources: ['quality', 'hunter'], findings: [] }]
  const history = [{ round: 1, file: 'a.py', unifiedCategory: 'error-handling', sources: ['quality', 'hunter'] }]
  assert.equal(detectConflictStalemate(conflicts, history), null)
})

test('detectConflictStalemate returns null when sources differ (different reviewer pair)', () => {
  const conflicts = [{ file: 'a.py', unifiedCategory: 'error-handling', sources: ['spec', 'quality'], findings: [] }]
  const history = [{ round: 1, file: 'a.py', unifiedCategory: 'error-handling', sources: ['quality', 'hunter'] }]
  assert.equal(detectConflictStalemate(conflicts, history), null)
})

test('detectConflictStalemate returns null for empty history', () => {
  const conflicts = [{ file: 'a.py', unifiedCategory: 'error-handling', sources: ['quality', 'hunter'], findings: [] }]
  assert.equal(detectConflictStalemate(conflicts, []), null)
})

test('detectConflictStalemate returns null for empty conflicts', () => {
  const history = [{ round: 1, file: 'a.py', unifiedCategory: 'error-handling', sources: ['quality', 'hunter'] }]
  assert.equal(detectConflictStalemate([], history), null)
})

test('detectConflictStalemate sources match regardless of order', () => {
  const conflicts = [{ file: 'a.py', unifiedCategory: 'error-handling', sources: ['hunter', 'quality'], findings: [] }]
  const history = [{ round: 1, file: 'a.py', unifiedCategory: 'error-handling', sources: ['quality', 'hunter'] }]
  const out = detectConflictStalemate(conflicts, history)
  assert.ok(out)
})
```

- [ ] **Step 2: Write failing tests for formatConflictSummary**

Add to `docs/superpowers/workflows/tests/helpers.test.js`:

```javascript
// —— formatConflictSummary ——
import { formatConflictSummary } from '../lib.js'

test('formatConflictSummary round 1 format (haiku suggestion, no arbitrator verdict)', () => {
  const conflict = {
    file: 'a.py',
    unifiedCategory: 'error-handling',
    sources: ['quality', 'hunter'],
    findings: [
      { source: 'quality', title: 'error handling over-engineered', fix: 'remove retry wrapper' },
      { source: 'hunter', title: 'retry wrapper swallows ConnectionError', fix: 'add explicit log' },
    ],
  }
  const record = { round: 1, strict_result: 'genuine_conflict' }
  const out = formatConflictSummary(conflict, record)
  assert.match(out, /⚠ REVIEWER CONFLICT \[error-handling\]/)
  assert.match(out, /quality: "error handling over-engineered/)
  assert.match(out, /hunter: "retry wrapper swallows ConnectionError/)
  assert.match(out, /genuinely opposed/)
  assert.doesNotMatch(out, /⚖ ARBITRATOR/)
})

test('formatConflictSummary round 2 format (opus arbitrator verdict)', () => {
  const conflict = {
    file: 'a.py',
    unifiedCategory: 'error-handling',
    sources: ['quality', 'hunter'],
    findings: [
      { source: 'quality', title: 'error handling over-engineered', fix: 'remove retry wrapper' },
      { source: 'hunter', title: 'retry wrapper swallows ConnectionError', fix: 'add explicit log' },
    ],
  }
  const record = {
    round: 2,
    strict_result: 'genuine_conflict',
    arbitrator_verdict: 'hunter wins',
    arbitrator_rationale: 'safety > simplicity for lottery notifications',
    spec_updated: true,
  }
  const out = formatConflictSummary(conflict, record)
  assert.match(out, /⚖ ARBITRATOR RESOLVED \[error-handling\]/)
  assert.match(out, /hunter wins/)
  assert.match(out, /safety > simplicity/)
  assert.match(out, /Spec updated/)
})

test('formatConflictSummary arbitrator unclear → note that arbitrator could not resolve', () => {
  const conflict = {
    file: 'a.py',
    unifiedCategory: 'error-handling',
    sources: ['quality', 'hunter'],
    findings: [
      { source: 'quality', title: 'x', fix: 'a' },
      { source: 'hunter', title: 'y', fix: 'b' },
    ],
  }
  const record = { round: 2, strict_result: 'genuine_conflict', arbitrator_verdict: 'unclear' }
  const out = formatConflictSummary(conflict, record)
  assert.match(out, /unclear/)
  assert.match(out, /could not determine/)
})

test('formatConflictSummary handles missing fix gracefully', () => {
  const conflict = {
    file: 'a.py',
    unifiedCategory: 'correctness',
    sources: ['spec', 'quality'],
    findings: [
      { source: 'spec', title: 'missing feature X' },
      { source: 'quality', title: 'over-built Y' },
    ],
  }
  const record = { round: 1, strict_result: 'genuine_conflict' }
  const out = formatConflictSummary(conflict, record)
  assert.match(out, /missing feature X/)
  assert.match(out, /over-built Y/)
})

test('formatConflictSummary empty findings → still produces header', () => {
  const conflict = { file: 'a.py', unifiedCategory: 'error-handling', sources: ['quality', 'hunter'], findings: [] }
  const record = { round: 1, strict_result: 'genuine_conflict' }
  const out = formatConflictSummary(conflict, record)
  assert.match(out, /⚠ REVIEWER CONFLICT \[error-handling\] \(a\.py\)/)
})
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd docs/superpowers/workflows && node --test 'tests/helpers.test.js'`
Expected: FAIL — `detectConflictStalemate` and `formatConflictSummary` not exported

- [ ] **Step 4: Implement detectConflictStalemate in lib.js**

Add after `detectConflictsLoose`:

```javascript
// 共识矩阵冲突升级判定：同一 (file, unifiedCategory, sources) 已在 conflict_history 中出现过 → stalemate。
// 三个条件同时满足（file + unifiedCategory + sources 均相同）才判定为同一冲突。spec §5.1。
// 纯函数，node --test 可测。
export function detectConflictStalemate(conflicts, conflictHistory) {
  if (!Array.isArray(conflicts) || conflicts.length === 0) return null
  if (!Array.isArray(conflictHistory) || conflictHistory.length === 0) return null
  const repeatConflicts = []
  for (const c of conflicts) {
    const prev = conflictHistory.find(h =>
      h.file === c.file &&
      h.unifiedCategory === c.unifiedCategory &&
      h.sources.slice().sort().join(',') === c.sources.slice().sort().join(',')
    )
    if (prev) repeatConflicts.push({ current: c, previous: prev, rounds: [prev.round, 'current'] })
  }
  return repeatConflicts.length > 0 ? repeatConflicts : null
}
```

- [ ] **Step 5: Implement formatConflictSummary in lib.js**

Add after `detectConflictStalemate`:

```javascript
// 共识矩阵冲突摘要：将冲突 + 记录格式化为 implementor fixIssues 注入文本。
// Round 1（haiku 建议）与 Round 2（opus 仲裁）使用不同标题和细节级别。spec §4.3。纯函数，node --test 可测。
export function formatConflictSummary(conflict, record) {
  const cat = conflict.unifiedCategory || 'unknown'
  const file = conflict.file || 'unknown'
  const findings = conflict.findings || []
  const isArbitrated = record && record.arbitrator_verdict && record.arbitrator_verdict !== 'unclear'

  let out = ''
  if (isArbitrated) {
    out += `\n⚖ ARBITRATOR RESOLVED [${cat}] (${file}):\n`
    out += `  Verdict: ${record.arbitrator_verdict}\n`
    if (record.arbitrator_rationale) out += `  Rationale: ${record.arbitrator_rationale}\n`
    if (record.spec_updated) out += `  Spec updated: yes (see ## Clarification section)\n`
    if (record.plan_updated) out += `  Plan updated: yes (see task description)\n`
    out += `  Round 1 findings:\n`
    for (const f of findings) {
      out += `    - [${f.source}] ${f.title}${f.fix ? ` — fix: ${f.fix}` : ''}\n`
    }
    out += `  → implementor: follow the arbitrator's verdict above.\n`
  } else if (record && record.strict_result === 'unclear') {
    out += `\n⚠ REVIEWER CONFLICT [${cat}] (${file}):\n`
    out += `  Status: unclear — could not determine if genuinely opposed or same-direction.\n`
    for (const f of findings) {
      out += `  [${f.source}]: "${f.title}"${f.fix ? ` — fix: ${f.fix}` : ''}\n`
    }
    out += `  → implementor: use your best judgment. If uncertain, prefer the safer option.\n`
  } else {
    out += `\n⚠ REVIEWER CONFLICT [${cat}] (${file}):\n`
    for (const f of findings) {
      out += `  ${f.source}: "${f.title}"${f.fix ? ` — fix: ${f.fix}` : ''}\n`
    }
    if (record && record.strict_result === 'genuine_conflict') {
      out += `  Verdict: genuinely opposed — reviewers want opposite things.\n`
      out += `  → implementor: look for a compromise that addresses both concerns. If impossible, prefer the safer/conservative option.\n`
    }
  }
  return out
}
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd docs/superpowers/workflows && node --test 'tests/helpers.test.js'`
Expected: ALL PASS (including 12 new tests for detectConflictStalemate + formatConflictSummary)

- [ ] **Step 7: Commit**

```bash
git add docs/superpowers/workflows/lib.js docs/superpowers/workflows/tests/helpers.test.js
git commit -m "feat(workflow-NN/T2): detectConflictStalemate + formatConflictSummary 纯函数"
```

---

### Task 3: lib.js — formatArbitratorInput 纯函数 (TDD)

**Files:**
- Modify: `docs/superpowers/workflows/lib.js` (append before `export const SCHEMAS`)
- Modify: `docs/superpowers/workflows/tests/helpers.test.js` (append test block)

- [ ] **Step 1: Write failing tests for formatArbitratorInput**

Add to `docs/superpowers/workflows/tests/helpers.test.js`:

```javascript
// —— formatArbitratorInput ——
import { formatArbitratorInput } from '../lib.js'

test('formatArbitratorInput builds structured input from conflict + history', () => {
  const conflict = {
    file: 'app/services/fetch_service.py',
    unifiedCategory: 'error-handling',
    sources: ['quality', 'hunter'],
    findings: [
      { source: 'quality', title: 'over-engineered error handling', fix: 'remove retry wrapper' },
      { source: 'hunter', title: 'swallowed ConnectionError', fix: 'add explicit log+alert' },
    ],
  }
  const conflictHistory = [
    { round: 1, file: 'app/services/fetch_service.py', unifiedCategory: 'error-handling',
      sources: ['quality', 'hunter'], strict_result: 'genuine_conflict' },
  ]
  const plan = { file: 'docs/superpowers/plans/05-notification.md', seq: '05' }
  const out = formatArbitratorInput(conflict, conflictHistory, plan, 'docs/superpowers/specs/spec.md')
  assert.equal(out.file, 'app/services/fetch_service.py')
  assert.equal(out.category, 'error-handling')
  assert.equal(out.specPath, 'docs/superpowers/specs/spec.md')
  assert.equal(out.planFile, 'docs/superpowers/plans/05-notification.md')
  assert.ok(out.round1)
  assert.equal(out.round1.findings.length, 2)
})

test('formatArbitratorInput handles empty conflict history', () => {
  const conflict = { file: 'a.py', unifiedCategory: 'security', sources: ['spec', 'quality'], findings: [] }
  const out = formatArbitratorInput(conflict, [], { file: 'plan.md', seq: '01' }, 'spec.md')
  assert.equal(out.round1, null)
})

test('formatArbitratorInput handles missing plan gracefully', () => {
  const conflict = { file: 'a.py', unifiedCategory: 'correctness', sources: ['spec', 'quality'], findings: [] }
  const out = formatArbitratorInput(conflict, [], null, 'spec.md')
  assert.equal(out.planFile, '')
  assert.equal(out.taskId, '')
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd docs/superpowers/workflows && node --test 'tests/helpers.test.js' -t formatArbitratorInput`
Expected: FAIL — `formatArbitratorInput` not exported

- [ ] **Step 3: Implement formatArbitratorInput in lib.js**

Add after `formatConflictSummary`:

```javascript
// 共识矩阵：构造 arbitrator agent 的输入上下文（spec §5.2）。
// conflict: detectConflictsLoose 输出的单个冲突对象。
// conflictHistory: perTask 的 conflict_history[] 数组。
// plan: {file, seq} — 当前 plan 信息。
// specPath: config 的 spec_path。
// 纯函数，node --test 可测。
export function formatArbitratorInput(conflict, conflictHistory, plan, specPath) {
  const prevRound = (Array.isArray(conflictHistory) && conflictHistory.length > 0)
    ? conflictHistory[conflictHistory.length - 1] : null

  return {
    file: conflict.file || '',
    category: conflict.unifiedCategory || 'unknown',
    current_findings: (conflict.findings || []).map(f => ({
      source: f.source, title: f.title, fix: f.fix || '', severity: f.severity || '',
    })),
    round1: prevRound ? {
      findings: prevRound.findings || [],
      strict_result: prevRound.strict_result || 'unknown',
      arbitrator_verdict: prevRound.arbitrator_verdict || null,
    } : null,
    specPath: specPath || '',
    planFile: plan?.file || '',
  }
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd docs/superpowers/workflows && node --test 'tests/helpers.test.js'`
Expected: ALL PASS (including 3 new tests)

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/workflows/lib.js docs/superpowers/workflows/tests/helpers.test.js
git commit -m "feat(workflow-NN/T3): formatArbitratorInput 纯函数"
```

---

### Task 4: PROMPTS.arbitrator + SCHEMAS.arbitrator (lib.js 真源)

**Files:**
- Modify: `docs/superpowers/workflows/lib.js` (add PROMPT + SCHEMA)
- Modify: `docs/superpowers/workflows/tests/sync.test.js` (add assertions)

- [ ] **Step 1: Add PROMPTS.arbitrator to lib.js**

Add to `PROMPTS` object in `docs/superpowers/workflows/lib.js`, after `lessonDistiller`:

```javascript
  arbitrator: `You are ARBITRATOR (model opus). Two reviewers gave contradictory judgments on the same file+categories, and two rounds of fixes failed to resolve the disagreement. Your job: read the spec and plan, understand both positions, and make a definitive ruling.

Inputs: arbitratorInput={{arbitratorInput}}

## Your task
1. Read arbitratorInput: {file, category, current_findings, round1, specPath, planFile}.
   - current_findings: this round's findings from both reviewers [{source, title, fix, severity}]
   - round1: previous round's findings and arbitration result (null if first conflict detection)
2. Read the SPEC at specPath — find relevant sections governing the file and category in conflict.
3. Read the PLAN at planFile — understand the task's intended scope and constraints.
4. Understand BOTH reviewers' arguments. What is each one optimizing for? What would happen if the other's recommendation were followed?
5. Make a VERDICT. Choose from:
   - "spec wins": the spec reviewer is correct — implement exactly to spec
   - "quality wins": the quality reviewer is correct — follow their fix suggestion
   - "hunter wins": the silent-failure hunter is correct — add the defense they recommend
   - "compromise": both have valid points — here is a middle-ground solution
   - "both_correct_different_contexts": both are right in different scenarios — here is when to use each
   - "unclear": cannot determine — the spec/plan is too ambiguous even for me

## Write clarifications (if verdict !== "unclear")
After deciding, write clarifications to eliminate the root ambiguity:
- SPEC (specPath): APPEND ONLY. Do NOT modify existing content.
  - If specPath ends with a "## Clarification" section, append to it.
  - Otherwise, add "## Clarification" at the end of the file and append.
  - Format: "### CL-<ts> <category>: <one-line verdict>"
  - Body: "**Background**: <conflict summary> | **Ruling**: <which side wins + why> | **Impact**: <which tasks should note this>"
- PLAN (planFile): APPEND ONLY to the current task's description. Do NOT touch the frontmatter (--- block).
  - Find the ## Task N section for the relevant task.
  - Before the next ## heading (or end of file), append: "> ⚠ Clarification (CL-<ts>): <verdict summary>"

## RED FLAGS
- 绝不添加合规红线禁止的功能到 spec
- 遇到 model 限额耗尽 → 返回 status:'model_unavailable'（非 unclear）
- 无法判断时宁可 unclear 也不要编造裁决
- 只追加 spec/plan，不删不改现有内容
- 不碰 plan frontmatter YAML 块（--- ... ---）

Return {status (ok|model_unavailable), verdict, choice (spec|quality|hunter|compromise|both|unclear), rationale, spec_clarification_id, spec_updated (bool), plan_updated (bool), summary}.`,
```

- [ ] **Step 2: Add SCHEMAS.arbitrator to lib.js**

Add to `SCHEMAS` object in `docs/superpowers/workflows/lib.js`, after `lessonDistiller`:

```javascript
  arbitrator: { type: 'object', required: ['status', 'verdict', 'choice'], additionalProperties: true,
    properties: {
      status: { type: 'string', enum: ['ok', 'model_unavailable'] },
      verdict: { type: 'string', enum: ['spec wins', 'quality wins', 'hunter wins', 'compromise', 'both_correct_different_contexts', 'unclear'] },
      choice: { type: 'string', enum: ['spec', 'quality', 'hunter', 'compromise', 'both', 'unclear'] },
      rationale: { type: 'string' },
      spec_clarification_id: { type: 'string' },
      spec_updated: { type: 'boolean' },
      plan_updated: { type: 'boolean' },
      summary: { type: 'string' },
    } },
```

- [ ] **Step 3: Add sync.test.js assertions for new PROMPT/SCHEMA**

Add to `docs/superpowers/workflows/tests/sync.test.js`:

In the `ROLES` array (line 21), add `'arbitrator'`:
```javascript
const ROLES = ['bootstrap', 'implementor', 'specReview', 'qualityReviewer', 'hunter', 'simplify', 'commit', 'contextFetcher', 'gate', 'headVerifier', 'finalReport', 'lessonDistiller', 'arbitrator']
```

After the existing SCHEMAS assertions section, add:

```javascript
test('SCHEMAS.arbitrator 须 lib.js ↔ run-plans.js 一致', () => {
  // arbitrator verdict 枚举须同步
  for (const src of [libSrc, runSrc]) {
    assert.match(src, /arbitrator:\s*\{/, '须有 SCHEMAS.arbitrator')
    assert.match(src, /verdict:\s*\{[^}]*enum:\s*\[/, 'arbitrator schema 须含 verdict enum')
    assert.match(src, /'spec wins'/, 'arbitrator verdict 须含 spec wins')
    assert.match(src, /'hunter wins'/, 'arbitrator verdict 须含 hunter wins')
  }
})

test('PROMPTS.arbitrator 须含写入规则 + RED FLAGS', () => {
  for (const src of [libSrc, runSrc]) {
    const p = promptBody(src, 'arbitrator')
    assert.match(p, /APPEND ONLY/, 'arbitrator 须强调只追加不删改')
    assert.match(p, /## Clarification/, 'arbitrator 须写 Clarification 子节')
    assert.match(p, /spec_clarification_id/, 'arbitrator 须返回 spec_clarification_id')
    assert.match(p, /frontmatter/, 'arbitrator 须知不碰 frontmatter')
    assert.doesNotMatch(p, /合规红线禁止|预测|推荐/, 'arbitrator 不得添加合规红线禁止功能')
  }
})
```

- [ ] **Step 4: Run tests to verify**

Run: `cd docs/superpowers/workflows && node --test 'tests/sync.test.js' -t arbitrator`
Expected: FAIL — `arbitrator` role not yet in ROLES array (the prompt comparison test will fail because run-plans.js doesn't have the arbitrator inline yet — that's Task 5)

Actually, for this task, the sync.test changes will partially fail (run-plans.js doesn't have the inline copies yet). We'll add the assertions now but they'll fail until Task 5 inlines the copies. This is TDD — the test forces us to add the inline copies in Task 5.

Run: `cd docs/superpowers/workflows && node --test 'tests/helpers.test.js'`
Expected: ALL existing tests still PASS (lib.js PROMPTS/SCHEMAS are pure data, don't affect runtime tests)

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/workflows/lib.js docs/superpowers/workflows/tests/sync.test.js
git commit -m "feat(workflow-NN/T4): PROMPTS.arbitrator + SCHEMAS.arbitrator (lib.js 真源)"
```

---

### Task 5: run-plans.js — inline 副本 + 共识矩阵集成 (runtime glue)

**Files:**
- Modify: `.claude/workflows/run-plans.js` (inline copies + review loop integration)

**⚠️ Runtime constraint checklist (commit 67a4164):**
- [x] `args` already defended with `typeof args === 'string' ? JSON.parse(args) : args` (line 1213)
- [x] No fs/Date.now/Math.random usage
- [x] All `agent()` calls pass through `safeAgent` or `dispatchImpl` (null-guarded)
- [x] Pure function inline copies match lib.js exactly (will be guarded by sync.test byte comparison)

- [ ] **Step 1: Inline new pure functions in run-plans.js**

After the existing inline helpers (before the `// ===== runtime helper` section), add the inline copies of the 5 new pure functions. Each copy must match lib.js byte-for-byte:

```javascript
// 共识矩阵：把三类 reviewer 的原生类别映射为统一类别（仅映射可能产生跨 reviewer 矛盾的类别）。
// 无映射的类别 → unifiedCategory=null → 不参与冲突检测（只参与现有 collectReviewFindings 反馈管道）。
// spec §3.2 完整映射表。—— inline 自 lib.js
function mapCategories(findings) {
  const MAPPING = {
    spec: {
      missing: 'correctness', extra: 'correctness', misunderstanding: 'correctness',
      'over-build': 'architecture',
      'missing-security': 'security',
    },
    quality: {
      'error-handling': 'error-handling',
      'bare-except': 'error-handling',
      'swallowed-exceptions': 'error-handling',
      'mutable-args': 'error-handling',
      'value-is-none': 'error-handling',
      'missing-type-hints': 'error-handling',
      'sql-injection': 'security',
      'command-injection': 'security',
      'missing-with': 'resource-mgmt',
      'n-plus-one': 'resource-mgmt',
      'print-instead-of-logger': 'observability',
      'blocking-in-async': 'concurrency',
      architecture: 'architecture',
      immutability: null,
      naming: null,
      'code-size': null,
      'shadowing-builtins': null,
    },
    hunter: {
      'swallowed-errors': 'error-handling',
      'bad-fallbacks': 'error-handling',
      'lost-stack-traces': 'error-handling',
      'missing-timeout': 'resource-mgmt',
      'log-and-forget': 'observability',
      'wrong-severity-logs': 'observability',
      'missing-await': 'concurrency',
      'fire-and-forget': 'concurrency',
      'swallowed-errors-security': 'security',
      'transaction-safety': null,
    },
  }

  return findings.map(f => ({
    ...f,
    unifiedCategory: (MAPPING[f.source] || {})[f.category] || null,
  }))
}

// 共识矩阵宽松匹配：按 (file, unifiedCategory) 分组 → 组中有 ≥2 个不同 source → 候选冲突。
// 纯函数，node --test 可测。spec §4.1。—— inline 自 lib.js
function detectConflictsLoose(findings) {
  const groups = {}
  for (const f of findings) {
    if (!f.file || !f.unifiedCategory) continue
    const key = `${f.file}::${f.unifiedCategory}`
    if (!groups[key]) groups[key] = { file: f.file, unifiedCategory: f.unifiedCategory, sources: new Set(), findings: [] }
    groups[key].sources.add(f.source)
    groups[key].findings.push(f)
  }
  return Object.values(groups).filter(g => g.sources.size >= 2)
    .map(g => ({ ...g, sources: [...g.sources] }))
}

// 共识矩阵冲突升级判定：同一 (file, unifiedCategory, sources) 已在 conflict_history 中出现过 → stalemate。
// 三个条件同时满足（file + unifiedCategory + sources 均相同）才判定为同一冲突。spec §5.1。
// 纯函数，node --test 可测。—— inline 自 lib.js
function detectConflictStalemate(conflicts, conflictHistory) {
  if (!Array.isArray(conflicts) || conflicts.length === 0) return null
  if (!Array.isArray(conflictHistory) || conflictHistory.length === 0) return null
  const repeatConflicts = []
  for (const c of conflicts) {
    const prev = conflictHistory.find(h =>
      h.file === c.file &&
      h.unifiedCategory === c.unifiedCategory &&
      h.sources.slice().sort().join(',') === c.sources.slice().sort().join(',')
    )
    if (prev) repeatConflicts.push({ current: c, previous: prev, rounds: [prev.round, 'current'] })
  }
  return repeatConflicts.length > 0 ? repeatConflicts : null
}

// 共识矩阵冲突摘要：将冲突 + 记录格式化为 implementor fixIssues 注入文本。
// Round 1（haiku 建议）与 Round 2（opus 仲裁）使用不同标题和细节级别。spec §4.3。纯函数，node --test 可测。—— inline 自 lib.js
function formatConflictSummary(conflict, record) {
  const cat = conflict.unifiedCategory || 'unknown'
  const file = conflict.file || 'unknown'
  const findings = conflict.findings || []
  const isArbitrated = record && record.arbitrator_verdict && record.arbitrator_verdict !== 'unclear'

  let out = ''
  if (isArbitrated) {
    out += `\n⚖ ARBITRATOR RESOLVED [${cat}] (${file}):\n`
    out += `  Verdict: ${record.arbitrator_verdict}\n`
    if (record.arbitrator_rationale) out += `  Rationale: ${record.arbitrator_rationale}\n`
    if (record.spec_updated) out += `  Spec updated: yes (see ## Clarification section)\n`
    if (record.plan_updated) out += `  Plan updated: yes (see task description)\n`
    out += `  Round 1 findings:\n`
    for (const f of findings) {
      out += `    - [${f.source}] ${f.title}${f.fix ? ` — fix: ${f.fix}` : ''}\n`
    }
    out += `  → implementor: follow the arbitrator's verdict above.\n`
  } else if (record && record.strict_result === 'unclear') {
    out += `\n⚠ REVIEWER CONFLICT [${cat}] (${file}):\n`
    out += `  Status: unclear — could not determine if genuinely opposed or same-direction.\n`
    for (const f of findings) {
      out += `  [${f.source}]: "${f.title}"${f.fix ? ` — fix: ${f.fix}` : ''}\n`
    }
    out += `  → implementor: use your best judgment. If uncertain, prefer the safer option.\n`
  } else {
    out += `\n⚠ REVIEWER CONFLICT [${cat}] (${file}):\n`
    for (const f of findings) {
      out += `  ${f.source}: "${f.title}"${f.fix ? ` — fix: ${f.fix}` : ''}\n`
    }
    if (record && record.strict_result === 'genuine_conflict') {
      out += `  Verdict: genuinely opposed — reviewers want opposite things.\n`
      out += `  → implementor: look for a compromise that addresses both concerns. If impossible, prefer the safer/conservative option.\n`
    }
  }
  return out
}

// 共识矩阵：构造 arbitrator agent 的输入上下文（spec §5.2）。—— inline 自 lib.js
function formatArbitratorInput(conflict, conflictHistory, plan, specPath) {
  const prevRound = (Array.isArray(conflictHistory) && conflictHistory.length > 0)
    ? conflictHistory[conflictHistory.length - 1] : null

  return {
    file: conflict.file || '',
    category: conflict.unifiedCategory || 'unknown',
    current_findings: (conflict.findings || []).map(f => ({
      source: f.source, title: f.title, fix: f.fix || '', severity: f.severity || '',
    })),
    round1: prevRound ? {
      findings: prevRound.findings || [],
      strict_result: prevRound.strict_result || 'unknown',
      arbitrator_verdict: prevRound.arbitrator_verdict || null,
    } : null,
    specPath: specPath || '',
    planFile: plan?.file || '',
  }
}
```

- [ ] **Step 2: Inline PROMPTS.arbitrator + SCHEMAS.arbitrator**

In `PROMPTS` object, add `arbitrator` entry (byte-identical to lib.js). In `SCHEMAS` object, add `arbitrator` entry (byte-identical to lib.js). Follow the existing pattern — copy the exact string from lib.js Task 4.

- [ ] **Step 3: Add conflict_history to perTask initialization**

In `ensurePerTaskDefaults` (around line 879), add `conflict_history: []` to the spread defaults:

```javascript
function ensurePerTaskDefaults(entry) {
  return {
    planId: null, status: 'in_progress', model: 'sonnet', review_rounds: 0,
    files_touched_per_round: [], review_history: [], commit_sha: null,
    simplify_reverted: false, simplify_review_findings: [],
    destructive_review_failed: false, destructive_review_findings: [],
    concerns: [], blocked_info: null,
    conflict_history: [],  // consensus matrix
    ...(entry || {}),
  }
}
```

- [ ] **Step 4: Add haltLikelySource coverage for conflict_stalemate**

In `haltLikelySource` (around line 176), add to `implReasons` Set:

```javascript
const implReasons = new Set([
  'model_unavailable', 'agent_error',
  'opus BLOCKED', 'opus BLOCKED after context-fetch',
  'OSCILLATING', 'review max rounds',
  'commit failed', 'commit out_of_scope',
  'simplify diff check failed', 'simplify amend failed', 'simplify checkout failed',
  'review_empty', 'review_failed_no_findings',
  'conflict_stalemate',  // consensus matrix stalemate (rare, arbitrator unclear + no oscillation)
])
```

- [ ] **Step 5: Integrate consensus matrix into runTask review loop**

In `runTask`, in the review loop (between `allGreen` break and `detectOscillation`), insert the consensus matrix stage. The insertion point is after line 1056 (`if (allGreen(spec, qual, hunt)) break`):

```javascript
    // —— 共识矩阵 (新增) ——
    const mappedFindings = mapCategories(collectReviewFindings(spec, qual, hunt))
    const conflicts = detectConflictsLoose(mappedFindings)
    let fixIssuesExtra = ''

    for (const c of conflicts) {
      // 第二阶段：严格判断（haiku inline agent）
      const strictSchema = { type: 'object', required: ['result'], properties: { result: { type: 'string', enum: ['genuine_conflict', 'same_direction', 'unclear'] }, reasoning: { type: 'string' } } }
      const findingsForPrompt = c.findings.map(f => `[${f.source}] ${f.title} — fix: ${f.fix || 'none'}`).join('\n')
      const strictAgent = await safeAgent(
        `Compare two review findings on the same file (${c.file}) and categories (${c.unifiedCategory}).\n${findingsForPrompt}\n\nDo these findings represent genuinely opposed judgments or mutually exclusive fixes?\nReturn: "genuine_conflict" (reviewers want opposite things) | "same_direction" (same problem from different angles) | "unclear" (cannot determine).`,
        { schema: strictSchema, label: `strict:${task.id}` }
      )
      const strictResult = (strictAgent && strictAgent.result) ? strictAgent.result : 'unclear'
      if (strictResult === 'same_direction') continue  // 不是真正分歧，跳过

      // 构建冲突记录
      const conflictRecord = {
        round,
        file: c.file,
        unifiedCategory: c.unifiedCategory,
        sources: c.sources.slice().sort(),
        findings: c.findings.map(f => ({ source: f.source, title: f.title, fix: f.fix || '' })),
        strict_result: strictResult,
        arbitrator_verdict: null,
        arbitrator_rationale: null,
        spec_updated: false,
        plan_updated: false,
      }

      // 检查是否为重复冲突
      const repeat = detectConflictStalemate([c], state.perTask[taskKey].conflict_history)
      if (repeat && round >= 2) {
        // 第 2 轮同一冲突 → opus 仲裁
        const arbInput = JSON.stringify(formatArbitratorInput(c, state.perTask[taskKey].conflict_history, plan, cfg.spec_path))
        const arb = await dispatchImpl(
          buildPrompt('arbitrator', { arbitratorInput: arbInput }),
          { schema: SCHEMAS.arbitrator, label: `arb:${task.id}`, model: 'opus' },
          'opus'
        )
        if (arb && !arb.halted && arb.verdict && arb.verdict !== 'unclear') {
          conflictRecord.arbitrator_verdict = arb.verdict
          conflictRecord.arbitrator_rationale = arb.rationale || ''
          conflictRecord.spec_updated = arb.spec_updated === true
          conflictRecord.plan_updated = arb.plan_updated === true
          log(`⚖ arbitrator resolved [${c.unifiedCategory}] (${c.file}): ${arb.verdict} — ${arb.rationale || 'no rationale provided'}`)
        } else if (arb && arb.halted) {
          // arbitrator 限额/异常 → log 警告，退回 fix-round（detectOscillation 兜底）
          log(`⚠ arbitrator unavailable for [${c.unifiedCategory}] (${c.file}): ${arb.reason || 'unknown'} — falling back to fix-round`)
        }
        // arbitrator 返回 unclear → 不记录 verdict，当普通冲突处理
      }

      fixIssuesExtra += formatConflictSummary(c, conflictRecord)
      state.perTask[taskKey].conflict_history.push(conflictRecord)
    }
```

- [ ] **Step 6: Wire conflict summary into fixIssues**

In the fix-round dispatch (around line 1061-1065), modify to append `fixIssuesExtra`:

Change:
```javascript
const findings = collectReviewFindings(spec, qual, hunt)
// ... detectOscillation ...
impl = await dispatchImpl(buildPrompt('implementor', implCtx(formatFindings(findings), `修复 review round ${round} 问题（${findings.length} 项发现：spec/quality/hunter）。`)), ...)
```

To include `fixIssuesExtra`:
```javascript
const findings = collectReviewFindings(spec, qual, hunt)
// ... consensus matrix (above) builds fixIssuesExtra ...
const fixIssues = formatFindings(findings) + fixIssuesExtra
// ... detectOscillation ...
impl = await dispatchImpl(buildPrompt('implementor', implCtx(fixIssues, `修复 review round ${round} 问题（${findings.length} 项发现：spec/quality/hunter）。`)), ...)
```

Note: This requires moving `const findings = collectReviewFindings(...)` BEFORE the consensus matrix block, and using `fixIssues` instead of `formatFindings(findings)` in the dispatchImpl call.

- [ ] **Step 7: Run sync.test.js to verify inline copies**

Run: `cd docs/superpowers/workflows && node --test 'tests/sync.test.js'`
Expected: Some tests may FAIL — need to update helper function lists and byte comparison lists to include the new functions. Fix inline until all sync tests pass.

The `test('run-plans.js inlines the new conditional-render helpers')` block (line 33) needs new function names added. The `QC-4` byte comparison block (line 52) needs new function names added.

Update the helpers existence check to include:
```javascript
'mapCategories', 'detectConflictsLoose', 'detectConflictStalemate', 'formatConflictSummary', 'formatArbitratorInput'
```

Update the byte comparison list to include:
```javascript
'mapCategories', 'detectConflictsLoose', 'detectConflictStalemate', 'formatConflictSummary', 'formatArbitratorInput'
```

- [ ] **Step 8: Run full workflow test suite**

Run: `cd docs/superpowers/workflows && node --test 'tests/*.test.js'`
Expected: ALL 229+ tests PASS (including new arbitrator assertions, byte comparisons, and helper checks)

- [ ] **Step 9: Commit**

```bash
git add .claude/workflows/run-plans.js docs/superpowers/workflows/tests/sync.test.js
git commit -m "feat(workflow-NN/T5): run-plans.js 共识矩阵集成 — inline 副本 + review loop 插入"
```

---

### Task 6: workflow-design.md §13i 文档

**Files:**
- Modify: `docs/superpowers/workflow-design.md` (add §13i)

- [ ] **Step 1: Add §13i to workflow-design.md**

Insert after §13h in `docs/superpowers/workflow-design.md`:

```markdown
### 13i. Reviewer Consensus Matrix (2026-07-05)

**动机**：当两个 reviewer 对同一文件给出矛盾判断时（如 quality 要简化 vs hunter 要加固），`allGreen` 永不为 true → `detectOscillation` 在第 3 轮 halt → 用户考古推断分歧点。共识矩阵在第 2 轮通过 opus 仲裁消解分歧，追加 spec/plan 澄清消除根因歧义。

**流程**：

```
review round N (spec ‖ quality ‖ hunter 并行)
    ↓
allGreen? → break (现有，不变)
    ↓ no
reviewHaltReason / reviewHaltForEmptyFailed? → halt (现有，不变)
    ↓ pass
┌─ 共识矩阵 ────────────────────────────────────────────┐
│ 1. mapCategories(findings) — 类别映射                  │
│ 2. detectConflictsLoose(findings) — 文件×类别交叉检测   │
│ 3. strictResolution (haiku) — 判定真伪分歧              │
│ 4. Round 1: 注入 fixIssues + 记录 conflict_history     │
│    Round 2 同一冲突: opus 仲裁 → 追加 spec/plan 澄清    │
│                      → commit → fix-round → re-review   │
└──────────────────────────────────────────────────────┘
    ↓
detectOscillation? → halt (兜底)
    ↓
fix-round implementor
```

**新增组件**：5 纯函数（`mapCategories`, `detectConflictsLoose`, `detectConflictStalemate`, `formatConflictSummary`, `formatArbitratorInput`）+ `PROMPTS.arbitrator` + `SCHEMAS.arbitrator`（lib.js 真源，run-plans.js inline，sync.test 字节比较守护）。

**类别映射**：三类 reviewer 保持各自原生类别（prompt 不改），经 `mapCategories` 映射为 7 个统一类别（`correctness`, `error-handling`, `security`, `architecture`, `resource-mgmt`, `observability`, `concurrency`）。仅一个 reviewer 覆盖的类别不映射（`immutability`, `naming`, `code-size`, `transaction-safety`）。

**冲突升级**：同一 `(file, unifiedCategory, sources)` 在 `conflict_history[]` 中已存在 → Round 2 同一冲突 → opus 仲裁。仲裁成功 → 追加 spec/plan 澄清 + fix-round 继续。仲裁 unclear → 退回 fix-round（`detectOscillation` 兜底）。arbitrator 限额/异常 → `log()` 警告 + 退回。

**Arbitrator agent**：opus 模型，单次调用（非 fallback 链），自读写 spec/plan（只追加不删改，不碰 frontmatter）。失败 best-effort 跳过，不阻塞 review 循环。

**数据模型**：`perTask.conflict_history[]` — `{round, file, unifiedCategory, sources[], findings[], strict_result, arbitrator_verdict, arbitrator_rationale, spec_updated, plan_updated}`。随 manifest.json 持久化，与 `review_history` 并列。
```

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/workflow-design.md
git commit -m "docs(workflow): §13i reviewer consensus matrix 设计文档"
```

---

### Task 7: 端到端验证

**Files:**
- (none modified — verification only)

- [ ] **Step 1: Run all workflow tests**

Run: `cd docs/superpowers/workflows && node --test 'tests/*.test.js'`
Expected: ALL PASS

- [ ] **Step 2: Run Python backend tests (regression check)**

Run: `cd "/Volumes/WD_BLACK SN850X 1/Projects/gitea/lottery-notification" && uv run pytest -v`
Expected: ALL 318+ tests PASS

- [ ] **Step 3: Run import-linter (domain purity)**

Run: `uv run lint-imports`
Expected: PASS (no new domain layer violations)

- [ ] **Step 4: Commit final verification**

```bash
git add -A
git diff --cached --stat  # verify only expected files changed
git commit -m "chore(workflow-NN): 共识矩阵端到端验证 — all tests green"
```

---

## Implementation Notes

### File Change Summary

| File | Task | Change Type |
|------|------|-------------|
| `docs/superpowers/workflows/lib.js` | T1-T4 | Add 5 pure functions + 1 PROMPT + 1 SCHEMA |
| `.claude/workflows/run-plans.js` | T5 | Inline copies + review loop integration (~60 lines new) |
| `docs/superpowers/workflows/tests/helpers.test.js` | T1-T3 | Add 40+ unit tests |
| `docs/superpowers/workflows/tests/sync.test.js` | T4-T5 | Add arbitrator assertions + update function lists |
| `docs/superpowers/workflow-design.md` | T6 | Add §13i |

### No-Modify Guarantee

The following are **NOT** modified by this plan:
- Three review agent prompts (specReview/qualityReviewer/hunter) — zero changes
- implementor prompt template — `fixIssues` placeholder unchanged
- `formatFindings` function — conflict summaries are appended, not embedded
- Existing halt logic — only `haltLikelySource` Set gains one new entry
- `ensurePerTaskDefaults` — one new field with default `[]`
- Python source code or tests — zero changes
