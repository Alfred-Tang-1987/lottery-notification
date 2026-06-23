from dataclasses import dataclass
from enum import Enum


class AmountType(str, Enum):
    FIXED = "fixed"
    FLOAT = "float"


@dataclass(frozen=True)
class PrizeTier:
    """奖级：命中条件 → 奖级号 → 奖金类型。append_multiplier 仅大乐透一二等奖。"""
    tier: int
    condition: str  # 表达式字符串（如 "front_hit==6 and back_hit==1"）
    amount: int | None  # 分；float 档为 None
    amount_type: AmountType
    append_multiplier: float = 1.0


@dataclass(frozen=True)
class HitResult:
    """一次比对结果。"""
    front_hit: int
    back_hit: int
    tier: int | None  # None = 未中奖
    amount: int | None  # 分；None = 浮动待派奖
    is_win: bool
