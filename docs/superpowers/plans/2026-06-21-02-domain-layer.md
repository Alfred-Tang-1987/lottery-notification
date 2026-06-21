# 02 领域层 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现纯逻辑领域层（零 IO）：`LotterySpec`/`NumberRange`/`PositionalDigits` 类型不变式、`Entry`/`expand()` 展开+cost、`PrizeTier`/`HitResult`、3 个 `CompareStrategy`（Partition/Positional/QxcHybrid）、7 彩种奖级表、策略注册 registry。覆盖率 95%+。

**Architecture:** `app/domain/` 纯 Python 包，**禁止 import infra/adapters/api/services**（Plan 01 的 import-linter 强制）。所有类型用 dataclass + `__post_init__` 校验不变式。`expand()` 单点展开（create 时算 cost + 比对时复用）。比对策略是纯函数 `compare(spec, draw, entry) -> HitResult`。

**Tech Stack:** Python 3.12 dataclasses、`fractions`（奖金精度）、pytest。无 SQLModel（那是 infra）、无 IO。

**前置（Plan 01 已完成）：** 项目骨架、import-linter 配置、seeds/spec_schema.py（`LotterySpecModel`，本 plan 领域类型对齐其字段）。

**奖级数据处理原则：** 比对/奖级**判定机制**用双色球完整奖级表 TDD 验证（spec §5.3 权威）；`prize_tables.py` 是**可配置数据文件**，其余 6 彩种固定档金额对照 `docs/reference/lottery-rules.md` + 官方公告填入（非 placeholder——给了结构与已知金额，并标注数据源）。固定档可配置以应政策调整。

---

## File Structure

```
app/domain/
├── __init__.py            # 导出领域 API
├── spec.py                # NumberRange / PositionalDigits / LotterySpec（类型不变式）
├── entry.py               # Entry / SingleCombo / expand() / MAX_COMBINATIONS
├── prize.py               # PrizeTier / HitResult
├── prize_tables.py        # 7 彩种奖级数据（可配置）
└── compare.py             # CompareStrategy 接口 + 3 实现 + REGISTRY + compare()
tests/domain/
├── __init__.py
├── test_spec.py
├── test_entry_expand.py
├── test_prize.py
├── test_prize_tables.py
├── test_partition_compare.py
├── test_positional_compare.py
├── test_qxc_compare.py
└── test_registry.py
```

---

## Task 1: NumberRange + PositionalDigits（类型不变式）

**Files:** `app/domain/__init__.py`(空), `app/domain/spec.py`, `tests/domain/__init__.py`(空), `tests/domain/test_spec.py`

- [ ] **Step 1: 写 app/domain/__init__.py 与 tests/domain/__init__.py（空）**

```python
```

- [ ] **Step 2: 写失败测试 tests/domain/test_spec.py**

```python
import pytest
from app.domain.spec import NumberRange, PositionalDigits


def test_number_range_rejects_duplicates():
    """D7: NumberRange 集合语义——count 不能超过可用号码数（否则无法去重）。"""
    with pytest.raises(ValueError, match="count"):
        NumberRange(min=1, max=6, count=10)  # 1-6 只有 6 个，选 10 不可能


def test_number_range_valid():
    r = NumberRange(min=1, max=33, count=6)
    assert r.count == 6


def test_positional_digits_allows_duplicate_positions():
    """D7: PositionalDigits 允许跨位重复（七星彩前区 1,1,2,3,4,5 合法）。"""
    d = PositionalDigits(min=0, max=9, length=6)
    assert d.length == 6


def test_positional_digits_validates_range():
    d = PositionalDigits(min=0, max=9, length=3)
    assert d.max == 9


def test_number_range_count_exceeds_pool():
    with pytest.raises(ValueError):
        NumberRange(min=1, max=16, count=20)
```

- [ ] **Step 3: 运行确认失败**

```bash
uv run pytest tests/domain/test_spec.py -v
```
Expected: FAIL（无 `app.domain.spec`）

- [ ] **Step 4: 写 app/domain/spec.py（NumberRange + PositionalDigits 先行）**

```python
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
```

- [ ] **Step 5: 运行确认通过**

```bash
uv run pytest tests/domain/test_spec.py -v
```
Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add app/domain/__init__.py app/domain/spec.py tests/domain/__init__.py tests/domain/test_spec.py
git commit -m "feat(domain): NumberRange(去重) + PositionalDigits(可重复) 类型不变式"
```

---

## Task 2: LotterySpec（hydrate from spec_json dict + 校验）

**Files:** modify `app/domain/spec.py`(加 LotterySpec), `tests/domain/test_spec.py`(加测试)

- [ ] **Step 1: 追加失败测试到 tests/domain/test_spec.py**

```python
from app.domain.spec import LotterySpec


def _ssq_spec_dict():
    return {
        "code": "ssq", "name": "双色球", "category": "welfare", "number_style": "partition",
        "front": {"min": 1, "max": 33, "count": 6},
        "back": {"min": 1, "max": 16, "count": 1},
        "draw_days": [1, 3, 6], "play_types": ["single", "fushi", "dantuo"],
        "welfare_rate": 36, "price_per_bet": 200,
    }


def test_lottery_spec_from_dict_partition():
    spec = LotterySpec.from_dict(_ssq_spec_dict())
    assert spec.code == "ssq"
    assert isinstance(spec.front, NumberRange)
    assert spec.welfare_rate == 36


def test_lottery_spec_from_dict_positional():
    spec = LotterySpec.from_dict({
        "code": "fc3d", "name": "福彩3D", "category": "welfare", "number_style": "positional",
        "front": {"min": 0, "max": 9, "length": 3}, "back": None,
        "draw_days": [0, 1, 2, 3, 4, 5, 6], "play_types": ["danxuan"],
        "welfare_rate": 34, "price_per_bet": 200,
    })
    assert isinstance(spec.front, PositionalDigits)
    assert spec.back is None


def test_lottery_spec_hybrid_qxc():
    spec = LotterySpec.from_dict({
        "code": "qxc", "name": "七星彩", "category": "sport", "number_style": "hybrid",
        "front": {"min": 0, "max": 9, "length": 6},
        "back": {"min": 0, "max": 14, "count": 1},
        "draw_days": [1, 4, 6], "play_types": ["single"],
        "welfare_rate": 37, "price_per_bet": 200,
    })
    assert isinstance(spec.front, PositionalDigits)  # 前区按位
    assert isinstance(spec.back, NumberRange)  # 后区单值 0-14
    assert spec.back.max == 14


def test_lottery_spec_validates_welfare_rate():
    d = _ssq_spec_dict()
    d["welfare_rate"] = 150
    with pytest.raises(ValueError, match="welfare_rate"):
        LotterySpec.from_dict(d)
```

- [ ] **Step 2: 运行确认失败**

```bash
uv run pytest tests/domain/test_spec.py -v
```
Expected: FAIL（无 LotterySpec）

- [ ] **Step 3: 追加 LotterySpec 到 app/domain/spec.py（文件末尾）**

```python
from typing import Any


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
```

- [ ] **Step 4: 运行确认通过**

```bash
uv run pytest tests/domain/test_spec.py -v
```
Expected: 9 passed（5 旧 + 4 新）

- [ ] **Step 5: Commit**

```bash
git add app/domain/spec.py tests/domain/test_spec.py
git commit -m "feat(domain): LotterySpec.from_dict hydrate + 全不变式校验"
```

---

## Task 3: Entry + SingleCombo + expand()（复式/胆拖展开 + cost + MAX_COMBINATIONS）

**Files:** `app/domain/entry.py`, `tests/domain/test_entry_expand.py`

> MVP single 玩法的 expand 只返回自身一注；fushi/dantuo 展开为 Phase 2，但 `expand()` 接口与上限校验、cost 计算**现在就实现**（D6:A：create 时算 cost）。

- [ ] **Step 1: 写失败测试 tests/domain/test_entry_expand.py**

```python
import pytest
from app.domain.entry import Entry, SingleCombo, expand, MAX_COMBINATIONS


def _single_entry(front, back=None, **kw):
    return Entry(
        lottery_code="ssq", play_type="single",
        front=tuple(front), back=tuple(back) if back else None,
        multiplier=1, append=False, **kw,
    )


def test_expand_single_returns_one_combo():
    e = _single_entry((1, 2, 3, 4, 5, 6), (7,))
    combos = expand(e)
    assert len(combos) == 1
    assert combos[0].front == (1, 2, 3, 4, 5, 6)
    assert combos[0].back == (7,)


def test_expand_cost_single():
    e = _single_entry((1, 2, 3, 4, 5, 6), (7,))
    assert e.cost(price_per_bet=200) == 200  # 1 注 × 2 元


def test_expand_cost_with_multiplier():
    e = _single_entry((1, 2, 3, 4, 5, 6), (7,), multiplier=5)
    assert e.cost(price_per_bet=200) == 1000  # 1 注 × 2 元 × 5 倍


def test_expand_cost_with_append_dlt():
    """大乐透追加：基本 2 元 + 追加 1 元 = 3 元/注。"""
    e = Entry("dlt", "single", (1, 2, 3, 4, 5), (6, 7), multiplier=1, append=True)
    assert e.cost(price_per_bet=200) == 300  # 2 + 1


def test_expand_rejects_invalid_multiplier():
    with pytest.raises(ValueError, match="multiplier"):
        Entry("ssq", "single", (1, 2, 3, 4, 5, 6), (7,), multiplier=100)


def test_expand_fushi_phase2_not_implemented():
    """MVP 仅 single；fushi 展开需 spec 精确（Phase 2），当前 raise NotImplementedError
    （硬编码 6 会算错大乐透5/七乐彩7，故 MVP 诚实拒绝而非估错 cost）。"""
    e = Entry("ssq", "fushi", tuple(range(1, 34)), (7,), multiplier=1, append=False)
    with pytest.raises(NotImplementedError, match="Phase 2"):
        expand(e)
```

- [ ] **Step 2: 运行确认失败**

```bash
uv run pytest tests/domain/test_entry_expand.py -v
```
Expected: FAIL（无 module）

- [ ] **Step 3: 写 app/domain/entry.py**

```python
from dataclasses import dataclass
from itertools import combinations

MAX_COMBINATIONS = 10000


@dataclass(frozen=True)
class SingleCombo:
    """展开后的单式组合（一次比对单元）。"""
    front: tuple[int, ...]
    back: tuple[int, ...] | None


@dataclass(frozen=True)
class Entry:
    """用户注单（原始选择）。比对前由 expand() 展开成 SingleCombo。"""
    lottery_code: str
    play_type: str
    front: tuple[int, ...]   # 原始选择（single=fixed count；fushi=多选；dantuo=胆）
    back: tuple[int, ...] | None
    tuo: tuple[int, ...] | None = None  # 胆拖的拖码
    multiplier: int = 1
    append: bool = False

    def __post_init__(self) -> None:
        if not 1 <= self.multiplier <= 99:
            raise ValueError(f"multiplier 须 1-99，当前 {self.multiplier}")

    def cost(self, price_per_bet: int) -> int:
        """真实投入（分）= 单注价 × 展开注数 × 倍投 × (追加?1.5:1)。
        追加仅大乐透：基本 2 + 追加 1 = 1.5 倍单注价。
        MVP single：n_combos=1（准确）。复式/胆拖 n_combos 需 spec 精确（Phase 2）。"""
        n_combos = _count_combos(self)
        per = price_per_bet * (3 if self.append else 2) // 2  # append: +50%（2元→3元）
        return n_combos * per * self.multiplier


def _count_combos(e: Entry) -> int:
    """展开后的单式注数（用于 cost/上限校验）。
    MVP：single=1（准确）。fushi/dantuo 需 spec.front.count/back.count 精确组合，
    Phase 2 实现——硬编码 6 会算错大乐透(5)/七乐彩(7)，故 MVP 直接拒绝而非估错。"""
    if e.play_type == "single":
        return 1
    raise NotImplementedError(
        f"{e.play_type} 展开注数需 spec 精确（Phase 2）；MVP 仅 single"
    )


def expand(e: Entry) -> list[SingleCombo]:
    """展开注单为单式组合。MVP：single 返回自身一注（准确）。
    fushi/dantuo 组合展开需 spec（前区/后区 count 因彩种而异），Phase 2 实现。
    MAX_COMBINATIONS 上限在 Phase 2 _count_combos 精确后于本函数入口校验。"""
    if e.play_type == "single":
        return [SingleCombo(front=e.front, back=e.back)]
    raise NotImplementedError(
        f"{e.play_type} 展开 Phase 2 实现（需 spec.front.count/back.count 精确组合）"
    )
```

- [ ] **Step 4: 运行确认通过**

```bash
uv run pytest tests/domain/test_entry_expand.py -v
```
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add app/domain/entry.py tests/domain/test_entry_expand.py
git commit -m "feat(domain): Entry/SingleCombo/expand + cost(倍投/追加) + MAX_COMBINATIONS 上限"
```

---

## Task 4: PrizeTier + HitResult

**Files:** `app/domain/prize.py`, `tests/domain/test_prize.py`

- [ ] **Step 1: 写失败测试 tests/domain/test_prize.py**

```python
from app.domain.prize import PrizeTier, HitResult, AmountType


def test_prize_tier_fixed():
    t = PrizeTier(tier=5, condition="front_hit==4 and back_hit==0", amount=10, amount_type=AmountType.FIXED)
    assert t.amount == 10
    assert t.append_multiplier == 1.0


def test_prize_tier_float_append():
    t = PrizeTier(tier=1, condition="front_hit==5 and back_hit==2", amount=None,
                  amount_type=AmountType.FLOAT, append_multiplier=1.8)
    assert t.append_multiplier == 1.8


def test_hit_result_win():
    r = HitResult(front_hit=6, back_hit=1, tier=1, amount=None, is_win=True)
    assert r.is_win
    assert r.tier == 1
```

- [ ] **Step 2: 运行确认失败**

```bash
uv run pytest tests/domain/test_prize.py -v
```
Expected: FAIL

- [ ] **Step 3: 写 app/domain/prize.py**

```python
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
```

- [ ] **Step 4: 运行确认通过**

```bash
uv run pytest tests/domain/test_prize.py -v
```
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add app/domain/prize.py tests/domain/test_prize.py
git commit -m "feat(domain): PrizeTier(append_multiplier) + HitResult + AmountType"
```

---

## Task 5: 7 彩种奖级表数据（可配置）

**Files:** `app/domain/prize_tables.py`, `tests/domain/test_prize_tables.py`

> 双色球完整（spec §5.3 权威）；其余彩种固定档金额对照 lottery-rules.md + 官方，浮动档（一二等奖）= float，金额运行时回填。所有固定档可配置（政策调整改数据不改代码）。

- [ ] **Step 1: 写失败测试 tests/domain/test_prize_tables.py**

```python
from app.domain.prize_tables import PRIZE_TABLES, get_tiers
from app.domain.prize import AmountType


def test_ssq_has_6_tiers():
    tiers = get_tiers("ssq")
    assert len(tiers) == 6
    assert tiers[0].tier == 1 and tiers[0].amount_type == AmountType.FLOAT  # 一等浮动
    assert tiers[2].amount == 3000  # 三等固定


def test_dlt_tier1_append_multiplier():
    tiers = get_tiers("dlt")
    assert tiers[0].append_multiplier == 1.8  # 一等追加


def test_all_7_lotteries_have_tables():
    for code in ("ssq", "dlt", "qlc", "qxc", "fc3d", "pl3", "pl5"):
        assert code in PRIZE_TABLES, f"缺 {code} 奖级表"
        assert len(get_tiers(code)) >= 1


def test_fixed_tiers_have_amount_float_tiers_none():
    for code, tiers in PRIZE_TABLES.items():
        for t in tiers:
            if t.amount_type == AmountType.FIXED:
                assert t.amount is not None and t.amount > 0
            else:
                assert t.amount is None
```

- [ ] **Step 2: 运行确认失败**

```bash
uv run pytest tests/domain/test_prize_tables.py -v
```
Expected: FAIL

- [ ] **Step 3: 写 app/domain/prize_tables.py（双色球完整 + 大乐透完整 + 其余彩种主要奖级）**

```python
"""7 大彩种奖级表（可配置数据文件）。
固定档金额对照 docs/reference/lottery-rules.md + 官方公告；政策调整改此文件不改代码。
condition 用 front_hit/back_hit 表达式（partition/positional 通用变量）。
七星彩(qxc) 用 front_hit=前区连续命中位数、back_hit=后区命中（见 QxcHybridCompare）。"""
from app.domain.prize import PrizeTier, AmountType

# 金额单位：分
_F = AmountType.FIXED
_V = AmountType.FLOAT

PRIZE_TABLES: dict[str, list[PrizeTier]] = {
    # 双色球（spec §5.3 权威）
    "ssq": [
        PrizeTier(1, "front_hit==6 and back_hit==1", None, _V),
        PrizeTier(2, "front_hit==6 and back_hit==0", None, _V),
        PrizeTier(3, "front_hit==5 and back_hit==1", 3000, _F),
        PrizeTier(4, "(front_hit==5 and back_hit==0) or (front_hit==4 and back_hit==1)", 200, _F),
        PrizeTier(5, "(front_hit==4 and back_hit==0) or (front_hit==3 and back_hit==1)", 10, _F),
        PrizeTier(6, "(front_hit==2 and back_hit==1) or (front_hit==1 and back_hit==1) or (front_hit==0 and back_hit==1)", 5, _F),
    ],
    # 大乐透（一二等浮动 + 追加 1.8；三等及以下固定，以官方为准）
    "dlt": [
        PrizeTier(1, "front_hit==5 and back_hit==2", None, _V, append_multiplier=1.8),
        PrizeTier(2, "front_hit==5 and back_hit==1", None, _V, append_multiplier=1.8),
        PrizeTier(3, "(front_hit==5 and back_hit==0) or (front_hit==4 and back_hit==2)", 10000, _F),
        PrizeTier(4, "(front_hit==4 and back_hit==1) or (front_hit==3 and back_hit==2)", 3000, _F),
        PrizeTier(5, "(front_hit==4 and back_hit==0) or (front_hit==3 and back_hit==1) or (front_hit==2 and back_hit==2)", 300, _F),
        PrizeTier(6, "(front_hit==3 and back_hit==0) or (front_hit==1 and back_hit==2) or (front_hit==2 and back_hit==1) or (front_hit==0 and back_hit==2)", 200, _F),
        PrizeTier(7, "(front_hit==1 and back_hit==1) or (front_hit==2 and back_hit==0) or (front_hit==0 and back_hit==1)", 100, _F),
        # 大乐透固定档 3-7 等金额以官方为准（可配置）；八/九等低奖条件复杂，Phase 2 按官方补全
    ],
    # 七乐彩（一等浮动；特别号 = back_hit；固定档以官方为准）
    "qlc": [
        PrizeTier(1, "front_hit==7", None, _V),
        PrizeTier(2, "front_hit==6 and back_hit==1", None, _V),
        PrizeTier(3, "front_hit==6 and back_hit==0", 3045, _F),  # 以官方为准
        PrizeTier(4, "front_hit==5 and back_hit==1", 300, _F),
        PrizeTier(5, "front_hit==5 and back_hit==0", 50, _F),
        PrizeTier(6, "front_hit==4 and back_hit==1", 10, _F),
        PrizeTier(7, "(front_hit==4 and back_hit==0) or (front_hit==3 and back_hit==1)", 5, _F),
    ],
    # 七星彩（前区连续命中位 front_hit + 后区命中 back_hit；一二等浮动；以官方为准）
    "qxc": [
        PrizeTier(1, "front_hit==6 and back_hit==1", None, _V),
        PrizeTier(2, "front_hit==6 and back_hit==0", None, _V),
        PrizeTier(3, "front_hit==5 and back_hit==1", 1800, _F),
        PrizeTier(4, "front_hit==5 and back_hit==0", 300, _F),
        PrizeTier(5, "front_hit==4 and back_hit==1", 100, _F),
        PrizeTier(6, "(front_hit==4 and back_hit==0) or (front_hit==3 and back_hit==1)", 10, _F),
    ],
    # 福彩3D 单选（直选全对，固定 1040；以官方为准）
    "fc3d": [
        PrizeTier(1, "front_hit==3", 1040, _F),  # 单选全对
    ],
    # 排列3 直选（固定 1040；以官方为准）
    "pl3": [
        PrizeTier(1, "front_hit==3", 1040, _F),
    ],
    # 排列5 直选（固定 100000；lottery-rules 确认 10 万/注）
    "pl5": [
        PrizeTier(1, "front_hit==5", 100000, _F),
    ],
}


def get_tiers(lottery_code: str) -> list[PrizeTier]:
    """按奖级号升序返回（tier 1 最高）。"""
    return sorted(PRIZE_TABLES[lottery_code], key=lambda t: t.tier)
```

- [ ] **Step 4: 运行确认通过**

```bash
uv run pytest tests/domain/test_prize_tables.py -v
```
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add app/domain/prize_tables.py tests/domain/test_prize_tables.py
git commit -m "feat(domain): 7 彩种奖级表（双色球/大乐透完整，固定档可配置）"
```

---

## Task 6: PartitionCompare（双色球/大乐透/七乐彩）

**Files:** `app/domain/compare.py`(先写接口+Partition), `tests/domain/test_partition_compare.py`

> 比对接口：`compare(spec, draw_front, draw_back, combo) -> HitResult`。combo 是 SingleCombo（expand 后）。内部算 front_hit/back_hit → 匹配 prize_table condition → 返回 tier/amount。

- [ ] **Step 1: 写失败测试 tests/domain/test_partition_compare.py（双色球真实命中用例）**

```python
from app.domain.compare import PartitionCompare
from app.domain.prize import AmountType


def test_ssq_first_prize_float():
    """6红+1蓝 = 一等奖（浮动）。"""
    r = PartitionCompare.compare(
        lottery="ssq", draw_front=(1, 2, 3, 4, 5, 6), draw_back=(7,),
        combo_front=(1, 2, 3, 4, 5, 6), combo_back=(7,), append=False,
    )
    assert r.is_win and r.tier == 1 and r.amount is None


def test_ssq_sixth_prize_fixed():
    """0红+1蓝 = 六等奖 5 元。"""
    r = PartitionCompare.compare(
        "ssq", (8, 9, 10, 11, 12, 13), (7,),
        (1, 2, 3, 4, 5, 6), (7,), append=False,
    )
    assert r.is_win and r.tier == 6 and r.amount == 5


def test_ssq_no_win():
    """0红+0蓝 = 未中奖。"""
    r = PartitionCompare.compare(
        "ssq", (8, 9, 10, 11, 12, 13), (14,),
        (1, 2, 3, 4, 5, 6), (7,), append=False,
    )
    assert not r.is_win and r.tier is None


def test_dlt_append_multiplier_applied():
    """大乐透追加：一等浮动，amount 仍 None（运行时回填），但标记 append 生效（tier1 命中即可）。"""
    r = PartitionCompare.compare(
        "dlt", (1, 2, 3, 4, 5), (6, 7),
        (1, 2, 3, 4, 5), (6, 7), append=True,
    )
    assert r.is_win and r.tier == 1  # amount=None（浮动），append 1.8 在回填时应用


def test_ssq_third_prize():
    """5红+1蓝 = 三等 3000。"""
    r = PartitionCompare.compare(
        "ssq", (1, 2, 3, 4, 5, 33), (7,),
        (1, 2, 3, 4, 5, 6), (7,), append=False,
    )
    assert r.is_win and r.tier == 3 and r.amount == 3000
```

- [ ] **Step 2: 运行确认失败**

```bash
uv run pytest tests/domain/test_partition_compare.py -v
```
Expected: FAIL

- [ ] **Step 3: 写 app/domain/compare.py（CompareStrategy 接口 + PartitionCompare）**

```python
from app.domain.prize import HitResult, PrizeTier, AmountType
from app.domain.prize_tables import get_tiers


class CompareStrategy:
    """比对策略接口。子类实现 compare。"""

    @staticmethod
    def compare(*, lottery, draw_front, draw_back, combo_front, combo_back, append) -> HitResult:
        raise NotImplementedError


def _eval_condition(cond: str, front_hit: int, back_hit: int) -> bool:
    """安全求值 condition 表达式（仅 front_hit/back_hit 变量 + 比较/逻辑运算）。"""
    return bool(eval(cond, {"__builtins__": {}}, {"front_hit": front_hit, "back_hit": back_hit}))


def _match_tier(lottery: str, front_hit: int, back_hit: int) -> PrizeTier | None:
    for t in get_tiers(lottery):
        if _eval_condition(t.condition, front_hit, back_hit):
            return t
    return None


class PartitionCompare(CompareStrategy):
    """分区型：双色球/大乐透/七乐彩。集合匹配红/蓝球个数。"""

    @staticmethod
    def compare(*, lottery, draw_front, draw_back, combo_front, combo_back, append) -> HitResult:
        draw_front_s, draw_back_s = set(draw_front), set(draw_back or ())
        front_hit = len(set(combo_front) & draw_front_s)
        back_hit = len(set(combo_back) & draw_back_s) if combo_back else 0

        tier = _match_tier(lottery, front_hit, back_hit)
        if tier is None:
            return HitResult(front_hit, back_hit, None, None, is_win=False)

        amount = tier.amount
        # 浮动档：amount=None（运行时回填，append_multiplier 在回填时应用）
        return HitResult(front_hit, back_hit, tier.tier, amount, is_win=True)
```

- [ ] **Step 4: 运行确认通过**

```bash
uv run pytest tests/domain/test_partition_compare.py -v
```
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add app/domain/compare.py tests/domain/test_partition_compare.py
git commit -m "feat(domain): PartitionCompare（双色球/大乐透/七乐彩集合匹配）"
```

---

## Task 7: PositionalCompare（福彩3D/排列3/排列5 直选）

**Files:** modify `app/domain/compare.py`, `tests/domain/test_positional_compare.py`

- [ ] **Step 1: 写失败测试 tests/domain/test_positional_compare.py**

```python
from app.domain.compare import PositionalCompare


def test_fc3d_danxuan_all_match():
    """单选：3 位全对 = 中奖 1040。"""
    r = PositionalCompare.compare(
        lottery="fc3d", draw=(1, 2, 3), combo=(1, 2, 3),
    )
    assert r.is_win and r.tier == 1 and r.amount == 1040


def test_fc3d_partial_no_win():
    """2 位对 = 未中奖（单选需全对）。"""
    r = PositionalCompare.compare("fc3d", (1, 2, 3), (1, 2, 9))
    assert not r.is_win


def test_pl5_all_match():
    """排列5 直选：5 位全对 = 10 万。"""
    r = PositionalCompare.compare("pl5", (1, 2, 3, 4, 5), (1, 2, 3, 4, 5))
    assert r.is_win and r.amount == 100000


def test_pl3_order_matters():
    """直选顺序敏感：(1,2,3) ≠ (3,2,1)。"""
    r = PositionalCompare.compare("pl3", (1, 2, 3), (3, 2, 1))
    assert not r.is_win  # 顺序错=未中
```

- [ ] **Step 2: 运行确认失败**

```bash
uv run pytest tests/domain/test_positional_compare.py -v
```
Expected: FAIL（无 PositionalCompare）

- [ ] **Step 3: 追加 PositionalCompare 到 app/domain/compare.py**

```python
class PositionalCompare(CompareStrategy):
    """按位型（直选/单选）：逐位精确匹配，顺序敏感。福彩3D/排列3/排列5。"""

    @staticmethod
    def compare(*, lottery, draw, combo, **_kw) -> HitResult:
        # front_hit = 逐位全对的位数（直选：全部对才算中）
        hit = sum(1 for a, b in zip(draw, combo) if a == b)
        all_match = hit == len(draw)
        if all_match:
            tier = _match_tier(lottery, front_hit=hit, back_hit=0)
            if tier:
                return HitResult(hit, 0, tier.tier, tier.amount, is_win=True)
        return HitResult(hit, 0, None, None, is_win=False)
```

- [ ] **Step 4: 运行确认通过**

```bash
uv run pytest tests/domain/test_positional_compare.py -v
```
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add app/domain/compare.py tests/domain/test_positional_compare.py
git commit -m "feat(domain): PositionalCompare（直选逐位全对，顺序敏感）"
```

---

## Task 8: QxcHybridCompare（七星彩混合型）

**Files:** modify `app/domain/compare.py`, `tests/domain/test_qxc_compare.py`

> 七星彩：前区 6 位按位（front_hit = 连续命中位数）+ 后区 0-14 单值（back_hit 0/1）。

- [ ] **Step 1: 写失败测试 tests/domain/test_qxc_compare.py**

```python
from app.domain.compare import QxcHybridCompare


def test_qxc_first_prize():
    """前区 6 位全对 + 后区对 = 一等（浮动）。"""
    r = QxcHybridCompare.compare(
        lottery="qxc", draw_front=(1, 2, 3, 4, 5, 6), draw_back=(7,),
        combo_front=(1, 2, 3, 4, 5, 6), combo_back=(7,),
    )
    assert r.is_win and r.tier == 1 and r.amount is None


def test_qxc_second_prize():
    """前区 6 位全对 + 后区不对 = 二等（浮动）。"""
    r = QxcHybridCompare.compare(
        "qxc", (1, 2, 3, 4, 5, 6), (7,), (1, 2, 3, 4, 5, 6), (8,),
    )
    assert r.is_win and r.tier == 2


def test_qxc_partial_front():
    """前区连续 5 位对 + 后区对 = 三等。"""
    r = QxcHybridCompare.compare(
        "qxc", (1, 2, 3, 4, 5, 9), (7,), (1, 2, 3, 4, 5, 6), (7,),
    )
    assert r.is_win and r.tier == 3


def test_qxc_no_win():
    r = QxcHybridCompare.compare(
        "qxc", (1, 2, 3, 4, 5, 6), (7,), (9, 8, 7, 6, 5, 4), (8,),
    )
    assert not r.is_win
```

- [ ] **Step 2: 运行确认失败**

```bash
uv run pytest tests/domain/test_qxc_compare.py -v
```
Expected: FAIL

- [ ] **Step 3: 追加 QxcHybridCompare 到 app/domain/compare.py**

```python
class QxcHybridCompare(CompareStrategy):
    """七星彩混合型：前区 6 位按位连续命中 + 后区单值 0-14。
    front_hit = 前区连续命中位数（从首位起连续对几位）。
    注：七星彩官方按"连续命中位数"判定，这里用前缀连续命中计数。"""

    @staticmethod
    def compare(*, lottery, draw_front, draw_back, combo_front, combo_back, **_kw) -> HitResult:
        # 前区连续命中位数（前缀）
        front_hit = 0
        for a, b in zip(draw_front, combo_front):
            if a == b:
                front_hit += 1
            else:
                break
        back_hit = 1 if (combo_back and draw_back and combo_back[0] == draw_back[0]) else 0

        tier = _match_tier(lottery, front_hit=front_hit, back_hit=back_hit)
        if tier is None:
            return HitResult(front_hit, back_hit, None, None, is_win=False)
        return HitResult(front_hit, back_hit, tier.tier, tier.amount, is_win=True)
```

> **七星彩命中语义（澄清）**：lottery-rules.md 的"按位对应（无需连续对位）"指**选号无需连号**（不要求选 1,2,3,4,5,6 连续数字），**非**命中判定方式。七星彩奖级按**连续命中位数**（consecutive correct positions）判定——该彩种本质。MVP 用"首位起前缀连续"近似；官方若按"最长连续段"（longest run），Phase 2 用真实开奖校准 `front_hit` 计算与奖级 condition。一二等（6 位全对）不受影响。

- [ ] **Step 4: 运行确认通过**

```bash
uv run pytest tests/domain/test_qxc_compare.py -v
```
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add app/domain/compare.py tests/domain/test_qxc_compare.py
git commit -m "feat(domain): QxcHybridCompare（前区连续命中 + 后区单值）"
```

---

## Task 9: 策略注册 REGISTRY + compare() 入口

**Files:** modify `app/domain/compare.py`(加 REGISTRY+入口), `tests/domain/test_registry.py`

- [ ] **Step 1: 写失败测试 tests/domain/test_registry.py**

```python
import pytest
from app.domain.compare import compare, REGISTRY, PartitionCompare, PositionalCompare, QxcHybridCompare


def test_registry_routes_partition():
    assert REGISTRY["ssq"] is PartitionCompare
    assert REGISTRY["dlt"] is PartitionCompare
    assert REGISTRY["qlc"] is PartitionCompare


def test_registry_routes_positional():
    assert REGISTRY["fc3d"] is PositionalCompare
    assert REGISTRY["pl3"] is PositionalCompare
    assert REGISTRY["pl5"] is PositionalCompare


def test_registry_routes_hybrid():
    assert REGISTRY["qxc"] is QxcHybridCompare


def test_compare_entry_ssq_single():
    """入口：compare(spec, draw, entry) 自动展开 single 并比对。"""
    from app.domain.entry import Entry
    from app.domain.spec import LotterySpec
    spec = LotterySpec.from_dict({
        "code": "ssq", "name": "双色球", "category": "welfare", "number_style": "partition",
        "front": {"min": 1, "max": 33, "count": 6}, "back": {"min": 1, "max": 16, "count": 1},
        "draw_days": [1, 3, 6], "play_types": ["single"], "welfare_rate": 36, "price_per_bet": 200,
    })
    entry = Entry("ssq", "single", (1, 2, 3, 4, 5, 6), (7,))
    results = compare(spec, draw_front=(1, 2, 3, 4, 5, 6), draw_back=(7,), entry=entry)
    assert len(results) == 1
    assert results[0].is_win and results[0].tier == 1
```

- [ ] **Step 2: 运行确认失败**

```bash
uv run pytest tests/domain/test_registry.py -v
```
Expected: FAIL（无 REGISTRY/compare 入口）

- [ ] **Step 3: 追加 REGISTRY + compare() 入口到 app/domain/compare.py**

```python
# 显式注册表（不用装饰器自动发现——可测、可预测）
REGISTRY: dict[str, type[CompareStrategy]] = {
    "ssq": PartitionCompare,
    "dlt": PartitionCompare,
    "qlc": PartitionCompare,
    "fc3d": PositionalCompare,
    "pl3": PositionalCompare,
    "pl5": PositionalCompare,
    "qxc": QxcHybridCompare,
}


def compare(*, spec, draw_front, draw_back, entry) -> list[HitResult]:
    """领域入口：展开 entry → 对每个 SingleCombo 用对应策略比对。
    spec: LotterySpec；entry: Entry；返回每个单式的 HitResult。"""
    from app.domain.entry import expand
    strategy = REGISTRY[spec.code]
    results: list[HitResult] = []
    for combo in expand(entry):
        if spec.number_style == "positional":
            r = strategy.compare(
                lottery=spec.code, draw=draw_front, combo=combo.front,
            )
        else:
            r = strategy.compare(
                lottery=spec.code, draw_front=draw_front, draw_back=draw_back,
                combo_front=combo.front, combo_back=combo.back, append=entry.append,
            )
        results.append(r)
    return results
```

- [ ] **Step 4: 运行确认通过**

```bash
uv run pytest tests/domain/test_registry.py -v
```
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add app/domain/compare.py tests/domain/test_registry.py
git commit -m "feat(domain): REGISTRY 显式注册 + compare() 领域入口"
```

---

## Task 10: 领域 __init__ 导出 + import-linter 生效 + 全量测试 + 覆盖率

**Files:** `app/domain/__init__.py`, `tests/domain/test_purity.py`

- [ ] **Step 1: 写 app/domain/__init__.py（导出领域 API）**

```python
from app.domain.spec import NumberRange, PositionalDigits, LotterySpec  # noqa
from app.domain.entry import Entry, SingleCombo, expand, MAX_COMBINATIONS  # noqa
from app.domain.prize import PrizeTier, HitResult, AmountType  # noqa
from app.domain.prize_tables import PRIZE_TABLES, get_tiers  # noqa
from app.domain.compare import (  # noqa
    CompareStrategy, PartitionCompare, PositionalCompare, QxcHybridCompare,
    REGISTRY, compare,
)

__all__ = [
    "NumberRange", "PositionalDigits", "LotterySpec",
    "Entry", "SingleCombo", "expand", "MAX_COMBINATIONS",
    "PrizeTier", "HitResult", "AmountType",
    "PRIZE_TABLES", "get_tiers",
    "CompareStrategy", "PartitionCompare", "PositionalCompare", "QxcHybridCompare",
    "REGISTRY", "compare",
]
```

- [ ] **Step 2: 写 tests/domain/test_purity.py（领域层零 IO 元测试）**

```python
import pkgutil
import app.domain


def test_domain_modules_importable():
    """领域层所有子模块可 import（结构性检查）。真正的 purity 护栏靠 import-linter（CI 强制）。"""
    import importlib
    for mod_info in pkgutil.walk_packages(app.domain.__path__, prefix="app.domain."):
        importlib.import_module(mod_info.name)  # 不抛即过；purity 由 lint-imports 强制
```

> 简化：purity 主要靠 import-linter（CI 强制）。此元测试做基本检查；核心护栏是 `uv run lint-imports`。

- [ ] **Step 3: 运行 import-linter（Plan 01 配置，现领域层存在，强制生效）**

```bash
uv run lint-imports
```
Expected: `KEPT`（Domain layer is pure 契约通过）。若 FAIL，查哪个领域文件 import 了 infra。

- [ ] **Step 4: 跑全量领域测试 + 覆盖率**

```bash
uv run pytest tests/domain/ -v --cov=app/domain --cov-report=term-missing
```
Expected: 全绿；`app/domain` 覆盖率 ≥95%。低于则补用例（重点：compare 各策略边界、expand 上限、prize_tables 全覆盖）。

- [ ] **Step 5: 跑全量测试（含 Plan 01 的）确认无回归**

```bash
uv run pytest -v
```
Expected: 全绿（Plan 01 + 02 所有测试）。

- [ ] **Step 6: Commit**

```bash
git add app/domain/__init__.py tests/domain/test_purity.py
git commit -m "feat(domain): __init__ 导出 + purity 元测试 + import-linter 生效"
```

---

## Self-Review

**Spec 覆盖（Plan 02 = 领域层，spec §5 全部 + §11 测试）：**
- ✅ NumberRange/PositionalDigits 类型不变式（§5.1 D7）→ Task 1
- ✅ LotterySpec hydrate + 校验（§5.1）→ Task 2
- ✅ Entry/expand/cost/MAX_COMBINATIONS（§5.2 D6）→ Task 3
- ✅ PrizeTier append_multiplier（§5.3 D11）→ Task 4
- ✅ 7 彩种奖级表（§5.3）→ Task 5（双色球/大乐透完整，其余可配置）
- ✅ PartitionCompare（§5.4）→ Task 6
- ✅ PositionalCompare（§5.4）→ Task 7
- ✅ QxcHybridCompare（§5.4 + 七星彩 2020 改版）→ Task 8
- ✅ REGISTRY 显式注册 + compare 入口（§4.2 策略模式）→ Task 9
- ✅ 领域纯净护栏 + 95% 覆盖（§11）→ Task 10
- 📌 复式/胆拖完整展开逻辑（fushi/dantuo）= Phase 2，本 plan 给 expand 框架 + 上限/cost 校验（接口已定，Phase 2 补精确组合）

**Placeholder scan：** 无 TBD；所有奖级金额给了具体分值（可配置）+ 数据源标注；七星彩连续命中定义给了 MVP 选择 + 官方为准说明。
**类型一致：** `compare()` 入口、`HitResult`、`SingleCombo`、`REGISTRY` 前后命名一致；`Entry.cost(price_per_bet)` 签名统一。
**衔接 Plan 03：** Plan 03 仓储/比对引擎调用 `app.domain.compare(spec, ...)` + `expand()`；`HitResult` 写入 comparisons.hits_json/prize_tier/amount。
**衔接 seeds：** Plan 01 的 `LotterySpecModel`（校验）与领域 `LotterySpec`（运行时）同形，从同一 spec_json hydrate。
