# 部署运维

> 适用 Plan 06 T10 / spec §4.3。目标环境：家庭 NAS（FnOS，端口 8280）。

## 首次部署

### 1. NAS 上拉取代码（含子模块）

```bash
ssh <NAS>
mkdir -p /vol1/1000/Docker/lottery-notification
cd /vol1/1000/Docker/lottery-notification

# 主仓库（用 NAS 本地 gitea 或任一远端）
git clone http://192.168.8.168:8418/gitea/lottery-notification.git .
# 子模块必须初始化（workflow-engine 等）
git submodule update --init
```

> **注意**：`git submodule update --init` 不可省略——`.claude/workflow-engine` 是独立子模块，
> 缺失会导致后续 git 操作失败。

### 2. 传输 .env（在本机执行，.env 不进库）

```bash
# .env 在 .gitignore 中，git 不会传输，需单独 scp
scp .env <NAS>:/vol1/1000/Docker/lottery-notification/.env
```

参考 `.env.example` 填写，关键字段：

| 字段 | 要求 | 说明 |
|---|---|---|
| `JWT_SECRET` | ≥32 字符 | JWT 签名 |
| `CRYPTO_KEY_V1` | 44 字符 Fernet key | 渠道密钥加密；启动会冒烟校验，无效直接拒启 |
| `MXNZP_API_KEY` + `MXNZP_APP_SECRET` | 必填 | 主数据源（MXNZP 双参数鉴权） |
| `JUHE_API_KEY` | 可空 | 备数据源；空则降级单源（详见下方「单源模式」） |
| `SMTP_*` | 可空 | email 渠道；空则不启用 email（合规） |
| `ADMIN_BARK_KEY` | 强烈建议 | 全渠道失败兜底告警；启 email 时强制必填 |
| `COOKIE_SECURE` | 见下表 | http=false / https=true |
| `CORS_ORIGINS` | JSON 数组 | 填实际访问地址，否则跨域 403 |

**CORS_ORIGINS 与 COOKIE_SECURE 按访问方式配置**：

| 访问方式 | `COOKIE_SECURE` | `CORS_ORIGINS` 示例 |
|---|---|---|
| HTTP + 局域网 IP | `false` | `["http://192.168.8.168:8280"]` |
| HTTPS + 域名 | `true` | `["https://lottery.example.com"]` |

> 反向代理上线后，需同步改 `COOKIE_SECURE=true` 并把域名加入 `CORS_ORIGINS`，否则 cookie 不回传或跨域 403。

### 3. 预创建挂载目录

```bash
mkdir -p data backups && chmod 755 data backups
```

### 4. 构建并启动

```bash
docker compose up -d --build
```

首次构建约 3–5 分钟（node `npm ci` + vite build + python `uv sync`）。容器启动时 `CMD` 会自动跑 `uv run alembic upgrade head`（无需手动迁移）。

### 5. 健康检查

```bash
curl http://<NAS>:8280/health
# 期望: {"status":"ok","tz":"Asia/Shanghai","db":"ok"}
```

> **首次启动注意**：若 `JUHE_API_KEY` 为空（单源模式），startup backfill 会对 7 个彩种各跑 juhe 404 重试 6 次（约 35s/彩种，串行约 4-5 分钟）。期间 uvicorn lifespan 未完成，healthcheck 显示 `starting`/`unhealthy`。**backfill 跑完后自动转 `healthy`**，无需干预。

### 6. 创建首个 admin

```bash
docker compose exec app uv run python -m app.cli create-admin --username admin --password '<强密码>'
```

### 7. 访问

打开 `http://<NAS>:8280`，admin 登录 → 后台生成邀请码 → 邀请普通用户。

## 单源模式（JUHE_API_KEY 空）

双源交叉校验是 spec §7.2 金标准，但单源降级部署也合规（`verified=True single_source=True`）。需知悉的副作用：

1. **MXNZP 故障时无备源**——该期可能漏抓，需人工补抓或等下个 tick 重试。
2. **启动慢**——startup backfill 对 7 彩种各跑 juhe 404 重试 6 次，约 4-5 分钟（详见上方「健康检查」）。
3. **path_a_tick 每期耗时增加**——每期 juhe 重试约 35s × 彩种数。

补 `JUHE_API_KEY` 后上述副作用全部消失，且恢复双源交叉校验。生产建议补齐。

## 日常运维

- **备份**（spec §4.3：每日备份保留 30 天）：
  - 容器内手动：`docker compose exec app /app/backup.sh`
  - 宿主 cron 自动：`0 3 * * * docker compose exec app /app/backup.sh`（凌晨 3 点）
- **日志**：`docker compose logs -f`
- **升级**：`git pull && git submodule update --init && docker compose up -d --build`（Alembic 自动迁移；前端会重新构建）
- **健康**：`curl http://<NAS>:8280/health`（200=正常；503=DB/启动校验失败）

## 冒烟（端到端）

手动跑一期完整闭环（spec §13 Phase 1.0.13：抓取 ssq → 双源校验 → 比对）：

```bash
docker compose exec app uv run python -m app.cli ssq
```

预期输出（成功）：

```
fetch ssq: stored=True verified=True single_source=<True|False> not_drawn=False error=None
compared N pending
```

- `stored=True verified=True`：抓取成功并入库。
- `single_source=True`：单源模式（JUHE 空）；`single_source=False`：双源交叉校验通过。
- `not_drawn=True`：本期尚未开奖，正常情况。
- `error` 非 None：源故障 / 双源号码不一致（告警人工介入）。

## 关键约束（spec §4.3）

| 项 | 值 / 说明 |
|----|----------|
| 端口 | **8280**（已核实空闲，避开 NAS 占用） |
| restart | **`always`**（FnOS 关机会 `docker stop`，`unless-stopped` 不会自启——见 NAS 维护记录） |
| 时区 | **Asia/Shanghai**（全局 tz-aware，避免 tz-naive 静默偏移 8 小时） |
| SQLite | WAL + synchronous=NORMAL + busy_timeout=5000 + 单写连接 pool_size=1 |
| 备份保留 | 30 天 |
| 通知日志 | 保留 90 天（独立归档任务） |
| 密钥 | 从 `.env` 注入，**不进库不进日志**；启动时 `validate_startup()` 端到端冒烟验证 |

## 回滚

- 代码回滚：`git checkout <旧 commit> && docker compose up -d --build`
- 数据回滚：从 `backups/lottery-YYYYMMDD.db` 恢复 → `docker cp ... :/app/data/lottery.db`
  → 重启容器（`docker compose restart app`）
- Alembic 回滚（谨慎）：`docker compose exec app uv run alembic downgrade -1`
  （需先确认无破坏性 schema 变更）
