"""CLI ``backfill-draw-costs`` 命令测试（spec §4：历史期次成本回填）。

行为契约：
1. 已有 DrawResult + enabled 追投注 + comparisons，但 DrawCost 为空（迁移前的历史数据）。
2. 运行 ``python -m app.cli backfill-draw-costs`` -> 按 (user, lottery, draw_no) 回填
   DrawCost，cost=该期 enabled 追投注 cost 之和，draw_date 取自 DrawResult.draw_date。
3. 幂等：重复运行不产生重复行、不改值（uq 兜底 upsert）。
4. 多期/多用户隔离正确。

设计：对齐 ``test_cli_reset_password.py`` 风格 -- cmd 函数 + monkeypatch cli.engine。
"""

import json
from datetime import datetime
from unittest.mock import MagicMock

from sqlmodel import Session, select

from app.models import Comparison, DrawCost, DrawResult, Ticket, User


def _seed_user(engine, username='u'):
    with Session(engine) as s:
        u = User(username=username, password_hash='x', role='user', invite_code='C')
        s.add(u)
        s.commit()
        s.refresh(u)
        return u.id


def _seed_draw_with_tickets(engine, uid, draw_no, draw_date, ticket_costs=(200,)):
    """造 DrawResult + tickets(+comparison) 但不记 DrawCost（模拟迁移前历史数据）。"""
    with Session(engine) as s:
        dr = DrawResult(
            lottery_code='ssq',
            draw_no=draw_no,
            draw_date=draw_date,
            numbers_json=json.dumps({'front': [1, 2, 3, 4, 5, 6], 'back': [7]}),
            source='mxnzp',
            verified=True,
            version=1,
        )
        s.add(dr)
        s.flush()
        for cost in ticket_costs:
            t = Ticket(
                user_id=uid, lottery_code='ssq', play_type='single',
                numbers_json=json.dumps({'front': [1, 2, 3, 4, 5, 6], 'back': [7]}),
                multiplier=1, append=False, cost=cost, enabled=True,
            )
            s.add(t)
            s.flush()
            s.add(Comparison(
                user_id=uid, draw_result_id=dr.id, ticket_id=t.id,
                hits_json='{}', prize_tier=None, is_win=False,
            ))
        s.commit()
        return dr.id


def test_backfill_draw_costs_populates_from_history(db_engine, monkeypatch):
    """历史 DrawResult+tickets 无 DrawCost -> 回填后 DrawCost 正确落库。"""
    import app.cli as cli_mod

    monkeypatch.setattr(cli_mod, 'engine', db_engine)
    uid = _seed_user(db_engine)
    _seed_draw_with_tickets(db_engine, uid, '2026070', datetime(2026, 7, 1, 21, 30), (200, 300))

    # 回填前无 DrawCost
    with Session(db_engine) as s:
        assert s.exec(select(DrawCost)).first() is None

    cli_mod.cmd_backfill_draw_costs(MagicMock())

    with Session(db_engine) as s:
        dcs = list(s.exec(select(DrawCost)).all())
        assert len(dcs) == 1
        assert dcs[0].user_id == uid
        assert dcs[0].lottery_code == 'ssq'
        assert dcs[0].draw_no == '2026070'
        assert dcs[0].cost == 500  # 200 + 300
        assert dcs[0].draw_date == datetime(2026, 7, 1, 21, 30)


def test_backfill_draw_costs_idempotent(db_engine, monkeypatch):
    """重复回填不产生重复行、不改值（uq 兜底 upsert）。"""
    import app.cli as cli_mod

    monkeypatch.setattr(cli_mod, 'engine', db_engine)
    uid = _seed_user(db_engine)
    _seed_draw_with_tickets(db_engine, uid, '2026070', datetime(2026, 7, 1, 21, 30), (200,))

    cli_mod.cmd_backfill_draw_costs(MagicMock())
    cli_mod.cmd_backfill_draw_costs(MagicMock())  # 重复

    with Session(db_engine) as s:
        dcs = list(s.exec(select(DrawCost)).all())
        assert len(dcs) == 1
        assert dcs[0].cost == 200


def test_backfill_draw_costs_skips_draws_without_tickets(db_engine, monkeypatch):
    """无追投注的 DrawResult 不记 DrawCost（无投入不记账）。"""
    import app.cli as cli_mod

    monkeypatch.setattr(cli_mod, 'engine', db_engine)
    _seed_user(db_engine)
    # 只造 DrawResult，无 ticket
    with Session(db_engine) as s:
        s.add(DrawResult(
            lottery_code='ssq', draw_no='2026070', draw_date=datetime(2026, 7, 1, 21, 30),
            numbers_json='{}', source='mxnzp', verified=True, version=1,
        ))
        s.commit()

    cli_mod.cmd_backfill_draw_costs(MagicMock())

    with Session(db_engine) as s:
        assert s.exec(select(DrawCost)).first() is None
