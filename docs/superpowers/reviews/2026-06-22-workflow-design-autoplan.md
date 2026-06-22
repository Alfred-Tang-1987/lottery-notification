# /autoplan Review — workflow-design.md

> 对象：`docs/superpowers/workflow-design.md`（Workflow Script 执行框架设计）
> 日期：2026-06-22 | Commit: ce764fe | Branch: main | via: autoplan
> Dual voice: Claude subagent only（Codex binary 不可用，降级）

## 评审对象性质

`workflow-design.md` 是一个 **meta 文档**——它设计的是一个"在 `superpowers:subagent-driven-development` 之上、用 `Workflow` 工具执行多 plan 的编排框架"。核心诉求：**通用、可复用于任何有 implementation plan 的项目**。

---

## Phase 1 — CEO Review（策略/范围/通用性）

### 现有 leverage map（upstream 已提供什么）

核对 upstream `subagent-driven-development/SKILL.md`（279行），确认以下**已存在**：

| workflow-design 章节 | upstream 已覆盖 |
|---|---|
| §2.1 三角色（implementor/spec/quality） | ✓ SKILL.md L8,47-58 |
| §4 两阶段 review 流程 + 顺序（spec 先于 quality） | ✓ L12,67-79 |
| §2.2 状态机 DONE/NEEDS_CONTEXT/BLOCKED + 升级 | ✓ L104-120（几乎逐字） |
| §2.2 模型选择（mechanical→cheap, judgment→capable） | ✓ L89-102（"least powerful model"） |
| §8 Red Flags | ✓ L236-248（几乎逐字） |

**真正的增量（仅 5 点）**：①多 plan + task 依赖图调度 ②3-slot 全局并发池 ③5维评分模型选择 ④NEEDS_CONTEXT 5分类 ⑤silent-failure-hunter 额外 review slot。

### CEO Consensus Table（主 agent + Claude subagent）

| 维度 | 主 agent | subagent | Consensus |
|---|---|---|---|
| 1. framework 存在形式（418行 vs 80行薄 delta） | 保留框架，精简重复部分 | 砍成 80 行薄 delta | **DISAGREE → 用户决策** |
| 2. 通用性声明是否成立 | §7 外仍有大量耦合 | 失败，需抽象接口 | **CONFIRMED 问题** |
| 3. 并行调度安全性 | 用户要求，但缺 isolation | 与 upstream Red Flag L242 冲突 | **CONFIRMED 问题（需 worktree）** |
| 4. 5维评分必要性 | 用户决策（讨论1） | 无校准，过度形式化 | **DISAGREE → User Challenge** |
| 5. NEEDS_CONTEXT 5分类 | 用户决策（明确要求细化） | 多余 plumbing | **DISAGREE → User Challenge** |
| 6. 额外 agent（silent-failure/simplify/pr-test-analyzer） | 用户决策（讨论6） | 应改为可选 hook | **DISAGREE → User Challenge** |
| 7. Workflow runtime 可行性 | 未考虑约束 | 多处缺口 | **CONFIRMED 问题** |

### 确认的问题（非 taste，应修复）

**问题 A — 通用性耦合（违背核心诉求）** [CRITICAL]
- §2.1 硬编码 8 个 ECC/superpowers agent（silent-failure-hunter/pr-test-analyzer/simplify/4个 command）
- §3 硬编码 `uv run pytest` / `pytest --cov`（排除 npm/cargo/go/maven/gradle）
- §2.2 L74 引用 Context7/WebSearch（特定 MCP）
- §4 L119 硬依赖 `superpowers:subagent-driven-development`
- 删除 §7 后仍无法在无 ECC+superpowers 插件的环境运行
- **修复**：引入抽象接口（TestRunner / Reviewer / project manifest）

**问题 B — Workflow runtime 可行性缺口** [HIGH]
- orchestrator（JS sandbox）**无文件系统访问**——§2.2 L70-73 "用 grep/glob 搜索" 实际须通过 agent() 委托
- **无 Date.now/Math.random**——重试/backoff 逻辑受限
- **agent() 上限 16 并发**：每 task fan-out 5 agent（implementor+spec+quality+hunter+simplify）× 3 task = 15，零 headroom，review 重跑会溢出
- **plan 是 Markdown 不是 JSON**——§5.1 的 JSON schema 需 LLM 解析，依赖图本身不可靠
- **修复**：补充 "Runtime constraints" 章节，说明每项约束如何被尊重

**问题 C — 并行与 upstream Red Flag 冲突** [HIGH]
- upstream L242 明确禁止并行 implementor（working tree 冲突）
- §5.2 的 3-slot 并行未提 worktree isolation
- **修复**：并行 task 必须用 `Workflow isolation:'worktree'`，文档明确说明

**问题 D — 重复 upstream（呈现冗余）** [MEDIUM]
- §2.1/§4 基础流程与 upstream 重复，应明确"继承 upstream，此处仅列 delta"

### Dream state delta

- 当前：418 行完整框架设计，混合了 upstream 重复 + 真实增量 + 项目耦合
- 12 个月理想：通用编排框架，通过 manifest 接入任意项目，delta 清晰，runtime 约束显式
- 此 plan 留下的差距：通用性是声明而非接口；runtime 约束缺失；并行缺 isolation

### 暂未决（待 Phase 3/3.5）

架构（依赖图死锁/状态持久化/崩溃恢复）+ DX（plan 格式约定/接入成本/可调试性）见后续 phase。

---

## Phase 3 — Eng Review（架构/runtime 可行性）

> 评审对象：CEO 决策后的修订架构（serial plan/task + review 并行 + modelHint + 项目配置）
> verdict：方向正确（serial + review 并行 + 砍评分是真正改进），但仅 ~60% specified。3 个 CRITICAL gap 阻止首次实现。

### Eng Consensus（主 agent + Claude subagent）

**CRITICAL（阻塞实现，必须修）**

| # | 发现 | 修复 |
|---|---|---|
| C1 | **并行 review 语义错误**：非写冲突，是时间正确性。spec 触发修复后 tree 变，并行 quality/hunter 的 verdict 针对旧 snapshot 过时。§4 的"每 reviewer 独立循环直到✅"自相矛盾——要么一次性并行（无循环），要么循环（不能并行） | 改 **bounded rounds**：round 内 3 review 并行（同 snapshot OK），round 间串行（任一 ❌ 触发新 round 针对新 tree），max 3 rounds，全 ✅ 退出 |
| C2 | **agent 返回值契约未定义**：orchestrator(JS) 无 fs/无 subprocess，测试通过/commit 成功/diff 全靠 agent() 返回文本。无 schema → 所有 gate 是 LLM 声称，无法验证 | 新增 **Agent Boundary Protocol**：每 agent 返回 `{status: ok\|failed\|blocked\|needs_context, evidence:{tests_exit_code, commit_sha, files_changed}, summary}`。gate 基于 evidence 非叙述 |
| C3 | **无崩溃恢复**：runtime 无持久化，workflow 死则状态全丢。6-plan ~50-task 必中断 | 新增 **Bootstrap & Resume phase**：bootstrap subagent 读 git log + plan，返回 `{completed_task_ids}`，orchestrator 跳过已完成 task。commit 用确定性 `feat(plan-X/T-Y)` 约定 |

**HIGH**

| # | 发现 | 修复 |
|---|---|---|
| H4 | 循环+并行 review+simplify 重跑可能超 16 agent 上限，无预算 | per-task agent 预算：max 3 review rounds + max 1 simplify；serial task → 同时仅 1 task in flight |
| H5 | config/plan 加载无 bootstrap 路径（JS 无 fs） | bootstrap subagent 读 config+plan，返回严格 JSON（echo verbatim + 校验 task 数/ID 唯一/model enum），失败 BLOCKED |
| H6 | commit 原子性弱（脏树/pre-commit hook 失败无分支） | commit subagent 原子化：git status check → test → commit → 返回 sha；失败重试一次→BLOCKED |

**MEDIUM**

| # | 发现 | 修复 |
|---|---|---|
| M7 | modelHint via Markdown comment 脆弱（格式漂移→静默降级 sonnet） | sidecar/typed enum `{enum:[sonnet,opus]}`，loader 校验，unknown → fail loud |
| M8 | BLOCKED 升级 opus 无上限无退出 | sonnet→opus→halt+写 `.workflow/blocked.md`+surface |
| M9 | NEEDS_CONTEXT 兑现表（orchestrator grep/glob/Context7）runtime 不可行 | 全改为 `agent({role:'context-fetcher'})` dispatch |
| M10 | simplify re-review "max 1" 信任自报（simplify 可谎报无改动） | simplify 后**无条件**重跑 review round（不信任自报），max 1 由 orchestrator 计数 |
| M11 | 未用 runtime 的 phase()/pipeline()（可能是 resume 原语） | 评估使用 phase() 做 resume 标记 |

### Eng Required Changes（实现前必需）

1. **review 改 bounded rounds**（C1+H4）—— round 内并行、round 间串行、max 3
2. **Agent Boundary Protocol**（C2+H6+M10）—— status enum + evidence，gate 基于证据
3. **Bootstrap & Resume phase**（C3+H5）—— git log + task-id commit 约定 + config/plan 加载

### 架构净变化（Eng 后）

```
[Phase 0] bootstrap subagent: 读 config + plan + git log → 返回 {config, plans, completed_task_ids}
  → 对每个 plan（串行，跳过已完成）：
      → 对每个 task（串行，跳过已完成）：
          implementor(model=modelHint||sonnet) → 返回 {status, evidence}
          [review rounds, max 3]:
            spec ‖ quality ‖ hunter 并行（同 snapshot）→ 收集 verdicts
            全 ✅ → break
            任一 ❌ → implementor 修复 → 下一 round
          simplify?（max 1, orchestrator 计数）→ 无条件触发 review round
          commit subagent: status+test+commit → 返回 {commit_sha}
      → plan 全量测试 subagent → 门禁
  → post-plan: pr-test-analyzer → verification → finishing
```

## Phase 3.5 — DX Review（开发者体验）

> subagent 读了实际 plan 文件（`2026-06-21-01-infra-bootstrap.md`），确认格式不匹配的真实摩擦
> verdict：优化了 orchestrator 能力，几乎没优化 operator legibility。TTHW 当前 ~2-3h，目标 15-20min。

### DX Consensus（主 agent + Claude subagent）

**CRITICAL**

| # | 发现 | 修复 |
|---|---|---|
| DX1 | **文档与修订决策脱节**：workflow-design.md 仍写 `uv run pytest`、§5.2 3-slot 并行池，与 CEO 决策（serial + 项目配置）矛盾。开发者读文档建错东西 | **重写文档**反映所有决策（final gate 后的核心动作） |
| DX2 | **无可观测性**：orchestrator JS 只有 log()，无 run manifest。崩溃后开发者翻 transcript 取证 | 强制 run manifest：`runs/<ts>/{manifest.json,log.ndjson,per-task/<id>.json}`，含 current_task/review_round/last_commit_sha |

**HIGH**

| # | 发现 | 修复 |
|---|---|---|
| DX3 | **false-green 风险**：Agent Boundary Protocol 信任 subagent 自报 `{tests_exit_code:0}`，但 orchestrator(JS) 无法独立验证。trust model 未文档化 | 文档化 trust model 表；plan 级 gate 由独立 subagent 在 committed SHA 上重跑 pytest（Bash 真实 exit code，非 LLM 判断） |
| DX4 | **plan 注解 3 机制竞争**（JSON deps / Markdown comment modelHint / commit 约定）都未明确，writing-plans 产出纯 MD | 统一为 **YAML frontmatter**（单文件，skill 可增强）+ `workflow validate-plans` validator |
| DX5 | **resume 半提交状态未定义**：崩溃在 implementor-return 和 commit 之间，working tree 有半成品，bootstrap 重跑冲突 | resume 契约：task 派发前写 `in_progress`+`pre_task_sha`；resume 时 `git reset --hard <pre_task_sha>`；commit = 状态原子转换 |
| DX6 | **review 振荡不可见无熔断**："循环直到✅"无上限，振荡 task 与收敛 task 不可区分 | max 3 rounds（已在 Eng）；manifest 记 per-round files_touched；振荡检测（同文件连续 round 反转 diff）→ 标 OSCILLATING + surface |

**MEDIUM**

| # | 发现 | 修复 |
|---|---|---|
| DX7 | 项目 config schema 未定义，typo 静默失败 | 定义 schema + 启动 config smoke step（`test_command --collect-only`）fail loud |
| DX8 | NEEDS_CONTEXT/BLOCKED 错误只有 problem 无 cause+fix | final report 每 BLOCKED 条目含 `{task, category, last_error, suggested_fix}` |
| DX9 | mid-run 编辑 plan 行为未定义 | 契约：resume 时 committed task 不重跑；`workflow reset --task T4` 强制重跑 |
| DX10 | 无 onboarding | `workflow init` 脚手架 config + 校验 plan + 打印 next steps |

### TTHW 评估

当前 ~2-3 小时（读矛盾文档 + 手写 config + 手注解 60+ task + 崩溃取证）。修复 DX1/2/4/7/10 后 → 15-20 分钟（`workflow init` + 5-key config + validator + manifest）。

### 3 个最差摩擦点

1. **崩溃后"发生了什么"**（DX2+DX5）—— 无 manifest + resume 契约未定义 = 每次翻 transcript + 手动 git 清理。开发者最可能在此放弃框架。
2. **"能信任这个 green 吗"**（DX3）—— 若要手动重跑每个 commit SHA 验证，框架省了实现却加了验证，净 DX 收益小。
3. **"这个 task 为什么卡 review"**（DX6）—— 振荡 25min 与收敛不可区分，唯一手段是杀进程。

---

## Decision Audit Trail

| # | Phase | Decision | Classification | 结果 |
|---|---|---|---|---|
| 1 | CEO | 通用性耦合修复 | 机械 | **范围A（同工具链不同项目）**：review agent 硬编码；新增轻量项目配置（test/build/lint 命令 + spec 路径） |
| 2 | CEO | runtime 约束补充 | 机械 | 待 §new 章节明确：orchestrator(JS) 不做 IO，全委托 subagent |
| 3 | CEO | 并行架构 | User Challenge→和解 | **implementor 串行 + review 并行**（spec/quality/hunter 3-slot）；砍 worktree（全盘开发不值得） |
| 4 | CEO | framework 形态 | User Challenge→和解 | 保留框架但精简为薄 delta（砍重复 upstream 部分，仅列增量） |
| 5 | CEO | 5维评分 | User Challenge→采纳subagent | **砍掉**（workflow JS 无法执行 LLM 打分）；改 modelHint(writing-plans 标注) + 默认sonnet + BLOCKED升级 |
| 6 | CEO | NEEDS_CONTEXT 5分类 | User Challenge→保留简化 | 保留分类（通用性脚手架），去 2次硬上限 |
| 7 | CEO | 额外 agent | User Challenge→硬编码 | review chain 硬编码 ECC（环境固定）；通用性靠项目配置 + 范围A声明 |

### CEO 最终架构（决策后）

```
读 plan 列表（按顺序）+ 读项目配置（test/build/lint 命令、spec 路径）
  → 对每个 plan（串行，按声明顺序）：
      → 对每个 task（串行，按声明顺序）：
          implementor(model = task.modelHint || 'sonnet')
            → [spec-reviewer ‖ quality-reviewer ‖ silent-failure-hunter 并行 3-slot]
            → simplify?（可选，改了代码则重跑 review）
            → 三重 review 全绿 → commit
      → plan 全量测试（项目配置的 test command）→ 门禁
  → post-plan: pr-test-analyzer → verification-before-completion → finishing-a-development-branch
```

**砍掉**：5维评分、plan/task 两级依赖图、worktree isolation、3-slot 并行 implementor。
**新增**：writing-plans modelHint 标注约定、轻量项目配置、runtime 约束章节。
