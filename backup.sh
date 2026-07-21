#!/bin/sh
# 每日 SQLite 备份（spec §4.3）。建议 cron: 0 3 * * * /app/backup.sh
#
# 设计要点（spec §4.3）：
# - 用 SQLite backup API（python sqlite3 模块的 .backup()）而非 sqlite3 CLI——
#   python:3.12-slim 镜像不自带 sqlite3 命令行工具，CLI 方式会失败。
# - 保留 30 天：find -mtime +30 -delete（spec §4.3：保留 30 天）。
# - set -e：任何步骤失败立即非零退出——cron 静默 success 是 silent-failure 陷阱。
# - DB 路径从 DATABASE_URL 派生（与 app/config.py default 一致）。
#
# 三重 guard 防 silent-success（review round 1 fixes）：
# (1) DATABASE_URL 为空/未设置时拒绝备份——否则 sqlite3.connect('') 连 in-memory
#     空 DB，.backup() 拷空 schema，cron 报 success 而真实 DB 从未备份。
# (2) 源 DB 文件不存在或为空字节时拒绝——sqlite3.connect('/nonexistent/x.db')
#     在 Python 里是**创建新空文件**而非失败；cron CWD 错位时会连错路径静默成功。
# (3) 备份产物必须非零字节——若 .backup() 连到错误（空）DB，产物 0 字节，仍报 success。
# 此外：DB 须解析为绝对路径，否则 cron 宿主 CWD != 容器 CWD 时连错路径。
set -e

DB="${DATABASE_URL#sqlite:///./}"
DB="${DB#sqlite:///}"

# (1) 空 DATABASE_URL guard：否则连 in-memory 空 DB 静默成功。
# L-20260706T010500Z: 不改变行为的「配置/筛选」是 silent-success——这里必须
# 真的拒绝执行（exit 2），而非只打一行 warning 后继续。
if [ -z "$DB" ]; then
    echo 'DATABASE_URL empty/unset, refusing to back up empty DB' >&2
    exit 2
fi

# (2) 解析为绝对路径——否则 cron 宿主 CWD=/ 时 ./data/lottery.db 解析为
# /data/lottery.db（不存在），sqlite3.connect 不报错反创建空文件。
if [ ! -f "$DB" ]; then
    # DB 不存在时无法 cd 到其目录解析绝对路径——先尝试按字面值检查
    ABS_DB="$DB"
else
    ABS_DB=$(cd "$(dirname "$DB")" && pwd)/$(basename "$DB")
fi
DB="$ABS_DB"

# (2) 源 DB 文件存在性 + 非空 guard：sqlite3.connect 对不存在路径会创建空文件
# 而非失败——必须显式断言，否则路径解析错时静默拷空 DB。
if [ ! -f "$DB" ] || [ ! -s "$DB" ]; then
    echo "DB $DB missing or empty, refusing to back up" >&2
    exit 2
fi

TS=$(date +%Y%m%d)
BACKUP_DIR="/app/backups"
mkdir -p "$BACKUP_DIR"
BACKUP_FILE="$BACKUP_DIR/lottery-${TS}.db"

# backup API 原子拷贝（含 WAL checkpoint），不会读到撕裂页。
python -c "import sqlite3; src=sqlite3.connect('$DB'); dst=sqlite3.connect('$BACKUP_FILE'); src.backup(dst); dst.close(); src.close()"

# (3) 备份产物非零字节 guard：连错（空）DB 时 .backup() 产生 0 字节文件却报 success。
if [ ! -s "$BACKUP_FILE" ]; then
    echo "backup produced empty file: $BACKUP_FILE" >&2
    exit 2
fi

# 保留 30 天（spec §4.3）。
find "$BACKUP_DIR" -name "lottery-*.db" -mtime +30 -delete
