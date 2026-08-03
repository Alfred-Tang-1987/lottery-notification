"""CompareService 测试：outbox 原子认领 + domain.compare 接线 + 写 comparisons/prize_claims。

覆盖 spec §7.1 核心数据流：开奖结果 verified 入库后 outbox(pending_comparisons)
被原子认领 → 取追投 tickets → 领域 compare() → 写 comparisons（唯一约束兜底）+
中奖写 prize_claims(pending)。
"""

import json
from datetime import datetime

from sqlmodel import Session, select

from app.models import (
    Comparison,
    DrawCost,
    DrawResult,
    PendingComparison,
    PrizeClaim,
    Ticket,
    User,
)
from app.services.compare_service import CompareService


def _make_user(session, username='u'):
    u = User(username=username, password_hash='x', role='user', invite_code='C')
    session.add(u)
    session.commit()
    session.refresh(u)
    return u


def _seed_draw(session, code='ssq', front=(1, 2, 3, 4, 5, 6), back=(7,)):
    dr = DrawResult(
        lottery_code=code,
        draw_no='062',
        draw_date=datetime.utcnow(),
        numbers_json=json.dumps({'front': list(front), 'back': list(back)}),
        source='mxnzp',
        verified=True,
        version=1,
    )
    session.add(dr)
    session.commit()
    session.refresh(dr)
    pc = PendingComparison(draw_result_id=dr.id)
    session.add(pc)
    session.commit()
    return dr


def _seed_ticket(session, user_id, front=(1, 2, 3, 4, 5, 6), back=(7,)):
    t = Ticket(
        user_id=user_id,
        lottery_code='ssq',
        play_type='single',
        numbers_json=json.dumps({'front': list(front), 'back': list(back)}),
        multiplier=1,
        append=False,
        cost=200,
        enabled=True,
    )
    session.add(t)
    session.commit()
    session.refresh(t)
    return t


def test_compare_writes_comparison_first_prize(db_engine):
    """一等奖（双色球 6红+1蓝）→ 写 comparison(is_win, tier=1) + prize_claim(pending)。"""
    with Session(db_engine) as s:
        u = _make_user(s)
        _seed_draw(s)
        _seed_ticket(s, u.id)
    svc = CompareService(db_engine)
    n = svc.process_pending()  # 处理所有 outbox
    assert n == 1
    with Session(db_engine) as s:
        cmp = s.exec(select(Comparison)).first()
        assert cmp.is_win and cmp.prize_tier == 1
        # 一等奖应生成 prize_claims(pending)
        claim = s.exec(select(PrizeClaim)).first()
        assert claim and claim.status == 'pending'


def test_compare_no_ticket_no_comparison(db_engine):
    """号码池无该彩种注 → outbox 标 processed 但不生成 comparisons。"""
    with Session(db_engine) as s:
        _seed_draw(s)  # 无 ticket
    svc = CompareService(db_engine)
    svc.process_pending()
    with Session(db_engine) as s:
        assert s.exec(select(Comparison)).first() is None
        pc = s.exec(select(PendingComparison)).first()
        assert pc.processed_at is not None  # 标 processed


def test_outbox_claim_idempotent(db_engine):
    """重复 process 不重复比对（已认领的 pending 不再处理）。"""
    with Session(db_engine) as s:
        u = _make_user(s)
        _seed_draw(s)
        _seed_ticket(s, u.id)
    svc = CompareService(db_engine)
    svc.process_pending()
    svc.process_pending()  # 第二次：无新 pending
    with Session(db_engine) as s:
        assert len(s.exec(select(Comparison)).all()) == 1  # 不重复


def test_compare_fixed_prize_applies_multiplier(db_engine):
    """倍投放大固定奖金额（lottery-rules §倍投：中奖金额 × 倍数）。

    双色球三等奖（5红+1蓝 = 3000 元 = 300000 分）× 3 倍 = 900000 分。
    回归保护：旧版直接存 hit.amount（单注 300000），漏乘 multiplier。
    """
    with Session(db_engine) as s:
        u = _make_user(s)
        _seed_draw(s, front=(1, 2, 3, 4, 5, 6), back=(7,))
        # 5 红 + 1 蓝 → 三等奖；倍投 3 倍
        t = Ticket(
            user_id=u.id,
            lottery_code='ssq',
            play_type='single',
            numbers_json=json.dumps({'front': [1, 2, 3, 4, 5, 33], 'back': [7]}),
            multiplier=3,
            append=False,
            cost=600,
            enabled=True,
        )
        s.add(t)
        s.commit()
        s.refresh(t)
    svc = CompareService(db_engine)
    svc.process_pending()
    with Session(db_engine) as s:
        cmp = s.exec(select(Comparison)).first()
        assert cmp is not None and cmp.prize_tier == 3
        assert cmp.prize_amount == 900000  # 300000 分 × 3 倍


def test_compare_float_prize_amount_stays_null_with_multiplier(db_engine):
    """浮动奖（一二等奖）amount 即使有倍投也保持 null——金额未知，倍投在回填（T5）应用。

    防止误把倍投乘到 None 上（None * 3 会崩或变 0）。"""
    with Session(db_engine) as s:
        u = _make_user(s)
        uid = u.id
        _seed_draw(s, front=(1, 2, 3, 4, 5, 6), back=(7,))
        # 一等奖（6红+1蓝，浮动），倍投 5 倍
        s.add(
            Ticket(
                user_id=uid,
                lottery_code='ssq',
                play_type='single',
                numbers_json=json.dumps({'front': [1, 2, 3, 4, 5, 6], 'back': [7]}),
                multiplier=5,
                append=False,
                cost=1000,
                enabled=True,
            )
        )
        s.commit()
    svc = CompareService(db_engine)
    svc.process_pending()
    with Session(db_engine) as s:
        cmps = s.exec(select(Comparison).order_by(Comparison.id)).all()
        assert len(cmps) == 1
        cmp = cmps[0]
        assert cmp.prize_tier == 1
        assert cmp.prize_amount is None  # 浮动奖不乘倍投，回填时应用


def test_compare_isolates_bad_ticket_same_draw(db_engine, caplog):
    """坏注单（格式异常）必须隔离，不影响同期其他注的比对（spec §10 line375）。

    同一期开奖下：用户 u1 的好注（中一等奖）+ 用户 u2 的坏注（numbers_json 损坏）。
    process_pending 不得抛异常，u1 的 comparison 必须照常写入，坏注被跳过并记日志。
    回归保护：旧版 _compare_one 无 per-ticket try/except，坏注的 json.loads 抛
    JSONDecodeError 直接 unwind 整个 _compare_one → u1 的 comparison 也在未 commit 的
    session 里被回滚丢失，且 _claim 已提交 processed_at → 该期永久无比对、中奖漏通知。
    """
    import logging

    with Session(db_engine) as s:
        u1 = _make_user(s, 'good')
        u2 = _make_user(s, 'bad')
        _seed_draw(s)
        _seed_ticket(s, u1.id)  # 好注：6红+7蓝 → 一等奖
        # 坏注：numbers_json 损坏（§10「格式异常」，如 CSV 导入脏数据）
        s.add(
            Ticket(
                user_id=u2.id,
                lottery_code='ssq',
                play_type='single',
                numbers_json='not-valid-json{',
                multiplier=1,
                append=False,
                cost=200,
                enabled=True,
            )
        )
        s.commit()
    svc = CompareService(db_engine)
    with caplog.at_level(logging.WARNING, logger='app.services.compare_service'):
        n = svc.process_pending()  # 不得抛异常
    assert n == 1  # 该期已处理（claim 成功 + 好注照常比对）
    with Session(db_engine) as s:
        cmps = s.exec(select(Comparison)).all()
        # u1 的好注 comparison 必须存在（坏注被隔离，未阻塞它）
        assert len(cmps) == 1
        assert cmps[0].is_win and cmps[0].prize_tier == 1
    # 坏注被跳过并记日志（§10「记录错误日志」，非静默）
    assert any('ticket' in rec.message.lower() for rec in caplog.records)


def test_compare_isolates_bad_ticket_across_draws(db_engine):
    """一个坏注所在的期不得阻塞后续期的比对（跨期隔离，silent-failure 回归保护）。

    期 A 含坏注（numbers_json 损坏 → json.loads 抛 JSONDecodeError），期 B 含好注。
    旧版 process_pending 的 for-pending 循环无 per-draw try/except，期 A 抛异常直接
    中断循环 → 期 B 永不比对。

    坏注触发用 corrupt JSON（稳定的纯 Python 错误），不依赖 fushi 的 Phase-2 未实现——
    Phase 2 实现 fushi 后该注会变合法，测试意图需存活（I3 稳定化）。"""
    with Session(db_engine) as s:
        u = _make_user(s)
        # 期 A：坏注（numbers_json 损坏，§10「格式异常」）
        _seed_draw(s, code='ssq', front=(1, 2, 3, 4, 5, 6), back=(7,))
        s.add(
            Ticket(
                user_id=u.id,
                lottery_code='ssq',
                play_type='single',
                numbers_json='not-valid-json{',  # 稳定的坏注触发，不依赖 Phase 2
                multiplier=1,
                append=False,
                cost=200,
                enabled=True,
            )
        )
        s.commit()
        # 期 B：好注（中一等奖）。_seed_draw 写死 draw_no="062"，另起一期
        dr_b = DrawResult(
            lottery_code='ssq',
            draw_no='063',
            draw_date=datetime.utcnow(),
            numbers_json=json.dumps({'front': [1, 2, 3, 4, 5, 6], 'back': [7]}),
            source='mxnzp',
            verified=True,
            version=1,
        )
        s.add(dr_b)
        s.commit()
        s.refresh(dr_b)
        s.add(PendingComparison(draw_result_id=dr_b.id))
        s.commit()
        _seed_ticket(s, u.id)  # 好注，但挂在期 A 同彩种——下面手动改 draw_result 不需要
        s.commit()
    svc = CompareService(db_engine)
    n = svc.process_pending()  # 不得抛异常；两期都应被 claim
    assert n == 2  # 期 A、期 B 均处理（坏注被隔离，不阻断期 B）
    with Session(db_engine) as s:
        cmps = s.exec(select(Comparison).order_by(Comparison.id)).all()
        # 期 B 的好注 comparison 必须存在
        assert any(c.is_win and c.prize_tier == 1 for c in cmps)


def test_compare_isolates_db_error_does_not_poison_session(db_engine, caplog):
    """C1 回归（quality review Critical）：per-ticket 失败若是 DB 级错误（flush 时抛
    IntegrityError），不得毒化共享 session 导致整期好注 comparison 全丢。

    场景：同期 3 注——好注#1（中一等奖，先比对→先 flush 落库）+ 坏注#2（DB flush
    抛 IntegrityError）+ 好注#3（中一等奖）。正确隔离：坏注#2 被回滚，好注#1、#3
    的 comparison 都落库（共 2 行 winning）。

    旧版（bare except 无 savepoint）实测：坏注#2 flush 抛 IntegrityError 被吞 →
    session 进入 PendingRollback 态 → 好注#3 的 session.exec 撞 PendingRollbackError
    也被吞（误记坏注）→ 末尾 s.commit() 在毒化 session 上变 rollback → 好注#1 已
    flush 的 comparison 也被抹 → 整期 0 行 comparison + claim 已提交 processed_at
    → 永久静默漏通知。

    DB 错触发用 monkeypatch 包装 _upsert_comparison：对坏注#2 的调用抛 IntegrityError
    （模拟 flush 时 NOT NULL/constraint 失败——真实场景如 database is locked /
    uq_cmp_draw_ticket 竞态 / schema 不匹配），其余正常。
    """
    import logging
    from unittest.mock import patch

    with Session(db_engine) as s:
        u = _make_user(s)
        _seed_draw(s)  # draw_no="062"
        # 3 注：好#1（蓝7中一等）、坏#2（DB flush 错）、好#3（蓝7中一等）
        _seed_ticket(s, u.id, front=(1, 2, 3, 4, 5, 6), back=(7,))
        bad_t = Ticket(
            user_id=u.id,
            lottery_code='ssq',
            play_type='single',
            numbers_json=json.dumps({'front': [2, 3, 4, 5, 6, 7], 'back': [8]}),
            multiplier=1,
            append=False,
            cost=200,
            enabled=True,
        )
        s.add(bad_t)
        s.commit()
        s.refresh(bad_t)
        _seed_ticket(s, u.id, front=(1, 2, 3, 4, 5, 6), back=(7,))
        s.commit()
        bad_ticket_id = bad_t.id

    # 包装 _upsert_comparison：坏注#2 模拟 flush 时 DB 约束失败（NOT NULL 违反）。
    # 关键：必须在 session.add 之后、flush 之时抛——这才毒化 session（entry 处抛不毒化，
    # 与 JSONDecodeError 同路径）。复刻 _upsert_comparison else 分支但 hits_json=None 触发
    # NOT NULL，flush 抛 IntegrityError，session 进入 PendingRollback 态。
    real_upsert = CompareService._upsert_comparison
    poisoned = {'called': False}

    def patched_upsert(self, session, *, user_id, draw_result_id, ticket_id, hit, multiplier=1):
        if ticket_id == bad_ticket_id:
            poisoned['called'] = True
            # 复刻真实 flush 时 DB 错：add 一个违反 NOT NULL 的行，flush 时抛
            cmp = Comparison(
                user_id=user_id,
                draw_result_id=draw_result_id,
                ticket_id=ticket_id,
                hits_json=None,  # NOT NULL 违反 → flush 抛 IntegrityError（毒化 session）
                prize_tier=hit.tier,
                prize_amount=None,
                is_win=hit.is_win,
            )
            session.add(cmp)
            session.flush()  # ← flush 时抛，session 进入 PendingRollback（C1 核心）
            return
        return real_upsert(
            self,
            session,
            user_id=user_id,
            draw_result_id=draw_result_id,
            ticket_id=ticket_id,
            hit=hit,
            multiplier=multiplier,
        )

    svc = CompareService(db_engine)
    with patch.object(CompareService, '_upsert_comparison', patched_upsert), caplog.at_level(
        logging.WARNING, logger='app.services.compare_service'
    ):
        svc.process_pending()  # 不得抛异常

    assert poisoned['called'], '测试前提：坏注#2 的 upsert 确被调用并注入了 flush 时 DB 错'
    with Session(db_engine) as s:
        cmps = s.exec(select(Comparison)).all()
        winning = [c for c in cmps if c.is_win and c.prize_tier == 1]
        # 两注好注的 comparison 必须都落库——DB 错只毒化坏注#2（被 savepoint 隔离），不波及好注
        assert len(winning) == 2, (
            f'C1：flush 时 DB 错须用 savepoint 隔离到坏注#2，好注#1/#3 应存活，'
            f'实际 {len(winning)} 行 winning（旧版 bare-except 无 savepoint 会全丢→0 行）'
        )


def test_correction_resets_unresolved_so_row_reenters_refill(db_engine):
    """I2（quality review Important）：官方更正重比命中同一注时，须重置 unresolved=False。

    场景：一注已标 unresolved=True（浮奖超期未回填，refill 排除它）。后官方更正开奖
    结果触发重比，_upsert_comparison 走 existing 分支重写 prize_amount（可能重置回 None
    待派奖）。若不重置 unresolved，该行永久卡死：refill 永远排除 unresolved=True，无人
    再查官方金额 → 中奖金额永远 null（spec §7.1 浮奖回填契约被破坏）。

    正确：更正重比命中时 existing.unresolved=False，让该行重回回填管线。
    """
    with Session(db_engine) as s:
        u = _make_user(s)
        _seed_draw(s)  # draw_no=062，6红+7蓝
        _seed_ticket(s, u.id)  # 好注，中一等奖
        s.commit()
    # 首次比对 → comparison 落库（prize_tier=1, prize_amount=None 浮动奖待派奖）
    CompareService(db_engine).process_pending()
    with Session(db_engine) as s:
        cmp = s.exec(select(Comparison)).first()
        cmp.unresolved = True  # 模拟浮奖超期未回填被标记
        s.commit()
        dr_id = cmp.draw_result_id
        cmp_id = cmp.id
    # 官方更正 → 重新生成 outbox（Plan 03 T6 DrawCorrectService 的职责，此处直接模拟）
    with Session(db_engine) as s:
        s.add(PendingComparison(draw_result_id=dr_id))
        s.commit()
    # 重比（走 _upsert_comparison existing 分支）
    CompareService(db_engine).process_pending()
    with Session(db_engine) as s:
        cmp = s.get(Comparison, cmp_id)
        assert cmp.unresolved is False, '官方更正重比命中须重置 unresolved=False，否则该行永久卡死、永不再查官方金额'
        assert cmp.corrected_at is not None  # 确认走了 existing 更新分支


# ---------------------------------------------------------------------------
# DrawCost 记账（spec §4：成本按开奖日记账；只要有 enabled 追投注就记）
# ---------------------------------------------------------------------------


def test_compare_records_draw_cost_per_user(db_engine):
    """比对一期 -> DrawCost 落库，cost=该用户该彩种该期所有 enabled 追投注 cost 之和。"""
    with Session(db_engine) as s:
        u = _make_user(s)
        uid = u.id
        _seed_ticket(s, u.id)  # cost=200
        t2 = Ticket(
            user_id=u.id,
            lottery_code='ssq',
            play_type='single',
            numbers_json=json.dumps({'front': [1, 2, 3, 4, 5, 6], 'back': [7]}),
            multiplier=1,
            append=False,
            cost=300,
            enabled=True,
        )
        s.add(t2)
        s.commit()
        dr = _seed_draw(s)
        draw_date = dr.draw_date
    CompareService(db_engine).process_pending()
    with Session(db_engine) as s:
        dc = s.exec(select(DrawCost)).first()
        assert dc is not None, '比对后应记 DrawCost'
        assert dc.user_id == uid
        assert dc.lottery_code == 'ssq'
        assert dc.draw_no == '062'
        assert dc.cost == 500  # 200 + 300
        assert dc.draw_date == draw_date  # 取自 DrawResult.draw_date


def test_compare_draw_cost_excludes_disabled_tickets(db_engine):
    """disabled 注不计入期次成本（比对范围由 enabled 号码池决定）。"""
    with Session(db_engine) as s:
        u = _make_user(s)
        _seed_ticket(s, u.id)  # cost=200 enabled
        s.add(Ticket(
            user_id=u.id,
            lottery_code='ssq',
            play_type='single',
            numbers_json=json.dumps({'front': [1, 2, 3, 4, 5, 6], 'back': [7]}),
            multiplier=1, append=False, cost=999, enabled=False,
        ))
        s.commit()
        _seed_draw(s)
    CompareService(db_engine).process_pending()
    with Session(db_engine) as s:
        dc = s.exec(select(DrawCost)).first()
        assert dc.cost == 200  # 仅 enabled 的 200


def test_compare_draw_cost_idempotent_on_reprocess(db_engine):
    """更正重比（重新认领 outbox）-> DrawCost 原地更新，不重复记账、不产生第二行。"""
    with Session(db_engine) as s:
        u = _make_user(s)
        _seed_ticket(s, u.id)  # cost=200
        dr = _seed_draw(s)
        dr_id = dr.id
    CompareService(db_engine).process_pending()
    # 模拟官方更正重比：重新入 outbox
    with Session(db_engine) as s:
        s.add(PendingComparison(draw_result_id=dr_id))
        s.commit()
    CompareService(db_engine).process_pending()
    with Session(db_engine) as s:
        dcs = list(s.exec(select(DrawCost)).all())
        assert len(dcs) == 1, '重比应 upsert 不重复'
        assert dcs[0].cost == 200


def test_compare_draw_cost_per_user_isolation(db_engine):
    """两用户同追投同彩种 -> 各自 DrawCost 行，成本独立（用户隔离）。"""
    with Session(db_engine) as s:
        u1 = _make_user(s, 'u1')
        u2 = _make_user(s, 'u2')
        uid1, uid2 = u1.id, u2.id
        _seed_ticket(s, u1.id)  # 200
        s.add(Ticket(
            user_id=u2.id, lottery_code='ssq', play_type='single',
            numbers_json=json.dumps({'front': [1, 2, 3, 4, 5, 6], 'back': [7]}),
            multiplier=1, append=False, cost=400, enabled=True,
        ))
        s.commit()
        _seed_draw(s)
    CompareService(db_engine).process_pending()
    with Session(db_engine) as s:
        dcs = {dc.user_id: dc.cost for dc in s.exec(select(DrawCost)).all()}
        assert dcs == {uid1: 200, uid2: 400}
