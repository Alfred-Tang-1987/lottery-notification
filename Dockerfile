# syntax=docker/dockerfile:1
# 多阶段构建（spec §4.3 / plan 06 T9）
#
# 阶段 1：node 构建前端 → /static
# 阶段 2：python:3.12-slim 运行后端 + 拷贝前端产物 + Alembic 迁移 + uvicorn 启动

# ---------- 阶段 1：前端构建 ----------
FROM node:20-alpine AS web
WORKDIR /web
# 先 copy package 元信息，利用 docker layer cache（仅依赖变化才重装 npm 依赖）。
# web/package-lock.json 已提交（仓库根），COPY 进镜像 → 确定性构建。
# review-fix [important]：去掉 `|| npm install` 兜底——lockfile 损坏或与 package.json
# 不一致时 npm install 会静默改写 lockfile，构建产物与提交漂移，CI 与本地镜像不一致。
COPY web/package.json web/package-lock.json ./
# npm ci 严格按 lockfile，失败即构建失败（暴露 lockfile 漂移而非掩盖）。
RUN npm ci
# copy 源码后再 build（vite build outDir=../static → /web/../static = /static）
COPY web/ ./
RUN npm run build
# 产物落在 /static（绝对路径，便于下一阶段 COPY --from=web）

# ---------- 阶段 2：后端运行 ----------
FROM python:3.12-slim
# 时区（spec §4.3：全局时区 Asia/Shanghai，避免 tz-naive 静默偏移 8 小时）
ENV TZ=Asia/Shanghai
# 阻止 uv run 运行时重新同步依赖（构建时已 uv sync --frozen --no-dev）。
# silent-failure 陷阱：uv run 默认检查 lockfile 完整性，发现 dev 依赖未装会尝试
# 下载（含 ruff/pytest 等 dev 包），NAS 网络受限时 SSL 失败 → 容器无限重启。
ENV UV_NO_SYNC=1
RUN pip install --no-cache-dir uv
WORKDIR /app
# 先 copy 依赖锁文件，利用 layer cache（仅 lockfile 变化才重装 python 依赖）
COPY pyproject.toml uv.lock ./
# --frozen：严格按 uv.lock，不解析；--no-dev：生产不装测试/lint 依赖
RUN uv sync --frozen --no-dev
# 应用代码
COPY app/ ./app/
COPY alembic ./alembic
# 备份脚本（docs/deploy.md 备份/升级引用 /app/backup.sh；源文件 755 可执行位随 COPY 保留）
COPY backup.sh /app/backup.sh
COPY alembic.ini ./
# 前端构建产物（spec §12.3 / T8：main.py 在 STATIC_DIR 存在时挂载 SPA）
COPY --from=web /static ./static
# data 目录占位（runtime 由 compose 卷挂载覆盖；占位保证直接 docker run 也能写）
RUN mkdir -p /app/data /app/backups
EXPOSE 8280
# HEALTHCHECK（spec §4.3：提供 /health 端点）。5s 超时 + 30s 间隔 + 启动 10s 宽限。
# 用 python stdlib urllib，避免额外装 curl 增加镜像体积。
# review-fix [critical]：必须用 try/except 包裹 urlopen——DB down / 网络异常时 urlopen 抛
# URLError/ConnectionRefusedError，except 块显式 sys.exit(1) 让 Docker 标 unhealthy。
# /health 在 DB down 时返回 HTTP 503（app/main.py:health），status==200 判定 → unhealthy。
# silent-failure 设防（L-20260706T010500Z 自验：try/except 分支真能改变 exit code——
# 实测 503/连接拒绝 exit=1，200 exit=0，不会误报 healthy）。
# 实现备注：Dockerfile 的 \<newline> 续行会折叠后续行首空白，python 单行 try/except 语法
# 非法，故用 printf 把多行脚本 piped 到 `python -`，保持可读 + 正确缩进。
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD printf '%s\n' \
        'import os, ssl, sys, urllib.request' \
        'proto = "https" if os.environ.get("TLS_CERT_FILE") else "http"' \
        'url = f"{proto}://localhost:8280/health"' \
        'ctx = ssl._create_unverified_context() if proto == "https" else None' \
        'try:' \
        '    r = urllib.request.urlopen(url, timeout=4, context=ctx)' \
        '    sys.exit(0 if r.status == 200 else 1)' \
        'except Exception:' \
        '    sys.exit(1)' \
      | python -
# 启动：先 alembic 迁移（spec §4.3：Schema 用 Alembic 管理）再起 uvicorn。
# && 串行：迁移失败时 uvicorn 不启动（避免 schema drift 静默运行）。
# HTTPS：配置 TLS_CERT_FILE + TLS_KEY_FILE 环境变量时自动启用 SSL（局域网自签证书）。
# 不配则走 HTTP（保持开发/CI 兼容）。geolocation API 要求安全上下文（HTTPS/localhost），
# HTTP 局域网部署浏览器会拒绝定位。
# 注意：不能用 SSL_CERT_FILE 这个名字——它是 OpenSSL 标准 CA 信任库环境变量，
# 设成自签证书会导致 Python 全局只信任该证书，外部 API（mxnzp 等）的系统 CA 签名被拒绝。
CMD ["sh", "-c", "uv run alembic upgrade head && if [ -n \"$TLS_CERT_FILE\" ] && [ -n \"$TLS_KEY_FILE\" ]; then uv run uvicorn app.main:app --host 0.0.0.0 --port 8280 --ssl-certfile \"$TLS_CERT_FILE\" --ssl-keyfile \"$TLS_KEY_FILE\"; else uv run uvicorn app.main:app --host 0.0.0.0 --port 8280; fi"]
