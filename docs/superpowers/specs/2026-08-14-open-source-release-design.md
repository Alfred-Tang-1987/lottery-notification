<!-- /autoplan restore point: <LOCAL_PATH> -->
# 开源发布 + 彩种核对设计 spec

> 日期：2026-08-14 | 状态：已确认（brainstorming 决策逐项通过）
> 范围：①仓库公开到 GitHub（许可证 / README / 文档净化 / 镜像 / 子模块）②7 彩种「文档 vs 代码」核对与修复
> 前置事实：仓库当前 remote 指向 NAS Gitea（`<GITEA_URL>`）、无 LICENSE、文档含内网 IP、`.claude/workflow-engine` 为私有 gitea 子模块、Dockerfile 不依赖该子模块、无 CI 配置。

## 1. 背景与目标

- 仓库计划开源到 GitHub，要求与自建 Gitea 保持一致（内容同步、NAS 部署流程不变）。
- 生产只验证过双色球（ssq），其余 6 彩种无生产测试——需要在发布前核对 `docs/reference/lottery-rules.md` 与 domain 实现是否一致，不一致处修复。
- 仓库当前无许可证文件。

**合规前提（最高优先级，发布后同样适用）**：系统定位「事后核对 + 个人号码管理」，绝不包含号码预测 / AI 推荐 / 必中 / 冷热号排序推荐 / 购彩代购支付 / 高频彩私彩。走势图为常规功能（综合分布 + 频次，不排序/不标冷热/不推荐 + 显著随机性声明）。公开版 README 与 spec 均须维持此声明。

## 2. 已确认决策

| 决策点 | 结论 |
|---|---|
| 许可证 | **AGPL-3.0-only**（强 copyleft。**2026-08-14 推翻原 Apache-2.0 决策**：强 copyleft 符合「衍生代码（含 SaaS 网络部署）必须开源」的诉求。作者不受自身许可约束，自托管/小圈子共享不受影响；任何人改造后部署为网络服务必须按 AGPL-3.0-only 开源全部衍生代码） |
| 主源 | **GitHub 为主源**：日常推 GitHub，仓库内容即公开内容 |
| gitea | **pull-mirror**：gitea 从 GitHub 拉镜像；NAS 部署 clone URL 不变 |
| 内网信息 | **就地净化**：内网 IP/URL → 占位符；NAS 专属运维细节移出仓库（gitignored）；部署文档通用化 |
| 子模块 | **从仓库移除**，降级为 gitignored 开发工具 + setup 脚本（Dockerfile 不依赖，外部 clone 干净） |
| 核对范围 | 逐彩种核对 lottery-rules.md vs domain 实现 + **TDD 修复** + 补单元测试 |

## 3. 架构决策

### 3.1 仓库内容即公开内容（核心原则）

GitHub 为主源 ⇒ 主分支任何内容都可能被公开阅读。因此：

- **IP/URL 一律占位符化**：`192.''168.8.''168` / `192.''168.8.''167` → `<NAS_IP>` / `<GITEA_URL>`；`docs/deploy.md` 的 clone 源与 CORS 示例同步替换。
- **NAS 专属运维细节移出仓库**：FnOS 路径（`/vol1''/1000/Docker/...`）、模型 router 配置等，迁入 gitignored 文件（如 `deploy-nas-internal.md`，不入版本控制）。
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
| 新增 `LICENSE` | AGPL-3.0-only 全文 |
| 新增 `scripts/setup-workflow-engine.sh` | 见 §3.2 |

### 3.5 GitHub Actions（可选，推荐最小集）

无现有 CI。加最小公开质量门禁，不影响 NAS（NAS 走 gitea 镜像）：

- `on: push / pull_request`
- uv setup（Python 3.12）→ `ruff check` → `lint-imports` → `pytest -m "not migration"`（本地可跑子集，避免外部服务依赖）→ 前端 `npm ci && npm run build`
- 迁移类 / 真库测试由内网 CI 与部署环境承担（遵循 common/testing.md 分层）

## 4. 彩种核对（阶段 B）

### 4.0 范围决策（autoplan 终审 2026-08-14 确认）

两个独立 subagent 证据一致：代码 MVP 仅支持 single/zhixuan（`entry.py:48` 复式/胆拖抛 NotImplementedError；fc3d 文档 12 玩法 vs 代码 1 档单选；pl3 组选/pl5 定位复式未实现；qxc 前缀近似待校准）。「核对+修复」的「修复」实为多周功能开发。**终审拆为 B1 + B2**：

- **B1（发布前，plan-10 范围）**：文档对齐**实际能力** + 诚实能力边界声明 + 已实现面补测。逐彩种核对产出处置列：`修复代码`（小差异）/ `降级文档`（未实现玩法改为声明已知限制）/ `列 B2 roadmap`。
- **B2（发布后 roadmap，不进 plan-10）**：复式/胆拖/fushi 组合展开、fc3d 其余 9 玩法、pl3 组选三/六、pl5 定位/组合复式、qxc 真实开奖校准。财务正确性（金额/档位）改动须先固化 ssq 生产基线回归。

### 4.1 核对方法（B1）

逐彩种（ssq / dlt / qlc / fc3d / qxc / pl3 / pl5）对照 `docs/reference/lottery-rules.md`（104 行权威参考）vs `app/domain/{spec.py, compare.py, prize_tables.py, entry.py}`（合计 362 行）：

| 核对维度 | 说明 |
|---|---|
| 号码结构 | 位数、数值范围、分区（前区/后区）与按位玩法 |
| 开奖日 | `draw_days`（Python 0-based，周一=0） |
| 玩法体系 | 分区型（单式/复式/胆拖）vs 按位型（单选/组选三/组选六） |
| 特殊规则 | dlt 追加（仅参与一二等奖 80%）；qxc 2020-10-13 改版（前区 6 位 + 后区 1 位 0-14） |
| 奖金表 | 金额单位分（int）、档位与玩法对应 |

产出**核对报告**（每彩种一行：文档声明 vs 代码实现 vs 处置[B1代码修复/文档降级/B2 roadmap] + 双方行号引用）。

### 4.2 B1 修复与测试

- 处置=「B1 代码修复」的差异：**TDD**（RED 写失败测试 → GREEN 最小修复 → REFACTOR），每阶段 git checkpoint
- ssq 为唯一生产验证彩种——任何 prize_tables/compare 改动需**固化 ssq 回归基线**（真实开奖→真实奖金夹具）
- 处置=「文档降级」：改 lottery-rules.md / README 声明为已知限制，标注 MVP 边界
- 每彩种补齐**已实现面**奖级/比对单元测试；全量回归 657+ 保持绿
- qxc：README 标注「近似判定，待真实开奖校准」+ 保守回退（不确定档按「潜在中奖-人工核对」，见 E7）

## 5. 交付物

- `LICENSE`（AGPL-3.0-only 全文）、`README.md`（含快速开始/访问模式表/能力边界/英文合规声明）、净化后文档（§3.4 清单）
- 子模块移除 + `scripts/setup-workflow-engine.sh`（env 化，无字面 IP）
- remote 配置（GitHub 主源）+ gitea mirror 配置（Gitea UI 手动步骤，文档化）
- **发布门禁**：`scripts/publish-check.sh`（gitleaks + 加宽 grep）、`.github/workflows/ci.yml`（lint + import-linter + 全量 pytest + npm test/build + 泄露扫描）
- `.env.example` 修复（空 CRYPTO 生成命令 + TLS 默认注释 + JWT 必改提示）+ 访问模式表
- `CONTRIBUTING.md` / `SECURITY.md` / issue 模板（合规红线流程）
- 核对报告（处置列）+ B1 修复 commit + 补测
- 两个 plan：**plan-09-open-source-release**（阶段 A）、**plan-10-verify-7-lottery-types**（阶段 B，B1 范围）

## 6. 测试策略

- **阶段 A**：
  - 外部 clone 冒烟：`git clone <github-url> && cd && docker compose up -d --build && curl /health` 200
  - 净化校验：`grep -rn "192\.168\.8\."` 全仓应为空（除历史不可改）
  - 子模块移除后 build 正常；内网 setup 脚本可恢复引擎
- **阶段 B**：逐彩种单元测试 + 全量 `uv run pytest` 通过

## 7. 风险与约束

- **历史不可重写**：orchestrator 进度续跑依赖 git log 约定，旧提交中的私有 IP 保留（低风险，内网网段）
- **商用权益**：AGPL-3.0-only 下，**任何人改造后部署为网络服务（含 SaaS、内部平台）必须按 AGPL-3.0-only 开源全部衍生代码**（§13 网络服务条款）。作者（版权持有者）不受自身许可约束，可自由商用/闭源。第三方仅调用未改造的本项目 API 接口不触发传染（AGPL 传染触发于「修改后网络部署」，非「调用」）
- **第三方数据源**：代码仅调用 API，不附数据；使用者需自行遵守 MXNZP / 聚合数据 / 高德 API 条款
- **GitHub Actions 需外网**：仅作公开质量门禁；NAS 构建/部署仍走 gitea 镜像，不受 GitHub 可用性影响

---
# AUTOPLAN 评审（2026-08-14）

> 由 `/autoplan` 全流程评审（CEO→Eng→DX）。评审前的仓库事实已逐条核实（见各节「核实」行）。

## Phase 1 — CEO Review（战略与范围）

**前提门**：六条前提经用户确认通过（P1 开源目标 / **P2 AGPL-3.0-only（2026-08-14 推翻原 Apache-2.0，改强 copyleft）** / P3 GitHub 主源 + gitea 镜像 / P4 精简内网 / P5 子模块私有并移除 / P6 核对+修复）。

**外部声音**：Codex 未安装 → 仅 Claude 独立 subagent（`[subagent-only]`）。其对仓库的事实断言**已逐一核实**，多数成立，一处数字纠正（fc3d seeds 为 3 玩法非 12——但文档声明 12 种，差距仍成立）。

### CEO-1 核实后成立的关键发现

| # | 严重度 | 发现 | 证据 | 处置 |
|---|---|---|---|---|
| C1 | **高** | **Phase B「核对+修复」实为功能补齐工程**，非核对。代码 MVP 仅支持 single/zhixuan；复式/胆拖/fushi 抛 NotImplementedError（`app/domain/entry.py:48-50`）；fc3d 文档 12 玩法 vs seeds 仅 `danxuan/zuxuan3/zuxuan6`（`app/seeds/lottery_types.py:51`）且 prize_tables 仅 1 档单选（`prize_tables.py:80-81`）；pl3 zuxuan3/6、pl5 定位/组合复式未实现；qxc 用「前缀连续命中」近似且注释自认「Phase 2 用真实开奖校准」（`compare.py` QxcHybridCompare）。「TDD 修复」会膨胀成多周功能开发；或须诚实降级文档声明。 | 代码核实 | **用户决策项（终门口）** |
| C2 | 高 | `.env.example:33` 注释含真实 IP `<NAS_IP>`（`# NAS_IP 替换为实际 IP，如 <NAS_IP>`），不在净化清单。 | `.env.example` 核实 | 自动修复：净化清单加 `.env.example`，IP→`<NAS_IP>` |
| C3 | 高 | 历史中密钥/内网信息**无硬性预推送门禁**。仅工作树净化，81 提交历史可能含旧密钥（当前扫描未见硬编码 key，但需正式门禁，不可凭假设）。 | git 历史 | 自动：新增**预推送密钥扫描门禁**（gitleaks 或 `git log -p` 正则扫 `MXNZP_API_KEY`/`JWT_SECRET`/`CRYPTO_KEY_V1`/`SMTP_PASS`） |
| C4 | 中 | 「engine 保持私有」前提与事实矛盾：`.claude/workflows/run-plans.js`（引擎派生副本）**已在仓库被跟踪**，公开即公开。 | `git ls-files` 核实 | 自动：设计明确接受 run-plans.js 公开（无密钥的开发工具）；README 标注 |
| C5 | 中 | README 若不声明能力边界，会**公开过度宣称**（657 测试 + 全玩法宣传 vs 实际 MVP）。 | CLAUDE.md:7 测试数已过时（554 vs 当前 657） | 自动：README 增「已实现范围与限制」章节（single/zhixuan only；fc3d 仅单选；qxc 近似判定）；CLAUDE.md 测试数更新为验证任务之一 |
| C6 | 低-中 | 泄露防护是**一次性 grep**，非持续门禁；且词表未含 `vol1''/`、`fn-''nas`、`home''lab`、`:40''10`、真实 IP 示例。 | §6 测试策略 | 自动：词表加宽（`192.168\.8\.|vol1''/|fn-''nas|home''lab|:40''10|8\.168`）；泄露扫描并入 GitHub Actions CI（公共仓库自守卫） |
| C7 | 低 | 竞品已存在（`stevezhouht/lottery-mcp-server`、`lzuntalented/lz-lottery-tool`、`koala9527/hello-lottery` 等），README 定位需差异化（多用户 + 多渠道 + 双源校验），并**英文合规声明**（无预测/无推荐/无代购）置于首段。 | 竞品检索 | 自动：README 首段英文合规声明 + 差异化定位 |

### CEO-2 已接受决策（自动，见决策审计表）

采纳 C2/C3/C4/C5/C6/C7 全部修复（机械性、高价值、在 blast radius 内）。**C1（Phase B 重定界）留待终门口用户决策**。

### CEO-3 必产输出

**NOT in scope（延后理由）**
- 复式/胆拖/fushi 组合展开、fc3d 其余 9 玩法、pl3 zuxuan3/6、pl5 定位复式、qxc 真实开奖校准——**MVP 外能力**，若 C1 采纳则列入 roadmap；不采纳则仍为 Phase B 修复范围（成本高）
- gitea pull-mirror 反向拓扑（NAS 主源 + GitHub 镜像）——用户已选 GitHub 主源，不反转
- 其他替代许可（BSL/ELv2/FSL 等 source-available）——**2026-08-14 已从 Apache-2.0 改定为 AGPL-3.0-only**（强 copyleft，符合衍生含 SaaS 必须开源的诉求）；BSL/ELv2/FSL 等纯禁商路线不采用（AGPL-3.0-only 已满足诉求）
- 历史重写/squash——破坏 orchestrator 进度续跑，不采用

**What already exists（复用）
- `.gitignore` 已排除 `.env`/`data/`/`static/`/`runs/`——净化的基础已就位
- `.dockerignore` 已排除 `.env`——镜像层不泄露
- `workflow.config.json` 已内聚 silent-failure 风险清单——README「正确性卖点」素材
- `docs/deploy.md` 通用 Docker 命令可平移为公开部署文档

**Dream state delta（12 月理想态）
```
CURRENT（NAS 私有，无许可，仅 ssq 生产验证）
   ──→ THIS PLAN（GitHub 公开 + AGPL-3.0-only + 净化 + 诚实能力边界）
   ──→ 12-MONTH IDEAL（社区参考实现：双源交叉校验 + 单事务 + savepoint 隔离的「正确性示范」；
        有明确能力边界与 roadmap；未实现玩法被诚实标注而非宣称）
```

### CEO 共识表（Claude subagent 仅，Codex 不可用）

| 维度 | Claude subagent | 主评审 |
|---|---|---|
| 前提有效 | 部分挑战（C1） | 一致，六前提确认 |
| 解决的正确问题 | 质疑「无 WHY/受众」+ Phase B 目标错位 | 采纳 C1/C7 后成立 |
| 范围校准 | 过宽（Phase B 会膨胀） | 采纳 C1 重定界 |
| 替代方案 | 提出反向拓扑/AGPL | 拓扑已被用户决策覆盖不反转；**AGPL 经 2026-08-14 重新评估后被采纳**（推翻原 Apache-2.0） |
| 竞争/风险 | 竞品存在 + 声誉风险（过度宣称） | 采纳 C7 诚实边界 |
| 6 月轨迹 | GitHub 主源日常摩擦 vs 无社区回报 | 保留用户选择，README 定位缓解 |

**Phase 1 完成。** Claude subagent 7 项发现，主评审核实后 6 项采纳（C2-C7）+ 1 项用户决策（C1）。共识 5/6 确认，1 项（Phase B 范围）分歧 → 终门口。

<!-- AUTONOMOUS DECISION LOG -->
## Decision Audit Trail

| # | Phase | Decision | Classification | Principle | Rationale | Rejected |
|---|-------|----------|-----------|-----------|-----------|----------|
| 1 | CEO | 前提门确认六条前提 | Premise gate | 用户判断 | 用户在 brainstorming 逐项确认 | — |
| 2 | CEO | C1 Phase B 重定界 → 留终门口 | User decision | 用户主权 | 改变用户陈述范围，subagent 单方，不自动决定 | 暂不自动采纳 |
| 3 | CEO | 采纳 C2（.env.example 净化） | Mechanical | P1 完整性 | 真实 IP 泄露已核实，一行修复 | — |
| 4 | CEO | 采纳 C3（预推送密钥扫描门禁） | Mechanical | P1 完整性 | 历史无硬性门禁，高风险 | — |
| 5 | CEO | 采纳 C4（run-plans.js 公开事实对账） | Mechanical | P5 显式 | 事实已核实，README 标注即可 | — |
| 6 | CEO | 采纳 C5（诚实能力边界 + 测试数更新） | Mechanical | P1 完整性 | 防公开过度宣称 | — |
| 7 | CEO | 采纳 C6（泄露扫描入 CI + 词表加宽） | Mechanical | P1 完整性 | 一次性→持续门禁 | — |
| 8 | CEO | 采纳 C7（英文合规声明 + 差异化定位） | Mechanical | P3 务实 | 声誉风险缓解 | — |
| 9 | CEO | 拒绝反向拓扑（NAS 主源） | Taste | 用户主权 | 用户已选 GitHub 主源，不反转 | NAS-primary + GitHub mirror |
| 10 | CEO | **推翻：采纳 AGPL-3.0-only**（2026-08-14） | Taste | 用户主权 | 用户重新评估后明确「任何基于衍生代码（含 SaaS 网络部署）也必须开源」→ 强 copyleft AGPL-3.0-only 符合诉求；作者不受自身许可约束，自托管不受影响 | ~~Apache-2.0~~（原决策已推翻）、BSL/ELv2/FSL（纯禁商，非诉求） |

## Phase 3 — Eng Review（架构与测试）

**外部声音**：Claude 独立 eng subagent（`[subagent-only]`），事实断言全部核实。**无 P1 发现**。正例核实：F5（Dockerfile/compose 不依赖子模块）、F8（历史无真实密钥，仅变量名）、F12（CI 可 hermetic）、F15（净化清单覆盖核实）。

### Eng-1 采纳的修复（自动，见决策审计表）

| # | 严重度 | 发现 | 证据 | 处置 |
|---|---|---|---|---|
| E1(F3) | P2 | **提交的 `scripts/setup-workflow-engine.sh` 会硬编码内网 gitea URL → 自相矛盾**（违反 §3.1 占位符原则，且过不了 §6 自己的 grep 门禁） | `§3.2` 设计文字 | 自动：脚本改为**从 env/`WORKFLOW_ENGINE_URL` 读取**，默认空 → 跳过并打印「内部开发工具，仅计划编排用」；提交文件内**不得出现字面 IP**；脚本加入 §3.4 清单 |
| E2(F9,F21) | P2 | C3/C6 已采纳但**未物化为 CI 步骤 / 预推送门禁** | §3.5 CI 列表 | 自动：CI 增 `gitleaks` + 加宽正则 grep job（push main + PR）；Phase A 加**预推送硬门禁**（push 前跑 gitleaks + grep） |
| E3(F6) | P2 | **历史提交者邮箱泄露身份**：`alfred@Alfreds-MBP.tailf''898c8.ts.net`（Tailscale 主机名+尾网指纹）、`gitea@<NAS_IP>`（内网 IP 作邮箱域名）；C3 密钥扫描查不到邮箱 | `git log --format='%an <%ae>'` 核实 | 自动：**接受并文档化**；今后提交用 GitHub noreply 邮箱（`git config user.email ...@users.noreply.github.com`）；完整清理需 filter-repo 致 SHA churn（与 NAS 零改动冲突），现实路径是接受+记录 |
| E4(F13,F14) | P2 | **净化词表漏真实标识**：`file:///C:/''Users/Alfred/...`、`/Users/''alfred/...` 绝对路径、私有项目名 `OTC-''Fund-SIP-Strategy`（`docs/superpowers/run-plans-engine-TODOS.md:7`、`2026-07-09-...-design.md`）；§6 grep 词表太窄漏 `8.''167`/`:40''10`/`vol1''/` | 文件核实 | 自动：词表加 `C:/''Users|/Users/|OTC-''Fund|8\.''167|:40''10|vol1''/|fn-''nas|home''lab`；§3.4 补替换项 |
| E5(F10) | P3 | **CI 的 `pytest -m "not migration"` 是空操作**——无 migration 标记；迁移测试是自含 SQLite（`tests/test_migration_*.py`），无外部服务依赖 | `pyproject.toml` / `tests/` 核实 | 自动：CI 直接 `uv run pytest` 全量（迁移测试是 schema-drift 最佳护栏，应收进 CI 而非排除） |
| E6(F22,F23) | P2 | **Phase B 是功能开发非核对**：无「修复代码/降级文档/列 roadmap」处置规则；financial-correctness 回归风险压在唯一生产验证的 ssq 上 | 与 CEO C1 同源 | **用户决策项（终门口）**：核对报告加处置列；建议拆 B1（文档修正+诚实边界+已实现面测试，先发）+ B2（缺口→roadmap，不阻塞发布） |
| E7(F24) | P3 | **qxc 近似判定是静默失败风险**——前缀命中近似可能漏判/误判中档，违反「中奖永不静默漏通知」 | `compare.py` QxcHybridCompare | 自动：README 标注 qxc「近似，待真实开奖校准」；考虑保守回退（不确定档按「潜在中奖-人工核对」） |
| E8(杂项P3) | P3 | F1 镜像同步延迟（文档化+短间隔）、F2 gitea 只读+弃置 deferred auto-PR、F4 submodule deinit 卫生、F7 重写历史理由措辞（实际是 SHA churn 非约定）、F11 CI 补 `npm test`、F17 `.dockerignore` 加 `.claude/`、F18 冒烟先 `cp .env.example .env`、F19 验证镜像同步、F20 门禁加子模块缺席检查 | 核实 | 自动：全部并入 plan/CI/门禁 |

### Eng-2 必产输出

**NOT in scope（延后理由）**
- gitea 只读后弃置 deferred auto-PR 自动化（F2）——未来若建则迁 GitHub Actions
- 历史邮箱/IP 完整清理（filter-repo）——致 SHA churn，与 NAS 零改动冲突；接受+文档化（E3）
- Phase B 未实现玩法（复式/胆拖/组选/12 玩法 fc3d/qxc 校准）——若 E6 采纳则 B2 进 roadmap

**What already exists（复用）**
- `tests/conftest.py` autouse env-reset fixture——CI hermetic 基础
- 自含 SQLite 迁移测试——CI 免费 schema-drift 护栏（E5 改为纳入全量）
- `.dockerignore` 已排 `.env`——补 `.claude/` 即可（E8-F17）

**架构 ASCII（公开拓扑）**
```
[ 开发机 ] --git push--> [ GitHub（主源，AGPL-3.0-only，净化版） ]
                              │ pull-mirror（cron 同步）
                              ▼
                    [ NAS Gitea（只读镜像） ] --git clone--> [ NAS Docker 部署 ]
                              ▲
[ 内网 dev ] --setup-workflow-engine.sh（env 读 URL，默认跳过）--> [ .claude/workflow-engine ]（gitignored）
```

**失败模式注册表（新 codepath）**

| CODEPATH | FAILURE MODE | RESCUED? | TEST? | USER SEES? | LOGGED? |
|---|---|---|---|---|---|
| setup-workflow-engine.sh | env URL 缺失 | Y（打印 dev-only 提示） | P1 | 明确提示 | Y |
| GitHub 首推 | 历史泄露（邮箱/IP/路径） | 预推送门禁 | Y（grep+gitleaks） | 门禁拦截 | Y |
| gitea 镜像同步 | 部署拉取滞后 | 文档化+手动 Sync Now | Y（F19） | 部署滞后一期 | Y |
| qxc 判定 | 近似漏判中档 | 保守回退+README 标注 | B2 | 「潜在中奖-人工核对」 | Y |
| 冒烟 smoke | `.env` 缺失 compose 失败 | `cp .env.example .env` | Y | 报错明确 | Y |

**Worktree 并行**：Phase A 三独立 lane——(A1) 文档净化+README+LICENSE、(A2) 子模块移除+setup 脚本+.dockerignore、(A3) CI+门禁脚本；Phase B 逐彩种 lane（共用 domain/ 需串行）。A 三 lane 可并行 worktree，B 待 A 后。

**Phase 3 完成。** Eng subagent 20 项发现（F1-F25 含正例 4），采纳 E1-E8（含 E6 留门）。无 P1，E6 与 CEO C1 收敛 → 终门口。

| # | Phase | Decision | Classification | Principle | Rationale | Rejected |
|---|-------|----------|-----------|-----------|-----------|----------|
| 11 | Eng | E1 setup 脚本 env 化（F3 自相矛盾） | Mechanical | P1 完整性 | 硬编码内网 URL 违反自身门禁 | 字面 IP 入脚本 |
| 12 | Eng | E2 C3/C6 物化为 CI + 预推送门禁（F9/F21） | Mechanical | P1 完整性 | 已采纳决策须落地 | 仅文档承诺 |
| 13 | Eng | E3 历史邮箱泄露：接受+文档化（F6） | Mechanical | P3 务实 | filter-repo 致 SHA churn，现实接受 | filter-repo 全清 |
| 14 | Eng | E4 词表加宽真实标识（F13/F14） | Mechanical | P1 完整性 | 绝对路径/私有项目名/内网 IP 泄漏 | — |
| 15 | Eng | E5 CI 改全量 pytest（F10） | Mechanical | P5 显式 | `-m not migration` 空操作，迁移测试 hermetic | — |
| 16 | Eng | E6 Phase B 拆分 B1/B2 → 留门（F22/F23） | User decision | 用户主权 | 与 CEO C1 同源，改变用户范围 | 暂不自动采纳 |
| 17 | Eng | E7 qxc 近似保守回退 + README 标注（F24） | Mechanical | P1 完整性 | 静默漏通知风险 | 裸近似上线 |
| 18 | Eng | E8 杂项并入（F1/F2/F4/F7/F11/F17/F18/F19/F20） | Mechanical | P2/P3 | 低风险一次性采纳 | — |

## Phase 3.5 — DX Review（开发者体验）

**外部声音**：Claude 独立 DX subagent（`[subagent-only]`）。目标开发者：自托管 hobbyist / 潜在 contributor，关心正确性（金钱相关工具）。模式：DX POLISH（现有产品公开化，打磨每个触点）。

### DX-1 采纳的修复（自动，关键断言已核实）

| # | 严重度 | 发现 | 证据（已核实） | 处置 |
|---|---|---|---|---|
| D1 | **P1** | **`cp .env.example .env && docker compose up` 必崩溃**：a) `CRYPTO_KEY_V1=` 空值，启动即 alembic 迁移 → `Settings()` 校验 `min_length=44` 抛 pydantic ValidationError；b) `.env.example:40-41` TLS_CERT/TLS_KEY 已置值 → Dockerfile CMD 走 SSL → 证书文件缺失 FileNotFoundError → crash-loop | `.env.example:7,40-41` / `config.py:48` / `Dockerfile:73-79` | 自动：`.env.example` 修——`CRYPTO_KEY_V1=` 留空注释+生成命令；TLS 变量**注释掉**（默认走 HTTP）；README 快速开始含密钥生成 |
| D2 | **P1** | **HTTP 局域网登录死循环**：`COOKIE_SECURE=true` 默认（Secure cookie 被 http 丢弃）+ `CORS_ORIGINS=["http://localhost:5173"]` → `http://<LAN_IP>:8280` 登录后 cookie 不回传 / CORS 403 | `.env.example:29,31` / `auth.py` Origin 校验 / `config.py:144-157` | 自动：README 快速开始内嵌**访问模式表**（HTTP+LAN IP → `COOKIE_SECURE=false` + `CORS_ORIGINS=["http://<LAN_IP>:8280"]`；HTTPS+域名 → true + 域名），从 deploy.md 平移 |
| D3 | P2 | 数据源 key 获取路径缺失（MXNZP app_id/app_secret 双参数、QPS=1 免费档；JUHE 可选备源；AMAP 可选） | README 计划 | 自动：README 增「数据源注册」节 |
| D4 | P2 | key 缺失时静默：`backfill.py:38-40` 仅 logger.info，dashboard 空，`validate_startup()` 不查数据源 key | `backfill.py` / `main.py:181-208` | 自动：启动时数据源 key 均空 → 明确告警（log + /health degraded 字段）；README 排障「无 key ⇒ 无数据」 |
| D5 | P2 | **公开已知 JWT_SECRET 默认值**：`change-''me-to-...`（51 字符过 min_length=32 校验）→ 字面快速开始跑公知签名 key，admin 会话可伪造（金钱相关工具 + 邀请码） | `.env.example:3` / `config.py:45` | 自动：`.env.example` 生成命令 + README 强提示必须改；可考虑 config 加「等于示例值则拒启」 |
| D6 | P2 | 公开版无 CONTRIBUTING.md / issue 模板 / SECURITY.md → OSS 采纳差 + 合规红线缺正式落点 | 计划 | 自动：增 CONTRIBUTING.md（合规红线流程）、issue 模板（bug/feature）、SECURITY.md |
| D7 | P3 | CLI 已良好但 README 未文档化 `create-admin` / `reset-password` / `backfill-draw-costs`；dev 端口文档不一致（CLAUDE.md 说 8000，vite proxy 8280）；升级无备份/标签/CHANGELOG 指引 | CLI / docs | 自动：README 增 CLI 表 + 升级指南（备份→pull→迁移）；端口统一 8280 |
| D8 | P3 | §6 冒烟自身会踩 D1/D2：改为断言**裸路径** `cp .env.example .env && docker compose up && curl /health` 200（含密钥生成 + 非 TLS） | §6 | 自动：§6 冒烟 = README 快速开始原样执行 |

### DX-2 评分卡

| 维度 | 当前 | 修复后 | 依据 |
|---|---|---|---|
| Getting Started | **2/10** | 8/10 | 现快速开始崩溃循环（D1/D2）→ 密钥+TLS+访问模式完备后 15-25 分钟 |
| API/CLI | 7/10 | 8/10 | CLI 良好，补 README 文档（D7） |
| Error Messages | 5/10 | 7/10 | 缺 key 静默（D4）、首败不明确 → 启动告警 |
| Documentation | 4/10 | 8/10 | deploy.md 是好原料，补 README/CONTRIBUTING/SECURITY（D6） |
| Upgrade Path | 4/10 | 7/10 | 补备份+迁移+标签/CHANGELOG（D7） |
| Dev Environment | 7/10 | 8/10 | uv/npm 可跑，端口统一（D7） |
| Community | 3/10 | 6/10 | 补 CONTRIBUTING/issue 模板/SECURITY（D6） |
| DX Measurement | 2/10 | 5/10 | 无 TTHW/反馈机制（延后） |

**TTHW**：当前「崩溃/无界」→ 目标 15-25 分钟（Docker-capable 自托管者）。**Competitive tier**：Needs Work → Competitive。**Magical moment**：缺失（首次 `curl /health` 200 即魔法时刻，README 文案承载）。

**NOT in scope（DX 延后）**：TTHW 测量埋点、反馈渠道（GitHub Discussions）、国际化 README 双语全文（首段英文合规声明已含，全文翻译延后）——发布后按社区反馈加。

**Phase 3.5 完成。** DX subagent 8 项发现，采纳 D1-D8（2 个 P1 已核实）。Overall 4→8/10。D1/D2 与 §6 冒烟自洽问题收敛。

| # | Phase | Decision | Classification | Principle | Rationale | Rejected |
|---|-------|----------|-----------|-----------|-----------|----------|
| 19 | DX | D1 .env.example 空 CRYPTO + TLS 默认注释（P1-1） | Mechanical | P1 完整性 | 裸路径启动必崩已核实 | 保持现状 |
| 20 | DX | D2 访问模式表（P1-2 登录死循环） | Mechanical | P1 完整性 | 局域网登录死循环已核实 | 保持现状 |
| 21 | DX | D3 数据源注册节 | Mechanical | P3 务实 | 快速开始需 key | — |
| 22 | DX | D4 缺 key 启动告警 | Mechanical | P1 完整性 | 静默无数据 | — |
| 23 | DX | D5 JWT 公知默认值处理 | Mechanical | P3 务实 | 安全默认 | — |
| 24 | DX | D6 CONTRIBUTING/issue 模板/SECURITY.md | Mechanical | P1 完整性 | OSS 采纳 + 合规落点 | — |
| 25 | DX | D7 CLI 文档 + 升级指南 + 端口统一 | Mechanical | P5 显式 | 文档一致性 | — |
| 26 | DX | D8 §6 冒烟断言裸路径 | Mechanical | P1 完整性 | 冒烟自洽 | — |

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/autoplan` | 范围与策略 | 1 | ISSUES_OPEN | 前提确认；采纳 C2-C7（净化/门禁/README 诚实边界），C1 留门 |
| Codex Review | `/autoplan` | 独立第二意见 | 0 | N/A | Codex 未安装，仅 Claude subagent |
| Eng Review | `/autoplan` | 架构与测试（required） | 1 | ISSUES_OPEN | 无 P1；采纳 E1-E8（setup 脚本 env 化/CI 落地/词表加宽/全量 pytest），E6 留门 |
| Design Review | `/autoplan` | UI/UX | 0 | SKIPPED | 无 UI 范围 |
| DX Review | `/autoplan` | 开发者体验 | 1 | ISSUES_OPEN | 4→8/10；采纳 D1-D8（含 2 个 P1 首跑崩溃：.env.example 空 CRYPTO+TLS、局域网登录死循环） |

- **VERDICT:** CEO + ENG + DX 已评审，无 P1 未决。终审**批准**（用户确认）：25 条已采纳修复全通过，Phase B 拆 B1+B2。CLEARED — 可进入 writing-plans 生成实施 plan。
- **CROSS-MODEL:** CEO 与 Eng 两 subagent 独立收敛于同一发现（Phase B 是功能开发非核对），DX subagent 独立核实首跑崩溃——交叉验证强信号。

NO UNRESOLVED DECISIONS
