"""T5 (plan 07): main.py 奖金查询接线冒烟测试。

验证：
1. _build_amount_lookup 路由闭包按彩种 code 分发到对应 PrizeSource（ssq/qlc→cwl，
   dlt/qxc→sporttery，固定档→None）。
2. lifespan teardown 关闭适配器 client（不泄漏 httpx 连接）。
3. validate_startup 中 _smoke_check_prize_sources 不阻塞启动（网络故障只 log）。

silent-failure 自验（L-20260706T010500Z）：测试须断言「不同 code 真的产生不同分发」，
否则 routing 闭包是 silent-success（filter 永不命中 / preset no-op）。
"""
import logging
from datetime import datetime
from unittest.mock import MagicMock

import pytest
from cryptography.fernet import Fernet

from app.config import reset_settings_cache


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    """health/startup 测试必备密钥；禁用 scheduler 避免 lifespan run_startup_backfill 抓真实数据源。"""
    reset_settings_cache()
    monkeypatch.setenv('JWT_SECRET', 'x' * 32)
    monkeypatch.setenv('CRYPTO_KEY_V1', Fernet.generate_key().decode())
    monkeypatch.setenv('SCHEDULER_ENABLED', 'false')


def test_build_amount_lookup_routes_cwl_codes_to_cwl_adapter():
    """ssq/qlc → cwl；dlt/qxc → sporttery；固定档 → None。

    L-20260706T010500Z 自验：断言不同 code 真的分发到不同 adapter，且固定档 None——
    防 filter 永不命中的 silent-success。
    """
    from app.main import _build_amount_lookup

    cwl = MagicMock()
    sporttery = MagicMock()
    cwl.lookup_amount.return_value = 111_00
    sporttery.lookup_amount.return_value = 222_00

    fn = _build_amount_lookup(cwl, sporttery)
    draw_date = datetime(2026, 7, 24)

    # cwl 彩种
    assert fn('ssq', '2026082', draw_date, 1) == 111_00
    assert fn('qlc', '2026082', draw_date, 1) == 111_00
    # sporttery 彩种
    assert fn('dlt', '2026082', draw_date, 1) == 222_00
    assert fn('qxc', '2026082', draw_date, 1) == 222_00
    # 固定档：不查询
    assert fn('fc3d', '2026082', draw_date, 1) is None
    assert fn('pl3', '2026082', draw_date, 1) is None
    assert fn('pl5', '2026082', draw_date, 1) is None

    # 真的分发到不同 adapter：cwl 收到 ssq/qlc，sporttery 收到 dlt/qxc
    cwl_codes = [c.args[0] for c in cwl.lookup_amount.call_args_list]
    sporttery_codes = [c.args[0] for c in sporttery.lookup_amount.call_args_list]
    assert cwl_codes == ['ssq', 'qlc']
    assert sporttery_codes == ['dlt', 'qxc']
    # 固定档 0 调用——不被错误地转发到任一 adapter
    assert len(cwl.lookup_amount.call_args_list) == 2
    assert len(sporttery.lookup_amount.call_args_list) == 2


def test_build_amount_lookup_passes_all_args():
    """draw_no / draw_date / tier 必须完整透传到下游 adapter——防签名被截断后下游
    查不到期号（silent-success：result 永远 None 被当未公布）。"""
    from app.main import _build_amount_lookup

    cwl = MagicMock()
    sporttery = MagicMock()
    fn = _build_amount_lookup(cwl, sporttery)
    draw_date = datetime(2026, 7, 24)

    fn('ssq', '2026082', draw_date, 2)
    cwl.lookup_amount.assert_called_once_with('ssq', '2026082', draw_date, 2)

    fn('dlt', '2026099', draw_date, 1)
    sporttery.lookup_amount.assert_called_once_with('dlt', '2026099', draw_date, 1)


def test_smoke_check_prize_sources_does_not_block_startup(monkeypatch, caplog):
    """_smoke_check_prize_sources 在网络故障时只 log error，不抛异常。

    spec §10/§11：冒烟验证非启动门禁，PDF 降级可能仍可用——不能因 API 不通阻塞启动。
    L-20260706T053000Z：通过 monkeypatch httpx.get 避免真实网络调用污染测试。
    """
    from app import main as main_mod

    # 让 httpx.get 抛网络异常——冒烟应吞掉
    def _boom(*a, **kw):
        raise OSError('network down')

    monkeypatch.setattr('httpx.get', _boom)
    log = logging.getLogger('app.startup')
    with caplog.at_level(logging.ERROR, logger='app.startup'):
        main_mod._smoke_check_prize_sources(MagicMock(), log)
    # 网络故障被吞下、记 error；未上抛即代表不阻塞启动
    assert any('smoke_' in rec.message for rec in caplog.records)


def test_smoke_check_prize_sources_reports_field_mismatch(monkeypatch, caplog):
    """响应缺关键字段（如 prizegrades）须 log error——避免 schema 漂移被静默吞。

    L-20260706T010500Z 自验：smoke 的字段检查必须真能改变 log 输出（mismatch 触发 error），
    否则 smoke 是 silent-success（no-op）。
    """
    from app import main as main_mod

    # cwl 响应缺 prizegrades → smoke_cwl_field_mismatch
    class _Resp:
        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

        def raise_for_status(self):
            pass  # 2xx 假定，不抛——让响应进入字段校验分支

    def _fake_get(url, **kw):
        if 'cwl.gov.cn' in url:
            return _Resp({'state': 0, 'result': [{'code': 'x'}]})  # 缺 prizegrades
        return _Resp({'data': {'list': [{'x': 1}]}})  # sporttery 缺 prizeLevelList

    monkeypatch.setattr('httpx.get', _fake_get)
    log = logging.getLogger('app.startup')
    with caplog.at_level(logging.ERROR, logger='app.startup'):
        main_mod._smoke_check_prize_sources(MagicMock(), log)
    msgs = [rec.message for rec in caplog.records]
    assert any('smoke_cwl_field_mismatch' in m for m in msgs), \
        '缺 prizegrades 必须 log smoke_cwl_field_mismatch（防 schema 漂移被静默吞）'
    assert any('smoke_sporttery_field_mismatch' in m for m in msgs), \
        '缺 prizeLevelList 必须 log smoke_sporttery_field_mismatch'


def test_smoke_check_prize_sources_no_ok_on_empty_cwl_result(monkeypatch, caplog):
    """空 result 不得报 smoke_cwl_ok——空响应是非验证（no_data_to_verify），不是 schema-OK。

    Review round 1 [important]：旧实现 else 分支对空 result 直接 log.info('smoke_cwl_ok')，
    导致上游返回空时冒烟被当成功——schema 漂移永远沉默（docstring 自警的 trap）。
    L-20260706T010500Z：冒烟的「成功」语义必须由非空 + 字段齐备共同保证，不能由 falsy 兜底。
    """
    from app import main as main_mod

    class _Resp:
        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

        def raise_for_status(self):
            pass

    def _fake_get(url, **kw):
        if 'cwl.gov.cn' in url:
            # state=0 但 result 为空——典型「未查到该期」响应
            return _Resp({'state': 0, 'result': []})
        # sporttery 给一个有效响应避免噪声
        return _Resp({'data': {'list': [{'prizeLevelList': []}]}})

    monkeypatch.setattr('httpx.get', _fake_get)
    log = logging.getLogger('app.startup')
    with caplog.at_level(logging.WARNING, logger='app.startup'):
        main_mod._smoke_check_prize_sources(MagicMock(), log)
    msgs = [rec.message for rec in caplog.records]
    # 空 result → no_data_to_verify（warning），绝不可 ok
    assert any('smoke_cwl_no_data_to_verify' in m for m in msgs), \
        '空 result 须 log smoke_cwl_no_data_to_verify（warning），而非 smoke_cwl_ok'
    assert not any('smoke_cwl_ok' in m for m in msgs), \
        '空 result 不得报 smoke_cwl_ok（false-positive schema-OK）'


def test_smoke_check_prize_sources_no_ok_on_empty_sporttery_list(monkeypatch, caplog):
    """空 list 不得报 smoke_sporttery_ok——空列表是非验证，不是 schema-OK。

    Review round 1 [important]：旧实现 `if items and ...` 对空 list 走 else 分支直接 ok，
    导致上游返回空 list 时冒烟被当成功。
    """
    from app import main as main_mod

    class _Resp:
        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

        def raise_for_status(self):
            pass

    def _fake_get(url, **kw):
        if 'cwl.gov.cn' in url:
            # cwl 给有效响应避免噪声
            return _Resp({'state': 0, 'result': [{'prizegrades': []}]})
        # sporttery 返回空 list
        return _Resp({'data': {'list': []}})

    monkeypatch.setattr('httpx.get', _fake_get)
    log = logging.getLogger('app.startup')
    with caplog.at_level(logging.WARNING, logger='app.startup'):
        main_mod._smoke_check_prize_sources(MagicMock(), log)
    msgs = [rec.message for rec in caplog.records]
    assert any('smoke_sporttery_no_data_to_verify' in m for m in msgs), \
        '空 list 须 log smoke_sporttery_no_data_to_verify（warning），而非 smoke_sporttery_ok'
    assert not any('smoke_sporttery_ok' in m for m in msgs), \
        '空 list 不得报 smoke_sporttery_ok（false-positive schema-OK）'


def test_smoke_check_prize_sources_classifies_http_error_as_failed(monkeypatch, caplog):
    """4xx/5xx 响应须 raise_for_status → 落入 failed（transient），而非 field_mismatch（schema drift）。

    Review round 1 [minor]：旧实现缺 raise_for_status，5xx 限流响应体被当 JSON 解析后
    误判为 schema drift，污染运维诊断（lookup 一直 None 的真实原因是 transient 而非 schema）。
    """
    from app import main as main_mod
    import httpx

    class _Resp:
        def __init__(self, payload, status_code=500):
            self._payload = payload
            self.status_code = status_code

        def json(self):
            return self._payload

        def raise_for_status(self):
            # 模拟 httpx 的 raise_for_status：非 2xx 抛 HTTPStatusError
            req = httpx.Request('GET', 'https://example.com')
            raise httpx.HTTPStatusError(
                'Server Error', request=req,
                response=httpx.Response(self.status_code, request=req),
            )

    def _fake_get(url, **kw):
        if 'cwl.gov.cn' in url:
            return _Resp({'error': 'rate limited'}, status_code=500)
        return _Resp({'error': 'rate limited'}, status_code=500)

    monkeypatch.setattr('httpx.get', _fake_get)
    log = logging.getLogger('app.startup')
    with caplog.at_level(logging.ERROR, logger='app.startup'):
        main_mod._smoke_check_prize_sources(MagicMock(), log)
    msgs = [rec.message for rec in caplog.records]
    # 5xx → failed（被 raise_for_status 抛出后由 except 捕获）
    assert any('smoke_cwl_failed' in m for m in msgs), \
        '5xx 响应须分类为 smoke_cwl_failed（transient），而非 field_mismatch（schema drift）'
    assert any('smoke_sporttery_failed' in m for m in msgs), \
        '5xx 响应须分类为 smoke_sporttery_failed（transient），而非 field_mismatch'
    # 绝不可误判为 schema drift
    assert not any('smoke_cwl_field_mismatch' in m for m in msgs), \
        '5xx 不得分类为 smoke_cwl_field_mismatch（误诊 schema drift）'
    assert not any('smoke_sporttery_field_mismatch' in m for m in msgs), \
        '5xx 不得分类为 smoke_sporttery_field_mismatch（误诊 schema drift）'


def test_smoke_check_prize_sources_sporttery_passes_term_param(monkeypatch):
    """sporttery 请求须带 term 参数——否则 API 返回空/摘要列表（无 prizeLevelList），
    使冒烟沦为 silent-success（永不命中真实开奖页）。

    Review round 1 [important]：cwl 用 code（期号）精确定位，sporttery 旧实现只传
    gameNo+isVerify+pageNo，未传 term——返回的是列表摘要，smoke 永远拿不到带
    prizeLevelList 的开奖详情。L-20260706T010500Z：filter 参数须真能命中目标行。
    """
    from app import main as main_mod

    captured = {}

    class _Resp:
        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

        def raise_for_status(self):
            pass

    def _fake_get(url, **kw):
        if 'sporttery' in url:
            captured['params'] = kw.get('params', {})
        return _Resp({'data': {'list': [{'prizeLevelList': []}]}})

    monkeypatch.setattr('httpx.get', _fake_get)
    main_mod._smoke_check_prize_sources(MagicMock(), logging.getLogger('app.startup'))
    assert 'term' in captured.get('params', {}), \
        'sporttery 冒烟请求须带 term 参数（期号），否则 API 返回摘要列表导致 smoke 永不命中真实开奖页'


def test_build_scheduler_and_deps_wires_real_adapters(db_engine, monkeypatch):
    """_build_scheduler_and_deps 必须把真实 CwlPrizeSource / SportteryPrizeSource 接入
    FloatRefillWorker（而非 _amount_lookup_stub）。

    L-20260706T010500Z 自验：断言接线后的 amount_lookup 真的按 code 分发——防 stub
    残留导致 amount_lookup 永远返回 None（silent-success：奖金永久 null）。
    """
    from app import main as main_mod
    from app.config import Settings, get_settings

    settings = get_settings()
    sched, deps = main_mod._build_scheduler_and_deps(db_engine, settings)
    try:
        # 真实 adapter 已注入 deps（供 lifespan close）
        from app.adapters.cwl_prize import CwlPrizeSource
        from app.adapters.sporttery_prize import SportteryPrizeSource
        assert isinstance(deps['cwl_prize'], CwlPrizeSource)
        assert isinstance(deps['sporttery_prize'], SportteryPrizeSource)
        # refill_worker 用的是真实路由闭包：dlt 应走 sporttery（非 None stub）
        refill = deps['refill_worker']
        # 用 spy 替换 adapter.lookup_amount 验证 refill._lookup 路由
        called = []
        orig = deps['sporttery_prize'].lookup_amount
        deps['sporttery_prize'].lookup_amount = lambda *a, **kw: (called.append(a) or 999_00)
        try:
            assert refill._lookup('dlt', '2026099', datetime(2026, 7, 24), 1) == 999_00
            assert called and called[0][0] == 'dlt', 'dlt 必须路由到 sporttery，非 stub'
        finally:
            deps['sporttery_prize'].lookup_amount = orig
    finally:
        # scheduler 未 start（构造函数不 start），shutdown 会 raise——按运行态判断
        from apscheduler.schedulers import SchedulerNotRunningError
        try:
            sched.shutdown(wait=False)
        except SchedulerNotRunningError:
            pass
        deps['cwl_prize'].close()
        deps['sporttery_prize'].close()
