from dataclasses import dataclass
from typing import Any


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


class LotterySpec:
    """彩种规格（配置驱动）。从 spec_json dict hydrate，校验所有不变式。"""

    def __init__(
        self,
        code: str,
        name: str,
        category: str,
        number_style: str,
        front: NumberRange | PositionalDigits,
        back: NumberRange | PositionalDigits | None,
        draw_days: list[int],
        play_types: list[str],
        welfare_rate: int,
        price_per_bet: int,
    ):
        if number_style not in ("partition", "positional", "hybrid"):
            raise ValueError(f"未知 number_style: {number_style}")
        if not 0 <= welfare_rate <= 100:
            raise ValueError(f"welfare_rate 须 0-100，当前 {welfare_rate}")
        if price_per_bet <= 0:
            raise ValueError(f"price_per_bet 须 >0，当前 {price_per_bet}")
        if not draw_days or not all(0 <= d <= 6 for d in draw_days):
            raise ValueError(f"draw_days 须 0-6（周一=0…周日=6），当前 {draw_days}")
        self.code = code
        self.name = name
        self.category = category
        self.number_style = number_style
        self.front = front
        self.back = back
        self.draw_days = draw_days
        self.play_types = play_types
        self.welfare_rate = welfare_rate
        self.price_per_bet = price_per_bet

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "LotterySpec":
        front = cls._zone(d["front"])
        back = cls._zone(d["back"]) if d.get("back") else None
        return cls(
            code=d["code"], name=d["name"], category=d["category"],
            number_style=d["number_style"], front=front, back=back,
            draw_days=list(d["draw_days"]), play_types=list(d["play_types"]),
            welfare_rate=d["welfare_rate"], price_per_bet=d["price_per_bet"],
        )

    @staticmethod
    def _zone(z: dict[str, Any]) -> NumberRange | PositionalDigits:
        if "length" in z:
            return PositionalDigits(min=z["min"], max=z["max"], length=z["length"])
        return NumberRange(min=z["min"], max=z["max"], count=z["count"])
