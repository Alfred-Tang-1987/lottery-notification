"""Plan 05 / T6：admin 操作审计日志写入（含脱敏）。"""

import json

from sqlmodel import Session

from app.models import AdminAuditLog

_SENSITIVE_KEYS = {'key', 'webhook', 'password', 'smtp_pass', 'ct', 'token'}


def _sanitize(values: dict | None) -> str | None:
    if values is None:
        return None
    return json.dumps(
        {
            k: ('***' if k.lower() in _SENSITIVE_KEYS else v)
            for k, v in values.items()
        },
        ensure_ascii=False,
    )


def write_audit(
    session: Session,
    *,
    admin_id: int,
    action: str,
    target_type: str,
    target_id: str | None = None,
    old_values: dict | None = None,
    new_values: dict | None = None,
) -> None:
    """写入一条 admin 审计日志；敏感字段会被替换为 ***。"""
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
    session.commit()
