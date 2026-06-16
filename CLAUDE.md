# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目状态

**设计/规划阶段**：spec、6 个分阶段实施 plan、仪表盘 prototype 已完成（见 `docs/superpowers/`）；**业务代码尚未实现**。开始编码前先读 spec 和对应 plan。

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
- **策略模式彩种引擎**：新增彩种 = 加 `LotterySpec` 配置 + 若需要加 `CompareStrategy`（`PartitionCompare` 分区型 ssq/dlt/qlc；`PositionalCompare` 按位型 fc3d/qxc/pl3/pl5）。领域层核心代码不动。
- **双源容灾**：MXNZP 主 + 聚合数据备，**交叉校验号码一致才入库**（`verified=true`），不一致拒绝入库+告警。准确性优先于及时性。
- **比对只做一次**：开奖结果入库触发一次比对写 `comparisons`；路径A（大奖当晚即时简讯）与路径B（次日 07:00 汇总）**复用** comparisons，不重复比对。
- **范围三层**：开奖获取=全局（所有彩种都拉）；比对/推送=仅用户追投的彩种（号码池决定，没追的不比对不推送）。
- **用户隔离**：所有用户私有表强制 `user_id`，repository 查询一律 `WHERE user_id=?`，FastAPI `current_user` 依赖注入；开奖结果 `draw_results` 是全局共享数据。
- **金额用分**（int）存储，展示层再除 100，避免浮点。

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
- 号码风格：`partition`（红/蓝分区，ssq/dlt/qlc）/ `positional`（按位 0-9，fc3d/qxc/pl3/pl5）
- 开奖日 `draw_days` 用 Python 0-based 周几（`date.weekday()`）：周一=0 … 周日=6
- 通知渠道：bark/feishu/dingtalk/wecom，可插拔，每用户配置，无主备
- 推送策略（每用户×每彩种）：`every`(每期推) / `win_only`(仅中奖推)
- 推送时机分层：大奖当晚即时简讯 + 次日 07:00 汇总（时间可配，默认 07:00）
- 全程时区 Asia/Shanghai

## 文档导航（权威来源）

- `docs/superpowers/specs/2026-06-16-lottery-notification-design.md` — **设计 spec（15 节，需求/架构/数据/合规的单一事实源）**
- `docs/superpowers/plans/2026-06-16-phase{1-6}-*.md` — **6 个分阶段实施 plan**：1 领域层 → 2 核心闭环 → 3 用户+API → 4 前端 → 5 扩展 → 6 部署。开发按 1→6 顺序，每个 plan 独立可测试交付，TDD（先写测试）。
- `docs/superpowers/prototypes/01-dashboard.html` — 仪表盘 prototype（视觉基准，10 页中已完成 1 页）

## NAS 部署约束

- 端口 **8280**（已核实空闲，避开 NAS 已占用端口）
- **`restart: always`**（FnOS 关机会 `docker stop` 所有容器，`unless-stopped` 策略不会自启——这是该 NAS 的已知坑）
- 部署目录：`/vol1/1000/Docker/lottery-notification/`
- 密钥（数据源 API key、`JWT_SECRET`、`CRYPTO_KEY`）从 `.env` 注入，不进库不进日志
