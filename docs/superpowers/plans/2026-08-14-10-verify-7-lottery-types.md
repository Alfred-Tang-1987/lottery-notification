# Plan 10：7 彩种「文档 vs 代码」核对与 B1 修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 逐彩种核对 `docs/reference/lottery-rules.md` vs `app/domain/` 实现，按 B1 范围处置：小差异 TDD 修复（dlt 2026 七档新规 / qlc 浮动档与金额 / qxc 任意对位语义与漏判 / fc3d danxuan 静默不比对）、未实现玩法诚实降级为文档声明 + B2 roadmap、已实现面补齐单元测试，产出带处置列的核对报告。

**Architecture:** 纯领域层数据/语义修正（`prize_tables.py` / `compare.py` / `entry.py`）+ API 边界玩法校验（`app/api/tickets.py`）+ 前端玩法列表裁剪（`web/src/lib/lotteries.ts`）。ssq 生产回归基线夹具先行（T0），之后每个彩种一个任务、独立 TDD 循环。不改表结构、不改适配器、不改调度。

**Tech Stack:** Python 3.12 / pytest / FastAPI TestClient / vitest。

**Spec:** `docs/superpowers/specs/2026-08-14-open-source-release-design.md` §4（B1 范围，autoplan 终审 2026-08-14 确认）

**前置事实（2026-08-14 官方源核实，写 plan 时已查证）：**

- **大乐透 2026 新规已生效**（财综〔2025〕51 号，2026-01-31 第 26014 期起）：9 档并 **7 档**——新三等 = 原(5+0)+(4+2) = 5000 元；新四等 = 4+1 = 300 元；新五等 = 原(4+0)+(3+2) = 150 元；新六等 = 3+1 或 2+2 = 15 元；新七等 = 3+0/1+2/2+1/0+2 = 5 元；一二等浮动、追加 80% 不变；奖池 ≥8 亿时固定档上浮（6666/380/200/18/7）。**代码现表把 2019 金额贴在错误的合并条件上，且把不中奖的 1+1/2+0/0+1 判成七等 100 元**——必须重写。
- **七星彩 2020 新规**（2020-10-11 起）：「**任意 N 位对位一致**」计数，非连续。三/四/五/六等 = 3000/500/30/5 元；六等含 3+0、2+1、1+1、0+1。**代码用「首位起前缀连续命中」近似 + 旧版金额 1800/300/100/10，且漏判 2+1/1+1/0+1**——静默漏中奖，必须修。
- **七乐彩**：一二三等皆浮动（70%/10%/20%）；四~七等固定 200/50/10/5 元；七等仅 4+0。**代码把三等误录固定 3045 元、四等误录 300 元、七等多判 3+1**。
- **双色球 2026 新规**：固定档不变（2026-08-13 第 2026093 期官方公告实测 3000/200/10/5）；新增「福运奖」（奖池 ≥15 亿时 3+0 也得 5 元，<3 亿停执行）依赖奖池数据 → B2。
- **fc3d**：UI 可建 `danxuan` 票，但 `app/domain/entry.py:48` 只接受 single/zhixuan → 比对抛 NotImplementedError 被 per-ticket 隔离**静默跳过**，fc3d 票永不比对。pl3 的 zuxuan3/6 同理可建不可比对。
- 金额单位「分」纪律（2026-08-03 事故）：所有固定档 = 官方元 ×100。

## Global Constraints

- **B1 范围红线**：只做「小差异代码修复 + 文档降级 + 已实现面补测」。**不实现**复式/胆拖/组选三六/fc3d 其余玩法/pl5 定位组合复式/ssq 福运奖/dlt 奖池上浮档（全部 B2 roadmap）。
- **ssq 基线先行**：T0 的 `tests/domain/test_ssq_regression_baseline.py` 在任何 `prize_tables.py`/`compare.py` 改动前必须先合入且绿；之后每次改动后必须仍绿。
- **TDD**：每任务 RED（失败测试）→ GREEN（最小修复）→ REFACTOR；commit 约定 `feat(plan-10/T<n>): <描述>`。
- **领域层零 IO**（`uv run lint-imports` 强制）；金额一律分（int）；`draw_days` 0-based 周几。
- **文档与代码同 commit**：每个修复任务同步更新 `docs/reference/lottery-rules.md` 对应彩种节与核对报告对应行。
- **断言更新纪律**：修复后全量回归若有旧断言失败，逐条确认该断言编码的是**旧错误规则**（对照本 plan 前置事实表）才可改，并在断言注释注明新规则出处；任何不确定的失败停下来人工核对，不改测试将就代码。
- 全程时区 Asia/Shanghai；测试不触网（领域层纯函数）。

---

### Task 0: ssq 生产回归基线夹具 + 核对报告骨架

**Files:**
- Create: `tests/fixtures/ssq_baseline_2026093.json`
- Create: `tests/domain/test_ssq_regression_baseline.py`
- Create: `docs/reference/lottery-verification-2026-08-14.md`

**Interfaces:**
- Consumes: `app.domain.compare.compare`（`compare(spec, *, draw_front, draw_back, entry) -> list[HitResult]`）；`app.domain.entry.Entry(lottery_code, play_type, front, back, tuo=None, multiplier=1, append=False)`；`app.seeds.lottery_types.SPECS`（`list[dict]`，可 `LotterySpec.from_dict` hydrate）。
- Produces: ssq 回归基线测试（后续所有任务改动后必跑）；核对报告文件（T1–T6 逐行填充）。`HitResult` 字段：`front_hit, back_hit, tier, amount, is_win`。

夹具数据为 **2026-08-13 第 2026093 期真实开奖**（官方 cwl API 实测，2026-08-14 抓取）：红球 05 08 15 20 21 24，蓝球 09，奖池 5.66 亿元（<15 亿 → 福运奖未激活），官方公布单注奖金：一等 6,581,443 元（8 注）、二等 145,086 元（109 注）、三等 3000 元、四等 200 元、五等 10 元、六等 5 元。

- [ ] **Step 1: 写夹具**

```json
{
  "issue": "2026093",
  "lottery_code": "ssq",
  "draw_date": "2026-08-13",
  "draw_front": [5, 8, 15, 20, 21, 24],
  "draw_back": [9],
  "pool_money_yuan": 566415284,
  "prizegrades_yuan": {"1": 6581443, "2": 145086, "3": 3000, "4": 200, "5": 10, "6": 5},
  "note": "2026-02-01 新规后真实开奖。奖池 5.66 亿 < 15 亿 → 福运奖（3+0=5 元）本期未激活，3+0 不中奖。",
  "source": "https://www.cwl.gov.cn/cwl_admin/front/cwlkj/search/kjxx/findDrawNotice?name=ssq&code=2026093",
  "fetched_at": "2026-08-14"
}
```

- [ ] **Step 2: 写基线测试（本步即应全过——ssq 是生产验证彩种，现状即基线；若失败说明现状已偏离，停下核对）**

```python
# tests/domain/test_ssq_regression_baseline.py
"""ssq 生产回归基线（Plan 10 / T0；spec §4.2）。

唯一生产验证彩种的「真实开奖 → 真实奖金」夹具回归：任何 prize_tables/compare
改动前后本文件必须全绿。夹具为 2026-08-13 第 2026093 期官方公告实测值
（2026-02-01 新规后），见 tests/fixtures/ssq_baseline_2026093.json。
"""

import json
from pathlib import Path

import pytest

from app.domain.compare import compare
from app.domain.entry import Entry
from app.domain.prize_tables import get_tiers
from app.domain.spec import LotterySpec
from app.seeds.lottery_types import SPECS

FIXTURE = json.loads(
    (Path(__file__).resolve().parent.parent / 'fixtures' / 'ssq_baseline_2026093.json').read_text()
)
DRAW_FRONT = tuple(FIXTURE['draw_front'])
DRAW_BACK = tuple(FIXTURE['draw_back'])

_SPEC = LotterySpec.from_dict(next(s for s in SPECS if s['code'] == 'ssq'))


def _hit(front, back):
    entry = Entry(lottery_code='ssq', play_type='single', front=tuple(front), back=tuple(back))
    results = compare(_SPEC, draw_front=DRAW_FRONT, draw_back=DRAW_BACK, entry=entry)
    assert len(results) == 1
    return results[0]


@pytest.mark.parametrize(
    ('front', 'back', 'tier', 'amount'),
    [
        # 一等/二等浮动：tier 命中、amount=None（开奖当晚「待官方派奖」，回填流程负责金额）
        ([5, 8, 15, 20, 21, 24], [9], 1, None),
        ([5, 8, 15, 20, 21, 24], [1], 2, None),
        # 固定档：金额=分（官方元 ×100），与夹具官方公布值一致
        ([5, 8, 15, 20, 21, 1], [9], 3, 300000),   # 5+1 → 3000 元
        ([5, 8, 15, 20, 21, 1], [1], 4, 20000),    # 5+0 → 200 元
        ([5, 8, 15, 20, 1, 2], [9], 4, 20000),     # 4+1 → 200 元
        ([5, 8, 15, 20, 1, 2], [1], 5, 1000),      # 4+0 → 10 元
        ([5, 8, 15, 1, 2, 3], [9], 5, 1000),       # 3+1 → 10 元
        ([5, 8, 1, 2, 3, 4], [9], 6, 500),         # 2+1 → 5 元
        ([5, 1, 2, 3, 4, 6], [9], 6, 500),         # 1+1 → 5 元
        ([1, 2, 3, 4, 6, 7], [9], 6, 500),         # 0+1 → 5 元
    ],
)
def test_ssq_baseline_tiers(front, back, tier, amount):
    r = _hit(front, back)
    assert r.is_win, f'{front}+{back} 应中 {tier} 等'
    assert r.tier == tier
    assert r.amount == amount


def test_ssq_baseline_3_plus_0_no_win_this_draw():
    """3+0 本期不中奖——本期奖池 5.66 亿 < 15 亿，2026 新规「福运奖」未激活。

    ⚠️ 福运奖（奖池 ≥15 亿时 3+0=5 元）未实现（B2 roadmap）：若未来奖池 ≥15 亿，
    3+0 实际中奖而系统判未中——属已知限制，README/规则文档已声明。
    """
    r = _hit([5, 8, 15, 1, 2, 3], [1])
    assert not r.is_win


def test_ssq_baseline_no_win():
    assert not _hit([1, 2, 3, 4, 6, 7], [1]).is_win  # 0+0


def test_ssq_fixed_amounts_match_official_announcement():
    """代码固定档（分） == 夹具官方公布（元 ×100）——锁定元/分单位不回退。"""
    tiers = {t.tier: t for t in get_tiers('ssq')}
    for tier_str, yuan in FIXTURE['prizegrades_yuan'].items():
        tier = int(tier_str)
        if tiers[tier].amount is None:
            continue  # 浮动档金额由回填流程负责，不在此断言
        assert tiers[tier].amount == yuan * 100, (
            f'ssq {tier} 等：官方 {yuan} 元 = {yuan * 100} 分，代码 {tiers[tier].amount}'
        )
```

- [ ] **Step 3: 跑基线确认绿（现状即基线）**

Run: `uv run pytest tests/domain/test_ssq_regression_baseline.py -v`
Expected: 13 passed（10 参数化命中 + 3+0 不中 + 全不中 + 官方金额一致性）。**若任何用例失败：ssq 现状已偏离官方规则——停下人工核对，不要继续后续任务。**

- [ ] **Step 4: 写核对报告骨架（含 ssq 行）**

```markdown
# 7 彩种「文档 vs 代码」核对报告（2026-08-14，plan-10，B1 范围）

> 方法：逐彩种对照 `docs/reference/lottery-rules.md`（及官方规则页）vs
> `app/domain/{spec.py, compare.py, prize_tables.py, entry.py}` + `app/seeds/lottery_types.py`。
> 核对维度：号码结构 / 开奖日 / 玩法体系 / 特殊规则 / 奖金表（金额单位分）。
> 处置列：B1代码修复（本 plan 内 TDD）/ 文档降级（声明已知限制）/ B2 roadmap。
> 官方源核实日期：2026-08-14。

## 汇总表

| 彩种 | 号码结构 | 开奖日 | 玩法体系 | 奖金表 | 处置 |
|---|---|---|---|---|---|
| ssq | ✅ 一致（6/33+1/16） | ✅ 二/四/日 | 单式已实现；复式/胆拖未实现 | ✅ 一致（2026 新规固定档不变，2026093 期实测） | 文档降级（福运奖/复式胆拖）+ B2 |
| dlt | ✅ 一致 | ✅ 一/三/六 | 单式+追加已实现；复式/胆拖未实现 | ❌ 旧表（2019 金额贴错条件；1+1/2+0/0+1 误判中奖） | **B1代码修复（T1）** + B2（上浮档/复式胆拖） |
| qlc | ✅ 一致 | ✅ 一/三/五 | 单式已实现 | ❌ 三等误录固定 3045（应浮动）；四等 300（应 200）；七等多判 3+1 | **B1代码修复（T2）** + B2 |
| qxc | ✅ 一致（2020 改版结构） | ✅ 二/五/日 | 单式已实现 | ❌ 前缀连续近似（应任意对位计数）+ 旧版金额 + 漏判 2+1/1+1/0+1 | **B1代码修复（T3）** |
| fc3d | ✅ 一致 | ✅ 每日 | ❌ danxuan 可建票但比对静默跳过；组选/其余 9 玩法未实现 | ✅ 单选 1040 一致 | **B1代码修复（T4）** + 文档降级 + B2 |
| pl3 | ✅ 一致 | ✅ 每日 | 直选已实现；组选三/六未实现（可建不可比对 → T4 拦截） | ✅ 直选 1040 一致 | **B1代码修复（T4）** + 文档降级 + B2 |
| pl5 | ✅ 一致 | ✅ 每日 | 直选已实现；定位/组合复式未实现 | ✅ 10 万/注一致 | 文档降级 + B2 |

## B2 roadmap（发布后，不进本 plan）

1. 复式 / 胆拖组合展开（`entry.py` `_count_combos` + `expand`）
2. fc3d 组选三/六及其余玩法（1D/2D/通选/和数/包选/猜大小/猜三同/拖拉机/猜奇偶）
3. pl3 组选三/六；pl5 直选定位复式 / 组合复式
4. ssq 福运奖（需奖池数据接入 compare 上下文）
5. dlt 固定档奖池 ≥8 亿上浮金额（需奖池数据）
6. 财务正确性改动的 ssq 生产基线扩展（更多期次夹具）

## 逐彩种明细

### ssq（T0 完成）
- 号码结构 6/33+1/16、开奖日 [1,3,6]、单式比对、固定档 3000/200/10/5 元——与官方一致（2026093 期实测锁定，`tests/domain/test_ssq_regression_baseline.py`）。
- 2026-02-01 新规影响：固定档不变；一二等单期总额封顶（1 亿 / 7000 万）不影响比对（金额由官方回填）；**福运奖（奖池 ≥15 亿时 3+0=5 元）未实现** → 处置：文档降级（README/规则文档已声明）+ B2 roadmap #4。
- 来源：cwl.gov.cn 第 2026093 期开奖公告（2026-08-14 抓取）。

### dlt（T1 完成）
### qlc（T2 完成）
### qxc（T3 完成）
### fc3d / pl3 / pl5（T4 完成）
```

（dlt/qlc/qxc/fc3d 节由对应任务填充；汇总表对应行的「处置」保持上表现状，任务完成时只补明细节。）

- [ ] **Step 5: 提交**

```bash
git add tests/fixtures/ssq_baseline_2026093.json tests/domain/test_ssq_regression_baseline.py docs/reference/lottery-verification-2026-08-14.md
git commit -m "feat(plan-10/T0): ssq 生产回归基线夹具（2026093 期真实开奖→真实奖金）+ 核对报告骨架"
```

---

### Task 1: dlt 修正为 2026 七档新规（TDD）

**Files:**
- Modify: `app/domain/prize_tables.py:33-57`（dlt 表重写）
- Modify: `tests/domain/test_prize_tables.py:53`（EXPECTED_FIXED_CENTS['dlt']）
- Create: `tests/domain/test_dlt_2026_rules.py`
- Modify: `docs/reference/lottery-rules.md`（dlt 节增奖级表）
- Modify: `docs/reference/lottery-verification-2026-08-14.md`（dlt 明细节）

**Interfaces:**
- Consumes: `PrizeTier(tier, condition, amount, amount_type, append_multiplier=1.0)`；`PartitionCompare.compare('dlt', draw_front, draw_back, combo_front, combo_back, *, append)`；T0 基线。
- Produces: 2026 新规 dlt 奖级表（7 档；金额=奖池 <8 亿基础档，单位分）；lottery-rules.md dlt 权威表。

- [ ] **Step 1: RED——改 `EXPECTED_FIXED_CENTS` + 写条件测试**

`tests/domain/test_prize_tables.py` 的 `EXPECTED_FIXED_CENTS` 中 dlt 行改为：

```python
    'dlt': {3: 500000, 4: 30000, 5: 15000, 6: 1500, 7: 500},
```

同时把上方注释块中 dlt 行改为 `#   dlt 三等5000 / 四等300 / 五等150 / 六等15 / 七等5 元（2026-02-01 新规，财综〔2025〕51 号）`。

新建 `tests/domain/test_dlt_2026_rules.py`：

```python
# tests/domain/test_dlt_2026_rules.py
"""dlt 2026 新规（9 档并 7 档）条件测试（Plan 10 / T1）。

依据：财综〔2025〕51 号 + 体彩中心公告（2026-01-16），第 26014 期（2026-01-31）起执行。
合并规则：原(5+0)+(4+2)→新三等；原(4+0)+(3+2)→新五等；1+1/2+0/0+1 不中奖。
金额为奖池 <8 亿基础档；≥8 亿上浮档（6666/380/200/18/7）需奖池数据，B2 roadmap。
"""

import pytest

from app.domain.compare import PartitionCompare
from app.domain.prize_tables import get_tiers

DRAW_FRONT = (1, 2, 3, 4, 5)
DRAW_BACK = (6, 7)


def _dlt(front, back, append=False):
    return PartitionCompare.compare(
        'dlt', DRAW_FRONT, DRAW_BACK, tuple(front), tuple(back), append=append,
    )


@pytest.mark.parametrize(
    ('front', 'back', 'tier', 'amount'),
    [
        ((1, 2, 3, 4, 5), (6, 7), 1, None),        # 5+2 一等浮动
        ((1, 2, 3, 4, 5), (6, 8), 2, None),        # 5+1 二等浮动
        ((1, 2, 3, 4, 5), (8, 9), 3, 500000),      # 5+0 → 新三等 5000 元
        ((1, 2, 3, 4, 9), (6, 7), 3, 500000),      # 4+2 → 新三等（合并档）
        ((1, 2, 3, 4, 9), (6, 8), 4, 30000),       # 4+1 → 四等 300 元
        ((1, 2, 3, 4, 9), (8, 9), 5, 15000),       # 4+0 → 新五等 150 元
        ((1, 2, 3, 9, 9), (6, 7), 5, 15000),       # 3+2 → 新五等（合并档）
        ((1, 2, 3, 9, 9), (6, 8), 6, 1500),        # 3+1 → 六等 15 元
        ((1, 2, 9, 9, 9), (6, 7), 6, 1500),        # 2+2 → 六等
        ((1, 2, 3, 9, 9), (8, 9), 7, 500),         # 3+0 → 七等 5 元
        ((1, 9, 9, 9, 9), (6, 7), 7, 500),         # 1+2 → 七等
        ((1, 2, 9, 9, 9), (6, 8), 7, 500),         # 2+1 → 七等
        ((9, 9, 9, 9, 9), (6, 7), 7, 500),         # 0+2 → 七等
    ],
)
def test_dlt_2026_tiers(front, back, tier, amount):
    r = _dlt(front, back)
    assert r.is_win, f'{front}+{back} 应中 {tier} 等'
    assert r.tier == tier and r.amount == amount


@pytest.mark.parametrize(
    ('front', 'back'),
    [
        ((1, 9, 9, 9, 9), (6, 8)),   # 1+1 不中奖（旧表误判七等 100 元）
        ((1, 2, 9, 9, 9), (8, 9)),   # 2+0 不中奖
        ((9, 9, 9, 9, 9), (6, 8)),   # 0+1 不中奖
        ((9, 9, 9, 9, 9), (8, 9)),   # 0+0
    ],
)
def test_dlt_2026_non_winning(front, back):
    assert not _dlt(front, back).is_win


def test_dlt_append_multiplier_only_on_float_tiers():
    """追加 1.8 仅一二等（浮动）；固定档 append_multiplier=1.0。"""
    tiers = {t.tier: t for t in get_tiers('dlt')}
    assert tiers[1].append_multiplier == 1.8
    assert tiers[2].append_multiplier == 1.8
    for n in range(3, 8):
        assert tiers[n].append_multiplier == 1.0, f'{n} 等不得有追加倍数'
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/domain/test_dlt_2026_rules.py tests/domain/test_prize_tables.py -v`
Expected: FAIL（旧表条件/金额不符）

- [ ] **Step 3: GREEN——重写 `prize_tables.py` 的 dlt 表**

```python
    # 大乐透（2026-02-01 新规，财综〔2025〕51 号，第 26014 期起；9 档并 7 档）：
    # 一二等浮动（追加 1.8 = 基本 + 追加 80%）；三等 5000 / 四等 300 / 五等 150 / 六等 15 / 七等 5 元。
    # 金额为奖池 <8 亿基础档；≥8 亿上浮（6666/380/200/18/7）需奖池数据，未实现（B2 roadmap，
    # 核对报告 dlt 节）。1+1 / 2+0 / 0+1 不中奖（旧表曾误判，2026-08-14 修正）。
    'dlt': [
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
    ],
```

（删除旧 dlt 表末尾「八/九等 Phase 2」注释——2026 新规已无八九等。）

- [ ] **Step 4: 跑新测试 + ssq 基线 + 全量回归**

Run: `uv run pytest tests/domain/ -q && uv run pytest -q`
Expected: 新测试全过、T0 基线绿、全量绿。若其他测试失败：逐条确认其编码的是旧错误规则（对照本任务 Step 1 注释的合并规则）后更新，断言注释注明「2026 新规，财综〔2025〕51 号」；不确定就停下人工核对。

- [ ] **Step 5: 更新 `docs/reference/lottery-rules.md` 的 dlt 节**

在「### 大乐透 dlt」节末尾追加：

```markdown
#### 奖级表（2026-02-01 起，财综〔2025〕51 号，第 26014 期执行；9 档并 7 档）

| 奖级 | 条件（前区+后区） | 奖金 |
|---|---|---|
| 一等 | 5+2 | 浮动（基本单注封顶 1000 万；追加 80%，合计封顶 1800 万；单期总额封顶 1 亿） |
| 二等 | 5+1 | 浮动（追加同上；单期总额封顶 1 亿） |
| 三等 | 5+0 或 4+2 | 5000 元（奖池 ≥8 亿时 6666 元） |
| 四等 | 4+1 | 300 元（≥8 亿时 380 元） |
| 五等 | 4+0 或 3+2 | 150 元（≥8 亿时 200 元） |
| 六等 | 3+1 或 2+2 | 15 元（≥8 亿时 18 元） |
| 七等 | 3+0 / 1+2 / 2+1 / 0+2 | 5 元（≥8 亿时 7 元） |

- 1+1 / 2+0 / 0+1 **不中奖**。追加仍仅参与一二等奖（80%）。
- 实现注记：固定档按「奖池 <8 亿基础档」录入；≥8 亿上浮档需奖池数据 → B2 roadmap。
- 来源：财政部财综〔2025〕51 号 + 国家体育总局体彩中心公告（2026-01-16）；lottery.gov.cn 大乐透规则页（2026-08-14 核对）。
```

- [ ] **Step 6: 填核对报告 dlt 明细节**

把报告中「### dlt（T1 完成）」扩写为：

```markdown
### dlt（T1 完成）
- 号码结构 5/35+2/12、开奖日 [0,2,5]、追加标志（`append_multiplier=1.8` 仅一二等）——一致。
- **发现**：代码旧表把 2019 金额贴在错误的合并条件上（如 4+2 判 10000 元），并把不中奖的 1+1/2+0/0+1 判成七等 100 元（错误的中奖通知！）。
- **处置（B1代码修复，T1）**：重写为 2026 七档新规（条件+基础档金额），`test_dlt_2026_rules.py` 14 档条件 + 4 不中奖用例锁定。
- **遗留（B2）**：奖池 ≥8 亿上浮档（需奖池数据）；复式/胆拖。
- 来源：财综〔2025〕51 号；lottery.gov.cn（2026-08-14 核对）。
```

- [ ] **Step 7: 提交**

```bash
git add app/domain/prize_tables.py tests/domain/test_prize_tables.py tests/domain/test_dlt_2026_rules.py docs/reference/lottery-rules.md docs/reference/lottery-verification-2026-08-14.md
git commit -m "feat(plan-10/T1): dlt 奖级表修正为 2026 七档新规（修 1+1/2+0/0+1 误判中奖）"
```

---

### Task 2: qlc 修正（三等浮动 / 四等 200 / 七等仅 4+0）（TDD）

**Files:**
- Modify: `app/domain/prize_tables.py:58-68`（qlc 表）
- Modify: `tests/domain/test_prize_tables.py:54`（EXPECTED_FIXED_CENTS['qlc']）
- Create: `tests/domain/test_qlc_rules.py`
- Modify: `docs/reference/lottery-rules.md`（qlc 节增奖级表）
- Modify: `docs/reference/lottery-verification-2026-08-14.md`（qlc 明细节）

**Interfaces:**
- Consumes: 同 T1（`PartitionCompare`，qlc 后区=特别号，`back_hit` 0/1）。
- Produces: 修正后 qlc 表。注意 qlc 三等改浮动后由 FloatRefillWorker 回填——`CwlPrizeSource` 已覆盖 qlc（`name=qlc` 直传 cwl API），无需适配器改动。

- [ ] **Step 1: RED**

`EXPECTED_FIXED_CENTS` 的 qlc 行改为（去掉三等——它是浮动档）：

```python
    'qlc': {4: 20000, 5: 5000, 6: 1000, 7: 500},
```

注释块 qlc 行改为 `#   qlc 三等浮动（高等奖 20%）/ 四等200 / 五等50 / 六等10 / 七等5 元（2026-08-14 核对福彩官方）`。

新建 `tests/domain/test_qlc_rules.py`：

```python
# tests/domain/test_qlc_rules.py
"""qlc 奖级修正测试（Plan 10 / T2）。

依据：福彩七乐彩官方规则——一二三等皆浮动（高等奖 70%/10%/20%）；
四~七等固定 200/50/10/5 元；七等仅 4+0（3+1 不中奖）。2026-08-14 核对。
"""

from app.domain.compare import PartitionCompare
from app.domain.prize_tables import get_tiers

DRAW_FRONT = (1, 2, 3, 4, 5, 6, 7)
SPECIAL = (8,)  # 特别号（同源 01-30 池）


def _qlc(front, back):
    return PartitionCompare.compare('qlc', DRAW_FRONT, SPECIAL, tuple(front), tuple(back), append=False)


def test_qlc_third_prize_is_float():
    """三等 6+0 是浮动奖（旧表误录固定 3045 元）。"""
    r = _qlc((1, 2, 3, 4, 5, 6, 9), (9,))
    assert r.is_win and r.tier == 3 and r.amount is None


def test_qlc_fourth_prize_200():
    """四等 5+1 = 200 元（旧表误录 300 元）。"""
    r = _qlc((1, 2, 3, 4, 5, 9, 9), (8,))
    assert r.is_win and r.tier == 4 and r.amount == 20000


def test_qlc_seventh_prize_only_4_plus_0():
    """七等仅 4+0 = 5 元；3+1 不中奖（旧表误含）。"""
    r = _qlc((1, 2, 3, 4, 9, 9, 9), (9,))
    assert r.is_win and r.tier == 7 and r.amount == 500
    r_31 = _qlc((1, 2, 3, 9, 9, 9, 9), (8,))
    assert not r_31.is_win, '3+1 在七乐彩不中奖'


def test_qlc_float_tiers_have_no_amount():
    tiers = {t.tier: t for t in get_tiers('qlc')}
    for n in (1, 2, 3):
        assert tiers[n].amount is None and tiers[n].amount_type == 'float'
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/domain/test_qlc_rules.py tests/domain/test_prize_tables.py -v`
Expected: FAIL（旧表不符）

- [ ] **Step 3: GREEN——重写 qlc 表**

```python
    # 七乐彩（一二三等浮动=高等奖 70%/10%/20%；特别号 = back_hit；2026-08-14 核对福彩官方）：
    # 四等 200 / 五等 50 / 六等 10 / 七等 5 元；七等仅 4+0（3+1 不中奖，旧表误含）。
    'qlc': [
        PrizeTier(1, 'front_hit==7', None, _V),
        PrizeTier(2, 'front_hit==6 and back_hit==1', None, _V),
        PrizeTier(3, 'front_hit==6 and back_hit==0', None, _V),
        PrizeTier(4, 'front_hit==5 and back_hit==1', 20000, _F),
        PrizeTier(5, 'front_hit==5 and back_hit==0', 5000, _F),
        PrizeTier(6, 'front_hit==4 and back_hit==1', 1000, _F),
        PrizeTier(7, 'front_hit==4 and back_hit==0', 500, _F),
    ],
```

- [ ] **Step 4: 回归（含 refill 路径）**

Run: `uv run pytest tests/domain/ tests/services/test_refill_service.py tests/adapters/ -q && uv run pytest -q`
Expected: 全绿（qlc 三等浮动后由 CwlPrizeSource `name=qlc` 回填，适配器无需改动；refill 相关测试应保持绿）。旧断言失败按 Global Constraints 断言更新纪律处理。

- [ ] **Step 5: 更新 lottery-rules.md qlc 节 + 核对报告**

qlc 节末尾追加：

```markdown
#### 奖级表（2026-08-14 核对福彩官方）

| 奖级 | 条件（基本号+特别号） | 奖金 |
|---|---|---|
| 一等 | 7+0 | 浮动（高等奖 70%） |
| 二等 | 6+1 | 浮动（高等奖 10%） |
| 三等 | 6+0 | 浮动（高等奖 20%） |
| 四等 | 5+1 | 200 元 |
| 五等 | 5+0 | 50 元 |
| 六等 | 4+1 | 10 元 |
| 七等 | 4+0 | 5 元（3+1 不中奖） |

- 来源：中国福彩七乐彩游戏规则（cwl.gov.cn）+ 深圳福彩中心规则页（2026-08-14 核对）。
```

核对报告 qlc 明细节：

```markdown
### qlc（T2 完成）
- 号码结构 7/30+特别号（同池）、开奖日 [0,2,4]——一致。
- **发现**：三等误录固定 3045 元（应浮动）；四等误录 300 元（应 200）；七等多判 3+1（不中奖）。
- **处置（B1代码修复，T2）**：表重写 + `test_qlc_rules.py` 锁定；三等浮动走既有 CwlPrizeSource 回填（覆盖 qlc）。
```

- [ ] **Step 6: 提交**

```bash
git add app/domain/prize_tables.py tests/domain/test_prize_tables.py tests/domain/test_qlc_rules.py docs/reference/lottery-rules.md docs/reference/lottery-verification-2026-08-14.md
git commit -m "feat(plan-10/T2): qlc 奖级修正——三等浮动/四等 200/七等仅 4+0"
```

---

### Task 3: qxc 修正（任意对位计数语义 + 2020 新规金额 + 补漏判档）（TDD）

**Files:**
- Modify: `app/domain/compare.py:94-123`（QxcHybridCompare）
- Modify: `app/domain/prize_tables.py:69-78`（qxc 表）
- Modify: `tests/domain/test_qxc_compare.py`（按新语义重写）
- Modify: `tests/domain/test_prize_tables.py:55`（EXPECTED_FIXED_CENTS['qxc']）
- Modify: `docs/reference/lottery-rules.md`（qxc 节）
- Modify: `docs/reference/lottery-verification-2026-08-14.md`（qxc 明细节）

**Interfaces:**
- Consumes: T0 基线；`_match_tier`（按 tier 升序匹配第一个 condition 命中）。
- Produces: `QxcHybridCompare.compare` 的 `front_hit` 语义 = 前区 6 位**任意位置对位命中数**（0-6，不再要求连续/前缀）；qxc 奖级表 = 2020 新规。

关键正确性说明（写进代码 docstring）：官方 2020 规则第三~六等为「投注号码中任意 N 个数字与开奖号码**对应位置**数字相同」——按位对号入座、不要求位置相邻。`front_hit`（前区对位数）+ `back_hit`（后区 0/1）即可表达全部条件：任意 5 位 = (5,0) 或 (4,1)；任意 4 位 = (4,0) 或 (3,1)；任意 3 位 = (3,0) 或 (2,1)；另有 (1,1) 与 (0,1) 两个六等特款。

- [ ] **Step 1: RED——重写 `tests/domain/test_qxc_compare.py` 全文件**

```python
# tests/domain/test_qxc_compare.py
"""QxcHybridCompare 测试（Plan 10 / T3 重写）。

依据：7星彩 2020-10-11 新规——「任意 N 位对位一致」（按位对号、不要求连续），
非旧版「连续 N 位」。固定档 3000/500/30/5 元（lottery.gov.cn 规则第二十二条，
2026-08-14 核对；旧版 1800/300/20 已作废）。
"""

from app.domain.compare import QxcHybridCompare


def test_qxc_first_prize():
    """前区 6 位全对 + 后区对 = 一等（浮动）。"""
    r = QxcHybridCompare.compare(
        lottery='qxc',
        draw_front=(1, 2, 3, 4, 5, 6),
        draw_back=(7,),
        combo_front=(1, 2, 3, 4, 5, 6),
        combo_back=(7,),
    )
    assert r.is_win and r.tier == 1 and r.amount is None


def test_qxc_second_prize():
    r = QxcHybridCompare.compare('qxc', (1, 2, 3, 4, 5, 6), (7,), (1, 2, 3, 4, 5, 6), (8,))
    assert r.is_win and r.tier == 2 and r.amount is None


def test_qxc_third_prize_any_5_plus_back():
    """前区任意 5 位对位 + 后区对 = 三等 3000 元。对位不要求连续：
    第 2 位错、其余 5 位对，照样三等。"""
    r = QxcHybridCompare.compare('qxc', (1, 2, 3, 4, 5, 6), (7,), (1, 9, 3, 4, 5, 6), (7,))
    assert r.is_win and r.tier == 3 and r.amount == 300000


def test_qxc_fourth_prize_any_5():
    """任意 5 位对位（含 4 前+后区）= 四等 500 元。"""
    assert QxcHybridCompare.compare('qxc', (1, 2, 3, 4, 5, 6), (7,), (1, 9, 3, 4, 5, 6), (8,)).amount == 50000  # 5+0
    assert QxcHybridCompare.compare('qxc', (1, 2, 3, 4, 5, 6), (7,), (1, 9, 3, 9, 5, 6), (7,)).amount == 50000  # 4+1


def test_qxc_fifth_prize_any_4():
    """任意 4 位对位 = 五等 30 元。"""
    # 4+0：第 0/2/3/4 位命中，后区不中
    assert QxcHybridCompare.compare('qxc', (1, 2, 3, 4, 5, 6), (7,), (1, 9, 3, 4, 5, 9), (8,)).amount == 3000
    # 3+1：第 0/2/4 位命中 + 后区中
    assert QxcHybridCompare.compare('qxc', (1, 2, 3, 4, 5, 6), (7,), (1, 9, 3, 9, 5, 9), (7,)).amount == 3000


def test_qxc_sixth_prize_all_forms():
    """六等 5 元：3+0 / 2+1 / 1+1 / 0+1（旧实现漏判后三种——静默漏中奖）。"""
    draw_f, draw_b = (1, 2, 3, 4, 5, 6), (7,)
    # 3+0：第 0/2/4 位命中，后区不中
    assert QxcHybridCompare.compare('qxc', draw_f, draw_b, (1, 9, 3, 9, 5, 9), (8,)).amount == 500
    # 2+1：第 0/5 位命中 + 后区中
    assert QxcHybridCompare.compare('qxc', draw_f, draw_b, (1, 9, 9, 9, 9, 6), (7,)).amount == 500
    # 1+1：仅第 0 位命中 + 后区中
    assert QxcHybridCompare.compare('qxc', draw_f, draw_b, (1, 9, 9, 9, 9, 9), (7,)).amount == 500
    # 0+1：仅后区中
    r = QxcHybridCompare.compare('qxc', draw_f, draw_b, (9, 9, 9, 9, 9, 9), (7,))
    assert r.is_win and r.tier == 6 and r.amount == 500


def test_qxc_no_win():
    # 0+0：逐位全错、后区不中
    r = QxcHybridCompare.compare('qxc', (1, 2, 3, 4, 5, 6), (7,), (9, 8, 7, 9, 9, 9), (8,))
    assert not r.is_win
    # 2+0 / 1+0 不中奖
    assert not QxcHybridCompare.compare('qxc', (1, 2, 3, 4, 5, 6), (7,), (1, 2, 9, 9, 9, 9), (8,)).is_win
```

`EXPECTED_FIXED_CENTS` 的 qxc 行改为：

```python
    'qxc': {3: 300000, 4: 50000, 5: 3000, 6: 500},
```

注释块 qxc 行改为 `#   qxc 三等3000 / 四等500 / 五等30 / 六等5 元（2020-10 新规，任意对位计数）`。

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/domain/test_qxc_compare.py tests/domain/test_prize_tables.py -v`
Expected: FAIL（旧前缀语义下 `test_qxc_third_prize_any_5_plus_back`、六等后三例、金额全部不符）

- [ ] **Step 3: GREEN——改 `QxcHybridCompare` + qxc 表**

`app/domain/compare.py` 的 `QxcHybridCompare` 类整体替换为：

```python
class QxcHybridCompare(CompareStrategy):
    """七星彩混合型：前区 6 位按位对号计数 + 后区单值 0-14。

    front_hit = 前区 6 位中**任意位置对位命中数**（0-6，按位对号入座、不要求连续）；
    back_hit = 后区单值是否命中（0/1）。

    规则依据（2026-08-14 核对 lottery.gov.cn 7星彩规则第二十二条，2020-10-11 起施行）：
    三至六等为「投注号码中任意 N 个数字与开奖号码对应位置数字相同」——任意位、非连续。
    旧实现曾用「首位起前缀连续命中」近似并注释「Phase 2 校准」，本次按官方规则修正；
    同时补上了旧实现漏判的六等 2+1 / 1+1 / 0+1（静默漏中奖，违反「中奖永不静默漏通知」）。
    """

    @staticmethod
    def compare(lottery, draw_front, draw_back, combo_front, combo_back, *, append=False, **_kw) -> HitResult:
        # 前区：任意位置对位命中数（每位独立比较，位置敏感）
        front_hit = sum(1 for a, b in zip(draw_front, combo_front, strict=False) if a == b)
        # 后区：单值是否命中（draw_back/combo_back 均为单元素 tuple）
        back_hit = 1 if (combo_back and draw_back and combo_back[0] == draw_back[0]) else 0

        tier = _match_tier(lottery, front_hit=front_hit, back_hit=back_hit)
        if tier is None:
            return HitResult(front_hit, back_hit, None, None, is_win=False)
        return HitResult(front_hit, back_hit, tier.tier, tier.amount, is_win=True)
```

`compare.py` 文件头 docstring 中 `QxcHybridCompare: 七星彩（T8）` 一行改为 `QxcHybridCompare: 七星彩（前区任意对位计数，2020 新规）`。

`prize_tables.py` 的 qxc 表替换为：

```python
    # 七星彩（2020-10-11 新规：任意 N 位对位计数，非连续；front_hit=前区对位数、back_hit=后区命中）：
    # 三等 3000 / 四等 500 / 五等 30 / 六等 5 元（lottery.gov.cn 规则第二十二条，2026-08-14 核对）。
    'qxc': [
        PrizeTier(1, 'front_hit==6 and back_hit==1', None, _V),
        PrizeTier(2, 'front_hit==6 and back_hit==0', None, _V),
        PrizeTier(3, 'front_hit==5 and back_hit==1', 300000, _F),
        PrizeTier(4, '(front_hit==5 and back_hit==0) or (front_hit==4 and back_hit==1)', 50000, _F),
        PrizeTier(5, '(front_hit==4 and back_hit==0) or (front_hit==3 and back_hit==1)', 3000, _F),
        PrizeTier(
            6,
            '(front_hit==3 and back_hit==0) or (front_hit==2 and back_hit==1) or (front_hit==1 and back_hit==1) or (front_hit==0 and back_hit==1)',
            500,
            _F,
        ),
    ],
```

- [ ] **Step 4: 回归**

Run: `uv run pytest tests/domain/ -q && uv run pytest -q`
Expected: 全绿（T0 基线必须仍绿）。旧断言失败按断言更新纪律处理（qxc 语义变更可能影响 services 层用例——确认其编码旧前缀语义后更新）。

- [ ] **Step 5: 更新 lottery-rules.md qxc 节 + 核对报告**

qxc 节（「### 七星彩 qxc」）整节替换为：

```markdown
### 七星彩 qxc（**⚠️ 2020-10-13 改版，旧规则已作废**）
- **现行**：前区 6 位（每位 0–9）+ 后区 1 位（**0–14**，共 15 个数字）。
- 旧规则（纯 7 位 0–9、连续对位）**已作废**，勿用。
- **中奖判定：任意 N 位对位一致（按位对号、不要求连续）**。玩法：单式/复式/倍投。

#### 奖级表（2020-10-11 起施行；lottery.gov.cn 规则第二十二条，2026-08-14 核对）

| 奖级 | 条件（前区对位数+后区） | 奖金 |
|---|---|---|
| 一等 | 6+1 | 浮动 |
| 二等 | 6+0 | 浮动 |
| 三等 | 任意 5 位+后区（5+1） | 3000 元 |
| 四等 | 任意 5 位（5+0 或 4+1） | 500 元 |
| 五等 | 任意 4 位（4+0 或 3+1） | 30 元 |
| 六等 | 任意 3 位（3+0 或 2+1）/ 1+1 / 0+1 | 5 元 |

- 注：旧版固定档（1800/300/20 元）与「连续对位」判定均已作废。
- 来源：中国体彩网 7星彩游戏规则（2026-08-14 核对）。
```

核对报告 qxc 明细节：

```markdown
### qxc（T3 完成）
- 号码结构（前区 6 位 0-9 + 后区 0-14）、开奖日 [1,4,6]——一致（2020 改版结构代码本已正确）。
- **发现**：① front_hit 用「首位起前缀连续命中」近似，官方实为「任意 N 位对位计数」；② 固定档误用旧版金额 1800/300/100/10（现行 3000/500/30/5）；③ 六等 2+1/1+1/0+1 三档漏判——中奖静默漏通知。
- **处置（B1代码修复，T3）**：QxcHybridCompare 改任意对位计数 + 表重写；`test_qxc_compare.py` 按新语义重写（含漏判档回归用例）。
- 来源：lottery.gov.cn 规则第二十二条（2026-08-14 核对）。
```

- [ ] **Step 6: 提交**

```bash
git add app/domain/compare.py app/domain/prize_tables.py tests/domain/test_qxc_compare.py tests/domain/test_prize_tables.py docs/reference/lottery-rules.md docs/reference/lottery-verification-2026-08-14.md
git commit -m "feat(plan-10/T3): qxc 修正为 2020 新规任意对位计数 + 补六等漏判档（静默漏中奖修复）"
```

---

### Task 4: fc3d danxuan 打通 + API 玩法校验 + UI 玩法裁剪（TDD）

**Files:**
- Modify: `app/domain/entry.py:44-50`（`_count_combos` 接受 danxuan；新增 `IMPLEMENTED_PLAY_TYPES`）
- Modify: `app/api/tickets.py`（create/update 增玩法校验）
- Modify: `web/src/lib/lotteries.ts:100-102`（fc3d→[danxuan]、pl3→[zhixuan]）
- Modify: `web/src/lib/lotteries.test.ts:97-106`（期望更新）
- Test: `tests/domain/test_entry_expand.py`（增 danxuan 例）、`tests/api/test_tickets.py`（增 3 例）
- Modify: `docs/reference/lottery-rules.md`（fc3d/pl3/pl5 节增「已实现范围」注记）
- Modify: `docs/reference/lottery-verification-2026-08-14.md`（fc3d/pl3/pl5 明细节）

**Interfaces:**
- Consumes: `Entry` / `_count_combos`；`app/api/tickets.py` 的 `create_ticket`/`update_ticket`（`HTTPException` 已导入）；`tests/api/test_tickets.py` 的 `_seed_ssq`/`_make_user`/`_auth_csrf_client` 辅助函数。
- Produces: `app.domain.entry.IMPLEMENTED_PLAY_TYPES: frozenset[str]`（`{'single', 'zhixuan', 'danxuan'}`）——API 层 import 使用（api→domain 方向合法，import-linter 不禁止）；danxuan 票可正常展开比对（fc3d 单选 1040 元档）；未实现玩法在建票/改票时被 400 明确拒绝（不再静默跳过）。

- [ ] **Step 1: RED——entry 测试**

`tests/domain/test_entry_expand.py` 追加：

```python
def test_danxuan_expands_as_single():
    """fc3d 单选（danxuan）= 每注一组 3 位号码，展开 1 注（Plan 10 / T4）。

    旧实现 _count_combos 只接受 single/zhixuan → danxuan 抛 NotImplementedError，
    比对层 per-ticket 隔离静默跳过——fc3d 票永不比对（静默漏中奖）。
    """
    from app.domain.entry import Entry, expand

    e = Entry(lottery_code='fc3d', play_type='danxuan', front=(1, 2, 3), back=None)
    combos = expand(e)
    assert len(combos) == 1
    assert combos[0].front == (1, 2, 3)


def test_implemented_play_types_exported():
    from app.domain.entry import IMPLEMENTED_PLAY_TYPES

    assert IMPLEMENTED_PLAY_TYPES == frozenset({'single', 'zhixuan', 'danxuan'})


def test_unimplemented_play_type_still_raises():
    """组选/复式等未实现玩法在领域层仍显式拒绝（不估算、不静默）。"""
    import pytest

    from app.domain.entry import Entry, expand

    e = Entry(lottery_code='pl3', play_type='zuxuan3', front=(1, 1, 2), back=None)
    with pytest.raises(NotImplementedError):
        expand(e)
```

- [ ] **Step 2: RED——API 测试（`tests/api/test_tickets.py` 追加）**

```python
def _seed_fc3d(db_engine):
    with Session(db_engine) as s:
        s.add(
            LotteryType(
                code='fc3d',
                name='福彩3D',
                category='welfare',
                spec_json='{}',
                draw_schedule_json='{}',
            )
        )
        s.commit()


_FC3D_TICKET = {
    'lottery_code': 'fc3d',
    'play_type': 'danxuan',
    'numbers_json': '{"front":[1,2,3]}',
    'cost': 200,
}


def test_create_fc3d_danxuan_accepted(db_engine):
    """fc3d 单选（danxuan）是已实现玩法——建票 201（Plan 10 / T4）。"""
    _seed_fc3d(db_engine)
    uid = _make_user(db_engine, 'bob')
    client = _auth_csrf_client(db_engine, uid)
    r = client.post('/tickets', json=_FC3D_TICKET)
    assert r.status_code == 201, r.text


def test_create_unimplemented_play_type_rejected_400(db_engine):
    """未实现玩法（组选三）建票 → 400 明确拒绝，不得入库后比对静默跳过。"""
    _seed_fc3d(db_engine)
    uid = _make_user(db_engine, 'carol')
    client = _auth_csrf_client(db_engine, uid)
    r = client.post('/tickets', json={**_FC3D_TICKET, 'play_type': 'zuxuan3'})
    assert r.status_code == 400, r.text
    assert '尚未实现' in r.json()['detail']


def test_update_to_unimplemented_play_type_rejected_400(db_engine):
    """改票改成未实现玩法同样 400。"""
    _seed_fc3d(db_engine)
    uid = _make_user(db_engine, 'dave')
    client = _auth_csrf_client(db_engine, uid)
    tid = client.post('/tickets', json=_FC3D_TICKET).json()['id']
    r = client.patch(f'/tickets/{tid}', json={'play_type': 'zuxuan6'})
    assert r.status_code == 400, r.text
```

- [ ] **Step 3: 跑测试确认失败**

Run: `uv run pytest tests/domain/test_entry_expand.py -v -k danxuan or -k implemented or -k unimplemented; uv run pytest tests/api/test_tickets.py -v -k fc3d or -k unimplemented`
Expected: FAIL（danxuan 抛 NotImplementedError / API 无校验）

- [ ] **Step 4: GREEN——`app/domain/entry.py`**

`_count_combos` 上方加常量，函数改为：

```python
# 已实现玩法（B1 边界）：每注一组号码、展开 1 注即可比对的玩法。
# 分区型 single（单式）；按位型 zhixuan（直选，pl3/pl5）/ danxuan（单选，fc3d）。
# 未实现玩法（fushi/dantuo/zuxuan3/zuxuan6/...）在 API 层被 400 拒绝（plan-10/T4），
# 领域层 _count_combos 显式 NotImplementedError 兜底——两层都不允许「建了票却静默不比对」。
IMPLEMENTED_PLAY_TYPES = frozenset({'single', 'zhixuan', 'danxuan'})


def _count_combos(e: Entry) -> int:
    """展开后的单式注数（用于 cost/上限校验）。
    已实现玩法（single/zhixuan/danxuan）=1（准确）。fushi/dantuo/zuxuan 等需组合展开，
    B2 实现——拒绝估算（硬编码会算错），显式抛错而非静默。"""
    if e.play_type in IMPLEMENTED_PLAY_TYPES:
        return 1
    raise NotImplementedError(f'{e.play_type} 展开注数需组合展开（B2 roadmap）；已实现玩法仅 {sorted(IMPLEMENTED_PLAY_TYPES)}')
```

`expand()` docstring 中「MVP：single/zhixuan 返回自身一注」改为「已实现玩法（single/zhixuan/danxuan）返回自身一注」。

`app/api/tickets.py`：顶部 import 加 `from app.domain.entry import IMPLEMENTED_PLAY_TYPES`，并加校验函数 + 两处调用：

```python
def _validate_play_type_implemented(play_type: str) -> None:
    """未实现玩法 400 拒绝（plan-10/T4）：允许建票却在比对层抛 NotImplementedError
    会被 per-ticket 隔离静默跳过——票永不比对、中奖永不通知（silent-failure 红线）。
    宁可在建票时明确拒绝。"""
    if play_type not in IMPLEMENTED_PLAY_TYPES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f'玩法 {play_type} 尚未实现（当前支持：{"、".join(sorted(IMPLEMENTED_PLAY_TYPES))}）；'
            f'复式/胆拖/组选等见 README Roadmap',
        )
```

`create_ticket` 在 `TicketRepo(...).create(...)` 前调 `_validate_play_type_implemented(body.play_type)`；`update_ticket` 在 `fields = body.model_dump(exclude_none=True)` 后加：

```python
    if 'play_type' in fields:
        _validate_play_type_implemented(fields['play_type'])
```

`web/src/lib/lotteries.ts`：`fc3d: ["danxuan", "zuxuan3", "zuxuan6"]` 改为 `fc3d: ["danxuan"]`；`pl3: ["zhixuan", "zuxuan3", "zuxuan6"]` 改为 `pl3: ["zhixuan"]`（未实现玩法不再展示，避免用户建了被 400）。`PLAY_TYPE_LABELS` 中 zuxuan3/zuxuan6 条目保留（B2 恢复时直接用）。

`web/src/lib/lotteries.test.ts`：`getPlayTypes("fc3d")` 期望改为 `["danxuan"]`；`getPlayTypes("pl3")` 期望改为 `["zhixuan"]`（对应两个 it 块同步改名，如 `returns danxuan only for fc3d (B1 boundary)`）。

- [ ] **Step 5: 跑新测试 + 前端测试 + 全量回归**

Run: `uv run pytest tests/domain/test_entry_expand.py tests/api/test_tickets.py -q && (cd web && npm test) && uv run pytest -q`
Expected: 全绿。

- [ ] **Step 6: 文档与报告**

`docs/reference/lottery-rules.md`：fc3d 节末尾加「**已实现范围**：仅单选（其余 11 种玩法 → B2 roadmap）；pl3 节加「**已实现范围**：仅直选（组选三/六 → B2）」；pl5 节加「**已实现范围**：仅直选单式（定位/组合复式 → B2）」。

核对报告补 fc3d/pl3/pl5 明细节：

```markdown
### fc3d / pl3 / pl5（T4 完成）
- fc3d：号码结构/开奖日/单选 1040 元一致。**发现**：danxuan 票可建但 `_count_combos` 不接受 → 比对静默跳过（永不比对）。**处置（B1代码修复，T4）**：danxuan 纳入 `IMPLEMENTED_PLAY_TYPES`（展开 1 注）；API 建/改票对未实现玩法 400 拒绝；UI 裁剪未实现玩法选项。其余 11 玩法（组选三/六/1D/2D/通选/和数/包选/猜大小/猜三同/拖拉机/猜奇偶）→ B2。
- pl3：直选 1040 元一致；组选三/六未实现 → T4 API 拦截 + UI 裁剪 + B2。
- pl5：直选 10 万/注一致；定位/组合复式未实现 → B2（play_types 本只声明 zhixuan，无需拦截）。
```

- [ ] **Step 7: 提交**

```bash
git add app/domain/entry.py app/api/tickets.py web/src/lib/lotteries.ts web/src/lib/lotteries.test.ts tests/domain/test_entry_expand.py tests/api/test_tickets.py docs/reference/lottery-rules.md docs/reference/lottery-verification-2026-08-14.md
git commit -m "feat(plan-10/T4): fc3d danxuan 打通比对 + 未实现玩法 API 400 拦截（杜绝建票后静默不比对）"
```

---

### Task 5: lottery-rules.md 结构化收尾（2026 新规章节 + 来源日期 + 已实现范围总注）

**Files:**
- Modify: `docs/reference/lottery-rules.md`

**Interfaces:**
- Consumes: T1–T4 已追加的各彩种奖级表。
- Produces: 规则文档成为「2026-08 现行规则」的完整权威参考。

- [ ] **Step 1: 文档首部「总表」下方插入 2026 新规警示节**

在 `## 总表` 表格之后、`## 重点规则细节` 之前插入：

```markdown
## ⚠️ 2026 年规则变更（2026-02-01 起施行，核对于 2026-08-14）

财政部财综〔2025〕50 号（双色球）/ 51 号（大乐透）批准规则变更，为近 30 年最大调整：

- **大乐透**（第 26014 期，2026-01-31 起）：9 档并 7 档（详见 dlt 节奖级表）；一二等单期总额各封顶 1 亿元；固定档随奖池（≥8 亿）上浮约 10–40%。
- **双色球**（第 2026014 期，2026-02-01 起）：固定档不变（3000/200/10/5 元）；新增「福运奖」——奖池 ≥15 亿元时，单注中 3 个红球（3+0）也得 5 元（奖池 <3 亿时停执行）；一等奖单期总额封顶 1 亿、二等奖封顶 7000 万。
- 其余彩种（qlc/fc3d/qxc/pl3/pl5）本次未调整；qxc 仍按 2020-10 改版规则。

**本仓库实现状态**：dlt 七档新规已实现（基础档金额）；ssq 福运奖与 dlt 上浮档依赖奖池数据，**未实现**（B2 roadmap）——对应场景按「不中奖 / 基础金额」处理，已在 README 能力边界声明。
```

- [ ] **Step 2: 总表「玩法」列加已实现范围脚注**

总表下方加一行注：

```markdown
> **本仓库已实现玩法**（B1 边界，2026-08-14）：分区型仅单式、按位型仅直选/单选（每注一组号码逐注比对）；复式/胆拖/组选/定位复式等组合玩法未实现（API 层 400 拒绝，见 `app/domain/entry.py` 的 `IMPLEMENTED_PLAY_TYPES`）。
```

- [ ] **Step 3: 「权威来源」节补充 2026 来源**

「权威来源」节的官方列表顶部追加：

```markdown
- 财政部 - 双色球/大乐透规则变更审批（财综〔2025〕50 号、51 号，2026-01-16 公告）：mof.gov.cn + 体彩/福彩中心公告
- 中国体彩网 - 7星彩游戏规则（2020 改版现行）：https://www.lottery.gov.cn/bzzx/yxgz/20191119/1002857.html（2026-08-14 核对：任意对位计数；3000/500/30/5 元）
- 深圳福彩中心 - 七乐彩规则页（2026-08-14 核对：一二三等浮动；200/50/10/5 元）：https://www.szlottery.org/fcw/wfjs/qlc/jx/index.html
```

- [ ] **Step 4: 一致性校对**

Run: `grep -n "1800\|3045\|连续命中\|前缀" docs/reference/lottery-rules.md`
Expected: 无残留旧规则表述（除「已作废」说明性引用）。

Run: `bash scripts/publish-check.sh --grep-only && uv run pytest -q`
Expected: 全绿。

- [ ] **Step 5: 提交**

```bash
git add docs/reference/lottery-rules.md
git commit -m "feat(plan-10/T5): lottery-rules.md 补 2026 新规章节 + 全量奖级表来源日期 + 已实现范围总注"
```

---

### Task 6: 全量回归 + 测试数更新 + 报告收尾

**Files:**
- Modify: `CLAUDE.md`（项目状态测试数）
- Modify: `docs/reference/lottery-verification-2026-08-14.md`（收尾结论）

- [ ] **Step 1: 全量回归（三道）**

Run: `uv run ruff check . && uv run lint-imports && uv run pytest -q`
Expected: 全绿。记录 pytest 实际通过数。

Run: `cd web && npm test && npm run build`
Expected: 全绿。

- [ ] **Step 2: CLAUDE.md 测试数更新**

「项目状态」行的测试数替换为 Step 1 实测值（如 `659+N tests green`）。

- [ ] **Step 3: 报告收尾**

核对报告「汇总表」下追加结论段：

```markdown
## 结论（2026-08-14，B1 完成）

- 4 项 B1 代码修复全部 TDD 落地：dlt 2026 七档新规（修 1+1/2+0/0+1 误判中奖）、qlc 浮动档/金额、qxc 任意对位语义 + 六等漏判档（静默漏中奖）、fc3d danxuan 打通 + 未实现玩法 API 拦截。
- ssq 生产基线（2026093 期真实夹具）全程绿。
- 已实现面单元测试补齐：每彩种每个已实现奖级 ≥1 命中用例 + 不中奖边界用例。
- 未实现项全部文档降级声明（README 能力边界 + lottery-rules.md 注记）并列 B2 roadmap，无过度宣称。
- 全量回归绿（后端 pytest + 前端 vitest + build）。
```

- [ ] **Step 4: 提交**

```bash
git add CLAUDE.md docs/reference/lottery-verification-2026-08-14.md
git commit -m "feat(plan-10/T6): 全量回归绿 + 核对报告收尾（B1 完成）"
```

- [ ] **Step 5: 回到 plan-09 执行 T11（首推 GitHub）与 T12（发布后验证）**

---

## Self-Review 记录（plan 落盘前已执行）

- **Spec 覆盖**：§4.1 核对五维度（结构/开奖日/玩法/特殊规则/奖金表）→ 报告汇总表五列 + T0–T4 逐彩种；§4.2 ssq 基线先行 → T0；TDD 修复 → T1–T4；文档降级 → 报告 + lottery-rules.md 注记 + README（plan-09/T5 已按此终态写）；补测 → 各任务新测试文件；全量回归 → T6；qxc 保守回退（E7）→ 被更强处置取代：官方规则已核实，直接修正语义而非保留近似（E7 的「近似+标注」是在未核实官方规则时的保守方案，现已拿到规则原文）。
- **类型一致性**：`IMPLEMENTED_PLAY_TYPES`（frozenset，entry.py 定义）在 API/测试引用一致；`HitResult`/`PrizeTier` 字段名与现有代码一致；`get_tiers` 签名未动。
- **Placeholder 扫描**：无 TBD/TODO；所有测试与实现代码均为完整可运行内容；夹具为真实官方数据（2026093 期，2026-08-14 抓取）。
