# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目状态

**Plan 01–07 全部完成（基础设施 / 领域层 / 仓储核心闭环 / 调度推送 / 认证用户管理 / Web UI 部署 / 浮动奖金查询），681 passed, 1 skipped。** Plan 06 9 页 UI + Docker 部署 + CLI 全部落地；Plan 07 浮动奖金查询（CwlPrizeSource + SportteryPrizeSource JSON+PDF 降级 + FloatRefillWorker 金额公式 + 22:00 回填 cron）已合入。plan 在 `docs/superpowers/plans/`。改代码前先读 spec + 对应 plan；实现通过 **workflow orchestrator**（见下）自动跑 plan。

## 项目是什么

"兑奖了吗？"——多用户中国彩票开奖**自动核对与通知**系统。覆盖福彩+体彩 7 大主流彩种。用户维护固定号码池，系统每期自动比对开奖结果并按用户配置的渠道/策略推送。部署在家庭 NAS（小圈子邀请制共享），架构预留大规模公开扩展。未来计划 iOS 原生 App（API-first 复用）。

## ⚠️ 合规红线（最高优先级，任何改动都要守住）

系统定位**「事后核对 + 个人号码管理」**，**绝不**包含：号码预测 / AI 推荐 / 必中 / 冷热号排序推荐 / 购彩代购支付 / 高频彩私彩。走势图作为**常规功能**（综合分布图 + 频次，不排序/不标冷热/不推荐 + 显著随机性声明）；选号仅用户自选/机选，系统不基于走势推荐。**遇到"选号辅助/预测"类需求必须拒绝**。详见 spec §9。

## 技术栈

- 后端：Python 3.12（**用 uv，不用 pip/poetry**——本机系统 Python 是 3.9，项目锁 3.12，`uv sync` 自动建 3.12 venv）、FastAPI、SQLModel + SQLite（类型选可迁 PostgreSQL）、Alembic、APScheduler、httpx、PyJWT、passlib、cryptography(Fernet)
- 前端：Vue3 + Vite + Vue Router + Pinia + UnoCSS + ECharts
- 部署：Docker（NAS FnOS）

## 架构（big picture）

分层，**领域层零 IO 是核心纪律**：

```
客户端(Vue3 SPA) → FastAPI(REST) → 应用服务(调度/获取/比对/推送/统计)
  → 领域层(纯逻辑: 彩种规格+比对策略+奖级, 无DB/网络) → 适配层(数据源) + 基础设施(SQLite)
```

关键设计（需读 spec §4/§5/§7 才能拼出的全貌）：
- **策略模式彩种引擎**：新增彩种 = 加 `LotterySpec` 配置 + 若需要加 `CompareStrategy`。领域层核心代码不动。
- **双源容灾**：MXNZP 主 + 聚合数据备，**交叉校验号码一致才入库**（`verified=true`），不一致拒绝入库+告警。准确性优先于及时性。
- **比对只做一次**：开奖结果入库触发一次比对写 `comparisons`；路径A（大奖当晚即时简讯）与路径B（次日 07:00 汇总）**复用** comparisons，不重复比对。
- **范围三层**：开奖获取=全局（所有彩种都拉）；比对/推送=仅用户追投的彩种（号码池决定，没追的不比对不推送）。
- **用户隔离**：所有用户私有表强制 `user_id`，repository 查询一律 `WHERE user_id=?`，FastAPI `current_user` 依赖注入；开奖结果 `draw_results` 是全局共享数据。
- **金额用分**（int）存储，展示层再除 100，避免浮点。

### 代码结构

```
app/
├── domain/            # 纯逻辑层，零 IO（import-linter 强制）
│   ├── spec.py        # LotterySpec（hydrate from spec_json + 校验）
│   ├── compare.py     # 策略模式：PartitionCompare/PositionalCompare/QxcHybridCompare + REGISTRY
│   └── prize_tables.py# 7 彩种奖级表（可配置）
├── adapters/          # 数据源适配器（httpx，MockTransport 测试友好）
├── services/          # 应用服务层（编排，调 domain 不反向）
│   ├── fetch_service.py    # 双源交叉校验 + grace + 退避 + 幂等存储
│   ├── compare_service.py  # outbox 原子认领 + domain.compare + comparisons/prize_claims（per-ticket savepoint）
│   └── notifier.py         # 路径A/B 编排 + 多渠道降级重试 + DND
├── infrastructure/    # Repository 基类 + 加密（Fernet 多版本）
├── models/            # SQLModel 全 13 表 + apscheduler_jobs
└── main.py            # FastAPI app；lifespan 启动校验+种子
alembic/               # 首迁移 0001 含全 schema；import_linter 配置内联于 pyproject.toml
```

**SQLite 并发模型**：`pool_size=1` + WAL + `busy_timeout` —— 单写连接串行化，配合 APScheduler jobstore 独立 engine 避免写竞争。

### ⚠️ 静默失败纪律（silent-failure，最高优先级）

系统核心价值是「**中奖永不静默漏通知**」。实现时务必主动设防：

- **DB 写不得 split-commit**：一个逻辑操作必须**单事务一次 commit**
- **per-row 异常隔离用 savepoint**：循环里逐行处理必须 `with session.begin_nested():`（SAVEPOINT）隔离
- **批量循环里单行故障不得中断整批**：per-row try/except + log（含 `exc_info=True`），不阻断后续行
- **更正路径重置终态标记**：行被标 `unresolved=True` 后经官方更正重比，须重置 `unresolved=False`
- **datetime 时区对齐**：SQLite 对 datetime 做**字符串比较**且**存取会剥离 tzinfo**。凡与其他 datetime 字段比较的写入值，须与 `TimestampMixin.created_at = default_factory=datetime.utcnow`（**naive UTC**）同时区**同数值**。用 `datetime.now(timezone.utc).replace(tzinfo=None)`

## 彩种规则（⚠️ 必读，领域层正确性的前提）

**权威源：[`docs/reference/lottery-rules.md`](docs/reference/lottery-rules.md)**。实现 `LotterySpec`/`prize_tables`/号码校验前**务必对照该文档**。

易错点：
- **七星彩 2020-10-13 改版**：现行为"**前区 6 位（0-9）+ 后区 1 位（0-14）**"混合型
- **玩法按彩种配套**：分区型（双色球/大乐透/七乐彩/七星彩）= 单式/复式/胆拖；按位型（福彩3D/排列3/排列5）= 单选/直选/组选三/组选六
- **福彩3D 现行叫"单选"**（旧称"直选"已废）；排列3/5 沿用"直选"。两套术语，勿混。
- **追加投注仅大乐透**（基本 2 元 + 追加 1 元，追加仅参与一二等奖 80%）

## 常用命令

```bash
# 后端
uv sync --extra dev                                          # 装依赖（uv.lock 锁定；首次 uv python install 3.12）
uv run pytest -v                                             # 全量测试
uv run pytest tests/test_models_t4c.py::test_defaults -v     # 单测试
uv run uvicorn app.main:app --reload --port 8280               # 启动 API（需 .env；端口与部署对齐）
uv run alembic upgrade head                                  # 应用迁移
uv run lint-imports                                          # app.domain 不得 import infra/adapters/api/services

# 前端（web/）
cd web && npm install && npm run dev                         # 开发（代理 /api → :8280）
cd web && npm test                                           # vitest 组件/逻辑测试（不进 Python gate）
cd web && npm run build                                      # 产物到 ../static

# workflow orchestrator 测试
cd docs/superpowers/workflows && node --test 'tests/*.test.js'

# 部署（NAS Docker，端口 8280）
docker compose up -d --build
docker compose build                                         # 仅构建

# 更新 run-plans-engine（内部开发工具，gitignored，不入库）
export WORKFLOW_ENGINE_URL=<内网引擎仓库地址>   # 本机 shell 配置，勿写入仓库
./scripts/setup-workflow-engine.sh             # clone/更新引擎 + 同步派生副本
git add .claude/workflows/run-plans.js
git commit -m "chore(workflow): bump run-plans-engine"
```

**密钥**（`.env`，不进库不进日志；模板 `.env.example`）：`JWT_SECRET`（≥32 字符）、`CRYPTO_KEY_V1`（44 字符 Fernet key）、`MXNZP_API_KEY`/`JUHE_API_KEY`（数据源）、`SMTP_*`（email 渠道）、`ADMIN_BARK_KEY`。启动时 `validate_startup()` 端到端冒烟验证 crypto key。

## workflow orchestrator（执行 plan）

`.claude/workflows/run-plans.js` 自动执行 `docs/superpowers/plans/*.md`：每 task implementor(TDD RED→GREEN→REFACTOR) → review 三链并行(spec 逐行 ‖ quality 架构 ‖ silent-failure-hunter) → simplify → commit `feat(plan-X/T-Y)`。

**进度以 git 为单一事实源**——bootstrap 读 git log 的 `feat(plan-X/T-Y)` convention 跳过已完成 task。跨机器/跨 session 续跑无需 manifest：clone + 跑全新 workflow 即从未完成的 task 继续。

触发（Claude 调 Workflow 工具）：
```
Workflow({ scriptPath: '.claude/workflows/run-plans.js', args: { plan: '03' } })  # 单 plan
```

**⚠️ 续跑用「全新跑」，不要用 `resumeFromRunId`**：resume 回放缓存的 bootstrap agent，看不到 halt 后手动提交的 task。halt/限额/断 session 后一律全新跑。

**§2.4 模型策略**：开发用指定 opus/sonnet/haiku；一旦不可用（含 429、router stderr、不在 `Error.message` 的情形）→ halt + 保存进度 → **等用户发指令才 resume**。**绝不降级到可用 model**。

## 关键约定

- 彩种代码：`ssq`(双色球)/`dlt`(大乐透)/`qlc`(七乐彩)/`fc3d`(福彩3D)/`qxc`(七星彩)/`pl3`(排列3)/`pl5`(排列5)
- 开奖日 `draw_days` 用 Python 0-based 周几（`date.weekday()`）：周一=0 … 周日=6
- 通知渠道：bark/feishu/email（可插拔，每用户配置）
- 推送策略：`every`(每期推) / `win_only`(仅中奖推)
- 推送时机：大奖当晚即时简讯 + 次日 07:00 汇总
- 全程时区 Asia/Shanghai

## 文档导航

- `docs/superpowers/specs/2026-06-16-lottery-notification-design.md` — **设计 spec（15 节，需求/架构/数据/合规的单一事实源）**
- `docs/reference/lottery-rules.md` — **7 大彩种规则权威参考**
- `.claude/workflow-engine/USAGE.md` — **workflow orchestrator 使用指南（子模块）**
- `.claude/workflow-engine/workflow-design.md` — **workflow orchestrator 设计文档（含历史修复记录摘要）**
- `docs/superpowers/workflows/USAGE.md` — 旧版 workflow 使用指南（已迁移，以子模块为准）
- `docs/superpowers/workflow-design.md` — 旧版 workflow 设计文档（已迁移，以子模块为准）
- `docs/superpowers/plans/` — implementation plan（7 份业务 plan：01-07 全部完成）

## 部署约束

- 端口默认 **8280**（`docker-compose.yml` 可改，同步改 `CORS_ORIGINS`）
- **`restart: always`**（宿主机重启后自启；`unless-stopped` 会静默消失——tests/test_docker_t9.py 是护栏）
- 通用部署/运维流程见 `docs/deploy.md`；作者 NAS 专属细节在本地 `deploy-nas-internal.md`（gitignored，不入库）
- 密钥从 `.env` 注入，不进库不进日志
