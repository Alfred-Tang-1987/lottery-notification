"""decrypt_channel_config 公共解密函数测试（Plan 08 / T0）。

从 Notifier._decrypt_config 抽出的公共实现：明文拒绝（INFO 级别 WARNING tag）、
密文损坏 WARNING + None、成功返回 dict。明文 config 绝不入日志。
"""

import json
import logging

from cryptography.fernet import Fernet

from app.infrastructure.crypto import CryptoService
from app.models import NotificationChannel
from app.notifications._decrypt import decrypt_channel_config

_KEY = Fernet.generate_key().decode()


def _crypto() -> CryptoService:
    return CryptoService({1: _KEY}, current_version=1)


def _ch_row(config_json: str, key_version: int = 1) -> NotificationChannel:
    return NotificationChannel(
        user_id=1, type='email', config_json=config_json, key_version=key_version,
    )


def test_decrypt_success_returns_dict():
    crypto = _crypto()
    ct = crypto.encrypt(json.dumps({'to': 'a@b.com'})).ciphertext
    row = _ch_row(json.dumps({'ct': ct}))
    assert decrypt_channel_config(row, crypto) == {'to': 'a@b.com'}


def test_decrypt_plaintext_rejected(caplog):
    row = _ch_row(json.dumps({'to': 'a@b.com'}))  # 无 'ct' 键 → 明文拒绝
    with caplog.at_level(logging.WARNING):
        assert decrypt_channel_config(row, _crypto()) is None
    assert 'notify_decrypt_skip_plaintext' in caplog.text
    assert 'a@b.com' not in caplog.text  # 明文 config 绝不入日志


def test_decrypt_broken_ciphertext_returns_none(caplog):
    row = _ch_row(json.dumps({'ct': 'not-a-valid-fernet!!!'}))
    with caplog.at_level(logging.WARNING):
        assert decrypt_channel_config(row, _crypto()) is None
    assert 'notify_decrypt_failed' in caplog.text


def test_decrypt_wrong_key_version_returns_none(caplog):
    crypto = _crypto()
    ct = crypto.encrypt(json.dumps({'to': 'a@b.com'})).ciphertext
    row = _ch_row(json.dumps({'ct': ct}), key_version=99)  # 未知版本
    with caplog.at_level(logging.WARNING):
        assert decrypt_channel_config(row, crypto) is None
    assert 'notify_decrypt_failed' in caplog.text
