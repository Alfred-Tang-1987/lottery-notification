"""渠道 config 解密公共实现（Plan 08 / T0）。

从 Notifier._decrypt_config 抽出，供 Notifier 与 PasswordResetService 共用——
明文拒绝 / 解密失败 WARNING / key_version 失配处理只有一份实现。
契约：只接受 {"ct": ...} 格式；任何失败返回 None 并记 WARNING（绝不静默）；
明文 config 绝不入日志。
"""

import json
import logging

from app.infrastructure.crypto import CryptoService
from app.models import NotificationChannel

logger = logging.getLogger(__name__)


def decrypt_channel_config(ch_row: NotificationChannel, crypto: CryptoService) -> dict | None:
    """解密渠道配置。只接受 {"ct": ...} 格式，拒绝明文（spec §8.1）。

    解密失败（Fernet key 失配 / 密文损坏 / key_version 轮换错位）记 WARNING 返回 None。
    """
    raw = json.loads(ch_row.config_json)
    if 'ct' not in raw:
        logger.warning(
            'notify_decrypt_skip_plaintext user_id=%s channel_id=%s type=%s '
            '（spec §8.1 拒绝明文，疑似旧数据/手改）',
            ch_row.user_id,
            ch_row.id,
            ch_row.type,
        )
        return None
    try:
        blob = (ch_row.key_version, raw['ct'])
        plaintext = crypto.decrypt(blob)
        return json.loads(plaintext)
    except Exception:
        logger.warning(
            'notify_decrypt_failed user_id=%s channel_id=%s type=%s key_version=%s '
            '（密文损坏 / key_version 失配 / Fernet key 轮换错位，该渠道将跳过）',
            ch_row.user_id,
            ch_row.id,
            ch_row.type,
            ch_row.key_version,
            exc_info=True,
        )
        return None
