from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent
TOKENS_CSS = PROJECT_ROOT / "web" / "src" / "styles" / "tokens.css"
MAIN_TS = PROJECT_ROOT / "web" / "src" / "main.ts"


@pytest.fixture
def tokens_css() -> str:
    assert TOKENS_CSS.exists(), "tokens.css 必须存在"
    return TOKENS_CSS.read_text(encoding="utf-8")


@pytest.fixture
def main_ts() -> str:
    assert MAIN_TS.exists(), "main.ts 必须存在"
    return MAIN_TS.read_text(encoding="utf-8")


@pytest.mark.webui
class TestTokensCss:
    """验证 DESIGN.md token 落地为 CSS 变量。"""

    def test_tokens_css_exists(self, tokens_css):
        """token 文件必须存在且非空。"""
        assert len(tokens_css) > 0

    def test_light_tokens_from_design_md(self, tokens_css):
        """亮色模式包含 DESIGN.md 定义的核心 token。"""
        light_vars = [
            "--bg: #f5f5f7",
            "--surface: #ffffff",
            "--fg: #1d1d1f",
            "--muted: #6e6e73",
            "--border: #d2d2d7",
            "--accent: #0071e3",
            "--accent-hover: #0077ed",
            "--red-ball: #e11d2a",
            "--blue-ball: #0071e3",
            "--success: #059669",
            "--danger: #dc2626",
            "--warning: #d97706",
        ]
        for var in light_vars:
            assert var in tokens_css, f"亮色 token 缺失: {var}"

    def test_dark_tokens_from_design_md(self, tokens_css):
        """深色模式包含 DESIGN.md 定义的核心 token。"""
        dark_vars = [
            "--bg: #000000",
            "--surface: #1c1c1e",
            "--fg: #f5f5f7",
            "--muted: #98989d",
            "--border: #38383a",
            "--accent: #0a84ff",
            "--red-ball: #ff453a",
            "--blue-ball: #0a84ff",
            "--success: #30d158",
            "--danger: #ff453a",
            "--surface-2: #2c2c2e",
        ]
        for var in dark_vars:
            assert var in tokens_css, f"深色 token 缺失: {var}"

    def test_font_tokens(self, tokens_css):
        """字体 token 使用 SF Pro 系统栈。"""
        assert "--font-display" in tokens_css
        assert "--font-body" in tokens_css
        assert "SF Pro Display" in tokens_css
        assert "SF Pro Text" in tokens_css

    def test_text_size_tokens(self, tokens_css):
        """字号阶梯与 DESIGN.md 一致。"""
        for var in [
            "--text-xs: 11px",
            "--text-sm: 12px",
            "--text-base: 13px",
            "--text-md: 14px",
            "--text-lg: 16px",
            "--text-xl: 18px",
            "--text-2xl: 24px",
        ]:
            assert var in tokens_css, f"字号 token 缺失: {var}"

    def test_radius_and_motion_tokens(self, tokens_css):
        """圆角与动效 token 与 DESIGN.md 一致。"""
        assert "--radius: 12px" in tokens_css
        assert "--dur-fast: 0.1s" in tokens_css
        assert "--dur-base: 0.15s" in tokens_css
        assert "--dur-slow: 0.25s" in tokens_css

    def test_body_uses_tokens(self, tokens_css):
        """body 使用 token 设置背景/前景/字体。"""
        assert "body {" in tokens_css
        assert "font-family: var(--font-body)" in tokens_css
        assert "background: var(--bg)" in tokens_css
        assert "color: var(--fg)" in tokens_css

    def test_no_double_maintenance_media_query(self, tokens_css):
        """不使用 prefers-color-scheme 媒体查询，避免与 .dark class 双份维护。"""
        assert "prefers-color-scheme" not in tokens_css


@pytest.mark.webui
class TestMainTsTheme:
    """验证 main.ts 引入 tokens 并设置主题偏好。"""

    def test_main_ts_imports_tokens(self, main_ts):
        """main.ts 必须 import tokens.css。"""
        assert "import './styles/tokens.css'" in main_ts

    def test_main_ts_theme_preference(self, main_ts):
        """main.ts 根据 localStorage 或系统偏好设置 .dark class。"""
        assert "localStorage.getItem('theme')" in main_ts
        assert "matchMedia('(prefers-color-scheme: dark)')" in main_ts
        assert "classList.add('dark')" in main_ts
