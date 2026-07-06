# Review v3 — Oscillation / Findings State Machine / Lessons 两层注入 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 OSCILLATING halt 从「纯计数（同文件 ≥3 轮）」改为「flipFlop/regressed 驱动 + budget guard 8」，加 findings 状态机（`[OPEN]`/`[FIXED]`/`[REGRESSED]`）防回归循环，lessons 改两层注入（silent-failure 始终 + 领域按 category），让 flipFlop=false 的 task（历史 3 次 halt 全属此类）能升 opus 后跑到收敛。

**Architecture:** 5 个改进联动：(A+B) lessons category 匹配 + 两层注入 → (E') findings 状态机纯函数 + 接线 → (OSC) shouldEscalateOnOscillation 改 flipFlop 驱动 + budget 8 + hasRegressed 联动 → (F) opus 升级 prompt 强化。纯函数进 lib.js（node:test 单测），runtime 胶水留 run-plans.js，sync.test 字节守护 inline 副本。

**Tech Stack:** JavaScript (Workflow runtime sandbox), node:test, Claude Code Workflow agent dispatch

**Spec / 设计依据:**
- `docs/superpowers/workflow-design.md` §5.5（新增 v3 改进）+ §13g（更新 halt 判定）
- `docs/superpowers/workflows/USAGE.md` §2 config `review_budget` + §12 调试 + §13 相关文件

**Runtime constraints:**
- Orchestrator 是 JS sandbox：无 fs、无 `Date.now()`/`Math.random()`、无 subprocess
- `agent()` 可能返回 null（thinking-only 空响应）—— 本 plan 不新增 agent 调用
- 纯函数 → lib.js（`node --test` 可测）；runtime 胶水 → run-plans.js
- lib.js 改了的 helper 必须同步 inline 副本到 run-plans.js（sync.test 字节比较守护）
- TDD 强制：RED（写失败测试）→ GREEN（最小实现）→ REFACTOR，每步 checkpoint commit

**注入边界（明确，设计已确认）：**
| 注入项 | 范围 | 来源 |
|--------|------|------|
| findings_history（`[OPEN]`/`[FIXED]`） | 仅当前 task | `state.perTask[taskKey].findings_history` |
| lessons（Tier 1 + Tier 2） | 跨 task / 跨 plan | `state.taskLessons[taskKey]`（bootstrap 全局匹配） |

**关键设计点：**
- findings 状态机：`open`（本轮仍存在）→ 不出现即 `fixed`（标注 `fixed_at_round`）→ 再次出现即 `regressed`（触发 halt，**不注入 fix prompt**）
- `[FIXED]` 全注入（标注 `file` 路径，无 commit SHA 因 task 内 fix 不 commit），implementor `git diff` 工作树定位已修复区域避免碰它
- `[REGRESSED]` 触发即 halt（与 `isFlipFlop=true` 同义但 diag 更精确：哪轮修好、哪轮复现）
- OSCILLATING halt：`flipFlop=true` OR `hasRegressed(history)` → halt；`flipFlop=false` 升 opus 后继续跑直到 `review_budget`（默认 8）
- 无 cap（用户确认接受 token 爆炸）

---

## File Structure

| 文件 | 责任 | 改动 |
|------|------|------|
| `docs/superpowers/workflows/lib.js` | 纯函数真源 | 新增 `formatUniversalLessons`/`formatDomainLessons`/`updateFindingsHistory`/`formatFindingsHistory`/`hasRegressed`；改 `shouldEscalateOnOscillation` 签名；新增 `resolveReviewBudget` |
| `.claude/workflows/run-plans.js` | runtime 胶水 | 同步 inline 副本；`implCtx` 改用新 lessons/findings helpers；bootstrap prompt 加 category 匹配；review loop 接 `updateFindingsHistory`；OSC 分支重写 |
| `docs/superpowers/workflows/tests/helpers.test.js` | 纯函数单测 | 新增 5 个 helper 的测试块 |
| `docs/superpowers/workflows/tests/sync.test.js` | inline 副本守护 | 新增 helper 加入 helpers 列表 + 字节比较列表 |

---

## Task 1: lib.js — lessons 两层注入纯函数（A+B，TDD）

**Files:**
- Modify: `docs/superpowers/workflows/lib.js`（在 `formatLessons` 之后追加 2 个新函数）
- Modify: `docs/superpowers/workflows/tests/helpers.test.js`（追加测试块）

**设计：** 现有 `formatLessons(items)` 单层注入改为两层。保留 `formatLessons` 不动（向后兼容 + sync.test 已守护），新增 `formatUniversalLessons(allLessons)`（Tier 1：`category==='silent-failure'` 始终注入）+ `formatDomainLessons(allLessons, taskCategories)`（Tier 2：其余 category 按 task 声明匹配，cap 5，同 plan 优先）。`allLessons` 是 bootstrap 解析 lessons.md 后的数组，每项 `{id, title, detail, category?, source?}`。

- [ ] **Step 1: Write failing tests**

追加到 `docs/superpowers/workflows/tests/helpers.test.js`（与现有 import 同一文件，复用已 import 的 `test`/`assert`）。先在文件顶部 import 段追加 2 个新函数：

```javascript
// 修改现有 import 行，追加 formatUniversalLessons, formatDomainLessons
import { ..., formatLessons, formatUniversalLessons, formatDomainLessons } from '../lib.js'
```

在文件末尾追加测试块：

```javascript
// —— formatUniversalLessons (Tier 1: silent-failure 始终注入) ——

test('formatUniversalLessons returns empty string when no silent-failure lessons', () => {
  const all = [
    { id: 'L1', title: 'savepoint', detail: 'use savepoint', category: 'silent-failure' },
    { id: 'L2', title: 'csv format', detail: 'use comma', category: 'test-strategy' },
  ]
  // 有 silent-failure → 非空
  assert.ok(formatUniversalLessons(all).length > 0)
})

test('formatUniversalLessons returns empty string when no silent-failure category', () => {
  const all = [
    { id: 'L2', title: 'csv format', detail: 'use comma', category: 'test-strategy' },
    { id: 'L3', title: 'no category', detail: 'legacy' },  // 无 category 字段
  ]
  assert.equal(formatUniversalLessons(all), '')
})

test('formatUniversalLessons includes only silent-failure category lessons', () => {
  const all = [
    { id: 'L1', title: 'savepoint', detail: 'use savepoint', category: 'silent-failure' },
    { id: 'L2', title: 'timezone', detail: 'naive UTC', category: 'silent-failure' },
    { id: 'L3', title: 'csv format', detail: 'use comma', category: 'test-strategy' },
  ]
  const out = formatUniversalLessons(all)
  assert.ok(out.includes('savepoint'))
  assert.ok(out.includes('timezone'))
  assert.ok(!out.includes('csv format'))
})

// —— formatDomainLessons (Tier 2: 按 task category 匹配，cap 5，同 plan 优先) ——

test('formatDomainLessons returns empty string when taskCategories empty', () => {
  const all = [{ id: 'L1', title: 'csv', detail: 'use comma', category: 'test-strategy' }]
  assert.equal(formatDomainLessons(all, []), '')
})

test('formatDomainLessons matches lessons by taskCategories', () => {
  const all = [
    { id: 'L1', title: 'csv format', detail: 'use comma', category: 'test-strategy' },
    { id: 'L2', title: 'no category', detail: 'legacy' },
    { id: 'L3', title: 'timezone', detail: 'naive UTC', category: 'silent-failure' },
  ]
  // task 声明 test-strategy → 只匹配 L1（L3 是 silent-failure 由 Tier 1 注入，不重复）
  const out = formatDomainLessons(all, ['test-strategy'])
  assert.ok(out.includes('csv format'))
  assert.ok(!out.includes('timezone'))  // silent-failure 不进 Tier 2
  assert.ok(!out.includes('no category'))  // 无 category 不匹配
})

test('formatDomainLessons excludes silent-failure (Tier 1 已注入，防重复)', () => {
  const all = [
    { id: 'L1', title: 'savepoint', detail: 'use savepoint', category: 'silent-failure' },
    { id: 'L2', title: 'csv', detail: 'use comma', category: 'test-strategy' },
  ]
  // 即使 task 声明 silent-failure，Tier 2 也不重复注入（Tier 1 已兜底始终注入）
  const out = formatDomainLessons(all, ['silent-failure', 'test-strategy'])
  assert.ok(!out.includes('savepoint'))
  assert.ok(out.includes('csv'))
})

test('formatDomainLessons caps at 5 lessons, same-plan source first', () => {
  const all = []
  for (let i = 1; i <= 8; i++) {
    all.push({ id: `L${i}`, title: `lesson ${i}`, detail: `d${i}`, category: 'test-strategy', source: i <= 3 ? 'plan-06/T1@x' : 'plan-05/T1@x' })
  }
  const out = formatDomainLessons(all, ['test-strategy'], 'plan-06')
  // cap 5 + 同 plan（plan-06）优先 → L1,L2,L3（同 plan）+ L4,L5（其他）
  const ids = (out.match(/L\d+/g) || [])
  assert.equal(ids.length, 5, `expected 5 lessons after cap, got ${ids.length}`)
  assert.ok(ids.includes('L1') && ids.includes('L2') && ids.includes('L3'), 'same-plan lessons first')
})

test('formatDomainLessons falls back to title keyword match when taskCategories absent', () => {
  // taskCategories null/undefined → 旧行为：按 title 关键词（用 task title 当唯一关键词）
  const all = [
    { id: 'L1', title: 'CSV import format', detail: 'use comma', category: 'test-strategy' },
    { id: 'L2', title: 'timezone', detail: 'naive UTC', category: 'silent-failure' },
  ]
  // 无 category 维度，用 taskTitle='CSV 批量导入' 关键词匹配 → L1（CSV）
  const out = formatDomainLessons(all, null, 'plan-06', 'CSV 批量导入')
  assert.ok(out.includes('CSV import format'))
  assert.ok(!out.includes('timezone'))  // silent-failure 不进 Tier 2
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd docs/superpowers/workflows && node --test tests/helpers.test.js`
Expected: FAIL — `formatUniversalLessons`/`formatDomainLessons` 未定义（import 报错或 `undefined is not a function`）。

- [ ] **Step 3: Write minimal implementation**

在 `docs/superpowers/workflows/lib.js` 的 `formatLessons` 函数之后追加：

```javascript
// —— v3 lessons 两层注入（2026-07-06，§5.5 A+B）——
// Tier 1 formatUniversalLessons: silent-failure category 始终注入（项目最高优先级纪律，
//   不靠关键词撞运气）。allLessons 是 bootstrap 解析 lessons.md 的全量数组。
// Tier 2 formatDomainLessons: 其余 category 按 task 声明匹配，cap 5，同 plan 优先；
//   taskCategories 为空时 fallback 到 title 关键词匹配（向后兼容）。
// 两层都排除对方已覆盖的 lesson 防重复：Tier 1 只取 silent-failure；Tier 2 排除 silent-failure。

export function formatUniversalLessons(allLessons) {
  if (!Array.isArray(allLessons) || allLessons.length === 0) return ''
  const universal = allLessons.filter(l => l && l.category === 'silent-failure')
  if (universal.length === 0) return ''
  const lines = universal.map(l => `- [${l.id}] ${l.title} — ${l.detail}`).join('\n')
  return `## Universal Discipline (silent-failure — always apply)
${lines}
These are project-wide silent-failure disciplines. Before reporting done, verify your code does not violate any of them (savepoint isolation, naive-UTC datetime, single-transaction commits, etc.).`
}

// taskCategories: task 声明的 lesson_categories（数组），null/空 → fallback title 关键词
// currentPlanSeq: 当前 plan seq（如 'plan-06'），用于同 plan 优先排序
// taskTitle: fallback 关键词匹配用（task 标题）
export function formatDomainLessons(allLessons, taskCategories, currentPlanSeq, taskTitle) {
  if (!Array.isArray(allLessons) || allLessons.length === 0) return ''
  // 排除 silent-failure（Tier 1 已注入）
  const candidates = allLessons.filter(l => l && l.category !== 'silent-failure')
  let matched = []
  if (Array.isArray(taskCategories) && taskCategories.length > 0) {
    // category 匹配
    matched = candidates.filter(l => taskCategories.includes(l.category))
  } else if (taskTitle) {
    // fallback: title 关键词重叠（旧行为）
    const tokens = String(taskTitle).toLowerCase().split(/[\s,，、]+/).filter(t => t.length > 1)
    matched = candidates.filter(l => {
      const text = `${l.title || ''} ${l.detail || ''}`.toLowerCase()
      return tokens.some(t => text.includes(t))
    })
  }
  if (matched.length === 0) return ''
  // 同 plan 优先（source 含 currentPlanSeq 排前）
  if (currentPlanSeq) {
    matched.sort((a, b) => {
      const aSame = a.source && String(a.source).includes(currentPlanSeq) ? 0 : 1
      const bSame = b.source && String(b.source).includes(currentPlanSeq) ? 0 : 1
      return aSame - bSame
    })
  }
  const capped = matched.slice(0, 5)
  const lines = capped.map(l => `- [${l.id}] ${l.title} — ${l.detail}`).join('\n')
  return `## Domain Lessons (check against these before implementing)
${lines}
If your plan is similar to any lesson above, explicitly state why your approach differs.`
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd docs/superpowers/workflows && node --test tests/helpers.test.js`
Expected: PASS — 全部新测试通过。

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/workflows/lib.js docs/superpowers/workflows/tests/helpers.test.js
git commit -m "feat(workflow-v3/T1): lessons 两层注入纯函数 — formatUniversalLessons + formatDomainLessons

A+B: silent-failure 始终注入 + 领域 category 匹配（cap 5，同 plan 优先）。
lib.js 新增 2 个 export 函数 + helpers.test.js 7 个测试块。TDD RED→GREEN。"
```

---

## Task 2: lib.js — findings 状态机纯函数（E'，TDD）

**Files:**
- Modify: `docs/superpowers/workflows/lib.js`（追加 3 个新函数）
- Modify: `docs/superpowers/workflows/tests/helpers.test.js`（追加测试块）

**设计：** findings_history 状态机。每条 finding 形态：`{title, severity, fix, file, first_seen, last_seen, rounds: [], status: 'open|fixed|regressed', fixed_at_round?}`。三个纯函数：
- `updateFindingsHistory(history, currentFindings, round)` → 新 history（不可变，返回新数组）
- `formatFindingsHistory(history)` → 分层字符串（`[OPEN]` 全注入 + `[FIXED]` 全注入，`[REGRESSED]` 不注入）
- `hasRegressed(history)` → boolean（任一 finding status==='regressed'）

currentFindings 形态复用 `collectReviewFindings` 输出：`{source, severity, title, file, fix}`。

- [ ] **Step 1: Write failing tests**

在 `docs/superpowers/workflows/tests/helpers.test.js` import 段追加：

```javascript
import { ..., updateFindingsHistory, formatFindingsHistory, hasRegressed } from '../lib.js'
```

文件末尾追加测试块：

```javascript
// —— updateFindingsHistory (状态机转换) ——

test('updateFindingsHistory returns empty array for first round', () => {
  const current = [{ source: 'spec', title: 'bug A', severity: 'important', file: 'a.py', fix: 'fix A' }]
  const h = updateFindingsHistory([], current, 1)
  assert.equal(h.length, 1)
  assert.equal(h[0].status, 'open')
  assert.equal(h[0].first_seen, 1)
  assert.equal(h[0].last_seen, 1)
  assert.deepEqual(h[0].rounds, [1])
})

test('updateFindingsHistory marks absent open finding as fixed', () => {
  const history = [{ title: 'bug A', severity: 'important', fix: 'fix A', file: 'a.py', first_seen: 1, last_seen: 1, rounds: [1], status: 'open' }]
  // round 2: bug A 不再出现 → fixed
  const h = updateFindingsHistory(history, [], 2)
  assert.equal(h[0].status, 'fixed')
  assert.equal(h[0].fixed_at_round, 2)
  assert.deepEqual(h[0].rounds, [1])  // rounds 不追加（本轮未出现）
})

test('updateFindingsHistory keeps open finding open with updated last_seen', () => {
  const history = [{ title: 'bug A', severity: 'important', fix: 'fix A', file: 'a.py', first_seen: 1, last_seen: 1, rounds: [1], status: 'open' }]
  const current = [{ source: 'spec', title: 'bug A', severity: 'important', file: 'a.py', fix: 'fix A' }]
  const h = updateFindingsHistory(history, current, 2)
  assert.equal(h[0].status, 'open')
  assert.equal(h[0].last_seen, 2)
  assert.deepEqual(h[0].rounds, [1, 2])
})

test('updateFindingsHistory marks regressed when fixed finding reappears', () => {
  const history = [{ title: 'bug A', severity: 'important', fix: 'fix A', file: 'a.py', first_seen: 1, last_seen: 1, rounds: [1], status: 'fixed', fixed_at_round: 2 }]
  const current = [{ source: 'spec', title: 'bug A', severity: 'important', file: 'a.py', fix: 'fix A' }]
  const h = updateFindingsHistory(history, current, 3)
  assert.equal(h[0].status, 'regressed')
  assert.equal(h[0].last_seen, 3)
  assert.deepEqual(h[0].rounds, [1, 3])
  assert.equal(h[0].fixed_at_round, 2)  // 保留修好的轮次（diag 用）
})

test('updateFindingsHistory is immutable (does not mutate input)', () => {
  const history = [{ title: 'bug A', severity: 'important', fix: '', file: 'a.py', first_seen: 1, last_seen: 1, rounds: [1], status: 'open' }]
  const current = [{ source: 'spec', title: 'bug A', severity: 'important', file: 'a.py', fix: 'fix' }]
  const h = updateFindingsHistory(history, current, 2)
  assert.notEqual(h, history)  // 新数组
  assert.equal(history[0].status, 'open')  // 原数组未变
  assert.deepEqual(history[0].rounds, [1])
})

test('updateFindingsHistory preserves regressed status (idempotent on re-regression)', () => {
  const history = [{ title: 'bug A', severity: 'important', fix: '', file: 'a', first_seen: 1, last_seen: 3, rounds: [1, 3], status: 'regressed', fixed_at_round: 2 }]
  const current = [{ source: 'spec', title: 'bug A', severity: 'important', file: 'a', fix: '' }]
  const h = updateFindingsHistory(history, current, 4)
  assert.equal(h[0].status, 'regressed')
  assert.equal(h[0].last_seen, 4)
  assert.deepEqual(h[0].rounds, [1, 3, 4])
})

test('updateFindingsHistory matches by title (cross-reviewer dedup)', () => {
  // round 1 quality 报 "stub URL"，round 2 spec 报同 title → 视为同 finding（不新增）
  const history = [{ title: 'stub URL', severity: 'important', fix: '', file: 'a', first_seen: 1, last_seen: 1, rounds: [1], status: 'open' }]
  const current = [{ source: 'spec', title: 'stub URL', severity: 'minor', file: 'a', fix: 'use startsWith' }]
  const h = updateFindingsHistory(history, current, 2)
  assert.equal(h.length, 1)
  assert.equal(h[0].status, 'open')
  assert.equal(h[0].last_seen, 2)
})

// —— hasRegressed ——

test('hasRegressed returns true when any finding is regressed', () => {
  const history = [{ title: 'A', status: 'open' }, { title: 'B', status: 'regressed' }]
  assert.equal(hasRegressed(history), true)
})

test('hasRegressed returns false when no regressed', () => {
  const history = [{ title: 'A', status: 'open' }, { title: 'B', status: 'fixed' }]
  assert.equal(hasRegressed(history), false)
})

test('hasRegressed returns false for empty history', () => {
  assert.equal(hasRegressed([]), false)
})

// —— formatFindingsHistory ——

test('formatFindingsHistory lists [OPEN] findings as must-fix', () => {
  const history = [
    { title: 'bug A', severity: 'important', fix: 'fix A', file: 'a.py', first_seen: 1, last_seen: 2, rounds: [1, 2], status: 'open' },
  ]
  const out = formatFindingsHistory(history)
  assert.ok(out.includes('[OPEN]'))
  assert.ok(out.includes('bug A'))
  assert.ok(out.includes('fix A'))
})

test('formatFindingsHistory lists [FIXED] findings as do-not-touch', () => {
  const history = [
    { title: 'bug A', severity: 'minor', fix: 'fix A', file: 'a.py', first_seen: 1, last_seen: 1, rounds: [1], status: 'fixed', fixed_at_round: 2 },
  ]
  const out = formatFindingsHistory(history)
  assert.ok(out.includes('[FIXED]'))
  assert.ok(out.includes('bug A'))
  assert.ok(out.includes('a.py'))  // 文件路径标注
  assert.ok(out.includes('r2'))  // fixed_at_round
})

test('formatFindingsHistory omits [REGRESSED] (triggers halt, not injected)', () => {
  const history = [
    { title: 'bug A', severity: 'important', fix: '', file: 'a', first_seen: 1, last_seen: 3, rounds: [1, 3], status: 'regressed', fixed_at_round: 2 },
  ]
  const out = formatFindingsHistory(history)
  // regressed 不注入（触发即 halt，implementor 永远看不到）
  assert.ok(!out.includes('bug A'))
  assert.ok(!out.includes('[REGRESSED]'))
})

test('formatFindingsHistory returns empty string when no open or fixed', () => {
  const history = [{ title: 'A', status: 'regressed' }]
  assert.equal(formatFindingsHistory(history), '')
})

test('formatFindingsHistory empty history returns empty string', () => {
  assert.equal(formatFindingsHistory([]), '')
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd docs/superpowers/workflows && node --test tests/helpers.test.js`
Expected: FAIL — 3 个新函数未定义。

- [ ] **Step 3: Write minimal implementation**

在 `docs/superpowers/workflows/lib.js` 的 `formatDomainLessons`（Task 1 追加）之后追加：

```javascript
// —— v3 findings 状态机（2026-07-06，§5.5 E'）——
// findings_history 累积全历史，每条带状态 open|fixed|regressed。
// regressed = 曾 fixed 的 finding 再次出现 = 回归循环信号 → 触发 halt（不注入 fix prompt）。
// [OPEN] 全注入（必须修）+ [FIXED] 全注入（标注 file，防回归）+ [REGRESSED] 不注入。
// currentFindings 形态同 collectReviewFindings 输出：{source, severity, title, file, fix}。
// 不可变：返回新数组，不改输入。

export function updateFindingsHistory(history, currentFindings, round) {
  if (!Array.isArray(history)) history = []
  const current = Array.isArray(currentFindings) ? currentFindings : []
  const currentTitles = new Set(current.map(f => f?.title).filter(Boolean))
  const result = history.map(h => {
    const stillPresent = currentTitles.has(h.title)
    if (stillPresent) {
      // 仍存在：open→open / fixed→regressed / regressed→regressed
      const status = h.status === 'open' ? 'open' : 'regressed'
      return {
        ...h,
        last_seen: round,
        rounds: [...h.rounds, round],
        status,
        // fixed→regressed 时保留 fixed_at_round（diag 用）；open/regressed 不变
        fixed_at_round: h.fixed_at_round,
      }
    }
    // 不存在：open→fixed；fixed/regressed 保持（已修好/已回归的不因缺席改变）
    if (h.status === 'open') {
      return { ...h, status: 'fixed', fixed_at_round: round }
    }
    return h
  })
  // 新 finding（title 在 history 无）：首次出现 → open
  const existingTitles = new Set(history.map(h => h.title))
  for (const f of current) {
    if (f?.title && !existingTitles.has(f.title)) {
      result.push({
        title: f.title,
        severity: f.severity,
        fix: f.fix,
        file: f.file,
        first_seen: round,
        last_seen: round,
        rounds: [round],
        status: 'open',
      })
    }
  }
  return result
}

export function hasRegressed(history) {
  if (!Array.isArray(history)) return false
  return history.some(h => h?.status === 'regressed')
}

export function formatFindingsHistory(history) {
  if (!Array.isArray(history) || history.length === 0) return ''
  const open = history.filter(h => h.status === 'open')
  const fixed = history.filter(h => h.status === 'fixed')
  const sections = []
  if (open.length > 0) {
    const lines = open.map(h => {
      const sev = h.severity ? `[${h.severity}]` : ''
      const seen = `(seen: r${h.rounds.join(',')})`
      const file = h.file ? `, file: ${h.file}` : ''
      const fix = h.fix ? ` — fix: ${h.fix}` : ''
      return `- ${sev} ${h.title} ${seen}${file}${fix}`
    }).join('\n')
    sections.push(`### [OPEN] 本轮仍存在 — 必须修完\n${lines}`)
  }
  if (fixed.length > 0) {
    const lines = fixed.map(h => {
      const sev = h.severity ? `[${h.severity}]` : ''
      const file = h.file ? `, file: ${h.file}` : ''
      const fix = h.fix ? ` — fix: ${h.fix}` : ''
      return `- ${sev} ${h.title} (fixed r${h.fixed_at_round}${file})${fix}`
    }).join('\n')
    sections.push(`### [FIXED] 已修好的 — 修新问题时勿碰这些文件区域，勿重新引入已修问题\n${lines}`)
  }
  // [REGRESSED] 不注入（触发即 halt，implementor 看不到）
  if (sections.length === 0) return ''
  return `## Findings History (全轮累积)\n${sections.join('\n\n')}`
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd docs/superpowers/workflows && node --test tests/helpers.test.js`
Expected: PASS — 全部新测试通过。

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/workflows/lib.js docs/superpowers/workflows/tests/helpers.test.js
git commit -m "feat(workflow-v3/T2): findings 状态机纯函数 — updateFindingsHistory + formatFindingsHistory + hasRegressed

E': open/fixed/regressed 三态，regressed 触发 halt 不注入，[FIXED] 全注入防回归。
lib.js 新增 3 个 export 函数 + helpers.test.js 14 个测试块。TDD RED→GREEN。"
```

---

## Task 3: lib.js — OSCILLATING halt 改 flipFlop 驱动 + resolveReviewBudget（OSC，TDD）

**Files:**
- Modify: `docs/superpowers/workflows/lib.js`（改 `shouldEscalateOnOscillation` + 新增 `resolveReviewBudget`）
- Modify: `docs/superpowers/workflows/tests/helpers.test.js`（更新 + 追加测试）

**设计：** `shouldEscalateOnOscillation(currentModel, alreadyEscalated)` 旧逻辑：已升级 → return false（halt）。新逻辑：移除「已升级→halt」语义（改由 flipFlop + budget 驱动），`shouldEscalateOnOscillation` 仅保留「未升 opus → 升级」判断，halt 决策上移到 run-plans.js 的 OSC 分支（用 `isFlipFlop` + `hasRegressed` + budget）。新增 `resolveReviewBudget(config)` 解析 budget（默认 8，仅无限模式生效）。

⚠️ `shouldEscalateOnOscillation` 签名/语义改了，run-plans.js OSC 分支必须同步改（Task 5），不能只改 lib.js。sync.test 字节守护会强制 run-plans.js inline 副本一致。

- [ ] **Step 1: Write failing tests**

先看现有 `shouldEscalateOnOscillation` 测试（在 helpers.test.js），更新它们 + 追加 budget 测试。在 import 段追加 `resolveReviewBudget`：

```javascript
import { ..., shouldEscalateOnOscillation, resolveReviewBudget } from '../lib.js'
```

更新现有 `shouldEscalateOnOscillation` 测试块（找到后替换）+ 追加 budget 测试。如果找不到现有测试块，直接在文件末尾追加：

```javascript
// —— shouldEscalateOnOscillation (v3: 仅判断"是否升级 opus"，halt 决策上移) ——

test('shouldEscalateOnOscillation returns true when non-opus and not yet escalated', () => {
  assert.equal(shouldEscalateOnOscillation('sonnet', false), true)
})

test('shouldEscalateOnOscillation returns false when already escalated', () => {
  // v3: 已升级 → return false（但 halt 决策已上移到 OSC 分支，不再意味 halt）
  assert.equal(shouldEscalateOnOscillation('opus', true), false)
})

test('shouldEscalateOnOscillation returns false when already opus', () => {
  assert.equal(shouldEscalateOnOscillation('opus', false), false)
})

// —— resolveReviewBudget ——

test('resolveReviewBudget returns 8 default when unconfigured', () => {
  assert.equal(resolveReviewBudget({}), 8)
  assert.equal(resolveReviewBudget(undefined), 8)
  assert.equal(resolveReviewBudget({ review_budget: null }), 8)
})

test('resolveReviewBudget returns configured positive integer', () => {
  assert.equal(resolveReviewBudget({ review_budget: 10 }), 10)
  assert.equal(resolveReviewBudget({ review_budget: 6 }), 6)
})

test('resolveReviewBudget returns default 8 for non-number', () => {
  assert.equal(resolveReviewBudget({ review_budget: '8' }), 8)
  assert.equal(resolveReviewBudget({ review_budget: 'eight' }), 8)
})

test('resolveReviewBudget returns 8 for zero or negative (use default)', () => {
  // 0/负数无意义（budget 必须正）→ 用默认 8
  assert.equal(resolveReviewBudget({ review_budget: 0 }), 8)
  assert.equal(resolveReviewBudget({ review_budget: -1 }), 8)
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd docs/superpowers/workflows && node --test tests/helpers.test.js`
Expected: FAIL — `resolveReviewBudget` 未定义；`shouldEscalateOnOscillation` 旧测试（如果有断言「已升级→halt」语义的）会失败。

- [ ] **Step 3: Write minimal implementation**

在 `docs/superpowers/workflows/lib.js` 替换现有 `shouldEscalateOnOscillation`（约 line 50-54）并在其后追加 `resolveReviewBudget`：

```javascript
// v3 (2026-07-06, §5.5): shouldEscalateOnOscillation 仅判断「是否升级 opus」。
// halt 决策上移到 run-plans.js OSC 分支（flipFlop OR hasRegressed → halt；else 继续 + budget guard）。
// 旧逻辑「已升级→return false→halt」已移除（那是纯计数 halt 的根因，浪费 opus 推进力）。
export function shouldEscalateOnOscillation(currentModel, alreadyEscalated) {
  if (alreadyEscalated) return false  // 已升级过不再重复升级（不影响 halt 决策）
  return currentModel !== 'opus'      // 非 opus → 升级
}

// v3 (2026-07-06, §5.5): 无限模式（review_max_rounds=0）的 review 轮数预算。
// flipFlop=false 持续推进时，升 opus 后继续跑直到 budget 耗尽（防 reviewer 同义变体漏报致无限跑）。
// 默认 8。仅无限模式生效；有限模式用 review_max_rounds 硬上限。
export function resolveReviewBudget(config) {
  const v = config?.review_budget
  if (typeof v !== 'number' || !Number.isFinite(v) || v <= 0) return 8
  return v
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd docs/superpowers/workflows && node --test tests/helpers.test.js`
Expected: PASS — 全部测试通过。

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/workflows/lib.js docs/superpowers/workflows/tests/helpers.test.js
git commit -m "feat(workflow-v3/T3): OSCILLATING halt 改 flipFlop 驱动 — shouldEscalateOnOscillation 语义化 + resolveReviewBudget

OSC: shouldEscalateOnOscillation 仅判断升级，halt 决策上移；resolveReviewBudget 默认 8。
⚠️ lib.js 改了，run-plans.js inline 副本须 Task 5 同步（sync.test 守护）。
TDD RED→GREEN。"
```

---

## Task 4: run-plans.js — lessons 两层注入接线 + bootstrap category 匹配（A+B 接线）

**Files:**
- Modify: `.claude/workflows/run-plans.js`（inline 副本 + implCtx + bootstrap prompt）
- Modify: `docs/superpowers/workflows/tests/sync.test.js`（helpers 列表 + 字节比较）

**设计：** 3 处改动：
1. inline 副本：在 `formatLessons` 之后追加 `formatUniversalLessons` + `formatDomainLessons` 的 inline 副本（与 lib.js 字节一致，sync.test 守护）
2. `implCtx`：`lessons` 字段改为拼 `formatUniversalLessons + formatDomainLessons`（替代旧 `formatLessons`）。需要 `state.taskAllLessons`（bootstrap 解析的全量 lessons，按 task 取）+ task 的 `lesson_categories`
3. bootstrap prompt：加 category 匹配说明（task_lessons 返回时附带 category），plan frontmatter 读 `lesson_categories`

⚠️ `state.taskLessons[taskKey]` 现存结构是 `[{id, title, detail}]`（无 category）。bootstrap 需改为返回全量 lessons（含 category），run-plans.js 存 `state.taskAllLessons`（不分 task，全量），implCtx 按需取 universal + domain。简化：bootstrap 仍返回 task-scoped `task_lessons`（keyword 匹配），但**额外**返回 `all_lessons`（全量，含 category），run-plans.js 存 `state.allLessons`，implCtx 用 `allLessons` 算两层。这样 task_lessons（旧 keyword）仍向后兼容。

- [ ] **Step 1: Write failing test (sync.test 守护新 helper)**

在 `docs/superpowers/workflows/tests/sync.test.js` 找到 helpers 列表（约 line 33，`for (const fn of [...])`），把 `formatUniversalLessons`, `formatDomainLessons` 加入数组：

```javascript
// 修改 line 33 的数组，追加 2 个新 helper（保持字母顺序或加在末尾）
for (const fn of ['formatReferencePaths', 'formatSilentFailureContext', 'formatFailedApproaches', 'formatLessons', 'formatUniversalLessons', 'formatDomainLessons', 'formatWriteFilesScope', 'formatSchemaCheck', 'languageChecklist', 'LANGUAGE_CHECKLISTS', 'gateCommands', 'collectReviewFindings', 'formatFindings', 'matchesPlanFilter', 'classifyThrown', 'reviewHaltReason', 'reviewHaltForEmptyFailed', 'haltLikelySource', 'fixModelForRound', 'resolveMaxRounds', 'resolveLessonsAutoDistill', 'distillLessonInput', 'summarizeReviewRound', 'groupFindingsByFile', 'formatCrossReviewerNote']) {
```

再找到字节比较列表（约 line 65-83，`for (const fn of [...])` 字节比较那段），追加 2 个新 helper：

```javascript
// 找到字节比较的数组（含 'extractCompletedFromSubjects', 'shouldEscalateOnOscillation', 'isFlipFlop' 等）
// 追加 'formatUniversalLessons', 'formatDomainLessons'
```

- [ ] **Step 2: Run sync test to verify it fails**

Run: `cd docs/superpowers/workflows && node --test tests/sync.test.js`
Expected: FAIL — run-plans.js 缺 `formatUniversalLessons`/`formatDomainLessons` 的 inline 副本。

- [ ] **Step 3: Add inline copies to run-plans.js**

在 `.claude/workflows/run-plans.js` 的 `formatLessons` inline 副本（约 line 451-457）之后追加（与 lib.js 字节一致——直接复制 Task 1 Step 3 的实现）：

```javascript
// —— v3 lessons 两层注入（inline 自 lib.js，sync.test 字节守护）——
function formatUniversalLessons(allLessons) {
  if (!Array.isArray(allLessons) || allLessons.length === 0) return ''
  const universal = allLessons.filter(l => l && l.category === 'silent-failure')
  if (universal.length === 0) return ''
  const lines = universal.map(l => `- [${l.id}] ${l.title} — ${l.detail}`).join('\n')
  return `## Universal Discipline (silent-failure — always apply)
${lines}
These are project-wide silent-failure disciplines. Before reporting done, verify your code does not violate any of them (savepoint isolation, naive-UTC datetime, single-transaction commits, etc.).`
}

function formatDomainLessons(allLessons, taskCategories, currentPlanSeq, taskTitle) {
  if (!Array.isArray(allLessons) || allLessons.length === 0) return ''
  const candidates = allLessons.filter(l => l && l.category !== 'silent-failure')
  let matched = []
  if (Array.isArray(taskCategories) && taskCategories.length > 0) {
    matched = candidates.filter(l => taskCategories.includes(l.category))
  } else if (taskTitle) {
    const tokens = String(taskTitle).toLowerCase().split(/[\s,，、]+/).filter(t => t.length > 1)
    matched = candidates.filter(l => {
      const text = `${l.title || ''} ${l.detail || ''}`.toLowerCase()
      return tokens.some(t => text.includes(t))
    })
  }
  if (matched.length === 0) return ''
  if (currentPlanSeq) {
    matched.sort((a, b) => {
      const aSame = a.source && String(a.source).includes(currentPlanSeq) ? 0 : 1
      const bSame = b.source && String(b.source).includes(currentPlanSeq) ? 0 : 1
      return aSame - bSame
    })
  }
  const capped = matched.slice(0, 5)
  const lines = capped.map(l => `- [${l.id}] ${l.title} — ${l.detail}`).join('\n')
  return `## Domain Lessons (check against these before implementing)
${lines}
If your plan is similar to any lesson above, explicitly state why your approach differs.`
}
```

- [ ] **Step 4: Add all_lessons parsing + state.allLessons**

在 `.claude/workflows/run-plans.js` 找到 `state.taskLessons = {}`（约 line 932），其后追加：

```javascript
  taskLessons: {},  // {taskKey: [{id, title, detail}]} — 旧 keyword 匹配（向后兼容）
  allLessons: [],  // v3: bootstrap 解析的全量 lessons（含 category），供两层注入
```

找到 bootstrap evidence 解析段（`for (const tl of boot.evidence.task_lessons || [])`，约 line 1412-1417），在其后追加 all_lessons 解析：

```javascript
// v3: bootstrap 额外返回 all_lessons（全量，含 category），存 state.allLessons
if (Array.isArray(boot.evidence.all_lessons)) {
  state.allLessons = boot.evidence.all_lessons
}
```

- [ ] **Step 5: Update implCtx to use two-tier lessons**

找到 `implCtx`（约 line 1061），把 `lessons: formatLessons(state.taskLessons?.[taskKey] || [])` 改为：

```javascript
  // v3 两层注入：Tier 1 silent-failure 始终 + Tier 2 领域 category 匹配
  const planSeq = `plan-${String(plan.seq).padStart(2, '0')}`
  const taskCats = plan.task_lesson_categories?.[task.id]  // plan frontmatter 可选声明
  const lessonsText = formatUniversalLessons(state.allLessons || []) + formatDomainLessons(state.allLessons || [], taskCats, planSeq, task.title)
  const implCtx = (fix, note, ctx = '') => ({ planId: plan.id, taskId: task.id, planFilePath: plan.file, specPath: cfg.spec_path, testCommand: cfg.test_command, buildCommand: cfg.build_command || '', fixIssues: fix, retryNote: note, fetchedContext: ctx, referencePaths: formatReferencePaths(cfg.reference_paths), failedApproaches: formatFailedApproaches(state.failedApproaches?.[taskKey] || []), lessons: lessonsText })
```

- [ ] **Step 6: Update bootstrap prompt to return all_lessons**

找到 bootstrap prompt 的 step 1（lessons_path 解析段，约 line 639），追加 all_lessons 返回说明：

```javascript
// 修改 bootstrap prompt 的 step 1 + Return 段
// step 1 追加：「Additionally, return all_lessons: 全量 lessons 数组 [{id, title, detail, category, source}] (含 category 字段，供 runtime 两层注入)。task_lessons 仍按 title 关键词匹配返回（向后兼容）。」
// Return evidence 段追加 all_lessons 字段
```

具体：在 step 1 文本「Return matched lessons per task in evidence as task_lessons」之后追加：
```
Additionally, return all_lessons: the full list of all lessons parsed from lessonsPath as [{id, title, detail, category, source}] (include category field even if absent on a legacy entry → null). This feeds v3 two-tier injection (Tier 1 silent-failure always + Tier 2 domain by category).
```

在 Return evidence 的 schema 段（约 line 649）追加 `all_lessons: [{id, title, detail, category, source}]`。同时在 bootstrap schema（约 line 575-576 的 evidence properties）追加 `all_lessons: { type: 'array' }`。

- [ ] **Step 7: Run sync test to verify inline copies match**

Run: `cd docs/superpowers/workflows && node --test tests/sync.test.js`
Expected: PASS — 2 个新 helper inline 副本与 lib.js 字节一致。

- [ ] **Step 8: Run helpers test (lib.js 改动未破坏)**

Run: `cd docs/superpowers/workflows && node --test tests/helpers.test.js`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add .claude/workflows/run-plans.js docs/superpowers/workflows/tests/sync.test.js
git commit -m "feat(workflow-v3/T4): lessons 两层注入接线 — implCtx + bootstrap all_lessons + sync.test 守护

A+B 接线：run-plans.js inline formatUniversalLessons/formatDomainLessons（字节守护），
implCtx 拼两层，bootstrap 返回 all_lessons（含 category），plan frontmatter 可声明
lesson_categories。sync.test 加 2 helper 守护。"
```

---

## Task 5: run-plans.js — findings 状态机接线 + OSC 分支重写（E' 接线 + OSC，核心）

**Files:**
- Modify: `.claude/workflows/run-plans.js`（inline 副本 + review loop 接线 + OSC 分支重写）
- Modify: `docs/superpowers/workflows/tests/sync.test.js`（守护新 helper + `shouldEscalateOnOscillation` 字节比较）

**设计：** 这是核心 task。4 处改动：
1. inline 副本：`updateFindingsHistory` + `formatFindingsHistory` + `hasRegressed` + `resolveReviewBudget`（与 lib.js 字节一致）
2. `state.perTask[taskKey]` 初始化加 `findings_history: []`
3. review loop：每轮 review 后 `updateFindingsHistory`；fix round 的 `implCtx` 用 `formatFindingsHistory` 替代 `formatFindings`（仅当前轮）
4. OSC 分支重写：`flipFlop || hasRegressed` → halt；else `shouldEscalateOnOscillation` 升 opus；budget guard 8

⚠️ sync.test 字节比较列表已有 `shouldEscalateOnOscillation`（line 76），Task 3 改了实现，本 task 同步 inline 副本后字节守护自动通过。

- [ ] **Step 1: Update sync.test helpers list + byte-compare list**

在 `docs/superpowers/workflows/tests/sync.test.js` 的 helpers 列表（line 33）追加：`updateFindingsHistory`, `formatFindingsHistory`, `hasRegressed`, `resolveReviewBudget`。

在字节比较列表（line 65-83）追加同样 4 个（含已有的 `shouldEscalateOnOscillation` 保持）：

```javascript
// 字节比较数组追加（保持现有元素，追加 4 个新 + 已有 shouldEscalateOnOscillation 不动）
'updateFindingsHistory', 'formatFindingsHistory', 'hasRegressed', 'resolveReviewBudget',
```

- [ ] **Step 2: Run sync test to verify it fails**

Run: `cd docs/superpowers/workflows && node --test tests/sync.test.js`
Expected: FAIL — run-plans.js 缺 4 个新 helper inline 副本。

- [ ] **Step 3: Add inline copies to run-plans.js**

在 `.claude/workflows/run-plans.js` 的 `formatDomainLessons` inline 副本（Task 4 加）之后追加（直接复制 Task 2 + Task 3 的 lib.js 实现，保持字节一致）：

```javascript
// —— v3 findings 状态机（inline 自 lib.js，sync.test 字节守护）——
function updateFindingsHistory(history, currentFindings, round) {
  if (!Array.isArray(history)) history = []
  const current = Array.isArray(currentFindings) ? currentFindings : []
  const currentTitles = new Set(current.map(f => f?.title).filter(Boolean))
  const result = history.map(h => {
    const stillPresent = currentTitles.has(h.title)
    if (stillPresent) {
      const status = h.status === 'open' ? 'open' : 'regressed'
      return { ...h, last_seen: round, rounds: [...h.rounds, round], status, fixed_at_round: h.fixed_at_round }
    }
    if (h.status === 'open') return { ...h, status: 'fixed', fixed_at_round: round }
    return h
  })
  const existingTitles = new Set(history.map(h => h.title))
  for (const f of current) {
    if (f?.title && !existingTitles.has(f.title)) {
      result.push({ title: f.title, severity: f.severity, fix: f.fix, file: f.file, first_seen: round, last_seen: round, rounds: [round], status: 'open' })
    }
  }
  return result
}

function hasRegressed(history) {
  if (!Array.isArray(history)) return false
  return history.some(h => h?.status === 'regressed')
}

function formatFindingsHistory(history) {
  if (!Array.isArray(history) || history.length === 0) return ''
  const open = history.filter(h => h.status === 'open')
  const fixed = history.filter(h => h.status === 'fixed')
  const sections = []
  if (open.length > 0) {
    const lines = open.map(h => {
      const sev = h.severity ? `[${h.severity}]` : ''
      const seen = `(seen: r${h.rounds.join(',')})`
      const file = h.file ? `, file: ${h.file}` : ''
      const fix = h.fix ? ` — fix: ${h.fix}` : ''
      return `- ${sev} ${h.title} ${seen}${file}${fix}`
    }).join('\n')
    sections.push(`### [OPEN] 本轮仍存在 — 必须修完\n${lines}`)
  }
  if (fixed.length > 0) {
    const lines = fixed.map(h => {
      const sev = h.severity ? `[${h.severity}]` : ''
      const file = h.file ? `, file: ${h.file}` : ''
      const fix = h.fix ? ` — fix: ${h.fix}` : ''
      return `- ${sev} ${h.title} (fixed r${h.fixed_at_round}${file})${fix}`
    }).join('\n')
    sections.push(`### [FIXED] 已修好的 — 修新问题时勿碰这些文件区域，勿重新引入已修问题\n${lines}`)
  }
  if (sections.length === 0) return ''
  return `## Findings History (全轮累积)\n${sections.join('\n\n')}`
}

// —— v3 OSCILLATING budget（inline 自 lib.js）——
function resolveReviewBudget(config) {
  const v = config?.review_budget
  if (typeof v !== 'number' || !Number.isFinite(v) || v <= 0) return 8
  return v
}
```

⚠️ 同时更新现有 `shouldEscalateOnOscillation` inline 副本（约 line 49-52，旧实现）为 Task 3 的新实现（字节与 lib.js 一致）：

```javascript
// 替换旧 shouldEscalateOnOscillation inline 副本
function shouldEscalateOnOscillation(currentModel, alreadyEscalated) {
  if (alreadyEscalated) return false
  return currentModel !== 'opus'
}
```

- [ ] **Step 4: Add findings_history to perTask init**

找到 `state.perTask[taskKey] = { ... }` 初始化（约 line 964，含 `files_touched_per_round: [], review_history: []`），追加 `findings_history: []`：

```javascript
  state.perTask[taskKey] = {
    plan_id: plan.id,
    status: 'in_progress',
    model,
    review_rounds: 0,
    files_touched_per_round: [],
    review_history: [],
    findings_history: [],  // v3: findings 状态机（open/fixed/regressed）
    commit_sha: null,
    opus_escalated: false,
    // ... 其余字段保持
  }
```

- [ ] **Step 5: Wire updateFindingsHistory into review loop**

找到 review loop 里 `state.perTask[taskKey].review_history.push(...)`（约 line 1131，summarizeReviewRound push 之后），在其后追加 findings_history 更新：

```javascript
    state.perTask[taskKey].review_history.push(summarizeReviewRound(round, spec, qual, hunt))
    // v3: findings 状态机更新（在 halt 检查之前，halt 轮也须持久化）
    const currentFindings = collectReviewFindings(spec, qual, hunt)
    state.perTask[taskKey].findings_history = updateFindingsHistory(
      state.perTask[taskKey].findings_history, currentFindings, round
    )
```

- [ ] **Step 6: Rewrite OSC branch (flipFlop/regressed driven + budget)**

找到现有 OSC 分支（约 line 1139-1151，`const osc = detectOscillation(...)` 整个 if 块），替换为：

```javascript
    const osc = detectOscillation(state.perTask[taskKey].files_touched_per_round)
    if (osc.oscillating) {
      const flipFlop = isFlipFlop(state.perTask[taskKey].review_history || [])
      const regressed = hasRegressed(state.perTask[taskKey].findings_history || [])
      // v3 (§5.5): flipFlop OR regressed → 立即 halt（真振荡/回归循环）
      if (flipFlop || regressed) {
        return {
          halted: true,
          reason: 'OSCILLATING',
          diag: {
            ...osc,
            flipFlop,
            regressed,
            regressedFindings: state.perTask[taskKey].findings_history.filter(h => h.status === 'regressed'),
            model,
          },
        }
      }
      // flipFlop=false 且无 regressed（每轮新 findings = 在推进）
      if (shouldEscalateOnOscillation(model, state.perTask[taskKey].opus_escalated)) {
        state.perTask[taskKey].opus_escalated = true
        model = 'opus'
        log(`⚠ ${task.id}: r${round} OSCILLATING (new-findings 补充, flipFlop=false) — escalate to opus, continue (v3)`)
        // 不 halt，继续下一轮
      } else {
        // 已升 opus，继续跑（new findings = progress），由 budget guard 兜底
        log(`⚠ ${task.id}: r${round} OSCILLATING (flipFlop=false, opus already escalated) — continue until budget (v3)`)
      }
    }
```

- [ ] **Step 7: Add budget guard for infinite mode**

找到现有「maxRounds=0 永不 halt」注释行（约 line 1153，`if (maxRounds !== 0 && round === maxRounds)` 之前），改为带 budget guard：

```javascript
    // v3: 无限模式（maxRounds=0）budget guard——flipFlop=false 持续推进的兜底
    //   防 reviewer 同义变体（改 title）让 [REGRESSED] 漏报后无限跑
    if (maxRounds === 0) {
      const budget = resolveReviewBudget(cfg)
      if (round >= budget) {
        return { halted: true, reason: 'review budget exhausted', diag: { round, budget, spec: spec.diagnostics, qual: qual.diagnostics, hunt: hunt.diagnostics } }
      }
    } else if (round === maxRounds) {
      // 有限模式仍用 maxRounds 硬上限
      return { halted: true, reason: 'review max rounds', diag: { spec: spec.diagnostics, qual: qual.diagnostics, hunt: hunt.diagnostics } }
    }
```

- [ ] **Step 8: Update fix round implCtx to use formatFindingsHistory**

找到 fix round 的 `fixIssues` 构造（约 line 1156，`const findings = collectReviewFindings(...)`）+ implCtx 调用。改为：当前轮 findings 仍作 `fixIssues`（implementor 看到本轮新发现），但 `implCtx` 的 `fixIssues` 拼上 `formatFindingsHistory`（全量 [OPEN]+[FIXED]）：

```javascript
    const findings = collectReviewFindings(spec, qual, hunt)
    const fixIssues = formatFindings(findings) + formatCrossReviewerNote(findings)
    // v3: 拼上 findings history（[OPEN] 全量 + [FIXED] 全量，防回归）
    const findingsHistoryText = formatFindingsHistory(state.perTask[taskKey].findings_history || [])
    const fullFixIssues = findingsHistoryText ? `${fixIssues}\n${findingsHistoryText}` : fixIssues
    const fixModel = fixModelForRound(round, model, maxRounds)
    impl = await dispatchImpl(buildPrompt('implementor', implCtx(fullFixIssues, `修复 review round ${round} 问题（${findings.length} 项新发现 + findings history）.`)), { schema: SCHEMAS.implementor, model: fixModel, label: `impl:${task.id}:fix${round}` }, fixModel, 'opus')
```

- [ ] **Step 9: Run sync test (all inline copies match lib.js)**

Run: `cd docs/superpowers/workflows && node --test tests/sync.test.js`
Expected: PASS — 全部 4 个新 helper + `shouldEscalateOnOscillation` inline 副本与 lib.js 字节一致。

- [ ] **Step 10: Run helpers test (lib.js untouched this task, sanity)**

Run: `cd docs/superpowers/workflows && node --test tests/helpers.test.js`
Expected: PASS

- [ ] **Step 11: Run full workflow test suite**

Run: `cd docs/superpowers/workflows && node --test 'tests/*.test.js'`
Expected: PASS — 全部测试绿（helpers + sync + 其他）。

- [ ] **Step 12: Commit**

```bash
git add .claude/workflows/run-plans.js docs/superpowers/workflows/tests/sync.test.js
git commit -m "feat(workflow-v3/T5): findings 状态机接线 + OSC 分支重写 — flipFlop/regressed 驱动 + budget 8

E' 接线 + OSC：run-plans.js inline 4 helper（字节守护），perTask 加 findings_history，
review loop 每轮 update，OSC 分支改 flipFlop||regressed→halt else 升 opus 继续 + budget 8。
fix round implCtx 拼 formatFindingsHistory（[OPEN]+[FIXED]）。"
```

---

## Task 6: run-plans.js — opus 升级 prompt 强化（F）

**Files:**
- Modify: `.claude/workflows/run-plans.js`（implCtx retryNote 分支）

**设计：** Task 5 的 fix round 已注入 `formatFindingsHistory`。本 task 在「升 opus」时（OSC 分支 `shouldEscalateOnOscillation` 返回 true 那刻）强化 `retryNote`，要求 implementor 本轮一次性修完全部 [OPEN]，并主动 git diff 检查不破坏 [FIXED]。

- [ ] **Step 1: Track oscillation escalation in state**

在 Task 5 Step 6 的 OSC 分支 `if (shouldEscalateOnOscillation(...))` 块内（升级 opus 那刻），追加标记：

```javascript
      if (shouldEscalateOnOscillation(model, state.perTask[taskKey].opus_escalated)) {
        state.perTask[taskKey].opus_escalated = true
        state.perTask[taskKey].oscillation_escalated_at_round = round  // v3 F: 升级轮次
        model = 'opus'
        log(`⚠ ${task.id}: r${round} OSCILLATING (new-findings 补充, flipFlop=false) — escalate to opus, continue (v3)`)
      }
```

- [ ] **Step 2: Strengthen retryNote when oscillation-escalated**

找到 fix round implCtx 调用（Task 5 Step 8），把 `retryNote` 改为分支构造：

```javascript
    const findings = collectReviewFindings(spec, qual, hunt)
    const fixIssues = formatFindings(findings) + formatCrossReviewerNote(findings)
    const findingsHistoryText = formatFindingsHistory(state.perTask[taskKey].findings_history || [])
    const fullFixIssues = findingsHistoryText ? `${fixIssues}\n${findingsHistoryText}` : fixIssues
    // v3 F: opus 升级轮强化 retryNote
    const oscEscRound = state.perTask[taskKey].oscillation_escalated_at_round
    const retryNote = oscEscRound === round
      ? `## ⚠ OSCILLATING ESCALATION (model: opus)
前 ${round - 1} 轮 review 已累计未修 findings（见 [OPEN] below）。你必须在【本轮一次性修完全部】——禁止增量补一个留下一个等下轮。逐条核对 findings history：[OPEN] 每个都要修或显式说明为何不修；[FIXED] 勿碰这些文件区域，修新问题时主动 \`git diff\` 检查不重新引入已修好的问题。`
      : `修复 review round ${round} 问题（${findings.length} 项新发现 + findings history）.`
    const fixModel = fixModelForRound(round, model, maxRounds)
    impl = await dispatchImpl(buildPrompt('implementor', implCtx(fullFixIssues, retryNote)), { schema: SCHEMAS.implementor, model: fixModel, label: `impl:${task.id}:fix${round}` }, fixModel, 'opus')
```

- [ ] **Step 3: Run full workflow test suite**

Run: `cd docs/superpowers/workflows && node --test 'tests/*.test.js'`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add .claude/workflows/run-plans.js
git commit -m "feat(workflow-v3/T6): opus 升级轮 prompt 强化 — 一次性修完全部 [OPEN] + 勿碰 [FIXED]

F: oscillation_escalated_at_round 标记升级轮，retryNote 强化要求一次性修完 +
git diff 检查不破坏 [FIXED]。依赖 T5 的 findings_history 注入。"
```

---

## Task 7: 全量验证 + 文档同步

**Files:**
- Verify: 全部 workflow tests 绿
- 已更新（前置）: `docs/superpowers/workflow-design.md` §5.5 + §13g, `docs/superpowers/workflows/USAGE.md`

**设计：** 最终验证所有改动协调工作。docs（§5.5 + §13g + USAGE）已在 plan 前置步骤更新（commit 在本 plan 之外的 docs commit），本 task 只做最终核对。

- [ ] **Step 1: Run full workflow test suite**

Run: `cd docs/superpowers/workflows && node --test 'tests/*.test.js'`
Expected: PASS — 全绿（helpers + sync + 其他单测，总数应从 261 增至 ~290+）。

- [ ] **Step 2: Verify docs already updated**

确认以下文件已含 v3 内容（应在前置 docs commit 已完成）：

```bash
grep -c "§5.5\|5\.5 Review v3" docs/superpowers/workflow-design.md  # 应 ≥1
grep -c "review_budget" docs/superpowers/workflows/USAGE.md  # 应 ≥1
grep -c "formatFindingsHistory\|hasRegressed" docs/superpowers/workflows/USAGE.md  # 应 ≥1
```

- [ ] **Step 3: Verify halt behavior matches design (manual trace)**

确认 OSC 分支逻辑符合 §5.5 设计：

```bash
grep -A 3 "flipFlop || regressed" .claude/workflows/run-plans.js  # 应有 halt 分支
grep "review budget exhausted" .claude/workflows/run-plans.js  # 应有 budget halt
grep "formatFindingsHistory" .claude/workflows/run-plans.js  # 应在 fix round 注入
```

- [ ] **Step 4: Final commit (if any doc drift)**

如果 Step 2 发现 docs 缺漏，补 commit：

```bash
git add docs/superpowers/workflow-design.md docs/superpowers/workflows/USAGE.md
git commit -m "docs(workflow-v3): 同步 §5.5 + §13g + USAGE review_budget/findings 状态机"
```

如果 docs 已完整，跳过本步。

---

## Self-Review

**1. Spec coverage（对照 v3 方案 5 改进）：**
- A（category 匹配）→ Task 1（lib.js）+ Task 4（bootstrap prompt + plan frontmatter）✅
- B（两层注入）→ Task 1（formatUniversalLessons + formatDomainLessons）+ Task 4（implCtx 拼接 + state.allLessons）✅
- E'（findings 状态机）→ Task 2（纯函数）+ Task 5（接线 + perTask + review loop）✅
- OSCILLATING（flipFlop 驱动 + budget 8 + hasRegressed）→ Task 3（lib.js）+ Task 5（OSC 分支重写 + budget guard）✅
- F（opus 升级 prompt 强化）→ Task 6 ✅
- 注入边界（findings 仅当前 task，lessons 跨 task）→ Task 5（perTask[taskKey].findings_history）+ Task 4（state.allLessons）✅
- [REGRESSED] 不注入 → Task 2 formatFindingsHistory 测试覆盖 ✅
- [FIXED] 全注入（无 cap）→ Task 2 formatFindingsHistory 实现（无 slice）✅
- task 内 fix 不 commit，[FIXED] 标 file 路径 → Task 2 实现（`file: ${h.file}`）✅

**2. Placeholder scan：** 全部代码块完整，无 TBD/TODO。每步含可执行命令 + 预期输出。

**3. Type consistency：**
- `formatUniversalLessons(allLessons)` / `formatDomainLessons(allLessons, taskCategories, currentPlanSeq, taskTitle)` — Task 1 定义，Task 4 implCtx 调用签名一致 ✅
- `updateFindingsHistory(history, currentFindings, round)` — Task 2 定义，Task 5 review loop 调用一致 ✅
- `formatFindingsHistory(history)` — Task 2 定义，Task 5 fix round 调用一致 ✅
- `hasRegressed(history)` — Task 2 定义，Task 5 OSC 分支调用一致 ✅
- `shouldEscalateOnOscillation(currentModel, alreadyEscalated)` — Task 3 改签名（语义化），Task 5 OSC 分支调用一致 ✅
- `resolveReviewBudget(config)` — Task 3 定义，Task 5 budget guard 调用一致 ✅
- finding 形态 `{title, severity, fix, file, first_seen, last_seen, rounds, status, fixed_at_round?}` — Task 2 定义，Task 5 currentFindings 用 `collectReviewFindings` 输出（`{source, severity, title, file, fix}`），`updateFindingsHistory` 内部转换一致 ✅

---

## 执行说明

本 plan 的 docs（§5.5 + §13g + USAGE）已在 plan 编写前的 docs commit 更新完毕（独立于本 plan 的代码 task）。Task 7 仅做最终核对，如发现 docs 缺漏再补。

**实施顺序严格 Task 1→7**：lib.js 纯函数先行（TDD 单测），再 run-plans.js 接线（sync.test 守护）。Task 5 是核心（OSC 分支重写），依赖 Task 1-4 全部完成。

每个 Task 完成后跑 `cd docs/superpowers/workflows && node --test 'tests/*.test.js'` 确认全绿，再进下一个 Task。
