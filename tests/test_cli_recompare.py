"""recompare CLI 冒烟（Plan 10 / T6）。模式沿用 tests/test_cli_reset_password.py：
mock engine / 捕获 argparse 调用。"""


def test_cli_recompare_invokes_service(db_engine, monkeypatch):
    """CLI 把 --lottery/--dry-run 正确传给 recompare_all 并打印统计。

    db_engine fixture 与既有 CLI 测试一致：预置 app.db.session._engine，让
    app.cli 模块级 ``engine = get_engine()`` 短路返回测试引擎（否则首次 import 时
    get_settings() 在 conftest 清空密钥环境下抛 ValidationError）。
    """
    from app import cli as cli_mod

    captured = {}
    monkeypatch.setattr(cli_mod, '_engine_from_env', lambda: object())  # 不真实构造 engine
    monkeypatch.setattr(
        cli_mod, 'recompare_all',
        lambda engine, lottery_code=None, dry_run=False:
        captured.update(lottery_code=lottery_code, dry_run=dry_run) or {'draws': 1, 'rows': 2, 'changed': 1},
    )
    cli_mod.main(['recompare', '--lottery', 'dlt', '--dry-run'])
    assert captured == {'lottery_code': 'dlt', 'dry_run': True}
