# 兑奖了吗？（lottery-notification）

> **Compliance notice (English):** This project is a **post-draw checking and personal ticket-management tool only**. It does **not** provide — and will never add — number prediction, AI-based "recommendations", hot/cold number ranking, guaranteed-win claims, ticket purchasing, or payment services. Trend charts, where shown, are plain frequency distributions without ranking or recommendation, accompanied by an explicit randomness notice.

多用户中国彩票开奖**自动核对与通知**系统：你维护固定号码池，系统每期自动比对官方开奖结果，并按你配置的渠道（Bark / 飞书 / 邮件）与策略（每期推 / 仅中奖推）推送。覆盖福彩 + 体彩 7 大主流彩种。设计目标是家庭 NAS 自托管（小圈子邀请制），架构 API-first，可扩展。

## 合规红线（最高优先级）

系统定位「**事后核对 + 个人号码管理**」，**绝不**包含：号码预测 / AI 推荐 / 必中宣传 / 冷热号排序推荐 / 购彩代购支付 / 高频彩私彩。走势图为常规功能（综合分布 + 频次，不排序、不标冷热、不推荐，附显著随机性声明）；选号仅用户自选 / 机选，系统不基于走势做任何推荐。**任何「选号辅助 / 预测」类的 Issue / PR 将被直接关闭**（见 CONTRIBUTING.md）。

## 功能

- 7 彩种开奖自动抓取：**双源交叉校验**（主源 MXNZP + 备源聚合数据，号码一致才入库；不一致拒绝入库 + 告警）——准确性优先于及时性
- 号码池管理：固定号码追投，倍投 1–99 倍，大乐透追加投注
- 自动比对：开奖入库即比对一次，路径 A（大奖当晚即时简讯）与路径 B（次日 07:00 汇总）复用同一比对结果
- 浮动奖金回填：一二等奖开奖当晚标「待官方派奖」，22:00 起自动回填官方公布金额
- 多渠道通知：Bark / 飞书 / 邮件，可插拔；免打扰时段；全渠道失败 → admin Bark 兜底告警
- Web UI：仪表盘（附近代销点地图 / 成本统计 / 走势图）、号码池、开奖浏览、中奖记录、后台管理（用户 / 邀请码 / 重置密码）
- 成本与公益金统计：按开奖日记账，仪表盘可视化
- 密码三路径重置：用户自助（邮件验证码）/ admin 后台 / CLI 运维兜底

## 支持彩种与能力边界（诚实声明）

| 彩种 | code | 号码结构 | 开奖日 | 已实现玩法 |
|---|---|---|---|---|
| 双色球 | ssq | 红球 6/33 + 蓝球 1/16 | 二/四/日 | 单式 |
| 大乐透 | dlt | 前区 5/35 + 后区 2/12 | 一/三/六 | 单式（含追加） |
| 七乐彩 | qlc | 基本号 7/30 + 特别号 1/30 | 一/三/五 | 单式 |
| 七星彩 | qxc | 前区 6 位 0–9 + 后区 1 位 0–14（2020 改版） | 二/五/日 | 单式 |
| 福彩 3D | fc3d | 3 位 000–999 | 每日 | 单选 |
| 排列 3 | pl3 | 3 位 000–999 | 每日 | 直选 |
| 排列 5 | pl5 | 5 位 00000–99999 | 每日 | 直选 |

奖级规则版本：双色球 / 大乐透按 **2026-02 新规**（大乐透九档并七档）；七星彩按 **2020-10 改版**（任意对位计数）；固定档金额见 `app/domain/prize_tables.py`（可配置数据文件）。

**已知限制（Roadmap，见下文）：**

- 复式 / 胆拖 / 组选 / 定位复式等组合玩法**未实现**——只支持每注一组号码的单式 / 直选 / 单选。创建不支持的玩法会在 API 层被明确拒绝（400），不会静默漏比对。
- 双色球「福运奖」（2026 新规：奖池 ≥15 亿时中 3 红球也得 5 元）依赖奖池数据，**未实现**——3+0 暂不判中奖。
- 大乐透固定档在奖池 ≥8 亿时的上浮金额（如三等 5000→6666 元）未实现——按基础金额展示，可能略低于实际派奖。
- 七乐彩三等奖为浮动奖——开奖当晚显示「待官方派奖」，回填后显示实际金额。

## 技术栈

后端 Python 3.12（uv）/ FastAPI / SQLModel + SQLite（WAL，可迁 PostgreSQL）/ Alembic / APScheduler / httpx；前端 Vue 3 + Vite + Pinia + UnoCSS + ECharts；部署单容器 Docker。

## 架构

```text
客户端(Vue3 SPA) → FastAPI(REST) → 应用服务(调度/获取/比对/推送/统计)
  → 领域层(纯逻辑: 彩种规格+比对策略+奖级, 无DB/网络) → 适配层(数据源) + 基础设施(SQLite)
```

正确性设计（本项目的差异化卖点）：领域层零 IO（import-linter 强制）；比对只做一次、两路推送复用；DB 写单事务一次 commit、逐行故障 SAVEPOINT 隔离；「中奖永不静默漏通知」是全系统的最高纪律。详见 `docs/superpowers/specs/2026-06-16-lottery-notification-design.md`。

## 快速开始（Docker，约 15 分钟）

前置：Docker + Docker Compose；宿主机有 `openssl` 与 `python3`（仅用于生成密钥）。

```bash
git clone <本仓库地址>
cd lottery-notification
./scripts/init-env.sh        # 由 .env.example 生成 .env 并自动填入随机密钥
docker compose up -d --build # 首次构建约 3–5 分钟
curl http://localhost:8280/health
# 期望: {"status":"ok","tz":"Asia/Shanghai","db":"ok","data_sources":"missing"}
```

创建首个管理员并访问：

```bash
docker compose exec app uv run python -m app.cli create-admin --username admin --password '<强密码>'
# 浏览器打开 http://localhost:8280 → admin 登录 → 后台生成邀请码 → 邀请用户
```

> `data_sources:"missing"` 表示还没配数据源 key（首次启动正常）——配上 MXNZP key 后重启即开始抓开奖。不配 key 服务能跑但永远抓不到数据（README 末尾「排障」）。

### 访问模式表（按访问方式改 .env，改完 `docker compose up -d` 生效）

| 访问方式 | `COOKIE_SECURE` | `CORS_ORIGINS` 示例 |
|---|---|---|
| HTTP + localhost（本机） | `true`（默认即可） | `["http://localhost:8280"]` |
| HTTP + 局域网 IP | **`false`**（否则 cookie 不回传，登录死循环） | `["http://<NAS_IP>:8280"]` |
| HTTPS + 域名 / 自签证书 | `true` | `["https://lottery.example.com"]` |

HTTPS（自签证书）配置步骤见 `.env.example` 末尾注释。

### 数据源注册（免费）

| 源 | 用途 | 申请 |
|---|---|---|
| MXNZP | **主源**（必填，否则抓不到开奖） | mxnzp.com 注册 → 创建应用得 `MXNZP_API_KEY`(app_id) + `MXNZP_APP_SECRET`(app_secret) 双参数；免费档 QPS=1，系统已内置限速 |
| 聚合数据 JUHE | 备源（强烈建议：双源交叉校验是准确性核心） | juhe.cn 申请「彩票开奖」API 得 `JUHE_API_KEY`；不配则单源降级运行 |
| 高德 AMAP | 仪表盘「附近代销点」POI（可选） | lbs.amap.com 申请 Web 服务 key 得 `AMAP_API_KEY`；不配回退示例数据 |

## CLI 一览（容器内执行）

| 命令 | 用途 |
|---|---|
| `docker compose exec app uv run python -m app.cli create-admin --username admin` | 创建首个 admin（bootstrap） |
| `docker compose exec app uv run python -m app.cli reset-password --username <名>` | 重置任意用户密码（运维兜底） |
| `docker compose exec app uv run python -m app.cli ssq` | 手动跑一期 ssq 端到端冒烟（抓取→校验→比对） |
| `docker compose exec app uv run python -m app.cli backfill-history` | 回填各彩种最近 50 期历史开奖 |
| `docker compose exec app uv run python -m app.cli backfill-draw-costs` | 回填历史期次成本（DrawCost） |
| `docker compose exec app uv run python -m app.cli recompare --dry-run` | 奖级表修正后重算存量比对行（先 dry-run 看影响面；实现见 plan-10/T6） |

## 升级

```bash
docker compose exec app /app/backup.sh   # 1. 先备份（ backups/ 保留 30 天）
git pull                                 # 2. 拉新代码
docker compose up -d --build             # 3. 重建（启动时自动 Alembic 迁移）
curl http://localhost:8280/health        # 4. 确认 200
```

回滚：`git checkout <旧 commit> && docker compose up -d --build`；数据从 `backups/` 恢复（详见 `docs/deploy.md`）。

## 开发与测试

```bash
uv sync --extra dev          # 装依赖（uv 自动建 Python 3.12 venv）
uv run pytest -q             # 后端全量测试
uv run ruff check .          # lint
uv run lint-imports          # 领域层零 IO 架构守护
uv run uvicorn app.main:app --reload --port 8280   # 本地起 API（需 .env）
cd web && npm install && npm run dev   # 前端开发（代理 /api → :8280）
cd web && npm test && npm run build    # 前端测试与构建
```

## 目录结构

```text
app/
├── domain/            # 纯逻辑层（彩种规格/比对策略/奖级表），零 IO
├── adapters/          # 数据源适配器（MXNZP/聚合/福彩官网/体彩）
├── services/          # 应用服务（抓取/比对/推送/回填编排）
├── infrastructure/    # Repository + 加密（Fernet 多版本）
├── models/            # SQLModel 全表
└── main.py            # FastAPI app
web/                   # Vue3 SPA
docs/                  # deploy.md / 设计 spec / 彩种规则权威参考
scripts/               # init-env.sh / publish-check.sh / setup-workflow-engine.sh
```

## 关于 `.claude/workflows/run-plans.js`

仓库内带有作者自用的 plan 编排器派生副本（纯开发工具，无密钥，与运行/部署/测试无关）。其上游引擎为私有仓库；内部开发者可用 `WORKFLOW_ENGINE_URL=<地址> ./scripts/setup-workflow-engine.sh` 恢复。**不需要它的用户可直接忽略 `.claude/` 目录。**

## 许可证与第三方声明

- 本项目以 **AGPL-3.0-only** 开源（见 `LICENSE`）。**任何人修改本项目后以网络服务（含 SaaS、内部平台）形式提供使用，必须按 AGPL-3.0-only 开源其全部衍生代码**（§13 网络交互条款）。第三方仅调用未改造的本项目 API 不触发传染。
- 本项目仅调用第三方数据 API，不附带其数据。使用者需自行遵守 [MXNZP](https://www.mxnzp.com)、[聚合数据](https://www.juhe.cn)、[高德开放平台](https://lbs.amap.com) 的服务条款；彩票规则与开奖数据的最终解释权属中国福彩 / 体彩官方。

## 免责声明

本系统仅供个人核对已购彩票使用，不构成任何购彩建议。奖级与奖金以官方公告为准；中奖信息请以彩票实体票面与官方兑奖渠道为最终依据。理性购彩，量力而行。

## 排障

| 现象 | 排查 |
|---|---|
| 启动 crash-loop，日志报 `jwt_secret`/`crypto_key_v1` 校验失败 | `.env` 密钥为空或过短——重跑 `./scripts/init-env.sh`（先删旧 `.env`）或按 `.env.example` 注释手工生成 |
| 服务正常但永远没有开奖数据 | 数据源 key 未配——日志有「数据源 key 全部为空」WARNING，`/health` 返回 `data_sources:"missing"`；按「数据源注册」配 MXNZP |
| 局域网 HTTP 登录后立刻掉出 / 登录 403 | `COOKIE_SECURE` 与 `CORS_ORIGINS` 与访问方式不匹配——对照「访问模式表」 |
| `docker compose up` 端口冲突 | 默认 8280；改 `docker-compose.yml` 的 `ports` 左侧与 `CORS_ORIGINS` |
