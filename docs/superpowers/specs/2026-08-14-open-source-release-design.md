# 开源发布 + 彩种核对设计 spec

> 日期：2026-08-14 | 状态：已确认（brainstorming 决策逐项通过）
> 范围：①仓库公开到 GitHub（许可证 / README / 文档净化 / 镜像 / 子模块）②7 彩种「文档 vs 代码」核对与修复
> 前置事实：仓库当前 remote 指向 NAS Gitea（`<NAS_IP>:8418`）、无 LICENSE、文档含内网 IP、`.claude/workflow-engine` 为私有 gitea 子模块、Dockerfile 不依赖该子模块、无 CI 配置。

## 1. 背景与目标

- 仓库计划开源到 GitHub，要求与自建 Gitea 保持一致（内容同步、NAS 部署流程不变）。
- 生产只验证过双色球（ssq），其余 6 彩种无生产测试——需要在发布前核对 `docs/reference/lottery-rules.md` 与 domain 实现是否一致，不一致处修复。
- 仓库当前无许可证文件。

**合规前提（最高优先级，发布后同样适用）**：系统定位「事后核对 + 个人号码管理」，绝不包含号码预测 / AI 推荐 / 必中 / 冷热号排序推荐 / 购彩代购支付 / 高频彩私彩。走势图为常规功能（综合分布 + 频次，不排序/不标冷热/不推荐 + 显著随机性声明）。公开版 README 与 spec 均须维持此声明。

## 2. 已确认决策

| 决策点 | 结论 |
|---|---|
| 许可证 | **Apache-2.0**（宽松许可。作者可商用，但无法排他——已明确接受） |
| 主源 | **GitHub 为主源**：日常推 GitHub，仓库内容即公开内容 |
| gitea | **pull-mirror**：gitea 从 GitHub 拉镜像；NAS 部署 clone URL 不变 |
| 内网信息 | **就地净化**：内网 IP/URL → 占位符；NAS 专属运维细节移出仓库（gitignored）；部署文档通用化 |
| 子模块 | **从仓库移除**，降级为 gitignored 开发工具 + setup 脚本（Dockerfile 不依赖，外部 clone 干净） |
| 核对范围 | 逐彩种核对 lottery-rules.md vs domain 实现 + **TDD 修复** + 补单元测试 |

## 3. 架构决策

### 3.1 仓库内容即公开内容（核心原则）

GitHub 为主源 ⇒ 主分支任何内容都可能被公开阅读。因此：

- **IP/URL 一律占位符化**：`<NAS_IP>` / `192.168.8.167` → `<NAS_IP>` / `<GITEA_URL>`；`docs/deploy.md` 的 clone 源与 CORS 示例同步替换。
- **NAS 专属运维细节移出仓库**：FnOS 路径（`/vol1/1000/Docker/...`）、模型 router 配置等，迁入 gitignored 文件（如 `deploy-nas-internal.md`，不入版本控制）。
- **部署文档通用化**：`docs/deploy.md` 改为通用 Docker 部署指南，不再绑定 FnOS / 8280 专属说明（端口保留为「默认 8280，可改」）。
- 今后新增内容遵守同一原则；`.env` 仍不进库。

### 3.2 子模块降级（run-plans-engine 保持私有）

原「发布脚本剔除子模块」思路与「GitHub 为主源 + gitea 镜像共享同一主分支」冲突——单一分支无法同时「有」与「无」子模块。改为**从仓库永久移除子模块机制**，效果等价（公开版无子模块、NAS 部署不受影响）：

1. `git rm --cached .claude/workflow-engine` + 删除 `.gitmodules` + `.gitignore` 增补 `.claude/workflow-engine/`
2. 新增 `scripts/setup-workflow-engine.sh`：从内网 gitea clone run-plans-engine 到 `.claude/workflow-engine`；未配置内网时跳过并打印「内部开发工具，仅计划编排用，运行/部署不需要」
3. `CLAUDE.md` 引擎更新流程改为「先跑 setup 脚本 → node scripts/sync.mjs → 提交派生副本」；删除 submodule 指针同步说明
4. `docs/deploy.md` 删除 `git submodule update --init` 相关说明（不再需要）
5. 验证：外部 `git clone` GitHub 仓库 → 无子模块 → `docker compose up -d --build` 正常；内网跑 setup 脚本可恢复引擎

> 注：`.claude/workflows/run-plans.js`（引擎派生副本）已跟踪在主仓库，orchestrator 日常执行不依赖子模块本身。

### 3.3 镜像与远程拓扑

- **GitHub**：新建公开仓库 `<owner>/lottery-notification`，本仓库新增 remote；最终 `origin` = GitHub（主源）。
- **Gitea**：`lottery-notification` 仓库改为 **pull-mirror**（Gitea UI：Settings → Repository → Mirroring，源 = GitHub），周期性从 GitHub 拉取；或保留双 remote 双推。选 pull-mirror（单一事实源）。
- **NAS 部署**：clone URL 不变（gitea 镜像地址），`docker compose` / deploy 流程零改动。
- **初始推送**：现有完整历史整体推送到 GitHub 新仓库，**不重写 / 不 squash 历史**——workflow orchestrator 依赖 git log 的 `feat(plan-X/T-Y)` 约定做进度续跑，重写会破坏。
- 历史中的私有 IP 为内网网段，非公网可达，风险可接受（与「就地净化」仅覆盖工作树并存）。

### 3.4 文档净化清单

| 文件 | 处理 |
|---|---|
| 新增 `README.md` | 项目简介、合规红线声明、功能、7 彩种、架构图、技术栈、快速开始（docker compose + `.env` 配置指引）、开发/测试命令、目录结构、许可证 + 第三方数据源声明（MXNZP/聚合数据/高德 API 条款适用）、免责声明 |
| `docs/deploy.md` | clone 源 → 占位符；CORS 示例 `["http://<NAS_IP>:8280"]`；去 FnOS 专属说明；去 submodule 说明；保留 .env 字段表 / 单源模式 / 备份 / 冒烟 / 密码重置 / 回滚 |
| `CLAUDE.md` | 删「本机 model 现状」（含内网 router IP）一行；NAS 部署约束泛化；workflow 引擎更新流程更新 |
| `docs/superpowers/specs/*` `plans/*` `run-plans-engine-TODOS.md` | 内网 IP/URL → 占位符 |
| `web/src/pages/Dashboard.vue` 注释 | 内网 IP → 占位符 |
| 新增 `LICENSE` | Apache-2.0 全文 |
| 新增 `scripts/setup-workflow-engine.sh` | 见 §3.2 |

### 3.5 GitHub Actions（可选，推荐最小集）

无现有 CI。加最小公开质量门禁，不影响 NAS（NAS 走 gitea 镜像）：

- `on: push / pull_request`
- uv setup（Python 3.12）→ `ruff check` → `lint-imports` → `pytest -m "not migration"`（本地可跑子集，避免外部服务依赖）→ 前端 `npm ci && npm run build`
- 迁移类 / 真库测试由内网 CI 与部署环境承担（遵循 common/testing.md 分层）

## 4. 彩种核对（阶段 B）

### 4.1 核对方法

逐彩种（ssq / dlt / qlc / fc3d / qxc / pl3 / pl5）对照 `docs/reference/lottery-rules.md`（104 行权威参考）vs `app/domain/{spec.py, compare.py, prize_tables.py, entry.py}`（合计 362 行）：

| 核对维度 | 说明 |
|---|---|
| 号码结构 | 位数、数值范围、分区（前区/后区）与按位玩法 |
| 开奖日 | `draw_days`（Python 0-based，周一=0） |
| 玩法体系 | 分区型（单式/复式/胆拖）vs 按位型（单选/组选三/组选六） |
| 特殊规则 | dlt 追加（仅参与一二等奖 80%）；qxc 2020-10-13 改版（前区 6 位 + 后区 1 位 0-14） |
| 奖金表 | 金额单位分（int）、档位与玩法对应 |

产出**核对报告**（每彩种一行：文档声明 vs 代码实现 vs 一致/不一致 + 双方行号引用）。

### 4.2 修复与测试

- 不一致处 **TDD**：RED（写失败测试）→ GREEN（最小修复）→ REFACTOR，每阶段 git checkpoint
- 每彩种补齐奖级/比对单元测试；全量回归 657+ 保持绿

## 5. 交付物

- `LICENSE`（Apache-2.0 全文）、`README.md`、净化后文档（§3.4 清单）
- 子模块移除 + `scripts/setup-workflow-engine.sh`
- remote 配置（GitHub 主源）+ gitea mirror 配置（Gitea UI 手动步骤，文档化）
- 核对报告 + 修复 commit + 补测
- 两个 plan：**plan-09-open-source-release**（阶段 A）、**plan-10-verify-7-lottery-types**（阶段 B）

## 6. 测试策略

- **阶段 A**：
  - 外部 clone 冒烟：`git clone <github-url> && cd && docker compose up -d --build && curl /health` 200
  - 净化校验：`grep -rn "192\.168\.8\."` 全仓应为空（除历史不可改）
  - 子模块移除后 build 正常；内网 setup 脚本可恢复引擎
- **阶段 B**：逐彩种单元测试 + 全量 `uv run pytest` 通过

## 7. 风险与约束

- **历史不可重写**：orchestrator 进度续跑依赖 git log 约定，旧提交中的私有 IP 保留（低风险，内网网段）
- **商用权益**：Apache-2.0 下任何人均可商用；若未来需要排他商用，需换 source-available 许可（BSL/ELv2/FSL）——当前不采用
- **第三方数据源**：代码仅调用 API，不附数据；使用者需自行遵守 MXNZP / 聚合数据 / 高德 API 条款
- **GitHub Actions 需外网**：仅作公开质量门禁；NAS 构建/部署仍走 gitea 镜像，不受 GitHub 可用性影响
