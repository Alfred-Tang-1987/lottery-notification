# run-plans workflow 提炼为通用仓库 — 设计文档

> **目标**：把 `lottery-notification` 仓库内的 run-plans workflow 引擎提炼为独立通用仓库 `run-plans-engine`，作为单一事实源，供其他项目以 git submodule + sync 脚本方式引用，消除跨项目手动复制同步负担。
>
> **状态**：设计已确认，待实现
> **日期**：2026-07-09
> **源仓库**：lottery-notification（提炼源头，迁移后变为消费项目）
> **首批消费项目**：lottery-notification、OTC-''Fund-SIP-Strategy

## 1. 背景与动机

### 1.1 现状

`lottery-notification` 仓库内 run-plans workflow 引擎分散在多处：

- `.claude/workflows/run-plans.js`（~143KB，引擎主体，inline 复制 lib.js 纯函数）
- `docs/superpowers/workflows/lib.js`（纯函数真源）
- `docs/superpowers/workflows/tests/`（260+ node:test，含 sync.test 守护字节一致）
- `docs/superpowers/workflow-design.md`（设计 spec）
- `docs/superpowers/workflows/USAGE.md`（使用文档）
- `docs/superpowers/workflow-plans/`（engine 自身历史 implementation plan）
- `docs/superpowers/workflows/research/`（loop engineering 研究报告）

`OTC-''Fund-SIP-Strategy` 仓库已通过**手动复制**方式接入 run-plans，存在完整副本（`.claude/workflows/run-plans.js` + `docs/superpowers/workflows/lib.js` + tests + design + USAGE）。两仓库的副本之间无版本锁定，手动同步负担已成确认痛点。

### 1.2 动机

- 消除跨项目手动复制 run-plans.js / lib.js / tests 的负担
- 建立 engine 版本锁定（submodule SHA），让消费项目可追溯所用 engine 版本
- 让 engine 演进（新 prompt / 新 schema / 新 halt 路径）能可靠分发到所有消费项目
- 让新项目接入 run-plans 的成本降至一次 `git submodule add` + 一次 sync

## 2. 引用机制：Git submodule + sync 脚本

### 2.1 为什么选 submodule + sync 脚本

对比三种引用机制：

| 方案 | 评估 | 结论 |
|---|---|---|
| npm 包 + postinstall 复制 | run-plans.js 运行时不能 require 外部模块（Workflow runtime 禁 fs/import），npm 只能"送货"不能"引用"；且会给 Python 消费项目强加 node_modules 依赖 | 否决 |
| 模板仓库 + 手动 sync 脚本 | 最简单，但无版本锁定，需手动记住跑脚本，本地可能被改分叉 | 不够 |
| **Git submodule + sync 脚本** | 单一事实源、版本锁定、零 Python 依赖污染、符合用户的 git/脚本风格 | **采用** |

### 2.2 为什么是复制而非 symlink

- **Workflow runtime 禁 fs/import**：run-plans.js 必须自包含（保留现有 inline-copy 架构 + sync.test 字节守护）
- **Windows symlink 需管理员/开发者模式权限**：复制 + commit 更稳，零权限依赖
- **复制产物是 submodule SHA 的派生物**：commit 它换取可追溯 + 离线可用

### 2.3 三层分离

| 层 | 位置 | 谁维护 | 例子 |
|---|---|---|---|
| Engine 通用代码 | submodule（`.claude/workflow-engine/`） | 通用仓库 | run-plans.js, lib.js, tests, design, USAGE |
| Engine 运行时副本 | `.claude/workflows/run-plans.js` | sync 脚本生成 + 消费项目 commit | 派生物，禁手改 |
| 项目特定配置 | 消费项目根 / `docs/superpowers/` | 消费项目 | workflow.config.json, lessons.md, plans/, specs/ |

### 2.4 引用拓扑

```
消费项目 (lottery-notification / OTC-''Fund-SIP-Strategy)
├── .claude/
│   ├── workflows/
│   │   └── run-plans.js          ← sync 脚本复制（派生物，commit 但禁手改）
│   └── workflow-engine/          ← git submodule (run-plans-engine 仓库)
│       ├── run-plans.js          ← canonical 源
│       ├── lib.js
│       ├── tests/                ← engine 自身的 node:test
│       ├── workflow-design.md
│       ├── USAGE.md
│       ├── research/
│       ├── workflow-plans/       ← engine 自身历史 plan
│       ├── .gitattributes        ← 强制 LF
│       ├── examples/
│       │   ├── workflow.config.example.json
│       │   ├── lessons.seed.md
│       │   └── plan-frontmatter.example.md
│       ├── scripts/
│       │   ├── common.mjs
│       │   ├── sync.mjs
│       │   ├── pre-commit-sync-check.sh
│       │   ├── pre-commit-sync-check.mjs
│       │   ├── session-start-check.sh
│       │   └── init-consumer.mjs
│       ├── README.md
│       └── package.json
├── workflow.config.json          ← 项目特定（从 example 拷贝后改）
└── docs/superpowers/
    ├── lessons.md                ← 项目特定（从 seed 拷贝后生长）
    ├── plans/                    ← 项目特定
    └── specs/                    ← 项目特定
```

## 3. Claude Code Workflow 规范合规性

### 3.1 合规结论：符合

逐条对照 `workflow-design.md` §4.3 的 Workflow runtime 约束清单：

| 规范要求 | 本设计如何满足 |
|---|---|
| 脚本须位于 `.claude/workflows/<name>.js` | sync 脚本把 canonical `run-plans.js` 复制到 `.claude/workflows/run-plans.js`，named-workflow 注册位置不变 |
| runtime 禁 fs / 模块 import | run-plans.js 仍自包含（inline 复制 lib.js 纯函数），不读 submodule、不 require 外部 |
| runtime 禁 Date.now / Math.random | 不受影响（engine 代码不变） |
| `Workflow({scriptPath, args})` 触发 | scriptPath 仍指向 `.claude/workflows/run-plans.js`，消费项目调用方式零改变 |
| agent() 上限 / 无持久化 | 不受影响 |

### 3.2 submodule 不干扰注册

named-workflow 按 `.claude/workflows/*.js` 枚举（非递归扫 `.claude/`），`.claude/workflow-engine/` 是同级不同目录，Claude Code 不扫描它。

### 3.3 其他仓库可用性

`OTC-''Fund-SIP-Strategy` 已有完整副本（`.claude/workflows/run-plans.js` + `docs/superpowers/workflows/lib.js` + tests 全在），迁移就是把"复制的源"换成"submodule + sync 产物"，运行时行为等价。新项目接入见 §4.1。

## 4. 使用方法

### 4.1 一次性初始化（新项目接入）

> 本地阶段 `<engine-repo-url>` = `file:///C:/''Users/Alfred/Documents/projects/run-plans-engine`（Windows 格式），后续推远程后替换为 Gitea URL。

```bash
# 1. 添加 engine submodule
git submodule add file:///C:/''Users/Alfred/Documents/projects/run-plans-engine .claude/workflow-engine
git commit -m "chore(workflow): add run-plans-engine submodule"

# 2-5. 使用 init-consumer 脚手架一键完成 sync、拷贝 examples、安装 hooks
node .claude/workflow-engine/scripts/init-consumer.mjs

# 3. 提交变更（如果 init-consumer 已自动 stage，则直接 commit）
git commit -m "chore(workflow): init run-plans-engine consumer"
```

`init-consumer.mjs` 会：
- 检查并添加 submodule（已存在则跳过）
- 运行 `sync.mjs` 生成 `.claude/workflows/run-plans.js`
- 拷贝 `examples/workflow.config.example.json` → `workflow.config.json`
- 拷贝 `examples/lessons.seed.md` → `docs/superpowers/lessons.md`
- 安装 pre-commit hook：若已有 `.git/hooks/pre-commit`，先检查是否已包含 `pre-commit-sync-check` 调用；若已包含则跳过安装，否则备份后合并。保证 `init-consumer` 幂等可重试。
- 可选安装 SessionStart hook（询问用户，默认不安装）

手动 6 步（旧版）仍保留在 engine 仓库的 `USAGE.md` 中作为参考。

### 4.1.1 必须步骤：根据消费仓库编辑 workflow.config.json

**`workflow.config.json` 是项目特定配置，拷贝自 example 后必须根据消费仓库自身情况编辑，否则 workflow 无法正确运行。** 拷贝后 `init-consumer.mjs` 打印醒目提示，并依赖 pre-commit 字节比对守护（见下）。

必须编辑的字段（example 中留占位）：

| 字段 | 含义 | lottery-notification 示例 | OTC-''Fund-SIP-Strategy 示例 |
|---|---|---|---|
| `test_command` | 单文件测试命令 | `python -m pytest {file} -x` | `python -m pytest {file} -x` |
| `full_test_command` | 全量测试命令 | `python -m pytest` | `python -m pytest` |
| `lint_command` | lint 命令 | `ruff check .` | `ruff check .` |
| `spec_path` | spec 目录 | `docs/superpowers/specs` | `docs/superpowers/specs` |
| `plans_dir` | plan 目录 | `docs/superpowers/plans` | `docs/superpowers/plans` |
| `language` | 主语言 | `python` | `python` |
| `silent_failure_context` | 项目特定的静默失败上下文 | 彩票 DB 5 条纪律 | 基金定投特定纪律 |

**config 守护机制（字节比对，不污染 JSON）**：

> `workflow.config.json` 是 `.json` 文件，JSON 标准不支持注释。**不采用**"注入 `// TODO: edit me` 标记"方案（会破坏 JSON.parse）。改用字节比对：

- `init-consumer.mjs` 拷贝 example 后打印警告："workflow.config.json 未编辑，workflow 将无法正确运行。请编辑后再提交。"
- pre-commit hook（gate 2）在 commit 时：若 `workflow.config.json` 与 `examples/workflow.config.example.json` **字节一致**（sha256 相同），则拒绝 commit 并提示"workflow.config.json 仍是 example 原值，请根据消费项目情况编辑"。
- 一旦用户编辑任意字段，字节即不等，hook 放行。这避免"拷贝了 example 就直接 commit，导致 workflow 用错 test_command"的静默失败。
- **已知小限制**：若用户编辑后改回 example 原值会被误拦——概率低，且误拦时按提示再编辑任意字段即可放行。

**跨平台注意**：`test_command` / `lint_command` 在 Windows 和 Mac 上可能不同（如 `python` vs `python3`）。若消费仓库跨平台开发，建议在 `workflow.config.json` 中用平台无关命令，或通过 `scripts/` 包装脚本分发。

### 4.2 触发 workflow（与现状完全一致）

```
让 Claude：用 run-plans workflow 跑 Plan 01
```

或显式：

```javascript
Workflow({
  scriptPath: '.claude/workflows/run-plans.js',
  args: {
    configPath: 'workflow.config.json',
    plansDir: 'docs/superpowers/plans',
    plan: '01'
  }
})
```

### 4.3 更新 engine 版本（消费项目侧）

```bash
# 1. 拉取 engine 最新版
git submodule update --remote .claude/workflow-engine

# 2. 重新 sync（也可省略，下次 commit 时 pre-commit hook 会自动 sync）
node .claude/workflow-engine/scripts/sync.mjs

# 3. commit
git add .claude/workflow-engine .claude/workflows/run-plans.js
git commit -m "chore(workflow): bump run-plans-engine@<new-sha>"
```

## 5. 更新分发机制：B + C 组合

### 5.1 机制选型

| 方案 | 触发时机 | 自动化 | 评估 |
|---|---|---|---|
| A. 纯手动 | 消费项目主动 submodule update + sync + commit | 全手动 | 不够 |
| **B. pre-commit 自动 sync** | git commit 时检测 engine 已更新但产物漂移 → 自动跑 sync + stage 产物 → 退出非零要求重新提交 | **半自动（commit 时自愈）** | **采用** |
| **C. SessionStart hook 提醒** | Claude Code session 开始 → 检测 engine 远程是否有新 commit → 有则提醒 | **提醒式（不改工作树）** | **采用** |
| D. 远程 CI 自动 PR | engine push → Gitea Actions 向消费仓库提 PR | 全自动 | 当前规模不划算（2 个消费仓库，CI 配置成本 > 收益），已记入 [`run-plans-engine-TODOS.md`](../run-plans-engine-TODOS.md) |

**B 解决"忘记 sync"**：用户只需 `git submodule update --remote`，下次 commit 时 pre-commit hook 自动检测 run-plans.js 漂移 → 跑 sync 脚本 → 把更新后的产物 stage 进当前 commit，然后退出非零要求重新提交。

**C 解决"不知道 engine 有更新"**：SessionStart hook 跑 `git -C .claude/workflow-engine fetch && git log HEAD..origin/main --oneline`，有新 commit 则提示。只提醒不改工作树。

### 5.2 B 的 pre-commit 脚本逻辑

消费项目由 `init-consumer` 生成的 `.git/hooks/pre-commit` 是一个 `#!/usr/bin/env node` 入口脚本，调用 engine 仓库的 `scripts/pre-commit-sync-check.mjs`：

```javascript
// pre-commit-sync-check.mjs 核心逻辑
// 1. 读取派生文件头 @sha
// 2. 计算 canonical run-plans.js 的 sha256（Node crypto）
// 3. 若不一致：运行 sync.mjs → stage 派生文件 → exit 1（要求重新提交）
// 4. 若一致：检查 workflow.config.json 是否与 examples/workflow.config.example.json 字节一致
//    - 若一致（未编辑）：exit 1 + 提示"请先编辑 workflow.config.json 再提交"（§4.1.1）
//    - 若不一致（已编辑）：exit 0
```

engine 仓库同时提供 POSIX 参考实现 `pre-commit-sync-check.sh`（供非 Windows 环境或手动安装）。

### 5.3 安全约束

- pre-commit 只自动 stage `.claude/workflows/run-plans.js` 这一个派生文件（绝不 `git add -A`）
- sync 脚本失败 → 退出非零阻断 commit（不静默放过）
- 派生文件头注入 `@sha` 标注，让 gate 2 可比对

### 5.4 C 的 SessionStart 提醒逻辑

```bash
#!/bin/bash
# scripts/session-start-check.sh — engine 更新提醒（Claude Code SessionStart hook 调用）
ENGINE=".claude/workflow-engine"
cd "$(git rev-parse --show-toplevel)" || exit 0
if [ ! -d "$ENGINE/.git" ]; then exit 0; fi

git -C "$ENGINE" fetch --quiet 2>/dev/null
NEW_COMMITS=$(git -C "$ENGINE" log HEAD..origin/main --oneline 2>/dev/null)
if [ -n "$NEW_COMMITS" ]; then
  COUNT=$(echo "$NEW_COMMITS" | wc -l)
  echo "ℹ run-plans-engine 有 $COUNT 个新提交，建议更新："
  echo "  git submodule update --remote .claude/workflow-engine"
  echo "  node .claude/workflow-engine/scripts/sync.mjs"
  echo "  git add .claude/workflow-engine .claude/workflows/run-plans.js && git commit"
fi
```

集成方式：在消费项目的 `.claude/settings.json` 中注册：

```json
{
  "hooks": {
    "SessionStart": [
      {
        "type": "command",
        "command": "bash .claude/workflow-engine/scripts/session-start-check.sh"
      }
    ]
  }
}
```

Claude Code 在 session 启动时执行此命令，脚本的 stdout 会被注入 session 上下文作为提醒。脚本只读 git 状态、不改工作树，符合 SessionStart hook 的安全约束。

## 6. Gate 控（双层防漂移）

| gate | 位置 | 作用 | 强制性 |
|---|---|---|---|
| gate 1：引擎 sync.test | 通用仓库 `tests/` | 守护 canonical `run-plans.js` 与 `lib.js` 字节一致（现有 sync.test 机制，路径调整后保留） | 引擎开发者侧强制 |
| gate 2：消费项目 pre-commit hook | 消费项目 `.git/hooks/pre-commit` | 检测 `run-plans.js` 漂移 → 自动 sync + stage，并退出非零要求重新提交（§5.2） | 消费项目开发者侧强制 |

说明：Gate 3（bootstrap 运行时软提醒）在 CEO review 中曾被考虑，但 Claude Code Workflow runtime 禁止 `run-plans.js` 在运行时读取文件系统，因此无法在 bootstrap 中实现。已移除。

### 6.1 为什么两层已足够

- **Gate 1** 保证 canonical `run-plans.js` 与 `lib.js` 字节一致（引擎仓库内）。
- **Gate 2** 保证消费项目提交时派生文件与 engine 源一致。
- **`init-consumer.mjs` 自动安装 hook** 解决「忘记安装 pre-commit」问题。
- 如果 hook 被绕过，这是团队纪律问题，而非技术问题；文档中明确禁止手改派生文件。

### 6.2 `@sha` 文件头格式（gate 2 辅助）

`sync.mjs` 在派生文件第二行注入：

```
// DO NOT EDIT — generated from workflow-engine@<sha256> by sync.mjs
```

Gate 2 的 pre-commit 脚本读取该 header，与 canonical `run-plans.js` 的 sha256 比对。缺失 `@sha` 时按 drift 处理，重新 sync。

## 7. 迁移执行计划

### 7.1 通用仓库 `run-plans-engine` 初始结构

从 `lottery-notification` 迁出的文件映射：

| 源路径（lottery-notification） | 目标路径（run-plans-engine） | 说明 |
|---|---|---|
| `.claude/workflows/run-plans.js` | `run-plans.js` | canonical 引擎主体 |
| `docs/superpowers/workflows/lib.js` | `lib.js` | 纯函数真源 |
| `docs/superpowers/workflows/tests/` | `tests/` | node:test（含 sync.test，路径需调整） |
| `docs/superpowers/workflows/package.json` | `package.json` | 改 name 为 `run-plans-engine` |
| `docs/superpowers/workflows/USAGE.md` | `USAGE.md` | 使用文档 |
| `docs/superpowers/workflow-design.md` | `workflow-design.md` | 设计 spec |
| `docs/superpowers/workflow-plans/` | `workflow-plans/` | engine 自身历史 plan（含 archive/） |
| `docs/superpowers/workflows/research/` | `research/` | loop engineering 研究报告 |
| `docs/superpowers/2026-06-22-workflow-orchestrator.md` | `workflow-plans/2026-06-22-workflow-orchestrator.md` | 早期 orchestrator 设计 |

新建文件：

| 路径 | 作用 |
|---|---|
| `.gitattributes` | 强制 LF 行尾，避免 Windows CRLF 问题影响 sync.test 字节守护和 hook 执行 |
| `examples/workflow.config.example.json` | 从 lottery 的 config 抽通用字段（test/lint/spec_path/language 留占位） |
| `examples/lessons.seed.md` | 通用静默失败模式（bare except / split-commit / savepoint 等跨项目通用项） |
| `examples/plan-frontmatter.example.md` | plan frontmatter 示例（含 model 字段） |
| `scripts/common.mjs` | 共享：提取 `@sha`、计算 sha256（sync.mjs 与 gate 2 复用） |
| `scripts/sync.mjs` | 复制 run-plans.js → 目标 + 注入 @sha 头（使用 Node crypto） |
| `scripts/pre-commit-sync-check.sh` | gate 2：漂移自愈（POSIX 参考实现） |
| `scripts/pre-commit-sync-check.mjs` | gate 2：跨平台 Node 入口（init-consumer 生成 hook 时调用） |
| `scripts/session-start-check.sh` | gate C：engine 更新提醒 |
| `scripts/init-consumer.mjs` | 新项目接入脚手架（submodule + sync + examples + hook） |
| `README.md` | 仓库说明 + 快速接入指引 |

### 7.2 sync.test.js 路径调整

唯一需要改代码的地方——引擎仓库内 tests 与 run-plans.js 同层：

```javascript
// 旧（lottery-notification 内，tests 在 docs/superpowers/workflows/tests/）
const runSrc = fs.readFileSync(
  path.resolve(__dirname, '../../../../.claude/workflows/run-plans.js'), 'utf8')

// 新（run-plans-engine 内，tests 与 run-plans.js 同层）
const runSrc = fs.readFileSync(path.resolve(__dirname, '../run-plans.js'), 'utf8')
```

其余断言逻辑不变。但在迁移后，需要检查所有 260+ tests 是否还有硬编码的消费项目路径，不只是 `sync.test.js`。

### 7.3 sync.mjs 核心逻辑

```javascript
// scripts/sync.mjs — 复制 canonical run-plans.js 到消费项目的 .claude/workflows/
import fs from 'node:fs'
import path from 'node:path'
import { computeSha256 } from './common.mjs'

const ENGINE_ROOT = path.resolve(import.meta.dirname, '..')
const SRC = path.join(ENGINE_ROOT, 'run-plans.js')
// 消费项目根 = engine submodule 的上两级（.claude/workflow-engine → .claude → 项目根）
const CONSUMER_ROOT = path.resolve(ENGINE_ROOT, '../..')
const DEST = path.join(CONSUMER_ROOT, '.claude', 'workflows', 'run-plans.js')

// 计算 canonical run-plans.js sha256（Node crypto，跨平台）
const sha = await computeSha256(SRC)

const srcContent = fs.readFileSync(SRC, 'utf8')
// 注入 @sha 标注到文件头（第二行注释，不破坏首行注释）
const marked = srcContent.replace(
  /^(\/\/.*\n)/,
  `$1// DO NOT EDIT — generated from workflow-engine@${sha} by sync.mjs\n`)

fs.mkdirSync(path.dirname(DEST), { recursive: true })
fs.writeFileSync(DEST, marked)
console.log(`✓ synced run-plans.js → ${path.relative(CONSUMER_ROOT, DEST)} (@sha ${sha.slice(0,12)})`)
```

约束：canonical `run-plans.js` 首行必须是 `//` 注释；`sync.test.js` 增加断言守护该约束。如果首行不匹配，sync.mjs 抛出清晰错误。

### 7.4 迁移顺序（4 阶段）

```
阶段一：建通用仓库（本地）
  1. 在 <LOCAL_PATH>/run-plans-engine 建 git 仓库
  2. 从 lottery-notification 复制 engine 文件（按 7.1 映射表）
  3. 调整 sync.test.js 路径（7.2）+ 创建脚本（7.3）+ examples + README + .gitattributes
  4. 跑 node --test tests/ 验证全绿（含 sync.test 字节守护；同时检查所有 tests 是否有硬编码消费项目路径）
  5. git commit -m "chore: initialize run-plans-engine from lottery-notification"

阶段二：lottery-notification 迁移为消费项目
  6. 删除 engine 源文件（.claude/workflows/run-plans.js + docs/superpowers/workflows/ + workflow-design.md + workflow-plans/）
  7. git submodule add file:///C:/''Users/Alfred/Documents/projects/run-plans-engine .claude/workflow-engine
  8. node .claude/workflow-engine/scripts/init-consumer.mjs → 生成 run-plans.js + 拷贝 examples + 安装 hook
  9. 验证：Workflow({scriptPath: '.claude/workflows/run-plans.js', args: {...}}) 跑一个轻量 task
 10. git commit

阶段三：OTC-''Fund-SIP-Strategy 迁移（重复 6-10，删除其旧副本）

阶段四：（后续）推 Gitea + 更新 submodule URL 为远程（已记入 [`run-plans-engine-TODOS.md`](../run-plans-engine-TODOS.md) T1）
```

### 7.5 验证检查清单

| 验证项 | 方法 | 期望 |
|---|---|---|
| 引擎测试全绿 | `cd run-plans-engine && node --test tests/` | 365+ tests pass；无硬编码消费项目路径失败 |
| sync.test 路径正确 | 同上 | sync.test 子项全绿 |
| sync 脚本可用 | `node sync.mjs` | 生成 .claude/workflows/run-plans.js + @sha 头 |
| lottery workflow 可触发 | `Workflow({scriptPath, args:{plan:'01',tasks:['T1']}})` | bootstrap 正常 + 识别已 commit task |
| pre-commit 自愈 | 故意改 .claude/workflows/run-plans.js + git commit | hook 自动 sync + stage + 退出非零要求重新提交 |
| pre-commit 拦截未编辑 config | 拷贝 example 后不编辑直接 commit | hook exit 1 + 提示"workflow.config.json 仍是 example 原值" |
| config 已针对消费项目编辑 | 人工核对 lottery / OTC 的 workflow.config.json | test_command/full_test_command/language/silent_failure_context 均非占位 |
| SessionStart 提醒 | engine 有新 commit + 开新 session | 提示"engine 有更新" |
| OTC workflow 可触发 | 同 lottery 验证 | bootstrap 正常 |
| 跨平台 sync | Windows + Mac 各跑一次 init-consumer + sync | 均成功；hook 在两平台均触发 |

### 7.6 保留在 lottery-notification 的项目特定文件

- `workflow.config.json`（保留彩票特定配置：silent_failure_context 的 5 条 DB 纪律等）
- `docs/superpowers/lessons.md`（保留，含彩票特定 lessons + 通用静默失败模式）
- `docs/superpowers/plans/`（业务 plan，如 2026-06-21-01-infra-bootstrap.md 等）
- `docs/superpowers/specs/`（业务 spec）
- `docs/superpowers/reviews/` + `prototypes/`（业务产物）

## 8. 决策记录

| 决策 | 选择 | 理由 |
|---|---|---|
| 引用机制 | submodule + sync 脚本 | 单一事实源 + 版本锁定 + 零 Python 依赖污染 |
| 派生方式 | 复制 + commit（非 symlink） | Windows symlink 权限问题；复制产物换取零权限依赖 |
| submodule 挂载位置 | `.claude/workflow-engine/` | 与 `.claude/workflows/` 同级，不干扰 named-workflow 枚举 |
| 更新分发 | B（pre-commit 自愈）+ C（SessionStart 提醒） | 零额外步骤 + 防遗忘；当前规模不上 CI 自动 PR |
| 仓库位置 | 先本地（file://）后推 Gitea | 两阶段，本地走通再上远程 |
| 迁移范围 | 完整（含历史 plan + research） | 完整迁移，lottery-notification 的 engine 相关文件清空 |
| 首批消费项目 | lottery-notification + OTC-''Fund-SIP-Strategy | 一次性解决两个项目的同步问题 |
| pre-commit 自动 sync 后行为 | 自动 sync + stage，退出非零，要求重新提交 | 符合 git hook 约定，不自动 commit |
| lessons.md 处理 | 消费项目保留（含项目特定 + 通用） | 通用部分已在 engine 的 lessons.seed.md，不强拆消费项目 lessons |
| workflow.config.json 守护 | pre-commit 字节比对 workflow.config.json 与 example；字节一致则拒绝 commit | JSON 不支持注释，不能用 TODO 标记；字节比对零污染且语义清晰（已编辑=不等于 example）；防止拷贝 example 就直接 commit 的静默失败 |
| 跨平台支持 | Node crypto + Node 入口 hook（POSIX .sh 仅参考）+ .gitattributes 强制 LF | Windows + Mac 均无外部命令依赖（不依赖 sha256sum/grep） |
| engine 可信源声明 | README.md 与 USAGE.md 中显式声明 | 消费项目应使用授权/审核过的 submodule URL，更新前 review 变更 |
| Gate 3 | **移除** | Claude Code Workflow runtime 禁止 run-plans.js 运行时读取文件系统，bootstrap 中无法实现软提醒；gate 1 + gate 2 + init-consumer 自动安装已足够 |

## 9. 风险与缓解

| 风险 | 缓解 |
|---|---|
| pre-commit hook 在无 node 环境的消费项目失败 | sync.mjs 是纯 node 脚本（无依赖）；hook 检测 `command -v node` 不存在时 exit 0 放行 + 提醒安装 |
| submodule 在 Windows 上的 CRLF 问题 | `.gitattributes` 在 engine 仓库强制 LF（lottery-notification 已有先例） |
| sync 脚本生成的 @sha 头破坏 run-plans.js 首行注释 | 用正则 `^(//.*\n)` 在首行注释后插入，不覆盖首行；由 sync.mjs 单元测试守护；首行必须是 `//` 注释（由 sync.test 守护） |
| 消费项目忘记配置 pre-commit hook | `init-consumer.mjs` 自动安装并验证；README/USAGE 强调；禁止手改派生文件 |
| 消费项目拷贝 example 后未编辑 workflow.config.json 就 commit | pre-commit hook 字节比对 workflow.config.json 与 example；字节一致则拒绝 commit 并提示编辑（§4.1.1）。不注入 JSON 注释（JSON 不支持注释） |
| 跨平台 test_command 差异（python vs python3） | example 注释提示；建议消费项目用平台无关命令或 scripts/ 包装 |
| engine 演进破坏旧消费项目（breaking change） | engine 仓库的 CHANGELOG 标注 breaking（[TODOS](../run-plans-engine-TODOS.md) T3）；消费项目可锁定 submodule SHA 不升级 |
| engine 仓库为可信源 | README.md 与 USAGE.md 显式声明：消费项目只应使用授权/审核过的 submodule URL，更新前 review 变更 |
| 本地 `file://` submodule URL 格式不标准 | 使用 `file:///C:/''Users/...` 格式，并在 Windows 11 本机验证 |
| init-consumer 重复运行导致嵌套 hook | 安装前检查 hook 是否已包含 `pre-commit-sync-check` 调用；已包含则跳过 |
| 260+ tests 中存在硬编码消费项目路径 | 迁移后运行全量测试，检查并修复所有路径相关断言 |

## 10. 回滚策略

- 迁移前备份旧 `.claude/workflows/run-plans.js` 到系统临时目录（`os.tmpdir()/run-plans-backup-<timestamp>.js`），避免备份文件留在 `.claude/` 下被 git 跟踪或干扰 named-workflow 枚举。
- 如果 submodule 无法工作，执行 `git rm -f .claude/workflow-engine` 和 `git submodule deinit -f .claude/workflow-engine`，从临时目录备份恢复旧文件。
- 保留旧 engine 文件删除的 commit，可通过 `git revert` 回滚。
- 先在 `lottery-notification` 验证通过后再迁移 `OTC-''Fund-SIP-Strategy`（分阶段，降低 blast radius）。

## 11. Review 报告

Review 过程产物（CEO/Eng review 结论、cross-model 发现等）已移至独立文件：[`docs/superpowers/reviews/2026-07-09-run-plans-engine-extraction-review.md`](reviews/2026-07-09-run-plans-engine-extraction-review.md)

**结论摘要**：CEO + Eng Review CLEARED — ready to implement。gate 3 因 Workflow runtime fs 限制被 cross-model 发现并移除。
