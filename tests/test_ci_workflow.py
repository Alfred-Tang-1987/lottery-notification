# tests/test_ci_workflow.py
"""CI workflow 形态测试（Plan 09 / T9）。

纯文本断言（不引 yaml 依赖）：锁定 spec §3.5 + autoplan E2/E5/F11 要求的最小质量门禁。
"""

import re
from pathlib import Path

WF = Path(__file__).resolve().parent.parent / '.github' / 'workflows' / 'ci.yml'


def test_ci_workflow_exists():
    assert WF.exists()


def test_ci_triggers_and_jobs():
    text = WF.read_text()
    assert 'pull_request' in text and 'push' in text
    for job in ('backend', 'frontend', 'leak-scan'):
        assert f'{job}:' in text, f'缺 job {job}'


def test_ci_backend_steps():
    text = WF.read_text()
    assert 'ruff check' in text
    assert 'lint-imports' in text
    # E5：全量 pytest（迁移测试自含 SQLite，收进 CI 而非排除）
    assert 'uv run pytest' in text and 'not migration' not in text


def test_ci_frontend_steps():
    text = WF.read_text()
    assert 'npm ci' in text
    assert 'npm test' in text      # F11：vitest 进 CI
    assert 'npm run build' in text


def test_ci_leak_scan():
    text = WF.read_text()
    assert 'gitleaks' in text
    assert 'publish-check.sh' in text
    assert 'fetch-depth: 0' in text, 'gitleaks 扫历史需完整 clone'


def test_ci_token_permissions_are_minimal():
    text = WF.read_text()
    assert re.search(r'^permissions:\s*\n\s+contents:\s*read', text, re.M), \
        'CI workflow must declare permissions: contents: read'


def test_ci_actions_pinned_to_sha():
    """Floating major versions are a supply-chain risk; require commit SHAs."""
    text = WF.read_text()
    uses_lines = [ln for ln in text.splitlines() if 'uses:' in ln and '@' in ln]
    assert uses_lines, 'ci.yml 应有 uses: actions'
    for ln in uses_lines:
        ref = ln.split('uses:')[1].strip().split('#')[0].strip()
        _, version = ref.rsplit('@', 1)
        assert len(version) == 40 and all(c in '0123456789abcdef' for c in version), \
            f'action 未 pin 完整 SHA：{ref}'


def test_ci_leak_scan_guards_fork_prs():
    text = WF.read_text()
    assert "github.event_name == 'push' || github.event.pull_request.head.repo.full_name == github.repository" in text, \
        'leak-scan 必须 push 或同 repo PR 才跑（fork PR 跳过防日志回显）'
