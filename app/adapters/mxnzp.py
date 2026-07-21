from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from app.adapters.base import DrawNumbers, normalize_draw_no

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
        expect = data['expect']  # '2026082'
        open_code = data['openCode']  # '05,07,10,14,21,28+04'（分区型）或 '9,0,6'（按位型）
        front, back = self._parse_open_code(open_code)
        return DrawNumbers(
            lottery_code=lottery_code,
            draw_no=normalize_draw_no(expect),
            draw_date=datetime.now(_CST).date(),
            front=front,
            back=back,
        )

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

