"""recompare_all 测试（Plan 10 / T6；eng-review 外部声音发现 1——旧错误表写出的
comparisons 行需要显式重算入口，否则永久错显示）。"""

from datetime import datetime

from sqlmodel import Session, select

from app.models import Comparison, DrawResult, LotteryType, PrizeClaim, Ticket, User
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


def _seed_ticket(s, user_id, front, back, lottery_code='dlt', created_at=None):
    """created_at：显式指定时用之（naive UTC，与 TimestampMixin 同表示）——
    recompare 票存在性过滤的夹具需要控制票/开奖日时序。"""
    t = Ticket(
        user_id=user_id, lottery_code=lottery_code, play_type='single',
        numbers_json=f'{{"front": {list(front)}, "back": {list(back)}}}', cost=200,
        **({'created_at': created_at} if created_at is not None else {}),
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
        # 票 created_at 须早于开奖日（_compare_one 票存在性过滤），只改时序不改断言
        t = _seed_ticket(s, u.id, (1, 9, 9, 9, 9), (6, 8), created_at=datetime(2026, 7, 1))
        # 模拟旧错误表写出的行：1+1 被误判 tier=7 / 10000 分
        s.add(Comparison(user_id=u.id, draw_result_id=dr.id, ticket_id=t.id,
                         hits_json='{"front_hit":1,"back_hit":1}',
                         prize_tier=7, prize_amount=10000, is_win=True))
        s.commit()
        return dr.id, t.id


def test_recompare_does_not_backfill_phantom_for_ticket_created_after_draw(db_engine, monkeypatch):
    """plan-10 设计缺口回归：票创建晚于开奖日 → recompare 不得为「票创建前的历史期」
    补 phantom 比对行（含虚假中奖 → 虚假 PrizeClaim）。

    _compare_one 按 lottery_code+enabled 选票、无票存在性过滤：正常流程只在开奖时点
    比对当时的票，天然不产生「历史期 × 后来创建的票」；recompare 全量回放会为历史期
    补出行（生产 DB 副本探针：250 行全 phantom，17 行虚假中奖）。

    票中 5+2（dlt 一等，浮动档）：保护② stub 掉避免单测真实网络（同 qlc 用例模式）。"""
    import app.services.refill_service as refill_mod

    class _StubFloatRefillWorker:
        def __init__(self, engine, amount_lookup, max_age_days):
            pass

        def refill(self):
            return 0

    monkeypatch.setattr(refill_mod, 'FloatRefillWorker', _StubFloatRefillWorker)

    with Session(db_engine) as s:
        _seed_lottery(s)
        u = _seed_user(s)
        dr = _seed_draw(s, 'dlt', '2026050', (1, 2, 3, 4, 5), (6, 7), datetime(2026, 5, 1))
        # 票创建于 2026-07-25（晚于 2026-05-01 开奖日近两个月）→ 对该期不存在
        t = _seed_ticket(s, u.id, (1, 2, 3, 4, 5), (6, 7), created_at=datetime(2026, 7, 25))
        dr_id, t_id = dr.id, t.id

    recompare_all(db_engine)

    with Session(db_engine) as s:
        row = s.exec(
            select(Comparison).where(
                Comparison.draw_result_id == dr_id, Comparison.ticket_id == t_id)
        ).first()
        assert row is None, '票创建晚于开奖日 → recompare 不得补 phantom 比对行'
        assert s.exec(select(PrizeClaim)).first() is None, '更不得产生虚假 PrizeClaim'


def test_recompare_still_compares_ticket_created_before_draw(db_engine):
    """正向护栏：票创建早于开奖日 → recompare 照常创建该行比对（过滤不得误杀正常路径）。

    4+2 在 2026-05-01（2026-01-31 后）按七档表 = 三等 500000 分（固定档，
    不触发保护②浮动回填 → 无网络）。"""
    with Session(db_engine) as s:
        _seed_lottery(s)
        u = _seed_user(s)
        dr = _seed_draw(s, 'dlt', '2026051', (1, 2, 3, 4, 5), (6, 7), datetime(2026, 5, 1))
        t = _seed_ticket(s, u.id, (1, 2, 3, 4, 8), (6, 7), created_at=datetime(2026, 4, 15))
        dr_id, t_id = dr.id, t.id

    recompare_all(db_engine)

    with Session(db_engine) as s:
        row = s.exec(
            select(Comparison).where(
                Comparison.draw_result_id == dr_id, Comparison.ticket_id == t_id)
        ).one()
        assert row.is_win is True and row.prize_tier == 3
        assert row.prize_amount == 500000


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


def test_recompare_dry_run_excludes_ticket_created_after_draw(db_engine):
    """dry-run 计数路径（_count_changed）与实跑同源——晚创建的票不得计入 changed。

    plan-10 phantom 修复（_compare_one 票存在性过滤）只补了实跑侧；_count_changed
    独立重实现比对逻辑，没带 created_at ≤ 开奖日过滤 → dry-run 过估（生产探针：
    dry-run 报 changed=250，实跑实际 ~0）。本测试锁死两路径同源。
    """
    with Session(db_engine) as s:
        _seed_lottery(s)
        u = _seed_user(s)
        _seed_draw(s, 'dlt', '2026054', (1, 2, 3, 4, 5), (6, 7), datetime(2026, 5, 1))
        # 票创建晚于开奖日近两个月 → 对该期不存在（phantom，实跑侧已不比对）
        _seed_ticket(s, u.id, (1, 2, 3, 4, 5), (6, 7), created_at=datetime(2026, 7, 25))

    stats = recompare_all(db_engine, dry_run=True)
    assert stats['changed'] == 0, 'dry-run 不得把晚创建的 phantom 票计入 changed'


def test_recompare_honors_version_gate(db_engine):
    """版本门接线：2026-01-30 的 dlt 期重比按 2019 表（4+2=四等 300000 分），
    2026-01-31 起按七档（4+2=三等 500000 分）——recompute 复用 _compare_one 即自动获得。"""
    with Session(db_engine) as s:
        _seed_lottery(s)
        u = _seed_user(s)
        # 同号 4+2 票：front(1,2,3,4,8) vs draw front(1,2,3,4,9)=4 红；back(5,6)=2 蓝
        # created_at=2026-01-01 早于两期开奖日（票存在性过滤）
        t = _seed_ticket(s, u.id, (1, 2, 3, 4, 8), (5, 6), created_at=datetime(2026, 1, 1))
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


def test_recompare_qlc_third_tier_misrecord_not_preserved(db_engine, monkeypatch):
    """final-review Important 1 回归：qlc 三等（固定→浮动重分类）的存量误录金额不得被
    保护①写回——304500 分（3045 元）是旧固定表误录（官方历来浮动，旧 refill 只回填
    (1,2)），recompare 后必须清 None，交给保护②按真实浮动金额回填。

    保护②（_force_refill_float_rows）在单测里不得打真实网络：stub FloatRefillWorker，
    记录调用（max_age_days=None 不限窗口被触发），但实际不回填，保证断言确定且无网络。"""
    import app.services.refill_service as refill_mod

    captured = {}

    class _StubFloatRefillWorker:
        """保护②的替身：记录实例化参数与调用，不发起官方奖金查询。"""

        def __init__(self, engine, amount_lookup, max_age_days):
            captured['max_age_days'] = max_age_days
            captured['amount_lookup'] = amount_lookup

        def refill(self):
            captured['refill_called'] = True
            return 0

    monkeypatch.setattr(refill_mod, 'FloatRefillWorker', _StubFloatRefillWorker)

    with Session(db_engine) as s:
        _seed_lottery(s, code='qlc', name='七乐彩')
        u = _seed_user(s)
        # qlc 三等 = 6+0：draw 前区 7 码 + 特别号 1；票 6 前区全中、特别号不中
        dr = _seed_draw(s, 'qlc', '2026101', (1, 2, 3, 4, 5, 6, 7), (8,), datetime(2026, 8, 1))
        # created_at 早于开奖日（票存在性过滤），只改时序不改断言
        t = _seed_ticket(s, u.id, (1, 2, 3, 4, 5, 6), (9,), lottery_code='qlc',
                         created_at=datetime(2026, 7, 1))
        # 模拟旧固定表写出的误录行：tier=3 / 304500 分（3045 元）
        s.add(Comparison(user_id=u.id, draw_result_id=dr.id, ticket_id=t.id,
                         hits_json='{"front_hit":6,"back_hit":0}',
                         prize_tier=3, prize_amount=304500, is_win=True))
        s.commit()
        dr_id, t_id = dr.id, t.id

    recompare_all(db_engine)

    with Session(db_engine) as s:
        row = s.exec(select(Comparison).where(
            Comparison.draw_result_id == dr_id, Comparison.ticket_id == t_id)).one()
        assert row.prize_amount is None, (
            f'qlc 三等误录金额不得被保护①写回，实得 {row.prize_amount}'
        )
        assert row.prize_tier == 3 and row.is_win is True  # 重分类为浮动，待保护②回填
    assert captured.get('refill_called') is True, '保护②应被触发（重比收尾强制回填）'
    assert captured.get('max_age_days') is None, '保护②须用 max_age_days=None（不限窗口）'


def test_recompare_preserves_refilled_float_amount(db_engine):
    """发现 1（HIGH）：已回填的浮动档金额不得被 recompare 抹成 None——7 天窗口会让它
    永久丢失。构造：一期 dlt + 中一等（5+2）票，Comparison(tier=1, prize_amount=50000000,
    is_win=True) 模拟已回填 500 万元；recompare 后该行 prize_amount 仍 == 50000000。"""
    with Session(db_engine) as s:
        _seed_lottery(s)
        u = _seed_user(s)
        dr = _seed_draw(s, 'dlt', '26001', (1, 2, 3, 4, 5), (6, 7), datetime(2026, 8, 1))
        # created_at 早于开奖日（票存在性过滤）：重比须真正触碰该票，保护①才被行使
        t = _seed_ticket(s, u.id, (1, 2, 3, 4, 5), (6, 7), created_at=datetime(2026, 7, 1))
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
