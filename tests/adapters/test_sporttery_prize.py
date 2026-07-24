"""SportteryPrizeSource 测试——sporttery.cn JSON API + PDF 降级。"""
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.adapters.base import PermanentLookupError
from app.adapters.sporttery_prize import SportteryPrizeSource

_CST = ZoneInfo('Asia/Shanghai')
_DRAW_DATE = datetime(2026, 7, 19, 21, 30, tzinfo=_CST)


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
        """JSON 解析失败 → 降级 PDF。

        Review round 1 Finding 2：pypdf 解析失败（损坏 PDF）现在 raise PermanentLookupError
        而非 return None。此测试验证降级确实发生（call_count==2），并验证 Finding 2 的契约。
        """
        call_count = 0
        def handler(req):
            nonlocal call_count
            call_count += 1
            if 'getHistoryPageListV1' in str(req.url):
                return httpx.Response(200, text='not json{{{')
            # 损坏 PDF（pypdf 解析失败）→ Finding 2: PermanentLookupError
            return httpx.Response(200, content=b'%PDF-1.4 fake pdf content')
        src = SportteryPrizeSource(transport=_mock_transport(handler))
        # JSON 解析失败 → 降级 PDF；PDF 解析失败 → PermanentLookupError（Finding 2）
        with pytest.raises(PermanentLookupError):
            src.lookup_amount('dlt', '082', _DRAW_DATE, 1)
        assert call_count == 2  # JSON + PDF（验证降级发生）

    def test_json_field_missing_fallback_to_pdf(self):
        """JSON 字段缺失 → 降级 PDF；PDF 解析失败 → PermanentLookupError（Finding 2）。"""
        call_count = 0
        def handler(req):
            nonlocal call_count
            call_count += 1
            if 'getHistoryPageListV1' in str(req.url):
                # 缺少 prizeLevelList 字段
                return httpx.Response(200, json={'state': 0, 'data': {'total': 1, 'list': [{'lotteryUnuseDrawNum': '082'}]}})
            return httpx.Response(200, content=b'%PDF-1.4 fake')
        src = SportteryPrizeSource(transport=_mock_transport(handler))
        with pytest.raises(PermanentLookupError):
            src.lookup_amount('dlt', '082', _DRAW_DATE, 1)
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

    def test_pdf_too_large_raises_permanent(self, caplog):
        """Review round 4 Finding A: PDF > 5MB → PermanentLookupError（非 return None）。

        超 5MB 的 PDF 是 stable-permanent 条件：同一 URL 每轮返回同一超限 PDF。
        return None 会被 worker（refill_service.py `if amount is not None`）当「未公布」，
        每轮重复下载同一 5MB+ blob 共 7 天（带宽 + 日志噪音），最终由
        _mark_expired_unresolved 兜底标记（与 Finding 2/4 同一 silent trap）。
        """
        def handler(req):
            if 'getHistoryPageListV1' in str(req.url):
                return httpx.Response(200, text='not json')
            # 模拟 >5MB PDF
            return httpx.Response(200, content=b'x' * (6 * 1024 * 1024))
        with caplog.at_level(logging.WARNING):
            src = SportteryPrizeSource(transport=_mock_transport(handler))
            with pytest.raises(PermanentLookupError):
                src.lookup_amount('dlt', '082', _DRAW_DATE, 1)
        assert 'pdf_too_large' in caplog.text

    def test_pdf_code_missing_raises_permanent(self, caplog):
        """Review round 4 Finding B: 彩种不在 _PDF_CODE → PermanentLookupError（非 return None）。

        _PDF_CODE 缺失是编程/配置错误（非「未公布」上游状态）。return None 会被 worker
        当「未公布」重试 7 天后由 _mark_expired_unresolved 兜底标记，真实 bug 被
        静默掩盖。修复后 raise PermanentLookupError，worker 立即标 unresolved 且
        WARNING 日志给出明确诊断键 sporttery_pdf_no_code。
        """
        # 强制 JSON 路径先失败，以便降级到 PDF 路径
        def handler(req):
            return httpx.Response(200, text='not json')
        with caplog.at_level(logging.WARNING):
            src = SportteryPrizeSource(transport=_mock_transport(handler))
            # 'ssq' 不在 _GAME_NO 也不在 _PDF_CODE — 但 _GAME_NO[ssq] 会 KeyError
            # → JSON 降级 PDF → _PDF_CODE.get('ssq') is None → 应 raise
            with pytest.raises(PermanentLookupError):
                src.lookup_amount('ssq', '082', _DRAW_DATE, 1)
        assert 'sporttery_pdf_no_code' in caplog.text

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


class TestSportteryPermanentSchemaErrors:
    """Review round 1 hardening：永久形状错误 raise PermanentLookupError。

    区分三态（与 CwlPrizeSource 一致、与 worker PermanentLookupError 分支对齐）：
      - 返回 int              → 已公布
      - 返回 None             → 官方尚未公布（下轮重试）
      - raise PermanentLookupError → 永久形状错误（worker 立即标 unresolved 不再重试）
    反例（silent-failure 陷阱）：把永久错误当 'return None' 会让 worker 每轮重查 7 天，
    最终才由 _mark_expired_unresolved 兜底标记（review round 1 critical/important）。
    """

    def test_json_unparseable_amount_raises_permanent(self, caplog):
        """Finding 1: stakeAmount 非数字 → PermanentLookupError（非 return None）。

        int('abc') 抛 ValueError 属永久 schema bug（上游字段类型变更），重查无意义。
        镜像 cwl_prize.py lines 94-103 的 try/except + raise PermanentLookupError from None。
        """
        def handler(req):
            return httpx.Response(200, json=_json_response(1, [{
                'lotteryUnuseDrawNum': '082',
                'prizeLevelList': [
                    {'prizeLevel': 1, 'stakeAmount': 'abc', 'stakeCount': '5'},
                ],
            }]))
        src = SportteryPrizeSource(transport=_mock_transport(handler))
        with caplog.at_level(logging.WARNING):
            with pytest.raises(PermanentLookupError):
                src.lookup_amount('dlt', '082', _DRAW_DATE, 1)
        # raw payload 落 WARNING 日志便于定位上游 schema 变更
        assert 'abc' in caplog.text

    def test_json_amount_type_error_raises_permanent(self, caplog):
        """Finding 1 变体：stakeAmount 为非数字非空对象 → PermanentLookupError。

        int({'nested': 'obj'}) 抛 TypeError，且非 None/''（缺失语义）→ 永久形状错误。
        （null/None 与缺失字段语义相同——均视为「未公布」return None，由 Finding 3 覆盖。）
        """
        def handler(req):
            return httpx.Response(200, json=_json_response(1, [{
                'lotteryUnuseDrawNum': '082',
                'prizeLevelList': [
                    # stakeAmount 为非数字对象（非 None/''/合法字符串）
                    {'prizeLevel': 1, 'stakeAmount': {'nested': 'unexpected'}, 'stakeCount': '5'},
                ],
            }]))
        src = SportteryPrizeSource(transport=_mock_transport(handler))
        with pytest.raises(PermanentLookupError):
            src.lookup_amount('dlt', '082', _DRAW_DATE, 1)

    def test_pdf_parse_failure_raises_permanent(self, caplog):
        """Finding 2: PDF 解析异常 → PermanentLookupError（非 return None）。

        pypdf 解析失败（损坏/不可读 PDF）每轮都会同样失败，是永久形状错误。
        return None 会让 worker 每轮重新下载并重新解析同一个坏 PDF 直到 7 天超期。
        """
        def handler(req):
            if 'getHistoryPageListV1' in str(req.url):
                return httpx.Response(200, text='not json')  # 触发 PDF 降级
            # 非 404、未超限、但内容无法被 pypdf 解析
            return httpx.Response(200, content=b'%PDF-1.4 \x00\x01\x02 corrupted')
        src = SportteryPrizeSource(transport=_mock_transport(handler))
        with caplog.at_level(logging.WARNING):
            with pytest.raises(PermanentLookupError):
                src.lookup_amount('dlt', '082', _DRAW_DATE, 1)
        assert 'sporttery_pdf_parse_failed' in caplog.text

    def test_pdf_text_present_regex_no_match_raises_permanent(self, caplog):
        """Finding 4: PDF 文本已提取但正则未命中 → PermanentLookupError。

        格式漂移（如官方改用「一等奖：500万元」）每轮都失败，是永久格式 drift。
        只有「文本为空」或「奖级越界」才允许 return None（真正「未公布」语义）。
        """
        # 构造一个合法 PDF（pypdf 能解析、能提取文本），但文本不符合既有正则格式
        # 用 reportlab 不可用则用 pypdf 直接构造——这里用 fpdf 风格的极简 PDF 太复杂，
        # 改为更直接的：直接验证 _parse_pdf_amount 的契约（非空文本+无匹配 → raise）。
        src = SportteryPrizeSource(transport=_mock_transport(lambda r: httpx.Response(200)))
        # 文本非空但格式不匹配既有正则 → 永久格式 drift
        with caplog.at_level(logging.WARNING):
            with pytest.raises(PermanentLookupError):
                # 调用内部静态方法直接验证契约（避免构造真实 PDF 的复杂性）
                SportteryPrizeSource._parse_pdf_amount('一等奖：500万元（格式已变更）', 1)
        assert 'pdf_format_drift' in caplog.text or 'pdf' in caplog.text.lower()

    def test_pdf_text_empty_returns_none(self):
        """Finding 4 边界：PDF 文本为空 → return None（真正未公布，下轮重试）。"""
        src = SportteryPrizeSource(transport=_mock_transport(lambda r: httpx.Response(200)))
        # 空文本（PDF 提取不出任何字符）→ 非 drift，是「未公布」语义
        assert src._parse_pdf_amount('', 1) is None

    def test_pdf_tier_out_of_range_returns_none(self):
        """Finding 4 边界：tier 超出中文数字范围 → return None（奖级越界）。"""
        src = SportteryPrizeSource(transport=_mock_transport(lambda r: httpx.Response(200)))
        # tier=10 超出 '一二三四五六七八九' 长度 → 越界，return None
        assert src._parse_pdf_amount('一等奖 5注 5,000,000元', 10) is None


class TestSportteryMissingStakeAmount:
    """Finding 3: stakeAmount 缺失/空 → return None（下轮重试），不默认 '0' 持久化。

    缺字段语义 = 官方尚未派奖（同 cwl typemoney='_'）。
    默认 '0' 会被 worker 当 successful refill（amount is not None）持久化为 0 分 →
    真实金额永远无法恢复（silent-wrong-data）。
    """

    def test_stake_amount_missing_returns_none(self):
        """stakeAmount 字段缺失 → None（未公布，下轮重试）。"""
        def handler(req):
            return httpx.Response(200, json=_json_response(1, [{
                'lotteryUnuseDrawNum': '082',
                'prizeLevelList': [
                    # 缺 stakeAmount 字段
                    {'prizeLevel': 1, 'stakeCount': '5'},
                ],
            }]))
        src = SportteryPrizeSource(transport=_mock_transport(handler))
        assert src.lookup_amount('dlt', '082', _DRAW_DATE, 1) is None

    def test_stake_amount_empty_string_returns_none(self):
        """stakeAmount 为空字符串 → None（未公布，下轮重试）。"""
        def handler(req):
            return httpx.Response(200, json=_json_response(1, [{
                'lotteryUnuseDrawNum': '082',
                'prizeLevelList': [
                    {'prizeLevel': 1, 'stakeAmount': '', 'stakeCount': '5'},
                ],
            }]))
        src = SportteryPrizeSource(transport=_mock_transport(handler))
        assert src.lookup_amount('dlt', '082', _DRAW_DATE, 1) is None

    def test_stake_amount_zero_still_published(self):
        """边界：stakeAmount='0' 显式为 0 → 视为已公布 0 分（合法，区别于缺失）。

        显式 '0' 与缺失/空字符串语义不同：显式 '0' 是上游主动告知「该奖级本期金额为 0」
        （如追加投注未中），属合法数据；缺失/空才是「未公布」。
        """
        def handler(req):
            return httpx.Response(200, json=_json_response(1, [{
                'lotteryUnuseDrawNum': '082',
                'prizeLevelList': [
                    {'prizeLevel': 1, 'stakeAmount': '0', 'stakeCount': '5'},
                ],
            }]))
        src = SportteryPrizeSource(transport=_mock_transport(handler))
        assert src.lookup_amount('dlt', '082', _DRAW_DATE, 1) == 0


class TestSportteryStateNonzero:
    """Review round 3 hardening（与 cwl_prize.py lines 60-68 同模式）：

    body.state != 0 通常表示上游 transient 故障（限流/临时故障），与「该期未公布」的
    permanent 语义不同。旧实现不检查 state，直接读 data → total=0 分支返回 None → worker
    当「未公布」下轮重试，但日志只有 not_published 总额=0 一条，无法区分「上游报错」与
    「正常未公布」，运维诊断「为何反复查不到」时定位困难（与 cwl_prize.py 同一 silent trap，
    cwl 在 review round 2 已加固，sporttery 本轮补齐一致性）。

    契约：state != 0 → raise httpx.HTTPError（让 worker transient-except 分支下轮重试，
    而非 return None 强制走「未公布」7 天窗口），并记 WARNING 含 state + message 字段
    便于运维区分。Option (a)（reviewer preferred）：transient 错误可被下轮重试清除。
    """

    def test_state_nonzero_raises_http_error_with_warning(self, caplog):
        """state != 0 → raise httpx.HTTPError（transient），并 WARNING 记 state + message。

        反例（被 silent trap 污染的旧实现）：state=2（限流）时直接读 data.total=0 →
        走 not_published 分支 return None → worker 下轮重试但日志只显示 not_published，
        运维无法区分限流 vs 真未公布。本测试断言契约：raise + WARNING + 区分性日志键。
        """
        def handler(req):
            # state=2 + message（模拟限流/临时故障响应）
            return httpx.Response(200, json={
                'state': 2,
                'message': '接口请求过于频繁',
                'data': {'total': 0, 'list': []},
            })
        src = SportteryPrizeSource(transport=_mock_transport(handler))
        with caplog.at_level(logging.WARNING):
            with pytest.raises(httpx.HTTPError):
                src.lookup_amount('dlt', '082', _DRAW_DATE, 1)
        # state + message 必须落 WARNING 日志便于运维诊断（区分上游报错 vs 未公布）
        assert 'state=2' in caplog.text
        assert '接口请求过于频繁' in caplog.text

    def test_state_nonzero_does_not_silently_return_none(self):
        """反 silent-success：state != 0 必须上抛，不得被当「未公布」return None。

        如果实现退化为 return None，worker 下轮重试节奏不变（pending filter 仍命中），
        但语义丢失：transient 上游错误被静默归类为「未公布」。本测试锁住 raise 契约，
        与 cwl_prize.py state!=0 加固保持一致性（review round 3 一致性要求）。
        """
        def handler(req):
            return httpx.Response(200, json={'state': 1, 'message': 'fail'})
        src = SportteryPrizeSource(transport=_mock_transport(handler))
        # 必须是 raise（任何 Exception 子类），不得 return None
        with pytest.raises(Exception):
            src.lookup_amount('dlt', '082', _DRAW_DATE, 1)


class TestSportteryDataNull:
    """Review round 4 Finding C：上游显式返回 data:null（未公布常见载荷形态）。

    sporttery JSON 在上期未公布/数据为空时可能返回 {"state":0,"data":null}。
    旧实现 `data = body.get('data', {})` 在 data 显式为 null 时得到 None（dict.get 默认值
    仅在 key 缺失时生效），下一行 `data.get('total',0)` 抛 AttributeError。
    AttributeError 不在 lookup_amount 外层 catch tuple
    （json.JSONDecodeError/KeyError/TypeError/IndexError）内 → 不降级 PDF，
    冒泡到 refill_service.py 通用 except → 被当 transient 隔离重试，每轮同样抛同样错，
    耗满 7 天窗口才由 _mark_expired_unresolved 兜底标记（与本文件 line 117-125 加固
    state!=0 想避免的同一 silent trap：transient/格式错被误分类）。

    语义判断（reviewer 推荐 option (a)）：data:null 更接近「未公布」（上游数据为空）
    而非「格式损坏应降级 PDF」——用 `body.get('data') or {}` 将 null/空统一为空 dict，
    自然落入 total=0 → return None 的「未公布」分支。cwl_prize.py line 70
    `body.get('result', [])` + `if not result` 天然对 null 安全（not None == True），
    sporttery 因多一层 .get 链式调用暴露此洞。
    """

    def test_data_null_returns_none_no_pdf(self):
        """data:null（未公布）→ return None，不抛 AttributeError，不降级 PDF。"""
        json_called = False
        pdf_called = False
        def handler(req):
            nonlocal json_called, pdf_called
            if 'getHistoryPageListV1' in str(req.url):
                json_called = True
                # 上游显式 data:null（JSON null → Python None）
                return httpx.Response(200, json={'state': 0, 'data': None})
            pdf_called = True
            return httpx.Response(404)
        src = SportteryPrizeSource(transport=_mock_transport(handler))
        # 契约：return None（未公布），不抛 AttributeError，不降级 PDF
        assert src.lookup_amount('dlt', '082', _DRAW_DATE, 1) is None
        assert json_called
        assert not pdf_called  # data:null 是「未公布」语义，不触发 PDF 降级


class TestSportteryPdfGoldenFile:
    """Golden-file 测试：真实 PDF 正则解析，防 pypdf 版本漂移（OV#9）。

    两层防御：
      1. test_parse_amount_regex_on_synthetic_text —— 始终运行，用模仿 sporttery 真实
         PDF 排版的合成文本验证 _parse_pdf_amount 正则。无真实 fixture 也能抓住正则
         /签名/单位回归（防 L-20260705T180100Z：纯 skip 测试即使方法被改名/删除也
         永不失败 → 静默漏测）。
      2. test_parse_real_pdf —— 仅当 tests/fixtures/dlt_sample.pdf 存在时运行（plan
         T7 Step 1：从 sporttery 抓取真实 PDF）。EdgeOne 拦截环境下 skip，NAS 部署后补做
         （plan line 1424 明示）。
    """

    @pytest.fixture
    def sample_pdf_path(self):
        from pathlib import Path
        return Path(__file__).parent.parent / 'fixtures' / 'dlt_sample.pdf'

    def test_parse_amount_regex_on_synthetic_text(self):
        """合成文本（模拟 sporttery PDF 排版）验证 _parse_pdf_amount 正则 + 单位换算。

        模仿真实 PDF 提取后的多行文本：含千分位逗号、多奖级、额外噪音行。
        断言：tier1/tier2 金额正确解析、去逗号、元→分换算；tier 未匹配走 drift 路径。
        """
        # 模拟 pypdf 从真实公告 PDF 提取的文本形状（plan T3 line 800 预期格式）
        text = (
            '中国体育彩票超级大乐透 第26082期 开奖公告\n'
            '一等奖  5注  5,000,000元\n'
            '二等奖  10注  500,000元\n'
            '三等奖  100注  50,000元\n'
        )
        # tier1: 5,000,000 元 → 500,000,000 分
        amount1 = SportteryPrizeSource._parse_pdf_amount(text, 1)
        assert amount1 == 500_000_000
        # tier2: 500,000 元 → 50,000,000 分
        amount2 = SportteryPrizeSource._parse_pdf_amount(text, 2)
        assert amount2 == 50_000_000

    def test_parse_real_pdf(self, sample_pdf_path):
        """用真实 PDF 验证 _parse_pdf_amount（fixture 不可用或损坏时 skip）。

        plan T7 Step 1：从 https://pdf.sporttery.cn/dlt/<period>/<period>.pdf 抓取。
        EdgeOne 拦截环境下无法抓取 → skip（plan line 1424 明示可暂跳，NAS 部署后补做）。

        额外防御（L-20260705T180100Z：勿让未处理错误路径静默掩盖预期 skip）：失败下载
        会把 HTML 404 页面（146 字节）存成 .pdf，PdfReader 解析抛 PdfStreamError 而非
        skip → 测试每次 error 而非干净 skip。文件缺失 OR 非 PDF 头 → skip。
        """
        if not sample_pdf_path.exists():
            pytest.skip('PDF fixture not available (download from sporttery)')
        # 校验是真 PDF（魔数 %PDF-）—— 失败下载的 HTML/JSON 404 页面会让下方 pypdf 抛
        # PdfStreamError 而非 skip，污染 gate。文件头校验是更稳的 skip 门槛。
        with open(sample_pdf_path, 'rb') as f:
            header = f.read(5)
        if header != b'%PDF-':
            pytest.skip(
                f'PDF fixture corrupt/non-PDF (header={header!r}); '
                're-download from sporttery'
            )
        import pypdf
        reader = pypdf.PdfReader(str(sample_pdf_path))
        text = '\n'.join(page.extract_text() or '' for page in reader.pages)
        # 验证能提取到一等奖金额（非 None，正数）
        amount = SportteryPrizeSource._parse_pdf_amount(text, 1)
        assert amount is not None
        assert amount > 0
        # 验证能提取到二等奖金额
        amount2 = SportteryPrizeSource._parse_pdf_amount(text, 2)
        assert amount2 is not None
        assert amount2 > 0
