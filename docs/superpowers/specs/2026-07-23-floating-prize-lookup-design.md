# 浮动奖金查询接口对接 — 设计文档

> **目标**：将 `_amount_lookup_stub`（永久返回 None）替换为真实奖金查询实现，覆盖 ssq/dlt/qlc/qxc 一二等奖的浮动奖回填。
>
> **状态**：设计已确认，待实现
> **日期**：2026-07-23
> **源仓库**：lottery-notification

## 1. 背景与动机

### 1.1 现状

`FloatRefillWorker` 负责回填一二等奖浮动奖金（`prize_amount`），其依赖的 `amount_lookup` 回调当前是 `_amount_lookup_stub`（[app/main.py](../../../app/main.py)），永久返回 `None`。这意味着生产环境中所有浮动奖金额永远为 `null`，用户中一等奖/二等奖后无法看到实际金额。

### 1.2 浮动奖范围

仅以下 4 个彩种的 tier 1/2 为浮动档（`amount_type=FLOAT`，`amount=None`）：

| 彩种 | 主管部门 | 数据源 |
|------|----------|--------|
| ssq（双色球） | 福彩 | cwl.gov.cn JSON API |
| qlc（七乐彩） | 福彩 | cwl.gov.cn JSON API |
| dlt（大乐透） | 体彩 | sporttery.cn JSON API（PDF 降级） |
| qxc（七星彩） | 体彩 | sporttery.cn JSON API（PDF 降级） |

fc3d/pl3/pl5 为纯固定档，不涉及回填。

### 1.3 动机

- 让浮动奖回填真正生效，用户能看到实际中奖金额
- 复用现有 `FloatRefillWorker` 框架（行级隔离、超期标记、naive UTC cutoff 等已完善）
- 最小化对现有代码的侵入

## 2. 架构设计

### 2.1 新增 PrizeSource Protocol

独立于现有 `DrawSource` Protocol（号码抓取），新增 `PrizeSource` Protocol（奖金查询）：

```python
# app/adapters/base.py 新增
class PrizeSource(Protocol):
    name: str
    def lookup_amount(self, lottery_code: str, draw_no: str, draw_date: date, tier: int) -> int | None:
        """查询浮动奖金（分）。None = 官方尚未公布。"""
        ...
```

**为什么独立 Protocol 而非扩展 DrawSource**：奖金查询的数据源（cwl.gov.cn / sporttery.cn）与号码抓取的数据源（MXNZP / 聚合数据）完全不同，职责隔离更清晰。

### 2.2 两个具体适配器

```
PrizeSource (Protocol)
├── CwlPrizeSource          ← 福彩：cwl.gov.cn JSON API（ssq, qlc）
└── SportteryPrizeSource    ← 体彩：sporttery JSON API + PDF 降级（dlt, qxc）
```

### 2.3 路由闭包

`main.py` 中构建按 `lottery_code` 路由的闭包，替换 `_amount_lookup_stub`：

```python
def _build_amount_lookup(cwl: PrizeSource, sporttery: PrizeSource):
    _WELFARE = {'ssq', 'qlc'}
    _SPORTS = {'dlt', 'qxc'}
    def lookup(lottery_code: str, draw_no: str, draw_date: date, tier: int) -> int | None:
        if lottery_code in _WELFARE:
            return cwl.lookup_amount(lottery_code, draw_no, draw_date, tier)
        if lottery_code in _SPORTS:
            return sporttery.lookup_amount(lottery_code, draw_no, draw_date, tier)
        return None  # 非浮动档彩种
    return lookup
```

### 2.4 整体数据流

```
FloatRefillWorker.refill()
  → 遍历 pending comparisons (tier 1/2, prize_amount IS NULL)
  → self._lookup(lottery_code, draw_no, draw_date, tier)
    → _build_amount_lookup 路由
      → CwlPrizeSource.lookup_amount()     [ssq, qlc]
      → SportteryPrizeSource.lookup_amount() [dlt, qxc]
        → JSON API 主路径
        → PDF 降级路径（JSON 失败时）
  → 返回基础奖金（分）
  → refill_service 应用 append_multiplier（如 DLT 追加 1.8x）
  → 写入 Comparison.prize_amount
```

## 3. CwlPrizeSource 适配器

### 3.1 API 调用

```
GET https://www.cwl.gov.cn/cwl_admin/front/cwlkj/search/kjxx/findDrawNotice
    ?name={lottery_code}&code={full_issue}
```

- `full_issue` = `f"{draw_date.year}{draw_no}"` → 如 draw_date=2026-07-19, draw_no="082" → "2026082"
- 支持 lottery_code：`ssq`、`qlc`

### 3.2 响应格式

```json
{
  "state": 0,
  "message": "查询成功",
  "result": [{
    "code": "2026082",
    "prizegrades": [
      {"type": 1, "typenum": "10", "typemoney": "7104774"},
      {"type": 2, "typenum": "215", "typemoney": "122370"},
      {"type": 3, "typenum": "1472", "typemoney": "3000"},
      ...
    ]
  }]
}
```

### 3.3 解析规则

- `state != 0` 或 `result` 为空 → 返回 None
- 遍历 `prizegrades`，匹配 `type == tier`
- `typemoney == "_"` → 未公布，返回 None（触发下轮重试）
- `typemoney` 为数字字符串 → `int(typemoney) * 100` → 分
- 未找到匹配 tier → 返回 None

### 3.4 HTTP 客户端

构造函数注入 `httpx.Client`，与现有 `MxnzpAdapter` 模式一致。

## 4. SportteryPrizeSource 适配器

### 4.1 JSON API 主路径

```
GET https://webapi.sporttery.cn/gateway/lottery/getHistoryPageListV1.qry
    ?gameNo={game_no}&provinceId=0&pageSize=1&isVerify=1&pageNo=1&termLimits=1
```

- Headers: `User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) ...`, `Referer: https://www.sporttery.cn/`
- DLT: `gameNo=85`，QXC: `gameNo=14`（实现时验证）
- 返回 JSON，解析 `value.list[0]` 中的奖级列表，匹配对应奖级
- **注意**：响应字段名（`bonusInfoList`、`bonusAmount`、`grade`）基于公开教程推断，未被实际 API 响应验证（EdgeOne 拦截了调研请求）。实现时需先验证实际响应结构，若字段名不同则适配解析逻辑。NAS 部署在中国 IP + 正确 headers 下 API 大概率可用。
- 奖金单位为元，`int(amount) * 100` → 分
- 过滤 `periodNumber` 匹配目标期号（重建为 7 位：`f"{draw_date.year}{draw_no}"`）
- JSON 请求失败（HTTP 非 200 / 超时 / 解析异常 / EdgeOne 拦截 567）→ 降级 PDF

### 4.2 PDF 降级路径

```
GET https://pdf.sporttery.cn/{pdf_code}/{period}/{period}.pdf
```

- DLT: `pdf_code=33800`，QXC: 待确认（实现时通过 sporttery 页面抓取确认）
- `period` 5 位：`f"{draw_date.year % 100:02d}{draw_no}"` → 如 "26082"
- HTTP 404 → 未开奖/未公布，返回 None
- 下载 PDF bytes → `pypdf.PdfReader` 提取文本 → 正则匹配奖级基本奖金
- 金额含千分位逗号（如 `8,286,251`）→ 去逗号 → `× 100` → 分
- 仅提取"基本"金额（追加由 refill_service 乘 append_multiplier 处理）
- 解析失败 → 返回 None + log warning

### 4.3 统一接口

```python
class SportteryPrizeSource:
    def lookup_amount(self, lottery_code, draw_no, draw_date, tier):
        amount = self._lookup_json(lottery_code, draw_no, draw_date, tier)
        if amount is not None:
            return amount
        return self._lookup_pdf(lottery_code, draw_no, draw_date, tier)
```

### 4.4 新增依赖

`pypdf`（轻量纯 Python PDF 文本提取库），加入 `pyproject.toml` dependencies。

## 5. FloatRefillWorker 改动

### 5.1 amount_lookup 签名扩展

```python
# 现在: Callable[[str, str, int], int | None]
# 改为: Callable[[str, str, date, int], int | None]
```

新增 `draw_date` 参数，用于适配器重建完整期号。

### 5.2 追加投注处理

lookup 返回基础奖金后，refill_service 根据 Ticket.append 标志应用 append_multiplier：

```python
amount = self._lookup(dr.lottery_code, dr.draw_no, dr.draw_date, cmp.prize_tier)
if amount is not None:
    # 查 ticket append 标志
    if ticket.append:
        # 从 prize_tables 获取 append_multiplier
        amount = int(amount * tier_info.append_multiplier)
    c.prize_amount = amount
```

### 5.3 预加载优化

- 批量预加载 `{ticket_id: Ticket}` 获取 append 标志，避免 N+1 查询
- 预加载 `{lottery_code: [PrizeTier]}` 获取 append_multiplier
- 与现有 DrawResult 预加载模式一致

## 6. main.py 接线改动

### 6.1 替换 _amount_lookup_stub

在 `_build_scheduler_and_deps()` 中：

```python
# 之前:
refill = FloatRefillWorker(engine, amount_lookup=_amount_lookup_stub)

# 之后（各适配器自建 client，与现有 MxnzpAdapter/JuheAdapter 模式一致——D1 决策）:
cwl = CwlPrizeSource()
sporttery = SportteryPrizeSource()
amount_lookup = _build_amount_lookup(cwl, sporttery)
refill = FloatRefillWorker(engine, amount_lookup=amount_lookup)
```

### 6.2 httpx.Client 模式

各适配器构造时自建 `httpx.Client(transport=..., timeout=10.0)`，与现有 `MxnzpAdapter`/`JuheAdapter` 模式一致（D1 决策）。不共享 client——现有代码中无共享 client 可注入。

新适配器加 `close()` 方法释放 client 资源，`lifespan` teardown 中调用（与 `notifier.close()` 平行）。

### 6.3 _amount_lookup_stub 移除

删除 `_amount_lookup_stub` 函数，由真实实现替代。

## 7. 测试策略

### 7.1 适配器单测

**test_cwl_prize_source.py**：
- mock httpx 响应，验证 JSON 解析
- `typemoney="7104774"` → `710477400`（分）
- `typemoney="_"` → None
- `state != 0` → None
- result 为空 → None
- tier 未找到 → None
- 期号重建：draw_no="082" + draw_date=2026-07-19 → "2026082"

**test_sporttery_prize_source.py**：
- mock httpx JSON 响应，验证奖级列表解析（字段名以实际 API 响应为准）
- mock JSON 失败 + mock PDF bytes，验证降级路径
- PDF 404 → None
- PDF 解析失败 → None + warning
- period 重建：draw_no="082" + draw_date=2026 → JSON "2026082" / PDF "26082"

### 7.2 服务层测试

扩展 **test_refill_service.py**：
- `amount_lookup` mock 签名增加 `draw_date` 参数
- 追加投注：ticket.append=True，验证 `amount * 1.8`
- 非追加：ticket.append=False，验证 `amount` 原值
- 非浮动彩种（fc3d tier=1）→ lookup 不被调用

### 7.3 接线测试

- `_build_amount_lookup` 路由：ssq → CwlPrizeSource，dlt → SportteryPrizeSource，fc3d → None
- 延续现有测试模式：MagicMock 注入、真实 DB fixture（`db_engine`）、断言最终 DB 状态

## 8. 配置项

无需新增 Settings 配置项。cwl.gov.cn 和 sporttery.cn 均为公开 API，无需 API Key。

httpx.Client 的超时等配置沿用现有 `Settings` 中已有的 httpx 相关参数。

## 9. 不在范围内

- **补推通知**：回填后 `prize_amount` 变更的推送由后续 Plan 04（Notifier 监听 prize_amount 变更）处理，本设计仅回填数据
- **复式/胆拖展开**：与浮动奖查询无关
- **DrawSource 扩展**：不修改现有号码抓取适配器
- **naive UTC 系统性迁移**：保持现有 naive UTC cutoff 逻辑不变

## 10. 审查修正（2026-07-24 CEO Review）

以下来自 `/plan-ceo-review` 的审查决策，**覆盖/补充**上文对应节：

### 10.1 审查决策汇总

| # | 决策 | 影响节 | 内容 |
|---|------|--------|------|
| 1A | draw_date 类型 | §2.1/§5.1 | 签名用 `datetime`（非 `date`），与 `DrawResult.draw_date` 类型一致 |
| 1B | 期号防御截断 | §3.1/§4.1 | `draw_no` 长度 >3 时 log warning + 取后 3 位，防未来 adapter 绕过归一化 |
| 1C | 每日两轮 | §6/jobs.py | 开奖日当晚 22:00 + 次日 08:00 两轮（原设计仅 08:00 单轮） |
| 4A | append guard | §5.2 | `if ticket.append and tier_info.append_multiplier:`（防 None 乘法） |
| 5A | 期号重建 DRY | §3.1/§4.1 | 提取 `rebuild_full_issue()` + `rebuild_short_period()` 到 base.py |
| 8A | 适配器日志 | §3/§4 | 关键分支加 `logger.info`（期号重建、API 状态、PDF 降级、解析结果） |
| D1 | httpx.Client | §6.2 | 各适配器自建 client（修正设计"共享 client"假设错误） |
| OV1 | **倍投乘法** | §5.2 | **CRITICAL**：`amount *= ticket.multiplier`（compare_service 明确委托 refill 应用倍投） |
| OV2 | 启动冒烟验证 | §6 | `validate_startup()` 加 sporttery API 字段名验证，log error 不阻止启动 |
| OV3 | 限流礼貌 | §5 | 每次 lookup 后 `sleep(0.5)` 防冷启动突发请求触发反爬 |
| OV4 | verified 过滤 | §5.1 | refill 查询加 `DrawResult.verified == True` 条件 |

### 10.2 修正后的金额公式

```python
# refill_service 中，lookup 返回基础奖金后：
amount = lookup_result  # 基础奖金（分）
if ticket.append and tier_info.append_multiplier:  # 4A guard
    amount = int(amount * tier_info.append_multiplier)  # 追加 1.8x
amount *= ticket.multiplier  # OV1: 倍投（compare_service 委托 refill 应用）
c.prize_amount = amount
```

### 10.3 修正后的 refill 查询条件

```python
# §5.1 查询增加 verified 过滤（OV4）：
pending = s.exec(
    select(Comparison)
    .join(DrawResult, Comparison.draw_result_id == DrawResult.id)
    .where(
        Comparison.is_win == True,
        Comparison.prize_tier.in_((1, 2)),
        Comparison.prize_amount.is_(None),
        Comparison.unresolved == False,
        Comparison.created_at >= cutoff,
        DrawResult.verified == True,  # OV4: 只回填已验证数据
    )
).all()
```

### 10.4 调度变更

```python
# jobs.py：新增开奖日当晚 22:00 轮（1C）
sched.add_job(_run_float_refill, 'cron', hour=22, minute=0, id='float_refill_night', args=[db_url], replace_existing=True)
# 保留原有 08:00 轮
```

## Implementation Tasks

Synthesized from this review's findings. Each task derives from a specific
finding above. Run with Claude Code or Codex; checkbox as you ship.

- [ ] **T1 (P1, human: ~3h / CC: ~20min)** — adapters — Add PrizeSource Protocol + CwlPrizeSource adapter
  - Surfaced by: Design §2/§3: new Protocol + cwl.gov.cn JSON API adapter for ssq/qlc
  - Files: `app/adapters/base.py`, `app/adapters/cwl_prize.py`, `tests/adapters/test_cwl_prize.py`
  - Verify: `uv run pytest tests/adapters/test_cwl_prize.py -v`
- [ ] **T2 (P1, human: ~4h / CC: ~25min)** — adapters — Add SportteryPrizeSource adapter (JSON API + PDF fallback)
  - Surfaced by: Design §4: sporttery.cn JSON + PDF fallback with pypdf for dlt/qxc
  - Files: `app/adapters/sporttery_prize.py`, `tests/adapters/test_sporttery_prize.py`, `pyproject.toml`
  - Verify: `uv run pytest tests/adapters/test_sporttery_prize.py -v`
- [ ] **T3 (P1, human: ~30min / CC: ~5min)** — adapters — Extract shared issue/period rebuild helpers to base.py
  - Surfaced by: Review 5A: DRY — rebuild_full_issue + rebuild_short_period with defensive truncation (1B)
  - Files: `app/adapters/base.py`, `tests/adapters/test_base_helpers.py`
  - Verify: `uv run pytest tests/adapters/test_base_helpers.py -v`
- [ ] **T4 (P1, human: ~2h / CC: ~15min)** — services — Update FloatRefillWorker: draw_date sig + append_multiplier + ticket.multiplier + guard + verified filter + rate limit
  - Surfaced by: Review 1A/4A/OV1/OV3/OV4: signature change + money formula + guard + verified filter + sleep(0.5)
  - Files: `app/services/refill_service.py`, `tests/services/test_refill_service.py`
  - Verify: `uv run pytest tests/services/test_refill_service.py -v`
- [ ] **T5 (P1, human: ~30min / CC: ~5min)** — main — Wire _build_amount_lookup in main.py, replace _amount_lookup_stub
  - Surfaced by: Design §6: routing closure + adapter instantiation (each own client per D1)
  - Files: `app/main.py`
  - Verify: `uv run pytest tests/test_health.py -v` (startup smoke)
- [ ] **T6 (P1, human: ~15min / CC: ~3min)** — scheduler — Add draw-night 22:00 refill run (keep 08:00)
  - Surfaced by: Review 1C: user chose draw-night 22:00 + next-day 08:00 two rounds
  - Files: `app/scheduler/jobs.py`
  - Verify: `uv run pytest tests/scheduler/ -v -k refill`
- [ ] **T7 (P2, human: ~30min / CC: ~5min)** — main — Add sporttery API field-name smoke check to validate_startup
  - Surfaced by: Review OV2: sporttery JSON field names unverified, need startup visibility
  - Files: `app/main.py`
  - Verify: Manual: start app, check logs for smoke check result
- [ ] **T8 (P2, human: ~15min / CC: ~3min)** — adapters — Add info-level logging at key adapter branches
  - Surfaced by: Review 8A: observability — log issue rebuild, API status, PDF fallback, parse result
  - Files: `app/adapters/cwl_prize.py`, `app/adapters/sporttery_prize.py`
  - Verify: Manual: trigger lookup, check log output

_No new tasks from Sections 3 (Security), 7 (Performance), 9 (Deployment), 10 (Long-term)._

## 11. 工程审查修正（2026-07-24 Eng Review）

以下来自 `/plan-eng-review` 的审查决策，**覆盖/补充**上文对应节：

### 11.1 审查决策汇总

| # | 决策 | 影响节 | 内容 |
|---|------|--------|------|
| 1A | PDF 安全 | §4.2 | `PdfReader(io.BytesIO(resp.content))` + 5MB 大小限制 |
| 2A | 降级异常分类 | §4.3 | `_lookup_json` 只 catch (JSONDecodeError, KeyError, TypeError, IndexError) 触发降级；httpx 异常上抛 |
| OV#1 | httpx.Client 矛盾 | §6.2 | 已修正为各适配器自建 client + close() + lifespan teardown |
| OV#2 | per-host 限流 | §5 | refill 循环按 lottery_code 分组，同组内 sleep(0.5) |
| OV#3 | 未公布不降级 | §4.3 | JSON 空/无匹配（未公布）→ 直接 None 不降级 PDF；仅 JSON 故障才降级 |
| OV#6 | draw_date 时区契约 | §2.1 | draw_date 为 aware CST（fetch_service:229），期号重建 year 依赖此契约 |
| OV#8 | 冒烟对称 | §6 | validate_startup() 同时验证 cwl + sporttery 字段名 |
| OV#9 | golden-file 测试 | §7 | 真实 DLT PDF fixture 防 pypdf 版本漂移 |
| OV#10 | 范围确认 | — | 继续完整实现（一二等奖自动化是核心价值完整性保证） |

### 11.2 修正后的 JSON→PDF 降级逻辑

```python
def lookup_amount(self, lottery_code, draw_no, draw_date, tier):
    """统一入口：JSON 主 → PDF 降级。"""
    try:
        result = self._lookup_json(lottery_code, draw_no, draw_date, tier)
        if result is not _NOT_PUBLISHED:  # OV#3: 区分未公布 vs 故障
            return result
        return None  # 未公布，不降级 PDF
    except (json.JSONDecodeError, KeyError, TypeError, IndexError):
        # JSON 故障（字段缺失/格式变动），降级 PDF
        logger.info('json_fallback_to_pdf lottery=%s draw_no=%s', lottery_code, draw_no)
        return self._lookup_pdf(lottery_code, draw_no, draw_date, tier)
    # httpx 异常不 catch，上抛给 refill_worker
```

### 11.3 draw_date 时区契约

`DrawResult.draw_date` 存储为 **aware CST**（`fetch_service:229` 确认：`datetime.combine(dn.draw_date, time(), tzinfo=_CST)`）。期号重建（`rebuild_full_issue`/`rebuild_short_period`）的 `draw_date.year` 依赖此契约——年末年初边界（12月31日 21:30 CST）年份正确。

### 11.4 multiplier 所有权边界

- `compare_service`：固定档 `amount × multiplier`（比对时应用）
- `refill_service`：浮动档 `base × append_multiplier × multiplier`（回填时应用）
- 两服务各管一段，不重叠。复式/胆拖展开（Phase 2）时每行 comparison 共享 `ticket_id`，refill 的 per-row multiplier 乘法仍然正确。

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 1 | issues_found | mode: HOLD_SCOPE, 11 decisions (1 CRITICAL: OV1 倍投遗漏） |
| Codex Review | — | Independent 2nd opinion | 0 | — | — |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | issues_found | 2 issues + 6 OV findings, 8 decisions confirmed |
| Design Review | — | UI/UX gaps | 0 | — | — |
| DX Review | — | Developer experience gaps | 0 | — | — |

**CROSS-MODEL:** Outside voice (Claude subagent) found 10 additional issues; 6 accepted as decisions (OV#1/#3/#6/#8/#9/#10), 4 noted as implementation details (OV#2/#4/#5/#7).

**VERDICT:** CEO + ENG issues_found — 所有决策已确认并写入设计文档。14 个实现任务（T1-T14）可开始。

NO UNRESOLVED DECISIONS
