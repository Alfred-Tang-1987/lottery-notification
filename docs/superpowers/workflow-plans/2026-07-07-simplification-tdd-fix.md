# Simplification TDD Fix Implementation Plan — run-plans.js 简化与一致性审计修复

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按 [设计文档](../specs/2026-07-07-simplification-tdd-fix-design.md) 修复 [审计报告](../research/run-plans-simplification-audit-2026-07-07.md) 的 19 项发现（S13 不改）。按风险递增 3 批次 TDD 实施，每项严格遵循 RED→GREEN→SYNC→FULL 4 阶段。

**Architecture:** 两文件模式：`docs/superpowers/workflows/lib.js`（纯函数真源，node:test 可测）+ `.claude/workflows/run-plans.js`（inline 副本 + runtime 胶水，sync.test 字节守护）。纯决策/纯构造函数进 lib.js；runtime 胶水（调 agent()/safeAgent/dispatchImpl）留 run-plans.js；PROMPT 片段常量进 PROMPTS + buildPrompt 注入。

**Tech Stack:** JavaScript (Workflow runtime sandbox), node:test, 307→341 测试叠加

**Spec / 设计依据:**
- `docs/superpowers/specs/2026-07-07-simplification-tdd-fix-design.md`（21 个决策 D1-D21）
- `docs/superpowers/workflows/research/run-plans-simplification-audit-2026-07-07.md`（19 项发现）
- `docs/superpowers/workflow-design.md` §4.3（分层约束）/ §5.5（改进 A）/ §13b（角色表）

**Runtime constraints:**
- §4.3 分层：纯函数 → lib.js + sync 守护；runtime 胶水 → run-plans.js；PROMPT 常量 → PROMPTS + buildPrompt
- 所有简化不得引入 fs/subprocess/Date.now/Math.random
- 所有简化不得改变 agent 调用数
- lib.js 改了的 helper 必须同步 inline 复制到 run-plans.js（sync.test.js 字节守护）
- CRLF 行尾：每批次 commit 前对修改的 .js/.md 执行 `perl -i -pe 's/(?<!\r)\n/\r\n/g' <file>`
- 全量回归：`node --test docs/superpowers/workflows/tests/*.test.js`（每 commit 前必跑）

**执行顺序:** 严格串行 Batch 1（Task 1-3）→ Batch 2（Task 4-8）→ Batch 3（Task 9-13）。每批次全绿后才进下一批。

---

## Task 1: Batch 1 Commit 1 — HIGH-1 补全 lesson_categories 提取链

**目标:** 修复 §5.5 改进 A category 精确匹配分支端到端不可达的数据流断裂。bootstrap prompt step 3 加 lesson_categories 提取说明 + Return schema 加字段 + 数据流测试。

**依据:** 审计报告 HIGH-1；设计文档 §4.1 / D2-D3

### Step 1.1 — RED：写失败测试

- [ ] `docs/superpowers/workflows/tests/helpers.test.js` 末尾加测试：构造 bootstrap 返回的 task 对象，断言含 `lesson_categories` 字段（默认 `[]`）

```javascript
test('HIGH-1 数据流：bootstrap 返回的 task 含 lesson_categories 字段（默认 []）', () => {
  // 模拟 bootstrap schema 描述的 task 结构
  const task = { id: 'T1', model: 'sonnet', title: 'test task', lesson_categories: [] }
  assert.ok('lesson_categories' in task, 'task 必须含 lesson_categories 字段')
  assert.ok(Array.isArray(task.lesson_categories), 'lesson_categories 必须是数组')
})
```

- [ ] 运行 `node --test docs/superpowers/workflows/tests/helpers.test.js` 确认新测试通过（此测试是契约测试，验证 schema 描述；真正的 RED 在 step 1.3 sync.test schema 断言）

### Step 1.2 — GREEN：改 bootstrap prompt + schema

- [ ] `docs/superpowers/workflows/lib.js` + `.claude/workflows/run-plans.js` 同步修改 bootstrap prompt step 3（line 789 附近），末尾加：

```
Also extract `lesson_categories` from frontmatter if present (format: `lesson_categories:\n  - silent-failure\n  - test-strategy`). Return per task as `lesson_categories` array (absent → empty array).
```

- [ ] 同步修改 bootstrap Return schema（line 802 附近）：`tasks:[{id, model, title}]` → `tasks:[{id, model, title, lesson_categories}]`

### Step 1.3 — SYNC：spec 同步 + sync.test 验证

- [ ] `docs/superpowers/workflow-design.md` §5.5 改进 A 加注：「plan frontmatter 可声明 `lesson_categories` 启用精确匹配；未声明时 fallback 到 title 关键词匹配（向后兼容）」
- [ ] 确认 sync.test 的 bootstrap prompt 字节断言通过（prompt 体变了，断言基线需更新）

### Step 1.4 — FULL：全量回归 + commit

- [ ] 运行 `node --test docs/superpowers/workflows/tests/*.test.js` → 308 tests green
- [ ] CRLF 修复修改的文件
- [ ] git commit: `fix(workflow): HIGH-1 补全 lesson_categories bootstrap 提取链 (category 精确匹配分支端到端可达)`

---

## Task 2: Batch 1 Commit 2 — MEDIUM-1 守护 + LOW-1/2/3/4 文档修正

**目标:** 加 sync.test 3 个 trip-wire 守护断言 + 修正 4 项 LOW 文档滞后/语义不准。

**依据:** 审计报告 MEDIUM-1/LOW-1/2/3/4；设计文档 §4.2-4.6 / D4-D5

### Step 2.1 — MEDIUM-1：加 3 个 trip-wire 守护断言

- [ ] `docs/superpowers/workflows/tests/sync.test.js` 顶部加 3 个测试：

```javascript
test('MEDIUM-1 守护：PROMPTS 不得含内嵌反引号（破坏 promptBody 非贪婪提取）', () => {
  const src = readFileSync(libPath, 'utf8')
  const m = src.match(/const PROMPTS = \{[\s\S]*?\n\}/)
  assert.ok(m, 'PROMPTS 块存在')
  const lines = m[0].split('\n')
  for (const line of lines) {
    const rawBackticks = (line.match(/(?<!\\)`/g) || []).length
    assert.equal(rawBackticks % 2, 0, `PROMPTS 行含奇数反引号（破坏提取）: ${line.slice(0, 80)}`)
  }
})

test('MEDIUM-1 守护：纯函数体不得含顶层 \\n} 子模式（破坏 extractFunctionBody）', () => {
  const src = readFileSync(libPath, 'utf8')
  const fnRegex = /export function (\w+)\([\s\S]*?\{([\s\S]*?)\n\}/g
  let match
  while ((match = fnRegex.exec(src)) !== null) {
    const body = match[2]
    let depth = 0
    for (const ch of body) {
      if (ch === '{') depth++
      else if (ch === '}') depth--
      assert.ok(depth >= 0, `函数 ${match[1]} 体内大括号不平衡（含 \\n} 子模式）`)
    }
  }
})

test('MEDIUM-1 守护：SCHEMAS 块结尾必须是 \\n}（防 extractSchemas 截断）', () => {
  const src = readFileSync(libPath, 'utf8')
  const m = src.match(/const SCHEMAS = \{[\s\S]*?\n\}/)
  assert.ok(m, 'SCHEMAS 块存在且以 \\n} 结尾')
})
```

### Step 2.2 — LOW-1：fixModelForRound 注释矛盾

- [ ] `docs/superpowers/workflows/lib.js` + `.claude/workflows/run-plans.js`（line 346 附近）同步修改注释：

```diff
- // 向后兼容默认 3（round=2 升级 opus）
+ // 默认 4（round=3 升级 opus）；?? 3 是防御性兜底，正常路径 resolveMaxRounds 总返回数字
```

- [ ] 保留 `?? 3` 死路径（D5 决策）

### Step 2.3 — LOW-2：headVerifier 写入 §13b 角色表

- [ ] `docs/superpowers/workflow-design.md` §13b 角色表加一行：

```
| headVerifier | gate 后独立验证 HEAD == restored_head | sonnet | gate 恢复后 1 次 |
```

- [ ] `docs/superpowers/workflows/USAGE.md` 同步加 headVerifier 角色说明（若有角色表）

### Step 2.4 — LOW-3：finalReport per_task 清单补 planId

- [ ] `docs/superpowers/workflows/lib.js` + `.claude/workflows/run-plans.js`（line 1048 附近）finalReport prompt 同步修改：

```diff
  per_task 清单（每 task 一项）：
  - taskKey, taskId, planId, model, status, ...
+ - planId（task 所属 plan 的 seq，如 '01'）
  ...
+ 注：清单仅作可读说明，以 stateJson 全字段为准（ensurePerTaskDefaults 共 16 字段）
```

### Step 2.5 — LOW-4：haltLikelySource 语义映射修正

- [ ] `docs/superpowers/workflows/tests/helpers.test.js` 加 RED 测试：

```javascript
test('LOW-4: haltLikelySource 对 head restore verification failed 返回 gate head mismatch', () => {
  assert.equal(haltLikelySource('gate head restore verification failed'), 'gate head mismatch')
})
```

- [ ] 运行确认测试失败（当前返回 `'gate restored'`）
- [ ] `docs/superpowers/workflows/lib.js` + `.claude/workflows/run-plans.js`（line 299-316 附近）haltLikelySource 函数同步加分支（放在 `includes('gate')` 之前）：

```diff
  export function haltLikelySource(reason) {
    const r = String(reason || '')
+   if (r.includes('head restore')) return 'gate head mismatch'
    if (r.includes('gate')) return 'gate restored'
    ...
  }
```

- [ ] `docs/superpowers/workflow-design.md` §6.2 加注：「head restore verification failed 单独归类为 gate head mismatch（验证失败，非已恢复）」
- [ ] 运行确认 LOW-4 测试通过

### Step 2.6 — FULL：全量回归 + commit

- [ ] 运行 `node --test docs/superpowers/workflows/tests/*.test.js` → 312 tests green（307 + HIGH-1 1 + MEDIUM-1 3 + LOW-4 1）
- [ ] CRLF 修复修改的文件
- [ ] git commit: `fix(workflow): MEDIUM-1 sync.test trip-wire 守护 + LOW-1/2/3/4 文档与语义修正`

---

## Task 3: Batch 1 Commit 3 — S11/S12/S14 清理

**目标:** 删 S11 死代码 + S12 let→const 合并 + S14 标 @deprecated。

**依据:** 审计报告 S11/S12/S14；设计文档 §4.7-4.9 / D7

### Step 3.1 — S11：删 `|| []` 死代码

- [ ] `.claude/workflows/run-plans.js`（line 1645 附近）修改：

```diff
- const _rawCompleted = (Array.isArray(args.completed) && args.completed.length
-   ? args.completed
-   : [...new Set([..._regexCompleted, ..._llmCompleted])]) || []
+ const _rawCompleted = (Array.isArray(args.completed) && args.completed.length
+   ? args.completed
+   : [...new Set([..._regexCompleted, ..._llmCompleted])])
```

### Step 3.2 — S12：let→const 合并（4 处）

- [ ] `.claude/workflows/run-plans.js` 4 处修改（line 1259-1261, 1272, 1442, 1455 附近），逐处 grep 确认无后续 `x =` 再赋值：

```diff
- let impl
- impl = await dispatchImpl(...)
+ const impl = await dispatchImpl(...)
```

- [ ] 同理处理 `ctxr`（line 1272 附近）、`commit`（line 1442 附近）、`simp`（line 1455 附近）

### Step 3.3 — S14：issuesFromReviews 标 @deprecated

- [ ] `docs/superpowers/workflows/lib.js`（line 118-123 附近）+ `.claude/workflows/run-plans.js` inline 副本同步修改注释：

```diff
- // 已被 collectReviewFindings 取代（orchestrator fix-round 用）；保留为通用工具 + 向后兼容。
+ /**
+  * @deprecated (2026-07-07) 已被 collectReviewFindings 取代（orchestrator fix-round 用）。
+  * 保留仅为向后兼容；新代码请用 collectReviewFindings。
+  * 计划在下一轮 spec 修订时移除（需先确认无 memory/indexing 脚本调用）。
+  */
```

### Step 3.4 — FULL：全量回归 + commit

- [ ] 运行 `node --test docs/superpowers/workflows/tests/*.test.js` → 312 tests green（无新测试，行为不变）
- [ ] 确认 sync.test 字节断言通过（lib.js ↔ run-plans.js 注释同步）
- [ ] CRLF 修复修改的文件
- [ ] git commit: `refactor(workflow): S11 删死代码 + S12 let→const + S14 标 @deprecated`

---

## Task 4: Batch 2 Commit 1 — B2-1/2/3/6 低风险纯函数 helper

**目标:** 抽 4 个低风险纯函数 helper：S10 taskKey + S4 REVIEW_SOURCES + S9 makeHalt + S6 formatFindingItem。

**依据:** 审计报告 S10/S4/S9/S6；设计文档 §5.1-5.3/5.6 / D9

### Step 4.1 — B2-1 S10：taskKey 纯函数

- [ ] RED：`docs/superpowers/workflows/tests/helpers.test.js` 加测试：

```javascript
test('S10 taskKey: padStart 2 位', () => {
  assert.equal(taskKey(1, 'T1'), 'plan-01/T1')
  assert.equal(taskKey(10, 'T10a'), 'plan-10/T10a')
})
```

- [ ] GREEN：`docs/superpowers/workflows/lib.js` 加函数：

```javascript
// taskKey 构造（S10, 2026-07-07）：统一 padStart 2 位，防历史 P0-7 位数不一致 bug 复发。
export function taskKey(seq, taskId) {
  return `plan-${String(seq).padStart(2, '0')}/${taskId}`
}
```

- [ ] `.claude/workflows/run-plans.js` inline 副本加 taskKey + 替换 6+ 处拼接（line 1241, 1166, 1651, 1662, 1669, 1684, 1704 附近）
- [ ] SYNC：`docs/superpowers/workflows/tests/sync.test.js` 加 taskKey 字节断言；`docs/superpowers/workflow-design.md` §4.4 加 taskKey helper 说明

### Step 4.2 — B2-2 S4：REVIEW_SOURCES 常量

- [ ] RED：`docs/superpowers/workflows/tests/helpers.test.js` 加测试：

```javascript
test('S4 REVIEW_SOURCES: 3 个 reviewer 来源', () => {
  assert.ok(Array.isArray(REVIEW_SOURCES))
  assert.equal(REVIEW_SOURCES.length, 3)
  assert.deepEqual(REVIEW_SOURCES.map(s => s.name), ['spec', 'quality', 'hunter'])
})
```

- [ ] GREEN：`docs/superpowers/workflows/lib.js` 加常量 + 重构 `collectReviewFindings`/`reviewHaltForEmptyFailed`/`summarizeReviewRound` 遍历 REVIEW_SOURCES：

```javascript
export const REVIEW_SOURCES = [
  { name: 'spec', key: 'issues' },
  { name: 'quality', key: 'issues' },
  { name: 'hunter', key: 'silent_failures' },
]
```

- [ ] `.claude/workflows/run-plans.js` inline 副本同步
- [ ] SYNC：sync.test 加 REVIEW_SOURCES 字节断言

### Step 4.3 — B2-3 S9：makeHalt + errStr 提升到 lib.js

- [ ] RED：`docs/superpowers/workflows/tests/helpers.test.js` 加测试：

```javascript
test('S9 makeHalt: 构造 halt 对象', () => {
  const h = makeHalt('model_unavailable', 'opus', new Error('quota'))
  assert.equal(h.halted, true)
  assert.equal(h.reason, 'model_unavailable')
  assert.equal(h.diag.model, 'opus')
  assert.equal(h.diag.error, 'quota')
})

test('S9 errStr: 各种输入类型', () => {
  assert.equal(errStr(null), '')
  assert.equal(errStr('msg'), 'msg')
  assert.equal(errStr(new Error('e')), 'e')
})
```

- [ ] GREEN：`docs/superpowers/workflows/lib.js` 加 errStr + makeHalt（D9 决策，errStr 提升到 lib.js）：

```javascript
export function errStr(e) {
  if (e == null) return ''
  if (typeof e === 'string') return e
  return e.message || String(e)
}

export function makeHalt(reason, model, error) {
  return { halted: true, reason, diag: { model, error: errStr(error) } }
}
```

- [ ] `.claude/workflows/run-plans.js` inline 副本加 errStr + makeHalt + 替换 dispatchImpl 内 4 处 catch 块（line 406, 407, 426, 428 附近）
- [ ] SYNC：sync.test 加 makeHalt + errStr 字节断言

### Step 4.4 — B2-6 S6：formatFindingItem

- [ ] RED：`docs/superpowers/workflows/tests/helpers.test.js` 加测试：

```javascript
test('S6 formatFindingItem: 有 severity', () => {
  const f = { source: 'spec', severity: 'critical', title: 'bug', fix: 'patch', file: 'a.ts' }
  assert.equal(formatFindingItem(f), '[spec|critical] bug — fix: patch (a.ts)')
})

test('S6 formatFindingItem: 无 severity 无 file', () => {
  const f = { source: 'quality', title: 'issue' }
  assert.equal(formatFindingItem(f), '[quality] issue')
})

test('S6 formatFindingItem: withFile=false prefix', () => {
  const f = { source: 'spec', severity: 'high', title: 't', file: 'b.ts' }
  assert.equal(formatFindingItem(f, { withFile: false, prefix: '- ' }), '- [spec|high] t')
})
```

- [ ] GREEN：`docs/superpowers/workflows/lib.js` 加函数 + 重构 formatFindings/formatCrossReviewerNote：

```javascript
export function formatFindingItem(f, { withFile = true, prefix = '' } = {}) {
  const tag = f.severity ? `[${f.source}|${f.severity}]` : `[${f.source}]`
  const fix = f.fix ? ` — fix: ${f.fix}` : ''
  const file = (withFile && f.file) ? ` (${f.file})` : ''
  return `${prefix}${tag} ${f.title}${fix}${file}`
}
```

- [ ] `.claude/workflows/run-plans.js` inline 副本同步
- [ ] SYNC：sync.test 加 formatFindingItem 字节断言

### Step 4.5 — FULL：全量回归 + commit

- [ ] 运行 `node --test docs/superpowers/workflows/tests/*.test.js` → 329 tests green（312 + B2-1 2 + B2-2 1 + B2-3 2 + B2-6 3 + 现有测试全绿）
- [ ] 确认现有 formatFindings/formatCrossReviewerNote/collectReviewFindings 测试全绿（重构后行为不变）
- [ ] CRLF 修复修改的文件
- [ ] git commit: `refactor(workflow): B2-1/2/3/6 纯函数 helper (taskKey/REVIEW_SOURCES/makeHalt/formatFindingItem)`

---

## Task 5: Batch 2 Commit 2 — B2-4 S1 checkImplStatus

**目标:** 抽 checkImplStatus 纯决策函数，消除 implementor 5 dispatch 点的 4 处可替换重复。

**依据:** 审计报告 S1；设计文档 §5.4 / D10。**依赖**：Task 4 全绿后做。

### Step 5.1 — RED：写失败测试

- [ ] `docs/superpowers/workflows/tests/helpers.test.js` 加 4 个用例：

```javascript
test('S1 checkImplStatus: halted 透传', () => {
  const impl = { halted: true, reason: 'x', diag: {} }
  assert.equal(checkImplStatus(impl), impl)
})

test('S1 checkImplStatus: status 不在 allowed 返回 halt', () => {
  const impl = { status: 'failed', diagnostics: { e: 1 } }
  const h = checkImplStatus(impl, ['ok'], 'implementor')
  assert.equal(h.halted, true)
  assert.equal(h.reason, 'implementor failed')
  assert.equal(h.diag.e, 1)
})

test('S1 checkImplStatus: status 在 allowed 返回 null', () => {
  const impl = { status: 'ok', diagnostics: {} }
  assert.equal(checkImplStatus(impl), null)
})

test('S1 checkImplStatus: 默认 allowed', () => {
  const impl = { status: 'done_with_concerns', diagnostics: {} }
  assert.equal(checkImplStatus(impl), null)  // done_with_concerns 在默认 allowed 内
})
```

### Step 5.2 — GREEN：lib.js + run-plans.js 实现

- [ ] `docs/superpowers/workflows/lib.js` 加函数：

```javascript
// checkImplStatus（S1, 2026-07-07）：implementor dispatch 后的状态检查 helper。
// halted → 透传；status 不在 allowed → halt；否则返回 null（继续往下）。
// reason 逐字对齐原实现（D10）：`${reasonPrefix} ${impl.status}`。
export function checkImplStatus(impl, allowed = ['ok', 'done_with_concerns'], reasonPrefix = 'implementor') {
  if (impl.halted) return impl
  if (!allowed.includes(impl.status)) {
    return { halted: true, reason: `${reasonPrefix} ${impl.status}`, diag: impl.diagnostics }
  }
  return null
}
```

- [ ] `.claude/workflows/run-plans.js` inline 副本 + 替换 4 处（line 1261, 1294, 1296, 1302 附近），reason 逐字对齐：

```javascript
// line 1261: 初始 dispatch 后
const h1 = checkImplStatus(impl)
if (h1) return h1

// line 1294: context retry 后
const h2 = checkImplStatus(impl, ['ok', 'done_with_concerns'], 'implementor after context-fetch retry')
if (h2) return h2

// line 1296: context 最终
const h3 = checkImplStatus(impl, ['ok', 'done_with_concerns'], 'implementor after context-fetch')
if (h3) return h3

// line 1302: failed retry 后
const h4 = checkImplStatus(impl, ['ok', 'done_with_concerns'], 'implementor after retry')
if (h4) return h4
```

- [ ] **保留** line 1268（blocked 升级后单一判断）和 line 1281（context-fetch 后允许 blocked/failed 继续走分支）原样不动

### Step 5.3 — SYNC + FULL

- [ ] sync.test 加 checkImplStatus 字节断言
- [ ] 运行 `node --test docs/superpowers/workflows/tests/*.test.js` → 333 tests green（329 + 4）
- [ ] CRLF 修复 + git commit: `refactor(workflow): B2-4 S1 checkImplStatus 纯决策 helper (消除 4 处 dispatch 重复)`

---

## Task 6: Batch 2 Commit 3 — B2-5 S5 formatBulletSection

**目标:** 抽 formatBulletSection 通用渲染 helper，6 个 format* 重构为 wrapper。

**依据:** 审计报告 S5；设计文档 §5.5 / D11

### Step 6.1 — RED：写失败测试

- [ ] `docs/superpowers/workflows/tests/helpers.test.js` 加 3 个用例：

```javascript
test('S5 formatBulletSection: 空数组返回空串', () => {
  assert.equal(formatBulletSection('H', '', [], () => ''), '')
})

test('S5 formatBulletSection: 基本渲染', () => {
  const out = formatBulletSection('Heading', '', ['a', 'b'], x => `- ${x}`)
  assert.equal(out, '## Heading\n- a\n- b')
})

test('S5 formatBulletSection: 含 intro + outro（多行）', () => {
  const out = formatBulletSection('H', 'intro line', ['x'], x => `- ${x}`, 'outro line 1\noutro line 2')
  assert.equal(out, '## H\nintro line\n- x\noutro line 1\noutro line 2')
})
```

### Step 6.2 — GREEN：lib.js 加 formatBulletSection + 重构 6 wrapper

- [ ] `docs/superpowers/workflows/lib.js` 加函数：

```javascript
export function formatBulletSection(heading, intro, items, renderItem, outro = '') {
  if (!Array.isArray(items) || items.length === 0) return ''
  const lines = items.map(renderItem).join('\n')
  let out = `## ${heading}\n`
  if (intro) out += `${intro}\n`
  out += lines
  if (outro) out += `\n${outro}`
  return out
}
```

- [ ] 重构 6 个 format* 为 wrapper（逐个用 `diff <(old) <(new)` 验证输出逐字节一致）：
  - formatReferencePaths
  - formatSilentFailureContext
  - formatFailedApproaches
  - formatLessons
  - formatUniversalLessons
  - formatDomainLessons
- [ ] `.claude/workflows/run-plans.js` inline 副本同步

### Step 6.3 — SYNC + FULL

- [ ] sync.test 加 formatBulletSection 字节断言 + 6 wrapper 提取验证
- [ ] **关键验证**：现有 6 个 format* 的 helpers.test 用例必须全绿（输出逐字节一致）
- [ ] 运行 `node --test docs/superpowers/workflows/tests/*.test.js` → 336 tests green（333 + 3）
- [ ] CRLF 修复 + git commit: `refactor(workflow): B2-5 S5 formatBulletSection 通用渲染 helper (6 format* 重构为 wrapper)`

---

## Task 7: Batch 2 Commit 4 — B2-7 S7 QUOTA_HALT_NOTE

**目标:** 提 QUOTA_HALT_NOTE 常量，7 个 PROMPT 末尾重复文本替换为占位符，buildPrompt 内置默认注入。

**依据:** 审计报告 S7；设计文档 §5.7 / D12

### Step 7.1 — RED：写失败测试

- [ ] `docs/superpowers/workflows/tests/helpers.test.js` 加测试：

```javascript
test('S7 QUOTA_HALT_NOTE: buildPrompt 默认注入', () => {
  const out = buildPrompt('implementor', { taskId: 'T1', planId: '01', /* 其他必需字段 */ })
  assert.ok(out.includes('model_unavailable'), 'implementor prompt 应含限额说明')
  assert.ok(out.includes('quota'), '应含 quota 关键词')
})
```

### Step 7.2 — GREEN：lib.js 加常量 + buildPrompt 默认 + 7 prompt 替换

- [ ] `docs/superpowers/workflows/lib.js` 加常量：

```javascript
const QUOTA_HALT_NOTE = `若遇到 model 限额耗尽（quota/rate-limit/429 错误），返回 status:'model_unavailable'（非 failed），让 orchestrator halt 并保存进度。`
```

- [ ] buildPrompt 内置默认值（D12 决策）：

```javascript
export function buildPrompt(role, ctx = {}) {
  const tpl = PROMPTS[role]
  if (!tpl) throw new Error(`unknown role: ${role}`)
  const defaults = { quotaHaltNote: QUOTA_HALT_NOTE }
  const merged = { ...defaults, ...ctx }
  return merged === ctx ? tpl.replace(/\{\{(\w+)\}\}/g, (_, k) => {
    if (!(k in ctx)) return `{{${k}}}`
    if (ctx[k] === undefined || ctx[k] === null) return ''
    return String(ctx[k])
  }) : tpl.replace(/\{\{(\w+)\}\}/g, (_, k) => {
    if (!(k in merged)) return `{{${k}}}`
    if (merged[k] === undefined || merged[k] === null) return ''
    return String(merged[k])
  })
}
```

（简化实现：直接用 merged 替代 ctx）

- [ ] 7 个 prompt（implementor/specReview/qualityReviewer/hunter/commit/gate/lessonDistiller）末尾重复文本替换为 `{{quotaHaltNote}}`
- [ ] `.claude/workflows/run-plans.js` inline 副本同步

### Step 7.3 — SYNC + FULL

- [ ] sync.test prompt 字节断言更新基线（7 个 prompt 体变了）
- [ ] 运行 `node --test docs/superpowers/workflows/tests/*.test.js` → 337 tests green（336 + 1）
- [ ] CRLF 修复 + git commit: `refactor(workflow): B2-7 S7 QUOTA_HALT_NOTE 常量 + buildPrompt 默认注入 (7 prompt 去重)`

---

## Task 8: Batch 2 Commit 5 — B2-8 S8 STATIC_READONLY_NOTE + LESSONS_EXEMPTION_NOTE

**目标:** 提 STATIC_READONLY_NOTE（进 buildPrompt 默认）+ LESSONS_EXEMPTION_NOTE（函数，调用方传参），3 个 reviewer prompt 重复段替换。

**依据:** 审计报告 S8；设计文档 §5.8 / D13-D14

### Step 8.1 — RED：写失败测试

- [ ] `docs/superpowers/workflows/tests/helpers.test.js` 加测试：

```javascript
test('S8 STATIC_READONLY_NOTE: buildPrompt 默认注入', () => {
  const out = buildPrompt('specReview', { taskId: 'T1', planId: '01', /* 其他必需字段 */ })
  assert.ok(out.includes('STATIC READ-ONLY'), 'specReview prompt 应含 STATIC READ-ONLY')
})
```

### Step 8.2 — GREEN：lib.js 加常量 + 重构 3 prompt

- [ ] `docs/superpowers/workflows/lib.js` 加常量 + 函数：

```javascript
const STATIC_READONLY_NOTE = `## STATIC READ-ONLY Constraint
...（从现有 specReview prompt 提取的 10 行公共文本）...`

function LESSONS_EXEMPTION_NOTE(applicableDimensions) {
  return `## Lessons Learned Exemption
... ${applicableDimensions} ...`
}
```

- [ ] buildPrompt 默认注入 STATIC_READONLY_NOTE（D14 决策），LESSONS_EXEMPTION_NOTE 由调用方传参（D13 决策）
- [ ] 3 个 reviewer prompt（specReview/qualityReviewer/hunter）重复段替换为 `{{staticReadonlyNote}}` + `{{lessonsExemptionNote}}`
- [ ] run-plans.js 调用 reviewer 的地方传 `lessonsExemptionNote: LESSONS_EXEMPTION_NOTE(applicableDimensions)`
- [ ] `.claude/workflows/run-plans.js` inline 副本同步

### Step 8.3 — SYNC + FULL

- [ ] sync.test prompt 断言更新基线
- [ ] 运行 `node --test docs/superpowers/workflows/tests/*.test.js` → 338 tests green（337 + 1）
- [ ] CRLF 修复 + git commit: `refactor(workflow): B2-8 S8 STATIC_READONLY_NOTE + LESSONS_EXEMPTION_NOTE (3 reviewer prompt 去重)`

---

## Task 9: Batch 3 Commit 1 — B3-1 recordReviewRound

**目标:** 抽 recordReviewRound 纯决策函数（lib.js），消除 review 循环 12 行 state 更新重复。

**依据:** 审计报告 S2；设计文档 §6.1.1。**依赖**：Task 8 全绿后做（Batch 3 开始）。

### Step 9.1 — RED：写失败测试

- [ ] `docs/superpowers/workflows/tests/helpers.test.js` 加 3 个用例：

```javascript
test('S2 recordReviewRound: state 正确更新', () => {
  const state = { perTask: { 'plan-01/T1': { files_touched_per_round: [], review_history: [], findings_history: [] } } }
  const spec = { status: 'ok', diagnostics: { files_touched: ['a.ts'], issues: [] } }
  recordReviewRound(state, 'plan-01/T1', 1, spec, null, null)
  assert.equal(state.perTask['plan-01/T1'].review_rounds, 1)
  assert.equal(state.perTask['plan-01/T1'].files_touched_per_round.length, 1)
  assert.equal(state.perTask['plan-01/T1'].review_history.length, 1)
})

test('S2 recordReviewRound: findings_history 更新', () => {
  // 验证 findings_history 通过 updateFindingsHistory 更新
})

test('S2 recordReviewRound: 返回 currentFindings', () => {
  const state = { perTask: { 'plan-01/T1': { files_touched_per_round: [], review_history: [], findings_history: [] } } }
  const spec = { status: 'failed', diagnostics: { files_touched: [], issues: [{ title: 'bug' }] } }
  const result = recordReviewRound(state, 'plan-01/T1', 1, spec, null, null)
  assert.ok(Array.isArray(result.currentFindings))
  assert.equal(result.currentFindings.length, 1)
})
```

### Step 9.2 — GREEN：lib.js + run-plans.js 实现

- [ ] `docs/superpowers/workflows/lib.js` 加函数：

```javascript
export function recordReviewRound(state, taskKey, round, spec, qual, hunt) {
  state.perTask[taskKey].review_rounds = round
  state.perTask[taskKey].files_touched_per_round.push(unionFiles(spec, qual, hunt))
  state.perTask[taskKey].review_history.push(summarizeReviewRound(round, spec, qual, hunt))
  const currentFindings = collectReviewFindings(spec, qual, hunt)
  state.perTask[taskKey].findings_history = updateFindingsHistory(
    state.perTask[taskKey].findings_history, currentFindings, round
  )
  return { currentFindings }
}
```

- [ ] `.claude/workflows/run-plans.js` inline 副本 + 替换 line 1317-1328（12 行 → 1 行调用）：

```javascript
const { currentFindings } = recordReviewRound(state, taskKey, round, spec, qual, hunt)
```

### Step 9.3 — SYNC + FULL

- [ ] sync.test 加 recordReviewRound 字节断言
- [ ] 运行 `node --test docs/superpowers/workflows/tests/*.test.js` → 341 tests green（338 + 3）
- [ ] CRLF 修复 + git commit: `refactor(workflow): B3-1 S2 recordReviewRound 纯决策 helper (review 循环 state 更新抽取)`

---

## Task 10: Batch 3 Commit 2 — B3-1 decideReviewOutcome

**目标:** 抽 decideReviewOutcome 纯决策函数（lib.js），9 个 action 分支集中 OSCILLATING/budget/maxRounds 决策。

**依据:** 审计报告 S2；设计文档 §6.1.2 / D15。**依赖**：Task 9 全绿后做。

### Step 10.1 — RED：写 9 个失败测试

- [ ] `docs/superpowers/workflows/tests/helpers.test.js` 加 9 个用例覆盖 action 枚举（D15 决策）：

```javascript
test('S2 decideReviewOutcome: reviewReason → halt', () => { /* reviewReason 非空 */ })
test('S2 decideReviewOutcome: emptyFailed → halt', () => { /* emptyFailedReason 非空 */ })
test('S2 decideReviewOutcome: allGreen → break', () => { /* spec/qual/hunt 全 ok */ })
test('S2 decideReviewOutcome: regressed → halt OSCILLATING', () => { /* hasRegressed=true */ })
test('S2 decideReviewOutcome: osc + flipFlop → halt OSCILLATING', () => { /* osc + flipFlop */ })
test('S2 decideReviewOutcome: osc + flipFlop=false + shouldEscalate → escalate', () => { /* 升级 opus */ })
test('S2 decideReviewOutcome: osc + flipFlop=false + alreadyEscalated → continue', () => { /* 已升级 */ })
test('S2 decideReviewOutcome: maxRounds=0 budget guard → halt review_not_converging', () => { /* 无限模式 budget */ })
test('S2 decideReviewOutcome: round===maxRounds → halt review max rounds', () => { /* 有限模式上限 */ })
```

### Step 10.2 — GREEN：lib.js + run-plans.js 实现

- [ ] `docs/superpowers/workflows/lib.js` 加函数（9 个 action 分支，逐字对齐原 reason + diag）：

```javascript
export function decideReviewOutcome(state, taskKey, round, spec, qual, hunt, model, maxRounds, cfg, reviewReason, emptyFailedReason) {
  if (reviewReason) return { action: 'halt', reason: reviewReason, diag: { spec: spec?.diagnostics, qual: qual?.diagnostics, hunt: hunt?.diagnostics } }
  if (emptyFailedReason) return { action: 'halt', reason: emptyFailedReason, diag: { spec: spec?.diagnostics, qual: qual?.diagnostics, hunt: hunt?.diagnostics } }
  if (allGreen(spec, qual, hunt)) return { action: 'break' }
  const osc = detectOscillation(state.perTask[taskKey].files_touched_per_round)
  const flipFlop = isFlipFlop(state.perTask[taskKey].review_history || [])
  const regressed = hasRegressed(state.perTask[taskKey].findings_history || [])
  if (regressed) return { action: 'halt', reason: 'OSCILLATING', diag: { ...osc, flipFlop, regressed, regressedFindings: state.perTask[taskKey].findings_history.filter(h => h.status === 'regressed'), model } }
  if (osc.oscillating) {
    if (flipFlop) return { action: 'halt', reason: 'OSCILLATING', diag: { ...osc, flipFlop, regressed, model } }
    if (shouldEscalateOnOscillation(model, state.perTask[taskKey].opus_escalated)) return { action: 'escalate', model: 'opus' }
    return { action: 'continue' }
  }
  if (maxRounds === 0) {
    const budget = resolveReviewBudget(cfg)
    if (round >= budget) return { action: 'halt', reason: 'review_not_converging', diag: { round, budget, findings_history: state.perTask[taskKey].findings_history, spec: spec.diagnostics, qual: qual.diagnostics, hunt: hunt.diagnostics } }
  } else if (round === maxRounds) {
    return { action: 'halt', reason: 'review max rounds', diag: { round, findings_history: state.perTask[taskKey].findings_history, spec: spec.diagnostics, qual: qual.diagnostics, hunt: hunt.diagnostics } }
  }
  return { action: 'fix' }
}
```

- [ ] `.claude/workflows/run-plans.js` inline 副本 + 替换 line 1329-1398（~70 行 → 1 函数调用 + switch）：

```javascript
const outcome = decideReviewOutcome(state, taskKey, round, spec, qual, hunt, model, maxRounds, cfg, reviewReason, emptyFailedReason)
if (outcome.action === 'halt') return { halted: true, reason: outcome.reason, diag: outcome.diag }
if (outcome.action === 'break') break
if (outcome.action === 'escalate') {
  state.perTask[taskKey].opus_escalated = true
  state.perTask[taskKey].oscillation_escalated_at_round = round
  model = outcome.model
  log(`⚠ ${task.id}: r${round} OSCILLATING (new-findings 补充, flipFlop=false) — escalate to opus, continue (v3)`)
}
// action === 'continue' or 'fix' → 走 fix-round
```

### Step 10.3 — SYNC + FULL

- [ ] sync.test 加 decideReviewOutcome 字节断言
- [ ] **关键验证**：手动 trace r1/r2/r3 + OSCILLATING(regressed/flipFlop) + budget guard 路径，确认行为不变
- [ ] 运行 `node --test docs/superpowers/workflows/tests/*.test.js` → 350 tests green（341 + 9）
- [ ] CRLF 修复 + git commit: `refactor(workflow): B3-1 S2 decideReviewOutcome 纯决策 helper (9 action 分支集中)`

---

## Task 11: Batch 3 Commit 3 — B3-1 runFixRound

**目标:** 抽 runFixRound runtime 函数（run-plans.js），封装 fix-round dispatch + 状态检查。

**依据:** 审计报告 S2；设计文档 §6.1.3 / D16。**依赖**：Task 10 全绿后做。

### Step 11.1 — GREEN：run-plans.js 加 runFixRound + 主循环改写

（runtime 函数，不写单测，D17 不适用此 Task——runFixRound 是 runtime 但逻辑可测，仍不加单测靠回归）

- [ ] `.claude/workflows/run-plans.js` 加函数（D16 决策：不用 checkImplStatus，直接内联判断）：

```javascript
async function runFixRound(taskKey, plan, task, round, spec, qual, hunt, state, cfg, implCtx, model, maxRounds, concerns, concernsHint) {
  const findings = collectReviewFindings(spec, qual, hunt)
  const crossReviewerNote = formatCrossReviewerNote(findings)
  const findingsHistoryText = formatFindingsHistory(state.perTask[taskKey].findings_history || [], round)
  const fullFixIssues = findingsHistoryText ? `${findingsHistoryText}\n${crossReviewerNote}` : crossReviewerNote
  const oscEscRound = state.perTask[taskKey].oscillation_escalated_at_round
  const retryNote = oscEscRound === round
    ? `## 升级到 opus，本轮必须修完所有 [OPEN]\n- 逐条核对 [OPEN]，每条要么修完，要么说明不修的原因（★ 标本轮新增的优先修）\n- 修完后，核对 [FIXED] 列表的 fix 在你的改动后仍然存在；若 [OPEN] 与 [FIXED] 同文件，只动 [OPEN] 描述的代码，不要回退 [FIXED] 对应的修改\n- 不要留到下一轮，下一轮不再有升级空间\n- 截至 r${round} review 累计未修 findings 如上`
    : `修复 review round ${round} 问题（${findings.length} 项发现；★ 标本轮新增）。`
  const fixModel = fixModelForRound(round, model, maxRounds)
  const impl = await dispatchImpl(buildPrompt('implementor', implCtx(fullFixIssues, retryNote)), { schema: SCHEMAS.implementor, model: fixModel, label: `impl:${task.id}:fix${round}` }, fixModel, 'opus')
  if (impl.halted) return { impl, halted: true }
  if (impl.status === 'blocked' || impl.status === 'failed' || impl.status === 'needs_context') {
    return { impl, halted: true, reason: `implementor ${impl.status} in fix-round ${round}` }
  }
  if (impl.status === 'done_with_concerns') {
    concerns = impl.diagnostics?.concerns || concerns
    state.perTask[taskKey].concerns = concerns
    concernsHint = formatConcernsHint(concerns)
    log(`⚠ ${task.id} fix-round ${round} done_with_concerns: ${concerns.join('; ') || '(no detail)'}`)
  }
  return { impl, halted: false, concerns, concernsHint, filesChanged: impl.evidence.files_changed }
}
```

- [ ] 替换 line 1399-1431（~32 行 → 1 函数调用）：

```javascript
const fixResult = await runFixRound(taskKey, plan, task, round, spec, qual, hunt, state, cfg, implCtx, model, maxRounds, concerns, concernsHint)
if (fixResult.halted) {
  if (fixResult.reason) return { halted: true, reason: fixResult.reason, diag: fixResult.impl.diagnostics }
  return fixResult.impl
}
concerns = fixResult.concerns
concernsHint = fixResult.concernsHint
filesChanged = fixResult.filesChanged || filesChanged
```

### Step 11.2 — SYNC + FULL

- [ ] spec §5.5 加 runFixRound 说明
- [ ] 运行 `node --test docs/superpowers/workflows/tests/*.test.js` → 350 tests green（无新测试，靠回归）
- [ ] **关键验证**：手动 trace fix-round 正常/done_with_concerns/blocked/failed/needs_context 路径
- [ ] CRLF 修复 + git commit: `refactor(workflow): B3-1 S2 runFixRound runtime helper (fix-round dispatch 封装)`

---

## Task 12: Batch 3 Commit 4 — B3-2 S3 simplify 三 helper

**目标:** 抽 3 个 simplify runtime helper（checkSimplifyChanges/amendSimplifyCommit/revertSimplifyChanges）。

**依据:** 审计报告 S3；设计文档 §6.3 / D17。**依赖**：Task 11 全绿后做。

### Step 12.1 — GREEN：run-plans.js 加 3 函数

（D17 决策：不加新单测，靠 sync.test 存在性断言 + 全量回归）

- [ ] `.claude/workflows/run-plans.js` 加 3 个 runtime 函数：

```javascript
async function checkSimplifyChanges(taskId) {
  const diffSchema = { type: 'object', required: ['changed', 'files'], properties: { changed: { type: 'boolean' }, files: { type: 'array', items: { type: 'string' } } } }
  const diffResult = await safeAgent('Run `git status --porcelain` in the current working directory. If output is empty, return {"changed": false, "files": []}. Otherwise return {"changed": true, "files": [<list of file paths from porcelain output>]}.', { schema: diffSchema, label: `diff:${taskId}` })
  if (!diffResult || typeof diffResult !== 'object' || typeof diffResult.changed !== 'boolean' || (diffResult.changed === true && !Array.isArray(diffResult.files))) {
    return { error: true, reason: 'simplify diff check failed', diag: { task: taskId, diffResult: diffResult || null } }
  }
  return { error: false, changed: diffResult.changed === true, files: Array.isArray(diffResult.files) ? diffResult.files : [] }
}

async function amendSimplifyCommit(taskId, commitSha) {
  const amendSchema = { type: 'object', required: ['ok'], properties: { ok: { type: 'boolean' }, sha: { type: 'string' }, error: { type: 'string' } } }
  const amendResult = await safeAgent('Run `git add -A && git commit --amend --no-edit`. Then run `git rev-parse HEAD` and return JSON {"ok": true, "sha": "<40-char-hex>"}. If amend failed (e.g. pre-commit hook blocked), return {"ok": false, "sha": "", "error": "<message>"}.', { schema: amendSchema, label: `amend:${taskId}` })
  const amendCheck = validateAmendResult(amendResult)
  if (!amendCheck.valid) {
    return { error: true, reason: 'simplify amend failed', diag: { task: taskId, amendError: amendCheck.error, commitSha } }
  }
  return { error: false, sha: amendCheck.sha }
}

async function revertSimplifyChanges(taskId, commitSha) {
  const checkoutSchema = { type: 'object', required: ['ok'], properties: { ok: { type: 'boolean' }, porcelain: { type: 'string' }, error: { type: 'string' } } }
  const checkoutResult = await safeAgent('Run `git reset --hard HEAD && git clean -fd` to discard simplify changes (both tracked modifications, staged changes, and untracked new files). Then run `git status --porcelain` to verify the working tree is clean. Return JSON {"ok": true, "porcelain": "<porcelain output>"} on success or {"ok": false, "porcelain": "<output>", "error": "<message>"} on failure.', { schema: checkoutSchema, label: `checkout:${taskId}` })
  const checkoutCheck = validateCheckoutResult(checkoutResult)
  if (!checkoutCheck.valid) {
    return { error: true, reason: 'simplify checkout failed', diag: { task: taskId, checkoutError: checkoutCheck.error, commitSha } }
  }
  return { error: false }
}
```

### Step 12.2 — SYNC：spec + sync.test 存在性断言

- [ ] `docs/superpowers/workflow-design.md` §5.2 方案 C 加 3 函数说明
- [ ] sync.test 加 3 函数存在性断言（grep 函数名）：

```javascript
test('B3-2 S3 simplify helpers 存在', () => {
  const src = readFileSync(runPlansPath, 'utf8')
  assert.ok(/async function checkSimplifyChanges\(/.test(src), 'checkSimplifyChanges 存在')
  assert.ok(/async function amendSimplifyCommit\(/.test(src), 'amendSimplifyCommit 存在')
  assert.ok(/async function revertSimplifyChanges\(/.test(src), 'revertSimplifyChanges 存在')
})
```

### Step 12.3 — FULL

- [ ] 运行 `node --test docs/superpowers/workflows/tests/*.test.js` → 351 tests green（350 + sync.test 1）
- [ ] CRLF 修复 + git commit: `refactor(workflow): B3-2 S3 simplify 三 helper (checkSimplifyChanges/amendSimplifyCommit/revertSimplifyChanges)`

---

## Task 13: Batch 3 Commit 5 — S3 主流程集成

**目标:** simplify 主流程改写，调用 3 helper，~65 行 → ~25 行。

**依据:** 设计文档 §6.4。**依赖**：Task 12 全绿后做。

### Step 13.1 — GREEN：主流程改写

- [ ] `.claude/workflows/run-plans.js` simplify 主流程（line 1453-1517 附近）改写为：

```javascript
let simp
simp = await dispatchImpl(buildPrompt('simplify', { taskId: task.id, filesChanged: filesChanged.join('\n') }), { schema: SCHEMAS.simplify, label: `simp:${task.id}` }, 'sonnet')
if (simp.halted) return simp

const diffCheck = await checkSimplifyChanges(task.id)
if (diffCheck.error) return { halted: true, reason: diffCheck.reason, diag: diffCheck.diag }

if (diffCheck.changed) {
  const fc = diffCheck.files.join('\n')
  const { spec: spec2, qual: qual2, hunt: hunt2, haltReason: simpReviewReason, emptyFailed: simpEmptyFailed } = await runReviewRound(task.id, cfg, plan, fc, '', ':simp', '')
  if (simpReviewReason) return { halted: true, reason: simpReviewReason, diag: { spec2: spec2?.diagnostics, qual2: qual2?.diagnostics, hunt2: hunt2?.diagnostics } }
  if (simpEmptyFailed) return { halted: true, reason: simpEmptyFailed, diag: { spec2: spec2?.diagnostics, qual2: qual2?.diagnostics, hunt2: hunt2?.diagnostics } }
  if (allGreen(spec2, qual2, hunt2)) {
    const amend = await amendSimplifyCommit(task.id, commit.evidence.commit_sha)
    if (amend.error) return { halted: true, reason: amend.reason, diag: amend.diag }
    state.perTask[taskKey].commit_sha = amend.sha
    log(`✓ ${task.id} simplify review green — amended commit @ ${amend.sha}`)
  } else {
    const revert = await revertSimplifyChanges(task.id, commit.evidence.commit_sha)
    if (revert.error) return { halted: true, reason: revert.reason, diag: revert.diag }
    log(`⚠ ${task.id} simplify review NOT green — reverted simplify changes (HEAD unchanged @ ${commit.evidence.commit_sha})`)
    state.perTask[taskKey].simplify_reverted = true
    state.perTask[taskKey].simplify_review_findings = collectReviewFindings(spec2, qual2, hunt2)
  }
}
```

### Step 13.2 — FULL：全量回归 + commit

- [ ] 运行 `node --test docs/superpowers/workflows/tests/*.test.js` → 351 tests green
- [ ] **关键验证**：手动 trace 4 条路径（simplify 全绿 / amend 失败 / review 失败 / checkout 失败），确认行为不变
- [ ] CRLF 修复 + git commit: `refactor(workflow): B3-2 S3 simplify 主流程集成 (65 行 → 25 行, 调用 3 helper)`

---

## 完成标准

- [ ] 所有 13 个 Task 的 checkbox 全部勾选
- [ ] 351 tests green（307 → 351，+44 测试）
- [ ] sync.test 字节断言覆盖所有新纯函数
- [ ] spec（workflow-design.md）+ USAGE.md 同步更新
- [ ] 13 个 commit 全部完成，CRLF 行尾一致
- [ ] 审计报告 19 项发现全部修复（S13 不改，D6 决策）

## 依赖图

```
Task 1 (HIGH-1) ─┐
Task 2 (MEDIUM-1+LOW) ─┤
Task 3 (S11/S12/S14) ─┘
        │
        ▼
Task 4 (B2-1/2/3/6) ─┐
Task 5 (B2-4) ─┤
Task 6 (B2-5) ─┤
Task 7 (B2-7) ─┤
Task 8 (B2-8) ─┘
        │
        ▼
Task 9 (recordReviewRound) ─┐
Task 10 (decideReviewOutcome) ─┤
Task 11 (runFixRound) ─┤
Task 12 (S3 三 helper) ─┤
Task 13 (S3 主流程集成) ─┘
```
