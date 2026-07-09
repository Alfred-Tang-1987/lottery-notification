# TODOS

## run-plans-engine 后续阶段

### T1: 推远程 Gitea

- **What**: 把 `run-plans-engine` 从本地仓库推送到 Gitea（`http://192.168.8.168:8418/gitea/run-plans-engine`），并把 `lottery-notification` 与 `OTC-Fund-SIP-Strategy` 的 `.gitmodules` URL 从 `file:///C:/Users/Alfred/Documents/projects/run-plans-engine` 切换为远程 URL。
- **Why**: 当前阶段用本地 `file://` 路径跑通，但 engine 必须上远程才能跨机器/新环境 clone，否则每次新环境都要先在本机建 engine 仓库。
- **Pros**: 跨机器可用；与 NAS 部署风格一致；为 CI 自动 PR 铺路。
- **Cons**: 需要创建 Gitea repo、配置权限、批量改消费项目的 `.gitmodules`。
- **Context**: 已在 `/plan-ceo-review` 中决定本次不纳入，作为后续阶段。本地跑通后立即执行。
- **Effort**: S（人工：~1 天 / CC+gstack：~10 分钟）
- **Priority**: P1
- **Depends on / blocked by**: 阶段一/二/三完成，本地 engine 仓库结构稳定。

### T2: CI 自动 PR

- **What**: 当 `run-plans-engine` 仓库 push 后，Gitea Actions workflow 自动向 `lottery-notification` 和 `OTC-Fund-SIP-Strategy` 提 PR：更新 submodule 到最新 commit，并重新 sync 派生 `run-plans.js`。
- **Why**: 消费项目数量 ≥3 或 engine 迭代频繁后，手动 `git submodule update --remote` + sync + commit 会成为负担。自动 PR 把升级变成可 review 的流程。
- **Pros**: engine 改进主动、可见地分发到所有消费项目；升级可被 review；减少遗忘。
- **Cons**: 需要配置 Gitea Actions、跨仓库 token、webhook，维护成本随消费项目数量线性增长。
- **Context**: 在 `/plan-ceo-review` 中用户初始选择不纳入，后改为加入 TODO。当前 2 个消费仓库时 pre-commit + SessionStart 已够用，仓库增多或迭代加快时启动本任务。
- **Effort**: M（人工：~2 天 / CC+gstack：~30 分钟）
- **Priority**: P2
- **Depends on / blocked by**: T1（远程 Gitea 必须先可用）。

### T3: CHANGELOG / 版本标签

- **What**: 在 `run-plans-engine` 仓库维护 `CHANGELOG.md`，并在每次 breaking change 或重要特性时打 git tag（如 `v1.0.0`）。
- **Why**: 消费项目需要判断「这次 engine 更新有没有 breaking change」，CHANGELOG 和 semver tag 是最清晰的信号。
- **Pros**: 升级决策有文档依据；可追溯 breaking change；与 CI 自动 PR 配合时可附带摘要。
- **Cons**: 增加发布纪律；每次提交都要考虑是否 breaking。
- **Context**: engine 刚成立，前期 commit 多为搬家和路径调整，CHANGELOG 价值低。待 T1 完成、engine 稳定运行后再启动。
- **Effort**: S（人工：~0.5 天 / CC+gstack：~10 分钟）
- **Priority**: P2
- **Depends on / blocked by**: engine 结构稳定、首批迁移完成。
