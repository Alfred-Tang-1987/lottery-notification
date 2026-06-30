"""Plan 05 / T1：安全工具测试（passlib 哈希 + PyJWT session token + CSRF token）。

Spec §4.3 D2:A —— httpOnly cookie + JWT；本 task 落实底层工具：
- password hash/verify（bcrypt，明文不可逆）
- JWT session token 签发/校验/过期拒绝（HS256，密钥来自 settings.jwt_secret）
- CSRF token 随机性（double-submit 用，长度 >= 32）

settings 经 get_settings() 惰性单例读取 env；测试用 monkeypatch.setenv +
reset_settings_cache() 注入 JWT_SECRET 与 CRYPTO_KEY_V1（两者均为 Settings 必填）。
"""

from cryptography.fernet import Fernet

from app.api.security import (
    create_session_token,
    csrf_tokens_match,
    decode_session_token,
    generate_csrf_token,
    hash_password,
    verify_password,
)
from app.config import get_settings, reset_settings_cache


def _set_required_env(monkeypatch):
    """注入 Settings 必填的 env（JWT 32 字符 + 真实 Fernet key）。"""
    monkeypatch.setenv('JWT_SECRET', 'a' * 32)
    monkeypatch.setenv('CRYPTO_KEY_V1', Fernet.generate_key().decode())
    reset_settings_cache()
    return get_settings()


def test_password_hash_roundtrip():
    h = hash_password('secret123')
    assert h != 'secret123'
    assert verify_password('secret123', h)
    assert not verify_password('wrong', h)


def test_jwt_token_roundtrip(monkeypatch):
    _set_required_env(monkeypatch)
    token = create_session_token(user_id=42, role='user')
    payload = decode_session_token(token)
    assert payload is not None
    assert payload['sub'] == '42' and payload['role'] == 'user'


def test_jwt_expired_rejected(monkeypatch):
    _set_required_env(monkeypatch)
    token = create_session_token(user_id=1, role='user', expires_minutes=-1)
    assert decode_session_token(token) is None


def test_jwt_tampered_rejected(monkeypatch):
    """签名被篡改 → 校验失败返回 None。"""
    _set_required_env(monkeypatch)
    token = create_session_token(user_id=1, role='user')
    tampered = token[:-4] + ('aaaa' if token[-4:] != 'aaaa' else 'bbbb')
    assert decode_session_token(tampered) is None


def test_csrf_token_random():
    a = generate_csrf_token()
    b = generate_csrf_token()
    assert a != b and len(a) >= 32


def test_csrf_tokens_match_rules():
    """double-submit 一致性判定：两者非空且相等才放行。"""
    assert csrf_tokens_match('t', 't') is True
    assert csrf_tokens_match('same-token', 'same-token') is True
    # 任一缺失 → 拒绝（防 attacker 只塞 header 不带 cookie）
    assert csrf_tokens_match(None, 't') is False
    assert csrf_tokens_match('t', None) is False
    assert csrf_tokens_match('', 't') is False
    assert csrf_tokens_match('t', '') is False
    # 不一致 → 拒绝（防伪造）
    assert csrf_tokens_match('cookie-val', 'header-val') is False
