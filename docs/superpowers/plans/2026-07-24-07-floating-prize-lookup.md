---
models:
  T1: sonnet
  T2: sonnet
  T3: sonnet
  T4: sonnet
  T5: sonnet
  T6: sonnet
  T7: sonnet
  T8: sonnet
---

# 浮动奖金查询接口对接 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现官方浮动奖金查询接口（CwlPrizeSource + SportteryPrizeSource），替换 `_amount_lookup_stub`，让 FloatRefillWorker 能真实回填一二等奖金额。

**Architecture:** 新增 `PrizeSource` Protocol + 两个官方源适配器（cwl.gov.cn JSON / sporttery.cn JSON+PDF），路由闭包按彩种分发，FloatRefillWorker 签名扩展 `draw_date` 并在回填时应用追加 × 倍投公式。

**Tech Stack:** Python 3.12, httpx, pypdf, SQLModel, APScheduler, pytest + MockTransport

## Global Constraints

- 金额单位：**分**（int），展示层再除 100
- 时区：**Asia/Shanghai**（draw_date 存 aware CST，期号重建 year 依赖此契约）
- 领域层（app/domain/）**零 IO**——不得 import httpx/pypdf（import-linter 强制）
- 所有新适配器：自建 `httpx.Client(transport=..., timeout=10.0)`，与 MxnzpAdapter/JuheAdapter 模式一致
- `draw_no` 已归一化（无年份前缀，3 位零填充），由 `normalize_draw_no` 强制执行
- 失败语义：lookup 返回 None = 官方未公布/查询失败，下轮重试，不写脏数据
- per-row 异常隔离：refill_worker 已有 `try/except Exception` 包裹 lookup 调用，适配器不 catch httpx 异常
- 测试模式：`httpx.MockTransport(handler)` mock HTTP，真实 DB fixtures（`db_engine`）

## File Structure

```
app/adapters/base.py              — 新增 PrizeSource Protocol + rebuild_full_issue + rebuild_short_period
app/adapters/cwl_prize.py         — 新增 CwlPrizeSource（ssq, qlc）
app/adapters/sporttery_prize.py   — 新增 SportteryPrizeSource（dlt, qxc）
app/services/refill_service.py    — 修改：签名扩展 + verified 过滤 + 金额公式 + 分组限流
app/main.py                       — 修改：_build_amount_lookup 替换 stub + lifespan close + 冒烟验证
app/scheduler/jobs.py             — 修改：新增 22:00 cron job
pyproject.toml                    — 修改：新增 pypdf 依赖
tests/adapters/test_prize_helpers.py    — 新增：共享辅助函数测试
tests/adapters/test_cwl_prize.py        — 新增：CwlPrizeSource 测试
tests/adapters/test_sporttery_prize.py  — 新增：SportteryPrizeSource 测试
tests/services/test_refill_service.py   — 修改：新增金额公式/verified/签名测试
tests/fixtures/                        — 新增：真实 DLT PDF golden-file fixture
```

---

### Task 1: 共享辅助函数 + PrizeSource Protocol

**Files:**
- Modify: `app/adapters/base.py`
- Test: `tests/adapters/test_prize_helpers.py`

**Interfaces:**
- Consumes: 现有 `normalize_draw_no`（同文件）
- Produces:
  - `PrizeSource` Protocol（`lookup_amount(lottery_code, draw_no, draw_date, tier) -> int | None`）
  - `rebuild_full_issue(draw_date: datetime, draw_no: str) -> str`（如 "2026082"）
  - `rebuild_short_period(draw_date: datetime, draw_no: str) -> str`（如 "26082"）

- [ ] **Step 1: Write the failing test**

```python
# tests/adapters/test_prize_helpers.py
"""共享期号重建辅助函数测试。"""
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.adapters.base import rebuild_full_issue, rebuild_short_period

_CST = ZoneInfo('Asia/Shanghai')


def _dt(year, month, day):
    return datetime(year, month, day, 21, 30, tzinfo=_CST)


class TestRebuildFullIssue:
    def test_normal(self):
        assert rebuild_full_issue(_dt(2026, 7, 19), '082') == '2026082'

    def test_year_start(self):
        assert rebuild_full_issue(_dt(2026, 1, 3), '001') == '2026001'

    def test_year_end(self):
        # 年末 12/31 开奖，year 仍 2026（draw_date 为 aware CST）
        assert rebuild_full_issue(_dt(2026, 12, 31), '154') == '2026154'

    def test_defensive_truncation(self, caplog):
        """draw_no 超长（未归一化）→ log warning + 取后 3 位（1B 决策）。"""
        with caplog.at_level(logging.WARNING):
            result = rebuild_full_issue(_dt(2026, 7, 19), '2026082')
        assert result == '2026082'  # '2026082'[-3:] = '082'
        assert 'draw_no_too_long' in caplog.text


class TestRebuildShortPeriod:
    def test_normal(self):
        assert rebuild_short_period(_dt(2026, 7, 19), '082') == '26082'

    def test_year_start(self):
        assert rebuild_short_period(_dt(2026, 1, 3), '001') == '26001'

    def test_year_end(self):
        assert rebuild_short_period(_dt(2026, 12, 31), '154') == '26154'

    def test_defensive_truncation(self, caplog):
        with caplog.at_level(logging.WARNING):
            result = rebuild_short_period(_dt(2026, 7, 19), '2026082')
        assert result == '26082'
        assert 'draw_no_too_long' in caplog.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/adapters/test_prize_helpers.py -v`
Expected: FAIL with `ImportError: cannot import name 'rebuild_full_issue'`

- [ ] **Step 3: Write minimal implementation**

在 `app/adapters/base.py` 末尾追加：

```python
import logging
from datetime import datetime
from typing import Protocol

logger = logging.getLogger(__name__)


class PrizeSource(Protocol):
    """官方奖金查询源（独立于 DrawSource——奖金查询与号码抓取是不同职责）。"""

    name: str

    def lookup_amount(
        self, lottery_code: str, draw_no: str, draw_date: datetime, tier: int
    ) -> int | None:
        """查询浮动奖金（分）。None = 官方尚未公布/查询失败。

        draw_date 为 aware CST（fetch_service 存入时的契约），期号重建 year 依赖此。
        异常上抛——由 FloatRefillWorker 统一 catch + 隔离（不 catch httpx 异常）。
        """
        ...


def _defensive_truncate(draw_no: str) -> str:
    """draw_no 防御截断：长度 >3 时 log warning + 取后 3 位（1B 决策）。

    正常路径 draw_no 已归一化（3 位零填充），此防御仅覆盖未来 adapter 绕过
    归一化直接写入的异常场景。
    """
    if len(draw_no) > 3:
        logger.warning(
            'draw_no_too_long draw_no=%s truncated_to=%s',
            draw_no,
            draw_no[-3:],
        )
        return draw_no[-3:]
    return draw_no


def rebuild_full_issue(draw_date: datetime, draw_no: str) -> str:
    """重建全年份期号（如 '2026082'）。

    draw_date 必须是 aware CST（期号重建 year 依赖此时区契约）。
    """
    safe_no = _defensive_truncate(draw_no)
    return f'{draw_date.year}{safe_no}'


def rebuild_short_period(draw_date: datetime, draw_no: str) -> str:
    """重建 2 位年份期号（如 '26082'）。用于 sporttery PDF URL。"""
    safe_no = _defensive_truncate(draw_no)
    return f'{draw_date.year % 100:02d}{safe_no}'
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/adapters/test_prize_helpers.py -v`
Expected: 6 PASSED

- [ ] **Step 5: Commit**

```bash
git add app/adapters/base.py tests/adapters/test_prize_helpers.py
git commit -m "feat: add PrizeSource Protocol + rebuild_full_issue/rebuild_short_period helpers"
```

---

### Task 2: CwlPrizeSource 适配器

**Files:**
- Create: `app/adapters/cwl_prize.py`
- Test: `tests/adapters/test_cwl_prize.py`

**Interfaces:**
- Consumes: `rebuild_full_issue`（Task 1）、`httpx.MockTransport`（测试模式）
- Produces: `CwlPrizeSource` 类（`lookup_amount` + `close`）

- [ ] **Step 1: Write the failing test**

```python
# tests/adapters/test_cwl_prize.py
"""CwlPrizeSource 测试——cwl.gov.cn JSON API。"""
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.adapters.cwl_prize import CwlPrizeSource

_CST = ZoneInfo('Asia/Shanghai')
_DRAW_DATE = datetime(2026, 7, 19, 21, 30, tzinfo=_CST)


def _mock_transport(handler):
    return httpx.MockTransport(handler)


def _cwl_response(prizegrades):
    """构造 cwl 标准响应。"""
    return {
        'state': 0,
        'message': 'success',
        'result': [{
            'code': '082',
            'name': '双色球',
            'prizegrades': prizegrades,
        }],
    }


class TestCwlPrizeSource:
    def test_happy_path(self):
        """正常查询：匹配 tier，返回分。"""
        def handler(req):
            return httpx.Response(200, json=_cwl_response([
                {'type': 1, 'typemoney': '5000000', 'typenum': '5'},
                {'type': 2, 'typemoney': '150000', 'typenum': '25'},
                {'type': 3, 'typemoney': '3000', 'typenum': '1500'},
            ]))
        src = CwlPrizeSource(transport=_mock_transport(handler))
        assert src.lookup_amount('ssq', '082', _DRAW_DATE, 1) == 500_000_000  # 500万分=500万
        assert src.lookup_amount('ssq', '082', _DRAW_DATE, 2) == 15_000_000   # 15万分

    def test_typemoney_underscore_returns_none(self):
        """typemoney == "_" → 未公布，返回 None。"""
        def handler(req):
            return httpx.Response(200, json=_cwl_response([
                {'type': 1, 'typemoney': '_', 'typenum': '_'},
            ]))
        src = CwlPrizeSource(transport=_mock_transport(handler))
        assert src.lookup_amount('ssq', '082', _DRAW_DATE, 1) is None

    def test_state_not_zero_returns_none(self):
        """state != 0 → 业务失败，返回 None。"""
        def handler(req):
            return httpx.Response(200, json={'state': 1, 'message': 'fail', 'result': []})
        src = CwlPrizeSource(transport=_mock_transport(handler))
        assert src.lookup_amount('ssq', '082', _DRAW_DATE, 1) is None

    def test_result_empty_returns_none(self):
        """result 空列表 → 无数据，返回 None。"""
        def handler(req):
            return httpx.Response(200, json={'state': 0, 'message': 'success', 'result': []})
        src = CwlPrizeSource(transport=_mock_transport(handler))
        assert src.lookup_amount('ssq', '082', _DRAW_DATE, 1) is None

    def test_tier_no_match_returns_none(self):
        """prizegrades 中无匹配 tier → 返回 None。"""
        def handler(req):
            return httpx.Response(200, json=_cwl_response([
                {'type': 3, 'typemoney': '3000', 'typenum': '1500'},
            ]))
        src = CwlPrizeSource(transport=_mock_transport(handler))
        assert src.lookup_amount('ssq', '082', _DRAW_DATE, 1) is None

    def test_http_error_raises(self):
        """HTTP 5xx → 上抛（由 refill_worker 统一 catch）。"""
        def handler(req):
            return httpx.Response(500)
        src = CwlPrizeSource(transport=_mock_transport(handler))
        with pytest.raises(httpx.HTTPStatusError):
            src.lookup_amount('ssq', '082', _DRAW_DATE, 1)

    def test_issue_rebuild_in_url(self):
        """验证期号重建：URL 中 code 参数为全年份期号。"""
        captured_url = None
        def handler(req):
            nonlocal captured_url
            captured_url = str(req.url)
            return httpx.Response(200, json=_cwl_response([
                {'type': 1, 'typemoney': '5000000', 'typenum': '5'},
            ]))
        src = CwlPrizeSource(transport=_mock_transport(handler))
        src.lookup_amount('ssq', '082', _DRAW_DATE, 1)
        assert 'code=2026082' in captured_url

    def test_close(self):
        """close() 释放 client 资源。"""
        src = CwlPrizeSource(transport=_mock_transport(lambda r: httpx.Response(200)))
        src.close()  # 不抛异常即通过
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/adapters/test_cwl_prize.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.adapters.cwl_prize'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/adapters/cwl_prize.py
"""CwlPrizeSource——中彩网（cwl.gov.cn）官方奖金查询。

覆盖彩种：ssq（双色球）、qlc（七乐彩）。
数据源：cwl.gov.cn 开奖公告 JSON API。
"""
import json
import logging
from datetime import datetime

import httpx

from app.adapters.base import rebuild_full_issue

logger = logging.getLogger(__name__)


class CwlPrizeSource:
    """中彩网浮动奖金查询。各适配器自建 httpx.Client（D1 决策）。"""

    name = 'cwl'

    def __init__(self, transport: httpx.BaseTransport | None = None):
        self._client = httpx.Client(transport=transport, timeout=10.0)

    def close(self) -> None:
        """释放 httpx.Client 资源（lifespan teardown 调用）。"""
        self._client.close()

    def lookup_amount(
        self, lottery_code: str, draw_no: str, draw_date: datetime, tier: int
    ) -> int | None:
        """查询浮动奖金（分）。None = 官方尚未公布。

        异常上抛——由 FloatRefillWorker 统一 catch + 隔离。
        """
        full_issue = rebuild_full_issue(draw_date, draw_no)
        logger.info(
            'cwl_lookup lottery=%s draw_no=%s full_issue=%s tier=%s',
            lottery_code, draw_no, full_issue, tier,
        )
        r = self._client.get(
            'https://www.cwl.gov.cn/cwl_admin/front/cwlkj/search/kjxx/findDrawNotice',
            params={'name': lottery_code, 'code': full_issue},
            headers={
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
                'Referer': 'https://www.cwl.gov.cn/',
            },
        )
        r.raise_for_status()
        body = r.json()

        if body.get('state') != 0:
            logger.info('cwl_state_nonzero state=%s', body.get('state'))
            return None

        result = body.get('result', [])
        if not result:
            logger.info('cwl_result_empty')
            return None

        prizegrades = result[0].get('prizegrades', [])
        for grade in prizegrades:
            if grade.get('type') == tier:
                typemoney = grade.get('typemoney', '_')
                if typemoney == '_':
                    logger.info('cwl_not_published tier=%s', tier)
                    return None
                amount = int(typemoney) * 100  # 元 → 分
                logger.info('cwl_found tier=%s amount=%s', tier, amount)
                return amount

        logger.info('cwl_tier_no_match tier=%s', tier)
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/adapters/test_cwl_prize.py -v`
Expected: 8 PASSED

- [ ] **Step 5: Commit**

```bash
git add app/adapters/cwl_prize.py tests/adapters/test_cwl_prize.py
git commit -m "feat: add CwlPrizeSource adapter for ssq/qlc floating prize lookup"
```

---

### Task 3: SportteryPrizeSource 适配器（JSON + PDF 降级）

**Files:**
- Create: `app/adapters/sporttery_prize.py`
- Test: `tests/adapters/test_sporttery_prize.py`
- Modify: `pyproject.toml`（新增 pypdf）

**Interfaces:**
- Consumes: `rebuild_full_issue` + `rebuild_short_period`（Task 1）、`pypdf`
- Produces: `SportteryPrizeSource` 类（`lookup_amount` + `close`）

**⚠️ 已知风险：** sporttery JSON 字段名未被实际 API 响应验证（EdgeOne 拦截）。QXC `pdf_code` 待确认。若实现时验证发现字段名不匹配，需调整解析逻辑。

- [ ] **Step 1: Add pypdf dependency**

```bash
uv add pypdf
```

- [ ] **Step 2: Write the failing test**

```python
# tests/adapters/test_sporttery_prize.py
"""SportteryPrizeSource 测试——sporttery.cn JSON API + PDF 降级。"""
import io
import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.adapters.sporttery_prize import SportteryPrizeSource

_CST = ZoneInfo('Asia/Shanghai')
_DRAW_DATE = datetime(2026, 7, 19, 21, 30, tzinfo=_CST)

_GAME_NO = {'dlt': '85', 'qxc': '14'}
_PDF_CODE = {'dlt': 'dlt', 'qxc': 'qxc'}  # qxc 待确认


def _mock_transport(handler):
    return httpx.MockTransport(handler)


def _json_response(total, items=None):
    """构造 sporttery JSON 响应。"""
    return {
        'state': 0,
        'data': {
            'total': total,
            'list': items or [],
        },
    }


class TestSportteryJsonHappyPath:
    def test_dlt_tier1(self):
        """JSON 正常查询：匹配 tier，返回分。"""
        def handler(req):
            return httpx.Response(200, json=_json_response(1, [{
                'lotteryUnuseDrawNum': '082',
                'prizeLevelList': [
                    {'prizeLevel': 1, 'stakeAmount': '5000000', 'stakeCount': '5'},
                    {'prizeLevel': 2, 'stakeAmount': '150000', 'stakeCount': '25'},
                ],
            }]))
        src = SportteryPrizeSource(transport=_mock_transport(handler))
        assert src.lookup_amount('dlt', '082', _DRAW_DATE, 1) == 500_000_000
        assert src.lookup_amount('dlt', '082', _DRAW_DATE, 2) == 15_000_000

    def test_issue_rebuild_in_url(self):
        """验证期号重建：URL 中 term 参数为全年份期号。"""
        captured_url = None
        def handler(req):
            nonlocal captured_url
            captured_url = str(req.url)
            return httpx.Response(200, json=_json_response(1, [{
                'lotteryUnuseDrawNum': '082',
                'prizeLevelList': [{'prizeLevel': 1, 'stakeAmount': '5000000', 'stakeCount': '5'}],
            }]))
        src = SportteryPrizeSource(transport=_mock_transport(handler))
        src.lookup_amount('dlt', '082', _DRAW_DATE, 1)
        assert 'term=2026082' in captured_url


class TestSportteryNotPublished:
    """OV#3: JSON 空/无匹配（未公布）→ 直接 None，不降级 PDF。"""

    def test_total_zero_returns_none_no_pdf(self):
        """total=0（未公布）→ 返回 None，不请求 PDF。"""
        json_called = False
        pdf_called = False
        def handler(req):
            nonlocal json_called, pdf_called
            if 'getHistoryPageListV1' in str(req.url):
                json_called = True
                return httpx.Response(200, json=_json_response(0))
            pdf_called = True
            return httpx.Response(404)
        src = SportteryPrizeSource(transport=_mock_transport(handler))
        assert src.lookup_amount('dlt', '082', _DRAW_DATE, 1) is None
        assert json_called
        assert not pdf_called  # 未公布不降级 PDF

    def test_no_matching_draw_returns_none_no_pdf(self):
        """列表中无匹配期号（未公布）→ 返回 None，不请求 PDF。"""
        def handler(req):
            return httpx.Response(200, json=_json_response(1, [{
                'lotteryUnuseDrawNum': '081',  # 不匹配 082
                'prizeLevelList': [{'prizeLevel': 1, 'stakeAmount': '5000000'}],
            }]))
        src = SportteryPrizeSource(transport=_mock_transport(handler))
        assert src.lookup_amount('dlt', '082', _DRAW_DATE, 1) is None


class TestSportteryJsonFallbackToPdf:
    """2A: JSON 故障（解析异常/字段缺失）→ 降级 PDF；httpx 异常上抛不降级。"""

    def test_json_parse_error_fallback_to_pdf(self):
        """JSON 解析失败 → 降级 PDF。"""
        call_count = 0
        def handler(req):
            nonlocal call_count
            call_count += 1
            if 'getHistoryPageListV1' in str(req.url):
                return httpx.Response(200, text='not json{{{')
            # PDF 请求
            return httpx.Response(200, content=b'%PDF-1.4 fake pdf content')
        src = SportteryPrizeSource(transport=_mock_transport(handler))
        # JSON 解析失败会触发 PDF 降级；PDF 内容无效会返回 None
        result = src.lookup_amount('dlt', '082', _DRAW_DATE, 1)
        assert result is None  # PDF 正则无法匹配 fake content
        assert call_count == 2  # JSON + PDF

    def test_json_field_missing_fallback_to_pdf(self):
        """JSON 字段缺失 → 降级 PDF。"""
        call_count = 0
        def handler(req):
            nonlocal call_count
            call_count += 1
            if 'getHistoryPageListV1' in str(req.url):
                # 缺少 prizeLevelList 字段
                return httpx.Response(200, json={'state': 0, 'data': {'total': 1, 'list': [{'lotteryUnuseDrawNum': '082'}]}})
            return httpx.Response(200, content=b'%PDF-1.4 fake')
        src = SportteryPrizeSource(transport=_mock_transport(handler))
        result = src.lookup_amount('dlt', '082', _DRAW_DATE, 1)
        assert result is None
        assert call_count == 2

    def test_http_error_raises_no_fallback(self):
        """HTTP 5xx → 上抛，不降级 PDF（2A：网络故障 ≠ 数据格式问题）。"""
        def handler(req):
            return httpx.Response(500)
        src = SportteryPrizeSource(transport=_mock_transport(handler))
        with pytest.raises(httpx.HTTPStatusError):
            src.lookup_amount('dlt', '082', _DRAW_DATE, 1)


class TestSportteryPdf:
    """PDF 降级路径测试。"""

    def test_pdf_404_returns_none(self):
        """PDF 404 → 未开奖/未公布，返回 None。"""
        def handler(req):
            if 'getHistoryPageListV1' in str(req.url):
                return httpx.Response(200, text='not json')  # JSON 失败
            return httpx.Response(404)
        src = SportteryPrizeSource(transport=_mock_transport(handler))
        assert src.lookup_amount('dlt', '082', _DRAW_DATE, 1) is None

    def test_pdf_too_large_returns_none(self, caplog):
        """PDF > 5MB → skip + log warning（1A 决策）。"""
        def handler(req):
            if 'getHistoryPageListV1' in str(req.url):
                return httpx.Response(200, text='not json')
            # 模拟 >5MB PDF
            return httpx.Response(200, content=b'x' * (6 * 1024 * 1024))
        with caplog.at_level(logging.WARNING):
            src = SportteryPrizeSource(transport=_mock_transport(handler))
            assert src.lookup_amount('dlt', '082', _DRAW_DATE, 1) is None
        assert 'pdf_too_large' in caplog.text

    def test_pdf_period_rebuild(self):
        """验证 period 重建：PDF URL 中为 2 位年份期号。"""
        captured_url = None
        def handler(req):
            nonlocal captured_url
            if 'getHistoryPageListV1' in str(req.url):
                return httpx.Response(200, text='not json')
            captured_url = str(req.url)
            return httpx.Response(404)
        src = SportteryPrizeSource(transport=_mock_transport(handler))
        src.lookup_amount('dlt', '082', _DRAW_DATE, 1)
        assert captured_url is not None
        assert '26082' in captured_url  # period = 26 + 082

    def test_close(self):
        """close() 释放 client 资源。"""
        src = SportteryPrizeSource(transport=_mock_transport(lambda r: httpx.Response(200)))
        src.close()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/adapters/test_sporttery_prize.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.adapters.sporttery_prize'`

- [ ] **Step 4: Write minimal implementation**

```python
# app/adapters/sporttery_prize.py
"""SportteryPrizeSource——中国体彩网（sporttery.cn）官方奖金查询。

覆盖彩种：dlt（大乐透）、qxc（七星彩）。
数据源：sporttery.cn JSON API 主 + PDF 降级（pypdf 正则解析）。
"""
import io
import json
import logging
import re
from datetime import datetime

import httpx
import pypdf

from app.adapters.base import rebuild_full_issue, rebuild_short_period

logger = logging.getLogger(__name__)

# 项目彩种 code → sporttery gameNo 映射
_GAME_NO = {
    'dlt': '85',
    'qxc': '14',
}

# 项目彩种 code → sporttery PDF 路径 code 映射
# ⚠️ qxc 待确认（实现时通过 sporttery 页面抓取确认）
_PDF_CODE = {
    'dlt': 'dlt',
    'qxc': 'qxc',
}

# PDF 大小限制（1A 决策：防异常大 PDF 导致内存膨胀）
_PDF_MAX_BYTES = 5 * 1024 * 1024  # 5MB


class SportteryPrizeSource:
    """中国体彩网浮动奖金查询。JSON 主 → PDF 降级。"""

    name = 'sporttery'

    def __init__(self, transport: httpx.BaseTransport | None = None):
        self._client = httpx.Client(transport=transport, timeout=10.0)

    def close(self) -> None:
        """释放 httpx.Client 资源（lifespan teardown 调用）。"""
        self._client.close()

    def lookup_amount(
        self, lottery_code: str, draw_no: str, draw_date: datetime, tier: int
    ) -> int | None:
        """统一入口：JSON 主 → PDF 降级。

        异常分类（2A 决策）：
        - JSON 解析异常/字段缺失 → 降级 PDF（数据格式问题）
        - httpx 异常（网络故障）→ 上抛（PDF 站点大概率也不可达）
        - JSON 空/无匹配（未公布）→ 直接 None，不降级 PDF（OV#3）
        """
        try:
            result = self._lookup_json(lottery_code, draw_no, draw_date, tier)
            return result  # 包括 None（未公布）
        except (json.JSONDecodeError, KeyError, TypeError, IndexError) as exc:
            logger.info(
                'sporttery_json_fallback lottery=%s draw_no=%s error=%s',
                lottery_code, draw_no, type(exc).__name__,
            )
            return self._lookup_pdf(lottery_code, draw_no, draw_date, tier)
        # httpx 异常不 catch，上抛给 refill_worker

    def _lookup_json(
        self, lottery_code: str, draw_no: str, draw_date: datetime, tier: int
    ) -> int | None:
        """JSON API 查询。返回 None = 未公布（不触发 PDF 降级）。"""
        game_no = _GAME_NO[lottery_code]
        full_issue = rebuild_full_issue(draw_date, draw_no)
        logger.info(
            'sporttery_json_lookup lottery=%s draw_no=%s full_issue=%s tier=%s',
            lottery_code, draw_no, full_issue, tier,
        )
        r = self._client.get(
            'https://webapi.sporttery.cn/gateway/lottery/getHistoryPageListV1.qry',
            params={
                'gameNo': game_no,
                'provinceId': '0',
                'pageSize': '1',
                'isVerify': '1',
                'pageNo': '1',
                'term': full_issue,
            },
            headers={
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
            },
        )
        r.raise_for_status()
        body = r.json()

        data = body.get('data', {})
        total = data.get('total', 0)
        if total == 0:
            logger.info('sporttery_json_not_published total=0')
            return None

        items = data.get('list', [])
        for item in items:
            if item.get('lotteryUnuseDrawNum') == draw_no:
                # ⚠️ 字段名未被实际 API 响应验证——若实现时不匹配需调整
                prize_list = item['prizeLevelList']  # KeyError → 降级 PDF
                for prize in prize_list:
                    if prize.get('prizeLevel') == tier:
                        amount_str = prize.get('stakeAmount', '0')
                        amount = int(amount_str) * 100  # 元 → 分
                        logger.info('sporttery_json_found tier=%s amount=%s', tier, amount)
                        return amount
                logger.info('sporttery_json_tier_no_match tier=%s', tier)
                return None

        logger.info('sporttery_json_draw_no_match draw_no=%s', draw_no)
        return None

    def _lookup_pdf(
        self, lottery_code: str, draw_no: str, draw_date: datetime, tier: int
    ) -> int | None:
        """PDF 降级：下载官方公告 PDF，pypdf 提取文本，正则匹配奖金。"""
        pdf_code = _PDF_CODE.get(lottery_code)
        if pdf_code is None:
            logger.warning('sporttery_pdf_no_code lottery=%s', lottery_code)
            return None

        period = rebuild_short_period(draw_date, draw_no)
        url = f'https://pdf.sporttery.cn/{pdf_code}/{period}/{period}.pdf'
        logger.info(
            'sporttery_pdf_lookup lottery=%s period=%s url=%s',
            lottery_code, period, url,
        )
        r = self._client.get(url)
        if r.status_code == 404:
            logger.info('sporttery_pdf_404 period=%s', period)
            return None
        r.raise_for_status()

        if len(r.content) > _PDF_MAX_BYTES:
            logger.warning(
                'pdf_too_large size=%s limit=%s period=%s',
                len(r.content), _PDF_MAX_BYTES, period,
            )
            return None

        try:
            reader = pypdf.PdfReader(io.BytesIO(r.content))
            text = '\n'.join(page.extract_text() for page in reader.pages)
        except Exception:
            logger.warning(
                'sporttery_pdf_parse_failed period=%s',
                period, exc_info=True,
            )
            return None

        # 正则匹配奖金（含千分位逗号）
        amount = self._parse_pdf_amount(text, tier, lottery_code)
        if amount is not None:
            logger.info('sporttery_pdf_found tier=%s amount=%s', tier, amount)
        else:
            logger.info('sporttery_pdf_no_match tier=%s', tier)
        return amount

    @staticmethod
    def _parse_pdf_amount(text: str, tier: int, lottery_code: str) -> int | None:
        """从 PDF 文本中提取指定奖级的奖金（分）。

        ⚠️ 正则基于预期格式，实现时需用真实 PDF 验证并可能调整。
        金额含千分位逗号（如 '5,000,000'），需去逗号后转 int。
        """
        # 匹配模式：奖级 N 后面的金额数字（含千分位）
        # 预期格式示例："一等奖  5注  5,000,000元"
        pattern = rf'{"一二三四五六七八九"[tier - 1]}等奖\s+[\d,]+\s*注\s+([\d,]+)\s*元'
        match = re.search(pattern, text)
        if match:
            amount_str = match.group(1).replace(',', '')
            return int(amount_str) * 100  # 元 → 分
        return None
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/adapters/test_sporttery_prize.py -v`
Expected: 10 PASSED

- [ ] **Step 6: Commit**

```bash
git add app/adapters/sporttery_prize.py tests/adapters/test_sporttery_prize.py pyproject.toml uv.lock
git commit -m "feat: add SportteryPrizeSource adapter (JSON + PDF fallback) for dlt/qxc"
```

---

### Task 4: FloatRefillWorker 修改（签名 + verified 过滤 + 金额公式 + 分组限流）

**Files:**
- Modify: `app/services/refill_service.py`
- Test: `tests/services/test_refill_service.py`（追加新测试）

**Interfaces:**
- Consumes: `Ticket`（append, multiplier）、`PrizeTier`（append_multiplier）、`DrawResult`（verified, draw_date）
- Produces: 修改后的 `FloatRefillWorker`（签名含 draw_date，金额公式完整）

- [ ] **Step 1: Write the failing test**

在 `tests/services/test_refill_service.py` 末尾追加：

```python
# --- 新增测试（Plan: 浮动奖金查询接口对接）---

from datetime import UTC
from unittest.mock import MagicMock, patch, call
from zoneinfo import ZoneInfo

from app.models import DrawResult, Ticket
from app.domain.prize_tables import get_tiers


def _seed_float_win_with_ticket(engine, days_ago=0, tier=1, suffix='',
                                 append=False, multiplier=1, verified=True,
                                 lottery_code=None):
    """seed 带完整 Ticket 属性的浮动奖中奖 comparison。
    lottery_code 默认根据 append 推断（dlt/ssq），可显式覆盖。"""
    code = lottery_code or ('dlt' if append else 'ssq')
    with Session(engine) as s:
        u = User(username=f'u{suffix}', password_hash='x', role='user', invite_code='C')
        s.add(u)
        s.commit()
        s.refresh(u)
        dr = DrawResult(
            lottery_code=code,
            draw_no=f'082{suffix}',
            draw_date=datetime(2026, 7, 19, 21, 30, tzinfo=ZoneInfo('Asia/Shanghai')) - timedelta(days=days_ago),
            numbers_json='{"front":[1,2,3,4,5],"back":[1,2]}' if code == 'dlt' else '{"front":[1,2,3,4,5,6],"back":[7]}',
            source='mxnzp',
            verified=verified,
            version=1,
        )
        s.add(dr)
        s.commit()
        s.refresh(dr)
        t = Ticket(
            user_id=u.id,
            lottery_code=code,
            play_type='single',
            numbers_json='{"front":[1,2,3,4,5],"back":[1,2]}' if code == 'dlt' else '{"front":[1,2,3,4,5,6],"back":[7]}',
            multiplier=multiplier,
            append=append,
            cost=200 * multiplier + (100 if append else 0),
        )
        s.add(t)
        s.commit()
        s.refresh(t)
        cmp = Comparison(
            user_id=u.id,
            draw_result_id=dr.id,
            ticket_id=t.id,
            hits_json='{}',
            prize_tier=tier,
            prize_amount=None,
            is_win=True,
            created_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days_ago),
        )
        s.add(cmp)
        s.commit()
        s.refresh(cmp)
        return cmp.id, t.id


class TestRefillAmountFormula:
    """金额公式正确性（OV1：倍投必须应用）。"""

    def test_refill_applies_multiplier(self, db_engine):
        """倍投注：amount = base * multiplier。"""
        cmp_id, _ = _seed_float_win_with_ticket(db_engine, multiplier=3)
        worker = FloatRefillWorker(
            db_engine,
            amount_lookup=MagicMock(return_value=1_000_000),  # 1万分
            max_age_days=7,
        )
        n = worker.refill()
        assert n == 1
        with Session(db_engine) as s:
            c = s.get(Comparison, cmp_id)
            assert c.prize_amount == 1_000_000 * 3  # 3倍投

    def test_refill_applies_append_multiplier(self, db_engine):
        """追加以：amount = base * append_multiplier（dlt tier 1 = 1.8x）。"""
        cmp_id, _ = _seed_float_win_with_ticket(db_engine, append=True, tier=1)
        worker = FloatRefillWorker(
            db_engine,
            amount_lookup=MagicMock(return_value=1_000_000),
            max_age_days=7,
        )
        n = worker.refill()
        assert n == 1
        with Session(db_engine) as s:
            c = s.get(Comparison, cmp_id)
            assert c.prize_amount == int(1_000_000 * 1.8)  # 追加1.8x

    def test_refill_applies_both_append_and_multiplier(self, db_engine):
        """追加+倍投：amount = base * append_multiplier * multiplier。"""
        cmp_id, _ = _seed_float_win_with_ticket(db_engine, append=True, multiplier=5, tier=1)
        worker = FloatRefillWorker(
            db_engine,
            amount_lookup=MagicMock(return_value=1_000_000),
            max_age_days=7,
        )
        n = worker.refill()
        assert n == 1
        with Session(db_engine) as s:
            c = s.get(Comparison, cmp_id)
            assert c.prize_amount == int(1_000_000 * 1.8) * 5  # 追加1.8x × 5倍投

    def test_refill_append_guard_none_multiplier(self, db_engine):
        """append=True 但彩种无 append_multiplier（数据异常）→ guard 跳过乘法（4A）。

        ssq 的 PrizeTier.append_multiplier 默认为 1.0（非 None），但 guard 条件是
        `if ticket.append and tier_info.append_multiplier`——1.0 为 truthy 会乘。
        真正 guard 场景是 _find_tier 返回 None（未知彩种）或 append_multiplier=0/None。
        此处测试 _find_tier 对未知彩种返回 None 时 guard 不 crash。
        """
        # seed 一个 dlt 彩种但 tier=99（不在 prize_tables 中）→ _find_tier 返回 None
        with Session(db_engine) as s:
            u = User(username='uguard', password_hash='x', role='user', invite_code='C')
            s.add(u)
            s.commit()
            s.refresh(u)
            dr = DrawResult(
                lottery_code='dlt', draw_no='082g',
                draw_date=datetime(2026, 7, 19, 21, 30, tzinfo=ZoneInfo('Asia/Shanghai')),
                numbers_json='{"front":[1,2,3,4,5],"back":[1,2]}',
                source='mxnzp', verified=True, version=1,
            )
            s.add(dr)
            s.commit()
            s.refresh(dr)
            t = Ticket(
                user_id=u.id, lottery_code='dlt', play_type='single',
                numbers_json='{"front":[1,2,3,4,5],"back":[1,2]}',
                multiplier=2, append=True, cost=400,
            )
            s.add(t)
            s.commit()
            s.refresh(t)
            cmp = Comparison(
                user_id=u.id, draw_result_id=dr.id, ticket_id=t.id,
                hits_json='{}', prize_tier=99,  # 不存在的 tier
                prize_amount=None, is_win=True,
                created_at=datetime.now(UTC).replace(tzinfo=None),
            )
            s.add(cmp)
            s.commit()
            s.refresh(cmp)
            cmp_id = cmp.id

        worker = FloatRefillWorker(
            db_engine,
            amount_lookup=MagicMock(return_value=1_000_000),
            max_age_days=7,
        )
        n = worker.refill()
        assert n == 1
        with Session(db_engine) as s:
            c = s.get(Comparison, cmp_id)
            # _find_tier('dlt', 99) 返回 None → guard 跳过 append 乘法
            # 只乘 multiplier：1_000_000 * 2
            assert c.prize_amount == 1_000_000 * 2


class TestRefillVerifiedFilter:
    """OV4：只回填 verified=True 的 draw_results。"""

    def test_refill_skips_unverified_draw(self, db_engine):
        """verified=False 的 comparison 不回填。"""
        cmp_id, _ = _seed_float_win_with_ticket(db_engine, verified=False)
        worker = FloatRefillWorker(
            db_engine,
            amount_lookup=MagicMock(return_value=999),
            max_age_days=7,
        )
        n = worker.refill()
        assert n == 0  # 不回填
        with Session(db_engine) as s:
            c = s.get(Comparison, cmp_id)
            assert c.prize_amount is None  # 仍为 None


class TestRefillLookupSignature:
    """1A：amount_lookup 签名扩展 draw_date。"""

    def test_lookup_receives_draw_date(self, db_engine):
        """amount_lookup 被调用时传入 draw_date 参数。"""
        cmp_id, _ = _seed_float_win_with_ticket(db_engine)
        mock_lookup = MagicMock(return_value=1_000_000)
        worker = FloatRefillWorker(db_engine, amount_lookup=mock_lookup, max_age_days=7)
        worker.refill()
        mock_lookup.assert_called_once()
        args = mock_lookup.call_args
        # 签名：(lottery_code, draw_no, draw_date, tier)
        assert len(args[0]) == 4  # 4 个位置参数
        assert isinstance(args[0][2], datetime)  # 第 3 个是 draw_date: datetime


class TestRefillRateLimit:
    """OV3：限流——按 lottery_code 分组，同组内 sleep。"""

    def test_sleep_called_between_lookups(self, db_engine):
        """验证 sleep(0.5) 在同彩种多次 lookup 间被调用（OV2：per-host 限流）。"""
        # 两个同彩种（ssq）comparison，同组 2 行触发 1 次 sleep
        _seed_float_win_with_ticket(db_engine, suffix='_a')
        _seed_float_win_with_ticket(db_engine, suffix='_b')
        worker = FloatRefillWorker(
            db_engine,
            amount_lookup=MagicMock(return_value=1_000_000),
            max_age_days=7,
        )
        with patch('app.services.refill_service.time.sleep') as mock_sleep:
            worker.refill()
        # 同组 2 行，第 1 行后 sleep，第 2 行（最后）不 sleep
        assert mock_sleep.call_count == 1
        mock_sleep.assert_called_with(0.5)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/services/test_refill_service.py -v -k "TestRefillAmountFormula or TestRefillVerifiedFilter or TestRefillLookupSignature or TestRefillRateLimit"`
Expected: FAIL（新测试类不存在/签名不匹配）

- [ ] **Step 3: Write minimal implementation**

修改 `app/services/refill_service.py`：

```python
"""浮奖回填：一二等奖 prize_amount=null 的，轮询官方金额回填，max_age_days 上限。

金额公式（OV1/4A）：
  base = amount_lookup(code, draw_no, draw_date, tier)  # 基础奖金（分）
  if ticket.append and tier_info.append_multiplier:     # 追加 guard（4A）
      base = int(base * tier_info.append_multiplier)    # 追加 1.8x
  base *= ticket.multiplier                              # 倍投（OV1）
  prize_amount = base
"""
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from app.domain.prize_tables import get_tiers
from app.models import Comparison, DrawResult, Ticket

logger = logging.getLogger(__name__)


def _cutoff_naive_utc(days: int) -> datetime:
    """回填窗口下限——**naive UTC**，刻意与 Comparison.created_at 同时区比较。"""
    return datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days)


class FloatRefillWorker:
    """浮奖回填：一二等奖 prize_amount=null 的，轮询官方金额回填，max_age_days 上限。"""

    def __init__(
        self,
        engine: Engine,
        amount_lookup: Callable[[str, str, datetime, int], int | None],
        max_age_days: int = 7,
    ):
        self._engine = engine
        self._lookup = amount_lookup  # (lottery_code, draw_no, draw_date, tier) -> 分 | None
        self._max_age = max_age_days

    def refill(self) -> int:
        cutoff = _cutoff_naive_utc(self._max_age)
        refilled = 0
        with Session(self._engine) as s:
            # 显式限定 prize_tier IN (1,2) —— 仅浮动档（spec §7.1 明文「一二等奖」）
            # OV4: join DrawResult 过滤 verified=True（只回填已验证数据）
            pending = list(
                s.exec(
                    select(Comparison)
                    .join(DrawResult, Comparison.draw_result_id == DrawResult.id)
                    .where(
                        Comparison.is_win == True,  # noqa: E712
                        Comparison.prize_tier.in_((1, 2)),
                        Comparison.prize_amount.is_(None),
                        Comparison.unresolved == False,  # noqa: E712
                        Comparison.created_at >= cutoff,
                        DrawResult.verified == True,  # noqa: E712  # OV4
                    )
                ).all()
            )
            # 预载 draw_result 映射（拿 lottery_code/draw_no/draw_date）
            dr_ids = {c.draw_result_id for c in pending}
            drs = {dr.id: dr for dr in s.exec(select(DrawResult).where(DrawResult.id.in_(dr_ids))).all()}
            # 预载 ticket 映射（拿 append/multiplier）
            ticket_ids = {c.ticket_id for c in pending}
            tickets = {t.id: t for t in s.exec(select(Ticket).where(Ticket.id.in_(ticket_ids))).all()}

        # 按 lottery_code 分组（OV2：per-host 限流，同组内 sleep）
        grouped: dict[str, list[tuple[Comparison, DrawResult, Ticket]]] = {}
        for cmp in pending:
            dr = drs.get(cmp.draw_result_id)
            ticket = tickets.get(cmp.ticket_id)
            if dr is None or ticket is None or cmp.prize_tier is None:
                continue
            grouped.setdefault(dr.lottery_code, []).append((cmp, dr, ticket))

        for lottery_code, rows in grouped.items():
            for i, (cmp, dr, ticket) in enumerate(rows):
                try:
                    amount = self._lookup(dr.lottery_code, dr.draw_no, dr.draw_date, cmp.prize_tier)
                except Exception:
                    logger.warning(
                        'refill_skip_lookup_failed comparison_id=%s lottery=%s draw_no=%s tier=%s',
                        cmp.id, dr.lottery_code, dr.draw_no, cmp.prize_tier,
                        exc_info=True,
                    )
                    continue
                if amount is not None:
                    # 金额公式：base → append_multiplier → multiplier
                    tier_info = self._find_tier(dr.lottery_code, cmp.prize_tier)
                    if ticket.append and tier_info and tier_info.append_multiplier:
                        amount = int(amount * tier_info.append_multiplier)  # 追加
                    amount *= ticket.multiplier  # 倍投（OV1）
                    with Session(self._engine) as s:
                        c = s.get(Comparison, cmp.id)
                        c.prize_amount = amount
                        s.commit()
                    refilled += 1
                # 同组内限流（OV3），最后一个不 sleep
                if i < len(rows) - 1:
                    time.sleep(0.5)

        self._mark_expired_unresolved(cutoff)
        return refilled

    @staticmethod
    def _find_tier(lottery_code: str, tier: int):
        """从 prize_tables 查找指定奖级信息。"""
        try:
            for t in get_tiers(lottery_code):
                if t.tier == tier:
                    return t
        except KeyError:
            pass
        return None

    def _mark_expired_unresolved(self, cutoff: datetime) -> None:
        """超期未回填的浮动奖标 unresolved=True。"""
        with Session(self._engine) as s:
            expired = list(
                s.exec(
                    select(Comparison).where(
                        Comparison.is_win == True,  # noqa: E712
                        Comparison.prize_tier.in_((1, 2)),
                        Comparison.prize_amount.is_(None),
                        Comparison.unresolved == False,  # noqa: E712
                        Comparison.created_at < cutoff,
                    )
                ).all()
            )
            for cmp in expired:
                cmp.unresolved = True
            if expired:
                s.commit()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/services/test_refill_service.py -v`
Expected: ALL PASSED（含既有 5 个测试 + 新增 7 个测试）

- [ ] **Step 5: Commit**

```bash
git add app/services/refill_service.py tests/services/test_refill_service.py
git commit -m "feat: refill signature + verified filter + amount formula (append*multiplier) + rate limit"
```

---

### Task 5: main.py 接线 + 路由闭包 + lifespan close + 冒烟验证

**Files:**
- Modify: `app/main.py`
- Test: 无需新测试文件（冒烟验证手动检查，路由闭包逻辑简单）

**Interfaces:**
- Consumes: `CwlPrizeSource`（Task 2）、`SportteryPrizeSource`（Task 3）、`FloatRefillWorker`（Task 4）
- Produces: `_build_amount_lookup` 路由闭包 + `validate_startup` 扩展

- [ ] **Step 1: 替换 `_amount_lookup_stub` 为真实路由闭包**

在 `app/main.py` 中，替换 `_amount_lookup_stub` 函数为 `_build_amount_lookup`：

```python
def _build_amount_lookup(cwl, sporttery):
    """构建路由闭包：按彩种分发到对应 PrizeSource。

    ssq/qlc → cwl（中彩网）
    dlt/qxc → sporttery（中国体彩网）
    其他（fc3d/pl3/pl5 固定档）→ None（不查询）
    """
    _CWL_CODES = frozenset({'ssq', 'qlc'})
    _SPORTTERY_CODES = frozenset({'dlt', 'qxc'})

    def amount_lookup(lottery_code: str, draw_no: str, draw_date, tier: int) -> int | None:
        if lottery_code in _CWL_CODES:
            return cwl.lookup_amount(lottery_code, draw_no, draw_date, tier)
        if lottery_code in _SPORTTERY_CODES:
            return sporttery.lookup_amount(lottery_code, draw_no, draw_date, tier)
        return None  # 固定档彩种不查询

    return amount_lookup
```

- [ ] **Step 2: 在 `_build_scheduler_and_deps` 中实例化适配器并接线**

在 `_build_scheduler_and_deps` 函数中，`refill = FloatRefillWorker(...)` 行之前添加：

```python
    from app.adapters.cwl_prize import CwlPrizeSource
    from app.adapters.sporttery_prize import SportteryPrizeSource

    cwl = CwlPrizeSource()
    sporttery = SportteryPrizeSource()
    amount_lookup = _build_amount_lookup(cwl, sporttery)
    refill = FloatRefillWorker(engine, amount_lookup=amount_lookup)
```

并删除旧的 `refill = FloatRefillWorker(engine, amount_lookup=_amount_lookup_stub)` 行。

在 deps dict 中添加 cwl/sporttery 引用（供 lifespan close）：

```python
    deps = {
        'engine': engine,
        'fetch_service': fetch,
        'compare_service': compare,
        'refill_worker': refill,
        'notifier': notifier,
        'cwl_prize': cwl,
        'sporttery_prize': sporttery,
    }
```

在 lifespan 中 `_build_scheduler_and_deps` 调用之后，将 deps 挂到 `app.state`（供 teardown close）：

```python
        sched, deps = _build_scheduler_and_deps(engine, settings)
        app.state._deps = deps  # 供 lifespan teardown close 适配器
```

- [ ] **Step 3: lifespan teardown 中 close 适配器**

在 lifespan 的 `finally` 块（`notifier.close()` 之后）添加：

```python
    # close 奖金查询适配器 client
    deps = getattr(app.state, '_deps', None)
    if deps:
        for key in ('cwl_prize', 'sporttery_prize'):
            adapter = deps.get(key)
            if adapter and hasattr(adapter, 'close'):
                try:
                    adapter.close()
                except Exception:
                    logger.warning('adapter_close_failed key=%s', key, exc_info=True)
```

- [ ] **Step 4: validate_startup 添加 cwl + sporttery 冒烟验证（OV2/OV8）**

在 `validate_startup()` 函数末尾添加：

```python
    # 奖金查询 API 字段名冒烟验证（OV2/OV8）：启动时确认 API 可用且字段名匹配。
    # 不匹配则 log error 但不阻止启动——PDF 降级可能仍可用。
    _smoke_check_prize_sources(settings, log)


def _smoke_check_prize_sources(settings: Settings, log: logging.Logger) -> None:
    """启动冒烟：验证 cwl + sporttery API 字段名匹配。"""
    import httpx

    # cwl 冒烟
    try:
        r = httpx.get(
            'https://www.cwl.gov.cn/cwl_admin/front/cwlkj/search/kjxx/findDrawNotice',
            params={'name': 'ssq', 'code': '2026082'},
            headers={'User-Agent': 'Mozilla/5.0'},
            timeout=5.0,
        )
        body = r.json()
        if 'result' not in body or 'state' not in body:
            log.error('smoke_cwl_field_mismatch: missing result/state in response')
        elif body.get('result') and 'prizegrades' not in body['result'][0]:
            log.error('smoke_cwl_field_mismatch: missing prizegrades in result[0]')
        else:
            log.info('smoke_cwl_ok')
    except Exception as exc:
        log.error('smoke_cwl_failed: %s', exc)

    # sporttery 冒烟
    try:
        r = httpx.get(
            'https://webapi.sporttery.cn/gateway/lottery/getHistoryPageListV1.qry',
            params={'gameNo': '85', 'provinceId': '0', 'pageSize': '1', 'isVerify': '1', 'pageNo': '1'},
            headers={'User-Agent': 'Mozilla/5.0'},
            timeout=5.0,
        )
        body = r.json()
        data = body.get('data', {})
        items = data.get('list', [])
        if items and 'prizeLevelList' not in items[0]:
            log.error('smoke_sporttery_field_mismatch: missing prizeLevelList in list[0]')
        else:
            log.info('smoke_sporttery_ok')
    except Exception as exc:
        log.error('smoke_sporttery_failed: %s', exc)
```

- [ ] **Step 5: 删除 `_amount_lookup_stub`**

删除 `_amount_lookup_stub` 函数（不再使用）。

- [ ] **Step 6: 验证现有测试不受影响**

Run: `uv run pytest tests/test_health.py -v`
Expected: PASS（startup 逻辑不变，只新增冒烟验证）

- [ ] **Step 7: Commit**

```bash
git add app/main.py
git commit -m "feat: wire prize sources in main.py, replace stub, add smoke check + close"
```

---

### Task 6: 调度器新增 22:00 开奖日回填轮

**Files:**
- Modify: `app/scheduler/jobs.py`
- Test: 无需新测试（cron 注册逻辑简单，已有 `test_register_all_jobs` 覆盖）

**Interfaces:**
- Consumes: 现有 `_run_float_refill`（不变）
- Produces: 新增 `float_refill_night` cron job

- [ ] **Step 1: 在 `register_all_jobs` 中添加 22:00 cron job**

在现有 `float_refill` job 注册之后添加：

```python
    # 浮奖回填：开奖日当晚 22:00（1C 决策——开奖后不久官方可能已公布金额）
    sched.add_job(
        _run_float_refill,
        'cron',
        hour=22,
        minute=0,
        id='float_refill_night',
        args=[db_url],
        replace_existing=True,
    )
```

- [ ] **Step 2: 验证 job 注册**

Run: `uv run pytest tests/scheduler/ -v -k "register"`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add app/scheduler/jobs.py
git commit -m "feat: add draw-night 22:00 refill cron job"
```

---

### Task 7: Golden-file PDF fixture 测试

**Files:**
- Create: `tests/fixtures/dlt_sample.pdf`（实现时从 sporttery 抓取真实 PDF）
- Modify: `tests/adapters/test_sporttery_prize.py`（追加 golden-file 测试）

**Interfaces:**
- Consumes: `SportteryPrizeSource._parse_pdf_amount`
- Produces: golden-file 测试防 pypdf 版本漂移

**⚠️ 此 task 需要在有网络的环境中执行（抓取真实 PDF）。若当前环境被 EdgeOne 拦截，可暂时跳过，在 NAS 部署后补做。**

- [ ] **Step 1: 抓取真实 DLT PDF 并存入 fixtures**

```bash
# 从 sporttery 下载最近一期大乐透公告 PDF（需真实网络）
# 期号需替换为实际最新期号
curl -o tests/fixtures/dlt_sample.pdf "https://pdf.sporttery.cn/dlt/26082/26082.pdf"
```

- [ ] **Step 2: Write the golden-file test**

在 `tests/adapters/test_sporttery_prize.py` 末尾追加：

```python
class TestSportteryPdfGoldenFile:
    """Golden-file 测试：真实 PDF 正则解析，防 pypdf 版本漂移（OV#9）。"""

    @pytest.fixture
    def sample_pdf_path(self):
        from pathlib import Path
        return Path(__file__).parent.parent / 'fixtures' / 'dlt_sample.pdf'

    def test_parse_real_pdf(self, sample_pdf_path):
        """用真实 PDF 验证 _parse_pdf_amount 正则匹配。"""
        if not sample_pdf_path.exists():
            pytest.skip('PDF fixture not available (download from sporttery)')
        import pypdf
        reader = pypdf.PdfReader(str(sample_pdf_path))
        text = '\n'.join(page.extract_text() for page in reader.pages)
        # 验证能提取到一等奖金额（非 None）
        amount = SportteryPrizeSource._parse_pdf_amount(text, 1, 'dlt')
        assert amount is not None
        assert amount > 0
        # 验证能提取到二等奖金额
        amount2 = SportteryPrizeSource._parse_pdf_amount(text, 2, 'dlt')
        assert amount2 is not None
        assert amount2 > 0
```

- [ ] **Step 3: Run test**

Run: `uv run pytest tests/adapters/test_sporttery_prize.py::TestSportteryPdfGoldenFile -v`
Expected: PASS（若 fixture 可用）或 SKIP

- [ ] **Step 4: Commit**

```bash
git add tests/fixtures/dlt_sample.pdf tests/adapters/test_sporttery_prize.py
git commit -m "test: add golden-file PDF fixture test for pypdf regex regression"
```

---

### Task 8: 全量验证

- [ ] **Step 1: 运行全量测试**

```bash
uv run pytest -v
```

Expected: ALL PASSED（318 + 新增 ~20 个测试）

- [ ] **Step 2: import-linter 验证**

```bash
uv run lint-imports
```

Expected: PASS（app.domain 不得 import adapters）

- [ ] **Step 3: ruff 检查**

```bash
uv run ruff check app/adapters/cwl_prize.py app/adapters/sporttery_prize.py app/adapters/base.py app/services/refill_service.py app/main.py app/scheduler/jobs.py
```

Expected: 无 error

- [ ] **Step 4: 最终 Commit**

```bash
git add -A
git commit -m "chore: final cleanup for floating prize lookup implementation"
```

---

## Self-Review Checklist

### Spec Coverage

| Spec 节 | 覆盖 Task |
|---------|----------|
| §2 架构（PrizeSource Protocol + 适配器） | T1, T2, T3 |
| §3 CwlPrizeSource | T2 |
| §4 SportteryPrizeSource | T3 |
| §5 FloatRefillWorker 修改 | T4 |
| §6 main.py 接线 | T5 |
| §10.2 金额公式（追加 × 倍投） | T4 |
| §10.3 verified 过滤 | T4 |
| §10.4 调度变更（22:00） | T6 |
| §11.1 PDF 安全（BytesIO + 5MB） | T3 |
| §11.1 降级异常分类 | T3 |
| §11.2 未公布不降级 | T3 |
| §11.3 draw_date 时区契约 | T1（docstring） |
| §11.4 multiplier 所有权 | T4（docstring） |
| OV#1 close() + lifespan | T2, T3, T5 |
| OV#2 per-host 限流 | T4 |
| OV#8 冒烟对称 | T5 |
| OV#9 golden-file | T7 |
| OV#10 范围确认 | 全部 |

### Placeholder Scan

- ✅ 无 TBD/TODO/"implement later"
- ✅ 所有代码步骤含完整代码块
- ✅ 所有测试含具体断言
- ⚠️ sporttery JSON 字段名标注为"未被实际 API 响应验证"——这是已知风险，非 placeholder
- ⚠️ QXC pdf_code 标注为"待确认"——同上
- ⚠️ T7 golden-file 需在 NAS 环境抓取真实 PDF——已标注为可跳过

### Type Consistency

- `amount_lookup` 签名统一为 `(lottery_code: str, draw_no: str, draw_date: datetime, tier: int) -> int | None`
- `PrizeSource` Protocol 签名与实现一致
- `_build_amount_lookup` 路由闭包签名与 `FloatRefillWorker.__init__` 的 `amount_lookup` 参数一致
- `draw_date` 类型统一为 `datetime`（aware CST）
