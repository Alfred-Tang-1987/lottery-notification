from datetime import UTC, date

import httpx

from app.adapters.base import normalize_draw_no
from app.adapters.juhe import JuheAdapter
from app.adapters.mxnzp import MxnzpAdapter


def _mock_transport(handler):
    return httpx.MockTransport(handler)


def test_normalize_draw_no():
    """期号归一化：MXNZP '2026062' 与 聚合 '062' 统一为不带年份的 3 位。"""
    assert normalize_draw_no('2026062') == '062'
    assert normalize_draw_no('062') == '062'


def test_normalize_draw_no_real_formats():
    """§7.2 真实数据格式归一化（不臆测异常宽度——超长/纯年份交给双源交叉校验安全网）。"""
    # MXNZP 标准格式：YYYY+NNN（7 位），去年份前缀
    assert normalize_draw_no('2026062') == '062'
    # 聚合 / 已归一化 3 位
    assert normalize_draw_no('062') == '062'
    # 非零填充短期号
    assert normalize_draw_no('62') == '062'
    # 不同年份前缀同样去前缀（跨年覆盖）
    assert normalize_draw_no('2021062') == '062'
    assert normalize_draw_no('1999062') == '062'


def test_mxnzp_adapter_parses():
    def handler(req: httpx.Request) -> httpx.Response:
        # 新契约（文档 id=3）：/lottery/common/latest，返回 {code, msg, data:{openCode,expect,...}}
        return httpx.Response(
            200, json={'code': 1, 'msg': 'ok', 'data': {
                'openCode': '01,02,03,04,05,06+07', 'code': 'ssq', 'expect': '2026062',
                'name': '双色球', 'time': '2026-06-12 21:15:00',
            }}
        )

    adapter = MxnzpAdapter(api_key='k', app_secret='s', transport=_mock_transport(handler))
    d = adapter.fetch('ssq')
    assert d is not None
    assert d.lottery_code == 'ssq'
    assert d.draw_no == '062'  # 归一化
    assert d.front == (1, 2, 3, 4, 5, 6)
    assert d.back == (7,)
    # draw_date 取 MXNZP 返回的 time 字段（真实开奖日），不是抓取日。
    # 回归点（2026-07-21 冒烟）：旧代码用 datetime.now()，ssq 7-19 开奖存成抓取日 7-21。
    assert d.draw_date == date(2026, 6, 12)


def test_mxnzp_adapter_sends_app_id_and_secret_and_code_param():
    """鉴权 + URL 契约：code 在 URL，app_id/app_secret 在 **header**（非 URL query），
    且 hit /common/latest。

    secret 必须走 header——放 URL query 会泄露到 server logs / proxy access logs /
    httpx request URL 日志（实测冒烟日志完整记录过 secret）。
    """
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen['url'] = str(req.url)
        seen['params'] = dict(req.url.params)
        seen['headers'] = dict(req.headers)
        return httpx.Response(200, json={'code': 1, 'data': {
            'openCode': '01,02,03,04,05,06+07', 'code': 'ssq', 'expect': '2026062', 'time': 't',
        }})

    adapter = MxnzpAdapter(api_key='my-id', app_secret='my-secret', transport=_mock_transport(handler))
    adapter.fetch('ssq')
    assert '/api/lottery/common/latest' in seen['url']
    assert seen['params']['code'] == 'ssq'  # code 留 URL（非敏感）
    assert seen['headers']['app_id'] == 'my-id'
    assert seen['headers']['app_secret'] == 'my-secret'
    # 防回归：secret 绝不能出现在 URL（server/proxy 日志会记录）
    assert 'app_id' not in seen['params']
    assert 'app_secret' not in seen['params']


def test_mxnzp_adapter_maps_dlt_to_cjdlt():
    """大乐透 code 映射：项目 dlt → MXNZP cjdlt（文档 line 38 权威）。"""
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen['code'] = req.url.params.get('code')
        # 大乐透真实 openCode 格式：前区5 + 后区2（两个 + 号）
        return httpx.Response(200, json={'code': 1, 'data': {
            'openCode': '08,16,18,24,34+09+12', 'code': 'cjdlt', 'expect': '2026081', 'time': 't',
        }})

    adapter = MxnzpAdapter(api_key='k', app_secret='s', transport=_mock_transport(handler))
    d = adapter.fetch('dlt')
    assert seen['code'] == 'cjdlt'
    assert d is not None
    assert d.lottery_code == 'dlt'  # 出口仍用项目 code
    assert d.front == (8, 16, 18, 24, 34)
    assert d.back == (9, 12)  # 两个 + 都正确解析为后区两号


def test_mxnzp_adapter_parses_positional_code_without_plus():
    """按位型彩种（fc3d/qxc/pl3/pl5）openCode 无 +，全部归 front，back=None。"""
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={'code': 1, 'data': {
            'openCode': '9,0,6', 'code': 'fc3d', 'expect': '2026191', 'time': 't',
        }})

    adapter = MxnzpAdapter(api_key='k', app_secret='s', transport=_mock_transport(handler))
    d = adapter.fetch('fc3d')
    assert d is not None
    assert d.front == (9, 0, 6)
    assert d.back is None


def test_mxnzp_adapter_empty_means_not_drawn():
    """HTTP 200 但 code=0 / data 为空 = 该期未开奖（非错误）。"""

    def handler(req):
        return httpx.Response(200, json={'code': 0, 'msg': 'no data', 'data': None})

    adapter = MxnzpAdapter(api_key='k', app_secret='s', transport=_mock_transport(handler))
    assert adapter.fetch('ssq') is None


def test_mxnzp_adapter_shanghai_date():
    """§4.3 + §7.3: draw_date 取自 MXNZP time 字段，按 Asia/Shanghai 解释（国内服务）。
    开奖窗口 21:30-01:00 横跨 Shanghai 午夜——若错用 UTC 解释 '2026-06-22 00:30:00'，
    转回 UTC 是前一天 16:30，date() 会少一天。time 字段无时区标记，必须按 CST 读。"""
    # time 给的是 CST 00:30（开奖跨午夜的真实场景）
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={'code': 1, 'data': {
                'openCode': '01,02,03,04,05,06+07', 'code': 'ssq', 'expect': '2026062',
                'time': '2026-06-22 00:30:00',  # CST 跨午夜
            }}
        )

    adapter = MxnzpAdapter(api_key='k', app_secret='s', transport=_mock_transport(handler))
    d = adapter.fetch('ssq')
    assert d is not None
    assert d.draw_date == date(2026, 6, 22)  # CST 日期是 6/22


def test_mxnzp_adapter_draw_date_falls_back_when_time_missing():
    """time 字段缺失/格式错时回退抓取日（不让解析错炸掉抓取）。"""
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={'code': 1, 'data': {
            'openCode': '01,02,03,04,05,06+07', 'code': 'ssq', 'expect': '2026062',
            'time': 'not-a-date',  # 格式错
        }})

    adapter = MxnzpAdapter(api_key='k', app_secret='s', transport=_mock_transport(handler))
    d = adapter.fetch('ssq')
    assert d is not None
    # 回退到今天（抓取日），不抛异常
    from datetime import date as _date
    assert d.draw_date == _date.today()


def test_juhe_adapter_shanghai_date():
    """§4.3 + §7.3 + §10: Juhe fallback date 必须用 Asia/Shanghai，不能回退 UTC。
    开奖窗口 21:30-01:00 横跨 Shanghai 午夜，UTC date 可能少一天。"""
    from datetime import datetime, timezone
    from unittest.mock import patch
    from zoneinfo import ZoneInfo

    def handler(req):
        return httpx.Response(
            200,
            json={
                'error_code': 0,
                'result': {
                    'lottery_no': 'ssq',
                    'lottery_date': 'bad-date',
                    'lottery_res': '01,02,03,04,05,06',
                    'blue_no': '07',
                    'period': '062',
                },
            },
        )

    adapter = JuheAdapter(api_key='k', transport=_mock_transport(handler))

    shanghai_0030 = datetime(2026, 6, 22, 0, 30, tzinfo=ZoneInfo('Asia/Shanghai'))
    utc_1630 = datetime(2026, 6, 21, 16, 30, tzinfo=UTC)
    with patch('app.adapters.juhe.datetime') as mock_dt:

        def _now(tz=None):
            if tz is UTC:
                return utc_1630
            if str(tz) == 'Asia/Shanghai':
                return shanghai_0030
            return utc_1630

        mock_dt.now = _now
        mock_dt.__name__ = 'datetime'
        mock_dt.timezone = timezone
        mock_dt.date = date
        d = adapter.fetch('ssq')
    assert d is not None
    assert d.draw_date == date(2026, 6, 22)  # Shanghai 日期是 6/22，不是 UTC 的 6/21
