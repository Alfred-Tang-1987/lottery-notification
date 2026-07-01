"""Plan 06 T5: responsive layout (sidebar / bottom tab bar / user identity / A11y landmark)."""

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
WEB = PROJECT_ROOT / "web"
APP_VUE = WEB / "src" / "App.vue"
ROUTER_TS = WEB / "src" / "router.ts"
NAV_DESKTOP = WEB / "src" / "components" / "NavDesktop.vue"
NAV_MOBILE = WEB / "src" / "components" / "NavMobile.vue"
USER_MENU = WEB / "src" / "components" / "UserMenu.vue"

NAV_ITEMS = [
    ("/", "仪表盘"),
    ("/numbers", "我的号码"),
    ("/query", "开奖查询"),
    ("/wins", "中奖记录"),
    ("/stats", "我的统计"),
    ("/trend", "开奖走势"),
    ("/settings", "设置"),
    ("/admin", "后台管理"),
]


@pytest.fixture
def app_vue() -> str:
    assert APP_VUE.exists(), "web/src/App.vue 必须存在"
    return APP_VUE.read_text(encoding="utf-8")


@pytest.fixture
def router_ts() -> str:
    assert ROUTER_TS.exists(), "web/src/router.ts 必须存在"
    return ROUTER_TS.read_text(encoding="utf-8")


@pytest.fixture
def nav_desktop() -> str:
    assert NAV_DESKTOP.exists(), "web/src/components/NavDesktop.vue 必须存在"
    return NAV_DESKTOP.read_text(encoding="utf-8")


@pytest.fixture
def nav_mobile() -> str:
    assert NAV_MOBILE.exists(), "web/src/components/NavMobile.vue 必须存在"
    return NAV_MOBILE.read_text(encoding="utf-8")


@pytest.fixture
def user_menu() -> str:
    assert USER_MENU.exists(), "web/src/components/UserMenu.vue 必须存在"
    return USER_MENU.read_text(encoding="utf-8")


@pytest.mark.webui
class TestRouter:
    """验证 9 路由 history 模式与 meta 信息。"""

    def test_router_uses_web_history(self, router_ts):
        assert "createWebHistory" in router_ts

    def test_router_has_nine_routes(self, router_ts):
        paths = ["/login", "/", "/numbers", "/query", "/wins", "/stats", "/trend", "/settings", "/admin"]
        for p in paths:
            assert f'"{p}"' in router_ts or f"'{p}'" in router_ts, f"缺少路由 {p}"

    def test_routes_have_nav_meta(self, router_ts):
        """除登录页外主要页面带 nav meta。"""
        for path, label in NAV_ITEMS:
            assert label in router_ts, f"路由 {path} 缺少中文标签 {label}"


@pytest.mark.webui
class TestAppLayout:
    """验证根布局：响应式、landmark、理性提示。"""

    def test_app_imports_layout_components(self, app_vue):
        assert "NavDesktop" in app_vue
        assert "NavMobile" in app_vue
        assert "UserMenu" in app_vue

    def test_app_has_header_landmark(self, app_vue):
        assert "<header" in app_vue
        assert "role=\"banner\"" in app_vue or "<header" in app_vue

    def test_app_has_main_landmark(self, app_vue):
        assert '<main' in app_vue
        assert 'role="main"' in app_vue

    def test_app_has_footer_disclaimer(self, app_vue):
        assert "<footer" in app_vue
        assert 'role="contentinfo"' in app_vue
        assert "理性购彩" in app_vue

    def test_app_renders_router_view(self, app_vue):
        assert "<router-view" in app_vue

    def test_app_uses_responsive_mobile_flag(self, app_vue):
        assert "window.innerWidth" in app_vue or "matchMedia" in app_vue


@pytest.mark.webui
class TestNavDesktop:
    """验证桌面 sidebar：8 项 + aria-current。"""

    def test_nav_desktop_has_eight_items(self, nav_desktop):
        """v-for 在源码中只有 1 个 router-link 标签，运行时展开为 8 项。"""
        assert 'v-for="item in' in nav_desktop
        assert nav_desktop.count('label:') >= 8 or nav_desktop.count('to:') >= 8

    def test_nav_desktop_has_all_labels(self, nav_desktop):
        for _, label in NAV_ITEMS:
            assert label in nav_desktop, f"sidebar 缺少 {label}"

    def test_nav_desktop_uses_aria_current(self, nav_desktop):
        assert "aria-current" in nav_desktop


@pytest.mark.webui
class TestNavMobile:
    """验证移动底部 tab bar：4 高频 + 更多抽屉。"""

    def test_nav_mobile_has_four_primary_tabs(self, nav_mobile):
        primary = ["仪表盘", "号码", "查询", "我的"]
        for label in primary:
            assert label in nav_mobile, f"底部 tab bar 缺少 {label}"

    def test_nav_mobile_has_more_drawer(self, nav_mobile):
        assert "更多" in nav_mobile
        assert 'role="dialog"' in nav_mobile or 'aria-haspopup' in nav_mobile

    def test_nav_mobile_is_fixed_bottom_bar(self, nav_mobile):
        assert "bottom: 0" in nav_mobile or "fixed" in nav_mobile or "position: fixed" in nav_mobile

    def test_nav_mobile_uses_buttons_or_links(self, nav_mobile):
        assert "<router-link" in nav_mobile
        assert "<button" in nav_mobile
        assert "<nav" in nav_mobile
        assert 'role="navigation"' in nav_mobile


@pytest.mark.webui
class TestUserMenu:
    """验证右上角用户身份区与登出。"""

    def test_user_menu_calls_logout(self, user_menu):
        auth_store = (PROJECT_ROOT / "web" / "src" / "stores" / "auth.ts").read_text(encoding="utf-8")
        assert "/auth/logout" in auth_store
        assert "auth.logout" in user_menu or "handleLogout" in user_menu

    def test_user_menu_has_logout_button(self, user_menu):
        assert "登出" in user_menu or "退出" in user_menu

    def test_user_menu_uses_button_not_div_click(self, user_menu):
        assert "<button" in user_menu
