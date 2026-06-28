from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
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
