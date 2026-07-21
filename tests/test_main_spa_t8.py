"""T8: FastAPI 静态托管 SPA（history catch-all）— spec §12.3。

行为契约（plan T8）：
1. STATIC_DIR 存在时，`GET /` 返回 SPA 的 index.html（content-type text/html）。
2. history 模式：`GET /任意/spa/路径` 不命中 API/health/assets 时回退 index.html。
3. STATIC_DIR 不存在时（未 build 前端 / 开发态），不注册 catch-all——访问 `/` 返回 404 default。

设计：main.py 在模块级 `if STATIC_DIR.exists():` 一次性挂载 /assets + catch_all。
为了让测试可重入（不污染全局 app 状态），实现暴露 ``mount_spa(app, static_dir)``
函数（pure registration helper，可被 main.py 模块级调用 + 测试显式调用并清理）。
"""

from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.config import reset_settings_cache


@pytest.fixture(autouse=True)
def _mock_env(monkeypatch):
    """注入有效环境变量 + 关闭 scheduler，避免 lifespan 抓取真实数据源。"""
    reset_settings_cache()
    monkeypatch.setenv('JWT_SECRET', 'x' * 32)
    monkeypatch.setenv('CRYPTO_KEY_V1', Fernet.generate_key().decode())
    monkeypatch.setenv('SCHEDULER_ENABLED', 'false')


def _build_static_dir(tmp_path: Path) -> Path:
    """在 tmp_path 下构造 static/ 目录 + index.html + assets/ 占位文件，返回 static 路径。"""
    static = tmp_path / 'static'
    static.mkdir()
    (static / 'index.html').write_text(
        '<!doctype html><html><head><title>兑奖了吗？</title></head>'
        '<body><div id="app"></div></body></html>',
        encoding='utf-8',
    )
    assets = static / 'assets'
    assets.mkdir()
    (assets / 'app.js').write_text('console.log("spa")', encoding='utf-8')
    return static


def _cleanup_spa_routes(app):
    """移除 mount_spa 注册的 routes，避免污染后续测试。"""
    app.router.routes = [
        r for r in app.router.routes if getattr(r, 'name', '') not in ('assets', 'spa_catch_all')
    ]


def test_spa_serves_index_html_at_root(tmp_path):
    """RED：STATIC_DIR 存在时 GET / 必须返回 SPA index.html（content-type text/html）。

    历史 fallback 是 Vue Router history 模式的核心契约：刷新 `/dashboard` 时不该 404，
    而应回退到 index.html 让前端 router 接管。本测试断言最基础一行：根路径返回 index.html。
    """
    import app.main as main_mod

    static_dir = _build_static_dir(tmp_path)
    main_mod.mount_spa(main_mod.app, static_dir)
    try:
        client = TestClient(main_mod.app)
        r = client.get('/')
        assert r.status_code == 200
        assert 'text/html' in r.headers.get('content-type', '')
        assert '<div id="app"></div>' in r.text
    finally:
        _cleanup_spa_routes(main_mod.app)


def test_spa_history_fallback_returns_index_for_arbitrary_path(tmp_path):
    """history 模式：`GET /dashboard/overview` 这种非 API 路径应回退到 index.html。

    不能返回 404，否则前端路由刷新失效。
    """
    import app.main as main_mod

    static_dir = _build_static_dir(tmp_path)
    main_mod.mount_spa(main_mod.app, static_dir)
    try:
        client = TestClient(main_mod.app)
        r = client.get('/dashboard/overview')
        assert r.status_code == 200
        assert 'text/html' in r.headers.get('content-type', '')
    finally:
        _cleanup_spa_routes(main_mod.app)


def test_spa_assets_served_via_staticfiles(tmp_path):
    """`/assets/<file>` 走 StaticFiles 挂载（不走 catch_all）。"""
    import app.main as main_mod

    static_dir = _build_static_dir(tmp_path)
    main_mod.mount_spa(main_mod.app, static_dir)
    try:
        client = TestClient(main_mod.app)
        r = client.get('/assets/app.js')
        assert r.status_code == 200
        assert 'console.log("spa")' in r.text
    finally:
        _cleanup_spa_routes(main_mod.app)
