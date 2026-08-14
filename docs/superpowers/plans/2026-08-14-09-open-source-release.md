# Plan 09：开源发布（AGPL-3.0-only + 净化 + 门禁 + README/CI/社区文件）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把仓库安全地公开到 GitHub（AGPL-3.0-only）：先立泄露门禁，再净化内网标识、移除私有子模块、修复「裸路径首跑必崩」的 `.env.example`，补齐 README/LICENSE/CI/社区文件，首推后完成 gitea 镜像切换与外部 clone 冒烟。

**Architecture:** 门禁先行（T0 RED：当前仓库必 FAIL）→ 净化转绿（T1–T3 GREEN）→ 文档与社区文件（T4–T7、T10）→ 唯一的代码改动 T8（数据源 key 缺失告警，TDD）→ CI（T9）→ 首推（T11）与发布后验证（T12）。GitHub 为主源，gitea 改 pull-mirror，NAS 部署 clone URL 不变。

**Tech Stack:** bash（门禁/安装脚本）/ gitleaks / GitHub Actions（uv + ruff + lint-imports + pytest + npm）/ FastAPI（T8 唯一代码改动）/ AGPL-3.0-only。

**Spec:** `docs/superpowers/specs/2026-08-14-open-source-release-design.md`（含 autoplan CEO/Eng/DX 评审 25 项采纳修复，本 plan 全量落地：C2–C7、D1–D8、E1–E8）

**执行顺序（重要）：** T0–T10 在本 plan 内顺序执行；**T11（首推）与 T12（发布后验证）必须等 plan-10（彩种核对 B1）合入 main 后再跑**——README 的能力边界声明按 plan-10 完成后的真实状态书写，先把未修复代码推出去会造成公开过度宣称（spec C5）。

## Global Constraints

- **仓库内容即公开内容**：任何新提交不得含内网标识。词表以 `scripts/publish-check.sh` 的 `PATTERNS` 为唯一权威（含 `192\.168\.8\.`、`8\.167`、内网 SSH alias、内网路径段、router 端口、本机绝对路径、私有项目名、tailnet 指纹等；脚本内以字符串拆分书写防自匹配）。
- **本 plan 文件自身也受门禁约束**：plan 正文引用敏感词时只写拆分/占位形式（如 `<NAS_IP>`、`vol1''/`），绝不写字面值。
- **不改写 git 历史**：不重写 / 不 squash / 不 filter-repo（orchestrator 进度续跑依赖 `feat(plan-X/T-Y)` log 约定；历史邮箱泄露已决策「接受+文档化」，spec E3）。
- **Docker 部署零改动**：Dockerfile / docker-compose.yml 行为不变；端口默认 8280；NAS clone URL 不变。
- **密钥纪律**：`.env` 不进库不进日志；`.env.example` 一律空值 + 生成命令，绝不出现可运行的默认密钥（spec D5）。
- **commit 约定**：`feat(plan-09/T<n>): <描述>`（orchestrator 进度事实源）。
- **T8 代码改动走 TDD**：RED → GREEN → REFACTOR，遵守 CLAUDE.md 静默失败纪律；领域层零 IO（import-linter 强制）。
- 全程文案中文（README 首段英文合规声明除外）；代码注释遵循项目现有中文风格。

---

### Task 0: 发布门禁 `scripts/publish-check.sh`（先立门禁，RED）

**Files:**
- Create: `scripts/publish-check.sh`
- Test: `tests/test_publish_check.py`

**Interfaces:**
- Consumes: 无（首个任务）。
- Produces: `scripts/publish-check.sh [--grep-only]`——退出码 0=干净 / 1=有泄露或 gitleaks 缺失；`PUBLISH_CHECK_ROOT` env 可覆盖扫描根（测试用）。T3 会追加子模块缺席检查；T9 CI 与 T11 首推前都会调用。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_publish_check.py
"""发布门禁 scripts/publish-check.sh 测试（Plan 09 / T0）。

用 PUBLISH_CHECK_ROOT 指向 tmp 目录做 hermetic 测试，不依赖仓库当前净化状态。
注意：构造泄露样本时敏感词用字符串拼接，避免本测试文件自身命中门禁词表。
"""

import os
import stat
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / 'scripts' / 'publish-check.sh'

# 拆分书写，防自匹配（门禁词表含这些词的字面形）
_IP = '192' + '.168.8.1'
_SECRET = 'JWT_' + 'SECRET=real-value-123'


def _run(root: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, 'PUBLISH_CHECK_ROOT': str(root)}
    return subprocess.run(
        ['bash', str(SCRIPT), '--grep-only'],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


def test_script_exists_and_executable():
    assert SCRIPT.exists(), 'scripts/publish-check.sh 不存在'
    assert SCRIPT.stat().st_mode & stat.S_IXUSR, '脚本缺可执行位（chmod +x）'


def test_clean_tree_passes(tmp_path):
    (tmp_path / 'README.md').write_text('# 干净项目\n占位符 <NAS_IP> 不算泄露\n')
    r = _run(tmp_path)
    assert r.returncode == 0, f'干净目录应通过：{r.stdout}{r.stderr}'


def test_internal_ip_fails(tmp_path):
    (tmp_path / 'leak.md').write_text(f'部署到 {_IP} 的 NAS\n')
    r = _run(tmp_path)
    assert r.returncode == 1, '含内网 IP 必须 exit 1'
    assert 'leak.md' in r.stdout + r.stderr, '输出应指出泄露文件'


def test_secret_assignment_fails(tmp_path):
    # 密钥赋值形锚定行首（env 文件形态）——泄露样本须独占一行
    (tmp_path / 'config.md').write_text(f'{_SECRET}\n')
    r = _run(tmp_path)
    assert r.returncode == 1, '含密钥赋值形必须 exit 1'


def test_placeholder_nas_ip_passes(tmp_path):
    (tmp_path / 'ok.md').write_text('clone 源写成 <GITEA_URL>，端口 8280\n')
    r = _run(tmp_path)
    assert r.returncode == 0, f'占位符不应误报：{r.stdout}{r.stderr}'
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_publish_check.py -v`
Expected: FAIL（`scripts/publish-check.sh 不存在`）

- [ ] **Step 3: 实现门禁脚本**

```bash
#!/usr/bin/env bash
# scripts/publish-check.sh — 发布/推送泄露门禁（Plan 09 / T0；spec 2026-08-14 §3.1/§6、autoplan C3/C6/E2/E4）
#
# 两道闸：
#   1. 工作树 grep：内网标识词表 + 密钥赋值形正则（tracked 内容不得含真实值）
#   2. gitleaks：git 全历史密钥扫描（--grep-only 跳过；CI 由 gitleaks-action 承担历史扫描）
#
# 用法：
#   scripts/publish-check.sh              # 本地预推送全量（需 gitleaks：brew install gitleaks）
#   scripts/publish-check.sh --grep-only  # 仅工作树 grep（CI / pytest / 未装 gitleaks 时）
#
# 设计说明：词表在源码中以字符串拼接拆分书写（如 'vol1''/'），使脚本自身不命中词表——
# 门禁对全仓（含本脚本与 plan 文档）零豁免生效。
set -euo pipefail

GREP_ONLY=0
if [ "${1:-}" = '--grep-only' ]; then
  GREP_ONLY=1
fi

ROOT="${PUBLISH_CHECK_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
cd "$ROOT"

# 内网/私人标识词表（拆分书写防自匹配；autoplan E4 加宽版）
PATTERNS=(
  '192\.168\.8\.'
  '8\.167'
  ':40''10'
  'vol1''/'
  'fn-''nas'
  'home''lab'
  'C:/''Users'
  '/Users/''alfred'
  'OTC-''Fund'
  'tailf''898c8'
)

# 密钥赋值形（行首 KEY=非空值——env 文件形态；行中出现的代码片段如 startswith('KEY=')
# 不算泄露，锚定行首避免误报门禁脚本自身与测试工具代码）
SECRET_PATTERNS=(
  '^[[:space:]]*(export[[:space:]]+)?JWT_''SECRET=[^[:space:]]'
  '^[[:space:]]*(export[[:space:]]+)?CRYPTO_''KEY_V1=[^[:space:]]'
  '^[[:space:]]*(export[[:space:]]+)?MXNZP_''API_KEY=[^[:space:]]'
  '^[[:space:]]*(export[[:space:]]+)?MXNZP_''APP_SECRET=[^[:space:]]'
  '^[[:space:]]*(export[[:space:]]+)?JUHE_''API_KEY=[^[:space:]]'
  '^[[:space:]]*(export[[:space:]]+)?SMTP_''PASS=[^[:space:]]'
  '^[[:space:]]*(export[[:space:]]+)?ADMIN_''BARK_KEY=[^[:space:]]'
)

EXCLUDE_DIRS=(
  --exclude-dir=.git --exclude-dir=node_modules --exclude-dir=.venv
  --exclude-dir=dist --exclude-dir=static --exclude-dir=.claude
  --exclude-dir=runs --exclude-dir=.gstack --exclude-dir=.superpowers
  --exclude-dir=.audit --exclude-dir=.workflow --exclude-dir=data
  --exclude-dir=backups --exclude-dir=htmlcov --exclude-dir=.playwright-mcp
  --exclude-dir=certs
  # gitignored 本地文件天然豁免（不入库）：真实 .env、NAS 内部记录
  --exclude=.env --exclude='.env.*-backup' --exclude=.env.polluted-backup
  --exclude=deploy-nas-internal.md
  # 注意：.env.example 必须被扫描（C2 泄漏点就在它身上），勿排除
)

FAIL=0
for pat in "${PATTERNS[@]}" "${SECRET_PATTERNS[@]}"; do
  # -I 忽略二进制；-E 扩展正则；命中即列出文件:行
  if grep -RnIE "${EXCLUDE_DIRS[@]}" -- "$pat" . ; then
    echo "FAIL: 命中泄露模式 /$pat/（见上方文件清单）" >&2
    FAIL=1
  fi
done

if [ "$GREP_ONLY" -eq 0 ]; then
  if ! command -v gitleaks >/dev/null 2>&1; then
    echo 'FAIL: 未安装 gitleaks。安装：brew install gitleaks；CI 场景用 --grep-only' >&2
    exit 1
  fi
  # 全历史扫描（首推前必须跑）；发现即非零退出
  gitleaks git --redact "$ROOT" || FAIL=1
fi

if [ "$FAIL" -ne 0 ]; then
  echo '发布门禁：未通过' >&2
  exit 1
fi
echo '发布门禁：通过'
```

注意：`.claude` 整体排除（含将被移除的 workflow-engine 与公开可留的 run-plans.js——run-plans.js 已确认无密钥，C4；若有疑虑可改为仅排除 `.claude/workflow-engine`，但 run-plans.js 内含引擎实现细节，排除整目录更稳）。

写完后 `chmod +x scripts/publish-check.sh`。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_publish_check.py -v`
Expected: 5 passed

- [ ] **Step 5: 全仓跑门禁确认 RED（证明门禁有牙）**

Run: `bash scripts/publish-check.sh --grep-only`
Expected: **exit 1**，命中 `.env.example`、`CLAUDE.md`、`docs/deploy.md`、`docs/superpowers/**`、`web/src/pages/Dashboard.vue` 等——这些正是 T1/T2 要净化的内容。若 exit 0 说明词表漏了，回看 spec §3.4/E4 补词表。

- [ ] **Step 6: 提交**

```bash
git add scripts/publish-check.sh tests/test_publish_check.py
git commit -m "feat(plan-09/T0): 发布泄露门禁 publish-check.sh（内网标识词表 + 密钥赋值形 + gitleaks 历史扫描）"
```

---

### Task 1: `.env.example` 修复 + `scripts/init-env.sh`（D1/D5/C2，首跑崩溃 P1）

**Files:**
- Modify: `.env.example`（全量重写）
- Create: `scripts/init-env.sh`
- Test: `tests/test_init_env.py`

**Interfaces:**
- Consumes: `app/config.py` 的校验事实——`jwt_secret: str = Field(min_length=32)`、`crypto_key_v1: str = Field(alias='CRYPTO_KEY_V1', min_length=44)` + Fernet 构造校验（空值/默认值会在启动时以明确 ValidationError 拒启，不静默）。
- Produces: `scripts/init-env.sh`——由 `.env.example` 生成 `.env` 并填入随机密钥；`INIT_ENV_ROOT` env 覆盖目标根（测试用）。README（T5）与冒烟（T12）依赖此脚本。

背景（spec D1/D5）：现 `.env.example` 有三个发布阻断问题：① `CRYPTO_KEY_V1` 空值 → `cp .env.example .env && docker compose up` 启动即 ValidationError crash-loop；② `TLS_CERT_FILE`/`TLS_KEY_FILE` 已置值 → Dockerfile CMD 走 SSL → 证书缺失 FileNotFoundError crash-loop；③ `JWT_SECRET` 带**公开已知默认值**（change-me-to-...，51 字符过校验）→ 照抄即公知签名 key，admin 会话可伪造；④ 注释含真实内网 IP（C2）。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_init_env.py
"""init-env.sh 与 .env.example 发布形态测试（Plan 09 / T1）。

锁定 spec D1/D5/C2 修复：.env.example 密钥一律空值 + TLS 默认注释掉 + 无内网 IP；
init-env.sh 生成可启动的 .env（随机 JWT/Fernet key），且幂等护栏不覆盖已有 .env。
"""

import os
import subprocess
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / '.env.example'
SCRIPT = ROOT / 'scripts' / 'init-env.sh'


def _read_example() -> str:
    return EXAMPLE.read_text()


def test_example_secrets_are_empty():
    """JWT_SECRET / CRYPTO_KEY_V1 必须空值（公知默认 key 是发布阻断，spec D5）。"""
    for line in _read_example().splitlines():
        if line.startswith('JWT_SECRET='):
            assert line == 'JWT_SECRET=', f'JWT_SECRET 不得带默认值：{line!r}'
        if line.startswith('CRYPTO_KEY_V1='):
            assert line == 'CRYPTO_KEY_V1=', f'CRYPTO_KEY_V1 不得带默认值：{line!r}'


def test_example_tls_commented_out():
    """TLS 变量默认注释掉（否则裸路径启动走 SSL 找不到证书 crash-loop，spec D1）。"""
    for line in _read_example().splitlines():
        assert not line.startswith('TLS_CERT_FILE='), 'TLS_CERT_FILE 必须注释掉'
        assert not line.startswith('TLS_KEY_FILE='), 'TLS_KEY_FILE 必须注释掉'
    assert '# TLS_CERT_FILE=' in _read_example()
    assert '# TLS_KEY_FILE=' in _read_example()


def test_example_no_internal_ip():
    ip = '192' + '.168.8.'  # 拆分书写防门禁自匹配
    assert ip not in _read_example(), '.env.example 不得含内网 IP（spec C2）'


def _run_init(root: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, 'INIT_ENV_ROOT': str(root)}
    return subprocess.run(
        ['sh', str(SCRIPT)], capture_output=True, text=True, env=env, timeout=60,
    )


def test_init_env_generates_bootable_env(tmp_path):
    (tmp_path / '.env.example').write_text(EXAMPLE.read_text())
    r = _run_init(tmp_path)
    assert r.returncode == 0, f'{r.stdout}{r.stderr}'
    env_text = (tmp_path / '.env').read_text()
    jwt = next(l for l in env_text.splitlines() if l.startswith('JWT_SECRET='))
    crypto = next(l for l in env_text.splitlines() if l.startswith('CRYPTO_KEY_V1='))
    assert len(jwt.split('=', 1)[1]) >= 32, 'JWT_SECRET 须 ≥32 字符'
    key = crypto.split('=', 1)[1]
    assert len(key) == 44, 'CRYPTO_KEY_V1 须 44 字符'
    Fernet(key.encode())  # 构造不抛异常即真 Fernet key（与 config.py 校验一致）


def test_init_env_refuses_overwrite(tmp_path):
    (tmp_path / '.env.example').write_text(EXAMPLE.read_text())
    (tmp_path / '.env').write_text('JWT_SECRET=existing\n')
    r = _run_init(tmp_path)
    assert r.returncode == 1, '.env 已存在必须拒覆盖（防密钥被重建冲掉）'
    assert (tmp_path / '.env').read_text() == 'JWT_SECRET=existing\n'


def test_init_env_missing_tools_fails_loudly(tmp_path):
    """openssl/python3 缺失时非零退出且有明确提示（不静默生成半成品 .env）。

    PATH 置空后 command -v 检查必失败；用 /bin/sh 绝对路径绕过查找。
    """
    (tmp_path / '.env.example').write_text(EXAMPLE.read_text())
    env = {**os.environ, 'INIT_ENV_ROOT': str(tmp_path), 'PATH': '/nonexistent'}
    r = subprocess.run(
        ['/bin/sh', str(SCRIPT)], capture_output=True, text=True, env=env, timeout=60,
    )
    assert r.returncode != 0
    assert '缺少' in r.stderr
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_init_env.py -v`
Expected: FAIL（`.env.example` 现值不符 + `init-env.sh` 不存在）

- [ ] **Step 3: 重写 `.env.example`**

```bash
# 必填（无默认值——生成命令见下，或直接用 scripts/init-env.sh 一键生成 .env）
# JWT_SECRET: HMAC 签名密钥，最少 32 字符。生成：openssl rand -base64 48
# ⚠️ 必须自行生成，切勿照抄任何示例值——公知 key 意味着任何人可伪造 admin 会话。
JWT_SECRET=
# CRYPTO_KEY_V1: Fernet 密钥（44 字符 url-safe base64）。生成命令（python3 标准库即可）：
#   python3 -c "import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
# 启动时会校验可否构造 Fernet 实例，无效即拒绝启动。
CRYPTO_KEY_V1=
# 数据源（主源 MXNZP 鉴权需 app_id + app_secret 双参数；申请见 README「数据源注册」）
MXNZP_API_KEY=
MXNZP_APP_SECRET=
JUHE_API_KEY=
# 高德地图 Web 服务 API Key（用于仪表盘「附近代销点」POI 搜索；不配则回退示例数据）
# 申请：https://lbs.amap.com/dev/key/app
AMAP_API_KEY=
# SMTP（启用 email 渠道时必填）
SMTP_HOST=
SMTP_PORT=465
SMTP_ENCRYPTION=SSL/TLS
SMTP_USER=
SMTP_PASS=
SMTP_FROM=lottery@example.com
# 管理员 Bark 兜底告警（启用 email 时必填）
ADMIN_BARK_KEY=
# 运行环境
DATABASE_URL=sqlite:///./data/lottery.db
TZ=Asia/Shanghai
# Cookie secure 标志：HTTPS 部署必须 true；HTTP + 局域网 IP 部署必须改 false
# （否则浏览器不回传 cookie，登录死循环）。按 README「访问模式表」配置。
COOKIE_SECURE=true
# CORS 允许的 origin（JSON 列表）。填实际访问地址，如 ["http://<NAS_IP>:8280"]
# 或 ["https://lottery.example.com"]；与访问地址不符会登录 403。
CORS_ORIGINS=["http://localhost:5173"]
# HTTPS 部署（局域网自签证书，可选）：geolocation API 要求安全上下文，HTTP 下浏览器禁用定位。
# 默认注释掉 = 走 HTTP（快速开始路径）。启用步骤：
# 1. 生成自签证书（<NAS_IP> 替换为实际 IP）：
#    mkdir -p certs && openssl req -x509 -newkey rsa:2048 -nodes \
#      -keyout certs/key.pem -out certs/cert.pem -days 3650 \
#      -subj "/CN=<NAS_IP>" -addext "subjectAltName=IP:<NAS_IP>"
# 2. 取消下面两行注释 → uvicorn 启动时自动启用 SSL
# 3. COOKIE_SECURE 保持 true，CORS_ORIGINS 加 "https://<NAS_IP>:8280"
# 注意：变量名不能用 SSL_CERT_FILE（OpenSSL 标准 CA 信任库变量，会污染全局 HTTPS 验证）
# TLS_CERT_FILE=/app/certs/cert.pem
# TLS_KEY_FILE=/app/certs/key.pem
```

- [ ] **Step 4: 实现 `scripts/init-env.sh`**

```sh
#!/bin/sh
# scripts/init-env.sh — 由 .env.example 生成 .env 并填入随机密钥（Plan 09 / T1）
#
# 幂等护栏：.env 已存在则报错退出（绝不覆盖现有密钥）。
# INIT_ENV_ROOT 可覆盖目标根（测试用；默认脚本所在仓库根）。
set -eu

ROOT="${INIT_ENV_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
cd "$ROOT"

if [ -f .env ]; then
  echo '.env 已存在，拒绝覆盖。如需重建请先备份后手动删除。' >&2
  exit 1
fi
if [ ! -f .env.example ]; then
  echo '.env.example 不存在——请在仓库根目录运行。' >&2
  exit 1
fi
command -v openssl >/dev/null 2>&1 || { echo '缺少 openssl。' >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo '缺少 python3（仅用标准库生成 Fernet key）。' >&2; exit 1; }

cp .env.example .env

JWT=$(openssl rand -base64 48 | tr -d '\n')
CRYPTO=$(python3 -c "import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())")

# awk 整行替换，避开 sed -i 的 macOS/Linux 差异
awk -v v="$JWT"    '{if ($0 ~ /^JWT_SECRET=$/) print "JWT_SECRET=" v; else print}' .env > .env.tmp && mv .env.tmp .env
awk -v v="$CRYPTO" '{if ($0 ~ /^CRYPTO_KEY_V1=$/) print "CRYPTO_KEY_V1=" v; else print}' .env > .env.tmp && mv .env.tmp .env
chmod 600 .env

echo '已生成 .env（JWT_SECRET / CRYPTO_KEY_V1 已随机填入，权限 600）。'
echo '下一步：按需编辑数据源 key（MXNZP/JUHE/AMAP）与 SMTP；HTTP+局域网 IP 访问记得 COOKIE_SECURE=false 并改 CORS_ORIGINS。'
```

写完后 `chmod +x scripts/init-env.sh`。

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run pytest tests/test_init_env.py -v`
Expected: 6 passed

- [ ] **Step 6: 提交**

```bash
git add .env.example scripts/init-env.sh tests/test_init_env.py
git commit -m "feat(plan-09/T1): .env.example 空密钥 + TLS 默认注释 + init-env.sh 一键生成（修裸路径首跑 crash-loop）"
```

---

### Task 2: 文档净化（内网标识 → 占位符，门禁转绿 GREEN）

**Files:**
- Modify: `CLAUDE.md`、`docs/deploy.md`、`docs/superpowers/specs/2026-06-16-lottery-notification-design.md`、`docs/superpowers/specs/2026-07-09-run-plans-engine-extraction-design.md`、`docs/superpowers/specs/2026-07-23-floating-prize-lookup-design.md`、`docs/superpowers/specs/2026-08-02-forgot-password-design.md`、`docs/superpowers/specs/2026-08-14-open-source-release-design.md`、`docs/superpowers/plans/2026-06-21-06-webui-deploy.md`、`docs/superpowers/run-plans-engine-TODOS.md`、`web/src/pages/Dashboard.vue`

**Interfaces:**
- Consumes: T0 门禁（`scripts/publish-check.sh --grep-only` 列出全部命中点）。
- Produces: 全仓工作树无内网标识；门禁 grep 部分转绿。占位符约定（后续 README/deploy 复用）：`<NAS_IP>`、`<GITEA_URL>`、`<NAS_DOCKER_DIR>`、`<NAS_SSH_ALIAS>`、`<LOCAL_PATH>`。

- [ ] **Step 1: 列出全部命中点**

Run: `bash scripts/publish-check.sh --grep-only 2>&1 | tee /tmp/publish-check-hits.txt`
Expected: exit 1，输出全部命中文件与行。逐个处理，不遗漏。

- [ ] **Step 2: 逐文件替换**

替换映射（机械执行；语义重写类的 CLAUDE.md 结构调整在 T7，本任务只做净化）：

| 命中内容 | 替换为 |
|---|---|
| 内网 IP 字面值（grep 模式 `192\.168\.8\.` 命中的三段式地址） | `<NAS_IP>` |
| 内网 gitea 完整 URL（`<NAS_IP>` + 端口 + 路径的 http 地址） | `<GITEA_URL>` |
| NAS 部署绝对路径（vol1 开头的存储路径段） | `<NAS_DOCKER_DIR>` |
| NAS SSH alias | `<NAS_SSH_ALIAS>` |
| 本机绝对路径（`/Users/<用户名>/...` 或 Windows 用户目录路径） | 相对路径或 `<LOCAL_PATH>` |
| 模型 router 地址（模式 `8\.167` 与 `:40''10` 命中处） | 所在行整体删除（CLAUDE.md「本机 model 现状」一行；T7 会重写该节） |
| 私有项目名（OTC 开头的内部基金项目名） | 「内部项目」 |
| tailnet 指纹 / 主机名 | `<HOST>` |

特例处理：
- `docs/superpowers/specs/2026-08-14-open-source-release-design.md` 第 1 行 autoplan restore point 的本机绝对路径 → `<LOCAL_PATH>`；正文中作为「净化对象」被引用的内网 IP 示例一律改 `<NAS_IP>`（spec 是设计记录，语义不变）。
- `web/src/pages/Dashboard.vue` 仅注释命中 → 注释内 IP 改 `<NAS_IP>`，不改任何代码逻辑。
- `CLAUDE.md`：删「本机 model 现状」整行（含 router IP 与端口）；「NAS 部署约束」节的部署目录路径 → `<NAS_DOCKER_DIR>`；其余 T7 处理。
- `docs/deploy.md`：T6 会整篇重写，本任务只做最小净化（IP→`<NAS_IP>`、路径→`<NAS_DOCKER_DIR>`），允许被 T6 覆盖。

- [ ] **Step 3: 门禁 grep 部分转绿**

Run: `bash scripts/publish-check.sh --grep-only`
Expected: exit 0，输出「发布门禁：通过」。若仍 FAIL，回到 Step 1 补漏。

- [ ] **Step 4: 全量回归确认净化未碰代码逻辑**

Run: `uv run pytest -q && (cd web && npm test)`
Expected: 全绿（本任务只动 markdown / 注释 / 示例值；Dashboard.vue 仅注释）。

- [ ] **Step 5: 提交**

```bash
git add -A
git commit -m "feat(plan-09/T2): 文档净化——内网 IP/路径/项目名 → 占位符，发布门禁 grep 转绿"
```

---

### Task 3: 子模块移除 + `scripts/setup-workflow-engine.sh`（§3.2/E1/F4/F17/F20）

**Files:**
- Modify: `.gitignore`（增 `.claude/workflow-engine/`、`deploy-nas-internal.md`、`certs/`）
- Modify: `.dockerignore`（增 `.claude/`）
- Delete: `.gitmodules`（`git rm`）；`.claude/workflow-engine`（`git rm --cached`）
- Create: `scripts/setup-workflow-engine.sh`
- Create: `deploy-nas-internal.md`（**本地文件，gitignored，不提交**）
- Modify: `scripts/publish-check.sh`（增子模块缺席检查，F20）
- Test: `tests/test_setup_workflow_engine.py`、`tests/test_publish_check.py`（增一例）

**Interfaces:**
- Consumes: 无代码依赖；`.claude/workflows/run-plans.js`（派生副本）已跟踪在主仓库，orchestrator 日常执行不依赖子模块（spec §3.2 注）。
- Produces: `scripts/setup-workflow-engine.sh`——从 env `WORKFLOW_ENGINE_URL` clone 引擎到 `.claude/workflow-engine` 并跑 `sync.mjs`；未配置 env 时跳过并打印说明（**脚本内不得出现任何字面内网 URL/IP**，E1）。CLAUDE.md（T7）引用此脚本。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_setup_workflow_engine.py
"""setup-workflow-engine.sh 测试（Plan 09 / T3）。

env 化设计（autoplan E1）：URL 从 WORKFLOW_ENGINE_URL 读，脚本内零字面内网地址；
未配置时跳过并打印「内部开发工具」说明；子模块机制已永久移除（F20 门禁兜底）。
"""

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / 'scripts' / 'setup-workflow-engine.sh'


def test_script_has_no_literal_internal_url():
    text = SCRIPT.read_text()
    ip = '192' + '.168.8.'  # 拆分书写防门禁自匹配
    assert ip not in text, '脚本不得硬编码内网 IP（E1：否则过不了自家门禁）'
    assert ':84' + '18' not in text, '脚本不得硬编码内网 gitea 端口'


def test_skip_without_env_url():
    env = {k: v for k, v in os.environ.items() if k != 'WORKFLOW_ENGINE_URL'}
    r = subprocess.run(
        ['bash', str(SCRIPT)], capture_output=True, text=True, env=env, timeout=60,
    )
    assert r.returncode == 0, f'未配置 env 应跳过且成功：{r.stdout}{r.stderr}'
    assert '内部开发工具' in r.stdout + r.stderr


def test_gitmodules_removed_and_engine_untracked():
    assert not (ROOT / '.gitmodules').exists(), '.gitmodules 应已删除'
    r = subprocess.run(
        ['git', 'ls-files', '.claude/workflow-engine'],
        capture_output=True, text=True, cwd=ROOT, timeout=30,
    )
    assert r.stdout.strip() == '', '.claude/workflow-engine 不得被 git 跟踪'


def test_gitignore_covers_engine_and_local_deploy_doc():
    text = (ROOT / '.gitignore').read_text()
    assert '.claude/workflow-engine/' in text
    assert 'deploy-nas-internal.md' in text


def test_dockerignore_covers_claude_dir():
    text = (ROOT / '.dockerignore').read_text()
    assert '.claude' in text, 'E8-F17：.claude/ 不得进镜像构建上下文'
```

并给 `tests/test_publish_check.py` 追加（F20 子模块回归门禁）：

```python
def test_submodule_reappearance_fails(tmp_path):
    """F20：.gitmodules 再现必须 FAIL（子模块已永久移除，不得回归）。"""
    (tmp_path / '.gitmodules').write_text('[submodule "x"]\n\tpath = x\n')
    r = _run(tmp_path)
    assert r.returncode == 1
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_setup_workflow_engine.py tests/test_publish_check.py -v`
Expected: FAIL（脚本不存在 / .gitmodules 仍在 / 门禁无子模块检查）

- [ ] **Step 3: 移除子模块**

```bash
git submodule deinit -f .claude/workflow-engine || true
git rm --cached .claude/workflow-engine
git rm .gitmodules
rm -rf .git/modules/.claude/workflow-engine
```

验证：`git ls-files .claude/workflow-engine` 输出为空；`git status` 显示 `.gitmodules` 删除 + 子模块 gitlink 删除。

- [ ] **Step 4: 更新 .gitignore / .dockerignore**

`.gitignore` 末尾追加：

```gitignore
# workflow 引擎（私有内部工具，经 scripts/setup-workflow-engine.sh 按需 clone，不入库）
.claude/workflow-engine/

# NAS 专属运维记录（内网细节，本地保留，不入库）
deploy-nas-internal.md

# 自签证书（本地生成，HTTPS 部署用）
certs/
```

`.dockerignore` 在「版本控制」节追加：

```dockerignore
# 开发工具与代理配置（E8-F17：与运行时无关，不进构建上下文）
.claude/
```

- [ ] **Step 5: 实现 `scripts/setup-workflow-engine.sh`**

```bash
#!/usr/bin/env bash
# scripts/setup-workflow-engine.sh — 按需恢复内部 workflow 引擎（Plan 09 / T3；autoplan E1）
#
# run-plans-engine 是私有内部开发工具（仅 plan 编排用，运行/部署不需要）。
# 从 env WORKFLOW_ENGINE_URL 读取 clone 源（内网 gitea 地址，由开发者本地配置，
# 不写入本仓库）。未配置时跳过——外部贡献者无需此工具。
set -euo pipefail

cd "$(dirname "$0")/.."
TARGET=.claude/workflow-engine
URL="${WORKFLOW_ENGINE_URL:-}"

if [ -z "$URL" ]; then
  echo 'WORKFLOW_ENGINE_URL 未配置——跳过。'
  echo 'run-plans-engine 是内部开发工具，仅 plan 编排用；运行/部署/测试均不需要。'
  exit 0
fi

if [ -d "$TARGET/.git" ]; then
  echo "已存在，拉取最新：git -C $TARGET pull --ff-only"
  git -C "$TARGET" pull --ff-only
else
  git clone "$URL" "$TARGET"
fi

node "$TARGET/scripts/sync.mjs"
echo '完成：派生副本 .claude/workflows/run-plans.js 已同步（如需提交请单独 commit）。'
```

`chmod +x scripts/setup-workflow-engine.sh`。

- [ ] **Step 6: 门禁加子模块缺席检查（F20）**

`scripts/publish-check.sh` 在 gitleaks 段之前插入：

```bash
# F20：子模块机制已永久移除（plan-09/T3），不得回归
if [ -f .gitmodules ] || git -C "$ROOT" ls-files --error-unmatch .claude/workflow-engine >/dev/null 2>&1; then
  echo 'FAIL: 检测到 .gitmodules 或已跟踪的 .claude/workflow-engine（子模块已移除，不得回归）' >&2
  FAIL=1
fi
```

注意：`git ls-files --error-unmatch` 在 tmp 测试目录（非 git 仓库）会失败 → 视为未跟踪，不触发 FAIL，hermetic 测试不受影响。但 `test_submodule_reappearance_fails` 依赖 `.gitmodules` 检查——确认该检查在 `--grep-only` 模式也执行（它必须在 grep 段，与 gitleaks 无关）。

- [ ] **Step 7: 创建本地 `deploy-nas-internal.md`（不提交）**

把 CLAUDE.md「NAS 部署约束」节的 NAS 专属细节（真实部署目录、SSH alias 用法、模型 router 配置）移入此本地文件，内容模板：

```markdown
# NAS 部署内部记录（本地文件，gitignored，不入库）

> 公开部署文档见 docs/deploy.md；本文件记录仅作者 NAS 环境相关的真实值。

- 部署目录：<填真实 NAS 路径>
- SSH：<填 alias / 地址>
- gitea clone 源：<填内网 gitea URL>
- 模型 router（无 Claude 订阅时的本地路由）：<填地址>
```

验证：`git check-ignore deploy-nas-internal.md` 输出该文件名（确认被忽略）；`git status` 不显示它。

- [ ] **Step 8: 跑测试确认通过 + 全量回归**

Run: `uv run pytest tests/test_setup_workflow_engine.py tests/test_publish_check.py -q && uv run pytest -q`
Expected: 新增测试全过；全量回归绿。

- [ ] **Step 9: 门禁全量（含子模块检查）转绿**

Run: `bash scripts/publish-check.sh --grep-only`
Expected: exit 0。

- [ ] **Step 10: 提交**

```bash
git add -A
git commit -m "feat(plan-09/T3): 移除 workflow-engine 子模块（降级 gitignored 开发工具 + env 化 setup 脚本 + 门禁子模块缺席检查）"
```

---

### Task 4: LICENSE（AGPL-3.0-only）+ pyproject 元数据

**Files:**
- Create: `LICENSE`
- Modify: `pyproject.toml`（`[project]` 增 license 字段）
- Test: `tests/test_license.py`

**Interfaces:**
- Consumes: spec §2 决策（AGPL-3.0-only，2026-08-14 推翻 Apache-2.0）。
- Produces: `LICENSE` 全文；pyproject `license = "AGPL-3.0-only"`。README（T5）引用。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_license.py
"""LICENSE 与包元数据测试（Plan 09 / T4）——AGPL-3.0-only（spec §2 决策）。"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_license_file_is_agpl3():
    text = (ROOT / 'LICENSE').read_text()
    assert 'GNU AFFERO GENERAL PUBLIC LICENSE' in text
    assert 'Version 3' in text
    # 网络服务条款是 AGPL 区别于 GPL 的核心（spec §7 商用权益声明依赖它）
    assert 'Remote Network Interaction' in text


def test_pyproject_declares_license():
    text = (ROOT / 'pyproject.toml').read_text()
    assert 'AGPL-3.0-only' in text
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_license.py -v`
Expected: FAIL（LICENSE 不存在）

- [ ] **Step 3: 写入 LICENSE 全文**

```bash
curl -fsSL https://www.gnu.org/licenses/agpl-3.0.txt -o LICENSE
grep -c 'GNU AFFERO GENERAL PUBLIC LICENSE' LICENSE   # 期望 ≥1
wc -l LICENSE                                          # 期望约 660 行
```

- [ ] **Step 4: pyproject 加 license 字段**

`pyproject.toml` `[project]` 段 `description` 行后加：

```toml
license = "AGPL-3.0-only"
```

验证元数据可构建：

Run: `uv lock && uv build --sdist 2>&1 | tail -2`
Expected: 构建成功。若 hatchling 版本不支持 PEP 639 SPDX 字符串（报 license 格式错），改用 `license = {text = "AGPL-3.0-only"}` 后重跑 `uv lock && uv build --sdist`；二选一必须过，并在 commit message 记录采用哪种。

清理构建产物：`rm -rf dist/*.tar.gz`（`dist/` 已被 .gitignore 覆盖，确认 `git status` 干净即可）。

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run pytest tests/test_license.py -v`
Expected: 2 passed

- [ ] **Step 6: 提交**

```bash
git add LICENSE pyproject.toml uv.lock tests/test_license.py
git commit -m "feat(plan-09/T4): AGPL-3.0-only LICENSE + pyproject license 元数据"
```

---

### Task 5: README.md（项目门面，含英文合规声明 + 诚实能力边界）

**Files:**
- Create: `README.md`

**Interfaces:**
- Consumes: T1 init-env.sh；T4 LICENSE；`app/cli.py` 的 5 个子命令（create-admin / reset-password / ssq / backfill-history / backfill-draw-costs）；plan-10 完成后的能力边界真实状态（本任务按该终态书写——执行顺序见 plan 头部说明）。
- Produces: `README.md`。T12 冒烟的裸路径 = 本 README 快速开始原样执行（spec D8）。

内容要求来源：spec §3.4（README 清单）、C4（run-plans.js 公开说明）、C5（诚实能力边界）、C7（英文合规声明首段 + 差异化定位）、D2（访问模式表）、D3（数据源注册）、D7（CLI 表 + 升级指南 + 端口 8280 统一）。

- [ ] **Step 1: 写 README.md（全量内容如下）**

````markdown
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
````

- [ ] **Step 2: 自检 README 不触发门禁**

Run: `bash scripts/publish-check.sh --grep-only`
Expected: exit 0（README 只含占位符 `<NAS_IP>`，不含词表字面值）。

- [ ] **Step 3: 提交**

```bash
git add README.md
git commit -m "feat(plan-09/T5): README——英文合规声明首段 + 诚实能力边界 + 快速开始/访问模式表/数据源注册/CLI/升级指南"
```

---

### Task 6: `docs/deploy.md` 通用化重写

**Files:**
- Modify: `docs/deploy.md`（全量重写）

**Interfaces:**
- Consumes: T2 净化后的 deploy.md；T1 init-env.sh；T5 README（快速开始与访问模式表在 README，deploy.md 引用而非重复）。
- Produces: 通用 Docker 部署文档（不绑定 FnOS / 内网；保留 .env 字段表 / 单源模式 / 备份 / 冒烟 / 密码重置 / 回滚；含 gitea 镜像同步说明 F1/F2）。

- [ ] **Step 1: 全量重写 `docs/deploy.md`**

````markdown
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
````

- [ ] **Step 2: 门禁 + 回归**

Run: `bash scripts/publish-check.sh --grep-only && uv run pytest -q`
Expected: 门禁 exit 0；测试全绿（deploy.md 被 `tests/test_docker_t9.py` 之类的文档断言引用时同步修——若全量 pytest 因 deploy.md 文案断言失败，按失败信息更新对应断言）。

- [ ] **Step 3: 提交**

```bash
git add docs/deploy.md
git commit -m "feat(plan-09/T6): deploy.md 通用化——去 FnOS/内网绑定，补镜像拓扑与 data_sources 健康语义"
```

---

### Task 7: `CLAUDE.md` 更新

**Files:**
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: T3 setup 脚本；T6 deploy.md。
- Produces: 更新后的 CLAUDE.md（项目状态测试数、端口统一 8280、引擎更新流程、部署约束泛化）。

- [ ] **Step 1: 逐项修改 `CLAUDE.md`**

1. 「项目状态」段的测试数：跑 `uv run pytest -q 2>&1 | tail -1` 取实际数，把「554 tests green（+1 skipped）」替换为实际结果（当前约 659，以实测为准）。
2. 「常用命令」节：
   - `uv run uvicorn app.main:app --reload` → `uv run uvicorn app.main:app --reload --port 8280`（D7 端口统一；vite proxy 默认指向 8280，见 `web/vite.config.ts` 的 `VITE_API_ORIGIN` 缺省值）。
   - 「更新 run-plans-engine 子模块」命令块整段替换为：

     ```bash
     # 更新 run-plans-engine（内部开发工具，gitignored，不入库）
     export WORKFLOW_ENGINE_URL=<内网引擎仓库地址>   # 本机 shell 配置，勿写入仓库
     ./scripts/setup-workflow-engine.sh             # clone/更新引擎 + 同步派生副本
     git add .claude/workflows/run-plans.js
     git commit -m "chore(workflow): bump run-plans-engine"
     ```

3. 「⚠️ 续跑」与「§2.4 模型策略」之间的「本机 model 现状」句（含内网 router 地址）——T2 已删，本步确认无残留。
4. 「NAS 部署约束」节替换为：

   ```markdown
   ## 部署约束

   - 端口默认 **8280**（`docker-compose.yml` 可改，同步改 `CORS_ORIGINS`）
   - **`restart: always`**（宿主机重启后自启；`unless-stopped` 会静默消失——tests/test_docker_t9.py 是护栏）
   - 通用部署/运维流程见 `docs/deploy.md`；作者 NAS 专属细节在本地 `deploy-nas-internal.md`（gitignored，不入库）
   - 密钥从 `.env` 注入，不进库不进日志
   ```

5. 「文档导航」节末尾追加一行：
   `- `docs/reference/lottery-verification-2026-08-14.md` — **7 彩种「文档 vs 代码」核对报告（plan-10 产出）**`

- [ ] **Step 2: 门禁 + 回归**

Run: `bash scripts/publish-check.sh --grep-only && uv run pytest -q`
Expected: 全绿。

- [ ] **Step 3: 提交**

```bash
git add CLAUDE.md
git commit -m "feat(plan-09/T7): CLAUDE.md——测试数/端口 8280/引擎 setup 脚本流程/部署约束泛化"
```

---

### Task 8: 数据源 key 缺失启动告警 + `/health` `data_sources` 字段（D4，代码 TDD）

**Files:**
- Modify: `app/main.py`（`validate_startup` 增告警；`health` 增字段）
- Test: `tests/test_health.py`（增 4 例）

**Interfaces:**
- Consumes: `app.config.Settings`（`mxnzp_api_key` / `mxnzp_app_secret` / `juhe_api_key`，缺省 `''`）；`app.main.health` 现有返回 `{'status','tz','db'}` 与 503-on-db-down 语义。
- Produces: `health` 响应新增 `data_sources: 'dual' | 'single_source' | 'missing'`（**不改 HTTP 状态码语义**——仅 DB down 才 503；key 缺失不置 503，避免全新安装未配 key 时被 HEALTHCHECK 误判重启循环）；`validate_startup()` 在 key 全空时 `log.warning`（logger 名 `app.startup`）。

设计理由（写进代码注释）：key 缺失 ≠ 容器不健康（用户首次启动还没配 key），只告警不打 503；但日志必须显眼（silent-failure 纪律：缺 key 若静默，dashboard 永远空，用户无从得知）。

- [ ] **Step 1: 写失败测试（追加到 `tests/test_health.py`）**

```python
# —— Plan 09 / T8：数据源 key 缺失告警（spec D4）——


def _set_source_keys(monkeypatch, *, mxnzp_id='', mxnzp_secret='', juhe=''):
    reset_settings_cache()
    monkeypatch.setenv('JWT_SECRET', 'x' * 32)
    monkeypatch.setenv('CRYPTO_KEY_V1', Fernet.generate_key().decode())
    monkeypatch.setenv('MXNZP_API_KEY', mxnzp_id)
    monkeypatch.setenv('MXNZP_APP_SECRET', mxnzp_secret)
    monkeypatch.setenv('JUHE_API_KEY', juhe)


def test_health_data_sources_missing(monkeypatch, db_engine):
    """key 全空 → data_sources=missing，但 HTTP 仍 200（缺 key ≠ 容器不健康，
    否则首次安装未配 key 就被 HEALTHCHECK 重启循环）。"""
    _set_source_keys(monkeypatch)
    app.dependency_overrides[get_db_for_health] = lambda: db_engine
    try:
        r = TestClient(app).get('/health')
        assert r.status_code == 200
        assert r.json()['data_sources'] == 'missing'
    finally:
        app.dependency_overrides.clear()


def test_health_data_sources_single_source(monkeypatch, db_engine):
    _set_source_keys(monkeypatch, mxnzp_id='id', mxnzp_secret='secret')
    app.dependency_overrides[get_db_for_health] = lambda: db_engine
    try:
        r = TestClient(app).get('/health')
        assert r.json()['data_sources'] == 'single_source'
    finally:
        app.dependency_overrides.clear()


def test_health_data_sources_dual(monkeypatch, db_engine):
    _set_source_keys(monkeypatch, mxnzp_id='id', mxnzp_secret='secret', juhe='key')
    app.dependency_overrides[get_db_for_health] = lambda: db_engine
    try:
        r = TestClient(app).get('/health')
        assert r.json()['data_sources'] == 'dual'
    finally:
        app.dependency_overrides.clear()


def test_validate_startup_warns_when_all_source_keys_empty(monkeypatch, caplog):
    """silent-failure 纪律：数据源 key 全空必须 WARNING 显眼告警——否则 dashboard
    永远空、用户无从得知（D4）。"""
    _set_source_keys(monkeypatch)
    with caplog.at_level(logging.WARNING, logger='app.startup'):
        validate_startup()
    assert any(
        '数据源' in rec.message and rec.levelno >= logging.WARNING
        for rec in caplog.records
    ), 'key 全空应 WARNING 告警'
```

注意：`validate_startup` 会真实发 cwl/sporttery 冒烟请求（非门禁，失败仅 log）——与既有 `test_validate_startup_proves_crypto_key` 行为一致，可接受。

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_health.py -v -k data_sources or -k warns_when_all`
Expected: FAIL（`data_sources` 键不存在 / 无 WARNING）

- [ ] **Step 3: 实现**

`app/main.py` 的 `validate_startup()` 在 `settings.validate_email_bark_fallback()` 调用后追加：

```python
    # 数据源 key 检查（spec D4）：全空 → WARNING（服务能跑但永远抓不到开奖，
    # dashboard 空；静默等于「系统正常」假象）。仅 JUHE 空 → INFO（合法单源模式，
    # deploy.md 已文档化）。注意：不 raise——key 缺失不是启动错误，用户可能先起服务再配 key。
    has_mxnzp = bool(settings.mxnzp_api_key and settings.mxnzp_app_secret)
    if not has_mxnzp and not settings.juhe_api_key:
        log.warning(
            '数据源 key 全部为空（MXNZP_API_KEY/MXNZP_APP_SECRET/JUHE_API_KEY）'
            '——开奖抓取不可用，dashboard 将无数据。配置见 README「数据源注册」。'
        )
    elif not settings.juhe_api_key:
        log.info('JUHE_API_KEY 未配置——单源模式运行（MXNZP 故障时无备源交叉校验，建议补齐）。')
```

`health()` 的 `body` 改为：

```python
    settings = get_settings()
    has_mxnzp = bool(settings.mxnzp_api_key and settings.mxnzp_app_secret)
    has_juhe = bool(settings.juhe_api_key)
    data_sources = 'dual' if (has_mxnzp and has_juhe) else ('single_source' if has_mxnzp or has_juhe else 'missing')
    body = {
        'status': 'ok' if db_ok else 'degraded',
        'tz': settings.tz,
        'db': 'ok' if db_ok else 'down',
        # key 缺失不打 503：缺 key ≠ 容器不健康（首次安装未配 key 属正常中间态），
        # 否则 HEALTHCHECK 会把全新安装误判 unhealthy 重启循环。字段供人类/运维判读（D4）。
        'data_sources': data_sources,
    }
```

（原 `body` 中 `'tz': get_settings().tz` 复用上面的 `settings` 变量。）

- [ ] **Step 4: 跑测试确认通过 + 全量回归**

Run: `uv run pytest tests/test_health.py -v && uv run pytest -q`
Expected: 全绿。

- [ ] **Step 5: 提交**

```bash
git add app/main.py tests/test_health.py
git commit -m "feat(plan-09/T8): 数据源 key 缺失启动 WARNING + /health data_sources 字段（D4，缺 key 不再静默）"
```

---

### Task 9: GitHub Actions CI（`.github/workflows/ci.yml`）

**Files:**
- Create: `.github/workflows/ci.yml`
- Test: `tests/test_ci_workflow.py`

**Interfaces:**
- Consumes: T0 门禁脚本（`--grep-only`）；T3 子模块缺席检查；`pyproject.toml`（ruff / importlinter / pytest 配置）；`web/package.json`（`npm test` = vitest run、`npm run build`）。
- Produces: push/PR 触发的三 job CI（backend / frontend / leak-scan）。E5：后端跑**全量** `pytest`（迁移测试自含 SQLite，是免费 schema-drift 护栏，不排除）。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_ci_workflow.py
"""CI workflow 形态测试（Plan 09 / T9）。

纯文本断言（不引 yaml 依赖）：锁定 spec §3.5 + autoplan E2/E5/F11 要求的最小质量门禁。
"""

from pathlib import Path

WF = Path(__file__).resolve().parent.parent / '.github' / 'workflows' / 'ci.yml'


def test_ci_workflow_exists():
    assert WF.exists()


def test_ci_triggers_and_jobs():
    text = WF.read_text()
    assert 'pull_request' in text and 'push' in text
    for job in ('backend', 'frontend', 'leak-scan'):
        assert f'{job}:' in text, f'缺 job {job}'


def test_ci_backend_steps():
    text = WF.read_text()
    assert 'ruff check' in text
    assert 'lint-imports' in text
    # E5：全量 pytest（迁移测试自含 SQLite，收进 CI 而非排除）
    assert 'uv run pytest' in text and 'not migration' not in text


def test_ci_frontend_steps():
    text = WF.read_text()
    assert 'npm ci' in text
    assert 'npm test' in text      # F11：vitest 进 CI
    assert 'npm run build' in text


def test_ci_leak_scan():
    text = WF.read_text()
    assert 'gitleaks' in text
    assert 'publish-check.sh' in text
    assert 'fetch-depth: 0' in text, 'gitleaks 扫历史需完整 clone'
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_ci_workflow.py -v`
Expected: FAIL（文件不存在）

- [ ] **Step 3: 写 `.github/workflows/ci.yml`**

```yaml
# CI（Plan 09 / T9；spec §3.5 + autoplan E2/E5/F11）
# 公开质量门禁：lint + 架构守护 + 全量测试 + 前端构建 + 泄露扫描。
# NAS 部署走 gitea 镜像 + 本地 docker build，不依赖本 CI（GitHub 不可用不影响部署）。
name: ci

on:
  push:
    branches: [main]
  pull_request:

jobs:
  backend:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
        with:
          python-version: '3.12'
      - run: uv sync --extra dev
      - run: uv run ruff check .
      - run: uv run lint-imports
      # E5：全量 pytest——迁移测试自含 SQLite（无外部服务依赖），是 schema-drift 最佳护栏
      - run: uv run pytest -q

  frontend:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: web
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: 20
          cache: npm
          cache-dependency-path: web/package-lock.json
      - run: npm ci
      - run: npm test
      - run: npm run build

  leak-scan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0 # gitleaks 扫全历史（C3）
      - uses: gitleaks/gitleaks-action@v2
      - name: 内网标识 + 子模块回归门禁
        run: bash scripts/publish-check.sh --grep-only
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_ci_workflow.py -v`
Expected: 5 passed

- [ ] **Step 5: 本地模拟 CI 命令序列（首推前自检）**

Run: `uv run ruff check . && uv run lint-imports && uv run pytest -q && (cd web && npm ci && npm test && npm run build)`
Expected: 全绿。`npm run build` 会重建 `static/`（gitignored，不影响提交）。

- [ ] **Step 6: 提交**

```bash
git add .github/workflows/ci.yml tests/test_ci_workflow.py
git commit -m "feat(plan-09/T9): GitHub Actions CI——ruff/lint-imports/全量 pytest/前端 test+build/gitleaks+门禁"
```

---

### Task 10: 社区文件（CONTRIBUTING / SECURITY / issue 模板，D6）

**Files:**
- Create: `CONTRIBUTING.md`
- Create: `SECURITY.md`
- Create: `.github/ISSUE_TEMPLATE/bug_report.md`
- Create: `.github/ISSUE_TEMPLATE/feature_request.md`

**Interfaces:**
- Consumes: README（T5）的合规红线与开发命令。
- Produces: OSS 采纳最小社区文件集；合规红线的正式流程落点（D6）。

- [ ] **Step 1: 写四个文件**

`CONTRIBUTING.md`：

```markdown
# 贡献指南

感谢关注！贡献前请先读两条硬约束：

## 合规红线（不可协商）

本项目定位「事后核对 + 个人号码管理」。**以下方向的 Issue / PR 一律关闭**：号码预测、AI 选号推荐、冷热号排序推荐、必中宣传、购彩代购 / 支付接入、高频彩 / 私彩相关。走势图保持「综合分布 + 频次」形态，不排序、不标冷热、不推荐。

## 正确性纪律

本项目处理金钱相关信息，最高纪律是「**中奖永不静默漏通知**」：

- DB 写单事务一次 commit，禁止 split-commit
- 批量循环逐行故障用 SAVEPOINT 隔离（`session.begin_nested()`），不中断整批
- 金额一律用「分」（int）存储
- 领域层（`app/domain/`）零 IO——`uv run lint-imports` 强制

## 开发流程

1. Fork + 分支（`feat/...` / `fix/...`）
2. **TDD**：先写失败测试，再最小实现（80%+ 覆盖率是底线；新逻辑必须有测试）
3. 提交信息：`<type>: <描述>`（type ∈ feat/fix/refactor/docs/test/chore/perf/ci）
4. PR 前本地过门禁：
   ```bash
   uv run ruff check . && uv run lint-imports && uv run pytest -q
   cd web && npm test && npm run build
   bash scripts/publish-check.sh --grep-only
   ```
5. PR 描述写清：动机、方案、测试证据（输出贴图/文本）

## 彩种规则改动

奖级表 / 比对规则改动的权威依据是 `docs/reference/lottery-rules.md`（其来源为官方规则页）。**改代码前先改文档并附官方来源链接**；双色球（唯一生产验证彩种）的回归基线 `tests/domain/test_ssq_regression_baseline.py` 必须保持绿。

## 许可证

提交即表示你同意贡献内容按本项目的 **AGPL-3.0-only** 许可发布。
```

`SECURITY.md`：

```markdown
# 安全政策

## 报告漏洞

**请勿公开开 Issue 报告安全漏洞。** 请使用 GitHub 私有漏洞报告（Repository → Security → Advisories → Report a vulnerability），或按仓库 owner 主页公开联系方式私下报告。预期响应：72 小时内确认收到。

## 范围

重点关注：认证 / 邀请码机制、用户数据隔离（IDOR）、渠道密钥加密（Fernet）、密码重置流程、通知内容泄露。

## 自托管安全基线

- `.env` 密钥必须自行生成（`scripts/init-env.sh`），**切勿照抄任何示例值**
- HTTPS 部署保持 `COOKIE_SECURE=true`；HTTP 仅限受信局域网
- 历史提交者邮箱含早期开发环境的指纹信息（已知并接受，见 spec 2026-08-14 E3）；当前提交使用 GitHub noreply 邮箱
```

`.github/ISSUE_TEMPLATE/bug_report.md`：

```markdown
---
name: Bug 报告
about: 报告功能缺陷
labels: bug
---

**⚠️ 合规提醒**：本项目不接受号码预测 / 推荐类请求（见 README 合规红线）。

**现象**（期望 vs 实际）：

**复现步骤**：

**环境**（部署方式 / 版本 commit / 浏览器）：

**日志**（`docker compose logs` 相关段，**脱敏后再贴**——不得含 `.env` 密钥内容）：

**是否涉及中奖判定 / 通知漏发**（是/否；此类问题最高优先级）：
```

`.github/ISSUE_TEMPLATE/feature_request.md`：

```markdown
---
name: 功能建议
about: 提出新功能想法
labels: enhancement
---

**⚠️ 合规红线**：号码预测 / AI 推荐 / 冷热号排序 / 购彩代购类建议**一律关闭**，请勿提交。

**场景**（你在什么场景下遇到什么不便）：

**期望行为**：

**备选方案**（可选）：
```

- [ ] **Step 2: 门禁 + 回归**

Run: `bash scripts/publish-check.sh --grep-only && uv run pytest -q`
Expected: 全绿。

- [ ] **Step 3: 提交**

```bash
git add CONTRIBUTING.md SECURITY.md .github/ISSUE_TEMPLATE/
git commit -m "feat(plan-09/T10): 社区文件——CONTRIBUTING（合规红线流程）/SECURITY/issue 模板"
```

---

### Task 11: 首推 GitHub（主源切换）

**前置硬条件（缺一不跑）：plan-10 全部任务已合入 main 且全量测试绿。**（README 能力边界按 plan-10 终态书写，先推未修复代码 = 公开过度宣称，spec C5。）

**Files:** 无文件改动（ops 任务）。

- [ ] **Step 1: 预推送门禁（全量，含 gitleaks 历史扫描）**

```bash
command -v gitleaks || brew install gitleaks   # 本机首次需安装
bash scripts/publish-check.sh                  # 不带 --grep-only：全历史扫描
```

Expected: exit 0。gitleaks 若报 finding，停下来逐条核对（预期无真实密钥，spec F8 已核实历史仅含变量名；新发现按 security.md 流程处理，不强行推）。

- [ ] **Step 2: 本地全量验证（模拟 CI）**

Run: `uv run ruff check . && uv run lint-imports && uv run pytest -q && (cd web && npm test && npm run build) && (cd docs/superpowers/workflows && node --test 'tests/*.test.js')`
Expected: 全绿。

- [ ] **Step 3: 提交身份切 GitHub noreply（E3）**

```bash
gh api user --jq '.id,.login'   # 记下 id 与 login
git config user.email "<id>+<login>@users.noreply.github.com"
```

- [ ] **Step 4: remote 重组 + 建库 + 首推**

```bash
git remote rename origin gitea                      # 旧 gitea 保留为镜像 remote（过渡）
gh repo create lottery-notification --public --source=. --remote=origin \
  --description "兑奖了吗？——多用户中国彩票开奖自动核对与通知（AGPL-3.0-only；事后核对工具，无预测/无推荐/无代购）"
git push -u origin main
git push origin --tags || true
```

验证：`git remote -v` 显示 `origin` = GitHub、`gitea` = 内网；GitHub 网页可见仓库与完整历史。

- [ ] **Step 5: CI 首跑验证 + 分支保护（CI 自验，testing.md 纪律）**

1. `gh run list --limit 3` / `gh run watch`：三个 job 全绿。
2. CI 自验（防「假绿」）：推一个含 `assert False` 的临时分支 → 开 PR → 确认 CI 变红 → 关闭 PR 删分支。
3. 自验通过后，GitHub Settings → Branches → main 加保护：require status checks（backend / frontend / leak-scan）+ require PR。

- [ ] **Step 6: 提交记录（本任务无代码改动，用空提交标记里程碑）**

```bash
git commit --allow-empty -m "feat(plan-09/T11): 首推 GitHub 完成——origin 主源切换，gitea 过渡为镜像 remote"
git push origin main
```

---

### Task 12: 发布后验证（外部 clone 冒烟 + gitea 镜像 + NAS 零改动确认）

**Files:** 无文件改动（ops 验证任务）；如冒烟暴露问题，回本 plan 对应任务修复。

- [ ] **Step 1: 外部 clone 冒烟（spec §6 / D8 裸路径）**

在**干净临时目录**模拟外部用户（README 快速开始原样执行）：

```bash
TMP=$(mktemp -d)
git clone https://github.com/<owner>/lottery-notification.git "$TMP/repo"
cd "$TMP/repo"
test ! -f .gitmodules && echo 'OK: 无子模块'          # §3.2 验证点
test ! -d .claude/workflow-engine && echo 'OK: 无引擎目录'
./scripts/init-env.sh
docker compose up -d --build
sleep 15
curl -fsS http://localhost:8280/health
# 期望 200: {"status":"ok","tz":"Asia/Shanghai","db":"ok","data_sources":"missing"}
curl -fsS http://localhost:8280/ | head -5            # SPA 首页 200
docker compose exec app uv run python -m app.cli create-admin --username smoke --password 'Smoke#Test123'
# 浏览器或 curl 走一遍登录页加载（/api 不 500 即可）
docker compose down -v
rm -rf "$TMP"
```

Expected: 全部如注释所示。任何一步失败 = 发布阻断，修复后重跑。

- [ ] **Step 2: gitea 改 pull-mirror（手动 Gitea UI 步骤）**

Gitea 不支持把已有普通仓库原地转 pull-mirror，采用「删库重建为镜像」（clone URL 不变，NAS 无感）：

1. 确认 GitHub 主源已含全部历史（Step 1 已完成）。
2. Gitea 后台删除旧 `gitea/lottery-notification` 仓库。
3. Gitea → 「+」→ New Migration → GitHub → 填 `https://github.com/<owner>/lottery-notification`（公开仓库无需 token）→ 勾选 **This repository will be a mirror**（pull 模式）→ Owner 选 `gitea`、仓库名保持 `lottery-notification`。
4. Migration 完成后，Settings → Repository → Mirror Settings 确认同步间隔（建议 1h）并点 **Sync Now** 手动触发一次。
5. 验证镜像 URL 可 clone：`git clone <GITEA_URL>/lottery-notification.git /tmp/mirror-check && rm -rf /tmp/mirror-check`。

注意（F1/F2，已在 deploy.md「镜像部署拓扑」文档化）：镜像只读、有同步延迟；禁止直接向 gitea push。

- [ ] **Step 3: 镜像同步验证（F19）**

```bash
git commit --allow-empty -m "chore: mirror sync probe"
git push origin main
# 等一个同步间隔或 Gitea UI 手动 Sync Now
git ls-remote <GITEA_URL>/lottery-notification.git | grep main
```

Expected: gitea 的 main 指针 = GitHub 的 main 指针。

- [ ] **Step 4: NAS 部署零改动确认**

```bash
ssh <NAS_SSH_ALIAS>
cd <NAS_DOCKER_DIR>          # 原部署目录（本地 deploy-nas-internal.md 里有真实值）
git remote -v                # 确认 clone URL 未变（gitea 镜像地址）
git pull                     # 应拉到 mirror sync probe 提交
docker compose up -d --build
curl -fsS http://localhost:8280/health
```

Expected: 200 ok；`data_sources` 为 `dual` 或 `single_source`（NAS 的 `.env` 有真实 key）。

- [ ] **Step 5: 收尾提交**

```bash
git commit --allow-empty -m "feat(plan-09/T12): 发布后验证完成——外部 clone 冒烟 / gitea pull-mirror / NAS 零改动确认"
git push origin main
```

---

## Self-Review 记录（plan 落盘前已执行）

- **Spec 覆盖**：LICENSE（§5→T4）、README（§5/C4/C5/C7/D2/D3/D7→T5）、净化清单（§3.4/C2/E4→T1/T2）、子模块（§3.2/E1/F4→T3）、gitea 镜像 + remote（§3.3/F1/F2/F19→T11/T12）、CI（§3.5/E2/E5/F11→T9）、发布门禁（§5/C3/C6/E2/F20→T0/T3/T11）、.env.example（§5/D1/D5→T1）、社区文件（§5/D6→T10）、启动告警（D4→T8）、历史邮箱（E3→T10 SECURITY + T11 Step 3）、冒烟（§6/D8/F18→T12）、.dockerignore（F17→T3）、端口统一（D7→T7）。
- **类型一致性**：`data_sources` 三态在 T8 实现 / T5 README / T6 deploy.md / T12 冒烟断言一致；`IMPLEMENTED_PLAY_TYPES` 属 plan-10，本 plan 不引用。
- **门禁自匹配**：本 plan 正文未含任何词表字面值（全部拆分或占位符书写）。
