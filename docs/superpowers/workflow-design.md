# Workflow Script 设计文档

> 基于 subagent-driven-development 技能 + dynamic-workflow 的通用实现 plan 执行框架。

## 1. 目标

将 6 份实现 plan 的执行过程自动化：每个 task 派发 subagent（implementor → spec-reviewer → quality-reviewer），orchestrator 调度全程，遵循 TDD，按 plan 顺序推进。

**通用性要求**：workflow script 不绑定本项目，可复用于任何有 implementation plan 的项目。

---

## 2. 角色与模型策略

### 2.1 固定角色

| 角色 | 模型 | 职责 | 介入时机 |
|---|---|---|---|
| **orchestrator** | session 主模型（opus） | 调度、context 构造、模型决策、状态管理 | 全程 |
| **implementor** | sonnet/opus（动态） | TDD 实现，遵循 subagent-driven-development | 每个 task |
| **spec-reviewer** | opus | 逐行比对代码 vs spec，独立验证 | 每个 task |
| **quality-reviewer** | opus | 代码质量/架构/边界/类型一致性 | 每个 task |
| **silent-failure-hunter** | — | 检查静默失败、吞掉的错误、bad fallback | 每个 task |
| **simplify** | — | 精简代码（可选，最多触发 1 次） | 每个 task |
| **pr-test-analyzer** | — | 审查测试覆盖质量 | 所有 plan 完成后 |
| **requesting-code-review** | — | 请求人工代码审查 | 用户主动发起（workflow 结束后） |
| **receiving-code-review** | — | 接收人工审查结果 | 用户主动发起（workflow 结束后） |
| **verification-before-completion** | — | 确认所有工作完成、无遗留 | 最终阶段 |
| **finishing-a-development-branch** | — | 准备提交、清理、合并 | 最终阶段 |

### 2.2 implementor 模型：orchestrator 动态决策

**不硬编码**。orchestrator 在派发每个 task 前，根据以下信号评分判断。

#### 评分维度（每项 0-2 分）

| 信号 | 0 分 | 1 分 | 2 分 |
|---|---|---|---|
| **文件数** | 1-2 文件 | 3-4 文件 | 5+ 文件 |
| **spec 完整度** | 完整代码 | 部分代码 | 仅意图/方向 |
| **领域复杂度** | 配置/声明/机械 | 业务逻辑 | 算法/状态机/安全 |
| **集成耦合** | 独立模块 | 轻度耦合 | 跨模块协调 |
| **判断需求** | 确定性（对/错） | 轻度模糊 | 高度模糊（多种方案） |

#### 决策规则

```
总分 0-5 → sonnet
总分 6-10 → opus
```

orchestrator 在 prompt 中编码评分逻辑，对每个 task 逐项打分后查表。

#### 升级策略

若 implementor（sonnet）返回 `BLOCKED` 或 `NEEDS_CONTEXT`：

**BLOCKED（能力不足）**
1. orchestrator 分析 blocker
2. 用 **opus 重新派发**同一 task（模型升级）
3. opus 仍 BLOCKED → 报告人工决策（跳过 / 修改 plan / 终止）

**NEEDS_CONTEXT（信息不足）**

通用分类与补充策略（不绑定具体项目）：

| 缺失类型 | 补充方式 | 说明 |
|---|---|---|
| **文件/路径** | orchestrator 用 `grep`/`glob` 搜索工作区，定位并注入 | 通用：任何项目都有文件搜索能力 |
| **接口签名** | orchestrator 读取相关文件，用 LSP 或正则提取函数/类签名注入 | 通用：语言无关的符号提取 |
| **spec/文档片段** | orchestrator 读取 plan 引用的文档路径，提取相关段落注入 | 通用：plan 中应声明引用路径 |
| **依赖状态** | orchestrator 读取前序 task 的代码文件，提取关键实现注入 | 通用：前序 task 的产出是确定的 |
| **外部知识** | orchestrator 用 Context7/WebSearch 查询后注入 | 通用：不依赖预置知识库 |

重试流程：
```
implementor 返回 NEEDS_CONTEXT + 描述缺什么
    ↓
orchestrator 分析缺失类型 → 用上表策略自动补充
    ↓
原模型重试（第 1 次）
    ↓
仍 NEEDS_CONTEXT？ → 升级 opus + 补充 context → opus 重试（第 2 次）
    ↓
opus 仍 NEEDS_CONTEXT？ → BLOCKED → 报告人工
```

**NEEDS_CONTEXT 最多重试 2 次**，超过升级为 BLOCKED。

---

## 3. 测试策略

| 层级 | 时机 | 命令 | 说明 |
|---|---|---|---|
| **task 级** | 每个 task 完成后 | `uv run pytest tests/xxx.py -v` | 只跑本 task 涉及的测试文件，秒级反馈 |
| **plan 级** | plan 全部 task 完成后 | `uv run pytest -v` | 全量套件，门禁：不绿不进下一个 plan |
| **跨 plan** | 全部 plan 完成后 | `uv run pytest -v --cov` | 含覆盖率报告 |

implementor 遵循 TDD（参考 superpowers:test-driven-development）：
1. 写失败测试 → run 确认红
2. 最小实现 → run 确认绿
3. 重构（可选）→ run 确认绿

> 注意：commit 不在 TDD 阶段，而在全部 review 通过后（见 §4）。

---

## 4. 每个 Task 的执行流程

```
┌─────────────────────────────────────────────────────────┐
│ orchestrator: 读取 task 文本 + 构造 context              │
│ orchestrator: 评估 task 复杂度 → 选择 implementor 模型    │
└──────────────────────┬──────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────┐
│ [implementor] TDD 实现（继承 subagent-driven-development）│
│   1. 写失败测试 → run 单文件测试（确认红）                  │
│   2. 实现代码 → run 单文件测试（确认绿）                    │
│   3. 重构（可选）→ run 单文件测试（确认绿）                 │
│   4. self-review → report status                         │
└──────────────────────┬──────────────────────────────────┘
                       ▼
              status == BLOCKED?
              ┌─── yes ──→ orchestrator 分析 → 升级模型/补 context → 重新派发
              │
              no
              ▼
┌─────────────────────────────────────────────────────────┐
│ [spec-reviewer opus] 读代码 vs spec 逐行比对              │
│   ✅ → 继续                                              │
│   ❌ → implementor 修复 → re-review（循环直到 ✅）         │
└──────────────────────┬──────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────┐
│ [quality-reviewer opus] 读 diff，检查：                    │
│   - 单一职责 / 模块可测试 / 文件结构 / 文件大小             │
│   ✅ → 继续                                              │
│   ❌ → implementor 修复 → re-review（循环直到 ✅）         │
└──────────────────────┬──────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────┐
│ [silent-failure-hunter] 检查：                            │
│   - 吞掉的错误 / 缺失的错误传播 / bad fallback             │
│   ✅ → 继续                                              │
│   ❌ → implementor 修复 → re-review（循环直到 ✅）         │
└──────────────────────┬──────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────┐
│ [simplify] 精简代码（可选）                                │
│   - 未改代码 → 直接 commit                                │
│   - 改了代码 → 重跑 spec-review → quality-review           │
│     → silent-failure-hunter（最多触发 1 次）               │
└──────────────────────┬──────────────────────────────────┘
                       ▼
              spec-review ✅ + quality-review ✅ + silent-failure-hunter ✅
              → commit → mark task done → 下一个 task
```

---

## 5. 依赖图与并行调度

### 5.1 依赖声明（通用格式）

plan 文件声明 plan 级和 task 级依赖：

```json
{
  "plans": [
    {
      "id": "plan-01",
      "deps": [],
      "tasks": [
        { "id": "T1", "deps": [] },
        { "id": "T2", "deps": ["T1"] },
        { "id": "T3", "deps": ["T1"] },
        { "id": "T4", "deps": ["T3"] }
      ]
    },
    {
      "id": "plan-02",
      "deps": ["plan-01"],
      "tasks": [...]
    }
  ]
}
```

### 5.2 调度模型

**全局并发池 = 3 slots**。无论并行的是 plan 还是 task，总数不超过 3。

```
并发池 (max 3)
├── slot 1: Plan A / Task X
├── slot 2: Plan B / Task Y
└── slot 3: Plan A / Task Z
```

调度逻辑：
1. 构建 plan 级依赖图 → 拓扑排序为 plan 层
2. 对每层 plan，构建跨 plan 的统一 task 池
3. task 池按依赖拓扑排序为 task 层
4. 每层 task 用全局 3-slot 并发执行
5. 层内全部通过 → 进下一层；任一失败 → BLOCKED

```javascript
async function executeAll(config) {
  const planLayers = topoSortLayers(config.plans)

  for (const planLayer of planLayers) {
    await executePlanLayer(planLayer, { maxConcurrency: 3 })

    // 门禁：当前层所有 plan 全量测试通过
    if (!planLayer.every(p => p.testsPassed)) {
      throw new Error(`Gate failed`)
    }
  }
}

async function executePlanLayer(plans, { maxConcurrency }) {
  const taskPool = collectReadyTasks(plans)

  while (taskPool.length > 0) {
    const batch = taskPool.splice(0, maxConcurrency)
    await parallel(batch.map(t => () => executeTask(t)), { maxConcurrency })
    updateDepsAndEnqueue(results, taskPool)
  }
}
```

### 5.3 门禁

- **task 层门禁**：层内所有 task review 通过 → 进下一层
- **plan 层门禁**：层内所有 plan 全量测试通过 → 进下一层 plan
- **全局门禁**：所有 plan 通过 → 进 post-plan 阶段（§10）

---

## 6. Workflow Script 通用结构

```javascript
export const meta = {
  name: 'execute-implementation-plans',
  description: 'Execute implementation plans with subagent-driven development',
}

// orchestrator 逻辑（全自动模式）：
// 1. 读取 plan 声明（依赖图 + task 列表）
// 2. 构建 plan 级 + task 级依赖图
// 3. 拓扑排序 → 按层执行（全局 3-slot 池）
// 4. 每个 task：implementor → spec-review → quality-review → silent-failure-hunter → simplify? → commit
// 5. 每个 plan：全量测试 → 门禁
// 6. 全部 plan 后：pr-test-analyzer → verification → finishing
// 7. 输出最终报告
// 8. 用户可选：requesting-code-review（workflow 结束后手动发起）
```

### 6.1 关键设计：模型选择不硬编码

**B 动态评估为主 + A plan 标注可选覆盖 + BLOCKED 升级兜底**

```javascript
function selectImplementorModel(task, planContext) {
  // 优先级 1：plan 文件标注（可选）
  if (task.metadata?.modelHint) return task.metadata.modelHint

  // 优先级 2：orchestrator 评分（§2.2）
  const score = evaluateTaskComplexity(task) // 0-10
  return score <= 5 ? 'sonnet' : 'opus'

  // 优先级 3：BLOCKED 升级（§2.2 升级策略）
}
```

**路径 A. Plan 文件标注（可选覆盖）**
plan 写作者可对高复杂 task 标注建议模型，orchestrator 直接采用：
```markdown
## Task 6: Crypto 服务 <!-- model: opus -->
```
无标注时 orchestrator 走路径 B 自动判断。

**路径 B. Orchestrator 动态评估（默认）**
orchestrator 读 task 文本，用 §2.2 的 5 维度评分逻辑判断。
零维护，通用可用，不依赖 plan 写作者的模型选择经验。

---

## 7. 本项目（lottery-notification）的具体分配

> 以下为本项目的 orchestrator 决策参考，不写入通用 workflow script。

### Plan 01（基础设施骨架）

| Task | 内容 | 模型 | 理由 |
|---|---|---|---|
| 1 | uv init + pyproject.toml | sonnet | CLI + 配置文件 |
| 2 | pydantic-settings | sonnet | spec 有完整代码 |
| 3 | DB engine WAL | sonnet | spec 有完整代码 |
| 4a-d | SQLModel 13 表 | sonnet | 声明式，无行为逻辑 |
| 5 | Alembic 迁移 | sonnet | CLI + 配置 |
| 6 | Crypto Fernet 多版本 | **opus** | 安全敏感，轮换逻辑 |
| 7 | 7 彩种种子 | sonnet | 数据填充 |
| 8 | FastAPI + /health | sonnet | spec 有完整代码 |
| 9 | import-linter | sonnet | 单个 toml |
| 10 | 全量测试 + 文档 | sonnet | 跑测试 + 编辑 |

### Plan 02（领域层）— 全部 opus

| 理由 |
|---|
| 纯算法 + 类型不变式 + 奖级表需对照 lottery-rules.md + 3 种 CompareStrategy 边界条件 |

### Plan 03（仓储+核心闭环）

| Task | 模型 | 理由 |
|---|---|---|
| 1-2 Repository 抽象 | **opus** | 多表协调、接口设计 |
| 3-4 双源获取+交叉校验 | **opus** | 容灾逻辑 |
| 5 比对引擎 | **opus** | 调用领域层 |
| 6-8 CLI/smoke | sonnet | 集成胶水 |

### Plan 04（调度+推送）

| Task | 模型 | 理由 |
|---|---|---|
| 1 APScheduler 接线 | **opus** | jobstore 独立 engine |
| 2-3 渠道+Notifier | **opus** | 双路径 + DND + 降级 |
| 4-6 模板/注册/backfill | sonnet | 模板渲染 + CLI |

### Plan 05（认证+用户）

| Task | 模型 | 理由 |
|---|---|---|
| 1-2 JWT + CSRF | **opus** | httpOnly cookie + 安全 |
| 3-4 用户 API + admin | sonnet | CRUD |

### Plan 06（Web UI + 部署）

| Task | 模型 | 理由 |
|---|---|---|
| 1-6 Vue3 页面 | sonnet | prototype HTML 照搬迁移 |
| 7 走势页 | **opus** | ECharts + 遗漏算法 |
| 8-10 Docker/部署 | sonnet | 配置文件 |

---

## 8. Red Flags（继承自 subagent-driven-development）

- 绝不跳过 spec-review、quality-review 或 silent-failure-hunter
- 绝不在 spec-review 未通过时启动 quality-review
- 绝不带着未修复的问题推进下一个 task
- 绝不用 "差不多" 模糊通过 spec compliance
- 绝不让 implementor 自审替代正式 review
- 绝不忽视 BLOCKED — 必须分析原因并调整策略
- 绝不让 simplify 改代码后跳过重跑 review

---

## 9. 待 workflow script 细化（5 项）

- [ ] **9a. 通用调度框架**：依赖图 + 拓扑排序 + 全局 3-slot 池 + 门禁
- [ ] **9b. 模型选择策略**：5 维度评分逻辑 + plan 标注覆盖 + BLOCKED 升级
- [ ] **9c. 三个 prompt 模板**（继承 subagent-driven-development 技能）：
  - implementor prompt（含 TDD + self-review + 项目上下文）
  - spec-reviewer prompt（含 spec 引用路径 + 逐行比对方法论）
  - quality-reviewer prompt（含编码规范 + 检查项清单）
- [ ] **9d. 错误恢复策略**：BLOCKED 分类 + 处理流程；NEEDS_CONTEXT 5 类通用补充策略 + 2 次重试上限
- [ ] **9e. plan 文件格式约定**：依赖声明 + 可选 model hint + task 输出格式

---

## 10. Post-Plan 阶段（所有 plan 完成后）

**全自动模式**：workflow 全程无人工介入，`requesting-code-review` 由用户在 workflow 结束后主动发起。

```
所有 plan 完成
    ↓
跨 plan 全量测试（含覆盖率报告）
    ↓
pr-test-analyzer — 审查测试覆盖质量（行为覆盖、边界测试）
    ↓
verification-before-completion — 确认所有工作完成、无遗留
    ↓
finishing-a-development-branch — 准备提交、清理、合并
    ↓
最终报告（workflow 结束）
    ↓
[用户主动发起 requesting-code-review → receiving-code-review]
```

### 10.1 完整 Pipeline 总览

```
Plan 层（按依赖图并行，全局 3-slot）
  │
  ├─ Task 层（按依赖图并行，共享全局 3-slot）
  │   implementor → spec-review → quality-review → silent-failure-hunter → simplify?
  │                                                                           │
  │                                           simplify 未改代码 → commit ◄────┘
  │                                           simplify 改了代码 → spec-review → quality-review
  │                                             → silent-failure-hunter → commit（最多 1 次循环）
  │
  ├─ plan 全量测试
  ├─ 门禁 ✅/❌
  │
  ▼
Post-Plan 阶段（§10，全自动）
  → pr-test-analyzer → verification → finishing → 最终报告

[workflow 结束后，用户可选]
  → requesting-code-review → receiving-code-review
```
