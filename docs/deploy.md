# 部署运维

> 适用 Plan 06 T10 / spec §4.3。目标环境：家庭 NAS（FnOS，端口 8280）。

## 首次部署

1. NAS 创建部署目录 `/vol1/1000/Docker/lottery-notification/`。
2. 同步项目代码到该目录，放置 `.env`（参考 `.env.example`，填 `JWT_SECRET` ≥ 32 字符、
   `CRYPTO_KEY_V1` 44 字符 Fernet key、数据源 `MXNZP_API_KEY`/`JUHE_API_KEY`、
   `SMTP_*`（若启用 email）、`ADMIN_BARK_KEY`）。
3. 预创建挂载目录并赋权：`mkdir -p data backups && chmod 755 data backups`。
4. 启动：`docker compose up -d --build`（首次会构建前端 + 后端镜像，约 3–5 分钟）。
5. Schema 迁移：容器启动时 `CMD` 会自动跑 `uv run alembic upgrade head`（无需手动）。
6. 创建首个 admin（bootstrap）：

   ```bash
   docker compose exec app uv run python -m app.cli create-admin --username admin --password '<强密码>'
   ```

7. 访问 `http://<NAS>:8280`，admin 登录 → 后台生成邀请码 → 邀请普通用户。

## 日常运维

- **备份**（spec §4.3：每日备份保留 30 天）：
  - 容器内手动：`docker compose exec app /app/backup.sh`
  - 宿主 cron 自动：`0 3 * * * docker compose exec app /app/backup.sh`（凌晨 3 点）
- **日志**：`docker compose logs -f`
- **升级**：`git pull && docker compose up -d --build`（Alembic 自动迁移；前端会重新构建）
- **健康**：`curl http://<NAS>:8280/health`（200=正常；503=DB/启动校验失败）

## 冒烟（端到端）

手动跑一期完整闭环（spec §13 Phase 1.0.13：抓取 ssq → 双源校验 → 比对）：

```bash
docker compose exec app uv run python -m app.cli ssq
```

预期输出（成功）：

```
fetch ssq: stored=True verified=True single_source=False not_drawn=False error=None
compared N pending
```

- `stored=True verified=True`：双源抓到且号码一致。
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
