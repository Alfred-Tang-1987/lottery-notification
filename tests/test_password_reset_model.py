"""PasswordResetCode model 测试（Plan 08 / T1）。"""

from datetime import UTC, datetime

from sqlmodel import Session, select

from app.models import PasswordResetCode, User


def test_defaults(db_engine):
    with Session(db_engine) as s:
        s.add(User(username='alice', password_hash='x', role='user', invite_code='C'))
        u = s.exec(select(User)).first()
        code = PasswordResetCode(
            user_id=u.id,
            code_hash='a' * 64,
            channel_type='email',
            expires_at=datetime.now(UTC).replace(tzinfo=None),
        )
        s.add(code)
        s.commit()
        s.refresh(code)
        assert code.id is not None
        assert code.attempts == 0
        assert code.used_at is None
        assert code.created_at is not None


def test_user_id_indexed_and_fk(db_engine):
    """user_id 有索引且是 users.id 外键（schema 断言）。"""
    from sqlalchemy import inspect as sa_inspect

    insp = sa_inspect(db_engine)
    cols = {c['name']: c for c in insp.get_columns('password_reset_codes')}
    assert 'user_id' in cols
    fks = insp.get_foreign_keys('password_reset_codes')
    assert any(fk['referred_table'] == 'users' for fk in fks)
    idxs = insp.get_indexes('password_reset_codes')
    assert any('user_id' in ix['column_names'] for ix in idxs)
