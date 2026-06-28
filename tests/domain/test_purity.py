import pkgutil

import app.domain


def test_domain_modules_importable():
    """领域层所有子模块可 import（结构性检查）。真正的 purity 护栏靠 import-linter（CI 强制）。"""
    import importlib

    for mod_info in pkgutil.walk_packages(app.domain.__path__, prefix='app.domain.'):
        importlib.import_module(mod_info.name)  # 不抛即过；purity 由 lint-imports 强制
