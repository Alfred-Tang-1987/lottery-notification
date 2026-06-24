# Workflow Orchestrator 使用指南

> 工具：`.claude/workflows/run-plans.js`（Claude Code `Workflow` 工具上的 JS orchestrator）
> 设计依据：`docs/superpowers/workflow-design.md`（§1-13 + §2.4）

## 1. 这是什么

`run-plans` 是一个**自动执行 implementation plan 的编排器**。给它一份或多份 plan，它会：

- **每 task**：派 implementor subagent（TDD RED→GREEN→REFACTOR）→ review chain 并行（spec 逐行比对 ‖ quality 架构 ‖ silent-failure-hunter）→ 精简 simplify → git commit
- **plan 级**：独立 gate（在 committed SHA 上重跑 test + lint_command + extra_lint_commands，任一非 0 halt，不信 implementor 自报）
- **全流程**：多 plan 串行、振荡检测、BLOCKED 升级链、限额容错（halt + resume）

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
  "language": "python"
}
```

### config 字段说明

| 字段 | 必填 | 作用 |
|---|---|---|
| `test_command` | 是 | implementor/commit 跑的单测命令 |
| `full_test_command` | 是 | plan gate 跑的全量测试命令 |
| `build_command` | 否 | 构建（当前未在 gate 强制） |
| `lint_command` | 否 | 通用 lint（gate 会跑，非 0 即 halt） |
| `extra_lint_commands` | 否 | **架构/专项 lint 数组**（gate 依次跑）。承载项目架构纪律——如本项目的 `uv run lint-imports`（domain 层零 IO 纯度护栏）。不配即不约束 |
| `spec_path` | 是 | 设计 spec（specReview 逐条对照） |
| `reference_paths` | 否 | **额外权威文档数组**（implementor/specReview 对照）。承载 spec 之外的硬规则——如本项目的彩种规则参考。不配即该 prompt 段消失 |
| `language` | 否 | `python` / `general`（可扩展 ts/go…）。决定 qualityReviewer 的语言专项清单。未知值 → 通用清单 |

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

| 参数 | 必填 | 说明 |
|---|---|---|
| `configPath` | 否 | workflow.config.json 路径。不传 → bootstrap 自动在项目根发现 |
| `plansDir` | 否 | plan 目录。不传 → bootstrap 自动发现 `docs/superpowers/plans/` |
| `plan` | 否 | 限定单 plan。传 **seq**（如 `'01'`）或 **id**（如 `'plan-01'`）。不传 → 跑所有 plan |
| `tasks` | 否 | 限定 task 子集（如 `['T1']`）。不传 → 跑该 plan 所有叶子 task |

## 5. 运行流程（自动）

```
get-ts（取时间戳）
  → bootstrap：读 config + 给 plan 生成 frontmatter(modelHint) + git log 识别已完成 task
    → for plan（args.plan 过滤）:
        for task（叶子优先，args.tasks 过滤，已完成跳过）:
          runTask:
            implementor(task.model || sonnet)
              → blocked → 升级 opus → 仍 blocked → halt
              → needs_context → contextFetcher → 重试
              → failed → 重试一次 → halt
              → done_with_concerns → 记疑虑，继续进 review（不 halt）
            review rounds (max 3): spec ‖ quality ‖ hunter 并行
              → 全绿 break / 任一❌→ implementor 修复 → 下轮 / max3 → halt
              → 振荡检测（同文件≥3 round）
            simplify (max 1) → 无条件重跑 review → 失败委托 commit 回退
            commit → git commit feat(plan-X/T-Y)
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
- surface：「X model 限额耗尽，进度已保存，额度恢复后 resumeFromRunId」
- **额度恢复后续跑**：
  ```
  Workflow({ scriptPath: '.claude/workflows/run-plans.js', resumeFromRunId: '<runId>', args: {...} })
  ```
  native resume：已 commit 的 task 跳过，中断的 task 重跑。

### 手动中断 / 崩溃
同样用 `resumeFromRunId` 续跑。崩在 implementor 后/commit 前的半成品，native resume 重跑 implementor 覆盖。

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
      "commit_sha": "abc1234",
      "blocked_info": { "reason": "...", "quota_exhausted": false, "last_error": "...", "suggested_fix": "..." }
    }
  },
  "result": "done" | "halted"
}
```
崩溃/halt 后看它定位问题（非翻 transcript）。

## 9. 常见场景

| 场景 | args |
|---|---|
| 跑单 plan 全 task | `{ plan: '01' }` |
| 跑单 task 验证闭环 | `{ plan: '01', tasks: ['T1'] }` |
| 跑所有 plan | `{}`（不传 plan/tasks） |
| resume 续跑 | `Workflow({ scriptPath, resumeFromRunId: '<runId>', args: {...} })` |
| 限额恢复后续跑 | 同 resume（先看 manifest 的 `quota_exhausted`） |

## 10. task modelHint（用哪个 model）

plan frontmatter 的 `model` 字段决定 implementor 用 sonnet 还是 opus：
- bootstrap **自动标**：标题含「安全/加密/认证/JWT/CSRF/Fernet/算法/比对/策略/边界/集成/接口」→ `opus`，其余 → `sonnet`
- review 固定用 opus（spec/quality）、sonnet（hunter）
- 手动改 plan frontmatter 的 model 可覆盖自动标注

> 环境若强制单一 model（如本机实测 implementor 跑 kimi-k2.7-code），modelHint 可能被环境默认覆盖——不影响功能，仅 model 选择层面。

## 11. 注意事项

- **首次跑会修改所有 plan 文件**（加 frontmatter，幂等，已加的不重写）
- **业务代码必须可测**——`test_command` 跑不通时 implementor/gate 会失败（项目未初始化时 bootstrap 容忍，但 implementor 跑测试需要 pyproject.toml 等就绪）
- **commit convention**：`feat(plan-X/T-Y): <title>`——resume 靠 git log 识别已完成 task
- **不降级继续**：限额 halt 后不会用弱 model 继续开发，只保存进度等恢复（§2.4 核心原则）
- **错误恢复**：bootstrap/get-ts/implementor/review/simplify/commit/gate/finalReport **所有** agent 路径都有 quota 捕获 + 兜底，不会裸 crash 丢进度

## 12. 调试

| 现象 | 排查 |
|---|---|
| workflow paused | `/workflows` 看卡在哪个 agent；读 `runs/<ts>/manifest.json` 或 transcript |
| implementor 反复失败 | 看 `blocked_info.last_error` + `suggested_fix`；可能 plan 顺序错（依赖前序 task） |
| review 振荡 halt | `blocked_info.reason: OSCILLATING`——同文件多 round 反复改，人工介入 |
| 限额 halt | `blocked_info.quota_exhausted: true`——等额度恢复 `resumeFromRunId` |
| gate 失败 | committed SHA 上 test/lint 任一非 0——看 `tests_exit_code` + `pytest_summary` + `lint_results`（lint 失败多为架构纪律违反，如 domain 层 import infra 被 `lint-imports` 抓到） |

## 13. 相关文件

| 文件 | 作用 |
|---|---|
| `.claude/workflows/run-plans.js` | orchestrator 主体（顶层 await + runTask/halt/编排；inline 复制 lib.js 的 PROMPTS/SCHEMAS/helpers） |
| `docs/superpowers/workflows/lib.js` | 纯函数真源（leafTasks/detectOscillation/buildPrompt/SCHEMAS/PROMPTS + 条件渲染 helpers，45 测试） |
| `docs/superpowers/workflows/tests/` | node:test 单元测试；含 `sync.test.js` 同步护栏（断言 run-plans.js 的 PROMPTS/SCHEMAS 与 lib.js 一致——改 lib 必须 sync 副本，否则测试红） |
| `workflow.config.json` | 项目配置（命令 + spec_path） |
| `docs/superpowers/workflow-design.md` | 设计 spec（§1-13 + §2.4） |
| `docs/superpowers/plans/` | implementation plan（6 份业务 plan + 1 份本工具 plan） |
