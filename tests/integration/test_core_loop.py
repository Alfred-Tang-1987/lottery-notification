"""端到端核心闭环集成测试（spec §7.1）：fetch → verify → compare → comparisons + prize_claims。

T1-T6 已分别单测覆盖各服务；本文件把它们接线成完整数据流，回归保护
「各服务单绿但组合断了」的集成层缺陷（如 FetchService outbox 未生成 →
CompareService 无 pending 可认领 → 中奖静默漏比对）。

双源 MagicMock 提供 DrawNumbers；grace_seconds=0 跳过 grace 睡眠；全程用真实
SQLite engine + 真实 domain.compare，不 mock 领域层（spec §4：领域是核心）。
"""

import json
from datetime import date, datetime
from unittest.mock import MagicMock

from sqlmodel import Session, select

from app.adapters.base import DrawNumbers
from app.models import Comparison, PrizeClaim, Ticket, User
from app.services.compare_service import CompareService
from app.services.fetch_service import FetchService


def _dn(front, back):
    """构造双色球同期归一化开奖号码（两源一致）。"""
    return DrawNumbers(
        lottery_code='ssq',
        draw_no='062',
        draw_date=date(2026, 6, 21),
        front=tuple(front),
        back=tuple(back),
    )


def _make_user_and_ticket(engine, ticket_front, ticket_back):
    """建用户 + 追投一注双色球（enabled），返回 (user_id, ticket_id)。"""
    with Session(engine) as s:
        u = User(username='u', password_hash='x', role='user', invite_code='C')
        s.add(u)
        s.commit()
        s.refresh(u)
        t = Ticket(
            user_id=u.id,
            lottery_code='ssq',
            play_type='single',
            numbers_json=json.dumps({'front': list(ticket_front), 'back': list(ticket_back)}),
            multiplier=1,
            append=False,
            cost=200,
            enabled=True,
            # 开奖日 2026-06-21 为过去：票须早于该日才被比对（_compare_one 票存在性过滤）
            created_at=datetime(2026, 6, 1),
        )
        s.add(t)
        s.commit()
        s.refresh(t)
        return u.id, t.id


def _fetch_sources(front, back):
    """双源 MagicMock，均返回给定号码（一致 → verified=true）。"""
    primary = MagicMock()
    primary.name = 'mxnzp'
    primary.fetch.return_value = _dn(front, back)
    backup = MagicMock()
    backup.name = 'juhe'
    backup.fetch.return_value = _dn(front, back)
    return primary, backup


def test_full_loop_win(db_engine):
    """完整闭环命中：抓取→双源校验→比对→comparisons(is_win, tier=1)+prize_claim(pending)。

    回归点：FetchService verified 入库须同事务写 pending_comparisons outbox（spec §7.1），
    否则 CompareService.process_pending 认领 0 条 → 无 comparison → 一等奖静默漏比对。
    """
    primary, backup = _fetch_sources([1, 2, 3, 4, 5, 6], [7])
    _make_user_and_ticket(db_engine, [1, 2, 3, 4, 5, 6], [7])

    r = FetchService(primary, backup, db_engine, grace_seconds=0).fetch_and_store('ssq')
    assert r.stored and r.verified  # 双源一致 → verified

    CompareService(db_engine).process_pending()

    with Session(db_engine) as s:
        cmp = s.exec(select(Comparison)).first()
        assert cmp is not None
        assert cmp.is_win and cmp.prize_tier == 1  # 双色球 6红+1蓝 = 一等奖
        claim = s.exec(select(PrizeClaim)).first()
        assert claim is not None and claim.status == 'pending'


def test_full_loop_lose(db_engine):
    """完整闭环未中：号码不匹配 → comparison(is_win=False)，不生成 prize_claim。

    回归点：未中不得建 prize_claim（否则孤儿待兑奖污染兑奖面板）。
    """
    primary, backup = _fetch_sources([8, 9, 10, 11, 12, 13], [14])
    _make_user_and_ticket(db_engine, [1, 2, 3, 4, 5, 6], [7])

    r = FetchService(primary, backup, db_engine, grace_seconds=0).fetch_and_store('ssq')
    assert r.stored and r.verified

    CompareService(db_engine).process_pending()

    with Session(db_engine) as s:
        cmp = s.exec(select(Comparison)).first()
        assert cmp is not None
        assert not cmp.is_win
        assert s.exec(select(PrizeClaim)).first() is None  # 未中无待兑奖
