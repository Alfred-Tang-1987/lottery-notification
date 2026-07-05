"""Plan 06 / T6f: admin 后台管理扩展 API。

在 Plan 05 / T6 admin.py 基础上追加：
- SMTP 发件配置读取、写入与测试发送
- 邀请码创建与列表
- 彩种列表查询与启用/停用 toggle
- 管理员操作审计日志查询
- 推送日志 6 维筛选 + 分页

所有 state-changing 端点强制 CSRF double-submit；只读端点沿用 require_admin。
"""

from __future__ import annotations

import json
import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.api.deps import current_user, get_session_dep, require_admin, verify_csrf
from app.config import SmtpEncryption, get_settings, reset_settings_cache
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

logger = logging.getLogger(__name__)

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


# 服务商预设（spec §12.2 row 9：QQ/网易/Gmail/自定义·选中自动填服务器/端口/加密）
# 列表顺序固定，前端下拉按此渲染。
SMTP_PROVIDERS: dict[str, dict[str, Any]] = {
    'qq': {'host': 'smtp.qq.com', 'port': 465, 'encryption': 'SSL/TLS'},
    'netease': {'host': 'smtp.163.com', 'port': 465, 'encryption': 'SSL/TLS'},
    'gmail': {'host': 'smtp.gmail.com', 'port': 587, 'encryption': 'STARTTLS'},
    'custom': {},  # 自定义需手动填 host/port/encryption
}


class SmtpConfigIn(BaseModel):
    """SMTP 写入表单 payload（spec §12.2 row 9：服务商下拉 + 账号 + 授权码 + 保存）。

    provider=qq/netease/gmail 自动填 host/port/encryption；provider=custom 需手动填。
    account + auth_code 是必填项（spec：只填账号+授权码）。
    from_address 缺省等于 account。
    """
    provider: Literal['qq', 'netease', 'gmail', 'custom'] = Field(
        ..., description='服务商下拉，custom 需手动填 host/port/encryption',
    )
    account: str = Field(..., min_length=1, description='发件账号（邮箱地址）')
    auth_code: str = Field(..., min_length=1, description='SMTP 授权码（不回显）')
    host: str | None = Field(default=None, description='自定义服务商 SMTP host')
    port: int | None = Field(default=None, ge=1, le=65535, description='自定义服务商 SMTP port')
    encryption: SmtpEncryption | None = Field(default=None, description='自定义服务商加密方式')
    from_address: str | None = Field(default=None, description='发件地址，缺省=account')


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


@router.post('/smtp-config', response_model=SmtpConfigOut)
def save_smtp_config(
    payload: SmtpConfigIn,
    admin: User = Depends(current_user),
    session: Session = Depends(get_session_dep),
    _csrf_ok: None = Depends(verify_csrf),
) -> SmtpConfigOut:
    """写入 SMTP 发件配置（运行时内存热更，不持久化到 .env）。

    spec §12.2 row 9 要求 SMTP UI 为写入表单（服务商下拉 + 自动填 + 只填账号+授权码）。
    本端点把 provider 预设展开为 host/port/encryption，连同 account/auth_code 写入
    当前进程环境变量 + 重置 settings 缓存，后续 GET /admin/smtp-config 与发送邮件
    均用新值（即时生效）。

    ⚠️ 不写 .env 文件（反模式，见 lesson L-20260706T010300Z）：运行时改 .env 非原子
    （crash 中途砖化启动）+ 密钥明文落盘 + Docker 相对路径失锚。跨重启持久化由运维
    通过 .env / docker-compose env_file 管理；本端点只保证当前进程即时生效。

    静默失败防护：写入失败立即抛 500，不静默吞错。
    """
    preset = SMTP_PROVIDERS.get(payload.provider, {})
    # custom 必须手动填 host/port/encryption；preset 模式下可被显式覆盖
    host = payload.host or preset.get('host')
    port = payload.port or preset.get('port')
    encryption = payload.encryption or preset.get('encryption')
    if not host or port is None or encryption is None:
        raise HTTPException(400, '自定义服务商需填 host/port/encryption')

    from_address = payload.from_address or payload.account

    # 内存热更：更新当前进程环境变量 + 重置 settings 缓存（不碰 .env 文件）
    _apply_smtp_env(
        host=host,
        port=port,
        encryption=encryption,
        account=payload.account,
        auth_code=payload.auth_code,
        from_address=from_address,
    )
    reset_settings_cache()
    s = get_settings()

    write_audit(
        session,
        admin_id=admin.id,
        action='smtp_config_save',
        target_type='system',
        target_id='smtp',
        old_values={'provider': payload.provider},
        new_values={'host': host, 'port': port, 'encryption': encryption, 'account': payload.account},
        commit=False,
    )
    session.commit()

    return SmtpConfigOut(
        smtp_host=s.smtp_host,
        smtp_port=s.smtp_port,
        smtp_encryption=s.smtp_encryption,
        smtp_user=s.smtp_user,
        smtp_from=s.smtp_from,
        configured=bool(s.smtp_host and s.smtp_user and s.smtp_pass and s.smtp_from),
    )


def _apply_smtp_env(
    *,
    host: str,
    port: int,
    encryption: str,
    account: str,
    auth_code: str,
    from_address: str,
) -> None:
    """把 SMTP 配置写入当前进程环境变量（内存热更，不碰 .env 文件）。

    反模式警示（lesson L-20260706T010300Z）：早期实现 _persist_smtp_env 会运行时改写
    .env 文件，导致非原子写（crash 中途砖化启动）+ 密钥明文落盘 + Docker 相对路径失锚，
    更曾因测试隔离不当污染用户真实 .env（覆盖 JWT_SECRET/CRYPTO_KEY_V1 等密钥）。
    现改为纯内存热更：只更新 os.environ + 由调用方 reset_settings_cache()，让
    get_settings() 重新读取环境变量拿到新值。跨重启持久化交运维（.env / env_file）。
    """
    import os

    updates = {
        'SMTP_HOST': host,
        'SMTP_PORT': str(port),
        'SMTP_ENCRYPTION': encryption,
        'SMTP_USER': account,
        'SMTP_PASS': auth_code,
        'SMTP_FROM': from_address,
    }
    for key, value in updates.items():
        os.environ[key] = value


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
        smtp_encryption=s.smtp_encryption,
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

    静默失败纪律（lesson L-20260706T010400Z）：邀请码 insert + 审计 insert 必须同事务
    单次 commit，否则第二次 commit 失败时邀请码落库但审计静默丢失。早期实现因
    pool_size=1 + InviteService.generate 死锁而 split-commit（先 commit 邀请码再 commit
    审计），此处改用 savepoint：邀请码 flush 在 begin_nested 内（冲突只回滚 savepoint
    不毒化外层事务），审计 insert 在外层事务，最后一次 session.commit() 让两者原子落库。
    """
    now = datetime.now(UTC).replace(tzinfo=None)
    ic: InviteCode | None = None
    for attempt in range(3):
        code = f'{secrets.randbelow(1_000_000):06d}'
        ic = InviteCode(
            code=code,
            created_by=admin.id,
            expires_at=now + timedelta(days=30),
        )
        # savepoint 隔离 flush：IntegrityError（code 唯一冲突）只回滚 savepoint，
        # 不毒化外层 session（PendingRollback），审计 insert 仍可在同事务后续执行。
        try:
            with session.begin_nested():
                session.add(ic)
                session.flush()
        except IntegrityError:
            ic = None  # 本轮失败，重置 ic 防止误用半成品
            if attempt == 2:
                raise HTTPException(500, '邀请码生成冲突过多，请重试') from None
            continue
        break  # flush 成功（savepoint 释放，邀请码行仍在 pending 外层事务里）
    # 循环正常 break 时 ic 已 flush；若三轮全冲突则上面 raise 了。生产路径禁用 assert
    # （review round 3 finding：assert 在 -O 模式被剥离 + 不符合生产错误处理规范）。
    if ic is None:
        raise HTTPException(500, '邀请码生成失败：内部状态异常')

    # 审计 insert 在外层事务（与邀请码同事务），单次 commit 让两者原子落库
    write_audit(
        session,
        admin_id=admin.id,
        action='create_invite_code',
        target_type='invite_code',
        target_id=ic.code,
        commit=False,
    )
    session.commit()
    session.refresh(ic)
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


class LotteryOut(BaseModel):
    """彩种配置（spec §12.2 row 9：启用/开奖日/双源三要素）。

    draw_days 从 draw_schedule_json 解析；enabled 反映 DB 真实状态（前端不再硬编码 true）。
    category 为福彩/体彩分类（welfare/sport）——spec §12.2 row 9「双源」指双源容灾
    （MXNZP+聚合数据，spec §4.2/§7.2），所有种子彩种均双源容灾，无独立字段需渲染
    （lesson L-20260706T010200Z：避免「双源」与 category 语义重叠导致顶替渲染）。
    """
    code: str
    name: str
    category: str
    enabled: bool
    draw_days: list[int]


@router.get('/lotteries', response_model=list[LotteryOut])
def list_lotteries(session: Session = Depends(get_session_dep)) -> list[LotteryOut]:
    """列出所有彩种及其启用状态 + 开奖日（spec §12.2 row 9）。

    供 Admin.vue 渲染真实启用状态而非硬编码 true。
    """
    result: list[LotteryOut] = []
    for lt in session.exec(select(LotteryType).order_by(LotteryType.code)).all():
        try:
            sched = json.loads(lt.draw_schedule_json) if lt.draw_schedule_json else {}
            draw_days = list(sched.get('draw_days', []))
        except (json.JSONDecodeError, TypeError):
            # hunter finding：原静默兜底 draw_days=[] 掩盖数据腐烂，运维无法察觉。
            # 改记 warning 让损坏的 draw_schedule_json 可被监控发现（draw_days 仅展示用，
            # 不影响比对/推送业务路径，故不 raise 中断整批列表）。
            logger.warning(
                'lottery %s draw_schedule_json 解析失败，回退 draw_days=[]: %r',
                lt.code, lt.draw_schedule_json,
            )
            draw_days = []
        result.append(LotteryOut(
            code=lt.code,
            name=lt.name,
            category=lt.category,
            enabled=lt.enabled,
            draw_days=draw_days,
        ))
    return result


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
    status: str | None = Query(None),
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: Session = Depends(get_session_dep),
) -> PushLogPageOut:
    """推送日志筛选 + 分页。

    筛选维度：日期/用户/彩种/状态（4 维）。lottery_code 来自关联 comparison.draw_result。

    注：spec §12.2 row 9 列「6 维筛选」含「类型/渠道」两维，但当前 NotificationLog
    表均无独立列支撑：
    - 「类型」（path_a/path_b）：type 列存的是通知标题（如「兑奖了吗 · 核对汇总」），
      与前端 TYPE_OPTIONS=['path_a','path_b'] 语义不匹配。原实现用 payload.contains(type)
      子串匹配对 path_a/path_b 永不命中，属误导性假筛选（lesson L-20260706T010500Z）。
    - 「渠道」（bark/feishu/email）：NotificationLog 无 channel 字段，渠道信息在
      Notifier._send_to_user_channels 内循环发送时不落库。原实现用 type == channel
      同样是 no-op 假筛选（type 存标题，永不等于 bark/feishu/email）。
    两维均需 DB 加列 + 迁移，超出 T6f 范围，暂移除；前端同步禁用两个下拉。
    """
    conds: list[Any] = []
    if user_id is not None:
        conds.append(NotificationLog.user_id == user_id)
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
