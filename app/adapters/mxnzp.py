import httpx
from datetime import datetime
from zoneinfo import ZoneInfo
from app.adapters.base import DrawNumbers, normalize_draw_no, DrawSource


_CST = ZoneInfo("Asia/Shanghai")


class MxnzpAdapter:
    name = "mxnzp"

    def __init__(self, api_key: str, transport: httpx.BaseTransport | None = None):
        self._key = api_key
        self._client = httpx.Client(transport=transport, timeout=10.0)

    def fetch(self, lottery_code: str) -> DrawNumbers | None:
        # MXNZP 接口（示例结构，真实字段以 MXNZP 文档为准）
        r = self._client.get(
            "https://www.mxnzp.com/api/lottery/common/result",
            params={"lottery_id": lottery_code, "app_id": self._key},
        )
        r.raise_for_status()
        body = r.json()
        data = body.get("data")
        if not data:
            return None  # 未开奖
        issue = data["issue"]  # '2026062'
        nums = data["numbers"]  # '01,02,03,04,05,06+07'
        front_str, _, back_str = nums.partition("+")
        front = tuple(int(x) for x in front_str.split(","))
        back = tuple(int(x) for x in back_str.split(",")) if back_str else None
        return DrawNumbers(
            lottery_code=lottery_code, draw_no=normalize_draw_no(issue),
            draw_date=datetime.now(_CST).date(), front=front, back=back,
        )
