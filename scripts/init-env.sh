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
