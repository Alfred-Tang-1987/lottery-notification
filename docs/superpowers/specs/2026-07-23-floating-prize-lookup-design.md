# 浮动奖金查询接口对接 — 设计文档

> **目标**：将 `_amount_lookup_stub`（永久返回 None）替换为真实奖金查询实现，覆盖 ssq/dlt/qlc/qxc 一二等奖的浮动奖回填。
>
> **状态**：设计已确认，待实现
> **日期**：2026-07-23
> **源仓库**：lottery-notification

## 1. 背景与动机

### 1.1 现状

`FloatRefillWorker` 负责回填一二等奖浮动奖金（`prize_amount`），其依赖的 `amount_lookup` 回调当前是 `_amount_lookup_stub`（[app/main.py](file://<LOCAL_PATH>/Documents/Projects/lottery-notification/app/main.py)），永久返回 `None`。这意味着生产环境中所有浮动奖金额永远为 `null`，用户中一等奖/二等奖后无法看到实际金额。

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

# 之后:
cwl = CwlPrizeSource(client=httpx_client)
sporttery = SportteryPrizeSource(client=httpx_client)
amount_lookup = _build_amount_lookup(cwl, sporttery)
refill = FloatRefillWorker(engine, amount_lookup=amount_lookup)
```

### 6.2 httpx.Client 共享

与现有 MxnzpAdapter / JuheAdapter 共享同一个 `httpx.Client` 实例（已在 `_build_scheduler_and_deps` 中创建）。

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
