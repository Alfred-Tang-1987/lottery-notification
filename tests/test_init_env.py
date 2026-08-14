"""init-env.sh 与 .env.example 发布形态测试（Plan 09 / T1）。

锁定 spec D1/D5/C2 修复：.env.example 密钥一律空值 + TLS 默认注释掉 + 无内网 IP；
init-env.sh 生成可启动的 .env（随机 JWT/Fernet key），且幂等护栏不覆盖已有 .env。
"""

import os
import subprocess
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / '.env.example'
SCRIPT = ROOT / 'scripts' / 'init-env.sh'


def _read_example() -> str:
    return EXAMPLE.read_text()


def test_example_secrets_are_empty():
    """JWT_SECRET / CRYPTO_KEY_V1 必须空值（公知默认 key 是发布阻断，spec D5）。"""
    for line in _read_example().splitlines():
        if line.startswith('JWT_SECRET='):
            assert line == 'JWT_SECRET=', f'JWT_SECRET 不得带默认值：{line!r}'
        if line.startswith('CRYPTO_KEY_V1='):
            assert line == 'CRYPTO_KEY_V1=', f'CRYPTO_KEY_V1 不得带默认值：{line!r}'


def test_example_tls_commented_out():
    """TLS 变量默认注释掉（否则裸路径启动走 SSL 找不到证书 crash-loop，spec D1）。"""
    for line in _read_example().splitlines():
        assert not line.startswith('TLS_CERT_FILE='), 'TLS_CERT_FILE 必须注释掉'
        assert not line.startswith('TLS_KEY_FILE='), 'TLS_KEY_FILE 必须注释掉'
    assert '# TLS_CERT_FILE=' in _read_example()
    assert '# TLS_KEY_FILE=' in _read_example()


def test_example_no_internal_ip():
    ip = '192' + '.168.8.'  # 拆分书写防门禁自匹配
    assert ip not in _read_example(), '.env.example 不得含内网 IP（spec C2）'


def _run_init(root: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, 'INIT_ENV_ROOT': str(root)}
    return subprocess.run(
        ['sh', str(SCRIPT)], capture_output=True, text=True, env=env, timeout=60,
    )


def test_init_env_generates_bootable_env(tmp_path):
    (tmp_path / '.env.example').write_text(EXAMPLE.read_text())
    r = _run_init(tmp_path)
    assert r.returncode == 0, f'{r.stdout}{r.stderr}'
    env_text = (tmp_path / '.env').read_text()
    jwt = next(l for l in env_text.splitlines() if l.startswith('JWT_SECRET='))
    crypto = next(l for l in env_text.splitlines() if l.startswith('CRYPTO_KEY_V1='))
    assert len(jwt.split('=', 1)[1]) >= 32, 'JWT_SECRET 须 ≥32 字符'
    key = crypto.split('=', 1)[1]
    assert len(key) == 44, 'CRYPTO_KEY_V1 须 44 字符'
    Fernet(key.encode())  # 构造不抛异常即真 Fernet key（与 config.py 校验一致）


def test_init_env_refuses_overwrite(tmp_path):
    (tmp_path / '.env.example').write_text(EXAMPLE.read_text())
    (tmp_path / '.env').write_text('JWT_SECRET=existing\n')
    r = _run_init(tmp_path)
    assert r.returncode == 1, '.env 已存在必须拒覆盖（防密钥被重建冲掉）'
    assert (tmp_path / '.env').read_text() == 'JWT_SECRET=existing\n'


def test_init_env_missing_tools_fails_loudly(tmp_path):
    """openssl/python3 缺失时非零退出且有明确提示（不静默生成半成品 .env）。

    PATH 置空后 command -v 检查必失败；用 /bin/sh 绝对路径绕过查找。
    """
    (tmp_path / '.env.example').write_text(EXAMPLE.read_text())
    env = {**os.environ, 'INIT_ENV_ROOT': str(tmp_path), 'PATH': '/nonexistent'}
    r = subprocess.run(
        ['/bin/sh', str(SCRIPT)], capture_output=True, text=True, env=env, timeout=60,
    )
    assert r.returncode != 0
    assert '缺少' in r.stderr


def test_init_env_missing_example_fails_loudly(tmp_path):
    """.env.example 缺失（非仓库根目录误跑）→ 报错退出，防呆提示不静默（eng-review Issue 6）。"""
    env = {**os.environ, 'INIT_ENV_ROOT': str(tmp_path)}  # tmp_path 里不放 .env.example
    r = subprocess.run(
        ['/bin/sh', str(SCRIPT)], capture_output=True, text=True, env=env, timeout=60,
    )
    assert r.returncode != 0
    assert '仓库根目录' in r.stderr
