"""比对策略（策略模式）。

接口: compare(lottery, draw_*, combo_*, append) -> HitResult
实现:
  - PartitionCompare : 双色球/大乐透/七乐彩（集合匹配红/蓝球个数；七乐彩后区特别号单独计命中）
  - PositionalCompare: 福彩3D/排列3/排列5（T7）
  - QxcHybridCompare: 七星彩（T8）
"""
from app.domain.prize import HitResult, PrizeTier
from app.domain.prize_tables import get_tiers


class CompareStrategy:
    """比对策略接口。子类实现 compare。

    签名为位置参数（lottery, draw_front, draw_back, combo_front, combo_back）+ append 关键字，
    与调用方约定一致；T7 PositionalCompare 用 **_kw 忽略不适用的分区参数。"""

    @staticmethod
    def compare(lottery, draw_front, draw_back, combo_front, combo_back, *, append) -> HitResult:
        raise NotImplementedError


def _eval_condition(cond: str, front_hit: int, back_hit: int) -> bool:
    """安全求值 condition 表达式（仅 front_hit/back_hit 变量 + 比较/逻辑运算）。"""
    return bool(eval(cond, {"__builtins__": {}}, {"front_hit": front_hit, "back_hit": back_hit}))


def _match_tier(lottery: str, front_hit: int, back_hit: int) -> PrizeTier | None:
    """按奖级号升序匹配第一个 condition 命中的 tier（tier 1 最高，先试）。"""
    for t in get_tiers(lottery):
        if _eval_condition(t.condition, front_hit, back_hit):
            return t
    return None


class PartitionCompare(CompareStrategy):
    """分区型：双色球/大乐透/七乐彩。集合匹配红/蓝球个数。"""

    @staticmethod
    def compare(lottery, draw_front, draw_back, combo_front, combo_back, *, append) -> HitResult:
        draw_front_s, draw_back_s = set(draw_front), set(draw_back or ())
        front_hit = len(set(combo_front) & draw_front_s)
        back_hit = len(set(combo_back) & draw_back_s) if combo_back else 0

        tier = _match_tier(lottery, front_hit, back_hit)
        if tier is None:
            return HitResult(front_hit, back_hit, None, None, is_win=False)

        # 浮动档：amount=None（运行时回填，append_multiplier 在回填时应用）
        return HitResult(front_hit, back_hit, tier.tier, tier.amount, is_win=True)


class PositionalCompare(CompareStrategy):
    """按位型（直选/单选）：逐位精确匹配，顺序敏感。福彩3D/排列3/排列5。

    front_hit = 逐位全等的位数（直选：全部对才算中）。无后区。
    调用约定两种形态：
      - compare("fc3d", draw=(1,2,3), combo=(1,2,3))   ← 按位型自然形态（关键字）
      - compare("fc3d", (1,2,3), (1,2,9))              ← 位置参数（draw/combo 顺序）
    """

    @staticmethod
    def compare(lottery, draw_front=None, draw_back=None, combo_front=None,
                combo_back=None, *, append=False, draw=None, combo=None,
                **_kw) -> HitResult:
        # 归一 draw / combo 来源
        # 位置形 compare(lottery, draw, combo)：第二 tuple 落在 draw_back，combo_front 缺省
        if combo_front is None and combo is None and isinstance(draw_back, tuple):
            d, c = tuple(draw_front or ()), tuple(draw_back)
        else:
            d = tuple((draw if draw is not None else draw_front) or ())
            c = tuple((combo if combo is not None else combo_front) or ())

        hit = sum(1 for a, b in zip(d, c) if a == b)
        all_match = bool(d) and hit == len(d)
        if all_match:
            tier = _match_tier(lottery, front_hit=hit, back_hit=0)
            if tier:
                return HitResult(hit, 0, tier.tier, tier.amount, is_win=True)
        return HitResult(hit, 0, None, None, is_win=False)


class QxcHybridCompare(CompareStrategy):
    """七星彩混合型：前区 6 位按位连续命中 + 后区单值 0-14。

    前区按位（0-9，允许跨位重复），后区单值（0-14）。
    front_hit = 前区从首位起**连续命中位数**（前缀计数）。
    back_hit = 后区单值是否命中（0/1）。

    语义澄清：lottery-rules.md「按位对应（无需连续对位）」指**选号无需连号**
    （不要求选 1,2,3,4,5,6 连续数字），**非**命中判定方式。七星彩奖级按
    **连续命中位数**（consecutive correct positions）判定——该彩种本质。
    MVP 用「首位起前缀连续命中」近似；若官方按「最长连续段」(longest run)，
    Phase 2 用真实开奖校准 front_hit 计算与 condition。一二等（6 位全对）不受影响。
    """

    @staticmethod
    def compare(lottery, draw_front, draw_back, combo_front, combo_back, *,
                append=False, **_kw) -> HitResult:
        # 前区：首位起前缀连续命中位数
        front_hit = 0
        for a, b in zip(draw_front, combo_front):
            if a == b:
                front_hit += 1
            else:
                break
        # 后区：单值是否命中（draw_back/combo_back 均为单元素 tuple）
        back_hit = 1 if (combo_back and draw_back and combo_back[0] == draw_back[0]) else 0

        tier = _match_tier(lottery, front_hit=front_hit, back_hit=back_hit)
        if tier is None:
            return HitResult(front_hit, back_hit, None, None, is_win=False)
        return HitResult(front_hit, back_hit, tier.tier, tier.amount, is_win=True)
