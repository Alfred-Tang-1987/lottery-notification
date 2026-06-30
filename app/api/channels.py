"""Plan 05 / T5：渠道配置加密写入/读取 API（spec §8.1）。

每用户配置自己的推送渠道（Bark/飞书存 webhook+key，邮箱存收件地址），config 必须
**加密存储**（Fernet，config_json = {"ct": "<密文>"} + key_version 单列）。明文绝不
落库或入日志（spec §8.1 / §10 安全要求）。

写入路径必须与 Plan 04 Notifier._decrypt_config 的读取路径严格对齐——Notifier 读
``raw['ct']``（明文拒绝）+ ``ch_row.key_version``，故本模块写入时：

1. ``config_json`` 必须是 ``{"ct": "<Fernet 密文>"}``（不含明文字段）。
2. ``key_version`` 必须记 ``CryptoService.encrypt`` 返回的真实版本号（轮换后逐条
   re-encrypt 时据此选解密 key；若记错，推送时 Fernet 解密失败 → 该渠道永不推送 →
   「中奖静默漏通知」spec §10）。

IDOR（spec §6.3）：所有读写经 ``current_user`` 拿 user_id，SQLModel 查询一律
``WHERE user_id == user.id``，用户只能看到/改自己的渠道。
"""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.api.deps import current_user, get_session_dep, verify_csrf
from app.config import get_settings
from app.infrastructure.crypto import CryptoService
from app.models import NotificationChannel, User

logger = logging.getLogger(__name__)

router = APIRouter(prefix='/channels', tags=['channels'])

# 渠道类型白名单（与 NotificationChannel.type 注释 + Notifier 已实现渠道一致）。
_ALLOWED_TYPES = {'bark', 'feishu', 'email'}

# 各渠道 config 必备字段（对齐 BarkChannel/FeishuChannel/EmailChannel.send 取值）。
# 边界校验、快速失败（spec §10）：坏配置挡在 400，避免落库成不可用渠道→推送阶段静默漏通知。
_REQUIRED_CONFIG_KEYS: dict[str, set[str]] = {
    'bark': {'key'},  # url 亦常用，BarkChannel.send 取 url+key；但 url 有服务端默认，仅强制 key。
    'feishu': {'webhook'},  # secret 可选（签名校验）。
    'email': {'address'},
}


class ChannelOut(BaseModel):
    """渠道响应模型（id/type/config/enabled），OpenAPI schema 与返回结构一致。"""

    id: int
    type: str
    config: dict
    enabled: bool


def _validate_config(channel_type: str, config: dict) -> None:
    """系统边界对 config 做按类型的最小结构校验（spec §10 fail-fast）。

    缺必要键即 raise 400——把错误配置挡在落库前，而非存成不可用渠道（后者在推送阶段
    会因 Notifier/Channel.send 取 config[key] 失败变成「静默漏通知」）。空对象 {} 同样拒绝。
    必备字段集对齐各 Channel.send 实际取值的 key（见 _REQUIRED_CONFIG_KEYS）。
    """
    if not isinstance(config, dict) or not config:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, '渠道 config 不能为空')
    required = _REQUIRED_CONFIG_KEYS.get(channel_type, set())
    missing = [k for k in required if not config.get(k)]
    if missing:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f'渠道 {channel_type} config 缺少必要字段: {", ".join(missing)}',
        )


def _crypto() -> CryptoService:
    """每次请求构造 CryptoService（轻量；读当前 settings，便于 key 轮换后即时生效）。"""
    s = get_settings()
    return CryptoService(s.crypto_keys, s.current_key_version)


class ChannelIn(BaseModel):
    type: str = Field(min_length=1, max_length=8)
    config: dict


@router.post('', response_model=ChannelOut, status_code=201)
def save_channel(
    body: ChannelIn,
    user: User = Depends(current_user),
    session: Session = Depends(get_session_dep),
    _csrf_ok: None = Depends(verify_csrf),
) -> ChannelOut:
    """新增渠道配置：加密 config 后落库（{"ct": <密文>} + key_version）。

    已登录 state-changing 路由——挂 verify_csrf 强制 double-submit（spec §4.3），
    与 /auth/logout 模式一致；CSRF 伪造可让攻击者把用户 webhook 改指向攻击者端点
    劫持中奖通知、或污染配置。GET /channels 只读不挂。
    """
    if body.type not in _ALLOWED_TYPES:
        # 早失败：未知渠道类型不浪费一次加密/落库。
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f'不支持的渠道类型: {body.type}')

    # 边界校验（spec §10）：必备字段缺失即 400，避免落库成不可用渠道→推送静默漏通知。
    _validate_config(body.type, body.config)

    crypto = _crypto()
    plaintext = json.dumps(body.config, ensure_ascii=False)
    blob = crypto.encrypt(plaintext)
    # config_json 严格 {"ct": ...}（对齐 Notifier._decrypt_config：raw['ct']）。
    stored = json.dumps({'ct': blob.ciphertext}, ensure_ascii=False)
    channel = NotificationChannel(
        user_id=user.id,
        type=body.type,
        config_json=stored,
        enabled=True,
        key_version=blob.version,
    )
    session.add(channel)
    session.commit()
    session.refresh(channel)
    return ChannelOut(id=channel.id, type=channel.type, config=body.config, enabled=channel.enabled)


@router.get('', response_model=list[ChannelOut])
def list_channels(
    user: User = Depends(current_user),
    session: Session = Depends(get_session_dep),
) -> list[ChannelOut]:
    """列出当前用户的全部渠道：解密 config 回明文（对齐 Notifier._decrypt_config）。

    逐行的 JSON 解析、结构校验、解密统一包进同一 try/except——单行坏数据（手改 DB /
    非 JSON config_json / 密文损坏 / key_version 失配 / Fernet key 轮换错位）记 WARNING
    后 continue，健康行照常返回，绝不让单条坏数据让整张渠道列表 500（spec §10 可靠性）。
    """
    crypto = _crypto()
    rows = session.exec(
        select(NotificationChannel).where(NotificationChannel.user_id == user.id)
    ).all()
    out: list[ChannelOut] = []
    for ch in rows:
        try:
            raw = json.loads(ch.config_json)
            ct = raw.get('ct')
            if ct is None:
                # 明文/异常结构：与 Notifier 一致地跳过（不解密、不爆栈），不泄露给前端。
                continue
            plaintext = crypto.decrypt((ch.key_version, ct))
            out.append(
                ChannelOut(
                    id=ch.id,
                    type=ch.type,
                    config=json.loads(plaintext),
                    enabled=ch.enabled,
                )
            )
        except Exception:
            # 对齐 Notifier._decrypt_config（notifier.py:277-287）：JSON 解析失败 / 密文损坏
            # / key_version 失配 / Fernet key 轮换错位时跳过该行，记 WARNING 含 channel 标识
            # 便于运维定位，而非让单条坏数据让整张渠道列表 500——否则用户无法加载列表、
            # 运维只能从前端报错察觉。
            logger.warning(
                'channel_decrypt_failed user_id=%s channel_id=%s type=%s key_version=%s '
                '（config_json 非法 / 密文损坏 / key_version 失配 / Fernet key 轮换错位，'
                '该渠道已从列表跳过）',
                user.id,
                ch.id,
                ch.type,
                ch.key_version,
                exc_info=True,
            )
            continue
    return out
