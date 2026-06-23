from dataclasses import dataclass
from datetime import date
from typing import Protocol


@dataclass(frozen=True)
class DrawNumbers:
    """归一化开奖号码（adapter 输出）。"""
    lottery_code: str
    draw_no: str        # 归一化（去年份，如 '062'）
    draw_date: date
    front: tuple[int, ...]
    back: tuple[int, ...] | None


def normalize_draw_no(raw: str) -> str:
    """期号归一化：去 4 位年份前缀（如 2026），统一为 3 位零填充字符串。
    两源归一后用于交叉校验（§7.2）：MXNZP '2026062' 与 聚合 '062' 对齐为 '062'。

    仅处理真实数据格式：
      - '2026062'（YYYY+NNN，7 位）-> '062'
      - '062'（已归一化）           -> '062'
      - '62'（非零填充短期号）       -> '062'
    非预期的超长 / 纯年份格式不臆测归一化结果——交给双源交叉校验安全网
    （两源不一致即拒绝入库 + 告警，见 spec §双源容灾）暴露，
    而非默默猜一个可能撞车的值（如纯年份回退成 '000' 会让不同期号归一后相同）。
    """
    s = raw.strip()
    if len(s) > 4 and s[:2] in ("19", "20"):
        s = s[4:]               # 去 4 位年份前缀：'2026062' -> '062'
    return s.zfill(3)            # '062' -> '062'；'62' -> '062'


class DrawSource(Protocol):
    name: str
    def fetch(self, lottery_code: str) -> DrawNumbers | None:
        """返回归一化号码；None = 该期未开奖（HTTP 200 但无数据）。抛异常 = 网络/服务错误。"""
        ...
