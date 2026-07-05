"""Plan 06 / T6f: admin 后台管理扩展 API。

在 Plan 05 / T6 admin.py 基础上追加：
- SMTP 发件配置读取与测试发送
- 邀请码创建与列表
- 彩种启用/停用 toggle
- 管理员操作审计日志查询
- 推送日志 6 维筛选 + 分页

所有 state-changing 端点强制 CSRF double-submit；只读端点沿用 require_admin。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.api.deps import current_user, get_session_dep, require_admin, verify_csrf
from app.config import SmtpEncryption, get_settings
from app.models import (
    AdminAuditLog,
    DrawResult,
    InviteCode,
    LotteryType,
    NotificationLog,
    User,
)
from app.notifications.base import NotificationPayload
from app.notifications.email_channel import EmailChannel
from app.services.audit_service import write_audit

router = APIRouter(prefix='/admin', tags=['admin'], dependencies=[Depends(require_admin)])

# ---------------------------------------------------------------------------
# SMTP 配置
# ---------------------------------------------------------------------------


class SmtpConfigOut(BaseModel):
    smtp_host: str | None
    smtp_port: int
    smtp_encryption: SmtpEncryption
    smtp_user: str | None
    smtp_from: str | None
    # smtp_pass 不回显
    configured: bool


class SmtpTestOut(BaseModel):
    ok: bool
    message: str


@router.get('/smtp-config', response_model=SmtpConfigOut)
def get_smtp_config() -> SmtpConfigOut:
    """返回当前环境 .env 里的 SMTP 发件配置（不含密码）。"""
    s = get_settings()
    return SmtpConfigOut(
        smtp_host=s.smtp_host,
        smtp_port=s.smtp_port,
        smtp_encryption=s.smtp_encryption,
        smtp_user=s.smtp_user,
        smtp_from=s.smtp_from,
        configured=bool(s.smtp_host and s.smtp_user and s.smtp_pass and s.smtp_from),
    )


@router.post('/smtp-test', response_model=SmtpTestOut)
def test_smtp(
    admin: User = Depends(current_user),
    session: Session = Depends(get_session_dep),
    _csrf_ok: None = Depends(verify_csrf),
) -> SmtpTestOut:
    """发送一封测试邮件到运维方发件地址，验证 SMTP 可用。

    静默失败纪律：邮箱渠道 send 永不抛异常，只返回 SendResult；这里把结果写入审计日志。
    """
    s = get_settings()
    missing = [k for k, v in {
        'host': s.smtp_host, 'user': s.smtp_user, 'pass': s.smtp_pass, 'from': s.smtp_from,
    }.items() if not v]
    if missing:
        raise HTTPException(400, f'SMTP 配置不完整，缺少: {", ".join(missing)}')

    channel = EmailChannel(
        smtp_host=s.smtp_host,  # type: ignore[arg-type]
        smtp_port=s.smtp_port,
        smtp_user=s.smtp_user,  # type: ignore[arg-type]
        smtp_pass=s.smtp_pass,  # type: ignore[arg-type]
        smtp_from=s.smtp_from,  # type: ignore[arg-type]
    )
    payload = NotificationPayload(
        title='兑奖了吗 · SMTP 测试',
        body=f'管理员 {admin.username} 于 {datetime.now(UTC).isoformat()} 发起测试，配置正常。',
    )
    result = channel.send(payload, {'address': s.smtp_from})
    ok = result.status == 'sent'
    write_audit(
        session,
        admin_id=admin.id,
        action='smtp_test',
        target_type='system',
        target_id='smtp',
        old_values={'configured': True},
        new_values={'ok': ok, 'error': result.error},
        commit=False,
    )
    session.commit()
    if ok:
        return SmtpTestOut(ok=True, message=f'测试邮件已发送至 {s.smtp_from}')
    return SmtpTestOut(ok=False, message=result.error or '发送失败')


# ---------------------------------------------------------------------------
# 邀请码
# ---------------------------------------------------------------------------


class InviteCodeOut(BaseModel):
    code: str
    created_by: int
    used_by: int | None
    used_at: datetime | None
    expires_at: datetime
    attempts: int
    locked_at: datetime | None
    created_at: datetime


@router.post('/invite-codes', response_model=InviteCodeOut, status_code=201)
def create_invite_code(
    admin: User = Depends(current_user),
    session: Session = Depends(get_session_dep),
    _csrf_ok: None = Depends(verify_csrf),
) -> InviteCodeOut:
    """admin 生成一个新的 6 位邀请码。

    静默失败纪律： InviteService.generate 默认会开启独立 Session，但项目 engine 是
    pool_size=1 / max_overflow=0，请求 Session 已占用唯一连接 → 嵌套 Session 会
    QueuePool 超时死锁。因此这里直接在请求 session 内生成并 commit，与审计日志同事务。
    """
    now = datetime.now(UTC).replace(tzinfo=None)
    for attempt in range(3):
        code = f'{__import__("secrets").randbelow(1_000_000):06d}'
        ic = InviteCode(
            code=code,
            created_by=admin.id,
            expires_at=now + timedelta(days=30),
        )
        session.add(ic)
        try:
            session.flush()
        except IntegrityError:
            session.rollback()
            if attempt == 2:
                raise HTTPException(500, '邀请码生成冲突过多，请重试') from None
            continue
        session.commit()
        session.refresh(ic)
        break
    else:
        raise HTTPException(500, '邀请码生成失败')

    # 新建一个只读事务写审计日志（刚 commit 的 session 仍可复用）
    write_audit(
        session,
        admin_id=admin.id,
        action='create_invite_code',
        target_type='invite_code',
        target_id=ic.code,
        commit=False,
    )
    return InviteCodeOut(
        code=ic.code,
        created_by=ic.created_by,
        used_by=ic.used_by,
        used_at=ic.used_at,
        expires_at=ic.expires_at,
        attempts=ic.attempts,
        locked_at=ic.locked_at,
        created_at=ic.created_at,
    )



@router.get('/invite-codes', response_model=list[InviteCodeOut])
def list_invite_codes(session: Session = Depends(get_session_dep)) -> list[InviteCodeOut]:
    """列出全部邀请码，按创建时间倒序。"""
    rows = session.exec(
        select(InviteCode).order_by(InviteCode.created_at.desc())
    ).all()
    return [
        InviteCodeOut(
            code=ic.code,
            created_by=ic.created_by,
            used_by=ic.used_by,
            used_at=ic.used_at,
            expires_at=ic.expires_at,
            attempts=ic.attempts,
            locked_at=ic.locked_at,
            created_at=ic.created_at,
        )
        for ic in rows
    ]


# ---------------------------------------------------------------------------
# 彩种启用/停用
# ---------------------------------------------------------------------------


class LotteryToggleOut(BaseModel):
    code: str
    enabled: bool


@router.patch('/lotteries/{code}/enabled', response_model=LotteryToggleOut)
def toggle_lottery(
    code: str,
    enabled: bool,
    admin: User = Depends(current_user),
    session: Session = Depends(get_session_dep),
    _csrf_ok: None = Depends(verify_csrf),
) -> LotteryToggleOut:
    """启用或停用指定彩种。"""
    lt = session.get(LotteryType, code)
    if lt is None:
        raise HTTPException(404, '彩种不存在')
    old = {'enabled': lt.enabled}
    lt.enabled = enabled
    write_audit(
        session,
        admin_id=admin.id,
        action='toggle_lottery',
        target_type='lottery_type',
        target_id=code,
        old_values=old,
        new_values={'enabled': enabled},
        commit=False,
    )
    session.commit()
    return LotteryToggleOut(code=lt.code, enabled=lt.enabled)


# ---------------------------------------------------------------------------
# 推送日志 6 维筛选 + 分页
# ---------------------------------------------------------------------------


class PushLogFilter(BaseModel):
    user_id: int | None = None
    lottery_code: str | None = None
    channel: str | None = None
    type: str | None = None
    status: str | None = None
    date_from: str | None = None
    date_to: str | None = None


class PushLogOut(BaseModel):
    id: int
    user_id: int
    username: str | None
    type: str
    status: str
    sent_at: datetime | None
    error: str | None


class PushLogPageOut(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[PushLogOut]


_MAX_PUSH_LOGS = 500


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, '%Y-%m-%d')
    except ValueError as e:
        raise HTTPException(422, f'日期格式错误: {e}') from e


@router.get('/push-logs', response_model=PushLogPageOut)
def filtered_push_logs(
    user_id: int | None = Query(None),
    lottery_code: str | None = Query(None),
    channel: str | None = Query(None),
    type: str | None = Query(None),
    status: str | None = Query(None),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: Session = Depends(get_session_dep),
) -> PushLogPageOut:
    """推送日志 6 维筛选 + 分页。

    6 维：日期/用户/彩种/渠道/类型/状态。lottery_code 来自关联 comparison.draw_result。
    """
    conds: list[Any] = []
    if user_id is not None:
        conds.append(NotificationLog.user_id == user_id)
    if channel:
        conds.append(NotificationLog.type == channel)  # type 字段存渠道/类型混合，按原型筛选语义
    if type:
        conds.append(NotificationLog.payload.contains(type))
    if status:
        conds.append(NotificationLog.status == status)

    start = _parse_date(date_from)
    end = _parse_date(date_to)
    if end is not None:
        end = end + timedelta(days=1)
    if start:
        conds.append(NotificationLog.created_at >= start)
    if end:
        conds.append(NotificationLog.created_at < end)

    if lottery_code:
        # 需要 comparison → draw_result → lottery_code
        from app.models import Comparison
        conds.append(Comparison.id == NotificationLog.comparison_id)
        conds.append(DrawResult.id == Comparison.draw_result_id)
        conds.append(DrawResult.lottery_code == lottery_code)
        base_stmt = select(NotificationLog).join(Comparison).join(DrawResult).where(*conds)
    else:
        base_stmt = select(NotificationLog).where(*conds)

    total = session.exec(select(func.count()).select_from(base_stmt.subquery())).first() or 0
    total = int(total)

    rows = session.exec(
        base_stmt
        .order_by(NotificationLog.id.desc())
        .offset((page - 1) * page_size)
        .limit(min(page_size, _MAX_PUSH_LOGS))
    ).all()

    user_ids = {log.user_id for log in rows}
    users = {
        u.id: u.username
        for u in session.exec(select(User).where(User.id.in_(user_ids))).all()
    } if user_ids else {}

    return PushLogPageOut(
        total=total,
        page=page,
        page_size=page_size,
        items=[
            PushLogOut(
                id=log.id,
                user_id=log.user_id,
                username=users.get(log.user_id),
                type=log.type,
                status=log.status,
                sent_at=log.sent_at,
                error=log.error,
            )
            for log in rows
        ],
    )


# ---------------------------------------------------------------------------
# 审计日志
# ---------------------------------------------------------------------------


class AuditLogOut(BaseModel):
    id: int
    admin_id: int
    admin_username: str | None
    action: str
    target_type: str
    target_id: str | None
    old_values: dict | None
    new_values: dict | None
    created_at: datetime


class AuditLogPageOut(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[AuditLogOut]


_MAX_AUDIT_LOGS = 500


@router.get('/audit-logs', response_model=AuditLogPageOut)
def audit_logs(
    action: str | None = Query(None),
    target_type: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: Session = Depends(get_session_dep),
) -> AuditLogPageOut:
    """管理员操作审计日志查询。"""
    conds: list[Any] = []
    if action:
        conds.append(AdminAuditLog.action == action)
    if target_type:
        conds.append(AdminAuditLog.target_type == target_type)

    base_stmt = select(AdminAuditLog).where(*conds)
    total = session.exec(select(func.count()).select_from(base_stmt.subquery())).first() or 0
    total = int(total)

    rows = session.exec(
        base_stmt
        .order_by(AdminAuditLog.id.desc())
        .offset((page - 1) * page_size)
        .limit(min(page_size, _MAX_AUDIT_LOGS))
    ).all()

    admin_ids = {log.admin_id for log in rows}
    users = {
        u.id: u.username
        for u in session.exec(select(User).where(User.id.in_(admin_ids))).all()
    } if admin_ids else {}

    def _json_or_none(value: str | None) -> dict | None:
        if not value:
            return None
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None

    return AuditLogPageOut(
        total=total,
        page=page,
        page_size=page_size,
        items=[
            AuditLogOut(
                id=log.id,
                admin_id=log.admin_id,
                admin_username=users.get(log.admin_id),
                action=log.action,
                target_type=log.target_type,
                target_id=log.target_id,
                old_values=_json_or_none(log.old_values),
                new_values=_json_or_none(log.new_values),
                created_at=log.created_at,
            )
            for log in rows
        ],
    )
