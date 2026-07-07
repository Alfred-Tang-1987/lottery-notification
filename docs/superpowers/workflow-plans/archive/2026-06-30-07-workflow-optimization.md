---
models:
  T1: sonnet
  T2: sonnet
  T3: sonnet
  T4: sonnet
  T5: sonnet
  T6: sonnet
---

# 07 Workflow 优化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 借鉴 flow-kit 的防错机制，增强 run-plans workflow 的 6 项能力：implementor 结构化自审、跨 session 失败方案追踪、write_files 边界控制、破坏性变更检测、LESSONS.md 跨任务知识库、schema 迁移一致性检查。所有增强保持通用性——项目特有内容走 config，prompt 保持单一模板。

**Architecture:** 改动集中在 3 个文件：`docs/superpowers/workflows/lib.js`（纯函数真源）→ `.claude/workflows/run-plans.js`（inline 副本 + orchestrator 胶水）→ `docs/superpowers/workflows/tests/*.test.js`（TDD 测试 + sync 护栏）。可选 config 字段扩展 `workflow.config.json`。

**Tech Stack:** node:test（纯函数测试）、JS template literals（prompt 模板）、JSON Schema（agent 输出约束）。

**核心约束：**
1. **lib.js ↔ run-plans.js 同步**：PROMPTS / SCHEMAS / helpers 必须逐字一致，sync.test.js 守护
2. **向后兼容**：所有新 config 字段可选——旧 config 无它们照跑（条件渲染：orchestrator 传空串，相关 prompt 段消失）
3. **通用性**：prompt 不含项目特定硬编码（彩种/domain 纪律等只走 config 注入）
4. **bootstrap prompt 累积修改**：T2/T3/T5 都修改 `PROMPTS.bootstrap`，须按 task 顺序累积应用，不可覆盖前序 task 的改动
5. **implCtx 工厂扩展方式**：新增 `failedApproaches` / `lessons` 等字段时，在 `implCtx` 工厂**函数体内**从 `state` 读取（如 `state.failedApproaches?.[task.id]`），**不新增工厂参数**——避免改动 7+ 处 call site
6. **state 新字段初始化**：T2/T3/T5 引入 `state.failedApproaches` / `state.taskWriteFiles` / `state.taskLessons`，须在 state 定义处（`run-plans.js` 约 line 541）显式初始化为 `{}`
7. **新占位符位置**：implementor prompt 的新占位符（`{{failedApproaches}}` / `{{lessons}}`）须**新起一行**，同 `{{referencePaths}}` 模式（独立行，条件渲染时空行无副作用）

---

## File Structure

```
docs/superpowers/workflows/
├── lib.js                        # 纯函数真源（新增 helpers + 修改 PROMPTS/SCHEMAS）
├── tests/
│   ├── self-review.test.js       # T1: 6 维自审清单（纯 prompt 断言，不扩展 sync.test.js）
│   ├── failed-approaches.test.js # T2: 失败方案追踪
│   ├── boundary-control.test.js  # T3: write_files 边界
│   ├── destructive-check.test.js # T4: 破坏性变更检测
│   ├── lessons.test.js           # T5: LESSONS.md
│   ├── schema-migration.test.js  # T6: schema 迁移检查
│   └── sync.test.js              # T2-T6 各自扩展：新增 helpers/wiring 同步断言
.claude/workflows/
└── run-plans.js                  # inline 副本 + orchestrator 胶水（同步 + 新增 wiring）
workflow.config.json              # 可选新字段：lessons_path / schema_tool / model_paths / migration_paths（共 4 个）
```

---

## Task 1: Implementor 6 维自审清单

**目标：** 在 implementor prompt 的 self-review 段增加结构化 6 维快查清单（认知过载/变更传播/知识重复/偶然复杂/依赖混乱/领域扭曲），让低级问题在 implementor 阶段就暴露，不浪费 expensive review rounds。

**文件：** `docs/superpowers/workflows/lib.js`、`.claude/workflows/run-plans.js`、`docs/superpowers/workflows/tests/self-review.test.js`

### Steps

- [ ] 1. **RED**：新建 `tests/self-review.test.js`，写测试断言 `PROMPTS.implementor` 包含 6 维关键词（Cognitive Overload / Change Propagation / Knowledge Duplication / Accidental Complexity / Dependency Disorder / Domain Distortion），且每维带可操作检查问题
- [ ] 2. 跑 `cd docs/superpowers/workflows && node --test tests/self-review.test.js` 确认失败（prompt 尚无 6 维清单）
- [ ] 3. **GREEN**：在 `lib.js` 的 `PROMPTS.implementor` 的 `## Self-Review Checklist` 段，在现有 4 条检查项后追加 `## 6-Dimension Quick Check (before reporting)` 段，含 6 维 × 各 1 句检查问题：
  - Cognitive Overload: any function > 50 lines or nesting > 3 levels?
  - Change Propagation: did you change files unrelated to this task?
  - Knowledge Duplication: did you paste similar logic in 2+ places?
  - Accidental Complexity: did you add abstraction not needed by current requirements?
  - Dependency Disorder: any business layer importing infrastructure implementation?
  - Domain Distortion: are variable names domain terms, not generic (data/item/info)?
  - 发现问题 → 先修再报 ok；未发现 → evidence 中隐含 self_review passed
- [ ] 4. 跑 `node --test tests/self-review.test.js` 确认绿
- [ ] 5. **SYNC**：将 `PROMPTS.implementor` 的修改逐字同步到 `run-plans.js`
- [ ] 6. **REFACTOR**：跑 `node --test tests/sync.test.js` 确认 PROMPTS.implementor 两副本一致；跑全量 `node --test tests/*.test.js` 确认无回归。**注意：本 task 不扩展 sync.test.js**（无新 helper / wiring / schema 字段），仅验证现有 sync 断言不被破坏

### Verify

```bash
cd docs/superpowers/workflows && node --test tests/self-review.test.js tests/sync.test.js
```

### Done

6 维清单出现在 implementor prompt 中；sync.test.js 绿；全量测试绿。

---

## Task 2: 跨 session 失败方案追踪

**目标：** bootstrap 扫描 `runs/*/manifest.json`（结构化 JSON，非人读 markdown）提取历史失败方案，注入 implementor prompt，防止跨 session 重复相同失败路径。finalReport halt 时在 blocked_info 中记录 `failed_approach` 字段。

**文件：** `docs/superpowers/workflows/lib.js`、`.claude/workflows/run-plans.js`、`docs/superpowers/workflows/tests/failed-approaches.test.js`

**关键设计决策：**
- **数据源**：扫 `runs/*/manifest.json` 的 `per_task[<taskId>].blocked_info`（已是 JSON 结构），**不扫 `blocked.md`**（人读 markdown，正则提取脆弱）
- **schema 位置**：`failed_approaches` 作为 `evidence.properties` 的**扁平字段**（同 `completed` / `dirty_tree` 模式），不嵌套在 `plans.items.properties.tasks` 内（现有 `plans: { type: 'array' }` 无 items 嵌套）
- **命名统一**：evidence 数组用 `failed_approaches`（复数）；blocked_info 单条用 `failed_approach`（单数）——两者语义不同，sync.test.js 分别断言

### 前置：formatFailedApproaches helper

- [ ] 1. **RED**：新建 `tests/failed-approaches.test.js`，测试 `formatFailedApproaches([])` → `''`，`formatFailedApproaches([{task_id:'T1', reason:'implementor failed after retry', error:'timeout'}])` → 含 task_id + reason + error 的可读段落
- [ ] 2. 跑确认失败（函数不存在）
- [ ] 3. **GREEN**：在 `lib.js` 新增 `formatFailedApproaches(items)`：
  - 空 → 空串（prompt 段消失）
  - 非空 → `## Prior Failed Approaches (do not repeat)` 标题 + 每条 `- T1: reason — error` 列表 + `If your plan is similar to any above, explicitly state the difference.`
- [ ] 4. 跑确认绿

### bootstrap prompt 扩展

- [ ] 5. **RED**：扩展测试，断言 `PROMPTS.bootstrap` 包含 `manifest.json` 扫描步骤和 `failed_approaches` 返回要求
- [ ] 6. 跑确认失败
- [ ] 7. **GREEN**：在 `PROMPTS.bootstrap` Steps 末尾追加（Step 7）：
  - `7. If runs/ directory exists: scan runs/*/manifest.json files. For each, read per_task object. For each task_id in per_task that has blocked_info, extract {task_id, reason (from blocked_info.reason), error (from blocked_info.last_error)}. Filter to task_ids that match leaf tasks in the current plans. Return as failed_approaches in evidence. If runs/ does not exist → failed_approaches=[].`
  - evidence 结构说明追加 `failed_approaches:[{task_id, reason, error}]`
  - 注意：Return 语句（lib.js 约 line 363）也要追加 `failed_approaches` 字段
- [ ] 8. 在 `SCHEMAS.bootstrap` 的 `evidence.properties` 中追加 `failed_approaches: { type: 'array' }`（扁平字段，同 completed/dirty_tree 层级）
- [ ] 9. 跑确认绿

### implementor prompt 扩展

- [ ] 10. **RED**：扩展测试，断言 `PROMPTS.implementor` 包含 `{{failedApproaches}}` 占位符（独立行，同 `{{referencePaths}}` 模式）
- [ ] 11. **GREEN**：在 `PROMPTS.implementor` 的 `{{referencePaths}}` 行后，**新起一行**追加 `{{failedApproaches}}` 占位符（条件渲染：空串时该行消失）
- [ ] 12. 跑确认绿

### finalReport prompt 扩展

- [ ] 13. **RED**：扩展测试，断言 `PROMPTS.finalReport` 在 halted 模式下将 `failed_approach` 渲染到 blocked.md 的字段列表中
- [ ] 14. **GREEN**：在 `PROMPTS.finalReport` 的 halted 分支，将 `failed_approach` **追加到现有 blocked_info 字段列表**（plan, task, reason, category, last_error, suggested_fix, quota_exhausted, likely_source, **failed_approach**），不新建独立 section。渲染格式：`Failed Approach: <failed_approach.task_id>: <failed_approach.reason> — <failed_approach.error>`（与现有字段列表一致）
- [ ] 15. 跑确认绿

### orchestrator 胶水（run-plans.js only）

- [ ] 16. 在 `run-plans.js` 的 state 定义处（约 line 541）显式初始化 `failedApproaches: {}`
- [ ] 17. 在 `run-plans.js` 的 bootstrap evidence 处理后：将 `evidence.failed_approaches` 按 `task_id` 索引存入 `state.failedApproaches`（如 `{T1: [{task_id, reason, error}], T2: [...]}`）
- [ ] 18. 在 `run-plans.js` 的 `implCtx` 工厂**函数体内**（不新增参数）：读取 `state.failedApproaches?.[task.id]`，调用 `formatFailedApproaches(...)` 并设为 `failedApproaches` 字段。这样 7+ 处 call site 无需改动
- [ ] 19. 在 `run-plans.js` 的 `halt()` 中（约 line 559，签名 `halt(plan, task, r)` 无 cfg 参数）：从 `state.config` 读 config，将 `failed_approach: {task_id: tid, reason: r.reason, error: r.diag?.last_error || r.reason}` 追加到 `blocked_info` 对象
- [ ] 20. **SYNC**：所有 lib.js 改动（PROMPTS / SCHEMAS / formatFailedApproaches）逐字同步到 run-plans.js
- [ ] 21. **REFACTOR**：扩展 `sync.test.js`：在 helper 存在性列表加 `formatFailedApproaches`；在 wiring 断言加 `failedApproaches:` 占位符接线；在 bootstrap schema 断言加 `failed_approaches`；在 finalReport wiring 断言加 `failed_approach`
- [ ] 22. 跑全量 `node --test tests/*.test.js` 确认绿

### Verify

```bash
cd docs/superpowers/workflows && node --test tests/failed-approaches.test.js tests/sync.test.js
```

### Done

bootstrap 扫描 `runs/*/manifest.json` 提取失败方案；implementor 收到 `{{failedApproaches}}` 注入；finalReport halt 时在 blocked_info 记录 `failed_approach`；sync.test.js 绿。

---

## Task 3: write_files 边界控制

**目标：** plan frontmatter 可选声明 `write_files`（每 task 允许修改的文件列表），commit agent 提交前检查 `git diff --name-only` 是否越界。不声明时跳过检查（向后兼容）。越界时 commit agent 返回 failed，orchestrator halt 并由后续全新跑的 implementor 重新实现。

**文件：** `docs/superpowers/workflows/lib.js`、`.claude/workflows/run-plans.js`、`docs/superpowers/workflows/tests/boundary-control.test.js`

**关键设计决策：**
- **frontmatter 解析**：现有 bootstrap prompt 只读 `models` key。需扩展 frontmatter 解析逻辑，读 `write_files` key（格式 `write_files: {T1: [src/a.py], T2: [...]}`）
- **evidence 结构**：`write_files` 作为 `evidence.properties` 的扁平字段 `task_write_files: [{task_id, files: [...]}]`（不嵌套在 plans.items 内）
- **越界处理**：commit agent 检测越界 → `status=failed` + `diagnostics.out_of_scope` → orchestrator halt（不 revert 工作树——越界改动留在工作树供人工排查，halt 后全新跑 bootstrap 会检测 dirty_tree）
- **不声明时**：`task_write_files` 为空数组 → `formatWriteFilesScope([])` → 空串 → commit agent 跳过检查

### 前置：formatWriteFilesScope helper

- [ ] 1. **RED**：新建 `tests/boundary-control.test.js`，测试 `formatWriteFilesScope([])` → `''`，`formatWriteFilesScope(['src/a.py','src/b.py'])` → 含 `## Write Files Boundary` 标题 + 文件列表 + `git diff must not exceed this scope` 指令
- [ ] 2. 跑确认失败
- [ ] 3. **GREEN**：在 `lib.js` 新增 `formatWriteFilesScope(files)`：
  - 空 → 空串（检查跳过）
  - 非空 → `## Write Files Boundary (commit agent will verify)` 标题 + 每文件 `- <file>` + 指令 `Before committing, run git diff --name-only. If any file is NOT in the list above, you MUST either: 1. revert the out-of-scope change, or 2. report status=failed with out_of_scope in diagnostics.`
- [ ] 4. 跑确认绿

### bootstrap prompt 扩展

- [ ] 5. **RED**：扩展测试，断言 `PROMPTS.bootstrap` 提到 `write_files` frontmatter 读取
- [ ] 6. **GREEN**：在 `PROMPTS.bootstrap` Step 3 追加：`Also read write_files from frontmatter if present (format: "write_files:\n  T1:\n    - src/a.py\n    - src/b.py"). Return as task_write_files in evidence: [{task_id, files:[...]}]. Absent → empty array.`
  - evidence 结构追加 `task_write_files:[{task_id, files:[...]}]`（扁平字段）
  - Return 语句也要追加 `task_write_files`
- [ ] 7. 在 `SCHEMAS.bootstrap` 的 `evidence.properties` 中追加 `task_write_files: { type: 'array' }`（扁平字段）
- [ ] 8. 跑确认绿

### commit prompt 扩展

- [ ] 9. **RED**：扩展测试，断言 `PROMPTS.commit` 包含 `{{writeFilesScope}}` 占位符和边界检查步骤
- [ ] 10. **GREEN**：在 `PROMPTS.commit` 的 Inputs 行追加 `writeFilesScope={{writeFilesScope}}`；在 Steps 的 `git add -A` 之前插入：
  - `If writeFilesScope is non-empty: run git diff --name-only. Compare with writeFilesScope. If any file is out of scope → status=failed, diagnostics.out_of_scope=[<files>]. Do NOT commit.`
- [ ] 11. 在 `SCHEMAS.commit` 的 `diagnostics.properties` 追加 `out_of_scope: { type: 'array' }`
  - 注意：现有 `diagnostics: { type: 'object' }` 无 properties，需改为 `diagnostics: { type: 'object', properties: { out_of_scope: { type: 'array' } } }`
- [ ] 12. 跑确认绿

### orchestrator 胶水（run-plans.js only）

- [ ] 13. 在 `run-plans.js` 的 state 定义处显式初始化 `taskWriteFiles: {}`
- [ ] 14. 在 bootstrap evidence 处理后：将 `evidence.task_write_files` 按 `task_id` 索引存入 `state.taskWriteFiles`
- [ ] 15. 在 `run-plans.js` 的 `runTask` 中：commit 派发时在 commit ctx 传入 `writeFilesScope: formatWriteFilesScope(state.taskWriteFiles?.[task.id] || [])`
- [ ] 16. commit 返回 `status=failed` + `out_of_scope` 时 → halt `commit out_of_scope`（不 revert 工作树——越界改动留供人工排查，halt 后全新跑 bootstrap 会检测 dirty_tree 并在 summary 中报告）
- [ ] 17. **SYNC**：所有 lib.js 改动逐字同步到 run-plans.js
- [ ] 18. **REFACTOR**：扩展 `sync.test.js`：helper 列表加 `formatWriteFilesScope`；wiring 断言加 `writeFilesScope:` 和 `out_of_scope`
- [ ] 19. 跑全量确认绿

### Verify

```bash
cd docs/superpowers/workflows && node --test tests/boundary-control.test.js tests/sync.test.js
```

### Done

plan frontmatter 可选声明 write_files；commit agent 越界检测；不声明时跳过（向后兼容）；越界时 halt 不 revert（工作树留供排查）；sync.test.js 绿。

---

## Task 4: 破坏性变更检测

**目标：** commit agent 提交前检测破坏性变更（删代码 ≥5 行 / 改公共导出签名 / 删文件），命中时在 diagnostics 标记 `destructive_changes`，orchestrator 记录到 manifest 并在额外 review round 中确认。commit 已入库，**不 revert、不 halt**——额外 review 失败时仅记录到 manifest 供人工排查，不阻断自动化流程（因为 commit 已在 git history，halt 无法回退）。

**文件：** `docs/superpowers/workflows/lib.js`、`.claude/workflows/run-plans.js`、`docs/superpowers/workflows/tests/destructive-check.test.js`

**关键设计决策：**
- **不 halt 的原因**：commit 已经完成（sha 入 git history），halt 无法回退 commit。续跑时 bootstrap 从 git log 见该 task 已 commit 会跳过。因此额外 review 失败时仅记录到 manifest 的 `destructive_review_failed` 字段，不阻断流程
- **确定性检测**：用 `git diff --cached --numstat` 获取删除行数（第一列是新增，第二列是删除），避免 LLM 手数 diff 行数的不确定性
- **额外 review 不计入 review_rounds 上限**：它是 post-commit 安全网，独立于主 review 循环

### Steps

- [ ] 1. **RED**：新建 `tests/destructive-check.test.js`，测试 `PROMPTS.commit` 包含破坏性变更检测指令（关键词：destructive / deleted code / signature change / file deletion），且 `SCHEMAS.commit` 的 diagnostics 含 `destructive_changes` 字段
- [ ] 2. 跑确认失败
- [ ] 3. **GREEN**：在 `lib.js` 的 `PROMPTS.commit` Steps 中，在 test 运行后、git add 前追加：
  ```
  3.5. Destructive Change Detection: run git diff --cached --numstat. For each file:
    a. If column 2 (deletions) >= 5 AND file is not a test file → record {type:'deleted_code', file, detail:'<N> lines deleted'}
    b. If file is deleted (git diff --cached --name-status shows D) → record {type:'file_deletion', file, detail:'file deleted'}
    c. For exported symbol signature changes: read the diff hunks. If a function/class exported symbol's params or return type changed → record {type:'signature_change', file, detail:'<symbol> signature changed'}
  If any hit → record in diagnostics.destructive_changes: [{type, file, detail}]. Still proceed to commit (status=ok), but orchestrator will trigger an extra review round.
  ```
- [ ] 4. 在 `SCHEMAS.commit` 的 diagnostics.properties 追加 `destructive_changes: { type: 'array' }`
  - 注意：如果 T3 已将 diagnostics 改为 `diagnostics: { type: 'object', properties: { out_of_scope: { type: 'array' } } }`，则在此 properties 中追加 `destructive_changes: { type: 'array' }`
- [ ] 5. 跑确认绿
- [ ] 6. **SYNC**：PROMPTS.commit + SCHEMAS.commit 逐字同步到 run-plans.js
- [ ] 7. **orchestrator 胶水**（run-plans.js only）：在 `runTask` 的 commit 成功后（`status='committed'` 后），检查 `commit.diagnostics?.destructive_changes`：
  - 非空 → log 警告 + 触发额外一轮 review（spec + quality + hunter 并行，同主 review 逻辑，但不计入 `review_rounds` 上限）
  - 额外 review 全绿 → 继续（commit 已完成，不需重新 commit）
  - 额外 review 不绿 → **不 halt**，在 manifest 的 `per_task[taskId]` 中追加 `destructive_review_failed: true` + review findings，继续下一个 task（commit 已在 git history，halt 无法回退）
- [ ] 8. **REFACTOR**：扩展 `sync.test.js`：wiring 断言加 `destructive_changes`；通用性断言加 commit prompt 不含项目硬编码
- [ ] 9. 跑全量确认绿

### Verify

```bash
cd docs/superpowers/workflows && node --test tests/destructive-check.test.js tests/sync.test.js
```

### Done

commit agent 用 `git diff --cached --numstat` 确定性地检测破坏性变更；orchestrator 触发额外 review round；额外 review 失败时记录到 manifest 不 halt；sync.test.js 绿。

---

## Task 5: LESSONS.md 跨任务失败知识库

**目标：** config 可选声明 `lessons_path`（项目级失败知识库文件）。bootstrap 读取并按当前 task 关键词匹配；implementor 收到匹配的 lessons 注入；finalReport halt 时自动追加新 lesson 条目。不配 lessons_path 时完全跳过（向后兼容）。

**文件：** `docs/superpowers/workflows/lib.js`、`.claude/workflows/run-plans.js`、`docs/superpowers/workflows/tests/lessons.test.js`、`workflow.config.json`

**关键设计决策：**
- **evidence 结构**：`lessons` 作为 `evidence.properties` 的扁平字段 `task_lessons: [{task_id, lessons: [{id, title, detail}]}]`（不嵌套在 plans.items 内）
- **finalReport 双模式接线**：finalReport 在 done 和 halted 两种模式下都被调用。`lessonsPath` 必须在**两种模式**下都传入（halted 模式用于追加 lesson，done 模式传入但 prompt 指令仅在 halted 分支执行）
- **halt() 签名**：`halt(plan, task, r)` 无 cfg 参数，从 `state.config.lessons_path` 读取
- **implCtx 扩展**：在工厂函数体内从 `state.taskLessons` 读取，不新增参数

### 前置：formatLessons helper

- [ ] 1. **RED**：新建 `tests/lessons.test.js`，测试 `formatLessons([])` → `''`，`formatLessons([{id:'L-001', title:'split-commit silent miss', detail:'DrawResult+outbox must single-commit'}])` → 含 `## Lessons Learned` 标题 + ID + title + detail
- [ ] 2. 跑确认失败
- [ ] 3. **GREEN**：在 `lib.js` 新增 `formatLessons(items)`：
  - 空 → 空串
  - 非空 → `## Lessons Learned (check against these before implementing)` 标题 + 每条 `- [L-001] title — detail` + `If your plan is similar to any lesson above, explicitly state why your approach differs.`
- [ ] 4. 跑确认绿

### bootstrap prompt 扩展

- [ ] 5. **RED**：扩展测试，断言 `PROMPTS.bootstrap` 提到 `lessons_path` 读取
- [ ] 6. **GREEN**：在 `PROMPTS.bootstrap` Step 1 追加：`If config contains lessons_path, read that file. Extract entries (each has id, title, detail). For each task in the current plan, match lessons whose title/detail keywords overlap with the task's title. Return matched lessons per task in evidence as task_lessons: [{task_id, lessons:[{id, title, detail}]}]. Absent lessons_path → empty array.`
  - evidence 结构追加 `task_lessons:[{task_id, lessons:[{id, title, detail}]}]`（扁平字段）
  - Return 语句也要追加 `task_lessons`
- [ ] 7. 在 `SCHEMAS.bootstrap` 的 `evidence.properties` 中追加 `task_lessons: { type: 'array' }`（扁平字段）
- [ ] 8. 跑确认绿

### implementor prompt 扩展

- [ ] 9. **RED**：扩展测试，断言 `PROMPTS.implementor` 包含 `{{lessons}}` 占位符（独立行，同 `{{referencePaths}}` 模式）
- [ ] 10. **GREEN**：在 `PROMPTS.implementor` 的 `{{failedApproaches}}` 行后（如 T2 已加）或 `{{referencePaths}}` 行后（如 T2 未加），**新起一行**追加 `{{lessons}}` 占位符
- [ ] 11. 跑确认绿

### finalReport prompt 扩展

- [ ] 12. **RED**：扩展测试，断言 `PROMPTS.finalReport` 在 halted 模式下追加 lesson 到 lessons_path 文件，且 `{{lessonsPath}}` 占位符存在于 prompt 中
- [ ] 13. **GREEN**：在 `PROMPTS.finalReport` 的 Inputs 行追加 `lessonsPath={{lessonsPath}}`；在 halted 分支追加：`If lessonsPath is non-empty AND blockedInfo has reason+last_error: append a new lesson entry to the lessonsPath file. Format: "## L-<timestamp>\ntitle: <reason>\ndetail: <last_error>\nstatus: active\n". Use append mode (do not overwrite). If file does not exist, create it with a header "# Lessons Learned".`
- [ ] 14. 跑确认绿

### config + orchestrator 胶水

- [ ] 15. 在 `workflow.config.json` 追加 `"lessons_path": "docs/superpowers/lessons.md"`（本项目启用）
- [ ] 16. 在 `run-plans.js` 的 state 定义处显式初始化 `taskLessons: {}`
- [ ] 17. 在 bootstrap evidence 处理后：将 `evidence.task_lessons` 按 `task_id` 索引存入 `state.taskLessons`
- [ ] 18. 在 `run-plans.js` 的 `implCtx` 工厂**函数体内**（不新增参数）：读取 `state.taskLessons?.[task.id]`，调用 `formatLessons(...)` 并设为 `lessons` 字段
- [ ] 19. 在 `run-plans.js` 的 `finalReportWithFallback` 函数中：从 `state.config?.lessons_path || ''` 读取，传入 `lessonsPath` 字段。**必须在 done 和 halted 两种模式的调用处都传入**（halted 模式约 line 570，done 模式约 line 793），否则 done 模式 prompt 残留字面 `{{lessonsPath}}`
- [ ] 20. **SYNC**：所有 lib.js 改动逐字同步到 run-plans.js
- [ ] 21. **REFACTOR**：扩展 `sync.test.js`：helper 列表加 `formatLessons`；wiring 断言加 `lessons:` 和 `lessonsPath:`；schema 断言加 `task_lessons`
- [ ] 22. 跑全量确认绿

### Verify

```bash
cd docs/superpowers/workflows && node --test tests/lessons.test.js tests/sync.test.js
```

### Done

config 声明 lessons_path；bootstrap 读取并匹配；implementor 收到注入；finalReport halt 时自动追加；done 模式也传 lessonsPath（避免占位符残留）；不配时跳过；sync.test.js 绿。

---

## Task 6: Schema 迁移一致性检查

**目标：** config 可选声明 `schema_tool`（如 `"alembic"`）、`model_paths`（ORM model 目录）、`migration_paths`（迁移目录）。gate agent 在 committed SHA 上检查：model 文件有变更但无对应迁移文件 → gate failed。不配时跳过（向后兼容）。

**文件：** `docs/superpowers/workflows/lib.js`、`.claude/workflows/run-plans.js`、`docs/superpowers/workflows/tests/schema-migration.test.js`、`workflow.config.json`

**关键设计决策：**
- **git diff 范围**：gate agent 已 `git checkout` 到 committed SHA（`run-plans.js` 约 line 776），此时 `HEAD~1` 即父提交。用 `git diff --name-only HEAD~1..HEAD`，**不依赖外部传入 prev_sha**（gate ctx 只有 `sha`，无 `prev_sha`）
- **helper 签名**：`formatSchemaCheck(schemaTool, modelPaths, migrationPaths)` —— 接收具体参数（同其他 helper 接收具体数组），不接收整个 config 对象（与其他 helper 签名一致）

### 前置：formatSchemaCheck helper

- [ ] 1. **RED**：新建 `tests/schema-migration.test.js`，测试 `formatSchemaCheck('', [], [])` → `''`，`formatSchemaCheck('alembic', ['app/models/'], ['alembic/versions/'])` → 含 `## Schema Migration Check` 标题 + 检查指令
- [ ] 2. 跑确认失败
- [ ] 3. **GREEN**：在 `lib.js` 新增 `formatSchemaCheck(schemaTool, modelPaths, migrationPaths)`：
  - `schemaTool` 为空串 → 空串（检查跳过）
  - 有 → `## Schema Migration Check (gate agent must verify)` 标题 + 指令：
    - `1. Run git diff --name-only HEAD~1..HEAD — you are already checked out to the committed SHA, so HEAD~1 is the parent commit.`
    - `2. Filter changed files by model_paths: ${modelPaths.join(', ')}`
    - `3. Filter changed files by migration_paths: ${migrationPaths.join(', ')}`
    - `4. If model files changed but NO migration files changed → status=failed, evidence.migration_missing=true`
- [ ] 4. 跑确认绿

### gate prompt 扩展

- [ ] 5. **RED**：扩展测试，断言 `PROMPTS.gate` 包含 `{{schemaCheck}}` 占位符
- [ ] 6. **GREEN**：在 `PROMPTS.gate` 的 Commands 段后**新起一行**追加 `{{schemaCheck}}` 占位符（条件渲染：空串时该段消失）
- [ ] 7. 在 `SCHEMAS.gate` 的 `evidence.properties` 追加 `migration_missing: { type: 'boolean' }`
- [ ] 8. 跑确认绿

### config + orchestrator 胶水

- [ ] 9. 在 `workflow.config.json` 追加：
  ```json
  "schema_tool": "alembic",
  "model_paths": ["app/models/"],
  "migration_paths": ["alembic/versions/"]
  ```
- [ ] 10. 在 `run-plans.js` 的 gate 派发处（约 line 776）：传入 `schemaCheck: formatSchemaCheck(cfg.schema_tool || '', cfg.model_paths || [], cfg.migration_paths || [])` 到 gate prompt context
- [ ] 11. gate 返回 `evidence.migration_missing === true` 时 → 在 gate status 判断中追加 `|| gate.evidence?.migration_missing` 触发 halt
- [ ] 12. **SYNC**：所有 lib.js 改动逐字同步到 run-plans.js
- [ ] 13. **REFACTOR**：扩展 `sync.test.js`：helper 列表加 `formatSchemaCheck`；wiring 断言加 `schemaCheck:` 和 `migration_missing`
- [ ] 14. 跑全量确认绿

### Verify

```bash
cd docs/superpowers/workflows && node --test tests/schema-migration.test.js tests/sync.test.js
```

### Done

config 声明 schema_tool；gate agent 用 `git diff --name-only HEAD~1..HEAD` 检查 model/migration 一致性；不配时跳过；sync.test.js 绿。

---

## 完成标准

- [ ] 全 6 task commit 完成（`feat(plan-07/TN): ...` convention）
- [ ] `cd docs/superpowers/workflows && node --test tests/*.test.js` 全绿
- [ ] `workflow.config.json` 新增 4 个可选字段（lessons_path / schema_tool / model_paths / migration_paths）
- [ ] lib.js ↔ run-plans.js sync.test.js 全绿（PROMPTS / SCHEMAS / helpers 逐字一致）
- [ ] 向后兼容：旧 config（无新字段）照跑，所有新 prompt 段条件渲染为空串
- [ ] 通用性：prompt 无项目特定硬编码（sync.test.js 通用性断言绿）
