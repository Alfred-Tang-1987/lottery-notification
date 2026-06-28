"""FetchService 测试：双源交叉校验 + 部分源 grace + 退避 + 幂等（spec §7.2/§10）。

每个测试用 MagicMock 模拟 DrawSource（满足 DrawSource 协议：name:str + fetch），
db_engine fixture 提供临时 SQLite。不打真实 API。
"""

import json
import logging
from datetime import date
from unittest.mock import MagicMock

from sqlmodel import Session, select

from app.adapters.base import DrawNumbers
from app.models import DrawResult, PendingComparison
from app.services.fetch_service import FetchService


def _dn(code, no, front, back=None, draw_date=date(2026, 6, 21)):
    """构造归一化开奖号码（adapter 输出形态）。draw_date 默认 2026-06-21。"""
    return DrawNumbers(
        lottery_code=code,
        draw_no=no,
        draw_date=draw_date,
        front=tuple(front),
        back=tuple(back) if back else None,
    )


def _src(fetch_return, name='src'):
    """构造满足 DrawSource 协议的 mock（name:str + fetch）。"""
    m = MagicMock()
    m.name = name  # 协议要求 name:str；Mock 默认返回 Mock 对象，不符合
    m.fetch.return_value = fetch_return
    return m


def test_fetch_cross_verify_match(db_engine):
    """双源一致 → verified=true，入库。"""
    primary = _src(_dn('ssq', '062', [1, 2, 3, 4, 5, 6], [7]), name='mxnzp')
    backup = _src(_dn('ssq', '062', [1, 2, 3, 4, 5, 6], [7]), name='juhe')
    svc = FetchService(primary, backup, db_engine, grace_seconds=0)
    result = svc.fetch_and_store('ssq')
    assert result.stored and result.verified
    with Session(db_engine) as s:
        dr = s.exec(select(DrawResult)).first()
        assert dr is not None
        assert dr.verified and not dr.single_source


def test_fetch_cross_verify_mismatch_rejected(db_engine):
    """双源不一致 → verified=false，不入库号码。"""
    primary = _src(_dn('ssq', '062', [1, 2, 3, 4, 5, 6], [7]), name='mxnzp')
    backup = _src(_dn('ssq', '062', [1, 2, 3, 4, 5, 6], [8]), name='juhe')  # 蓝球不同
    svc = FetchService(primary, backup, db_engine, grace_seconds=0)
    r = svc.fetch_and_store('ssq')
    assert not r.verified  # 拒绝
    assert not r.stored


def test_fetch_partial_source_single(db_engine):
    """主源有、备源无 → grace 后单源 verified=true single_source=true。"""
    primary = _src(_dn('ssq', '062', [1, 2, 3, 4, 5, 6], [7]), name='mxnzp')
    backup = _src(None, name='juhe')  # 未开奖/无
    svc = FetchService(primary, backup, db_engine, grace_seconds=0)
    r = svc.fetch_and_store('ssq')
    assert r.stored and r.verified and r.single_source


def test_fetch_not_drawn(db_engine):
    """双源都无 → 未开奖，不存。"""
    primary = _src(None, name='mxnzp')
    backup = _src(None, name='juhe')
    svc = FetchService(primary, backup, db_engine, grace_seconds=0)
    r = svc.fetch_and_store('ssq')
    assert not r.stored and r.not_drawn


def test_fetch_cross_verify_positional_order_sensitive(db_engine):
    """positional 彩种（福彩3D）双源号码顺序不同 → 必须判 mismatch，verified=False。
    回归保护：旧版 _numbers_match 用 sorted 会把 (1,2,3) 与 (3,2,1) 判同，对 positional 失效。
    参照 docs/reference/lottery-rules.md：3D/排列3/排列5 按位、顺序敏感。"""
    primary = _src(_dn('fc3d', '062', [1, 2, 3]), name='mxnzp')
    backup = _src(_dn('fc3d', '062', [3, 2, 1]), name='juhe')  # 同号不同序
    svc = FetchService(primary, backup, db_engine, grace_seconds=0)
    r = svc.fetch_and_store('fc3d')
    assert not r.verified  # 顺序不同 → mismatch，拒绝入库


def test_fetch_cross_verify_hybrid_front_order_sensitive(db_engine):
    """hybrid 彩种（七星彩）前区是 PositionalDigits（有序、每位独立、允许跨位重复），
    双源前区同数字不同位 → 必须判 mismatch，verified=False。

    回归保护：旧版 _numbers_match 把 hybrid 前区塞进 partition 的 sorted 分支，
    把 (1,1,2,3,4,5) 与 (5,4,3,2,1,1) 判同——但按位对应规则下这是不同的开奖结果，
    削弱了交叉校验安全网（spec §5.1 line154 / §5.4 line205 / §7.2 line290）。
    参照 docs/reference/lottery-rules.md：七星彩前区按位对应、允许跨位重复、无需连续。"""
    primary = _src(_dn('qxc', '062', [1, 1, 2, 3, 4, 5], [7]), name='mxnzp')
    backup = _src(_dn('qxc', '062', [5, 4, 3, 2, 1, 1], [7]), name='juhe')  # 同号不同位
    svc = FetchService(primary, backup, db_engine, grace_seconds=0)
    r = svc.fetch_and_store('qxc')
    assert not r.verified  # 按位不同 → mismatch，拒绝入库


def test_fetch_cross_verify_hybrid_back_mismatch(db_engine):
    """hybrid 七星彩后区单值(0-14)不同 → mismatch。后区是标量，顺序无意义但数值要一致。"""
    primary = _src(_dn('qxc', '062', [1, 2, 3, 4, 5, 6], [7]), name='mxnzp')
    backup = _src(_dn('qxc', '062', [1, 2, 3, 4, 5, 6], [8]), name='juhe')  # 后区不同
    svc = FetchService(primary, backup, db_engine, grace_seconds=0)
    r = svc.fetch_and_store('qxc')
    assert not r.verified


def test_fetch_cross_verify_hybrid_same_order_match(db_engine):
    """hybrid 七星彩双源前区按位完全一致 + 后区一致 → verified=true（正常入库）。"""
    primary = _src(_dn('qxc', '062', [1, 1, 2, 3, 4, 5], [14]), name='mxnzp')
    backup = _src(_dn('qxc', '062', [1, 1, 2, 3, 4, 5], [14]), name='juhe')
    svc = FetchService(primary, backup, db_engine, grace_seconds=0)
    r = svc.fetch_and_store('qxc')
    assert r.stored and r.verified


def test_fetch_both_sources_failed(db_engine):
    """双源都故障（抛异常）→ 告警不存（spec §10）。注入 no-op sleep 避免退避真睡眠。"""
    primary = _src(_dn('ssq', '062', [1, 2, 3, 4, 5, 6], [7]), name='mxnzp')
    primary.fetch.side_effect = RuntimeError('timeout')
    backup = _src(_dn('ssq', '062', [1, 2, 3, 4, 5, 6], [7]), name='juhe')
    backup.fetch.side_effect = RuntimeError('timeout')
    svc = FetchService(primary, backup, db_engine, max_attempts=2, backoff_base=0, sleep=lambda *_: None)
    r = svc.fetch_and_store('ssq')
    assert not r.stored and not r.verified and not r.not_drawn
    assert r.error  # 有错误标记（供告警）


def test_fetch_idempotent_repeated(db_engine):
    """同彩种同期重复抓取 → 幂等，不重复入库。"""
    primary = _src(_dn('ssq', '062', [1, 2, 3, 4, 5, 6], [7]), name='mxnzp')
    backup = _src(_dn('ssq', '062', [1, 2, 3, 4, 5, 6], [7]), name='juhe')
    svc = FetchService(primary, backup, db_engine, grace_seconds=0)
    r1 = svc.fetch_and_store('ssq')
    r2 = svc.fetch_and_store('ssq')  # 同期号
    assert r1.stored and r2.stored
    with Session(db_engine) as s:
        assert len(s.exec(select(DrawResult)).all()) == 1  # 仅一条


def test_fetch_partial_grace_recovers_dual_source(db_engine):
    """部分源 grace：首抓主源有备源无 → grace 内重抓到备源且一致 → 双源 verified=true。
    grace_seconds>0 触发 grace 块；注入 no-op sleep 避免 5 分钟真等待。"""
    primary = _src(_dn('ssq', '062', [1, 2, 3, 4, 5, 6], [7]), name='mxnzp')
    backup = MagicMock()
    backup.name = 'juhe'
    # 首次无 → grace 重抓时有
    backup.fetch.side_effect = [None, _dn('ssq', '062', [1, 2, 3, 4, 5, 6], [7])]
    svc = FetchService(primary, backup, db_engine, grace_seconds=1, sleep=lambda *_: None)
    r = svc.fetch_and_store('ssq')
    assert r.stored and r.verified and not r.single_source


# ───────────────────── review round-2 修复覆盖 ─────────────────────
# spec §7.1 line271 / §7.2 / §10 + silent-failure-hunter + quality


def test_store_writes_pending_comparison_on_verified(db_engine):
    """spec §7.1 line271：draw_results 首次 verified=true 入库后写 pending_comparisons 一行。
    未开奖/拒绝入库不得写 outbox（否则 CompareService 空转）。"""
    primary = _src(_dn('ssq', '062', [1, 2, 3, 4, 5, 6], [7]), name='mxnzp')
    backup = _src(_dn('ssq', '062', [1, 2, 3, 4, 5, 6], [7]), name='juhe')
    svc = FetchService(primary, backup, db_engine, grace_seconds=0)
    r = svc.fetch_and_store('ssq')
    assert r.stored and r.verified
    with Session(db_engine) as s:
        dr = s.exec(select(DrawResult)).first()
        pc = s.exec(select(PendingComparison).where(PendingComparison.draw_result_id == dr.id)).first()
        assert pc is not None, 'verified 入库必须写 pending_comparisons outbox'
        assert pc.processed_at is None  # 待 CompareService 认领


def test_store_writes_pending_on_single_source(db_engine):
    """单源 verified=true 也触发 outbox（spec §7.2：单源 verified=true）。"""
    primary = _src(_dn('ssq', '062', [1, 2, 3, 4, 5, 6], [7]), name='mxnzp')
    backup = _src(None, name='juhe')
    svc = FetchService(primary, backup, db_engine, grace_seconds=0)
    svc.fetch_and_store('ssq')
    with Session(db_engine) as s:
        assert s.exec(select(PendingComparison)).first() is not None


def test_store_no_pending_on_not_drawn(db_engine):
    """未开奖 → 不写 outbox。"""
    primary = _src(None, name='mxnzp')
    backup = _src(None, name='juhe')
    svc = FetchService(primary, backup, db_engine, grace_seconds=0)
    r = svc.fetch_and_store('ssq')
    assert r.not_drawn and not r.stored
    with Session(db_engine) as s:
        assert s.exec(select(PendingComparison)).first() is None
        assert s.exec(select(DrawResult)).first() is None


def test_store_no_pending_on_mismatch(db_engine):
    """双源不一致拒绝入库 → 不写 outbox、不入库。"""
    primary = _src(_dn('ssq', '062', [1, 2, 3, 4, 5, 6], [7]), name='mxnzp')
    backup = _src(_dn('ssq', '062', [1, 2, 3, 4, 5, 6], [8]), name='juhe')
    svc = FetchService(primary, backup, db_engine, grace_seconds=0)
    r = svc.fetch_and_store('ssq')
    assert not r.verified and not r.stored
    with Session(db_engine) as s:
        assert s.exec(select(PendingComparison)).first() is None
        assert s.exec(select(DrawResult)).first() is None


def test_store_idempotent_no_duplicate_pending(db_engine):
    """同期重复抓取幂等：仅一行 pending_comparisons。"""
    primary = _src(_dn('ssq', '062', [1, 2, 3, 4, 5, 6], [7]), name='mxnzp')
    backup = _src(_dn('ssq', '062', [1, 2, 3, 4, 5, 6], [7]), name='juhe')
    svc = FetchService(primary, backup, db_engine, grace_seconds=0)
    svc.fetch_and_store('ssq')
    svc.fetch_and_store('ssq')
    with Session(db_engine) as s:
        assert len(s.exec(select(PendingComparison)).all()) == 1


def test_fetch_logs_source_failure(db_engine, caplog):
    """silent-failure：源故障异常不得静默吞没，须结构化日志（供告警/排障）。
    主源故障 + 备源正常 → 单源入库；主源错误已被记录。"""
    primary = MagicMock()
    primary.name = 'mxnzp'
    primary.fetch.side_effect = RuntimeError('connection timeout to mxnzp')
    backup = _src(_dn('ssq', '062', [1, 2, 3, 4, 5, 6], [7]), name='juhe')
    svc = FetchService(primary, backup, db_engine, grace_seconds=0, max_attempts=1)
    with caplog.at_level(logging.WARNING):
        r = svc.fetch_and_store('ssq')
    assert r.stored and r.single_source  # 备源单源兜底
    assert 'source_fetch_failed' in caplog.text, '源故障必须记录，不得静默吞没'


def test_grace_refetch_mismatch_rejected_not_single_source(db_engine):
    """spec §7.2 回归：grace 内重抓到缺失源数据但号码不一致 → 必须判 mismatch 拒绝，
    不得降级单源入库。否则双源安全网在 grace 路径被绕过（准确性优先，§10）。
    主源首抓未开奖，grace 重抓返回与备源蓝球不一致的号码。"""
    primary = MagicMock()
    primary.name = 'mxnzp'
    primary.fetch.side_effect = [
        None,  # 首抓：主源未开奖
        _dn('ssq', '062', [1, 2, 3, 4, 5, 6], [8]),  # grace 重抓：蓝球与备源不同
    ]
    backup = _src(_dn('ssq', '062', [1, 2, 3, 4, 5, 6], [7]), name='juhe')
    svc = FetchService(primary, backup, db_engine, grace_seconds=1, sleep=lambda *_: None)
    r = svc.fetch_and_store('ssq')
    assert not r.verified, 'grace 重抓号码不一致 → 必须拒绝，不得单源入库'
    assert not r.stored
    assert r.error == 'cross_verify_mismatch'
    with Session(db_engine) as s:
        assert s.exec(select(DrawResult)).first() is None


def test_idempotent_upgrades_single_source_to_dual(db_engine):
    """spec §7.2 金标准是双源 verified：已单源入库的行，后续双源一致时须升级
    single_source→False（避免 UI 永久挂黄"单源校验"）。号码未变，不重复写 outbox。"""
    dn = _dn('ssq', '062', [1, 2, 3, 4, 5, 6], [7])
    # 第一次：主源有、备源无 → 单源
    svc1 = FetchService(_src(dn, name='mxnzp'), _src(None, name='juhe'), db_engine, grace_seconds=0)
    r1 = svc1.fetch_and_store('ssq')
    assert r1.stored and r1.verified and r1.single_source
    # 第二次：双源一致 → 升级
    svc2 = FetchService(_src(dn, name='mxnzp'), _src(dn, name='juhe'), db_engine, grace_seconds=0)
    r2 = svc2.fetch_and_store('ssq')
    assert r2.verified is True
    assert r2.single_source is False, '双源一致后须从单源升级为双源 verified'
    with Session(db_engine) as s:
        dr = s.exec(select(DrawResult)).first()
        assert dr.verified is True and dr.single_source is False
        assert len(s.exec(select(PendingComparison)).all()) == 1  # 不重复 outbox


def test_draw_date_is_official_not_fetch_time(db_engine):
    """round-2 test gap：draw_date 必须是源返回的官方开奖日，非抓取时刻。
    spec §4.3 全程 Asia/Shanghai；draw_date 与 fetched_at 语义不同。"""
    official = date(2026, 5, 1)  # 一个明确不同于"今天"的官方开奖日
    primary = _src(_dn('ssq', '062', [1, 2, 3, 4, 5, 6], [7], draw_date=official), name='mxnzp')
    backup = _src(_dn('ssq', '062', [1, 2, 3, 4, 5, 6], [7], draw_date=official), name='juhe')
    FetchService(primary, backup, db_engine, grace_seconds=0).fetch_and_store('ssq')
    with Session(db_engine) as s:
        dr = s.exec(select(DrawResult)).first()
        assert dr.draw_date.date() == official, f'draw_date 必须是官方开奖日 {official}，得到 {dr.draw_date.date()}'


# ───────────────────── review round-3 修复覆盖 ─────────────────────
# silent-failure-hunter (HIGH) + quality (IMPORTANT) 修复锁定


def test_store_verified_and_outbox_atomic(db_engine):
    """silent-failure (HIGH)：DrawResult 与 PendingComparison 必须同事务原子落库。

    旧版分两次 commit：首 commit 落库 verified=true、次 commit（写 outbox）失败时，
    重试走幂等分支 existing.verified 已 True → upgraded=False → 不补 outbox，
    CompareService 永不比对 → 中奖静默漏通知。修复后单事务，落库即必有 outbox。
    验证：verified 入库后 DrawResult 与 PendingComparison 行数都恰好 1。"""
    primary = _src(_dn('ssq', '062', [1, 2, 3, 4, 5, 6], [7]), name='mxnzp')
    backup = _src(_dn('ssq', '062', [1, 2, 3, 4, 5, 6], [7]), name='juhe')
    FetchService(primary, backup, db_engine, grace_seconds=0).fetch_and_store('ssq')
    with Session(db_engine) as s:
        drs = s.exec(select(DrawResult)).all()
        pcs = s.exec(select(PendingComparison).where(PendingComparison.draw_result_id == drs[0].id)).all()
        assert len(drs) == 1 and len(pcs) == 1, (
            'verified 入库与 outbox 必须原子：DrawResult 与 PendingComparison 同生同在'
        )


def test_upgrade_does_not_bless_changed_numbers(db_engine):
    """quality (IMPORTANT)：单源→双源升级不得 bless 号码不一致的旧行。

    场景：先用旧号码（蓝 7）单源入库；后双源返回新号码（蓝 9，官方更正）。
    旧版升级分支只改 single_source flag 不校验号码 → 把旧号码 bless 成双源 verified。
    属官方更正语义（T6 DrawCorrectService 专管 version++/重比）；T3 保守不动号码、
    不 bless、不改 version。验证：号码保持旧值、version 仍 1、不重写 outbox。"""
    old_dn = _dn('ssq', '062', [1, 2, 3, 4, 5, 6], [7])
    new_dn = _dn('ssq', '062', [1, 2, 3, 4, 5, 6], [9])  # 蓝球变更（官方更正）
    # 第一次：主源单源（旧号码）入库
    svc1 = FetchService(_src(old_dn, name='mxnzp'), _src(None, name='juhe'), db_engine, grace_seconds=0)
    svc1.fetch_and_store('ssq')
    # 第二次：双源返回新号码 → 不应 bless 旧号码
    svc2 = FetchService(_src(new_dn, name='mxnzp'), _src(new_dn, name='juhe'), db_engine, grace_seconds=0)
    r2 = svc2.fetch_and_store('ssq')
    with Session(db_engine) as s:
        dr = s.exec(select(DrawResult)).first()
        assert json.loads(dr.numbers_json)['back'] == [7], (
            '号码变更属官方更正（T6），T3 不得 bless 旧号码为双源 verified'
        )
        assert dr.version == 1, '更正递增 version 是 T6 职责，T3 不动 version'
        # 旧号码保留 single_source=True（未升级，因号码不一致）
        assert dr.single_source is True
        assert len(s.exec(select(PendingComparison)).all()) == 1  # 不重复 outbox
    assert r2.stored  # 仍返回成功（幂等），但不 bless


def test_max_attempts_zero_rejected(db_engine):
    """Minor guard：max_attempts<1 是配置错误，须显式 raise，不得静默伪装成 not_drawn。

    旧版 range(0) 为空 → _fetch_with_backoff 隐式返回 None → 被归类为"未开奖"，
    把配置错误误报成该期未开奖（错误方向，spec §10 准确性优先）。"""
    import pytest

    with pytest.raises(ValueError, match='max_attempts'):
        FetchService(_src(None), _src(None), db_engine, max_attempts=0)


def test_unknown_lottery_defaults_to_strict_positional(db_engine):
    """Minor guard：未知彩种（未在 seeds 注册）默认 positional（严格按位比），非 partition。

    spec §10 准确性优先：partition 的 sorted/multiset 更宽松，会放过顺序不同的双源号码
    → verified=true 入错号。默认 positional 更安全（顺序不同即拒）。验证：未注册彩种双源
    同号不同序 → mismatch 拒绝（而非 lenient 放过）。"""
    primary = _src(_dn('zzz', '062', [1, 2, 3]), name='mxnzp')  # zzz 未注册
    backup = _src(_dn('zzz', '062', [3, 2, 1]), name='juhe')
    svc = FetchService(primary, backup, db_engine, grace_seconds=0)
    r = svc.fetch_and_store('zzz')
    assert not r.verified, '未知彩种默认 strict positional：顺序不同即拒，不得 lenient 放过'


# ────────── quality review Important 修复覆盖 ──────────


def test_cross_verify_mismatch_logged(db_engine, caplog):
    """quality Important①：双源交叉校验不一致必须告警日志（spec §7.2「不一致→告警」）。

    不一致是最严重信号（双源分歧=潜在脏数据），与源故障同属不得静默吞没——否则运维查
    "为何 062 没入库"无迹可寻。主流程与 grace 路径两处 mismatch 都须留痕。"""
    primary = _src(_dn('ssq', '062', [1, 2, 3, 4, 5, 6], [7]), name='mxnzp')
    backup = _src(_dn('ssq', '062', [1, 2, 3, 4, 5, 6], [8]), name='juhe')  # 蓝球不同
    svc = FetchService(primary, backup, db_engine, grace_seconds=0)
    with caplog.at_level(logging.WARNING):
        r = svc.fetch_and_store('ssq')
    assert r.error == 'cross_verify_mismatch'
    assert 'cross_verify_mismatch' in caplog.text, '双源不一致必须告警日志，不得静默'


def test_grace_recover_attributes_actual_source(db_engine):
    """quality Important②：grace 恢复入库的 source 必须是实际提供数据的源，而非恒记主源。

    场景：主源首抓未开奖，grace 重抓仍无 → 此时备源是 present 源；grace 内若重抓主源
    成功且匹配，入库数据实际来自备源（present_dn=b）。source 字段应记备源名，否则主源
    故障期间靠备源恢复的行全错标主源，丢失 ops 追溯。与单源兜底归属逻辑一致。"""
    # 备源 present（有数据）；主源 grace 重抓成功且与备源匹配 → 双源入库
    backup_dn = _dn('ssq', '062', [1, 2, 3, 4, 5, 6], [7])
    primary = MagicMock()
    primary.name = 'mxnzp'
    primary.fetch.side_effect = [None, backup_dn]  # 首抓无 → grace 重抓有
    backup = _src(backup_dn, name='juhe')
    svc = FetchService(primary, backup, db_engine, grace_seconds=1, sleep=lambda *_: None)
    r = svc.fetch_and_store('ssq')
    assert r.stored and r.verified and not r.single_source
    with Session(db_engine) as s:
        dr = s.exec(select(DrawResult)).first()
        # present 源是备源（主源首抓为 None）→ source 应记备源 juhe，非 mxnzp
        assert dr.source == 'juhe', f'grace 恢复入库 source 须记实际提供数据的源 juhe，得到 {dr.source}'


def test_single_source_fallback_attributes_actual_source(db_engine):
    """quality Important② 配套：单源兜底归属已正确（锁定不回归）。主源故障→备源单源入库，
    source 必须记备源。"""
    primary = MagicMock()
    primary.name = 'mxnzp'
    primary.fetch.side_effect = RuntimeError('mxnzp down')
    backup = _src(_dn('ssq', '062', [1, 2, 3, 4, 5, 6], [7]), name='juhe')
    svc = FetchService(primary, backup, db_engine, grace_seconds=0, max_attempts=1)
    svc.fetch_and_store('ssq')
    with Session(db_engine) as s:
        dr = s.exec(select(DrawResult)).first()
        assert dr.source == 'juhe'
