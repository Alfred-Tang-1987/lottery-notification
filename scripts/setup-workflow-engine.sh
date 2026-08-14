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
