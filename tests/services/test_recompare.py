"""recompare_all 测试（Plan 10 / T6；eng-review 外部声音发现 1——旧错误表写出的
comparisons 行需要显式重算入口，否则永久错显示）。"""

from datetime import datetime

from sqlmodel import Session, select

from app.models import Comparison, DrawResult, LotteryType, Ticket, User
from app.services.compare_service import recompare_all


def _seed_lottery(s, code='dlt', name='大乐透'):
    """种彩种元数据（Ticket.lottery_code FK；FK 未强开但保持一致）。"""
    s.add(LotteryType(code=code, name=name, category='sport',
                      spec_json='{}', draw_schedule_json='{}'))


def _seed_user(s, username='alice'):
    u = User(username=username, password_hash='x', role='user', invite_code=username)
    s.add(u)
    s.commit()
    s.refresh(u)
    return u


def _seed_draw(s, lottery_code, draw_no, front, back, draw_date):
    """种 verified 开奖（DrawResult.source 无默认，沿用既有 seed 模式补 source）。"""
    dr = DrawResult(
        lottery_code=lottery_code, draw_no=draw_no, verified=True,
        numbers_json=f'{{"front": {list(front)}, "back": {list(back)}}}',
        draw_date=draw_date,
        source='mxnzp',
    )
    s.add(dr)
    s.commit()
    s.refresh(dr)
    return dr


def _seed_ticket(s, user_id, front, back, lottery_code='dlt'):
    t = Ticket(
        user_id=user_id, lottery_code=lottery_code, play_type='single',
        numbers_json=f'{{"front": {list(front)}, "back": {list(back)}}}', cost=200,
    )
    s.add(t)
    s.commit()
    s.refresh(t)
    return t


def _seed_stale_dlt_false_win(db_engine):
    """种「旧错误表写出的 1+1 中奖 100 元」dlt 场景，返回 (dr_id, ticket_id)。"""
    with Session(db_engine) as s:
        _seed_lottery(s)
        u = _seed_user(s)
        dr = _seed_draw(s, 'dlt', '2026099', (1, 2, 3, 4, 5), (6, 7), datetime(2026, 8, 1))
        t = _seed_ticket(s, u.id, (1, 9, 9, 9, 9), (6, 8))
        # 模拟旧错误表写出的行：1+1 被误判 tier=7 / 10000 分
        s.add(Comparison(user_id=u.id, draw_result_id=dr.id, ticket_id=t.id,
                         hits_json='{"front_hit":1,"back_hit":1}',
                         prize_tier=7, prize_amount=10000, is_win=True))
        s.commit()
        return dr.id, t.id


def test_recompare_corrects_stale_dlt_false_win(db_engine):
    """旧表误判的 dlt 1+1『中奖 100 元』行，recompare 后按新表判未中奖（is_win 翻 False，
    tier/amount 清 None），同一行原地更新（uq_cmp_draw_ticket 不产生新行）。"""
    dr_id, t_id = _seed_stale_dlt_false_win(db_engine)

    stats = recompare_all(db_engine)

    assert stats['draws'] >= 1 and stats['rows'] >= 1
    with Session(db_engine) as s:
        row = s.exec(
            select(Comparison).where(
                Comparison.draw_result_id == dr_id, Comparison.ticket_id == t_id)
        ).one()
        assert row.is_win is False, '新表下 1+1 不中奖——旧行必须被纠正'
        assert row.prize_tier is None and row.prize_amount is None


def test_recompare_dry_run_writes_nothing(db_engine):
    """--dry-run 只统计不写库（人工核对安全阀）。"""
    dr_id, t_id = _seed_stale_dlt_false_win(db_engine)

    before = recompare_all(db_engine, dry_run=True)
    with Session(db_engine) as s:
        # 行内容保持旧错误值
        row = s.exec(
            select(Comparison).where(
                Comparison.draw_result_id == dr_id, Comparison.ticket_id == t_id)
        ).one()
        assert row.prize_tier == 7 and row.prize_amount == 10000  # 未被改写
        assert row.is_win is True
    assert before['changed'] >= 1  # 但统计到了会变更的行


def test_recompare_honors_version_gate(db_engine):
    """版本门接线：2026-01-30 的 dlt 期重比按 2019 表（4+2=四等 300000 分），
    2026-01-31 起按七档（4+2=三等 500000 分）——recompute 复用 _compare_one 即自动获得。"""
    with Session(db_engine) as s:
        _seed_lottery(s)
        u = _seed_user(s)
        # 同号 4+2 票：front(1,2,3,4,8) vs draw front(1,2,3,4,9)=4 红；back(5,6)=2 蓝
        t = _seed_ticket(s, u.id, (1, 2, 3, 4, 8), (5, 6))
        dr_old = _seed_draw(s, 'dlt', '0001', (1, 2, 3, 4, 9), (5, 6), datetime(2026, 1, 30))
        dr_new = _seed_draw(s, 'dlt', '0002', (1, 2, 3, 4, 9), (5, 6), datetime(2026, 1, 31))
        # 在 session 内取 id（commit 后实例 detached，退出 with 再访问 .id 会
        # DetachedInstanceError）
        old_id, new_id, t_id = dr_old.id, dr_new.id, t.id

    recompare_all(db_engine)

    with Session(db_engine) as s:
        old_row = s.exec(select(Comparison).where(
            Comparison.draw_result_id == old_id,
            Comparison.ticket_id == t_id,
        )).one()
        new_row = s.exec(select(Comparison).where(
            Comparison.draw_result_id == new_id,
            Comparison.ticket_id == t_id,
        )).one()
        # 2019 九档：4+2 = 四等 3000 元；2026 七档：4+2 = 三等 5000 元
        assert old_row.prize_tier == 4, f'2026-01-30 应走 2019 表 4+2=四等，实得 tier {old_row.prize_tier}'
        assert old_row.prize_amount == 300000
        assert new_row.prize_tier == 3, f'2026-01-31 应走 2026 表 4+2=三等，实得 tier {new_row.prize_tier}'
        assert new_row.prize_amount == 500000


def test_recompare_preserves_refilled_float_amount(db_engine):
    """发现 1（HIGH）：已回填的浮动档金额不得被 recompare 抹成 None——7 天窗口会让它
    永久丢失。构造：一期 dlt + 中一等（5+2）票，Comparison(tier=1, prize_amount=50000000,
    is_win=True) 模拟已回填 500 万元；recompare 后该行 prize_amount 仍 == 50000000。"""
    with Session(db_engine) as s:
        _seed_lottery(s)
        u = _seed_user(s)
        dr = _seed_draw(s, 'dlt', '26001', (1, 2, 3, 4, 5), (6, 7), datetime(2026, 8, 1))
        t = _seed_ticket(s, u.id, (1, 2, 3, 4, 5), (6, 7))
        s.add(Comparison(user_id=u.id, draw_result_id=dr.id, ticket_id=t.id,
                         hits_json='{"front_hit":5,"back_hit":2}',
                         prize_tier=1, prize_amount=50000000, is_win=True))
        s.commit()
        dr_id, t_id = dr.id, t.id

    recompare_all(db_engine)

    with Session(db_engine) as s:
        row = s.exec(select(Comparison).where(
            Comparison.draw_result_id == dr_id, Comparison.ticket_id == t_id)).one()
        assert row.prize_amount == 50000000, (
            f'已回填的浮动奖金额不得被 recompare 抹成 None，实得 {row.prize_amount}'
        )
        assert row.prize_tier == 1 and row.is_win is True
