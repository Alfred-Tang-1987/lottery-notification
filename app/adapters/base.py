import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DrawNumbers:
    """归一化开奖号码（adapter 输出）。"""

    lottery_code: str
    draw_no: str  # 归一化（去年份，如 '062'）
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
    if len(s) > 4 and s[:2] in ('19', '20'):
        s = s[4:]  # 去 4 位年份前缀：'2026062' -> '062'
    return s.zfill(3)  # '062' -> '062'；'62' -> '062'


class DrawSource(Protocol):
    name: str

    def fetch(self, lottery_code: str) -> DrawNumbers | None:
        """返回归一化号码；None = 该期未开奖（HTTP 200 但无数据）。抛异常 = 网络/服务错误。"""
        ...


class PrizeSource(Protocol):
    """官方奖金查询源（独立于 DrawSource——奖金查询与号码抓取是不同职责）。"""

    name: str

    def lookup_amount(
        self, lottery_code: str, draw_no: str, draw_date: datetime, tier: int
    ) -> int | None:
        """查询浮动奖金（分）。None = 官方尚未公布/查询失败。

        draw_date 为 aware CST（fetch_service 存入时的契约），期号重建 year 依赖此。
        异常上抛——由 FloatRefillWorker 统一 catch + 隔离（不 catch httpx 异常）。
        """
        ...


class PermanentLookupError(Exception):
    """永久性查询错误——上游数据形状/契约错误，重试无意义。

    区别于 transient HTTP 错误（5xx/超时，下轮重试可能成功）与「未公布」（返回 None，
    下轮重试直到官方派奖）。典型场景：typemoney 为非数字/非 '_'（上游 schema 变更）。

    FloatRefillWorker.except 分支识别本异常类型后立即把 comparison 标 unresolved
    （不再重试），避免永久 schema bug 被当 transient 每轮重查 7 天、日志噪声巨大且
    最终才由 _mark_expired_unresolved 兜底标记（spec §7.1 line 276 精神延伸）。

    定义在 base.py 而非具体 adapter：worker 通过 Callable 注入 lookup，应只依赖
    PrizeSource Protocol 共享的类型（本异常 + Protocol 同位），避免耦合具体 adapter。
    """


class TransientLookupError(Exception):
    """瞬时性查询错误——重试可成功，区别于 PermanentLookupError（重试无意义）。

    典型场景：MXNZP code=101（QPS 超限）。FetchService._fetch_with_backoff 识别本异常
    后走指数退避重试（与普通 Exception 一致），但语义上更明确：调用方可知这是 transient
    而非永久契约错误。

    引入背景（L-20260726T013000Z）：MXNZP 1 QPS 限制下，path_a_tick 串行调 7 彩种触发
    code=101，旧实现 `if code != 1: return None` 把限流伪装成「未开奖」→ 开奖静默漏抓。
    限流须抛本异常让 fetch_service 退避重试，而非吞 None（spec §10 核心价值）。
    """


def _defensive_truncate(draw_no: str) -> str:
    """draw_no 防御截断：长度 >3 时 log warning + 取后 3 位（1B 决策）。

    正常路径 draw_no 已归一化（3 位零填充），此防御仅覆盖未来 adapter 绕过
    归一化直接写入的异常场景。
    """
    if len(draw_no) > 3:
        logger.warning(
            'draw_no_too_long draw_no=%s truncated_to=%s',
            draw_no,
            draw_no[-3:],
        )
        return draw_no[-3:]
    return draw_no


def rebuild_full_issue(draw_date: datetime, draw_no: str) -> str:
    """重建全年份期号（如 '2026082'）。

    draw_date 必须是 aware CST（期号重建 year 依赖此时区契约）。
    """
    safe_no = _defensive_truncate(draw_no)
    return f'{draw_date.year}{safe_no}'


def rebuild_short_period(draw_date: datetime, draw_no: str) -> str:
    """重建 2 位年份期号（如 '26082'）。用于 sporttery PDF URL。"""
    safe_no = _defensive_truncate(draw_no)
    return f'{draw_date.year % 100:02d}{safe_no}'
