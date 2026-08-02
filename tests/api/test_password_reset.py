"""忘记密码 API 测试（Plan 08 / T4）。

铁律：HTTP 间串行；seed 用独立 Session（关闭后才 HTTP）；HTTP 后验证开新 Session；
绝不嵌套 Session；渠道 send 注入假插件（monkeypatch app.state.channels）。
"""

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.api.deps import get_session_dep
from app.api.security import verify_password
from app.config import reset_settings_cache
from app.infrastructure.crypto import CryptoService
from app.main import app
from app.models import NotificationChannel, PasswordResetCode, User
from app.notifications.base import ChannelStatus, SendResult

_KEY = Fernet.generate_key().decode()
_UNIFORM_MSG = '若账号存在，验证码已发送至你的邮箱'


class FakeEmailChannel:
    type = 'email'

    def __init__(self, fail=False):
        self.calls = []
        self._fail = fail

    def send(self, payload, config):
        self.calls.append((payload, config))
        return SendResult(ChannelStatus.FAILED if self._fail else ChannelStatus.SENT,
                          'smtp down' if self._fail else None)


@pytest.fixture(autouse=True)
def _mock_env(monkeypatch):
    reset_settings_cache()
    monkeypatch.setenv('JWT_SECRET', 't' * 32)
    monkeypatch.setenv('CRYPTO_KEY_V1', _KEY)
    monkeypatch.setenv('SCHEDULER_ENABLED', 'false')
    # 进程级 app 单例：每测试重置限流器，避免跨测试累计 hit 导致 4/5 次调用被 429。
    app.state.password_reset_limiter = None


@pytest.fixture
def fake_channel():
    fake = FakeEmailChannel()
    app.state.channels = {'email': fake}
    app.state.crypto = CryptoService({1: _KEY}, current_version=1)
    yield fake
    app.state.channels = {}


def _client(db_engine):
    def _override():
        with Session(db_engine) as s:
            yield s
    app.dependency_overrides[get_session_dep] = _override
    return TestClient(app)


def _seed_user(db_engine, username='alice', password='oldpass123', *, with_email=True):
    from app.api.security import hash_password
    crypto = CryptoService({1: _KEY}, current_version=1)
    with Session(db_engine) as s:
        u = User(username=username, password_hash=hash_password(password),
                 role='user', invite_code=username)
        s.add(u)
        s.commit()
        s.refresh(u)
        if with_email:
            ct = crypto.encrypt(json.dumps({'to': f'{username}@example.com'})).ciphertext
            s.add(NotificationChannel(user_id=u.id, type='email',
                                      config_json=json.dumps({'ct': ct}), key_version=1))
            s.commit()
        return u.id


def _seed_code(db_engine, user_id, code='123456', *, expired=False, attempts=0):
    now = datetime.now(UTC).replace(tzinfo=None)
    with Session(db_engine) as s:
        row = PasswordResetCode(
            user_id=user_id, code_hash=hashlib.sha256(code.encode()).hexdigest(),
            channel_type='email',
            expires_at=now + timedelta(minutes=-1 if expired else 15),
            attempts=attempts,
        )
        s.add(row)
        s.commit()


def test_forgot_sends_code(db_engine, fake_channel):
    _seed_user(db_engine)
    client = _client(db_engine)
    try:
        r = client.post('/auth/forgot-password', json={'username': 'alice'})
        assert r.status_code == 200
        assert r.json() == {'ok': True, 'message': _UNIFORM_MSG}
        assert len(fake_channel.calls) == 1
        body = fake_channel.calls[0][0].body
        assert re.search(r'验证码 \d{6}', body)
        with Session(db_engine) as s:
            assert s.exec(select(PasswordResetCode)).first() is not None
    finally:
        app.dependency_overrides.clear()


def test_forgot_unknown_user_identical_response(db_engine, fake_channel):
    client = _client(db_engine)
    try:
        r = client.post('/auth/forgot-password', json={'username': 'ghost'})
        assert r.status_code == 200
        assert r.json() == {'ok': True, 'message': _UNIFORM_MSG}  # 逐字同成功响应
        assert fake_channel.calls == []
    finally:
        app.dependency_overrides.clear()


def test_forgot_no_email_channel_identical_response(db_engine, fake_channel):
    _seed_user(db_engine, with_email=False)
    client = _client(db_engine)
    try:
        r = client.post('/auth/forgot-password', json={'username': 'alice'})
        assert r.status_code == 200
        assert r.json() == {'ok': True, 'message': _UNIFORM_MSG}
        assert fake_channel.calls == []
    finally:
        app.dependency_overrides.clear()


def test_forgot_smtp_not_configured_identical_response(db_engine):
    _seed_user(db_engine)
    app.state.channels = {}  # SMTP 未配 → 无 email 键
    app.state.crypto = CryptoService({1: _KEY}, current_version=1)
    client = _client(db_engine)
    try:
        r = client.post('/auth/forgot-password', json={'username': 'alice'})
        assert r.status_code == 200
        assert r.json() == {'ok': True, 'message': _UNIFORM_MSG}
        with Session(db_engine) as s:
            assert s.exec(select(PasswordResetCode)).first() is None
    finally:
        app.dependency_overrides.clear()


def test_forgot_send_failure_still_uniform_200(db_engine):
    fake = FakeEmailChannel(fail=True)
    app.state.channels = {'email': fake}
    app.state.crypto = CryptoService({1: _KEY}, current_version=1)
    _seed_user(db_engine)
    client = _client(db_engine)
    try:
        r = client.post('/auth/forgot-password', json={'username': 'alice'})
        assert r.status_code == 200
        assert r.json() == {'ok': True, 'message': _UNIFORM_MSG}
        with Session(db_engine) as s:
            row = s.exec(select(PasswordResetCode)).first()
            assert row.used_at is not None  # 码已作废
    finally:
        app.dependency_overrides.clear()


def test_reset_success_then_login_with_new_password(db_engine, fake_channel):
    uid = _seed_user(db_engine)
    _seed_code(db_engine, uid)
    client = _client(db_engine)
    try:
        r = client.post('/auth/reset-password', json={
            'username': 'alice', 'code': '123456', 'new_password': 'newpass123'})
        assert r.status_code == 200 and r.json() == {'ok': True}
        # HTTP 后开新 Session 验证（铁律 #3）
        with Session(db_engine) as s:
            u = s.get(User, uid)
            assert verify_password('newpass123', u.password_hash)
            row = s.exec(select(PasswordResetCode)).first()
            assert row.used_at is not None
        # 串行第二次 HTTP：新密码可登录（铁律 #1：HTTP 间串行即可）
        r2 = client.post('/auth/login', json={'username': 'alice', 'password': 'newpass123'})
        assert r2.status_code == 200
    finally:
        app.dependency_overrides.clear()


def test_reset_wrong_code_400_and_attempts_increment(db_engine, fake_channel):
    uid = _seed_user(db_engine)
    _seed_code(db_engine, uid)
    client = _client(db_engine)
    try:
        r = client.post('/auth/reset-password', json={
            'username': 'alice', 'code': '000000', 'new_password': 'newpass123'})
        assert r.status_code == 400
        assert r.json() == {'detail': '验证码错误或已过期'}
        with Session(db_engine) as s:
            row = s.exec(select(PasswordResetCode)).first()
            assert row.attempts == 1
            assert verify_password('oldpass123', s.get(User, uid).password_hash)
    finally:
        app.dependency_overrides.clear()


def test_reset_unknown_user_identical_400(db_engine, fake_channel):
    client = _client(db_engine)
    try:
        r = client.post('/auth/reset-password', json={
            'username': 'ghost', 'code': '123456', 'new_password': 'newpass123'})
        assert r.status_code == 400
        assert r.json() == {'detail': '验证码错误或已过期'}  # 逐字同码错误响应
    finally:
        app.dependency_overrides.clear()


def test_reset_expired_code_400(db_engine, fake_channel):
    uid = _seed_user(db_engine)
    _seed_code(db_engine, uid, expired=True)
    client = _client(db_engine)
    try:
        r = client.post('/auth/reset-password', json={
            'username': 'alice', 'code': '123456', 'new_password': 'newpass123'})
        assert r.status_code == 400
    finally:
        app.dependency_overrides.clear()


def test_reset_attempts_exhausted_400(db_engine, fake_channel):
    uid = _seed_user(db_engine)
    _seed_code(db_engine, uid, attempts=5)
    client = _client(db_engine)
    try:
        r = client.post('/auth/reset-password', json={
            'username': 'alice', 'code': '123456', 'new_password': 'newpass123'})
        assert r.status_code == 400
    finally:
        app.dependency_overrides.clear()


def test_reset_cross_site_origin_403(db_engine, fake_channel):
    uid = _seed_user(db_engine)
    _seed_code(db_engine, uid)
    client = _client(db_engine)
    try:
        r = client.post(
            '/auth/reset-password',
            json={'username': 'alice', 'code': '123456', 'new_password': 'newpass123'},
            headers={'Origin': 'https://evil.example.com'},
        )
        assert r.status_code == 403
        with Session(db_engine) as s:
            assert verify_password('oldpass123', s.get(User, uid).password_hash)
    finally:
        app.dependency_overrides.clear()


def test_reset_short_password_422(db_engine, fake_channel):
    client = _client(db_engine)
    try:
        r = client.post('/auth/reset-password', json={
            'username': 'alice', 'code': '123456', 'new_password': 'short'})
        assert r.status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_reset_non_digit_code_422(db_engine, fake_channel):
    client = _client(db_engine)
    try:
        r = client.post('/auth/reset-password', json={
            'username': 'alice', 'code': 'abcdef', 'new_password': 'newpass123'})
        assert r.status_code == 422
    finally:
        app.dependency_overrides.clear()
