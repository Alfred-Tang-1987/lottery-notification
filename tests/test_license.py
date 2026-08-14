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
