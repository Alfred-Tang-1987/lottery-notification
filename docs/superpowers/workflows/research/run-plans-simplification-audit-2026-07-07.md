# run-plans.js 简化与一致性审计报告

> **日期**: 2026-07-07
> **调查者**: Claude（code-simplifier + Explore agent + 人工复核）
> **范围**: `.claude/workflows/run-plans.js`（1738 行）+ `docs/superpowers/workflows/lib.js`（1194 行）+ `docs/superpowers/workflow-design.md`（1066 行）+ `docs/superpowers/workflow-plans/2026-07-07-workflow-consolidated.md`（1701 行）+ `docs/superpowers/workflows/tests/sync.test.js` + `helpers.test.js`
> **基线**: 307 tests green（`node --test docs/superpowers/workflows/tests/*.test.js`）
> **性质**: 只读调查，未修改任何代码

---

## 调查方法

两条并行调查线 + 人工复核：

1. **简化机会分析**（code-simplifier agent）—— 聚焦 `run-plans.js` + `lib.js` 的冗余、重复模式、函数体积、命名、死代码
2. **设计-实现一致性对照**（Explore agent）—— 对照 `workflow-design.md` 与实现，找出文档滞后 / 代码滞后 / 潜在 bug
3. **人工验证**—— 对 agent 标记 HIGH 的发现，亲自 grep + 链路追踪确认，避免 agent 误读

**已明确排除的"伪问题"**：`lib.js` ↔ `run-plans.js` 的 inline 副本重复。这是设计契约（sync.test 字节守护），非可简化项。

---

## 按影响排序的发现

### 🔴 HIGH-1（已人工验证）：`lesson_categories` 端到端链路断裂 — §5.5 改进 A 实际是死代码

**这是本次调查最重要的发现：单测全绿，但一个设计能力端到端从未生效。**

#### 链路追踪

| 层 | 位置 | 内容 |
|---|---|---|
| 消费 | `run-plans.js:1256` | `const taskCategories = task.lesson_categories \|\| []` ← 期望 task 带 `lesson_categories` |
| schema | `run-plans.js:802` | bootstrap Return schema = `tasks:[{id, model, title}]` ← **无 lesson_categories 字段** |
| prompt | `run-plans.js:789`（step 3）| 只指示提取 `models` map 和 `write_files`，**从未指示提取 `lesson_categories`** |
| 数据源 | `docs/superpowers/plans/` + `workflow-plans/` | grep `lesson_categories` → **无任何 plan frontmatter 声明该字段**（只有 consolidated plan 的设计文档和归档 plan 提到它） |

#### 后果

运行时 `task.lesson_categories` 永远是 `undefined` → `formatDomainLessons`（`lib.js:570-601`）永远走 `else if (taskTitle)` 的 **title 关键词 fallback**，**category 精确匹配分支（`helpers.test.js:952-964` 测的那条）端到端从不触发**。

#### 为什么单测是绿的（"测试绿 ≠ 能力生效"的典型）

```
helpers.test.js:959  →  formatDomainLessons(all, ['test-strategy'])   // 直接传 category，纯函数测试
                          ↳ 绕过了 bootstrap 提取链
sync.test.js         →  守护 formatDomainLessons 函数体字节一致       // 守字节，不守数据流
```

`helpers.test` 测的是**纯函数在给定输入下的行为**——输入直接由测试构造，不经过 bootstrap。`sync.test` 只守 `lib.js ↔ run-plans.js` 的函数体字节一致，**无法捕获"bootstrap 没填这个字段"的数据流断裂**。这是分层测试的固有盲区：每一层都正确，端到端却失效。

#### 影响范围

- §5.5 改进 A（v3 Tier 2 category 匹配）核心能力失效
- consolidated plan Task 4（`formatDomainLessons` 设计）的 category 精确匹配路径形同虚设
- **不影响稳定性**：title 关键词 fallback 可工作，lessons 仍能注入，只是匹配精度退化为旧逻辑

#### 修复方向（供后续实施决策，二选一）

| 方案 | 改动 | 代价 |
|---|---|---|
| **A. 补全提取链** | bootstrap prompt step 3 加 `lesson_categories` 提取说明 + Return schema `tasks:[{id, model, title, lesson_categories}]` + 真实 plan frontmatter 声明 category | 中（改 prompt + schema + 各 plan frontmatter） |
| **B. 承认未启用并简化** | 删 `formatDomainLessons` 的 category 分支，只保留 title 关键词 fallback；删 `task.lesson_categories` 引用 | 小（简化代码，但放弃精确匹配能力） |

**推荐**：先确认是否有 plan 真的需要 category 精确匹配（当前 lessons.md 内容若已按 title 能匹配上，方案 B 更省）。若未来 lesson 数量增长导致 title 匹配噪音大，再走方案 A。

---

### 🟡 MEDIUM-1：`sync.test.js` 正则提取的脆弱性（未来重构隐患）

`sync.test.js` 用正则从源码提取片段做字节比较，当前有效，但存在"现在绿、未来静默失效"的隐患：

#### 脆弱点 1：`promptBody(src, role)` 的 prompt 提取

```js
// sync.test.js:14-19
const re = new RegExp(`  ${role}: \\\`([\\s\\S]*?)\\\`,`)
```

用非贪婪 `[\s\S]*?` 匹配 prompt 模板字面量。**若未来 prompt 内嵌反引号**（如 `` `${var}` `` 模板），正则会过早闭合，两端截断在同一位置 → **假阳性"通过"**（比较的是截断后的片段，两边恰好截断一致）。

当前所有 PROMPTS 不含内嵌反引号，所以安全。但这是隐性约束，无守护。

#### 脆弱点 2：`extractFunctionBody` 的闭合匹配

```js
// sync.test.js:41-49
const closeMatch = afterFn.match(/\n\}/)
```

用第一个 `\n}` 闭合函数体。**若函数体含 `\n}` 子模式**（嵌套对象字面量、正则含换行、嵌套函数），会过早闭合。当前 40+ 个纯函数都不含这种结构，所以有效。

#### 脆弱点 3：`extractSchemas` 整块提取

用 `const SCHEMAS = \{[\s\S]*?\n\}` 提取 SCHEMAS 整块，若 SCHEMAS 后紧跟 `\n}` 会截断。

#### 建议

无需立即改，但在 sync.test 顶部加注释声明这些隐性约束（"PROMPTS 不得含内嵌反引号"、"纯函数不得含顶层 `\n}` 子模式"），或改用 AST 解析（如 acorn）彻底消除正则脆弱性。后者是较大重构。

---

### 🟢 LOW-1：`fixModelForRound` 注释与 `resolveMaxRounds` 默认值矛盾（文档滞后）

- `run-plans.js:346` 注释："向后兼容默认 3（round=2 升级 opus）"
- `resolveMaxRounds`（`run-plans.js:357-365`）实际默认 **4**（round=3 升级 opus）
- `fixModelForRound` 的 `const max = maxRounds ?? 3` 中 `?? 3` 是**死路径**（resolveMaxRounds 总返回数字）

**影响**：无（代码正确）。注释滞后误导阅读。

**修复**：更新注释为"默认 4（round=3 升级 opus）"，或删除向后兼容注释 + 删 `?? 3` 死路径（需确认 helpers.test 是否直接调 fixModelForRound 不传 maxRounds，若是则保留 `?? 3`）。

---

### 🟢 LOW-2：`headVerifier` 角色未写入 `workflow-design.md`（文档滞后）

- 实现：`run-plans.js:1037`（headVerifier prompt）+ `run-plans.js:1721-1724`（gate 后独立验证 HEAD 恢复）
- 设计文档：§13a 骨架 + §13b 角色表 **均无 headVerifier**

P1-1c（第 13 轮）新增的功能未回写设计文档。功能正确，降低可追溯性。

**修复**：`workflow-design.md` §13b 角色表加 headVerifier（gate 后验证 HEAD == restored_head，不符则 halt `gate head restore verification failed`）。

---

### 🟢 LOW-3：`finalReport` per_task 清单缺 `planId` 字段（文档滞后）

- `ensurePerTaskDefaults`（`run-plans.js:1149-1161`）初始化 **16** 个字段，含 `planId`
- `finalReport` prompt 清单（`run-plans.js:1048`）只列 **15** 个，漏 `planId`

**影响**：无。prompt 同时说"保留全部字段不得 strip"，`JSON.stringify(state)` 不会丢字段——纯清单不完整。

**修复**：清单补 `planId`，或加注"清单仅作可读说明，以 stateJson 全字段为准"。

---

### 🟢 LOW-4：`haltLikelySource` 对 `gate head restore verification failed` 语义映射不准

`haltLikelySource`（`run-plans.js:299-316`）用显式 Set + startsWith 做 reason→source 映射：
- `'gate head restore verification failed'` 匹配 `r.includes('gate')` → 返回 `'gate restored'`
- 但实际语义是**验证失败**而非 gate 已恢复

**影响**：低。`likely_source` 仅用于 `blocked.md` 给人类定位脏状态来源，不影响控制流。用户看到 `likely_source=gate restored` 配合 `reason=gate head restore verification failed` 仍能理解。

**修复**（可选）：`haltLikelySource` 加 `if (r.includes('head restore')) return 'gate head mismatch'` 分支，放在 `includes('gate')` 之前。

---

## 设计-实现一致性：已确认一致的部分

以下设计文档声明的控制流，经逐行对照与 sync.test 断言验证，**与实现完全一致**（无差异）：

| 设计章节 | 实现位置 | sync.test 守护 |
|---|---|---|
| §5.5 OSCILLATING 判定顺序（regressed → flipFlop → escalate → budget） | `run-plans.js:1340-1398` | sync.test:123-157（allGreen < detectOscillation 顺序、findings_history 更新顺序、regressed 分支、budget guard） |
| §5.5 findings 状态机（open→fixed→regressed→fixed） | `lib.js:74-119` + `run-plans.js` inline | QC-4 字节比较 + helpers.test |
| §5.2 simplify 方案 C（commit→simplify→git status--porcelain→review→amend/checkout） | `run-plans.js:1434-1517` | sync.test:298-320（git status--porcelain / amend / reset--hard HEAD / checkout 验证 / halt reason） |
| §3 gate 独立验证 + lastSha 反向查找 | `run-plans.js:1698-1728` | sync.test SCHEMAS gate evidence required |
| §5.4 halt 流程（blocked_info → distiller best-effort → finalReport fallback） | `run-plans.js:1163-1211` | sync.test 结构断言 |
| review 双守卫（reviewHaltReason + reviewHaltForEmptyFailed） | `lib.js:223-296` + run-plans inline | QC-4 + helpers.test |
| Task Scope Boundary / Lessons Learned Exemption（W1-5a/5e + H-F4） | PROMPTS specReview/qualityReviewer | sync.test prompt 逐字比对 |

**结论**：核心控制流实现忠实于设计。问题集中在**数据流断裂**（HIGH-1）和**文档滞后**（LOW 系列），不在控制流逻辑。

---

## 简化机会清单（code-simplifier 发现）

> **重要约束**：所有 `lib.js` 纯函数的改动，必须逐字同步到 `run-plans.js` 的 inline 副本，否则 sync.test 失败。PROMPT 模板修改可能改变 agent 行为，需 workflow 级验证。以下按收益排序。

### 高收益（结构性重构）

#### S1. implementor 5 个 dispatch 点模式重复

**位置**: `run-plans.js:1259-1303`

**问题**: `runTask` 中初始 dispatch、blocked 升级、needs_context 后的 context-fetch + retry、failed retry 等路径，每段都重复：
1. 构造 `implCtx(fix, note, ctx)`
2. `dispatchImpl(..., model, 'opus')`
3. `if (impl.halted) return impl`
4. 对 `blocked/failed/needs_context` 做分支判断并返回 halted

`1263-1269` 与 `1283-1289` 几乎逐行镜像，仅 reason 字符串不同（`'opus BLOCKED'` vs `'opus BLOCKED after context-fetch'`）。

**建议**: 抽 `checkImplStatus(impl, allowed, reasonPrefix)` helper：
```js
function checkImplStatus(impl, allowed = ['ok', 'done_with_concerns'], reasonPrefix = 'implementor') {
  if (impl.halted) return impl
  if (!allowed.includes(impl.status)) {
    return { halted: true, reason: `${reasonPrefix} ${impl.status}`, diag: impl.diagnostics }
  }
  return null
}
```

**收益**: 消除 5 段重复；新增 implementor 状态时兜底处理只改一处。

---

#### S2. review 循环体 ~120 行职责混合

**位置**: `run-plans.js:1316-1432`

**问题**: 循环同时负责：调 runReviewRound、更新三个 state 数组、处理 review halt、allGreen 检查、OSCILLATING/flipFlop/regressed 判断、模型升级、budget guard、fix-round dispatch、fix-round 后状态处理、更新 filesChanged。这是 `runTask` 340 行中最复杂的部分。

**建议**: 拆三个函数：
- `recordReviewRound(taskKey, round, spec, qual, hunt)` — 更新 state.perTask
- `decideReviewOutcome(taskKey, round, spec, qual, hunt, model)` — 返回 `{ break, halted, reason, diag }`
- `runFixRound(taskKey, plan, task, round, findings, model)` — 封装 fix-round dispatch + 状态检查

主循环降到 ~40 行。

**收益**: OSCILLATING/flipFlop/regression/budget 决策集中；fix-round 状态检查复用 S1 的 helper。

---

#### S3. simplify 流程 ~65 行混杂

**位置**: `run-plans.js:1453-1517`

**问题**: simplify 后流程含 dispatchImpl('simplify') + safeAgent(git status--porcelain) + diff 校验 + runReviewRound + safeAgent(amend) + validateAmendResult + safeAgent(reset/checkout) + validateCheckoutResult，且 schema（diffSchema/amendSchema/checkoutSchema）inline 在流程中。

**建议**: 抽三个 runtime helper：
- `async checkSimplifyChanges(taskId)` → `{ changed, files }`
- `async amendSimplifyCommit(taskId, commitSha)` → `{ ok, sha }`
- `async revertSimplifyChanges(taskId, commitSha)` → `{ ok }`

每个封装 schema + safeAgent + validateXxxResult + 日志。

**收益**: ~65 行降到 ~20 行；schema/校验/日志封装在独立函数。

---

#### S4. reviewer 来源三元组硬编码 3 处

**位置**: `lib.js` 的 `collectReviewFindings`(203-205) / `reviewHaltForEmptyFailed`(223-233) / `summarizeReviewRound`(255-262)

**问题**: 三处都硬编码 `['spec','issues'], ['quality','issues'], ['hunter','silent_failures']`。新增 reviewer（如 securityReviewer）需改三处，易遗漏。

**建议**: lib.js 定义常量（需同步 run-plans.js inline）：
```js
const REVIEW_SOURCES = [
  { name: 'spec', key: 'issues' },
  { name: 'quality', key: 'issues' },
  { name: 'hunter', key: 'silent_failures' },
]
```
三个函数遍历它。

**收益**: 消除 triplet 重复；reviewer 扩展改一处。

---

### 中收益（去重）

#### S5. 6 个 `format*` 条件渲染 helper 结构重复

**位置**: `lib.js:519-538`（formatReferencePaths）/ `530-538`（formatSilentFailureContext）/ `541-547`（formatFailedApproaches）/ `551-557`（formatLessons）/ `560-568`（formatUniversalLessons）/ `570-601`（formatDomainLessons）

**共同模式**:
```js
if (!Array.isArray(items) || items.length === 0) return ''
const lines = items.map(it => `- ${formatter(it)}`).join('\n')
return `## ${heading}\n${intro}\n${lines}\n${outro}`
```

其中 `formatLessons` 与 `formatDomainLessons` 的渲染行格式完全一致（`- [${id}] ${title} — ${detail}`）。

**建议**: 抽 `formatBulletSection(title, intro, items, renderItem, outro)`，各 format* 复用。

**收益**: 6 helper 合并为 1 通用 + 少量 wrapper；prompt 片段渲染统一。

---

#### S6. `formatFindings` 与 `formatCrossReviewerNote` 的 finding 格式化重复

**位置**: `lib.js:238-246`（formatFindings）/ `673-686`（formatCrossReviewerNote）

**问题**: 两处都拼 `[source|severity] title — fix: ...`，仅前缀和 file 处理不同：
```js
// formatFindings
const tag = f.severity ? `[${f.source}|${f.severity}]` : `[${f.source}]`
// formatCrossReviewerNote
out += `- [${f.source}${f.severity ? '|' + f.severity : ''}] ${f.title}...`
```

**建议**: 抽 `formatFindingItem(f, { withFile, prefix })`。

**收益**: 统一 finding 格式化；未来 source/severity 格式变更改一处。

---

#### S7. 7 个 PROMPT 末尾重复"限额耗尽返回 model_unavailable"说明

**位置**: implementor / specReview / qualityReviewer / hunter / commit / gate / lessonDistiller 的 RED FLAG 段

**重复文本**: `若遇到 model 限额耗尽（quota/rate-limit/429 错误），返回 status:'model_unavailable'（非 failed），让 orchestrator halt 并保存进度。`

**建议**: 提 `QUOTA_HALT_NOTE` 常量片段，各 prompt 用 `{{quotaHaltNote}}` 占位，buildPrompt 注入。

**注意**: buildPrompt 对未定义 key 保留 `{{k}}`，须显式传 quotaHaltNote（空串或公共文本）。PROMPT 改动需 workflow 级验证（agent 行为可能变）。

**收益**: 7 处重复归一；限额话术统一。

---

#### S8. reviewer prompt 重复"STATIC READ-ONLY + Lessons Learned Exemption"段

**位置**: specReview / qualityReviewer / hunter 各含 10-20 行高度重复段落

**建议**: 提 `STATIC_READONLY_NOTE` + `LESSONS_EXEMPTION_NOTE(applicableDimensions)` 公共片段，占位注入。

**收益**: reviewer prompt 重复文本减少约 30%。

---

#### S9. `dispatchImpl` 6 处 halted 构造重复

**位置**: `run-plans.js:397-437`

**问题**: 多次 `return { halted: true, reason, diag: { model, error: errStr(e) } }`，仅 reason/error 不同。

**建议**: 抽 `makeHalt(reason, model, error)` helper（runtime，留 run-plans.js）。

**收益**: ~6 处重复构造消除；错误对象标准化。

---

#### S10. `taskKey` 字符串拼接散落 6+ 处

**位置**: `run-plans.js:1241`（runTask）/ `1166`（halt tid）/ `1651`（failedApproaches）/ `1662`（taskWriteFiles）/ `1669`（taskLessons）/ `1684`（顶层 skip）/ `1704`（lastSha 查找）

**重复模式**: `` `plan-${String(seq).padStart(2, '0')}/${id}` ``

**风险**: `padStart` 位数不一致已是历史 bug 根因（P0-7 注释提到 plan.seq=1 → "plan-1" 查不到 "plan-01"）。

**建议**: lib.js 加 `taskKey(seq, taskId)` 纯函数（与 commitSubject/bareTaskId 同源），同步 run-plans.js inline。

**收益**: 消除 6+ 处拼接；杜绝 padStart 位数不一致 bug。

---

### 低收益（清理）

#### S11. `_rawCompleted` 末尾 `|| []` 是死代码

**位置**: `run-plans.js:1645`

```js
const _rawCompleted = (Array.isArray(args.completed) && args.completed.length
  ? args.completed
  : [...new Set([..._regexCompleted, ..._llmCompleted])]) || []
```

`[...new Set(...)]` 永远返回真数组（即使空），`|| []` 永不触发。

**修复**: 删 `|| []`。无行为变化。

---

#### S12. 多处 `let x; x = await ...` 可合并为 `const`

**位置**: `run-plans.js:1259-1261`（impl）/ `1272`（ctxr）/ `1442`（commit）/ `1455`（simp）

```js
let impl
impl = await dispatchImpl(...)
```

变量无需重新赋值。

**修复**: 合并为 `const impl = await dispatchImpl(...)`。小幅可读性提升。

---

#### S13.（可选）`dispatchImpl` 命名已泛化

**位置**: `run-plans.js:397`

函数名暗示只 dispatch implementor，但实际用于 bootstrap/implementor/contextFetcher/commit/simplify/gate/headVerifier 共 7 类 agent。

**建议**: 改名 `dispatchAgent` 或 `dispatchWithRetry`。涉及多处调用点 + sync.test 断言，收益与风险并存，谨慎。

---

#### S14.（可选）`issuesFromReviews` 是"僵尸"导出

**位置**: `lib.js:118-123`

注释自述"已被 collectReviewFindings 取代，保留为通用工具 + 向后兼容"。grep 显示 run-plans.js 无调用。

**建议**: 确认无外部消费者（含 memory/indexing 脚本）后标记 `@deprecated` 或删除。

---

## 汇总表

| ID | 类型 | 严重度 | 位置 | 一句话 |
|---|---|---|---|---|
| **HIGH-1** | 数据流断裂 | 🔴 HIGH | run-plans.js:789,802,1256 | `lesson_categories` bootstrap 不提取 → §5.5 category 匹配端到端失效 |
| **MEDIUM-1** | 测试盲区 | 🟡 MEDIUM | sync.test.js:14,41 | 正则提取脆弱，未来重构可能假阳性通过 |
| LOW-1 | 文档滞后 | 🟢 LOW | run-plans.js:346 | fixModelForRound 注释"默认3"与实际"默认4"矛盾 |
| LOW-2 | 文档滞后 | 🟢 LOW | workflow-design.md §13b | headVerifier 角色未记录 |
| LOW-3 | 文档滞后 | 🟢 LOW | run-plans.js:1048 | finalReport per_task 清单缺 planId |
| LOW-4 | 语义不准 | 🟢 LOW | run-plans.js:299-316 | haltLikelySource 对 head-restore-failed 映射为 gate restored |
| S1 | 重复模式 | 高收益 | run-plans.js:1259-1303 | implementor 5 dispatch 点模式重复 |
| S2 | 函数过长 | 高收益 | run-plans.js:1316-1432 | review 循环 ~120 行职责混合 |
| S3 | 函数过长 | 高收益 | run-plans.js:1453-1517 | simplify 流程 ~65 行混杂 |
| S4 | 数据重复 | 高收益 | lib.js 三处 | reviewer 来源三元组硬编码 |
| S5 | 结构重复 | 中收益 | lib.js:519-601 | 6 个 format* helper 结构相同 |
| S6 | 逻辑重复 | 中收益 | lib.js:238,673 | finding 格式化重复 |
| S7 | 文本重复 | 中收益 | 7 个 PROMPT | 限额耗尽说明重复 |
| S8 | 文本重复 | 中收益 | reviewer PROMPT | STATIC READ-ONLY + Exemption 段重复 |
| S9 | 构造重复 | 中收益 | run-plans.js:397-437 | dispatchImpl halted 构造重复 |
| S10 | 拼接重复 | 中收益 | run-plans.js 6+ 处 | taskKey 拼接散落，padStart 不一致风险 |
| S11 | 死代码 | 低收益 | run-plans.js:1645 | `|| []` 永不触发 |
| S12 | 风格 | 低收益 | run-plans.js 4 处 | `let x; x=` 可合并为 const |
| S13 | 命名 | 低收益 | run-plans.js:397 | dispatchImpl 已泛化，名不达意 |
| S14 | 僵尸代码 | 低收益 | lib.js:118-123 | issuesFromReviews 已被取代 |

---

## 总体评价

**架构健康**：lib.js 纯函数 + run-plans.js runtime 胶水的分层清晰，sync.test 字节守护 + 307 测试是扎实的防护网。核心控制流（OSCILLATING 判定顺序、simplify 方案 C、gate、halt、findings 状态机）**与设计文档完全一致**。

**唯一实质问题**是 HIGH-1（`lesson_categories` 链断裂）—— "单测绿但端到端失效"的典型。sync.test 守不了数据流，只守字节一致；helpers.test 守纯函数，不守 bootstrap 提取链。**这是分层测试的固有盲区**：每一层都正确，端到端却失效。值得作为后续修复优先项。

**可维护性负担**主要在 runTask（340 行）和重复模式（S1-S10），但都是"可用、有测试保护"的状态，简化属改进非修复。建议优先级：
1. **HIGH-1** 决策（补全链路 or 简化删除）—— 影响设计能力是否生效
2. **S1-S3** 结构性重构 —— 降低 runTask 认知负载
3. **S10** taskKey helper —— 杜绝 padStart 历史 bug 复发
4. 其余 LOW / S4-S9 / S11-S14 视后续改动顺手处理

---

## 附录：调查执行的命令与验证

```bash
# 基线测试
node --test docs/superpowers/workflows/tests/*.test.js   # 307 pass

# HIGH-1 验证链
grep -n "lesson_categories" .claude/workflows/run-plans.js          # schema/prompt 无提取
grep -rn "lesson_categories" docs/superpowers/plans/ docs/superpowers/workflow-plans/  # 无 plan 声明
grep -n "formatDomainLessons\|taskCategories" docs/superpowers/workflows/tests/helpers.test.js  # 纯函数测试，绕过 bootstrap

# 文件规模
wc -l docs/superpowers/workflow-design.md docs/superpowers/workflows/lib.js docs/superpowers/workflows/USAGE.md
# 1066 / 1194 / 323
```

**END OF REPORT**
