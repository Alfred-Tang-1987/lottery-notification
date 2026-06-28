import pytest
from sqlmodel import Session

from app.infrastructure.repositories import TicketRepo, UserRepository
from app.models import User


def _make_user(session, username='u1', role='user'):
    u = User(username=username, password_hash='x', role=role, invite_code='ABC123')
    session.add(u)
    session.commit()
    session.refresh(u)
    return u


def test_ticket_repo_scoped_by_user_id(db_engine):
    with Session(db_engine) as s:
        u1 = _make_user(s, 'u1')
        u2 = _make_user(s, 'u2')
        repo1 = TicketRepo(s, user_id=u1.id)
        repo2 = TicketRepo(s, user_id=u2.id)
        repo1.create(
            lottery_code='ssq',
            play_type='single',
            numbers_json='{"front":[1,2,3,4,5,6],"back":[7]}',
            cost=200,
        )
        assert len(repo1.list_all()) == 1
        assert len(repo2.list_all()) == 0  # 隔离：u2 看不到 u1 的票


def test_ticket_repo_idor_safe(db_engine):
    """u2 不能通过 ticket_id 读 u1 的票。"""
    with Session(db_engine) as s:
        u1 = _make_user(s, 'u1')
        u2 = _make_user(s, 'u2')
        repo1 = TicketRepo(s, user_id=u1.id)
        t = repo1.create(
            lottery_code='ssq',
            play_type='single',
            numbers_json='{"front":[1,2,3,4,5,6],"back":[7]}',
            cost=200,
        )
        repo2 = TicketRepo(s, user_id=u2.id)
        assert repo2.get(t.id) is None  # IDOR 防护


def test_ticket_repo_list_by_lottery(db_engine):
    with Session(db_engine) as s:
        u = _make_user(s, 'u1')
        repo = TicketRepo(s, user_id=u.id)
        repo.create(lottery_code='ssq', play_type='single', numbers_json='{"front":[1,2,3,4,5,6],"back":[7]}', cost=200)
        repo.create(lottery_code='dlt', play_type='single', numbers_json='{"front":[1,2,3,4,5],"back":[1,2]}', cost=200)
        repo.create(
            lottery_code='ssq',
            play_type='single',
            numbers_json='{"front":[2,3,4,5,6,7],"back":[8]}',
            cost=200,
            enabled=False,
        )

        ssq_enabled = repo.list_by_lottery('ssq', only_enabled=True)
        assert len(ssq_enabled) == 1

        ssq_all = repo.list_by_lottery('ssq', only_enabled=False)
        assert len(ssq_all) == 2

        dlt_all = repo.list_by_lottery('dlt')
        assert len(dlt_all) == 1


def test_ticket_repo_update(db_engine):
    with Session(db_engine) as s:
        u = _make_user(s, 'u1')
        repo = TicketRepo(s, user_id=u.id)
        t = repo.create(
            lottery_code='ssq', play_type='single', numbers_json='{"front":[1,2,3,4,5,6],"back":[7]}', cost=200
        )

        updated = repo.update(t.id, label='my label', multiplier=5)
        assert updated is not None
        assert updated.label == 'my label'
        assert updated.multiplier == 5

        # IDOR-safe: u2 cannot update u1's ticket
        u2 = _make_user(s, 'u2')
        repo2 = TicketRepo(s, user_id=u2.id)
        assert repo2.update(t.id, label='hacked') is None


def test_ticket_repo_delete(db_engine):
    with Session(db_engine) as s:
        u = _make_user(s, 'u1')
        repo = TicketRepo(s, user_id=u.id)
        t = repo.create(
            lottery_code='ssq', play_type='single', numbers_json='{"front":[1,2,3,4,5,6],"back":[7]}', cost=200
        )

        # IDOR-safe: u2 cannot delete u1's ticket
        u2 = _make_user(s, 'u2')
        repo2 = TicketRepo(s, user_id=u2.id)
        assert repo2.delete(t.id) is False

        # u1 can delete
        assert repo.delete(t.id) is True
        assert len(repo.list_all()) == 0


def test_ticket_repo_update_rejects_user_id_reassignment(db_engine):
    """HIGH: update() must not allow reassigning user_id via setattr whitelist."""
    with Session(db_engine) as s:
        u1 = _make_user(s, 'u1')
        u2 = _make_user(s, 'u2')
        repo1 = TicketRepo(s, user_id=u1.id)
        t = repo1.create(
            lottery_code='ssq',
            play_type='single',
            numbers_json='{"front":[1,2,3,4,5,6],"back":[7]}',
            cost=200,
        )
        # Attempt to reassign ownership via update
        repo1.update(t.id, user_id=u2.id)
        s.refresh(t)
        assert t.user_id == u1.id, 'user_id must not be mutable via update()'


def test_ticket_repo_update_validates_multiplier_range(db_engine):
    """HIGH: update() must reject out-of-range multiplier (ge=1 le=99)."""
    with Session(db_engine) as s:
        u = _make_user(s, 'u1')
        repo = TicketRepo(s, user_id=u.id)
        t = repo.create(
            lottery_code='ssq',
            play_type='single',
            numbers_json='{"front":[1,2,3,4,5,6],"back":[7]}',
            cost=200,
        )
        with pytest.raises(ValueError, match='multiplier'):
            repo.update(t.id, multiplier=999)
        with pytest.raises(ValueError, match='multiplier'):
            repo.update(t.id, multiplier=0)
        s.refresh(t)
        assert t.multiplier == 1


def test_ticket_repo_update_validates_cost_non_negative(db_engine):
    """HIGH: update() must reject negative cost (ge=0)."""
    with Session(db_engine) as s:
        u = _make_user(s, 'u1')
        repo = TicketRepo(s, user_id=u.id)
        t = repo.create(
            lottery_code='ssq',
            play_type='single',
            numbers_json='{"front":[1,2,3,4,5,6],"back":[7]}',
            cost=200,
        )
        with pytest.raises(ValueError, match='cost'):
            repo.update(t.id, cost=-100)
        s.refresh(t)
        assert t.cost == 200


def test_user_repository_create_keyword_only_signature(db_engine):
    """LOW: role should be keyword-only for consistent signature style."""
    import inspect

    sig = inspect.signature(UserRepository.create)
    params = list(sig.parameters.values())
    # username, password_hash, invite_code should be keyword-only
    # role should also be keyword-only (not positional-or-keyword between them)
    for p in params:
        if p.name in ('username', 'password_hash', 'role', 'invite_code'):
            assert p.kind == inspect.Parameter.KEYWORD_ONLY, f'{p.name} must be keyword-only'
