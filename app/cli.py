"""CLI: 创建首个 admin（bootstrap）+ 手动触发一期闭环冒烟。

用法（spec §13 Phase 1.0.13）::

    uv run python -m app.cli create-admin --username admin --password <p>
    uv run python -m app.cli ssq

设计：
- ``create-admin`` 在 bootstrap 场景下使用：第一个管理员必须在无 admin 后台的初始状态下
  创建（鸡蛋问题）。重复 username 会抛 IntegrityError——不静默吞（防止误以为创建成功）。
- ``ssq`` 手动触发一期端到端冒烟：抓取 → 双源校验入库 → 比对 outbox。打印结构化结果
  供运维肉眼判断（不能 silent-success）。
- engine 取 ``app.db.session.get_engine()`` 全局单例：与 lifespan / API 共用同一 engine
  （spec §4.3：APScheduler SQLAlchemyJobStore 共享同一 engine，让 WAL/busy_timeout 生效）。
"""

import argparse
import logging
import sys

from app.adapters.juhe import JuheAdapter
from app.adapters.mxnzp import MxnzpAdapter
from app.api.security import hash_password
from app.config import get_settings
from app.db.session import get_engine
from app.models import User
from app.services.compare_service import CompareService
from app.services.fetch_service import FetchService
from sqlmodel import Session

logger = logging.getLogger(__name__)

# 模块级 engine 引用：lifespan/main.py 与 CLI 共享同一 engine；测试通过 monkeypatch
# 替换 cli.engine 指向隔离 engine（db_engine fixture）。
engine = get_engine()


def cmd_create_admin(argparse_ns) -> None:
    """创建首个 admin 用户（spec §13 Phase 1.0.13 bootstrap）。

    username 重复会从 SQLAlchemy 上抛 IntegrityError——调用方（CLI main）不捕获，
    异常非零退出，避免「静默成功」的 silent-failure 陷阱（运维误以为已建账号）。
    """
    with Session(engine) as s:
        u = User(
            username=argparse_ns.username,
            password_hash=hash_password(argparse_ns.password),
            role='admin',
            invite_code='BOOTSTRAP',  # CLI bootstrap 标记（不走邀请码防爆破）
        )
        s.add(u)
        s.commit()  # 单事务单 commit（silent-failure：状态变更只 commit 一次）
    print(f'admin {argparse_ns.username} 创建成功')


def cmd_smoke(argparse_ns) -> None:
    """手动触发一期闭环冒烟（spec §13 Phase 1.0.13）。

    抓取 ssq → 双源交叉校验入库（或单源 grace 兜底）→ CompareService 处理 outbox。
    无 ticket 也照常抓取（只是比对认领 0 条）。
    """
    settings = get_settings()
    # 双源适配器（spec §4.3 / §7.2）：MXNZP 主 + 聚合数据备
    fetch = FetchService(
        MxnzpAdapter(settings.mxnzp_api_key),
        JuheAdapter(settings.juhe_api_key),
        engine,
    )
    r = fetch.fetch_and_store('ssq')
    # 结构化摘要行：运维肉眼判断不能 silent-success（L-20260706T010500Z 自验：
    # 这些 print 真的能改变 stdout 内容——测试断言 fetch/compared 子串存在）
    print(
        f'fetch ssq: stored={r.stored} verified={r.verified} '
        f'single_source={r.single_source} not_drawn={r.not_drawn} error={r.error}'
    )
    # fetch 报错时非零退出：deploy.md 已记录 cron 模式，exit-0-on-error 会让
    # 自动化包装此冒烟时把故障隐藏为 success（silent-success 陷阱）。
    # L-20260706T010500Z: 必须真的 sys.exit(1)——单纯打印后 return 是 no-op guard。
    if r.error:
        print(f'ERROR: {r.error}', file=sys.stderr)
        sys.exit(1)
    n = CompareService(engine).process_pending()
    print(f'compared {n} pending')


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog='app.cli', description='运维 CLI（spec §13）')
    sub = p.add_subparsers(dest='cmd', required=True)

    ca = sub.add_parser('create-admin', help='创建首个 admin（bootstrap）')
    ca.add_argument('--username', required=True)
    ca.add_argument('--password', required=True)
    ca.set_defaults(func=cmd_create_admin)

    smoke = sub.add_parser('ssq', help='手动触发一期 ssq 端到端冒烟')
    smoke.set_defaults(func=cmd_smoke)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
    main()
