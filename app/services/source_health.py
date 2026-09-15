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
from sqlmodel import Session, select

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


def evaluate_source_alerts(
    engine: Engine,
    sender_factory: Callable[[], Callable[[str, str], None] | None],
    now: NowFn = now_naive_utc,
) -> None:
    """评估健康表驱动告警状态机（spec §1.3；挂载于 path_a tick 尾 + 启动 backfill 尾）。

    sender_factory（eng-voice H1/M2）：惰性构造 sender 的工厂——只在确有告警/恢复
    待发送时才调用。无待发送项时绝不调用：不新建未关闭的 BarkChannel/httpx.Client
    （bark.py:35-37，client 仅经 close() 释放），也不在健康表全 ok 时触碰
    get_settings()（H1：调度测试环境 conftest 删 JWT_SECRET/CRYPTO_KEY_V1，
    无条件调 get_settings() 会 ValidationError 被外层 except 吞掉，评估静默不执行）。
    工厂返回 None（ADMIN_BARK_KEY 未配 / SOURCE_HEALTH_ALERTS_ENABLED=false）→
    不发送也**不转移**（dx-voice F6：alerted 的语义是「故障告警已送达」（spec §1.1），
    未送达就标 alerted 是状态说谎——面板会显示「已通知」而实际无人收到；保持 none
    并每次评估 warning 一次，配好 key 后下一轮评估自然补发）。
    发送异常不转移状态（下轮重试直到送达）——2026-09-15 DNS 事故教训：故障期
    告警通道大概率同时挂。

    两阶段（autoplan M1，pool_size=1 纪律）：短 session 读+决策后关闭 → session
    外发 HTTP 告警 → 短 session 守卫重读后落转移。绝不在持有唯一连接的 session
    内做 httpx 调用——DNS 故障（本 plan 目标场景）下 Bark 挂到 10s 超时，同期
    其他 job/请求借不到连接撞 busy_timeout，告警机制反而制造它要防的漏通知
    （jobs.py:255-258、password_reset_service.py:249-255 两次实测事故同型）。
    落转移前重读校验状态未变：读与落之间若有 fetch 写入（如故障恰好恢复），
    放弃本轮转移下轮重评，不覆盖并发写入（eng-voice L2 已知取舍：此时已送达的
    告警下一轮可能因状态未落而重复发送一次——duplicate > silence，运维看到
    重复告警比漏告警安全；deploy.md 写明避免误当 bug 追查）。
    """
    t = now()
    # 阶段 1：短 session 读 + 决策（快照出 session，不留 ORM 对象跨 session）
    with Session(engine) as s:
        rows = [
            (h.source, h.status, h.alerted, h.down_since, h.error)
            for h in s.exec(select(ApiSourceHealth)).all()
        ]
    # 备源状态速查（design-voice D5：告警体必须回答「开奖是否受影响」——
    # 单源 down 但备源正常时，告警文案若暗示漏开奖是最快的静音之路）。
    peer_status = {source: status for source, status, *_ in rows}

    # 阶段 1.5：先决策出全部待发送项；没有就直接返回——工厂不被调用（eng M2）。
    pending: list[tuple[str, str, str, str]] = []  # (source, 目标 alerted, title, body)
    for source, status, alerted, down_since, error in rows:
        if (
            status == 'down'
            and alerted == 'none'
            and down_since is not None
            and t - down_since >= _down_alert_after()
        ):
            minutes = int((t - down_since).total_seconds() // 60)
            peers = {p: ps for p, ps in peer_status.items() if p != source}
            if not peers:
                impact = '仅此一个数据源，开奖可能延迟入库'
            elif all(ps == 'ok' for ps in peers.values()):
                impact = f'备用源正常（{"、".join(peers)}），开奖未受影响'
            elif any(ps == 'down' for ps in peers.values()):
                impact = '双源同时故障，开奖可能延迟入库'
            else:
                # 备源 unknown/degraded（如 juhe 未配置）——不是「双源故障」，
                # 但主源 down 时实际上只剩单点，延迟风险同样要说清。
                impact = (
                    f'备用源非健康（{"、".join(f"{p}={ps}" for p, ps in peers.items())}），'
                    f'开奖可能延迟入库'
                )
            # 文案不带绝对时间（design-voice D3：UTC 括号与同句的分钟数
            # 相差 8h 自相矛盾；时长已足够定位）。fix 指引（dx-voice F10：
            # problem+cause+fix——半夜收到的人需要知道下一步做什么）。
            pending.append((
                source,
                'alerted',
                '开奖抓取持续失败',
                f'数据源 {source} 已持续失败约 {minutes} 分钟。{impact}。'
                f'最近错误：{(error or "")[:200]}。'
                f'处理：检查 NAS 网络/DNS 与上游状态；面板 /admin/health 查看；'
                f'详见 docs/deploy.md「数据源健康告警」。',
            ))
        elif alerted == 'recovering' and status == 'ok':
            duration = t - down_since if down_since else timedelta(0)
            minutes = int(duration.total_seconds() // 60)
            # 诚实声明缺口（design-voice D6：闭环不能只到「恢复」——
            # 故障窗口的开奖是否补回，admin 必须知道要不要人工介入）。
            pending.append((
                source,
                'none',
                '开奖抓取已恢复',
                f'数据源 {source} 已恢复抓取（故障持续约 {minutes} 分钟）。'
                f'故障期间的开奖缺失将随今晚 path_a 轮询与启动回填'
                f'（最近 2 天）覆盖；更长缺口请人工确认是否需要补抓。',
            ))
    if not pending:
        return

    # 阶段 2：惰性取 sender（此刻才碰 settings/BarkChannel，eng H1/M2）；
    # F6：取不到就不发送不转移，每次评估 warning 一次。
    send_alert = sender_factory()
    if send_alert is None:
        logger.warning(
            'source_alerts_disabled reason=no_sender '
            '(ADMIN_BARK_KEY 未配或 SOURCE_HEALTH_ALERTS_ENABLED=false)：'
            '健康告警只落表不发送'
        )
        return
    # session 外发送；送达成功的进待落清单
    delivered: list[tuple[str, str]] = []  # (source, 目标 alerted 状态)
    for source, target, title, body in pending:
        try:
            send_alert(title, body)
        except Exception:
            # error 级（dx-voice F12：告警链路本身挂了是重大运维事件，
            # 不是普通 warning；重试语义不变——下轮继续尝试直到送达）。
            logger.error('source_alert_send_failed source=%s', source, exc_info=True)
            continue  # 未送达不转移，下轮重试
        delivered.append((source, target))
    if not delivered:
        return
    # 阶段 3：短 session 守卫重读后落转移（recovering 完成时清 down_since）
    with Session(engine) as s:
        for source, target in delivered:
            h = s.get(ApiSourceHealth, source)
            if h is None:
                continue
            if target == 'alerted' and h.alerted == 'none' and h.status == 'down':
                h.alerted = 'alerted'
            elif target == 'none' and h.alerted == 'recovering' and h.status == 'ok':
                h.alerted = 'none'
                h.down_since = None
        s.commit()


def admin_alert_sender_factory() -> Callable[[str, str], None] | None:
    """健康告警 sender 工厂（dx-voice F18 独立开关 + eng-voice H1/M2 惰性构造）。

    SOURCE_HEALTH_ALERTS_ENABLED=false → None（健康告警与密码重置告警不共用
    一个总开关）；开关开但 ADMIN_BARK_KEY 未配 → build_admin_alert() 自身返回 None。
    评估器只在确有待发送项时才调用本工厂——无待发送项不碰 settings/httpx。
    """
    from app.config import get_settings
    from app.notifications.admin_alert import build_admin_alert

    if not get_settings().source_health_alerts_enabled:
        return None
    return build_admin_alert()
