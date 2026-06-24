import json
import logging
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlmodel import Session, select
from sqlalchemy.engine import Engine

from app.notifications.base import NotifierChannel, NotificationPayload, SendResult, ChannelStatus
from app.notifications.templates import build_path_a, build_path_b
from app.infrastructure.crypto import CryptoService
from app.models import (
    Comparison, DrawResult, Ticket,
    NotificationChannel, NotificationRule, NotificationLog,
    LotteryType,
)

_CST = ZoneInfo("Asia/Shanghai")

logger = logging.getLogger(__name__)


def _now_hour() -> int:
    return datetime.now(_CST).hour


class Notifier:
    """推送编排：路径A异步/路径B汇总/多渠道降级重试/DND/Bark fallback（spec §7.1/§8.2）。"""

    def __init__(self, engine: Engine, channels: dict[str, NotifierChannel],
                 crypto: CryptoService, max_retries: int = 3,
                 admin_bark_config: dict | None = None,
                 dnd_start: int = 22, dnd_end: int = 7):
        self._engine = engine
        self._channels = channels
        self._crypto = crypto
        self._max_retries = max_retries
        self._admin_bark_config = admin_bark_config
        self._dnd_start = dnd_start
        self._dnd_end = dnd_end
        # admin Bark channel 懒加载（只在需要时构造）
        self._admin_bark_channel: NotifierChannel | None = None

    def close(self) -> None:
        """释放渠道持有的资源（httpx.Client 连接池等）。

        N3（quality re-review）：admin Bark channel 懒加载后长期不释放，APScheduler
        常驻进程下连接泄漏。用户渠道（bark/feishu）也持有 httpx.Client，由 Notifier 统一
        释放。email 用完即关无需处理，close() 默认空操作（base.py）幂等。
        """
        for ch in self._channels.values():
            try:
                ch.close()
            except Exception:
                logger.warning("notify_close_channel_failed type=%s", ch.type, exc_info=True)
        if self._admin_bark_channel is not None:
            try:
                self._admin_bark_channel.close()
            except Exception:
                logger.warning("notify_close_admin_channel_failed", exc_info=True)
            self._admin_bark_channel = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _in_dnd(self) -> bool:
        h = _now_hour()
        if self._dnd_start < self._dnd_end:
            return self._dnd_start <= h < self._dnd_end
        return h >= self._dnd_start or h < self._dnd_end

    def notify_path_a(self, *, comparison_id: int, lottery_name: str, draw_no: str,
                      tier: int, amount: int | None) -> None:
        """路径A：命中一二等即时简讯（异步调用，不阻塞比对事务）。DND 破例（大奖不容耽搁）。

        spec §7.1: 先读 DB 取必要数据，关闭 Session 后再做网络发送（含重试/退避），
        绝不持有 DB 连接 during slow SMTP/HTTP。
        """
        # Step 1: 从 DB 读取所有必要数据，然后关闭 Session
        with Session(self._engine) as s:
            cmp = s.get(Comparison, comparison_id)
            if cmp is None:
                return
            user_id = cmp.user_id
            # 获取 lottery_code（从 DrawResult）
            dr = s.get(DrawResult, cmp.draw_result_id)
            if dr is None:
                # 开奖结果缺失属数据异常（comparison 引用了不存在的 draw_result）——
                # 不得臆造 lottery_code='ssq' 静默误分类（quality review），记日志后跳过。
                logger.error(
                    "notify_path_a_missing_draw comparison_id=%s draw_result_id=%s "
                    "（开奖结果缺失，无法确定彩种，跳过推送）",
                    comparison_id, cmp.draw_result_id,
                )
                return
            lottery_code = dr.lottery_code
            # 预读渠道配置（解密后缓存到内存，避免 Session 关闭后再访问 ORM）
            channels_data = self._load_channels(s, user_id)
            # 写 log（在 Session 内完成 DB 写）
            payload = build_path_a(lottery_name=lottery_name, draw_no=draw_no,
                                   tier_name=_tier_name(lottery_code, tier),
                                   tier=tier, amount=amount)
            # 先写 pending log（无 sent_at）
            log = NotificationLog(
                user_id=user_id, type=payload.title, payload=payload.body,
                status="pending", error=None, sent_at=None,
            )
            s.add(log)
            s.commit()
            log_id = log.id

        # Step 2: Session 已关闭，做网络发送（重试/退避不阻塞 DB）
        result = self._send_to_user_channels(user_id, channels_data, payload, force=True)

        # Step 3: 更新 log 状态（新开短 Session）
        self._update_log_status(log_id, result)

    def notify_path_b(self, *, user_id: int, date_str: str) -> int:
        """路径B：次日 07:00 汇总。DND 时顺延（返回 0，由调度器重排）。返回已推用户数。

        spec §7.1：与路径A 对齐——Session 内只读数据 + 写 pending log，关 Session 后
        再做网络发送（含重试/退避），绝不持有 DB 连接 during slow HTTP/SMTP（SQLite
        pool_size=1 单写连接，长持有会阻塞 APScheduler jobstore 写）。
        """
        if self._in_dnd():
            return 0  # 顺延：调度器在 DND 结束时刻重排（Task 7）
        # Step 1: Session 内读数据 + 渠道配置 + 写 pending log，然后关闭 Session
        with Session(self._engine) as s:
            wins, loses, details = self._collect_user_results(s, user_id, date_str)
            if wins == 0 and loses == 0:
                return 0  # 无活动，不推空消息
            # 按策略：win_only 且无中奖 → 不推；every 则推（含未中奖汇总）
            # 实际策略已在 _collect_user_results 中过滤，此处只需确保有内容
            payload = build_path_b(date_str=date_str, total=wins + loses, wins=wins,
                                   win_details=details, loses=loses)
            channels_data = self._load_channels(s, user_id)
            log = NotificationLog(
                user_id=user_id, type=payload.title, payload=payload.body,
                status="pending", error=None, sent_at=None,
            )
            s.add(log)
            s.commit()
            log_id = log.id

        # Step 2: Session 已关闭，做网络发送（重试/退避不阻塞 DB）
        result = self._send_to_user_channels(user_id, channels_data, payload, force=False)

        # Step 3: 更新 log 状态（新开短 Session）
        self._update_log_status(log_id, result)
        return 1

    def _load_channels(self, session: Session, user_id: int) -> list:
        """Session 内读取并解密用户启用渠道，返回 [(plugin, config, type), ...]。
        解密失败的渠道已被 _decrypt_config 记 WARNING 并跳过。"""
        channels_data = []
        for ch_row in session.exec(select(NotificationChannel).where(
            NotificationChannel.user_id == user_id, NotificationChannel.enabled == True  # noqa: E712
        )).all():
            plugin = self._channels.get(ch_row.type)
            if plugin is None:
                continue
            config = self._decrypt_config(ch_row)
            if config is None:
                continue
            channels_data.append((plugin, config, ch_row.type))
        return channels_data

    def _update_log_status(self, log_id: int, result: SendResult) -> None:
        """新开短 Session 更新 NotificationLog 状态（路径A/B 共用）。"""
        if log_id is None:
            return
        with Session(self._engine) as s:
            log = s.get(NotificationLog, log_id)
            if log is None:
                return
            log.status = result.status.value
            log.error = result.error
            if result.status == ChannelStatus.SENT:
                # naive UTC，刻意与 created_at（default_factory=datetime.utcnow）同时区同数值。
                # SQLite 对 datetime 做字符串比较且存取剥离 tzinfo——若用 datetime.now(_CST)，
                # 写入 CST 本地数值（比 UTC 早 8h），未来按时间过滤 log 的运维查询会静默
                # 误判边界行（与 FloatRefillWorker _cutoff_naive_utc 同源雷，CLAUDE.md 纪律）。
                log.sent_at = datetime.now(timezone.utc).replace(tzinfo=None)
            s.commit()

    def _send_to_user_channels(self, user_id: int, channels_data: list,
                               payload: NotificationPayload, force: bool) -> SendResult:
        """Session 外发送（路径A/B 共用）。channels_data: [(plugin, config, type), ...]

        DND 检查由调用方负责：路径B notify_path_b 入口已检 DND 顺延；路径A force=True
        破例。此处不再二次检查（force 参数保留供未来扩展，当前恒由调用方保证 DND 语义）。
        """
        # 空渠道 ≠ 全渠道失败：用户未配/全禁用渠道（新用户）不应触发 admin 告警——
        # 否则每次有内容都告警，噪音淹没真实「全渠道失败」（N4）。
        if not channels_data:
            logger.warning(
                "notify_no_channels user_id=%s type=%s（用户未配置/全禁用渠道，跳过推送）",
                user_id, payload.title,
            )
            return SendResult(ChannelStatus.FAILED, "no channels")
        last = SendResult(ChannelStatus.FAILED, "no channels")
        for plugin, config, _ch_type in channels_data:
            last = self._send_with_retry(plugin, payload, config)
            if last.status == ChannelStatus.SENT:
                return last
        # 全渠道失败 → admin Bark fallback（spec §8.1/§10）
        self._alert_admin(payload, user_id=user_id)
        return last

    def _send_with_retry(self, plugin: NotifierChannel, payload: NotificationPayload,
                         config: dict) -> SendResult:
        last = SendResult(ChannelStatus.FAILED, "no attempt")
        for attempt in range(self._max_retries):
            last = plugin.send(payload, config)
            if last.status == ChannelStatus.SENT:
                return last
            time.sleep(2 ** attempt)  # 指数退避
        return last

    def _decrypt_config(self, ch_row: NotificationChannel) -> dict | None:
        """解密渠道配置。只接受 {"ct": ...} 格式，拒绝明文（spec §8.1）。

        解密失败（Fernet key 失配 / 密文损坏 / key_version 轮换错位）须记 WARNING——
        bare except 静默返回 None 会让该用户该渠道永不推送且运维无感知，破坏「中奖永不
        静默漏通知」（spec §10）。明文拒绝属配置校验，单独记 INFO 便于排查。
        """
        raw = json.loads(ch_row.config_json)
        if "ct" not in raw:
            logger.warning(
                "notify_decrypt_skip_plaintext user_id=%s channel_id=%s type=%s "
                "（spec §8.1 拒绝明文，疑似旧数据/手改）",
                ch_row.user_id, ch_row.id, ch_row.type,
            )
            return None  # 明文拒绝
        try:
            blob = (ch_row.key_version, raw["ct"])  # 加密存储 {"ct": ...}
            plaintext = self._crypto.decrypt(blob)
            return json.loads(plaintext)
        except Exception:
            logger.warning(
                "notify_decrypt_failed user_id=%s channel_id=%s type=%s key_version=%s "
                "（密文损坏 / key_version 失配 / Fernet key 轮换错位，该渠道将跳过）",
                ch_row.user_id, ch_row.id, ch_row.type, ch_row.key_version, exc_info=True,
            )
            return None

    def _alert_admin(self, payload: NotificationPayload, *, user_id: int) -> None:
        """全渠道失败 → admin Bark fallback（spec §8.1/§10）。

        admin send 永不抛异常（BarkChannel 契约），但可能返回 FAILED（admin key 失效 /
        Bark 服务挂）。须记录返回值——否则「全渠道失败」时连兜底告警也挂了却无感知，
        双重静默（N2）。
        """
        if self._admin_bark_config is None:
            logger.warning(
                "admin_alert_skipped user_id=%s（未配置 admin_bark_config，全渠道失败无处告警）",
                user_id,
            )
            return
        if self._admin_bark_channel is None:
            from app.notifications.bark import BarkChannel
            self._admin_bark_channel = BarkChannel()
        admin_payload = NotificationPayload(
            title="推送失败告警",
            body=f"用户 {user_id} 推送失败（全渠道不可用）。消息：{payload.title}",
            user_id=user_id,
        )
        result = self._admin_bark_channel.send(admin_payload, self._admin_bark_config)
        if result.status != ChannelStatus.SENT:
            # 兜底告警通道自身失败——ERROR 级，运维须立刻感知（告警链路断了）
            logger.error(
                "admin_bark_alert_failed user_id=%s error=%s（全渠道失败 + 兜底告警也失败，"
                "中奖推送完全丢失，须人工介入）",
                user_id, result.error,
            )

    def _collect_user_results(self, session, user_id, date_str):
        """汇总该用户当日比对结果（仅含追投彩种）。

        spec §7.4/§8.2: 推送范围 = 该用户号码池里有启用注的彩种。
        """
        from datetime import date as _date
        d = _date.fromisoformat(date_str)
        # 该用户追投的彩种（有启用 ticket 的 lottery_code）
        tracked_codes = {
            t.lottery_code for t in session.exec(select(Ticket).where(
                Ticket.user_id == user_id, Ticket.enabled == True  # noqa: E712
            )).all()
        }
        # 每彩种策略
        rules = {
            r.lottery_code: r.strategy
            for r in session.exec(select(NotificationRule).where(
                NotificationRule.user_id == user_id
            )).all()
        }
        # 查 comparisons（只查追投彩种）
        cmps = list(session.exec(select(Comparison).where(
            Comparison.user_id == user_id,
        )).all())
        wins, loses, details = 0, 0, []
        # 预读 lottery_type 名称映射
        lottery_names = {
            lt.code: lt.name for lt in session.exec(select(LotteryType)).all()
        }
        for c in cmps:
            dr = session.get(DrawResult, c.draw_result_id)
            if dr is None or dr.draw_date.date() != d:
                continue
            if dr.lottery_code not in tracked_codes:
                continue  # 未追投，不推
            strategy = rules.get(dr.lottery_code, "every")
            if c.is_win:
                wins += 1
                name = lottery_names.get(dr.lottery_code, dr.lottery_code)
                details.append((name, _tier_name(dr.lottery_code, c.prize_tier), c.prize_amount))
            else:
                # win_only 策略且未中奖 → 不计入汇总
                if strategy == "win_only":
                    continue
                loses += 1
        return wins, loses, details


def _tier_name(lottery_code: str, tier: int | None) -> str:
    """按彩种返回奖级名称（lottery-rules.md 权威）。"""
    if tier is None:
        return "未中奖"
    # partition + qxc: 一等奖/二等奖/...
    if lottery_code in ("ssq", "dlt", "qlc", "qxc"):
        cn = {1: "一等奖", 2: "二等奖", 3: "三等奖", 4: "四等奖", 5: "五等奖",
              6: "六等奖", 7: "七等奖", 8: "八等奖", 9: "九等奖"}
        return cn.get(tier, "未中奖")
    # positional: fc3d 用"单选/组选三/组选六"；pl3/pl5 用"直选/组选三/组选六"
    if lottery_code == "fc3d":
        cn = {1: "单选", 2: "组选三", 3: "组选六"}
        return cn.get(tier, "未中奖")
    if lottery_code in ("pl3", "pl5"):
        cn = {1: "直选", 2: "组选三", 3: "组选六"}
        return cn.get(tier, "未中奖")
    return "未中奖"
