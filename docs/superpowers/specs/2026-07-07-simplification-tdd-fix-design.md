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

> **复核补充（2026-07-07）**：除审计 16 项外，新增 **B1-10 通用性守护断言**（sync.test 加一条防项目耦合断言，见 §4.10）——这是复核"保持 workflow 通用性"诉求时新增的防御项，**不计入审计 16 项**，是额外的通用性护栏。实施时并入 Batch 1 commit 2。

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
| D4 | MEDIUM-1 防御 | 加 2 个 trip-wire 守护断言（删脆弱点 1 的 trip-wire） | 见 D4 复核修正 |
| D5 | LOW-1 `?? 3` | 保留死路径作防御性兜底 | helpers.test 可能直接调 fixModelForRound 不传 maxRounds |
| D6 | S13 改名 | 不改名 | 13 处调用点风险高，收益仅命名美化 |
| D7 | S14 处理 | 标 @deprecated | 有 helpers.test 覆盖说明曾为公共 API，软过渡 |
| D8 | 实施策略 | 方案 C 按风险递增 | 低风险建安全网 → 中风险纯函数 → 高风险 runtime 重构 |
| D9 | B2-3 errStr | 提升到 lib.js | makeHalt 内部调 errStr，调用方更简洁 |
| D10 | B2-4 checkImplStatus reason | 逐字对齐原实现 | 防止 reason 字符串变化破坏现有日志/诊断。复核修正（2026-07-07）：原实现的 reason 把 `${impl.status}` 放在中间（如 `implementor ${impl.status} after retry`），3-arg `reasonPrefix` 函数形只能把 status 放尾部 → 逐字对齐不可能。改签名为 `reasonTemplate='implementor {status}'`，函数内 `reasonTemplate.replace('{status}', impl.status)`，调用方传 `'implementor {status} after retry'` 等。保留 helper 抽取 + D10 + 调用简洁。 |
| D11 | B2-5 formatBulletSection outro | 支持多行 string | 原 6 个 format* 的 outro 有单行也有多行 |
| D12 | B2-7 buildPrompt 默认注入 | 内置 quotaHaltNote 默认值 | 调用方不需显式传（opt-out 见 buildPrompt defaults 语义注） |
| D13 | ~~B2-8 LESSONS_EXEMPTION_NOTE~~ | **已撤销（三轮复核 2026-07-07）** | 原以为 3 reviewer 共享 Exemption 段；核查实际：specReview 是 EXTRA 检测（9 行 'NOT EXTRA'）、qualityReviewer 是质量维度豁免（12 行 '限定维度硬性豁免'）、hunter 无此段。三段概念不同非参数化差异 → 无可去重公共文本，DROP。 |
| D14 | B2-8 STATIC_READONLY_NOTE | 函数，调用方传 reviewType（三轮复核修正） | 原决策"进 buildPrompt 默认（3 reviewer 文本一致）"错误：核查实际仅 specReview/qualityReviewer 文本近似（唯一差异 'spec verification' vs 'quality review'），hunter 文本实质不同（'git status, git diff' 顺序 + 'silent-failure hunting' 措辞）。改为 `STATIC_READONLY_NOTE(reviewType)` 函数（D13 风格），仅 specReview/qualityReviewer 2 处去重，hunter 保留原文。不进 buildPrompt 默认（reviewType 随 reviewer 变）。 |
| D15 | B3-1 decideReviewOutcome action | 10 个 action 枚举 | halt 的 6 个子类用 reason 区分，action 顶层用 halt 统一；非 halt 4 个（break/escalate/continue/fix）。详见 D15 复核修正 |
| D16 | B3-1 runFixRound | 不用 checkImplStatus | fix-round 的 blocked/failed/needs_context 都是 halt，语义不同于初始 dispatch |
| D17 | B3-2 测试策略 | 不加新单测 | 可测逻辑已被 lib.js 纯函数测试覆盖，剩余只是胶水调用（详见 D17 复核修正） |
| D18 | Batch 1 commit | 3 个分项 | HIGH-1 / MEDIUM-1+LOW / S11-S14 清理 |
| D19 | Batch 2 commit | 8 个分项 | 复核修正：B2-1/2/3/6 独立无依赖，拆为各自 commit 保回滚粒度（初稿 5 个合一削弱回滚）；B2-4/5/7/8 各一 |
| D20 | Batch 3 commit | 5 个分项 | recordReviewRound / decideReviewOutcome / runFixRound / S3 三 helper 合一 / 主流程集成 |
| D21 | 执行顺序 | 严格串行 Batch 1 → 2 → 3 | 每批次全绿后才进下一批 |

> **D4 复核修正（2026-07-07 复核）**：初稿列出 3 个 trip-wire，复核发现 **trip-wire 1（PROMPTS 反引号成对性）对当前代码就 FAIL 且守住不了一致目标**，删除。理由：
>
> 1. **对当前代码 FAIL**：PROMPTS 是多行模板字面量，每个 `role: \`多行...\`` 起始行只有一个开反引号（奇数），闭合反引号在数行后 `RED FLAG:...\`` 行。按行断言 `rawBackticks % 2 === 0` 对每个 `role:` 行必然 FAIL（实测 24 行奇数）。
> 2. **根因是误译**：MEDIUM-1 脆弱点 1 是"单个 prompt 模板内出现**成对内嵌反引号**（如 `` `${var}` ``）导致 `[\s\S]*?` 过早闭合"，而非"每行反引号成对"。多行模板字面量的正常形态就是跨行成对。
> 3. **方案 A（按 `promptBody` 正文断言反引号计数 == 0）也有假阳性**：若 prompt 真含成对内嵌反引号，`promptBody` 的非贪婪正则在第一个内嵌反引号处闭合，截断后正文反引号计数为 0 → 假绿通过。
>
> **结论**：脆弱点 1 的根治只能靠 AST 解析（D4 已排除 AST 重构），trip-wire 守不住。删 trip-wire 1，保留 trip-wire 2（纯函数体大括号平衡）/ trip-wire 3（SCHEMAS 结尾 `\n}`），代价更小且两者确实有效。

> **D15 复核修正（2026-07-07 复核）**：初稿称"9 个 action 枚举（halt 的 5 个子类 + ...）"。追踪 review 循环（`run-plans.js:1330-1397`）halt 子类实际是 **6 个**（不是 5 个）：
>
> | # | 路径 | reason |
> |---|---|---|
> | 1 | reviewReason halt | `reviewReason` |
> | 2 | emptyFailed halt | `emptyFailedReason` |
> | 3 | regressed halt | `'OSCILLATING'` (regressed) |
> | 4 | flipFlop halt | `'OSCILLATING'` (flipFlop) |
> | 5 | budget guard halt（maxRounds=0） | `'review_not_converging'` |
> | 6 | maxRounds halt（有限模式） | `'review max rounds'` |
>
> 外加 4 个非 halt action：`break` / `escalate` / `continue` / `fix`。**总计 10 个 action 分支**（6 halt + 4 非 halt）。§6.1.2 伪代码已含 budget/maxRounds 分支（逻辑正确），仅 action 枚举计数漏列。budget guard（`maxRounds === 0`）与 `round === maxRounds`（有限模式）是 `if/else if` 互斥分支，decideReviewOutcome 必须都覆盖。

> **D17 复核修正（2026-07-07 复核）**：决策本身保留（B3-2 不加新单测），但理由从"mock 成本高"改为更准确的论证：simplify 三个 helper 的可测逻辑分层为——schema 定义（纯字面量，sync.test 存在性断言可守）、`validateAmendResult`/`validateCheckoutResult` 调用（**已是 lib.js 纯函数，helpers.test 已覆盖失败分支**）、`{error:true,...}` 构造（纯构造，可源码字面量断言）、`safeAgent()` 调用（runtime，唯一难测点）。**真正未测的只是胶水调用，可测逻辑已被 lib.js 纯函数测试覆盖**。本项目已有 `dispatchImpl-retry.test.js` 用源码字面量断言绕过 mock 的先例，"runtime 一律不测"是误解。

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
| Batch 1 | +6 | +0 | 313 |
| Batch 2 | +19 | +8 + prompt 基线更新 | 332 |
| Batch 3 | +14 | +5 | 346 |

> **复核修正说明（二轮，2026-07-07）**：初稿 Batch 1 +5（MEDIUM-1 +3）/ Batch 2 +17 / Batch 3 +12（decideReviewOutcome 9 用例）。一轮复核后 Batch 1 +6（MEDIUM-1 删 trip-wire 1 → +2，新增 B1-10 通用性守护 +1）/ Batch 3 +13（decideReviewOutcome halt 子类 6 个 → 10 用例，+1）。二轮复核（对照 plan 实际 test() 块计数）进一步修正：Batch 2 +19（B2-1 taskKey=2, B2-2 REVIEW_SOURCES=1, B2-3 makeHalt=2, B2-4 checkImplStatus=4, B2-5 formatBulletSection=3, B2-6 formatFindingItem=3, B2-7 QUOTA_HALT_NOTE=2[默认注入+opt-out], B2-8 STATIC_READONLY=2[默认注入+LESSONS_EXEMPTION_NOTE 传参]）/ Batch 3 +14（recordReviewRound=3, decideReviewOutcome=10, runFixRound=0, S3 simplify helper sync.test=1）。累计终点 307+6+19+14 = 346。**plan 为权威计数**，spec §3.3 表格同步对齐。
>
> **三轮修正（2026-07-07，Task 10/11 实施前）**：
> - B2-7 QUOTA_HALT_NOTE 范围从"7 prompt 去重"修正为"5 prompt 去重 + implementor/lessonDistiller 保留变体"（implementor 含 `（非 failed/blocked）` 防 agent 误返 blocked 触发升级链；lessonDistiller 用 `decisions:[{action:'skip'}]` 非 model_unavailable status）。RED 测试从 implementor target 改 specReview target（implementor 非替换目标）。B2-7 测试 +2→+3（常量内容 + 默认注入 + opt-out）。
> - B2-8 STATIC_READONLY_NOTE + LESSONS_EXEMPTION_NOTE：**DROP LESSONS_EXEMPTION_NOTE**（specReview 是 EXTRA 检测 / qualityReviewer 是质量维度豁免 / hunter 无此段——概念不同非参数化差异，无可去重公共文本）。STATIC_READONLY_NOTE 改为**函数** `STATIC_READONLY_NOTE(reviewType)`（D13 风格，非 buildPrompt 默认），仅 specReview/qualityReviewer 2 处去重（hunter 文本不同保留）。B2-8 测试 +2（reviewType 插值 + buildPrompt 注入）不变。
> - Batch 2 累计 +19→+20，终点 346→347 不变（B2-8 仍 +2）。各实际 commit 后的全量回归数为准（307→313→332→...→347，每 task 累加该 task 实际新增 test() 块数）。

---

## 4. Batch 1 低风险批详情

### 4.1 B1-1: HIGH-1 补全 lesson_categories 提取链

**改动 3 处**：

| 文件 | 位置 | 改动 |
|---|---|---|
| run-plans.js + lib.js | bootstrap prompt step 3 | 末尾加：「Also extract `lesson_categories` from frontmatter if present (format: `lesson_categories:\n  - silent-failure\n  - test-strategy`). Return per task as `lesson_categories` array (absent → empty array).」 |
| run-plans.js + lib.js | bootstrap Return schema (line 802) | `tasks:[{id, model, title}]` → `tasks:[{id, model, title, lesson_categories}]` |
| sync.test.js | 新增源码字面量断言 | 断言 bootstrap prompt 文本含 `lesson_categories` 提取说明 **且** Return schema 字符串含 `lesson_categories` 字段（见下方 TDD 流程说明） |

**TDD 流程**：
1. RED：sync.test 加源码字面量断言——`promptBody(runSrc, 'bootstrap')` 含 `lesson_categories` + bootstrap Return schema 字符串含 `lesson_categories`（当前两处均缺 → fail）
2. GREEN：改 prompt + schema + run-plans.js inline 同步
3. SYNC：workflow-design.md §5.5 加注「plan frontmatter 可声明 lesson_categories 启用精确匹配；未声明时 fallback 到 title 关键词匹配」
4. FULL：307 + 1 测试

**测试性质说明（复核修正）**：本测试是**源码字面量断言**（断言 prompt 文本/schema 字符串含字段名），**不是端到端数据流测试**。helpers.test 测的是 lib.js 纯函数，bootstrap 是 runtime（调 agent），helpers.test 无法测真实 bootstrap 返回值。审计报告 HIGH-1 的"分层测试盲区"**未真正闭合**——只是把盲区从"字段缺失"移到"字段名存在于 prompt/schema 文本"。真正的端到端数据流测试需要 mock agent 返回（成本高，留作未来改进）。本测试的价值在于：防止后续重构误删 prompt 提取说明或 schema 字段（这类回归可被捕获）。

**范围说明**：只补提取链 + schema。不在范围：批量给现有 plan frontmatter 添加 lesson_categories 声明（数据迁移留给 plan 作者按需 opt-in）。

> **prompt 文案风格注（复核补充，低优先级）**：增量文案 "Also extract `lesson_categories`..." 是中英混杂，与周围 bootstrap prompt 现状（step 5 的 "Workflow artifact changes" / "lessons.md" 等已是中英混杂）一致。跟随现状即可——若未来统一 prompt 语言风格，再一并处理，不在本次范围。

### 4.2 B1-2: MEDIUM-1 sync.test 守护断言

**改动 1 处**（sync.test.js 顶部加 2 个 trip-wire 测试；初稿列 3 个，复核后删 trip-wire 1，理由见 D4 复核修正）：

```js
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

**TDD 流程**：直接 GREEN（断言当前代码已满足，实测 55 个 `export function` 大括号均平衡、SCHEMAS 块以 `\n}` 结尾）。

**不再包含的 trip-wire 1**（PROMPTS 反引号成对性）：对当前代码就 FAIL（多行模板字面量每行反引号不成对），且即使改为按 `promptBody` 正文断言也有假阳性（非贪婪正则遇内嵌反引号截断后正文反引号计数为 0）。脆弱点 1 只能靠 AST 解析根治，trip-wire 守不住，见 D4 复核修正。

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

### 4.10 B1-10: 通用性守护断言（新增，复核补充）

**动机**：该 workflow 设计为跨项目复用的通用工具，需防止项目耦合（本项目专有路径/文件名）混入 PROMPTS。一旦耦合，移植到其他项目即失效。

**改动 1 处**（sync.test.js 加 1 个断言，并入 B1-2 的 MEDIUM-1 守护区）：

```js
test('通用性守护：PROMPTS 不得含本项目专有路径/文件名', () => {
  const src = readFileSync(libPath, 'utf8')
  const m = src.match(/const PROMPTS = \{[\s\S]*?\n\}/)
  assert.ok(m, 'PROMPTS 块存在')
  // 项目耦合黑名单（lottery-notification 专有）。项目特定内容应靠 config 驱动注入，
  // 不应硬编码进通用 workflow 的 PROMPTS。
  const blacklist = [
    'lottery', 'notification',           // 仓库名/项目名
    'lessons.md',                         // 本项目 lessons 文件名（应靠 config.lessons_path）
  ]
  for (const bad of blacklist) {
    assert.ok(!m[0].toLowerCase().includes(bad.toLowerCase()),
      `PROMPTS 含本项目专有词 "${bad}"——项目耦合，应改 config 驱动注入`)
  }
})
```

**TDD 流程**：直接 GREEN（断言当前 PROMPTS 不含黑名单词；实测 bootstrap prompt 用 `{{configPath}}`/`{{plansDir}}` 占位符，无硬编码路径）。

**说明**：黑名单是保守起点，实施时若发现已耦合项可补。本断言防止未来重构（尤其 B2-7/B2-8 prompt 改动）误把项目内容硬编码进通用 PROMPTS。

### 4.11 Batch 1 测试与 commit

- **新测试**：+6（HIGH-1 +1, MEDIUM-1 +2[删 trip-wire 1 后], LOW-4 +1, 通用性守护 +1, 见 §4.11）

> 注：初稿列 +5（MEDIUM-1 +3），复核删 trip-wire 1（MEDIUM-1 → +2）+ 新增 B1-10 通用性守护（+1）= 净 +6。
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

**签名**（复核修正 2026-07-07：`reasonTemplate` 替代 `reasonPrefix`，详见 D10 修正说明）：
```js
// lib.js
export function checkImplStatus(impl, allowed = ['ok', 'done_with_concerns'], reasonTemplate = 'implementor {status}') {
  if (impl.halted) return impl
  if (!allowed.includes(impl.status)) {
    return { halted: true, reason: reasonTemplate.replace('{status}', impl.status), diag: impl.diagnostics }
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

reason 逐字对齐原实现（D10 决策），如 `'implementor failed after retry'`。调用方传 reasonTemplate：`'implementor {status} after retry'`、`'implementor {status} after context-fetch'`、`'implementor {status} after context-fetch retry'`。

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

**重构 6 个 format* 为 wrapper**（复核修正：**5→1 + 1 复杂 wrapper**，非"6→1"）：
- formatReferencePaths
- formatSilentFailureContext
- formatFailedApproaches
- formatLessons
- formatUniversalLessons
- formatDomainLessons（**复杂 wrapper**：含过滤 silent-failure + category 匹配/title fallback + 同 plan 优先 sort + cap 5 业务逻辑，formatBulletSection 只负责最后渲染 bullet lines，前 4 步留在 wrapper）

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

> **buildPrompt defaults opt-out 语义（D12/D14 统一注，复核补充）**：`{ ...defaults, ...ctx }` 合并意味着调用方可传 `quotaHaltNote: ''`（或 B2-8 的 `staticReadonlyNote: ''`）**显式关闭默认注入**（覆盖默认值，注入空串）。这是通用性的关键——默认开（多数 reviewer 需要）、可 opt-out（未来某 reviewer 不需要限额 halt 说明时可关）。spec 此处点明 opt-out 路径，避免未来读者误以为默认注入是不可关闭的硬约束。

**TDD 流程**：
1. RED：helpers.test 加 QUOTA_HALT_NOTE 常量断言 + buildPrompt 注入/opt-out 断言（specReview target，非 implementor——见下范围修正）
2. GREEN：lib.js 加常量 + buildPrompt 默认 + **5** prompt 替换占位符；run-plans.js inline
3. SYNC：sync.test prompt 字节断言更新（5 个替换 prompt 体变了）
4. FULL：307 + 3 测试 + 所有现有 prompt 字节断言更新基线

> **替换范围修正（2026-07-07，实施前复核）**：原 spec/audit 称 7 个 prompt 共享相同重复文本。核查当前代码实际 3 类：
> - **5 个完全匹配常量**（`（非 failed），让 orchestrator halt 并保存进度。`）：specReview/qualityReviewer/hunter/commit/gate → 替换为 `{{quotaHaltNote}}` 占位符。
> - **implementor 变体**（`（非 failed/blocked）`）：implementor schema enum 含 `blocked` 状态，限额说明须区分 model_unavailable 与 failed/blocked（防 agent 误返 blocked 触发升级链而非 halt）。**保留原文**。
> - **lessonDistiller 完全不同**（用 `decisions: [{action:'skip'}]`，非 model_unavailable status）：**保留原文**。
> 故实际替换 **5 处**（非 7）。常量仍统一限额话术，但尊重各 prompt 的 schema 语义差异。

### 5.8 B2-8: S8 STATIC_READONLY_NOTE（三轮复核修正：DROP LESSONS_EXEMPTION_NOTE）

**复核修正（2026-07-07，实施前）**：原决策假设 3 reviewer prompt 共享 STATIC READ-ONLY + Lessons Exemption 重复段。核查当前代码实际：
- **STATIC READ-ONLY**：specReview/qualityReviewer 文本近似（唯一差异 `spec verification` vs `quality review`），hunter 实质不同（`git status, git diff` 顺序 + `silent-failure hunting` 措辞）。**仅 specReview/qualityReviewer 2 处可去重**，hunter 保留原文。
- **Lessons Exemption**：specReview 是 EXTRA 检测（9 行 'NOT EXTRA' 语义）、qualityReviewer 是质量维度豁免（12 行 '限定维度硬性豁免' 语义）、hunter 无此段。三段**概念不同**（非参数化差异）→ **无可去重公共文本，DROP**。

**签名**（STATIC_READONLY_NOTE 改为函数，D13 风格，非 buildPrompt 默认——reviewType 随 reviewer 变）：
```js
export function STATIC_READONLY_NOTE(reviewType) {
  return `This is a STATIC READ-ONLY review. You may use 'git diff', 'git status', 'find', 'grep'/'rg', and read files to locate and inspect changes. Do NOT run the test suite, ruff, lint, or any build — ${reviewType} is done by reading code, not by running it. Running tests/builds is the implementor's and gate's job, not yours.`
}
```
（reviewType = 'spec verification' / 'quality review'）

**改动**：
- lib.js 加 `STATIC_READONLY_NOTE(reviewType)` 函数（export）
- specReview prompt 的 STATIC READ-ONLY 段替换为 `{{staticReadonlyNote}}` 占位符
- qualityReviewer prompt 的 STATIC READ-ONLY 段替换为 `{{staticReadonlyNote}}` 占位符
- hunter prompt **不动**（文本不同）
- run-plans.js 调用 specReview/qualityReviewer 的 buildPrompt 处传 `staticReadonlyNote: STATIC_READONLY_NOTE('spec verification'/'quality review')`
- run-plans.js inline 副本同步 STATIC_READONLY_NOTE 函数

**TDD 流程**：
1. RED：helpers.test 加 STATIC_READONLY_NOTE 函数测试（2 用例：reviewType 插值 + 内容关键词）
2. GREEN：lib.js 加函数 + 重构 2 prompt；run-plans.js inline + 调用点传参
3. SYNC：sync.test prompt 断言更新（specReview/qualityReviewer prompt 体变）
4. FULL：307 + 2 测试（STATIC_READONLY_NOTE 内容 + reviewType 插值）

### 5.9 Batch 2 测试与 commit

- **新测试**：+17
- **commit 粒度**：8 个分项 commit（复核修正：初稿 5 个，B2-1/2/3/6 合一削弱回滚粒度，拆为各自独立）
  1. B2-1 taskKey
  2. B2-2 REVIEW_SOURCES
  3. B2-3 makeHalt + errStr
  4. B2-6 formatFindingItem
  5. B2-4 checkImplStatus
  6. B2-5 formatBulletSection
  7. B2-7 QUOTA_HALT_NOTE
  8. B2-8 STATIC_READONLY_NOTE + LESSONS_EXEMPTION_NOTE

**拆分理由**：B2-1/2/3/6 四项互相独立、无依赖（taskKey 是字符串拼接，REVIEW_SOURCES 是常量，makeHalt 是错误构造，formatFindingItem 是 finding 格式化）。合一 commit 唯一好处是省 commit 动作，但若 B2-3 引入回归，回滚会连带 B2-1/2/6（都是好改动）。本项目 commit 历史偏好细粒度单主题。

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
  // action 枚举（D15 决策，详见 D15 复核修正）：
  //   - 'halt' (reason 区分 6 子类: reviewReason/emptyFailed/regressed/flipFlop/budget/maxRounds)
  //   - 'break' (allGreen)
  //   - 'escalate' (osc + flipFlop=false + shouldEscalate)
  //   - 'continue' (osc + flipFlop=false + alreadyEscalated)
  //   - 'fix' (else)
  // 共 10 个 action 分支（6 halt + 4 非 halt）
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
   - helpers.test 加 decideReviewOutcome 10 个用例（覆盖 10 个 action 分支：6 halt 子类 + break/escalate/continue/fix，详见 D15 复核修正）
   - runFixRound 不写单测（runtime 靠回归）
2. GREEN：
   - lib.js 加 recordReviewRound + decideReviewOutcome；run-plans.js inline
   - run-plans.js 加 runFixRound；主循环改为调用 3 函数
3. SYNC：sync.test 加 2 纯函数字节断言；spec §5.5 加 3 函数说明
4. FULL：307 + 13 测试 + 现有 review 循环测试全绿

**风险（高）**：
- decideReviewOutcome 10 分支需逐字对齐原 reason + diag（6 halt 子类 + 4 非 halt，详见 D15 复核修正）
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
1. RED：无（可测逻辑已被 lib.js 纯函数测试覆盖——validateAmendResult/validateCheckoutResult 的失败分支已有 helpers.test 覆盖；3 helper 剩余只是 safeAgent 胶水调用，详见 D17 复核修正）
2. GREEN：run-plans.js 加 3 函数 + 主流程改写
3. SYNC：spec §5.2 方案 C 加 3 函数说明；sync.test 加 3 函数存在性断言（grep 函数名）
4. FULL：全量回归全绿（靠现有测试守护行为不变）

**风险（中）**：
- 3 函数返回 `{ error: true, ... }` vs `{ error: false, ... }` 调用方判断需逐处核对
- revertSimplifyChanges 成功时不返回 sha（HEAD 不变），调用方不更新 commit_sha
- simplify_review_findings 赋值在 revert 成功后，保留在调用方

**缓解**：B3-2 在 B3-1 全绿后做。改写后手动 trace 4 条路径（simplify 全绿/amend 失败/review 失败/checkout 失败）。

### 6.6 Batch 3 测试与 commit

- **新测试**：+13（recordReviewRound +3, decideReviewOutcome +10, runFixRound 0）
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
3. **MEDIUM-1 trip-wire**：纯函数体大括号平衡 / SCHEMAS 结尾正确（2 个，trip-wire 1 已删，见 D4 复核修正）
4. **HIGH-1 数据流测试**：bootstrap prompt/schema 文本含 lesson_categories（源码字面量断言；非端到端，盲区未完全闭合，见 B1-1 测试性质说明）
5. **通用性守护**（B1-10）：PROMPTS 不得含本项目专有路径/文件名（防项目耦合）
6. **现有 307 测试**：核心控制流守护

### 7.2 CRLF 修复

每批次 commit 前对修改的 .js/.md 文件执行：
```bash
perl -i -pe 's/(?<!\r)\n/\r\n/g' <file>
```

> **本项目特定约束注（复核补充）**：CRLF 强制是 lottery-notification 仓库的本地约定（`.gitattributes` 注明 Windows 项目 + sync.test promptBody 正则对行尾敏感），**不是通用 workflow 的约束**。若本 workflow 被其他项目（非 Windows、用 LF）复用，此步骤应删除或改为跟随目标仓库的行尾约定。移植者注意：sync.test 的 promptBody 行尾一致性断言依赖固定行尾——若目标仓库用 LF，相应断言也需调整。

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
| commit 粒度合理 | ✓ | 3+8+5=16 个 commit，便于回滚（复核修正：初稿 3+5+5=13，Batch 2 拆分后 8 个） |
| 风险递增顺序 | ✓ | 低→中→高，每批建立保护网后再进下一批 |

### 8.2 潜在风险与缓解

| 风险 | 缓解 |
|---|---|
| B2-5 formatBulletSection 重构输出不一致 | 用 diff 验证 6 个 wrapper 输出逐字节一致 |
| B2-7/B2-8 prompt 变化破坏 sync.test 基线 | 同步更新 prompt 断言基线 |
| B3-1 decideReviewOutcome 10 分支 reason 不对齐 | 逐字对照原实现，10 个用例覆盖（6 halt 子类 + 4 非 halt，详见 D15 复核修正） |
| B3-1 runFixRound concerns 闭包变量丢失 | 通过返回值传出，调用方更新 |
| B3-2 simplify helper error 判断遗漏 | 手动 trace 4 条路径 |
| CRLF 行尾不一致 | 每批次 commit 前 perl 修复 |

### 8.3 不在范围

- S13 dispatchImpl 改名（D6 决策，不改）
- 现有 plan frontmatter 批量添加 lesson_categories（D3 决策，留给 plan 作者）
- AST 重构 sync.test（D4 决策，用 2 个 trip-wire 代替；脆弱点 1 的 trip-wire 已删，见 D4 复核修正）
- B3-2 runtime 函数单测（D17 决策，靠回归）

---

## 9. 实施计划入口

设计批准后，调用 writing-plans 技能创建实施计划，计划文件写入 `docs/superpowers/workflow-plans/2026-07-07-simplification-tdd-fix.md`。

计划结构（建议）：
- Task 1: Batch 1 commit 1（HIGH-1 补链）
- Task 2: Batch 1 commit 2（MEDIUM-1 守护[含 B1-10 通用性守护] + LOW-1/2/3/4）
- Task 3: Batch 1 commit 3（S11/S12/S14 清理）
- Task 4: Batch 2 commit 1（B2-1 taskKey）
- Task 5: Batch 2 commit 2（B2-2 REVIEW_SOURCES）
- Task 6: Batch 2 commit 3（B2-3 makeHalt + errStr）
- Task 7: Batch 2 commit 4（B2-6 formatFindingItem）
- Task 8: Batch 2 commit 5（B2-4 checkImplStatus）
- Task 9: Batch 2 commit 6（B2-5 formatBulletSection）
- Task 10: Batch 2 commit 7（B2-7 QUOTA_HALT_NOTE）
- Task 11: Batch 2 commit 8（B2-8 STATIC_READONLY_NOTE + LESSONS_EXEMPTION_NOTE）
- Task 12: Batch 3 commit 1（recordReviewRound）
- Task 13: Batch 3 commit 2（decideReviewOutcome）
- Task 14: Batch 3 commit 3（runFixRound）
- Task 15: Batch 3 commit 4（S3 三 helper）
- Task 16: Batch 3 commit 5（主流程集成）

> **复核修正**：初稿 13 个 Task，Batch 2 拆分后 16 个 Task（3+8+5）。每个 Task 严格 TDD 4 阶段，全量回归后 commit。
