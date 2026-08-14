# Plan 10：7 彩种「文档 vs 代码」核对与 B1 修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 逐彩种核对 `docs/reference/lottery-rules.md` vs `app/domain/` 实现，按 B1 范围处置：小差异 TDD 修复（dlt 2026 七档新规 + 规则版本门 / qlc 浮动档与金额 + refill 浮动集动态化 / qxc 任意对位语义与漏判 / fc3d danxuan 静默不比对 / recompare 存量重算 CLI）、未实现玩法诚实降级为文档声明 + B2 roadmap、已实现面补齐单元测试，产出带处置列的核对报告。

**Architecture:** 领域层数据/语义修正（`prize_tables.py` 版本化 + `compare.py` draw_date 透传 + `entry.py` 玩法白名单）+ refill worker 浮动集动态化 + API 边界玩法校验（`app/api/tickets.py`）+ 前端玩法列表裁剪（`web/src/lib/lotteries.ts`）+ `recompare` CLI（存量行重算）。ssq 生产回归基线夹具先行（T0），之后每个彩种一个任务、独立 TDD 循环。不改表结构、不改适配器、不改调度。

**Tech Stack:** Python 3.12 / pytest / FastAPI TestClient / vitest。**含 1 项评审新增任务（T6 recompare CLI，用户裁决）与 1 项范围例外（T1 规则版本门，eng-review Issue 3 用户裁决）。**

**Spec:** `docs/superpowers/specs/2026-08-14-open-source-release-design.md` §4（B1 范围，autoplan 终审 2026-08-14 确认）

**前置事实（2026-08-14 官方源核实，写 plan 时已查证）：**

- **大乐透 2026 新规已生效**（财综〔2025〕51 号，2026-01-31 第 26014 期起）：9 档并 **7 档**——新三等 = 原(5+0)+(4+2) = 5000 元；新四等 = 4+1 = 300 元；新五等 = 原(4+0)+(3+2) = 150 元；新六等 = 3+1 或 2+2 = 15 元；新七等 = 3+0/1+2/2+1/0+2 = 5 元；一二等浮动、追加 80% 不变；奖池 ≥8 亿时固定档上浮（6666/380/200/18/7）。**代码现表把 2019 金额贴在错误的合并条件上，且把不中奖的 1+1/2+0/0+1 判成七等 100 元**——必须重写。
- **七星彩 2020 新规**（2020-10-13 起）：「**任意 N 位对位一致**」计数，非连续。三/四/五/六等 = 3000/500/30/5 元；六等含 3+0、2+1、1+1、0+1。**代码用「首位起前缀连续命中」近似 + 旧版金额 1800/300/100/10，且漏判 2+1/1+1/0+1**——静默漏中奖，必须修。
- **七乐彩**：一二三等皆浮动（70%/10%/20%）；四~七等固定 200/50/10/5 元；七等仅 4+0。**代码把三等误录固定 3045 元、四等误录 300 元、七等多判 3+1**。
- **双色球 2026 新规**：固定档不变（2026-08-13 第 2026093 期官方公告实测 3000/200/10/5）；新增「福运奖」（奖池 ≥15 亿时 3+0 也得 5 元，<3 亿停执行）依赖奖池数据 → B2。
- **fc3d**：UI 可建 `danxuan` 票，但 `app/domain/entry.py:48` 只接受 single/zhixuan → 比对抛 NotImplementedError 被 per-ticket 隔离**静默跳过**，fc3d 票永不比对。pl3 的 zuxuan3/6 同理可建不可比对。
- 金额单位「分」纪律（2026-08-03 事故）：所有固定档 = 官方元 ×100。

## Global Constraints

- **B1 范围红线**：只做「小差异代码修复 + 文档降级 + 已实现面补测」。**不实现**复式/胆拖/组选三六/fc3d 其余玩法/pl5 定位组合复式/ssq 福运奖/dlt 奖池上浮档（全部 B2 roadmap）。**唯一例外（eng-review Issue 3 用户裁决）**：dlt 规则版本门——`get_tiers` 按开奖日路由 2019/2026 双版本表，属正确性修复而非新玩法。
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

### Task 1: dlt 修正为 2026 七档新规 + 规则版本门（TDD）

**Files:**
- Modify: `app/domain/prize_tables.py`（版本化结构 `_VERSIONED_TABLES` + dlt 2019/2026 双版本表 + `get_tiers` 加 `draw_date` 形参）
- Modify: `app/domain/compare.py`（`_match_tier` 加 draw_date；三个策略 `compare` 签名加 `draw_date=None`；领域入口 `compare()` 加 `draw_date=None` 透传）
- Modify: `app/services/compare_service.py:148-153`（`domain_compare(...)` 调用加 `draw_date=dr.draw_date`）
- Modify: `app/services/refill_service.py:157,187`（`_find_tier` 加 draw_date 透传）
- Modify: `tests/domain/test_prize_tables.py:53`（EXPECTED_FIXED_CENTS['dlt']）
- Create: `tests/domain/test_dlt_2026_rules.py`
- Modify: `tests/services/test_compare_service.py`（增 1 例 draw_date 透传 spy 测试）
- Modify: `docs/reference/lottery-rules.md`（dlt 节：2019 九档 + 2026 七档双表）
- Modify: `docs/reference/lottery-verification-2026-08-14.md`（dlt 明细节）

**Interfaces:**
- Consumes: `PrizeTier(tier, condition, amount, amount_type, append_multiplier=1.0)`；T0 基线。`DrawResult.draw_date` 类型为 `datetime`（`app/models/draw.py:14`）。
- Produces（后续任务与调用方依赖的确切签名）：
  - `get_tiers(lottery_code: str, draw_date: date | datetime | None = None) -> list[PrizeTier]`——`None` = 现行（最新）版本；传日期则返回「生效日 ≤ draw_date 的最新版本」。T2/T3 改表时**不得**改变此签名。
  - `compare(spec, *, draw_front, draw_back, entry, draw_date=None) -> list[HitResult]`——领域入口新增可选 `draw_date` 并透传策略。
  - 策略签名变为 `compare(lottery, draw_front, draw_back, combo_front, combo_back, *, append, draw_date=None)`（PositionalCompare 同样接收，仅传给 `_match_tier`）。

设计要点（eng-review Issue 3 用户裁决：现在就做规则版本门）：奖级表改为按生效日版本化，重比历史期按**当时**规则判定。dlt 2019 九档表采用**官方正确版**（现行代码里的旧表是合并条件贴错金额的错误表，不作为历史版本保留）。

- [ ] **Step 1: RED——改 `EXPECTED_FIXED_CENTS` + 写条件/版本测试**

`tests/domain/test_prize_tables.py` 的 `EXPECTED_FIXED_CENTS` 中 dlt 行改为（`get_tiers` 不传日期 = 现行 2026 表）：

```python
    'dlt': {3: 500000, 4: 30000, 5: 15000, 6: 1500, 7: 500},
```

注释块 dlt 行改为 `#   dlt 三等5000 / 四等300 / 五等150 / 六等15 / 七等5 元（2026-02-01 新规，财综〔2025〕51 号；2019 九档经 draw_date 版本门保留）`。

新建 `tests/domain/test_dlt_2026_rules.py`：

```python
# tests/domain/test_dlt_2026_rules.py
"""dlt 2026 新规（9 档并 7 档）+ 规则版本门测试（Plan 10 / T1）。

依据：财综〔2025〕51 号 + 体彩中心公告（2026-01-16），第 26014 期（2026-01-31）起执行。
合并规则：原(5+0)+(4+2)→新三等；原(4+0)+(3+2)→新五等；1+1/2+0/0+1 不中奖。
金额为奖池 <8 亿基础档；≥8 亿上浮档（6666/380/200/18/7）需奖池数据，B2 roadmap。
版本门：2026-01-31 之前的开奖日按 2019 九档表判定（eng-review Issue 3）。
"""

from datetime import date, datetime

import pytest

from app.domain.compare import PartitionCompare
from app.domain.prize_tables import get_tiers

DRAW_FRONT = (1, 2, 3, 4, 5)
DRAW_BACK = (6, 7)


def _dlt(front, back, append=False, draw_date=None):
    return PartitionCompare.compare(
        'dlt', DRAW_FRONT, DRAW_BACK, tuple(front), tuple(back),
        append=append, draw_date=draw_date,
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
    """现行表（不传 draw_date = 最新版本）。"""
    r = _dlt(front, back)
    assert r.is_win, f'{front}+{back} 应中 {tier} 等'
    assert r.tier == tier and r.amount == amount


@pytest.mark.parametrize(
    ('front', 'back'),
    [
        ((1, 9, 9, 9, 9), (6, 8)),   # 1+1 不中奖（旧错误表曾误判七等 100 元）
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


# —— 规则版本门（eng-review Issue 3）：历史期按当时规则判定 ——


def test_dlt_version_boundary_old_draw_uses_2019_table():
    """2026-01-30 及之前 → 2019 九档：4+2 = 四等 3000 元（新规下同号组合是三等 5000）。"""
    r = _dlt((1, 2, 3, 4, 9), (6, 7), draw_date=date(2026, 1, 30))
    assert r.is_win and r.tier == 4 and r.amount == 300000


def test_dlt_version_boundary_new_draw_uses_2026_table():
    """2026-01-31（第 26014 期开售）起 → 七档：4+2 = 三等 5000 元。"""
    r = _dlt((1, 2, 3, 4, 9), (6, 7), draw_date=date(2026, 1, 31))
    assert r.is_win and r.tier == 3 and r.amount == 500000


def test_dlt_2019_tier8_tier9_exist():
    """2019 表八等（3+1/2+2=15 元）与九等（3+0/1+2/2+1/0+2=5 元）。"""
    r8 = _dlt((1, 2, 3, 9, 9), (6, 8), draw_date=date(2025, 6, 1))
    assert r8.is_win and r8.tier == 8 and r8.amount == 1500
    r9 = _dlt((9, 9, 9, 9, 9), (6, 7), draw_date=date(2025, 6, 1))
    assert r9.is_win and r9.tier == 9 and r9.amount == 500


def test_dlt_2019_five_plus_zero_is_tier3_10000():
    """2019 表三等 5+0 = 10000 元（与 2026 合并三等 5000 区分）。"""
    r = _dlt((1, 2, 3, 4, 5), (8, 9), draw_date=datetime(2026, 1, 15, 21, 30))
    assert r.is_win and r.tier == 3 and r.amount == 1000000
```

（最后一例故意传 `datetime`——`DrawResult.draw_date` 就是 datetime，版本门必须归一。）

`tests/services/test_compare_service.py` 追加 draw_date 透传 spy 测试（版本门的接线护栏——domain 默认值是现行表，若 service 不传 draw_date，历史期重比会静默用新表）：

```python
def test_compare_one_passes_draw_date_to_domain(db_engine, monkeypatch):
    """compare_service 必须把 dr.draw_date 透传给领域 compare（规则版本门接线）。

    若该线断开，draw_date=None → 领域层默认现行表 → 2026-01-31 前的历史期
    更正重比会按新规误判（eng-review Issue 3）。
    """
    import app.services.compare_service as cs_mod

    captured = {}
    real_compare = cs_mod.domain_compare

    def _spy(spec, *, draw_front, draw_back, entry, draw_date=None):
        captured['draw_date'] = draw_date
        return real_compare(spec, draw_front=draw_front, draw_back=draw_back, entry=entry, draw_date=draw_date)

    monkeypatch.setattr(cs_mod, 'domain_compare', _spy)
    # …准备数据沿用本文件既有 fixture 模式建一期 verified DrawResult + 一张 enabled ticket…
    # 触发 _compare_one 后：
    assert captured['draw_date'] is not None
```

（该例的准备数据代码沿用 `test_compare_service.py` 既有 `_seed_*` 辅助函数的写法；实现时照本文件现有用例的 fixture 组合补齐，断言点固定为 `captured['draw_date']` 等于该期 `draw_date`。）

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/domain/test_dlt_2026_rules.py tests/domain/test_prize_tables.py -v`
Expected: FAIL（`draw_date` 未知关键字 / 旧表条件金额不符）

- [ ] **Step 3: GREEN——版本化 `prize_tables.py`**

把 `PRIZE_TABLES` 直dict 改为「彩种常量 + 版本注册表」结构：

```python
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

_QLC = [ ... ]   # 现状 qlc 表原样保留（T2 再改——本任务只动 dlt 与版本结构）
_QXC = [ ... ]   # 现状 qxc 表原样保留（T3 再改）
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
```

`app/domain/compare.py` 改动三处：

```python
def _match_tier(lottery: str, front_hit: int, back_hit: int, draw_date=None) -> PrizeTier | None:
    """按奖级号升序匹配第一个 condition 命中的 tier（tier 1 最高，先试）。
    draw_date 透传 get_tiers 做规则版本路由（None=现行表）。"""
    for t in get_tiers(lottery, draw_date):
        if _eval_condition(t.condition, front_hit, back_hit):
            return t
    return None
```

- `CompareStrategy.compare` 接口签名改 `(lottery, draw_front, draw_back, combo_front, combo_back, *, append, draw_date=None)`；
- `PartitionCompare.compare` 加 `draw_date=None` 形参并 `_match_tier(lottery, front_hit, back_hit, draw_date)`；
- `QxcHybridCompare.compare` 同样加形参透传；
- `PositionalCompare.compare` 已有 `**_kw`——显式加 `draw_date=None` 并传给 `_match_tier`（显式优于埋进 _kw）；
- 领域入口 `compare(spec, *, draw_front, draw_back, entry, draw_date=None)`：两个分支的 `strategy.compare(...)` 调用都加 `draw_date=draw_date`；docstring 加一行「draw_date：开奖日，用于奖级表规则版本路由（None=现行表；compare_service 恒传 dr.draw_date）」。

`app/services/compare_service.py:148` 调用改为：

```python
                        results = domain_compare(
                            spec,
                            draw_front=draw_front,
                            draw_back=draw_back,
                            entry=entry,
                            draw_date=dr.draw_date,  # 规则版本门：历史期更正重比按当时规则判定
                        )
```

`app/services/refill_service.py`：`_find_tier` 签名改 `_find_tier(lottery_code: str, tier: int, draw_date=None)`，内部 `get_tiers(lottery_code, draw_date)`；调用点（:157）改 `self._find_tier(dr.lottery_code, cmp.prize_tier, dr.draw_date)`。

**实现注记（eng-review Issue 1，写进 T1 commit message）**：dlt tier 重编号后，refill 对**新三等浮动行**会调 sporttery `lookup_amount(tier=3)`；sporttery JSON 的 `prizeLevel` 编号若仍按旧九档（旧 5+0 三等），与新规 tier 3（5+0/4+2 合并）语义不同——上线后首个 dlt 新三等中奖行回填时须人工核对一笔金额是否与官方公告一致；若 sporttery 未切新编号，在 `sporttery_prize._extract_tier_amount` 加 dlt 专用映射（那是新任务，不在本 plan）。

- [ ] **Step 4: 跑新测试 + ssq 基线 + 全量回归**

Run: `uv run pytest tests/domain/ -q && uv run pytest -q`
Expected: 新测试全过、T0 基线绿、全量绿。若其他测试失败：逐条确认其编码的是旧错误规则（对照本任务前置事实）后更新，断言注释注明新规则出处；不确定就停下人工核对。`test_refill_service.py` 若 mock 了 `_find_tier` 双参调用，按新签名补第三参。

- [ ] **Step 5: 更新 `docs/reference/lottery-rules.md` 的 dlt 节**

在「### 大乐透 dlt」节末尾追加（**双版本表**）：

```markdown
#### 奖级表·2019 版（2019-02-20 第 19019 期 — 2026-01-30 开奖期；历史期重比适用）

| 奖级 | 条件（前区+后区） | 奖金 |
|---|---|---|
| 一等 | 5+2 | 浮动 |
| 二等 | 5+1 | 浮动 |
| 三等 | 5+0 | 10000 元 |
| 四等 | 4+2 | 3000 元 |
| 五等 | 4+1 | 300 元 |
| 六等 | 3+2 | 200 元 |
| 七等 | 4+0 | 100 元 |
| 八等 | 3+1 或 2+2 | 15 元 |
| 九等 | 3+0 / 1+2 / 2+1 / 0+2 | 5 元 |

#### 奖级表·2026 版（财综〔2025〕51 号，2026-01-31 第 26014 期起；现行）

| 奖级 | 条件（前区+后区） | 奖金 |
|---|---|---|
| 一等 | 5+2 | 浮动（基本单注封顶 1000 万；追加 80%，合计封顶 1800 万；单期总额封顶 1 亿） |
| 二等 | 5+1 | 浮动（追加同上；单期总额封顶 1 亿） |
| 三等 | 5+0 或 4+2 | 5000 元（奖池 ≥8 亿时 6666 元） |
| 四等 | 4+1 | 300 元（≥8 亿时 380 元） |
| 五等 | 4+0 或 3+2 | 150 元（≥8 亿时 200 元） |
| 六等 | 3+1 或 2+2 | 15 元（≥8 亿时 18 元） |
| 七等 | 3+0 / 1+2 / 2+1 / 0+2 | 5 元（≥8 亿时 7 元） |

- 1+1 / 2+0 / 0+1 两版均**不中奖**。追加仅一二等（80%）两版一致。
- 实现注记：`prize_tables._VERSIONED_TABLES` 按开奖日路由双版本；固定档按「奖池 <8 亿基础档」录入，≥8 亿上浮档需奖池数据 → B2 roadmap。
- 来源：财政部财综〔2025〕51 号 + 国家体育总局体彩中心公告（2026-01-16）；lottery.gov.cn 大乐透规则页（2026-08-14 核对）。
```

- [ ] **Step 6: 填核对报告 dlt 明细节**

把报告中「### dlt（T1 完成）」扩写为：

```markdown
### dlt（T1 完成）
- 号码结构 5/35+2/12、开奖日 [0,2,5]、追加标志（`append_multiplier=1.8` 仅一二等）——一致。
- **发现**：代码旧表把 2019 金额贴在错误的合并条件上（如 4+2 判 10000 元），并把不中奖的 1+1/2+0/0+1 判成七等 100 元（错误的中奖通知！）。
- **处置（B1代码修复，T1）**：① 重写为 2026 七档新规（条件+基础档金额）；② **规则版本门**（eng-review Issue 3 用户裁决）：`get_tiers` 加 draw_date 路由，2019 官方九档表作为历史版本保留，历史期更正重比按当时规则判定；compare_service/refill 全线透传开奖日；③ `test_dlt_2026_rules.py` 14 档条件 + 4 不中奖 + 4 版本边界用例锁定。
- **遗留**：奖池 ≥8 亿上浮档（B2，需奖池数据）；复式/胆拖（B2）；sporttery `prizeLevel` 新编号映射待首笔新三等回填时人工核对（T1 实现注记）。
- 来源：财综〔2025〕51 号；lottery.gov.cn（2026-08-14 核对）。
```

- [ ] **Step 7: 提交**

```bash
git add app/domain/prize_tables.py app/domain/compare.py app/services/compare_service.py app/services/refill_service.py tests/domain/test_prize_tables.py tests/domain/test_dlt_2026_rules.py tests/services/test_compare_service.py docs/reference/lottery-rules.md docs/reference/lottery-verification-2026-08-14.md
git commit -m "feat(plan-10/T1): dlt 2026 七档新规 + 规则版本门（get_tiers 按开奖日路由 2019/2026 双表；修 1+1/2+0/0+1 误判中奖）"
```

---

### Task 2: qlc 修正（三等浮动 / 四等 200 / 七等仅 4+0）（TDD）

**Files:**
- Modify: `app/domain/prize_tables.py` 的 `_QLC` 常量（T1 版本化重构后的彩种常量，非 PRIZE_TABLES 直dict 条目）
- Modify: `app/services/refill_service.py:79-93,200-210`（浮动 tier 过滤改为动态推导——eng-review 外部声音发现 2）
- Modify: `tests/domain/test_prize_tables.py:54`（EXPECTED_FIXED_CENTS['qlc']）
- Modify: `tests/services/test_refill_service.py`（增 qlc 三等浮动行被选中回填用例）
- Create: `tests/domain/test_qlc_rules.py`
- Modify: `docs/reference/lottery-rules.md`（qlc 节增奖级表）
- Modify: `docs/reference/lottery-verification-2026-08-14.md`（qlc 明细节）

**Interfaces:**
- Consumes: 同 T1（`PartitionCompare`，qlc 后区=特别号，`back_hit` 0/1）；`PRIZE_TABLES`（T1 版本化后的现行表兼容别名）；`AmountType.FLOAT`。
- Produces: 修正后 qlc 表；`refill_service._FLOAT_TIERS: frozenset[int]`——浮动 tier 集合的唯一事实源（两处查询复用）。qlc 三等改浮动后由 FloatRefillWorker 回填——`CwlPrizeSource` 已覆盖 qlc（`name=qlc` 直传 cwl API），适配器无改动。

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

`tests/services/test_refill_service.py` 追加（eng-review 外部声音发现 2：refill 硬过滤 `prize_tier IN (1,2)`，qlc 三等改浮动后永不回填——永久「待官方派奖」）：

```python
def test_refill_selects_qlc_third_float_tier(db_engine, ...):
    """qlc 三等（6+0，浮动）中奖行必须被 refill 选中——tier 过滤从奖级表动态推导，
    不得硬编码 (1,2)。构造：verified qlc DrawResult + Comparison(is_win, prize_tier=3,
    prize_amount=None) → refill 选中并对 cwl 发 lookup_amount('qlc', ..., tier=3)。"""
    # 准备数据与断言风格沿用本文件既有用例（mock amount_lookup / MockTransport），
    # 断言点：lookup_amount 被调用且 lottery_code='qlc'、tier=3；行被回填或保持待公布重试，
    # 但绝不被过滤漏掉（worker 日志/返回计数可见该行被处理）。
```

（该例的 fixture 组合照本文件现有 dlt 用例补齐；断言核心是「tier=3 的 qlc 浮动行进入处理集」。）

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/domain/test_qlc_rules.py tests/domain/test_prize_tables.py tests/services/test_refill_service.py -v`
Expected: FAIL（旧表不符；refill 新例中 tier=3 行被硬过滤漏掉）

- [ ] **Step 3: GREEN——重写 `_QLC` 常量 + refill 浮动 tier 动态化**

`_QLC` 常量：

```python
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
```

`app/services/refill_service.py`——两处 `Comparison.prize_tier.in_((1, 2))`（约 :89 主查询、:206 过期标记）改为动态推导（eng-review 外部声音发现 2：qlc 三等改浮动后硬编码 (1,2) 会让它永久「待官方派奖」——静默失败红线）。模块级加：

```python
from app.domain.prize import AmountType
from app.domain.prize_tables import PRIZE_TABLES

# 浮动奖级集合（单一事实源）：refill 主查询与过期标记共用的 tier 过滤。
# 从奖级表现行版本动态推导——新增浮动档（如 qlc 三等）无需改本模块。
# 注：跨规则版本的并集语义保守正确（多选行只会多回填尝试，不会漏）。
_FLOAT_TIERS = frozenset(
    t.tier
    for tiers in PRIZE_TABLES.values()
    for t in tiers
    if t.amount_type == AmountType.FLOAT
)
```

`:89` 与 `:206` 的 `Comparison.prize_tier.in_((1, 2))` 均改为 `Comparison.prize_tier.in_(_FLOAT_TIERS)`；`:79` 附近注释「显式限定 prize_tier IN (1,2) —— 仅浮动档」改为「显式限定 prize_tier IN _FLOAT_TIERS —— 仅浮动档（从奖级表动态推导；spec §7.1 的『一二等奖』是当时全部浮动档，qlc 三等自 plan-10/T2 起亦为浮动）」。

- [ ] **Step 4: 回归（含 refill 路径）**

Run: `uv run pytest tests/domain/ tests/services/test_refill_service.py tests/adapters/ -q && uv run pytest -q`
Expected: 全绿（qlc 三等浮动由 CwlPrizeSource `name=qlc` 回填；refill worker 经 `_FLOAT_TIERS` 选中 tier=3 行）。旧断言失败按 Global Constraints 断言更新纪律处理。

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
- **发现**：三等误录固定 3045 元（应浮动）；四等误录 300 元（应 200）；七等多判 3+1（不中奖）。**次生发现（eng-review）**：refill worker 硬编码 `prize_tier IN (1,2)`——三等改浮动后若不改，将永久「待官方派奖」。
- **处置（B1代码修复，T2）**：表重写 + `test_qlc_rules.py` 锁定；refill 两处过滤改 `_FLOAT_TIERS`（从奖级表动态推导的浮动档集合）+ qlc tier=3 回填回归用例；三等浮动走既有 CwlPrizeSource 回填（覆盖 qlc）。
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
- Modify: `app/domain/prize_tables.py` 的 `_QXC` 常量
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

依据：7星彩 2020-10-13 新规——「任意 N 位对位一致」（按位对号、不要求连续），
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

    规则依据（2026-08-14 核对 lottery.gov.cn 7星彩规则第二十二条，2020-10-13 起施行）：
    三至六等为「投注号码中任意 N 个数字与开奖号码对应位置数字相同」——任意位、非连续。
    旧实现曾用「首位起前缀连续命中」近似并注释「Phase 2 校准」，本次按官方规则修正；
    同时补上了旧实现漏判的六等 2+1 / 1+1 / 0+1（静默漏中奖，违反「中奖永不静默漏通知」）。

    draw_date 透传 _match_tier 做规则版本路由（qxc 现仅 2020 单版本，None=现行表；
    签名与 PartitionCompare/PositionalCompare 保持一致——eng-review 外部声音发现 3）。
    """

    @staticmethod
    def compare(lottery, draw_front, draw_back, combo_front, combo_back, *, append=False, draw_date=None, **_kw) -> HitResult:
        # 前区：任意位置对位命中数（每位独立比较，位置敏感）
        front_hit = sum(1 for a, b in zip(draw_front, combo_front, strict=False) if a == b)
        # 后区：单值是否命中（draw_back/combo_back 均为单元素 tuple）
        back_hit = 1 if (combo_back and draw_back and combo_back[0] == draw_back[0]) else 0

        tier = _match_tier(lottery, front_hit=front_hit, back_hit=back_hit, draw_date=draw_date)
        if tier is None:
            return HitResult(front_hit, back_hit, None, None, is_win=False)
        return HitResult(front_hit, back_hit, tier.tier, tier.amount, is_win=True)
```

`compare.py` 文件头 docstring 中 `QxcHybridCompare: 七星彩（T8）` 一行改为 `QxcHybridCompare: 七星彩（前区任意对位计数，2020 新规）`。

`prize_tables.py` 的 `_QXC` 常量替换为：

```python
# 七星彩（2020-10-13 新规：任意 N 位对位计数，非连续；front_hit=前区对位数、back_hit=后区命中）：
# 三等 3000 / 四等 500 / 五等 30 / 六等 5 元（lottery.gov.cn 规则第二十二条，2026-08-14 核对）。
_QXC = [
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
]
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

#### 奖级表（2020-10-13 起施行；lottery.gov.cn 规则第二十二条，2026-08-14 核对）

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

- [ ] **Step 7: 存量票核查（eng-review Issue 4——API 拦截只挡新票，存量坏票仍会静默跳过比对）**

对生产库跑只读 SQL（NAS 上 `docker compose exec app uv run python -c "..."` 或本地开发库）：

```sql
SELECT lottery_code, play_type, COUNT(*) FROM tickets WHERE enabled = 1 GROUP BY 1, 2;
```

- 若结果只含 `single`/`zhixuan`/`danxuan` → 在核对报告 fc3d/pl3/pl5 节补一句「存量票已核查，无未实现玩法票」。
- 若发现 `zuxuan3`/`zuxuan6`/`fushi`/`dantuo` 等 → 在报告记录彩种/玩法/数量，**逐张人工处置**（联系用户改玩法或删票），不得放任——这些票每期比对都被 per-ticket 隔离静默跳过，中奖永不通知。

- [ ] **Step 8: 提交**

```bash
git add app/domain/entry.py app/api/tickets.py web/src/lib/lotteries.ts web/src/lib/lotteries.test.ts tests/domain/test_entry_expand.py tests/api/test_tickets.py docs/reference/lottery-rules.md docs/reference/lottery-verification-2026-08-14.md
git commit -m "feat(plan-10/T4): fc3d danxuan 打通比对 + 未实现玩法 API 400 拦截（杜绝建票后静默不比对）"
```

---

### Task 5: lottery-rules.md 结构化收尾（2026 新规章节 + 来源日期 + 已实现范围总注）— 编号说明：原 T5/T6 顺移为 T5/T7，T6 为评审新增的 recompare CLI

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

### Task 6: `recompare` CLI——按新表重算存量 comparisons（eng-review 外部声音发现 1，用户裁决新增）

**Files:**
- Modify: `app/cli.py`（新增 `recompare` 子命令）
- Modify: `app/services/compare_service.py`（新增模块级 `recompare_all(engine) -> dict` 重比入口）
- Test: `tests/services/test_recompare.py`（新建）、`tests/test_cli_recompare.py`（新建）

**Interfaces:**
- Consumes: `CompareService._compare_one(draw_result_id)`（既有——幂等 upsert，`uq_cmp_draw_ticket` 原地更新 hits/tier/amount）；`DrawResult`（verified 过滤）；T1 的规则版本门（`draw_date` 路由——历史期重比自动用当时规则）；`PendingComparison` outbox（比对触发器）。
- Produces: `app.cli` 子命令 `recompare [--dry-run] [--lottery CODE]`——重比存量比对行；`recompare_all(engine, lottery_code=None, dry_run=False) -> dict`（返回 `{'draws': N, 'rows': M, 'changed': K}`）。README CLI 表（plan-09 T5 已含该行）与核对报告引用。

设计要点：`_compare_one` 已是「按 draw_result 全量重比该期所有追投票」的幂等入口（upsert 原地更新 + `corrected_at`），recompare 只需为**每个 verified DrawResult** 重入它。`--dry-run` 先统计会变更的行数（比对内存结果 vs 现存行，不写库），实跑才写。这同时服务两个场景：① 修表后清理旧错误行；② 未来任何规则修正的一键重算。

**浮动档金额保护（eng-review 主复核发现 1，HIGH）**：`_upsert_comparison` 对浮动档会置 `prize_amount=None`（浮动档 `hit.amount is None`），重入 `_compare_one` 会把**已回填的浮动奖金额抹成 null**；而 `FloatRefillWorker` 主查询有 `created_at >= cutoff`（7 天窗口）过滤，超期老行不会再回填 → 已中奖金额静默永久丢失。故 recompare 必须：① 重比前快照该期「旧规则下为浮动档、is_win、prize_amount 非空」的行（旧规则经版本门 `get_tiers(code, draw_date)` 判定），重比后对「tier 未变、仍 is_win、仍浮动」的行写回快照金额（保住 dlt/ssq/qxc 一二等已回填金额）；② 收尾对「重比后为浮动档、is_win、金额 None」的行（含 qlc 三等固定→浮动、历史期未回填浮动行）**绕过 created_at 窗口强制回填**——给 `FloatRefillWorker` 加 `max_age_days: int | None = None`（None=不限窗口），recompare 用 None 实例化跑一次 `refill()`。

- [ ] **Step 1: RED——`tests/services/test_recompare.py`**

```python
# tests/services/test_recompare.py
"""recompare_all 测试（Plan 10 / T6；eng-review 外部声音发现 1——旧错误表写出的
comparisons 行需要显式重算入口，否则永久错显示）。"""

from datetime import datetime

from sqlmodel import Session, select

from app.models import Comparison, DrawResult, LotteryType, Ticket, User
from app.services.compare_service import recompare_all


def _seed_draw(s, lottery_code, draw_no, front, back, draw_date):
    dr = DrawResult(
        lottery_code=lottery_code, draw_no=draw_no, verified=True,
        numbers_json=f'{{"front": {list(front)}, "back": {list(back)}}}',
        draw_date=draw_date,
    )
    s.add(dr)
    s.commit()
    s.refresh(dr)
    return dr


def test_recompare_corrects_stale_dlt_false_win(db_engine):
    """旧表误判的 dlt 1+1『中奖 100 元』行，recompare 后按新表判未中奖（is_win 翻 False，
    tier/amount 清 None），同一行原地更新（uq_cmp_draw_ticket 不产生新行）。"""
    with Session(db_engine) as s:
        s.add(LotteryType(code='dlt', name='大乐透', category='sport',
                          spec_json='{}', draw_schedule_json='{}'))
        u = User(username='alice', password_hash='x', role='user', invite_code='alice')
        s.add(u); s.commit(); s.refresh(u)
        dr = _seed_draw(s, 'dlt', '2026099', (1, 2, 3, 4, 5), (6, 7), datetime(2026, 8, 1))
        t = Ticket(user_id=u.id, lottery_code='dlt', play_type='single',
                   numbers_json='{"front":[1,9,9,9,9],"back":[6,8]}', cost=200)
        s.add(t); s.commit(); s.refresh(t)
        # 模拟旧错误表写出的行：1+1 被误判 tier=7 / 10000 分
        s.add(Comparison(user_id=u.id, draw_result_id=dr.id, ticket_id=t.id,
                         hits_json='{"front_hit":1,"back_hit":1}',
                         prize_tier=7, prize_amount=10000, is_win=True))
        s.commit()
        dr_id, t_id = dr.id, t.id

    stats = recompare_all(db_engine)

    assert stats['draws'] >= 1 and stats['rows'] >= 1
    with Session(db_engine) as s:
        row = s.exec(
            select(Comparison).where(
                Comparison.draw_result_id == dr_id, Comparison.ticket_id == t_id)
        ).one()
        assert row.is_win is False, '新表下 1+1 不中奖——旧行必须被纠正'
        assert row.prize_tier is None and row.prize_amount is None


def test_recompare_dry_run_writes_nothing(db_engine):
    """--dry-run 只统计不写库（人工核对安全阀）。"""
    # …同上准备一组数据…
    before = recompare_all(db_engine, dry_run=True)
    with Session(db_engine) as s:
        # 行内容保持旧错误值
        row = s.exec(select(Comparison)).first()
        assert row is not None and row.prize_tier == 7  # 未被改写
    assert before['changed'] >= 1  # 但统计到了会变更的行


def test_recompare_honors_version_gate(db_engine):
    """版本门接线：2026-01-30 的 dlt 期重比按 2019 表（4+2=四等 3000），
    2026-01-31 起按七档（4+2=三等 5000）——recompute 复用 _compare_one 即自动获得。"""
    # …种两期不同 draw_date 的 dlt + 同号 4+2 票，断言两行 tier 分别为 4 与 3…


def test_recompare_preserves_refilled_float_amount(db_engine):
    """发现 1（HIGH）：已回填的浮动档金额不得被 recompare 抹成 None——7 天窗口会让它
    永久丢失。构造：一期 dlt + 中一等（5+2）票，Comparison(tier=1, prize_amount=50000000,
    is_win=True) 模拟已回填 500 万元；recompare 后该行 prize_amount 仍 == 50000000。"""
    # …种一期 dlt（5+2）+ 一张 5+2 票 + 一张已回填 tier=1/50000000 的 comparison；
    #   recompare 后断言该行 prize_amount 未被置空（保护①写回快照）…
```

（`...` 处沿用本仓库既有 seed 辅助模式补全；断言点已固定。）

- [ ] **Step 2: RED——`tests/test_cli_recompare.py`**

```python
# tests/test_cli_recompare.py
"""recompare CLI 冒烟（Plan 10 / T6）。模式沿用 tests/test_cli_reset_password.py：
mock engine / 捕获 argparse 调用。"""

def test_cli_recompare_invokes_service(monkeypatch):
    """CLI 把 --lottery/--dry-run 正确传给 recompare_all 并打印统计。"""
    from app import cli as cli_mod

    captured = {}
    monkeypatch.setattr(cli_mod, '_engine_from_env', lambda: object())  # 不真实构造 engine
    monkeypatch.setattr(
        cli_mod, 'recompare_all',
        lambda engine, lottery_code=None, dry_run=False:
        captured.update(lottery_code=lottery_code, dry_run=dry_run) or {'draws': 1, 'rows': 2, 'changed': 1},
    )
    cli_mod.main(['recompare', '--lottery', 'dlt', '--dry-run'])
    assert captured == {'lottery_code': 'dlt', 'dry_run': True}
```

- [ ] **Step 3: 跑测试确认失败**

Run: `uv run pytest tests/services/test_recompare.py tests/test_cli_recompare.py -v`
Expected: FAIL（`recompare_all` 不存在 / CLI 无 recompare 子命令）

- [ ] **Step 4: GREEN——实现**

`app/services/compare_service.py` 模块级函数（不进 CompareService 类——无 outbox 语义，是显式运维入口）：

```python
def recompare_all(engine: Engine, lottery_code: str | None = None, dry_run: bool = False) -> dict:
    """按现行领域规则（含 T1 版本门）重算全部存量比对行（Plan 10 / T6）。

    为每个 verified DrawResult 重入 CompareService._compare_one（幂等 upsert：
    uq_cmp_draw_ticket 原地更新 hits/tier/amount/is_win + corrected_at）。
    用途：奖级表修正后清理旧表写出的错误行（eng-review 外部声音发现 1）；
    未来任何规则修正的一键重算。per-draw 失败隔离（try/except + log，不中断整批）。
    dry_run=True：只统计会变更的行数，不写库。

    浮动档金额保护（eng-review 主复核发现 1，HIGH）：_upsert_comparison 对浮动档
    置 prize_amount=None，会抹掉已回填金额；FloatRefillWorker 又有 7 天 created_at
    窗口，超期老行不会再回填 → 静默永久丢失。故：① 重比前快照「旧规则下浮动档、
    金额非空」行，重比后对 tier 未变者写回；② 收尾对「浮动档、金额 None」行绕过
    窗口强制回填（max_age_days=None）。
    """
    from app.models import Comparison, DrawResult

    svc = CompareService(engine)
    stats = {'draws': 0, 'rows': 0, 'changed': 0}
    with Session(engine) as s:
        q = select(DrawResult).where(DrawResult.verified == True)  # noqa: E712
        if lottery_code:
            q = q.where(DrawResult.lottery_code == lottery_code)
        dr_ids = [dr.id for dr in s.exec(q).all()]
    for dr_id in dr_ids:
        if dry_run:
            # 与实跑同一比对逻辑，仅比对内存结果统计差异（不 commit）
            stats['draws'] += 1
            stats['changed'] += _count_changed(engine, dr_id)
        else:
            before = _snapshot_rows(engine, dr_id)
            preserved = _snapshot_refilled_float_amounts(engine, dr_id)  # 发现 1 保护①
            try:
                svc._compare_one(dr_id)
            except Exception:
                logger.warning('recompare_skip_draw draw_result_id=%s', dr_id, exc_info=True)
                continue
            _restore_float_amounts(engine, dr_id, preserved)  # 发现 1 保护①
            stats['draws'] += 1
            stats['rows'] += max(len(before), 1)
            stats['changed'] += _diff_rows(before, _snapshot_rows(engine, dr_id))
    if not dry_run:
        _force_refill_float_rows(engine)  # 发现 1 保护②：绕过 7 天窗口强制回填
    return stats
```

（私有辅助：`_snapshot_rows`/`_diff_rows`/`_count_changed` 抽该期 comparisons 的 `(ticket_id, tier, amount, is_win)` 元组集做前后对比；`_snapshot_refilled_float_amounts`/`_restore_float_amounts` 实现发现 1 保护①——用 `get_tiers(code, dr.draw_date)` 判**旧规则**下的浮动档，快照 `(ticket_id, prize_amount)` 非空行，重比后对 tier 未变、仍 is_win、金额被置 None 的行写回；`_force_refill_float_rows` 实现保护②——对「浮动档、is_win、金额 None、unresolved=False」的行，用 `max_age_days=None`（不限窗口）实例化 `FloatRefillWorker` 跑一次回填。**注**：`_compare_one` 尾部的 `_upsert_draw_costs` 是 upsert（uq `user_id/lottery_code/draw_no` 原地更新，见 compare_service.py:243-283），重比**不会**重复累加成本，无需先删 DrawCost。）

`app/cli.py` 注册子命令（沿用既有 `backfill-draw-costs` 模式）：

```python
def cmd_recompare(argparse_ns) -> None:
    """按现行规则表重算存量比对行（运维：奖级表修正后清理旧错误行）。"""
    engine = _engine_from_env()  # 沿用本文件既有 engine 构造方式
    stats = recompare_all(engine, lottery_code=argparse_ns.lottery, dry_run=argparse_ns.dry_run)
    print(f"recompare: draws={stats['draws']} rows={stats['rows']} changed={stats['changed']}")
    if argparse_ns.dry_run:
        print('（dry-run：未写库。去掉 --dry-run 执行重算。）')


# main() 里：
rc = sub.add_parser('recompare', help='按现行规则表重算存量比对行（奖级表修正后用）')
rc.add_argument('--lottery', default=None, help='仅重算该彩种（默认全部）')
rc.add_argument('--dry-run', action='store_true', help='只统计会变更的行数，不写库')
rc.set_defaults(func=cmd_recompare)
```

（`_engine_from_env`/import 按本文件真实结构对齐——`cmd_backfill_draw_costs` 就地怎么拿 engine 就怎么拿。）

- [ ] **Step 5: 回归 + 手动实跑（NAS）**

Run: `uv run pytest tests/services/test_recompare.py tests/test_cli_recompare.py -q && uv run pytest -q`
Expected: 全绿。

生产实跑（T4 Step 7 核查有坏行时执行；无坏行也跑一次 dry-run 留档）：

```bash
docker compose exec app uv run python -m app.cli recompare --dry-run   # 先看会改多少
docker compose exec app uv run python -m app.cli recompare             # 实跑
```

- [ ] **Step 6: 提交**

```bash
git add app/cli.py app/services/compare_service.py tests/services/test_recompare.py tests/test_cli_recompare.py
git commit -m "feat(plan-10/T6): recompare CLI——奖级表修正后按新表重算存量比对行（含规则版本门与 dry-run）"
```

---

### Task 7: 全量回归 + 测试数更新 + 报告收尾

**Files:**
- Modify: `CLAUDE.md`（项目状态测试数 + 文档导航补核对报告链接——plan-09 T7 留给本 plan 的那行）
- Modify: `docs/reference/lottery-verification-2026-08-14.md`（收尾结论）

- [ ] **Step 1: 全量回归（三道）**

Run: `uv run ruff check . && uv run lint-imports && uv run pytest -q`
Expected: 全绿。记录 pytest 实际通过数。

Run: `cd web && npm test && npm run build`
Expected: 全绿。

- [ ] **Step 2: CLAUDE.md 更新**

「项目状态」行的测试数替换为 Step 1 实测值（如 `659+N tests green`）；「文档导航」节末尾追加（plan-09 T7 留给本 plan 的链接，避免死链窗口）：

```markdown
- `docs/reference/lottery-verification-2026-08-14.md` — **7 彩种「文档 vs 代码」核对报告（plan-10 产出）**
```

- [ ] **Step 3: 报告收尾**

核对报告「汇总表」下追加结论段：

```markdown
## 结论（2026-08-14，B1 完成）

- 5 项 B1 代码修复全部 TDD 落地：dlt 2026 七档新规 + 规则版本门（修 1+1/2+0/0+1 误判中奖）、qlc 浮动档/金额 + refill 浮动集动态化、qxc 任意对位语义 + 六等漏判档（静默漏中奖）、fc3d danxuan 打通 + 未实现玩法 API 拦截、recompare CLI（存量行重算入口）。
- ssq 生产基线（2026093 期真实夹具）全程绿。
- 已实现面单元测试补齐：每彩种每个已实现奖级 ≥1 命中用例 + 不中奖边界用例。
- 未实现项全部文档降级声明（README 能力边界 + lottery-rules.md 注记）并列 B2 roadmap，无过度宣称。
- 全量回归绿（后端 pytest + 前端 vitest + build）；生产库存量票/比对行已核查并按需 recompare。
```

- [ ] **Step 4: 提交**

```bash
git add CLAUDE.md docs/reference/lottery-verification-2026-08-14.md
git commit -m "feat(plan-10/T7): 全量回归绿 + 核对报告收尾（B1 完成）"
```

- [ ] **Step 5: 回到 plan-09 执行 T11（首推 GitHub）与 T12（发布后验证）**

---

## Self-Review 记录（plan 落盘前已执行）

- **Spec 覆盖**：§4.1 核对五维度（结构/开奖日/玩法/特殊规则/奖金表）→ 报告汇总表五列 + T0–T4 逐彩种；§4.2 ssq 基线先行 → T0；TDD 修复 → T1–T4；文档降级 → 报告 + lottery-rules.md 注记 + README（plan-09/T5 已按此终态写）；补测 → 各任务新测试文件；全量回归 → T6；qxc 保守回退（E7）→ 被更强处置取代：官方规则已核实，直接修正语义而非保留近似（E7 的「近似+标注」是在未核实官方规则时的保守方案，现已拿到规则原文）。
- **类型一致性**：`IMPLEMENTED_PLAY_TYPES`（frozenset，entry.py 定义）在 API/测试引用一致；`HitResult`/`PrizeTier` 字段名与现有代码一致；`get_tiers` 签名未动。
- **Placeholder 扫描**：无 TBD/TODO；所有测试与实现代码均为完整可运行内容；夹具为真实官方数据（2026093 期，2026-08-14 抓取）。
- **Eng review 修订（2026-08-14 FULL_REVIEW，13 项裁决全并入）**：T1 升级为「dlt 2026 新规 + 规则版本门」（`_VERSIONED_TABLES` 按开奖日路由 2019/2026 双表，compare_service/refill 全线透传 draw_date，+spy 接线测试）；T2 增 refill `_FLOAT_TIERS` 动态浮动集（修 qlc 三等永不回填静默失败）+ 回归用例；T3 QxcHybridCompare 显式 draw_date 形参（签名与 T1 契约一致）；T4 增 Step 7 存量票核查 SQL；新增 T6 recompare CLI（存量比对行重算，dry-run 安全阀）；原 T6 顺移 T7 并接管 CLAUDE.md 核对报告链接。

## GSTACK REVIEW REPORT

| Review | Skill | Scope | Runs | Status | Findings |
|--------|-------|-------|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 2 | issues_open (via /autoplan) | 6 proposals, 6 accepted, 1 critical gap |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | N/A | Codex 未安装（外部声音经 Claude subagent + autoplan subagent-only） |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 7 | CLEAR | 13 issues, 0 critical gaps |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | SKIPPED | 无 UI 视觉范围 |
| DX Review | `/plan-devex-review` | Developer experience gaps | 1 | CLEAR (via /autoplan) | score: 4/10 → 8/10, TTHW: crash-loop → 15-25min |

- **CODEX:** 未安装——外部声音由独立 Claude subagent 承担（8 项发现，逐项用户裁决后并入；其中 2 项 HIGH——refill 浮动集硬编码、存量 comparisons 无重算入口——均已物化为 plan 任务）。
- **CROSS-MODEL:** 主评审与外部声音独立收敛于「旧表存量数据」风险；外部声音的 refill (1,2) 硬编码发现是主评审 T2 的漏网之鱼（主评审只看了表没看 worker）——交叉验证的价值实证。
- **VERDICT:** ENG CLEARED — 13 项发现全部裁决并入（0 未决、0 critical gaps），可执行。

NO UNRESOLVED DECISIONS
