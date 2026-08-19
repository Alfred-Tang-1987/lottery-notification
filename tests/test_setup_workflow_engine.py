"""setup-workflow-engine.sh 测试（Plan 09 / T3）。

env 化设计（autoplan E1）：URL 从 WORKFLOW_ENGINE_URL 读，脚本内零字面内网地址；
未配置时跳过并打印「内部开发工具」说明；子模块机制已永久移除（F20 门禁兜底）。
"""

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / 'scripts' / 'setup-workflow-engine.sh'


def test_script_has_no_literal_internal_url():
    text = SCRIPT.read_text()
    ip = '192' + '.168.8.'  # 拆分书写防门禁自匹配
    assert ip not in text, '脚本不得硬编码内网 IP（E1：否则过不了自家门禁）'
    assert ':84' + '18' not in text, '脚本不得硬编码内网 gitea 端口'


def test_skip_without_env_url():
    env = {k: v for k, v in os.environ.items() if k != 'WORKFLOW_ENGINE_URL'}
    r = subprocess.run(
        ['bash', str(SCRIPT)], capture_output=True, text=True, env=env, timeout=60,
    )
    assert r.returncode == 0, f'未配置 env 应跳过且成功：{r.stdout}{r.stderr}'
    assert '内部开发工具' in r.stdout + r.stderr


def test_gitmodules_removed_and_engine_untracked():
    assert not (ROOT / '.gitmodules').exists(), '.gitmodules 应已删除'
    r = subprocess.run(
        ['git', 'ls-files', '.claude/workflow-engine'],
        capture_output=True, text=True, cwd=ROOT, timeout=30,
    )
    assert r.stdout.strip() == '', '.claude/workflow-engine 不得被 git 跟踪'
    # 2026-08-19：公共仓库已移除引擎，派生副本同样不得入库（内部工具回归兜底）
    r2 = subprocess.run(
        ['git', 'ls-files', '.claude/workflows/run-plans.js'],
        capture_output=True, text=True, cwd=ROOT, timeout=30,
    )
    assert r2.stdout.strip() == '', '.claude/workflows/run-plans.js 不得被 git 跟踪'


def test_gitignore_covers_engine_and_local_deploy_doc():
    text = (ROOT / '.gitignore').read_text()
    assert '.claude/workflow-engine/' in text
    assert '.claude/workflows/run-plans.js' in text
    assert 'deploy-nas-internal.md' in text


def test_dockerignore_covers_claude_dir():
    text = (ROOT / '.dockerignore').read_text()
    assert '.claude' in text, 'E8-F17：.claude/ 不得进镜像构建上下文'
