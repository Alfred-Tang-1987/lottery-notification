# run-plans.js 简化 TDD 修复设计

> **日期**: 2026-07-07
> **状态**: 设计批准（用户授权跳过审阅），待写实施计划
> **依据**: [审计报告](../research/run-plans-simplification-audit-2026-07-07.md) 16 项发现
> **基线**: 307 tests green（`node --test docs/superpowers/workflows/tests/*.test.js`）
> **目标**: 全 16 项根治，按风险递增 3 批次 TDD 实施

---

## 1. 背景与范围

### 1.1 问题来源

审计报告 [run-plans-simplification-audit-2026-07-07.md](../research/run-plans-simplification-audit-2026-07-07.md) 发现 16 项问题：

- **HIGH-1**（数据流断裂）：`lesson_categories` bootstrap 不提取 → §5.5 改进 A category 精确匹配分支端到端不可达
- **MEDIUM-1**（测试盲区）：sync.test 正则提取的脆弱性
- **LOW-1/2/3/4**（文档滞后/语义不准）
- **S1-S14**（简化重构机会）

### 1.2 修复范围

**全 16 项根治**。S13（dispatchImpl 改名）保持不改（13 处调用点风险高，收益仅命名美化），不在 16 项内。

### 1.3 关键约束（§4.3 分层）

- **纯决策/纯构造函数**（不调 `agent()`）→ 必须进 lib.js + 同步 run-plans.js inline 副本，sync.test 字节守护
- **runtime 胶水**（调 `agent()`/`safeAgent`/`dispatchImpl`）→ 只能留 run-plans.js，lib.js 是纯模块不能调 runtime 全局
- **PROMPT 片段常量** → 进 lib.js `PROMPTS` 真源 + run-plans.js inline，`buildPrompt` 注入
- **所有简化不得引入 fs / subprocess / Date.now / Math.random**
- **所有简化不得改变 agent 调用数**

---

## 2. 决策记录

| # | 决策项 | 选择 | 理由 |
|---|---|---|---|
| D1 | 修复范围 | 全 16 项根治 | 彻底闭环审计报告 |
| D2 | HIGH-1 方案 | A. 补全提取链 | spec §5.5 改进 A 设计了该能力，v3 plan 要求 bootstrap 提取但实现未完成，是 bug |
| D3 | HIGH-1 范围 | 只补提取链 + schema，不动现有 plan frontmatter | 数据迁移留给 plan 作者按需 opt-in |
| D4 | MEDIUM-1 防御 | 加 3 个 trip-wire 守护断言 | TDD 导向，代价小于 AST 重构 |
| D5 | LOW-1 `?? 3` | 保留死路径作防御性兜底 | helpers.test 可能直接调 fixModelForRound 不传 maxRounds |
| D6 | S13 改名 | 不改名 | 13 处调用点风险高，收益仅命名美化 |
| D7 | S14 处理 | 标 @deprecated | 有 helpers.test 覆盖说明曾为公共 API，软过渡 |
| D8 | 实施策略 | 方案 C 按风险递增 | 低风险建安全网 → 中风险纯函数 → 高风险 runtime 重构 |
| D9 | B2-3 errStr | 提升到 lib.js | makeHalt 内部调 errStr，调用方更简洁 |
| D10 | B2-4 checkImplStatus reason | 逐字对齐原实现 | 防止 reason 字符串变化破坏现有日志/诊断 |
| D11 | B2-5 formatBulletSection outro | 支持多行 string | 原 6 个 format* 的 outro 有单行也有多行 |
| D12 | B2-7 buildPrompt 默认注入 | 内置 quotaHaltNote 默认值 | 调用方不需显式传 |
| D13 | B2-8 LESSONS_EXEMPTION_NOTE | 函数，调用方传参 | applicableDimensions 随 reviewer 变化 |
| D14 | B2-8 STATIC_READONLY_NOTE | 进 buildPrompt 默认 | 3 个 reviewer 文本一致 |
| D15 | B3-1 decideReviewOutcome action | 9 个 action 枚举 | halt 的 5 个子类用 reason 区分，action 顶层用 halt 统一 |
| D16 | B3-1 runFixRound | 不用 checkImplStatus | fix-round 的 blocked/failed/needs_context 都是 halt，语义不同于初始 dispatch |
| D17 | B3-2 测试策略 | 不加新单测 | runtime 函数调 safeAgent，mock 成本高，靠 sync.test 存在性断言 + 全量回归 |
| D18 | Batch 1 commit | 3 个分项 | HIGH-1 / MEDIUM-1+LOW / S11-S14 清理 |
| D19 | Batch 2 commit | 5 个分项 | B2-1/2/3/6 低风险合一，B2-4/5/7/8 各一 |
| D20 | Batch 3 commit | 5 个分项 | recordReviewRound / decideReviewOutcome / runFixRound / S3 三 helper 合一 / 主流程集成 |
| D21 | 执行顺序 | 严格串行 Batch 1 → 2 → 3 | 每批次全绿后才进下一批 |

---

## 3. 架构概述

### 3.1 三批次依赖关系

```
Batch 1 (低风险) — 建立安全网 + 文档/清理
├─ HIGH-1 (补链)
├─ MEDIUM-1 (守护) ← 为 Batch 2/3 重构提供回归保护
├─ LOW-1/2/3/4
└─ S11/S12/S14
        │
        ▼ （MEDIUM-1 守护 + HIGH-1 数据流测试建立）
Batch 2 (中风险) — 纯函数 helper 抽取
├─ S10 taskKey ─┐
├─ S4 REVIEW_SOURCES ─┤
├─ S9 makeHalt ─┤    ← 所有纯函数进 lib.js + sync 守护
├─ S1 checkImplStatus ─┤    (runtime 部分留 run-plans.js)
├─ S5 formatBulletSection ─┤
├─ S6 formatFindingItem ─┤
├─ S7 QUOTA_HALT_NOTE ─┤    (PROMPT 常量 + buildPrompt)
└─ S8 STATIC_READONLY_NOTE ─┘
        │
        ▼ （S1 checkImplStatus 就位后）
Batch 3 (高风险) — runtime 循环拆分
├─ S2 review 循环拆分（依赖 S1）
│   ├─ recordReviewRound (纯函数 → lib.js)
│   ├─ decideReviewOutcome (纯函数 → lib.js)
│   └─ runFixRound (runtime → run-plans.js)
└─ S3 simplify helper 拆分
    ├─ checkSimplifyChanges (runtime)
    ├─ amendSimplifyCommit (runtime)
    └─ revertSimplifyChanges (runtime)
```

### 3.2 TDD 4 阶段流程

每项改动严格遵循：
1. **RED**：先写失败测试
2. **GREEN**：最小实现通过测试
3. **SYNC**：spec/USAGE 同步
4. **FULL**：全量回归 + git commit

### 3.3 测试叠加

| 批次 | 新测试 | sync.test 新断言 | 累计测试数 |
|---|---|---|---|
| 基线 | — | — | 307 |
| Batch 1 | +5 | +0 | 312 |
| Batch 2 | +17 | +8 + prompt 基线更新 | 329 |
| Batch 3 | +12 | +5 | 341 |

---

## 4. Batch 1 低风险批详情

### 4.1 B1-1: HIGH-1 补全 lesson_categories 提取链

**改动 3 处**：

| 文件 | 位置 | 改动 |
|---|---|---|
| run-plans.js + lib.js | bootstrap prompt step 3 | 末尾加：「Also extract `lesson_categories` from frontmatter if present (format: `lesson_categories:\n  - silent-failure\n  - test-strategy`). Return per task as `lesson_categories` array (absent → empty array).」 |
| run-plans.js + lib.js | bootstrap Return schema (line 802) | `tasks:[{id, model, title}]` → `tasks:[{id, model, title, lesson_categories}]` |
| helpers.test.js | 新增端到端数据流测试 | 断言 bootstrap 返回的 task 对象含 `lesson_categories` 字段（默认 `[]`，frontmatter 声明时按声明值） |

**TDD 流程**：
1. RED：写测试断言 bootstrap schema 含 lesson_categories（当前 undefined → fail）
2. GREEN：改 prompt + schema + 测试 stub
3. SYNC：workflow-design.md §5.5 加注「plan frontmatter 可声明 lesson_categories 启用精确匹配；未声明时 fallback 到 title 关键词匹配」
4. FULL：307 + 1 测试

**范围说明**：只补提取链 + schema。不在范围：批量给现有 plan frontmatter 添加 lesson_categories 声明（数据迁移留给 plan 作者按需 opt-in）。

### 4.2 B1-2: MEDIUM-1 sync.test 守护断言

**改动 1 处**（sync.test.js 顶部加 3 个 trip-wire 测试）：

```js
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

**TDD 流程**：直接 GREEN（断言当前代码已满足）。

### 4.3 B1-3: LOW-1 fixModelForRound 注释矛盾

**改动**（run-plans.js:346 + lib.js 对应位置）：

```diff
- // 向后兼容默认 3（round=2 升级 opus）
+ // 默认 4（round=3 升级 opus）；?? 3 是防御性兜底，正常路径 resolveMaxRounds 总返回数字
```

保留 `?? 3` 死路径作防御性兜底（D5 决策）。

### 4.4 B1-4: LOW-2 headVerifier 写入 §13b 角色表

**改动**（workflow-design.md §13b）：

```diff
+ | headVerifier | gate 后独立验证 HEAD == restored_head | sonnet | gate 恢复后 1 次 |
```

USAGE.md 同步加 headVerifier 角色说明（若有）。

### 4.5 B1-5: LOW-3 finalReport per_task 清单补 planId

**改动**（run-plans.js:1048 finalReport prompt + lib.js 对应位置）：

```diff
  per_task 清单（每 task 一项）：
  - taskKey, taskId, planId, model, status, ...
+ - planId（task 所属 plan 的 seq，如 '01'）
  ...
+ 注：清单仅作可读说明，以 stateJson 全字段为准（ensurePerTaskDefaults 共 16 字段）
```

### 4.6 B1-6: LOW-4 haltLikelySource 语义映射修正

**改动**（run-plans.js:299-316 + lib.js 对应位置）：

```diff
  export function haltLikelySource(reason) {
    const r = String(reason || '')
+   if (r.includes('head restore')) return 'gate head mismatch'
    if (r.includes('gate')) return 'gate restored'
    ...
  }
```

**TDD 流程**：
1. RED：helpers.test 加 `haltLikelySource('gate head restore verification failed')` === `'gate head mismatch'`
2. GREEN：加 head restore 分支放在 gate 之前
3. SYNC：spec §6.2 加注「head restore verification failed 单独归类为 gate head mismatch」
4. FULL：307 + 1 测试

### 4.7 B1-7: S11 删 `|| []` 死代码

**改动**（run-plans.js:1645）：

```diff
- const _rawCompleted = (Array.isArray(args.completed) && args.completed.length
-   ? args.completed
-   : [...new Set([..._regexCompleted, ..._llmCompleted])]) || []
+ const _rawCompleted = (Array.isArray(args.completed) && args.completed.length
+   ? args.completed
+   : [...new Set([..._regexCompleted, ..._llmCompleted])])
```

### 4.8 B1-8: S12 let→const 合并

**改动 4 处**（run-plans.js:1259-1261, 1272, 1442, 1455）：

```diff
- let impl
- impl = await dispatchImpl(...)
+ const impl = await dispatchImpl(...)
```

逐处 grep 确认无后续 `x =` 再赋值。

### 4.9 B1-9: S14 issuesFromReviews 标 @deprecated

**改动**（lib.js:118-123 + run-plans.js inline 副本）：

```diff
- // 已被 collectReviewFindings 取代（orchestrator fix-round 用）；保留为通用工具 + 向后兼容。
+ /**
+  * @deprecated (2026-07-07) 已被 collectReviewFindings 取代（orchestrator fix-round 用）。
+  * 保留仅为向后兼容；新代码请用 collectReviewFindings。
+  * 计划在下一轮 spec 修订时移除（需先确认无 memory/indexing 脚本调用）。
+  */
```

### 4.10 Batch 1 测试与 commit

- **新测试**：+5（HIGH-1 +1, MEDIUM-1 +3, LOW-4 +1）
- **commit 粒度**：3 个分项 commit
  1. HIGH-1 补链 + 测试
  2. MEDIUM-1 守护 + LOW-1/2/3/4 文档/修正
  3. S11/S12/S14 清理

---

## 5. Batch 2 中风险批详情（8 个纯函数 helper）

按依赖顺序排列。

### 5.1 B2-1: S10 taskKey 纯函数

**签名**：
```js
// lib.js
export function taskKey(seq, taskId) {
  return `plan-${String(seq).padStart(2, '0')}/${taskId}`
}
```

**替换点**（run-plans.js 6+ 处）：line 1241, 1166, 1651, 1662, 1669, 1684, 1704

**TDD 流程**：
1. RED：helpers.test 加 `taskKey(1, 'T1')` === `'plan-01/T1'`；`taskKey(10, 'T10a')` === `'plan-10/T10a'`
2. GREEN：lib.js 加 taskKey；run-plans.js inline + 替换 6 处
3. SYNC：sync.test 加 taskKey 字节断言；spec §4.4 加 taskKey helper 说明
4. FULL：307 + 2 测试

### 5.2 B2-2: S4 REVIEW_SOURCES 常量

**改动**（lib.js）：
```js
export const REVIEW_SOURCES = [
  { name: 'spec', key: 'issues' },
  { name: 'quality', key: 'issues' },
  { name: 'hunter', key: 'silent_failures' },
]
```

重构 `collectReviewFindings` / `reviewHaltForEmptyFailed` / `summarizeReviewRound` 遍历 REVIEW_SOURCES。

**TDD 流程**：
1. RED：helpers.test 加 REVIEW_SOURCES 导出断言 + length===3
2. GREEN：lib.js 加常量 + 重构 3 函数；run-plans.js inline 同步
3. SYNC：sync.test 加 REVIEW_SOURCES 字节断言
4. FULL：307 + 1 测试

### 5.3 B2-3: S9 makeHalt 纯构造函数

**签名**：
```js
// lib.js
export function errStr(e) {
  if (e == null) return ''
  if (typeof e === 'string') return e
  return e.message || String(e)
}

export function makeHalt(reason, model, error) {
  return { halted: true, reason, diag: { model, error: errStr(error) } }
}
```

**替换点**（dispatchImpl 内 4 处 catch 块）：
- line 406（首次 quota）→ `makeHalt('model_unavailable', model, e)`
- line 407（首次 agent_error）→ `makeHalt('agent_error', model, e)`
- line 426（retry quota）→ `makeHalt('model_unavailable', retryModel, e)`
- line 428（retry agent_error）→ `makeHalt('agent_error', retryModel, e)`

另 3 处 diag 是 `impl.diagnostics` 非 errStr，不替换。

**TDD 流程**：
1. RED：helpers.test 加 makeHalt + errStr 用例
2. GREEN：lib.js 加 errStr + makeHalt；run-plans.js inline + 替换 4 处
3. SYNC：sync.test 加 makeHalt + errStr 字节断言
4. FULL：307 + 1 测试

### 5.4 B2-4: S1 checkImplStatus 纯决策函数

**签名**：
```js
// lib.js
export function checkImplStatus(impl, allowed = ['ok', 'done_with_concerns'], reasonPrefix = 'implementor') {
  if (impl.halted) return impl
  if (!allowed.includes(impl.status)) {
    return { halted: true, reason: `${reasonPrefix} ${impl.status}`, diag: impl.diagnostics }
  }
  return null  // null 表示通过，继续往下
}
```

**替换点**（4 处可替换，2 处逻辑特殊保留）：
- line 1261（初始 dispatch 后）→ 可替换
- line 1268（blocked 升级后）→ 保留（单一 blocked 判断）
- line 1281（context-fetch 后）→ 保留（允许 blocked/failed 继续走分支）
- line 1294（context retry 后）→ 可替换
- line 1296（context 最终）→ 可替换
- line 1302（failed retry 后）→ 可替换

reason 逐字对齐原实现（D10 决策），如 `'implementor failed after retry'`。

**TDD 流程**：
1. RED：helpers.test 加 4 个用例（halted 透传 / status 不在 allowed / status 在 allowed / 默认 allowed）
2. GREEN：lib.js 加 checkImplStatus；run-plans.js inline + 替换 4 处
3. SYNC：sync.test 加 checkImplStatus 字节断言
4. FULL：307 + 4 测试

**依赖**：B2-4 在 B2-1/2/3 全绿后做。

### 5.5 B2-5: S5 formatBulletSection 通用渲染 helper

**签名**：
```js
// lib.js
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

outro 支持多行 string（D11 决策）。

**重构 6 个 format* 为 wrapper**：
- formatReferencePaths
- formatSilentFailureContext
- formatFailedApproaches
- formatLessons
- formatUniversalLessons
- formatDomainLessons

**TDD 流程**：
1. RED：helpers.test 加 formatBulletSection 3 个用例（空数组 / 基本渲染 / 含 intro+outro）
2. GREEN：lib.js 加 formatBulletSection + 重构 6 wrapper；run-plans.js inline 同步
3. SYNC：sync.test 加 formatBulletSection 字节断言 + 6 wrapper 验证
4. FULL：307 + 3 测试 + 现有 6 个 format* 测试全绿（输出逐字节一致）

**关键验证**：用 `diff <(old output) <(new output)` 确认每个 wrapper 输出不变。

### 5.6 B2-6: S6 formatFindingItem

**签名**：
```js
// lib.js
export function formatFindingItem(f, { withFile = true, prefix = '' } = {}) {
  const tag = f.severity ? `[${f.source}|${f.severity}]` : `[${f.source}]`
  const fix = f.fix ? ` — fix: ${f.fix}` : ''
  const file = (withFile && f.file) ? ` (${f.file})` : ''
  return `${prefix}${tag} ${f.title}${fix}${file}`
}
```

**重构**：
- `formatFindings`：`findings.map(f => formatFindingItem(f)).join('\n')`
- `formatCrossReviewerNote`：用 `formatFindingItem(f, { withFile: false, prefix: '- ' })`

**TDD 流程**：
1. RED：helpers.test 加 formatFindingItem 4 个用例
2. GREEN：lib.js 加 formatFindingItem + 重构 2 函数；run-plans.js inline
3. SYNC：sync.test 加 formatFindingItem 字节断言
4. FULL：307 + 4 测试

### 5.7 B2-7: S7 QUOTA_HALT_NOTE PROMPT 常量

**改动**：
1. lib.js 加常量：
```js
const QUOTA_HALT_NOTE = `若遇到 model 限额耗尽（quota/rate-limit/429 错误），返回 status:'model_unavailable'（非 failed），让 orchestrator halt 并保存进度。`
```
2. 7 个 prompt 末尾重复文本替换为 `{{quotaHaltNote}}`
3. buildPrompt 内置默认值（D12 决策）：
```js
export function buildPrompt(role, ctx = {}) {
  const defaults = { quotaHaltNote: QUOTA_HALT_NOTE }
  const merged = { ...defaults, ...ctx }
  // ... 原逻辑用 merged
}
```

**TDD 流程**：
1. RED：helpers.test 加 `buildPrompt('implementor', {})` 输出含限额说明文本
2. GREEN：lib.js 加常量 + buildPrompt 默认 + 7 prompt 替换占位符；run-plans.js inline
3. SYNC：sync.test prompt 字节断言更新（7 个 prompt 体变了）
4. FULL：307 + 1 测试 + 所有现有 prompt 字节断言更新基线

### 5.8 B2-8: S8 STATIC_READONLY_NOTE + LESSONS_EXEMPTION_NOTE

**签名**：
```js
const STATIC_READONLY_NOTE = `## STATIC READ-ONLY Constraint
...（10 行公共文本）`

function LESSONS_EXEMPTION_NOTE(applicableDimensions) {
  return `## Lessons Learned Exemption
... ${applicableDimensions} ...`
}
```

**改动**：
- STATIC_READONLY_NOTE 进 buildPrompt 默认（D14 决策）
- LESSONS_EXEMPTION_NOTE 是函数，由调用方传参（D13 决策，applicableDimensions 随 reviewer 变化）
- 3 个 reviewer prompt 重复段替换为占位符

**TDD 流程**：
1. RED：helpers.test 加 buildPrompt 注入断言
2. GREEN：lib.js 加常量 + 重构 3 prompt；run-plans.js inline
3. SYNC：sync.test prompt 断言更新
4. FULL：307 + 1 测试

### 5.9 Batch 2 测试与 commit

- **新测试**：+17
- **commit 粒度**：5 个分项 commit（D19 决策）
  1. B2-1/2/3/6 低风险合一
  2. B2-4 checkImplStatus
  3. B2-5 formatBulletSection
  4. B2-7 QUOTA_HALT_NOTE
  5. B2-8 STATIC_READONLY_NOTE

---

## 6. Batch 3 高风险批详情（runtime 循环拆分）

依赖 Batch 2 的 checkImplStatus（S1）就位。

### 6.1 B3-1: S2 review 循环拆分（3 个函数）

#### 6.1.1 函数 1: recordReviewRound（纯决策 → lib.js）

**签名**：
```js
// lib.js
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

**替换** run-plans.js:1317-1328（12 行 → 1 行调用）。

state 是引用，函数内直接 mutate（与现有风格一致）。返回 currentFindings 供后续使用。

#### 6.1.2 函数 2: decideReviewOutcome（纯决策 → lib.js）

**签名**：
```js
// lib.js
export function decideReviewOutcome(
  state, taskKey, round, spec, qual, hunt,
  model, maxRounds, cfg, reviewReason, emptyFailedReason
) {
  // 返回 { action, reason?, diag?, model? }
  // action 枚举（D15 决策）：
  //   - 'halt' (reason 区分 5 子类: reviewReason/emptyFailed/regressed/flipFlop/budget/maxRounds)
  //   - 'break' (allGreen)
  //   - 'escalate' (osc + flipFlop=false + shouldEscalate)
  //   - 'continue' (osc + flipFlop=false + alreadyEscalated)
  //   - 'fix' (else)
}
```

**替换** run-plans.js:1329-1398（~70 行决策逻辑 → 1 函数调用 + switch）。

函数内不 mutate state（escalate 时 setting opus_escalated/oscillation_escalated_at_round 由调用方做，保持纯决策）。

**调用方使用**：
```js
const outcome = decideReviewOutcome(state, taskKey, round, spec, qual, hunt, model, maxRounds, cfg, reviewReason, emptyFailedReason)
if (outcome.action === 'halt') return { halted: true, reason: outcome.reason, diag: outcome.diag }
if (outcome.action === 'break') break
if (outcome.action === 'escalate') {
  state.perTask[taskKey].opus_escalated = true
  state.perTask[taskKey].oscillation_escalated_at_round = round
  model = outcome.model
  log(...)
}
// action === 'continue' or 'fix' → 走 fix-round
```

#### 6.1.3 函数 3: runFixRound（runtime 胶水 → run-plans.js）

**签名**：
```js
// run-plans.js（runtime，调 dispatchImpl）
async function runFixRound(taskKey, plan, task, round, spec, qual, hunt, state, cfg, implCtx, model, maxRounds, concerns, concernsHint) {
  const findings = collectReviewFindings(spec, qual, hunt)
  const crossReviewerNote = formatCrossReviewerNote(findings)
  const findingsHistoryText = formatFindingsHistory(state.perTask[taskKey].findings_history || [], round)
  const fullFixIssues = findingsHistoryText ? `${findingsHistoryText}\n${crossReviewerNote}` : crossReviewerNote
  const oscEscRound = state.perTask[taskKey].oscillation_escalated_at_round
  const retryNote = oscEscRound === round ? `## 升级到 opus...` : `修复 review round ${round}...`
  const fixModel = fixModelForRound(round, model, maxRounds)
  const impl = await dispatchImpl(buildPrompt('implementor', implCtx(fullFixIssues, retryNote)), { schema: SCHEMAS.implementor, model: fixModel, label: `impl:${task.id}:fix${round}` }, fixModel, 'opus')
  if (impl.halted) return { impl, halted: true }
  // D16 决策：不用 checkImplStatus，直接内联判断
  if (impl.status === 'blocked' || impl.status === 'failed' || impl.status === 'needs_context') {
    return { impl, halted: true, reason: `implementor ${impl.status} in fix-round ${round}` }
  }
  if (impl.status === 'done_with_concerns') {
    concerns = impl.diagnostics?.concerns || concerns
    state.perTask[taskKey].concerns = concerns
    concernsHint = formatConcernsHint(concerns)
    log(...)
  }
  return { impl, halted: false, concerns, concernsHint, filesChanged: impl.evidence.files_changed }
}
```

**替换** run-plans.js:1399-1431（~32 行 → 1 函数调用）。

concerns/concernsHint 是闭包变量，通过返回值传出（不能像原代码直接 mutate 闭包）。

### 6.2 B3-1 TDD 流程与风险

**TDD 流程**：
1. RED：
   - helpers.test 加 recordReviewRound 3 个用例
   - helpers.test 加 decideReviewOutcome 9 个用例（覆盖 9 个 action 分支）
   - runFixRound 不写单测（runtime 靠回归）
2. GREEN：
   - lib.js 加 recordReviewRound + decideReviewOutcome；run-plans.js inline
   - run-plans.js 加 runFixRound；主循环改为调用 3 函数
3. SYNC：sync.test 加 2 纯函数字节断言；spec §5.5 加 3 函数说明
4. FULL：307 + 12 测试 + 现有 review 循环测试全绿

**风险（高）**：
- decideReviewOutcome 9 分支需逐字对齐原 reason + diag
- runFixRound concerns/concernsHint 闭包变量改返回值传出
- 主循环改写后需手动 trace r1/r2/r3 + OSCILLATING + budget guard 路径

**缓解**：分 3 个 sub-commit，每个全量回归。

### 6.3 B3-2: S3 simplify helper 拆分（3 个 runtime 函数）

3 个函数全留 run-plans.js（调 safeAgent，按 §4.3）。

#### 6.3.1 checkSimplifyChanges

```js
async function checkSimplifyChanges(taskId) {
  const diffSchema = { type: 'object', required: ['changed', 'files'], properties: { changed: { type: 'boolean' }, files: { type: 'array', items: { type: 'string' } } } }
  const diffResult = await safeAgent('Run `git status --porcelain`...', { schema: diffSchema, label: `diff:${taskId}` })
  if (!diffResult || typeof diffResult !== 'object' || typeof diffResult.changed !== 'boolean' || (diffResult.changed === true && !Array.isArray(diffResult.files))) {
    return { error: true, reason: 'simplify diff check failed', diag: { task: taskId, diffResult: diffResult || null } }
  }
  return { error: false, changed: diffResult.changed === true, files: Array.isArray(diffResult.files) ? diffResult.files : [] }
}
```

替换 run-plans.js:1462-1472（11 行 → 1 行调用）。

#### 6.3.2 amendSimplifyCommit

```js
async function amendSimplifyCommit(taskId, commitSha) {
  const amendSchema = { type: 'object', required: ['ok'], properties: { ok: { type: 'boolean' }, sha: { type: 'string' }, error: { type: 'string' } } }
  const amendResult = await safeAgent('Run `git add -A && git commit --amend --no-edit`...', { schema: amendSchema, label: `amend:${taskId}` })
  const amendCheck = validateAmendResult(amendResult)
  if (!amendCheck.valid) {
    return { error: true, reason: 'simplify amend failed', diag: { task: taskId, amendError: amendCheck.error, commitSha } }
  }
  return { error: false, sha: amendCheck.sha }
}
```

替换 run-plans.js:1489-1497（9 行 → 1 行调用）。

#### 6.3.3 revertSimplifyChanges

```js
async function revertSimplifyChanges(taskId, commitSha) {
  const checkoutSchema = { type: 'object', required: ['ok'], properties: { ok: { type: 'boolean' }, porcelain: { type: 'string' }, error: { type: 'string' } } }
  const checkoutResult = await safeAgent('Run `git reset --hard HEAD && git clean -fd`...', { schema: checkoutSchema, label: `checkout:${taskId}` })
  const checkoutCheck = validateCheckoutResult(checkoutResult)
  if (!checkoutCheck.valid) {
    return { error: true, reason: 'simplify checkout failed', diag: { task: taskId, checkoutError: checkoutCheck.error, commitSha } }
  }
  return { error: false }
}
```

替换 run-plans.js:1506-1514（9 行 → 1 行调用）。

### 6.4 B3-2 主流程改写后（~65 行 → ~25 行）

```js
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

### 6.5 B3-2 TDD 流程与风险

**TDD 流程**（D17 决策，不加新单测）：
1. RED：无（runtime 函数依赖 safeAgent 全局，mock 成本高）
2. GREEN：run-plans.js 加 3 函数 + 主流程改写
3. SYNC：spec §5.2 方案 C 加 3 函数说明；sync.test 加 3 函数存在性断言（grep 函数名）
4. FULL：307 测试全绿（靠回归）

**风险（中）**：
- 3 函数返回 `{ error: true, ... }` vs `{ error: false, ... }` 调用方判断需逐处核对
- revertSimplifyChanges 成功时不返回 sha（HEAD 不变），调用方不更新 commit_sha
- simplify_review_findings 赋值在 revert 成功后，保留在调用方

**缓解**：B3-2 在 B3-1 全绿后做。改写后手动 trace 4 条路径（simplify 全绿/amend 失败/review 失败/checkout 失败）。

### 6.6 Batch 3 测试与 commit

- **新测试**：+12
- **commit 粒度**：5 个分项 commit（D20 决策）
  1. recordReviewRound
  2. decideReviewOutcome
  3. runFixRound
  4. S3 三 helper 合一
  5. 主流程集成

---

## 7. 全局测试策略

### 7.1 回归保护网

1. **sync.test 字节断言**：所有 lib.js 纯函数 inline 副本一致性（前移到 Batch 2 每项落地时补）
2. **helpers.test 纯函数测试**：每个 helper 的行为契约
3. **MEDIUM-1 trip-wire**：PROMPTS 无内嵌反引号 / 纯函数体平衡 / SCHEMAS 结尾正确
4. **HIGH-1 数据流测试**：bootstrap schema 含 lesson_categories（闭合分层测试盲区）
5. **现有 307 测试**：核心控制流守护

### 7.2 CRLF 修复

每批次 commit 前对修改的 .js/.md 文件执行：
```bash
perl -i -pe 's/(?<!\r)\n/\r\n/g' <file>
```

### 7.3 全量回归命令

```bash
node --test docs/superpowers/workflows/tests/*.test.js
```

每批次结束、每个 commit 前必跑。

### 7.4 执行顺序（D21 决策）

严格串行 Batch 1 → Batch 2 → Batch 3，每批次全绿后才进下一批。

---

## 8. Self-Review / Decision Audit Trail

### 8.1 设计自审清单

| 检查项 | 状态 | 说明 |
|---|---|---|
| 所有改动遵守 §4.3 分层约束 | ✓ | 纯函数进 lib.js，runtime 留 run-plans.js，PROMPT 常量进 PROMPTS |
| 所有改动不引入 fs/subprocess/Date.now/Math.random | ✓ | 仅字符串/数组操作 + 已有 runtime 调用 |
| 所有改动不改变 agent 调用数 | ✓ | 重构是抽 helper，调用点不变 |
| TDD 流程每项有 RED→GREEN→SYNC→FULL | ✓ | runtime 函数（B3-2）靠回归，无 RED，已说明理由 |
| sync.test 字节断言覆盖所有新纯函数 | ✓ | Batch 2 每项 + Batch 3 recordReviewRound/decideReviewOutcome |
| spec 同步更新 | ✓ | §4.4/§5.2/§5.5/§6.2/§13b 各项对应 |
| commit 粒度合理 | ✓ | 3+5+5=13 个 commit，便于回滚 |
| 风险递增顺序 | ✓ | 低→中→高，每批建立保护网后再进下一批 |

### 8.2 潜在风险与缓解

| 风险 | 缓解 |
|---|---|
| B2-5 formatBulletSection 重构输出不一致 | 用 diff 验证 6 个 wrapper 输出逐字节一致 |
| B2-7/B2-8 prompt 变化破坏 sync.test 基线 | 同步更新 prompt 断言基线 |
| B3-1 decideReviewOutcome 9 分支 reason 不对齐 | 逐字对照原实现，9 个用例覆盖 |
| B3-1 runFixRound concerns 闭包变量丢失 | 通过返回值传出，调用方更新 |
| B3-2 simplify helper error 判断遗漏 | 手动 trace 4 条路径 |
| CRLF 行尾不一致 | 每批次 commit 前 perl 修复 |

### 8.3 不在范围

- S13 dispatchImpl 改名（D6 决策，不改）
- 现有 plan frontmatter 批量添加 lesson_categories（D3 决策，留给 plan 作者）
- AST 重构 sync.test（D4 决策，用 trip-wire 代替）
- B3-2 runtime 函数单测（D17 决策，靠回归）

---

## 9. 实施计划入口

设计批准后，调用 writing-plans 技能创建实施计划，计划文件写入 `docs/superpowers/workflow-plans/2026-07-07-simplification-tdd-fix.md`。

计划结构（建议）：
- Task 1: Batch 1 commit 1（HIGH-1 补链）
- Task 2: Batch 1 commit 2（MEDIUM-1 + LOW-1/2/3/4）
- Task 3: Batch 1 commit 3（S11/S12/S14 清理）
- Task 4: Batch 2 commit 1（B2-1/2/3/6 低风险合一）
- Task 5: Batch 2 commit 2（B2-4 checkImplStatus）
- Task 6: Batch 2 commit 3（B2-5 formatBulletSection）
- Task 7: Batch 2 commit 4（B2-7 QUOTA_HALT_NOTE）
- Task 8: Batch 2 commit 5（B2-8 STATIC_READONLY_NOTE）
- Task 9: Batch 3 commit 1（recordReviewRound）
- Task 10: Batch 3 commit 2（decideReviewOutcome）
- Task 11: Batch 3 commit 3（runFixRound）
- Task 12: Batch 3 commit 4（S3 三 helper）
- Task 13: Batch 3 commit 5（主流程集成）

每个 Task 严格 TDD 4 阶段，全量回归后 commit。
