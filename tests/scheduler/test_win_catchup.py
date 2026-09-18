"""回填中奖补推（win catch-up）机制测试。

背景（2026-09-15 NAS 事故复盘）：9月6-15日容器 DNS 故障期间抓取/比对/推送全灭；
9月15日恢复后启动回填补比产生 3 笔中奖（ssq 099/106 期），但常规推送路径两头
够不着——路径A 只推「开奖当晚」窗口内的浮动档大奖，路径B 只汇总「昨天」——
回填产生的隔期中奖被静默漏推（违反「中奖永不静默漏通知」红线，spec §10）。

本机制：比对创建时间晚于「开奖日+2天（CST）」（常规路径已不可能覆盖）且从未有
sent 推送记录的 is_win 比对 → 补推。
"""
from datetime import datetime
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

from sqlmodel import Session

from app.models import Comparison, DrawResult, NotificationLog, Ticket, User
from app.services.win_catchup import find_catchup_candidates, push_win_catchups

_CST = ZoneInfo('Asia/Shanghai')

# 固定基准时间（避免依赖 now）：
#   开奖日 2026-09-10（CST）→ 常规窗口关闭于 2026-09-12 00:00 CST（= 09-11 16:00 UTC）
#   迟到比对创建于 2026-09-15 05:00 UTC（> 窗口关闭）→ 应补推
_LATE_DRAW_DATE = datetime(2026, 9, 10, 0, 0, 0)
_LATE_COMPARED_AT = datetime(2026, 9, 15, 5, 0, 0)
_NOW = datetime(2026, 9, 18, 4, 0, 0)
_USER_SEQ = 0


def _seed(db_engine, *, draw_date=_LATE_DRAW_DATE, compared_at=_LATE_COMPARED_AT,
          is_win=True, lottery_code='ssq', log_status=None, corrected_at=None):
    """建一个用户 + 一期开奖 + 一注票 + 一条比对（created_at 可控）。

    log_status 非 None 时额外插一条引用该比对的 notification_logs
    （path_a/catch-up 风格，comparison_id 直连），用于验证去重。
    corrected_at 非 None 时模拟官方更正重比（created_at 保留首比时刻）。
    返回 (comparison_id, draw_no)。
    """
    global _USER_SEQ
    _USER_SEQ += 1
    with Session(db_engine) as s:
        u = User(username=f'catchup_u{_USER_SEQ}', password_hash='x', role='user', invite_code='C')
        s.add(u)
        s.commit()
        s.refresh(u)
        dr = DrawResult(
            lottery_code=lottery_code,
            draw_no=f'10{_USER_SEQ}',
            draw_date=draw_date,
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
            lottery_code=lottery_code,
            play_type='single',
            numbers_json='{"front":[1,2,3,4,5,6],"back":[7]}',
            multiplier=1,
            cost=200,
            enabled=True,
        )
        s.add(t)
        s.commit()
        s.refresh(t)
        cmp = Comparison(
            user_id=u.id,
            draw_result_id=dr.id,
            ticket_id=t.id,
            hits_json='{}',
            prize_tier=6,
            prize_amount=500,
            is_win=is_win,
        )
        s.add(cmp)
        s.commit()
        s.refresh(cmp)
        cmp.created_at = compared_at
        cmp.corrected_at = corrected_at
        if log_status is not None:
            s.add(
                NotificationLog(
                    user_id=u.id,
                    comparison_id=cmp.id,
                    type='x',
                    payload='x',
                    status=log_status,
                )
            )
        s.commit()
        return cmp.id, dr.draw_no


def test_find_returns_late_unnotified_win(db_engine, monkeypatch):
    """迟到（创建于开奖日+2天后）且无 sent 记录的中奖比对 → 补推候选。"""
    monkeypatch.setattr('app.services.win_catchup._now_utc', lambda: _NOW)
    cid, draw_no = _seed(db_engine)
    candidates = find_catchup_candidates(db_engine)
    assert [c['comparison_id'] for c in candidates] == [cid]
    c = candidates[0]
    assert c['lottery_code'] == 'ssq'
    assert c['lottery_name'] == '双色球'
    assert c['draw_no'] == draw_no
    assert c['draw_date_str'] == '2026-09-10'
    assert c['tier'] == 6
    assert c['amount'] == 500


def test_find_skips_fresh_win_within_normal_window(db_engine, monkeypatch):
    """比对创建于常规窗口内（开奖当晚，path_b 次日 07:00 汇总前）→ 由路径A/B 覆盖，不进补推候选。"""
    monkeypatch.setattr('app.services.win_catchup._now_utc', lambda: _NOW)
    # 开奖 09-17，比对 09-17 22:00 CST（= 14:00 UTC）创建——早于 cutoff（D+1 07:00 CST
    # = 09-17 23:00 UTC），次日 07:00 path_b 会覆盖 → 非迟到。
    _seed(db_engine, draw_date=datetime(2026, 9, 17), compared_at=datetime(2026, 9, 17, 14, 0, 0))
    assert find_catchup_candidates(db_engine) == []


def test_find_returns_win_created_after_path_b_run(db_engine, monkeypatch):
    """隔夜故障恢复场景：比对创建于 path_b 汇总（D+1 07:00 CST）之后 → path_b 对 D 期
    永不再扫（只汇总昨天），必须补推，否则真空窗口静默漏推（code-review CRITICAL-1）。"""
    monkeypatch.setattr('app.services.win_catchup._now_utc', lambda: _NOW)
    # 开奖 09-10，比对 09-11 09:00 CST（= 09-11 01:00 UTC）创建——隔夜故障恢复的
    # 典型形状；晚于 cutoff（D+1 07:00 CST = 09-10 23:00 UTC）→ 迟到，须补推。
    cid, _ = _seed(db_engine, draw_date=datetime(2026, 9, 10), compared_at=datetime(2026, 9, 11, 1, 0, 0))
    assert [c['comparison_id'] for c in find_catchup_candidates(db_engine)] == [cid]


def test_find_returns_win_created_exactly_at_cutoff(db_engine, monkeypatch):
    """created_at == cutoff（D+1 07:00 CST）：path_b 读取与该提交可能竞态，
    补推是安全方向（重复远轻于漏推）→ 进候选。"""
    monkeypatch.setattr('app.services.win_catchup._now_utc', lambda: _NOW)
    cid, _ = _seed(db_engine, draw_date=datetime(2026, 9, 10), compared_at=datetime(2026, 9, 10, 23, 0, 0))
    assert [c['comparison_id'] for c in find_catchup_candidates(db_engine)] == [cid]


def test_find_includes_win_exactly_at_age_floor(db_engine, monkeypatch):
    """年龄边界：created_at == now-14d 恰在第 14 天 → 含（>= 语义，无 off-by-one）。"""
    monkeypatch.setattr('app.services.win_catchup._now_utc', lambda: _NOW)
    # created = 2026-09-04 04:00 UTC（= _NOW - 14d）；draw 08-30 → cutoff 早已过。
    cid, _ = _seed(db_engine, draw_date=datetime(2026, 8, 30), compared_at=datetime(2026, 9, 4, 4, 0, 0))
    assert [c['comparison_id'] for c in find_catchup_candidates(db_engine)] == [cid]


def test_find_returns_win_flipped_by_late_correction(db_engine, monkeypatch):
    """更正重比翻转的中奖：首比在常规窗口内（created_at 早），但官方更正把 is_win
    翻为 True 的时刻晚于 cutoff → 以 corrected_at 判迟到，须补推（MEDIUM-1）。

    只看 created_at 会漏：首比当晚完成 + 次日更正翻转的行，path_b 已跑过、
    win_catchup 又判「未迟到」→ 静默漏推。
    """
    monkeypatch.setattr('app.services.win_catchup._now_utc', lambda: _NOW)
    # 开奖 09-10，首比当晚 21:30 CST（= 13:30 UTC，cutoff 前）；更正 09-12 10:00 CST
    #（= 09-12 02:00 UTC，cutoff 后、年龄内）翻转为中奖 → 候选。
    cid, _ = _seed(
        db_engine,
        draw_date=datetime(2026, 9, 10),
        compared_at=datetime(2026, 9, 10, 13, 30, 0),
        corrected_at=datetime(2026, 9, 12, 2, 0, 0),
    )
    assert [c['comparison_id'] for c in find_catchup_candidates(db_engine)] == [cid]


def test_find_correction_refreshes_age_window(db_engine, monkeypatch):
    """年龄上限以活动时间（corrected_at 优先）计：创建虽久、更正刚发生的行仍须补推。"""
    monkeypatch.setattr('app.services.win_catchup._now_utc', lambda: _NOW)
    # 创建 08-02（远超 14 天上限），更正 09-17 12:00 CST（= 04:00 UTC，1 天前）→ 候选。
    cid, _ = _seed(
        db_engine,
        draw_date=datetime(2026, 8, 1),
        compared_at=datetime(2026, 8, 2, 13, 30, 0),
        corrected_at=datetime(2026, 9, 17, 4, 0, 0),
    )
    assert [c['comparison_id'] for c in find_catchup_candidates(db_engine)] == [cid]


def test_find_skips_win_already_sent(db_engine, monkeypatch):
    """已有 sent 推送记录（comparison_id 直连）的中奖 → 已通知过，不重复补推。"""
    monkeypatch.setattr('app.services.win_catchup._now_utc', lambda: _NOW)
    _seed(db_engine, log_status='sent')
    assert find_catchup_candidates(db_engine) == []


def test_find_keeps_win_with_only_failed_log(db_engine, monkeypatch):
    """只有 failed 记录的中奖 → 从未送达用户，仍须补推（重试语义，
    与 jobs._path_a_tick 的「已有 sent 才跳过」一致）。"""
    monkeypatch.setattr('app.services.win_catchup._now_utc', lambda: _NOW)
    _seed(db_engine, log_status='failed')
    assert len(find_catchup_candidates(db_engine)) == 1


def test_find_skips_late_non_win(db_engine, monkeypatch):
    """迟到的未中奖比对 → 无中奖可推，不进候选。"""
    monkeypatch.setattr('app.services.win_catchup._now_utc', lambda: _NOW)
    _seed(db_engine, is_win=False)
    assert find_catchup_candidates(db_engine) == []


def test_find_skips_win_older_than_max_age(db_engine, monkeypatch):
    """迟到超过 _CATCHUP_MAX_AGE_DAYS 的历史比对 → 不补推。

    历史中奖多已被汇总推送覆盖（汇总不写 comparison_id，无法反查去重），
    无年龄上限会在机制上线时把全部历史中奖重推一遍。
    """
    monkeypatch.setattr('app.services.win_catchup._now_utc', lambda: _NOW)
    # 开奖 08-01，比对 08-20 创建：迟到，但距 now（09-18）> 14 天 → 排除。
    _seed(db_engine, draw_date=datetime(2026, 8, 1), compared_at=datetime(2026, 8, 20))
    assert find_catchup_candidates(db_engine) == []


def test_push_calls_notifier_per_candidate(db_engine, monkeypatch):
    """push_win_catchups 对每个候选调用 notifier.notify_win_catchup，返回成功数。"""
    monkeypatch.setattr('app.services.win_catchup._now_utc', lambda: _NOW)
    cid, draw_no = _seed(db_engine)
    notifier = MagicMock()
    push_win_catchups({'engine': db_engine, 'notifier': notifier})
    notifier.notify_win_catchup.assert_called_once()
    kwargs = notifier.notify_win_catchup.call_args.kwargs
    assert kwargs['comparison_id'] == cid
    assert kwargs['lottery_name'] == '双色球'
    assert kwargs['draw_no'] == draw_no
    assert kwargs['draw_date_str'] == '2026-09-10'
    assert kwargs['tier'] == 6
    assert kwargs['amount'] == 500


def test_push_isolates_per_candidate_failure(db_engine, monkeypatch):
    """单个候选推送抛异常不得中断其余候选（CLAUDE.md 批量循环单行隔离纪律）。"""
    monkeypatch.setattr('app.services.win_catchup._now_utc', lambda: _NOW)
    _seed(db_engine)
    _seed(db_engine)
    notifier = MagicMock()
    notifier.notify_win_catchup.side_effect = [RuntimeError('boom'), None]
    sent = push_win_catchups({'engine': db_engine, 'notifier': notifier})
    assert notifier.notify_win_catchup.call_count == 2
    assert sent == 1
