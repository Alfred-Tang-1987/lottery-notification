"""Plan 05 / T5 + Plan 06 / T6e：渠道配置 + 推送策略规则 + DND + 模板预览 + 用户偏好 + 用户全局通知设置 API。

每用户配置自己的推送渠道（Bark/飞书/邮箱存 webhook+key，邮箱存收件地址），config 必须
**加密存储**（Fernet，config_json = {"ct": "<密文>"} + key_version 单列）。明文绝不
落库或入日志（spec §8.1 / §10 安全要求）。

T6e 新增：
- 每彩种推送规则 NotificationRule（every/win_only）。
- 用户全局通知设置 NotificationSettings（master_enable / path_a_enable / summary_time /
  new_numbers_default_enabled），spec §12.2 row 8 明确这些项为 per-user 全局项。
- 用户 DND 配置持久化到 users.dnd_json。
- 用户偏好（主题）持久化到 users.preferences_json；新号码默认启用由全局设置提供。
- 模板预览 GET /channels/templates 返回路径 A/B 示例文案。
"""

import json
import logging
import re

from cryptography.fernet import InvalidToken
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlmodel import Session, select

from app.api.deps import current_user, get_session_dep, verify_csrf
from app.config import get_settings
from app.infrastructure.crypto import CryptoService
from app.models import (
    NotificationChannel,
    NotificationRule,
    NotificationSettings,
    User,
)
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

_TIME_PATTERN = r'^\d{2}:\d{2}$'

# 模板预览固定示例值（明确是演示数据，集中一处便于维护）。
_PREVIEW_LOTTERY_NAME = '双色球'
_PREVIEW_DRAW_NO = '2026150'
_PREVIEW_TIER_NAME = '二等奖'
_PREVIEW_TIER = 2
_PREVIEW_DATE_STR = '2026-01-01'
# path_b 预览：2 个追投彩种，1 中奖（双色球二等奖 浮动）+ 1 未中奖（大乐透）。
_PREVIEW_TRACKED = 2
_PREVIEW_UNWON = ['大乐透']


class ChannelOut(BaseModel):
    """渠道响应模型（id/type/config/enabled），OpenAPI schema 与返回结构一致。"""

    id: int
    type: str
    config: dict
    enabled: bool


class TemplateBody(BaseModel):
    title: str
    body: str


class RuleOut(BaseModel):
    id: int
    lottery_code: str
    strategy: str


class RuleIn(BaseModel):
    lottery_code: str = Field(min_length=1, max_length=8)
    strategy: str = Field(default='every', max_length=8)

    @field_validator('strategy')
    @classmethod
    def _check_strategy(cls, v: str) -> str:
        if v not in _ALLOWED_STRATEGIES:
            raise ValueError(f'策略必须是 {", ".join(sorted(_ALLOWED_STRATEGIES))}')
        return v


class DndIn(BaseModel):
    enabled: bool
    start: str = Field(pattern=_TIME_PATTERN)
    end: str = Field(pattern=_TIME_PATTERN)

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
    path_a: TemplateBody
    path_b: TemplateBody


class SettingsOut(BaseModel):
    """用户全局通知设置（spec §12.2 row 8）。"""

    master_enable: bool
    path_a_enable: bool
    summary_time: str | None
    new_numbers_default_enabled: bool


class SettingsIn(BaseModel):
    master_enable: bool = True
    path_a_enable: bool = True
    summary_time: str | None = None
    new_numbers_default_enabled: bool = True

    @field_validator('summary_time')
    @classmethod
    def _check_time(cls, v: str | None) -> str | None:
        if v is None or v == '':
            return None
        if not isinstance(v, str) or not re.match(_TIME_PATTERN, v):
            raise ValueError('时间格式无效，应为 HH:MM')
        hh, mm = v.split(':')
        if not (0 <= int(hh) < 24 and 0 <= int(mm) < 60):
            raise ValueError('时间格式无效')
        return v


class PreferencesOut(BaseModel):
    theme: str


class PreferencesIn(BaseModel):
    theme: str = Field(default='auto', pattern=r'^(light|dark|auto)$')


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
    response: Response,
    user: User = Depends(current_user),
    session: Session = Depends(get_session_dep),
) -> list[ChannelOut]:
    """列出当前用户的全部渠道：解密 config 回明文。

    逐行解密失败时跳过该行（不阻塞健康行），并通过响应头
    X-Channel-Decrypt-Failed 返回失败 channel id 列表，让前端/运维可感知。
    """
    crypto = _crypto()
    rows = session.exec(
        select(NotificationChannel).where(NotificationChannel.user_id == user.id)
    ).all()
    out: list[ChannelOut] = []
    failed_ids: list[int] = []
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
        except (json.JSONDecodeError, ValueError, KeyError, InvalidToken):
            logger.warning(
                'channel_decrypt_failed user_id=%s channel_id=%s type=%s key_version=%s',
                user.id,
                ch.id,
                ch.type,
                ch.key_version,
                exc_info=True,
            )
            failed_ids.append(ch.id)
            continue
    if failed_ids:
        response.headers['X-Channel-Decrypt-Failed'] = ','.join(str(i) for i in failed_ids)
    return out


# ---------------------------------------------------------------------------
# T6e: notification rules (per-lottery only strategy now)
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
    """新增/更新每彩种推送策略。同一 (user, lottery_code) 只保留一行。"""
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
    session.commit()
    session.refresh(existing)
    return RuleOut(
        id=existing.id,
        lottery_code=existing.lottery_code,
        strategy=existing.strategy,
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
# T6e: global notification settings
# ---------------------------------------------------------------------------


def _get_or_create_settings(session: Session, user_id: int) -> NotificationSettings:
    """获取或创建当前用户的全局通知设置。"""
    settings = session.get(NotificationSettings, user_id)
    if settings is None:
        settings = NotificationSettings(user_id=user_id)
        session.add(settings)
    return settings


@router.get('/settings', response_model=SettingsOut)
def get_settings_endpoint(
    user: User = Depends(current_user),
    session: Session = Depends(get_session_dep),
) -> SettingsOut:
    """读取当前用户的全局通知设置。"""
    settings = _get_or_create_settings(session, user.id)
    return SettingsOut(
        master_enable=settings.master_enable,
        path_a_enable=settings.path_a_enable,
        summary_time=settings.summary_time,
        new_numbers_default_enabled=settings.new_numbers_default_enabled,
    )


@router.put('/settings', response_model=SettingsOut)
def save_settings(
    body: SettingsIn,
    user: User = Depends(current_user),
    session: Session = Depends(get_session_dep),
    _csrf_ok: None = Depends(verify_csrf),
) -> SettingsOut:
    """保存当前用户的全局通知设置。"""
    settings = _get_or_create_settings(session, user.id)
    settings.master_enable = body.master_enable
    settings.path_a_enable = body.path_a_enable
    settings.summary_time = body.summary_time
    settings.new_numbers_default_enabled = body.new_numbers_default_enabled
    session.commit()
    session.refresh(settings)
    return SettingsOut(
        master_enable=settings.master_enable,
        path_a_enable=settings.path_a_enable,
        summary_time=settings.summary_time,
        new_numbers_default_enabled=settings.new_numbers_default_enabled,
    )


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
    """解析用户 DND 配置；损坏/缺失/类型错误时回退默认。"""
    default = {'enabled': False, 'start': '22:00', 'end': '07:00'}
    if not raw:
        return default
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning('dnd_json_parse_failed raw=%s', _safe_raw(raw), exc_info=True)
        return default

    if not isinstance(parsed, dict):
        return default

    enabled = parsed.get('enabled')
    start = parsed.get('start')
    end = parsed.get('end')

    if not isinstance(enabled, bool) or not isinstance(start, str) or not isinstance(end, str):
        return default
    if not _is_valid_time(start) or not _is_valid_time(end):
        return default

    return {'enabled': enabled, 'start': start, 'end': end}


def _is_valid_time(value: str) -> bool:
    """Validate HH:MM string and range."""
    if not isinstance(value, str) or not re.match(_TIME_PATTERN, value):
        return False
    hh, mm = value.split(':')
    return 0 <= int(hh) < 24 and 0 <= int(mm) < 60


# ---------------------------------------------------------------------------
# T6e: user preferences
# ---------------------------------------------------------------------------


def _safe_raw(raw: str | None) -> str:
    """截断用户可控的 JSON 原始串，避免日志注入/膨胀。"""
    if raw is None:
        return 'None'
    snippet = raw[:200]
    if len(raw) > 200:
        snippet = f'{snippet}...({len(raw)} chars)'
    return snippet


def _parse_preferences(raw: str | None) -> dict:
    """解析用户偏好；损坏/缺失/类型错误时回退默认。"""
    default = {'theme': 'auto'}
    if not raw:
        return default
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning('preferences_json_parse_failed raw=%s', _safe_raw(raw), exc_info=True)
        return default

    if not isinstance(parsed, dict):
        return default

    theme = parsed.get('theme', default['theme'])
    if theme not in {'light', 'dark', 'auto'}:
        theme = default['theme']

    return {'theme': theme}


@router.get('/preferences', response_model=PreferencesOut)
def get_preferences(
    user: User = Depends(current_user),
) -> PreferencesOut:
    """读取当前用户的偏好（主题）。"""
    prefs = _parse_preferences(user.preferences_json)
    return PreferencesOut(theme=prefs['theme'])


@router.post('/preferences', response_model=PreferencesOut)
def save_preferences(
    body: PreferencesIn,
    user: User = Depends(current_user),
    session: Session = Depends(get_session_dep),
    _csrf_ok: None = Depends(verify_csrf),
) -> PreferencesOut:
    """保存当前用户的偏好（主题）。"""
    user.preferences_json = json.dumps(
        {'theme': body.theme},
        ensure_ascii=False,
    )
    session.add(user)
    session.commit()
    return PreferencesOut(theme=body.theme)


# ---------------------------------------------------------------------------
# T6e: template preview
# ---------------------------------------------------------------------------


@router.get('/templates', response_model=TemplatePreview)
def template_preview(
    user: User = Depends(current_user),
) -> TemplatePreview:
    """返回路径 A/B 推送模板示例文案。"""
    path_a = build_path_a(
        lottery_name=_PREVIEW_LOTTERY_NAME,
        draw_no=_PREVIEW_DRAW_NO,
        tier_name=_PREVIEW_TIER_NAME,
        tier=_PREVIEW_TIER,
        amount=None,
    )
    path_b = build_path_b(
        date_str=_PREVIEW_DATE_STR,
        tracked_lottery_count=_PREVIEW_TRACKED,
        win_details=[(_PREVIEW_LOTTERY_NAME, _PREVIEW_TIER_NAME, None)],
        unwon_lottery_names=_PREVIEW_UNWON,
    )
    return TemplatePreview(
        path_a=TemplateBody(title=path_a.title, body=path_a.body),
        path_b=TemplateBody(title=path_b.title, body=path_b.body),
    )
