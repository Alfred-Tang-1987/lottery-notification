# Workflow Script 设计文档

> 基于 subagent-driven-development 技能 + dynamic-workflow 的通用实现 plan 执行框架。

## 1. 目标

将 6 份实现 plan 的执行过程自动化：每个 task 派发 subagent（implementor → spec-reviewer → quality-reviewer），orchestrator 调度全程，遵循 TDD，按 plan 顺序推进。

**通用性要求**：workflow script 不绑定本项目，可复用于任何有 implementation plan 的项目。

---

## 2. 角色与模型策略

### 2.1 固定角色

| 角色 | 模型 | 职责 |
|---|---|---|
| **orchestrator** | session 主模型（opus） | 调度、context 构造、模型决策、状态管理 |
| **spec-reviewer** | opus | 逐行比对代码 vs spec，独立验证 |
| **quality-reviewer** | opus | 代码质量/架构/边界/类型一致性 |

### 2.2 implementor 模型：orchestrator 动态决策

**不硬编码**。orchestrator 在派发每个 task 前，根据以下信号判断：

#### 决策信号

| 信号 | 倾向 sonnet | 倾向 opus |
|---|---|---|
| **文件数** | 1-2 文件 | 3+ 文件 |
| **spec 完整度** | plan 给了完整代码 | plan 只给了意图/方向 |
| **领域复杂度** | 配置/声明/机械 | 算法/状态机/安全逻辑 |
| **集成耦合** | 独立模块 | 跨模块协调 |
| **判断需求** | 确定性（对/错） | 模糊性（多种合理方案） |

#### 决策规则（orchestrator prompt 中编码）

```
对每个 task，orchestrator 评估：

1. task spec 是否包含完整实现代码？ → sonnet
2. task 是否涉及安全/加密/认证？ → opus
3. task 是否涉及纯算法 + 边界条件？ → opus
4. task 是否是声明式（schema/config/seed）？ → sonnet
5. task 是否涉及多模块集成或接口设计？ → opus
6. 不确定？ → 默认 sonnet，BLOCKED 时升级 opus
```

#### 升级策略

若 implementor（sonnet）返回 `BLOCKED` 或 `NEEDS_CONTEXT`：
1. orchestrator 分析 blocker
2. 若是能力不足（非 context 缺失），用 **opus 重新派发**同一 task
3. 若是 context 缺失，补充 context 后用原模型重试

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
4. commit

---

## 4. 每个 Task 的执行流程

```
┌─────────────────────────────────────────────────────────┐
│ orchestrator: 读取 task 文本 + 构造 context              │
│ orchestrator: 评估 task 复杂度 → 选择 implementor 模型    │
└──────────────────────┬──────────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────────┐
│ [implementor] TDD 实现                                   │
│   1. 写失败测试 → run 单文件测试（确认红）                  │
│   2. 实现代码 → run 单文件测试（确认绿）                    │
│   3. self-review → commit → report status                │
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
              mark task done → 下一个 task
```

---

## 5. Plan 间依赖与门禁

```
Plan 01 ──全量测试──→ Plan 02 ──全量测试──→ Plan 03 ──→ ...
   │                    │                    │
   └─ task 1..10        └─ task 1..10        └─ task 1..N
      (串行)               (串行)               (串行)
```

- Plan 内 task **必须串行**（有依赖）
- Plan 之间**必须串行**（后 plan 依赖前 plan 代码）
- **门禁**：plan 全量测试不绿 → 停止，不进下一个 plan

---

## 6. Workflow Script 通用结构

```javascript
export const meta = {
  name: 'execute-implementation-plans',
  description: 'Execute implementation plans with subagent-driven development',
}

// orchestrator 逻辑：
// 1. 读取 plan 文件列表（从 args 传入）
// 2. 对每个 plan：
//    a. 提取所有 task
//    b. 对每个 task：
//       - 评估复杂度 → 选模型
//       - 派发 implementor
//       - 派发 spec-reviewer
//       - 派发 quality-reviewer
//    c. 跑全量测试
// 3. 输出最终报告
```

### 6.1 关键设计：模型选择不硬编码

workflow script 中的模型选择逻辑应作为 **可配置策略**：

```javascript
// 策略函数：由项目上下文决定，workflow 调用
function selectImplementorModel(task, planContext) {
  // 默认策略（可被项目覆盖）
  // task.metadata.modelHint 可由 plan 文件标注
  // 或 orchestrator 根据 task 文本动态判断
  if (task.metadata?.modelHint) return task.metadata.modelHint
  return 'sonnet' // 默认
}
```

**两种实现路径**：

**A. Plan 文件标注（推荐）**
在 plan 的 task 中加 metadata 标注建议模型：
```markdown
## Task 6: Crypto 服务 <!-- model: opus -->
```
orchestrator 读取标注，无标注则默认 sonnet。

**B. Orchestrator 动态评估**
orchestrator 读 task 文本，用启发式规则判断（见 §2.2）。
更灵活但更依赖 orchestrator 的判断质量。

**建议：A + B 结合**——plan 标注提供默认，orchestrator 可根据 BLOCKED 升级。

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

- 绝不跳过 spec-review 或 quality-review
- 绝不在 spec-review 未通过时启动 quality-review
- 绝不带着未修复的问题推进下一个 task
- 绝不用 "差不多" 模糊通过 spec compliance
- 绝不让 implementor 自审替代正式 review
- 绝不忽视 BLOCKED — 必须分析原因并调整策略

---

## 9. 待 workflow script 细化

- [ ] workflow script 通用框架（读 plan → 提取 task → 派发 → review → 门禁）
- [ ] 模型选择策略的可配置接口
- [ ] implementor prompt 模板（含 TDD + self-review + 本项目上下文）
- [ ] spec-reviewer prompt 模板（含本项目 spec 引用路径）
- [ ] quality-reviewer prompt 模板（含本项目编码规范）
- [ ] plan 文件中标注 model hint 的格式约定
- [ ] 错误恢复策略（BLOCKED → 升级/拆分/人工介入）
