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
  '[cC]:/''Users'
  '/Users/''alfred'
  'OTC-''Fund'
  'tailf''898c8'
  # 弱默认密钥占位串（eng-review 外部声音发现 6：代码内嵌默认密钥也要拦）
  '[Cc]hange[-_]''me'
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
  --exclude-dir=dist --exclude-dir=static --exclude-dir=workflow-engine
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
  # fail-closed：match (rc=0) 与 error (rc≠1) 都置 FAIL；仅 no-match (rc=1) 放行
  if grep -RnIE "${EXCLUDE_DIRS[@]}" -- "$pat" .; then
    echo "FAIL: 命中泄露模式 /$pat/（见上方文件清单）" >&2
    FAIL=1
  else
    rc=$?
    if [ "$rc" -ne 1 ]; then
      echo "WARN: 门禁扫描出错（rc=${rc}，模式 /$pat/）——fail-closed" >&2
      FAIL=1
    fi
  fi
done

# F20：子模块机制已永久移除（plan-09/T3），不得回归
if [ -f .gitmodules ] || git -C "$ROOT" ls-files --error-unmatch .claude/workflow-engine >/dev/null 2>&1; then
  echo 'FAIL: 检测到 .gitmodules 或已跟踪的 .claude/workflow-engine（子模块已移除，不得回归）' >&2
  FAIL=1
fi

if [ "$GREP_ONLY" -eq 0 ]; then
  if ! command -v gitleaks >/dev/null 2>&1; then
    echo 'FAIL: 未安装 gitleaks。安装：brew install gitleaks；CI 场景用 --grep-only' >&2
    exit 1
  fi
  # 全历史「密钥形态」扫描（eng-review 外部声音发现 4：gitleaks 不匹配 IP/路径类
  # 内网标识——历史含内网 IP 属 spec §7/E3 已接受风险，本工具只担保「工作树无内网
  # 标识 + 全历史无密钥」，不宣称历史无内网标识）；发现即非零退出
  gitleaks git --redact "$ROOT" || FAIL=1
fi

if [ "$FAIL" -ne 0 ]; then
  echo '发布门禁：未通过' >&2
  exit 1
fi
echo '发布门禁：通过'
