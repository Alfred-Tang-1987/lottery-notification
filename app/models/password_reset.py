"""密码重置验证码（Plan 08）。

同一用户同时至多一条活跃码（used_at IS NULL）：新请求事务内作废旧码再插新行。
code 只存 SHA-256 hex，不存明文。expires_at/used_at 一律 naive UTC
（datetime.now(UTC).replace(tzinfo=None)），与 TimestampMixin.created_at 同时区同数值。
"""

from datetime import datetime

from sqlmodel import Field

from app.models._base import TimestampMixin


class PasswordResetCode(TimestampMixin, table=True):
    __tablename__ = 'password_reset_codes'
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key='users.id', index=True)
    code_hash: str = Field(max_length=64)  # SHA-256(验证码) hex
    channel_type: str = Field(max_length=8)  # 实际发送渠道（审计），当前恒 'email'
    expires_at: datetime  # 创建 + 15min，naive UTC
    attempts: int = Field(default=0, sa_column_kwargs={'server_default': '0'})
    used_at: datetime | None = None  # 非空即作废（成功/被顶替/send 失败）
