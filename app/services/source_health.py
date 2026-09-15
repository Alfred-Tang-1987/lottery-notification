"""数据源健康记录与告警评估（plan-11 / 2026-09-15 DNS 事故跟进）。

ApiSourceHealth 长期「有读无写」（admin 面板空表）——本模块补写路径：fetch 按
源记录 ok/down；评估器按「down 持续 ≥30 分钟」发 admin Bark（送达才转移状态。
DNS 教训：故障期告警通道大概率同挂，未送达必须下轮重试）。

时间纪律（CLAUDE.md）：down_since/last_success_at 均 naive UTC，与
TimestampMixin.created_at 同表示，DB 内不做 naive/aware 混比。
"""

import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy.engine import Engine
from sqlmodel import Session

from app.models import ApiSourceHealth

logger = logging.getLogger(__name__)

# outcome（抓取结果三态）→ status（面板四态）映射（dx-voice F3 词表统一）：
#   ok        → ok        健康（含业务「未开奖」）
#   down      → down      运行故障（网络/DNS/限流重试耗尽）
#   permanent → degraded  配置态（key 未配置/schema 契约变更）——置 degraded 并清
#                         down_since：dx-voice F11，down 之后接 permanent 不得继续
#                         按运行故障告警（key 被删是配置事实，不该「持续失败 1440 分钟」）。
Outcome = Literal['ok', 'down', 'permanent']

# 告警阈值由 settings.source_health_alert_after_minutes 供给（dx-voice F19 逃生舱：
# 面向人的告警阈值必须可调，默认 30 分钟；时间窗实现与 tick 次数解耦——
# 持久、不怕容器重启，2026-09-15 事故中容器恰在故障期重启，内存计数会清零）。
_DEFAULT_ALERT_AFTER_MINUTES = 30


def _down_alert_after() -> timedelta:
    """阈值（分钟）→ timedelta；settings 读不到时回退默认值（复核修订 2026-09-15）。

    为什么必须容错：写入侧 `record_source_health` 的 ok 分支与评估侧触发条件都要读它，
    而设置读取失败（如测试环境 conftest 清空 JWT_SECRET/CRYPTO_KEY_V1 → Settings
    ValidationError，实测）会沿调用链上抛；写入侧那个异常还会被 `_record_health` 的
    `except Exception` 吞掉——健康表从此**静默停写**（silent-failure 纪律不允许）。
    回退到 30 分钟 + warning 留痕：告警阈值退化，但健康观测链路继续工作。
    **真值源仍是 settings**（F19 逃生舱：正常路径一律读配置，可随环境调整）；
    本常量仅兜底，并由 `test_fallback_threshold_matches_settings_default` 钉住不漂移。
    """
    try:
        from app.config import get_settings

        return timedelta(minutes=get_settings().source_health_alert_after_minutes)
    except Exception:
        logger.warning(
            'alert_threshold_settings_unavailable fallback_minutes=%d',
            _DEFAULT_ALERT_AFTER_MINUTES, exc_info=True,
        )
        return timedelta(minutes=_DEFAULT_ALERT_AFTER_MINUTES)

# error 脱敏（autoplan M13）：juhe 把 api key 放 query（juhe.py:27），
# raise_for_status 异常消息含完整 URL；健康表 error 会进 admin 面板与 Bark
# 告警体（第三方服务器），密钥参数值落表前一律替换 [REDACTED]。
# eng-voice M4：公开命名（去下划线，F21 一致性）——fetch_service 日志路径复用。
_SENSITIVE_QUERY_RE = re.compile(r'([?&](?:key|app_id|app_secret|token)=)[^&\s]+')

# now 注入点：生产用默认；测试注入固定时钟，避免真实 sleep/时间竞争。
NowFn = Callable[[], datetime]  # dx-voice F21：公开签名不用私有名


def now_naive_utc() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def sanitize_error(error: str | None) -> str | None:
    """剥离 error 文本中的敏感 query 参数值（M13），保留 URL 其余部分供排障。

    两个消费点：record_source_health 落表前；FetchService._fetch_with_backoff
    打日志前（eng-voice M4——容器日志同在家族 NAS 上，key 不该落日志文件）。
    """
    if error is None:
        return None
    return _SENSITIVE_QUERY_RE.sub(r'\1[REDACTED]', error)


def record_source_health(
    engine: Engine,
    source: str,
    outcome: Outcome,
    error: str | None = None,
    now: NowFn = now_naive_utc,
) -> None:
    """按源 upsert 健康表（spec §1.2 语义表）。outcome→status 映射见模块头注释。

    ok + alerted=='none' + 故障时长 ≥ 阈值（autoplan M11）：转 recovering 补发
    恢复通知——故障期告警通道同挂（DNS 教训）导致告警从未送达，恢复时若静默
    清零，长故障将零通知（9 天事故复现路径）。
    """
    t = now()
    with Session(engine) as s:
        h = s.get(ApiSourceHealth, source)
        if h is None:
            h = ApiSourceHealth(source=source)
            s.add(h)
        if outcome == 'ok':
            h.status = 'ok'
            h.last_success_at = t
            h.error = None
            if h.alerted == 'none':
                if h.down_since is not None and t - h.down_since >= _down_alert_after():
                    h.alerted = 'recovering'  # M11：长故障零送达 → 补发恢复通知
                else:
                    h.down_since = None  # 短故障：静默恢复（未达告警阈值，无通知义务）
            else:
                # 已告警过 → 待恢复通知；保留 down_since 供评估侧算故障时长。
                h.alerted = 'recovering'
        elif outcome == 'down':
            h.status = 'down'
            if h.alerted == 'recovering':
                # 抖动/再故障（eng-voice M3a）：翻回 none 而非 alerted——alerted 的
                # 语义是「本次故障的告警已送达」（spec §1.1），M11 路径（告警从未
                # 送达）的行翻 alerted 会让面板显示「已通知」而无人收到。
                # 按新 episode 重新计时：恢复通知自然取消（发送要求 status=='ok'），
                # 是否重告警由评估器送达门控决定（duplicate > silence）。
                h.alerted = 'none'
                h.down_since = t
            elif h.down_since is None:
                h.down_since = t
            h.error = sanitize_error(error)
        elif outcome == 'permanent':
            # 配置态（dx-voice F11）：置 degraded 并清 down_since——不再按运行故障
            # 计时/告警；degraded 由此成为真实写路径（此前列注释枚举但无写入方）。
            # eng-voice M3b：alerted 一并清 none——配置态转移终结运行故障 episode，
            # 不欠恢复通知（否则 recovering 卡在 degraded 行上永远无法清偿）。
            h.status = 'degraded'
            h.down_since = None
            h.alerted = 'none'
            h.error = sanitize_error(error)
        else:
            raise ValueError(f'unknown outcome: {outcome}')
        s.commit()
