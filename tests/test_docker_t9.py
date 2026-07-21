"""T9: Docker + docker-compose（spec §4.3）— infra-as-code 契约测试。

行为契约（plan T9 + spec §4.3）：
1. Dockerfile 多阶段构建：node 阶段 build 前端 → python:3.12-slim 运行后端。
2. EXPOSE 8280（spec §4.3：端口 8280 已核实空闲，避开 NAS 占用）。
3. HEALTHCHECK 调用 /health 端点（spec §4.3：Docker healthcheck 提供 /health）。
4. 时区 Asia/Shanghai（spec §4.3：全局时区 Asia/Shanghai）。
5. CMD 启动前先 alembic upgrade head（spec §4.3：Schema 迁移用 Alembic 管理）。
6. docker-compose：restart: always（spec §4.3：FnOS 关机坑，必须 always）。
7. docker-compose：端口映射 8280:8280。
8. docker-compose：挂载 data 卷（SQLite 持久化）+ backups 卷。
9. docker-compose：env_file: .env（密钥从 .env 注入，不进库不进日志）。
10. .dockerignore：排除 .git / node_modules / .venv / __pycache__ / data/ / backups/，
    避免构建上下文携带运行时状态或密钥污染镜像。

设计：Dockerfile/compose/.dockerignore 是声明式配置，无法跑单元测试；改为
**内容契约测试**——解析文件并断言关键键存在，防止误删/误改（例如把 restart: always
误改成 unless-stopped，会导致 FnOS 关机后服务无法自启）。
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

DOCKERFILE = ROOT / 'Dockerfile'
COMPOSE = ROOT / 'docker-compose.yml'
DOCKERIGNORE = ROOT / '.dockerignore'


def _read(path: Path) -> str:
    if not path.exists():
        pytest.fail(f'{path.name} 不存在（plan T9 要求创建）')
    return path.read_text(encoding='utf-8')


# ---------- Dockerfile 契约 ----------


def test_dockerfile_exists_and_uses_multi_stage_build():
    """RED：Dockerfile 必须存在，多阶段（node 构建 + python 运行）。"""
    content = _read(DOCKERFILE)
    # 多阶段：至少有 AS 别名 + FROM python:3.12-slim
    assert 'node:20-alpine' in content, '必须用 node 阶段构建前端（spec §12.3）'
    assert 'python:3.12-slim' in content, '后端基础镜像必须 python:3.12-slim'
    assert ' AS ' in content.upper() or ' as ' in content, '必须用多阶段（FROM ... AS ...）'


def test_dockerfile_exposes_8280():
    """RED：EXPOSE 8280（spec §4.3）。"""
    content = _read(DOCKERFILE)
    assert 'EXPOSE 8280' in content, '端口必须 8280（spec §4.3：已核实空闲）'


def test_dockerfile_has_healthcheck_hitting_health_endpoint():
    """RED：HEALTHCHECK 必须调用 /health（spec §4.3：提供 /health 端点）。"""
    content = _read(DOCKERFILE)
    assert 'HEALTHCHECK' in content, '必须有 HEALTHCHECK 指令（spec §4.3）'
    # healthcheck 必须打到 /health，且端口 8280
    assert '8280' in content and '/health' in content, (
        'HEALTHCHECK 必须访问 http://localhost:8280/health'
    )


def test_dockerfile_healthcheck_handles_urlopen_exceptions():
    """[critical review-fix]：HEALTHCHECK 必须用 try/except 包裹 urlopen 并显式 sys.exit(1)。

    静默失败陷阱：DB down 或网络异常时，urlopen 会抛 URLError / ConnectionRefusedError。
    若 HEALTHCHECK 没 except 显式 sys.exit(1)，依赖默认异常退出虽表面够用，但任何后续补丁
    （如改用 requests / 添加输出格式化）一旦吞掉异常，Docker 会误报 healthy。必须「显式
    sys.exit(1)」封锁 future regression——这是 silent-failure 主动设防纪律。
    """
    content = _read(DOCKERFILE)
    # 找到 HEALTHCHECK 块（HEALTHCHECK 行到下一空行）
    lines = content.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.strip().startswith('HEALTHCHECK'))
    end = next((i for i in range(start + 1, len(lines)) if not lines[i].strip()), len(lines))
    healthcheck_block = '\n'.join(lines[start:end])
    assert 'try' in healthcheck_block, (
        'HEALTHCHECK 必须用 try/except 包裹 urlopen（DB down 抛 URLError）'
    )
    assert 'except' in healthcheck_block, 'HEALTHCHECK 必须有 except 块捕获 urlopen 异常'
    assert 'sys.exit(1)' in healthcheck_block.replace(' ', ''), (
        'HEALTHCHECK except 块必须显式 sys.exit(1)（防 regression 把异常吞掉）'
    )


def test_dockerfile_npm_install_must_not_fallback_to_npm_ci_failure():
    """[important review-fix]：禁止 `npm ci || npm install` —— 掩盖 lockfile 损坏。

    静默失败陷阱：`npm ci || npm install` 在 lockfile 与 package.json 不一致时静默回退到
    install，install 会改写 lockfile；构建产物与提交不一致，CI 与本地镜像漂移，且真实故障
    （lockfile 损坏）被掩盖。既然 web/package-lock.json 已提交（仓库根存在），必须 `npm ci`。
    """
    content = _read(DOCKERFILE)
    # 找到所有 RUN 指令（npm ci / npm install 出现的地方）
    run_lines = [ln.strip() for ln in content.splitlines() if ln.strip().startswith('RUN ')]
    run_blob = '\n'.join(run_lines)
    # 禁止 || 兜底（任何 RUN 命令都不该用 ||，这会掩盖失败）
    assert '||' not in run_blob, (
        '禁止 `npm ci || npm install` 兜底——lockfile 损坏/与 package.json 不一致时 '
        'install 会静默改写 lockfile，构建产物与提交不一致'
    )
    # 必须 COPY package-lock.json（lockfile 已提交，确定性构建）
    assert 'package-lock.json' in content, (
        'web/package-lock.json 已提交，必须 COPY 进镜像（确定性构建）'
    )
    # 必须用 npm ci（lockfile 存在时唯一正确选择）
    assert 'npm ci' in content, 'lockfile 存在时必须 npm ci（确定性构建）'


def test_dockerfile_sets_timezone_asia_shanghai():
    """RED：TZ=Asia/Shanghai（spec §4.3：全局时区 Asia/Shanghai）。"""
    content = _read(DOCKERFILE)
    assert 'Asia/Shanghai' in content, '容器时区必须 Asia/Shanghai（spec §4.3）'


def test_dockerfile_runs_alembic_before_uvicorn():
    """RED：CMD 必须 alembic upgrade head 后再 uvicorn（spec §4.3：Alembic 管理迁移）。"""
    content = _read(DOCKERFILE)
    assert 'alembic upgrade head' in content, '启动前必须 alembic upgrade head'
    assert 'uvicorn app.main:app' in content, 'CMD 必须启动 uvicorn'
    # alembic 必须在 uvicorn 之前（&& 串行）
    assert content.index('alembic upgrade head') < content.index('uvicorn app.main:app'), (
        'alembic 必须先于 uvicorn 执行'
    )


def test_dockerfile_copies_static_from_web_stage():
    """RED：必须 COPY --from=<web stage> 前端构建产物到 ./static（spec §12.3 / T8）。"""
    content = _read(DOCKERFILE)
    # 多阶段产物拷贝：web 阶段 build 输出到 /static 或类似路径，再 COPY 到运行镜像
    assert '--from=' in content, '必须从 web 阶段 COPY 前端产物'
    assert 'static' in content, '前端产物目标必须是 static/（T8 STATIC_DIR 约定）'


# ---------- docker-compose.yml 契约 ----------


def test_compose_exists_and_port_8280():
    """RED：compose 端口映射 8280:8280（spec §4.3）。"""
    content = _read(COMPOSE)
    assert '8280:8280' in content, '端口映射必须 8280:8280（spec §4.3）'


def test_compose_restart_always():
    """RED：restart: always（spec §4.3：FnOS 关机坑，unless-stopped 不会自启）。

    静默失败陷阱：误写 unless-stopped / no / on-failure 不会立即崩，但 NAS 重启后
    服务静默消失——用户错过开奖通知。此测试就是该陷阱的护栏。
    """
    content = _read(COMPOSE)
    assert 'restart: always' in content, (
        'restart 策略必须 always（spec §4.3：FnOS 关机坑）——unless-stopped 不会自启'
    )


def test_compose_mounts_data_and_backups_volumes():
    """RED：挂载 data（SQLite 持久化）+ backups 卷（spec §4.3）。"""
    content = _read(COMPOSE)
    assert './data:/app/data' in content, '必须挂载 data 卷（SQLite 持久化）'
    assert './backups:/app/backups' in content, '必须挂载 backups 卷（spec §4.3 备份）'


def test_compose_uses_env_file():
    """RED：env_file: .env（CLAUDE.md 密钥约束：密钥从 .env 注入，不进库不进日志）。"""
    content = _read(COMPOSE)
    assert 'env_file' in content and '.env' in content, '必须 env_file: .env'


def test_compose_builds_from_local_context():
    """RED：build: .（本地构建，非拉远端镜像）。"""
    content = _read(COMPOSE)
    assert 'build:' in content, '必须从本地 Dockerfile 构建'


# ---------- .dockerignore 契约 ----------


def test_dockerignore_excludes_runtime_and_secret_pollution():
    """RED：.dockerignore 必须排除 .git / node_modules / .venv / __pycache__ / data / backups。

    静默失败陷阱：不排除这些会导致
    - 构建上下文巨大（node_modules 几百 MB）拖慢/超时构建；
    - 把本地 .env（含真实密钥）拷进镜像层（密钥泄露）；
    - 把本地 data/lottery.db（含用户数据）烤进镜像（数据漂移）。
    """
    content = _read(DOCKERIGNORE)
    required = ['.git', 'node_modules', '.venv', '__pycache__', 'data/', 'backups/']
    missing = [pat for pat in required if pat not in content]
    assert not missing, f'.dockerignore 缺少必需排除项：{missing}'


def test_dockerignore_excludes_env_files():
    """RED：.dockerignore 必须排除 .env（密钥不进镜像）。

    静默失败陷阱：构建时把 .env 烤进镜像，密钥被 docker layer cache 持久化，
    推到任意 registry 即泄露。CLAUDE.md「密钥不进库不进日志」的镜像层延伸。
    """
    content = _read(DOCKERIGNORE)
    assert '.env' in content, '.dockerignore 必须排除 .env（密钥不进镜像层）'
