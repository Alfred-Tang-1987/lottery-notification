from pydantic import BaseModel, Field


class NumberRangeModel(BaseModel):
    min: int
    max: int
    count: int


class PositionalDigitsModel(BaseModel):
    min: int
    max: int
    length: int


class LotterySpecModel(BaseModel):
    """spec_json 的校验 schema（与领域 LotterySpec 同形，Plan 02 复用）。"""

    code: str
    name: str
    category: str  # welfare | sport
    number_style: str  # partition | positional | hybrid
    front: NumberRangeModel | PositionalDigitsModel
    back: NumberRangeModel | PositionalDigitsModel | None = None
    draw_days: list[int] = Field(description='0=周一…6=周日（Python weekday）')
    play_types: list[str]
    welfare_rate: int = Field(ge=0, le=100)
    price_per_bet: int = Field(gt=0, description='分')
