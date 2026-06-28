from datetime import date, datetime
from zoneinfo import ZoneInfo

import httpx

from app.adapters.base import DrawNumbers, normalize_draw_no

_CST = ZoneInfo('Asia/Shanghai')


class JuheAdapter:
    name = 'juhe'

    def __init__(self, api_key: str, transport: httpx.BaseTransport | None = None):
        self._key = api_key
        self._client = httpx.Client(transport=transport, timeout=10.0)

    def fetch(self, lottery_code: str) -> DrawNumbers | None:
        r = self._client.get(
            'https://v.juhe.cn/lottery/query',
            params={'lottery_id': lottery_code, 'key': self._key},
        )
        r.raise_for_status()
        body = r.json()
        if body.get('error_code') != 0 or not body.get('result'):
            return None
        res = body['result']
        front = tuple(int(x) for x in res['lottery_res'].split(','))
        back = tuple(int(x) for x in res['blue_no'].split(',')) if res.get('blue_no') else None
        d: date
        try:
            d = date.fromisoformat(res['lottery_date'])
        except Exception:
            d = datetime.now(_CST).date()
        return DrawNumbers(
            lottery_code=lottery_code,
            draw_no=normalize_draw_no(res.get('period', '')),
            draw_date=d,
            front=front,
            back=back,
        )
