from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import session as sa_session_module
from sqlmodel import Session

from app.models import Comparison, DrawResult, Ticket, User
from app.services.refill_service import FloatRefillWorker


def _seed_float_win(engine, days_ago=0, tier=1, suffix=''):
    """seed 一条浮动奖中奖 comparison（prize_amount=None）。
    suffix 区分多次调用（username/draw_no 均有唯一约束）。"""
    with Session(engine) as s:
        u = User(username=f'u{suffix}', password_hash='x', role='user', invite_code='C')
        s.add(u)
        s.commit()
        s.refresh(u)
        dr = DrawResult(
            lottery_code='ssq',
            draw_no=f'062{suffix}',
            draw_date=datetime.utcnow() - timedelta(days=days_ago),
            numbers_json='{"front":[1,2,3,4,5,6],"back":[7]}',
            source='mxnzp',
            verified=True,
            version=1,
        )
        s.add(dr)
        s.commit()
        s.refresh(dr)
        t = Ticket(
            user_id=u.id,
            lottery_code='ssq',
            play_type='single',
            numbers_json='{"front":[1,2,3,4,5,6],"back":[7]}',
            multiplier=1,
            cost=200,
        )
        s.add(t)
        s.commit()
        s.refresh(t)
        cmp = Comparison(
            user_id=u.id,
            draw_result_id=dr.id,
            ticket_id=t.id,
            hits_json='{}',
            prize_tier=tier,
            prize_amount=None,
            is_win=True,
            created_at=datetime.utcnow() - timedelta(days=days_ago),
        )
        s.add(cmp)
        s.commit()
        s.refresh(cmp)
        return cmp.id, f'062{suffix}'  # comparison_id, draw_no（供 lookup 路由用）


def test_refill_updates_null_amount(db_engine):
    cmp_id, _ = _seed_float_win(db_engine, days_ago=1)
    from unittest.mock import MagicMock

    amount_lookup = MagicMock(return_value=5_000_000)  # 官方公布 5 万元（分）
    worker = FloatRefillWorker(db_engine, amount_lookup=amount_lookup, max_age_days=7)
    n = worker.refill()
    assert n == 1
    with Session(db_engine) as s:
        assert s.get(Comparison, cmp_id).prize_amount == 5_000_000


def test_refill_skips_after_max_age_and_marks_unresolved(db_engine):
    """超 7 天的浮奖不再查，并标记 unresolved=true。"""
    cmp_id, _ = _seed_float_win(db_engine, days_ago=10)
    from unittest.mock import MagicMock

    worker = FloatRefillWorker(db_engine, amount_lookup=MagicMock(return_value=999), max_age_days=7)
    assert worker.refill() == 0  # 超期不查
    with Session(db_engine) as s:
        cmp = s.get(Comparison, cmp_id)
        assert cmp.prize_amount is None
        assert cmp.unresolved is True  # 必须标记 unresolved


def test_refill_explicit_tier_filter_only_float(db_engine):
    """仅 prize_tier IN (1,2) 的浮动档才进入回填；三等奖固定档 prize_amount 有值，不应被选中。"""
    cmp_id, _ = _seed_float_win(db_engine, days_ago=1, tier=3)
    # 手动把 prize_amount 设为 None 模拟错误状态（固定档不应为 None）
    with Session(db_engine) as s:
        cmp = s.get(Comparison, cmp_id)
        cmp.prize_amount = None
        s.commit()
    from unittest.mock import MagicMock

    worker = FloatRefillWorker(db_engine, amount_lookup=MagicMock(return_value=999), max_age_days=7)
    assert worker.refill() == 0  # tier 3 不是浮动档，不应回填
    with Session(db_engine) as s:
        cmp = s.get(Comparison, cmp_id)
        assert cmp.prize_amount is None  # 未被修改
        assert cmp.unresolved is not True


# ────────── quality review C1 修复覆盖 ──────────


def test_refill_lookup_raises_isolates_row_and_still_marks_expired(db_engine):
    """C1 回归（quality review Critical）：单行 amount_lookup 抛异常不得中断后续行回填，
    也不得跳过超期标记。

    场景：cutoff 内 2 行（A 触发 lookup 抛异常、B 正常应回填）+ 超期 1 行 C。
    正确隔离：A 被隔离（不阻断），B 照常回填，C 照常标 unresolved。三者互不影响。

    旧版（amount_lookup 无 try/except）：A 第一次 raise 即中断循环 → B 静默丢失 +
    expired 标记块不可达 → C 永远 unresolved=False、每轮重查、永不 resolve（T5 的
    terminal-state 契约被破坏，每日调度复利恶化）。
    """
    a_id, a_no = _seed_float_win(db_engine, days_ago=1, suffix='A')  # cutoff 内，触发 raise
    b_id, _ = _seed_float_win(db_engine, days_ago=1, suffix='B')  # cutoff 内，应回填
    c_id, _ = _seed_float_win(db_engine, days_ago=10, suffix='C')  # 超期，应标 unresolved

    # A 的 amount_lookup 抛异常；B 正常（C 超期不会到 lookup）
    def lookup(lottery_code, draw_no, tier):
        if draw_no == a_no:  # A 触发源故障
            raise RuntimeError('source 5xx for A')
        return 5_000_000

    worker = FloatRefillWorker(db_engine, amount_lookup=lookup, max_age_days=7)
    n = worker.refill()  # 不得抛异常
    assert n == 1, f'仅 B 回填（A 被隔离），实际 {n}'
    with Session(db_engine) as s:
        a = s.get(Comparison, a_id)
        b = s.get(Comparison, b_id)
        c = s.get(Comparison, c_id)
        assert a.prize_amount is None, 'A 源故障被隔离，不回填'
        assert b.prize_amount == 5_000_000, 'B 不受 A 故障影响，照常回填'
        assert c.prize_amount is None, 'C 超期不查'
        assert c.unresolved is True, 'C 超期须标 unresolved（expired 块即使 A raise 也须执行）'
        assert a.unresolved is not True, 'A cutoff 内未超期，不标 unresolved（下轮重试）'


def test_refill_lookup_returns_none_is_patient_retry(db_engine):
    """Minor 锁：amount_lookup 返回 None（官方尚未公布）→ 该行跳过、保持 prize_amount=None、
    unresolved=False（下轮再查，区别于超期标 unresolved）。"""
    cmp_id, _ = _seed_float_win(db_engine, days_ago=1)
    from unittest.mock import MagicMock

    worker = FloatRefillWorker(db_engine, amount_lookup=MagicMock(return_value=None), max_age_days=7)
    assert worker.refill() == 0
    with Session(db_engine) as s:
        cmp = s.get(Comparison, cmp_id)
        assert cmp.prize_amount is None  # 未公布，不回填
        assert cmp.unresolved is not True  # cutoff 内，下轮重试，不标 unresolved


# ────────── quality re-review tz 回归修复 ──────────


def test_refill_boundary_not_misclassified_expired_by_tz(db_engine):
    """tz 回归（quality re-review）：created_at（模型默认 naive UTC）与 cutoff 须同时区比较，
    否则 SQLite 字符串比较会让恰好 7 天的行被误判「超期」→ 标 unresolved → 永久排除回填
    → 浮动奖金额永久 null（spec §7.1 核心特性静默失效）。

    场景：created_at = 6 天 20 小时前（naive UTC，与模型 default_factory 一致）——明确在
    [0, 7d] 窗口内（refillable），且落在 tz bug 的 8h 误判区（真实 [7d-8h, 7d) 内的行被
    误判超期；6d20h = 7d-4h 在该区）。
    正确：该行窗口内 → 应被选入回填（refillable），不标 unresolved。

    旧 bug（I3 引入）：cutoff 用 aware CST（_now()），created_at 是 naive UTC → SQLite 字符串
    比较把 aware-CST 串排在 naive-UTC 串之后 → created_at < cutoff 误成立 → 该行被判超期、
    标 unresolved、永久排除 → 浮奖金额永远 null。8 小时窗口（CST=UTC+8）内的边界行全中招。
    """
    from datetime import datetime, timedelta

    # created_at 用 naive UTC（忠实复刻模型 default_factory=datetime.utcnow 的真实存储形态）。
    # 6d20h 前 = 窗口内（< 7d），且在 tz bug 8h 误判区内（7d-8h=6d16h < 6d20h < 7d）。
    created_at_naive_utc = datetime.utcnow() - timedelta(days=6, hours=20)
    with Session(db_engine) as s:
        u = User(username='utz', password_hash='x', role='user', invite_code='C')
        s.add(u)
        s.commit()
        s.refresh(u)
        dr = DrawResult(
            lottery_code='ssq',
            draw_no='062tz',
            draw_date=datetime.utcnow(),
            numbers_json='{"front":[1,2,3,4,5,6],"back":[7]}',
            source='mxnzp',
            verified=True,
            version=1,
        )
        s.add(dr)
        s.commit()
        s.refresh(dr)
        t = Ticket(
            user_id=u.id,
            lottery_code='ssq',
            play_type='single',
            numbers_json='{"front":[1,2,3,4,5,6],"back":[7]}',
            multiplier=1,
            cost=200,
        )
        s.add(t)
        s.commit()
        s.refresh(t)
        cmp = Comparison(
            user_id=u.id,
            draw_result_id=dr.id,
            ticket_id=t.id,
            hits_json='{}',
            prize_tier=1,
            prize_amount=None,
            is_win=True,
            created_at=created_at_naive_utc,  # 恰好 7 天前，naive UTC（模型真实形态）
        )
        s.add(cmp)
        s.commit()
        s.refresh(cmp)
        cmp_id = cmp.id

    from unittest.mock import MagicMock

    worker = FloatRefillWorker(db_engine, amount_lookup=MagicMock(return_value=5_000_000), max_age_days=7)
    n = worker.refill()
    with Session(db_engine) as s:
        cmp = s.get(Comparison, cmp_id)
        # 恰好 7 天 = 窗口边界内（>= cutoff）→ 应回填，不标 unresolved
        assert n == 1, f'恰好 7 天的行应 refillable（窗口内），实际回填 {n}'
        assert cmp.prize_amount == 5_000_000, '边界行应被回填'
        assert cmp.unresolved is not True, '边界行不得因 tz 字符串比较被误判超期标 unresolved → 永久排除回填'


# ────────── PermanentLookupError 契约（plan 07 T2 fix-issue critical）──────────


def test_refill_permanent_lookup_error_marks_unresolved_immediately(db_engine):
    """PermanentLookupError（永久形状错误，如 typemoney='abc'）→ 立即标 unresolved=True。

    回归 fix-issue（review round 2 critical）：CwlPrizeSource 在永久数据形状错误时 raise
    PermanentLookupError（区别于「未公布」返回 None、transient HTTP 错误 raise 其他异常）。
    worker except 分支须识别该异常类型并立即标 unresolved——避免永久 schema bug 被当
    「未公布」每轮重查 7 天，期间日志噪声大且定位困难，最终才由 _mark_expired_unresolved
    兜底标记。

    三态语义（spec §7.1 line 276「超期标 unresolved 不再查」的精神延伸到「永久错误」）：
      - 返回 None       → 暂未公布，下轮重试（unresolved=False）
      - PermanentLookupError → 永久错误，立即标 unresolved（不再重试）
      - 其他 Exception  → transient，下轮重试（unresolved=False，保持既有隔离行为）
    """
    from app.adapters.cwl_prize import PermanentLookupError

    perm_id, perm_no = _seed_float_win(db_engine, days_ago=1, suffix='P')

    def lookup(lottery_code, draw_no, tier):
        if draw_no == perm_no:
            raise PermanentLookupError('typemoney=abc not parseable')
        return 5_000_000

    worker = FloatRefillWorker(db_engine, amount_lookup=lookup, max_age_days=7)
    n = worker.refill()
    assert n == 0, 'permanent error 不回填'
    with Session(db_engine) as s:
        cmp = s.get(Comparison, perm_id)
        assert cmp.prize_amount is None, 'permanent error 不回填金额'
        # 关键断言：永久错误**立即**标 unresolved（不等 7 天超期兜底）
        assert cmp.unresolved is True, (
            'PermanentLookupError 须立即标 unresolved，避免永久 schema bug 被当未公布重试 7 天'
        )


def test_refill_transient_exception_still_retries_not_unresolved(db_engine):
    """transient Exception（非 PermanentLookupError）→ 保持既有隔离行为，下轮重试不标 unresolved。

    防回归：PermanentLookupError 分支引入后，通用 transient 异常路径不得被误改为也标
    unresolved（会破坏 test_refill_lookup_raises_isolates_row_and_still_marks_expired line 134
    既有契约：transient raise → unresolved is not True，下轮重试）。
    """
    trans_id, trans_no = _seed_float_win(db_engine, days_ago=1, suffix='T')

    def lookup(lottery_code, draw_no, tier):
        if draw_no == trans_no:
            raise RuntimeError('source 5xx transient')
        return 5_000_000

    worker = FloatRefillWorker(db_engine, amount_lookup=lookup, max_age_days=7)
    worker.refill()
    with Session(db_engine) as s:
        cmp = s.get(Comparison, trans_id)
        # transient 异常：下轮重试，不立即标 unresolved
        assert cmp.unresolved is not True, (
            'transient Exception 不得标 unresolved（区别于 PermanentLookupError）'
        )


# ────────── 批末兜底标记 unconditional（fix-round ★ OPEN）──────────


def test_refill_marks_expired_even_when_success_commit_fails(db_engine, monkeypatch):
    """成功回填路径 s.commit() 抛 DB 异常时，批末 _mark_expired_unresolved 仍须执行（finally 兜底）。

    回归 fix-round（review round 3 important）：旧实现 _mark_expired_unresolved(cutoff) 只在
    for 循环正常走完后才执行（refill_service.py line 116 位于循环外的顺序代码路径）——
    成功分支 s.commit()（line 108）抛 database is locked / disk full 时异常冒泡出 refill()，
    expired 行**永不标记 unresolved** → 下轮仍被 pending 选中（created_at < cutoff 但未标
    unresolved）→ 每轮重查永不终止，terminal-state 契约被破坏（silent-failure）。

    修法契约：_mark_expired_unresolved 移入 try/finally，即便循环内任意 commit 抛异常，
    超期行仍被标 unresolved；原异常向上传播（调用方知晓回填失败）。
    """
    a_id, _ = _seed_float_win(db_engine, days_ago=1, suffix='F')  # cutoff 内，可回填成功
    c_id, _ = _seed_float_win(db_engine, days_ago=10, suffix='G')  # 超期，应标 unresolved

    orig_commit = sa_session_module.Session.commit
    refill_commits = {'n': 0}

    def fail_on_refill_success_commit(self):
        refill_commits['n'] += 1
        if refill_commits['n'] == 1:
            # refill() 主循环内第一次 commit = 成功回填 a 的 prize_amount——模拟 DB 故障
            raise RuntimeError('database is locked')
        return orig_commit(self)

    monkeypatch.setattr(sa_session_module.Session, 'commit', fail_on_refill_success_commit)

    worker = FloatRefillWorker(db_engine, amount_lookup=MagicMock(return_value=5_000_000), max_age_days=7)

    with pytest.raises(RuntimeError, match='database is locked'):
        worker.refill()

    # 关键断言：即便成功分支 commit 抛异常冒泡，超期行 C 仍须被标 unresolved
    # （finally 兜底，terminal-state 契约不因单行 commit 故障被破坏）。
    with Session(db_engine) as s:
        c = s.get(Comparison, c_id)
        assert c.unresolved is True, (
            '成功分支 commit 抛异常时，批末 expired 标记仍须执行（finally）——'
            '否则超期行永不标记、每轮重查、terminal-state 契约破坏'
        )


def test_refill_marks_expired_even_when_permanent_unresolved_commit_fails(db_engine, monkeypatch):
    """PermanentLookupError 分支 s.commit()（标 unresolved 那次）抛异常时，expired 标记仍执行。

    同 fix-round OPEN：except PermanentLookupError 分支内 s.commit()（refill_service.py
    line 82）也可能抛 DB 异常——旧实现该异常冒泡出 refill()，_mark_expired_unresolved
    不可达 → 超期行静默漏标。finally 兜底后即便该分支 commit 失败，expired 行仍标记。
    """
    from app.adapters.cwl_prize import PermanentLookupError

    p_id, p_no = _seed_float_win(db_engine, days_ago=1, suffix='H')  # cutoff 内，触发 permanent error
    c_id, _ = _seed_float_win(db_engine, days_ago=10, suffix='I')  # 超期，应标 unresolved

    orig_commit = sa_session_module.Session.commit
    refill_commits = {'n': 0}

    def fail_on_permanent_mark_commit(self):
        refill_commits['n'] += 1
        if refill_commits['n'] == 1:
            # refill() 内第一次 commit = PermanentLookupError 分支标 p 为 unresolved——模拟故障
            raise RuntimeError('disk full')
        return orig_commit(self)

    monkeypatch.setattr(sa_session_module.Session, 'commit', fail_on_permanent_mark_commit)

    def lookup(lottery_code, draw_no, tier):
        if draw_no == p_no:
            raise PermanentLookupError('typemoney=abc')
        return None

    worker = FloatRefillWorker(db_engine, amount_lookup=lookup, max_age_days=7)

    with pytest.raises(RuntimeError, match='disk full'):
        worker.refill()

    with Session(db_engine) as s:
        c = s.get(Comparison, c_id)
        assert c.unresolved is True, (
            'PermanentLookupError 分支 commit 抛异常时，批末 expired 标记仍须执行（finally）'
        )
