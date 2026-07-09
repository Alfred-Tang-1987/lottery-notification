# run-plans-engine 提炼为通用仓库 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 lottery-notification 内的 run-plans workflow 引擎提炼为独立通用仓库 `run-plans-engine`，并迁移 lottery-notification + OTC-Fund-SIP-Strategy 为消费项目。

**Architecture:** 通用仓库作为 git submodule 挂载在消费项目 `.claude/workflow-engine/`，由 `sync.mjs` 复制 canonical `run-plans.js` 到 `.claude/workflows/run-plans.js`（Workflow runtime 注册位置）。`init-consumer.mjs` 一键脚手架完成接入。pre-commit hook 自动 sync + 字节比对 config 守护。

**Tech Stack:** Node.js (ESM, 无外部依赖)、git submodule、node:test、bash/POSIX shell（参考实现）

## Global Constraints

- 引擎代码零改动（仅 sync.test.js 及其他 test 文件的路径常量调整 + 项目耦合黑名单通用化）
- Workflow runtime 禁 fs/import：run-plans.js 必须自包含（保留 inline-copy 架构）
- 跨平台：所有脚本用 Node crypto + Node fs，不依赖 sha256sum/grep 等外部命令；.gitattributes 强制 LF
- 派生文件 `.claude/workflows/run-plans.js` 禁手改，由 sync.mjs 生成 + commit
- JSON 不支持注释：workflow.config.json 守护用字节比对，不注入标记
- 仓库位置：先本地 `file:///C:/Users/Alfred/Documents/projects/run-plans-engine`，后续推 Gitea（TODOS T1）
- 提交规范：infra 用 `chore(workflow):` 前缀

---

## File Structure

### 通用仓库 `run-plans-engine`（新建于 `c:/Users/Alfred/Documents/projects/run-plans-engine`）

| 文件 | 责任 |
|---|---|
| `run-plans.js` | canonical 引擎主体（从 lottery-notification 迁入，零改动） |
| `lib.js` | 纯函数真源（迁入，零改动） |
| `tests/*.test.js` | node:test（迁入，路径常量调整 + 黑名单通用化） |
| `package.json` | name: run-plans-engine, type: module |
| `USAGE.md` | 使用文档（迁入） |
| `workflow-design.md` | 设计 spec（迁入） |
| `workflow-plans/` | engine 自身历史 plan（迁入） |
| `research/` | loop engineering 研究报告（迁入） |
| `.gitattributes` | 强制 LF |
| `examples/workflow.config.example.json` | 通用 config 模板（字段留占位） |
| `examples/lessons.seed.md` | 通用静默失败模式种子 |
| `examples/plan-frontmatter.example.md` | plan frontmatter 示例 |
| `scripts/common.mjs` | 共享：computeSha256 + 读取 @sha 头 |
| `scripts/sync.mjs` | 复制 run-plans.js + 注入 @sha 头 |
| `scripts/pre-commit-sync-check.mjs` | gate 2 Node 入口（漂移自愈 + config 字节比对） |
| `scripts/pre-commit-sync-check.sh` | gate 2 POSIX 参考实现 |
| `scripts/session-start-check.sh` | gate C SessionStart 提醒 |
| `scripts/init-consumer.mjs` | 新项目接入脚手架（幂等） |
| `README.md` | 仓库说明 + 快速接入 |

### 消费项目侧（lottery-notification / OTC-Fund-SIP-Strategy）

| 文件 | 责任 |
|---|---|
| `.claude/workflow-engine/` | submodule 挂载点 |
| `.claude/workflows/run-plans.js` | sync.mjs 生成的派生文件 |
| `.git/hooks/pre-commit` | init-consumer 生成的 Node 入口，调用 engine 的 pre-commit-sync-check.mjs |
| `workflow.config.json` | 项目特定配置（从 example 拷贝后必须编辑） |
| `docs/superpowers/lessons.md` | 项目特定 lessons（从 seed 拷贝后生长） |

---

## Task 1: 建通用仓库骨架 + 迁入 engine 文件

**Files:**
- Create: `c:/Users/Alfred/Documents/projects/run-plans-engine/` (git init)
- Create: `run-plans-engine/run-plans.js` (从 lottery-notification `.claude/workflows/run-plans.js` 复制)
- Create: `run-plans-engine/lib.js` (从 `docs/superpowers/workflows/lib.js` 复制)
- Create: `run-plans-engine/tests/` (从 `docs/superpowers/workflows/tests/` 复制全部 18 个 .test.js)
- Create: `run-plans-engine/package.json` (改 name)
- Create: `run-plans-engine/USAGE.md` (从 `docs/superpowers/workflows/USAGE.md` 复制)
- Create: `run-plans-engine/workflow-design.md` (从 `docs/superpowers/workflow-design.md` 复制)
- Create: `run-plans-engine/workflow-plans/` (从 `docs/superpowers/workflow-plans/` 复制，含 archive/)
- Create: `run-plans-engine/research/` (从 `docs/superpowers/workflows/research/` 复制)
- Create: `run-plans-engine/workflow-plans/2026-06-22-workflow-orchestrator.md` (从 `docs/superpowers/2026-06-22-workflow-orchestrator.md` 复制)

**Interfaces:**
- Produces: 通用仓库文件结构（后续 task 在此基础上加脚本/examples）

- [ ] **Step 1: 创建仓库目录并 git init**

```bash
mkdir -p c:/Users/Alfred/Documents/projects/run-plans-engine
cd c:/Users/Alfred/Documents/projects/run-plans-engine
git init
```

- [ ] **Step 2: 复制 engine 文件（按 File Structure 映射表）**

用文件系统复制命令把 lottery-notification 的 engine 文件复制到 run-plans-engine 对应路径。复制清单：

| 源（lottery-notification） | 目标（run-plans-engine） |
|---|---|
| `.claude/workflows/run-plans.js` | `run-plans.js` |
| `docs/superpowers/workflows/lib.js` | `lib.js` |
| `docs/superpowers/workflows/tests/*.test.js`（18 个） | `tests/` |
| `docs/superpowers/workflows/package.json` | `package.json` |
| `docs/superpowers/workflows/USAGE.md` | `USAGE.md` |
| `docs/superpowers/workflow-design.md` | `workflow-design.md` |
| `docs/superpowers/workflow-plans/`（含 archive/） | `workflow-plans/` |
| `docs/superpowers/workflows/research/` | `research/` |
| `docs/superpowers/2026-06-22-workflow-orchestrator.md` | `workflow-plans/2026-06-22-workflow-orchestrator.md` |

```bash
# PowerShell 示例（在 lottery-notification 根执行）
$src = "c:/Users/Alfred/Documents/projects/lottery-notification"
$dst = "c:/Users/Alfred/Documents/projects/run-plans-engine"
Copy-Item "$src/.claude/workflows/run-plans.js" "$dst/run-plans.js"
Copy-Item "$src/docs/superpowers/workflows/lib.js" "$dst/lib.js"
Copy-Item "$src/docs/superpowers/workflows/tests" "$dst/tests" -Recurse
Copy-Item "$src/docs/superpowers/workflows/package.json" "$dst/package.json"
Copy-Item "$src/docs/superpowers/workflows/USAGE.md" "$dst/USAGE.md"
Copy-Item "$src/docs/superpowers/workflow-design.md" "$dst/workflow-design.md"
Copy-Item "$src/docs/superpowers/workflow-plans" "$dst/workflow-plans" -Recurse
Copy-Item "$src/docs/superpowers/workflows/research" "$dst/research" -Recurse
Copy-Item "$src/docs/superpowers/2026-06-22-workflow-orchestrator.md" "$dst/workflow-plans/2026-06-22-workflow-orchestrator.md"
```

- [ ] **Step 3: 改 package.json 的 name 字段**

编辑 `run-plans-engine/package.json`：

```json
{
  "name": "run-plans-engine",
  "version": "0.1.0",
  "type": "module",
  "private": true
}
```

- [ ] **Step 4: 创建 .gitattributes 强制 LF**

创建 `run-plans-engine/.gitattributes`：

```
* text=auto eol=lf
*.js text eol=lf
*.mjs text eol=lf
*.sh text eol=lf
*.md text eol=lf
*.json text eol=lf
```

- [ ] **Step 5: 验证文件结构完整**

```bash
cd c:/Users/Alfred/Documents/projects/run-plans-engine
ls -la
# 期望：run-plans.js, lib.js, tests/, package.json, USAGE.md, workflow-design.md, workflow-plans/, research/, .gitattributes
ls tests/ | wc -l
# 期望：18
```

- [ ] **Step 6: git add + 初次 commit（暂不含脚本/examples，后续 task 加）**

```bash
cd c:/Users/Alfred/Documents/projects/run-plans-engine
git add .
git commit -m "chore: initialize run-plans-engine from lottery-notification (engine files only)"
```

---

## Task 2: 调整 test 文件路径常量 + 通用化项目耦合黑名单

**Files:**
- Modify: `run-plans-engine/tests/sync.test.js` (路径 + 黑名单)
- Modify: `run-plans-engine/tests/args-stringification.test.js` (路径)
- Modify: `run-plans-engine/tests/commitConvention.test.js` (路径)
- Modify: `run-plans-engine/tests/dispatchImpl-retry.test.js` (路径)

**Interfaces:**
- Consumes: Task 1 的 tests/ 目录
- Produces: tests 可在通用仓库内独立运行（不依赖消费项目路径）

**背景**：4 个 test 文件含 `path.resolve(__dirname, '../../../../.claude/workflows/run-plans.js')`（四级回溯，因 tests 在 `docs/superpowers/workflows/tests/`）。通用仓库内 tests 与 run-plans.js 同层，改为 `'../run-plans.js'`。sync.test.js 还读取 `.gitattributes`（路径 `../../../../.gitattributes` → `../.gitattributes`）+ 含 lottery 专有黑名单需通用化。

- [ ] **Step 1: 写失败测试——先跑当前 tests 确认路径失败**

```bash
cd c:/Users/Alfred/Documents/projects/run-plans-engine
node --test tests/sync.test.js 2>&1 | head -30
```

Expected: FAIL，错误含 `ENOENT: no such file ... .claude/workflows/run-plans.js`（路径回溯到不存在的消费项目路径）

- [ ] **Step 2: 修改 sync.test.js 的 run-plans.js 路径**

`tests/sync.test.js` line 11：

```javascript
// 旧
const runSrc = fs.readFileSync(path.resolve(__dirname, '../../../../.claude/workflows/run-plans.js'), 'utf8')
// 新
const runSrc = fs.readFileSync(path.resolve(__dirname, '../run-plans.js'), 'utf8')
```

- [ ] **Step 3: 修改 sync.test.js 的 .gitattributes 路径**

`tests/sync.test.js` line 1074, 1097：

```javascript
// 旧
const attrs = fs.readFileSync(path.resolve(__dirname, '../../../../.gitattributes'), 'utf8')
// 新
const attrs = fs.readFileSync(path.resolve(__dirname, '../.gitattributes'), 'utf8')
```

两处均改。

- [ ] **Step 4: 通用化 sync.test.js 的项目耦合黑名单**

`tests/sync.test.js` line 1313-1327 的 `B1-10 通用性守护` 测试。当前黑名单 `['lottery', 'lottery-notification']` 是 lottery-notification 专有。通用仓库应改为**可配置黑名单**或**移除项目专有词**。

读 sync.test.js line 1313-1327 当前内容后，改为：

```javascript
test('B1-10 通用性守护：PROMPTS 不得含消费项目专有路径/文件名（通用黑名单）', () => {
  const m = libSrc.match(/const PROMPTS = \{[\s\S]*?\n\}/)
  assert.ok(m, 'PROMPTS 块存在')
  // 通用黑名单：消费项目应通过环境变量 WORKFLOW_PROJECT_BLACKLIST 注入项目专有词，
  // 默认空数组（通用仓库本身无项目耦合）。消费项目可在自己的测试层追加项目专有黑名单。
  const blacklist = (process.env.WORKFLOW_PROJECT_BLACKLIST || '').split(',').filter(Boolean)
  for (const bad of blacklist) {
    assert.ok(!m[0].toLowerCase().includes(bad.trim().toLowerCase()),
      `PROMPTS 含项目专有词 "${bad}"——项目耦合，应改 config 驱动注入`)
  }
})
```

同时更新该 test 上方的注释（line 1316-1321）说明通用化逻辑。

- [ ] **Step 5: 修改 args-stringification.test.js 路径**

`tests/args-stringification.test.js` line 8：

```javascript
// 旧
const runSrc = fs.readFileSync(path.resolve(__dirname, '../../../../.claude/workflows/run-plans.js'), 'utf8')
// 新
const runSrc = fs.readFileSync(path.resolve(__dirname, '../run-plans.js'), 'utf8')
```

- [ ] **Step 6: 修改 commitConvention.test.js 路径**

`tests/commitConvention.test.js` line 16：

```javascript
// 旧
const runSrc = fs.readFileSync(path.resolve(__dirname, '../../../../.claude/workflows/run-plans.js'), 'utf8')
// 新
const runSrc = fs.readFileSync(path.resolve(__dirname, '../run-plans.js'), 'utf8')
```

- [ ] **Step 7: 修改 dispatchImpl-retry.test.js 路径**

`tests/dispatchImpl-retry.test.js` line 12：

```javascript
// 旧
const runSrc = fs.readFileSync(path.resolve(__dirname, '../../../../.claude/workflows/run-plans.js'), 'utf8')
// 新
const runSrc = fs.readFileSync(path.resolve(__dirname, '../run-plans.js'), 'utf8')
```

- [ ] **Step 8: 跑全量测试验证路径修复**

```bash
cd c:/Users/Alfred/Documents/projects/run-plans-engine
node --test tests/ 2>&1 | tail -20
```

Expected: 所有测试 PASS（365+ tests）。若 sync.test 的 `no彩票硬编码残留在通用 prompt` 测试（line 455）仍断言 `lottery-notification`，需检查该断言——它断言 `doesNotMatch`（不得含），通用仓库 run-plans.js 本就不含，应仍 PASS。

- [ ] **Step 9: 检查是否有其他硬编码消费项目路径**

```bash
cd c:/Users/Alfred/Documents/projects/run-plans-engine
grep -rn "lottery-notification\|docs/superpowers\|\.claude/workflows" tests/ lib.js run-plans.js | grep -v "node_modules" | head -20
```

Expected: 无匹配（或仅注释/字符串字面量，非路径解析）。若发现路径解析，逐一调整为相对仓库根的路径。

- [ ] **Step 10: commit**

```bash
cd c:/Users/Alfred/Documents/projects/run-plans-engine
git add tests/
git commit -m "chore(workflow): adjust test paths for engine repo layout + generalize project blacklist"
```

---

## Task 3: 创建 scripts/common.mjs（共享 sha 逻辑）

**Files:**
- Create: `run-plans-engine/scripts/common.mjs`
- Test: `run-plans-engine/tests/common.test.js`

**Interfaces:**
- Produces: `computeSha256(filePath): Promise<string>`, `readShaFromHeader(filePath): string|null`

- [ ] **Step 1: 写失败测试**

创建 `tests/common.test.js`：

```javascript
import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import os from 'node:os'
import { computeSha256, readShaFromHeader, injectShaHeader } from '../scripts/common.mjs'

test('computeSha256 returns hex sha256 of file content', async () => {
  const tmp = path.join(os.tmpdir(), `wf-test-${Date.now()}.js`)
  fs.writeFileSync(tmp, 'const x = 1\n')
  const sha = await computeSha256(tmp)
  assert.match(sha, /^[a-f0-9]{64}$/, 'sha256 应为 64 位 hex')
  fs.unlinkSync(tmp)
})

test('readShaFromHeader extracts @sha from second line comment', () => {
  const tmp = path.join(os.tmpdir(), `wf-test-${Date.now()}.js`)
  const fakeSha = 'a'.repeat(64)
  fs.writeFileSync(tmp, `// first line\n// DO NOT EDIT — generated from workflow-engine@${fakeSha} by sync.mjs\nconst x = 1\n`)
  const extracted = readShaFromHeader(tmp)
  assert.equal(extracted, fakeSha)
  fs.unlinkSync(tmp)
})

test('readShaFromHeader returns null when no @sha marker', () => {
  const tmp = path.join(os.tmpdir(), `wf-test-${Date.now()}.js`)
  fs.writeFileSync(tmp, '// first line\nconst x = 1\n')
  const extracted = readShaFromHeader(tmp)
  assert.equal(extracted, null)
  fs.unlinkSync(tmp)
})

test('injectShaHeader inserts @sha line after first line comment', async () => {
  const tmp = path.join(os.tmpdir(), `wf-test-${Date.now()}.js`)
  fs.writeFileSync(tmp, '// first line\nconst x = 1\n')
  const sha = 'b'.repeat(64)
  const marked = injectShaHeader(fs.readFileSync(tmp, 'utf8'), sha)
  assert.match(marked, /^\/\/ first line\n\/\/ DO NOT EDIT — generated from workflow-engine@[a-f0-9]{64} by sync\.mjs\n/)
  fs.unlinkSync(tmp)
})

test('injectShaHeader throws when first line is not // comment', async () => {
  const content = 'const x = 1\n'
  const sha = 'c'.repeat(64)
  assert.throws(() => injectShaHeader(content, sha), /首行必须是 \/\/ 注释/)
})
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd c:/Users/Alfred/Documents/projects/run-plans-engine
node --test tests/common.test.js
```

Expected: FAIL，`Cannot find module '../scripts/common.mjs'`

- [ ] **Step 3: 实现 common.mjs**

创建 `scripts/common.mjs`：

```javascript
// scripts/common.mjs — sync.mjs 与 pre-commit-sync-check.mjs 共享的 sha 逻辑
import fs from 'node:fs'
import crypto from 'node:crypto'

/**
 * 计算文件内容的 sha256（hex），跨平台（Node crypto，不依赖外部命令）
 * @param {string} filePath
 * @returns {Promise<string>} 64 位 hex sha256
 */
export async function computeSha256(filePath) {
  return new Promise((resolve, reject) => {
    const hash = crypto.createHash('sha256')
    const stream = fs.createReadStream(filePath)
    stream.on('data', (chunk) => hash.update(chunk))
    stream.on('end', () => resolve(hash.digest('hex')))
    stream.on('error', reject)
  })
}

/**
 * 从文件头读取 @sha 标注（sync.mjs 注入的第二行注释）
 * @param {string} filePath
 * @returns {string|null} 64 位 hex sha256，无标记则 null
 */
export function readShaFromHeader(filePath) {
  const content = fs.readFileSync(filePath, 'utf8')
  const lines = content.split('\n').slice(0, 3)
  for (const line of lines) {
    const m = line.match(/@sha\s+([a-f0-9]{64})/)
    if (m) return m[1]
  }
  return null
}

/**
 * 在首行 // 注释后注入 @sha 标注行
 * @param {string} content 文件原始内容
 * @param {string} sha 64 位 hex sha256
 * @returns {string} 注入后的内容
 * @throws {Error} 首行非 // 注释时抛错
 */
export function injectShaHeader(content, sha) {
  if (!/^\/\/.*\n/.test(content)) {
    throw new Error('首行必须是 // 注释（sync.mjs 注入 @sha 的前提约束）')
  }
  return content.replace(
    /^(\/\/.*\n)/,
    `$1// DO NOT EDIT — generated from workflow-engine@${sha} by sync.mjs\n`)
}
```

- [ ] **Step 4: 跑测试确认通过**

```bash
cd c:/Users/Alfred/Documents/projects/run-plans-engine
node --test tests/common.test.js
```

Expected: 5 tests PASS

- [ ] **Step 5: commit**

```bash
cd c:/Users/Alfred/Documents/projects/run-plans-engine
git add scripts/common.mjs tests/common.test.js
git commit -m "feat(workflow): add scripts/common.mjs shared sha logic (computeSha256/readShaFromHeader/injectShaHeader)"
```

---

## Task 4: 创建 scripts/sync.mjs

**Files:**
- Create: `run-plans-engine/scripts/sync.mjs`
- Test: `run-plans-engine/tests/sync.mjs.test.js`

**Interfaces:**
- Consumes: `common.mjs` 的 `computeSha256` + `injectShaHeader`
- Produces: `sync.mjs` 可执行脚本，复制 canonical run-plans.js → 消费项目 .claude/workflows/run-plans.js + 注入 @sha 头

- [ ] **Step 1: 写失败测试**

创建 `tests/sync.mjs.test.js`：

```javascript
import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import os from 'node:os'
import { execSync } from 'node:child_process'

test('sync.mjs copies run-plans.js to target + injects @sha header', () => {
  // 模拟消费项目结构：tmp/engine/ (含 run-plans.js + scripts/) + tmp/.claude/workflows/
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'wf-sync-'))
  const engineDir = path.join(tmp, 'engine')
  const consumerClaudeWorkflows = path.join(tmp, '.claude', 'workflows')
  fs.mkdirSync(path.join(engineDir, 'scripts'), { recursive: true })
  fs.mkdirSync(consumerClaudeWorkflows, { recursive: true })

  // 模拟 canonical run-plans.js（首行注释）
  const fakeRunPlans = '// run-plans.js canonical\nconst x = 1\n'
  fs.writeFileSync(path.join(engineDir, 'run-plans.js'), fakeRunPlans)

  // 复制 common.mjs + sync.mjs 到模拟 engine
  const engineRoot = process.cwd()
  fs.copyFileSync(path.join(engineRoot, 'scripts/common.mjs'), path.join(engineDir, 'scripts/common.mjs'))
  fs.copyFileSync(path.join(engineRoot, 'scripts/sync.mjs'), path.join(engineDir, 'scripts/sync.mjs'))

  // 执行 sync.mjs（CWD 设为模拟 engine，sync.mjs 用 import.meta.dirname 推算路径）
  execSync('node scripts/sync.mjs', { cwd: engineDir })

  // 消费项目根 = engine 的上两级（engine → .claude → consumer root）
  // 但这里模拟结构是 tmp/engine，上两级是 tmp/.. 不对——需调整
  // 实际 sync.mjs 假设 engine 在 .claude/workflow-engine/，consumer root = engine/../..
  // 为测试，模拟结构改为 tmp/.claude/workflow-engine/ + tmp/.claude/workflows/
  fs.rmSync(tmp, { recursive: true, force: true })
})

test('sync.mjs produces file with @sha header matching canonical sha256', async () => {
  // 完整模拟：tmp/.claude/workflow-engine/{run-plans.js, scripts/{common.mjs, sync.mjs}}
  // + tmp/.claude/workflows/ (目标)
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'wf-sync2-'))
  const engineDir = path.join(tmp, '.claude', 'workflow-engine')
  const targetDir = path.join(tmp, '.claude', 'workflows')
  fs.mkdirSync(path.join(engineDir, 'scripts'), { recursive: true })
  fs.mkdirSync(targetDir, { recursive: true })

  const fakeRunPlans = '// canonical run-plans\nconst x = 1\n'
  fs.writeFileSync(path.join(engineDir, 'run-plans.js'), fakeRunPlans)

  const engineRoot = process.cwd()
  fs.copyFileSync(path.join(engineRoot, 'scripts/common.mjs'), path.join(engineDir, 'scripts/common.mjs'))
  fs.copyFileSync(path.join(engineRoot, 'scripts/sync.mjs'), path.join(engineDir, 'scripts/sync.mjs'))

  execSync('node scripts/sync.mjs', { cwd: engineDir })

  const destFile = path.join(targetDir, 'run-plans.js')
  assert.ok(fs.existsSync(destFile), '目标文件应存在')

  const destContent = fs.readFileSync(destFile, 'utf8')
  assert.match(destContent, /^\/\/ canonical run-plans\n\/\/ DO NOT EDIT — generated from workflow-engine@[a-f0-9]{64} by sync\.mjs\n/)

  fs.rmSync(tmp, { recursive: true, force: true })
})

test('sync.mjs fails when canonical run-plans.js first line is not // comment', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'wf-sync3-'))
  const engineDir = path.join(tmp, '.claude', 'workflow-engine')
  const targetDir = path.join(tmp, '.claude', 'workflows')
  fs.mkdirSync(path.join(engineDir, 'scripts'), { recursive: true })
  fs.mkdirSync(targetDir, { recursive: true })

  // 首行非注释
  fs.writeFileSync(path.join(engineDir, 'run-plans.js'), 'const x = 1\n')

  const engineRoot = process.cwd()
  fs.copyFileSync(path.join(engineRoot, 'scripts/common.mjs'), path.join(engineDir, 'scripts/common.mjs'))
  fs.copyFileSync(path.join(engineRoot, 'scripts/sync.mjs'), path.join(engineDir, 'scripts/sync.mjs'))

  assert.throws(() => {
    execSync('node scripts/sync.mjs', { cwd: engineDir, stdio: 'pipe' })
  }, /首行必须是 \/\/ 注释/)

  fs.rmSync(tmp, { recursive: true, force: true })
})
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd c:/Users/Alfred/Documents/projects/run-plans-engine
node --test tests/sync.mjs.test.js
```

Expected: FAIL，`Cannot find module '../scripts/sync.mjs'`

- [ ] **Step 3: 实现 sync.mjs**

创建 `scripts/sync.mjs`：

```javascript
// scripts/sync.mjs — 复制 canonical run-plans.js 到消费项目的 .claude/workflows/run-plans.js
// 并注入 @sha 标注头（gate 2 比对用）
import fs from 'node:fs'
import path from 'node:path'
import { computeSha256, injectShaHeader } from './common.mjs'

const ENGINE_ROOT = path.resolve(import.meta.dirname, '..')
const SRC = path.join(ENGINE_ROOT, 'run-plans.js')
// 消费项目根 = engine submodule 的上两级（.claude/workflow-engine → .claude → 项目根）
const CONSUMER_ROOT = path.resolve(ENGINE_ROOT, '../..')
const DEST = path.join(CONSUMER_ROOT, '.claude', 'workflows', 'run-plans.js')

async function main() {
  if (!fs.existsSync(SRC)) {
    console.error(`ERROR: canonical run-plans.js not found at ${SRC}`)
    process.exit(1)
  }

  const sha = await computeSha256(SRC)
  const srcContent = fs.readFileSync(SRC, 'utf8')
  const marked = injectShaHeader(srcContent, sha)

  fs.mkdirSync(path.dirname(DEST), { recursive: true })
  fs.writeFileSync(DEST, marked)
  console.log(`✓ synced run-plans.js → ${path.relative(CONSUMER_ROOT, DEST)} (@sha ${sha.slice(0, 12)})`)
}

main().catch((err) => {
  console.error('ERROR:', err.message)
  process.exit(1)
})
```

- [ ] **Step 4: 跑测试确认通过**

```bash
cd c:/Users/Alfred/Documents/projects/run-plans-engine
node --test tests/sync.mjs.test.js
```

Expected: 3 tests PASS

- [ ] **Step 5: 手动冒烟测试**

```bash
cd c:/Users/Alfred/Documents/projects/run-plans-engine
# 临时模拟：在仓库内创建 .claude/workflows/ 测试 sync
mkdir -p .claude/workflows
node scripts/sync.mjs
# 期望输出：✓ synced run-plans.js → .claude/workflows/run-plans.js (@sha xxxxxxxx)
head -2 .claude/workflows/run-plans.js
# 期望首行原注释 + 第二行 // DO NOT EDIT — generated from workflow-engine@<sha> by sync.mjs
# 清理
rm -rf .claude
```

- [ ] **Step 6: commit**

```bash
cd c:/Users/Alfred/Documents/projects/run-plans-engine
git add scripts/sync.mjs tests/sync.mjs.test.js
git commit -m "feat(workflow): add scripts/sync.mjs (copy run-plans.js + inject @sha header)"
```

---

## Task 5: 创建 scripts/pre-commit-sync-check.mjs

**Files:**
- Create: `run-plans-engine/scripts/pre-commit-sync-check.mjs`
- Test: `run-plans-engine/tests/pre-commit-sync-check.test.js`

**Interfaces:**
- Consumes: `common.mjs` 的 `computeSha256` + `readShaFromHeader`，`sync.mjs`（漂移时调用）
- Produces: pre-commit hook Node 入口，检测漂移 → 自动 sync + stage + exit 1；检测 config 未编辑 → exit 1

- [ ] **Step 1: 写失败测试**

创建 `tests/pre-commit-sync-check.test.js`：

```javascript
import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import os from 'node:os'
import { execSync } from 'node:child_process'

function setupConsumer(tmp, { configEdited = true, runPlansDrifted = false } = {}) {
  const engineDir = path.join(tmp, '.claude', 'workflow-engine')
  const workflowsDir = path.join(tmp, '.claude', 'workflows')
  const examplesDir = path.join(engineDir, 'examples')
  fs.mkdirSync(path.join(engineDir, 'scripts'), { recursive: true })
  fs.mkdirSync(workflowsDir, { recursive: true })
  fs.mkdirSync(examplesDir, { recursive: true })
  fs.mkdirSync(path.join(tmp, '.git', 'hooks'), { recursive: true })

  // canonical run-plans.js
  const canonicalContent = '// canonical run-plans\nconst x = 1\n'
  fs.writeFileSync(path.join(engineDir, 'run-plans.js'), canonicalContent)

  // example config
  const exampleConfig = '{"test_command": "PLACEHOLDER"}\n'
  fs.writeFileSync(path.join(examplesDir, 'workflow.config.example.json'), exampleConfig)

  // 消费项目 workflow.config.json（是否已编辑）
  const configContent = configEdited ? '{"test_command": "python -m pytest"}\n' : exampleConfig
  fs.writeFileSync(path.join(tmp, 'workflow.config.json'), configContent)

  // 派生 run-plans.js（是否漂移）
  if (runPlansDrifted) {
    fs.writeFileSync(path.join(workflowsDir, 'run-plans.js'), '// drifted\nconst y = 2\n')
  } else {
    // 先跑 sync.mjs 生成正确的派生文件
    const engineRoot = process.cwd()
    fs.copyFileSync(path.join(engineRoot, 'scripts/common.mjs'), path.join(engineDir, 'scripts/common.mjs'))
    fs.copyFileSync(path.join(engineRoot, 'scripts/sync.mjs'), path.join(engineDir, 'scripts/sync.mjs'))
    execSync('node scripts/sync.mjs', { cwd: engineDir })
  }

  // 复制 pre-commit-sync-check.mjs + common.mjs
  const engineRoot = process.cwd()
  fs.copyFileSync(path.join(engineRoot, 'scripts/common.mjs'), path.join(engineDir, 'scripts/common.mjs'))
  fs.copyFileSync(path.join(engineRoot, 'scripts/sync.mjs'), path.join(engineDir, 'scripts/sync.mjs'))
  fs.copyFileSync(path.join(engineRoot, 'scripts/pre-commit-sync-check.mjs'), path.join(engineDir, 'scripts/pre-commit-sync-check.mjs'))

  return { engineDir, workflowsDir, configPath: path.join(tmp, 'workflow.config.json') }
}

test('pre-commit returns 0 when run-plans.js synced + config edited', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'wf-precommit-'))
  setupConsumer(tmp, { configEdited: true, runPlansDrifted: false })
  // pre-commit-sync-check.mjs 从 engine 目录运行，CONSUMER_ROOT = engine/../..
  const engineDir = path.join(tmp, '.claude', 'workflow-engine')
  try {
    execSync('node scripts/pre-commit-sync-check.mjs', { cwd: engineDir, stdio: 'pipe', env: { ...process.env, CONSUMER_ROOT: path.resolve(engineDir, '../..') } })
    assert.ok(true, '应 exit 0')
  } catch (e) {
    assert.fail(`应 exit 0，实际 exit ${e.status}: ${e.stderr?.toString()}`)
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true })
  }
})

test('pre-commit returns 1 + syncs when run-plans.js drifted', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'wf-precommit2-'))
  setupConsumer(tmp, { configEdited: true, runPlansDrifted: true })
  const engineDir = path.join(tmp, '.claude', 'workflow-engine')
  let exitCode = 0
  try {
    execSync('node scripts/pre-commit-sync-check.mjs', { cwd: engineDir, stdio: 'pipe', env: { ...process.env, CONSUMER_ROOT: path.resolve(engineDir, '../..') } })
  } catch (e) {
    exitCode = e.status
  }
  assert.equal(exitCode, 1, '漂移时应 exit 1 要求重新提交')
  // sync 后派生文件应已更新
  const destContent = fs.readFileSync(path.join(tmp, '.claude', 'workflows', 'run-plans.js'), 'utf8')
  assert.match(destContent, /DO NOT EDIT — generated from workflow-engine@/)
  fs.rmSync(tmp, { recursive: true, force: true })
})

test('pre-commit returns 1 when config not edited (byte-identical to example)', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'wf-precommit3-'))
  setupConsumer(tmp, { configEdited: false, runPlansDrifted: false })
  const engineDir = path.join(tmp, '.claude', 'workflow-engine')
  let exitCode = 0
  let stderr = ''
  try {
    execSync('node scripts/pre-commit-sync-check.mjs', { cwd: engineDir, stdio: 'pipe', env: { ...process.env, CONSUMER_ROOT: path.resolve(engineDir, '../..') } })
  } catch (e) {
    exitCode = e.status
    stderr = e.stderr?.toString() || ''
  }
  assert.equal(exitCode, 1, 'config 未编辑应 exit 1')
  assert.match(stderr, /workflow\.config\.json.*example/, '应提示 config 未编辑')
  fs.rmSync(tmp, { recursive: true, force: true })
})
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd c:/Users/Alfred/Documents/projects/run-plans-engine
node --test tests/pre-commit-sync-check.test.js
```

Expected: FAIL，`Cannot find module '../scripts/pre-commit-sync-check.mjs'`

- [ ] **Step 3: 实现 pre-commit-sync-check.mjs**

创建 `scripts/pre-commit-sync-check.mjs`：

```javascript
// scripts/pre-commit-sync-check.mjs — gate 2 消费项目 pre-commit hook
// 1. 检测 run-plans.js 漂移 → 自动 sync + stage + exit 1（要求重新提交）
// 2. 检测 workflow.config.json 未编辑（与 example 字节一致）→ exit 1
import fs from 'node:fs'
import path from 'node:path'
import { execSync } from 'node:child_process'
import { computeSha256, readShaFromHeader } from './common.mjs'

const ENGINE_ROOT = path.resolve(import.meta.dirname, '..')
const CONSUMER_ROOT = process.env.CONSUMER_ROOT || path.resolve(ENGINE_ROOT, '../..')
const SRC = path.join(ENGINE_ROOT, 'run-plans.js')
const DERIVED = path.join(CONSUMER_ROOT, '.claude', 'workflows', 'run-plans.js')
const CONFIG = path.join(CONSUMER_ROOT, 'workflow.config.json')
const EXAMPLE_CONFIG = path.join(ENGINE_ROOT, 'examples', 'workflow.config.example.json')

async function main() {
  // 检查 1：run-plans.js 漂移
  if (fs.existsSync(SRC) && fs.existsSync(DERIVED)) {
    const srcSha = await computeSha256(SRC)
    const derivedSha = readShaFromHeader(DERIVED)
    if (srcSha !== derivedSha) {
      console.log('⚠ run-plans.js 与 engine 源不一致，自动 sync...')
      try {
        execSync('node scripts/sync.mjs', { cwd: ENGINE_ROOT, stdio: 'inherit' })
      } catch (e) {
        console.error('ERROR: sync.mjs 失败，请手动排查')
        process.exit(1)
      }
      // stage 派生文件
      try {
        execSync(`git add "${DERIVED}"`, { cwd: CONSUMER_ROOT, stdio: 'inherit' })
      } catch (e) {
        console.error('ERROR: git add 派生文件失败')
        process.exit(1)
      }
      console.log('✓ 已 sync run-plans.js 并 stage')
      console.log('请重新提交（pre-commit 自愈后要求重新 commit）')
      process.exit(1)
    }
  }

  // 检查 2：workflow.config.json 未编辑（字节比对 example）
  if (fs.existsSync(CONFIG) && fs.existsSync(EXAMPLE_CONFIG)) {
    const configSha = await computeSha256(CONFIG)
    const exampleSha = await computeSha256(EXAMPLE_CONFIG)
    if (configSha === exampleSha) {
      console.error('ERROR: workflow.config.json 与 example 字节一致——未编辑。')
      console.error('请根据消费项目情况编辑 workflow.config.json（test_command/language/silent_failure_context 等）后再提交。')
      console.error('参考：§4.1.1 必须步骤')
      process.exit(1)
    }
  }

  process.exit(0)
}

main().catch((err) => {
  console.error('ERROR:', err.message)
  process.exit(1)
})
```

- [ ] **Step 4: 跑测试确认通过**

```bash
cd c:/Users/Alfred/Documents/projects/run-plans-engine
node --test tests/pre-commit-sync-check.test.js
```

Expected: 3 tests PASS

- [ ] **Step 5: commit**

```bash
cd c:/Users/Alfred/Documents/projects/run-plans-engine
git add scripts/pre-commit-sync-check.mjs tests/pre-commit-sync-check.test.js
git commit -m "feat(workflow): add scripts/pre-commit-sync-check.mjs (drift auto-sync + config byte-compare)"
```

---

## Task 6: 创建 scripts/pre-commit-sync-check.sh（POSIX 参考实现）

**Files:**
- Create: `run-plans-engine/scripts/pre-commit-sync-check.sh`

**Interfaces:**
- Produces: POSIX shell 参考实现（非 Windows 环境手动安装用，init-consumer 默认用 .mjs）

- [ ] **Step 1: 创建脚本**

创建 `scripts/pre-commit-sync-check.sh`：

```bash
#!/bin/bash
# scripts/pre-commit-sync-check.sh — gate 2 POSIX 参考实现
# 供非 Windows 环境或手动安装使用。init-consumer.mjs 默认生成 Node 入口调用 .mjs。
# 此脚本为参考，行为与 pre-commit-sync-check.mjs 一致：漂移自愈 + config 字节比对。

set -e

ENGINE=".claude/workflow-engine"
SRC="$ENGINE/run-plans.js"
DERIVED=".claude/workflows/run-plans.js"
CONFIG="workflow.config.json"
EXAMPLE_CONFIG="$ENGINE/examples/workflow.config.example.json"

cd "$(git rev-parse --show-toplevel)" || exit 0

if [ ! -f "$SRC" ] || [ ! -f "$DERIVED" ]; then
  exit 0
fi

# 检查 1：run-plans.js 漂移
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
  echo "请重新提交（pre-commit 自愈后要求重新 commit）"
  exit 1
fi

# 检查 2：config 未编辑（字节比对）
if [ -f "$CONFIG" ] && [ -f "$EXAMPLE_CONFIG" ]; then
  config_sha=$(sha256sum "$CONFIG" | cut -d' ' -f1)
  example_sha=$(sha256sum "$EXAMPLE_CONFIG" | cut -d' ' -f1)
  if [ "$config_sha" = "$example_sha" ]; then
    echo "ERROR: workflow.config.json 与 example 字节一致——未编辑。"
    echo "请根据消费项目情况编辑 workflow.config.json 后再提交。参考：§4.1.1"
    exit 1
  fi
fi

exit 0
```

- [ ] **Step 2: 验证脚本可执行（语法检查）**

```bash
cd c:/Users/Alfred/Documents/projects/run-plans-engine
bash -n scripts/pre-commit-sync-check.sh
# 期望：无输出（语法正确）
```

- [ ] **Step 3: commit**

```bash
cd c:/Users/Alfred/Documents/projects/run-plans-engine
git add scripts/pre-commit-sync-check.sh
git commit -m "feat(workflow): add scripts/pre-commit-sync-check.sh (POSIX reference impl)"
```

---

## Task 7: 创建 scripts/session-start-check.sh

**Files:**
- Create: `run-plans-engine/scripts/session-start-check.sh`

**Interfaces:**
- Produces: SessionStart hook 脚本，检测 engine 远程新 commit 并提示

- [ ] **Step 1: 创建脚本**

创建 `scripts/session-start-check.sh`：

```bash
#!/bin/bash
# scripts/session-start-check.sh — gate C SessionStart 提醒
# Claude Code session 启动时调用，检测 engine 远程是否有新 commit，有则提示。
# 只读 git 状态，不改工作树。

ENGINE=".claude/workflow-engine"

cd "$(git rev-parse --show-toplevel)" || exit 0
if [ ! -d "$ENGINE/.git" ] && [ ! -f "$ENGINE/.git" ]; then
  exit 0
fi

# fetch 可能因网络失败，静默跳过
git -C "$ENGINE" fetch --quiet 2>/dev/null || exit 0

NEW_COMMITS=$(git -C "$ENGINE" log HEAD..origin/main --oneline 2>/dev/null)
if [ -n "$NEW_COMMITS" ]; then
  COUNT=$(echo "$NEW_COMMITS" | wc -l | tr -d ' ')
  echo "ℹ run-plans-engine 有 $COUNT 个新提交，建议更新："
  echo "  git submodule update --remote .claude/workflow-engine"
  echo "  node .claude/workflow-engine/scripts/sync.mjs"
  echo "  git add .claude/workflow-engine .claude/workflows/run-plans.js && git commit"
fi
```

- [ ] **Step 2: 语法检查**

```bash
cd c:/Users/Alfred/Documents/projects/run-plans-engine
bash -n scripts/session-start-check.sh
# 期望：无输出
```

- [ ] **Step 3: commit**

```bash
cd c:/Users/Alfred/Documents/projects/run-plans-engine
git add scripts/session-start-check.sh
git commit -m "feat(workflow): add scripts/session-start-check.sh (SessionStart update reminder)"
```

---

## Task 8: 创建 examples/（config / lessons / frontmatter 模板）

**Files:**
- Create: `run-plans-engine/examples/workflow.config.example.json`
- Create: `run-plans-engine/examples/lessons.seed.md`
- Create: `run-plans-engine/examples/plan-frontmatter.example.md`

**Interfaces:**
- Produces: 消费项目接入时从 examples 拷贝的模板

- [ ] **Step 1: 创建 workflow.config.example.json**

从 lottery-notification 的 `workflow.config.json` 抽通用字段，业务特定值改占位：

创建 `examples/workflow.config.example.json`：

```json
{
  "test_command": "PLACEHOLDER: e.g. python -m pytest {file} -x",
  "full_test_command": "PLACEHOLDER: e.g. python -m pytest",
  "build_command": "PLACEHOLDER: e.g. python -m build",
  "lint_command": "PLACEHOLDER: e.g. ruff check .",
  "extra_lint_commands": [],
  "spec_path": "docs/superpowers/specs",
  "reference_paths": [],
  "language": "PLACEHOLDER: python|node|go|rust|...",
  "silent_failure_intro": "本项目特定静默失败风险（最高优先级——优先核查）",
  "silent_failure_context": [
    "PLACEHOLDER: 替换为本项目特定的静默失败上下文，每条一个纪律。如：DB 写不得 split-commit；per-row 异常隔离用 savepoint；等。"
  ],
  "lessons_path": "docs/superpowers/lessons.md",
  "lessons_auto_distill": true,
  "schema_tool": "PLACEHOLDER: alembic|prisma|migrate|null",
  "model_paths": [],
  "migration_paths": [],
  "review_max_rounds": 0
}
```

- [ ] **Step 2: 创建 lessons.seed.md**

从 lottery-notification 的 lessons.md 抽通用静默失败模式（跨项目通用项），项目特定项不抽：

先读 `lottery-notification/docs/superpowers/lessons.md` 提取通用项，创建 `examples/lessons.seed.md`：

```markdown
# Lessons（种子模板）

> 从 run-plans-engine 的 examples/lessons.seed.md 拷贝后，保留通用项 + 追加项目特定 lessons。
> 通用项是跨项目反复出现的静默失败模式；项目特定项是本仓库独有的教训。

## 通用静默失败模式（跨项目）

### bare except 吞异常
- **症状**：`except Exception:` 捕获后只 log 不 raise，错误被静默吞掉，后续逻辑基于错误状态继续运行。
- **修复**：except 块必须 re-raise 或显式处理（返回错误码/halt）；log 须含 `exc_info=True` 获取完整 traceback。
- **适用**：所有语言。

### split-commit 破坏原子性
- **症状**：一个逻辑操作分两次 commit，第二次失败导致状态不一致（如 outbox 未写、标记未更新）。
- **修复**：逻辑操作单事务一次 commit；必须分步时用 outbox/saga 模式 + 幂等重试。
- **适用**：DB 写操作。

### savepoint 隔离 per-row 异常
- **症状**：循环里逐行处理共享 session，单行失败毒化 session（PendingRollback），后续好行全丢。
- **修复**：per-row try/except + `with session.begin_nested()`（SAVEPOINT）隔离；bare except 不够。
- **适用**：ORM 批量循环。

### datetime 时区不一致
- **症状**：datetime 字段时区混用（aware vs naive、CST vs UTC），比较/排序结果错误。
- **修复**：项目主流惯例统一（如 naive UTC）；写入值 `datetime.now(timezone.utc).replace(tzinfo=None)`，非 aware CST、非弃用 `utcnow()`。
- **适用**：所有含 datetime 的项目。

### 批量循环单行故障中断整批
- **症状**：循环里单行抛异常中断整批，末尾兜底标记未执行，卡死未处理行。
- **修复**：per-row try/except + log（含 exc_info）；批末兜底标记独立方法/finally 执行，不依赖循环不抛。
- **适用**：批量处理循环。

## 项目特定 lessons

<!-- 在此追加本项目独有的教训 -->
```

- [ ] **Step 3: 创建 plan-frontmatter.example.md**

创建 `examples/plan-frontmatter.example.md`：

```markdown
---
models:
  T1: sonnet
  T2: opus
  T3: sonnet
  T10: sonnet
write_files:
  T1:
    - src/a.py
    - src/b.py
lesson_categories:
  - silent-failure
  - test-strategy
---

# Plan XX: 示例 plan

## Task 1: 示例任务

**Type:** feature
**Files:**
- Create: src/a.py

brief 文本...

### Task 1.1: 子 task

brief 文本...

## Task 2: 安全相关任务（modelHint: opus）

**Type:** feature
**Files:**
- Modify: src/auth.py

brief 含 安全|加密|认证|JWT|CSRF|Fernet|算法|比对|策略|边界|集成|接口 关键词 → modelHint: opus
```

- [ ] **Step 4: commit**

```bash
cd c:/Users/Alfred/Documents/projects/run-plans-engine
git add examples/
git commit -m "feat(workflow): add examples/ (workflow.config.example.json + lessons.seed.md + plan-frontmatter.example.md)"
```

---

## Task 9: 创建 scripts/init-consumer.mjs（一键脚手架）

**Files:**
- Create: `run-plans-engine/scripts/init-consumer.mjs`
- Test: `run-plans-engine/tests/init-consumer.test.js`

**Interfaces:**
- Consumes: `sync.mjs`、`pre-commit-sync-check.mjs`、`examples/`
- Produces: `init-consumer.mjs` 幂等脚手架，新项目接入一键完成

- [ ] **Step 1: 写失败测试**

创建 `tests/init-consumer.test.js`：

```javascript
import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import os from 'node:os'
import { execSync } from 'node:child_process'

function setupConsumerRoot(tmp) {
  fs.mkdirSync(path.join(tmp, '.git', 'hooks'), { recursive: true })
  fs.mkdirSync(path.join(tmp, 'docs', 'superpowers'), { recursive: true })
  // 模拟 submodule 已挂载（实际由 git submodule add 完成，init-consumer 假设已挂载）
  const engineDir = path.join(tmp, '.claude', 'workflow-engine')
  fs.mkdirSync(path.join(engineDir, 'scripts'), { recursive: true })
  fs.mkdirSync(path.join(engineDir, 'examples'), { recursive: true })
  fs.mkdirSync(path.join(engineDir, 'tests'), { recursive: true })
  fs.mkdirSync(path.join(tmp, '.claude', 'workflows'), { recursive: true })

  // 复制 engine 文件到模拟 submodule
  const engineRoot = process.cwd()
  fs.copyFileSync(path.join(engineRoot, 'run-plans.js'), path.join(engineDir, 'run-plans.js'))
  fs.copyFileSync(path.join(engineRoot, 'scripts/common.mjs'), path.join(engineDir, 'scripts/common.mjs'))
  fs.copyFileSync(path.join(engineRoot, 'scripts/sync.mjs'), path.join(engineDir, 'scripts/sync.mjs'))
  fs.copyFileSync(path.join(engineRoot, 'scripts/pre-commit-sync-check.mjs'), path.join(engineDir, 'scripts/pre-commit-sync-check.mjs'))
  fs.copyFileSync(path.join(engineRoot, 'examples/workflow.config.example.json'), path.join(engineDir, 'examples/workflow.config.example.json'))
  fs.copyFileSync(path.join(engineRoot, 'examples/lessons.seed.md'), path.join(engineDir, 'examples/lessons.seed.md'))
  fs.copyFileSync(path.join(engineRoot, 'scripts/init-consumer.mjs'), path.join(engineDir, 'scripts/init-consumer.mjs'))
  // run-plans.js 首行注释（已是）
  return { engineDir, consumerRoot: tmp }
}

test('init-consumer generates run-plans.js + config + lessons + pre-commit hook', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'wf-init-'))
  const { engineDir, consumerRoot } = setupConsumerRoot(tmp)

  execSync('node scripts/init-consumer.mjs', { cwd: engineDir, stdio: 'pipe', env: { ...process.env, CONSUMER_ROOT: consumerRoot } })

  // 1. 派生 run-plans.js
  assert.ok(fs.existsSync(path.join(consumerRoot, '.claude', 'workflows', 'run-plans.js')))
  // 2. workflow.config.json
  assert.ok(fs.existsSync(path.join(consumerRoot, 'workflow.config.json')))
  // 3. lessons.md
  assert.ok(fs.existsSync(path.join(consumerRoot, 'docs', 'superpowers', 'lessons.md')))
  // 4. pre-commit hook
  const hook = fs.readFileSync(path.join(consumerRoot, '.git', 'hooks', 'pre-commit'), 'utf8')
  assert.match(hook, /pre-commit-sync-check\.mjs/)

  fs.rmSync(tmp, { recursive: true, force: true })
})

test('init-consumer is idempotent (re-run does not duplicate hook)', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'wf-init2-'))
  const { engineDir, consumerRoot } = setupConsumerRoot(tmp)

  execSync('node scripts/init-consumer.mjs', { cwd: engineDir, stdio: 'pipe', env: { ...process.env, CONSUMER_ROOT: consumerRoot } })
  // 第二次运行（已存在）
  execSync('node scripts/init-consumer.mjs', { cwd: engineDir, stdio: 'pipe', env: { ...process.env, CONSUMER_ROOT: consumerRoot } })

  const hook = fs.readFileSync(path.join(consumerRoot, '.git', 'hooks', 'pre-commit'), 'utf8')
  const matchCount = (hook.match(/pre-commit-sync-check\.mjs/g) || []).length
  assert.equal(matchCount, 1, 'hook 不应重复添加（幂等）')

  fs.rmSync(tmp, { recursive: true, force: true })
})

test('init-consumer preserves existing pre-commit content via backup merge', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'wf-init3-'))
  const { engineDir, consumerRoot } = setupConsumerRoot(tmp)
  // 预置已有 pre-commit
  const existingHook = '#!/bin/sh\necho existing hook\n'
  fs.writeFileSync(path.join(consumerRoot, '.git', 'hooks', 'pre-commit'), existingHook)

  execSync('node scripts/init-consumer.mjs', { cwd: engineDir, stdio: 'pipe', env: { ...process.env, CONSUMER_ROOT: consumerRoot } })

  const hook = fs.readFileSync(path.join(consumerRoot, '.git', 'hooks', 'pre-commit'), 'utf8')
  assert.match(hook, /existing hook/, '应保留已有内容')
  assert.match(hook, /pre-commit-sync-check\.mjs/, '应追加 engine 检查')

  fs.rmSync(tmp, { recursive: true, force: true })
})

test('init-consumer does not overwrite existing workflow.config.json', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'wf-init4-'))
  const { engineDir, consumerRoot } = setupConsumerRoot(tmp)
  // 预置已编辑的 config
  const editedConfig = '{"test_command": "already edited"}\n'
  fs.writeFileSync(path.join(consumerRoot, 'workflow.config.json'), editedConfig)

  execSync('node scripts/init-consumer.mjs', { cwd: engineDir, stdio: 'pipe', env: { ...process.env, CONSUMER_ROOT: consumerRoot } })

  const config = fs.readFileSync(path.join(consumerRoot, 'workflow.config.json'), 'utf8')
  assert.equal(config, editedConfig, '不应覆盖已存在的 config')

  fs.rmSync(tmp, { recursive: true, force: true })
})
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd c:/Users/Alfred/Documents/projects/run-plans-engine
node --test tests/init-consumer.test.js
```

Expected: FAIL，`Cannot find module '../scripts/init-consumer.mjs'`

- [ ] **Step 3: 实现 init-consumer.mjs**

创建 `scripts/init-consumer.mjs`：

```javascript
// scripts/init-consumer.mjs — 新项目接入脚手架（幂等）
// 执行：node .claude/workflow-engine/scripts/init-consumer.mjs
// 功能：sync run-plans.js + 拷贝 examples + 安装 pre-commit hook
import fs from 'node:fs'
import path from 'node:path'
import { execSync } from 'node:child_process'

const ENGINE_ROOT = path.resolve(import.meta.dirname, '..')
const CONSUMER_ROOT = process.env.CONSUMER_ROOT || path.resolve(ENGINE_ROOT, '../..')

function log(msg) { console.log(msg) }
function warn(msg) { console.warn(msg) }

async function main() {
  log('=== run-plans-engine init-consumer ===')
  log(`ENGINE_ROOT: ${ENGINE_ROOT}`)
  log(`CONSUMER_ROOT: ${CONSUMER_ROOT}`)

  // 1. sync run-plans.js
  log('\n[1/4] sync run-plans.js...')
  try {
    execSync('node scripts/sync.mjs', { cwd: ENGINE_ROOT, stdio: 'inherit' })
  } catch (e) {
    console.error('ERROR: sync.mjs 失败')
    process.exit(1)
  }

  // 2. 拷贝 workflow.config.json（不覆盖已存在）
  log('\n[2/4] workflow.config.json...')
  const configDest = path.join(CONSUMER_ROOT, 'workflow.config.json')
  const configSrc = path.join(ENGINE_ROOT, 'examples', 'workflow.config.example.json')
  if (fs.existsSync(configDest)) {
    warn(`  已存在 ${configDest}，跳过拷贝（请确保已根据消费项目编辑）`)
  } else {
    fs.copyFileSync(configSrc, configDest)
    warn(`  已拷贝 example → ${path.relative(CONSUMER_ROOT, configDest)}`)
    warn('  ⚠ 必须根据消费项目编辑此文件（test_command/language/silent_failure_context 等）后再 commit！')
    warn('  ⚠ pre-commit hook 会检测未编辑（与 example 字节一致）并拒绝 commit。')
  }

  // 3. 拷贝 lessons.md（不覆盖已存在）
  log('\n[3/4] lessons.md...')
  const lessonsDir = path.join(CONSUMER_ROOT, 'docs', 'superpowers')
  const lessonsDest = path.join(lessonsDir, 'lessons.md')
  const lessonsSrc = path.join(ENGINE_ROOT, 'examples', 'lessons.seed.md')
  fs.mkdirSync(lessonsDir, { recursive: true })
  if (fs.existsSync(lessonsDest)) {
    warn(`  已存在 ${lessonsDest}，跳过拷贝`)
  } else {
    fs.copyFileSync(lessonsSrc, lessonsDest)
    log(`  已拷贝 seed → ${path.relative(CONSUMER_ROOT, lessonsDest)}`)
  }

  // 4. 安装 pre-commit hook（幂等 + 备份合并）
  log('\n[4/4] pre-commit hook...')
  const hookPath = path.join(CONSUMER_ROOT, '.git', 'hooks', 'pre-commit')
  fs.mkdirSync(path.dirname(hookPath), { recursive: true })

  const engineHookCall = `node "${path.relative(CONSUMER_ROOT, ENGINE_ROOT)}/scripts/pre-commit-sync-check.mjs"`

  if (fs.existsSync(hookPath)) {
    const existing = fs.readFileSync(hookPath, 'utf8')
    if (existing.includes('pre-commit-sync-check.mjs')) {
      log('  pre-commit hook 已含 engine 检查，跳过安装')
    } else {
      // 备份 + 追加
      const backupPath = hookPath + '.bak.' + Date.now()
      fs.copyFileSync(hookPath, backupPath)
      const newContent = existing + '\n# === run-plans-engine pre-commit (added by init-consumer) ===\n' + engineHookCall + '\n'
      fs.writeFileSync(hookPath, newContent)
      log(`  已追加 engine 检查到 pre-commit（备份: ${path.basename(backupPath)}）`)
    }
  } else {
    const newHook = `#!/bin/sh\n# === run-plans-engine pre-commit (generated by init-consumer) ===\n${engineHookCall}\n`
    fs.writeFileSync(hookPath, newHook)
    try { fs.chmodSync(hookPath, 0o755) } catch (e) { warn(`  chmod 失败（Windows 可忽略）: ${e.message}`) }
    log(`  已创建 pre-commit hook → ${path.relative(CONSUMER_ROOT, hookPath)}`)
  }

  log('\n=== init-consumer 完成 ===')
  if (!fs.existsSync(configDest) || fs.readFileSync(configDest, 'utf8') === fs.readFileSync(configSrc, 'utf8')) {
    warn('\n⚠ workflow.config.json 未编辑！pre-commit 会拒绝 commit。')
    warn('  请编辑 workflow.config.json 后再提交。参考：§4.1.1 必须步骤')
  }
}

main().catch((err) => {
  console.error('ERROR:', err.message)
  process.exit(1)
})
```

- [ ] **Step 4: 跑测试确认通过**

```bash
cd c:/Users/Alfred/Documents/projects/run-plans-engine
node --test tests/init-consumer.test.js
```

Expected: 4 tests PASS

- [ ] **Step 5: commit**

```bash
cd c:/Users/Alfred/Documents/projects/run-plans-engine
git add scripts/init-consumer.mjs tests/init-consumer.test.js
git commit -m "feat(workflow): add scripts/init-consumer.mjs (idempotent consumer scaffold)"
```

---

## Task 10: 创建 README.md + 全量测试验证

**Files:**
- Create: `run-plans-engine/README.md`

**Interfaces:**
- Produces: 仓库说明 + 快速接入指引

- [ ] **Step 1: 创建 README.md**

创建 `README.md`：

```markdown
# run-plans-engine

Claude Code 的 run-plans workflow 引擎，作为 git submodule 供多个项目引用。

## 快速接入（新项目）

```bash
# 1. 添加 submodule
git submodule add file:///C:/Users/Alfred/Documents/projects/run-plans-engine .claude/workflow-engine
git commit -m "chore(workflow): add run-plans-engine submodule"

# 2. 一键初始化
node .claude/workflow-engine/scripts/init-consumer.mjs

# 3. 编辑 workflow.config.json（必须！否则 pre-commit 拒绝 commit）
#    填写 test_command / full_test_command / language / silent_failure_context 等
#    参考 .claude/workflow-engine/examples/workflow.config.example.json

# 4. commit
git add -A
git commit -m "chore(workflow): init run-plans-engine consumer"
```

## 触发 workflow

```
让 Claude：用 run-plans workflow 跑 Plan 01
```

或显式：

```javascript
Workflow({
  scriptPath: '.claude/workflows/run-plans.js',
  args: { configPath: 'workflow.config.json', plansDir: 'docs/superpowers/plans', plan: '01' }
})
```

## 更新 engine 版本

```bash
git submodule update --remote .claude/workflow-engine
# 下次 commit 时 pre-commit hook 自动 sync + stage，exit 1 要求重新提交
git add .claude/workflow-engine .claude/workflows/run-plans.js
git commit -m "chore(workflow): bump run-plans-engine"
```

## 架构

- **canonical 源**：本仓库 `run-plans.js`（Workflow runtime 禁 fs/import，必须自包含）
- **派生副本**：消费项目 `.claude/workflows/run-plans.js`（由 `sync.mjs` 复制 + 注入 @sha 头）
- **守护**：gate 1 引擎 sync.test（字节一致）+ gate 2 消费项目 pre-commit（漂移自愈 + config 字节比对）

详见 `workflow-design.md` 和 `USAGE.md`。

## 可信源声明

消费项目应只使用授权/审核过的 submodule URL。更新 engine 前 review 变更。
```

- [ ] **Step 2: 跑全量测试验证仓库完整**

```bash
cd c:/Users/Alfred/Documents/projects/run-plans-engine
node --test tests/ 2>&1 | tail -10
```

Expected: 所有测试 PASS（365+ 原有 + 新增 common/sync.mjs/pre-commit/init-consumer 测试）

- [ ] **Step 3: commit**

```bash
cd c:/Users/Alfred/Documents/projects/run-plans-engine
git add README.md
git commit -m "docs(workflow): add README.md (quick start + architecture + trusted-source declaration)"
```

---

## Task 11: 迁移 lottery-notification 为消费项目

**Files:**
- Delete: `lottery-notification/.claude/workflows/run-plans.js`
- Delete: `lottery-notification/docs/superpowers/workflows/` (lib.js, tests/, package.json, USAGE.md, research/)
- Delete: `lottery-notification/docs/superpowers/workflow-design.md`
- Delete: `lottery-notification/docs/superpowers/workflow-plans/`
- Delete: `lottery-notification/docs/superpowers/2026-06-22-workflow-orchestrator.md`
- Create: `lottery-notification/.claude/workflow-engine/` (submodule)
- Create: `lottery-notification/.claude/workflows/run-plans.js` (sync 生成)
- Modify: `lottery-notification/.gitmodules`
- Keep: `lottery-notification/workflow.config.json`（项目特定，保留）
- Keep: `lottery-notification/docs/superpowers/lessons.md`（项目特定，保留）

**Interfaces:**
- Consumes: Task 1-10 的 run-plans-engine 仓库
- Produces: lottery-notification 作为消费项目可正常运行 workflow

- [ ] **Step 1: 备份旧 run-plans.js 到系统临时目录**

```bash
cd c:/Users/Alfred/Documents/projects/lottery-notification
# PowerShell
$ts = Get-Date -Format "yyyyMMddHHmmss"
$tmpBackup = "$env:TEMP/run-plans-backup-$ts.js"
Copy-Item .claude/workflows/run-plans.js $tmpBackup
Write-Host "Backup: $tmpBackup"
```

- [ ] **Step 2: 删除 engine 源文件**

```bash
cd c:/Users/Alfred/Documents/projects/lottery-notification
git rm .claude/workflows/run-plans.js
git rm -r docs/superpowers/workflows/
git rm docs/superpowers/workflow-design.md
git rm -r docs/superpowers/workflow-plans/
git rm docs/superpowers/2026-06-22-workflow-orchestrator.md
```

- [ ] **Step 3: 添加 engine submodule**

```bash
cd c:/Users/Alfred/Documents/projects/lottery-notification
git submodule add file:///C:/Users/Alfred/Documents/projects/run-plans-engine .claude/workflow-engine
```

- [ ] **Step 4: 跑 init-consumer**

```bash
cd c:/Users/Alfred/Documents/projects/lottery-notification
node .claude/workflow-engine/scripts/init-consumer.mjs
```

Expected: 输出 sync 成功 + config/lessons 跳过（已存在）+ pre-commit hook 安装

- [ ] **Step 5: 验证派生 run-plans.js 生成**

```bash
cd c:/Users/Alfred/Documents/projects/lottery-notification
head -2 .claude/workflows/run-plans.js
# 期望：首行原注释 + 第二行 // DO NOT EDIT — generated from workflow-engine@<sha> by sync.mjs
```

- [ ] **Step 6: 验证 workflow.config.json 未被覆盖（保留彩票特定配置）**

```bash
cd c:/Users/Alfred/Documents/projects/lottery-notification
cat workflow.config.json | head -5
# 期望：仍是彩票特定配置（test_command: uv run pytest 等），未被 example 覆盖
```

- [ ] **Step 7: 验证 workflow 可触发**

```
让 Claude：用 run-plans workflow 跑 Plan 01 的 T1（或一个轻量已 commit task）
```

Expected: bootstrap 正常 + 识别已 commit task + config smoke test 通过

- [ ] **Step 8: commit**

```bash
cd c:/Users/Alfred/Documents/projects/lottery-notification
git add .gitmodules .claude/workflow-engine .claude/workflows/run-plans.js .git/hooks/pre-commit
git status
# 确认删除的文件 + 新增的 submodule + 派生文件 + hook 都在 staging
git commit -m "chore(workflow): migrate lottery-notification to run-plans-engine submodule consumer"
```

---

## Task 12: 迁移 OTC-Fund-SIP-Strategy 为消费项目

**Files:**
- Delete: `OTC-Fund-SIP-Strategy/.claude/workflows/run-plans.js`
- Delete: `OTC-Fund-SIP-Strategy/docs/superpowers/workflows/` (旧副本)
- Delete: `OTC-Fund-SIP-Strategy/docs/superpowers/workflow-design.md`（若存在）
- Create: `OTC-Fund-SIP-Strategy/.claude/workflow-engine/` (submodule)
- Create: `OTC-Fund-SIP-Strategy/.claude/workflows/run-plans.js` (sync 生成)
- Modify: `OTC-Fund-SIP-Strategy/.gitmodules`
- Modify: `OTC-Fund-SIP-Strategy/workflow.config.json`（根据 OTC 项目编辑）

**Interfaces:**
- Consumes: Task 1-10 的 run-plans-engine 仓库
- Produces: OTC-Fund-SIP-Strategy 作为消费项目可正常运行 workflow

- [ ] **Step 1: 确认 OTC-Fund-SIP-Strategy 当前的 engine 文件结构**

```bash
cd c:/Users/Alfred/Documents/projects/OTC-Fund-SIP-Strategy
ls .claude/workflows/run-plans.js
ls docs/superpowers/workflows/
cat workflow.config.json
```

记录当前 config 内容（迁移后需对照编辑）。

- [ ] **Step 2: 备份旧 run-plans.js**

```bash
cd c:/Users/Alfred/Documents/projects/OTC-Fund-SIP-Strategy
$ts = Get-Date -Format "yyyyMMddHHmmss"
Copy-Item .claude/workflows/run-plans.js "$env:TEMP/otc-run-plans-backup-$ts.js"
```

- [ ] **Step 3: 删除旧副本**

```bash
cd c:/Users/Alfred/Documents/projects/OTC-Fund-SIP-Strategy
git rm .claude/workflows/run-plans.js
git rm -r docs/superpowers/workflows/  # 含 lib.js, tests/, USAGE.md 等
# 若有 workflow-design.md 也删
git rm docs/superpowers/workflow-design.md 2>$null
```

- [ ] **Step 4: 添加 submodule + init-consumer**

```bash
cd c:/Users/Alfred/Documents/projects/OTC-Fund-SIP-Strategy
git submodule add file:///C:/Users/Alfred/Documents/projects/run-plans-engine .claude/workflow-engine
node .claude/workflow-engine/scripts/init-consumer.mjs
```

- [ ] **Step 5: 编辑 workflow.config.json（必须！根据 OTC 项目）**

init-consumer 会拷贝 example 到 workflow.config.json（OTC 原有 config 已删）。需手动编辑：

```bash
cd c:/Users/Alfred/Documents/projects/OTC-Fund-SIP-Strategy
# 编辑 workflow.config.json，填 OTC 特定值：
# - test_command: OTC 的测试命令
# - full_test_command: OTC 全量测试
# - language: python（若 OTC 是 Python）
# - silent_failure_context: OTC 特定静默失败纪律
# - spec_path / plans_dir: OTC 的路径
```

对照 Step 1 记录的旧 config 内容编辑。

- [ ] **Step 6: 验证 config 已编辑（不等于 example）**

```bash
cd c:/Users/Alfred/Documents/projects/OTC-Fund-SIP-Strategy
# 尝试 commit，pre-commit 应放行（config 已编辑）
git add workflow.config.json
git commit -m "chore(workflow): edit workflow.config.json for OTC project"
# 期望：commit 成功（pre-commit 不拦截）
```

- [ ] **Step 7: 验证 workflow 可触发**

```
让 Claude：用 run-plans workflow 跑 OTC 的一个轻量 task
```

Expected: bootstrap 正常

- [ ] **Step 8: commit 迁移**

```bash
cd c:/Users/Alfred/Documents/projects/OTC-Fund-SIP-Strategy
git add .gitmodules .claude/workflow-engine .claude/workflows/run-plans.js .git/hooks/pre-commit
git commit -m "chore(workflow): migrate OTC-Fund-SIP-Strategy to run-plans-engine submodule consumer"
```

---

## Task 13: 跨平台验证（Windows + Mac）

**Files:**
- 无新文件，仅验证

- [ ] **Step 1: Windows 全流程验证**

在 Windows（当前环境）跑：

```bash
# 1. engine 全量测试
cd c:/Users/Alfred/Documents/projects/run-plans-engine
node --test tests/

# 2. lottery-notification workflow 触发
cd c:/Users/Alfred/Documents/projects/lottery-notification
# 让 Claude 跑 run-plans workflow Plan 01 T1

# 3. pre-commit 自愈测试
echo " " >> .claude/workflows/run-plans.js  # 故意漂移
git add -A
git commit -m "test: trigger pre-commit self-heal"
# 期望：pre-commit 自动 sync + stage + exit 1 要求重新提交
git status  # 确认 run-plans.js 已 stage 且 @sha 头更新
git commit -m "test: re-commit after self-heal"
# 期望：commit 成功
```

- [ ] **Step 2: Mac 全流程验证（如有 Mac 环境）**

在 Mac 上 clone lottery-notification + submodule：

```bash
git clone --recurse-submodules <lottery-repo> lottery-notification
cd lottery-notification
node .claude/workflow-engine/scripts/init-consumer.mjs
# 期望：sync 成功 + hook 安装（chmod 755 生效）

# 跑 workflow
# 让 Claude 跑 run-plans workflow
```

- [ ] **Step 3: 记录验证结果**

在 `docs/superpowers/reviews/2026-07-09-run-plans-engine-extraction-review.md` 追加跨平台验证结果。

---

## Self-Review

**1. Spec coverage:**
- §2 引用机制（submodule + sync）→ Task 1-4, 11, 12
- §3 合规性 → Task 2（路径调整不改 runtime 行为）+ Task 11/12 验证
- §4.1 初始化 → Task 9（init-consumer）+ Task 11/12 Step 4
- §4.1.1 config 编辑守护 → Task 5（pre-commit 字节比对）+ Task 12 Step 5-6
- §4.2 触发 → Task 11 Step 7, Task 12 Step 7
- §4.3 更新 → Task 10 README 文档 + Task 13 验证
- §5.2 pre-commit 自愈 → Task 5
- §5.4 SessionStart → Task 7
- §6 gate 1 + gate 2 → Task 2（gate 1 sync.test 路径）+ Task 5（gate 2）
- §7.1 文件映射 → Task 1
- §7.2 sync.test 路径 → Task 2
- §7.3 sync.mjs → Task 4
- §7.4 迁移顺序 → Task 1-12
- §7.5 验证清单 → Task 13
- §10 回滚 → Task 11 Step 1（备份到 os.tmpdir）
- 无遗漏

**2. Placeholder scan:**
- Task 12 Step 5 "编辑 workflow.config.json，填 OTC 特定值"——这是运行时编辑步骤，具体值依赖 OTC 项目，plan 中给出字段清单 + "对照 Step 1 记录的旧 config"，非占位符
- 无 TBD/TODO

**3. Type consistency:**
- `computeSha256(filePath): Promise<string>` — Task 3 定义，Task 4/5 使用，签名一致
- `readShaFromHeader(filePath): string|null` — Task 3 定义，Task 5 使用，一致
- `injectShaHeader(content, sha): string` — Task 3 定义，Task 4 使用，一致
- `CONSUMER_ROOT` 环境变量 — Task 5/9 使用，一致

**4. 跨任务文件路径一致性:**
- `scripts/common.mjs` — Task 3 创建，Task 4/5/9 引用，路径一致
- `scripts/sync.mjs` — Task 4 创建，Task 5/9 引用，一致
- `examples/workflow.config.example.json` — Task 8 创建，Task 5/9 引用，一致
- 无不一致
