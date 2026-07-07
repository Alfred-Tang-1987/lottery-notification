# Refactor 类 Task AUDIT 阶段 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 run-plans workflow 加一个 AUDIT 阶段——refactor 类 task 的 implementor 在 RED 前先核查 brief 对现状代码的假设，遇 brief 缺陷 halt 交 controller。

**Architecture:** 仅改 run-plans workflow 自身（lib.js + run-plans.js + 测试 + 文档），不动全局 writing-plans skill。核心：implementor PROMPTS 加 `{{auditDirective}}` 占位（buildPrompt 默认空串，非 refactor task 零影响）；bootstrap 解析 task `Type` 字段 + 扫 refactor 关键词 → `audit_required`；runTask 初始 dispatch 据此传 auditDirective；SCHEMAS + dispatchImpl 加 `needs_audit_fix` halt 分支。

**Tech Stack:** Node.js（`node --test`），两文件模型（lib.js 纯函数源 + run-plans.js inline 副本，sync.test 字节守护），CRLF 强制（`.gitattributes`）。

## Global Constraints

- **两文件模型**：lib.js 纯函数/常量/PROMPTS/SCHEMAS 是源（带 `export`）；run-plans.js inline 复制（无 `export`）。sync.test QC-4 `extractFunctionBody` 守护字节一致。每个新纯函数/常量进 lib.js + 同步 run-plans.js + 加 sync.test 守护。
- **CRLF 强制**：每批次 commit 前对修改的 .js/.md 文件执行 `perl -i -pe 's/(?<!\r)\n/\r\n/g' <file>`。sync.test 有 bare-LF 守护。
- **buildPrompt opt-out 语义**：`{ ...defaults, ...ctx }` 合并，调用方传空串显式关闭默认注入（与 QUOTA_HALT_NOTE 同模式）。
- **sentinel status**：`needs_audit_fix` 是 orchestrator-internal sentinel（与 `needs_context` 同层），不入 review schema enum，但**入 implementor schema enum**（implementor 需能返回它）。
- **通用性**：PROMPTS/常量不得含本项目专有路径/文件名（B1-10 通用性守护）。
- **基线测试**：350 green（本计划执行前）。
- **依据 spec**：`docs/superpowers/specs/2026-07-08-refactor-audit-stage-design.md`。

---

## Task 1: AUDIT_DIRECTIVE 常量 + haltLikelySource 映射

**目标:** 加 `AUDIT_DIRECTIVE` 常量（implementor 注入的核查指令 + 5 行表格模板 + 差异分级规则），haltLikelySource 加 `audit fix needed → unknown` 映射。这两个是后续 task 的依赖。

**Files:**
- Modify: `docs/superpowers/workflows/lib.js`（加常量 + haltLikelySource）
- Modify: `.claude/workflows/run-plans.js`（inline 同步）
- Modify: `docs/superpowers/workflows/tests/helpers.test.js`（import + 2 测试）
- Modify: `docs/superpowers/workflows/tests/sync.test.js`（QC-4b 常量守护 + haltLikelySource 已有守护无需改）

**Interfaces:**
- Produces: `export const AUDIT_DIRECTIVE`（lib.js，run-plans.js inline 无 export）；`haltLikelySource('audit fix needed') === 'unknown'`

- [ ] **Step 1: Write failing tests** — `helpers.test.js` import 列表加 `AUDIT_DIRECTIVE`；加 2 测试：

```javascript
test('AUDIT_DIRECTIVE: 常量导出 + 内容关键词', () => {
  assert.equal(typeof AUDIT_DIRECTIVE, 'string')
  assert.ok(AUDIT_DIRECTIVE.length > 100, '须是完整指令非空串')
  // 5 项清单关键词
  assert.ok(AUDIT_DIRECTIVE.includes('A1'), '须含 A1 site 数')
  assert.ok(AUDIT_DIRECTIVE.includes('A2'), '须含 A2 文本一致')
  assert.ok(AUDIT_DIRECTIVE.includes('A3'), '须含 A3 控制流')
  assert.ok(AUDIT_DIRECTIVE.includes('A4'), '须含 A4 行号')
  assert.ok(AUDIT_DIRECTIVE.includes('A5'), '须含 A5 字面量')
  // 差异分级关键词
  assert.ok(AUDIT_DIRECTIVE.includes('needs_audit_fix'), '须含 needs_audit_fix 状态')
  assert.ok(AUDIT_DIRECTIVE.includes('.audit/'), '须指示写 .audit/ 报告')
})

test('AUDIT_DIRECTIVE: haltLikelySource 映射 audit fix needed → unknown', () => {
  assert.equal(haltLikelySource('audit fix needed'), 'unknown')
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test docs/superpowers/workflows/tests/helpers.test.js`
Expected: FAIL — `AUDIT_DIRECTIVE is not defined`（import 报错）+ haltLikelySource 测试 FAIL（'audit fix needed' 无映射）。

- [ ] **Step 3: Write minimal implementation** — `lib.js` 在 `QUOTA_HALT_NOTE` 常量附近加：

```javascript
// AUDIT_DIRECTIVE（2026-07-08）：refactor 类 task implementor 在 RED 前核查 brief 现状假设的指令。
// buildPrompt 默认空串（非 refactor task 零影响）；audit_required 时调用方传此常量。
// A3 强制可审计：不管判断一致与否写推理记录，供下游 review 复查（spec §3.2）。
export const AUDIT_DIRECTIVE = `## Pre-RED Audit（此 task 标记为 refactor 类）
在写 RED 测试之前，先核查 brief 对现状代码的假设。对下表每项执行核查 + 填「实际」，产出写到 .audit/<taskKey>.md：

| 项 | 核查动作 | 什么算差异 |
|---|---|---|
| A1 site 数 | grep brief 声称的可替换 pattern，数实际命中 | brief 说 N 处，实际 M 处（M≠N）→ 差异 |
| A2 文本一致 | diff 各 site 的待去重文本 | 多类变体 → 差异 |
| A3 控制流 | 列出重构涉及的控制流关键路径（if/return/continue/break/短路/await 顺序，或被调函数返回值影响分支），trace 重构前后；读被调函数相关注释摘要。**不管判断一致与否，A3 推理过程必须写进报告（含 brief 声明 + 注释摘要 + 你的判断）** | brief 声明的控制流与实际不符 → 差异 |
| A4 行号/签名 | grep brief 提到的函数名/签名，核对行号 + 参数 | 仅行号漂移 → 无害；符号不存在 → 缺陷（按 A1 处理） |
| A5 字面量 | 提取 brief 给的目标字面量（reason/diag/string），与现状对应字段 diff | 字面量位置/内容不符 → 差异 |

差异分级响应：
- 无差异 / 仅 A4 行号漂移 → 进 RED。
- A1/A2/A5 差异且你能判定为「有意变体」（读代码确认设计意图，如 schema enum 含某状态）→ 报告标注「有意变体 + 理由」→ 进 RED。
- A1/A2/A3/A5 差异且判定为「brief 缺陷」→ STOP，status='needs_audit_fix'，diag 含差异清单。
- 拿不准是有意变体还是缺陷 → STOP，status='needs_audit_fix'（拿不准时阻断比强行实现安全）。`
```

`haltLikelySource`（lib.js）的 `implReasons` Set **不加** 'audit fix needed'（它不是 implementor 代码改动），让它落到末尾 `return 'unknown'`。无需改 haltLikelySource 代码——'audit fix needed' 自然不匹配任何分支 → 'unknown'。**但加显式注释**（在 `return 'unknown'` 前一行）：
```javascript
  // audit fix needed（refactor task AUDIT 差异 halt）不涉及工作树脏状态 → unknown（自然落空，无需加 Set）
```

- [ ] **Step 4: sync run-plans.js inline** — `run-plans.js` 加 byte-identical `AUDIT_DIRECTIVE` 副本（无 export）+ haltLikelySource 注释同步。sync.test QC-4 `extractFunctionBody` 不适用于 const（与 QUOTA_HALT_NOTE 同），加 QC-4b 风格守护（Step 5）。

- [ ] **Step 5: sync.test 加 AUDIT_DIRECTIVE 守护** — 参照 QUOTA_HALT_NOTE 的 QC-4b 模式，加常量存在性 + lib.js↔run-plans.js 字节一致守护：

```javascript
test('QC-4b AUDIT_DIRECTIVE: lib.js ↔ run-plans.js 字节一致', () => {
  const libM = libSrc.match(/export const AUDIT_DIRECTIVE = `([\s\S]*?)`/)
  const runM = runSrc.match(/const AUDIT_DIRECTIVE = `([\s\S]*?)`/)
  assert.ok(libM, 'lib.js 须含 AUDIT_DIRECTIVE 常量定义')
  assert.ok(runM, 'run-plans.js 须含 AUDIT_DIRECTIVE inline 副本')
  assert.equal(libM[1], runM[1], 'AUDIT_DIRECTIVE 字节一致')
})
```

- [ ] **Step 6: Run full test suite**

Run: `node --test docs/superpowers/workflows/tests/*.test.js`
Expected: 352 pass / 0 fail（350 + 2 新）。

- [ ] **Step 7: CRLF + commit**

```bash
perl -i -pe 's/(?<!\r)\n/\r\n/g' docs/superpowers/workflows/lib.js .claude/workflows/run-plans.js docs/superpowers/workflows/tests/helpers.test.js docs/superpowers/workflows/tests/sync.test.js
git add docs/superpowers/workflows/lib.js .claude/workflows/run-plans.js docs/superpowers/workflows/tests/helpers.test.js docs/superpowers/workflows/tests/sync.test.js
git commit -m "feat(workflow): AUDIT_DIRECTIVE 常量 + haltLikelySource audit 映射注释 (Task 1/5)"
```

---

## Task 2: SCHEMAS.implementor enum 加 needs_audit_fix + dispatchImpl halt 分支

**目标:** implementor schema status enum 加 `needs_audit_fix`（implementor 须能返回此 sentinel）；dispatchImpl 状态检查加 halt 分支。

**Files:**
- Modify: `docs/superpowers/workflows/lib.js`（SCHEMAS.implementor enum）
- Modify: `.claude/workflows/run-plans.js`（SCHEMAS inline + dispatchImpl 状态检查）
- Modify: `docs/superpowers/workflows/tests/dispatchImpl-retry.test.js`（源码字面量断言）

**Interfaces:**
- Consumes: Task 1 的 `AUDIT_DIRECTIVE`（无直接依赖，但同属 AUDIT 机制）
- Produces: `SCHEMAS.implementor` enum 含 `needs_audit_fix`；`dispatchImpl` 对 `impl.status === 'needs_audit_fix'` 返回 halt

- [ ] **Step 1: Write failing test** — `dispatchImpl-retry.test.js` 加源码字面量断言（参照该文件现有 P0-4 等模式）：

```javascript
test('AUDIT: dispatchImpl 对 needs_audit_fix 返回 halt', () => {
  // run-plans.js 的 dispatchImpl 状态检查须含 needs_audit_fix 分支
  assert.match(runSrc, /needs_audit_fix/,
    'run-plans.js 须处理 needs_audit_fix status（AUDIT 差异 halt）')
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test docs/superpowers/workflows/tests/dispatchImpl-retry.test.js`
Expected: FAIL — `needs_audit_fix` 未出现在 run-plans.js。

- [ ] **Step 3: Write minimal implementation**

`lib.js` SCHEMAS.implementor（约 line 897）enum 加 `needs_audit_fix`：
```javascript
status: { type: 'string', enum: ['ok', 'done_with_concerns', 'failed', 'blocked', 'needs_context', 'needs_audit_fix', 'model_unavailable'] },
```
（放在 needs_context 之后、model_unavailable 之前，语义分组：needs_* 类）

`run-plans.js`：
- SCHEMAS.implementor inline 副本同步加 `needs_audit_fix`。
- dispatchImpl 状态检查段（约 line 416，`if (impl?.status === 'model_unavailable')` 附近）加分支。**注意位置**：needs_audit_fix 须在 dispatchImpl 的**初始 dispatch 后**检查（不在 fix-round——fix-round 不跑 AUDIT）。但 dispatchImpl 是所有 implementor dispatch 的公共函数，fix-round 也走它。核查：fix-round dispatch 也经 dispatchImpl，若 fix-round implementor 返回 needs_audit_fix，dispatchImpl 也会 halt——这是**可接受的**（fix-round 不应返回 needs_audit_fix，因为 AUDIT 只在初始 dispatch 跑；若返回了说明 agent 误判，halt 暴露问题比静默继续安全）。所以在 dispatchImpl 公共路径加分支即可：

```javascript
if (impl?.status === 'needs_audit_fix') return { halted: true, reason: 'audit fix needed', diag: impl.diagnostics }
```
放在 `if (impl?.status === 'model_unavailable')` 分支**之前**（needs_audit_fix 比 model_unavailable 更具体，先查）。

- [ ] **Step 4: sync.test SCHEMAS 守护** — sync.test 已有 SCHEMAS 字节守护（QC 系列），两端 enum 同步加 `needs_audit_fix` 即可通过。**额外加一个 enum 内容断言**（防 enum 漏加）：

```javascript
test('AUDIT: SCHEMAS.implementor enum 含 needs_audit_fix', () => {
  assert.match(libSrc, /needs_audit_fix/,
    'lib.js SCHEMAS.implementor 须含 needs_audit_fix（AUDIT sentinel）')
  assert.match(runSrc, /needs_audit_fix/,
    'run-plans.js SCHEMAS inline 须同步 needs_audit_fix')
})
```
（可与 Step 1 的 dispatchImpl 断言合并到同一 test，或分开。）

- [ ] **Step 5: Run full test suite**

Run: `node --test docs/superpowers/workflows/tests/*.test.js`
Expected: 354 pass / 0 fail（352 + 2 新）。

- [ ] **Step 6: CRLF + commit**

```bash
perl -i -pe 's/(?<!\r)\n/\r\n/g' docs/superpowers/workflows/lib.js .claude/workflows/run-plans.js docs/superpowers/workflows/tests/dispatchImpl-retry.test.js docs/superpowers/workflows/tests/sync.test.js
git add -A
git commit -m "feat(workflow): SCHEMAS.implementor needs_audit_fix sentinel + dispatchImpl halt 分支 (Task 2/5)"
```

---

## Task 3: implementor PROMPTS 加 {{auditDirective}} 占位

**目标:** implementor prompt 模板加 `{{auditDirective}}` 占位符（在 `{{retryNote}}` 之后），buildPrompt 默认 `auditDirective: ''`（空串 → 占位消失，非 refactor task 零影响）。

**Files:**
- Modify: `docs/superpowers/workflows/lib.js`（PROMPTS.implementor + buildPrompt 默认值）
- Modify: `.claude/workflows/run-plans.js`（PROMPTS inline + buildPrompt inline）
- Modify: `docs/superpowers/workflows/tests/helpers.test.js`（2 测试：默认空串 + 传值注入）
- Modify: `docs/superpowers/workflows/tests/sync.test.js`（PROMPTS.implementor identical 守护两端同步）

**Interfaces:**
- Consumes: Task 1 的 `AUDIT_DIRECTIVE`
- Produces: `buildPrompt('implementor', {auditDirective: ...})` 注入；默认空串

- [ ] **Step 1: Write failing tests** — `helpers.test.js` 加 2 测试（参照 QUOTA_HALT_NOTE 测试模式）：

```javascript
test('AUDIT: buildPrompt implementor 默认 auditDirective 空串（非 refactor task 零影响）', () => {
  const out = buildPrompt('implementor', { taskId: 'T1', planId: '01', retryNote: '', fixIssues: '' })
  // 默认不注入 AUDIT_DIRECTIVE（占位被空串替换）
  assert.ok(!out.includes('Pre-RED Audit'), '默认不应含 AUDIT 指令（非 refactor task）')
})

test('AUDIT: buildPrompt implementor 传 auditDirective 注入', () => {
  const out = buildPrompt('implementor', { taskId: 'T1', planId: '01', retryNote: '', fixIssues: '', auditDirective: 'MARKER_AUDIT_TEST' })
  assert.ok(out.includes('MARKER_AUDIT_TEST'), '传 auditDirective 应注入占位')
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test docs/superpowers/workflows/tests/helpers.test.js`
Expected: FAIL — test 1：当前无 `{{auditDirective}}` 占位，但 implementor prompt 当前也不含 'Pre-RED Audit'，test 1 可能已 pass；test 2：当前无占位机制，MARKER 不注入 → FAIL。**若 test 1 已 pass 不影响**（test 2 是 RED 主证据）。

- [ ] **Step 3: Write minimal implementation**

`lib.js` PROMPTS.implementor（line 1013）加 `{{auditDirective}}` 占位——在 `{{retryNote}}` 之后、换行后、`## Discipline` 之前：
```javascript
implementor: `You are the IMPLEMENTOR for {{taskId}} (plan {{planId}}). TDD strict (RED→GREEN→REFACTOR). {{retryNote}}
{{auditDirective}}
## Discipline (HARD REQUIREMENTS — 违反会导致 workflow 状态混乱)
```

`lib.js` buildPrompt（约 line 94，当前 `const defaults = { quotaHaltNote: QUOTA_HALT_NOTE }`）defaults 加 `auditDirective`：
```javascript
const defaults = { quotaHaltNote: QUOTA_HALT_NOTE, auditDirective: '' }
```
**注**：auditDirective 默认**空串**（非 refactor task opt-out），与 quotaHaltNote（默认常量）相反——auditDirective 的默认是「不注入」。STATIC_READONLY_NOTE 是函数（调用方传参），不进 buildPrompt 默认，此处不涉及。

- [ ] **Step 4: sync run-plans.js inline** — PROMPTS.implementor + buildPrompt 两端同步加 `{{auditDirective}}` 占位 + `auditDirective: ''` 默认。

- [ ] **Step 5: sync.test 守护** — `PROMPTS.implementor identical` 测试两端同步即可（两端都加占位 → 仍 identical）。buildPrompt 字节守护（若 QC-4 含 buildPrompt）两端同步。

- [ ] **Step 6: Run full test suite**

Run: `node --test docs/superpowers/workflows/tests/*.test.js`
Expected: 356 pass / 0 fail（354 + 2 新）。

- [ ] **Step 7: CRLF + commit**

```bash
perl -i -pe 's/(?<!\r)\n/\r\n/g' docs/superpowers/workflows/lib.js .claude/workflows/run-plans.js docs/superpowers/workflows/tests/helpers.test.js docs/superpowers/workflows/tests/sync.test.js
git add -A
git commit -m "feat(workflow): implementor prompt {{auditDirective}} 占位 + buildPrompt 默认空串 (Task 3/5)"
```

---

## Task 4: bootstrap 解析 task Type + 关键词强制兜底 → audit_required

**目标:** bootstrap prompt 加「读 task `Type` 字段 + 扫 refactor 关键词 → audit_required」指示；bootstrap 返回的 tasks 数组每项加 `audit_required` 字段；runTask 读此字段。

**Files:**
- Modify: `docs/superpowers/workflows/lib.js`（bootstrap prompt step 3 + Return schema tasks 字段）
- Modify: `.claude/workflows/run-plans.js`（bootstrap inline + ensurePerTaskDefaults 加 audit_required + runTask 初始 dispatch 传 auditDirective）
- Modify: `docs/superpowers/workflows/tests/sync.test.js`（源码字面量断言）

**Interfaces:**
- Consumes: Task 1 `AUDIT_DIRECTIVE`、Task 3 `{{auditDirective}}` 占位
- Produces: `state.perTask[taskKey].audit_required`（布尔）；runTask 初始 dispatch implementor 传 `auditDirective`

- [ ] **Step 1: Write failing test** — `sync.test.js` 加 3 个源码字面量断言：

```javascript
test('AUDIT bootstrap: prompt 含 Type 字段解析 + refactor 关键词扫描指示', () => {
  assert.match(libSrc, /Type.*refactor|audit_required/i,
    'bootstrap prompt 须指示读 task Type 字段并判定 audit_required')
})

test('AUDIT bootstrap: Return schema tasks 含 audit_required 字段', () => {
  assert.match(libSrc, /audit_required/,
    'bootstrap Return schema tasks 须含 audit_required 字段')
})

test('AUDIT runTask: 初始 dispatch implementor 据 audit_required 传 auditDirective', () => {
  // 初始 dispatch（implCtx('', '')）处须传 auditDirective
  assert.match(runSrc, /auditDirective.*AUDIT_DIRECTIVE/,
    'runTask 初始 dispatch 须按 audit_required 传 AUDIT_DIRECTIVE')
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test docs/superpowers/workflows/tests/sync.test.js`
Expected: FAIL — 三处 `audit_required` / `auditDirective.*AUDIT_DIRECTIVE` 未出现。

- [ ] **Step 3: Write minimal implementation**

`lib.js` bootstrap prompt step 3（约 line 997，读 frontmatter tasks 那段）末尾加一句：
```
Also read each task's "Type" field from frontmatter (if present) and scan the task's brief text for refactor keywords (替换|去重|抽取|行为不变|逐字对齐|N 处可替换|refactor|extract). audit_required = (Type === 'refactor') OR (brief contains any refactor keyword). Return per task as "audit_required" (boolean, default false if Type absent and no keyword hit).
```

`lib.js` bootstrap Return schema（约 line 1010，`tasks:[{id, model, title, lesson_categories}]`）加 `audit_required`：
```
tasks:[{id, model, title, lesson_categories, audit_required}]
```

`run-plans.js`：
- bootstrap inline 同步（prompt + Return schema）。
- `ensurePerTaskDefaults`（约 line 1227，perTask 默认字段）加 `audit_required: false`（防 bootstrap 未返回时 undefined）。
- runTask 初始 dispatch（约 line 1450）传 auditDirective。当前：`buildPrompt('implementor', implCtx('', ''))`。改为：

```javascript
const auditRequired = state.perTask[tk].audit_required
impl = await dispatchImpl(buildPrompt('implementor', { ...implCtx('', ''), auditDirective: auditRequired ? AUDIT_DIRECTIVE : '' }), { schema: SCHEMAS.implementor, model, label: `impl:${task.id}` }, model, 'opus')
```

**注意**：`AUDIT_DIRECTIVE` 在 run-plans.js 是 inline const（无 export），直接引用即可。`auditDirective` 字段通过 spread 注入 implCtx 返回的对象（implCtx 本身不传 auditDirective，避免 fix-round/ctx-retry 等其他 dispatch 也注入——AUDIT 只在初始 dispatch 跑）。

**关键**：只改初始 dispatch（line 1450），**不改** fix-round（runFixRound 内 line 1368）/ blocked-retry（1456）/ ctx（1469）/ ctx-opus（1475）——这些是同 task 的后续 dispatch，AUDIT 已在初始 dispatch 跑过，不重复。

- [ ] **Step 4: finalReport prompt + ensurePerTaskDefaults 字段数更新** — `lib.js` finalReport prompt（约 line 1256）当前写 `ensurePerTaskDefaults 共 16 字段` 并列了 16 个字段名。加 `audit_required` 后须：
  - 字段清单加 `audit_required`（加在 `blocked_info` 之后或 `concerns` 附近，语义分组无强约束）
  - 计数 `16 字段` → `17 字段`
  - run-plans.js inline 同步
  核查命令：`grep -n "16 字段\|共 16" docs/superpowers/workflows/lib.js .claude/workflows/run-plans.js`
  ensurePerTaskDefaults 函数体（lib.js / run-plans.js inline）加 `audit_required: false` 默认。

- [ ] **Step 5: Run full test suite**

Run: `node --test docs/superpowers/workflows/tests/*.test.js`
Expected: 359 pass / 0 fail（356 + 3 新）。

- [ ] **Step 6: CRLF + commit**

```bash
perl -i -pe 's/(?<!\r)\n/\r\n/g' docs/superpowers/workflows/lib.js .claude/workflows/run-plans.js docs/superpowers/workflows/tests/sync.test.js
git add -A
git commit -m "feat(workflow): bootstrap Type 字段+关键词→audit_required + runTask 初始 dispatch 注入 (Task 4/5)"
```

---

## Task 5: 文档（workflow-design.md §5 AUDIT 阶段 + §13b implementor 行）+ .gitignore

**目标:** workflow-design.md 加 AUDIT 阶段说明（§5 + §13b implementor 角色行 + §4.4 perTask state 加 audit_required）；.gitignore 加 `.audit/`。

**Files:**
- Modify: `docs/superpowers/workflow-design.md`（§5 + §13b + §4.4）
- Modify: `.gitignore`（加 `.audit/`）

- [ ] **Step 1: workflow-design.md §5 加 AUDIT 阶段** — 在 §5 Task 执行流程的流程图后（约 line 290，`### 5.1` 之前）加一段：

```markdown
### 5.0 AUDIT 阶段（refactor 类 task，RED 前核查 brief 现状假设，2026-07-08）

**动机**：refactor/extract 类 task 的 brief 描述「现状代码的重复 pattern + 怎么改」，依赖对现状的假设。实测（2026-07-07 simplification 执行）7/7 spec/plan 缺陷出现在这类 task——brief 说「4 处可替换」实际 3 处、「文本一致」实际有变体、「行为不变」实际控制流须改。

**机制**：
1. bootstrap 解析 task `Type: refactor`（显式）或扫 brief refactor 关键词（强制兜底）→ `audit_required`。
2. runTask 初始 dispatch implementor 时，audit_required 则 `auditDirective: AUDIT_DIRECTIVE`（否则空串，非 refactor task 零影响）。
3. implementor 在 RED 前跑 5 项核查（A1 site 数 / A2 文本一致 / A3 控制流 / A4 行号 / A5 字面量），产出 `.audit/<taskKey>.md`。
4. 差异分级：无差异/仅 A4 漂移 → 进 RED；有意变体（读代码确认）→ 标注理由 + 进 RED；brief 缺陷/拿不准 → STOP 返回 `needs_audit_fix`，halt 交 controller。

**A3 强制可审计**：不管判断一致与否，A3 推理过程（控制流路径 + brief 声明 + 被调函数注释摘要 + 判断）必须写进 `.audit/` 报告，供下游 review 复查（语义漏检无法阻止，但可追溯）。

**局限**（详见 spec §5）：AUDIT 抓 A1/A2/A4/A5 数量/文本类差异；A3 语义差异靠 implementor 读代码判断 + 下游 review。`needs_audit_fix` halt 是人工/controller gate（与全自动模式张力，缓解靠「多数无差异通过」+「SDD controller 自动处理」）。
```

- [ ] **Step 2: workflow-design.md §13b implementor 行更新** — §13b 角色表（约 line 820+）implementor 行（约 line 23 `| implementor | modelHint || sonnet | TDD 实现 + self-review | 每 task |`）description 加「refactor 类 task 先 AUDIT」。

- [ ] **Step 3: workflow-design.md §4.4 perTask state 加 audit_required** — §4.4（约 line 187）perTask state 字段清单加 `audit_required`（布尔，bootstrap 解析）。

- [ ] **Step 4: .gitignore 加 .audit/**

```bash
echo ".audit/" >> .gitignore
```

- [ ] **Step 5: Run full test suite（回归——文档改不应影响测试，但确保 bare-LF 守护等不因 .gitignore 报错）**

Run: `node --test docs/superpowers/workflows/tests/*.test.js`
Expected: 359 pass / 0 fail（无新测试）。

- [ ] **Step 6: CRLF + commit**

```bash
perl -i -pe 's/(?<!\r)\n/\r\n/g' docs/superpowers/workflow-design.md
git add docs/superpowers/workflow-design.md .gitignore
git commit -m "docs(workflow): §5.0 AUDIT 阶段 + §13b implementor 行 + §4.4 audit_required + .gitignore (Task 5/5)"
```

---

## 完成标准

- [ ] 5 个 Task 全部 commit
- [ ] 359 tests green（350 → 359，+9：Task1×2 + Task2×2 + Task3×2 + Task4×3 + Task5×0）
- [ ] sync.test 守护覆盖 AUDIT_DIRECTIVE（QC-4b）+ SCHEMAS enum 含 needs_audit_fix + PROMPTS.implementor 含 {{auditDirective}} 占位
- [ ] PROMPTS/常量无本项目专有 token（B1-10 通用性守护通过）
- [ ] CRLF 行尾一致
- [ ] spec（2026-07-08-refactor-audit-stage-design.md）全部 §1-§5 落地：触发（Task4）/ 清单（Task1 AUDIT_DIRECTIVE）/ 产出分级（Task1+2）/ prompt 注入（Task3）/ halt（Task2）/ 文档（Task5）

## 依赖图

```
Task 1 (AUDIT_DIRECTIVE 常量 + haltLikelySource) ─┐
Task 2 (SCHEMAS needs_audit_fix + dispatchImpl halt) ─┤  独立，但都属 AUDIT 机制
Task 3 (implementor prompt {{auditDirective}} 占位) ─┤  依赖 Task 1（AUDIT_DIRECTIVE 引用）
        │
        ▼
Task 4 (bootstrap audit_required + runTask 注入) ── 依赖 Task 1 + 3
        │
        ▼
Task 5 (文档 + .gitignore) ── 依赖全前序（记录最终形态）
```

Task 1/2/3 可并行（独立改动），Task 4 依赖 1+3，Task 5 最后。SDD 执行建议串行 Task 1→2→3→4→5（每 task 全量回归）。
