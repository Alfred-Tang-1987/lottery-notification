# 部署运维

> 适用 Plan 06 T10 / spec §4.3。目标环境：家庭 NAS（FnOS，端口 8280）。

## 首次部署

### 1. NAS 上拉取代码（含子模块）

```bash
ssh <NAS>
mkdir -p <NAS_DOCKER_DIR>
cd <NAS_DOCKER_DIR>

# 主仓库（用 NAS 本地 gitea 或任一远端）
git clone <GITEA_URL> .
# 子模块必须初始化（workflow-engine 等）
git submodule update --init
```

> **注意**：`git submodule update --init` 不可省略——`.claude/workflow-engine` 是独立子模块，
> 缺失会导致后续 git 操作失败。

### 2. 传输 .env（在本机执行，.env 不进库）

```bash
# .env 在 .gitignore 中，git 不会传输，需单独 scp
scp .env <NAS>:<NAS_DOCKER_DIR>/.env
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
| HTTP + 局域网 IP | `false` | `["http://<NAS_IP>:8280"]` |
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

> **单源模式启动**：若 `JUHE_API_KEY` 为空，`JuheAdapter` 会抛 `PermanentLookupError`，
> `FetchService._fetch_with_backoff` 识别此异常**不重试**（永久性错误重试无意义），
> 立即走单源兜底。启动速度与双源模式一致（仅 mxnzp 一次请求/彩种），不会阻塞 healthcheck。

### 6. 创建首个 admin

```bash
docker compose exec app uv run python -m app.cli create-admin --username admin --password '<强密码>'
```

### 7. 访问

打开 `http://<NAS>:8280`，admin 登录 → 后台生成邀请码 → 邀请普通用户。

## 单源模式（JUHE_API_KEY 空）

双源交叉校验是 spec §7.2 金标准，但单源降级部署也合规（`verified=True single_source=True`）。需知悉的副作用：

1. **MXNZP 故障时无备源**——该期可能漏抓，需人工补抓或等下个 tick 重试。
2. **启动速度与双源一致**——`JuheAdapter` key 空时抛 `PermanentLookupError`，`_fetch_with_backoff` 识别此异常不重试，立即走单源兜底（详见 [fetch_service.py](../app/services/fetch_service.py) 的 `PermanentLookupError` 处理）。

补 `JUHE_API_KEY` 后恢复双源交叉校验（核心安全网）。生产建议补齐。

## 日常运维

- **备份**（spec §4.3：每日备份保留 30 天）：
  - 容器内手动：`docker compose exec app /app/backup.sh`
  - 宿主 cron 自动：`0 3 * * * docker compose exec app /app/backup.sh`（凌晨 3 点）
- **日志**：`docker compose logs -f`
- **升级**：`git pull && git submodule update --init && docker compose up -d --build`（Alembic 自动迁移；前端会重新构建）
- **健康**：`curl http://<NAS>:8280/health`（200=正常；503=DB/启动校验失败）

## 密码重置

系统有**三条**重置路径，按场景选用，互不冲突（改密均同事务作废该用户活跃验证码，防止旧码把刚重置的密码改回）：

| 场景 | 路径 | 前置条件 |
|------|------|----------|
| 用户自助（忘密码） | 登录页「忘记密码」-> email 验证码 -> 重置 | 该用户已配 email 渠道 + 全局 SMTP |
| admin 后台干预 | 登录后台 -> 用户列表 -> 重置密码 | admin 能登录 |
| 运维兜底（admin 登不进 / 用户未配 email） | CLI `reset-password` | SSH + `docker exec` 权限 |

### 运维兜底：CLI 重置任意用户密码

当 admin 忘了密码（后台端点用不了）且该 admin 未配 email 渠道（自助流程发不出码）时，用 CLI：

```bash
# 交互输入新密码（推荐，不进 shell history）
docker compose exec app uv run python -m app.cli reset-password --username admin

# 或读环境变量
docker compose exec -e ADMIN_PASSWORD='<新密码>' app uv run python -m app.cli reset-password --username admin
```

- **不限 role**：可重置任意用户（admin 或普通用户）。该命令是受信任的运维工具（需 SSH + docker exec 权限方可执行），不承担访问控制 -- 那是 SSH/docker 权限的职责。
- **安全**：改密同事务作废该用户所有活跃验证码（与自助/admin 端点路径一致）；明文不入库（bcrypt 哈希）。
- **静默失败防护**：用户不存在 `exit 1`，空密码 `exit 2`，不静默成功。
- 与 `create-admin` 的区别：`create-admin` 仅用于 bootstrap 首个 admin（重复 username 报错）；`reset-password` 改已存在用户的密码。


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
