from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from apscheduler.schedulers.background import BackgroundScheduler
    from sqlalchemy.engine import Engine

    from app.notifications.notifier import Notifier
    from app.services.compare_service import CompareService
    from app.services.fetch_service import FetchService
    from app.services.refill_service import FloatRefillWorker


class _JobDeps(TypedDict):
    engine: Engine
    fetch_service: FetchService
    compare_service: CompareService
    refill_worker: FloatRefillWorker
    notifier: Notifier
    # 调度器实例（运行时任务内 add_job 一次性推送 / DND 顺延登记用）。
    # 不作为 job args 持久化（services 经注册表解析，sched 同样运行时取回）。
    _sched: BackgroundScheduler
