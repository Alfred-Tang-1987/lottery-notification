from app.notifications.base import NotificationPayload


def _fmt_amount(cents: int | None) -> str:
    return "待官方派奖" if cents is None else f"{cents / 100:.2f} 元"


def build_path_a(*, lottery_name: str, draw_no: str, tier_name: str,
                 tier: int, amount: int | None) -> NotificationPayload:
    """路径A 大奖即时简讯（spec §8.3）。"""
    amt = _fmt_amount(amount)
    title = f"🎉 恭喜中奖！{lottery_name} {tier_name}"
    body = (f"第 {draw_no} 期开奖，你追投的号码命中 {tier_name}，奖金 {amt}。"
            f"请在 60 天内兑奖；单注 ≥1 万元将代扣 20% 偶然所得税。"
            f"以官方开奖为准。理性购彩，量力而行。")
    return NotificationPayload(title=title, body=body, draw_no=draw_no, tier=tier, amount=amount)


def build_path_b(*, date_str: str, total: int, wins: int,
                 win_details: list[tuple[str, str, int | None]], loses: int) -> NotificationPayload:
    """路径B 次日汇总（spec §8.3）。win_details: [(彩种, 奖级, 金额分)]。"""
    lines = [f"  · {name} {tier} {_fmt_amount(amt)}" for name, tier, amt in win_details]
    detail = "\n".join(lines) if lines else "无"
    title = f"兑奖了吗 · {date_str} 核对汇总"
    body = (f"本期共核对 {total} 个追投彩种，中奖 {wins} 笔：\n{detail}\n"
            f"其余 {loses} 个未中奖。点击查看明细。以官方开奖为准。理性购彩。")
    return NotificationPayload(title=title, body=body)
