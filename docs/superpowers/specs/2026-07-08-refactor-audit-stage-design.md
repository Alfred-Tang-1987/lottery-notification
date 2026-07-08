# Refactor 类 Task 的 AUDIT 阶段设计

**日期**：2026-07-08
**状态**：设计待 review
**动机来源**：2026-07-07 simplification TDD 修复执行（16 task SDD），过程中发现 7 处 spec/plan 缺陷——全部出现在 refactor/extract 类 task，因为这类 task 的 brief 必须描述「现状代码的重复/可替换 pattern」，依赖对现状代码的假设，而假设未被核查。

## 0. 问题陈述

当前 TDD 流程（RED → GREEN → SYNC → FULL）隐含假设 brief 对现状代码的描述是准的。对纯新功能 task（描述「目标行为」）这个假设成立；对 refactor/extract task（描述「现状的重复 pattern + 怎么改」）不成立。

**实测证据**（2026-07-07 执行的 16 task）：

| Task | brief 的现状假设 | 实际 | 缺陷类型 | 发现方式 |
|---|---|---|---|---|
| 8 | "4 处可替换，reasonPrefix 形" | 3 处；reason 把 status 放中间，3-arg 形无法逐字对齐 | site 数 + 签名形 | controller pre-audit |
| 10 | "7 个 prompt 共享相同限额文本" | 分 3 类：5 匹配 / implementor 变体 / lessonDistiller 完全不同 | 文本一致性 | controller pre-audit |
| 11 | "3 reviewer 共享 STATIC + Exemption 段" | hunter 无 exemption；spec/quality 是不同概念 | 文本一致性 + 概念 | controller pre-audit |
| 13 | "escalate/continue 在 osc 块内 return" | 须 fall-through 到 budget guard，否则无限模式无限跑 | 控制流语义 | controller pre-audit（读 resolveReviewBudget 注释） |
| 1 | brief 用 backticks 标 inline code | implementor prompt 是 template literal，backtick 非法 | 语法 | implementor 撞墙 |
| 2 | trip-wire regex 守护函数体大括号平衡 | regex 假阳性（buildPrompt 默认参数 `{}`） | 正则 | implementor 撞墙 |
| 9 | test 3 期望值 `\n\n`（空行） | 函数代码用单 `\n` | brief 内部不一致 | implementor 撞墙 |

7/7 出现在 refactor/extract 类 task。4 处由 controller 在 dispatch implementer 前的 pre-audit 发现（机械核查 site 数 / diff 文本 / 读注释），3 处由 implementor 在执行时撞墙才发现（成本更高）。

**目标**：把 controller 手动做的 pre-audit 机制化，下沉为 refactor 类 task 的标准步骤，让 implementor 在 RED 前先核查 brief 的现状假设。**非目标**：抓所有 spec 缺陷（语义级控制流 bug 仍需人工判断，见 §5.1）。

## 1. 触发机制（显式标记 + 关键词强制兜底）

**显式标记（主路径）**：plan 作者在 refactor 类 task 的 frontmatter（或 task 头部）加 `Type: refactor`。新功能/bugfix task 标 `Type: feature` 或不标。bootstrap 解析时读 `Type`，若 `=== refactor` 则该 task 的 `state.perTask[taskKey].audit_required = true`。

**关键词强制兜底（安全网）**：bootstrap 同时扫 task 的 brief 文本，命中 refactor 语义词（`替换|去重|抽取|行为不变|逐字对齐|N 处可替换|refactor|extract`，初版列表，随实践迭代）但 task 未标 `Type: refactor` 时，**强制** `audit_required = true`（不是警告——警告可被 implementor 忽略，§5.2 漏检未真正缓解）。

> **为什么强制而非警告**：这次缺陷正是 plan 作者（controller）没意识到 brief 有问题才没标 Type。纯显式标记会漏；关键词强制兜底是「作者忘了标但 brief 措辞露馅」的安全网。代价是新功能 task 若措辞偶合 refactor 关键词（如「替换 placeholder」）会被误判强制 AUDIT，多花几十秒（可接受——见 §5.3 成本）。

**触发判定逻辑**（bootstrap 实现）：
```
audit_required = (task.Type === 'refactor') OR (brief 含 refactor 关键词)
```

> **大小写归一化**：frontmatter 解析时，对 `Type` 字段值做 `trim().toLowerCase()` 后再比较。避免 `Refactor`、`REFACTOR` 因大小写失效。

> **双层扫描职责区分**：第一层是 bootstrap agent 读取 `Type` 字段并扫描 brief 关键词（主路径，利用 LLM 理解能力）；第二层是 bootstrap 输出后的**确定性正则校验 guard**（运行时兜底，不依赖 LLM）。两者不是重复：前者负责生成结构化状态，后者负责验证前者没有被幻觉或 frontmatter 解析错误绕过。第二层只校验第一层的输出，不替代第一层的解析。
>
> **诚实边界（Code Review 2026-07-08）**：两层共享相同的关键词列表盲点（非标准措辞如「统一构造」代替「去重」会两层都漏）。runtime fallback 只扫 `task.title`/`task.model` 等 frontmatter 可得字段，**不扫 plan 文件 body 内的 brief**（实现 run-plans.js:1490-1491、workflow-design.md §13l 已记录）。故「独立」指职责分层（LLM vs 确定性正则），非覆盖面互补；brief body 含关键词但 title 不含的 case 主要靠 bootstrap LLM。

## 2. AUDIT 清单内容（implementor 现场核查的 5 项）

AUDIT 不是开放式「看看代码对不对」，而是针对 brief 里 5 类常见现状假设的机械核查清单。每类对应一个具体动作 + 明确的「什么算差异」。5 类从 §0 的 7 处缺陷归纳。

| # | brief 假设类型 | 核查动作 | 什么算差异 |
|---|---|---|---|
| A1 | **"N 处可替换"**（site 数量） | 使用 Grep 工具精确搜索 brief 声称的 pattern，数实际命中；避免宽泛正则 | brief 说 4 处，实际 3 处 → 差异 |
| A2 | **"文本完全一致"**（去重前提） | 使用 Read 工具读取各 site 后 diff 待去重文本 | 5 处里 3 类变体 → 差异 |
| A3 | **"行为不变"**（refactor 控制流契约） | 列出控制流关键路径（**判定标准**：重构涉及的代码块里有 `if/return/continue/break/短路/await 顺序` 等改变执行流向的结构，或被调函数的返回值影响调用方分支——这些就是关键路径），trace 重构前后；**使用 Read 工具读取被调函数定义并摘录相关注释** | brief 说 escalate 早 return，实际须 fall-through → 差异 |
| A4 | **"行号/签名引用"**（brief 指向现状） | 使用 Grep 搜索 brief 提到的函数名/签名，核对行号 + 参数；行号漂移仅记录 | brief 说 line 1317，实际 1470 → 漂移（通常无害）；brief 引用的符号不存在 → 缺陷（按 A1 逻辑处理，site 数 0） |
| A5 | **"逐字对齐"**（reason/diag/string 字面量） | 使用 Read 工具提取 brief 给的目标字面量，与现状代码对应字段 diff | brief 的 reasonTemplate 把 status 放尾部，现状放中间 → 差异 |

**关键边界**：
- A4（行号漂移）通常**无害**（前序 task 改了文件，后续行号自然漂）——不阻断。只有「brief 引用的符号不存在」才算缺陷。
- A3（控制流）是唯一不能纯机械核查的项——AUDIT 只要求 implementor「列出路径 + 读注释 + 标注 brief 声明」，**判断仍由 implementor**（它能读注释、读被调函数）。但 A3 有「强制可审计」规则（见 §3.2），即使误判也留下推理记录供下游 review 复查。
- 清单不保证抓所有缺陷——抓 A1/A2/A4/A5 这类「数量/文本/字面量」差异（§0 占 5/7），A3 类语义差异靠 implementor 读代码的判断力 + 下游 review。
- **A1/A2/A4/A5 工具约束**：必须使用 `Grep`（精确搜索）和 `Read`（读取函数/文件内容）等确定性工具；不得假设 shell 可用，也不得用 shell 做字符串处理（跨平台/安全考虑）。在 AUDIT_DIRECTIVE 中给出具体示例："用 Grep 搜索 `bareTaskId` 在 `.claude/workflows/run-plans.js` 中的命中；用 Read 读取 `bareTaskId` 函数定义并核对参数列表。"

## 3. 产出与差异处理流程

### 3.1 产出位置与形式

implementor 在 RED 前先把 AUDIT 结果写到 `.audit/<taskKey>.md`（与 manifest 同级的临时观测文件，不进 git——`.gitignore` 加 `.audit/`）。内容是 §2 清单的 filled 表格：

```markdown
# AUDIT: plan-04/T4 — B2-1 taskKey

| 项 | brief 声明 | 实际 | 差异? |
|---|---|---|---|
| A1 site 数 | 6+ 处拼接 | grep 命中 7 处 | 否 |
| A2 文本一致 | — (非去重) | — | — |
| A3 控制流 | — (新函数无控制流) | — | — |
| A4 行号/签名 | bareTaskId/commitSubject 附近 | line 480/485 | 漂移（无害） |
| A5 逐字对齐 | `plan-${padStart(2,'0')}/${id}` | 7 处逐字一致 | 否 |
```

**`.audit/` 状态管理**：
- 每次 run 对同一个 task 覆盖写入。
- 如果 AUDIT 适用但报告缺失，不得进入 RED（视为无法判定 → needs_audit_fix）。
- `.audit/` 用于当前 session 的调试与 downstream review 复查；跨 session 不保证存在，跨机器恢复仍以 `blocked.md` 和 git 为准。
- `.gitignore` 已忽略 `.audit/`，运行结束后不清理也不会污染 git，但建议由 manifest 目录清理策略管理。

### 3.2 A3 强制可审计规则

**不管 A3 判断一致与否**，若该 task 涉及控制流重构（A3 适用），`.audit/<taskKey>.md` **必须**包含一段：
- 列出的控制流关键路径（如「escalate 分支 → budget guard」）
- brief 对该路径的声明（如「escalate 在 osc 块内 return」）
- **使用 Read 工具读取被调函数定义后摘录的相关注释/摘要**（如「resolveReviewBudget 注释：升 opus 后跑直到 budget 耗尽」）
- implementor 的判断（一致 / 不一致 + 理由）

**目的**：A3 语义漏检（implementor 误判为一致，见 §5.1）无法靠机制阻止，但留下推理记录让下游 SDD task reviewer / final review 能复查 implementor 当时的判断过程，抓到不一致。Task 13 那种 bug 如果 A3 报告里写了完整推理（「我读了注释说跑直到 budget 耗尽，但 brief 说早 return，我认为...」），下游 review 更容易发现矛盾。

### 3.3 差异分级响应

| 差异类型 | implementor 动作 | 是否阻断 RED |
|---|---|---|
| **无差异 / 仅 A4 行号漂移** | 进 RED | 否 |
| **A1/A2/A5 差异，且 implementor 能判定为「有意变体」**（如 Task 10 的 `（非 failed/blocked）`——读 schema enum 确认是设计意图） | 报告标注「有意变体，保留原文 + 理由」（若该 task A3 适用，A3 强制可审计规则同样适用），进 RED | 否 |

**「有意变体」判定标准**：implementor 必须在 `.audit/<taskKey>.md` 中给出证据——读到的 schema 字段、注释、代码逻辑等，能解释为什么 brief 的简化说法与现状不完全一致但仍合理。仅凭"感觉"不算有意变体；拿不准则必须按「无法判定」处理。该判定同样写入 A3 报告（若适用）。
| **A1/A2/A3/A5 差异，implementor 判定为「brief 缺陷」**（如 Task 8 reason 把 status 放中间、Task 13 控制流） | **STOP**，状态报 `needs_audit_fix`，差异清单交 controller | **是** |
| **implementor 无法判定**（拿不准是有意变体还是缺陷） | **STOP**，状态报 `needs_audit_fix`，交 controller | **是** |
| **工具执行失败**（Grep/Read 报错）或 **`.audit/` 写入失败** | **STOP**，状态报 `needs_audit_fix`，标注「无法执行核查」 | **是** |

**为什么「拿不准也阻断」**：这次 Task 13 如果 implementor 拿不准 fall-through 是不是必须、选择了「按 brief 字面写」（早 return），就会引入无限循环 bug。拿不准时阻断比强行实现安全。

**为什么「工具/写入失败也阻断」**：如果核查无法执行，implementor 没有可靠依据判断 brief 是否与现状一致，继续 RED 等同于盲跑。必须 halt 让 controller 介入。

## 4. 落地改动（仅 run-plans workflow 自身，不动全局 writing-plans skill）

### 4.1 implementor prompt 条件注入

- PROMPTS.implementor 加 `{{auditDirective}}` 占位符（插在 `{{retryNote}}` 之后、TDD 指示之前）。
- buildPrompt 默认 `auditDirective: ''`（空串 → 占位消失，非 refactor task 零影响，与 QUOTA_HALT_NOTE 的 opt-out 语义一致）。
- refactor 类 task 时，runTask dispatch implementor 传 `auditDirective: AUDIT_DIRECTIVE`。
- `AUDIT_DIRECTIVE` 是 lib.js 常量（与 QUOTA_HALT_NOTE 同区），内容：一段固定指令 + §2 的 5 行表格模板 + §3 的差异分级规则摘要 + 工具约束（Grep/Read，不 shell）。

### 4.2 bootstrap 解析 Type + 关键词强制兜底

- bootstrap agent prompt（已解析 plan frontmatter 的那段）加一步：读每个 task 的 `Type` 字段（小写化后）、扫 brief refactor 关键词，按 §1 逻辑判定 `audit_required`，写进 `state.perTask[taskKey].audit_required`。
- runTask dispatch implementor 时：`auditDirective: state.perTask[tk].audit_required ? AUDIT_DIRECTIVE : ''`。
- 在 bootstrap 输出后加一道**确定性正则校验**：对 brief 文本用 `/(替换|去重|抽取|行为不变|逐字对齐|N 处可替换|refactor|extract)/i` 再扫一遍，命中但 `audit_required` 为 false 时强制设为 true（作为 bootstrap 的 LLM 输出 guard）。
- **runTask 运行时兜底**：如果 `state.perTask[tk].audit_required` 缺失或 bootstrap 未设置，runTask 在 dispatch implementor 前用同样的确定性正则重新计算 `audit_required`（从 brief 文本 + frontmatter Type 字段）。这样即使 bootstrap 完全漏读 frontmatter，runtime 也不会让 refactor task 无 AUDIT 通过。运行时的计算与 bootstrap 的 LLM 输出是两层职责分层的 guard（LLM 理解 vs 确定性正则），共享相同关键词盲点，runtime fallback 覆盖面限于 frontmatter 可得字段（见 §1 诚实边界）。

### 4.3 SCHEMAS + dispatchImpl + haltLikelySource

- SCHEMAS.implementor status enum 加 `'needs_audit_fix'`（sentinel，与 `needs_context` 同层——都是「需要外部介入」）。sentinel 同时带一个 `audit_reason` 字段，枚举：`brief_defect` / `intentional_variant_unclear` / `tool_failure`；外加一个 `taskKey` 字段（string，plan-scoped key 如 `plan-04/T4`），needs_audit_fix 时回传，供 blocked.md 定位 `.audit/<taskKey>.md` 报告。
- dispatchImpl 状态检查加分支：`if (impl.status === 'needs_audit_fix') return { halted: true, reason: 'audit fix needed', diag: impl.diagnostics }`。`diag` 必须包含 `audit_reason`。
- haltLikelySource 映射加 `audit fix needed → unknown`（audit 差异不涉及工作树脏状态）。
- 同步更新 `blocked.md` 诊断文案：按 `audit_reason` 给出不同 actionable 提示——`brief_defect` 提示修 brief；`intentional_variant_unclear` 提示确认是否确实是有意变体；`tool_failure` 提示检查文件系统/工具可用性。`needs_audit_fix` 根因说明：brief 与现状代码不一致或无法完成核查。

### 4.4 改动文件清单

| 文件 | 改动 |
|---|---|
| lib.js + run-plans.js inline | PROMPTS.implementor 加 `{{auditDirective}}` 占位；加 `AUDIT_DIRECTIVE` 常量；SCHEMAS.implementor enum 加 `needs_audit_fix` + `audit_reason`；加 `AUDIT_REFACTOR_KEYWORDS` 常量 |
| run-plans.js runTask | dispatch implementor 时按 `audit_required` 传 auditDirective；加 runtime 确定性兜底计算 `audit_required`；dispatchImpl 加 `needs_audit_fix` halt 分支 |
| run-plans.js haltLikelySource（lib.js inline） | 加 `audit fix needed → unknown` 映射 |
| lib.js bootstrap prompt | 加「读 task Type + 扫 refactor 关键词 → audit_required」指示；加确定性正则二次校验 |
| .gitignore | 加 `.audit/` |
| workflow-design.md | 新增 §13l「AUDIT 阶段」：流程图、状态机、与 review/halt/resume 交互；§5 加 AUDIT 阶段说明；§13b implementor 行加「refactor 类 task 先 AUDIT」 |
| blocked.md 模板 | 加 `audit fix needed` 按 `audit_reason` 分类的诊断说明 |

## 5. 局限（诚实边界）

> **两类问题划清边界**：§5.1/§5.2 是**漏检问题**（该 halt 的没 halt——implementor 误判为一致，或 AUDIT 根本没触发）；§5.3/§5.4 是**频率/成本问题**（halt 触发了，但担心太多/太贵）。§5.4 的缓解（多数无差异通过、SDD 计划中的 controller 支持）只对**频率问题**有效——它们降低「halt 触发时的代价」，但对「没触发 halt 导致漏检」无效（halt 没发生，controller 没机会介入，"多数无差异"说的是别的 task）。别误用 §5.4 缓解到 §5.1/§5.2。

### 5.1 语义级控制流 bug：AUDIT 不阻止，靠 A3 可追溯 + 下游 review

A3 是清单里唯一不能纯机械核查的项。这次 Task 13 的 fall-through bug 靠的是读 `resolveReviewBudget` 注释的意图——AUDIT 只能要求 implementor「列出路径 + 读注释 + 标注」，判断是否一致仍靠 implementor 读代码。**若 implementor 误判为一致（没读出注释意图），bug 会漏到实现阶段**。

**缓解**（§3.2 A3 强制可审计）：不管判断一致与否，A3 推理过程必须写进报告，下游 SDD task reviewer / final review 能复查。**这是「让漏检可追溯 + 给下游抓手」，不是「阻止 bug 进入实现」**。NEEDS_AUDIT_FIX 不覆盖此 case（它是「判定为缺陷/拿不准」才触发，误判为一致时不触发）。

### 5.2 触发漏检：靠关键词强制兜底，但关键词列表需迭代

显式标记是主路径，但作者可能没意识到才没标。**缓解**：§1 关键词**强制**触发（命中即 audit_required=true，非警告）。但作者若用非标准措辞（如「统一构造」代替「去重」）会漏检。**初版关键词列表**从 §0 的 7 处缺陷归纳，后续发现漏检再补。NEEDS_AUDIT_FIX 不覆盖此 case（AUDIT 不跑它也不跑）。

**关键词迭代机制**：每次发现漏检案例后，由 controller 在 24h 内更新 lib.js 的 `AUDIT_REFACTOR_KEYWORDS` 常量，并补一个 REGRESSION 测试（helpers.test.js 中），确保同一措辞下次触发。迭代记录写入 workflow-design.md §5。

### 5.3 成本：每 refactor task 多一次文件扫描

AUDIT 的 A1/A2/A4/A5 是 Grep/Read 机械核查，多耗时几秒到几十秒，但每 refactor task 都跑。对重构密集 plan（如这次的 8 个 Batch 2 task）累计不可忽略。**缓解**：AUDIT 在 implementor 内部跑（不额外派 agent），成本是多读几个文件，远低于「派错返工」的成本（1-3 轮 review 重跑 + 可能的 OSCILLATING）。

**token 成本估算**：AUDIT_DIRECTIVE 指令本身约 300-500 token；A1/A2/A4/A5 每个 refactor task 通常多读 1-3 个文件，约 500-2000 token。对于已接近 sonnet 槽 262k 限制的大 refactor task，总 prompt 可能逼近上限，触发 §2.4 的 retryModel='opus'。实现前应估算总 prompt 长度，必要时把大 brief 拆分或先 audit 再进入完整 TDD。

### 5.4 `needs_audit_fix` 是人工 gate，与全自动模式张力

run-plans 标榜「全自动执行 plan」。AUDIT 引入可能的人工介入点。**当前状态**：`needs_audit_fix` 是人工 gate——controller 需要读差异、判断、修 brief、然后 resume。缓解方向（未来探索，不在本计划内）：可以设计一个 SDD controller agent 自动处理（读差异 + 判断 + 修 brief + resume），但该 controller 的输入/输出/状态机尚未定义，本计划不落地。§5.4 的缓解（1）多数 refactor task 的 AUDIT 会无差异通过；（2）halt 的 blocked.md 清楚列差异，人介入成本低。

### 5.5 不适用于纯新功能 task（YAGNI）

§1 触发机制（仅 refactor 类 + 关键词命中）已排除纯新功能 task。关键词误判（新功能 task 偶合 refactor 词）会触发 AUDIT 但几十秒后无差异通过，可接受。

## 6. 测试策略

### 6.1 lib.js 纯函数测试（helpers.test.js）

- `AUDIT_DIRECTIVE` 常量导出 + 内容断言（含 5 行表格模板关键词：A1/A2/A3/A4/A5 + `needs_audit_fix` + 工具约束）
- `haltLikelySource('audit fix needed') === 'unknown'`
- 新增 `AUDIT_REFACTOR_KEYWORDS` 常量命中测试：命中 `替换`、`refactor`、`extract` 等词时返回 true；纯 feature 词返回 false
- A3 报告格式测试：给定含控制流重构的 brief，生成的 `.audit/<taskKey>.md` 必须包含「关键路径」「brief 声明」「注释/被调函数摘要」「判断」四段
- **buildPrompt 占位符测试**：`buildPrompt('implementor', { auditDirective: AUDIT_DIRECTIVE })` 渲染后包含 AUDIT 指令；`buildPrompt('implementor', { auditDirective: '' })` 渲染后无 AUDIT 指令（空串替换占位符）。注：因 `auditDirective` 进 buildPrompt defaults（默认空串），`buildPrompt('implementor', {})` 等价于传空串 → 无 AUDIT 指令、无 `{{auditDirective}}` 残留（非 refactor task prompt 清洁）。不复测 `{}` 的占位符保留行为——它与 defaults 设计冲突，且占位符残留会污染 prompt。

### 6.2 sync.test 守护

- PROMPTS.implementor 两端字节一致（含新 `{{auditDirective}}` 占位）
- `AUDIT_DIRECTIVE` lib.js ↔ run-plans.js 字节一致（QC-4 或 QC-4b 模式）
- SCHEMAS.implementor enum 两端一致 + 含 `needs_audit_fix`
- bootstrap prompt 两端一致 + 含「读 task Type + 扫 refactor 关键词」指示 + 关键词正则二次校验

### 6.3 bootstrap audit_required 判定（源码字面量断言，sync.test）

- bootstrap prompt 含「读 task Type + 扫 refactor 关键词 → audit_required」指示
- runTask dispatch implementor 处含 `auditDirective: state.perTask[tk].audit_required ? AUDIT_DIRECTIVE : ''`
- Type 字段小写化比较的字面量存在（如 `.toLowerCase()`）
- **runtime fallback 测试**：构造 `state.perTask[tk]` 缺失 `audit_required` 的 task，断言 runTask 用正则重新计算为 true；构造 `audit_required=false` 但 brief 含关键词的 task，断言 runtime 兜底修正为 true

### 6.4 dispatchImpl needs_audit_fix halt（源码字面量断言，dispatchImpl-retry.test.js）

- dispatchImpl 状态检查含 `needs_audit_fix` 分支 + 返回 halt
- 返回的 `diag` 包含 taskKey、差异清单、被影响项
- blocked.md 模板含 `audit fix needed` 诊断文案

### 6.5 回归测试基线

- 改动前跑 `node --test docs/superpowers/workflows/tests/*.test.js`（从仓库根）作为基线，全部通过（当前基线 350 green）。
- 新增测试按 RED→GREEN→REFACTOR 分阶段提交；每阶段跑 workflow 测试确保不破坏现有 350 个测试和 workflow 测试。
- 实现完成后用 mini-plan（含至少 1 个 refactor task 和 1 个 feature task）跑端到端验证，确认：(a) refactor task 触发 AUDIT；(b) feature task 不触发；(c) 故意写错 brief 的 refactor task 触发 needs_audit_fix halt。

**注**：AUDIT 清单本身的执行（implementor 实际跑 Grep/Read）无法单测（依赖真实代码状态），靠 implementor prompt 指示 + 端到端。这是 layered-test 固有盲区（与 HIGH-1 同类）。

## 7. 决策记录

| ID | 决策 | 理由 |
|---|---|---|
| D1 | 仅 refactor 类 task 触发 | §0 实测 7/7 缺陷在 refactor 类；纯新功能 task 无现状假设 |
| D2 | 显式标记 + 关键词强制兜底（非警告） | 纯显式会漏（作者没意识到）；警告可被忽略；强制才真兜底 |
| D3 | AUDIT 只收集证据，implementor 判断 | 「有意变体 vs 缺陷」需语义判断（读 schema/注释）；机械部分只负责收集 |
| D4 | A3 强制可审计（不管判断一致与否写报告） | §5.1 语义漏检无法阻止，但可追溯 + 给下游 review 抓手 |
| D5 | implementor 现场跑 + 临时报告（.audit/） | 比 plan 作者预写清单灵活（作者也可能有错假设）；报告不进 git |
| D6 | 改 run-plans workflow 自身，不动 writing-plans skill | AUDIT 是本项目实践总结，改全局 skill 影响面大且未必适合所有项目 |
| D7 | 方案 A（implementor prompt 内嵌）非独立 subagent / reviewer 补查 | 零新 agent、零新 gate、与现有模型一致；方案 B gate 太重，方案 C 太晚 |
| D8 | `needs_audit_fix` 复用现有 halt/blocked.md/resume | 不引入新恢复路径；controller 修 brief 后 resume，bootstrap 重读重审 |
| D9 | Type 字段小写化归一 | 避免 `Refactor`/`REFACTOR` 漏触发；与 keyword 正则 `i` 标志一致 |
| D10 | `.audit/` 覆盖写入 + 缺失报告不得 RED | 防止旧报告误导、防止报告缺失仍盲跑 |
| D11 | 工具/写入失败 → needs_audit_fix | 无法执行核查时不能继续 RED；安全优先 |
| D12 | A3 必须 Read 被调函数定义 | 不能只 grep 函数名；读定义才能摘录注释 |
| D13 | 关键词列表迭代由 controller 24h 内补 REGRESSION 测试 | 关键词是经验列表，需持续迭代，每次漏检必须留下测试 |
| D14 | （编号预留，无对应决策——复核时发现 D13→D15 跳号，保留跳号避免重排打乱后续引用） | — |
| D15 | 运行时确定性兜底计算 audit_required | 防止 bootstrap 漏读 frontmatter 导致 refactor task 无 AUDIT 通过 |
| D16 | needs_audit_fix 带 audit_reason 分类 | 让 blocked.md 给出更具体的 actionable 诊断 |
| D17 | AUDIT_DIRECTIVE 明确指定 Read/Grep 工具名 | 避免 implementor 因工具名不确定而误用 shell 或错误工具 |
| D18 | 有意变体必须有 schema/注释/代码证据 | 减少 implementor 主观判断不一致 |
| D19 | workflow-design.md 新增 §13l AUDIT 阶段 | 完整记录 AUDIT 流程、状态机、与现有机制交互 |
| D20 | 不设置硬性 token 阈值，保持定性说明 | 不同 refactor task 大小差异大，硬性阈值可能误伤或误导；靠运行时 retryModel 兜底 |
| D21 | buildPrompt 占位符三态测试 | 确保 provided/空串/缺失三种行为正确 |
| D22 | runtime fallback 源码字面量测试 | 确保 runTask 在 bootstrap 输出异常时仍能正确计算 audit_required |

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 1 | issues_open | HOLD SCOPE; 14 findings, 0 critical gaps |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | not_installed | Outside voice via Claude subagent: 12 findings, 2 cross-model tensions resolved |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 0 | — | not run — recommended next |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | SKIPPED (no UI scope) |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | not run — optional |

- **CODEX:** Codex CLI not installed (`not_installed`). Used Claude subagent as outside voice. 12 findings; 2 substantive tensions presented to user.
- **CROSS-MODEL:** Tension #1 (A3 reviewer forced verification) — review said keep trace-only, outside voice said force reviewer to read `.audit/`; user chose **trace-only**. Tension #2 (SDD controller auto-processing) — outside voice said undefined vision; user chose **fix §5.4 honesty**.
- **VERDICT:** CEO CLEARED — HOLD SCOPE accepted. **Eng review required** before implementation (`/plan-eng-review`).

NO UNRESOLVED DECISIONS

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 1 | issues_open | HOLD SCOPE; 14 findings, 0 critical gaps |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | not_installed | Outside voice via Claude subagent: 12 findings, 2 cross-model tensions resolved |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | issues_open | FULL_REVIEW; 9 issues folded into plan, 0 critical gaps |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | SKIPPED (no UI scope) |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | not run — optional |

- **CODEX:** Codex CLI not installed (`not_installed`). Used Claude subagent as outside voice. 12 findings; 2 substantive tensions presented to user in CEO review.
- **CROSS-MODEL:** No new cross-model tension in Eng review.
- **VERDICT:** CEO + ENG CLEARED — ready to implement (pending execution). Design/DX not required.

NO UNRESOLVED DECISIONS