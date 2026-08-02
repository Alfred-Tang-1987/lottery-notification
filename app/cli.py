"""CLI: 创建首个 admin（bootstrap）+ 手动触发一期闭环冒烟。

用法（spec §13 Phase 1.0.13）::

    # 推荐：交互式 prompt（密码不进 shell history / ps）
    uv run python -m app.cli create-admin --username admin
    # 或环境变量（适合非交互自动化：Docker entrypoint / ansible）
    ADMIN_PASSWORD=<p> uv run python -m app.cli create-admin --username admin
    # --password 显式（向后兼容；注意会进 shell history / ps，仅可信 shell 用）
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
import getpass
import logging
import os
import sys

from sqlmodel import Session

from app.adapters.juhe import JuheAdapter
from app.adapters.mxnzp import MxnzpAdapter
from app.api.security import hash_password
from app.config import get_settings
from app.db.session import get_engine
from app.models import User
from app.services.compare_service import CompareService
from app.services.fetch_service import FetchService

logger = logging.getLogger(__name__)

# 模块级 engine 引用：lifespan/main.py 与 CLI 共享同一 engine；测试通过 monkeypatch
# 替换 cli.engine 指向隔离 engine（db_engine fixture）。
engine = get_engine()


def resolve_password(argparse_ns) -> str:
    """解析 admin 密码，三档优先级（避免密码泄露到 shell history / ps）：

    1. ``--password`` 显式参数（向后兼容；仅可信 shell 用，会进 history/ps）
    2. ``ADMIN_PASSWORD`` 环境变量（适合非交互自动化：Docker entrypoint / ansible）
    3. 交互式 ``getpass`` prompt（推荐，密码不落盘）

    第三档在非交互终端（无 TTY，如某些 CI/容器场景）会抛 ``getpass.GetpassWarning``
    回退到明文输入或直接报错——此时应改用 env 变量。
    """
    if argparse_ns.password:
        return argparse_ns.password
    env_pw = os.environ.get('ADMIN_PASSWORD')
    if env_pw:
        return env_pw
    return getpass.getpass('Admin password: ')


def cmd_create_admin(argparse_ns) -> None:
    """创建首个 admin 用户（spec §13 Phase 1.0.13 bootstrap）。

    username 重复会从 SQLAlchemy 上抛 IntegrityError——调用方（CLI main）不捕获，
    异常非零退出，避免「静默成功」的 silent-failure 陷阱（运维误以为已建账号）。
    """
    password = resolve_password(argparse_ns)
    if not password:
        # getpass 空输入 / env 为空串：不静默创建空密码账号（silent-failure 防护）
        print('ERROR: password 不能为空（提供 --password / ADMIN_PASSWORD / 交互输入）', file=sys.stderr)
        sys.exit(2)
    with Session(engine) as s:
        u = User(
            username=argparse_ns.username,
            password_hash=hash_password(password),
            role='admin',
            invite_code='BOOTSTRAP',  # CLI bootstrap 标记（不走邀请码防爆破）
        )
        s.add(u)
        s.commit()  # 单事务单 commit（silent-failure：状态变更只 commit 一次）
    print(f'admin {argparse_ns.username} 创建成功')


def cmd_reset_password(argparse_ns) -> None:
    """重置已存在用户的密码（运维兜底：admin 忘密码且自助忘记密码流程不可用时）。

    与 ``create-admin`` 互补：create-admin 仅 bootstrap 首个 admin（重复 username 抛
    IntegrityError）；本命令改已存在用户的 ``password_hash``，不动其他字段。

    silent-failure 防护：
    - 用户不存在 -> sys.exit(1)：不静默成功（否则运维误以为已重置，实则无人能登）。
    - 空密码 -> sys.exit(2)：不静默写空密码哈希（bcrypt 接受空串，但空密码 = 无认证）。
    """
    from sqlmodel import select

    password = resolve_password(argparse_ns)
    if not password:
        print('ERROR: password 不能为空（提供 --password / ADMIN_PASSWORD / 交互输入）', file=sys.stderr)
        sys.exit(2)
    with Session(engine) as s:
        user = s.exec(select(User).where(User.username == argparse_ns.username)).first()
        if user is None:
            print(f'ERROR: 用户 {argparse_ns.username} 不存在', file=sys.stderr)
            sys.exit(1)
        user.password_hash = hash_password(password)
        s.add(user)
        s.commit()  # 单事务单 commit（silent-failure：状态变更只 commit 一次）
    print(f'{argparse_ns.username} 密码已重置')


def cmd_smoke(argparse_ns) -> None:
    """手动触发一期闭环冒烟（spec §13 Phase 1.0.13）。

    抓取 ssq → 双源交叉校验入库（或单源 grace 兜底）→ CompareService 处理 outbox。
    无 ticket 也照常抓取（只是比对认领 0 条）。
    """
    settings = get_settings()
    # 双源适配器（spec §4.3 / §7.2）：MXNZP 主 + 聚合数据备
    fetch = FetchService(
        MxnzpAdapter(settings.mxnzp_api_key, settings.mxnzp_app_secret),
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


def cmd_backfill_history(argparse_ns) -> None:
    """手动触发历史开奖回填：对所有启用彩种抓取最近 50 期历史开奖并入库。

    用于补充已有数据库的历史数据（自动 backfill 仅在 DB 为空时触发）。
    幂等：已存在的 (lottery_code, draw_no) 跳过，不重复入库。
    """
    import time

    from app.scheduler.backfill import _enabled_lotteries, _store_history_draws

    settings = get_settings()
    if not settings.mxnzp_api_key or not settings.mxnzp_app_secret:
        print('ERROR: mxnzp_api_key / mxnzp_app_secret 未配置', file=sys.stderr)
        sys.exit(1)
    adapter = MxnzpAdapter(settings.mxnzp_api_key, settings.mxnzp_app_secret)
    codes = [c for c, _ in _enabled_lotteries(engine)]
    for i, code in enumerate(codes):
        if i > 0:
            time.sleep(1.2)  # MXNZP 免费账号 QPS=1，请求间隔需 >1s
        try:
            draws = adapter.fetch_history(code, size=50)
            if not draws:
                print(f'{code}: 无历史数据')
                continue
            _store_history_draws(engine, draws, source_name='mxnzp')
            print(f'{code}: 回填 {len(draws)} 期')
        except Exception as exc:
            print(f'{code}: 失败 {exc}', file=sys.stderr)


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog='app.cli', description='运维 CLI（spec §13）')
    sub = p.add_subparsers(dest='cmd', required=True)

    ca = sub.add_parser('create-admin', help='创建首个 admin（bootstrap）')
    ca.add_argument('--username', required=True)
    ca.add_argument(
        '--password',
        default=None,
        help='admin 密码；省略则读 ADMIN_PASSWORD 环境变量，再省略则交互 prompt（推荐，'
        '避免密码进 shell history / ps）',
    )
    ca.set_defaults(func=cmd_create_admin)

    rp = sub.add_parser('reset-password', help='重置已存在用户的密码（运维兜底）')
    rp.add_argument('--username', required=True)
    rp.add_argument(
        '--password',
        default=None,
        help='新密码；省略则读 ADMIN_PASSWORD 环境变量，再省略则交互 prompt（推荐，'
        '避免密码进 shell history / ps）',
    )
    rp.set_defaults(func=cmd_reset_password)

    smoke = sub.add_parser('ssq', help='手动触发一期 ssq 端到端冒烟')
    smoke.set_defaults(func=cmd_smoke)

    bh = sub.add_parser('backfill-history', help='手动回填各彩种最近 50 期历史开奖')
    bh.set_defaults(func=cmd_backfill_history)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
    main()
