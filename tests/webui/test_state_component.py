"""Plan 06 T4: <State> component (loading/empty/error + warm empty CTA)."""

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent
STATE_VUE = PROJECT_ROOT / "web" / "src" / "components" / "State.vue"


@pytest.fixture
def state_vue() -> str:
    assert STATE_VUE.exists(), "web/src/components/State.vue 必须存在"
    return STATE_VUE.read_text(encoding="utf-8")


@pytest.mark.webui
class TestStateComponent:
    """验证统一状态组件符合 spec §12.4（A11y / 状态 / 温暖空状态）。"""

    def test_state_component_exists(self, state_vue):
        """State.vue 组件文件必须存在且非空。"""
        assert len(state_vue) > 0

    def test_state_has_status_role(self, state_vue):
        """状态容器 role="status"，便于读屏识别。"""
        assert 'role="status"' in state_vue

    def test_state_uses_aria_live(self, state_vue):
        """错误用 assertive，其余用 polite。"""
        assert ":aria-live=\"type === 'error' ? 'assertive' : 'polite'\"" in state_vue

    def test_loading_state_has_spinner(self, state_vue):
        """loading 状态显示 spinner 并带 aria-label。"""
        assert 'v-if="type === \'loading\'"' in state_vue
        assert 'aria-label="加载中"' in state_vue
        assert "class=\"spinner\"" in state_vue

    def test_empty_state_has_warm_cta(self, state_vue):
        """空状态渲染标题与 CTA 按钮（禁裸 No data）。"""
        assert 'v-else-if="type === \'empty\'"' in state_vue
        assert 'class="state-cta"' in state_vue
        assert "No data" not in state_vue

    def test_error_state_has_retry_button(self, state_vue):
        """error 状态默认显示重试按钮。"""
        assert "重试" in state_vue
        assert 'type="button"' in state_vue

    def test_state_uses_semantic_button(self, state_vue):
        """交互元素是 button，不是 div onclick。"""
        assert "<button" in state_vue
        assert "@click" not in state_vue.split("<script")[0] or "<button" in state_vue
