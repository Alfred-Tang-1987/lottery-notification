# Refactor 类 Task AUDIT 阶段 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 run-plans workflow 加一个 AUDIT 阶段——refactor 类 task 的 implementor 在 RED 前先用 Grep/Read 工具核查 brief 对现状代码的假设，遇 brief 缺陷/工具失败/拿不准 halt 交 controller。

**Architecture:** 仅改 run-plans workflow 自身（lib.js + run-plans.js + 测试 + 文档），不动全局 writing-plans skill。核心：implementor PROMPTS 加 `{{auditDirective}}` 占位（buildPrompt defaults 默认空串，非 refactor task 零影响）；bootstrap 解析 task `Type` 字段（小写归一）+ 扫 refactor 关键词 → `audit_required`（双层 guard：bootstrap LLM + runtime 确定性正则兜底）；runTask 初始 dispatch 据此传 auditDirective；SCHEMAS + dispatchImpl 加 `needs_audit_fix` halt 分支（带 audit_reason 分类）。

**Tech Stack:** Node.js（`node --test`），两文件模型（lib.js 纯函数源 + run-plans.js inline 副本，sync.test 字节守护），CRLF 强制（`.gitattributes`）。AUDIT 核查用 claude code 的 Grep/Read 工具（确定性，不 shell）。

## Global Constraints

- **两文件模型**：lib.js 纯函数/常量/PROMPTS/SCHEMAS 是源（带 `export`）；run-plans.js inline 复制（无 `export`）。sync.test QC-4 `extractFunctionBody` 守护字节一致。每个新纯函数/常量进 lib.js + 同步 run-plans.js + 加 sync.test 守护。
- **CRLF 强制**：每批次 commit 前对修改的 .js/.md 文件执行 `perl -i -pe 's/(?<!\r)\n/\r\n/g' <file>`。sync.test 有 bare-LF 守护。
- **buildPrompt opt-out 语义**：`{ ...defaults, ...ctx }` 合并，`auditDirective` 进 defaults 为空串（非 refactor task 零影响）；调用方传 `AUDIT_DIRECTIVE` 常量启用。
- **sentinel status**：`needs_audit_fix` 是 orchestrator-internal sentinel（与 `needs_context` 同层），不入 review schema enum，但**入 implementor schema enum**（implementor 须能返回它）+ 带 `audit_reason` 字段。
- **工具约束（通用性 + claude code 规范）**：AUDIT 核查必须用 Grep（精确搜索）/ Read（读函数定义）工具，不得 shell（跨平台/安全）。AUDIT_DIRECTIVE 须给出工具名示例。
- **通用性**：PROMPTS/常量不得含本项目专有路径/文件名（B1-10 通用性守护）。
- **基线测试**：350 green（本计划执行前，从仓库根跑 `node --test docs/superpowers/workflows/tests/*.test.js`）。
- **依据 spec**：`docs/superpowers/specs/2026-07-08-refactor-audit-stage-design.md`（已过 CEO + Eng review）。

---

## Task 1: AUDIT_DIRECTIVE + AUDIT_REFACTOR_KEYWORDS 常量 + haltLikelySource

**目标:** 加 `AUDIT_DIRECTIVE`（核查指令 + 5 行表格 + 工具约束 + 分级规则含工具失败）+ `AUDIT_REFACTOR_KEYWORDS`（关键词正则源，供 Task 4 双层 guard 复用）；haltLikelySource 加注释。

**Files:**
- Modify: `docs/superpowers/workflows/lib.js`（加 2 常量 + haltLikelySource 注释）
- Modify: `.claude/workflows/run-plans.js`（inline 同步）
- Modify: `docs/superpowers/workflows/tests/helpers.test.js`（import + 测试）
- Modify: `docs/superpowers/workflows/tests/sync.test.js`（QC-4b 常量守护）

**Interfaces:**
- Produces: `export const AUDIT_DIRECTIVE`（string）；`export const AUDIT_REFACTOR_KEYWORDS`（RegExp 或 string，供 Task 4 双层 guard）；`haltLikelySource('audit fix needed') === 'unknown'`（自然落空，加注释）

- [ ] **Step 1: Write failing tests** — `helpers.test.js` import 加 `AUDIT_DIRECTIVE, AUDIT_REFACTOR_KEYWORDS`；加测试：

```javascript
test('AUDIT_DIRECTIVE: 常量导出 + 内容关键词', () => {
  assert.equal(typeof AUDIT_DIRECTIVE, 'string')
  assert.ok(AUDIT_DIRECTIVE.length > 100, '须是完整指令非空串')
  for (const k of ['A1', 'A2', 'A3', 'A4', 'A5']) assert.ok(AUDIT_DIRECTIVE.includes(k), `须含 ${k}`)
  assert.ok(AUDIT_DIRECTIVE.includes('needs_audit_fix'), '须含 needs_audit_fix 状态')
  assert.ok(AUDIT_DIRECTIVE.includes('.audit/'), '须指示写 .audit/ 报告')
  // 工具约束（D17）
  assert.ok(AUDIT_DIRECTIVE.includes('Grep'), '须指定 Grep 工具')
  assert.ok(AUDIT_DIRECTIVE.includes('Read'), '须指定 Read 工具')
  // 工具/写入失败也阻断（D11）
  assert.ok(AUDIT_DIRECTIVE.includes('工具') && AUDIT_DIRECTIVE.includes('失败'), '须含工具失败分级')
})

test('AUDIT_REFACTOR_KEYWORDS: 命中 refactor 词 + 不命中 feature 词', () => {
  assert.ok(AUDIT_REFACTOR_KEYWORDS.test('替换 4 处重复'), '命中 替换')
  assert.ok(AUDIT_REFACTOR_KEYWORDS.test('refactor the helper'), '命中 refactor')
  assert.ok(AUDIT_REFACTOR_KEYWORDS.test('extract pure function'), '命中 extract')
  assert.ok(!AUDIT_REFACTOR_KEYWORDS.test('add new login feature'), '不命中 feature 词')
})

test('AUDIT_DIRECTIVE: haltLikelySource audit fix needed → unknown', () => {
  assert.equal(haltLikelySource('audit fix needed'), 'unknown')
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test docs/superpowers/workflows/tests/helpers.test.js`
Expected: FAIL — `AUDIT_DIRECTIVE is not defined`（import 报错）。

- [ ] **Step 3: Write minimal implementation** — `lib.js` 在 `QUOTA_HALT_NOTE` 附近加：

```javascript
// AUDIT_REFACTOR_KEYWORDS（2026-07-08）：refactor 类 task 关键词，双层 guard 共用（bootstrap + runtime）。
// 命中 → audit_required=true。初版从 2026-07-07 simplification 7 处缺陷归纳，随实践迭代（D13）。
// 大小写不敏感（i 标志）。
export const AUDIT_REFACTOR_KEYWORDS = /(替换|去重|抽取|行为不变|逐字对齐|N 处可替换|refactor|extract)/i

// AUDIT_DIRECTIVE（2026-07-08）：refactor 类 task implementor 在 RED 前核查 brief 现状假设的指令。
// buildPrompt defaults 默认空串（非 refactor task 零影响）；audit_required 时调用方传此常量。
// 工具约束（D17）：必须用 Grep/Read，不得 shell。A3 强制可审计（D4/D12）。工具/写入失败也阻断（D11）。
export const AUDIT_DIRECTIVE = `## Pre-RED Audit（此 task 标记为 refactor 类）
在写 RED 测试之前，先用工具核查 brief 对现状代码的假设。对下表每项执行核查 + 填「实际」，产出写到 .audit/<taskKey>.md（覆盖写入；若 AUDIT 适用但报告缺失，不得进入 RED）：

| 项 | 核查动作（须用指定工具） | 什么算差异 |
|---|---|---|
| A1 site 数 | 用 Grep 工具精确搜索 brief 声称的 pattern（如 Grep "bareTaskId" 在目标文件），数实际命中 | brief 说 N 处，实际 M 处（M≠N）→ 差异 |
| A2 文本一致 | 用 Read 工具读取各 site 后 diff 待去重文本 | 多类变体 → 差异 |
| A3 控制流 | 列出重构涉及的控制流关键路径（if/return/continue/break/短路/await 顺序，或被调函数返回值影响分支），trace 重构前后；**用 Read 工具读取被调函数定义并摘录相关注释**。**不管判断一致与否，A3 推理过程必须写进报告（含 brief 声明 + 注释摘要 + 你的判断）** | brief 声明的控制流与实际不符 → 差异 |
| A4 行号/签名 | 用 Grep 搜索 brief 提到的函数名/签名，核对行号 + 参数 | 仅行号漂移 → 无害（记录即可）；符号不存在 → 缺陷（按 A1 处理） |
| A5 字面量 | 用 Read 工具提取 brief 给的目标字面量（reason/diag/string），与现状对应字段 diff | 字面量位置/内容不符 → 差异 |

工具约束：必须使用 Grep（精确搜索）和 Read（读函数定义）；不得用 shell 做字符串处理（跨平台/安全）。

差异分级响应：
- 无差异 / 仅 A4 行号漂移 → 进 RED。
- A1/A2/A5 差异且你能判定为「有意变体」——**必须有证据**（用 Read 读到的 schema 字段/注释/代码逻辑，能解释为何 brief 简化说法与现状不一致但仍合理；仅凭感觉不算）→ 报告标注「有意变体 + 证据」→ 进 RED。
- A1/A2/A3/A5 差异且判定为「brief 缺陷」→ STOP，status='needs_audit_fix'，diag 含 audit_reason='brief_defect' + 差异清单。
- 拿不准是有意变体还是缺陷 → STOP，status='needs_audit_fix'，audit_reason='intentional_variant_unclear'（拿不准时阻断比强行实现安全）。
- 工具执行失败（Grep/Read 报错）或 .audit/ 写入失败 → STOP，status='needs_audit_fix'，audit_reason='tool_failure'（无法核查时不能盲跑 RED）。`
```

`haltLikelySource`（lib.js）`return 'unknown'` 前加注释（不改逻辑——'audit fix needed' 自然落空到 unknown）：
```javascript
  // audit fix needed（refactor task AUDIT 差异 halt）不涉及工作树脏状态 → unknown（自然落空，无需加 Set）
```

- [ ] **Step 4: sync run-plans.js inline** — 加 byte-identical `AUDIT_DIRECTIVE` + `AUDIT_REFACTOR_KEYWORDS`（无 export）+ haltLikelySource 注释。

- [ ] **Step 5: sync.test QC-4b 守护** — 参照 QUOTA_HALT_NOTE 模式，加两常量 lib.js↔run-plans.js 字节一致守护：

```javascript
test('QC-4b AUDIT: AUDIT_DIRECTIVE + AUDIT_REFACTOR_KEYWORDS 两端字节一致', () => {
  for (const name of ['AUDIT_DIRECTIVE', 'AUDIT_REFACTOR_KEYWORDS']) {
    const libM = libSrc.match(new RegExp(`export const ${name} = ([\\s\\S]*?)(?:\\nexport |\\nconst |$)`))
    const runM = runSrc.match(new RegExp(`const ${name} = ([\\s\\S]*?)(?:\\nfunction |\\nconst |$)`))
    assert.ok(libM, `lib.js 须含 ${name}`)
    assert.ok(runM, `run-plans.js 须含 ${name} inline`)
    assert.equal(libM[1], runM[1], `${name} 字节一致`)
  }
})
```
（实施时核对正则边界——AUDIT_DIRECTIVE 是 template literal `` ` ``，AUDIT_REFACTOR_KEYWORDS 是 RegExp literal `/.../ `，提取方式不同。）

- [ ] **Step 6: Run full test suite**

Run: `node --test docs/superpowers/workflows/tests/*.test.js`
Expected: 353 pass / 0 fail（350 + 3）。

- [ ] **Step 7: CRLF + commit**

```bash
perl -i -pe 's/(?<!\r)\n/\r\n/g' docs/superpowers/workflows/lib.js .claude/workflows/run-plans.js docs/superpowers/workflows/tests/helpers.test.js docs/superpowers/workflows/tests/sync.test.js
git add -A && git commit -m "feat(workflow): AUDIT_DIRECTIVE + AUDIT_REFACTOR_KEYWORDS 常量 + haltLikelySource 注释 (Task 1/5)"
```

---

## Task 2: SCHEMAS needs_audit_fix + audit_reason + dispatchImpl halt + blocked.md 文案

**目标:** implementor schema enum 加 `needs_audit_fix` + `audit_reason` 字段（enum: brief_defect/intentional_variant_unclear/tool_failure）；dispatchImpl halt 分支 diag 含 audit_reason；finalReport prompt（blocked.md 模板）按 audit_reason 给分类诊断。

**Files:**
- Modify: `docs/superpowers/workflows/lib.js`（SCHEMAS.implementor + finalReport prompt blocked.md 段）
- Modify: `.claude/workflows/run-plans.js`（SCHEMAS inline + dispatchImpl 状态检查）
- Modify: `docs/superpowers/workflows/tests/dispatchImpl-retry.test.js`（源码字面量断言）

**Interfaces:**
- Produces: `SCHEMAS.implementor` enum 含 `needs_audit_fix` + `audit_reason` 字段；dispatchImpl 对 `needs_audit_fix` halt，diag 含 audit_reason

- [ ] **Step 1: Write failing test** — `dispatchImpl-retry.test.js` 加断言：

```javascript
test('AUDIT: dispatchImpl 对 needs_audit_fix 返回 halt + diag 含 audit_reason', () => {
  assert.match(runSrc, /needs_audit_fix/, 'run-plans.js 须处理 needs_audit_fix status')
  assert.match(runSrc, /audit_reason/, 'dispatchImpl halt diag 须含 audit_reason 字段')
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test docs/superpowers/workflows/tests/dispatchImpl-retry.test.js`
Expected: FAIL — `needs_audit_fix` / `audit_reason` 未出现。

- [ ] **Step 3: Write minimal implementation**

`lib.js` SCHEMAS.implementor（约 line 897）enum 加 `needs_audit_fix` + properties 加 `audit_reason`：
```javascript
status: { type: 'string', enum: ['ok', 'done_with_concerns', 'failed', 'blocked', 'needs_context', 'needs_audit_fix', 'model_unavailable'] },
audit_reason: { type: 'string', enum: ['brief_defect', 'intentional_variant_unclear', 'tool_failure'] },
```
（audit_reason 加在 diagnostics 同级或顶层——核查 SCHEMAS.implementor 结构，audit_reason 应是顶层字段便于 dispatchImpl 直接读 `impl.audit_reason`，不放 diagnostics 内。）

`run-plans.js`：
- SCHEMAS.implementor inline 同步。
- dispatchImpl 状态检查（约 line 416）加分支，**放在 model_unavailable 之前**（更具体先查）：
```javascript
if (impl?.status === 'needs_audit_fix') return { halted: true, reason: 'audit fix needed', diag: { ...impl.diagnostics, audit_reason: impl.audit_reason, taskKey: impl.taskKey } }
```

`lib.js` finalReport prompt（约 line 1063，`If mode=halted: write .workflow/blocked.md` 段）加 audit fix needed 分类诊断。在现有 blocked.md 渲染指示后加：
```
If blocked_info.reason === 'audit fix needed'（refactor task AUDIT 阶段发现 brief 缺陷）：按 blocked_info.diag.audit_reason 分类渲染：
- brief_defect: "## AUDIT: Brief 与现状代码不一致\n差异清单: <diag 中的差异项>。\nAction: 修正 plan brief 后 resume，bootstrap 会重读重审。"
- intentional_variant_unclear: "## AUDIT: 无法判定是否有意变体\n差异: <...>。\nAction: 确认是有意变体（在 brief 标注理由）还是缺陷（修 brief），resume。"
- tool_failure: "## AUDIT: 核查工具执行失败\n失败原因: <diag>。\nAction: 检查文件系统/工具可用性后 resume。"
```
run-plans.js inline 同步。

- [ ] **Step 4: sync.test SCHEMAS 守护** — 加 enum 内容断言：

```javascript
test('AUDIT: SCHEMAS.implementor 含 needs_audit_fix + audit_reason', () => {
  assert.match(libSrc, /needs_audit_fix/, 'lib.js SCHEMAS.implementor 须含 needs_audit_fix')
  assert.match(libSrc, /audit_reason/, 'lib.js SCHEMAS.implementor 须含 audit_reason 字段')
  for (const r of ['brief_defect', 'intentional_variant_unclear', 'tool_failure']) {
    assert.match(libSrc, new RegExp(r), `lib.js 须含 audit_reason 枚举 ${r}`)
  }
})
```

- [ ] **Step 5: Run full test suite**

Run: `node --test docs/superpowers/workflows/tests/*.test.js`
Expected: 355 pass / 0 fail（353 + 2）。

- [ ] **Step 6: CRLF + commit**

```bash
perl -i -pe 's/(?<!\r)\n/\r\n/g' docs/superpowers/workflows/lib.js .claude/workflows/run-plans.js docs/superpowers/workflows/tests/dispatchImpl-retry.test.js docs/superpowers/workflows/tests/sync.test.js
git add -A && git commit -m "feat(workflow): SCHEMAS needs_audit_fix+audit_reason + dispatchImpl halt + blocked.md 分类诊断 (Task 2/5)"
```

---

## Task 3: implementor PROMPTS {{auditDirective}} 占位 + buildPrompt defaults

**目标:** implementor prompt 加 `{{auditDirective}}` 占位（retryNote 之后）；buildPrompt defaults 加 `auditDirective: ''`（空串默认）。

**Files:**
- Modify: `docs/superpowers/workflows/lib.js`（PROMPTS.implementor + buildPrompt）
- Modify: `.claude/workflows/run-plans.js`（inline 同步）
- Modify: `docs/superpowers/workflows/tests/helpers.test.js`（2 测试：provided 注入 / 空串不注入；**不测 `{}` 占位符保留**——与 defaults 冲突，见 spec §6.1 修正说明）

**Interfaces:**
- Consumes: Task 1 `AUDIT_DIRECTIVE`
- Produces: `buildPrompt('implementor', {auditDirective: ...})` 注入

- [ ] **Step 1: Write failing tests** — `helpers.test.js` 加 2 测试（两态，非三态）：

```javascript
test('AUDIT: buildPrompt implementor 传 auditDirective 注入', () => {
  const out = buildPrompt('implementor', { taskId: 'T1', planId: '01', retryNote: '', fixIssues: '', auditDirective: 'MARKER_AUDIT_TEST' })
  assert.ok(out.includes('MARKER_AUDIT_TEST'), '传 auditDirective 应注入占位')
})

test('AUDIT: buildPrompt implementor auditDirective 默认空串（非 refactor 无残留）', () => {
  // auditDirective 进 defaults 为空串 → {auditDirective:''} 或 {} 都渲染为空串，无 {{auditDirective}} 残留
  const out = buildPrompt('implementor', { taskId: 'T1', planId: '01', retryNote: '', fixIssues: '' })
  assert.ok(!out.includes('Pre-RED Audit'), '默认不应含 AUDIT 指令')
  assert.ok(!out.includes('{{auditDirective}}'), '默认无占位符残留（prompt 清洁）')
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test docs/superpowers/workflows/tests/helpers.test.js`
Expected: FAIL — test 1（当前无占位机制，MARKER 不注入）；test 2 当前可能 pass（无占位也无残留）——test 1 是 RED 主证据。

- [ ] **Step 3: Write minimal implementation**

`lib.js` PROMPTS.implementor（line 1013）加占位——`{{retryNote}}` 后、`## Discipline` 前：
```javascript
implementor: `You are the IMPLEMENTOR for {{taskId}} (plan {{planId}}). TDD strict (RED→GREEN→REFACTOR). {{retryNote}}
{{auditDirective}}
## Discipline (HARD REQUIREMENTS — 违反会导致 workflow 状态混乱)
```

`lib.js` buildPrompt（line 94，当前 `const defaults = { quotaHaltNote: QUOTA_HALT_NOTE }`）加 auditDirective：
```javascript
const defaults = { quotaHaltNote: QUOTA_HALT_NOTE, auditDirective: '' }
```
（auditDirective 默认**空串**——非 refactor task opt-out。STATIC_READONLY_NOTE 是函数不进默认，不涉及。）

- [ ] **Step 4: sync run-plans.js inline** — PROMPTS.implementor + buildPrompt 两端同步。

- [ ] **Step 5: sync.test 守护** — `PROMPTS.implementor identical` 两端同步即可。

- [ ] **Step 6: Run full test suite**

Run: `node --test docs/superpowers/workflows/tests/*.test.js`
Expected: 357 pass / 0 fail（355 + 2）。

- [ ] **Step 7: CRLF + commit**

```bash
perl -i -pe 's/(?<!\r)\n/\r\n/g' docs/superpowers/workflows/lib.js .claude/workflows/run-plans.js docs/superpowers/workflows/tests/helpers.test.js docs/superpowers/workflows/tests/sync.test.js
git add -A && git commit -m "feat(workflow): implementor prompt {{auditDirective}} 占位 + buildPrompt defaults 空串 (Task 3/5)"
```

---

## Task 4: bootstrap audit_required（双层 guard）+ runTask 初始 dispatch 注入 + runtime fallback

**目标:** bootstrap prompt 加「读 task Type（小写归一）+ 扫 refactor 关键词 → audit_required」；runTask 初始 dispatch 传 auditDirective；**runtime 确定性兜底**（bootstrap 漏读时 runTask 用 AUDIT_REFACTOR_KEYWORDS 正则重算）。

**Files:**
- Modify: `docs/superpowers/workflows/lib.js`（bootstrap prompt step 3 + Return schema + finalReport 字段清单）
- Modify: `.claude/workflows/run-plans.js`（bootstrap inline + ensurePerTaskDefaults + runTask 初始 dispatch + runtime fallback）
- Modify: `docs/superpowers/workflows/tests/sync.test.js`（源码字面量断言）

**Interfaces:**
- Consumes: Task 1 `AUDIT_DIRECTIVE` + `AUDIT_REFACTOR_KEYWORDS`、Task 3 `{{auditDirective}}` 占位
- Produces: `state.perTask[taskKey].audit_required`（布尔）；runTask 初始 dispatch 注入 auditDirective

- [ ] **Step 1: Write failing tests** — `sync.test.js` 加源码字面量断言：

```javascript
test('AUDIT bootstrap: prompt 含 Type 字段小写归一 + 关键词扫描指示', () => {
  assert.match(libSrc, /Type.*refactor.*audit_required|audit_required.*Type.*refactor/is,
    'bootstrap prompt 须指示读 task Type 字段（小写归一）并判定 audit_required')
  assert.match(libSrc, /toLowerCase/, '须含 Type 字段小写归一（D9）')
})

test('AUDIT bootstrap: Return schema tasks 含 audit_required 字段', () => {
  assert.match(libSrc, /audit_required/, 'bootstrap Return schema tasks 须含 audit_required')
})

test('AUDIT runTask: 初始 dispatch 据 audit_required 传 auditDirective + runtime fallback 正则', () => {
  assert.match(runSrc, /auditDirective.*AUDIT_DIRECTIVE/, 'runTask 初始 dispatch 须传 AUDIT_DIRECTIVE')
  assert.match(runSrc, /AUDIT_REFACTOR_KEYWORDS/, 'runTask 须含 runtime fallback 正则（D15）')
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test docs/superpowers/workflows/tests/sync.test.js`
Expected: FAIL — audit_required / toLowerCase / AUDIT_REFACTOR_KEYWORDS（runTask 内）未出现。

- [ ] **Step 3: Write minimal implementation**

`lib.js` bootstrap prompt step 3（约 line 997）末尾加：
```
Also read each task's "Type" field from frontmatter (if present), normalize via trim().toLowerCase(), and scan the task's brief text for refactor keywords (用 AUDIT_REFACTOR_KEYWORDS 正则). audit_required = (type === 'refactor') OR (brief matches keyword regex). Return per task as "audit_required" (boolean, default false).
```

`lib.js` bootstrap Return schema（约 line 1010）tasks 加 `audit_required`：
```
tasks:[{id, model, title, lesson_categories, audit_required}]
```

`run-plans.js`：
- bootstrap inline 同步（prompt + Return schema）。
- `ensurePerTaskDefaults`（约 line 1227）加 `audit_required: false`。
- finalReport prompt（约 line 1256）`ensurePerTaskDefaults 共 16 字段` → `17 字段`，清单加 `audit_required`（run-plans.js inline 同步）。
- runTask 初始 dispatch（约 line 1450）+ runtime fallback。当前：`buildPrompt('implementor', implCtx('', ''))`。改为：

```javascript
// audit_required 双层 guard：bootstrap 输出 + runtime 确定性兜底（D15）
// bootstrap 漏读 frontmatter 或幻觉时，runtime 用 AUDIT_REFACTOR_KEYWORDS 重算
const briefText = `${task.title || ''} ${task.model || ''}` // brief 摘要（frontmatter 字段 + title；完整 brief 在 plan 文件，此处用可得字段）
const auditRequired = state.perTask[tk].audit_required || AUDIT_REFACTOR_KEYWORDS.test(briefText)
state.perTask[tk].audit_required = auditRequired // 回填，持久化
impl = await dispatchImpl(buildPrompt('implementor', { ...implCtx('', ''), auditDirective: auditRequired ? AUDIT_DIRECTIVE : '' }), { schema: SCHEMAS.implementor, model, label: `impl:${task.id}` }, model, 'opus')
```

**关键设计点**：
- runtime fallback 的 brief 文本——runTask 能拿到的是 task 的 frontmatter 字段（title/model/id）+ plan 文件路径。完整 brief 在 plan 文件 body，runTask 不读（它是 JS orchestrator，不读 plan md 内容）。所以 fallback 正则扫的是**可得字段**（title 等），不是完整 brief。这是 fallback 的局限——它只能兜"title/frontmatter 含 refactor 词"的 case，不能兜"brief body 含词但 title 不含"。bootstrap（LLM）读完整 brief 才是主路径。**在注释里说清这个局限**。
- 只改初始 dispatch（line 1450），不改 fix-round/blocked-retry/ctx 等（AUDIT 只跑一次）。

- [ ] **Step 4: Run full test suite**

Run: `node --test docs/superpowers/workflows/tests/*.test.js`
Expected: 360 pass / 0 fail（357 + 3）。

- [ ] **Step 5: CRLF + commit**

```bash
perl -i -pe 's/(?<!\r)\n/\r\n/g' docs/superpowers/workflows/lib.js .claude/workflows/run-plans.js docs/superpowers/workflows/tests/sync.test.js
git add -A && git commit -m "feat(workflow): bootstrap audit_required 双层 guard + runTask 初始 dispatch 注入 + runtime fallback (Task 4/5)"
```

---

## Task 5: 文档（workflow-design.md §13l + §5 + §13b + §4.4）+ .gitignore

**目标:** workflow-design.md 新增 §13l「AUDIT 阶段」（流程图/状态机/与 review/halt/resume 交互）+ §5 AUDIT 阶段说明 + §13b implementor 行 + §4.4 perTask state 加 audit_required；.gitignore 加 `.audit/`。

**Files:**
- Modify: `docs/superpowers/workflow-design.md`（§13l 新增 + §5 + §13b + §4.4）
- Modify: `.gitignore`

- [ ] **Step 1: workflow-design.md §13l 新增 AUDIT 阶段** — 在 §13b（约 line 820）之后加 §13l，内容覆盖：
  - 触发机制（Type: refactor + 关键词强制兜底 + 双层 guard）
  - 5 项核查清单（A1-A5）+ 工具约束（Grep/Read）
  - 产出（.audit/<taskKey>.md，覆盖写，缺失不 RED）
  - 差异分级（无差异/有意变体带证据/brief 缺陷/拿不准/工具失败 → needs_audit_fix + audit_reason）
  - A3 强制可审计
  - 状态机：audit_required → implementor AUDIT → (无差异|有意变体→RED | 缺陷/拿不准/工具失败→needs_audit_fix halt→controller 修 brief→resume→重审)
  - 与现有机制交互：复用 halt/blocked.md/resume；haltLikelySource audit→unknown；blocked.md 按 audit_reason 分类
  - 局限（§5.1 语义漏检靠 A3 可追溯；§5.2 关键词迭代；§5.4 needs_audit_fix 是人工 gate）

- [ ] **Step 2: §5 加 AUDIT 阶段说明** — §5 Task 执行流程流程图后（约 line 290）加 §5.0（精简版，指向 §13l 详见）。

- [ ] **Step 3: §13b implementor 行 + §4.4 audit_required** — implementor 行 description 加「refactor 类 task 先 AUDIT」；§4.4 perTask state 字段清单加 `audit_required`。

- [ ] **Step 4: .gitignore 加 .audit/**

```bash
echo ".audit/" >> .gitignore
```

- [ ] **Step 5: Run full test suite（回归）**

Run: `node --test docs/superpowers/workflows/tests/*.test.js`
Expected: 360 pass / 0 fail（无新测试）。

- [ ] **Step 6: CRLF + commit**

```bash
perl -i -pe 's/(?<!\r)\n/\r\n/g' docs/superpowers/workflow-design.md
git add docs/superpowers/workflow-design.md .gitignore && git commit -m "docs(workflow): §13l AUDIT 阶段 + §5.0 + §13b + §4.4 audit_required + .gitignore (Task 5/5)"
```

---

## 完成标准

- [ ] 5 个 Task 全部 commit
- [ ] 360 tests green（350 → 360，+10：Task1×3 + Task2×2 + Task3×2 + Task4×3 + Task5×0）
- [ ] sync.test 守护覆盖 AUDIT_DIRECTIVE + AUDIT_REFACTOR_KEYWORDS（QC-4b）+ SCHEMAS enum 含 needs_audit_fix/audit_reason + PROMPTS.implementor 含 {{auditDirective}}
- [ ] PROMPTS/常量无本项目专有 token（B1-10 通用性守护通过）
- [ ] CRLF 行尾一致
- [ ] **手动端到端验证（spec §6.5 line 211）**：用 mini-plan（1 refactor task + 1 feature task）跑 run-plans，确认：(a) refactor task 触发 AUDIT；(b) feature task 不触发；(c) 故意写错 brief 的 refactor task 触发 needs_audit_fix halt。此为集成验证，不在 `node --test` 内（layered-test 盲区，靠手动）。

## 依赖图

```
Task 1 (AUDIT_DIRECTIVE + AUDIT_REFACTOR_KEYWORDS + haltLikelySource) ─┐
Task 2 (SCHEMAS needs_audit_fix+audit_reason + dispatchImpl + blocked.md) ─┤  独立
Task 3 (implementor prompt {{auditDirective}} + buildPrompt defaults) ─┤  依赖 Task 1（AUDIT_DIRECTIVE 引用）
        │
        ▼
Task 4 (bootstrap audit_required 双层 guard + runTask 注入 + runtime fallback) ── 依赖 Task 1 + 3
        │
        ▼
Task 5 (文档 §13l + §5 + §13b + §4.4 + .gitignore) ── 依赖全前序
```

Task 1/2/3 可并行（独立），Task 4 依赖 1+3，Task 5 最后。SDD 执行建议串行 Task 1→2→3→4→5（每 task 全量回归）。
