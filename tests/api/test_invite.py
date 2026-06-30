"""Plan 05 / T2：邀请码服务测试。

Spec §6.2：邀请码单次使用 + 有效期 + 失败尝试锁定，仅 admin 生成，无默认 bootstrap 码。
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlmodel import Session, select

from app.services.invite_service import InviteCode, InviteService


def _utc_now_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def test_generate_invite_returns_6_digit(db_engine):
    svc = InviteService(db_engine)
    code = svc.generate(admin_id=1)
    assert len(code) == 6 and code.isdigit()


def test_consume_claims_code_atomically(db_engine):
    """consume 应在一行内原子占用邀请码，返回 user_id；再次消费失败。"""
    svc = InviteService(db_engine)
    code = svc.generate(admin_id=1)
    with Session(db_engine) as s:
        used_by = svc.consume(code, user_id=1, session=s)
        assert used_by == 1
        s.commit()
    with Session(db_engine) as s:
        ic = s.exec(select(InviteCode).where(InviteCode.code == code)).first()
        assert ic.used_by == 1
        assert ic.used_at is not None
    # 已用后再次 consume 失败
    with Session(db_engine) as s:
        assert svc.consume(code, user_id=2, session=s) is None


def test_consume_expired_fails(db_engine):
    svc = InviteService(db_engine, ttl_days=1)
    code = svc.generate(admin_id=1)
    with Session(db_engine) as s:
        ic = s.exec(select(InviteCode).where(InviteCode.code == code)).first()
        ic.expires_at = _utc_now_naive() - timedelta(days=2)
        s.commit()
    with Session(db_engine) as s:
        assert svc.consume(code, user_id=1, session=s) is None


def test_consume_expired_accumulates_attempts_then_locks(db_engine):
    """过期码仍累计尝试次数，跨请求也能持久化，超限后锁定。"""
    svc = InviteService(db_engine, ttl_days=1, max_attempts=3)
    code = svc.generate(admin_id=1)
    with Session(db_engine) as s:
        ic = s.exec(select(InviteCode).where(InviteCode.code == code)).first()
        ic.expires_at = _utc_now_naive() - timedelta(days=2)
        s.commit()

    # 模拟三个独立请求：每个请求都 commit，但consume False时caller也会rollback
    # 这里为了验证 attempts 在 consume 内部被持久化，手动 commit 模拟"消费失败但尝试已记录"
    for _ in range(3):
        with Session(db_engine) as s:
            svc.consume(code, user_id=1, session=s)
            s.commit()  # consume False 时仍应保留 attempts 递增

    with Session(db_engine) as s:
        ic = s.exec(select(InviteCode).where(InviteCode.code == code)).first()
        assert ic.attempts == 3
        assert ic.locked_at is not None


def test_brute_force_nonexistent_code_does_not_create_rows(db_engine):
    """猜测不存在的码不得创建 invite_codes 行。"""
    svc = InviteService(db_engine, max_attempts=3)
    for _ in range(5):
        with Session(db_engine) as s:
            assert svc.consume('000000', user_id=1, session=s) is None
            s.commit()
    with Session(db_engine) as s:
        assert s.exec(select(InviteCode).where(InviteCode.code == '000000')).first() is None


def test_brute_force_valid_code_locks_after_failed_claims(db_engine):
    """对真实未用码连续失败尝试（无 user_id）超限后锁定，后续正确 user_id 也失败。"""
    svc = InviteService(db_engine, max_attempts=3)
    code = svc.generate(admin_id=1)
    for _ in range(3):
        with Session(db_engine) as s:
            svc.consume(code, user_id=None, session=s)  # 仅校验不占用
            s.commit()
    with Session(db_engine) as s:
        ic = s.exec(select(InviteCode).where(InviteCode.code == code)).first()
        assert ic.attempts == 3
        assert ic.locked_at is not None
        assert svc.consume(code, user_id=1, session=s) is None


def test_no_default_bootstrap_code(db_engine):
    """无默认码：首启无任何有效邀请码。"""
    svc = InviteService(db_engine)
    with Session(db_engine) as s:
        assert svc.consume('000000', user_id=1, session=s) is None
        assert svc.consume('123456', user_id=1, session=s) is None


def test_consume_attempts_not_visible_on_valid_code(db_engine):
    """有效码 consume 成功后，不会递增 attempts（尝试锁定只针对失败）。"""
    svc = InviteService(db_engine)
    code = svc.generate(admin_id=1)
    with Session(db_engine) as s:
        assert svc.consume(code, user_id=1, session=s) == 1
        ic = s.exec(select(InviteCode).where(InviteCode.code == code)).first()
        assert ic.attempts == 0
        s.rollback()


def test_consume_atomic_update_via_sql(db_engine):
    """同一有效码连续两次尝试用不同 user_id 占用，只能有一个成功。"""
    svc = InviteService(db_engine)
    code = svc.generate(admin_id=1)
    with Session(db_engine) as s1:
        r1 = svc.consume(code, user_id=1, session=s1)
        s1.commit()
    with Session(db_engine) as s2:
        r2 = svc.consume(code, user_id=2, session=s2)
    assert r1 == 1
    assert r2 is None


def test_attempts_column_has_server_default(db_engine):
    """数据库层非 ORM 插入时 attempts 默认应为 0。"""
    with Session(db_engine) as s:
        s.exec(
            text(
                "INSERT INTO invite_codes (code, created_by, expires_at, created_at) "
                "VALUES (:code, :admin_id, :expires, :created)"
            ).bindparams(
                code='MANUAL',
                admin_id=1,
                expires=_utc_now_naive() + timedelta(days=1),
                created=_utc_now_naive(),
            )
        )
        s.commit()
    with Session(db_engine) as s:
        ic = s.get(InviteCode, 'MANUAL')
        assert ic.attempts == 0
