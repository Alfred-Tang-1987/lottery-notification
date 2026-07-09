# run-plans workflow 提炼为通用仓库 — 设计文档

> **目标**：把 `lottery-notification` 仓库内的 run-plans workflow 引擎提炼为独立通用仓库 `run-plans-engine`，作为单一事实源，供其他项目以 git submodule + sync 脚本方式引用，消除跨项目手动复制同步负担。
>
> **状态**：设计已确认，待实现
> **日期**：2026-07-09
> **源仓库**：lottery-notification（提炼源头，迁移后变为消费项目）
> **首批消费项目**：lottery-notification、OTC-Fund-SIP-Strategy

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

`OTC-Fund-SIP-Strategy` 仓库已通过**手动复制**方式接入 run-plans，存在完整副本（`.claude/workflows/run-plans.js` + `docs/superpowers/workflows/lib.js` + tests + design + USAGE）。两仓库的副本之间无版本锁定，手动同步负担已成确认痛点。

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
消费项目 (lottery-notification / OTC-Fund-SIP-Strategy)
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
│       ├── examples/
│       │   ├── workflow.config.example.json
│       │   ├── lessons.seed.md
│       │   └── plan-frontmatter.example.md
│       ├── scripts/
│       │   ├── sync.mjs
│       │   ├── pre-commit-sync-check.sh
│       │   └── session-start-check.sh
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

`OTC-Fund-SIP-Strategy` 已有完整副本（`.claude/workflows/run-plans.js` + `docs/superpowers/workflows/lib.js` + tests 全在），迁移就是把"复制的源"换成"submodule + sync 产物"，运行时行为等价。新项目接入见 §4.1。

## 4. 使用方法

### 4.1 一次性初始化（新项目接入）

> 以下命令中的 `<engine-repo-url>`、`<sha>`、`<new-sha>` 为运行时变量，接入时替换为实际值：`<engine-repo-url>` = `c:/Users/Alfred/Documents/projects/run-plans-engine`（本地阶段）或 Gitea 远程 URL（阶段四后）；`<sha>` 由 sync.mjs 自动计算并输出，无需手填。

```bash
# 1. 添加 engine submodule
git submodule add <engine-repo-url> .claude/workflow-engine
git commit -m "chore(workflow): add run-plans-engine submodule"

# 2. 生成 .claude/workflows/run-plans.js（named-workflow 注册位置）
node .claude/workflow-engine/scripts/sync.mjs

# 3. commit 派生产物
git add .claude/workflows/run-plans.js
git commit -m "chore(workflow): sync run-plans.js from engine@<sha>"

# 4. 从模板拷贝项目特定配置并改
cp .claude/workflow-engine/examples/workflow.config.example.json workflow.config.json
cp .claude/workflow-engine/examples/lessons.seed.md docs/superpowers/lessons.md
# 编辑 workflow.config.json：填 test_command/full_test_command/spec_path/language 等
# 编辑 lessons.md：保留通用静默失败模式 + 追加项目特定 lessons

# 5. 安装 pre-commit gate
cp .claude/workflow-engine/scripts/pre-commit-sync-check.sh .git/hooks/pre-commit
chmod +x .git/hooks/pre-commit

# 6. （可选）安装 SessionStart 提醒
cp .claude/workflow-engine/scripts/session-start-check.sh <某处>  # 见 §5.4
```

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
| **B. pre-commit 自动 sync** | git commit 时检测 engine 已更新但产物漂移 → 自动跑 sync + stage 产物 → 允许 commit | **半自动（commit 时自愈）** | **采用** |
| **C. SessionStart hook 提醒** | Claude Code session 开始 → 检测 engine 远程是否有新 commit → 有则提醒 | **提醒式（不改工作树）** | **采用** |
| D. 远程 CI 自动 PR | engine push → Gitea Actions 向消费仓库提 PR | 全自动 | 当前规模不划算（2 个消费仓库，CI 配置成本 > 收益） |

**B 解决"忘记 sync"**：用户只需 `git submodule update --remote`，下次 commit 时 pre-commit hook 自动检测 run-plans.js 漂移 → 跑 sync 脚本 → 把更新后的产物 stage 进当前 commit。

**C 解决"不知道 engine 有更新"**：SessionStart hook 跑 `git -C .claude/workflow-engine fetch && git log HEAD..origin/main --oneline`，有新 commit 则提示。只提醒不改工作树。

### 5.2 B 的 pre-commit 脚本逻辑

```bash
#!/bin/bash
# .git/hooks/pre-commit — engine 漂移自动 sync
ENGINE=".claude/workflow-engine"
SRC="$ENGINE/run-plans.js"
DERIVED=".claude/workflows/run-plans.js"

if [ ! -f "$SRC" ] || [ ! -f "$DERIVED" ]; then exit 0; fi

src_sha=$(sha256sum "$SRC" | cut -d' ' -f1)
derived_marked_sha=$(head -3 "$DERIVED" | grep -oE '@sha [a-f0-9]+' | cut -d' ' -f2)

if [ "$src_sha" != "$derived_marked_sha" ]; then
  echo "⚠ run-plans.js 与 engine 源不一致，自动 sync..."
  if ! node "$ENGINE/scripts/sync.mjs"; then
    echo "ERROR: sync 脚本失败，请手动排查"
    exit 1
  fi
  git add "$DERIVED"
  echo "✓ 已 sync run-plans.js 并 stage"
fi
```

### 5.3 安全约束

- pre-commit 只自动 stage `.claude/workflows/run-plans.js` 这一个派生文件（绝不 `git add -A`）
- sync 脚本失败 → 退出非零阻断 commit（不静默放过）
- 派生文件头注入 `@sha` 标注，让 gate 3（bootstrap warning）与 gate 2 可比对

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

集成方式：在消费项目的 `.claude/settings.json` 注册 SessionStart hook 调用此脚本（输出会被 Claude Code session 启动时读取）。

## 6. Gate 控（三层防漂移）

| gate | 位置 | 作用 | 强制性 |
|---|---|---|---|
| gate 1：引擎 sync.test | 通用仓库 CI | 守护 canonical run-plans.js 与 lib.js 字节一致（现有 sync.test 机制，路径调整后保留） | 引擎开发者侧强制 |
| gate 2：消费项目 pre-commit hook | 消费项目 `.git/hooks/pre-commit` | 检测 run-plans.js 漂移 → 自动 sync + stage（§5.2） | 消费项目开发者侧强制 |
| gate 3：bootstrap agent 启动 warning | run-plans.js bootstrap 阶段 | 读派生文件头 `@sha` 与 submodule HEAD 比对，不符 log warning（不 halt） | 软提醒 |

**为什么 gate 3 设为软提醒而非 halt**：bootstrap agent 已有大量 halt 路径（quota/OSCILLATING/audit 等），engine 版本不一致是"派生物可能过期"而非"运行时错误"，halt 会过度阻断开发。warning 进 manifest 供事后核查即可。

> **gate 3 的实现范围**：gate 3 需要 engine 代码本身的小幅改动（bootstrap 读派生文件头 `@sha` + 比对 submodule HEAD 的 run-plans.js sha256）。**本次迁移不实现 gate 3**——保持 engine 代码零改动（§7.2 唯一改代码处仅为 sync.test.js 路径调整），仅交付 gate 1（引擎 sync.test）+ gate 2（消费项目 pre-commit 自愈）双层防护。gate 3 作为**后续可选增强**，待双层防护实际运行一段时间、确认需要运行时软提醒时再单独迭代。

### 6.1 SessionStart hook 集成方式

§5.4 的 `session-start-check.sh` 通过 Claude Code 的 hooks 机制集成。在消费项目的 `.claude/settings.json` 中注册：

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
| `examples/workflow.config.example.json` | 从 lottery 的 config 抽通用字段（test/lint/spec_path/language 留占位） |
| `examples/lessons.seed.md` | 通用静默失败模式（bare except / split-commit / savepoint 等跨项目通用项） |
| `examples/plan-frontmatter.example.md` | plan frontmatter 示例（含 model 字段） |
| `scripts/sync.mjs` | 复制 run-plans.js → 目标 + 注入 @sha 头 |
| `scripts/pre-commit-sync-check.sh` | gate 2：漂移自愈 |
| `scripts/session-start-check.sh` | gate C：engine 更新提醒 |
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

其余断言逻辑不变。

### 7.3 sync.mjs 核心逻辑

```javascript
// scripts/sync.mjs — 复制 canonical run-plans.js 到消费项目的 .claude/workflows/
import fs from 'node:fs'
import path from 'node:path'
import { execSync } from 'node:child_process'

const ENGINE_ROOT = path.resolve(import.meta.dirname, '..')
const SRC = path.join(ENGINE_ROOT, 'run-plans.js')
// 消费项目根 = engine submodule 的上两级（.claude/workflow-engine → .claude → 项目根）
const CONSUMER_ROOT = path.resolve(ENGINE_ROOT, '../..')
const DEST = path.join(CONSUMER_ROOT, '.claude', 'workflows', 'run-plans.js')

// 计算 engine HEAD 的 run-plans.js sha256（gate 比对用）
const sha = execSync(`sha256sum "${SRC}"`, { cwd: ENGINE_ROOT }).toString().split(' ')[0]

const srcContent = fs.readFileSync(SRC, 'utf8')
// 注入 @sha 标注到文件头（第二行注释，不破坏首行注释）
const marked = srcContent.replace(
  /^(\/\/.*\n)/,
  `$1// DO NOT EDIT — generated from workflow-engine@${sha} by sync.mjs\n`)

fs.mkdirSync(path.dirname(DEST), { recursive: true })
fs.writeFileSync(DEST, marked)
console.log(`✓ synced run-plans.js → ${path.relative(CONSUMER_ROOT, DEST)} (@sha ${sha.slice(0,12)})`)
```

### 7.4 迁移顺序（4 阶段）

```
阶段一：建通用仓库（本地）
  1. 在 c:/Users/Alfred/Documents/projects/run-plans-engine 建 git 仓库
  2. 从 lottery-notification 复制 engine 文件（按 7.1 映射表）
  3. 调整 sync.test.js 路径（7.2）+ 创建脚本（7.3）+ examples + README
  4. 跑 node --test tests/ 验证全绿（含 sync.test 字节守护）
  5. git commit -m "chore: initialize run-plans-engine from lottery-notification"

阶段二：lottery-notification 迁移为消费项目
  6. 删除 engine 源文件（.claude/workflows/run-plans.js + docs/superpowers/workflows/ + workflow-design.md + workflow-plans/）
  7. git submodule add <engine-path> .claude/workflow-engine
  8. node .claude/workflow-engine/scripts/sync.mjs → 生成 .claude/workflows/run-plans.js
  9. 配置 hooks：cp .claude/workflow-engine/scripts/pre-commit-sync-check.sh .git/hooks/pre-commit && chmod +x
 10. 验证：Workflow({scriptPath: '.claude/workflows/run-plans.js', args: {...}}) 跑一个轻量 task

阶段三：OTC-Fund-SIP-Strategy 迁移（重复 6-10，删除其旧副本）

阶段四：（后续）推 Gitea + 更新 submodule URL 为远程
```

### 7.5 验证检查清单

| 验证项 | 方法 | 期望 |
|---|---|---|
| 引擎测试全绿 | `cd run-plans-engine && node --test tests/` | 365+ tests pass |
| sync.test 路径正确 | 同上 | sync.test 子项全绿 |
| sync 脚本可用 | `node sync.mjs` | 生成 .claude/workflows/run-plans.js + @sha 头 |
| lottery workflow 可触发 | `Workflow({scriptPath, args:{plan:'01',tasks:['T1']}})` | bootstrap 正常 + 识别已 commit task |
| pre-commit 自愈 | 故意改 .claude/workflows/run-plans.js + git commit | hook 自动 sync + stage |
| SessionStart 提醒 | engine 有新 commit + 开新 session | 提示"engine 有更新" |
| OTC workflow 可触发 | 同 lottery 验证 | bootstrap 正常 |

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
| 首批消费项目 | lottery-notification + OTC-Fund-SIP-Strategy | 一次性解决两个项目的同步问题 |
| gate 3 强度 | 软 warning（非 halt） | engine 版本不一致非运行时错误，halt 过度阻断 |
| gate 3 实现范围 | 本次不实现，后续可选增强 | 保持 engine 代码零改动（仅 sync.test.js 路径调整）；gate 1 + gate 2 双层已足够 |
| lessons.md 处理 | 消费项目保留（含项目特定 + 通用） | 通用部分已在 engine 的 lessons.seed.md，不强拆消费项目 lessons |

## 9. 风险与缓解

| 风险 | 缓解 |
|---|---|
| pre-commit hook 在无 node 环境的消费项目失败 | sync.mjs 是纯 node 脚本（无依赖）；hook 检测 `command -v node` 不存在时 exit 0 放行 + 提醒安装 |
| submodule 在 Windows 上的 CRLF 问题 | `.gitattributes` 在 engine 仓库强制 LF（lottery-notification 已有先例） |
| sync 脚本生成的 @sha 头破坏 run-plans.js 首行注释 | 用正则 `^(//.*\n)` 在首行注释后插入，不覆盖首行 |
| 消费项目忘记配置 pre-commit hook | 文档强调（USAGE.md §接入步骤）；gate 3 的 bootstrap warning 作为兜底提醒 |
| engine 演进破坏旧消费项目（breaking change） | engine 仓库的 CHANGELOG 标注 breaking；消费项目可锁定 submodule SHA 不升级 |
