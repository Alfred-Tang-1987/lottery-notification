"""Plan 05 / T2：邀请码服务。

Spec §6.2：邀请码单次使用 + 有效期 + 失败尝试锁定，仅 admin 生成，无默认 bootstrap 码。
"""

import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.models.invite import InviteCode

_MAX_GENERATE_RETRIES = 3


def _utc_now_naive() -> datetime:
    """返回与项目 TimestampMixin.created_at 同数值的 naive UTC 时间。"""
    return datetime.now(UTC).replace(tzinfo=None)


class InviteService:
    """邀请码：admin 生成（6 位），注册时消耗，单次+有效期+尝试锁定。无默认 bootstrap 码。"""

    def __init__(self, engine: Engine, ttl_days: int = 30, max_attempts: int = 5):
        self._engine = engine
        self._ttl = ttl_days
        self._max_attempts = max_attempts

    def generate(self, *, admin_id: int) -> str:
        for attempt in range(_MAX_GENERATE_RETRIES):
            code = f'{secrets.randbelow(1000000):06d}'
            with Session(self._engine) as s:
                s.add(
                    InviteCode(
                        code=code,
                        created_by=admin_id,
                        expires_at=_utc_now_naive() + timedelta(days=self._ttl),
                    )
                )
                try:
                    s.commit()
                except IntegrityError:
                    s.rollback()
                    if attempt == _MAX_GENERATE_RETRIES - 1:
                        raise RuntimeError('邀请码生成冲突过多，请重试') from None
                    continue
            return code
        # 理论上不会到达，但保留明确出口让类型系统/静态分析满意
        raise RuntimeError('邀请码生成失败')

    def consume(
        self,
        code: str,
        *,
        user_id: int | None,
        session: Session,
    ) -> int | None:
        """尝试消耗一个邀请码。

        参数:
            user_id: 若提供，则在码有效时立即原子占用（设置 used_by/used_at）。
                     若提供但码无效/过期/已用/已锁，则只累计尝试次数并返回 None。
                     若提供 None，则只校验不占用（失败时仍累计尝试次数）。
            session: 调用方提供的 SQLModel Session，必须由调用方 commit/rollback。

        返回:
            占用成功时返回 user_id（原子占用，单次使用）。
            任何失败（不存在/已用/已锁/过期）返回 None。

        注意:
            1. 失败尝试会立即在 session 中写入 attempts 并可能更新 locked_at，
               调用方应在返回 False 后显式 commit 或 rollback 该 session。
               对注册流程：如果随后因用户名冲突等原因要拒绝注册，仍应 commit 本次
               attempts/lock 变更，否则防爆破计数会跨请求丢失（silent failure）。
            2. 占用（user_id 非 None 且码有效）时，使用 UPDATE ... RETURNING 原子
               更新 used_by/used_at，避免 read-check-then-write 竞态。
        """
        # 先按 code 取行；若存在则在该行上记录一次失败尝试，然后判定。
        ic = session.exec(select(InviteCode).where(InviteCode.code == code).with_for_update()).first()
        if ic is None:
            return None

        now = _utc_now_naive()

        # 1. 已用 / 已锁 → 直接失败，不递增 attempts（避免 dead rows 被反复刷）
        if ic.used_by is not None or ic.locked_at is not None:
            return None

        # 2. 未请求占用（user_id is None）或已过期 → 累计 attempts，可能锁定，返回失败
        if user_id is None or now > ic.expires_at:
            ic.attempts += 1
            if ic.attempts >= self._max_attempts:
                ic.locked_at = now
            return None

        # 4. 原子占用：UPDATE ... WHERE used_by IS NULL ... RETURNING code
        #    用 UPDATE 的 rowcount 作为唯一成功占用凭证，避免 TOCTOU。
        stmt = (
            update(InviteCode)
            .where(
                InviteCode.code == code,
                InviteCode.used_by.is_(None),
                InviteCode.locked_at.is_(None),
                InviteCode.expires_at > now,
            )
            .values(used_by=user_id, used_at=now)
            .returning(InviteCode.code)
        )
        result = session.exec(stmt)  # type: ignore[arg-type]
        claimed_code = result.one_or_none()
        if claimed_code is None:
            # 并发下已被他人占用或锁定/过期
            return None
        return user_id
