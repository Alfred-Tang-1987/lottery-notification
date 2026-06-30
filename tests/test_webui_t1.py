"""Plan 06 T1: web project skeleton (Vite + Vue3 + UnoCSS + ECharts)."""

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"


@pytest.fixture
def package_json():
    path = WEB / "package.json"
    assert path.exists(), "web/package.json is missing"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def vite_config():
    path = WEB / "vite.config.ts"
    assert path.exists(), "web/vite.config.ts is missing"
    return path.read_text(encoding="utf-8")


@pytest.fixture
def index_html():
    path = WEB / "index.html"
    assert path.exists(), "web/index.html is missing"
    return path.read_text(encoding="utf-8")


@pytest.fixture
def main_ts():
    path = WEB / "src" / "main.ts"
    assert path.exists(), "web/src/main.ts is missing"
    return path.read_text(encoding="utf-8")


def test_package_json_has_required_dependencies(package_json):
    deps = package_json.get("dependencies", {})
    required = {"vue", "vue-router", "pinia", "echarts", "unocss"}
    missing = required - set(deps)
    assert not missing, f"missing runtime dependencies: {missing}"


def test_package_json_has_required_dev_dependencies(package_json):
    dev = package_json.get("devDependencies", {})
    required = {"@vitejs/plugin-vue", "@unocss/preset-uno", "typescript", "vite", "vue-tsc"}
    missing = required - set(dev)
    assert not missing, f"missing dev dependencies: {missing}"


def test_package_json_scripts_build_to_static(package_json):
    scripts = package_json.get("scripts", {})
    assert scripts.get("dev") == "vite"
    assert scripts.get("build") == "vue-tsc && vite build"
    assert scripts.get("preview") == "vite preview"


def test_vite_config_uses_vue_and_unocss(vite_config):
    assert "@vitejs/plugin-vue" in vite_config
    assert "unocss" in vite_config.lower() or "UnoCSS" in vite_config


def test_vite_config_proxies_api_and_builds_to_static(vite_config):
    assert '"/api"' in vite_config
    assert '"/auth"' in vite_config
    assert "8280" in vite_config
    assert '"../static"' in vite_config
    assert "emptyOutDir" in vite_config


def test_index_html_has_app_mount(index_html):
    assert '<div id="app"></div>' in index_html
    assert 'src="/src/main.ts"' in index_html


def test_main_ts_mounts_vue_app(main_ts):
    assert 'createApp' in main_ts
    assert './App.vue' in main_ts
    assert "#app" in main_ts


def test_main_ts_wires_router_and_pinia(main_ts):
    assert "vue-router" in main_ts
    assert "pinia" in main_ts
