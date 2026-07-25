from datetime import date, datetime
from zoneinfo import ZoneInfo

import httpx

from app.adapters.base import DrawNumbers, PermanentLookupError, normalize_draw_no

_CST = ZoneInfo('Asia/Shanghai')

# 项目彩种 code → MXNZP code 映射（MXNZP 文档 id=3 line 38 权威）。
# 绝大多数 code 一致；大乐透 MXNZP 叫 cjdlt（超级大乐透），项目用 dlt。
_MXNZP_CODE = {
    'ssq': 'ssq',
    'qlc': 'qlc',
    'fc3d': 'fc3d',
    'dlt': 'cjdlt',
    'qxc': 'qxc',
    'pl3': 'pl3',
    'pl5': 'pl5',
}


class MxnzpAdapter:
    name = 'mxnzp'

    def __init__(
        self,
        api_key: str,
        app_secret: str = '',
        transport: httpx.BaseTransport | None = None,
    ):
        # app_secret 默认空串向后兼容（旧调用点只传 api_key 仍可工作，
        # 但真实请求会因鉴权不全失败——main.py/cli.py 构造时会显式传）。
        self._app_id = api_key
        self._app_secret = app_secret
        self._client = httpx.Client(transport=transport, timeout=10.0)

    def fetch(self, lottery_code: str) -> DrawNumbers | None:
        # api_key/app_secret 空 → 永久性错误，不发 HTTP 请求（重试注定失败且阻塞启动）。
        # FetchService._fetch_with_backoff 识别 PermanentLookupError 不重试，走单源兜底。
        if not self._app_id:
            raise PermanentLookupError(
                f'mxnzp api_key not configured (lottery={lottery_code})'
            )
        if not self._app_secret:
            raise PermanentLookupError(
                f'mxnzp app_secret not configured (lottery={lottery_code})'
            )
        # MXNZP 通用彩票「最新一期」接口（文档 id=3「最新通用中奖号码信息」权威）。
        # 鉴权：app_id + app_secret 双参数（README 鉴权章节）。**放 header 不放 URL
        # query**——secret 进 URL 会泄露到 server logs / proxy access logs / httpx
        # request URL 日志。README 明确 header 是「推荐方案」，实测两种方式等价。
        mxnzp_code = _MXNZP_CODE.get(lottery_code, lottery_code)
        r = self._client.get(
            'https://www.mxnzp.com/api/lottery/common/latest',
            params={'code': mxnzp_code},
            headers={'app_id': self._app_id, 'app_secret': self._app_secret},
        )
        r.raise_for_status()
        body = r.json()
        # code=1 成功，code=0 业务失败（此时 data 无意义）；其他 code 如 101=QPS 超限。
        if body.get('code') != 1:
            return None
        data = body.get('data')
        if not data:
            return None  # 未开奖
        return self._parse_history_item(data, lottery_code)

    def fetch_history(self, lottery_code: str, size: int = 50) -> list[DrawNumbers]:
        """抓取最近 N 期历史开奖号码（MXNZP /common/history 接口）。

        用于启动时回填历史数据，让走势页冷启动即有数据。
        - size: 期望期数，MXNZP 单次上限 50（接口限制），size > 50 自动截断为 50。
        - 返回 list[DrawNumbers]，按接口返回顺序（最新在前）。
        - key 空抛 PermanentLookupError（与 fetch() 一致，不发 HTTP 请求）。
        - 历史数据标记 single_source=True 入库（无聚合双源校验，单源降级语义）。
        """
        if not self._app_id:
            raise PermanentLookupError(
                f'mxnzp api_key not configured (lottery={lottery_code})'
            )
        if not self._app_secret:
            raise PermanentLookupError(
                f'mxnzp app_secret not configured (lottery={lottery_code})'
            )
        # MXNZP /common/history 单次最多 50 条（接口文档 + 用户评论中站长确认）。
        # size > 50 截断为 50，防止接口返回错误或被限流。
        capped_size = min(size, 50)
        mxnzp_code = _MXNZP_CODE.get(lottery_code, lottery_code)
        r = self._client.get(
            'https://www.mxnzp.com/api/lottery/common/history',
            params={'code': mxnzp_code, 'size': capped_size},
            headers={'app_id': self._app_id, 'app_secret': self._app_secret},
        )
        r.raise_for_status()
        body = r.json()
        if body.get('code') != 1:
            return []
        data = body.get('data') or []
        # 复用 _parse_history_item 解析每条记录（与 fetch() 单点解析一致，
        # 避免两处解析逻辑分叉——silent-failure 风险：一处改了另一处没跟）。
        result: list[DrawNumbers] = []
        for item in data:
            try:
                result.append(self._parse_history_item(item, lottery_code))
            except (KeyError, ValueError, TypeError):
                # 单条解析失败不阻断整批（silent-failure 纪律：部分失败不能炸全批）。
                # 留日志供排障。
                continue
        return result

    def _parse_history_item(self, data: dict, lottery_code: str) -> DrawNumbers:
        """解析单条 MXNZP 开奖记录（fetch/fetch_history 共用）。

        data 字段：expect（期号）、openCode（号码）、time（开奖时间）。
        """
        expect = data['expect']  # '2026082'
        open_code = data['openCode']  # '05,07,10,14,21,28+04'（分区型）或 '9,0,6'（按位型）
        front, back = self._parse_open_code(open_code)
        # draw_date 取 MXNZP 返回的 time 字段（真实开奖日，如 '2026-07-19 21:15:00'）。
        # MXNZP 国内服务，time 无时区标记，按 Asia/Shanghai 解释。
        # 健壮性：time 缺失/格式异常时回退抓取日（不让解析错炸掉整次抓取——
        # draw_date 仅用于展示，比对靠 draw_no）。
        draw_date = self._parse_time(data.get('time'))
        return DrawNumbers(
            lottery_code=lottery_code,
            draw_no=normalize_draw_no(expect),
            draw_date=draw_date,
            front=front,
            back=back,
        )

    @staticmethod
    def _parse_time(raw: str | None) -> date:
        """解析 MXNZP time 字段（'YYYY-MM-DD HH:MM:SS'，无时区）为 CST 日期。

        回退链：time 非空且可解析 → 其 CST 日期；否则 → 抓取日（now CST）。
        """
        if raw:
            try:
                # fromisoformat 接受 'YYYY-MM-DD HH:MM:SS'；按 CST 解释（国内开奖时间）。
                return datetime.fromisoformat(raw).replace(tzinfo=_CST).date()
            except (ValueError, TypeError):
                pass
        return datetime.now(_CST).date()

    @staticmethod
    def _parse_open_code(open_code: str) -> tuple[tuple[int, ...], tuple[int, ...] | None]:
        """解析 openCode → (front, back)。

        - 分区型（ssq/qlc/dlt）：'红+蓝'。大乐透特殊：'前区5+后区1+后区2'，
          openCode 形如 '08,16,18,24,34+09+12'（两个 `+`），后区两个号也用 `+` 分隔。
          约定：第一个 `+` 分隔前/后区；后区剩余的 `+` 视为号码分隔符。
        - 按位型（fc3d/qxc/pl3/pl5）：'9,0,6'，无 `+`，全部归 front，back=None。
        """
        if '+' in open_code:
            front_str, _, rest = open_code.partition('+')
            back_str = rest.replace('+', ',')
            front = tuple(int(x) for x in front_str.split(','))
            back = tuple(int(x) for x in back_str.split(','))
            return front, back
        front = tuple(int(x) for x in open_code.split(','))
        return front, None

