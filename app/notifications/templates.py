from app.notifications.base import NotificationPayload


def _fmt_amount(cents: int | None) -> str:
    return '待官方派奖' if cents is None else f'{cents / 100:.2f} 元'


def build_path_a(
    *, lottery_name: str, draw_no: str, tier_name: str, tier: int, amount: int | None
) -> NotificationPayload:
    """路径A 大奖即时简讯（spec §8.3）。"""
    amt = _fmt_amount(amount)
    title = f'🎉 恭喜中奖！{lottery_name} {tier_name}'
    body = (
        f'第 {draw_no} 期开奖，你追投的号码命中 {tier_name}，奖金 {amt}。'
        f'请在 60 天内兑奖；单注 ≥1 万元将代扣 20% 偶然所得税。'
        f'以官方开奖为准。理性购彩，量力而行。'
    )
    return NotificationPayload(title=title, body=body, draw_no=draw_no, tier=tier, amount=amount)


def build_path_b(
    *,
    date_str: str,
    tracked_lottery_count: int,
    win_details: list[tuple[str, str, int | None]],
    unwon_lottery_names: list[str],
) -> NotificationPayload:
    """路径B 次日汇总（spec §8.3 line347）。

    按**彩种级**聚合（非注数）--spec {N}/{X} 是彩种数：
      - tracked_lottery_count: 当日有比对的追投彩种数（{N}）
      - win_details: 中奖逐笔 [(彩种名, 奖级名, 金额分)]（{M}=len）
      - unwon_lottery_names: 未中奖彩种名名单（{X}=len）

    旧实现传注数（wins+loses）-> 用户追 1 彩种 5 注时文案误报「5 个追投彩种」
    （2026-07-28 NAS 实测）。改为彩种级聚合对齐 spec。

    「点击查看明细」已删除：Bark 纯文本推送无 click URL 落地页，承诺点开是空头。
    后续若加 SITE_URL + Bark url 字段跳转再恢复。
    """
    win_lines = [f'  · {name} {tier} {_fmt_amount(amt)}' for name, tier, amt in win_details]
    detail = '\n'.join(win_lines) if win_lines else '无'
    unwon = '、'.join(unwon_lottery_names) if unwon_lottery_names else '无'
    wins = len(win_details)
    loses = len(unwon_lottery_names)
    title = f'兑奖了吗 · {date_str} 核对汇总'
    body = (
        f'本期共核对 {tracked_lottery_count} 个追投彩种，中奖 {wins} 笔：\n{detail}\n'
        f'未中奖彩种（{loses}）：{unwon}。以官方开奖为准。理性购彩。'
    )
    return NotificationPayload(title=title, body=body)
