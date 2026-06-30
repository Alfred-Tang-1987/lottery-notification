"""Plan 05 / T1：安全工具（passlib 哈希 + PyJWT session token + CSRF token）。

Spec §4.3 D2:A —— httpOnly cookie + JWT 的底层原语。

设计要点：
- 密码用 bcrypt，明文不可逆；hash/verify 一一对应。
  直接使用 ``bcrypt`` 包（passlib 1.7.4 与 bcrypt 5.x 不兼容——版本探测失败导致
  hash 路径报错；bcrypt 本身稳定可用，且已作为 ``passlib[bcrypt]`` 的传递依赖安装）。
- JWT 用 HS256，签名密钥来自 ``get_settings().jwt_secret``（惰性读取，便于测试注入 env）。
  ``sub`` 存 str(user_id)、``role`` 存角色、``iat``/``exp`` 用 epoch 秒。
  过期/签名错误统一返回 None（调用方按 None 判失效）。
- CSRF token 用 ``secrets.token_urlsafe(32)`` 生成（>= 43 字符 url-safe），供 double-submit。
- ``COOKIE_NAME``/``CSRF_HEADER`` 为常量，供 deps/auth 统一引用。
"""

import secrets
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

from app.config import get_settings

# bcrypt 限制明文 <= 72 字节；超出截断（与 passlib/许多实现一致，避免 ValueError）。
_BCRYPT_MAX_BYTES = 72
COOKIE_NAME = 'session'
CSRF_HEADER = 'X-CSRF-Token'


def _bcrypt_bytes(plain: str) -> bytes:
    """明文按 bcrypt 上限（72 字节）截断并编码；hash/verify 须共用同一规则。"""
    return plain.encode('utf-8')[:_BCRYPT_MAX_BYTES]


def hash_password(plain: str) -> str:
    """bcrypt 哈希明文密码，返回 ``$2b$`` 开头的哈希字符串。"""
    return bcrypt.hashpw(_bcrypt_bytes(plain), bcrypt.gensalt()).decode('utf-8')


def verify_password(plain: str, hashed: str) -> bool:
    """校验明文与 bcrypt 哈希是否匹配。"""
    try:
        return bcrypt.checkpw(_bcrypt_bytes(plain), hashed.encode('utf-8'))
    except ValueError:
        # 哈希格式非法（损坏/非 bcrypt）→ 视为不匹配，不向上抛。
        return False


def create_session_token(*, user_id: int, role: str, expires_minutes: int = 60 * 24 * 7) -> str:
    """签发 JWT session token（HS256），默认 7 天过期。"""
    now = datetime.now(UTC)
    exp = int((now + timedelta(minutes=expires_minutes)).timestamp())
    payload = {
        'sub': str(user_id),
        'role': role,
        'iat': int(now.timestamp()),
        'exp': exp,
    }
    return jwt.encode(payload, get_settings().jwt_secret, algorithm='HS256')


def decode_session_token(token: str) -> dict | None:
    """校验并解码 JWT；过期/签名错误/格式错误均返回 None。"""
    try:
        return jwt.decode(token, get_settings().jwt_secret, algorithms=['HS256'])
    except jwt.PyJWTError:
        return None


def generate_csrf_token() -> str:
    """生成随机 CSRF token（url-safe，>= 32 字符），供 double-submit。"""
    return secrets.token_urlsafe(32)


def csrf_tokens_match(cookie_token: str | None, header_token: str | None) -> bool:
    """判断 double-submit 的 cookie token 与 header token 是否一致且非空。

    抽成纯函数便于单测：两者均非空且字符串相等才放行；任一缺失/不一致即拒绝。
    不在此处抛 HTTP 异常——保持 security 层零 web 框架依赖（仅 FastAPI 的
    HTTPException 由调用点 deps/auth 触发），与领域/工具层职责一致。
    """
    if not cookie_token or not header_token:
        return False
    return cookie_token == header_token
