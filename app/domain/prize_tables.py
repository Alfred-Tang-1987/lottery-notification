"""7 大彩种奖级表（可配置数据文件）。
固定档金额对照 docs/reference/lottery-rules.md + 官方公告；政策调整改此文件不改代码。
condition 用 front_hit/back_hit 表达式（partition/positional 通用变量）。
七星彩(qxc) 用 front_hit=前区连续命中位数、back_hit=后区命中（见 QxcHybridCompare）。"""

from app.domain.prize import AmountType, PrizeTier

# 金额单位：分
_F = AmountType.FIXED
_V = AmountType.FLOAT

PRIZE_TABLES: dict[str, list[PrizeTier]] = {
    # 双色球（spec §5.3 权威）
    'ssq': [
        PrizeTier(1, 'front_hit==6 and back_hit==1', None, _V),
        PrizeTier(2, 'front_hit==6 and back_hit==0', None, _V),
        PrizeTier(3, 'front_hit==5 and back_hit==1', 3000, _F),
        PrizeTier(4, '(front_hit==5 and back_hit==0) or (front_hit==4 and back_hit==1)', 200, _F),
        PrizeTier(5, '(front_hit==4 and back_hit==0) or (front_hit==3 and back_hit==1)', 10, _F),
        PrizeTier(
            6,
            '(front_hit==2 and back_hit==1) or (front_hit==1 and back_hit==1) or (front_hit==0 and back_hit==1)',
            5,
            _F,
        ),
    ],
    # 大乐透（一二等浮动 + 追加 1.8；三等及以下固定，以官方为准）
    'dlt': [
        PrizeTier(1, 'front_hit==5 and back_hit==2', None, _V, append_multiplier=1.8),
        PrizeTier(2, 'front_hit==5 and back_hit==1', None, _V, append_multiplier=1.8),
        PrizeTier(3, '(front_hit==5 and back_hit==0) or (front_hit==4 and back_hit==2)', 10000, _F),
        PrizeTier(4, '(front_hit==4 and back_hit==1) or (front_hit==3 and back_hit==2)', 3000, _F),
        PrizeTier(
            5,
            '(front_hit==4 and back_hit==0) or (front_hit==3 and back_hit==1) or (front_hit==2 and back_hit==2)',
            300,
            _F,
        ),
        PrizeTier(
            6,
            '(front_hit==3 and back_hit==0) or (front_hit==1 and back_hit==2) or (front_hit==2 and back_hit==1) or (front_hit==0 and back_hit==2)',
            200,
            _F,
        ),
        PrizeTier(
            7,
            '(front_hit==1 and back_hit==1) or (front_hit==2 and back_hit==0) or (front_hit==0 and back_hit==1)',
            100,
            _F,
        ),
        # 大乐透固定档 3-7 等金额以官方为准（可配置）；八/九等低奖条件复杂，Phase 2 按官方补全
    ],
    # 七乐彩（一等浮动；特别号 = back_hit；固定档以官方为准）
    'qlc': [
        PrizeTier(1, 'front_hit==7', None, _V),
        PrizeTier(2, 'front_hit==6 and back_hit==1', None, _V),
        PrizeTier(3, 'front_hit==6 and back_hit==0', 3045, _F),  # 以官方为准
        PrizeTier(4, 'front_hit==5 and back_hit==1', 300, _F),
        PrizeTier(5, 'front_hit==5 and back_hit==0', 50, _F),
        PrizeTier(6, 'front_hit==4 and back_hit==1', 10, _F),
        PrizeTier(7, '(front_hit==4 and back_hit==0) or (front_hit==3 and back_hit==1)', 5, _F),
    ],
    # 七星彩（前区连续命中位 front_hit + 后区命中 back_hit；一二等浮动；以官方为准）
    'qxc': [
        PrizeTier(1, 'front_hit==6 and back_hit==1', None, _V),
        PrizeTier(2, 'front_hit==6 and back_hit==0', None, _V),
        PrizeTier(3, 'front_hit==5 and back_hit==1', 1800, _F),
        PrizeTier(4, 'front_hit==5 and back_hit==0', 300, _F),
        PrizeTier(5, 'front_hit==4 and back_hit==1', 100, _F),
        PrizeTier(6, '(front_hit==4 and back_hit==0) or (front_hit==3 and back_hit==1)', 10, _F),
    ],
    # 福彩3D 单选（直选全对，固定 1040；以官方为准）
    'fc3d': [
        PrizeTier(1, 'front_hit==3', 1040, _F),  # 单选全对
    ],
    # 排列3 直选（固定 1040；以官方为准）
    'pl3': [
        PrizeTier(1, 'front_hit==3', 1040, _F),
    ],
    # 排列5 直选（固定 100000；lottery-rules 确认 10 万/注）
    'pl5': [
        PrizeTier(1, 'front_hit==5', 100000, _F),
    ],
}


def get_tiers(lottery_code: str) -> list[PrizeTier]:
    """按奖级号升序返回（tier 1 最高）。"""
    return sorted(PRIZE_TABLES[lottery_code], key=lambda t: t.tier)
