from zoneinfo import ZoneInfo

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine

_CST = ZoneInfo('Asia/Shanghai')


def build_scheduler(engine: Engine) -> BackgroundScheduler:
    """构建调度器（spec §4.3）。

    - SQLAlchemyJobStore 使用**独立 engine / 连接池**（同一 SQLite 文件），
      不与 FastAPI 的 pool_size=1 引擎共享连接，避免请求持有连接时调度器死锁。
    - 独立 engine 自注册 PRAGMA（WAL / synchronous=NORMAL / busy_timeout），
      与 app.db.engine 保持一致。
    - 全局 Asia/Shanghai（所有 job tz-aware）
    - coalesce=True（misfire 堆积合并为一次）/ max_instances=1（同 job 不并发）
    - misfire_grace_time=600s
    """
    # 独立 engine：同一数据库文件，独立连接池；APScheduler 线程与 FastAPI 请求不竞争。
    # pool_size=1/max_overflow=0 与 app.db.engine 保持一致，避免多连接并发写 SQLite 触发
    # 静默 database locked（spec §4.3 单写连接纪律）。
    job_engine = create_engine(
        engine.url,
        connect_args={'check_same_thread': False},
        pool_size=1,
        max_overflow=0,
    )

    @event.listens_for(job_engine, 'connect')
    def _set_job_engine_pragmas(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute('PRAGMA journal_mode=WAL')
        cur.execute('PRAGMA synchronous=NORMAL')
        cur.execute('PRAGMA busy_timeout=5000')
        cur.close()

    jobstore = SQLAlchemyJobStore(engine=job_engine)
    sched = BackgroundScheduler(
        timezone=_CST,
        job_defaults={'coalesce': True, 'max_instances': 1, 'misfire_grace_time': 600},
        jobstores={'default': jobstore},
    )
    return sched
