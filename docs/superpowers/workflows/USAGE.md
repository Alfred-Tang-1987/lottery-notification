# Workflow Orchestrator 使用指南

> 工具：`.claude/workflows/run-plans.js`（Claude Code `Workflow` 工具上的 JS orchestrator）
> 设计依据：`docs/superpowers/workflow-design.md`（§1-13 + §2.4）

## 1. 这是什么

`run-plans` 是一个**自动执行 implementation plan 的编排器**。给它一份或多份 plan，它会：

- **每 task**：派 implementor subagent（TDD RED→GREEN→REFACTOR）→ review chain 并行（spec 逐行比对 ‖ quality 架构 ‖ silent-failure-hunter）+ **cross-reviewer 文件重叠标记（`⚠ CROSS-REVIEWER`，≥2 reviewer 标记同文件时注入 fixIssues）** → git commit → 精简 simplify（git status 触发 re-review，全绿 amend / 失败 checkout 回退）
- **plan 级**：独立 gate（在 committed SHA 上重跑 test + lint_command + extra_lint_commands，任一非 0 halt，不信 implementor 自报）
- **全流程**：多 plan 串行、振荡检测、BLOCKED 升级链、限额容错（halt，恢复后用全新跑续跑，见 §7.1）

它把"用 subagent-driven-development 手动跑 plan"自动化了——你就是用它替代手动 dispatch + review。

## 2. 前置条件

| 条件 | 说明 |
|---|---|
| `workflow.config.json`（项目根） | test/build/lint 命令 + spec_path + language + 可选 extra_lint_commands / reference_paths。bootstrap 会自动发现 |
| plan 文件（`docs/superpowers/plans/*.md`） | 无 YAML frontmatter 也行——bootstrap 自动生成（含 modelHint） |
| 业务代码可测 | `test_command` 要能跑（否则 implementor TDD 失败）。项目未初始化时 bootstrap 容忍（标 'project not yet initialized'，status 仍 ok） |

`workflow.config.json` 示例（本项目）：
```json
{
  "test_command": "uv run pytest",
  "full_test_command": "uv run pytest -v",
  "build_command": "uv build",
  "lint_command": "uv run ruff check .",
  "extra_lint_commands": ["uv run lint-imports"],
  "spec_path": "docs/superpowers/specs/2026-06-16-lottery-notification-design.md",
  "reference_paths": ["docs/reference/lottery-rules.md"],
  "language": "python",
  "silent_failure_intro": "<项目特定静默失败风险标题，可选>",
  "silent_failure_context": ["<项目特定静默失败纪律条目，见下>"],
  "review_max_rounds": 4,
  "lessons_auto_distill": true
}
```

### config 字段说明

| 字段 | 必填 | 作用 |
|---|---|---|
| `test_command` | 是 | implementor/commit 跑的单测命令 |
| `full_test_command` | 是 | plan gate 跑的全量测试命令 |
| `build_command` | 否 | 构建（**implementor GREEN 前跑验证可构建性**，第 6 轮 P1-11；空串/未配 → 跳过构建验证） |
| `lint_command` | 否 | 通用 lint（gate 会跑，非 0 即 halt） |
| `extra_lint_commands` | 否 | **架构/专项 lint 数组**（gate 依次跑）。承载项目架构纪律——如本项目的 `uv run lint-imports`（domain 层零 IO 纯度护栏）。不配即不约束 |
| `spec_path` | 是 | 设计 spec（specReview 逐条对照） |
| `reference_paths` | 否 | **额外权威文档数组**（implementor/specReview 对照）。承载 spec 之外的硬规则——如本项目的彩种规则参考。不配即该 prompt 段消失 |
| `language` | 否 | `python` / `general`（可扩展 ts/go…）。决定 qualityReviewer 的语言专项清单。未知值 → 通用清单 |
| `silent_failure_intro` | 否 | **项目特定静默失败风险标题**（hunter 注入段落的 `##` 标题）。不配 → 默认标题 `Project-Specific Silent-Failure Risks (HIGHEST PRIORITY — hunt these first)` |
| `silent_failure_context` | 否 | **项目特定静默失败纪律数组**（hunter 优先核查）。承载本项目反复踩的领域致命点——如 DB split-commit / savepoint 隔离 / 批量循环兜底 / 更正重置终态 / datetime 时区对齐。hunter 先查这些再查通用 `except:pass` 模式。不配即 hunter 退化为通用清单 |
| `review_max_rounds` | 否 | **review 最大轮数**（默认 4）。正整数 N → N 轮仍有 ❌ 则 halt；`0`/负数 → 无限模式（永不因轮数 halt，仅靠 `detectOscillation` 同文件 ≥3 round halt）。**最后 1 轮 fix 强制 opus**：有限模式 `round===maxRounds-1`、无限模式 `round>=4`。未配/null/非数字 → 默认 4。详见设计文档 §5.3 |
| `lessons_auto_distill` | 否 | **halt 时自动提炼 lesson**（默认 true）。halt 时调 `lessonDistiller` agent（opus）从 halt 根因中提炼可复用知识，过滤瞬态事件（review_empty/model_unavailable），语义去重对比现有 lessons 后 append/update/skip。distiller 失败/限额 → best-effort 跳过，不阻塞 manifest。显式 `false` 关闭（旧行为）。详见设计文档 §5.4 |

> **通用性原则**：项目特有内容（彩种规则、domain 纯度纪律）只走 config，不写进 prompt。换一个非彩票 Python 项目，改几个路径即可复用；换 TS 项目加 `language: "typescript"` 清单即可。新字段全部可选——旧 config 无它们照跑（条件渲染：orchestrator 传空串，相关 prompt 段消失）。

## 3. 怎么触发

在 Claude Code 会话里**指示 Claude 调 Workflow 工具**（你不能直接调，是 Claude 的工具）。两种方式：

**方式 A：named workflow**（推荐，系统已注册 `.claude/workflows/run-plans.js`）
```
让 Claude：用 run-plans workflow 跑 Plan 01
```
Claude 会调 `Workflow({ name: 'run-plans', args: {...} })`。

**方式 B：scriptPath 显式**
```
Workflow({
  scriptPath: '.claude/workflows/run-plans.js',
  args: {
    configPath: 'workflow.config.json',
    plansDir: 'docs/superpowers/plans',
    plan: '01',
    tasks: ['T1']
  }
})
```

## 4. 参数（args）

> **WAI1 注意**：Claude Code Workflow runtime 在某些版本下会把 `args` 序列化为 JSON 字符串注入（issues #72248 / #68969 / #73899）。`run-plans.js` 入口已做 `JSON.parse` 防御，所以你可按正常对象传参；若仍遇到 `args.configPath must be a non-empty string` 报错，请确认调用了 `Workflow({ ..., args: { ... } })`（args 为对象而非字符串）。

| 参数 | 必填 | 说明 |
|---|---|---|
| `configPath` | **是** | workflow.config.json 路径（第 7 轮 P2-7 起必填，缺失/空串 → fail-fast throw） |
| `plansDir` | **是** | plan 目录路径（第 7 轮 P2-7 起必填，缺失/空串 → fail-fast throw） |
| `plan` | 否 | 限定单 plan。传 **seq**（如 `'01'`）或 **id**（如 `'plan-01'`）。不传 → 跑所有 plan |
| `tasks` | 否 | 限定 task 子集（如 `['T1']`）。不传 → 跑该 plan 所有叶子 task |
| `completed` | 否 | 手动覆盖已完成 task id 列表（如 `['plan-01/T1']`）。**最高优先级**——runtime completed 提取三层 fallback：`args.completed` > `extractCompletedFromSubjects(git_log_subjects)`（正则，feat\|fix\|refactor）> `boot.evidence.completed`（LLM 提取，不稳定）。bootstrap agent (kimi-k2.7) 偶漏识别已 commit task（如 plan-06/T6d：feat `b08e3e7` + fix `ac28750` 都在，bootstrap evidence.completed 却漏 T6d → 已完成 task 误判 pending 重跑）。应急：`git log --oneline \| grep -oE '(feat\|fix\|refactor)\(plan-[0-9]+/T[0-9]+[a-z]?\)'` 提取完整列表传 args.completed 强制 skip。正常无需传（git_log_subjects 正则提取已确定性，2026-07-05 起 Defaults 到此机制） |

## 5. 运行流程（自动）

```
get-ts（取时间戳）
  → bootstrap：读 config + 生成 frontmatter(modelHint) + git log 返回 git_log_subjects（原始 subjects）+ completed（LLM 提取，fallback）→ runtime 三层处理：
      (a) extractCompletedFromSubjects 正则提取确定性 completed（feat|fix|refactor，不依赖 LLM）
      (b) bareTaskId strip plan-XX/ 前缀（防 task_id 双重前缀 feat(plan-06/plan-06/T1)）
      (c) dropParentTasks 过滤非叶子父 task（T6+T6b 共存 → drop T6，防 implementor 跑说明段）
    详见设计文档 §13k
    （sonnet 执行，失败自动升级 opus 重试——retryModel 机制，见 §7.2）
    → for plan（args.plan 过滤）:
        for task（叶子优先，args.tasks 过滤，已完成跳过）:
          runTask:
            implementor(task.model || sonnet)
              → blocked → 升级 opus → 仍 blocked → halt
              → needs_context → contextFetcher → 重试
              → failed → 重试一次 → halt
              → done_with_concerns → 记疑虑，继续进 review（不 halt）
            review rounds (max N，默认 4，可配 0=无限): spec ‖ quality ‖ hunter 并行
              → 全绿 break / 任一❌→ implementor 修复 → 下轮 / maxN → halt（0=无限仅靠振荡检测）
              → **cross-reviewer 标记**：≥2 reviewer 标记同文件时，fixIssues 末尾追加文件分组 + `⚠ CROSS-REVIEWER` 标记
              → 最后 1 轮 fix 强制 opus（有限 round===maxRounds-1 / 无限 round>=4）
              → 任一 review 空响应/异常（agent_error|model_unavailable|review_empty）→ halt
              → 任一 review failed 但 0 findings（review_failed_no_findings）→ halt
              → 振荡检测（同文件≥3 round）
            commit → git commit feat(plan-X/T-Y)（方案 C：commit 提前到 simplify 前）
            simplify (max 1) → git status --porcelain subagent 独立验证是否动代码（staged+unstaged，不信任自报）
              → 有改动 → re-review（spec‖qual‖hunt，runReviewRound helper）
                → 全绿 → git commit --amend + git rev-parse HEAD（正则校验 40 位 hex）
                → 失败 → git checkout -- . && git clean -fd（回退 tracked + untracked 改动，HEAD 不变）
              → 无改动 → 跳过 review（省成本）
              → 三个 subagent（diff/amend/checkout）均 safeAgent 包装 + 返回值验证 + 失败 halt
        plan gate: committed SHA 上依次重跑 test + lint_command + extra_lint_commands（任一非 0 halt）
    → finalize: 写 manifest
```

## 6. 看进度

`/workflows` —— 实时面板，看 phase（Bootstrap/Plan/Finalize）、agent label、**当前工具调用**（implementor 跑 `uv sync` 时能看到）。

每个 task 开头会 log：
```
▶ T1 (sonnet): 派发 implementor — TDD 可能含长命令(uv sync/build/全量测试)，正常耗时请等待
```
**看到长命令（uv sync/build）是正常的**，别误中断（首次装依赖可能 10-20 分钟）。

## 7. 限额 / 中断处理

### 限额耗尽（opus/sonnet 额度用完）
- workflow **halt**（不降级继续开发——避免弱 model 污染进度）
- 用 fallback 链 `[opus, sonnet, haiku, 环境默认]` **逐一尝试保存 manifest**（至少 haiku/默认能存）
- surface：「X model 限额耗尽，进度已保存，额度恢复后用全新跑续跑」
- **额度恢复后续跑**：见 §7.1（用**全新跑**，不是 resumeFromRunId）。

### 手动中断 / 崩溃
同样见 §7.1——用全新跑续跑。崩在 implementor 后/commit 前的半成品，全新跑会重跑该 task 覆盖。

> **⚠️ dirty_tree 自愈警告（第 7 轮）**：若上次 run 崩在 implementor 完成但 commit 未执行，工作树会残留半成品。bootstrap agent 检测到 `dirty_tree=true` 时会**自动执行 `git reset --hard HEAD`** 清理（§6.2 半提交自愈），**会静默丢弃工作树中所有未提交改动**——包括**非工作流的用户改动**（如手动编辑未 commit 的文件）。续跑前请确认工作树无重要未提交内容，或用 `git stash` 暂存。

## 7.1 续跑：用「全新跑」，不要用 resumeFromRunId（重要）

**进度以 git 为单一事实源**——**全新跑**时 bootstrap 从 git log 解析已完成的
task（`feat(plan-X/T-Y)` convention），已 commit 的 task 一律跳过。所以**跨 session、跨机器、
限额恢复后、review halt 后手动修完继续**——全部用「全新跑」：

```
Workflow({ scriptPath: '.claude/workflows/run-plans.js', args: { configPath: 'workflow.config.json', plansDir: 'docs/superpowers/plans', plan: '03' } })   # 从未完成 task 继续
Workflow({ scriptPath: '.claude/workflows/run-plans.js', args: { configPath: 'workflow.config.json', plansDir: 'docs/superpowers/plans' } })               # 所有 plan，跳过已 commit
```

全新跑每次重新 bootstrap：重新读 config、重新解析 git log、重新生成 frontmatter。已 commit 的
task 被识别为 completed 直接跳过，从第一个未 commit 的 task 接着跑。**不依赖 runId、不依赖 manifest**。

### ⚠️ 为什么不要用 `resumeFromRunId` 续跑业务 plan

`resumeFromRunId` 是 Workflow runtime 的**缓存回放**机制——它把上次 run 里**已完成的 agent 调用**
按 `(prompt, opts)` 原样返回缓存结果，**第一个未命中缓存的 agent 起才真正重跑**。这对

## 7.2 retryModel 机制：模型能力不足自动升级

**场景**：`agent()` 返回 `null` 不一定是 quota 耗尽——也可能是**模型能力不足**（如 `qwen3.7-plus` 跑复杂 bootstrap 被 router 以 "Repetitive tool calls" 400 中断，runtime 吞为 null）。旧逻辑一律视作 `model_unavailable` halt，导致弱 model 永远无法完成任务。

**机制**：`dispatchImpl(prompt, opts, model, retryModel = null)` 新增第 4 参数：
- `agent()` 返回 `null` 时，若 `retryModel` 非空且 ≠ `model`，用 `retryModel` **重试一次**
- 重试仍 `null` 或抛 quota 错误 → halt（不再无限重试）
- 不重试 quota 错误（第一层 `catch` 已 halt，不浪费更强模型的额度）

**当前使用**：
- bootstrap 调用：`dispatchImpl(..., 'sonnet', 'opus')`——sonnet 跑 bootstrap 失败时自动升级 opus
- 其他 agent 调用（implementor/review/commit/gate）：暂不启用 retryModel，保留旧行为

**测试**：`docs/superpowers/workflows/tests/dispatchImpl-retry.test.js` 覆盖 8 个场景（null 无 retry / null 有 retry 成功 / retry 也 null / retry == model 跳过 / quota 错误各路径）。

**日志**：重试时 `log()` 打 `⚠ label: model returned null (capability failure likely), retry with retryModel`，便于定位。

run-plans.js 这种 git-log 驱动的编排器是**错配**，会踩两个坑：

1. **看不到 workflow 外的提交**。halt 后你（或 Claude 主循环）手动修完一个 task 并 commit 了
   `feat(plan-03/T3)`。resume 会**回放缓存的 bootstrap agent**——它的 `evidence.completed` 是
   **halt 当时的快照**，不含你新提交的 T3。于是它仍判定 T3「未完成/被 block」，直接回放旧的
   halt 结果，**瞬间 halt、0 token、0 agent**，没有任何推进。bootstrap 的 git-log 重解析逻辑
   在 resume 路径下被缓存跳过了。

2. **resume 不传 `args` 会直接 crash**。脚本体访问 `args.configPath`（bootstrapprompt 构造处）。
   resume 调用若省略 `args`，`args` 为 `undefined`，`if (!args) throw` 抛
   `Error: args must be a non-null object (Workflow runtime contract)`，workflow 立刻失败。
   要 resume 必须带上 `args: { configPath, plansDir }`——但即便带上了，坑 1 仍在。

**resume 唯一真正合适的场景**：在**同一个 session 内**、**没有任何 workflow 外改动**的前提下，
限额 halt 后立刻恢复额度、想省掉重跑已完成 agent 的 token。即便如此，全新跑也能正确续跑，只是
重新跑一遍 bootstrap/get-ts（成本可忽略）。**结论：续跑一律用全新跑。**

### 手动修完 review-halt 的 task 后继续（推荐流程）

review 链 max-rounds halt 后，task 留在「未 commit」状态（implementor 的改动在工作树/未提交）。
推荐流程：

1. 看 `runs/<ts>/manifest.json` 的 `per_task.<T>.blocked_info`（`reason: review max rounds` +
   spec/qual/hunt 的 issues）定位阻塞缺陷；或直接看 `runs/<ts>/blocked.md`——含 `likely_source`
   + Working Tree 段（git status 真实输出 + 接手指引），一眼看清哪些文件被改、是否脏。
2. 手动修代码（主循环 Claude 或你）→ 跑 test + lint 确认绿 → `git commit -m "feat(plan-X/T-Y): ..."`
   （遵守 convention）。
3. （可选）派 spec-review / quality-review subagent 复核该 commit。
4. **全新跑**续跑：`Workflow({ scriptPath, args: { configPath: 'workflow.config.json', plansDir: 'docs/superpowers/plans', plan: '<seq>' } })`——bootstrap 读 git log
   见该 task 已 commit，跳过，从下一个未完成 task 继续。

> 该流程正是本项目 Plan-03/T3 实际走过的路径：review halt → 手动修 split-commit 静默失败 +
   stale-numbers bless → commit → spec/quality subagent 复核 → 全新跑从 T4 继续。

## 8. manifest 输出

`runs/<run-ts>/manifest.json`（finalReport 唯一写盘）：
```json
{
  "run_ts": "...",
  "mode": "done" | "halted",
  "plans": [...],
  "per_task": {
    "T1": {
      "status": "committed" | "blocked" | "in_progress",
      "model": "sonnet",
      "review_rounds": 1,
      "files_touched_per_round": [...],
      "review_history": [{ "round": 1, "spec": { "status": "ok", "findings": [] }, "quality": { "status": "failed", "findings": [{ "title": "...", "severity": "high" }] }, "hunter": { "status": "ok", "findings": [] } }],
      "commit_sha": "abc1234",
      "simplify_reverted": false,
      "simplify_review_findings": [],
      "destructive_review_failed": false,
      "destructive_review_findings": [],
      "concerns": [],
      "blocked_info": { "reason": "...", "quota_exhausted": false, "last_error": "...", "suggested_fix": "...", "likely_source": "implementor changes | gate restored | bootstrap frontmatter | unknown" }
    }
  },
  "result": "done" | "halted"
}
```
崩溃/halt 后看它定位问题（非翻 transcript）。

`runs/<run-ts>/blocked.md`（仅 mode=halted 时 finalReport 写）：人读摘要。finalReport 收到独立 `blockedInfo` 占位符（halted task 的 `blocked_info` JSON，无需从整个 state 里捞字段），渲染 each field：plan / task / reason / category / last_error / suggested_fix / quota_exhausted / **likely_source**（工作树脏状态来源语义：`implementor changes` / `gate restored` / `bootstrap frontmatter` / `unknown`）。再加 **Working Tree** 段——finalReport halt 时跑 `git status --porcelain` + `git diff --stat` 的 ground truth 输出（dirty 时附文件列表 + diff stat + 接手指引；clean 时标注，如 gate halt 已 restore HEAD）。若 halt 源自 review round 失败，还追加 **Cross-Reviewer Findings** 段——按文件分组 findings + `⚠ CROSS-REVIEWER` 标记突出 ≥2 reviewer 同时标记的文件，一眼看出 reviewer 分歧点。`likely_source` 是基于 reason 的确定性映射（非 dirty 推断），与 git status ground truth 并存。git 探查 best-effort，失败不阻塞 manifest 写入。

## 9. 常见场景

| 场景 | 触发 |
|---|---|
| 跑单 plan 全 task | `Workflow({ scriptPath, args: { configPath, plansDir, plan: '01' } })` |
| 跑单 task 验证闭环 | `Workflow({ scriptPath, args: { configPath, plansDir, plan: '01', tasks: ['T1'] } })` |
| 跑所有 plan | `Workflow({ scriptPath, args: { configPath, plansDir } })` |
| **续跑（halt/限额/断 session/手动修完）** | **全新跑**——`Workflow({ scriptPath, args: { configPath, plansDir, plan: '<seq>' } })`（详见 §7.1，不要用 resumeFromRunId） |
| 限额恢复后续跑 | 同上，全新跑（先看 manifest 的 `quota_exhausted` 确认是限额而非别的 halt 原因） |

## 10. task modelHint（用哪个 model）

plan frontmatter 的 `model` 字段决定 implementor 用 sonnet 还是 opus：
- bootstrap **自动标**：标题含「安全/加密/认证/JWT/CSRF/Fernet/算法/比对/策略/边界/集成/接口」→ `opus`，其余 → `sonnet`
- review 固定用 opus（spec/quality）、sonnet（hunter）
- 手动改 plan frontmatter 的 model 可覆盖自动标注

> 环境若强制单一 model（如本机实测 implementor 跑 kimi-k2.7-code），modelHint 可能被环境默认覆盖——不影响功能，仅 model 选择层面。

## 11. 注意事项

- **首次跑会修改所有 plan 文件**（加 frontmatter，幂等，已加的不重写）
- **业务代码必须可测**——`test_command` 跑不通时 implementor/gate 会失败（项目未初始化时 bootstrap 容忍，但 implementor 跑测试需要 pyproject.toml 等就绪）
- **commit convention**：`feat(plan-X/T-Y): <title>`——**全新跑**靠 git log 识别已完成 task 并跳过（这是续跑的单一体机制，见 §7.1）
- **不降级继续**：限额 halt 后不会用弱 model 继续开发，只保存进度等恢复（§2.4 核心原则）
- **错误恢复**：bootstrap/get-ts/implementor/review/simplify/commit/gate/finalReport **所有** agent 路径都有 quota 捕获 + 兜底，不会裸 crash 丢进度

## 12. 调试

| 现象 | 排查 |
|---|---|
| workflow paused | `/workflows` 看卡在哪个 agent；读 `runs/<ts>/manifest.json` 或 transcript |
| implementor 反复失败 | 看 `blocked_info.last_error` + `suggested_fix`；可能 plan 顺序错（依赖前序 task） |
| review 振荡 halt | `blocked_info.reason: OSCILLATING`——reviewer 持续分歧，同文件多 round 反复改不收敛。**2026-07-01 allGreen 修复后**：r3 全 ok 会先放行（不再收敛误报），故 OSCILLATING 现在意味**真矛盾**（如 CLAUDE.md 两规则冲突、reviewer 反向报）。看 manifest 的 `per_task.<task>.review_history`（每轮每 reviewer 的 findings title+severity，2026-07-01 起存档）找分歧点，人工裁定一侧 → 手动 commit `feat(plan-X/T-Y)` → 全新跑续（见 §7.1） |
| review 空响应 halt | `blocked_info.reason: review_empty`——某 review agent 静默空返回（thinking-only 空响应，模型瞬态 hiccup；非限额非崩溃）。**全新跑续即可**（见 §7.1，勿用 resumeFromRunId）；频繁复发则换 model 槽 |
| review 空诊断 halt | `blocked_info.reason: review_failed_no_findings`——某 review agent 明确判 `failed` 但 `issues`/`silent_failures` 为空（无可执行发现）。implementor 无法据空反馈修复，halt 暴露而非跑空循环误报 max rounds。多为 prompt/schema 与 model 不匹配（如 LLM 用错字段名）——看该 review 的 diag，**全新跑续**（见 §7.1） |
| 限额 halt | `blocked_info.quota_exhausted: true`——等额度恢复后**全新跑**续跑（见 §7.1，勿用 resumeFromRunId） |
| gate 失败 | committed SHA 上 test/lint 任一非 0——看 `tests_exit_code` + `pytest_summary` + `lint_results`（lint 失败多为架构纪律违反，如 domain 层 import infra 被 `lint-imports` 抓到） |

## 13. 相关文件

| 文件 | 作用 |
|---|---|
| `.claude/workflows/run-plans.js` | orchestrator 主体（顶层 await + runTask/halt/编排；inline 复制 lib.js 的 PROMPTS/SCHEMAS/helpers） |
| `docs/superpowers/workflows/lib.js` | 纯函数真源（leafTasks/detectOscillation/buildPrompt/SCHEMAS/PROMPTS + 条件渲染 helpers + cross-reviewer 重叠检测 `groupFindingsByFile`/`formatCrossReviewerNote` + **bootstrap task 识别三层防御** `bareTaskId`（sanitize task_id）/ `dropParentTasks`（leaf-guard）/ `extractCompletedFromSubjects`（确定性 completed 提取），253 测试） |
| `docs/superpowers/workflows/tests/` | node:test 单元测试；含 `sync.test.js` 同步护栏（断言 run-plans.js 的 PROMPTS/SCHEMAS 与 lib.js 一致——改 lib 必须 sync 副本，否则测试红） |
| `workflow.config.json` | 项目配置（命令 + spec_path） |
| `docs/superpowers/workflow-design.md` | 设计 spec（§1-14 + cross-reviewer surfacing §13j） |
| `docs/superpowers/workflow-plans/` | workflow 自身的 implementation plan 和 spec（共识矩阵 v1.0→v2.0 简化历程） |
| `docs/superpowers/workflows/research/` | loop engineering 研究报告 + run-plans.js 评估 |
