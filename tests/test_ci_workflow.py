# tests/test_ci_workflow.py
"""CI workflow 形态测试（Plan 09 / T9）。

纯文本断言（不引 yaml 依赖）：锁定 spec §3.5 + autoplan E2/E5/F11 要求的最小质量门禁。
"""

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
