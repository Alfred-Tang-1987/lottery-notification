# Reviewer Schema 严格化 + findingsOf 兜底加固 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 specReview schema 宽松导致 LLM 返缺 title/fix 的 issue 对象 → `findingsOf` 兜底 `String(it)` = `[object Object]` → 畸形 finding 污染 `findings_history` → `formatFindingsHistory` 渲染出 `-  [object Object] ★本轮新增`（用户 prompt 实测）。同时加固 quality/hunter schema（severity 未 required）+ `findingsOf` 兜底（`String(it)` → `JSON.stringify(it)`），消除同源风险。

**Architecture:** 仅改 run-plans workflow 自身，遵守既有 §4.3 分层——SCHEMAS + `findingsOf` 纯函数进 lib.js（+ run-plans.js inline + sync.test 字节守护）、specReview prompt 改动留 run-plans.js PROMPTS（lib.js 无 PROMPTS）。修复坚持 TDD（RED→GREEN→SYNC→FULL），不破坏通用性（B1-10 守护：PROMPTS 不含项目专有词），不改变 agent 调用数。

**Tech Stack:** Node.js（`node --test`），两文件模型（lib.js 纯函数源 + run-plans.js inline 副本，sync.test QC-4 `extractSchemas`/`extractFunctionBody` 守护字节一致），CRLF 强制。源码字面量断言用 `extractSchemas`/`extractFunctionBody` 提取防假绿（既有模式）。

## Global Constraints

- **两文件模型**：lib.js 纯函数/常量/SCHEMAS 是源（带 `export`）；run-plans.js inline 复制（无 `export`）。改 lib.js 的纯函数/SCHEMAS 必须同步 run-plans.js inline 副本 + sync.test 守护。改 run-plans.js 独有 runtime（PROMPTS）只动 run-plans.js。
- **CRLF 强制**：每 Task commit 前对修改的 .js/.md 文件执行 CRLF 修复（Windows 上用 PowerShell：`[IO.File]::ReadAllText($f) -replace '(?<!\r)\n','\r\n'`；perl 不可用）。sync.test 有 bare-LF 守护。
- **基线测试**：**365 green**（本计划执行前，从仓库根跑 `node --test docs/superpowers/workflows/tests/*.test.js` 确认）。
- **不破坏通用性**：B1-10 守护——PROMPTS 不得含本项目专有路径/文件名（lottery/notification/lessons.md）。
- **不改变 agent 调用数**：所有修复是改 schema 约束 / 改 prompt return 指令 / 改 `findingsOf` 兜底，不增减 dispatchImpl 调用、不增减 agent 调用。
- **依据**：用户 prompt 实测（2026-07-08）——specReview 返缺 title/fix 对象 → `-  [object Object] ★本轮新增` × 2 污染 fix implementor prompt；根因复核见对话记录。

---

## Task 1: 新建 specReviewSchema() 替换 specReview 的宽松 reviewSchema()

**目标:** specReview 当前用 `reviewSchema()`（[lib.js:956](file:///c:/Users/Alfred/Documents/projects/lottery-notification/docs/superpowers/workflows/lib.js#L956) / [run-plans.js:806](file:///c:/Users/Alfred/Documents/projects/lottery-notification/.claude/workflows/run-plans.js#L806)），`issues: { type: 'array' }` 无 items 约束 → LLM 返缺 title/fix 的对象不被 schema 拦截。新建 `specReviewSchema()`（与 `qualityReviewSchema()` 同构，加 `dimension` 字段适配 spec 三维度），`specReview: specReviewSchema()`。`reviewSchema()` 保留（无其他消费者，但保留供未来潜在通用 reviewer 用，避免破坏性删除）。

**Files:**
- Modify: `docs/superpowers/workflows/lib.js`（新增 `specReviewSchema()` 函数 + `SCHEMAS.specReview` 改用新函数）
- Modify: `.claude/workflows/run-plans.js`（inline 同步）
- Modify: `docs/superpowers/workflows/tests/sync.test.js`（QC-4 SCHEMAS 字节守护 + 新 schema 存在性断言）

**Interfaces:**
- Produces: `specReviewSchema()` 返回与 `qualityReviewSchema()` 同构的 schema，`issues.items` 强制 `{type:'object', required:['title','fix'], properties:{dimension, severity, title, file, fix}}`。`dimension` 是 specReview 专有字段（`MISSING`/`EXTRA`/`MISUNDERSTANDING`，对应 [specReview prompt L1039-1041](file:///c:/Users/Alfred/Documents/projects/lottery-notification/.claude/workflows/run-plans.js#L1039-L1041) 三维度）。

- [ ] **Step 1: Write failing tests (RED)** — `sync.test.js` 加断言。在既有「qualityReviewer 拆出独立 schema」测试块（约 [sync.test.js:163-172](file:///c:/Users/Alfred/Documents/projects/lottery-notification/docs/superpowers/workflows/tests/sync.test.js#L163-L172)）后追加：

```javascript
// specReview 拆出独立 schema（issues items 强制 {title,fix}，防 [object Object] 污染 findings_history）
assert.match(runSrc, /function specReviewSchema/, 'run-plans.js 须 inline specReviewSchema')
assert.match(libSrc, /function specReviewSchema/)
// SCHEMAS.specReview 须用 specReviewSchema() 而非 reviewSchema()
const specSchemaCall = extractSchemas(runSrc).match(/specReview:\s*(\w+)\(\)/)
assert.ok(specSchemaCall, 'SCHEMAS.specReview 须调用 schema 工厂函数')
assert.equal(specSchemaCall[1], 'specReviewSchema', 'SCHEMAS.specReview 须用 specReviewSchema()（非宽松 reviewSchema()）')
// issues items 须强制对象 + required title/fix + dimension 字段
const specSchemaFn = extractSchemas(runSrc).match(/function specReviewSchema\(\)\s*\{[\s\S]*?\n\}/)?.[0] || ''
assert.match(specSchemaFn, /issues:\s*\{\s*type:\s*'array',\s*items:\s*\{[\s\S]*?required:\s*\['title',\s*'fix'\]/,
  'specReviewSchema issues items 须强制对象 + required title/fix')
assert.match(specSchemaFn, /dimension:\s*\{\s*type:\s*'string',\s*enum:\s*\['MISSING',\s*'EXTRA',\s*'MISUNDERSTANDING'\]/,
  'specReviewSchema 须含 dimension 字段（MISSING/EXTRA/MISUNDERSTANDING 三维度）')
```

- [ ] **Step 2: Implement (GREEN)** — `lib.js` 在 `qualityReviewSchema()` 后（约 L967 后）新增 `specReviewSchema()`：

```javascript
// specReview 单独 schema（S7, 2026-07-08）：issues items 强制对象 {dimension, title, fix, severity?, file?}。
// 旧实现用宽松 reviewSchema()（issues: {type:'array'} 无 items 约束）→ LLM 返缺 title/fix 对象不被 schema 拦截
// → findingsOf 的 it.title||String(it) 兜底为 [object Object] → 畸形 finding 污染 findings_history
// → formatFindingsHistory 渲染出 "-  [object Object] ★本轮新增"（用户 prompt 实测 2026-07-08）。
// dimension 字段对应 specReview prompt 三维度 MISSING/EXTRA/MISUNDERSTANDING（L1039-1041）。
export function specReviewSchema() {
  return { type: 'object', required: ['status'], additionalProperties: true,
    properties: {
      status: { type: 'string', enum: ['ok', 'failed', 'model_unavailable'] },
      diagnostics: { type: 'object', properties: { files_touched: { type: 'array' },
        issues: { type: 'array', items: {
          type: 'object', required: ['title', 'fix'],
          properties: {
            dimension: { type: 'string', enum: ['MISSING', 'EXTRA', 'MISUNDERSTANDING'] },
            severity: { type: 'string', enum: ['critical', 'important', 'minor'] },
            title: { type: 'string' },
            file: { type: 'string' },
            fix: { type: 'string' },
          },
        } } } },
      summary: { type: 'string' },
    } }
}
```

然后改 `SCHEMAS.specReview`（[lib.js:909](file:///c:/Users/Alfred/Documents/projects/lottery-notification/docs/superpowers/workflows/lib.js#L909)）：
```diff
-  specReview: reviewSchema(),
+  specReview: specReviewSchema(),
```

`run-plans.js` inline 同步（去 `export`）：新增 `specReviewSchema()` 函数（同上但无 `export`）+ 改 `SCHEMAS.specReview`（[run-plans.js:852](file:///c:/Users/Alfred/Documents/projects/lottery-notification/.claude/workflows/run-plans.js#L852)）。

- [ ] **Step 3: SYNC** — 无 spec/USAGE 改动（schema 内部结构）。注释更新：`reviewSchema()` 函数注释可补一行「specReview 已迁出至 specReviewSchema()，本函数暂无消费者，保留供未来通用 reviewer」。

- [ ] **Step 4: FULL regression + commit** — 跑 `node --test docs/superpowers/workflows/tests/*.test.js`（从仓库根），确认全绿（365 + 新断言）。CRLF 修复。commit `fix(workflow): specReview 独立 schema 强制 issues items 对象防 [object Object] 污染 findings_history`。

---

## Task 2: specReview prompt return 指令从字符串模板改为对象模板

**目标:** specReview prompt（[run-plans.js:1046](file:///c:/Users/Alfred/Documents/projects/lottery-notification/.claude/workflows/run-plans.js#L1046)）return 指令当前是 `issues:[<dimension>: <spec requirement>: <code gap or over-build>]`（字符串模板），与 Task 1 新 schema（强制对象）矛盾。改为对象模板 `issues:[{dimension, severity, title, file, fix}]`，与 schema 对齐。

**Files:**
- Modify: `.claude/workflows/run-plans.js`（specReview prompt L1046，runtime 胶水只在此文件）

**Interfaces:**
- Produces: specReview prompt return 指令改为 `issues:[{dimension: MISSING|EXTRA|MISUNDERSTANDING, severity: critical|important|minor, title, file, fix}]`，与 Task 1 `specReviewSchema()` 字段对齐。

- [ ] **Step 1: Write failing tests (RED)** — `sync.test.js` 加源码字面量断言（specReview prompt 是 run-plans.js 独有，用字面量断言防假绿）：

```javascript
test('specReview prompt return 指令须用对象模板（与 specReviewSchema 对齐，非字符串模板）', () => {
  // 旧 prompt: issues:[<dimension>: <spec requirement>: <code gap or over-build>]（字符串模板）
  // 新 prompt: issues:[{dimension: MISSING|EXTRA|MISUNDERSTANDING, severity, title, file, fix}]（对象模板）
  // 字符串模板与 specReviewSchema 的 items 强制对象矛盾 → LLM 返字符串被 schema 拒 → 重试耗限额。
  const promptMatch = runSrc.match(/specReview:\s*`([\s\S]*?)`/)
  assert.ok(promptMatch, '须有 specReview prompt')
  const prompt = promptMatch[1]
  assert.doesNotMatch(prompt, /issues:\[<dimension>:/,
    'specReview prompt 不得再用字符串模板 issues:[<dimension>: ...]')
  assert.match(prompt, /issues:\[\{dimension:\s*MISSING\|EXTRA\|MISUNDERSTANDING,\s*severity[^}]*title[^}]*file[^}]*fix\}\]/,
    'specReview prompt 须用对象模板 issues:[{dimension, severity, title, file, fix}]')
})
```

- [ ] **Step 2: Implement (GREEN)** — `.claude/workflows/run-plans.js` specReview prompt（L1046 附近），把：

```
Return {status (ok|failed), diagnostics:{files_touched:[...], issues:[<dimension>: <spec requirement>: <code gap or over-build>]}, summary}.
```

改为：

```
Return {status (ok|failed), diagnostics:{files_touched:[...], issues:[{dimension: MISSING|EXTRA|MISUNDERSTANDING, severity: critical|important|minor, title, file, fix}]}, summary}.
```

同时检查 prompt 其他位置是否引用旧字符串模板格式（如示例 `issues: ["MISSING: ..."]`），若有则同步改为对象示例。若 prompt 末尾的 `RED FLAG` 段（L1047）提及 issues 格式，也同步对齐。

- [ ] **Step 3: SYNC** — 无 spec/USAGE 改动。注释更新：specReview prompt 顶部可补一行注释「return 指令用对象模板（与 specReviewSchema 对齐），旧字符串模板已弃用」。

- [ ] **Step 4: FULL regression + commit** — 跑全量测试。CRLF 修复。commit `fix(workflow): specReview prompt return 指令改对象模板与 specReviewSchema 对齐`。

> **副作用说明**：此 Task 与 Task 1 必须配套——Task 1 改 schema 强制对象，Task 2 改 prompt 要求对象，两者缺一都会让 LLM 返字符串被 schema 拒（重试耗限额）或返对象缺字段不被拦（`[object Object]` 复现）。执行顺序固定：Task 1 → Task 2。

---

## Task 3: findingsOf 兜底 String(it) → JSON.stringify(it)

**目标:** `findingsOf`（[lib.js:243](file:///c:/Users/Alfred/Documents/projects/lottery-notification/docs/superpowers/workflows/lib.js#L243) / [run-plans.js:243](file:///c:/Users/Alfred/Documents/projects/lottery-notification/.claude/workflows/run-plans.js#L243)）的 `it.title || String(it)` 兜底，当 `it` 是缺 title 的对象时 `String(it)` = `"[object Object]"`（零信息）。改为 `it.title || (typeof it === 'string' ? it : JSON.stringify(it))`——字符串原样返回，对象序列化为可读 JSON。治标但有用：即使 Task 1 schema 严格，runtime 校验偶有边界 case，兜底应保留诊断价值。

**Files:**
- Modify: `docs/superpowers/workflows/lib.js`（`findingsOf` L243）
- Modify: `.claude/workflows/run-plans.js`（inline 同步 L243）
- Modify: `docs/superpowers/workflows/tests/helpers.test.js`（RED: 畸形对象 → 不再 `[object Object]`，而是可读 JSON）

**Interfaces:**
- Produces: `findingsOf` 的 title 兜底从 `String(it)` 改为 `typeof it === 'string' ? it : JSON.stringify(it)`。字符串原样，对象变 JSON 字符串（如 `'{"dimension":"EXTRA","desc":"..."}'`）。

- [ ] **Step 1: Write failing tests (RED)** — `helpers.test.js` 在既有「formatFindings never emits [object Object]」测试（L40）后追加。**关键：用缺 title 的对象 fixture，复刻用户 prompt 实测场景**：

```javascript
test('findingsOf 缺 title 对象兜底不再 [object Object]，输出可读 JSON（用户 prompt 实测 2026-07-08）', () => {
  // specReview 返缺 title 的 issue 对象（schema 漏拦或 runtime 边界 case）
  // 旧兜底 String(it) = "[object Object]"（零信息）→ formatFindingsHistory 渲染 "-  [object Object] ★本轮新增"
  // 新兜底 JSON.stringify(it) → 至少 implementor 能读到 dimension/desc 等字段尝试修复
  const specBadObj = { status: 'failed', diagnostics: { issues: [
    { dimension: 'EXTRA', severity: 'minor', fix: 'remove unused helper' },  // 缺 title
    { dimension: 'MISSING', desc: 'spec req Y not implemented', fix: 'add Y' },  // 缺 title，有 desc
  ] } }
  const out = collectReviewFindings(specBadObj, { status: 'ok' }, { status: 'ok' })
  assert.equal(out.length, 2)
  // 不再 [object Object]
  assert.ok(!out[0].title.includes('[object Object]'), '缺 title 对象不得兜底为 [object Object]')
  assert.ok(!out[1].title.includes('[object Object]'), '缺 title 对象不得兜底为 [object Object]')
  // 须是可读 JSON（含原对象字段）
  assert.match(out[0].title, /"dimension"\s*:\s*"EXTRA"/, '兜底 JSON 须含 dimension 字段')
  assert.match(out[1].title, /"desc"\s*:\s*"spec req Y/, '兜底 JSON 须含 desc 字段')
})

test('findingsOf 字符串 issue 仍原样返回（不 JSON.stringify 字符串）', () => {
  // 既有 specBad fixture 用字符串 'MISSING: spec req X (a.py:10)'
  // 字符串须原样返回，不得 JSON.stringify 成 '"MISSING: ..."'（带引号）
  const specStr = { status: 'failed', diagnostics: { issues: ['MISSING: spec req X (a.py:10)'] } }
  const out = collectReviewFindings(specStr, { status: 'ok' }, { status: 'ok' })
  assert.equal(out[0].title, 'MISSING: spec req X (a.py:10)', '字符串 issue 须原样返回，不得 JSON.stringify')
})
```

- [ ] **Step 2: Implement (GREEN)** — `lib.js` + `run-plans.js` `findingsOf`（L243）同步改：

```diff
   for (const it of (r.diagnostics?.[key] || [])) {
     if (it && typeof it === 'object') out.push({ source, severity: it.severity, title: it.title || (typeof it === 'string' ? it : JSON.stringify(it)), file: normalizeFilePath(it.file), fix: it.fix })
-    if (it && typeof it === 'object') out.push({ source, severity: it.severity, title: it.title || String(it), file: normalizeFilePath(it.file), fix: it.fix })
+    if (it && typeof it === 'object') out.push({ source, severity: it.severity, title: it.title || (typeof it === 'string' ? it : JSON.stringify(it)), file: normalizeFilePath(it.file), fix: it.fix })
     else out.push({ source, title: String(it) })
   }
```

**注意**：`if (it && typeof it === 'object')` 分支里 `it` 已是对象，`typeof it === 'string'` 恒为 false——但保留三元表达式是防御性写法（未来若调整分支条件，字符串路径仍安全）。更简洁的写法是直接 `JSON.stringify(it)`，但三元显式表达「字符串原样、对象序列化」的意图。两可，选后者更简洁：

```diff
-    if (it && typeof it === 'object') out.push({ source, severity: it.severity, title: it.title || String(it), file: normalizeFilePath(it.file), fix: it.fix })
+    if (it && typeof it === 'object') out.push({ source, severity: it.severity, title: it.title || JSON.stringify(it), file: normalizeFilePath(it.file), fix: it.fix })
```

（`else` 分支 `String(it)` 保持不变——字符串 `String('foo')` = `'foo'`，原样。）

- [ ] **Step 3: SYNC** — 无 spec/USAGE 改动。注释更新：`findingsOf` 函数注释补一行「兜底从 String(it) 改 JSON.stringify(it)（S7, 2026-07-08）：对象序列化为可读 JSON 而非零信息 [object Object]」。

- [ ] **Step 4: FULL regression + commit** — 跑全量测试。CRLF 修复。commit `fix(workflow): findingsOf 兜底 String(it) 改 JSON.stringify 保诊断价值`。

---

## Task 4: quality/hunter schema severity 加 required

**目标:** `qualityReviewSchema()`（[lib.js:967](file:///c:/Users/Alfred/Documents/projects/lottery-notification/docs/superpowers/workflows/lib.js#L967)）和 `SCHEMAS.hunter`（[lib.js:914](file:///c:/Users/Alfred/Documents/projects/lottery-notification/docs/superpowers/workflows/lib.js#L914) / [run-plans.js:854](file:///c:/Users/Alfred/Documents/projects/lottery-notification/.claude/workflows/run-plans.js#L854)）的 `items` 只 `required: ['title', 'fix']`，`severity` 未 required → LLM 可省略 severity → [formatFindingsHistory L128](file:///c:/Users/Alfred/Documents/projects/lottery-notification/.claude/workflows/run-plans.js#L128) 按 severity 排序（critical 优先）失效 → 弱模型先修 minor 漏 critical。prompt 已要求 severity（[L1082](file:///c:/Users/Alfred/Documents/projects/lottery-notification/.claude/workflows/run-plans.js#L1082) / [L1106](file:///c:/Users/Alfred/Documents/projects/lottery-notification/.claude/workflows/run-plans.js#L1106)），schema 应强制。

**Files:**
- Modify: `docs/superpowers/workflows/lib.js`（`qualityReviewSchema()` L967 + `SCHEMAS.hunter` L914）
- Modify: `.claude/workflows/run-plans.js`（inline 同步 L817 + L854）
- Modify: `docs/superpowers/workflows/tests/sync.test.js`（QC-4 SCHEMAS 字节守护含 severity required）

**Interfaces:**
- Produces: `qualityReviewSchema()` 和 `SCHEMAS.hunter` 的 `items.required` 从 `['title', 'fix']` 改为 `['title', 'fix', 'severity']`。

- [ ] **Step 1: Write failing tests (RED)** — `sync.test.js` 加断言：

```javascript
// quality/hunter schema severity 须 required（防 LLM 省略 severity → formatFindingsHistory 排序失效）
const qualSchemaFn = extractSchemas(runSrc).match(/function qualityReviewSchema\(\)\s*\{[\s\S]*?\n\}/)?.[0] || ''
assert.match(qualSchemaFn, /required:\s*\['title',\s*'fix',\s*'severity'\]/,
  'qualityReviewSchema items 须 required title+fix+severity')
const huntSchemaBlock = extractSchemas(runSrc).match(/hunter:\s*\{[\s\S]*?silent_failures[\s\S]*?\}\}/)?.[0] || ''
assert.match(huntSchemaBlock, /required:\s*\['title',\s*'fix',\s*'severity'\]/,
  'hunter silent_failures items 须 required title+fix+severity')
```

- [ ] **Step 2: Implement (GREEN)** — `lib.js` + `run-plans.js` 同步改两处：

`qualityReviewSchema()`（L967 附近）：
```diff
   issues: { type: 'array', items: {
-    type: 'object', required: ['title', 'fix'],
+    type: 'object', required: ['title', 'fix', 'severity'],
     properties: { severity: { type: 'string', enum: ['critical', 'important', 'minor'] }, title: { type: 'string' }, file: { type: 'string' }, fix: { type: 'string' } },
```

`SCHEMAS.hunter`（L914/L854 附近）：
```diff
   silent_failures: { type: 'array', items: {
-    type: 'object', required: ['title', 'fix'],
+    type: 'object', required: ['title', 'fix', 'severity'],
     properties: { title: { type: 'string' }, severity: { type: 'string', enum: ['critical', 'important', 'minor'] }, file: { type: 'string' }, line: { type: 'integer' }, fix: { type: 'string' } },
```

- [ ] **Step 3: SYNC** — 无 spec/USAGE 改动。注释更新：`qualityReviewSchema()` 和 `SCHEMAS.hunter` 注释补「severity 加 required（S7, 2026-07-08）：防 LLM 省略 severity → formatFindingsHistory L128 severity 排序失效」。

- [ ] **Step 4: FULL regression + commit** — 跑全量测试。CRLF 修复。commit `fix(workflow): quality/hunter schema severity 加 required 防排序失效`。

> **风险评估**：加 `severity` required 会让原本省略 severity 的 LLM 返回被 schema 拒 → runtime 强制重试。若 LLM 持续不返 severity 会触发 `model_unavailable`（限额耗尽）或 `agent_error`。但 prompt 已明确要求 severity（L1082/L1106），schema 加严只是与 prompt 对齐，不应增加重试率。若实测重试率上升，回滚此 Task（Task 1-3 仍独立有效）。

---

## Task 5: 文档同步 spec §6.1 + USAGE.md schema 严格化说明

**目标:** spec §6.1（[2026-07-08-refactor-audit-stage-design.md](file:///c:/Users/Alfred/Documents/projects/lottery-notification/docs/superpowers/specs/2026-07-08-refactor-audit-stage-design.md)）和 USAGE.md 同步说明 reviewer schema 严格化（specReview 独立 schema + quality/hunter severity required + findingsOf 兜底加固）。纯文档，无 TDD。

**Files:**
- Modify: `docs/superpowers/specs/2026-07-08-refactor-audit-stage-design.md`（§6.1 reviewer schema 章节）
- Modify: `docs/superpowers/workflows/USAGE.md`（§7 中断处理 或 §13 相关文件，补 reviewer schema 严格化说明）

**纯文档，无 TDD。**

- [ ] **Step 1: spec §6.1 补 reviewer schema 严格化说明** — 在 spec §6.1 现有「AUDIT_DIRECTIVE 常量导出」「haltLikelySource」「AUDIT_REFACTOR_KEYWORDS」等 bullet 附近，补一个 bullet 说明 reviewer schema 严格化：

```markdown
- reviewer schema 严格化（S7, 2026-07-08）：specReview 独立 `specReviewSchema()`（issues items 强制对象 `{dimension, title, fix, severity?, file?}`，对齐 prompt 对象模板），消除旧 `reviewSchema()` 宽松 `issues: {type:'array'}` 无 items 约束导致 LLM 返缺 title/fix 对象 → `findingsOf` 兜底 `[object Object]` 污染 `findings_history` 的 bug；quality/hunter schema `severity` 加 `required`（防 `formatFindingsHistory` severity 排序失效）；`findingsOf` 兜底 `String(it)` → `JSON.stringify(it)`（保诊断价值，治标兜底）。
```

- [ ] **Step 2: USAGE.md 补 reviewer schema 严格化说明** — 在 USAGE.md §7（中断处理）或合适位置，补一段说明 reviewer 返回结构约束：

```markdown
### reviewer 返回结构约束（schema 强制）

三类 reviewer（spec/quality/hunter）的 `issues`/`silent_failures` 须返对象数组，schema 强制 `required: ['title', 'fix', 'severity']`（quality/hunter）或 `required: ['title', 'fix']`（specReview，severity 可选但建议）。LLM 返字符串或缺字段对象会被 schema 拒，runtime 强制重试。若持续不合规 → `model_unavailable`/`agent_error` halt。`findingsOf` 兜底用 `JSON.stringify(it)`（非 `String(it)`），即使畸形对象也输出可读 JSON 而非 `[object Object]`。
```

- [ ] **Step 3: commit** — CRLF 修复。commit `docs(workflow): S7 reviewer schema 严格化同步 spec §6.1 + USAGE.md`。

---

## Self-Review

### 1. Spec coverage
- ✅ 根因（specReview schema 宽松）→ Task 1
- ✅ prompt 与 schema 对齐（字符串模板矛盾）→ Task 2
- ✅ 兜底加固（String(it) → JSON.stringify）→ Task 3
- ✅ quality/hunter 同源风险（severity 未 required）→ Task 4
- ✅ 文档同步 → Task 5

### 2. Placeholder scan
- 无 TBD/TODO
- 所有 Step 含完整代码
- 测试 fixture 用复刻用户 prompt 实测场景（缺 title 对象）

### 3. Type consistency
- `specReviewSchema()` 字段 `{dimension, severity, title, file, fix}` 与 specReview prompt Task 2 改后的 `issues:[{dimension, severity, title, file, fix}]` 对齐
- `findingsOf` 兜底 `JSON.stringify(it)` 与 helpers.test Task 3 断言 `match(/"dimension"\s*:\s*"EXTRA"/)` 对齐
- `qualityReviewSchema`/`hunter` 的 `required: ['title', 'fix', 'severity']` 与 sync.test Task 4 断言对齐

### 4. 执行顺序依赖
- Task 1 → Task 2 必须配套（schema 强制对象 + prompt 要求对象，缺一都会出问题）
- Task 3 独立（兜底加固，不依赖 Task 1/2）
- Task 4 独立（quality/hunter severity required，不依赖 Task 1-3）
- Task 5 依赖 Task 1-4 完成（文档同步总结）
- 建议执行顺序：T1 → T2 → T3 → T4 → T5
