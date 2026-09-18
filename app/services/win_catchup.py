"""回填中奖补推（win catch-up）：兜底扫描迟到比对中的中奖，杜绝静默漏推。

背景（2026-09-15 NAS 事故）：9月6-15日容器 DNS 故障期间抓取/比对/推送全灭；
恢复后启动回填补比产生的隔期中奖，常规推送路径两头够不着——
  - 路径A 只推「开奖当晚」窗口内的浮动档大奖（jobs._path_a_tick 按 draw_date=今天 过滤）；
  - 路径B 只汇总「昨天」（比对创建晚于 07:00 汇总时刻则永不覆盖）。
→ 中奖静默漏通知（违反 spec §10 核心价值）。

判定规则：
  - 迟到 = comparison 活动时间（created_at，更正后取 corrected_at；naive UTC）晚于
    「开奖日次日 07:00（CST）」——path_b 对该期的唯一一次汇总在 D+1 07:00 执行
    （只汇总昨天，之后永不重扫；DND 07:00 不触发无顺延余量），此刻之后创建的
    比对常规路径（当晚 path_a / path_b）均已不可能覆盖它。注意：cutoff 不能晚于
    该时刻——曾用「开奖日+2天」，D+1 07:00~24:00 恢复补比的中奖被永久跳过
    （path_b 已跑过/只汇总昨天），构成真空窗口静默漏推（code-review CRITICAL-1）；
  - 从未通知 = 无 status='sent' 的 NotificationLog.comparison_id 直连记录
    （与 jobs._path_a_tick 的去重语义一致：failed 记录代表从未送达，须重试）；
  - 年龄上限 = 比对创建距今 ≤ _CATCHUP_MAX_AGE_DAYS 天——历史中奖多已被汇总推送
    覆盖，但汇总 log 不带 comparison_id 无法反查去重，无上限会在机制上线时
    把全部历史中奖重推一遍。

已知可接受的重复：周报/月报按开奖日区间聚合，可能把已补推的中奖再「回顾」一次。
重复回顾远轻于静默漏推（红线优先级），不为此做交叉去重。
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select as sa_select
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from app.models import Comparison, DrawResult, NotificationLog

_CST = ZoneInfo('Asia/Shanghai')
_CATCHUP_MAX_AGE_DAYS = 14

logger = logging.getLogger(__name__)


def _now_utc() -> datetime:
    """可注入时钟（测试钉住 now 判年龄上限）。naive UTC，与 created_at 同时区。"""
    return datetime.now(UTC).replace(tzinfo=None)


def _cutoff_utc(draw_date: datetime) -> datetime:
    """开奖日的常规推送窗口关闭时刻（naive UTC）= 开奖日次日 07:00 CST。

    即 path_b 汇总该期的执行时刻：只汇总「昨天」，07:00 之后创建的比对该路径
    永不覆盖。draw_date 存的是 CST 墙钟数值（fetch_service 以 aware-CST 写入，
    SQLite 存取剥 tzinfo，CLAUDE.md datetime 纪律）；+1天7小时 后转 UTC 再剥
    tzinfo，与 created_at（naive UTC）同时区同数值比较。
    """
    draw_cst = draw_date.replace(tzinfo=_CST)
    return (draw_cst + timedelta(days=1, hours=7)).astimezone(UTC).replace(tzinfo=None)


def find_catchup_candidates(engine: Engine) -> list[dict]:
    """返回需补推的中奖比对列表（按 created_at 升序，一次事故按时间线补）。"""
    age_floor = _now_utc() - timedelta(days=_CATCHUP_MAX_AGE_DAYS)
    sent_log = sa_select(NotificationLog.id).where(
        NotificationLog.comparison_id == Comparison.id,
        NotificationLog.status == 'sent',
    )
    with Session(engine) as s:
        rows = list(
            s.exec(
                select(Comparison, DrawResult)
                .join(DrawResult, Comparison.draw_result_id == DrawResult.id)
                .where(
                    Comparison.is_win == True,  # noqa: E712
                    ~sent_log.exists(),
                    # 年龄上限按活动时间计：首比或最近一次更正任一在上限内即入选
                    #（更正重比保留 created_at，只看它会把更正翻转的中奖挡在窗外）。
                    (Comparison.created_at >= age_floor) | (Comparison.corrected_at >= age_floor),
                )
            ).all()
        )
    from app.seeds import SPECS

    code_to_name = {x['code']: x['name'] for x in SPECS}
    candidates = []
    for cmp, dr in rows:
        # 活动时间 = 该行走上当前判定结果的时刻：更正重比（corrected_at）晚于首比。
        activity_at = cmp.corrected_at or cmp.created_at
        if activity_at < _cutoff_utc(dr.draw_date):
            continue  # 未过常规窗口，路径A/B 仍会覆盖（防与汇总重复推送）
        candidates.append(
            {
                'comparison_id': cmp.id,
                'user_id': cmp.user_id,
                'lottery_code': dr.lottery_code,
                'lottery_name': code_to_name.get(dr.lottery_code, dr.lottery_code),
                'draw_no': dr.draw_no,
                'draw_date_str': dr.draw_date.date().isoformat(),
                'tier': cmp.prize_tier,
                'amount': cmp.prize_amount,
            }
        )
    candidates.sort(key=lambda c: c['comparison_id'])
    return candidates


def push_win_catchups(deps: dict) -> int:
    """对全部补推候选执行推送，返回成功数。单候选故障不中断批次（隔离纪律）。

    deps 即 scheduler 的 _JobDeps 注册表项（取 engine/notifier 两键）；用 dict
    而非 _JobDeps 类型，避免 services → scheduler 的模块依赖。
    """
    engine: Engine = deps['engine']
    notifier = deps['notifier']
    sent = 0
    for c in find_catchup_candidates(engine):
        try:
            notifier.notify_win_catchup(
                comparison_id=c['comparison_id'],
                lottery_name=c['lottery_name'],
                draw_no=c['draw_no'],
                draw_date_str=c['draw_date_str'],
                tier=c['tier'],
                amount=c['amount'],
            )
            sent += 1
        except Exception:
            # per-row 隔离：单笔补推故障（DB 错/渠道异常）不得中断其余补推
            # （CLAUDE.md 批量循环单行故障纪律）；下轮扫描未 sent 的自然重试。
            logger.error('win_catchup_push_failed comparison_id=%s', c['comparison_id'], exc_info=True)
    if sent:
        logger.info('win_catchup_pushed count=%d', sent)
    return sent
