"""ssq 生产回归基线（Plan 10 / T0；spec §4.2）。

唯一生产验证彩种的「真实开奖 → 真实奖金」夹具回归：任何 prize_tables/compare
改动前后本文件必须全绿。夹具为 2026-08-13 第 2026093 期官方公告实测值
（2026-02-01 新规后），见 tests/fixtures/ssq_baseline_2026093.json。
"""

import json
from pathlib import Path

import pytest

from app.domain.compare import compare
from app.domain.entry import Entry
from app.domain.prize_tables import get_tiers
from app.domain.spec import LotterySpec
from app.seeds.lottery_types import SPECS

FIXTURE = json.loads(
    (Path(__file__).resolve().parent.parent / 'fixtures' / 'ssq_baseline_2026093.json').read_text()
)
DRAW_FRONT = tuple(FIXTURE['draw_front'])
DRAW_BACK = tuple(FIXTURE['draw_back'])

_SPEC = LotterySpec.from_dict(next(s for s in SPECS if s['code'] == 'ssq'))


def _hit(front, back):
    entry = Entry(lottery_code='ssq', play_type='single', front=tuple(front), back=tuple(back))
    results = compare(_SPEC, draw_front=DRAW_FRONT, draw_back=DRAW_BACK, entry=entry)
    assert len(results) == 1
    return results[0]


@pytest.mark.parametrize(
    ('front', 'back', 'tier', 'amount'),
    [
        # 一等/二等浮动：tier 命中、amount=None（开奖当晚「待官方派奖」，回填流程负责金额）
        ([5, 8, 15, 20, 21, 24], [9], 1, None),
        ([5, 8, 15, 20, 21, 24], [1], 2, None),
        # 固定档：金额=分（官方元 ×100），与夹具官方公布值一致
        ([5, 8, 15, 20, 21, 1], [9], 3, 300000),   # 5+1 → 3000 元
        ([5, 8, 15, 20, 21, 1], [1], 4, 20000),    # 5+0 → 200 元
        ([5, 8, 15, 20, 1, 2], [9], 4, 20000),     # 4+1 → 200 元
        ([5, 8, 15, 20, 1, 2], [1], 5, 1000),      # 4+0 → 10 元
        ([5, 8, 15, 1, 2, 3], [9], 5, 1000),       # 3+1 → 10 元
        ([5, 8, 1, 2, 3, 4], [9], 6, 500),         # 2+1 → 5 元
        ([5, 1, 2, 3, 4, 6], [9], 6, 500),         # 1+1 → 5 元
        ([1, 2, 3, 4, 6, 7], [9], 6, 500),         # 0+1 → 5 元
    ],
)
def test_ssq_baseline_tiers(front, back, tier, amount):
    r = _hit(front, back)
    assert r.is_win, f'{front}+{back} 应中 {tier} 等'
    assert r.tier == tier
    assert r.amount == amount


def test_ssq_baseline_3_plus_0_no_win_this_draw():
    """3+0 本期不中奖——本期奖池 5.66 亿 < 15 亿，2026 新规「福运奖」未激活。

    ⚠️ 福运奖（奖池 ≥15 亿时 3+0=5 元）未实现（B2 roadmap）：若未来奖池 ≥15 亿，
    3+0 实际中奖而系统判未中——属已知限制，README/规则文档已声明。
    """
    r = _hit([5, 8, 15, 1, 2, 3], [1])
    assert not r.is_win


def test_ssq_baseline_no_win():
    assert not _hit([1, 2, 3, 4, 6, 7], [1]).is_win  # 0+0


def test_ssq_fixed_amounts_match_official_announcement():
    """代码固定档（分） == 夹具官方公布（元 ×100）——锁定元/分单位不回退。"""
    tiers = {t.tier: t for t in get_tiers('ssq')}
    for tier_str, yuan in FIXTURE['prizegrades_yuan'].items():
        tier = int(tier_str)
        if tiers[tier].amount is None:
            continue  # 浮动档金额由回填流程负责，不在此断言
        assert tiers[tier].amount == yuan * 100, (
            f'ssq {tier} 等：官方 {yuan} 元 = {yuan * 100} 分，代码 {tiers[tier].amount}'
        )
