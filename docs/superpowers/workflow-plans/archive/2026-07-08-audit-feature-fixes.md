# Audit Feature Review 修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 `workflow-simplification-tdd-fix` feature 三维审计（Spec Review / Silent-failure Hunter / Code Review）+ Claude 独立复核（`docs/superpowers/workflows/research/audit-feature-review-2026-07-08.md` §8）确认的 2 个 P0 + 4 个 P1。核心是堵住两类「被吞的真实 halt reason」——AUDIT halt 分类诊断能渲染（P0-1）、retry 路径不漏 needs_audit_fix（P0-2）、review budget halt 不因 diagnostics 缺失 TypeError 被顶层 catch 吞（P1-5，与 P0-2 同源）；外加 schema taskKey 闭环（P1-4）与三处文档同步（P1-2/P1-3/P1-6）。

**Architecture:** 仅改 run-plans workflow 自身，遵守既有 §4.3 分层——纯决策函数进 lib.js（+ run-plans.js inline + sync.test 字节守护）、runtime 胶水（halt/dispatchImpl）留 run-plans.js、SCHEMAS 进 lib.js。修复坚持 TDD（RED→GREEN→SYNC→FULL），不破坏通用性（B1-10 守护：PROMPTS 不含项目专有词），不改变 agent 调用数。

**Tech Stack:** Node.js（`node --test`），两文件模型（lib.js 纯函数源 + run-plans.js inline 副本，sync.test QC-4 `extractFunctionBody` 守护字节一致），CRLF 强制。源码字面量断言用 `extractDispatchImpl`/`extractFunctionBody` 提取函数体防假绿（既有模式）。

## Global Constraints

- **两文件模型**：lib.js 纯函数/常量/SCHEMAS 是源（带 `export`）；run-plans.js inline 复制（无 `export`）。改 lib.js 的纯函数/SCHEMAS 必须同步 run-plans.js inline 副本 + sync.test 守护。改 run-plans.js 独有 runtime（halt/dispatchImpl）只动 run-plans.js。
- **CRLF 强制**：每 Task commit 前对修改的 .js/.md 文件执行 `perl -i -pe 's/(?<!\r)\n/\r\n/g' <file>`。sync.test 有 bare-LF 守护。
- **基线测试**：**361 green**（本计划执行前，从仓库根跑 `node --test docs/superpowers/workflows/tests/*.test.js` 确认）。
- **不破坏通用性**：B1-10 守护——PROMPTS 不得含本项目专有路径/文件名（lottery/notification/lessons.md）。
- **不改变 agent 调用数**：所有修复是补字段/补分支/补 `?.`，不增减 dispatchImpl 调用、不增减 agent 调用。
- **依据**：`docs/superpowers/workflows/research/audit-feature-review-2026-07-08.md` §8（Claude 独立验证裁定，2026-07-08 11:11）。

---

## Task 1: P0-1 halt() blocked_info 加顶层 diag 字段

**目标:** `halt()` 构建的 `blocked_info` 当前只有 `raw: r.diag`（run-plans.js:1333），但 finalReport blocked.md 模板（:1203-1206）读 `blocked_info.diag.audit_reason`——路径断裂，AUDIT halt 分类诊断（brief_defect / intentional_variant_unclear / tool_failure）无法渲染。加顶层 `diag: r.diag || {}` 字段。

**Files:**
- Modify: `.claude/workflows/run-plans.js`（halt() 的 blocked_info，runtime 胶水只在此文件）

**Interfaces:**
- Produces: `blocked_info` 同时含 `raw`（全量 dump，保留）与 `diag`（与模板读取路径对齐的结构化诊断键）两个顶层字段。

- [ ] **Step 1: Write failing tests (RED)** — `docs/superpowers/workflows/tests/sync.test.js` 加断言。halt 是 run-plans.js 独有 runtime，用源码字面量断言：

```javascript
test('P0-1: halt() blocked_info 须含顶层 diag 字段（与 finalReport 模板读取路径对齐）', () => {
  // finalReport blocked.md 模板读 blocked_info.diag.audit_reason（audit fix needed halt 分类渲染）。
  // halt() 须把 r.diag 同时写入 raw（全量 dump）与 diag（结构化键），否则 audit_reason 分类诊断永不渲染。
  assert.match(runSrc, /blocked_info:\s*\{[\s\S]*?diag:\s*r\.diag \|\| \{\}/,
    'halt() blocked_info 须含顶层 diag: r.diag || {}（与 finalReport 模板 blocked_info.diag.audit_reason 对齐）')
})
```

- [ ] **Step 2: Implement (GREEN)** — `.claude/workflows/run-plans.js` halt() 的 `blocked_info`（约 :1325-1334），在 `raw: r.diag || {},` 后加一行 `diag: r.diag || {},`。两个键并存：`raw` 保留为全量兜底 dump（现有消费方不破），`diag` 是与模板对齐的结构化键。

```diff
     failed_approach: { task_id: tid, reason: r.reason, error: r.diag?.last_error || r.reason },
     raw: r.diag || {},
+    diag: r.diag || {},
   }
```

- [ ] **Step 3: SYNC** — 无 spec/USAGE 改动（halt 内部结构，不对外）。注释更新：halt() 内加一行注释说明 `diag` 与 `raw` 的职责区分。

- [ ] **Step 4: FULL regression + commit** — 跑 `node --test docs/superpowers/workflows/tests/*.test.js`（从仓库根），确认全绿（361 + 1）。CRLF 修复。commit `fix(workflow): P0-1 halt blocked_info 加顶层 diag 字段修复 AUDIT 分类诊断渲染断裂`。

---

## Task 2: P0-2 dispatchImpl retry 路径补 needs_audit_fix 检查

**目标:** `dispatchImpl` retry 分支（run-plans.js:499-501）retry 升 opus 后只查 `model_unavailable`，漏 `needs_audit_fix`。当首次 dispatch 因 router 限额被吞为 null → retry 升 opus → opus 跑 AUDIT 发现 brief 缺陷返回 `needs_audit_fix` → retry 分支当成功 `return impl` → 调用方 `if (impl.halted)` 拿到非 halted 对象 → 悄悄穿过所有 status 分支进入 review loop，在错误工作树上跑。补与首层（:486）对称的检查。

**Files:**
- Modify: `.claude/workflows/run-plans.js`（dispatchImpl retry 分支，runtime 胶水只在此文件）
- Modify: `docs/superpowers/workflows/tests/dispatchImpl-retry.test.js`（RED 守护）

**Interfaces:**
- Produces: dispatchImpl retry 分支（`if (impl != null)` 块内）在 `model_unavailable` 检查**之前**加 `needs_audit_fix` 检查，返回同首层的 halt 结构。

- [ ] **Step 1: Write failing tests (RED)** — `dispatchImpl-retry.test.js` 加测试，仿既有「AUDIT: dispatchImpl 对 needs_audit_fix 返回 halt」测试（:134-146）的 `extractDispatchImpl` 函数体断言模式：

```javascript
test('P0-2: dispatchImpl retry 路径须检查 needs_audit_fix（与首层对称）', () => {
  // retry 升 opus 跑 AUDIT 发现 brief 缺陷返回 needs_audit_fix 时，
  // retry 分支不得当成功 return impl——须与首层（:486）对称 halt。
  // 否则：首次 null（限额吞）→ retry opus → AUDIT needs_audit_fix → 当成功 → review loop 在错误工作树上跑。
  const body = extractDispatchImpl(runSrc)
  // retry 块（impl = await agent(prompt, { ...opts, model: retryModel }) 之后）
  const retryAgentIdx = body.indexOf('model: retryModel')
  assert.ok(retryAgentIdx > -1, '须有 retry 分支（model: retryModel）')
  const retryBlock = body.slice(retryAgentIdx)
  // retry 块内须有 needs_audit_fix 检查，且在 model_unavailable 之前
  const retryAuditIdx = retryBlock.indexOf("impl?.status === 'needs_audit_fix'")
  const retryMuIdx = retryBlock.indexOf("impl?.status === 'model_unavailable'")
  assert.ok(retryAuditIdx > -1, 'retry 路径须有 needs_audit_fix status 检查')
  assert.ok(retryMuIdx > -1, 'retry 路径须有 model_unavailable status 检查')
  assert.ok(retryAuditIdx < retryMuIdx,
    'retry 路径 needs_audit_fix 检查必须在 model_unavailable 之前（与首层对称）')
})
```

- [ ] **Step 2: Implement (GREEN)** — `.claude/workflows/run-plans.js` dispatchImpl retry 分支（约 :499-502），在 `if (impl?.status === 'model_unavailable')` 之前插入 needs_audit_fix 检查（与首层 :486 同构）：

```diff
       if (impl != null) {
+        if (impl?.status === 'needs_audit_fix') return { halted: true, reason: 'audit fix needed', diag: { ...impl.diagnostics, audit_reason: impl.audit_reason, taskKey: impl.taskKey } }
         if (impl?.status === 'model_unavailable') return { halted: true, reason: 'model_unavailable', diag: impl.diagnostics }
         return impl
       }
```

- [ ] **Step 3: SYNC** — 无 spec/USAGE 改动（dispatchImpl 内部）。注释更新：retry 块内加一行说明「P0-2：retry 路径须与首层对称检查 needs_audit_fix，防 AUDIT halt 被 retry 吞」。

- [ ] **Step 4: FULL regression + commit** — 跑全量测试（361 + 2）。CRLF 修复。commit `fix(workflow): P0-2 dispatchImpl retry 路径补 needs_audit_fix 检查防 AUDIT halt 被吞`。

> **副作用说明**：此 Task 同时**消除** Hunter P1-1（checkImplStatus retry/context-fetch 路径漏 needs_audit_fix 短路）。因为 dispatchImpl 是所有 implementor dispatch 的唯一入口（含 retry/context-fetch/initial），needs_audit_fix 在 dispatchImpl 内即 halt，永远流不到 checkImplStatus。无需单独改 checkImplStatus（复核 §8.1 已裁定）。

---

## Task 3: P1-4 SCHEMAS.implementor 加 taskKey 字段

**目标:** dispatchImpl halt diag 读 `impl.taskKey`（:486），但 `SCHEMAS.implementor`（lib.js + run-plans.js :839-843）未要求该字段 → implementor 多半不回传 → halt diag 里 `taskKey: undefined` → blocked.md 缺 `.audit/<taskKey>.md` 定位。补 schema 字段。影响小（`blocked_info.task`=tid 已能定位 task），但补齐是闭环。

**Files:**
- Modify: `docs/superpowers/workflows/lib.js`（SCHEMAS.implementor properties）
- Modify: `.claude/workflows/run-plans.js`（inline 同步）
- Modify: `docs/superpowers/workflows/tests/sync.test.js`（QC-4 SCHEMAS 字节守护含新字段）

**Interfaces:**
- Produces: `SCHEMAS.implementor.properties.taskKey`（string，描述「当前 task 的 plan-scoped key，如 plan-04/T4；needs_audit_fix 时回传供 blocked.md 定位 .audit/ 报告」）。

- [ ] **Step 1: Write failing tests (RED)** — `sync.test.js` 加断言（QC-4 模式，extractSchemas 提取 + 字节一致）：

```javascript
test('P1-4: SCHEMAS.implementor 须含 taskKey 字段（needs_audit_fix 定位 .audit/ 报告）', () => {
  const libSchemas = extractSchemas(libSrc)
  const runSchemas = extractSchemas(runSrc)
  for (const [name, src] of [['lib.js', libSchemas], ['run-plans.js', runSchemas]]) {
    const impl = src.match(/implementor:\s*\{[\s\S]*?\n\s{4}\}/)?.[0] || ''
    assert.match(impl, /taskKey:\s*\{\s*type:\s*'string'/,
      `${name} SCHEMAS.implementor 须含 taskKey 字段（string）`)
  }
})
```

- [ ] **Step 2: Implement (GREEN)** — lib.js SCHEMAS.implementor properties（约 :840-843），在 `audit_reason` 后加：

```diff
     audit_reason: { type: 'string', enum: ['brief_defect', 'intentional_variant_unclear', 'tool_failure'] },
+    taskKey: { type: 'string', description: 'plan-scoped task key (e.g. plan-04/T4); echo back on needs_audit_fix so blocked.md can locate .audit/<taskKey>.md' },
```

run-plans.js inline 同步（字节一致，含 description）。AUDIT_DIRECTIVE 文本（lib.js 常量）已指示 implementor 在 needs_audit_fix 时写 `.audit/<taskKey>.md`，再补一句「回传 taskKey」（若现有指令未明示）。

- [ ] **Step 3: SYNC** — spec `2026-07-08-refactor-audit-stage-design.md` §4.3（:130）`audit_reason` 字段描述后补「+ taskKey 字段（plan-scoped key，needs_audit_fix 时回传）」。

- [ ] **Step 4: FULL regression + commit** — 跑全量测试（361 + 3）。CRLF 修复。commit `fix(workflow): P1-4 SCHEMAS.implementor 加 taskKey 字段闭环 AUDIT halt 定位`。

---

## Task 4: P1-5 decideReviewOutcome budget/maxRounds halt 用 ?. 访问 diagnostics（与 P0-2 同源）

**目标:** `decideReviewOutcome`（lib.js:792/794 + run-plans.js inline 同步）budget/maxRounds halt 分支用非 `?.` 访问 `spec.diagnostics`/`qual.diagnostics`/`hunt.diagnostics`。若 review 对象存在但 `.diagnostics` 为 undefined（SCHEMAS 允许 `diagnostics: {type:'object'}` 不强制，LLM 偶省略）→ TypeError → 顶层 catch 吞为笼统 agent_error → 真实 `review_not_converging`/`review max rounds` reason 丢失，blocked.md 误导用户「瞬态失败重跑即可」而无限重试。

**为什么这是 P1 而非 P2（2026-07-08 11:27 提级）**：与 P0-2 同源——都是「被吞的真实 halt reason」。证据链：(1) helpers.test:1410/1421 注释**逐字自承**「budget/maxRounds halt 分支用 qual.diagnostics/hunt.diagnostics（非 ?.），须传真实 review 对象」=作者已知不安全、靠约定兜底；(2) 三个相关测试（:1408/:1419/:1480）**全部构造带 diagnostics 的对象**，`diagnostics: undefined` 路径**零覆盖**，今天 green 纯属约定未被破坏（测试假绿）；(3) TypeError 后果是 debug 路径完全跑偏且不可见（无日志、测试不覆盖）。触发条件比 P0-2 窄（需 diagnostics 缺失），但一旦触发用户会被误导。修 P1-5 就是堵 P0-2 的同源漏洞，紧跟 Task 2 之后。

**Files:**
- Modify: `docs/superpowers/workflows/lib.js`（decideReviewOutcome :792/794）
- Modify: `.claude/workflows/run-plans.js`（inline 同步 :190-192）
- Modify: `docs/superpowers/workflows/tests/helpers.test.js`（:1408-1425 注释 + 加 RED）

**Interfaces:**
- Produces: decideReviewOutcome 两个 budget/maxRounds halt 分支的 diag 用 `spec?.diagnostics`/`qual?.diagnostics`/`hunt?.diagnostics`（与 :773-774 reviewReason/emptyFailed 分支一致）。

- [ ] **Step 1: Write failing tests (RED)** — `helpers.test.js` 现有两个测试（:1408 `maxRounds=0 budget guard`、:1419 `round===maxRounds`）的注释「须传真实 review 对象（非 ?.）」删除（不再需要约定）。加一个新 RED：

```javascript
test('P1-5: decideReviewOutcome budget halt 对 diagnostics 缺失的 review 不 TypeError（防御性 ?.）', () => {
  // SCHEMAS review diagnostics 非强制（LLM 偶省略）。budget/maxRounds halt 须用 ?.
  // 与 reviewReason/emptyFailed 分支（spec?.diagnostics）一致，防 TypeError 被顶层 catch 吞为 agent_error。
  const state = makeState({ /* perTask 初始化 */ })
  const reviewNoDiag = { status: 'failed' }  // 无 diagnostics 字段
  // 不应 throw
  const out = decideReviewOutcome(state, 'plan-01/T1', 5, reviewNoDiag, reviewNoDiag, reviewNoDiag,
    'sonnet', 0, {}, null, null)  // maxRounds=0, round>=budget(5)
  assert.equal(out.action, 'halt')
  assert.equal(out.reason, 'review_not_converging')
  assert.equal(out.diag.spec, undefined)  // ?. 兜底，非 TypeError
  assert.equal(out.diag.qual, undefined)
})
```

- [ ] **Step 2: Implement (GREEN)** — lib.js decideReviewOutcome（:792/794）：

```diff
-    if (round >= budget) return { action: 'halt', reason: 'review_not_converging', diag: { round, budget, findings_history: state.perTask[taskKey].findings_history, spec: spec.diagnostics, qual: qual.diagnostics, hunt: hunt.diagnostics } }
+    if (round >= budget) return { action: 'halt', reason: 'review_not_converging', diag: { round, budget, findings_history: state.perTask[taskKey].findings_history, spec: spec?.diagnostics, qual: qual?.diagnostics, hunt: hunt?.diagnostics } }
   } else if (round === maxRounds) {
-    return { action: 'halt', reason: 'review max rounds', diag: { round, findings_history: state.perTask[taskKey].findings_history, spec: spec.diagnostics, qual: qual.diagnostics, hunt: hunt.diagnostics } }
+    return { action: 'halt', reason: 'review max rounds', diag: { round, findings_history: state.perTask[taskKey].findings_history, spec: spec?.diagnostics, qual: qual?.diagnostics, hunt: hunt?.diagnostics } }
```

run-plans.js inline 副本同步（sync.test QC-4 extractFunctionBody 字节守护，自动校验）。

- [ ] **Step 3: SYNC** — spec `2026-07-07-simplification-tdd-fix-design.md` §5（或 workflow-design.md decideReviewOutcome 说明）无强制改动（`?.` 是实现细节）。可选：helpers.test :1410/1421 旧注释改为「review.diagnostics 可选（schema 非强制），用 ?. 兜底」。

- [ ] **Step 4: FULL regression + commit** — 跑全量测试（361 + 4）。注意：sync.test QC-4 decideReviewOutcome 字节守护会自动验证 inline 一致。CRLF 修复。commit `fix(workflow): P1-5 decideReviewOutcome budget/maxRounds halt 用 ?. 防 diagnostics 缺失 TypeError`。

---

## Task 5: P1-6 spec §4.2/§1 "两层独立 guard" 措辞软化

**目标:** Code Review 指出 spec §4.2（:126）/§1（:42）「两层独立 guard」措辞言过其实——两层（bootstrap LLM + runtime 正则）共享相同关键词盲点，runtime fallback 只扫 `task.title`/`task.model`（不扫 brief body，run-plans.js:1490-1491 自承）。实现已诚实记录（workflow-design.md §13l :1065），spec 需软化对齐。

**Files:**
- Modify: `docs/superpowers/specs/2026-07-08-refactor-audit-stage-design.md`（§1 :42 + §4.2 :126）

**纯文档，无 TDD。**

- [ ] **Step 1: 改 §1（:42）** — 「双层扫描职责区分」段，把「后者负责验证前者没有被幻觉或 frontmatter 解析错误绕过」补充诚实边界：

```diff
> **双层扫描职责区分**：第一层是 bootstrap agent 读取 `Type` 字段并扫描 brief 关键词（主路径，利用 LLM 理解能力）；第二层是 bootstrap 输出后的**确定性正则校验 guard**（运行时兜底，不依赖 LLM）。两者不是重复：前者负责生成结构化状态，后者负责验证前者没有被幻觉或 frontmatter 解析错误绕过。第二层只校验第一层的输出，不替代第一层的解析。
+>
+> **诚实边界（Code Review 2026-07-08）**：两层共享相同的关键词列表盲点（非标准措辞如「统一构造」代替「去重」会两层都漏）。runtime fallback 只扫 `task.title`/`task.model` 等 frontmatter 可得字段，**不扫 plan 文件 body 内的 brief**（实现 run-plans.js:1490-1491、workflow-design.md §13l 已记录）。故「独立」指职责分层（LLM vs 确定性正则），非覆盖面互补；brief body 含关键词但 title 不含的 case 主要靠 bootstrap LLM。
```

- [ ] **Step 2: 改 §4.2（:126）** — 「runTask 运行时兜底」段末句「运行时的计算与 bootstrap 的 LLM 输出是两层独立 guard」改为：

```diff
-运行时的计算与 bootstrap 的 LLM 输出是两层独立 guard。
+运行时的计算与 bootstrap 的 LLM 输出是两层职责分层的 guard（LLM 理解 vs 确定性正则），共享相同关键词盲点，runtime fallback 覆盖面限于 frontmatter 可得字段（见 §1 诚实边界）。
```

- [ ] **Step 3: SYNC** — workflow-design.md §13l（:1065 附近）已有 runtime fallback 局限记录，确认与 spec §1 诚实边界措辞一致，无需改（若不一致则对齐）。

- [ ] **Step 4: commit** — 无测试。CRLF 修复。commit `docs(workflow): P1-6 spec §1/§4.2 软化"两层独立 guard"措辞补诚实边界`。

---

## Task 6: P1-2 USAGE.md 同步 AUDIT 阶段文档

**目标:** USAGE.md 无任何 AUDIT/needs_audit_fix/.audit/Type:refactor 提及。feature B（AUDIT 阶段）新增 halt reason 未补用户文档（feature A 的 headVerifier 已补）。blocked.md 渲染本身可操作（用户不会卡住），但文档一致性缺失。补 §7（限额/中断处理）+ §7.1（续跑）。

**Files:**
- Modify: `docs/superpowers/workflows/USAGE.md`（§7 / §7.1）

**纯文档，无 TDD。**

- [ ] **Step 1: §7（中断处理）加 AUDIT halt 子节** — 在现有 halt reason 列表后加：

```markdown
### AUDIT halt（refactor task brief 与现状不一致）

refactor 类 task（plan frontmatter `Type: refactor`，或 brief 含 `替换/去重/抽取/refactor/extract` 等词）在 RED 前 implementor 会先跑 AUDIT 核查 brief 对现状代码的假设。遇以下情况 halt，`reason: audit fix needed`：

- **brief_defect**：brief 的现状假设与代码不符（如声称「4 处可替换」实际 3 处）。Action：修正 plan brief 后全新跑续，bootstrap 会重读重审。
- **intentional_variant_unclear**：brief 简化说法与现状不完全一致，但无法判定是有意变体还是缺陷。Action：在 brief 标注理由（有意变体）或修正（缺陷）后续跑。
- **tool_failure**：AUDIT 的 Grep/Read 工具执行失败或 `.audit/` 写入失败。Action：检查文件系统/工具可用性后续跑。

现场核查记录在 `.audit/<taskKey>.md`（git 忽略，跨 session 不保证存在）。halt 详情见 `.workflow/blocked.md`。
```

- [ ] **Step 2: §7.1（续跑）补一句** — 在「手动修完 review-halt 的 task 后继续」段附近，补 AUDIT halt 同走「全新跑」流程（与所有 halt 一致，无需特殊路径）：

```markdown
> AUDIT halt（`audit fix needed`）同此流程：修 brief → commit（若有代码改动）→ 全新跑续。bootstrap 重读重审，无需 resumeFromRunId。
```

- [ ] **Step 3: §13（相关文件）补 `.audit/`** — 文件清单加 `.audit/<taskKey>.md`（临时观测，git 忽略）。

- [ ] **Step 4: commit** — 无测试。CRLF 修复。commit `docs(workflow): P1-2 USAGE.md 同步 AUDIT 阶段（audit fix needed/.audit/Type:refactor）`。

---

## Task 7: P1-3 spec §6.1 消除 A3 报告格式测试与免责声明矛盾

**目标:** spec `2026-07-08-refactor-audit-stage-design.md` §6.1（:184）要求「A3 报告格式测试：给定含控制流重构的 brief，生成的 .audit/<taskKey>.md 必须包含四段」，但同节 :213 自承「AUDIT 清单本身的执行（implementor 实际跑 Grep/Read）无法单测（依赖真实代码状态）」。两处矛盾。实现遵循 :213（未实现该测试，逻辑正确）。改 :184 标注消除矛盾。

**Files:**
- Modify: `docs/superpowers/specs/2026-07-08-refactor-audit-stage-design.md`（§6.1 :184）

**纯文档，无 TDD。**

- [ ] **Step 1: 改 §6.1（:184）** — A3 报告格式测试项标注不可单测，指向 :213 + 端到端验证：

```diff
-A3 报告格式测试：给定含控制流重构的 brief，生成的 `.audit/<taskKey>.md` 必须包含「关键路径」「brief 声明」「注释/被调函数摘要」「判断」四段
+A3 报告格式测试：**不可单测**（.audit/ 报告由 implementor 现场生成，依赖真实代码状态，见本节末 layered-test 盲区注）。改为断言 `AUDIT_DIRECTIVE` 常量文本含「关键路径」「brief 声明」「注释/被调函数摘要」「判断」四段关键词（Task 1 helpers.test 已实现，确保指令指示 implementor 写全四段）；实际报告格式靠端到端 mini-plan 验证（§6.5 完成标准 (a)(b)(c)）。
```

- [ ] **Step 2: 确认 helpers.test 已覆盖** — Task 1 的 AUDIT_DIRECTIVE 内容断言（helpers.test）应已含 A3 四段关键词；若未含，补断言（属 Task 1 范畴，此 Task 仅改 spec 对齐）。

- [ ] **Step 3: commit** — 无测试。CRLF 修复。commit `docs(workflow): P1-3 spec §6.1 消除 A3 报告格式测试与免责声明矛盾`。

---

## 全局收尾

- [ ] **跑全量回归**：`node --test docs/superpowers/workflows/tests/*.test.js`，确认 361 + 7 = **368 green**（Task 1 +1, Task 2 +1, Task 3 +1, Task 4 +1，实际新增 RED 测试 4 个；Task 1 的 helpers.test 断言若在原 feature 已有则不计增量）。
- [ ] **更新复核文档**：`docs/superpowers/workflows/research/audit-feature-review-2026-07-08.md` §8 末补「修复完成」状态（每个 Task commit 后勾选）。
- [ ] **更新 MEMORY**：若用户要求，把 AUDIT halt 链路修复经验写入项目 memory（dispatchImpl 双路径对称、halt blocked_info diag/raw 双键）。

## 不在范围（复核 §8.2 裁定不修）

- Hunter P1-5（revert diag 漏 commitSha）：**不成立**，run-plans.js:1457 已有 commitSha。
- Hunter P1-3（fix-round 不传 auditDirective）：**不成立**，AUDIT 是 Pre-RED 核查，设计上只首轮跑。
- Hunter P1-4（validateAmendResult sha 当 error）：**降 P2**，罕见 case，不阻塞。
- Spec P1-1（三层 guard 缺中间层）：**降 P2**，spec §1/§4.2 措辞已在 Task 5 澄清，不加非必要 runtime 胶水。
- Spec P1-2（基线 350）：**不成立**，feature 前确为 350。
- Hunter P2-1/P2-2/P2-3（checkSimplify 自洽/escalate 副作用/runtime fallback 不扫 body）：观察项，不修。
