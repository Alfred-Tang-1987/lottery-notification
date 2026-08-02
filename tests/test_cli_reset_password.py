"""CLI ``reset-password`` 命令测试（admin 自助/运维重置场景）。

行为契约：
1. ``python -m app.cli reset-password --username X --password Y`` 把已存在用户的
   ``password_hash`` 更新为新哈希（明文不入库），旧密码立即失效。
2. 用户不存在时非零退出（不静默成功 -- 否则运维误以为已重置）。
3. 空密码拒绝（sys.exit 非零），不静默写空密码哈希。
4. 缺 ``--username`` 时 argparse 拒绝（required=True）。

设计：对齐 ``test_cli_t10.py`` 风格 -- ``cmd_reset_password(argparse_ns=MagicMock(...))``
+ monkeypatch ``cli_mod.engine`` 指向隔离 db_engine，断言落库状态。
复用 ``resolve_password``（已由 create-admin 测试覆盖三档解析），此处聚焦 reset 语义。
"""

from unittest.mock import MagicMock

import pytest
from sqlmodel import Session

from app.api.security import verify_password
from app.models import User


def _seed_admin(engine, username='admin', password='old-pass-123'):
    """种一个 admin 用户，返回 (id, old_hash)。"""
    import app.cli as cli_mod

    with Session(engine) as s:
        u = User(
            username=username,
            password_hash=cli_mod.hash_password(password),
            role='admin',
            invite_code='BOOTSTRAP',
        )
        s.add(u)
        s.commit()
        s.refresh(u)
        return u.id, u.password_hash


def test_reset_password_updates_hash_and_invalidates_old(db_engine, monkeypatch, capsys):
    """reset-password 更新哈希；旧密码失效、新密码可验；明文不入库。"""
    import app.cli as cli_mod

    monkeypatch.setattr(cli_mod, 'engine', db_engine)
    uid, old_hash = _seed_admin(db_engine)

    cli_mod.cmd_reset_password(
        argparse_ns=MagicMock(username='admin', password='new-pass-456')
    )

    with Session(db_engine) as s:
        u = s.get(User, uid)
        assert u.password_hash != old_hash  # 哈希确已变更
        assert u.password_hash != 'new-pass-456'  # 明文不得入库
        assert u.password_hash.startswith('$2')  # 仍是 bcrypt
        # 旧密码失效（改密的核心安全语义）
        assert not verify_password('old-pass-123', u.password_hash)
        # 新密码可登
        assert verify_password('new-pass-456', u.password_hash)

    out = capsys.readouterr().out
    assert 'admin' in out  # 反馈不得静默


def test_reset_password_rejects_unknown_user(db_engine, monkeypatch, capsys):
    """用户不存在时 sys.exit 非零，不静默成功。

    回归点：若查不到用户仍报 success，运维以为已重置，实则无人能登 -- silent-failure。
    """
    import app.cli as cli_mod

    monkeypatch.setattr(cli_mod, 'engine', db_engine)
    # 不种任何用户
    with pytest.raises(SystemExit) as exc_info:
        cli_mod.cmd_reset_password(
            argparse_ns=MagicMock(username='ghost', password='whatever-1')
        )
    assert exc_info.value.code != 0
    err = capsys.readouterr().err
    assert 'ghost' in err or '不存在' in err  # 错误信息可见


def test_reset_password_rejects_empty_password(db_engine, monkeypatch):
    """空密码（prompt 空输入 / env 空串）拒绝，不静默写空密码哈希。

    回归点：空密码哈希后能落库（bcrypt 接受空串），但空密码 = 无认证，是 silent-failure。
    """
    import app.cli as cli_mod

    monkeypatch.setattr(cli_mod, 'engine', db_engine)
    _seed_admin(db_engine)
    monkeypatch.delenv('ADMIN_PASSWORD', raising=False)
    monkeypatch.setattr(cli_mod.getpass, 'getpass', lambda prompt: '')  # 空输入

    with pytest.raises(SystemExit) as exc_info:
        cli_mod.cmd_reset_password(argparse_ns=MagicMock(username='admin', password=None))
    assert exc_info.value.code != 0


def test_reset_password_required_username_argparse(db_engine, monkeypatch):
    """缺 --username 时 argparse 拒绝（required=True，subparser 层强制）。

    覆盖 CLI main 注册：subparser 必须 required=True，否则空跑静默成功。
    """
    import app.cli as cli_mod

    # 通过 main(['reset-password', '--password', 'x']) 触发 argparse 校验
    monkeypatch.setattr(cli_mod, 'engine', db_engine)
    with pytest.raises(SystemExit):  # argparse 缺 required 必非零退出
        cli_mod.main(['reset-password', '--password', 'x'])
