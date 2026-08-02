"""T10: CLI + backup.sh + deploy.md 契约测试（spec §4.3 + §13 Phase 1.0.13）。

行为契约（plan 06 T10 + spec §4.3 / §13）：
1. ``python -m app.cli create-admin --username X --password Y`` 创建 role=admin 的 User，
   密码经 hash_password 哈希（明文不入库）；重复用户名须失败（unique 约束）。
2. ``python -m app.cli ssq`` 端到端跑一期冒烟：调用 FetchService.fetch_and_store('ssq')
   + CompareService.process_pending()，并打印结构化结果（fetch + compare 行）。
3. ``backup.sh`` 存在、可执行，使用 SQLite backup API（spec §4.3：进程内备份），
   保留 30 天（find -mtime +30 -delete）。
4. ``docs/deploy.md`` 部署文档存在，含首次部署/admin bootstrap/备份/cron/冒烟说明。

设计：
- ``create-admin`` 用真实 SQLite engine（db_engine fixture）+ monkeypatch ``app.cli.engine``
  指向测试 engine，断言 User 落库 + role=admin + password_hash != password 明文。
- ``ssq`` 用 monkeypatch 替换 ``FetchService`` 和 ``CompareService`` 为 MagicMock，避免
  真实网络调用（CI 无密钥/外网，会 false negative）。断言调用次数 + 参数 + stdout 含
  fetch/compare 摘要行（防止「打印被静默吞」silent-success）。
- ``backup.sh``/``deploy.md`` 是声明式文件，用内容契约测试（同 T9 Dockerfile 测试风格）。
"""

from unittest.mock import MagicMock

import pytest
from sqlmodel import Session, select

from app.models import User

ROOT = __import__('pathlib').Path(__file__).resolve().parent.parent
BACKUP_SH = ROOT / 'backup.sh'
DEPLOY_MD = ROOT / 'docs' / 'deploy.md'


# ---------------------------------------------------------------- create-admin
def test_create_admin_persists_admin_user(db_engine, monkeypatch, capsys):
    """create-admin 写入 User(role=admin)，password 经哈希。"""
    import app.cli as cli_mod

    # CLI 默认从 app.db.session 取 engine；测试指向隔离 engine。
    monkeypatch.setattr(cli_mod, 'engine', db_engine)

    cli_mod.cmd_create_admin(
        argparse_ns=MagicMock(username='admin', password='s3cret-pass')
    )

    with Session(db_engine) as s:
        users = list(s.exec(select(User)).all())
    assert len(users) == 1
    u = users[0]
    assert u.username == 'admin'
    assert u.role == 'admin'  # bootstrap 用户必须是 admin（spec §13 Phase1.0.13）
    # 密码不得明文入库（silent-failure：明文落库 = 密钥泄漏）
    assert u.password_hash != 's3cret-pass'
    assert u.password_hash.startswith('$2')  # bcrypt

    out = capsys.readouterr().out
    assert 'admin' in out  # 反馈「创建成功」不得静默


def test_create_admin_rejects_duplicate_username(db_engine, monkeypatch):
    """重复 username 必须 raise（unique 约束），不能静默吞。"""
    import app.cli as cli_mod

    monkeypatch.setattr(cli_mod, 'engine', db_engine)
    cli_mod.cmd_create_admin(
        argparse_ns=MagicMock(username='admin', password='first-pass')
    )
    # 第二次同 username → IntegrityError 上抛（不能静默成功）
    with pytest.raises(Exception):
        cli_mod.cmd_create_admin(
            argparse_ns=MagicMock(username='admin', password='second-pass')
        )


# ---------------------------------------------------------------- password 解析（安全）
def test_resolve_password_prefers_explicit_arg():
    """--password 显式参数优先级最高（向后兼容）。"""
    import app.cli as cli_mod

    ns = MagicMock(password='explicit-pass')
    assert cli_mod.resolve_password(ns) == 'explicit-pass'


def test_resolve_password_falls_back_to_env(monkeypatch):
    """省略 --password 时读 ADMIN_PASSWORD 环境变量（非交互自动化场景）。"""
    import app.cli as cli_mod

    monkeypatch.setenv('ADMIN_PASSWORD', 'env-pass')
    ns = MagicMock(password=None)  # 省略 --password
    assert cli_mod.resolve_password(ns) == 'env-pass'


def test_resolve_password_falls_back_to_getpass(monkeypatch):
    """env 也省略时走交互 getpass prompt（密码不进 shell history / ps）。"""
    import app.cli as cli_mod

    monkeypatch.delenv('ADMIN_PASSWORD', raising=False)
    # 替换 getpass.getpass 避免真卡 stdin（CI 无 TTY 会 hang）
    monkeypatch.setattr(cli_mod.getpass, 'getpass', lambda prompt: 'typed-pass')
    ns = MagicMock(password=None)
    assert cli_mod.resolve_password(ns) == 'typed-pass'


def test_create_admin_rejects_empty_password(monkeypatch):
    """密码解析为空（prompt 空输入 / env 空串）时 sys.exit(2)，不静默创建空密码账号。

    回归点：空密码哈希后仍能落库（bcrypt 接受空串），但空密码 = 无认证，是
    silent-failure（运维以为建了账号，实则任何人空密码可登）。必须显式拒绝。
    """
    import app.cli as cli_mod

    monkeypatch.delenv('ADMIN_PASSWORD', raising=False)
    monkeypatch.setattr(cli_mod.getpass, 'getpass', lambda prompt: '')  # 空输入
    ns = MagicMock(username='admin', password=None)

    with pytest.raises(SystemExit) as exc_info:
        cli_mod.cmd_create_admin(argparse_ns=ns)
    assert exc_info.value.code == 2


# ---------------------------------------------------------------- ssq smoke
def test_ssq_smoke_runs_fetch_and_compare(db_engine, monkeypatch, capsys):
    """ssq 冒烟：调用 FetchService('ssq') + CompareService.process_pending + 打印摘要。

    用 MagicMock 替换真实 FetchService/CompareService，避免 CI 真实网络调用（无密钥）。
    回归点：CLI 必须真的调用了两个服务并打印结果——否则 silent-success（CLI 报 ok
    但什么都没做）。
    """
    import app.cli as cli_mod

    # 构造真实模块引用 + 替换服务类
    fake_fetch = MagicMock()
    fake_fetch.fetch_and_store.return_value = MagicMock(
        stored=True, verified=True, single_source=False, not_drawn=False, error=None
    )
    fake_compare = MagicMock()
    fake_compare.process_pending.return_value = 3

    # 替换 FetchService/CompareService 类工厂：CLI 内部 new 时拿到 mock 实例
    monkeypatch.setattr(cli_mod, 'FetchService', lambda *a, **kw: fake_fetch)
    monkeypatch.setattr(cli_mod, 'CompareService', lambda *a, **kw: fake_compare)
    monkeypatch.setattr(cli_mod, 'engine', db_engine)
    # settings 读取必须可工作——cli 内部读 mxnzp_api_key/juhe_api_key。conftest
    # _reset_settings_and_env 删除了 JWT_SECRET/CRYPTO_KEY_V1，因此直接替换 get_settings
    # 避免触发真实 Settings 构造的 ValidationError（与本测试意图无关）。
    monkeypatch.setattr(
        cli_mod, 'get_settings', lambda: MagicMock(mxnzp_api_key='k', juhe_api_key='k')
    )
    monkeypatch.setattr(cli_mod, 'MxnzpAdapter', MagicMock(name='MxnzpAdapter'))
    monkeypatch.setattr(cli_mod, 'JuheAdapter', MagicMock(name='JuheAdapter'))

    cli_mod.cmd_smoke(argparse_ns=MagicMock())

    fake_fetch.fetch_and_store.assert_called_once_with('ssq')
    fake_compare.process_pending.assert_called_once()

    out = capsys.readouterr().out
    # 摘要行不得静默（spec §13：冒烟要可见结果）
    assert 'fetch ssq' in out.lower()
    assert 'compared' in out.lower()


def test_ssq_smoke_exits_nonzero_on_fetch_error(db_engine, monkeypatch, capsys):
    """fetch 返回 error（all_sources_failed / cross_verify_mismatch）时须非零退出。

    场景：fetch_and_store 返回 ``FetchResult(stored=False, error='all_sources_failed')``
    时，当前 cmd_smoke 只 print error 字段就正常 return——``python -m app.cli ssq`` exit 0。
    若 cron/自动化包装此冒烟（deploy.md 已记录 cron 模式），exit-0-on-error 会隐藏故障
    （silent-success）。须在打印后判 ``r.error`` 并 ``sys.exit(1)``。
    """
    import app.cli as cli_mod

    fake_fetch = MagicMock()
    fake_fetch.fetch_and_store.return_value = MagicMock(
        stored=False, verified=False, single_source=False, not_drawn=False,
        error='all_sources_failed',
    )
    fake_compare = MagicMock()
    fake_compare.process_pending.return_value = 0

    monkeypatch.setattr(cli_mod, 'FetchService', lambda *a, **kw: fake_fetch)
    monkeypatch.setattr(cli_mod, 'CompareService', lambda *a, **kw: fake_compare)
    monkeypatch.setattr(cli_mod, 'engine', db_engine)
    monkeypatch.setattr(
        cli_mod, 'get_settings', lambda: MagicMock(mxnzp_api_key='k', juhe_api_key='k')
    )
    monkeypatch.setattr(cli_mod, 'MxnzpAdapter', MagicMock(name='MxnzpAdapter'))
    monkeypatch.setattr(cli_mod, 'JuheAdapter', MagicMock(name='JuheAdapter'))

    # fetch 报错时必须非零退出（regression point：不能 silent-success）
    with pytest.raises(SystemExit) as exc_info:
        cli_mod.cmd_smoke(argparse_ns=MagicMock())
    assert exc_info.value.code != 0, (
        'cmd_smoke 须在 r.error 非 None 时 sys.exit(非零)——否则 cron 包装冒烟时'
        '故障被隐藏为 success'
    )

    # 错误信息仍要打印到 stderr 供运维肉眼判断（graceful-reporting，不能完全静默）
    err = capsys.readouterr().err
    assert 'all_sources_failed' in err or 'all_sources_failed' in capsys.readouterr().out


# -------------------------------------------------------- backfill-history
def test_backfill_history_cli_stores_draws(db_engine, monkeypatch, capsys):
    """backfill-history CLI：对所有启用彩种抓取历史开奖并入库。

    回归点：CLI 必须真的调用 MxnzpAdapter.fetch_history 并存储结果——否则
    silent-success（CLI 报 ok 但 DB 空）。
    """
    from datetime import date as _date

    import app.cli as cli_mod
    from app.adapters.base import DrawNumbers
    from app.models import DrawResult, LotteryType

    # 种 1 个启用彩种
    with Session(db_engine) as s:
        s.add(LotteryType(code='ssq', name='双色球', category='welfare',
                          spec_json='{}', draw_schedule_json='{"draw_days":[0,2,4]}', enabled=True))
        s.commit()

    fake_draws = [
        DrawNumbers(lottery_code='ssq', draw_no='062', draw_date=_date(2026, 6, 12),
                    front=(1, 2, 3, 4, 5, 6), back=(7,)),
        DrawNumbers(lottery_code='ssq', draw_no='061', draw_date=_date(2026, 6, 10),
                    front=(8, 11, 15, 22, 29, 33), back=(12,)),
    ]
    fake_adapter = MagicMock()
    fake_adapter.fetch_history = MagicMock(return_value=fake_draws)
    monkeypatch.setattr(cli_mod, 'MxnzpAdapter', lambda *a, **kw: fake_adapter)
    monkeypatch.setattr(cli_mod, 'engine', db_engine)
    monkeypatch.setattr(
        cli_mod, 'get_settings',
        lambda: MagicMock(mxnzp_api_key='k', mxnzp_app_secret='s'),
    )

    cli_mod.cmd_backfill_history(argparse_ns=MagicMock())

    fake_adapter.fetch_history.assert_called_once_with('ssq', size=50)
    with Session(db_engine) as s:
        rows = list(s.exec(select(DrawResult).where(DrawResult.lottery_code == 'ssq')).all())
        assert len(rows) == 2
        assert all(r.single_source for r in rows)
    out = capsys.readouterr().out
    assert 'ssq' in out
    assert '回填' in out


def test_backfill_history_cli_exits_when_mxnzp_key_missing(db_engine, monkeypatch):
    """mxnzp key 未配置时 sys.exit(1)，不静默继续。"""
    import app.cli as cli_mod

    monkeypatch.setattr(cli_mod, 'engine', db_engine)
    monkeypatch.setattr(
        cli_mod, 'get_settings',
        lambda: MagicMock(mxnzp_api_key='', mxnzp_app_secret=''),
    )
    with pytest.raises(SystemExit) as exc_info:
        cli_mod.cmd_backfill_history(argparse_ns=MagicMock())
    assert exc_info.value.code == 1


# ---------------------------------------------------------------- backup.sh
def _read_backup_sh():
    if not BACKUP_SH.exists():
        pytest.fail('backup.sh 不存在（plan T10 Step 1 要求创建）')
    return BACKUP_SH.read_text(encoding='utf-8')


def test_backup_sh_exists_and_is_executable():
    """backup.sh 文件存在 + 可执行位设置（cron 需要 +x）。"""
    _read_backup_sh()  # 内容存在
    # 可执行位：T10 plan Step 4 chmod +x backup.sh
    mode = BACKUP_SH.stat().st_mode
    assert mode & 0o100, 'backup.sh 缺少 owner 可执行位（cron 调用会 Permission denied）'


def test_backup_sh_uses_sqlite_backup_api():
    """spec §4.3：进程内用 SQLite backup API 备份（python:3.12-slim 无 sqlite3 CLI）。"""
    content = _read_backup_sh()
    # 必须用 python sqlite3 模块的 .backup() 方法，不能用 sqlite3 CLI（镜像没装）
    assert 'sqlite3' in content
    assert '.backup(' in content or 'backup(' in content
    assert 'python' in content  # 用 python 模块而非 CLI


def test_backup_sh_retains_30_days():
    """spec §4.3：每日备份，保留 30 天（find -mtime +30 -delete）。"""
    content = _read_backup_sh()
    # -mtime +30 -delete 才是「保留 30 天」的正确语义
    assert '-mtime' in content
    assert '30' in content
    assert '-delete' in content


def test_backup_sh_outputs_to_backups_dir():
    """备份文件落到 /app/backups（compose 卷挂载点）。"""
    content = _read_backup_sh()
    assert '/app/backups' in content


def test_backup_sh_uses_set_e():
    """set -e：备份失败必须非零退出（cron 静默 success 是 silent-failure）。"""
    content = _read_backup_sh()
    assert 'set -e' in content


def test_backup_sh_guards_empty_database_url():
    """empty/unset DATABASE_URL 必须非零退出，不能备份空 DB 后静默 success。

    场景：DATABASE_URL 为空字符串或未设置时，``${DATABASE_URL#sqlite:///./}`` 得空串，
    sqlite3.connect('') 会连到 in-memory 空 DB，.backup() 拷贝空 schema，cron 报 success
    而真实 DB 从未备份——典型 silent-failure。脚本须显式 guard 并非零退出。
    """
    content = _read_backup_sh()
    # 必须显式判空 DB 变量（无论变量名是 DB 还是其他），并非零退出
    # 容忍写法：[ -z "$DB" ] 或 test -z "$DB" 或 : "${DB:?...}"
    assert (
        ('-z "$DB"' in content or '-z "${DB}"' in content)
        and ('exit 2' in content or 'exit 1' in content)
    ), 'backup.sh 缺少对空 DB 的 guard（silent-success 陷阱：连空 DB 后 cron 报 ok）'


def test_backup_sh_guards_missing_or_empty_db_file():
    """DB 文件不存在或为空字节时必须非零退出。

    场景：``sqlite3.connect('/nonexistent/path/x.db')`` 在 Python 里是**创建新空文件**
    而非失败——如果路径解析错了（cron CWD != 容器内 CWD），备份会静默拷贝空 DB。
    须在调 .backup() 前断言源文件存在且非空（``[ -f ] && [ -s ]``）。
    """
    content = _read_backup_sh()
    # 必须同时检查 -f（存在）和 -s（非空）——单独 -f 不够，0 字节文件也会通过
    assert '-f' in content and '-s' in content, (
        'backup.sh 缺少对源 DB 文件存在/非空的断言（silent-success：sqlite3.connect '
        '对不存在路径会创建新空文件而非失败）'
    )
    assert 'exit 2' in content or 'exit 1' in content


def test_backup_sh_asserts_backup_nonzero():
    """备份产物必须非零字节，否则非零退出。

    场景：.backup() 连到了错误的（空）DB，拷贝出 0 字节文件，cron 报 success。
    须在 .backup() 之后断言目标文件非空。
    """
    content = _read_backup_sh()
    # 须在 python .backup() 调用之后断言产物 -s
    python_idx = content.find('sqlite3.connect')
    assert python_idx != -1, 'backup.sh 缺少 sqlite3 backup API 调用'
    after_backup = content[python_idx:]
    assert '-s' in after_backup, (
        'backup.sh 缺少对备份产物非零字节的断言（.backup() 后）——'
        '连错 DB 会产生空文件却报 success'
    )


def test_backup_sh_resolves_absolute_db_path():
    """DB 路径须解析为绝对路径，否则 cron/容器 CWD 不一致时连错 DB。

    场景：DATABASE_URL=sqlite:///./data/lottery.db，在容器内 CWD=/app 时正确，
    但 cron 宿主 CWD=/ 时 ``./data/lottery.db`` 解析为 ``/data/lottery.db``（不存在），
    sqlite3.connect 不报错反而创建空文件。脚本须在备份前把 DB 解析为绝对路径
    （dirname/pwd/basename，或强制 DATABASE_URL 必须是绝对路径）。
    """
    content = _read_backup_sh()
    # 接受以下任一写法：(a) 用 cd + pwd 把相对路径转绝对 (b) readlink -f (c) realpath
    has_resolve = (
        ('$(cd ' in content and 'pwd' in content)
        or 'readlink -f' in content
        or 'realpath' in content
    )
    assert has_resolve, (
        'backup.sh 须把 DB 解析为绝对路径（cd+pwd / readlink -f / realpath）——'
        '否则 cron CWD != 容器 CWD 时连错路径静默成功'
    )


# ---------------------------------------------------------------- deploy.md
def _read_deploy_md():
    if not DEPLOY_MD.exists():
        pytest.fail('docs/deploy.md 不存在（plan T10 Step 3 要求创建）')
    return DEPLOY_MD.read_text(encoding='utf-8')


def test_deploy_md_documents_bootstrap_admin():
    """部署文档含首次 admin bootstrap（spec §13 Phase 1.0.13）。"""
    content = _read_deploy_md()
    assert 'create-admin' in content
    assert 'app.cli' in content


def test_deploy_md_documents_backup_cron():
    """部署文档含每日备份 cron（spec §4.3）。"""
    content = _read_deploy_md()
    # cron 配置或备份说明
    assert 'backup' in content.lower()
    assert 'cron' in content.lower() or '0 3' in content


def test_deploy_md_documents_smoke():
    """部署文档含 ssq 冒烟说明（spec §13 Phase 1.0.13）。"""
    content = _read_deploy_md()
    assert 'ssq' in content
    assert 'cli' in content.lower()


def test_deploy_md_documents_port_and_restart():
    """部署文档含关键约束：端口 8280 + restart: always（spec §4.3）。"""
    content = _read_deploy_md()
    assert '8280' in content
    assert 'always' in content
