"""7 大彩种奖级表（可配置数据文件）。
固定档金额对照 docs/reference/lottery-rules.md + 官方公告；政策调整改此文件不改代码。
condition 用 front_hit/back_hit 表达式（partition/positional 通用变量）。
七星彩(qxc) 用 front_hit=前区连续命中位数、back_hit=后区命中（见 QxcHybridCompare）。"""

from app.domain.prize import AmountType, PrizeTier

# 金额单位：分（= 元 × 100）。固定档金额对照 docs/reference/lottery-rules.md + 官方公告。
# ⚠️ 必须用分：系统全程按分处理（Ticket.cost=200 分=2 元、dashboard「公益贡献（分）」、
# 前端 fmtMoney(cents/100)、浮动奖 refill adapter int(元)*100）。
# 旧版误把官方「元」金额原样录入（如 ssq 六等 5）被当分 -> 推送显示缩小 100 倍
# （5 元显示 0.05 元，2026-08-03 用户实测）。固定档一律 ×100 转分。
_F = AmountType.FIXED
_V = AmountType.FLOAT

PRIZE_TABLES: dict[str, list[PrizeTier]] = {
    # 双色球（spec §5.3 权威）：三等 3000 / 四等 200 / 五等 10 / 六等 5 元
    'ssq': [
        PrizeTier(1, 'front_hit==6 and back_hit==1', None, _V),
        PrizeTier(2, 'front_hit==6 and back_hit==0', None, _V),
        PrizeTier(3, 'front_hit==5 and back_hit==1', 300000, _F),
        PrizeTier(4, '(front_hit==5 and back_hit==0) or (front_hit==4 and back_hit==1)', 20000, _F),
        PrizeTier(5, '(front_hit==4 and back_hit==0) or (front_hit==3 and back_hit==1)', 1000, _F),
        PrizeTier(
            6,
            '(front_hit==2 and back_hit==1) or (front_hit==1 and back_hit==1) or (front_hit==0 and back_hit==1)',
            500,
            _F,
        ),
    ],
    # 大乐透（一二等浮动 + 追加 1.8；三等及以下固定，以官方为准）：
    # 三等 10000 / 四等 3000 / 五等 300 / 六等 200 / 七等 100 元
    'dlt': [
        PrizeTier(1, 'front_hit==5 and back_hit==2', None, _V, append_multiplier=1.8),
        PrizeTier(2, 'front_hit==5 and back_hit==1', None, _V, append_multiplier=1.8),
        PrizeTier(3, '(front_hit==5 and back_hit==0) or (front_hit==4 and back_hit==2)', 1000000, _F),
        PrizeTier(4, '(front_hit==4 and back_hit==1) or (front_hit==3 and back_hit==2)', 300000, _F),
        PrizeTier(
            5,
            '(front_hit==4 and back_hit==0) or (front_hit==3 and back_hit==1) or (front_hit==2 and back_hit==2)',
            30000,
            _F,
        ),
        PrizeTier(
            6,
            '(front_hit==3 and back_hit==0) or (front_hit==1 and back_hit==2) or (front_hit==2 and back_hit==1) or (front_hit==0 and back_hit==2)',
            20000,
            _F,
        ),
        PrizeTier(
            7,
            '(front_hit==1 and back_hit==1) or (front_hit==2 and back_hit==0) or (front_hit==0 and back_hit==1)',
            10000,
            _F,
        ),
        # 大乐透固定档 3-7 等金额以官方为准（可配置）；八/九等低奖条件复杂，Phase 2 按官方补全
    ],
    # 七乐彩（一等浮动；特别号 = back_hit；固定档以官方为准）：
    # 三等 3045 / 四等 300 / 五等 50 / 六等 10 / 七等 5 元
    'qlc': [
        PrizeTier(1, 'front_hit==7', None, _V),
        PrizeTier(2, 'front_hit==6 and back_hit==1', None, _V),
        PrizeTier(3, 'front_hit==6 and back_hit==0', 304500, _F),  # 以官方为准
        PrizeTier(4, 'front_hit==5 and back_hit==1', 30000, _F),
        PrizeTier(5, 'front_hit==5 and back_hit==0', 5000, _F),
        PrizeTier(6, 'front_hit==4 and back_hit==1', 1000, _F),
        PrizeTier(7, '(front_hit==4 and back_hit==0) or (front_hit==3 and back_hit==1)', 500, _F),
    ],
    # 七星彩（前区连续命中位 front_hit + 后区命中 back_hit；一二等浮动；以官方为准）：
    # 三等 1800 / 四等 300 / 五等 100 / 六等 10 元
    'qxc': [
        PrizeTier(1, 'front_hit==6 and back_hit==1', None, _V),
        PrizeTier(2, 'front_hit==6 and back_hit==0', None, _V),
        PrizeTier(3, 'front_hit==5 and back_hit==1', 180000, _F),
        PrizeTier(4, 'front_hit==5 and back_hit==0', 30000, _F),
        PrizeTier(5, 'front_hit==4 and back_hit==1', 10000, _F),
        PrizeTier(6, '(front_hit==4 and back_hit==0) or (front_hit==3 and back_hit==1)', 1000, _F),
    ],
    # 福彩3D 单选（直选全对，固定 1040 元；以官方为准）
    'fc3d': [
        PrizeTier(1, 'front_hit==3', 104000, _F),  # 单选全对
    ],
    # 排列3 直选（固定 1040 元；以官方为准）
    'pl3': [
        PrizeTier(1, 'front_hit==3', 104000, _F),
    ],
    # 排列5 直选（固定 10 万/注 = 10000000 分；lottery-rules 确认 10 万/注）
    'pl5': [
        PrizeTier(1, 'front_hit==5', 10000000, _F),
    ],
}


def get_tiers(lottery_code: str) -> list[PrizeTier]:
    """按奖级号升序返回（tier 1 最高）。"""
    return sorted(PRIZE_TABLES[lottery_code], key=lambda t: t.tier)
