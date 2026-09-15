"""build_admin_alert 共享模块测试（plan-11：从 auth.py 迁出供 scheduler 复用）。"""

from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet

from app.config import reset_settings_cache


def test_build_admin_alert_none_without_key(monkeypatch):
    """ADMIN_BARK_KEY 未配 → 返回 None（调用方据此只写日志不发）。"""
    reset_settings_cache()
    monkeypatch.delenv('ADMIN_BARK_KEY', raising=False)
    monkeypatch.setenv('JWT_SECRET', 'x' * 32)
    # CRYPTO_KEY_V1 必须合法 Fernet key（autoplan M3：'x'*44 解码 33 字节非法；
    # 仓库范式见 tests/api/test_admin.py:16）。
    monkeypatch.setenv('CRYPTO_KEY_V1', Fernet.generate_key().decode())
    from app.notifications.admin_alert import build_admin_alert

    assert build_admin_alert() is None


def test_build_admin_alert_sends_bark_with_key(monkeypatch):
    """配 key → 返回 callable，调用时经 BarkChannel 发送 title/body。"""
    reset_settings_cache()
    monkeypatch.setenv('JWT_SECRET', 'x' * 32)
    monkeypatch.setenv('CRYPTO_KEY_V1', Fernet.generate_key().decode())
    monkeypatch.setenv('ADMIN_BARK_KEY', 'test-key')
    from app.notifications import admin_alert as mod
    from app.notifications.base import ChannelStatus, SendResult

    bark = MagicMock()
    bark.send.return_value = SendResult(ChannelStatus.SENT)
    with patch.object(mod, 'BarkChannel', return_value=bark):
        alert = mod.build_admin_alert()
        alert('标题', '正文')
    bark.send.assert_called_once()
    payload = bark.send.call_args.args[0]
    assert payload.title == '标题' and payload.body == '正文'


def test_build_admin_alert_raises_on_failed_send(monkeypatch):
    """bark 返回 FAILED（非抛异常）也必须 raise（autoplan M2 送达契约）——

    BarkChannel.send 吞掉一切失败返回 SendResult（bark.py:49-50），若忽略返回值，
    评估器「送达成功才转移」退化为「尝试即转移」，DNS 事故场景告警永不重试。
    """
    reset_settings_cache()
    monkeypatch.setenv('JWT_SECRET', 'x' * 32)
    monkeypatch.setenv('CRYPTO_KEY_V1', Fernet.generate_key().decode())
    monkeypatch.setenv('ADMIN_BARK_KEY', 'test-key')
    from app.notifications import admin_alert as mod
    from app.notifications.base import ChannelStatus, SendResult

    bark = MagicMock()
    bark.send.return_value = SendResult(ChannelStatus.FAILED, error='bark code 400')
    with patch.object(mod, 'BarkChannel', return_value=bark):
        alert = mod.build_admin_alert()
        with pytest.raises(RuntimeError):
            alert('标题', '正文')


def test_build_admin_alert_uses_settings_url(monkeypatch):
    """eng-voice M8.6：Bark config 的 url 必须来自 settings.admin_bark_url——

    只断言默认值存在管不住「退回硬编码 https://api.day.app」的回归（F20 同源初衷）。
    """
    reset_settings_cache()
    monkeypatch.setenv('JWT_SECRET', 'x' * 32)
    monkeypatch.setenv('CRYPTO_KEY_V1', Fernet.generate_key().decode())
    monkeypatch.setenv('ADMIN_BARK_KEY', 'test-key')
    monkeypatch.setenv('ADMIN_BARK_URL', 'https://bark.nas.local:8443')
    from app.notifications import admin_alert as mod
    from app.notifications.base import ChannelStatus, SendResult

    bark = MagicMock()
    bark.send.return_value = SendResult(ChannelStatus.SENT)
    with patch.object(mod, 'BarkChannel', return_value=bark):
        mod.build_admin_alert()('t', 'b')
    config = bark.send.call_args.args[1]
    assert config['url'] == 'https://bark.nas.local:8443'
