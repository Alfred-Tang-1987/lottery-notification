# Reviewer Consensus Matrix 设计

> **日期**: 2026-07-05 | **类型**: workflow orchestrator 增强 | **状态**: spec
> **前置**: Plan 01–05 已完成（318 tests green），run-plans.js 稳定运行
> **参考**: [loop-engineering-evaluation](../workflows/research/loop-engineering-evaluation-2026-07-05.md) §8.3

## 1. 问题与动机

### 1.1 现状

当前 review 循环的共识模型是隐式的：

```
allGreen(spec, qual, hunt) → break  // 隐式全票通过
否则 → collectReviewFindings → fix-round
```

**缺失的能力**：当两个 reviewer 对同一文件给出矛盾判断时（如 Plan 05 T7 的时区分歧——quality 要 CST、hunter 要 naive UTC），orchestrator 不知道这是"有意义的 reviewer 分歧"还是"不同 reviewer 从不同角度指出了不同问题"。只能靠 `detectOscillation`（同一文件 ≥3 round 被碰）事后兜底，导致：

- **用户体验差**：halt 后需考古推断分歧点（`review_history` 只有 findings 摘要，无 reviewer 间矛盾记录）
- **浪费轮次**：3 轮才 halt，前 2 轮修复在矛盾方向上来回摇摆
- **知识流失**：分歧的根因（通常是 spec/plan 歧义）没有被消除，下次 run 可能重蹈

### 1.2 目标

三个维度一次性实现：

1. **防振荡** — 提前发现 reviewer 之间的持续分歧，在 2 轮内通过 opus 仲裁消解（非 halt）
2. **提升审查质量** — 检测到真正的 reviewer 矛盾时，自动追加 spec/plan 澄清以消除歧义
3. **可观测性增强** — `conflict_history[]` 显式记录 reviewer 分歧 + 仲裁结果，进 manifest

## 2. 设计概述

### 2.1 核心流程

```
review round N (spec ‖ quality ‖ hunter 并行)
    ↓
allGreen? → break (现有，不变)
    ↓ no
reviewHaltReason / reviewHaltForEmptyFailed? → halt (现有，不变)
    ↓ pass
┌─ 共识矩阵 (新) ──────────────────────────────────────────┐
│ 1. mapCategories(findings) — 类别映射                      │
│ 2. detectConflictsLoose(findings) — 文件×类别交叉检测       │
│ 3. strictResolution(conflict) — haiku 判定真伪分歧          │
│ 4. Round 1: 注入 fixIssues + 记录 conflict_history        │
│    Round 2 同一冲突: opus 仲裁 → 追加 spec/plan 澄清        │
│                      → commit → fix-round → re-review      │
│    Round 2 新冲突: 同 Round 1 处理                         │
└──────────────────────────────────────────────────────────┘
    ↓
detectOscillation? → halt (现有，不变，兜底)
    ↓
fix-round implementor (现有，fixIssues 含冲突摘要+仲裁建议)
```

### 2.2 关键决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 仲裁者角色 | 顾问（非绑定法官） | implementor 保持最终判断权；裁决失败时 `detectOscillation` 兜底 |
| 类别体系 | 映射表（非共享分类法/不映射） | 不改 review prompt（零回归风险），纯函数可测 |
| 冲突判定 | 两阶段（宽松匹配 → 严格判断） | 文件×类别交集小，haiku 成本可忽略 |
| 冲突记忆 | 带记忆 + 升级（非单轮快照/纯观测） | 填补 `detectOscillation` 的语义盲区，2 轮升级比 3 轮快 |
| 第 2 轮行为 | Opus 仲裁 + 更新 spec/plan（非 halt） | 降低人力介入成本；`detectOscillation` 在 Round 3 兜底保护 |

## 3. 类别映射表（mapCategories）

### 3.1 设计原则

三类 reviewer 保持各自原生类别（prompt 不改）。在 orchestrator 用纯函数映射为 7 个统一类别。仅映射"可能产生跨 reviewer 矛盾"的类别——无映射的类别不参与冲突检测（只参与现有的 `collectReviewFindings` 反馈管道）。

### 3.2 映射关系

```
        spec                     quality                    hunter
        ────                     ───────                    ──────
missing ──┐
extra   ──┤── correctness ───┤─ architecture ─────┤ (hunter 无直接对应)
misund. ─┘                   │                     │
                              ├─ error-handling ───┤─ swallowed-errors
                              │  (bare except,      │─ bad-fallbacks
                              │   swallowed exc,     │─ lost stack traces
                              │   mutable args,      │
                              │   value == None,     │
                              │   missing type hints)│
                              ├─ security ──────────┤─ swallowed-errors
                              │  (SQL injection,     │  (安全相关吞错)
                              │   command injection) │
                              ├─ resource-mgmt ─────┤─ missing-timeout
                              │  (missing with,      │  (network/file/db
                              │   N+1 queries)       │   无超时处理)
                              ├─ observability ─────┤─ log-and-forget
                              │  (print→logger)      │─ wrong-severity
                              ├─ concurrency ───────┤─ missing-await
                              │  (blocking in async) │─ fire-and-forget
                              │                      │
immutability ─────────────────┤ (quality only)       │
naming ───────────────────────┤ (quality only)       │
code-size ────────────────────┤ (quality only)       │
                              │                      │ transaction-safety
                              │                      │   (hunter only)
```

### 3.3 统一类别

| 统一类别 | 来源 reviewer | 冲突可能性 | 说明 |
|---------|-------------|-----------|------|
| `correctness` | spec(3) + quality(1) | 高 | spec 说"实现了"、quality 说"实现方式有问题" |
| `error-handling` | quality + hunter | 高 | quality 可能说"够了"、hunter 说"在吞错" |
| `security` | spec + quality + hunter | 中 | 安全相关跨 reviewer 交叉判断 |
| `architecture` | quality + spec | 中 | 架构越界/过度工程 vs spec 要求 |
| `resource-mgmt` | quality + hunter | 中 | 资源管理 vs 超时处理 |
| `observability` | quality + hunter | 低 | 日志规范 vs 日志无效 |
| `concurrency` | quality + hunter | 低 | 异步阻塞 vs 缺少 await |

以下类别只有一个 reviewer 覆盖，不参与冲突检测：

| 类别 | 覆盖者 |
|------|--------|
| `immutability` | quality only |
| `naming` | quality only |
| `code-size` | quality only |
| `transaction-safety` | hunter only |

### 3.4 函数签名

```javascript
// lib.js 纯函数
// 输入单个 finding，输出带有 unifiedCategory 字段
export function mapCategories(findings) {
  // 映射表:
  const MAPPING = {
    spec: {
      missing: 'correctness', extra: 'correctness', misunderstanding: 'correctness',
      'over-build': 'architecture',
      'missing-security': 'security',
    },
    quality: {
      'error-handling': 'error-handling',
      'bare-except': 'error-handling',
      'swallowed-exceptions': 'error-handling',
      'sql-injection': 'security',
      'command-injection': 'security',
      'missing-with': 'resource-mgmt',
      'n-plus-one': 'resource-mgmt',
      'print-instead-of-logger': 'observability',
      'blocking-in-async': 'concurrency',
      architecture: 'architecture',
      // 以下不映射（留空，不参与冲突检测）
      immutability: null, naming: null, 'code-size': null,
      'mutable-args': 'error-handling',
      'value-is-none': 'error-handling',
      'missing-type-hints': 'error-handling',
      'shadowing-builtins': null,
    },
    hunter: {
      'swallowed-errors': 'error-handling',
      'bad-fallbacks': 'error-handling',
      'lost-stack-traces': 'error-handling',
      'missing-timeout': 'resource-mgmt',
      'log-and-forget': 'observability',
      'wrong-severity-logs': 'observability',
      'missing-await': 'concurrency',
      'fire-and-forget': 'concurrency',
      'swallowed-errors-security': 'security',
      // 以下不映射
      'transaction-safety': null,
    },
  }

  return findings.map(f => ({
    ...f,
    unifiedCategory: (MAPPING[f.source] || {})[f.category] || null,
  }))
}
```

## 4. 冲突检测算法

### 4.1 第一阶段：宽松匹配（纯函数）

```javascript
// lib.js
// 按 (file, unifiedCategory) 分组 → 组中有 ≥2 个不同 source → 候选冲突
export function detectConflictsLoose(findings) {
  const groups = {}
  for (const f of findings) {
    if (!f.file || !f.unifiedCategory) continue
    const key = `${f.file}::${f.unifiedCategory}`
    if (!groups[key]) groups[key] = { file: f.file, unifiedCategory: f.unifiedCategory, sources: new Set(), findings: [] }
    groups[key].sources.add(f.source)
    groups[key].findings.push(f)
  }
  return Object.values(groups).filter(g => g.sources.size >= 2)
    .map(g => ({ ...g, sources: [...g.sources] }))
}
```

### 4.2 第二阶段：严格判断（haiku agent 调用）

haiku prompt（30-50 词）：

```
Compare two review findings on the same file+categories.
Finding A ({sourceA}): {titleA} — fix: {fixA}
Finding B ({sourceB}): {titleB} — fix: {fixB}

Do these findings represent genuinely opposed judgments or mutually exclusive fixes?
Return: "genuine_conflict" | "same_direction" | "unclear"
- genuine_conflict: the two reviewers want opposite things
- same_direction: they found the same problem from different angles
- unclear: cannot determine
```

返回三种结果：
- `genuine_conflict` — 真正相反判断或互斥修复方向
- `same_direction` — 从不同角度指出同一问题（不是分歧，不记录）
- `unclear` — 无法判定，保守视为 conflict

### 4.3 注入格式

冲突检测结果追加到现有 `formatFindings` 输出末尾，不改 prompt 模板：

```
[quality|critical] function over 50 lines should be split (app/services/fetch_service.py)
[hunter|important] swallowed ConnectionError in retry wrapper (app/services/fetch_service.py)

⚠ REVIEWER CONFLICT [error-handling] (app/services/fetch_service.py):
  quality: "error handling is over-engineered — remove the retry wrapper"
  hunter: "retry wrapper swallows ConnectionError — silent-failure risk"
  Arbitrator (haiku): genuinely opposed — quality wants simpler, hunter wants safer
  Recommendation: keep the retry but add explicit log+alert on ConnectionError rather than pass
```

## 5. 冲突升级与 opus 仲裁

### 5.1 升级触发

```javascript
// lib.js
// 三个条件同时满足 → 同一冲突再现：
// 1. file 相同
// 2. unifiedCategory 相同
// 3. sources 相同（同一对 reviewer 的对立）
export function detectConflictStalemate(conflicts, conflictHistory) {
  const repeatConflicts = []
  for (const c of conflicts) {
    const prev = conflictHistory.find(h =>
      h.file === c.file &&
      h.unifiedCategory === c.unifiedCategory &&
      h.sources.sort().join(',') === c.sources.sort().join(',')
    )
    if (prev) repeatConflicts.push({ current: c, previous: prev, rounds: [prev.round, 'current'] })
  }
  return repeatConflicts.length > 0 ? repeatConflicts : null
}
```

### 5.2 Arbitrator Agent

第 11 个 agent 角色（新增 `PROMPTS.arbitrator` + `SCHEMAS.arbitrator`）：

| 属性 | 值 |
|------|-----|
| **模型** | opus（裁决需要最强推理） |
| **触发** | 仅 round 2，同一冲突再现 |
| **可写** | spec.md（追加澄清）+ plan 文件（追加说明） |
| **不可写** | frontmatter YAML 块、其他 source 文件、lessons.md |
| **失败处理** | 限额/异常 → `log()` 警告 → 退回 fix-round（不 halt，`detectOscillation` 兜底） |

### 5.3 裁决类型

| verdict | 含义 | fix-round 行为 |
|---------|------|---------------|
| `spec wins` | spec reviewer 正确 | 严格按 spec 执行 |
| `quality wins` | quality reviewer 正确 | 按 quality 的 fix 建议执行 |
| `hunter wins` | hunter 正确 | 按 hunter 的 fix 建议执行 |
| `compromise` | 双方各有道理 | implementor 按折中方案执行 |
| `both_correct_different_contexts` | 取决于场景 | implementor 按场景选择 |
| `unclear` | 无法裁决 | 退回 fix-round（`detectOscillation` 兜底） |

### 5.4 写入规则

**Spec 更新**（只追加，不删不改）：

```
在 spec 末尾追加或追加到已有的 ## Clarification 子节：

### CL-<ts> <unifiedCategory>: <一句话裁决>
**背景**: <冲突简述 + 双方论据>
**裁决**: <选择哪一方 + 理由>
**影响**: <哪些 task 需要注意>
```

**Plan 更新**（只修改 task 正文，不碰 frontmatter）：

```
在当前 ## Task N 段落后、下一个 ## 之前追加：

> ⚠ Clarification (CL-<ts>): <裁决摘要>
```

### 5.5 编排

```
Round 2: 同一冲突再现
  ↓
dispatch arbitrator (opus)
  ↓ (verdict !== 'unclear')
commit spec/plan 更新
  chore(plan-XX): resolve reviewer conflict — <unifiedCategory>
  ↓
log() surface: "⚖ arbitrator resolved conflict [error-handling] (file.py): hunter wins — add explicit error handling"
  ↓
inject verdict into fixIssues
  ↓
fix-round implementor（带仲裁结果）
  ↓
new review round → allGreen 预期（歧义已消除）
  ↓ (若 Round 3 仍失败)
detectOscillation halt（兜底，与原有行为一致）
```

### 5.6 新增 halt reason

`conflict_stalemate` 作为 halt reason 存在但**极少触发**——仅当 arbitrator 返回 `unclear` 且 oscillation 也检测不到（理论上极罕见，保留作为安全网）。正常路径是 arbitrator 裁决成功 → 继续；arbitrator 裁决失败 → 回落 `detectOscillation`。

```javascript
// haltLikelySource 新增
'conflict_stalemate' → 'implementor changes'
```

## 6. 数据模型

### 6.1 perTask 新增字段

```javascript
state.perTask[taskKey] = {
  // ... 现有字段 ...
  conflict_history: [],  // 新增
  // conflict_history 元素:
  // {
  //   round: int,
  //   file: string,
  //   unifiedCategory: string,
  //   sources: [string, string],       // 冲突双方（如 ['spec','quality']）
  //   findings: [{source, title, fix}], // 双方论据
  //   strict_result: 'genuine_conflict' | 'same_direction' | 'unclear',
  //   arbitrator_verdict: string | null, // round 1 为 null（haiku 建议）; round 2 为 opus 裁决
  //   arbitrator_rationale: string | null,
  //   spec_updated: boolean,
  //   plan_updated: boolean,
  // }
}
```

### 6.2 manifest.json 新增

`conflict_history` 随 `per_task` 序列化进 `manifest.json`，与 `review_history` 并列。

## 7. 新增/修改清单

### 7.1 新增组件

| 组件 | 位置 | 类型 |
|------|------|------|
| `mapCategories(findings)` | lib.js（真源）+ run-plans.js（inline）| 纯函数 |
| `detectConflictsLoose(findings)` | lib.js + run-plans.js | 纯函数 |
| `detectConflictStalemate(conflicts, history)` | lib.js + run-plans.js | 纯函数 |
| `formatArbitratorInput(conflict, history, plan, specPath)` | lib.js + run-plans.js | 纯函数 |
| `PROMPTS.arbitrator` | lib.js + run-plans.js | prompt 模板 |
| `SCHEMAS.arbitrator` | lib.js + run-plans.js | JSON Schema |

### 7.2 修改文件

| 文件 | 改动说明 |
|------|---------|
| `run-plans.js` | `runTask` review 循环中插入共识矩阵阶段（~35 行新增，~80→~120 行） |
| `lib.js` | 新增 4 纯函数 + 1 PROMPT + 1 SCHEMA |
| `workflow-design.md` | §13 新增子节 "13i. Reviewer Consensus Matrix" |
| `sync.test.js` | 新增 inline 副本一致性断言 + 纯函数单元测试 |

### 7.3 不修改

- 三个 review agent prompt（零改动）
- implementor prompt 模板（`fixIssues` 占位符不变）
- `formatFindings` 函数（冲突摘要追加在输出末尾）
- 现有 halt 逻辑（只新增 `conflict_stalemate` 作为 halt reason，不删不改）
- `ensurePerTaskDefaults`（新增字段有默认值 `[]`）

## 8. 控制流集成

```
runTask review 循环伪代码：

for (let round = 1; ...; round++) {
  // ... 现有: runReviewRound + reviewHaltReason + reviewHaltForEmptyFailed + allGreen ...

  // === 共识矩阵 (新增) ===
  const mappedFindings = mapCategories(collectReviewFindings(spec, qual, hunt))
  const conflicts = detectConflictsLoose(mappedFindings)

  for (const c of conflicts) {
    const strict = await strictResolutionAgent(c)  // haiku
    if (strict !== 'genuine_conflict') continue

    // 检查是否为重复冲突
    const repeat = detectConflictStalemate([c], state.perTask[taskKey].conflict_history)
    if (repeat && round >= 2) {
      // 第 2 轮同一冲突 → opus 仲裁
      const arb = await dispatchImpl(
        buildPrompt('arbitrator', formatArbitratorInput(c, state.perTask[taskKey].conflict_history, plan, cfg.spec_path)),
        { schema: SCHEMAS.arbitrator, label: `arb:${task.id}`, model: 'opus' },
        'opus'
      )
      if (arb && arb.verdict !== 'unclear') {
        // 记录仲裁结果
        conflictRecord.arbitrator_verdict = arb.verdict
        conflictRecord.arbitrator_rationale = arb.rationale
        conflictRecord.spec_updated = arb.spec_updated
        conflictRecord.plan_updated = arb.plan_updated
        log(`⚖ arbitrator resolved [${c.unifiedCategory}] (${c.file}): ${arb.verdict}`)
      }
    }

    // 注入冲突摘要到 fixIssues
    fixIssuesExtra += formatConflictSummary(c, conflictRecord)
    state.perTask[taskKey].conflict_history.push(conflictRecord)
  }
  // ====================

  // ... 现有: detectOscillation + fix-round implementor ...
}
```

## 9. 测试策略

| 层级 | 测试 | 说明 |
|------|------|------|
| 单元测试 (lib.js) | `mapCategories` 覆盖所有映射 + null 路径 | `node --test` |
| 单元测试 (lib.js) | `detectConflictsLoose` 覆盖 0/1/多冲突 + 边界（同 source 多 finding/空 findings） | `node --test` |
| 单元测试 (lib.js) | `detectConflictStalemate` 覆盖重复/新冲突/空 history | `node --test` |
| 单元测试 (lib.js) | `formatArbitratorInput` 结构正确性 | `node --test` |
| 同步测试 (sync.test.js) | inline 副本一致性 + 新增函数/常量列表更新 | `node --test` |
| 集成测试 | Plan 05 重跑（验证回归——共识矩阵不应打断现有 green path） | 实际 workflow 运行 |
| 冲突路径测试 | 构造含已知冲突的 task（如 T7 时区分歧）→ 验证仲裁→fix→收敛 | 实际 workflow 运行 |

## 10. 风险与缓解

| 风险 | 严重度 | 缓解 |
|------|--------|------|
| 仲裁 agent 误改 spec | HIGH | 只追加不删改；`## Clarification` 子节与原文区分 |
| 仲裁 agent 破坏 plan frontmatter | HIGH | 不碰 `---` 块；只修改 task 正文描述 |
| 仲裁失败导致 stalled | MEDIUM | `verdict==='unclear'` → 退回 fix-round；`detectOscillation` 在 Round 3 兜底 |
| 冲突检测误报（false positive） | MEDIUM | 两阶段（宽松→严格）降低误报率；haiku 判定保守 |
| 仲裁+fix+re-review 链条过长 | LOW | 仅 Round 2 触发；冲突本身不频繁 |
| 用户不知情 spec/plan 被修改 | LOW | `log()` 立即打出显眼摘要 + 文件路径；git log 可追溯 |

## 11. 预期效果

| 前 | 后 |
|----|-----|
| Plan 05 T7: 3 轮 OSCILLATING halt + 考古推断分歧点 | Round 2: 仲裁 → spec 追加澄清 → fix → Round 3 全绿 |
| `review_history` 看不出 reviewer 间矛盾 | `conflict_history[]` 显式记录双方论据 + 仲裁结果 |
| 无 reviewer 间分歧可观测性 | manifest.json + `log()` 实时 surface |

## GSTACK REVIEW REPORT

**Runs**: CEO (Claude subagent) + Eng (Claude subagent) | **Status**: issues found | **Date**: 2026-07-05

### Findings

| # | Severity | Source | Area | Issue |
|---|----------|--------|------|-------|
| C1 | CRITICAL | CEO | Problem Frequency | 47 tasks completed, exactly 1 genuine reviewer conflict (Plan 05 T7). Occurrence rate ~2%. The other OSCILLATING halts were scope/planning issues, not reviewer disagreements. |
| C2 | CRITICAL | CEO | Maintenance | Hardcoded `MAPPING` table must be synchronized with 3 review prompts that change over time. Stale mappings = silently missed conflicts or false positives. |
| C3 | CRITICAL | Eng | Error Handling | `arb.halted` not checked before accessing `arb.verdict`. On quota error, dispatchImpl returns `{halted:true}` which has no `.verdict` → `undefined !== 'unclear'` is true → code records undefined verdict and proceeds as if arbitration succeeded. |
| C4 | CRITICAL | Eng | Runtime Safety | Arbitrator writes spec/plan files but there is NO commit mechanism within the review loop. The plan says "commit spec/plan" in §5.5 but Section 8 pseudo-code has no commit step between arbitrator and fix-round. The next task's commit agent would bundle these unrelated changes. |
| H1 | HIGH | CEO | Failure Mode | Arbitrator autonomously appends to spec documents. An opus agent misunderstanding the conflict and writing a wrong clarification is a single point of spec-corruption -- downstream tasks follow corrupted spec. "只追加不删改" prevents deletion but not hallucinated content. |
| H2 | HIGH | CEO | Alternatives | A simpler alternative exists and is not evaluated: enhance `blocked.md` generation to group findings by file and flag cross-reviewer patterns (~5-10 lines, zero new agents). Surface the observability gap without building the full matrix. |
| H3 | HIGH | Eng | Error Handling | haiku strictResolution returns null → treated as non-conflict (`continue`). Plan says `unclear` should be treated conservatively as conflict; null (agent failure) gets the OPPOSITE treatment. |
| H4 | HIGH | Eng | Pattern Violation | `strictResolutionAgent(c)` is a bare agent call, not using `dispatchImpl`. Violates the existing codebase pattern -- misses null-guard, quota-handling, and retryModel. |
| M1 | MEDIUM | CEO | Cost | No cost analysis. Opus arbitrator per stale conflict x 6+ remaining plans. A human spending 15 min reading enhanced `blocked.md` costs zero tokens. |
| M2 | MEDIUM | CEO | Complexity | 3-round arbitration path: round1 detect → round2 arbitrate+commit+fix → round3 re-review. Still 3 rounds before allGreen, same as current OSCILLATING halt. Net round savings not established. |
| M3 | MEDIUM | Eng | Undefined Symbol | `formatConflictSummary` referenced in §8 pseudo-code but never defined in §7.1 (new components list). |
| M4 | MEDIUM | Eng | Integration | No framing in fixIssues to help implementor distinguish "fix this specific code issue" from "advisory: reviewers disagree, here is a recommendation." |
| M5 | MEDIUM | Eng | Data Model | `conflict_history` not in `ensurePerTaskDefaults` despite plan claiming "no modification needed" in §7.3. Accessing before first write returns undefined, breaks `detectConflictStalemate`. |
| L1 | LOW | Eng | Accuracy | §2.1 flow chart implies matrix runs before `collectReviewFindings`; actually must run after it but before `formatFindings`. |
| L2 | LOW | Eng | Pseudo-code | `conflictRecord` used before initialization in §8 loop. |
| L3 | LOW | Eng | Naming | "Arbitrator (haiku)" in §4.3 example text is misleading -- Round 1 uses strictResolution (not arbitration). |
| L4 | LOW | Eng | Sync | PROMPTS key enumeration in sync.test needs `'arbitrator'` added. |
| L5 | LOW | CEO | Risk | Arbitrator writes may violate task `writeFilesScope` boundary, causing commit agent to reject as `out_of_scope`. |
| L6 | LOW | Eng | LikelySource | `conflict_stalemate` halt reason's `likelySource` should note potential arbitrator spec/plan edits, not just `'implementor changes'`. |

### VERDICT

**4 CRITICAL, 4 HIGH, 6 MEDIUM, 6 LOW findings across both reviews.**

The core strategic concern (C1) is that reviewer conflict is a ~2% occurrence event, and the plan builds substantial infrastructure (~40 tests, 5 functions, 1 agent type, mapping table maintenance burden) to automate resolution. The CEO reviewer recommends a phased approach: first enhance `blocked.md` surface-only (5-10 lines, zero new agents, zero ongoing maintenance), then collect data for 3 more plans, and only build arbitration if it remains the bottleneck.

The core engineering concerns (C3, C4) are fixable with existing patterns (`dispatchImpl` + null guard, dedicated commit agent dispatch), but the plan as written does not address them.

**Recommended path**: 
1. Defer the full consensus matrix
2. Implement the "surfacing" alternative first: enhance `blocked.md` to group findings by file with `⚠ CROSS-REVIEWER` flags when two reviewers touch the same file with different stances
3. Track for 3 plans whether enhanced blocked.md is sufficient
4. If OSCILLATING halts remain frequent and are genuinely reviewer-conflict-driven, revisit arbitration with actual frequency data

**NO UNRESOLVED DECISIONS**
