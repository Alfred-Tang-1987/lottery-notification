from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler

import app.scheduler.setup as scheduler_setup
from app.scheduler.setup import build_scheduler


def test_build_scheduler_uses_independent_engine(db_engine, tmp_path):
    """调度器 jobstore 必须使用独立 engine/连接池，避免与 FastAPI 请求争用 pool_size=1。"""
    sched = build_scheduler(engine=db_engine)
    assert isinstance(sched, BackgroundScheduler)
    # 全局时区 Asia/Shanghai
    assert str(sched.timezone) == 'Asia/Shanghai'
    # job_defaults
    jd = sched._job_defaults
    assert jd['coalesce'] is True
    assert jd['max_instances'] == 1
    assert jd['misfire_grace_time'] == 600
    # jobstore 是 SQLAlchemyJobStore，且 engine 与传入的 app engine 不是同一个实例
    # _lookup_jobstore 是 APScheduler 内部 API，测试用其访问默认 jobstore
    store = sched._lookup_jobstore('default')
    assert isinstance(store, SQLAlchemyJobStore)
    assert store.engine is not db_engine


def test_scheduler_engine_uses_single_writer_pool(db_engine, monkeypatch):
    """独立 engine 仍须遵守单写连接纪律，避免多连接并发写 SQLite 触发静默 database locked。"""
    captured = {}
    original = scheduler_setup.create_engine

    def _spy(url, **kwargs):
        captured['kwargs'] = kwargs
        return original(url, **kwargs)

    monkeypatch.setattr(scheduler_setup, 'create_engine', _spy)
    build_scheduler(engine=db_engine)
    assert captured['kwargs'].get('pool_size') == 1
    assert captured['kwargs'].get('max_overflow') == 0
