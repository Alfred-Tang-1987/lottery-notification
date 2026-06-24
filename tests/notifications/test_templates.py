from app.notifications.base import NotificationPayload
from app.notifications.templates import build_path_a, build_path_b


def test_path_a_template_float():
    p = build_path_a(lottery_name="双色球", draw_no="062", tier_name="二等奖",
                     tier=2, amount=None)
    assert isinstance(p, NotificationPayload)
    assert "恭喜中奖" in p.title and "双色球" in p.title
    assert "062" in p.body and "待官方派奖" in p.body
    assert p.user_id is None
    assert p.lottery_code is None
    assert p.draw_no == "062"
    assert p.tier == 2
    assert p.amount is None


def test_path_a_template_fixed_amount():
    p = build_path_a(lottery_name="双色球", draw_no="062", tier_name="三等奖",
                     tier=3, amount=3000)
    assert "30.00" in p.body  # 3000分 → 30.00元
    assert "60 天" in p.body  # 兑奖期
    assert p.tier == 3
    assert p.amount == 3000


def test_path_b_template():
    p = build_path_b(date_str="2026-06-21", total=3, wins=1,
                     win_details=[("双色球", "二等奖", None)], loses=2)
    assert "2026-06-21" in p.title
    assert "3" in p.body and "1" in p.body
    assert p.user_id is None
    assert p.lottery_code is None
    assert p.draw_no is None


def test_path_b_template_multiple_wins():
    p = build_path_b(date_str="2026-06-21", total=2, wins=2,
                     win_details=[("双色球", "二等奖", None),
                                  ("大乐透", "三等奖", 50000)], loses=0)
    assert "双色球" in p.body
    assert "大乐透" in p.body
    assert "500.00" in p.body  # 50000分 → 500.00元
    assert "待官方派奖" in p.body
