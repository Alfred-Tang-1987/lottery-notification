"""7 大彩种奖级表（可配置数据文件，按规则生效日版本化）。
固定档金额对照 docs/reference/lottery-rules.md + 官方公告；政策调整改此文件不改代码。
condition 用 front_hit/back_hit 表达式（partition/positional 通用变量）。
七星彩(qxc) 用 front_hit=前区任意对位命中数、back_hit=后区命中（见 QxcHybridCompare）。

版本门（2026-08-14，eng-review Issue 3）：规则变更只允许在 _VERSIONED_TABLES 追加新行，
不得改历史行——官方更正触发的历史期重比按「当时生效」的规则表判定。"""

from datetime import date, datetime

from app.domain.prize import AmountType, PrizeTier

# （_F/_V 与元/分纪律注释保持原样）

_F = AmountType.FIXED
_V = AmountType.FLOAT

_SSQ = [
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
]
# ssq 2026-02-01 新规：固定档不变（2026093 期实测），仅新增福运奖（奖池 ≥15 亿时 3+0=5 元，
# 依赖奖池数据，未实现 → B2）与一二等单期封顶（不影响比对，金额官方回填）。

# 大乐透 2019 九档（2019-02-20 第 19019 期 — 2026-01-30 开奖期）：
# 三等 5+0=10000 / 四等 4+2=3000 / 五等 4+1=300 / 六等 3+2=200 / 七等 4+0=100 /
# 八等 3+1|2+2=15 / 九等 3+0|1+2|2+1|0+2=5 元。追加仅一二等 80%（1.8）。
# ⚠️ 这是官方正确九档表——本仓库旧代码里的「合并条件贴 2019 金额」表是错误表，未作历史版本保留。
_DLT_2019 = [
    PrizeTier(1, 'front_hit==5 and back_hit==2', None, _V, append_multiplier=1.8),
    PrizeTier(2, 'front_hit==5 and back_hit==1', None, _V, append_multiplier=1.8),
    PrizeTier(3, 'front_hit==5 and back_hit==0', 1000000, _F),
    PrizeTier(4, 'front_hit==4 and back_hit==2', 300000, _F),
    PrizeTier(5, 'front_hit==4 and back_hit==1', 30000, _F),
    PrizeTier(6, 'front_hit==3 and back_hit==2', 20000, _F),
    PrizeTier(7, 'front_hit==4 and back_hit==0', 10000, _F),
    PrizeTier(8, '(front_hit==3 and back_hit==1) or (front_hit==2 and back_hit==2)', 1500, _F),
    PrizeTier(
        9,
        '(front_hit==3 and back_hit==0) or (front_hit==1 and back_hit==2) or (front_hit==2 and back_hit==1) or (front_hit==0 and back_hit==2)',
        500,
        _F,
    ),
]

# 大乐透 2026 七档（财综〔2025〕51 号，2026-01-31 第 26014 期起；9 档并 7 档）：
# 一二等浮动（追加 1.8 不变）；三等 5000 / 四等 300 / 五等 150 / 六等 15 / 七等 5 元。
# 金额为奖池 <8 亿基础档；≥8 亿上浮（6666/380/200/18/7）需奖池数据，未实现（B2 roadmap）。
# 1+1 / 2+0 / 0+1 不中奖。
_DLT_2026 = [
    PrizeTier(1, 'front_hit==5 and back_hit==2', None, _V, append_multiplier=1.8),
    PrizeTier(2, 'front_hit==5 and back_hit==1', None, _V, append_multiplier=1.8),
    PrizeTier(3, '(front_hit==5 and back_hit==0) or (front_hit==4 and back_hit==2)', 500000, _F),
    PrizeTier(4, 'front_hit==4 and back_hit==1', 30000, _F),
    PrizeTier(5, '(front_hit==4 and back_hit==0) or (front_hit==3 and back_hit==2)', 15000, _F),
    PrizeTier(6, '(front_hit==3 and back_hit==1) or (front_hit==2 and back_hit==2)', 1500, _F),
    PrizeTier(
        7,
        '(front_hit==3 and back_hit==0) or (front_hit==1 and back_hit==2) or (front_hit==2 and back_hit==1) or (front_hit==0 and back_hit==2)',
        500,
        _F,
    ),
]

# 七乐彩（一二三等浮动=高等奖 70%/10%/20%；特别号 = back_hit；2026-08-14 核对福彩官方）：
# 四等 200 / 五等 50 / 六等 10 / 七等 5 元；七等仅 4+0（3+1 不中奖，旧表误含）。
_QLC = [
    PrizeTier(1, 'front_hit==7', None, _V),
    PrizeTier(2, 'front_hit==6 and back_hit==1', None, _V),
    PrizeTier(3, 'front_hit==6 and back_hit==0', None, _V),
    PrizeTier(4, 'front_hit==5 and back_hit==1', 20000, _F),
    PrizeTier(5, 'front_hit==5 and back_hit==0', 5000, _F),
    PrizeTier(6, 'front_hit==4 and back_hit==1', 1000, _F),
    PrizeTier(7, 'front_hit==4 and back_hit==0', 500, _F),
]

# 现状 qxc 表原样保留（T3 再改）
_QXC = [
    PrizeTier(1, 'front_hit==6 and back_hit==1', None, _V),
    PrizeTier(2, 'front_hit==6 and back_hit==0', None, _V),
    PrizeTier(3, 'front_hit==5 and back_hit==1', 180000, _F),
    PrizeTier(4, 'front_hit==5 and back_hit==0', 30000, _F),
    PrizeTier(5, 'front_hit==4 and back_hit==1', 10000, _F),
    PrizeTier(6, '(front_hit==4 and back_hit==0) or (front_hit==3 and back_hit==1)', 1000, _F),
]

_FC3D = [PrizeTier(1, 'front_hit==3', 104000, _F)]
_PL3 = [PrizeTier(1, 'front_hit==3', 104000, _F)]
_PL5 = [PrizeTier(1, 'front_hit==5', 10000000, _F)]

# 版本注册表：code -> [(生效日, 表)] 按生效日升序；最后一个 生效日<=draw_date 的生效。
# date.min = 系统最早数据起生效。qxc 2020 改版前的纯 7 位旧规则早于系统任何数据（回填仅
# 最近约 50 期），不建版本。
_VERSIONED_TABLES: dict[str, list[tuple[date, list[PrizeTier]]]] = {
    'ssq': [(date.min, _SSQ)],
    'dlt': [(date.min, _DLT_2019), (date(2026, 1, 31), _DLT_2026)],
    'qlc': [(date.min, _QLC)],
    'qxc': [(date.min, _QXC)],
    'fc3d': [(date.min, _FC3D)],
    'pl3': [(date.min, _PL3)],
    'pl5': [(date.min, _PL5)],
}

# 兼容别名：现行版本直查表（既有 PRIZE_TABLES 引用方不破坏）。
PRIZE_TABLES: dict[str, list[PrizeTier]] = {code: versions[-1][1] for code, versions in _VERSIONED_TABLES.items()}


def get_tiers(lottery_code: str, draw_date: date | datetime | None = None) -> list[PrizeTier]:
    """按开奖日返回适用规则版本的奖级表（tier 1 最高升序）。

    draw_date=None → 现行（最新）版本；传 date/datetime 则返回生效日 ≤ 该日期的最新版本。
    datetime 自动归一为 date（DrawResult.draw_date 是 datetime）。
    """
    versions = _VERSIONED_TABLES[lottery_code]
    if draw_date is None:
        table = versions[-1][1]
    else:
        if isinstance(draw_date, datetime):
            draw_date = draw_date.date()
        table = max((v for v in versions if v[0] <= draw_date), key=lambda v: v[0])[1]
    return sorted(table, key=lambda t: t.tier)
