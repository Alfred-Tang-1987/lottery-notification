"""Plan 05 / T6：admin 操作审计日志写入（含脱敏）。"""

import json

from sqlmodel import Session

from app.models import AdminAuditLog

_SENSITIVE_KEYS = {'key', 'webhook', 'password', 'smtp_pass', 'ct', 'token'}


def _sanitize(value: dict | list | None) -> str | None:
    if value is None:
        return None

    def _walk(obj):
        if isinstance(obj, dict):
            return {k: ('***' if k.lower() in _SENSITIVE_KEYS else _walk(v)) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_walk(item) for item in obj]
        return obj

    return json.dumps(_walk(value), ensure_ascii=False)


def write_audit(
    session: Session,
    *,
    admin_id: int,
    action: str,
    target_type: str,
    target_id: str | None = None,
    old_values: dict | list | None = None,
    new_values: dict | list | None = None,
    commit: bool = True,
) -> None:
    """写入一条 admin 审计日志；敏感字段会被替换为 ***。

    Args:
        commit: 为 False 时只 session.add 不 commit，供调用方在同一事务内原子提交。
    """
    session.add(
        AdminAuditLog(
            admin_id=admin_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            old_values=_sanitize(old_values),
            new_values=_sanitize(new_values),
        )
    )
    if commit:
        session.commit()
