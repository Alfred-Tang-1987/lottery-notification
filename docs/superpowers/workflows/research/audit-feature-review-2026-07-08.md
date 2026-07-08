# AUDIT Feature 三维复核复核报告

**日期**：2026-07-08
**复核对象**：`workflow-simplification-tdd-fix` 分支（a6f63eb，361 tests green）
**复核范围**：Spec Review + Silent-failure Hunter + Code Review 三维审计发现的问题
**复核分支**：`workflow-simplification-tdd-fix`（AUDIT feature 所在分支，非 `main`）

---

## 0. 复核环境纠正

复核过程中发现工作树之前因 `git checkout 218a039~1` 后 `git checkout main` 失败，停留在 detached HEAD（bf57540，AUDIT 之前的状态），导致第一轮 grep 全部误判为"代码缺失"。已切回 `workflow-simplification-tdd-fix`（a6f63eb，361 green）重新核实。**所有结论基于正确分支**。

---

## 1. 确认成立 — 必须修（2 项 P0）

### P0-1（Spec Review）：AUDIT halt 链路字段路径断裂

**状态**：✅ 确认成立

**证据**：
- `.claude/workflows/run-plans.js:486` — `dispatchImpl` 把 audit_reason 放进 `r.diag`：
  ```js
  if (impl?.status === 'needs_audit_fix') return { halted: true, reason: 'audit fix needed', diag: { ...impl.diagnostics, audit_reason: impl.audit_reason, taskKey: impl.taskKey } }
  ```
- `.claude/workflows/run-plans.js:1325-1334` — `halt()` 构建的 `blocked_info` 只有 `raw: r.diag || {}`，**没有 `diag` 顶层字段**：
  ```js
  blocked_info: {
    plan: plan?.id, task: tid, reason: r.reason,
    category: r.diag?.blocked_category || ...,
    last_error: r.diag?.last_error || ...,
    suggested_fix: r.diag?.suggested_fix || null,
    quota_exhausted: r.reason === 'model_unavailable',
    likely_source: haltLikelySource(r.reason),
    failed_approach: { task_id: tid, reason: r.reason, error: r.diag?.last_error || r.reason },
    raw: r.diag || {},
  }
  ```
- `.claude/workflows/run-plans.js:1203` — `blocked.md` 模板读的是 `blocked_info.diag.audit_reason`：
  ```
  If blocked_info.reason === 'audit fix needed': 按 blocked_info.diag.audit_reason 分类渲染
  ```

**影响**：audit fix needed halt 时，分类诊断文案（brief_defect / intentional_variant_unclear / tool_failure）无法渲染，用户看不到具体 action。`raw.audit_reason` 存在但模板读的是 `diag.audit_reason`，路径断裂。

**修复建议**：在 `halt()` 的 `blocked_info` 中加 `diag: r.diag || {}` 顶层字段。

---

### P0-2（Hunter P0-1）：dispatchImpl retry 路径漏查 needs_audit_fix

**状态**：✅ 确认成立

**证据**：`.claude/workflows/run-plans.js:499-501` — retry 路径只查 model_unavailable：
```js
if (impl != null) {
  if (impl?.status === 'model_unavailable') return { halted: true, reason: 'model_unavailable', diag: impl.diagnostics }
  return impl  // ← 漏查 needs_audit_fix
}
```
首次调用（L486）有 `needs_audit_fix` 检查，但 retry 路径（L499-501）只查 `model_unavailable`。

**影响**：refactor task 首次 sonnet 返回 null（能力不足 / 400 Repetitive tool calls）→ retry opus → opus 跑 AUDIT 发现 brief 缺陷返回 `needs_audit_fix` → **被当成功 return impl** → 在错误工作树上跑 review loop。这完全绕过了 AUDIT 的阻断保护。

**修复建议**：retry 路径补与首次分支对称的 `needs_audit_fix` 检查（L500 后加一行）：
```js
if (impl?.status === 'needs_audit_fix') return { halted: true, reason: 'audit fix needed', diag: { ...impl.diagnostics, audit_reason: impl.audit_reason, taskKey: impl.taskKey } }
```

---

## 2. 确认成立 — 应修（P1）

### P1-1（Hunter P1-2）：checkImplStatus 不含 needs_audit_fix 短路

**状态**：✅ 确认（P0-2 衍生，修 P0-2 后自动消失）

`checkImplStatus`（L346-348）的 `allowed` 默认 `['ok', 'done_with_concerns']`。若 P0-2 未修，retry 后 `needs_audit_fix` 流到 checkImplStatus → reason 变成 `implementor needs_audit_fix after ...` → blocked.md 不渲染 AUDIT 分类。

**修复 P0-2 即可消除此问题**。

---

### P1-2（Spec P1-3 / Code review 2）：USAGE.md 未同步 AUDIT 阶段

**状态**：✅ 确认成立

`docs/superpowers/workflows/USAGE.md` grep `audit|AUDIT|needs_audit_fix|Type: refactor` 无匹配。功能 A（headVerifier）加了 USAGE 条目，功能 B（AUDIT）没加。

**影响**：文档一致性缺失。blocked.md 渲染本身可操作（用户不会被卡），但用户在 USAGE 中找不到 `needs_audit_fix` halt 原因的说明。

**修复建议**：USAGE.md 补 AUDIT / needs_audit_fix / Type: refactor 条目。

---

### P1-3（Spec P1-4）：spec §6.1 A3 报告格式测试与 line 213 矛盾

**状态**：✅ 确认成立

- `specs/2026-07-08-refactor-audit-stage-design.md:184` 要求："A3 报告格式测试：给定含控制流重构的 brief，生成的 `.audit/<taskKey>.md` 必须包含「关键路径」「brief 声明」「注释/被调函数摘要」「判断」四段"
- `specs/2026-07-08-refactor-audit-stage-design.md:213` 说："AUDIT 清单本身的执行（implementor 实际跑 Grep/Read）无法单测（依赖真实代码状态）"

`.audit/<taskKey>.md` 是 implementor agent 生成的文件，helpers.test.js 纯函数测试无法跑 agent。**spec 内部矛盾**。

**修复建议**：改 L184 为"prompt 断言：AUDIT_DIRECTIVE 含四段格式指示"。

---

### P1-4（Hunter P1-1）：schema 未要求 taskKey

**状态**：✅ 确认（影响小）

`SCHEMAS.implementor`（L839-843）有 `needs_audit_fix` + `audit_reason` 但无 `taskKey`。`dispatchImpl`（L486）读 `impl.taskKey`。schema 不保证 → agent 可能不返回 → `diag.taskKey=undefined`。

**影响小**：`blocked_info.task`（tid）已有，用户能定位 .audit 文件。

**修复建议**：schema 加 `taskKey` 或 dispatchImpl 用 tid fallback。

---

### P1-5（Hunter P0-2 降级）：decideReviewOutcome 用非 ?. 访问 spec.diagnostics

**状态**：✅ 确认（防御性一致性，当前不会 TypeError）

`run-plans.js:190/192` 用 `spec.diagnostics`，而 L171 用 `spec?.diagnostics`。

**当前不会 TypeError**：到 L190 时 spec 非 null（reviewHaltReason 会把 null status 拦截为 review_empty halt）。但风格不一致，未来代码变更可能让 null 流到此处。

**修复建议**：加 ?. 保持一致。

---

### P1-6（Code review 1）：spec "两层独立 guard" 措辞误导

**状态**：✅ 确认成立

`specs/2026-07-08-refactor-audit-stage-design.md:126`："运行时的计算与 bootstrap 的 LLM 输出是两层独立 guard"。

两层共享同一 `AUDIT_REFACTOR_KEYWORDS` 正则盲点，runtime fallback 只扫 title+model 不扫 brief body（实现已如实记录：`run-plans.js:1490-1491`, `workflow-design.md:1065`）。"独立"措辞高估了防护独立性。

**修复建议**：软化为"两层冗余 guard（共享关键词盲点）"。

---

## 3. 不成立 / 降级为 P2

| # | 审计项 | 复核结论 | 理由 |
|---|---|---|---|
| Spec P1-1 | 三层 guard 缺中间层 | **降级 P2** | spec §1/§4.2 对"几层"自身有歧义；runtime fallback（`run-plans.js:1493`）已覆盖中间层意图。建议 spec 澄清而非加代码 |
| Spec P1-2 | 基线 350 声明错误 | **不成立** | 已核实 feature 前（`218a039~1`）确实是 350 green。plan 声明正确 |
| Hunter P1-3 | fix-round 不传 auditDirective | **不成立** | 设计决策——AUDIT 是 Pre-RED 核查，只在首次跑。plan Task 4 明确"只改初始 dispatch" |
| Hunter P1-4 | validateAmendResult 把 sha 当 error | **降级 P2** | `run-plans.js:401` `result?.error \|\| result?.sha \|\| 'invalid sha'` 仅当 agent 返回 `{ok:false, sha:...}` 但无 error 时触发。不理想但不严重 |
| Hunter P1-5 | revertSimplifyChanges diag 漏 commitSha | **不成立** | `run-plans.js:1457` `diag: { task: taskId, checkoutError: ..., commitSha }` — **有 commitSha** |

---

## 4. Code review Minor 项（全部确认合理，不阻塞）

1. **audit_reason 非条件必需** — schema 有 audit_reason 但不在 required 数组，agent 可能返回 needs_audit_fix 但漏 audit_reason。建议加 required 条件或 dispatchImpl 加 fallback。
2. **runtime fallback task.model 无效** — `run-plans.js:1493` `AUDIT_REFACTOR_KEYWORDS.test(briefText)` 只扫 briefText，不扫 task.model（model 值如 'sonnet' 不会匹配 refactor 关键词）。合理观察。
3. **D21 三态简化为两态** — 设计决策，合理简化。

---

## 5. Hunter P2 观察项（确认合理，不修）

- checkSimplifyChanges 不校验 changed=false 与 files 自洽 — 已知边界
- escalate 的副作用依赖调用方设置 opus_escalated — 设计决策
- runtime fallback 只扫 title+model，不扫 brief body（已知局限）

---

## 6. 修复优先级建议

| 优先级 | 项 | 修复方式 | 工作量 |
|---|---|---|---|
| **P0 必修** | P0-1 halt diag 路径 | halt() 加 `diag: r.diag || {}` | 1 行 |
| **P0 必修** | P0-2 retry 漏查 needs_audit_fix | retry 路径补对称检查 | 1 行 |
| P1 应修 | P1-2 USAGE.md | 补 AUDIT/needs_audit_fix 条目 | 文档 |
| P1 应修 | P1-3 spec L184 矛盾 | 改为 prompt 断言描述 | 文档 |
| P1 应修 | P1-6 "独立"措辞 | spec 软化措辞 | 文档 |
| P2 可选 | P1-1 checkImplStatus | 修 P0-2 后自动消失 | 0 |
| P2 可选 | P1-4 schema taskKey | 加 taskKey 或用 tid | 1 行 |
| P2 可选 | P1-5 ?. 一致性 | 加 ?. 保持一致 | 3 处 |

---

## 7. 总结

**两个 P0 是真正必须修的**：
- **P0-1** 让 AUDIT halt 的诊断信息对用户不可见（路径断裂）
- **P0-2** 让 refactor task 的 AUDIT 发现被完全绕过（retry 路径漏洞）

这两个问题都出现在 AUDIT feature 的核心阻断路径上——前者破坏可观测性，后者破坏阻断本身。其余都是文档一致性或防御性改进，不阻塞合并但建议一并修复。

**复核过程中发现的元问题**：复核前未确认当前分支，导致工作树停留在 detached HEAD 上跑了大量无效 grep。建议未来复核前先 `git branch --show-current` + `git log --oneline -1` 确认分支与提交。

---

## 8. 最终裁定（Claude 独立验证，2026-07-08 11:11）

基线复核：分支 `workflow-simplification-tdd-fix` @ `a6f63eb`，**361 tests green** 已实跑确认。

### 8.1 代码点逐条验证结果

| 项 | 审计源 | 代码证据（已逐行核实） | 裁定 |
|---|---|---|---|
| **P0-1** halt diag 路径断裂 | Spec Review | `run-plans.js:1333` `blocked_info` 仅 `raw: r.diag \|\| {}`，无顶层 `diag`；`:1203-1206` 模板读 `blocked_info.diag.audit_reason`；`:486` dispatchImpl 把 `audit_reason` 放进 `r.diag` | ✅ **成立，必修** |
| **P0-2** retry 漏查 needs_audit_fix | Hunter | `run-plans.js:499-501` retry 分支仅查 `model_unavailable`，L501 `return impl`；首层 L486 有 needs_audit_fix 检查但不覆盖 retry | ✅ **成立，必修** |
| P1-1 checkImplStatus 漏短路 | Hunter | `:346` 默认 allowed 不含 needs_audit_fix；`:1528/1531/1538` 三处调用 | ✅ 修 P0-2 后**自动消除**（dispatchImpl 内即 halt，不流到 checkImplStatus） |
| P1-2 USAGE.md 未同步 | Spec/Code | grep 确认无匹配 | ✅ 成立，补文档 |
| P1-3 spec §6.1 矛盾 | Spec | spec L184 要求单测，L213 自承不可单测 | ✅ 成立，改 spec L184 |
| P1-4 schema 无 taskKey | Hunter | `:839-843` SCHEMAS.implementor 无 taskKey；`:486` 读 `impl.taskKey` | ✅ 成立，影响小（`blocked_info.task`=tid 已能定位） |
| P1-5 `?.` 一致性 | Hunter(原 P0-2) | `lib.js:773-774` 用 `?.`，`:792/794` 用非 `?.`；helpers.test:1410/1421 注释**逐字自承**「须传真实 review 对象（非 ?.）」=作者已知、靠约定兜底；三个相关测试（:1408/:1419/:1480）**全部构造带 diagnostics 的对象**，`diagnostics: undefined` 路径**零覆盖**，今天 green 纯属约定未被破坏 | ✅ **成立，应修（不是 P2）**——与 P0-2 同源（被吞的真实 halt reason）：`spec.diagnostics` 抛 TypeError → 顶层 catch 吞为笼统 `agent_error` → 真实 `review_not_converging`/`review max rounds` reason 丢失，blocked.md 误导用户「瞬态失败重跑即可」而无限重试。触发条件比 P0-2 窄（需 diagnostics 缺失），但 debug 路径完全跑偏且不可见（无日志、测试不覆盖） |
| P1-6 "独立 guard" 措辞 | Code Review | spec `:126`「两层独立 guard」与实现 `:1490-1491` 自承共享关键词盲点矛盾 | ✅ 成立，软化 spec |

### 8.2 不成立 / 降级裁定（与 §3 一致，补充证据）

| 审计项 | 裁定 | 补充证据 |
|---|---|---|
| Hunter P1-5 revert diag 漏 commitSha | **不成立** | `run-plans.js:1457` diag 实为 `{ task: taskId, checkoutError: ..., commitSha }`——**有 commitSha**，与 amend `:1446` 对称。hunter 误读 |
| Hunter P1-3 fix-round 不传 auditDirective | **不成立** | AUDIT 是 Pre-RED 核查（spec §3.1 `AUDIT 在 RED 前先把结果写到 .audit/`），设计上只首轮跑。fix-round 跑 GREEN/REFACTOR 不引入新 refactor 假设 |
| Hunter P1-4 validateAmendResult sha 当 error | **降 P2** | `lib.js:311` `result?.error \|\| result?.sha \|\| 'invalid sha'`——仅 `{ok:false, sha:非空}` 且无 error 时触发，罕见 |
| Spec P1-1 三层 guard 缺中间层 | **降 P2** | spec §1/§4.2 自身对「几层」措辞有歧义；runtime fallback（`:1493`）已覆盖意图。改 spec 澄清而非加代码（守 §4.3 不增加非必要 runtime 胶水） |
| Spec P1-2 基线 350 | **不成立** | feature 前 `218a039~1` 确为 350（simplification 终点）。spec 声明正确 |

### 8.3 修复优先级（最终）

**必修（P0，阻塞合并）**：P0-1、P0-2
**应修（P1）**：
- **代码**：P1-5（`?.`——与 P0-2 同源，被吞的真实 halt reason）、P1-4（schema taskKey，顺手闭环）
- **文档**：P1-2（USAGE）、P1-3（spec §6.1 矛盾）、P1-6（spec 措辞）

> **P1-5 提级说明（2026-07-08 11:27 复核）**：初判写「防御性修」语气偏弱。复核证据链——(1) helpers.test:1410/1421 注释逐字自承「须传真实 review 对象（非 ?.）」=作者已知；(2) 三个相关测试（:1408/:1419/:1480）全部构造带 diagnostics 对象，`diagnostics: undefined` 零覆盖，测试假绿；(3) TypeError → 顶层 catch 吞为 `agent_error`，真实 `review_not_converging`/`review max rounds` reason 丢失，用户误判「瞬态重跑」而无限重试——证明这是**与 P0-2 同源**（被吞的错误信号），不是可选防御。修 P1-5 就是堵 P0-2 的同源漏洞，应与 P0 等重视。

**修 P0-2 自动消除**：P1-1
**不修（P2/观察项）**：其余

### 8.4 TDD 修复落点（用于生成修复方案）

- **P0-1**：改 `halt()`（run-plans.js:1333 加 `diag: r.diag || {}`）。TDD：helpers.test 或 sync.test 加断言「blocked_info 含顶层 diag 字段」。
- **P0-2**：改 dispatchImpl retry 分支（run-plans.js:500 后加 needs_audit_fix 检查）。TDD：dispatchImpl-retry.test.js 加 RED——断言 retry 路径含 needs_audit_fix halt 分支（用 extractDispatchImpl 提取函数体，仿现有 AUDIT 测试模式）。
- **P1-4**：schema 加 taskKey（lib.js SCHEMAS + run-plans.js inline，sync.test 字节守护）。
- **P1-5**：lib.js:792/794 + run-plans.js inline 两处 `spec.diagnostics`→`spec?.diagnostics`（×3 字段）。helpers.test 现有注释「须传真实 review 对象」改为「可选 ?. 兜底」并加 RED（传无 diagnostics 的对象不 TypeError）。
- **P1-2/P1-3/P1-6**：纯文档，无 TDD。
