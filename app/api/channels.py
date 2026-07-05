"""Plan 05 / T5 + Plan 06 / T6e：渠道配置 + 推送策略规则 + DND + 模板预览 API。

每用户配置自己的推送渠道（Bark/飞书/邮箱存 webhook+key，邮箱存收件地址），config 必须
**加密存储**（Fernet，config_json = {"ct": "<密文>"} + key_version 单列）。明文绝不
落库或入日志（spec §8.1 / §10 安全要求）。

T6e 新增：
- 每彩种推送规则 NotificationRule（every/win_only + timing）。
- 用户 DND 配置持久化到 users.dnd_json。
- 模板预览 GET /channels/templates 返回路径 A/B 示例文案。
"""

import json
import logging
import re

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlmodel import Session, select

from app.api.deps import current_user, get_session_dep, verify_csrf
from app.config import get_settings
from app.infrastructure.crypto import CryptoService
from app.models import NotificationChannel, NotificationRule, User
from app.notifications.templates import build_path_a, build_path_b

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

_ALLOWED_STRATEGIES = {'every', 'win_only'}


class ChannelOut(BaseModel):
    """渠道响应模型（id/type/config/enabled），OpenAPI schema 与返回结构一致。"""

    id: int
    type: str
    config: dict
    enabled: bool


class RuleOut(BaseModel):
    id: int
    lottery_code: str
    strategy: str
    timing: str | None


class RuleIn(BaseModel):
    lottery_code: str = Field(min_length=1, max_length=8)
    strategy: str = Field(default='every', max_length=8)
    timing: str | None = None

    @field_validator('strategy')
    @classmethod
    def _check_strategy(cls, v: str) -> str:
        if v not in _ALLOWED_STRATEGIES:
            raise ValueError(f'策略必须是 {", ".join(sorted(_ALLOWED_STRATEGIES))}')
        return v


class DndIn(BaseModel):
    enabled: bool
    start: str = Field(pattern=r'^\d{2}:\d{2}$')
    end: str = Field(pattern=r'^\d{2}:\d{2}$')

    @field_validator('start', 'end')
    @classmethod
    def _check_time(cls, v: str) -> str:
        hh, mm = v.split(':')
        if not (0 <= int(hh) < 24 and 0 <= int(mm) < 60):
            raise ValueError('时间格式无效')
        return v


class DndOut(BaseModel):
    enabled: bool
    start: str
    end: str


class TemplatePreview(BaseModel):
    path_a: dict
    path_b: dict


def _validate_config(channel_type: str, config: dict) -> None:
    """系统边界对 config 做按类型的最小结构校验（spec §10 fail-fast）。"""
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
    """新增渠道配置：加密 config 后落库（{"ct": <密文>} + key_version）。"""
    if body.type not in _ALLOWED_TYPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f'不支持的渠道类型: {body.type}')
    _validate_config(body.type, body.config)

    crypto = _crypto()
    plaintext = json.dumps(body.config, ensure_ascii=False)
    blob = crypto.encrypt(plaintext)
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
    """列出当前用户的全部渠道：解密 config 回明文。"""
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
            logger.warning(
                'channel_decrypt_failed user_id=%s channel_id=%s type=%s key_version=%s',
                user.id,
                ch.id,
                ch.type,
                ch.key_version,
                exc_info=True,
            )
            continue
    return out


# ---------------------------------------------------------------------------
# T6e: notification rules
# ---------------------------------------------------------------------------


@router.get('/rules', response_model=list[RuleOut])
def list_rules(
    user: User = Depends(current_user),
    session: Session = Depends(get_session_dep),
) -> list[RuleOut]:
    """列出当前用户的全部推送规则。"""
    rows = session.exec(
        select(NotificationRule).where(NotificationRule.user_id == user.id)
    ).all()
    return [
        RuleOut(
            id=r.id,
            lottery_code=r.lottery_code,
            strategy=r.strategy,
            timing=r.timing,
        )
        for r in rows
    ]


@router.put('/rules', response_model=RuleOut)
def upsert_rule(
    body: RuleIn,
    user: User = Depends(current_user),
    session: Session = Depends(get_session_dep),
    _csrf_ok: None = Depends(verify_csrf),
) -> RuleOut:
    """新增/更新每彩种推送规则。同一 (user, lottery_code) 只保留一行。"""
    existing = session.exec(
        select(NotificationRule).where(
            NotificationRule.user_id == user.id,
            NotificationRule.lottery_code == body.lottery_code,
        )
    ).first()
    if existing is None:
        existing = NotificationRule(user_id=user.id, lottery_code=body.lottery_code)
        session.add(existing)
    existing.strategy = body.strategy
    existing.timing = body.timing
    session.commit()
    session.refresh(existing)
    return RuleOut(
        id=existing.id,
        lottery_code=existing.lottery_code,
        strategy=existing.strategy,
        timing=existing.timing,
    )


@router.delete('/rules/{rule_id}', response_model=dict[str, bool])
def delete_rule(
    rule_id: int,
    user: User = Depends(current_user),
    session: Session = Depends(get_session_dep),
    _csrf_ok: None = Depends(verify_csrf),
) -> dict[str, bool]:
    """删除当前用户的某条推送规则。"""
    rule = session.get(NotificationRule, rule_id)
    if rule is None or rule.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, '规则不存在')
    session.delete(rule)
    session.commit()
    return {'ok': True}


# ---------------------------------------------------------------------------
# T6e: DND
# ---------------------------------------------------------------------------


@router.get('/dnd', response_model=DndOut)
def get_dnd(
    user: User = Depends(current_user),
) -> DndOut:
    """读取当前用户的 DND 配置。"""
    dnd = _parse_dnd(user.dnd_json)
    return DndOut(enabled=dnd['enabled'], start=dnd['start'], end=dnd['end'])


@router.post('/dnd', response_model=DndOut)
def save_dnd(
    body: DndIn,
    user: User = Depends(current_user),
    session: Session = Depends(get_session_dep),
    _csrf_ok: None = Depends(verify_csrf),
) -> DndOut:
    """保存当前用户的 DND 配置。"""
    user.dnd_json = json.dumps(
        {'enabled': body.enabled, 'start': body.start, 'end': body.end},
        ensure_ascii=False,
    )
    session.add(user)
    session.commit()
    return DndOut(enabled=body.enabled, start=body.start, end=body.end)


def _parse_dnd(raw: str | None) -> dict:
    """解析用户 DND 配置；损坏/缺失时回退默认。"""
    default = {'enabled': False, 'start': '22:00', 'end': '07:00'}
    if not raw:
        return default
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict) and 'enabled' in parsed and 'start' in parsed and 'end' in parsed:
            return parsed
    except Exception:
        pass
    return default


# ---------------------------------------------------------------------------
# T6e: template preview
# ---------------------------------------------------------------------------


@router.get('/templates', response_model=TemplatePreview)
def template_preview(
    user: User = Depends(current_user),
) -> TemplatePreview:
    """返回路径 A/B 推送模板示例文案。"""
    path_a = build_path_a(
        lottery_name='双色球',
        draw_no='2026150',
        tier_name='二等奖',
        tier=2,
        amount=None,
    )
    path_b = build_path_b(
        date_str='2026-01-01',
        total=3,
        wins=1,
        win_details=[('双色球', '二等奖', None)],
        loses=2,
    )
    return TemplatePreview(
        path_a={'title': path_a.title, 'body': path_a.body},
        path_b={'title': path_b.title, 'body': path_b.body},
    )
