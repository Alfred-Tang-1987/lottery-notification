import httpx
from datetime import date
from app.adapters.base import DrawNumbers, normalize_draw_no
from app.adapters.mxnzp import MxnzpAdapter
from app.adapters.juhe import JuheAdapter


def _mock_transport(handler):
    return httpx.MockTransport(handler)


def test_normalize_draw_no():
    """期号归一化：MXNZP '2026062' 与 聚合 '062' 统一为不带年份的 3 位。"""
    assert normalize_draw_no("2026062") == "062"
    assert normalize_draw_no("062") == "062"


def test_normalize_draw_no_real_formats():
    """§7.2 真实数据格式归一化（不臆测异常宽度——超长/纯年份交给双源交叉校验安全网）。"""
    # MXNZP 标准格式：YYYY+NNN（7 位），去年份前缀
    assert normalize_draw_no("2026062") == "062"
    # 聚合 / 已归一化 3 位
    assert normalize_draw_no("062") == "062"
    # 非零填充短期号
    assert normalize_draw_no("62") == "062"
    # 不同年份前缀同样去前缀（跨年覆盖）
    assert normalize_draw_no("2021062") == "062"
    assert normalize_draw_no("1999062") == "062"


def test_mxnzp_adapter_parses():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": "200", "data": {
            "issue": "2026062", "numbers": "01,02,03,04,05,06+07"}})
    adapter = MxnzpAdapter(api_key="k", transport=_mock_transport(handler))
    d = adapter.fetch("ssq")
    assert d is not None
    assert d.lottery_code == "ssq"
    assert d.draw_no == "062"  # 归一化
    assert d.front == (1, 2, 3, 4, 5, 6)
    assert d.back == (7,)


def test_mxnzp_adapter_empty_means_not_drawn():
    """HTTP 200 但 data 为空 = 该期未开奖（非错误）。"""
    def handler(req): return httpx.Response(200, json={"code": "200", "data": None})
    adapter = MxnzpAdapter(api_key="k", transport=_mock_transport(handler))
    assert adapter.fetch("ssq") is None


def test_mxnzp_adapter_shanghai_date():
    """§4.3 + §7.3: draw_date 必须用 Asia/Shanghai 时区，不能回退到 UTC。
    开奖窗口 21:30-01:00 横跨 Shanghai 午夜，00:30 CST = 前一天 16:30 UTC，
    若用 UTC.date() 会少一天。"""
    from unittest.mock import patch
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": "200", "data": {
            "issue": "2026062", "numbers": "01,02,03,04,05,06+07"}})
    adapter = MxnzpAdapter(api_key="k", transport=_mock_transport(handler))

    # 模拟 Shanghai 00:30（= UTC 前一天 16:30）
    shanghai_0030 = datetime(2026, 6, 22, 0, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    utc_1630 = datetime(2026, 6, 21, 16, 30, tzinfo=timezone.utc)
    with patch("app.adapters.mxnzp.datetime") as mock_dt:
        # 关键：代码调用 datetime.now(timezone.utc) 时返回 utc_1630
        # 若代码用 UTC，则 date() 得到 6/21；若用 Asia/Shanghai 则得到 6/22
        def _now(tz=None):
            if tz is timezone.utc:
                return utc_1630
            if str(tz) == "Asia/Shanghai":
                return shanghai_0030
            return utc_1630  # 默认
        mock_dt.now = _now
        mock_dt.__name__ = "datetime"
        mock_dt.timezone = timezone
        d = adapter.fetch("ssq")
    assert d is not None
    assert d.draw_date == date(2026, 6, 22)  # Shanghai 日期是 6/22，不是 UTC 的 6/21


def test_juhe_adapter_shanghai_date():
    """§4.3 + §7.3 + §10: Juhe fallback date 必须用 Asia/Shanghai，不能回退 UTC。
    开奖窗口 21:30-01:00 横跨 Shanghai 午夜，UTC date 可能少一天。"""
    from unittest.mock import patch
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo

    def handler(req): return httpx.Response(200, json={"error_code": 0, "result": {
        "lottery_no": "ssq", "lottery_date": "bad-date",
        "lottery_res": "01,02,03,04,05,06", "blue_no": "07", "period": "062"}})
    adapter = JuheAdapter(api_key="k", transport=_mock_transport(handler))

    shanghai_0030 = datetime(2026, 6, 22, 0, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    utc_1630 = datetime(2026, 6, 21, 16, 30, tzinfo=timezone.utc)
    with patch("app.adapters.juhe.datetime") as mock_dt:
        def _now(tz=None):
            if tz is timezone.utc:
                return utc_1630
            if str(tz) == "Asia/Shanghai":
                return shanghai_0030
            return utc_1630
        mock_dt.now = _now
        mock_dt.__name__ = "datetime"
        mock_dt.timezone = timezone
        mock_dt.date = date
        d = adapter.fetch("ssq")
    assert d is not None
    assert d.draw_date == date(2026, 6, 22)  # Shanghai 日期是 6/22，不是 UTC 的 6/21
