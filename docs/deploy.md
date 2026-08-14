# 部署运维

> 通用 Docker 单容器部署（默认端口 8280，可改）。快速开始见 [README](../README.md)；本文档覆盖生产化细节。

## 首次部署

### 1. 拉取代码

```bash
mkdir -p lottery-notification && cd lottery-notification
git clone <仓库地址> .
```

无需子模块、无需额外构建工具——`docker compose` 完成全部构建（前端 vite build + 后端 uv sync 均在镜像内）。

### 2. 配置 .env

```bash
./scripts/init-env.sh   # 生成 .env 并填入随机 JWT_SECRET / CRYPTO_KEY_V1
```

按需编辑 `.env`，关键字段：

| 字段 | 要求 | 说明 |
|---|---|---|
| `JWT_SECRET` | ≥32 字符 | JWT 签名（init-env.sh 已生成） |
| `CRYPTO_KEY_V1` | 44 字符 Fernet key | 渠道密钥加密；启动冒烟校验，无效拒启 |
| `MXNZP_API_KEY` + `MXNZP_APP_SECRET` | 必填 | 主数据源（双参数鉴权；申请见 README「数据源注册」） |
| `JUHE_API_KEY` | 可空 | 备数据源；空则降级单源（见下方「单源模式」） |
| `AMAP_API_KEY` | 可空 | 附近代销点 POI；空则示例数据 |
| `SMTP_*` | 可空 | email 渠道；空则不启用 email |
| `ADMIN_BARK_KEY` | 强烈建议 | 全渠道失败兜底告警；启 email 时强制必填 |
| `COOKIE_SECURE` | 见 README 访问模式表 | http+局域网=false / https=true |
| `CORS_ORIGINS` | JSON 数组 | 填实际访问地址，否则登录 403 |

### 3. 预创建挂载目录

```bash
mkdir -p data backups && chmod 755 data backups
```

### 4. 构建并启动

```bash
docker compose up -d --build
```

首次构建约 3–5 分钟（node `npm ci` + vite build + python `uv sync`）。容器启动时自动 `alembic upgrade head`（无需手动迁移）。

### 5. 健康检查

```bash
curl http://<主机>:8280/health
# 期望: {"status":"ok","tz":"Asia/Shanghai","db":"ok","data_sources":"dual"|"single_source"|"missing"}
```

`data_sources:"missing"` = 数据源 key 未配（服务可用但抓不到开奖），配齐 MXNZP 后 `docker compose up -d` 即可。

### 6. 创建首个 admin

```bash
docker compose exec app uv run python -m app.cli create-admin --username admin --password '<强密码>'
```

### 7. 访问

打开 `http://<主机>:8280`，admin 登录 → 后台生成邀请码 → 邀请普通用户。

## 单源模式（JUHE_API_KEY 空）

双源交叉校验是准确性金标准，单源降级部署也可运行（`verified=True single_source=True`）。副作用：

1. **MXNZP 故障时无备源**——该期可能漏抓，需人工补抓或等下个 tick 重试。
2. **启动速度与双源一致**——`JuheAdapter` key 空时抛 `PermanentLookupError`，抓取层识别此异常不重试，立即走单源兜底（见 `app/services/fetch_service.py`）。

补 `JUHE_API_KEY` 后恢复双源交叉校验（核心安全网）。生产建议补齐。

## 日常运维

- **备份**（每日，保留 30 天）：
  - 容器内手动：`docker compose exec app /app/backup.sh`
  - 宿主 cron 自动：`0 3 * * * docker compose exec app /app/backup.sh`（凌晨 3 点）
- **日志**：`docker compose logs -f`
- **升级**：`docker compose exec app /app/backup.sh && git pull && docker compose up -d --build`（Alembic 自动迁移；前端重新构建）
- **健康**：`curl http://<主机>:8280/health`（200=正常；503=DB/启动校验失败）

## 密码重置

三条路径，按场景选用，互不冲突（改密均同事务作废该用户活跃验证码，防止旧码把刚重置的密码改回）：

| 场景 | 路径 | 前置条件 |
|------|------|----------|
| 用户自助（忘密码） | 登录页「忘记密码」→ email 验证码 → 重置 | 该用户已配 email 渠道 + 全局 SMTP |
| admin 后台干预 | 登录后台 → 用户列表 → 重置密码 | admin 能登录 |
| 运维兜底（admin 登不进 / 用户未配 email） | CLI `reset-password` | SSH + `docker exec` 权限 |

### 运维兜底：CLI 重置任意用户密码

```bash
# 交互输入新密码（推荐，不进 shell history）
docker compose exec app uv run python -m app.cli reset-password --username admin

# 或读环境变量
docker compose exec -e ADMIN_PASSWORD='<新密码>' app uv run python -m app.cli reset-password --username admin
```

- 不限 role；用户不存在 `exit 1`，空密码 `exit 2`，不静默成功。
- 改密同事务作废该用户所有活跃验证码；明文不入库（bcrypt 哈希）。

## 冒烟（端到端）

```bash
docker compose exec app uv run python -m app.cli ssq
```

预期输出：

```text
fetch ssq: stored=True verified=True single_source=<True|False> not_drawn=False error=None
compared N pending
```

- `single_source=True`：单源模式（JUHE 空）；`False`：双源交叉校验通过。
- `not_drawn=True`：本期尚未开奖，正常。
- `error` 非 None：源故障 / 双源号码不一致（告警人工介入）。

## 关键约束

| 项 | 值 / 说明 |
|----|----------|
| 端口 | 默认 **8280**（改 `docker-compose.yml` 的 `ports` 左侧 + `CORS_ORIGINS`） |
| restart | **`always`**（NAS/宿主机重启后自启；改 `unless-stopped` 会导致重启后服务静默消失） |
| 时区 | **Asia/Shanghai**（全局 tz-aware） |
| SQLite | WAL + synchronous=NORMAL + busy_timeout=5000 + 单写连接 pool_size=1 |
| 备份保留 | 30 天 |
| 通知日志 | 保留 90 天（独立归档任务） |
| 密钥 | 从 `.env` 注入，**不进库不进日志**；启动时 `validate_startup()` 端到端冒烟验证 |

## 镜像部署拓扑（作者自用环境，供参考）

主源在 GitHub；自建 gitea 配置为 **pull-mirror**（周期性从 GitHub 拉取），NAS 从 gitea 镜像 clone——部署流程与直接从 GitHub clone 完全一致。

- gitea pull-mirror 有同步间隔（默认按 Gitea 配置，可 Mirror Settings 里手动 **Sync Now**）——紧急部署请直接从 GitHub clone 或先手动同步。
- gitea 镜像仓库视为**只读**：不要在镜像上直接 push（会被下次同步覆盖）；所有改动走 GitHub 主源。

## 回滚

- 代码回滚：`git checkout <旧 commit> && docker compose up -d --build`
- 数据回滚：从 `backups/lottery-YYYYMMDD.db` 恢复 → `docker cp ... :/app/data/lottery.db` → `docker compose restart app`
- Alembic 回滚（谨慎）：`docker compose exec app uv run alembic downgrade -1`（需先确认无破坏性 schema 变更）
