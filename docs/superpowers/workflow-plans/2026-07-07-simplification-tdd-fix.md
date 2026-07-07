# Simplification TDD Fix Implementation Plan — run-plans.js 简化与一致性审计修复

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按 [设计文档](../specs/2026-07-07-simplification-tdd-fix-design.md) 修复 [审计报告](../research/run-plans-simplification-audit-2026-07-07.md) 的 16 项发现（S13 不改）+ 1 项复核新增的通用性守护。按风险递增 3 批次 TDD 实施，每项严格遵循 RED→GREEN→SYNC→FULL 4 阶段。

**Architecture:** 两文件模式：`docs/superpowers/workflows/lib.js`（纯函数真源，node:test 可测）+ `.claude/workflows/run-plans.js`（inline 副本 + runtime 胶水，sync.test 字节守护）。纯决策/纯构造函数进 lib.js；runtime 胶水（调 agent()/safeAgent/dispatchImpl）留 run-plans.js；PROMPT 片段常量进 PROMPTS + buildPrompt 注入。

**Tech Stack:** JavaScript (Workflow runtime sandbox), node:test, 307→346 测试叠加

**Spec / 设计依据:**
- `docs/superpowers/specs/2026-07-07-simplification-tdd-fix-design.md`（21 个决策 D1-D21 + D4/D15/D17 复核修正）
- `docs/superpowers/workflows/research/run-plans-simplification-audit-2026-07-07.md`（16 项发现）
- `docs/superpowers/workflow-design.md` §4.3（分层约束）/ §5.5（改进 A）/ §13b（角色表）

**Runtime constraints:**
- §4.3 分层：纯函数 → lib.js + sync 守护；runtime 胶水 → run-plans.js；PROMPT 常量 → PROMPTS + buildPrompt
- 所有简化不得引入 fs/subprocess/Date.now/Math.random
- 所有简化不得改变 agent 调用数
- lib.js 改了的 helper 必须同步 inline 复制到 run-plans.js（sync.test.js 字节守护）

**CRLF（本项目特定约束，非通用 workflow 约束）:** 每批次 commit 前对修改的 .js/.md 执行 `perl -i -pe 's/(?<!\r)\n/\r\n/g' <file>`。这是 lottery-notification 仓库的本地约定（`.gitattributes` 注明 Windows 项目 + sync.test promptBody 正则对行尾敏感）。若移植到其他项目，删除此步骤或改为跟随目标仓库行尾约定。

**全量回归命令:** `node --test docs/superpowers/workflows/tests/*.test.js`（每 commit 前必跑）

**执行顺序:** 严格串行 Batch 1（Task 1-3）→ Batch 2（Task 4-11）→ Batch 3（Task 12-16）。每批次全绿后才进下一批。共 16 个 Task，3+8+5 个 commit。

**测试叠加（修订）:** 307 基线 → Batch 1 +6 (313) → Batch 2 +19 (332) → Batch 3 +14 (346)。注：实际 test() 块计数与 spec §3.3 预估（Batch 2 +17 / Batch 3 +13）略有出入——以 plan 内各 Task 的 test() 块为准，实施时以最终全绿计数为准。

---

## Task 1: Batch 1 Commit 1 — HIGH-1 补全 lesson_categories 提取链

**目标:** 修复 §5.5 改进 A category 精确匹配分支端到端不可达的数据流断裂。bootstrap prompt step 3 加 lesson_categories 提取说明 + Return schema 加字段。

**依据:** 审计报告 HIGH-1；设计文档 §4.1 / D2-D3

**注意:** 测试是**源码字面量断言**（断言 prompt/schema 文本含字段名），非端到端数据流测试。审计报告 HIGH-1 的"分层测试盲区"未真正闭合——只是把盲区从"字段缺失"移到"字段名存在于 prompt/schema 文本"。真正的端到端测试需 mock agent 返回，成本高，留作未来改进。

### Step 1.1 — RED：写失败测试（源码字面量断言）

- [ ] `docs/superpowers/workflows/tests/sync.test.js` 末尾加测试，断言 bootstrap prompt 文本 + Return schema 字符串含 `lesson_categories`：

```javascript
test('HIGH-1: bootstrap prompt step 3 含 lesson_categories 提取说明', () => {
  const boot = promptBody(libSrc, 'bootstrap')
  assert.ok(boot.includes('lesson_categories'),
    'bootstrap prompt 须指示提取 lesson_categories（当前未指示 → category 精确匹配分支端到端不可达）')
})

test('HIGH-1: bootstrap Return schema tasks 含 lesson_categories 字段', () => {
  const boot = promptBody(libSrc, 'bootstrap')
  // Return schema 在 prompt 文本内描述为 tasks:[{id, model, title, ...}]
  assert.match(boot, /tasks:\[\{[^}]*lesson_categories/,
    'bootstrap Return schema tasks 须含 lesson_categories 字段')
})
```

- [ ] 运行 `node --test docs/superpowers/workflows/tests/sync.test.js` 确认两个新测试 FAIL（当前 prompt/schema 不含 lesson_categories）

### Step 1.2 — GREEN：改 bootstrap prompt + schema

- [ ] `docs/superpowers/workflows/lib.js` + `.claude/workflows/run-plans.js` 同步修改 bootstrap prompt step 3（line 789 附近），在 "Return as task_write_files ... Absent → empty array." 之后、"4. git log" 之前加：

```
Also extract `lesson_categories` from frontmatter if present (format: `lesson_categories:\n  - silent-failure\n  - test-strategy`). Return per task as `lesson_categories` array (absent → empty array).
```

- [ ] 同步修改 bootstrap Return schema（line 802 附近 prompt 文本中的 `tasks:[{id, model, title}]`）→ `tasks:[{id, model, title, lesson_categories}]`

- [ ] 运行 `node --test docs/superpowers/workflows/tests/sync.test.js` 确认两个新测试 PASS（prompt/schema 现含 lesson_categories）
- [ ] **关键检查**：sync.test 的 `PROMPTS.bootstrap identical between lib.js and run-plans.js` 测试须 PASS（两端 prompt 体字节一致）

### Step 1.3 — SYNC：spec 同步

- [ ] `docs/superpowers/workflow-design.md` §5.5 改进 A 加注：「plan frontmatter 可声明 `lesson_categories` 启用精确匹配；未声明时 fallback 到 title 关键词匹配（向后兼容）」

### Step 1.4 — FULL：全量回归 + commit

- [ ] 运行 `node --test docs/superpowers/workflows/tests/*.test.js` → 309 tests green（307 + 2）
- [ ] CRLF 修复修改的文件（`perl -i -pe 's/(?<!\r)\n/\r\n/g' lib.js run-plans.js workflow-design.md`）
- [ ] git commit: `fix(workflow): HIGH-1 补全 lesson_categories bootstrap 提取链 (category 精确匹配分支端到端可达)`

---

## Task 2: Batch 1 Commit 2 — MEDIUM-1 守护 + B1-10 通用性守护 + LOW-1/2/3/4 文档修正

**目标:** 加 sync.test 2 个 trip-wire 守护断言（trip-wire 1 已删，见设计文档 D4 复核修正）+ 1 个通用性守护断言 + 修正 4 项 LOW 文档滞后/语义不准。

**依据:** 审计报告 MEDIUM-1/LOW-1/2/3/4；设计文档 §4.2/§4.10/§4.3-4.6 / D4-D5

**TDD 流程说明:** 本 Task 混合两类改动——(1) 守护断言（MEDIUM-1 trip-wire + B1-10 通用性守护）的 TDD 流程是**直接 GREEN**（断言当前代码已满足，无 RED 阶段——这是 spec §4.2/§4.10 的明确决策，守护断言的目标是防止未来回归而非验证当前行为）；(2) LOW-4 haltLikelySource 是标准 RED→GREEN（先写失败测试再修实现）。实施者注意区分：Step 2.1/2.2 是直接 GREEN，Step 2.6 的 LOW-4 是 RED→GREEN。

### Step 2.1 — MEDIUM-1：加 2 个 trip-wire 守护断言（trip-wire 1 已删）

> **注意**：初稿列 3 个 trip-wire，复核发现 trip-wire 1（PROMPTS 反引号成对性）对当前代码就 FAIL（多行模板字面量每行反引号不成对）且方案 A 也有假阳性。删 trip-wire 1，只保留 trip-wire 2/3。详见设计文档 D4 复核修正。

- [ ] `docs/superpowers/workflows/tests/sync.test.js` 加 2 个测试：

```javascript
test('MEDIUM-1 守护：纯函数体不得含顶层 \\n} 子模式（破坏 extractFunctionBody）', () => {
  const fnRegex = /export function (\w+)\([\s\S]*?\{([\s\S]*?)\n\}/g
  let match
  while ((match = fnRegex.exec(libSrc)) !== null) {
    const body = match[2]
    let depth = 0
    for (const ch of body) {
      if (ch === '{') depth++
      else if (ch === '}') depth--
      assert.ok(depth >= 0, `函数 ${match[1]} 体内大括号不平衡（含 \\n} 子模式，破坏 extractFunctionBody）`)
    }
  }
})

test('MEDIUM-1 守护：SCHEMAS 块结尾必须是 \\n}（防 extractSchemas 截断）', () => {
  const m = libSrc.match(/const SCHEMAS = \{[\s\S]*?\n\}/)
  assert.ok(m, 'SCHEMAS 块存在且以 \\n} 结尾')
})
```

- [ ] 运行确认两个 trip-wire 测试 PASS（断言当前代码已满足：55 个 export function 大括号均平衡、SCHEMAS 块以 `\n}` 结尾）

### Step 2.2 — B1-10 通用性守护（复核新增，不计审计 16 项）

- [ ] `docs/superpowers/workflows/tests/sync.test.js` 加测试，防项目耦合混入通用 PROMPTS：

```javascript
test('B1-10 通用性守护：PROMPTS 不得含本项目专有路径/文件名', () => {
  const m = libSrc.match(/const PROMPTS = \{[\s\S]*?\n\}/)
  assert.ok(m, 'PROMPTS 块存在')
  // 项目耦合黑名单（lottery-notification 专有）。项目特定内容应靠 config 驱动注入，
  // 不应硬编码进通用 workflow 的 PROMPTS。
  const blacklist = ['lottery', 'notification', 'lessons.md']
  for (const bad of blacklist) {
    assert.ok(!m[0].toLowerCase().includes(bad.toLowerCase()),
      `PROMPTS 含本项目专有词 "${bad}"——项目耦合，应改 config 驱动注入`)
  }
})
```

- [ ] 运行确认测试 PASS（当前 PROMPTS 用 `{{configPath}}`/`{{plansDir}}` 占位符，无硬编码路径）

### Step 2.3 — LOW-1：fixModelForRound 注释矛盾

- [ ] `docs/superpowers/workflows/lib.js` + `.claude/workflows/run-plans.js`（line 346 附近）同步修改注释。当前注释为：
```javascript
// maxRounds 未传（向后兼容）→ 默认 3（round=2 升级 opus）。已是 opus 返回 'opus'（语义等价）。
```
改为：
```javascript
// maxRounds 未传 → 默认 4（round=3 升级 opus）；?? 3 是防御性兜底，正常路径 resolveMaxRounds 总返回数字。已是 opus 返回 'opus'（语义等价）。
```

- [ ] 保留 `const max = maxRounds ?? 3` 死路径（D5 决策——helpers.test.js:469 直接调 fixModelForRound 不传 maxRounds）

### Step 2.4 — LOW-2：headVerifier 写入 §13b 角色表

- [ ] `docs/superpowers/workflow-design.md` §13b 角色表加一行（放在 gate 行之后）：

```
| headVerifier | gate 后独立验证 HEAD == restored_head | sonnet | gate 恢复后 1 次 |
```

- [ ] `docs/superpowers/workflows/USAGE.md` 同步加 headVerifier 角色说明（若有角色表）

### Step 2.5 — LOW-3：finalReport per_task 清单补 planId

- [ ] `docs/superpowers/workflows/lib.js` + `.claude/workflows/run-plans.js`（line 1048 附近）finalReport prompt 同步修改。找到 per_task 清单描述，确保含 planId + 加注：

```diff
  per_task 清单（每 task 一项）：
  - taskKey, taskId, planId, model, status, ...
  ...
+ 注：清单仅作可读说明，以 stateJson 全字段为准（ensurePerTaskDefaults 共 16 字段）
```

（若清单已含 planId 则只加注；实施时先 grep 确认当前清单内容）

### Step 2.6 — LOW-4：haltLikelySource 语义映射修正

- [ ] RED：`docs/superpowers/workflows/tests/helpers.test.js` 加测试（haltLikelySource 已在 import 列表 line 3）：

```javascript
test('LOW-4: haltLikelySource 对 head restore verification failed 返回 gate head mismatch', () => {
  assert.equal(haltLikelySource('gate head restore verification failed'), 'gate head mismatch')
})
```

- [ ] 运行确认测试 FAIL（当前 `r.includes('gate')` 命中 → 返回 `'gate restored'`）

- [ ] GREEN：`docs/superpowers/workflows/lib.js` + `.claude/workflows/run-plans.js` haltLikelySource 函数同步加分支。当前函数体第一行判断是：
```javascript
if (r === 'plan gate failed' || r.includes('gate')) return 'gate restored'
```
改为（head restore 分支放在 gate 之前）：
```javascript
if (r.includes('head restore')) return 'gate head mismatch'
if (r === 'plan gate failed' || r.includes('gate')) return 'gate restored'
```

- [ ] `docs/superpowers/workflow-design.md` §6.2 加注：「head restore verification failed 单独归类为 gate head mismatch（验证失败，非已恢复）」
- [ ] 运行确认 LOW-4 测试 PASS

### Step 2.7 — FULL：全量回归 + commit

- [ ] 运行 `node --test docs/superpowers/workflows/tests/*.test.js` → 313 tests green（309 + MEDIUM-1 2 + B1-10 1 + LOW-4 1）
- [ ] **关键检查**：sync.test 字节断言通过（lib.js ↔ run-plans.js haltLikelySource 函数体同步）
- [ ] CRLF 修复修改的文件
- [ ] git commit: `fix(workflow): MEDIUM-1 trip-wire 守护(2) + B1-10 通用性守护 + LOW-1/2/3/4 文档与语义修正`

---

## Task 3: Batch 1 Commit 3 — S11/S12/S14 清理

**目标:** 删 S11 死代码 + S12 let→const 合并 + S14 标 @deprecated。

**依据:** 审计报告 S11/S12/S14；设计文档 §4.7-4.9 / D7

**TDD 流程说明:** 本 Task 是重构清理（删死代码、let→const、加 @deprecated 注释），**无新行为、无新测试**，TDD 流程是保持现有 313 测试全绿（FULL 阶段验证行为不变）。无 RED 阶段——这些改动不引入新功能，靠现有测试守护正确性。

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

- [ ] `.claude/workflows/run-plans.js` 4 处修改，逐处 grep 确认无后续 `x =` 再赋值：

```diff
- let impl
- impl = await dispatchImpl(...)
+ const impl = await dispatchImpl(...)
```

4 处位置（line 附近）：`impl`（1259-1261）、`ctxr`（1272）、`commit`（1442）、`simp`（1455）。实施前每处用 `grep -n "let impl\|let ctxr\|let commit\|let simp" .claude/workflows/run-plans.js` 确认行号，并检查变量后续无再赋值。

### Step 3.3 — S14：issuesFromReviews 标 @deprecated

- [ ] `docs/superpowers/workflows/lib.js`（issuesFromReviews 函数，lib.js:118-123 附近）当前注释为：
```javascript
// 已被 collectReviewFindings 取代（orchestrator fix-round 用）；保留为通用工具 + 向后兼容。
export function issuesFromReviews(...reviews) {
```
改为：
```javascript
/**
 * @deprecated (2026-07-07) 已被 collectReviewFindings 取代（orchestrator fix-round 用）。
 * 保留仅为向后兼容；新代码请用 collectReviewFindings。
 * 计划在下一轮 spec 修订时移除（需先确认无 memory/indexing 脚本调用）。
 */
export function issuesFromReviews(...reviews) {
```

- [ ] `.claude/workflows/run-plans.js` inline 副本同步（若 run-plans.js 有 issuesFromReviews 的 inline 副本，注释也同步；若无 inline 副本则只改 lib.js）

### Step 3.4 — FULL：全量回归 + commit

- [ ] 运行 `node --test docs/superpowers/workflows/tests/*.test.js` → 313 tests green（无新测试，行为不变）
- [ ] **关键检查**：sync.test 字节断言通过（注释变化不影响函数体字节比较，但确认 issuesFromReviews 若在 QC-4 列表内仍字节一致）
- [ ] CRLF 修复修改的文件
- [ ] git commit: `refactor(workflow): S11 删死代码 + S12 let→const + S14 标 @deprecated`

---

## Task 4: Batch 2 Commit 1 — B2-1 S10 taskKey 纯函数

**目标:** 抽 taskKey 纯函数，统一 padStart 2 位，防历史 P0-7 位数不一致 bug 复发。

**依据:** 审计报告 S10；设计文档 §5.1

### Step 4.1 — RED：写失败测试

- [ ] `docs/superpowers/workflows/tests/helpers.test.js` 加测试（先在 line 3 import 列表加 `taskKey`）：

```javascript
test('S10 taskKey: 单位 seq padStart 为 01', () => {
  assert.equal(taskKey(1, 'T1'), 'plan-01/T1')
})

test('S10 taskKey: 双位 seq 不补零', () => {
  assert.equal(taskKey(10, 'T10a'), 'plan-10/T10a')
})
```

- [ ] 运行 `node --test docs/superpowers/workflows/tests/helpers.test.js` 确认测试 FAIL（taskKey 未定义）

### Step 4.2 — GREEN：lib.js 加 taskKey + run-plans.js inline + 替换 6+ 处

- [ ] `docs/superpowers/workflows/lib.js` 加函数（放在 bareTaskId/commitSubject 附近，与其他 task id helper 同源）：

```javascript
// taskKey 构造（S10, 2026-07-07）：统一 padStart 2 位，防历史 P0-7 位数不一致 bug 复发。
export function taskKey(seq, taskId) {
  return `plan-${String(seq).padStart(2, '0')}/${taskId}`
}
```

- [ ] `.claude/workflows/run-plans.js` inline 副本加 taskKey（与 lib.js 字节一致，去掉 export）
- [ ] 替换 run-plans.js 中 6+ 处 `` `plan-${String(seq).padStart(2, '0')}/${id}` `` 拼接为 `taskKey(seq, id)` 调用。用 `grep -n "plan-\${String" .claude/workflows/run-plans.js` 定位所有拼接点（line 1241, 1166, 1651, 1662, 1669, 1684, 1704 附近），逐处替换。

### Step 4.3 — SYNC + FULL

- [ ] `docs/superpowers/workflows/tests/sync.test.js` 加 taskKey 字节断言（参照 QC-4 模式，extractFunctionBody 比较）：
```javascript
// 在 QC-4 的 fns 数组加 'taskKey'
```
- [ ] `docs/superpowers/workflow-design.md` §4.4 加 taskKey helper 说明
- [ ] 运行 `node --test docs/superpowers/workflows/tests/*.test.js` → 315 tests green（313 + 2）
- [ ] CRLF 修复 + git commit: `refactor(workflow): B2-1 S10 taskKey 纯函数 (统一 padStart 2 位)`

---

## Task 5: Batch 2 Commit 2 — B2-2 S4 REVIEW_SOURCES 常量

**目标:** 提 REVIEW_SOURCES 常量，消除 reviewer 来源三元组在 3 处的硬编码。

**依据:** 审计报告 S4；设计文档 §5.2

### Step 5.1 — RED：写失败测试

- [ ] `docs/superpowers/workflows/tests/helpers.test.js` 加测试（先在 line 3 import 列表加 `REVIEW_SOURCES`）：

```javascript
test('S4 REVIEW_SOURCES: 3 个 reviewer 来源', () => {
  assert.ok(Array.isArray(REVIEW_SOURCES))
  assert.equal(REVIEW_SOURCES.length, 3)
  assert.deepEqual(REVIEW_SOURCES.map(s => s.name), ['spec', 'quality', 'hunter'])
  assert.deepEqual(REVIEW_SOURCES.map(s => s.key), ['issues', 'issues', 'silent_failures'])
})
```

- [ ] 运行确认测试 FAIL（REVIEW_SOURCES 未导出）

### Step 5.2 — GREEN：lib.js 加常量 + 重构 3 函数

- [ ] `docs/superpowers/workflows/lib.js` 加常量（放在 collectReviewFindings 之前）：

```javascript
// reviewer 来源三元组（S4, 2026-07-07）：消除 collectReviewFindings/reviewHaltForEmptyFailed/summarizeReviewRound 三处硬编码。
export const REVIEW_SOURCES = [
  { name: 'spec', key: 'issues' },
  { name: 'quality', key: 'issues' },
  { name: 'hunter', key: 'silent_failures' },
]
```

- [ ] 重构 `collectReviewFindings`（lib.js:143-145）/ `reviewHaltForEmptyFailed`（lib.js:161-171）/ `summarizeReviewRound`（lib.js:195-202）遍历 REVIEW_SOURCES 替代硬编码。实施时逐函数读取当前实现，用 `REVIEW_SOURCES.forEach(s => { ... r.diagnostics?.[s.key] ... s.name ... })` 模式重构，**用 diff 验证输出逐字节一致**。
- [ ] `.claude/workflows/run-plans.js` inline 副本同步（REVIEW_SOURCES + 3 函数体）

### Step 5.3 — SYNC + FULL

- [ ] sync.test 加 REVIEW_SOURCES 字节断言（QC-4 fns 数组 + 常量存在性）
- [ ] **关键检查**：现有 collectReviewFindings/reviewHaltForEmptyFailed/summarizeReviewRound 测试全绿（重构后行为不变）
- [ ] 运行 `node --test docs/superpowers/workflows/tests/*.test.js` → 316 tests green（315 + 1）
- [ ] CRLF 修复 + git commit: `refactor(workflow): B2-2 S4 REVIEW_SOURCES 常量 (消除 reviewer 三元组硬编码)`

---

## Task 6: Batch 2 Commit 3 — B2-3 S9 makeHalt + errStr

**目标:** 抽 makeHalt 纯构造函数（errStr 已在 lib.js:213，makeHalt 内部调它），消除 dispatchImpl 内 4 处 catch 块的 halted 构造重复。

**依据:** 审计报告 S9；设计文档 §5.3 / D9

### Step 6.1 — RED：写失败测试

- [ ] `docs/superpowers/workflows/tests/helpers.test.js` 加测试（errStr 已在 import 列表；加 `makeHalt`）：

```javascript
test('S9 makeHalt: 构造 halt 对象', () => {
  const h = makeHalt('model_unavailable', 'opus', new Error('quota'))
  assert.equal(h.halted, true)
  assert.equal(h.reason, 'model_unavailable')
  assert.equal(h.diag.model, 'opus')
  assert.equal(h.diag.error, 'quota')
})

test('S9 makeHalt: error 为 null/字符串', () => {
  assert.equal(makeHalt('x', 'm', null).diag.error, '')
  assert.equal(makeHalt('x', 'm', 'msg').diag.error, 'msg')
})
```

- [ ] 运行确认测试 FAIL（makeHalt 未定义）

### Step 6.2 — GREEN：lib.js 加 makeHalt + run-plans.js inline + 替换 4 处

- [ ] `docs/superpowers/workflows/lib.js` 加函数（放在 errStr 之后）：

```javascript
// makeHalt（S9, 2026-07-07）：统一 halt 对象构造，消除 dispatchImpl 内 catch 块重复。
export function makeHalt(reason, model, error) {
  return { halted: true, reason, diag: { model, error: errStr(error) } }
}
```

（errStr 已在 lib.js:213，无需新增）

- [ ] `.claude/workflows/run-plans.js` inline 副本加 makeHalt（errStr 已在 run-plans.js:271）
- [ ] 替换 dispatchImpl 内 4 处 catch 块的 halted 构造。当前 4 处为（line 406, 407, 426, 428 附近）：
  - `return { halted: true, reason: 'model_unavailable', diag: { model, error: errStr(e) } }` → `return makeHalt('model_unavailable', model, e)`
  - `return { halted: true, reason: 'agent_error', diag: { model, error: errStr(e) } }` → `return makeHalt('agent_error', model, e)`
  - retry 路径同理（model 改 retryModel）
- [ ] **注意**：dispatchImpl 内另有 3 处 diag 是 `impl.diagnostics` 非 errStr（如 `if (impl?.status === 'model_unavailable') return { halted: true, reason: 'model_unavailable', diag: impl.diagnostics }`），**不替换**——这些不是 catch 块构造。

### Step 6.3 — SYNC + FULL

- [ ] sync.test 加 makeHalt 字节断言（QC-4 fns 数组）
- [ ] 运行 `node --test docs/superpowers/workflows/tests/*.test.js` → 318 tests green（316 + 2）
- [ ] CRLF 修复 + git commit: `refactor(workflow): B2-3 S9 makeHalt 纯构造函数 (消除 dispatchImpl catch 块重复)`

---

## Task 7: Batch 2 Commit 4 — B2-6 S6 formatFindingItem

**目标:** 抽 formatFindingItem，统一 formatFindings 与 formatCrossReviewerNote 的 finding 格式化。

**依据:** 审计报告 S6；设计文档 §5.6

### Step 7.1 — RED：写失败测试

- [ ] `docs/superpowers/workflows/tests/helpers.test.js` 加测试（加 `formatFindingItem` 到 import）：

```javascript
test('S6 formatFindingItem: 有 severity + fix + file', () => {
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

- [ ] 运行确认测试 FAIL（formatFindingItem 未定义）

### Step 7.2 — GREEN：lib.js 加函数 + 重构 2 函数

- [ ] `docs/superpowers/workflows/lib.js` 加函数（放在 formatFindings 之前）：

```javascript
// formatFindingItem（S6, 2026-07-07）：统一 finding 格式化，消除 formatFindings/formatCrossReviewerNote 重复。
export function formatFindingItem(f, { withFile = true, prefix = '' } = {}) {
  const tag = f.severity ? `[${f.source}|${f.severity}]` : `[${f.source}]`
  const fix = f.fix ? ` — fix: ${f.fix}` : ''
  const file = (withFile && f.file) ? ` (${f.file})` : ''
  return `${prefix}${tag} ${f.title}${fix}${file}`
}
```

- [ ] 重构 `formatFindings`（lib.js:176-184）当前实现为 `findings.map(f => { ... }).join('\n')`，改为 `findings.map(f => formatFindingItem(f)).join('\n')`
- [ ] 重构 `formatCrossReviewerNote`（lib.js:757-770）用 `formatFindingItem(f, { withFile: false, prefix: '- ' })`。**实施时读取当前 formatCrossReviewerNote 完整实现**，确保输出逐字节一致（用 diff 验证）。
- [ ] `.claude/workflows/run-plans.js` inline 副本同步

### Step 7.3 — SYNC + FULL

- [ ] sync.test 加 formatFindingItem 字节断言
- [ ] **关键检查**：现有 formatFindings/formatCrossReviewerNote 测试全绿（重构后行为不变）
- [ ] 运行 `node --test docs/superpowers/workflows/tests/*.test.js` → 321 tests green（318 + 3）
- [ ] CRLF 修复 + git commit: `refactor(workflow): B2-6 S6 formatFindingItem (统一 finding 格式化)`

---

## Task 8: Batch 2 Commit 5 — B2-4 S1 checkImplStatus 纯决策函数

**目标:** 抽 checkImplStatus 纯决策函数，消除 implementor dispatch 点可替换重复。

**依据:** 审计报告 S1；设计文档 §5.4 / D10。**依赖**：Task 4-7 全绿后做。

**复核修正（2026-07-07，实施前）**：原 plan 假设 4 处可替换，但当前代码核查实际只有 **3 处**匹配 `impl.status !== 'ok' && impl.status !== 'done_with_concerns'` 模式（见 Step 8.2 实际位置）。原"初始 dispatch 后"site（spec 旧 line 1261）在当前代码只有 `if (impl.halted) return impl` + `if (impl.status === 'blocked')` 分支，无 `status !== ok` 检查——故不可替换，已在保留集合内。fix-round site（`implementor ${impl.status} in fix-round ${round}`）按 D16 不用 checkImplStatus（条件不同：`blocked || failed || needs_context`）。故 Task 8 实际替换 3 处。测试用例数不变（4 个：halted 透传 / status 不在 allowed / status 在 allowed / 默认 allowed）。

**签名修正（2026-07-07，实施前）**：原 spec/plan 用 3-arg `reasonPrefix`，函数形 `${reasonPrefix} ${impl.status}` 把 status 放尾部。但当前 3 处 reason 都把 status 放**中间**（如 `implementor ${impl.status} after retry`）→ `reasonPrefix` 形无法逐字对齐（D10 决策）。改 `reasonTemplate`（含 `{status}` 占位符），函数内 `reasonTemplate.replace('{status}', impl.status)`。spec §5.4 / D10 已同步修正。

### Step 8.1 — RED：写失败测试

- [ ] `docs/superpowers/workflows/tests/helpers.test.js` 加 4 个用例（加 `checkImplStatus` 到 import）：

```javascript
test('S1 checkImplStatus: halted 透传', () => {
  const impl = { halted: true, reason: 'x', diag: {} }
  assert.equal(checkImplStatus(impl), impl)
})

test('S1 checkImplStatus: status 不在 allowed 返回 halt', () => {
  const impl = { status: 'failed', diagnostics: { e: 1 } }
  const h = checkImplStatus(impl, ['ok'], 'implementor {status}')
  assert.equal(h.halted, true)
  assert.equal(h.reason, 'implementor failed')
  assert.equal(h.diag.e, 1)
})

test('S1 checkImplStatus: status 在 allowed 返回 null', () => {
  const impl = { status: 'ok', diagnostics: {} }
  assert.equal(checkImplStatus(impl), null)
})

test('S1 checkImplStatus: 默认 allowed 含 done_with_concerns', () => {
  const impl = { status: 'done_with_concerns', diagnostics: {} }
  assert.equal(checkImplStatus(impl), null)
})
```

- [ ] 运行确认测试 FAIL（checkImplStatus 未定义）

### Step 8.2 — GREEN：lib.js + run-plans.js 实现

- [ ] `docs/superpowers/workflows/lib.js` 加函数：

```javascript
// checkImplStatus（S1, 2026-07-07）：implementor dispatch 后的状态检查 helper。
// halted → 透传；status 不在 allowed → halt；否则返回 null（继续往下）。
// reason 逐字对齐原实现（D10）：reasonTemplate 含 {status} 占位符，函数内 replace。
// 复核修正（2026-07-07）：原 reasonPrefix 形把 status 放尾部，但原实现把 status 放中间
// （如 'implementor failed after retry'）→ 用 {status} 占位符模板保留原 reason 形。
export function checkImplStatus(impl, allowed = ['ok', 'done_with_concerns'], reasonTemplate = 'implementor {status}') {
  if (impl.halted) return impl
  if (!allowed.includes(impl.status)) {
    return { halted: true, reason: reasonTemplate.replace('{status}', impl.status), diag: impl.diagnostics }
  }
  return null
}
```

- [ ] `.claude/workflows/run-plans.js` inline 副本 + 替换 **3 处**（实际行号实施时核查，当前约 1299/1301/1307）。**实施时逐处读取当前代码，确认 reason 字符串逐字对齐**。3 处替换为：

```javascript
// site 1（context-fetch retry 后，原 reason: `implementor ${impl.status} after context-fetch retry`）：
const h1 = checkImplStatus(impl, undefined, 'implementor {status} after context-fetch retry')
if (h1) return h1
// site 2（context-fetch 最终，原 reason: `implementor ${impl.status} after context-fetch`）：
const h2 = checkImplStatus(impl, undefined, 'implementor {status} after context-fetch')
if (h2) return h2
// site 3（failed retry 后，原 reason: `implementor ${impl.status} after retry`）：
const h3 = checkImplStatus(impl, undefined, 'implementor {status} after retry')
if (h3) return h3
```

注：变量名 `h1/h2/h3` 仅为示意，实施时避免与同作用域已有变量名冲突（参照 Task 4 的 `tk` 命名纪律）。

- [ ] **保留**原样不动（非 checkImplStatus 模式）：初始 dispatch 后的 `if (impl.status === 'blocked')` 升级链、context-fetch 后的 `if (impl.status === 'blocked')` / `if (impl.status === 'failed')` 分支、fix-round 的 `if (blocked || failed || needs_context)` 判断（D16）。

### Step 8.3 — SYNC + FULL

- [ ] sync.test 加 checkImplStatus 字节断言（QC-4 fns 数组 + QC-3 existence）
- [ ] 运行 `node --test docs/superpowers/workflows/tests/*.test.js` → 326 tests green（322 + 4）
- [ ] CRLF 修复 + git commit: `refactor(workflow): B2-4 S1 checkImplStatus 纯决策 helper (消除 3 处 dispatch 重复, reasonTemplate 保留原 reason)`

---

## Task 9: Batch 2 Commit 6 — B2-5 S5 formatBulletSection 通用渲染 helper

**目标:** 抽 formatBulletSection 通用渲染 helper，5 个简单 format* 重构为 wrapper + formatDomainLessons 为复杂 wrapper（保留 sort/cap 业务逻辑）。

**依据:** 审计报告 S5；设计文档 §5.5 / D11

> **注意**：6 个 format* 中，formatDomainLessons 含过滤 silent-failure + category 匹配/title fallback + 同 plan 优先 sort + cap 5 业务逻辑，formatBulletSection 只负责最后渲染 bullet lines，前 4 步留在 wrapper。是"5→1 + 1 复杂 wrapper"，非"6→1"。

### Step 9.1 — RED：写失败测试

- [ ] `docs/superpowers/workflows/tests/helpers.test.js` 加 3 个用例（加 `formatBulletSection` 到 import）：

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
  // bullets 与 outro 之间单 \n（非空行），与 6 个原 format* 的模板字面量 spacing 一致。
  assert.equal(out, '## H\nintro line\n- x\noutro line 1\noutro line 2')
})
```

- [ ] 运行确认测试 FAIL（formatBulletSection 未定义）

### Step 9.2 — GREEN：lib.js 加 formatBulletSection + 重构 6 wrapper

- [ ] `docs/superpowers/workflows/lib.js` 加函数：

```javascript
// formatBulletSection（S5, 2026-07-07）：通用 bullet section 渲染，6 个 format* 复用。
// outro 支持多行 string（D11）。
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

- [ ] 重构 6 个 format* 为 wrapper。**实施时逐个读取当前实现，用 `diff <(node -e "console.log(oldFn(testInput))") <(node -e "console.log(newFn(testInput))")` 验证输出逐字节一致**。关键：每个 wrapper 的 heading/intro/outro 文本必须与原实现完全一致（含换行、标点）。
  - formatReferencePaths / formatSilentFailureContext / formatFailedApproaches / formatLessons / formatUniversalLessons：简单 wrapper（items.map + heading + intro + outro）
  - formatDomainLessons：**复杂 wrapper**——保留过滤 silent-failure + category 匹配/title fallback + sort + cap 5 业务逻辑，最后调 formatBulletSection 渲染 bullet lines
- [ ] `.claude/workflows/run-plans.js` inline 副本同步

### Step 9.3 — SYNC + FULL

- [ ] sync.test 加 formatBulletSection 字节断言 + 6 wrapper 验证（QC-4 fns 数组）
- [ ] **关键检查**：现有 6 个 format* 的 helpers.test 用例必须全绿（输出逐字节一致）
- [ ] 运行 `node --test docs/superpowers/workflows/tests/*.test.js` → 328 tests green（325 + 3）
- [ ] CRLF 修复 + git commit: `refactor(workflow): B2-5 S5 formatBulletSection 通用渲染 helper (5 简单 + 1 复杂 wrapper)`

---

## Task 10: Batch 2 Commit 7 — B2-7 S7 QUOTA_HALT_NOTE PROMPT 常量

**目标:** 提 QUOTA_HALT_NOTE 常量，7 个 PROMPT 末尾重复文本替换为占位符，buildPrompt 内置默认注入。

**依据:** 审计报告 S7；设计文档 §5.7 / D12

> **buildPrompt defaults opt-out 语义（D12/D14）**：`{ ...defaults, ...ctx }` 合并意味着调用方可传 `quotaHaltNote: ''` 显式关闭默认注入。默认开（多数 prompt 需要）、可 opt-out。

### Step 10.1 — RED：写失败测试

- [ ] `docs/superpowers/workflows/tests/helpers.test.js` 加测试（buildPrompt 已可测，参照 buildPrompt.test.js）：

```javascript
test('S7 QUOTA_HALT_NOTE: buildPrompt 默认注入限额说明', () => {
  const out = buildPrompt('implementor', { taskId: 'T1', planId: '01', retryNote: '', fixIssues: '' })
  assert.ok(out.includes('model_unavailable'), 'implementor prompt 应含限额说明')
  assert.ok(out.includes('quota'), '应含 quota 关键词')
})

test('S7 QUOTA_HALT_NOTE: 调用方可 opt-out（传空串）', () => {
  const out = buildPrompt('implementor', { taskId: 'T1', planId: '01', retryNote: '', fixIssues: '', quotaHaltNote: '' })
  assert.ok(!out.includes('model_unavailable'), '传空串应关闭限额说明注入')
})
```

- [ ] 运行确认测试 FAIL（当前 buildPrompt 无默认注入）

### Step 10.2 — GREEN：lib.js 加常量 + buildPrompt 默认 + 7 prompt 替换

- [ ] `docs/superpowers/workflows/lib.js` 加常量（放在 PROMPTS 之前）：

```javascript
const QUOTA_HALT_NOTE = `若遇到 model 限额耗尽（quota/rate-limit/429 错误），返回 status:'model_unavailable'（非 failed），让 orchestrator halt 并保存进度。`
```

- [ ] 修改 `buildPrompt`（lib.js:86）。当前实现：
```javascript
export function buildPrompt(role, ctx = {}) {
  const tpl = PROMPTS[role]
  if (!tpl) throw new Error(`unknown role: ${role}`)
  return tpl.replace(/\{\{(\w+)\}\}/g, (_, k) => {
    if (!(k in ctx)) return `{{${k}}}`
    if (ctx[k] === undefined || ctx[k] === null) return ''
    return String(ctx[k])
  })
}
```
改为：
```javascript
export function buildPrompt(role, ctx = {}) {
  const tpl = PROMPTS[role]
  if (!tpl) throw new Error(`unknown role: ${role}`)
  const defaults = { quotaHaltNote: QUOTA_HALT_NOTE }
  const merged = { ...defaults, ...ctx }
  return tpl.replace(/\{\{(\w+)\}\}/g, (_, k) => {
    if (!(k in merged)) return `{{${k}}}`
    if (merged[k] === undefined || merged[k] === null) return ''
    return String(merged[k])
  })
}
```

- [ ] 7 个 prompt（implementor/specReview/qualityReviewer/hunter/commit/gate/lessonDistiller）末尾的限额说明重复文本替换为 `{{quotaHaltNote}}`。**实施时先 grep 定位每个 prompt 中的限额说明文本**（`grep -n "限额耗尽\|model_unavailable\|quota" docs/superpowers/workflows/lib.js`），逐个替换。注意 lessonDistiller 的限额说明措辞略不同（decisions: [{action:'skip'}]），需单独处理或保留。
- [ ] `.claude/workflows/run-plans.js` inline 副本同步（PROMPTS + buildPrompt）

### Step 10.3 — SYNC + FULL

- [ ] sync.test prompt 字节断言更新基线（7 个 prompt 体变了，`PROMPTS.{role} identical` 测试两端同步即可，但若有逐字内容断言需更新）
- [ ] 运行 `node --test docs/superpowers/workflows/tests/*.test.js` → 330 tests green（328 + 2：默认注入 + opt-out 两个测试）
- [ ] CRLF 修复 + git commit: `refactor(workflow): B2-7 S7 QUOTA_HALT_NOTE 常量 + buildPrompt 默认注入 (7 prompt 去重)`

---

## Task 11: Batch 2 Commit 8 — B2-8 S8 STATIC_READONLY_NOTE + LESSONS_EXEMPTION_NOTE

**目标:** 提 STATIC_READONLY_NOTE（进 buildPrompt 默认，opt-out 同 D12）+ LESSONS_EXEMPTION_NOTE（函数，调用方传参），3 个 reviewer prompt 重复段替换。

**依据:** 审计报告 S8；设计文档 §5.8 / D13-D14

### Step 11.1 — RED：写失败测试

- [ ] `docs/superpowers/workflows/tests/helpers.test.js` 加测试：

```javascript
test('S8 STATIC_READONLY_NOTE: buildPrompt 默认注入', () => {
  const out = buildPrompt('specReview', { taskId: 'T1', planId: '01', specPath: '', implSummary: '', filesTouched: '' })
  assert.ok(out.includes('STATIC READ-ONLY'), 'specReview prompt 应含 STATIC READ-ONLY')
})

test('S8 LESSONS_EXEMPTION_NOTE: 调用方传 applicableDimensions', () => {
  const out = buildPrompt('specReview', { taskId: 'T1', planId: '01', specPath: '', implSummary: '', filesTouched: '', lessonsExemptionNote: LESSONS_EXEMPTION_NOTE('spec compliance') })
  assert.ok(out.includes('spec compliance'), '应含调用方传入的 applicableDimensions')
})
```

（需在 import 加 `LESSONS_EXEMPTION_NOTE`）

- [ ] 运行确认测试 FAIL

### Step 11.2 — GREEN：lib.js 加常量 + 函数 + 重构 3 prompt

- [ ] `docs/superpowers/workflows/lib.js` 加常量 + 函数。**实施时先读取 specReview/qualityReviewer/hunter 三个 prompt，提取它们的 STATIC READ-ONLY 段（10 行公共文本）作为常量值**：

```javascript
const STATIC_READONLY_NOTE = `## STATIC READ-ONLY Constraint
...（从现有 specReview prompt 提取的 10 行公共文本，实施时逐字复制）...`

// LESSONS_EXEMPTION_NOTE 是函数（D13）：applicableDimensions 随 reviewer 变化
export function LESSONS_EXEMPTION_NOTE(applicableDimensions) {
  return `## Lessons Learned Exemption
... ${applicableDimensions} ...`
}
```

- [ ] buildPrompt defaults 加 `staticReadonlyNote: STATIC_READONLY_NOTE`（D14 决策，opt-out 同 D12）：
```javascript
const defaults = { quotaHaltNote: QUOTA_HALT_NOTE, staticReadonlyNote: STATIC_READONLY_NOTE }
```
（注意：`lessonsExemptionNote` **不进默认**——它是函数返回值，由调用方传参）

- [ ] 3 个 reviewer prompt（specReview/qualityReviewer/hunter）重复段替换为 `{{staticReadonlyNote}}` + `{{lessonsExemptionNote}}`
- [ ] run-plans.js 调用 reviewer 的地方传 `lessonsExemptionNote: LESSONS_EXEMPTION_NOTE(applicableDimensions)`。**实施时 grep run-plans.js 中 buildPrompt('specReview'/'qualityReviewer'/'hunter') 调用点**，加 lessonsExemptionNote 参数。
- [ ] `.claude/workflows/run-plans.js` inline 副本同步

### Step 11.3 — SYNC + FULL

- [ ] sync.test prompt 断言更新基线
- [ ] 运行 `node --test docs/superpowers/workflows/tests/*.test.js` → 332 tests green（330 + 2：默认注入 + LESSONS_EXEMPTION_NOTE 传参两个测试）
- [ ] CRLF 修复 + git commit: `refactor(workflow): B2-8 S8 STATIC_READONLY_NOTE + LESSONS_EXEMPTION_NOTE (3 reviewer prompt 去重)`

---

## Task 12: Batch 3 Commit 1 — B3-1 recordReviewRound

**目标:** 抽 recordReviewRound 纯决策函数（lib.js），消除 review 循环 12 行 state 更新重复。

**依据:** 审计报告 S2；设计文档 §6.1.1。**依赖**：Task 11 全绿后做（Batch 3 开始）。

### Step 12.1 — RED：写失败测试

- [ ] `docs/superpowers/workflows/tests/helpers.test.js` 加 3 个用例（加 `recordReviewRound` 到 import）。所需依赖函数（unionFiles/summarizeReviewRound/collectReviewFindings/updateFindingsHistory）已在 import 或可间接用：

```javascript
test('S2 recordReviewRound: state 正确更新', () => {
  const state = { perTask: { 'plan-01/T1': { files_touched_per_round: [], review_history: [], findings_history: [] } } }
  const spec = { status: 'ok', diagnostics: { files_touched: ['a.ts'], issues: [] } }
  recordReviewRound(state, 'plan-01/T1', 1, spec, null, null)
  assert.equal(state.perTask['plan-01/T1'].review_rounds, 1)
  assert.equal(state.perTask['plan-01/T1'].files_touched_per_round.length, 1)
  assert.equal(state.perTask['plan-01/T1'].review_history.length, 1)
})

test('S2 recordReviewRound: findings_history 通过 updateFindingsHistory 更新', () => {
  const state = { perTask: { 'plan-01/T1': { files_touched_per_round: [], review_history: [], findings_history: [] } } }
  const spec = { status: 'failed', diagnostics: { files_touched: [], issues: [{ title: 'bug', severity: 'critical' }] } }
  recordReviewRound(state, 'plan-01/T1', 1, spec, null, null)
  assert.ok(state.perTask['plan-01/T1'].findings_history.length > 0, 'findings_history 应被更新')
})

test('S2 recordReviewRound: 返回 currentFindings', () => {
  const state = { perTask: { 'plan-01/T1': { files_touched_per_round: [], review_history: [], findings_history: [] } } }
  const spec = { status: 'failed', diagnostics: { files_touched: [], issues: [{ title: 'bug' }] } }
  const result = recordReviewRound(state, 'plan-01/T1', 1, spec, null, null)
  assert.ok(Array.isArray(result.currentFindings))
  assert.equal(result.currentFindings.length, 1)
})
```

- [ ] 运行确认测试 FAIL（recordReviewRound 未定义）

### Step 12.2 — GREEN：lib.js + run-plans.js 实现

- [ ] `docs/superpowers/workflows/lib.js` 加函数：

```javascript
// recordReviewRound（S2, 2026-07-07）：review 循环每轮 state 更新抽取。
// state 是引用，函数内直接 mutate（与现有风格一致）。返回 currentFindings 供后续使用。
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
（注意：原代码在 halt 检查前 push，替换后须保持 push 顺序——recordReviewRound 内部已按原顺序 mutate）

### Step 12.3 — SYNC + FULL

- [ ] sync.test 加 recordReviewRound 字节断言
- [ ] 运行 `node --test docs/superpowers/workflows/tests/*.test.js` → 335 tests green（332 + 3）
- [ ] CRLF 修复 + git commit: `refactor(workflow): B3-1 S2 recordReviewRound 纯决策 helper (review 循环 state 更新抽取)`

---

## Task 13: Batch 3 Commit 2 — B3-1 decideReviewOutcome

**目标:** 抽 decideReviewOutcome 纯决策函数（lib.js），10 个 action 分支集中 OSCILLATING/budget/maxRounds 决策。

**依据:** 审计报告 S2；设计文档 §6.1.2 / D15（复核修正：10 个 action，6 halt 子类 + 4 非 halt）。**依赖**：Task 12 全绿后做。

### Step 13.1 — RED：写 10 个失败测试

- [ ] `docs/superpowers/workflows/tests/helpers.test.js` 加 10 个用例覆盖 action 枚举（D15 复核修正：6 halt 子类 + break/escalate/continue/fix）。加 `decideReviewOutcome` 到 import。所需依赖（allGreen/detectOscillation/isFlipFlop/hasRegressed/shouldEscalateOnOscillation/resolveReviewBudget）已在 import：

```javascript
// 6 halt 子类
test('S2 decideReviewOutcome: reviewReason → halt', () => {
  const state = mkState()
  const out = decideReviewOutcome(state, 'plan-01/T1', 1, {status:'failed'}, null, null, 'sonnet', 4, {}, 'review_failed', null)
  assert.equal(out.action, 'halt'); assert.equal(out.reason, 'review_failed')
})
test('S2 decideReviewOutcome: emptyFailed → halt', () => { /* emptyFailedReason 非空 → action='halt' */ })
test('S2 decideReviewOutcome: regressed → halt OSCILLATING', () => { /* hasRegressed=true → action='halt', reason='OSCILLATING' */ })
test('S2 decideReviewOutcome: osc + flipFlop → halt OSCILLATING', () => { /* osc + flipFlop → action='halt', reason='OSCILLATING' */ })
test('S2 decideReviewOutcome: maxRounds=0 budget guard → halt review_not_converging', () => { /* 无限模式 round>=budget → action='halt', reason='review_not_converging' */ })
test('S2 decideReviewOutcome: round===maxRounds → halt review max rounds', () => { /* 有限模式上限 → action='halt', reason='review max rounds' */ })
// 4 非 halt
test('S2 decideReviewOutcome: allGreen → break', () => { /* spec/qual/hunt 全 ok → action='break' */ })
test('S2 decideReviewOutcome: osc + flipFlop=false + shouldEscalate → escalate', () => { /* → action='escalate', model='opus' */ })
test('S2 decideReviewOutcome: osc + flipFlop=false + alreadyEscalated → continue', () => { /* → action='continue' */ })
test('S2 decideReviewOutcome: else → fix', () => { /* 正常未收敛 → action='fix' */ })
```

（`mkState()` 是测试辅助，构造 `{ perTask: { 'plan-01/T1': { files_touched_per_round: [...], review_history: [...], findings_history: [...], opus_escalated: false } } }`，实施时在每个用例内联或顶部定义 helper）

- [ ] 运行确认 10 个测试 FAIL（decideReviewOutcome 未定义）

### Step 13.2 — GREEN：lib.js + run-plans.js 实现

- [ ] `docs/superpowers/workflows/lib.js` 加函数（10 个 action 分支，逐字对齐原 reason + diag，参照 run-plans.js:1329-1398）：

```javascript
// decideReviewOutcome（S2, 2026-07-07）：review 循环决策抽取，10 个 action 分支。
// 6 halt 子类（reason 区分）+ 4 非 halt（break/escalate/continue/fix）。
// 函数内不 mutate state（escalate 时 opus_escalated/oscillation_escalated_at_round 由调用方做）。
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
// action === 'continue' or 'fix' → 走 fix-round（runFixRound，Task 14 抽取）
```

### Step 13.3 — SYNC + FULL

- [ ] sync.test 加 decideReviewOutcome 字节断言
- [ ] **关键检查**：手动 trace r1/r2/r3 + OSCILLATING(regressed/flipFlop) + budget guard 路径，确认行为不变
- [ ] 运行 `node --test docs/superpowers/workflows/tests/*.test.js` → 345 tests green（335 + 10）
- [ ] CRLF 修复 + git commit: `refactor(workflow): B3-1 S2 decideReviewOutcome 纯决策 helper (10 action 分支集中)`

---

## Task 14: Batch 3 Commit 3 — B3-1 runFixRound

**目标:** 抽 runFixRound runtime 函数（run-plans.js），封装 fix-round dispatch + 状态检查。

**依据:** 审计报告 S2；设计文档 §6.1.3 / D16。**依赖**：Task 13 全绿后做。

> **不加新单测**（D17 复核修正）：可测逻辑已被 lib.js 纯函数测试覆盖（validateAmendResult 等），runFixRound 剩余只是胶水调用，靠 sync.test 存在性断言 + 全量回归兜底。本项目已有 dispatchImpl-retry.test.js 用源码字面量断言绕过 mock 的先例，但此处胶水部分不值得 mock。

### Step 14.1 — GREEN：run-plans.js 加 runFixRound + 主循环改写

- [ ] `.claude/workflows/run-plans.js` 加函数（D16 决策：不用 checkImplStatus，直接内联判断；参照 line 1399-1431 当前实现逐字抽取）：

```javascript
// runFixRound（S2, 2026-07-07）：fix-round dispatch + 状态检查封装。
// D16: 不用 checkImplStatus——fix-round 的 blocked/failed/needs_context 都 halt，语义不同。
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

- [ ] 替换主循环 line 1399-1431（~32 行 → 1 函数调用）：

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

（concerns/concernsHint 是闭包变量，通过返回值传出——不能像原代码直接 mutate 闭包）

### Step 14.2 — SYNC + FULL

- [ ] `docs/superpowers/workflow-design.md` §5.5 加 runFixRound 说明
- [ ] sync.test 加 runFixRound 存在性断言（grep 函数名，参照 dispatchImpl-retry.test.js 模式）
- [ ] 运行 `node --test docs/superpowers/workflows/tests/*.test.js` → 345 tests green（无新测试，靠回归）
- [ ] **关键检查**：手动 trace fix-round 正常/done_with_concerns/blocked/failed/needs_context 路径
- [ ] CRLF 修复 + git commit: `refactor(workflow): B3-1 S2 runFixRound runtime helper (fix-round dispatch 封装)`

---

## Task 15: Batch 3 Commit 4 — B3-2 S3 simplify 三 helper

**目标:** 抽 3 个 simplify runtime helper（checkSimplifyChanges/amendSimplifyCommit/revertSimplifyChanges）。

**依据:** 审计报告 S3；设计文档 §6.3 / D17。**依赖**：Task 14 全绿后做。

> **不加新单测**（D17 复核修正）：可测逻辑（validateAmendResult/validateCheckoutResult）已是 lib.js 纯函数，helpers.test 已覆盖失败分支。3 helper 剩余只是 safeAgent 胶水调用，靠 sync.test 存在性断言 + 全量回归。

### Step 15.1 — GREEN：run-plans.js 加 3 函数

- [ ] `.claude/workflows/run-plans.js` 加 3 个 runtime 函数（参照 line 1462-1472/1489-1497/1506-1514 当前实现逐字抽取）：

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
  const amendResult = await safeAgent('Run `git add -A && git commit --amend --no-edit`. Then run `git rev-parse HEAD` and return JSON {"ok": true, "sha": "<40-char-hex>"}. If amend failed, return {"ok": false, "sha": "", "error": "<message>"}.', { schema: amendSchema, label: `amend:${taskId}` })
  const amendCheck = validateAmendResult(amendResult)
  if (!amendCheck.valid) {
    return { error: true, reason: 'simplify amend failed', diag: { task: taskId, amendError: amendCheck.error, commitSha } }
  }
  return { error: false, sha: amendCheck.sha }
}

async function revertSimplifyChanges(taskId, commitSha) {
  const checkoutSchema = { type: 'object', required: ['ok'], properties: { ok: { type: 'boolean' }, porcelain: { type: 'string' }, error: { type: 'string' } } }
  const checkoutResult = await safeAgent('Run `git reset --hard HEAD && git clean -fd` to discard simplify changes. Then run `git status --porcelain` to verify clean. Return JSON {"ok": true, "porcelain": "<output>"} or {"ok": false, "porcelain": "<output>", "error": "<message>"}.', { schema: checkoutSchema, label: `checkout:${taskId}` })
  const checkoutCheck = validateCheckoutResult(checkoutResult)
  if (!checkoutCheck.valid) {
    return { error: true, reason: 'simplify checkout failed', diag: { task: taskId, checkoutError: checkoutCheck.error, commitSha } }
  }
  return { error: false }
}
```

### Step 15.2 — SYNC：spec + sync.test 存在性断言

- [ ] `docs/superpowers/workflow-design.md` §5.2 方案 C 加 3 函数说明
- [ ] sync.test 加 3 函数存在性断言：

```javascript
test('B3-2 S3 simplify helpers 存在', () => {
  assert.match(runSrc, /async function checkSimplifyChanges\(/, 'checkSimplifyChanges 存在')
  assert.match(runSrc, /async function amendSimplifyCommit\(/, 'amendSimplifyCommit 存在')
  assert.match(runSrc, /async function revertSimplifyChanges\(/, 'revertSimplifyChanges 存在')
})
```

### Step 15.3 — FULL

- [ ] 运行 `node --test docs/superpowers/workflows/tests/*.test.js` → 346 tests green（345 + sync.test 1）
- [ ] CRLF 修复 + git commit: `refactor(workflow): B3-2 S3 simplify 三 helper (checkSimplifyChanges/amendSimplifyCommit/revertSimplifyChanges)`

---

## Task 16: Batch 3 Commit 5 — S3 主流程集成

**目标:** simplify 主流程改写，调用 3 helper，~65 行 → ~25 行。

**依据:** 设计文档 §6.4。**依赖**：Task 15 全绿后做。

### Step 16.1 — GREEN：主流程改写

- [ ] `.claude/workflows/run-plans.js` simplify 主流程（line 1453-1517 附近）改写为：

```javascript
const simp = await dispatchImpl(buildPrompt('simplify', { taskId: task.id, filesChanged: filesChanged.join('\n') }), { schema: SCHEMAS.simplify, label: `simp:${task.id}` }, 'sonnet')
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

（注意：`let simp` 已在 Task 3 S12 改为 `const simp`；此处与 S12 合并一致）

### Step 16.2 — FULL：全量回归 + commit

- [ ] 运行 `node --test docs/superpowers/workflows/tests/*.test.js` → 346 tests green
- [ ] **关键检查**：手动 trace 4 条路径（simplify 全绿 / amend 失败 / review 失败 / checkout 失败），确认行为不变
- [ ] CRLF 修复 + git commit: `refactor(workflow): B3-2 S3 simplify 主流程集成 (65 行 → 25 行, 调用 3 helper)`

---

## 完成标准

- [ ] 所有 16 个 Task 的 checkbox 全部勾选
- [ ] 346 tests green（307 → 346，+39 测试：Batch 1 +6, Batch 2 +19, Batch 3 +14）
- [ ] sync.test 字节断言覆盖所有新纯函数（taskKey/REVIEW_SOURCES/makeHalt/formatFindingItem/checkImplStatus/formatBulletSection/recordReviewRound/decideReviewOutcome）
- [ ] spec（workflow-design.md）+ USAGE.md 同步更新
- [ ] 16 个 commit 全部完成，CRLF 行尾一致
- [ ] 审计报告 16 项发现全部修复（S13 不改，D6 决策）+ B1-10 通用性守护新增

## 依赖图

```
Batch 1（低风险，建安全网 + 文档/清理）
Task 1 (HIGH-1 补链) ─┐
Task 2 (MEDIUM-1 守护 + B1-10 通用性 + LOW-1/2/3/4) ─┤
Task 3 (S11/S12/S14 清理) ─┘
        │
        ▼ （MEDIUM-1 守护 + HIGH-1 数据流测试建立）
Batch 2（中风险，纯函数 helper 抽取，8 commit）
Task 4 (B2-1 taskKey) ─┐
Task 5 (B2-2 REVIEW_SOURCES) ─┤
Task 6 (B2-3 makeHalt) ─┤
Task 7 (B2-6 formatFindingItem) ─┤    所有纯函数进 lib.js + sync 守护
Task 8 (B2-4 checkImplStatus) ─┤    (依赖 Task 4-7 全绿)
Task 9 (B2-5 formatBulletSection) ─┤
Task 10 (B2-7 QUOTA_HALT_NOTE) ─┤
Task 11 (B2-8 STATIC_READONLY_NOTE) ─┘
        │
        ▼ （checkImplStatus 就位后）
Batch 3（高风险，runtime 循环拆分，5 commit）
Task 12 (recordReviewRound) ─┐
Task 13 (decideReviewOutcome) ─┤    依赖 Task 11 全绿
Task 14 (runFixRound) ─┤    依赖 Task 13
Task 15 (S3 三 helper) ─┤    依赖 Task 14
Task 16 (S3 主流程集成) ─┘    依赖 Task 15
```
