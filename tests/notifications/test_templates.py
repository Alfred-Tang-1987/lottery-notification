from app.notifications.base import NotificationPayload
from app.notifications.templates import build_path_a, build_path_b


def test_path_a_template_float():
    p = build_path_a(lottery_name='双色球', draw_no='062', tier_name='二等奖', tier=2, amount=None)
    assert isinstance(p, NotificationPayload)
    assert '恭喜中奖' in p.title and '双色球' in p.title
    assert '062' in p.body and '待官方派奖' in p.body
    assert p.user_id is None
    assert p.lottery_code is None
    assert p.draw_no == '062'
    assert p.tier == 2
    assert p.amount is None


def test_path_a_template_fixed_amount():
    p = build_path_a(lottery_name='双色球', draw_no='062', tier_name='三等奖', tier=3, amount=3000)
    assert '30.00' in p.body  # 3000分 → 30.00元
    assert '60 天' in p.body  # 兑奖期
    assert p.tier == 3
    assert p.amount == 3000


def test_path_b_template():
    p = build_path_b(
        date_str='2026-06-21',
        tracked_lottery_count=3,
        win_details=[('双色球', '二等奖', None)],
        unwon_lottery_names=['七乐彩', '福彩3D'],
    )
    assert '2026-06-21' in p.title
    assert '共核对 3 个追投彩种' in p.body and '中奖 1 笔' in p.body
    assert '未中奖彩种（2）' in p.body
    assert p.user_id is None
    assert p.lottery_code is None
    assert p.draw_no is None


def test_path_b_template_multiple_wins():
    p = build_path_b(
        date_str='2026-06-21',
        tracked_lottery_count=2,
        win_details=[('双色球', '二等奖', None), ('大乐透', '三等奖', 50000)],
        unwon_lottery_names=[],
    )
    assert '双色球' in p.body
    assert '大乐透' in p.body
    assert '500.00' in p.body  # 50000分 -> 500.00元
    assert '待官方派奖' in p.body
    assert '未中奖彩种（0）：无' in p.body  # 空名单 -> 「无」


# ---------------------------------------------------------------------------
# 回归：path_b 文案语义对齐 spec §8.3（2026-07-28 NAS 部署后发现）
#
# 旧实现 total=wins+loses（注数）、loses（未中注数）-> 文案「共核对 5 个追投彩种」
# 但用户只追 1 个彩种（5 注）-> 语义错位。spec §8.3 line347：{N}/{X} 是彩种数。
# 且旧文案「点击查看明细」无落地页（Bark 纯文本无 click URL）-> 空头承诺，删除。
#
# 新签名：build_path_b 按**彩种级**聚合，参数自解释：
#   tracked_lottery_count: 追投彩种数（{N}）
#   win_details: [(彩种名, 奖级名, 金额分)] 中奖逐笔（{M}=len）
#   unwon_lottery_names: [彩种名] 未中奖彩种名单（{X}=len）
# ---------------------------------------------------------------------------


def test_path_b_counts_lotteries_not_tickets():
    """{N}/{X} 必须是彩种数，不是注数（spec §8.3 line347）。

    用户追 1 个彩种 5 注全未中 -> 文案「共核对 1 个追投彩种...其余 1 个未中奖」，
    而非「5 个」。旧实现传注数导致语义错位（NAS 实测）。
    """
    from app.notifications.templates import build_path_b

    p = build_path_b(
        date_str='2026-07-26',
        tracked_lottery_count=1,
        win_details=[],
        unwon_lottery_names=['双色球'],
    )
    assert '共核对 1 个追投彩种' in p.body, p.body
    assert '中奖 0 笔' in p.body, p.body
    assert '未中奖彩种（1）：双色球' in p.body, p.body
    # 不应出现把注数当彩种数的「5 个」
    assert '5 个追投彩种' not in p.body
    assert '5 个未中奖' not in p.body


def test_path_b_lists_won_lottery_details():
    """中奖彩种逐笔列出（彩种 奖级 金额），未中奖彩种列名单。"""
    from app.notifications.templates import build_path_b

    p = build_path_b(
        date_str='2026-07-26',
        tracked_lottery_count=2,
        win_details=[('双色球', '二等奖', None), ('大乐透', '三等奖', 50000)],
        unwon_lottery_names=['七乐彩'],
    )
    # 中奖逐笔：彩种 + 奖级 + 金额
    assert '双色球' in p.body and '二等奖' in p.body
    assert '大乐透' in p.body and '三等奖' in p.body and '500.00' in p.body
    assert '待官方派奖' in p.body  # 浮动档 None
    # 未中奖彩种名单
    assert '七乐彩' in p.body
    assert '中奖 2 笔' in p.body
    assert '未中奖彩种（1）：七乐彩' in p.body


def test_path_b_no_click_to_detail_phrase():
    """删除「点击查看明细」空头承诺（Bark 纯文本无 click URL 落地页）。

    后续若加 SITE_URL + Bark url 字段跳转，再恢复该文案。当前先删避免误导。
    """
    from app.notifications.templates import build_path_b

    p = build_path_b(
        date_str='2026-07-26',
        tracked_lottery_count=1,
        win_details=[],
        unwon_lottery_names=['双色球'],
    )
    assert '点击查看明细' not in p.body, '无落地页时不得承诺点击查看'
