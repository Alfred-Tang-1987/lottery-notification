from dataclasses import dataclass


@dataclass(frozen=True)
class NumberRange:
    """分区型号码范围：集合语义，去重、无序。用于双色球红球/蓝球、大乐透前/后区等。"""
    min: int
    max: int
    count: int

    def __post_init__(self) -> None:
        if self.min > self.max:
            raise ValueError(f"min({self.min}) > max({self.max})")
        pool = self.max - self.min + 1
        if self.count < 1:
            raise ValueError(f"count 必须 ≥1，当前 {self.count}")
        if self.count > pool:
            raise ValueError(f"count({self.count}) 超过可用号码数({pool})：无法去重选 {self.count} 个")


@dataclass(frozen=True)
class PositionalDigits:
    """按位型号码：有序、每位独立、允许跨位重复。用于福彩3D/排列3/排列5/七星彩前区。"""
    min: int
    max: int
    length: int

    def __post_init__(self) -> None:
        if self.min > self.max:
            raise ValueError(f"min({self.min}) > max({self.max})")
        if self.length < 1:
            raise ValueError(f"length 必须 ≥1，当前 {self.length}")
