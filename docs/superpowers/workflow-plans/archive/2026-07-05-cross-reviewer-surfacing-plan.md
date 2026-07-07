# Cross-Reviewer Pattern Surfacing 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add cross-reviewer file-level overlap detection to fixIssues and blocked.md — when ≥2 reviewers flag the same file, surface the pattern without any new agents or mapping tables.

**Architecture:** 2 pure functions in lib.js (`groupFindingsByFile`, `formatCrossReviewerNote`) → inline copies in run-plans.js → inject into fixIssues in review loop → enhance finalReport prompt → sync.test.js guard. Zero new agents, zero prompt template changes for existing reviewers.

**Tech Stack:** JavaScript (Workflow runtime sandbox), node:test, Claude Code Workflow agent dispatch

**Spec:** `docs/superpowers/workflow-plans/2026-07-05-reviewer-consensus-matrix.md` (v2.0 simplified)

**Runtime constraints (commit 67a4164):**
- `args` may be JSON-stringified by Workflow runtime → entry defense already exists (line 1213)
- Orchestrator is JS sandbox: no fs, no Date.now/Math.random, no subprocess
- `agent()` may return null (thinking-only empty response) → no new agent calls added
- Pure functions → lib.js (`node --test` testable); runtime glue → run-plans.js
- Inline copies guarded by sync.test.js byte comparison

---

### Task 1: lib.js — groupFindingsByFile + formatCrossReviewerNote 纯函数 (TDD)

**Files:**
- Modify: `docs/superpowers/workflows/lib.js` (append before `export const SCHEMAS`)
- Modify: `docs/superpowers/workflows/tests/helpers.test.js` (append test blocks)

- [ ] **Step 1: Write failing tests**

Add to `docs/superpowers/workflows/tests/helpers.test.js`:

```javascript
// —— groupFindingsByFile ——
import { groupFindingsByFile } from '../lib.js'

test('groupFindingsByFile groups findings by file', () => {
  const findings = [
    { source: 'spec', title: 'missing X', file: 'a.py' },
    { source: 'quality', title: 'wrong way', file: 'a.py' },
    { source: 'hunter', title: 'swallowed error', file: 'b.py' },
  ]
  const groups = groupFindingsByFile(findings)
  assert.equal(groups.length, 2)
  const a = groups.find(g => g.file === 'a.py')
  const b = groups.find(g => g.file === 'b.py')
  assert.equal(a.findings.length, 2)
  assert.equal(b.findings.length, 1)
  assert.deepEqual([...a.sources].sort(), ['quality', 'spec'])
})

test('groupFindingsByFile skips findings without file', () => {
  const findings = [
    { source: 'spec', title: 'no file finding' },
    { source: 'quality', title: 'has file', file: 'a.py' },
  ]
  const groups = groupFindingsByFile(findings)
  assert.equal(groups.length, 1)
  assert.equal(groups[0].file, 'a.py')
})

test('groupFindingsByFile empty array → empty array', () => {
  assert.deepEqual(groupFindingsByFile([]), [])
})

// —— formatCrossReviewerNote ——
import { formatCrossReviewerNote } from '../lib.js'

test('formatCrossReviewerNote produces output when ≥2 sources flag same file', () => {
  const findings = [
    { source: 'spec', severity: 'critical', title: 'missing feature X', file: 'a.py', fix: 'add it' },
    { source: 'quality', severity: 'important', title: 'wrong approach', file: 'a.py', fix: 'use pattern Y' },
  ]
  const out = formatCrossReviewerNote(findings)
  assert.match(out, /Cross-Reviewer Overlap/)
  assert.match(out, /a\.py.*flagged by:.*quality.*spec/)
  assert.match(out, /spec\|critical/)
  assert.match(out, /quality\|important/)
})

test('formatCrossReviewerNote empty when only one source per file', () => {
  const findings = [
    { source: 'quality', title: 'issue 1', file: 'a.py' },
    { source: 'quality', title: 'issue 2', file: 'a.py' },
    { source: 'hunter', title: 'issue 3', file: 'b.py' },
  ]
  assert.equal(formatCrossReviewerNote(findings), '')
})

test('formatCrossReviewerNote empty for empty findings', () => {
  assert.equal(formatCrossReviewerNote([]), '')
})

test('formatCrossReviewerNote handles findings without severity gracefully', () => {
  const findings = [
    { source: 'spec', title: 'missing X', file: 'a.py' },
    { source: 'hunter', title: 'bad fallback', file: 'a.py' },
  ]
  const out = formatCrossReviewerNote(findings)
  assert.match(out, /spec\] missing X/)
  assert.match(out, /hunter\] bad fallback/)
})

test('formatCrossReviewerNote handles findings without fix gracefully', () => {
  const findings = [
    { source: 'spec', title: 'missing X', file: 'a.py' },
    { source: 'quality', title: 'wrong way', file: 'a.py', fix: 'use Y' },
  ]
  const out = formatCrossReviewerNote(findings)
  assert.match(out, /spec\] missing X\n/)           // no "— fix:" suffix
  assert.match(out, /quality\].*— fix: use Y/)      // has fix
})

test('formatCrossReviewerNote multiple overlap groups', () => {
  const findings = [
    { source: 'spec', title: 'missing X', file: 'a.py' },
    { source: 'quality', title: 'wrong way', file: 'a.py' },
    { source: 'quality', title: 'bare except', file: 'b.py' },
    { source: 'hunter', title: 'swallowed error', file: 'b.py' },
  ]
  const out = formatCrossReviewerNote(findings)
  assert.match(out, /a\.py.*flagged by:/)
  assert.match(out, /b\.py.*flagged by:/)
  // a.py should have spec + quality, b.py should have quality + hunter
  const aIdx = out.indexOf('a.py')
  const bIdx = out.indexOf('b.py')
  assert.ok(aIdx > -1 && bIdx > -1)
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd docs/superpowers/workflows && node --test 'tests/helpers.test.js'`
Expected: FAIL — `groupFindingsByFile` and `formatCrossReviewerNote` not exported

- [ ] **Step 3: Implement groupFindingsByFile in lib.js**

Add before `export const SCHEMAS` in `docs/superpowers/workflows/lib.js`:

```javascript
// 跨 reviewer 文件重叠检测：按 file 分组 findings → 返回分组数组。
// 纯函数，不依赖任何映射表或 agent 调用。spec §3.1。
export function groupFindingsByFile(findings) {
  const groups = {}
  for (const f of findings) {
    if (!f.file) continue
    if (!groups[f.file]) groups[f.file] = { file: f.file, sources: new Set(), findings: [] }
    groups[f.file].sources.add(f.source)
    groups[f.file].findings.push(f)
  }
  return Object.values(groups)
}
```

- [ ] **Step 4: Implement formatCrossReviewerNote in lib.js**

Add after `groupFindingsByFile`:

```javascript
// 格式化跨 reviewer 文件重叠为 implementor 可读的注入文本。
// 仅当某文件有 ≥2 个不同 reviewer 标记时才输出该段。spec §3.1。
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

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd docs/superpowers/workflows && node --test 'tests/helpers.test.js'`
Expected: ALL PASS (including ~10 new tests)

- [ ] **Step 6: Commit**

```bash
git add docs/superpowers/workflows/lib.js docs/superpowers/workflows/tests/helpers.test.js
git commit -m "feat(workflow-plan/T1): groupFindingsByFile + formatCrossReviewerNote 纯函数"
```

---

### Task 2: run-plans.js — inline 副本 + fixIssues 注入 + finalReport prompt 增强 + sync.test 更新

**Files:**
- Modify: `.claude/workflows/run-plans.js` (inline copies + review loop injection)
- Modify: `docs/superpowers/workflows/lib.js` (PROMPTS.finalReport Step 4 update)
- Modify: `docs/superpowers/workflows/tests/sync.test.js` (new guard assertions)

- [ ] **Step 1: Inline new pure functions in run-plans.js**

After the existing inline helpers, add byte-identical copies of `groupFindingsByFile` and `formatCrossReviewerNote` from lib.js (Task 1). Each must have the `// —— inline 自 lib.js` comment suffix.

- [ ] **Step 2: Inject formatCrossReviewerNote into fixIssues in review loop**

In `runTask`, locate the review loop where `collectReviewFindings` is called (around line 1061). Change:

```javascript
const findings = collectReviewFindings(spec, qual, hunt)
```

To also compute the cross-reviewer note:

```javascript
const findings = collectReviewFindings(spec, qual, hunt)
const fixIssues = formatFindings(findings) + formatCrossReviewerNote(findings)
```

Then in the `dispatchImpl` call (line 1065), change `formatFindings(findings)` to `fixIssues`:

```javascript
// Before:
impl = await dispatchImpl(buildPrompt('implementor', implCtx(formatFindings(findings), `修复 review round ${round}...`)), ...)

// After:
impl = await dispatchImpl(buildPrompt('implementor', implCtx(fixIssues, `修复 review round ${round}...`)), ...)
```

- [ ] **Step 3: Update finalReport prompt (lib.js + run-plans.js)**

In `PROMPTS.finalReport`, Step 4 (`If mode=halted: write .workflow/blocked.md`), add after the Working Tree section:

```
Also include: if the halt was due to a failed review round (not model_unavailable/agent_error/gate/commit), add a "## Cross-Reviewer Findings (grouped by file)" section to blocked.md: group all findings from the halted task's blocked_info by file, and highlight files where ≥2 reviewers reported findings with ⚠ CROSS-REVIEWER markers. This helps spot reviewer disagreements at a glance. Use the blockedInfo.raw field to extract reviewer findings — the raw field contains the diagnostics from spec/quality/hunter reviews.
```

Update both lib.js and run-plans.js copies. Then re-run sync.test to confirm PROMPTS byte-identical.

- [ ] **Step 4: Update sync.test.js**

In the helpers existence check (line 33), add `'groupFindingsByFile', 'formatCrossReviewerNote'` to the function list.

In the byte comparison list (line 56), add `'groupFindingsByFile', 'formatCrossReviewerNote'` to the `fns` array.

Add a new test:

```javascript
test('finalReport prompt 须含 cross-reviewer 分组段落', () => {
  for (const src of [libSrc, runSrc]) {
    const p = promptBody(src, 'finalReport')
    assert.match(p, /Cross-Reviewer/, 'finalReport 须含 cross-reviewer 分组指引')
    assert.match(p, /grouped by file/, 'cross-reviewer 须按文件分组')
  }
})
```

- [ ] **Step 5: Run full workflow test suite**

Run: `cd docs/superpowers/workflows && node --test 'tests/*.test.js'`
Expected: ALL tests PASS

- [ ] **Step 6: Verify no regression — Python backend tests**

Run: `cd "/Volumes/WD_BLACK SN850X 1/Projects/gitea/lottery-notification" && uv run pytest -v`
Expected: ALL 318+ tests PASS

- [ ] **Step 7: Commit**

```bash
git add .claude/workflows/run-plans.js docs/superpowers/workflows/lib.js docs/superpowers/workflows/tests/sync.test.js
git commit -m "feat(workflow-plan/T2): cross-reviewer surfacing — fixIssues 注入 + finalReport prompt 增强"
```

---

## Implementation Notes

### File Change Summary

| File | Task | Change Type |
|------|------|-------------|
| `docs/superpowers/workflows/lib.js` | T1, T2 | Add 2 pure functions + update PROMPTS.finalReport |
| `.claude/workflows/run-plans.js` | T2 | Inline copies + fixIssues injection (~10 lines net new) |
| `docs/superpowers/workflows/tests/helpers.test.js` | T1 | Add ~10 unit tests |
| `docs/superpowers/workflows/tests/sync.test.js` | T2 | Add function checks + byte comparison + new assertion |

### No-Modify Guarantee

- Three review agent prompts — zero changes
- implementor prompt template — zero changes
- `formatFindings` function — zero changes (note is appended after its output)
- Halt logic — zero changes
- `ensurePerTaskDefaults` — zero changes
- SCHEMAS — zero changes (no new agent)
- Python source code or tests — zero changes

### What's Different from v1.0

| v1.0 (废弃) | v2.0 (本 plan) |
|------------|---------------|
| 5 new pure functions | 2 new pure functions |
| 1 new agent type (arbitrator) | 0 new agent types |
| Mapping table (maintenance burden) | No mapping — file-level only |
| 2 new agent calls per conflict | 0 new agent calls |
| `conflict_history[]` data model | No new data model |
| 40+ tests | ~10 tests |
| 7 tasks | 2 tasks |
