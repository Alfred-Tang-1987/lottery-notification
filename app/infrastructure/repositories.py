from sqlmodel import Session, select
from app.models import User, Ticket

# Updatable fields allowlist for TicketRepo.update (security: prevents user_id reassignment, etc.)
_TICKET_UPDATABLE = {"label", "multiplier", "append", "enabled", "numbers_json", "tuo_json", "play_type", "cost"}

# Validation constraints mirroring SQLModel Field validators
_MULTIPLIER_MIN = 1
_MULTIPLIER_MAX = 99
_COST_MIN = 0


def _validate_ticket_field(k: str, v):
    """Raise ValueError if value violates Ticket field constraints."""
    if k == "multiplier":
        if not isinstance(v, int) or not (_MULTIPLIER_MIN <= v <= _MULTIPLIER_MAX):
            raise ValueError(f"multiplier must be between {_MULTIPLIER_MIN} and {_MULTIPLIER_MAX}, got {v}")
    if k == "cost":
        if not isinstance(v, int) or v < _COST_MIN:
            raise ValueError(f"cost must be >= {_COST_MIN}, got {v}")


class TicketRepo:
    """号码池仓储。构造注入 session + user_id，所有查询 WHERE user_id。IDOR-safe。"""

    def __init__(self, session: Session, user_id: int):
        self._s = session
        self._uid = user_id

    def create(
        self,
        *,
        lottery_code,
        play_type,
        numbers_json,
        cost,
        tuo_json=None,
        label=None,
        multiplier=1,
        append=False,
        enabled=True,
    ) -> Ticket:
        t = Ticket(
            user_id=self._uid,
            lottery_code=lottery_code,
            play_type=play_type,
            numbers_json=numbers_json,
            tuo_json=tuo_json,
            label=label,
            multiplier=multiplier,
            append=append,
            cost=cost,
            enabled=enabled,
        )
        self._s.add(t)
        self._s.commit()
        self._s.refresh(t)
        return t

    def get(self, ticket_id: int) -> Ticket | None:
        """IDOR-safe：仅返回属于本 user 的票。"""
        return self._s.exec(
            select(Ticket).where(Ticket.id == ticket_id, Ticket.user_id == self._uid)
        ).first()

    def list_all(self) -> list[Ticket]:
        return list(
            self._s.exec(select(Ticket).where(Ticket.user_id == self._uid)).all()
        )

    def list_by_lottery(self, lottery_code: str, only_enabled=True) -> list[Ticket]:
        stmt = select(Ticket).where(
            Ticket.user_id == self._uid, Ticket.lottery_code == lottery_code
        )
        if only_enabled:
            stmt = stmt.where(Ticket.enabled == True)  # noqa: E712
        return list(self._s.exec(stmt).all())

    def update(self, ticket_id: int, **fields) -> Ticket | None:
        """IDOR-safe 更新：先 get 校验归属再改（不绕过 user_id）。
        仅允许更新白名单字段；未知字段静默丢弃；字段值经校验器验证。"""
        t = self.get(ticket_id)
        if t is None:
            return None
        for k, v in fields.items():
            if k not in _TICKET_UPDATABLE:
                continue  # drop unknown / forbidden fields (e.g. user_id)
            _validate_ticket_field(k, v)
            setattr(t, k, v)
        self._s.commit()
        self._s.refresh(t)
        return t

    def delete(self, ticket_id: int) -> bool:
        """IDOR-safe 删除。"""
        t = self.get(ticket_id)
        if t is None:
            return False
        self._s.delete(t)
        self._s.commit()
        return True


class UserRepository:
    """全局用户仓储（注册/登录/角色，不经 user_id 隔离）。"""

    def __init__(self, session: Session):
        self._s = session

    def get_by_username(self, username: str) -> User | None:
        return self._s.exec(
            select(User).where(User.username == username)
        ).first()

    def create(self, *, username, password_hash, invite_code, role="user") -> User:
        u = User(
            username=username,
            password_hash=password_hash,
            role=role,
            invite_code=invite_code,
        )
        self._s.add(u)
        self._s.commit()
        self._s.refresh(u)
        return u
