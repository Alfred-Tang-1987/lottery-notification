# run-plans-engine 提炼 — 跨平台验证报告

> 日期：2026-07-09
> 验证人：SDD 执行（controller + subagents）
> 范围：run-plans-engine 引擎提炼（Tasks 1-10）+ lottery-notification / OTC-Fund-SIP-Strategy 消费项目迁移（Tasks 11-12）+ 跨平台验证（Task 13）
> 引擎 HEAD：`7e7902e601322de6fbd7f6e2316dab5baad11c42`；lottery 消费 commit `287874d`；OTC 消费 commit `c577aa3`

> 注：本文件原为 spec 评审 stub（CEO/Eng Review 结论）。Task 13 将其替换为跨平台验证报告。spec 评审结论已并入下文"Minor findings roll-up"与"已知限制"。

## 验证结果汇总

| 验证项 | 方法 | 结果 |
|---|---|---|
| 引擎测试全绿 | `for f in tests/*.test.js; do node --test "$f"; done`（per-file）| ✅ 385/385 pass（22 files，0 fail / 0 skip）|
| sync 脚本可用 | sync.mjs.test.js + sync.test.js（114+3 pass）| ✅ @sha 头注入正确，CRLF 兼容 |
| pre-commit 自愈（Windows）| temp clone 漂移 + commit 触发 | ✅ 检测漂移 → auto-sync → stage → exit 1 → 重提成功 |
| pre-commit 拦截未编辑 config | pre-commit-sync-check.test.js（3 pass）| ✅ exit 1 + 提示 `workflow.config.json.*example` |
| config 已针对消费项目编辑 | lottery `uv run pytest` + OTC `.venv/bin/pytest` | ✅ 均非占位（byte-compare ≠ example）|
| lottery submodule 迁移 | Task 11 | ✅ commit `287874d`，submodule@`7e7902e` |
| OTC submodule 迁移 | Task 12 | ✅ commit `c577aa3`，submodule@`7e7902e` |
| Mac 全流程 | — | ⏳ DEFERRED（当前环境 Windows，无 Mac）|
| live workflow 触发 | — | ⏳ MANUAL（人工验收步骤，subagent 无法触发 live Workflow）|

## Windows 验证详情

### 1a. 引擎全量测试

在引擎仓库 `C:\Users\Alfred\Documents\projects\run-plans-engine` 跑 per-file 测试（Node v24.16.0 / Windows；`node --test tests/` 在该 Node 版本被误解为 module require，故用 glob per-file 形式）：

| 测试文件 | pass | fail |
|---|---|---|
| args-stringification.test.js | 2 | 0 |
| boundary-control.test.js | 7 | 0 |
| buildPrompt.test.js | 2 | 0 |
| commitConvention.test.js | 6 | 0 |
| common.test.js | 7 | 0 |
| destructive-check.test.js | 3 | 0 |
| detectOscillation.test.js | 4 | 0 |
| dispatchImpl-retry.test.js | 8 | 0 |
| failed-approaches.test.js | 6 | 0 |
| helpers.test.js | 178 | 0 |
| init-consumer.test.js | 4 | 0 |
| leafTasks.test.js | 4 | 0 |
| lessons.test.js | 6 | 0 |
| normalizeCompleted.test.js | 7 | 0 |
| pre-commit-sync-check.test.js | 3 | 0 |
| rendering.test.js | 5 | 0 |
| review-history.test.js | 3 | 0 |
| schema-migration.test.js | 4 | 0 |
| schemas.test.js | 7 | 0 |
| self-review.test.js | 2 | 0 |
| sync.mjs.test.js | 3 | 0 |
| sync.test.js | 114 | 0 |
| **TOTAL** | **385** | **0** |

**22/22 files green，385/385 tests pass，0 fail / 0 skip。** 引擎在 Windows 上全绿，重确认 Task 10 的 385 测试基线。

### 1b. pre-commit 自愈测试（TEMP CLONE）

按 human decision "TEMP CLONE" 方式：将 lottery-notification 连同 submodule 完整 clone 到系统 temp 目录，在 clone 内制造漂移 + 触发 commit，观察 hook 自愈，随后销毁 temp clone。**不在 lottery 主仓库 drift/commit。**

**关键发现（drift 触发方式）：** pre-commit hook 的漂移检测逻辑是比较 `computeSha256(ENGINE_SRC/run-plans.js)` 与派生文件 header 记录的 `@sha`（`readShaFromHeader`）。因此向**派生文件**追加空格**不会**触发自愈（header @sha 不变，仍等于源 sha，视为同步）。正确触发方式是修改**引擎源** `run-plans.js`，使其 sha 偏离派生文件 header 记录的 @sha。

**执行的步骤：**
1. `mktemp -d /tmp/lottery-selfheal-XXXX` → `/tmp/lottery-selfheal-s8kz`
2. `git -c protocol.file.allow=always clone --recurse-submodules <lottery> <tmp>`（需 `-c protocol.file.allow=always` 绕过 git 安全默认对 file-protocol submodule 的拦截）→ clone 成功，submodule @ `7e7902e`
3. clone 内 `node .claude/workflow-engine/scripts/init-consumer.mjs` → 安装 pre-commit hook（clone 不复制 `.git/hooks/`）
4. 漂移引擎源：`printf '// drift-trigger comment\n' >> .claude/workflow-engine/run-plans.js`
   - 新 srcSha = `81241c85917e6139000f337307d1d49061f863a45d224b63b6d47000486a2a53`
   - 派生 header 仍记 `cb09dd146325...`（旧 @sha）→ mismatch
5. stage 派生文件后 `git commit -m "test: trigger pre-commit self-heal"` → hook 触发：

```
⚠ run-plans.js 与 engine 源不一致，自动 sync...
✓ synced run-plans.js → .claude\workflows\run-plans.js (@sha 81241c85917e)
✓ 已 sync run-plans.js 并 stage
请重新提交（pre-commit 自愈后要求重新 commit）
```
- **commit exit code = 1**（commit 被拦截，符合预期）

6. 验证派生文件已 re-sync：header 第 2 行变为 `workflow-engine@81241c85917e6139000f337307d1d49061f863a45d224b63b6d47000486a2a53`，与新 srcSha 一致（MATCH ✓）；`git status` 显示派生文件已 stage（`M  .claude/workflows/run-plans.js`）
7. 重新 `git commit -m "test: re-commit after self-heal"` → **exit 0，commit `6671755`**（重提成功）
8. `git log --oneline -4`：测试 commit（`6671755`、之前的 `415d0ee` 已 reset）仅存在于 temp clone
9. **销毁 temp clone：** `rm -rf /tmp/lottery-selfheal-s8kz` → `ls` 确认不存在

**lottery 主仓库未受影响：**
- 主 HEAD 仍为 `287874d777d91a7edc00ea84368926f6f6bf09db`（未变）
- 工作树 clean（`git status --short` 空）
- 派生 run-plans.js header 仍为 `cb09dd146325...`（未被测试污染）

**结论：** pre-commit 自愈链路（drift detect → sync.mjs auto-sync → git stage → exit 1 + "请重新提交" → 重提 exit 0）在 Windows Git Bash 下完整可用。

> 若 clone-with-submodule 失败的 fallback（直接在 lottery 工作树 drift + `bash .git/hooks/pre-commit`）未启用——temp clone 方式一次成功（加 `protocol.file.allow=always`）。

## Mac 验证（DEFERRED）

当前环境为 Windows（win32 10.0.26200 x64），无 Mac 环境。Mac 全流程验证待后续在 Mac 上执行，届时应覆盖：

- `git clone --recurse-submodules <lottery-repo>` → submodule 正常拉取
- `node .claude/workflow-engine/scripts/init-consumer.mjs` → 验证 hook `chmod 755` 在 Mac 生效（Windows 下已 `-rwxr-xr-x`）
- hook 路径 `.claude\workflow-engine/scripts/...`（Windows 反斜杠）在纯 POSIX shell 下是否仍能 `node` 解析——**这是 Mac 验证的重点关注项**（见"已知限制"）
- 跑 run-plans workflow（人工触发 live Workflow）

## 已知限制（非阻塞，phase-1 本地）

1. **`.gitmodules` URL 为本地绝对路径** `C:/Users/Alfred/Documents/projects/run-plans-engine`（lottery 与 OTC 同）。phase-1 本地开发用；推 Gitea 后需替换为远程 URL（见 TODOS T1）。后果：在其他机器 fresh clone 需引擎仓库已存在于该路径，或更新 URL。非回归。
2. **submodule clone 需 `protocol.file.allow=always`** —— git 安全默认拦截 file-protocol submodule clone。本地开发已知，非阻塞。
3. **pre-commit hook 路径含 Windows 反斜杠**：`.claude\workflow-engine/scripts/pre-commit-sync-check.mjs`（由 engine 的 init-consumer 生成）。Git Bash 下 `node` 正常解析（exit 0）。纯 POSIX shell（Mac 默认 `/bin/sh`）能否解析待 Mac 验证——若失败为 engine-side fix（统一用正斜杠）。
4. **live workflow 触发为人工验收步骤** —— subagent 无法调用 live Claude Code Workflow。静态前提（派生文件存在 + config 有效 + submodule populated）已在 Task 11/12 验证通过；live 触发交人工。
5. **`node --test tests/` 在 Node v24.16.0/Windows 被误解为 module require**（MODULE_NOT_FOUND）。需用 glob per-file 形式 `for f in tests/*.test.js; do node --test "$f"; done`。非阻塞，仅影响测试调用方式。

## Minor findings roll-up（来自各 task review，供后续 polish）

| Task | Finding | 影响 / 处置 |
|---|---|---|
| T2 | 双 commit split（fix report `e66cfba` 在主 commit 之外）| 非阻塞，审查修复留痕 |
| T3 | `readShaFromHeader` 3 行 slice（`slice(0,3)`）| 轻微 over-scan（@sha 在第 2 行），非阻塞 |
| T4 | sync.mjs cleanup 无 try/finally（temp 文件清理）| 单测覆盖，非阻塞 |
| T5 | hook staging 未在单测中**直接断言** `git add` 被调用（仅断言 exit 1 + sync 后内容）| 行为已由 Task 13 temp clone 实测补强（staged `M` 确认）|
| T6 | `.gitattributes` 原含 `* binary` 无 `*.sh` override，致 `.sh` 被当二进制（`Bin 0 -> N bytes`，不可 diff）| 已修（`dd4c770` 加 `*.sh text`），并预防 T7 的 `.sh` |
| T6 | `node --test tests/` glob 说明（见已知限制 5）| 文档化 |
| T9 | sentinel/path 判定较脆（`CONSUMER_ROOT` 默认 `ENGINE_ROOT/../..`）| env override 可测，生产路径依赖目录结构约定 |
| T9 | CRLF bug in common.mjs（`injectShaHeader` regex 不匹配 `\r\n`）→ 审查发现并修复（+3 回归测试）| 已修，锁定 |
| T11 | `.gitmodules` 机器特定本地路径（见已知限制 1）| phase-1，待推远程 |
| T11 | hook 反斜杠路径（见已知限制 3）| engine-side，待统一 |
| T12 | 同 T11：`.gitmodules` 本地路径 + hook 反斜杠 | 同上 |
| T12 | `LF will be replaced by CRLF` 警告（run-plans.js Windows 换行归一化）| 良性，不影响内容 |

**spec 评审历史（原 stub 内容，归档）：**
- CEO Review：SELECTIVE_EXPANSION，5 proposals，2 accepted / 3 deferred，0 critical gaps
- Eng Review：Node crypto（采纳）、移除 gate 3（Workflow runtime fs/import 限制，采纳）、sync.mjs 幂等性、测试计划已写
- spec 后续修正：移除"注入 `// TODO: edit me`"方案（JSON 不支持注释）→ 改 pre-commit 字节比对；回滚备份路径 `.claude/workflows/*.bak` → `os.tmpdir()`；review 报告从 spec 末尾移至 `docs/superpowers/reviews/`
- **VERDICT：** CEO + Eng Review CLEARED → 已实现完成（Tasks 1-12）+ 跨平台验证（Task 13，Windows 通过，Mac deferred）

## 结论

- **Windows：** 引擎 385/385 全绿，pre-commit 自愈链路实测可用（temp clone，主仓库未污染），config 守护可用，两消费项目迁移 commit 就位。
- **Mac：** deferred（无环境），重点验证 hook 反斜杠路径在 POSIX shell 下的解析。
- **live workflow：** 人工验收步骤（静态前提已验证）。
- **整体：** phase-1 本地开发目标达成。剩余项（远程 URL、hook 路径统一、Mac 验证、live 触发）均为已记录的后续 polish / 人工步骤，非阻塞。
