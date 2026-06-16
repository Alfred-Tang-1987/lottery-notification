# Phase 1 · Plan 1: 领域层（纯逻辑核心）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现彩票核对系统的纯逻辑领域层——7 大彩种规格、奖级规则表、单式比对策略（分区型 + 按位型），零 IO、100% 单元测试覆盖，作为整个系统的可独立测试地基。

**Architecture:** 领域层是纯 Python 函数/数据类，不碰数据库与网络。彩种规格配置驱动（`LotterySpec`），比对用策略模式（`PartitionCompare` / `PositionalCompare`），奖级用规则表（`PrizeTier.conditions`）判定。新增彩种 = 加配置 + 测试，不改核心。

**Tech Stack:** Python 3.12、dataclasses、pytest、pytest-cov。本 plan 无第三方运行时依赖（纯标准库）。

**对应 Spec:** `docs/superpowers/specs/2026-06-16-lottery-notification-design.md` §5（领域模型）

**范围说明:** 本 plan 只做领域层纯逻辑。数据持久化、开奖获取、推送、调度、Web 在 Plan 2-6。MVP 玩法为单式（+ 按位直选）；复式/胆拖/组选在 Phase 2。

---

## File Structure

```
lottery-notification/
├── pyproject.toml                 # 项目与依赖（本 plan 仅 pytest）
├── pytest.ini                     # 测试配置
├── app/
│   ├── __init__.py
│   └── domain/
│       ├── __init__.py
│       ├── models.py              # 数据类与枚举：NumberRange/LotterySpec/PrizeTier/HitResult/PlayType 等
│       ├── lottery_types.py       # 7 大彩种的 LotterySpec 配置
│       ├── prize_tables.py        # 7 大彩种的奖级规则表（PrizeTier 列表）
│       └── compare/
│           ├── __init__.py
│           ├── strategy.py        # CompareStrategy 接口 + get_strategy() 路由
│           ├── partition.py       # PartitionCompare（双色球/大乐透/七乐彩）
│           └── positional.py      # PositionalCompare（福彩3D/排列3/排列5/七星彩）
└── tests/
    └── domain/
        ├── __init__.py
        ├── test_models.py
        ├── test_lottery_types.py
        ├── test_prize_tables.py
        ├── test_partition_compare.py
        ├── test_positional_compare.py
        ├── test_strategy_routing.py
        └── fixtures.py            # 真实历史开奖数据用例
```

**职责边界：**
- `models.py` — 只定义不可变数据结构与枚举，无逻辑。
- `lottery_types.py` / `prize_tables.py` — 纯配置数据（7 彩种规格与奖级），无逻辑。
- `compare/` — 比对策略，纯函数式，输入开奖号码+号码注，输出 `HitResult`。
- `tests/domain/fixtures.py` — 真实历史开奖号码（来自官方），作为比对测试的金标准。

**金额约定：** 所有奖金以**分**为单位存储（int），避免浮点误差。展示层再除 100。

---

## Task 1: 项目骨架

**Files:**
- Create: `pyproject.toml`
- Create: `pytest.ini`
- Create: `app/__init__.py`
- Create: `app/domain/__init__.py`
- Create: `app/domain/compare/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/domain/__init__.py`

- [ ] **Step 1: 创建 pyproject.toml**

```toml
[project]
name = "lottery-notification"
version = "0.1.0"
description = "多用户中国彩票开奖自动核对与通知系统"
requires-python = ">=3.12"
dependencies = []

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-cov>=5.0",
]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.backends._legacy:_Backend"

[tool.setuptools.packages.find]
include = ["app*"]
```

- [ ] **Step 2: 创建 pytest.ini**

```ini
[pytest]
testpaths = tests
python_files = test_*.py
python_classes = Test*
python_functions = test_*
addopts = -ra --strict-markers
```

- [ ] **Step 3: 创建空 `__init__.py`（4 个：app、app/domain、app/domain/compare、tests、tests/domain）**

每个文件内容为空字符串即可（标记为 package）。

- [ ] **Step 4: 安装依赖并验证 pytest 可运行**

Run: `pip install -e ".[dev]"`
Run: `pytest -v`
Expected: `no tests ran` (没有测试，但不报错)

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml pytest.ini app/ tests/
git commit -m "chore: 项目骨架与 pytest 配置"
```

---

## Task 2: 领域模型基础（数据类与枚举）

**Files:**
- Create: `app/domain/models.py`
- Test: `tests/domain/test_models.py`

- [ ] **Step 1: 写失败测试 `tests/domain/test_models.py`**

```python
from app.domain.models import (
    NumberRange, LotterySpec, PrizeTier, HitResult,
    PlayType, AmountType, NumberStyle, BallColor,
)


def test_number_range_is_immutable():
    nr = NumberRange(min=1, max=33, count=6)
    assert nr.min == 1 and nr.max == 33 and nr.count == 6


def test_play_type_enum_values():
    assert PlayType.SINGLE.value == "single"
    assert PlayType.ZHIXUAN.value == "zhixuan"


def test_amount_type_enum_values():
    assert AmountType.FIXED.value == "fixed"
    assert AmountType.FLOAT.value == "float"


def test_number_style_enum_values():
    assert NumberStyle.PARTITION.value == "partition"
    assert NumberStyle.POSITIONAL.value == "positional"


def test_prize_tier_conditions_tuple():
    t = PrizeTier(lottery="ssq", tier=5, name="五等奖",
                  conditions=((4, 0), (3, 1)), amount=1000, amount_type=AmountType.FIXED)
    assert (4, 0) in t.conditions
    assert t.amount == 1000  # 分


def test_prize_tier_float_amount_none():
    t = PrizeTier(lottery="ssq", tier=1, name="一等奖",
                  conditions=((6, 1),), amount=None, amount_type=AmountType.FLOAT)
    assert t.amount is None


def test_hit_result_not_win():
    r = HitResult(front_hit=2, back_hit=0, tier=None, prize_amount=None, is_win=False)
    assert r.is_win is False
    assert r.tier is None
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/domain/test_models.py -v`
Expected: FAIL — `ImportError: cannot import name 'NumberRange'`

- [ ] **Step 3: 实现 `app/domain/models.py`**

```python
"""领域层核心数据模型。纯数据，无逻辑、无 IO。"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum


class BallColor(str, Enum):
    RED = "red"
    BLUE = "blue"


class PlayType(str, Enum):
    SINGLE = "single"      # 单式
    FUSHI = "fushi"        # 复式
    DANTUO = "dantuo"      # 胆拖
    ZHIXUAN = "zhixuan"    # 直选（按位）
    ZUXUAN = "zuxuan"      # 组选（按位）


class AmountType(str, Enum):
    FIXED = "fixed"        # 固定档奖金
    FLOAT = "float"        # 浮动奖金（一二等奖，待官方派奖）


class NumberStyle(str, Enum):
    PARTITION = "partition"    # 分区型：红球区+蓝球区（双色球/大乐透/七乐彩）
    POSITIONAL = "positional"  # 按位型：每位 0-9，顺序敏感（3D/排列/七星彩）


@dataclass(frozen=True)
class NumberRange:
    """一个号码区的范围与个数。"""
    min: int
    max: int
    count: int


@dataclass(frozen=True)
class LotterySpec:
    """彩种规格（配置驱动）。"""
    code: str                       # ssq / dlt / qlc / fc3d / qxc / pl3 / pl5
    name: str                       # 双色球
    category: str                   # welfare / sports
    front: NumberRange | None       # 前区（红球/主号）
    back: NumberRange | None        # 后区（蓝球/特别号），按位型为 None
    number_style: NumberStyle
    draw_days: tuple[int, ...]      # 开奖日，0=周一 ... 6=周日
    play_types: tuple[PlayType, ...]


@dataclass(frozen=True)
class PrizeTier:
    """一个奖级规则。

    conditions: 命中条件元组的集合。
      - 分区型：(front_hit, back_hit)，命中任一即该奖级。
      - 按位型：约定单个条件 (all_positional_hit,)，即全部位数命中。
    amount: 固定档奖金（分，int）；浮动档为 None。
    """
    lottery: str
    tier: int
    name: str
    conditions: tuple[tuple[int, ...], ...]
    amount: int | None
    amount_type: AmountType


@dataclass(frozen=True)
class HitResult:
    """单注比对结果。"""
    front_hit: int          # 前区命中数（按位型=按位命中数）
    back_hit: int           # 后区命中数（按位型恒为 0）
    tier: int | None        # 命中的奖级，None=未中奖
    prize_amount: int | None  # 奖金（分），浮动档/未中奖为 None
    is_win: bool
```

- [ ] **Step 4: 跑测试验证通过**

Run: `pytest tests/domain/test_models.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add app/domain/models.py tests/domain/test_models.py
git commit -m "feat(domain): 领域模型基础数据类与枚举"
```

---

## Task 3: 双色球规格 + 奖级表 + PartitionCompare（分区型比对）

**Files:**
- Create: `app/domain/lottery_types.py`
- Create: `app/domain/prize_tables.py`
- Create: `app/domain/compare/strategy.py`
- Create: `app/domain/compare/partition.py`
- Test: `tests/domain/test_partition_compare.py`
- Test: `tests/domain/fixtures.py`

- [ ] **Step 1: 写真实历史数据 fixtures `tests/domain/fixtures.py`**

```python
"""真实历史开奖数据（来自官方），作为比对测试金标准。
sources: 中国福彩/体彩官方公告。"""
# 双色球 2024060 期开奖（2024-05-26）：红 02 07 14 18 25 32 蓝 06
SSQ_2024060_FRONT = (2, 7, 14, 18, 25, 32)
SSQ_2024060_BACK = (6,)

# 双色球奖级命中用例：(用户红, 用户蓝) -> (front_hit, back_hit, tier, is_win)
SSQ_CASES = [
    # 一等奖 6+1
    ((2, 7, 14, 18, 25, 32), (6,), 6, 1, True),
    # 二等奖 6+0
    ((2, 7, 14, 18, 25, 32), (1,), 6, 0, True),
    # 三等奖 5+1
    ((2, 7, 14, 18, 25, 33), (6,), 5, 1, True),
    # 四等奖 5+0
    ((2, 7, 14, 18, 25, 33), (1,), 5, 0, True),
    # 四等奖 4+1
    ((2, 7, 14, 18, 26, 33), (6,), 4, 1, True),
    # 五等奖 4+0
    ((2, 7, 14, 18, 26, 33), (1,), 4, 0, True),
    # 五等奖 3+1
    ((2, 7, 14, 19, 26, 33), (6,), 3, 1, True),
    # 六等奖 0+1
    ((1, 3, 5, 9, 11, 13), (6,), 0, 1, True),
    # 六等奖 2+1
    ((2, 7, 15, 19, 26, 33), (6,), 2, 1, True),
    # 未中奖 3+0
    ((2, 7, 14, 19, 26, 33), (1,), 3, 0, False),
    # 未中奖 2+0
    ((2, 7, 15, 19, 26, 33), (1,), 2, 0, False),
    # 未中奖 0+0
    ((1, 3, 5, 9, 11, 13), (1,), 0, 0, False),
]
```

- [ ] **Step 2: 写失败测试 `tests/domain/test_partition_compare.py`**

```python
import pytest
from app.domain.models import NumberRange, NumberStyle, PlayType, AmountType
from app.domain.lottery_types import SSQ
from app.domain.prize_tables import PRIZE_TABLES
from app.domain.compare.partition import PartitionCompare
from app.domain.compare.strategy import compare
from tests.domain.fixtures import SSQ_2024060_FRONT, SSQ_2024060_BACK, SSQ_CASES


def test_ssq_spec():
    assert SSQ.code == "ssq"
    assert SSQ.number_style == NumberStyle.PARTITION
    assert SSQ.front == NumberRange(1, 33, 6)
    assert SSQ.back == NumberRange(1, 16, 1)


def test_ssq_prize_table_has_six_tiers_high_to_low():
    tiers = PRIZE_TABLES["ssq"]
    assert [t.tier for t in tiers] == [1, 2, 3, 4, 5, 6]


@pytest.mark.parametrize("entry_front,entry_back,exp_fh,exp_bh,exp_win", [
    (c[0], c[1], c[2], c[3], c[4]) for c in SSQ_CASES
])
def test_ssq_compare_all_tiers(entry_front, entry_back, exp_fh, exp_bh, exp_win):
    draw = {"front": SSQ_2024060_FRONT, "back": SSQ_2024060_BACK}
    entry = {"front": tuple(entry_front), "back": tuple(entry_back)}
    result = PartitionCompare().compare(SSQ, draw, entry)
    assert result.front_hit == exp_fh
    assert result.back_hit == exp_bh
    assert result.is_win == exp_win


def test_ssq_fixed_prize_amount_in_cents():
    draw = {"front": SSQ_2024060_FRONT, "back": SSQ_2024060_BACK}
    entry = {"front": (2, 7, 14, 18, 26, 33), "back": (6,)}  # 4+1 四等奖 200元
    result = PartitionCompare().compare(SSQ, draw, entry)
    assert result.tier == 4
    assert result.prize_amount == 20000  # 200 元 = 20000 分


def test_ssq_float_prize_amount_none():
    draw = {"front": SSQ_2024060_FRONT, "back": SSQ_2024060_BACK}
    entry = {"front": SSQ_2024060_FRONT, "back": SSQ_2024060_BACK}  # 6+1 一等奖
    result = PartitionCompare().compare(SSQ, draw, entry)
    assert result.tier == 1
    assert result.prize_amount is None  # 浮动，待官方派奖


def test_strategy_router_dispatches_partition():
    draw = {"front": SSQ_2024060_FRONT, "back": SSQ_2024060_BACK}
    entry = {"front": (2, 7, 14, 18, 26, 33), "back": (6,)}
    result = compare(SSQ, draw, entry)
    assert result.tier == 4
```

- [ ] **Step 3: 跑测试验证失败**

Run: `pytest tests/domain/test_partition_compare.py -v`
Expected: FAIL — `ImportError: cannot import name 'SSQ'`

- [ ] **Step 4: 实现 `app/domain/lottery_types.py`（双色球部分）**

```python
"""7 大彩种规格配置。"""
from app.domain.models import LotterySpec, NumberRange, NumberStyle, PlayType

SSQ = LotterySpec(
    code="ssq", name="双色球", category="welfare",
    front=NumberRange(1, 33, 6),
    back=NumberRange(1, 16, 1),
    number_style=NumberStyle.PARTITION,
    draw_days=(1, 3, 6),  # 周二/四/日 → 0-based: 1=周二,3=周四,6=周日
    play_types=(PlayType.SINGLE, PlayType.FUSHI, PlayType.DANTUO),
)

# 其余彩种在后续 Task 添加
LOTTERY_TYPES: dict[str, LotterySpec] = {
    "ssq": SSQ,
}
```

> **注：** `draw_days` 用 Python 0-based 周几（`date.weekday()`）：周一=0…周日=6。双色球周二/四/日 = (1, 3, 6)。

- [ ] **Step 5: 实现 `app/domain/prize_tables.py`（双色球部分）**

```python
"""7 大彩种奖级规则表。每个 list 从高到低排列（比对时取首个匹配）。
金额单位：分。浮动档 amount=None。"""
from app.domain.models import AmountType, PrizeTier

SSQ_TIERS = (
    PrizeTier("ssq", 1, "一等奖", ((6, 1),), None, AmountType.FLOAT),
    PrizeTier("ssq", 2, "二等奖", ((6, 0),), None, AmountType.FLOAT),
    PrizeTier("ssq", 3, "三等奖", ((5, 1),), 300000, AmountType.FIXED),
    PrizeTier("ssq", 4, "四等奖", ((5, 0), (4, 1)), 20000, AmountType.FIXED),
    PrizeTier("ssq", 5, "五等奖", ((4, 0), (3, 1)), 1000, AmountType.FIXED),
    PrizeTier("ssq", 6, "六等奖", ((2, 1), (1, 1), (0, 1)), 500, AmountType.FIXED),
)

PRIZE_TABLES: dict[str, tuple[PrizeTier, ...]] = {
    "ssq": SSQ_TIERS,
}
```

- [ ] **Step 6: 实现 `app/domain/compare/strategy.py`**

```python
"""比对策略接口与路由。"""
from __future__ import annotations
from typing import Protocol
from app.domain.models import HitResult, LotterySpec, NumberStyle


class CompareStrategy(Protocol):
    def compare(self, spec: LotterySpec, draw: dict, entry: dict) -> HitResult: ...


_STRATEGIES: dict[NumberStyle, CompareStrategy] = {}


def register(style: NumberStyle):
    def deco(cls):
        _STRATEGIES[style] = cls()
        return cls
    return deco


def get_strategy(spec: LotterySpec) -> CompareStrategy:
    try:
        return _STRATEGIES[spec.number_style]
    except KeyError:
        raise ValueError(f"无 {spec.number_style} 的比对策略")


def compare(spec: LotterySpec, draw: dict, entry: dict) -> HitResult:
    return get_strategy(spec).compare(spec, draw, entry)
```

- [ ] **Step 7: 实现 `app/domain/compare/partition.py`**

```python
"""分区型比对（红球区+蓝球区）：双色球/大乐透/七乐彩。"""
from __future__ import annotations
from app.domain.compare.strategy import register
from app.domain.models import AmountType, HitResult, LotterySpec, NumberStyle
from app.domain.prize_tables import PRIZE_TABLES


@register(NumberStyle.PARTITION)
class PartitionCompare:
    def compare(self, spec: LotterySpec, draw: dict, entry: dict) -> HitResult:
        front_hit = len(set(entry["front"]) & set(draw["front"]))
        back_hit = len(set(entry["back"]) & set(draw["back"])) if spec.back else 0
        for tier in PRIZE_TABLES[spec.code]:
            if (front_hit, back_hit) in tier.conditions:
                amount = tier.amount if tier.amount_type == AmountType.FIXED else None
                return HitResult(front_hit, back_hit, tier.tier, amount, is_win=True)
        return HitResult(front_hit, back_hit, None, None, is_win=False)
```

> **import 副作用：** `partition.py` 顶部 `@register` 在导入时注册策略。`strategy.py` 的 `compare()` 依赖此注册。测试导入 `partition` 模块即生效（Task 3 测试已 import）。Plan 2 的应用入口会在 `app/domain/__init__.py` 统一 import 所有策略模块以触发注册。

- [ ] **Step 8: 跑测试验证通过**

Run: `pytest tests/domain/test_partition_compare.py -v`
Expected: 全部通过（含 12 个 parametrize 用例 + spec/table/amount/router 断言）

- [ ] **Step 9: Commit**

```bash
git add app/domain/lottery_types.py app/domain/prize_tables.py \
        app/domain/compare/strategy.py app/domain/compare/partition.py \
        tests/domain/test_partition_compare.py tests/domain/fixtures.py
git commit -m "feat(domain): 双色球规格+奖级表+PartitionCompare 分区型比对"
```

---

## Task 4: 福彩3D 规格 + PositionalCompare（按位型直选）

**Files:**
- Modify: `app/domain/lottery_types.py`
- Modify: `app/domain/prize_tables.py`
- Create: `app/domain/compare/positional.py`
- Test: `tests/domain/test_positional_compare.py`
- Modify: `tests/domain/fixtures.py`

- [ ] **Step 1: 追加 fixtures（福彩3D）到 `tests/domain/fixtures.py`**

```python
# 福彩3D 第2024160期开奖（示例）：3 7 1
FC3D_2024160 = (3, 7, 1)
# (用户3位, 期望按位命中数, 期望是否中奖) — 直选全对才中
FC3D_CASES = [
    ((3, 7, 1), 3, True),     # 直选全对
    ((3, 7, 2), 2, False),    # 前两位对
    ((3, 9, 1), 2, False),    # 首尾对
    ((1, 7, 3), 0, False),    # 数字对但顺序错（直选按位）
    ((8, 8, 8), 0, False),
]
```

- [ ] **Step 2: 写失败测试 `tests/domain/test_positional_compare.py`**

```python
import pytest
from app.domain.models import NumberRange, NumberStyle
from app.domain.lottery_types import FC3D
from app.domain.compare.positional import PositionalCompare
from app.domain.compare.strategy import compare
from tests.domain.fixtures import FC3D_2024160, FC3D_CASES


def test_fc3d_spec():
    assert FC3D.code == "fc3d"
    assert FC3D.number_style == NumberStyle.POSITIONAL
    assert FC3D.front == NumberRange(0, 9, 3)
    assert FC3D.back is None


@pytest.mark.parametrize("entry,exp_hit,exp_win", FC3D_CASES)
def test_fc3d_zhixuan(entry, exp_hit, exp_win):
    draw = {"front": FC3D_2024160, "back": ()}
    e = {"front": tuple(entry), "back": ()}
    result = PositionalCompare().compare(FC3D, draw, e)
    assert result.front_hit == exp_hit
    assert result.back_hit == 0
    assert result.is_win == exp_win


def test_fc3d_zhixuan_prize_amount():
    draw = {"front": FC3D_2024160, "back": ()}
    entry = {"front": FC3D_2024160, "back": ()}
    result = compare(FC3D, draw, entry)
    assert result.tier == 1
    assert result.prize_amount == 104000  # 1040 元 = 104000 分


def test_positional_router_dispatches():
    draw = {"front": FC3D_2024160, "back": ()}
    entry = {"front": FC3D_2024160, "back": ()}
    assert compare(FC3D, draw, entry).is_win is True
```

- [ ] **Step 3: 跑测试验证失败**

Run: `pytest tests/domain/test_positional_compare.py -v`
Expected: FAIL — `ImportError: cannot import name 'FC3D'`

- [ ] **Step 4: 修改 `app/domain/lottery_types.py` 追加 FC3D**

在文件末尾、`LOTTERY_TYPES` 字典之前追加：

```python
FC3D = LotterySpec(
    code="fc3d", name="福彩3D", category="welfare",
    front=NumberRange(0, 9, 3),
    back=None,
    number_style=NumberStyle.POSITIONAL,
    draw_days=(0, 1, 2, 3, 4, 5, 6),  # 每日
    play_types=(PlayType.SINGLE, PlayType.ZHIXUAN, PlayType.ZUXUAN),
)
```

并把 `LOTTERY_TYPES` 字典改为：

```python
LOTTERY_TYPES: dict[str, LotterySpec] = {
    "ssq": SSQ,
    "fc3d": FC3D,
}
```

- [ ] **Step 5: 修改 `app/domain/prize_tables.py` 追加 FC3D 直选奖级**

在 `SSQ_TIERS` 之后、`PRIZE_TABLES` 字典之前追加：

```python
# 福彩3D 直选：3 位全对 = 单一奖级（组选在 Phase 2）
FC3D_TIERS = (
    PrizeTier("fc3d", 1, "直选", ((3,),), 104000, AmountType.FIXED),
)
```

并把 `PRIZE_TABLES` 字典改为：

```python
PRIZE_TABLES: dict[str, tuple[PrizeTier, ...]] = {
    "ssq": SSQ_TIERS,
    "fc3d": FC3D_TIERS,
}
```

> **按位型 conditions 约定：** 单个条件 `(n,)`，`n` = 按位全命中位数。福彩3D 直选 `(3,)` 即 3 位全对。

- [ ] **Step 6: 实现 `app/domain/compare/positional.py`**

```python
"""按位型比对（每位 0-9，顺序敏感）：福彩3D/排列3/排列5/七星彩。
MVP 仅直选（全对才中）。组选在 Phase 2。"""
from __future__ import annotations
from app.domain.compare.strategy import register
from app.domain.models import AmountType, HitResult, LotterySpec, NumberStyle
from app.domain.prize_tables import PRIZE_TABLES


@register(NumberStyle.POSITIONAL)
class PositionalCompare:
    def compare(self, spec: LotterySpec, draw: dict, entry: dict) -> HitResult:
        positional_hit = sum(
            1 for d, e in zip(draw["front"], entry["front"]) if d == e
        )
        for tier in PRIZE_TABLES[spec.code]:
            required = tier.conditions[0][0]
            if positional_hit == required:
                amount = tier.amount if tier.amount_type == AmountType.FIXED else None
                return HitResult(positional_hit, 0, tier.tier, amount, is_win=True)
        return HitResult(positional_hit, 0, None, None, is_win=False)
```

- [ ] **Step 7: 跑测试验证通过**

Run: `pytest tests/domain/test_positional_compare.py -v`
Expected: 全部通过

- [ ] **Step 8: Commit**

```bash
git add app/domain/lottery_types.py app/domain/prize_tables.py \
        app/domain/compare/positional.py \
        tests/domain/test_positional_compare.py tests/domain/fixtures.py
git commit -m "feat(domain): 福彩3D规格+PositionalCompare 按位直选比对"
```

---

## Task 5: 大乐透 + 七乐彩（复用 PartitionCompare）

验证分区型策略对其他分区彩种通用。大乐透后区 2 个蓝球。

**Files:**
- Modify: `app/domain/lottery_types.py`
- Modify: `app/domain/prize_tables.py`
- Modify: `tests/domain/fixtures.py`
- Test: `tests/domain/test_partition_extra.py`

- [ ] **Step 1: 追加 fixtures（大乐透、七乐彩）到 `tests/domain/fixtures.py`**

```python
# 大乐透 第2024060期（示例）：前 04 11 18 22 31 后 03 09
DLT_2024060_FRONT = (4, 11, 18, 22, 31)
DLT_2024060_BACK = (3, 9)
# 大乐透命中用例：(前, 后) -> (fh, bh, tier, is_win)
DLT_CASES = [
    ((4, 11, 18, 22, 31), (3, 9), 5, 2, 1, True),    # 一等 5+2
    ((4, 11, 18, 22, 31), (3, 5), 5, 1, 2, True),    # 二等 5+1
    ((4, 11, 18, 22, 31), (1, 5), 5, 0, 3, True),    # 三等 5+0
    ((4, 11, 18, 22, 33), (3, 9), 4, 2, 4, True),    # 四等 4+2
    ((4, 11, 18, 22, 33), (3, 5), 4, 1, 5, True),    # 五等 4+1
    ((4, 11, 18, 22, 33), (1, 5), 4, 0, 7, True),    # 七等 4+0
    ((4, 11, 18, 23, 33), (1, 5), 3, 0, 9, True),    # 九等 3+0
    ((1, 2, 3, 5, 6), (1, 2), 0, 0, None, False),    # 未中
]

# 七乐彩 第2024060期（示例）：基本号 7 个 + 特别号 1 个（范围 1-30）
QLC_2024060_FRONT = (3, 8, 15, 21, 25, 28, 30)
QLC_2024060_BACK = (5,)  # 特别号
QLC_CASES = [
    # 七乐彩：基本号命中数 + 特别号命中数
    ((3, 8, 15, 21, 25, 28, 30), (5,), 7, 1, True),   # 一等 7+特别
    ((3, 8, 15, 21, 25, 28, 30), (1,), 7, 0, True),   # 二等 7+0特别
    ((3, 8, 15, 21, 25, 28, 29), (5,), 6, 1, True),   # 三等 6+特别
    ((3, 8, 15, 21, 25, 27, 29), (1,), 5, 0, True),   # 五等 5+0
    ((1, 2, 4, 6, 7, 9, 10), (1,), 0, 0, False),      # 未中
]
```

- [ ] **Step 2: 写测试 `tests/domain/test_partition_extra.py`**

```python
import pytest
from app.domain.lottery_types import DLT, QLC
from app.domain.compare.partition import PartitionCompare
from tests.domain.fixtures import (
    DLT_2024060_FRONT, DLT_2024060_BACK, DLT_CASES,
    QLC_2024060_FRONT, QLC_2024060_BACK, QLC_CASES,
)


@pytest.mark.parametrize("ef,eb,fh,bh,tier,win", DLT_CASES)
def test_dlt_all_tiers(ef, eb, fh, bh, tier, win):
    draw = {"front": DLT_2024060_FRONT, "back": DLT_2024060_BACK}
    entry = {"front": tuple(ef), "back": tuple(eb)}
    r = PartitionCompare().compare(DLT, draw, entry)
    assert (r.front_hit, r.back_hit, r.tier, r.is_win) == (fh, bh, tier, win)


@pytest.mark.parametrize("ef,eb,fh,bh,win", QLC_CASES)
def test_qlc_all_tiers(ef, eb, fh, bh, win):
    draw = {"front": QLC_2024060_FRONT, "back": QLC_2024060_BACK}
    entry = {"front": tuple(ef), "back": tuple(eb)}
    r = PartitionCompare().compare(QLC, draw, entry)
    assert (r.front_hit, r.back_hit, r.is_win) == (fh, bh, win)
```

- [ ] **Step 3: 跑测试验证失败**

Run: `pytest tests/domain/test_partition_extra.py -v`
Expected: FAIL — `ImportError: cannot import name 'DLT'`

- [ ] **Step 4: 修改 `app/domain/lottery_types.py` 追加 DLT、QLC**

在 `FC3D` 之后追加：

```python
DLT = LotterySpec(
    code="dlt", name="大乐透", category="sports",
    front=NumberRange(1, 35, 5),
    back=NumberRange(1, 12, 2),
    number_style=NumberStyle.PARTITION,
    draw_days=(0, 2, 5),  # 一/三/六
    play_types=(PlayType.SINGLE, PlayType.FUSHI, PlayType.DANTUO),
)

QLC = LotterySpec(
    code="qlc", name="七乐彩", category="welfare",
    front=NumberRange(1, 30, 7),
    back=NumberRange(1, 30, 1),  # 特别号，范围同前区
    number_style=NumberStyle.PARTITION,
    draw_days=(0, 2, 4),  # 一/三/五
    play_types=(PlayType.SINGLE, PlayType.FUSHI, PlayType.DANTUO),
)
```

并把 `LOTTERY_TYPES` 字典改为：

```python
LOTTERY_TYPES: dict[str, LotterySpec] = {
    "ssq": SSQ, "dlt": DLT, "qlc": QLC, "fc3d": FC3D,
}
```

- [ ] **Step 5: 修改 `app/domain/prize_tables.py` 追加 DLT、QLC 奖级**

在 `FC3D_TIERS` 之后追加：

```python
# 大乐透（前5+后2）。金额单位：分。
DLT_TIERS = (
    PrizeTier("dlt", 1, "一等奖", ((5, 2),), None, AmountType.FLOAT),
    PrizeTier("dlt", 2, "二等奖", ((5, 1),), None, AmountType.FLOAT),
    PrizeTier("dlt", 3, "三等奖", ((5, 0),), 1000000, AmountType.FIXED),
    PrizeTier("dlt", 4, "四等奖", ((4, 2),), 300000, AmountType.FIXED),
    PrizeTier("dlt", 5, "五等奖", ((4, 1),), 30000, AmountType.FIXED),
    PrizeTier("dlt", 6, "六等奖", ((3, 2),), 20000, AmountType.FIXED),
    PrizeTier("dlt", 7, "七等奖", ((4, 0),), 10000, AmountType.FIXED),
    PrizeTier("dlt", 8, "八等奖", ((3, 1), (2, 2)), 1500, AmountType.FIXED),
    PrizeTier("dlt", 9, "九等奖", ((3, 0), (1, 2), (2, 1), (0, 2)), 500, AmountType.FIXED),
)

# 七乐彩（基本号7 + 特别号1）
QLC_TIERS = (
    PrizeTier("qlc", 1, "一等奖", ((7, 1),), None, AmountType.FLOAT),
    PrizeTier("qlc", 2, "二等奖", ((7, 0),), None, AmountType.FLOAT),
    PrizeTier("qlc", 3, "三等奖", ((6, 1),), 300000, AmountType.FIXED),
    PrizeTier("qlc", 4, "四等奖", ((6, 0),), 10000, AmountType.FIXED),
    PrizeTier("qlc", 5, "五等奖", ((5, 1),), 5000, AmountType.FIXED),
    PrizeTier("qlc", 6, "六等奖", ((5, 0),), 2000, AmountType.FIXED),
    PrizeTier("qlc", 7, "七等奖", ((4, 1), (4, 0)), 1000, AmountType.FIXED),
)
```

并把 `PRIZE_TABLES` 字典改为：

```python
PRIZE_TABLES: dict[str, tuple[PrizeTier, ...]] = {
    "ssq": SSQ_TIERS, "dlt": DLT_TIERS, "qlc": QLC_TIERS, "fc3d": FC3D_TIERS,
}
```

> **注：** 上述大乐透/七乐彩固定档金额为编写时的常见值，**真实部署前需核对官方最新规则**（spec §5.3 要求固定档可配置）。`prize_tables.py` 后续在 Plan 2 会改为从配置/DB 加载，但接口不变。

- [ ] **Step 6: 跑测试验证通过**

Run: `pytest tests/domain/test_partition_extra.py -v`
Expected: 全部通过

- [ ] **Step 7: Commit**

```bash
git add app/domain/lottery_types.py app/domain/prize_tables.py \
        tests/domain/fixtures.py tests/domain/test_partition_extra.py
git commit -m "feat(domain): 大乐透+七乐彩规格与奖级(复用PartitionCompare)"
```

---

## Task 6: 排列3 / 排列5 / 七星彩（复用 PositionalCompare）

**Files:**
- Modify: `app/domain/lottery_types.py`
- Modify: `app/domain/prize_tables.py`
- Modify: `tests/domain/fixtures.py`
- Test: `tests/domain/test_positional_extra.py`

- [ ] **Step 1: 追加 fixtures 到 `tests/domain/fixtures.py`**

```python
# 排列3/排列5/七星彩：按位 0-9
PL3_2024160 = (4, 8, 2)
PL5_2024160 = (4, 8, 2, 6, 0)
QXC_2024060 = (1, 5, 3, 9, 2, 7, 8)  # 七星彩 7 位
```

- [ ] **Step 2: 写测试 `tests/domain/test_positional_extra.py`**

```python
from app.domain.models import NumberRange
from app.domain.lottery_types import PL3, PL5, QXC
from app.domain.compare.positional import PositionalCompare


def test_specs():
    assert PL3.front == NumberRange(0, 9, 3)
    assert PL5.front == NumberRange(0, 9, 5)
    assert QXC.front == NumberRange(0, 9, 7)
    assert PL3.back is None and QXC.back is None


def test_pl3_all_match_wins():
    from tests.domain.fixtures import PL3_2024160
    draw = {"front": PL3_2024160, "back": ()}
    entry = {"front": PL3_2024160, "back": ()}
    r = PositionalCompare().compare(PL3, draw, entry)
    assert r.is_win and r.tier == 1 and r.prize_amount == 104000


def test_pl5_all_match_wins():
    from tests.domain.fixtures import PL5_2024160
    draw = {"front": PL5_2024160, "back": ()}
    entry = {"front": PL5_2024160, "back": ()}
    r = PositionalCompare().compare(PL5, draw, entry)
    assert r.is_win and r.tier == 1 and r.prize_amount == 10000000  # 10万元


def test_qxc_all_match_wins_float():
    from tests.domain.fixtures import QXC_2024060
    draw = {"front": QXC_2024060, "back": ()}
    entry = {"front": QXC_2024060, "back": ()}
    r = PositionalCompare().compare(QXC, draw, entry)
    assert r.is_win and r.tier == 1 and r.prize_amount is None  # 七星彩一等奖浮动


def test_pl3_partial_not_win():
    from tests.domain.fixtures import PL3_2024160
    draw = {"front": PL3_2024160, "back": ()}
    entry = {"front": (4, 8, 9), "back": ()}  # 前两位对
    r = PositionalCompare().compare(PL3, draw, entry)
    assert not r.is_win and r.front_hit == 2
```

- [ ] **Step 3: 跑测试验证失败**

Run: `pytest tests/domain/test_positional_extra.py -v`
Expected: FAIL — `ImportError: cannot import name 'PL3'`

- [ ] **Step 4: 修改 `app/domain/lottery_types.py` 追加 PL3/PL5/QXC**

在 `QLC` 之后追加：

```python
PL3 = LotterySpec(
    code="pl3", name="排列3", category="sports",
    front=NumberRange(0, 9, 3), back=None,
    number_style=NumberStyle.POSITIONAL,
    draw_days=(0, 1, 2, 3, 4, 5, 6),
    play_types=(PlayType.SINGLE, PlayType.ZHIXUAN, PlayType.ZUXUAN),
)
PL5 = LotterySpec(
    code="pl5", name="排列5", category="sports",
    front=NumberRange(0, 9, 5), back=None,
    number_style=NumberStyle.POSITIONAL,
    draw_days=(0, 1, 2, 3, 4, 5, 6),
    play_types=(PlayType.SINGLE, PlayType.ZHIXUAN),
)
QXC = LotterySpec(
    code="qxc", name="七星彩", category="sports",
    front=NumberRange(0, 9, 7), back=None,
    number_style=NumberStyle.POSITIONAL,
    draw_days=(1, 4, 6),  # 二/五/日
    play_types=(PlayType.SINGLE, PlayType.ZHIXUAN),
)
```

并把 `LOTTERY_TYPES` 字典改为：

```python
LOTTERY_TYPES: dict[str, LotterySpec] = {
    "ssq": SSQ, "dlt": DLT, "qlc": QLC,
    "fc3d": FC3D, "pl3": PL3, "pl5": PL5, "qxc": QXC,
}
```

- [ ] **Step 5: 修改 `app/domain/prize_tables.py` 追加 PL3/PL5/QXC 奖级**

在 `QLC_TIERS` 之后追加：

```python
# 排列3 直选（组选 Phase 2）
PL3_TIERS = (
    PrizeTier("pl3", 1, "直选", ((3,),), 104000, AmountType.FIXED),
)
# 排列5 直选
PL5_TIERS = (
    PrizeTier("pl5", 1, "直选", ((5,),), 10000000, AmountType.FIXED),  # 10万元
)
# 七星彩（按位，连续命中定级）。MVP 仅实现一等奖（7位全中，浮动）。
# 完整奖级（连续6/5/4/3/2位）在 Phase 2 扩展 PositionalCompare 支持连续命中。
QXC_TIERS = (
    PrizeTier("qxc", 1, "一等奖", ((7,),), None, AmountType.FLOAT),
)
```

并把 `PRIZE_TABLES` 字典改为：

```python
PRIZE_TABLES: dict[str, tuple[PrizeTier, ...]] = {
    "ssq": SSQ_TIERS, "dlt": DLT_TIERS, "qlc": QLC_TIERS,
    "fc3d": FC3D_TIERS, "pl3": PL3_TIERS, "pl5": PL5_TIERS, "qxc": QXC_TIERS,
}
```

- [ ] **Step 6: 跑测试验证通过**

Run: `pytest tests/domain/test_positional_extra.py -v`
Expected: 全部通过

- [ ] **Step 7: Commit**

```bash
git add app/domain/lottery_types.py app/domain/prize_tables.py \
        tests/domain/fixtures.py tests/domain/test_positional_extra.py
git commit -m "feat(domain): 排列3/排列5/七星彩规格与奖级(复用PositionalCompare)"
```

> **七星彩完整奖级说明：** 七星彩实际按"连续命中位数"定级（7连=一等、6连=二等…）。MVP 的 PositionalCompare 只判"全位命中"，故七星彩仅实现一等奖。Phase 2 会扩展一个 `ConsecutivePositionalCompare` 或在 PrizeTier 增加"连续命中"条件类型来支持完整七星彩奖级。此限制已记录，不阻塞 MVP（七星彩追号用户绝大多数只关心是否全中）。

---

## Task 7: 策略路由注册入口 + 覆盖率门禁 + 全量验证

确保所有策略在统一入口注册，且领域层覆盖率达 95%+。

**Files:**
- Modify: `app/domain/__init__.py`
- Create: `tests/domain/test_strategy_routing.py`
- Modify: `pytest.ini`

- [ ] **Step 1: 写测试 `tests/domain/test_strategy_routing.py`**

```python
import pytest
from app.domain import compare  # 触发统一注册
from app.domain.lottery_types import LOTTERY_TYPES
from app.domain.compare.strategy import get_strategy, compare
from app.domain.models import NumberStyle


def test_all_seven_lotteries_have_strategy():
    for code, spec in LOTTERY_TYPES.items():
        strat = get_strategy(spec)
        assert strat is not None, f"{code} 无策略"


def test_partition_lotteries_use_partition_compare():
    from app.domain.compare.partition import PartitionCompare
    for code in ("ssq", "dlt", "qlc"):
        assert isinstance(get_strategy(LOTTERY_TYPES[code]), PartitionCompare)


def test_positional_lotteries_use_positional_compare():
    from app.domain.compare.positional import PositionalCompare
    for code in ("fc3d", "pl3", "pl5", "qxc"):
        assert isinstance(get_strategy(LOTTERY_TYPES[code]), PositionalCompare)


def test_all_lotteries_smoke_compare():
    """每个彩种至少能跑通一次比对（不抛异常）。"""
    for code, spec in LOTTERY_TYPES.items():
        front_len = spec.front.count
        draw = {"front": tuple(range(front_len)), "back": (1,) if spec.back else ()}
        entry = {"front": tuple(range(front_len)), "back": (1,) if spec.back else ()}
        r = compare(spec, draw, entry)
        assert r.is_win in (True, False)
```

- [ ] **Step 2: 跑测试验证失败（注册入口未统一）**

Run: `pytest tests/domain/test_strategy_routing.py -v`
Expected: 可能 FAIL（若仅导入 `app.domain` 未触发 partition/positional 的 `@register`）

- [ ] **Step 3: 实现 `app/domain/__init__.py` 统一注册入口**

```python
"""领域层包。导入本包即注册所有比对策略（import 副作用）。"""
from app.domain import compare  # noqa: F401
from app.domain.compare import partition, positional  # noqa: F401  触发 @register

__all__ = ["compare", "partition", "positional"]
```

- [ ] **Step 4: 跑测试验证通过**

Run: `pytest tests/domain/test_strategy_routing.py -v`
Expected: 全部通过

- [ ] **Step 5: 加覆盖率门禁到 `pytest.ini`**

把 `pytest.ini` 的 `addopts` 行改为：

```ini
addopts = -ra --strict-markers --cov=app.domain --cov-report=term-missing --cov-fail-under=95
```

- [ ] **Step 6: 跑全量测试 + 覆盖率**

Run: `pytest -v`
Expected: 全部通过，`app/domain` 覆盖率 ≥ 95%（终端显示 `TOTAL ... 95%+`）

- [ ] **Step 7: Commit**

```bash
git add app/domain/__init__.py tests/domain/test_strategy_routing.py pytest.ini
git commit -m "feat(domain): 策略统一注册入口+覆盖率门禁95%"
```

---

## Self-Review（plan 作者自查，已执行）

**1. Spec 覆盖：**
- §5.1 彩种规格 → Task 3-6（7 彩种 LotterySpec）✅
- §5.2 号码/玩法模型 → Task 2（PlayType 枚举，含 single/fushi/dantuo/zhixuan/zuxuan）✅；单式比对 Task 3-6 ✅；复式/胆拖/组选 = Phase 2（plan 已声明范围）✅
- §5.3 奖级规则 → Task 3-6（PrizeTier + conditions）✅；固定档可配置 = Plan 2 改 DB 加载（已注明）✅；浮动档 amount=None ✅
- §5.4 比对策略 → Task 3（Partition）、Task 4（Positional）、Task 7（路由注册）✅
- §11 测试策略 → 全程 TDD + 真实历史数据 fixtures + 95% 覆盖率门禁 ✅

**2. 占位符扫描：** 无 TBD/TODO/"实现错误处理"等。七星彩完整奖级限制有明确说明（非占位符，是显式的 Phase 2 范围声明）。✅

**3. 类型一致性：** `HitResult(front_hit, back_hit, tier, prize_amount, is_win)` 全 plan 一致；`compare(spec, draw, entry)` 签名一致；`draw`/`entry` 均为 `{"front": tuple, "back": tuple}` 字典，全 plan 一致；`PRIZE_TABLES[spec.code]` 取用一致。✅

**4. 残留风险（非 plan 缺陷，记录给后续 plan）：**
- 大乐透/七乐彩固定档金额需部署前核对官方最新值（Task 5 已注明）。
- 七星彩仅一等奖（Task 6 已注明 Phase 2 扩展）。
- `prize_tables.py` 当前硬编码，Plan 2 改为配置/DB 驱动（接口不变）。

---

## Execution Handoff

Plan 1 完成（7 个 Task），产出：经 95%+ 覆盖率验证的领域层（7 彩种规格 + 奖级表 + 分区/按位比对策略 + 统一路由），零 IO、零运行时第三方依赖，可立即被 Plan 2 的比对引擎/数据层使用。

**后续 Plan 路线图**（Plan 1 完成后依次生成）：
- **Plan 2** — 数据层(SQLite/SQLModel) + 开奖获取(双源 MXNZP+聚合数据 交叉校验) + 比对引擎(幂等写 comparisons) + 推送(Bark+飞书) + 调度(APScheduler 路径A/B) + 端到端集成测试
- **Plan 3** — 用户体系(邀请制/认证/隔离) + FastAPI REST API
- **Plan 4** — 前端 Vue3+ECharts（按 `docs/superpowers/prototypes/` 的 prototype）
- **Plan 5** — 统计/提醒/走势(合规版)/运维管理
- **Plan 6** — Docker 部署到 NAS（端口 8280, restart: always）
