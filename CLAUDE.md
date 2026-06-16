# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目状态

**设计/规划阶段**：spec、6 个分阶段 plan、2/10 页 prototype 已完成（见 `docs/`）；**业务代码尚未实现**。开始编码前先读 spec 和对应 plan。

## 项目是什么

"兑奖了吗？"——多用户中国彩票开奖**自动核对与通知**系统。覆盖福彩+体彩 7 大主流彩种。用户维护固定号码池，系统每期自动比对开奖结果并按用户配置的渠道/策略推送。部署在家庭 NAS（小圈子邀请制共享），架构预留大规模公开扩展。未来计划 iOS 原生 App（API-first 复用）。

## ⚠️ 合规红线（最高优先级，任何改动都要守住）

系统定位**「事后核对 + 个人号码管理」**，**绝不**包含：号码预测 / AI 推荐 / 必中 / 冷热号排序推荐 / 购彩代购支付 / 高频彩私彩。走势图仅"合规降级版"（历史连线 + 近 N 期频次，不排序/不标冷热/不推荐 + 显著随机性声明 + 公开版默认关闭）。**遇到"选号辅助/预测"类需求必须拒绝**。详见 spec §9。

## 技术栈

- 后端：Python 3.12、FastAPI、SQLModel + SQLite（类型选可迁 PostgreSQL）、APScheduler、httpx、PyJWT、passlib、cryptography(Fernet)
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

## 彩种规则（⚠️ 必读，领域层正确性的前提）

**权威源：[`docs/reference/lottery-rules.md`](docs/reference/lottery-rules.md)**（子代理复核福彩/体彩官网，含号码规则/玩法/称呼/奖级/倍投/追加 + 来源链接）。实现 `LotterySpec`/`prize_tables`/号码校验前**务必对照该文档**，不要凭记忆。

易错点（已复核修正）：
- **七星彩 2020-10-13 改版**：现行为"**前区 6 位（0-9）+ 后区 1 位（0-14）**"混合型，**不是纯 7 位 0-9**、不是纯 positional。
- **玩法按彩种配套，禁止硬编码"单式/复式/胆拖"三件套**：分区型（双色球/大乐透/七乐彩/七星彩）= 单式/复式/胆拖；按位型（福彩3D/排列3/排列5）= 单选/直选/组选三/组选六。
- **福彩3D 现行叫"单选"**（旧称"直选"已废），共 12 种玩法；排列3/5 沿用"直选"。两套术语，勿混。
- **倍投**：所有彩种 2–99 倍（影响投入与中奖金额）。**追加投注仅大乐透**（基本 2 元 + 追加 1 元，追加仅参与一二等奖 80%）。
- 七乐彩特别号**同源于 01–30 池**（非独立分区）。

## 常用命令

> 代码尚未实现，以下为 plan 规划的命令（实现后生效）。

```bash
# 后端
pip install -e ".[dev]"                                    # 安装（含 dev 依赖）
pytest -v                                                  # 全量测试（领域层覆盖率门禁 95%+）
pytest tests/domain/test_partition_compare.py -v           # 单文件
pytest tests/domain/test_partition_compare.py::test_x -v   # 单测试
python -m app.cli ssq                                      # 手动触发一期闭环（获取→比对→推送）
uvicorn app.main:app --reload                              # 启动 API（开发）

# 前端（web/）
cd web && npm install
npm run dev          # 开发（代理 /api → localhost:8000）
npm run build        # 产物到 ../static（后端静态托管 SPA）
npx vitest run       # 组件测试

# 部署（NAS Docker，端口 8280）
docker compose up -d --build
```

## 关键约定

- 彩种代码：`ssq`(双色球)/`dlt`(大乐透)/`qlc`(七乐彩)/`fc3d`(福彩3D)/`qxc`(七星彩)/`pl3`(排列3)/`pl5`(排列5)
- 号码风格：partition（多区：红/蓝、前/后区、基本/特别号）/ positional（按位 0-9）
- 开奖日 `draw_days` 用 Python 0-based 周几（`date.weekday()`）：周一=0 … 周日=6
- 通知渠道：bark/feishu/dingtalk/wecom，可插拔，每用户配置，无主备
- 推送策略（每用户×每彩种）：`every`(每期推) / `win_only`(仅中奖推)
- 推送时机分层：大奖当晚即时简讯 + 次日 07:00 汇总（时间可配，默认 07:00）
- 全程时区 Asia/Shanghai

## Prototype 协作模式（前端页面）

10 页 prototype 进行中（已完成：仪表盘、我的号码）。经验：
- **表单/选号/管理类页面**（号码、设置、后台）→ **手写 HTML 更稳**（OD 易截断/跑偏/换视觉体系）。
- **纯展示类页面**（仪表盘、查询、走势、统计图表）→ OD 出效果快，但需修复截断/污染（OD 模型遇"彩票/赌博"主题会触发安全审查，把审查独白+`<artifact>`标签泄漏进 HTML）。
- OD 用 `qwen3.6-plus` 模型（非 kimi-k2.6，后者污染严重）+ brief 开头加合规声明。
- 所有页面**复用仪表盘的视觉系统**（CSS 变量 `--bg/--surface/--fg/--accent/--red-ball/--blue-ball`、`:root.dark` 深色、9 项导航、右上角用户区、红蓝球）。

## 文档导航（权威来源）

- `docs/superpowers/specs/2026-06-16-lottery-notification-design.md` — **设计 spec（15 节，需求/架构/数据/合规的单一事实源）**
- `docs/reference/lottery-rules.md` — **7 大彩种规则权威参考**（号码/玩法/称呼/奖级/倍投/追加 + 来源）
- `docs/superpowers/plans/2026-06-16-phase{1-6}-*.md` — **6 个分阶段实施 plan**：1 领域层 → 2 核心闭环 → 3 用户+API → 4 前端 → 5 扩展 → 6 部署。开发按 1→6，TDD（先写测试）。**⚠️ plan 暂未同步最新复核修正**（七星彩/玩法/倍投/追加），全部 prototype 完成后统一更新 plan。
- `docs/superpowers/prototypes/` — 页面 prototype（视觉基准）

## NAS 部署约束

- 端口 **8280**（已核实空闲，避开 NAS 已占用端口）
- **`restart: always`**（FnOS 关机会 `docker stop` 所有容器，`unless-stopped` 策略不会自启——这是该 NAS 的已知坑）
- 部署目录：`/vol1/1000/Docker/lottery-notification/`
- 密钥（数据源 API key、`JWT_SECRET`、`CRYPTO_KEY`）从 `.env` 注入，不进库不进日志
