# Phase 1 · Plan 6: Docker 部署到 NAS Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 将完整系统（后端 + 前端静态）打包为单 Docker 镜像，以 docker-compose 部署到 NAS（FnOS, <NAS_IP>），端口 8280，`restart: always`（FnOS 关机坑），SQLite 持久化，配置走 .env。

**Architecture:** 多阶段 Dockerfile（Node 构建前端 → Python 运行时托管 dist + 跑 FastAPI/uvicorn/scheduler）。单容器。部署目录 `/vol1/1000/Docker/lottery-notification/`，遵循 NAS 既有惯例。

**Tech Stack:** Docker、docker-compose、Uvicorn、Node（构建期）。

**前置依赖:** Plan 1-5 完成（应用可运行）。

**对应 Spec:** §4.3（部署）、§10（密钥）、NAS 维护记录（restart: always）

---

## Task 1: 多阶段 Dockerfile

**Files:**
- Create: `Dockerfile`
- Create: `.dockerignore`

- [ ] **Step 1: `Dockerfile`**

```dockerfile
# ---- Stage 1: 构建前端 ----
FROM node:20-alpine AS web-builder
WORKDIR /web
COPY web/package.json web/package-lock.json* ./
RUN npm ci || npm install
COPY web/ ./
RUN npm run build   # 产出 /web/../static（vite outDir: ../static）

# ---- Stage 2: Python 运行时 ----
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install -e ".[dev]" 2>/dev/null || pip install -e .
COPY app/ ./app/
COPY --from=web-builder /static ./static
RUN mkdir -p /app/data
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

> **前端构建产物路径：** Plan 4 Task 7 已设 `build.outDir: "../static"`。Stage 1 在 `/web` 构建，产出 `/static`（因 outDir 相对 web/ 上一级）。Stage 2 从 `/static` copy 到 `/app/static`，与 `app/main.py` 的 `STATIC = ../static` 一致。

- [ ] **Step 2: `.dockerignore`**

```
.git
__pycache__
*.pyc
.venv
node_modules
web/node_modules
data/
*.db
.env
.DS_Store
```

- [ ] **Step 3: 本地构建验证**

Run: `docker build -t lottery-notification:dev .` → 构建成功
Run: `docker run --rm -p 8280:8000 -e JWT_SECRET=test lottery-notification:dev` → 健康检查
Run: `curl http://localhost:8280/api/health` → `{"status":"ok"}`

- [ ] **Step 4: Commit**

```bash
git add Dockerfile .dockerignore
git commit -m "feat: 多阶段Dockerfile(前端构建+后端托管)"
```

---

## Task 2: docker-compose.yml（NAS 部署配置）

**Files:**
- Create: `docker-compose.yml`
- Create: `.env.example`

- [ ] **Step 1: `docker-compose.yml`**

```yaml
services:
  lottery-notification:
    build: .
    image: lottery-notification:latest
    container_name: lottery-notification
    restart: always              # 关键：FnOS 关机会 docker stop 所有容器，unless-stopped 不自启
    ports:
      - "8280:8000"              # 已核实 8280 空闲（避开 NAS 占用端口）
    volumes:
      - ./data:/app/data         # SQLite 持久化
      - ./config:/app/config     # 可选：外部配置覆盖
    env_file: .env
    environment:
      - TZ=Asia/Shanghai
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')"]
      interval: 60s
      timeout: 5s
      retries: 3
      start_period: 30s
```

> **restart: always 的含义：** 即使用 `docker stop` 手动停止，下次 Docker daemon 重启容器仍会自启。长期停服用 `docker compose down` 或 `docker update --restart=no`（见 NAS 维护记录 2026-05-11）。

- [ ] **Step 2: `.env.example`（全部配置项）**

```bash
# 数据库
DATABASE_URL=sqlite:///./data/lottery.db

# 安全（生产必改，用 openssl rand -hex 32 生成）
JWT_SECRET=CHANGE_ME_TO_RANDOM_32_BYTES

# 数据源（按需申请）
MXNZP_APP_ID=
MXNZP_APP_SECRET=
JUHE_API_KEY=

# 调度（默认值见 config.py，按需覆盖）
SUMMARY_HOUR=7
POLL_START_OFFSET_MIN=30

# 功能开关
TREND_ENABLED=false   # 走势图公开版默认关

# 时区
TZ=Asia/Shanghai
```

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml .env.example
git commit -m "feat: docker-compose(NAS部署 8280/restart:always/持久化/健康检查)"
```

---

## Task 3: NAS 部署执行 + 验证

**Files:**
- Create: `docs/deployment.md`（部署手册）

- [ ] **Step 1: 写 `docs/deployment.md`**

```markdown
# NAS 部署手册

## 前置
- NAS: <NAS_IP>（FnOS），SSH: `ssh -i ~/.ssh/fn_nas admin@<NAS_IP>`
- 已装 Docker + docker compose

## 部署步骤
1. 上传项目到 NAS：
   `ssh admin@<NAS_IP> "mkdir -p /vol1/1000/Docker/lottery-notification"`
   `rsync -av --exclude data --exclude .env ./ admin@<NAS_IP>:/vol1/1000/Docker/lottery-notification/`
2. SSH 进 NAS，配置 .env：
   `cd /vol1/1000/Docker/lottery-notification`
   `cp .env.example .env && nano .env`（填 API key + `openssl rand -hex 32` 生成 JWT_SECRET）
3. 构建启动：
   `docker compose up -d --build`
4. 验证：
   `curl http://localhost:8280/api/health` → {"status":"ok"}
   浏览器开 http://<NAS_IP>:8280 → 登录页

## 运维
- 查看日志: `docker compose logs -f`
- 重启: `docker compose restart`
- 更新代码: `git pull && docker compose up -d --build`
- 备份数据: `cp -r data/ data_backup_$(date +%F)/`
- 恢复: 停服 → 替换 data/ → 启服

## NAS 重启后验证
FnOS 重启后，`restart: always` 确保容器自启。验证：
`docker ps | grep lottery-notification` → running
`curl http://localhost:8280/api/health` → ok

## 数据源首次配置
- 注册 MXNZP（mxnzp.com）获取 app_id/app_secret，填入 .env
- 注册聚合数据（juhe.cn）获取 api_key，填入 .env
- 重启容器生效
```

- [ ] **Step 2: 执行部署（手动，按手册）**

Run（在本地，可选先本地验证）: `docker compose up -d --build` → `curl localhost:8280/api/health` → ok

Run（部署到 NAS，用户执行）: 按手册 rsync + ssh + compose up

> **注：** 实际 NAS 部署由用户在准备好 API key 后执行。本 plan 提供手册与可复现命令。

- [ ] **Step 3: 部署后冒烟测试（部署后执行）**

```bash
# 1. 健康检查
curl http://<NAS_IP>:8280/api/health
# 2. 注册首个 admin 用户（首次手动设 admin）
curl -X POST http://<NAS_IP>:8280/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"...","invite_code":"WELCOME"}'
# 3. SQL 将首个用户提为 admin（首次部署）
docker exec lottery-notification python -c "
from app.db.database import init_db
from sqlmodel import Session, select
from app.db.models import User
e = init_db()
with Session(e) as s:
    u = s.exec(select(User).where(User.username=='admin')).first()
    u.role='admin'; s.add(u); s.commit()
print('admin set')
"
# 4. 触发一次开奖获取比对
curl -X POST http://<NAS_IP>:8280/api/admin/trigger?lottery_code=ssq \
  -H "Authorization: Bearer <admin_token>"
```

- [ ] **Step 4: Commit**

```bash
git add docs/deployment.md
git commit -m "docs: NAS 部署手册(部署/运维/备份/冒烟测试)"
```

---

## Task 4: 数据备份与恢复自动化（可选 cron）

**Files:**
- Create: `scripts/backup.sh`

- [ ] **Step 1: `scripts/backup.sh`（NAS cron 每日备份 SQLite）**

```bash
#!/bin/bash
# 每日备份 lottery SQLite，保留 30 天
set -e
DIR=/vol1/1000/Docker/lottery-notification
BACKUP=$DIR/data_backups
mkdir -p $BACKUP
docker exec lottery-notification sqlite3 /app/data/lottery.db ".backup '$BACKUP/lottery_$(date +%F).db'"
find $BACKUP -name "lottery_*.db" -mtime +30 -delete
```

- [ ] **Step 2: NAS cron 注册（用户执行）**

```bash
# crontab -e (NAS admin)
0 3 * * * /vol1/1000/Docker/lottery-notification/scripts/backup.sh
```

- [ ] **Step 3: Commit**

```bash
git add scripts/backup.sh
git commit -m "ops: SQLite 每日备份脚本(保留30天)"
```

---

## Task 5: 部署验收清单

- [ ] 容器 `restart=always` 且 running
- [ ] `curl http://<NAS_IP>:8280/api/health` 返回 ok
- [ ] 浏览器登录 → 仪表盘可见
- [ ] 注册首个用户 → 提为 admin
- [ ] 配置 Bark 渠道 → 手动触发一期比对 → 收到推送
- [ ] 双源 API key 配置后，次日 07:00 自动汇总推送到达
- [ ] NAS 模拟重启（`docker compose restart`）→ 容器自启 → 服务恢复
- [ ] 备份脚本手动跑一次 → 产出 db 备份文件

---

## Self-Review（已执行）

**1. Spec 覆盖：** §4.3 部署（单容器/8280/restart always/持久化卷）→ Task 1-2 ✅；§10 密钥（env 注入 JWT_SECRET）→ Task 2 ✅；NAS 维护记录（restart: always 坑）→ Task 2 注明 ✅。
**2. 占位符：** 实际 NAS 部署由用户执行（API key 准备好后），手册提供可复现命令，非占位符。冒烟测试给出具体 curl。✅
**3. 路径一致：** `/app/data`、`/app/static`、端口 8280:8000、`app.main:app` 全 plan 与 Plan 1-5 一致。✅

---

## Execution Handoff

Plan 6 完成（5 Task）：完整 Docker 化 + NAS 部署手册 + 备份。**至此 Phase 1 MVP 全部 6 个 Plan 完成，系统可部署上线。**

---

## 全局路线图（Plan 1-6 总览）

| Plan | 内容 | 状态 |
|---|---|---|
| Plan 1 | 领域层（纯逻辑核心） | 已生成 |
| Plan 2 | 核心闭环（数据层+获取+比对+推送+调度） | 已生成 |
| Plan 3 | 用户体系 + FastAPI API | 已生成 |
| Plan 4 | 前端 Vue3（10 页） | 已生成 |
| Plan 5 | 扩展功能（统计/走势/提醒/钉钉企微/奖级DB化/运维） | 已生成 |
| Plan 6 | Docker 部署到 NAS | 已生成 |

**执行顺序：** Plan 1 → 2 → 3 → 4 → 5 → 6（依赖链）。每个 plan 独立可测试交付。可用 subagent-driven 逐 plan 执行。
