# Cross-Reviewer Pattern Surfacing 设计

> **日期**: 2026-07-05 | **版本**: 2.0（简化版，替代 v1.0 完整共识矩阵） | **类型**: workflow orchestrator 增强
> **前置**: Plan 01–05 已完成（318 tests green），run-plans.js 稳定运行
> **背景**: v1.0 完整共识矩阵（映射表+haiku判定+opus仲裁+自动改spec）经 CEO+Eng 双审查后否决——问题频率 ~2%（47 task 仅 1 次真正的 reviewer 冲突），投入产出比不对。本 v2.0 改做 surface-only：仅增强可观测性，不自动化裁决。
> **审查**: GSTACK REVIEW REPORT 见文末（4 CRITICAL / 4 HIGH / 6 MEDIUM / 6 LOW → 推荐简化）

## 1. 问题与动机

### 1.1 现状

当前 review 循环的共识模型是隐式的：

```
allGreen(spec, qual, hunt) → break  // 隐式全票通过
否则 → collectReviewFindings → fix-round
```

**缺失的能力**: 当两个 reviewer 对同一文件都报了 finding 时（无论方向相同还是相反），`blocked.md` 和 `formatFindings` 输出中看不出"两个 reviewer 在关注同一个文件"。导致:

- **halt 后需考古推断**: `review_history` 只存 findings 摘要，不标文件归属，用户无法快速看出 "quality 和 hunter 同时盯上了 `fetch_service.py` 但可能给出不同建议"
- **implementor 缺上下文**: fix-round implementor 收到平铺的 findings 列表，不知道 `[quality]` 和 `[hunter]` 两条都指向同一个文件——可能修了 A 看不懂 B 的关联

**频率数据**: 47 个已完成 task 中，真正的 reviewer 方向性矛盾仅 Plan 05 T7 一次（~2%）。但跨 reviewer 同文件重叠 更常见——几乎每个 failed review round 都有至少一个文件被 ≥2 个 reviewer 同时标记。

### 1.2 目标

**仅增强可观测性，零自动化裁决**:

1. **fixIssues 注入**: 在 `formatFindings` 输出末尾追加跨 reviewer 文件重叠提示——implementor 能看到 "quality 和 hunter 都对 `a.py` 有 finding"，自主判断
2. **blocked.md 增强**: halt 时 blocked.md 按文件分组 findings，标记哪些文件被 ≥2 个 reviewer 同时标记
3. **零新 agent**: 不添加 arbitrator/strictResolution 角色，不改 review prompt，不建映射表

## 2. 设计

### 2.1 核心流程

```
review round N (spec ‖ quality ‖ hunter 并行)
    ↓
allGreen? → break (现有，不变)
    ↓ no
reviewHaltReason / reviewHaltForEmptyFailed? → halt (现有，不变)
    ↓ pass
┌─ 跨 reviewer 分组标记 (新，纯函数，~15 行) ─────────────┐
│ 1. groupFindingsByFile(findings) — 按 file 分组            │
│ 2. 组内有 ≥2 个不同 source → 追加 ⚠ CROSS-REVIEWER 标记  │
│ 3. 追加到 fixIssues 末尾（不改 formatFindings 本身）       │
└──────────────────────────────────────────────────────┘
    ↓
detectOscillation? → halt (现有，不变)
    ↓
fix-round implementor (现有，fixIssues 含跨 reviewer 标记)
```

### 2.2 关键决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 自动化程度 | 仅 surface，零自动化裁决 | 频率 ~2%，不值得建全套基础设施 |
| 检测粒度 | 文件级（不改 prompt，不加映射表） | 零维护成本；不同 reviewer 的类别体系本就不同，硬映射会漂移 |
| 后续演进 | 积累 3 个 plan 数据后重评估 | 有真实频率数据再决定是否需要仲裁 |
| blocked.md 增强 | 按文件分组 findings + `⚠ CROSS-REVIEWER` | 用户 2 分钟内判断真分歧 vs 不同角度 |

## 3. 实现

### 3.1 新增纯函数（lib.js）

```javascript
// 跨 reviewer 文件重叠检测：按 file 分组 findings → 组中有 ≥2 个不同 source → 标记。
// 纯函数，不依赖任何映射表或 agent 调用。spec §2.1。
export function groupFindingsByFile(findings) {
  const groups = {}
  for (const f of findings) {
    if (!f.file) continue
    if (!groups[f.file]) groups[f.file] = { file: f.file, sources: new Set(), findings: [] }
    groups[f.file].sources.add(f.source)
    groups[f.file].findings.push(f)
  }
  return Object.values(groups)
}

// 格式化跨 reviewer 文件重叠为 implementor 可读的注入文本。
// 仅当某文件有 ≥2 个不同 reviewer 标记时才输出该段。spec §4.3（v2.0 简化版）。
export function formatCrossReviewerNote(findings) {
  const groups = groupFindingsByFile(findings).filter(g => g.sources.size >= 2)
  if (groups.length === 0) return ''

  let out = '\n## ⚠ Cross-Reviewer Overlap (≥2 reviewers flagged same file — check for conflicts)\n'
  for (const g of groups) {
    const srcs = [...g.sources].join('/')
    out += `\n### ${g.file} (flagged by: ${srcs})\n`
    for (const f of g.findings) {
      out += `- [${f.source}${f.severity ? '|' + f.severity : ''}] ${f.title}${f.fix ? ' — fix: ' + f.fix : ''}\n`
    }
  }
  return out
}
```

### 3.2 fixIssues 注入（run-plans.js）

在 review 循环中，`formatFindings(findings)` 之后、`dispatchImpl` 之前追加:

```javascript
const findings = collectReviewFindings(spec, qual, hunt)
const fixIssues = formatFindings(findings) + formatCrossReviewerNote(findings)
// ... 现有的 detectOscillation + fix-round ...
impl = await dispatchImpl(buildPrompt('implementor', implCtx(fixIssues, `修复 review round ${round}...`)), ...)
```

### 3.3 blocked.md 增强（finalReport prompt 修改）

在 `finalReport` prompt 的 Step 4（写 blocked.md 的 Working Tree 段）中，追加跨 reviewer 分组提示:

```
If halt was due to failed review round (not model_unavailable/agent_error/gate): 
also include a "## Cross-Reviewer Findings (per file)" section grouping the halted 
task's blocked_info diagnostics by file, highlighting files where ≥2 reviewers 
reported findings. This helps the user spot reviewer disagreements at a glance.
```

修改范围: lib.js `PROMPTS.finalReport` + run-plans.js inline 副本。

### 3.4 不做什么

- 不加映射表（`mapCategories`）— reviewer 类别体系本身会演进，硬编码映射是维护负担
- 不加 haiku strictResolution agent — 不判定 "真分歧 vs 伪分歧"，让 implementor 和人判断
- 不加 opus arbitrator agent — 不自动改 spec/plan
- 不加 `conflict_history[]` 数据模型 — 不追踪跨轮次冲突
- 不改 halt 逻辑 — 不新增 halt reason
- 不改 review agent prompt

## 4. 修改清单

| 文件 | 改动 | 新增行 |
|------|------|--------|
| `lib.js` | 新增 `groupFindingsByFile` + `formatCrossReviewerNote`（真源） | ~40 |
| `run-plans.js` | inline 副本 + review 循环中追加 `formatCrossReviewerNote(findings)` | ~45 |
| `finalReport` prompt（lib.js + run-plans.js 两处） | Step 4 追加 cross-reviewer 分组段落 | ~15 |
| `helpers.test.js` | 新增 ~10 个纯函数测试 | ~60 |
| `sync.test.js` | 函数存在性 + 字节一致性 + finalReport prompt 新段落断言 | ~20 |

**不修改**: review prompt、implementor prompt、halt 逻辑、ensurePerTaskDefaults、SCHEMAS（不改，不新增）。

## 5. 测试策略

| 层级 | 测试 | 说明 |
|------|------|------|
| 单元 (lib.js) | `groupFindingsByFile` 覆盖 0/1/多组 + 同 source 多 finding + 无 file 的 finding | `node --test` |
| 单元 (lib.js) | `formatCrossReviewerNote` 覆盖 0/1/多重叠 + 输出格式 | `node --test` |
| 同步 (sync.test.js) | inline 副本一致性 + finalReport prompt 含新段落 | `node --test` |
| 回归 | Plan 05 重跑（验证 green path 不被打断） | workflow 运行 |

## 6. 预期效果

| 前 | 后 |
|----|-----|
| halt 后 `blocked.md` 只看得到平铺的 findings 列表，看不出 reviewer 间文件重叠 | `blocked.md` 按文件分组，`⚠ CROSS-REVIEWER` 一眼看出谁和谁都在盯同一文件 |
| fix-round implementor 收 findings 列表，不知道哪些 file 被多个 reviewer 关注 | fixIssues 末尾追加文件分组段落，implementor 能看到跨 reviewer 重叠 |
| 无跨 reviewer 可观测性 | `formatCrossReviewerNote` 纯函数，无 agent 成本，零维护负担 |

## GSTACK REVIEW REPORT

**Runs**: CEO (Claude subagent) + Eng (Claude subagent) | **Status**: spec revised per review verdict | **Date**: 2026-07-05

### Findings from v1.0 Review (deferred — v2.0 simplified)

| # | Severity | Source | Issue | v2.0 Resolution |
|---|----------|--------|-------|-----------------|
| C1 | CRITICAL | CEO | Problem frequency ~2% | ✅ Simplified to surface-only — no arbitration infrastructure |
| C2 | CRITICAL | CEO | Mapping table maintenance burden | ✅ Removed — no mapping table needed for file-level grouping |
| C3 | CRITICAL | Eng | `arb.halted` not checked | ✅ Removed — no arbitrator agent |
| C4 | CRITICAL | Eng | No commit mechanism for arbitrator writes | ✅ Removed — no file writes by new agents |
| H1 | HIGH | CEO | Arbitrator spec-corruption risk | ✅ Removed — no arbitrator |
| H2 | HIGH | CEO | Simpler alternative not evaluated | ✅ This IS the simpler alternative |
| H3 | HIGH | Eng | haiku null treated as non-conflict | ✅ Removed — no strictResolution agent |
| H4 | HIGH | Eng | Agent call pattern violation | ✅ Removed — no new agent calls in review loop |

### VERDICT

v1.0 完整共识矩阵（映射表+haiku判定+opus仲裁）的 4 CRITICAL / 4 HIGH 问题在 v2.0 简化版中**全部消除**。v2.0 仅新增 2 个纯函数（~40 行），零新 agent，零映射表维护负担——解决核心痛点（可观测性）而不引入复杂度。

**NO UNRESOLVED DECISIONS**
