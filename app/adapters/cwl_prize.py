"""CwlPrizeSource——中彩网（cwl.gov.cn）官方奖金查询。

覆盖彩种：ssq（双色球）、qlc（七乐彩）。
数据源：cwl.gov.cn 开奖公告 JSON API。
"""
import logging
from datetime import datetime

import httpx

from app.adapters.base import PermanentLookupError, rebuild_full_issue

logger = logging.getLogger(__name__)


# 重新导出：保持 `from app.adapters.cwl_prize import PermanentLookupError` 可用，
# 既有调用方（如测试、worker 的本地 import）不需改动。规范定义在 base.py。
__all__ = ['CwlPrizeSource', 'PermanentLookupError']


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

        三态语义（区别于旧实现「None 笼统代表所有失败」）：
          - 返回 int              → 已公布金额（分）
          - 返回 None             → 暂未公布（typemoney='_' / 无该奖级行 / 空结果），下轮重试
          - raise PermanentLookupError → 永久形状错误，worker 立即标 unresolved 不再重试
        transient HTTP 错误（5xx/超时）由 raise_for_status 上抛，worker 通用 except 隔离重试。
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
            # state != 0 可能是 transient server error（接口限流/临时故障），与「该期无数据」
            # 的 permanent 语义不同——提升为 WARNING 并记录 message 字段，便于运维诊断
            # 「为何反复查不到」时区分「上游报错」与「正常未公布」（review round 2 minor）。
            logger.warning(
                'cwl_state_nonzero state=%s message=%r',
                body.get('state'), body.get('message'),
            )
            return None

        result = body.get('result', [])
        if not result:
            logger.info('cwl_result_empty')
            return None

        prizegrades = result[0].get('prizegrades', [])
        for grade in prizegrades:
            # 统一类型后比较：cwl.gov.cn 真实 API prizegrades[].type 是字符串（如 '1'），
            # 而 tier 参数是 int（Comparison.prize_tier: int）。str == int 在 Python 恒为 False
            # → 旧实现 grade.get('type') == tier 永不命中 → 返回 None → 该奖级行被当「未公布」
            # 重试 7 天后静默 unresolved（review round 2 important）。str(int)==str(int) 恒真，
            # 防御性兼容 str/int 两种上游格式。
            if str(grade.get('type')) == str(tier):
                typemoney = grade.get('typemoney', '_')
                # typemoney == '_' 表示官方尚未公布该奖级金额（开奖后高奖级需等摇奖）。
                # 直接字符串比较，不做 bool(typemoney) —— 后者会把非空字符串都判 True，
                # 无法区分「未公布」与「已公布金额」（silent-success 陷阱，L-20260705T120000Z）。
                if typemoney == '_':
                    logger.info('cwl_not_published tier=%s', tier)
                    return None
                # 防御性解析 typemoney：上游 schema 变更可能送入非数字（'abc' / null 等）。
                # 永久形状错误 → raise PermanentLookupError（带 raw payload WARNING 日志），
                # worker 据此**立即**标 unresolved（区别于旧实现的「return None 当未公布重试 7 天」，
                # review round 2 critical）。raw payload 落日志便于定位上游 schema 变更根因。
                try:
                    amount = int(typemoney) * 100  # 元 → 分
                except (ValueError, TypeError):
                    logger.warning(
                        'cwl_typemoney_unparseable tier=%s raw=%r',
                        tier, typemoney,
                    )
                    raise PermanentLookupError(
                        f'typemoney unparseable: tier={tier} raw={typemoney!r}'
                    ) from None
                logger.info('cwl_found tier=%s amount=%s', tier, amount)
                return amount

        logger.info('cwl_tier_no_match tier=%s', tier)
        return None
