"""CwlPrizeSource 测试——cwl.gov.cn JSON API。"""
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

    def test_typemoney_non_numeric_raises_permanent_error(self, caplog):
        """typemoney 为非数字/非 '_' 字符串 → 永久数据形状错误，raise PermanentLookupError。

        回归 fix-issue（review round 2 critical）：int(typemoney) 在 typemoney='abc' 时抛
        ValueError，旧实现用通用 except 捕获返回 None，但 worker 把 None 当「未公布」每轮
        重查 7 天后才由 _mark_expired_unresolved 兜底标 unresolved——永久 schema bug 被当
        transient 重试，期间日志噪声巨大且定位困难。
        正确语义：永久形状错误 raise PermanentLookupError，worker except 分支识别该异常类型
        后**立即**标 unresolved（不再重试），与「未公布」(None) 与「transient HTTP 错误」
        (其他异常，下轮重试) 三者语义区分。
        """
        from app.adapters.cwl_prize import PermanentLookupError

        def handler(req):
            return httpx.Response(200, json=_cwl_response([
                {'type': 1, 'typemoney': 'abc', 'typenum': '5'},
            ]))
        src = CwlPrizeSource(transport=_mock_transport(handler))
        with caplog.at_level('WARNING', logger='app.adapters.cwl_prize'), pytest.raises(PermanentLookupError):
            src.lookup_amount('ssq', '082', _DRAW_DATE, 1)
        # 必须记录原始 raw payload，便于定位上游 schema 变更根因
        assert any(
            'abc' in rec.getMessage() and rec.levelname == 'WARNING'
            for rec in caplog.records
        ), f'expected WARNING containing raw payload abc, got {[r.getMessage() for r in caplog.records]}'

    def test_typemoney_none_payload_raises_permanent_error(self):
        """typemoney 为 None（JSON null）→ 永久形状错误，raise PermanentLookupError 不当 transient。"""
        from app.adapters.cwl_prize import PermanentLookupError

        def handler(req):
            return httpx.Response(200, json=_cwl_response([
                {'type': 1, 'typemoney': None, 'typenum': '5'},
            ]))
        src = CwlPrizeSource(transport=_mock_transport(handler))
        with pytest.raises(PermanentLookupError):
            src.lookup_amount('ssq', '082', _DRAW_DATE, 1)

    def test_grade_type_string_matches_int_tier(self):
        """真实 API 契约：cwl.gov.cn prizegrades[].type 是字符串（如 '1'），tier 参数是 int。

        回归 fix-issue（review round 2 important）：旧实现 `grade.get('type') == tier` 在
        str-vs-int 比较时 Python 恒为 False → 循环永不命中 → 返回 None → 该奖级行被当
        「未公布」重试 7 天后静默 unresolved。happy-path 测试用数字 type 掩盖了契约不匹配。
        必须用真实 API 的字符串 type fixture 验证匹配生效。
        """
        def handler(req):
            return httpx.Response(200, json=_cwl_response([
                # 真实 cwl.gov.cn 返回字符串 type（非数字）
                {'type': '1', 'typemoney': '5000000', 'typenum': '5'},
                {'type': '2', 'typemoney': '150000', 'typenum': '25'},
            ]))
        src = CwlPrizeSource(transport=_mock_transport(handler))
        # tier 参数是 int（Comparison.prize_tier: int），但上游 type 是 str —— 必须匹配
        assert src.lookup_amount('ssq', '082', _DRAW_DATE, 1) == 500_000_000
        assert src.lookup_amount('ssq', '082', _DRAW_DATE, 2) == 15_000_000

    def test_grade_type_int_still_matches(self):
        """防御性：若上游将来改回数字 type，仍能匹配（str(int)==str(int) 恒真）。"""
        def handler(req):
            return httpx.Response(200, json=_cwl_response([
                {'type': 1, 'typemoney': '5000000', 'typenum': '5'},
            ]))
        src = CwlPrizeSource(transport=_mock_transport(handler))
        assert src.lookup_amount('ssq', '082', _DRAW_DATE, 1) == 500_000_000

    def test_state_nonzero_logs_warning_with_message(self, caplog):
        """state != 0 可能是 transient server error（限流/临时故障），须 WARNING + message 字段。

        回归 fix-issue（review round 2 minor）：旧实现 state!=0 用 INFO 记录，与
        result_empty（正常未公布）同级 INFO 混淆，运维诊断「为何反复查不到」时不易发现
        上游报错。提升为 WARNING 并记录 message 字段，与正常未公布 INFO 区分。
        """
        def handler(req):
            return httpx.Response(200, json={'state': 1, 'message': '接口限流', 'result': []})
        src = CwlPrizeSource(transport=_mock_transport(handler))
        with caplog.at_level('WARNING', logger='app.adapters.cwl_prize'):
            result = src.lookup_amount('ssq', '082', _DRAW_DATE, 1)
        assert result is None
        # 必须是 WARNING 级别（不是 INFO），且含 state + message 两个字段
        matching = [
            rec for rec in caplog.records
            if 'cwl_state_nonzero' in rec.getMessage() and rec.levelname == 'WARNING'
        ]
        assert matching, (
            f'expected WARNING cwl_state_nonzero, got '
            f'{[(r.levelname, r.getMessage()) for r in caplog.records]}'
        )
        # 记录中须含 message 字段值，便于运维定位限流/故障根因
        assert '接口限流' in matching[0].getMessage()

    def test_close(self):
        """close() 释放 client 资源。"""
        src = CwlPrizeSource(transport=_mock_transport(lambda r: httpx.Response(200)))
        src.close()  # 不抛异常即通过
