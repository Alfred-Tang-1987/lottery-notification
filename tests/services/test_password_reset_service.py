# tests/services/test_password_reset_service.py
"""PasswordResetService.request_reset 测试（Plan 08 / T2）。

纪律：所有 DB 操作用 with Session 串行（先 seed 关 → 调 service → 再开 Session 验证），
绝不嵌套。fake_send 假插件不打网络。
"""

import json
from datetime import timedelta

import pytest
from cryptography.fernet import Fernet
from sqlmodel import Session, select

from app.infrastructure.crypto import CryptoService
from app.models import NotificationChannel, PasswordResetCode, User
from app.notifications.base import ChannelStatus, SendResult
from app.services.password_reset_service import (
    PasswordResetService,
    RateLimited,
    RateLimiter,
)

_KEY = Fernet.generate_key().decode()


class FakeEmailChannel:
    type = 'email'

    def __init__(self, fail: bool = False):
        self.calls = []
        self._fail = fail

    def send(self, payload, config):
        self.calls.append((payload, config))
        if self._fail:
            return SendResult(ChannelStatus.FAILED, 'smtp down')
        return SendResult(ChannelStatus.SENT)


def _crypto() -> CryptoService:
    return CryptoService({1: _KEY}, current_version=1)


def _seed_user(db_engine, username='alice', *, with_email=True) -> int:
    """独立 Session 建用户（+可选 email 渠道，真 Fernet 加密 config）。返回 user_id。"""
    crypto = _crypto()
    with Session(db_engine) as s:
        u = User(username=username, password_hash='x', role='user', invite_code=username)
        s.add(u)
        s.commit()
        s.refresh(u)
        if with_email:
            ct = crypto.encrypt(json.dumps({'to': f'{username}@example.com'})).ciphertext
            s.add(NotificationChannel(
                user_id=u.id, type='email',
                config_json=json.dumps({'ct': ct}), key_version=1,
            ))
            s.commit()
        return u.id


def _service(db_engine, fake, **kw) -> PasswordResetService:
    return PasswordResetService(
        db_engine, email_channel=fake, crypto=_crypto(), **kw,
    )


def _request(db_engine, svc, username='alice'):
    """模拟 API 层：注入 session 调 request_reset（Session 由调用方持有并关闭）。"""
    with Session(db_engine) as s:
        svc.request_reset(username, client_ip='10.0.0.1', session=s)


def test_request_sends_code_and_stores_hash(db_engine):
    uid = _seed_user(db_engine)
    fake = FakeEmailChannel()
    _request(db_engine, _service(db_engine, fake))
    assert len(fake.calls) == 1
    payload, config = fake.calls[0]
    assert config == {'to': 'alice@example.com'}
    import re
    m = re.search(r'验证码 (\d{6})', payload.body)
    assert m, f'body 应含 6 位验证码: {payload.body!r}'
    with Session(db_engine) as s:
        row = s.exec(select(PasswordResetCode)).first()
        assert row is not None and row.user_id == uid
        assert row.code_hash != m.group(1)  # hash 非明文
        import hashlib
        assert row.code_hash == hashlib.sha256(m.group(1).encode()).hexdigest()
        assert row.channel_type == 'email'
        assert row.used_at is None
        # expires ≈ created + 15min（naive UTC）
        delta = row.expires_at - row.created_at
        assert timedelta(minutes=14) < delta < timedelta(minutes=16)


def test_request_unknown_user_silent(db_engine):
    fake = FakeEmailChannel()
    _request(db_engine, _service(db_engine, fake), username='ghost')
    assert fake.calls == []
    with Session(db_engine) as s:
        assert s.exec(select(PasswordResetCode)).first() is None


def test_request_no_email_channel_silent(db_engine):
    _seed_user(db_engine, with_email=False)
    fake = FakeEmailChannel()
    _request(db_engine, _service(db_engine, fake))
    assert fake.calls == []
    with Session(db_engine) as s:
        assert s.exec(select(PasswordResetCode)).first() is None


def test_request_smtp_not_configured_silent(db_engine):
    """email_channel=None（SMTP 未配）→ 统一静默，不写码。"""
    _seed_user(db_engine)
    _request(db_engine, _service(db_engine, None))
    with Session(db_engine) as s:
        assert s.exec(select(PasswordResetCode)).first() is None


def test_request_resend_within_60s_skipped(db_engine):
    """同用户 60s 内已有活跃码 → 静默跳过（不发新码不顶废旧码）。

    用注入的宽松 rate_limiter 隔离 IP 限流干扰（默认 3 次/min 够 2 次调用，
    但显式注入避免与未来用例顺序耦合）。
    """
    _seed_user(db_engine)
    fake = FakeEmailChannel()
    svc = _service(db_engine, fake, rate_limiter=RateLimiter(max_per_minute=10))
    _request(db_engine, svc)
    _request(db_engine, svc)  # 60s 内第二次 → 静默跳过
    assert len(fake.calls) == 1
    with Session(db_engine) as s:
        assert len(s.exec(select(PasswordResetCode)).all()) == 1


def test_request_new_code_invalidates_old(db_engine):
    """超过重发窗（resend_interval=0）再请求：旧码作废，新码唯一活跃。"""
    _seed_user(db_engine)
    fake = FakeEmailChannel()
    svc = _service(db_engine, fake, resend_interval_seconds=0,
                   rate_limiter=RateLimiter(max_per_minute=10))
    _request(db_engine, svc)
    _request(db_engine, svc)
    assert len(fake.calls) == 2
    with Session(db_engine) as s:
        rows = s.exec(select(PasswordResetCode)).all()
        assert len(rows) == 2
        active = [r for r in rows if r.used_at is None]
        assert len(active) == 1


def test_request_send_failure_marks_code_used(db_engine):
    _seed_user(db_engine)
    fake = FakeEmailChannel(fail=True)
    alerts = []
    svc = _service(
        db_engine, fake, send_retries=0,
        admin_alert=lambda title, body: alerts.append((title, body)),
    )
    _request(db_engine, svc)
    assert len(fake.calls) == 1
    with Session(db_engine) as s:
        row = s.exec(select(PasswordResetCode)).first()
        assert row is not None and row.used_at is not None  # 码作废
    assert len(alerts) == 1  # send_retries=0 直接失败 → admin 告警


def test_rate_limiter_window():
    rl = RateLimiter(max_per_minute=3)
    assert rl.hit('1.1.1.1') is True
    assert rl.hit('1.1.1.1') is True
    assert rl.hit('1.1.1.1') is True
    assert rl.hit('1.1.1.1') is False  # 第 4 次超限
    assert rl.hit('2.2.2.2') is True   # 不同 key 互不影响


def test_request_ip_rate_limited_raises(db_engine):
    _seed_user(db_engine)
    fake = FakeEmailChannel()
    svc = _service(db_engine, fake, rate_limiter=RateLimiter(max_per_minute=2),
                   resend_interval_seconds=0)  # 关闭重发窗干扰，只验限流
    _request(db_engine, svc)
    _request(db_engine, svc)
    with pytest.raises(RateLimited):
        _request(db_engine, svc)  # 第 3 次超限（窗口内 2 次已记）
    assert len(fake.calls) == 2  # 超限次未发码
