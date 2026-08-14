"""发布门禁 scripts/publish-check.sh 测试（Plan 09 / T0）。

用 PUBLISH_CHECK_ROOT 指向 tmp 目录做 hermetic 测试，不依赖仓库当前净化状态。
注意：构造泄露样本时敏感词用字符串拼接，避免本测试文件自身命中门禁词表。
"""

import os
import stat
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / 'scripts' / 'publish-check.sh'

# 拆分书写，防自匹配（门禁词表含这些词的字面形）
_IP = '192' + '.168.8.1'
_SECRET = 'JWT_' + 'SECRET=real-value-123'


def _run(root: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, 'PUBLISH_CHECK_ROOT': str(root)}
    return subprocess.run(
        ['bash', str(SCRIPT), '--grep-only'],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


def test_script_exists_and_executable():
    assert SCRIPT.exists(), 'scripts/publish-check.sh 不存在'
    assert SCRIPT.stat().st_mode & stat.S_IXUSR, '脚本缺可执行位（chmod +x）'


def test_clean_tree_passes(tmp_path):
    (tmp_path / 'README.md').write_text('# 干净项目\n占位符 <NAS_IP> 不算泄露\n')
    r = _run(tmp_path)
    assert r.returncode == 0, f'干净目录应通过：{r.stdout}{r.stderr}'


def test_internal_ip_fails(tmp_path):
    (tmp_path / 'leak.md').write_text(f'部署到 {_IP} 的 NAS\n')
    r = _run(tmp_path)
    assert r.returncode == 1, '含内网 IP 必须 exit 1'
    assert 'leak.md' in r.stdout + r.stderr, '输出应指出泄露文件'


def test_secret_assignment_fails(tmp_path):
    # 密钥赋值形锚定行首（env 文件形态）——泄露样本须独占一行
    (tmp_path / 'config.md').write_text(f'{_SECRET}\n')
    r = _run(tmp_path)
    assert r.returncode == 1, '含密钥赋值形必须 exit 1'


def test_placeholder_nas_ip_passes(tmp_path):
    (tmp_path / 'ok.md').write_text('clone 源写成 <GITEA_URL>，端口 8280\n')
    r = _run(tmp_path)
    assert r.returncode == 0, f'占位符不应误报：{r.stdout}{r.stderr}'


def test_weak_default_secret_fails(tmp_path):
    """代码内嵌弱默认密钥（eng-review 外部声音发现 6）必须拦。"""
    weak = 'change-' + 'me-to-anything'  # 拆分书写防自匹配
    (tmp_path / 'config.py').write_text(f"import os\nJWT = os.getenv('JWT_SECRET', '{weak}')\n")
    r = _run(tmp_path)
    assert r.returncode == 1, '代码内嵌弱默认密钥必须 exit 1'


def test_every_wordlist_pattern_has_teeth(tmp_path):
    """词表衰退回归（eng-review 外部声音发现 7）：PATTERNS 每条模式都必须能拦住
    对应样本——模式被误删/误改时本测试变红。

    测试持有独立的样本清单（与脚本词表互抄没意义；样本匹配不上=脚本词表衰退）。
    样本字面值一律拆分书写，防本文件命中门禁。
    """
    import re

    src = SCRIPT.read_text()
    block = src.split('PATTERNS=(')[1].split(')')[0]
    patterns = []
    for line in block.splitlines():
        line = line.strip()
        if line.startswith("'"):
            patterns.append(line.strip("',").replace("''", ''))
    assert len(patterns) >= 10, f'词表条目异常缩减：{patterns}'

    samples = {
        '192\\.168\\.8\\.': '192' + '.168.8.1',
        '8\\.167': '10.0.' + '8.' + '167',
        ':40' + '10': 'router :' + '4010',
        'vol1' + '/': '/vol1' + '/1000/Docker',
        'fn-' + 'nas': 'ssh fn' + '-nas',
        'home' + 'lab': 'home' + 'lab.local',
        'C:/' + 'Users': 'C:/' + 'Users/Alfred',
        '/Users/' + 'alfred': '/Users/' + 'alfred/x',
        'OTC-' + 'Fund': 'OTC-' + 'Fund-Project',
        'tailf' + '898c8': 'tailf' + '898c8.ts.net',
        '[Cc]hange[-_]me': 'change' + '-me-default',
    }
    for pat in patterns:
        sample = samples.get(pat)
        assert sample is not None, f'词表新增模式 {pat!r} 缺少对应衰退样本——在 samples 补上'
        (tmp_path / 'probe.md').write_text(f'{sample}\n')
        r = _run(tmp_path)
        assert r.returncode == 1, f'模式 {pat!r} 未能拦截样本 {sample!r}（词表衰退）'
    assert set(samples) <= set(patterns), 'samples 有脚本中已不存在的模式（词表被删）'
