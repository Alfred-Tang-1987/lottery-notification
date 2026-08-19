"""LICENSE 与包元数据测试（Plan 09 / T4；2026-08-19 决策更新为 MIT——spec §2）。"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_license_file_is_mit():
    text = (ROOT / 'LICENSE').read_text()
    assert 'MIT License' in text
    assert 'Permission is hereby granted, free of charge' in text
    # MIT 宽松许可核心：允许商用/闭源/修改/再分发，仅要求保留版权与许可声明
    assert 'without restriction, including without limitation' in text


def test_pyproject_declares_license():
    text = (ROOT / 'pyproject.toml').read_text()
    assert 'license = "MIT"' in text
