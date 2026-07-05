"""T4c: 通知 + 健康 + 审计 + 调度 表 schema 校验（对齐 spec §6 数据模型）。

覆盖 notification_channels / notification_rules / notification_logs /
api_source_health / admin_audit_logs / apscheduler_jobs 六张表的字段、
外键、默认值、主键与可写回性。
"""

from datetime import UTC, datetime

from sqlalchemy import inspect
from sqlmodel import Session, select

from app.models.audit import AdminAuditLog
from app.models.health import ApiSourceHealth
from app.models.lottery import LotteryType
from app.models.notification import NotificationChannel, NotificationLog, NotificationRule
from app.models.scheduler import ApschedulerJob
from app.models.user import User

# ---------- 字段存在性（spec §6 逐列核对） ----------

_EXPECTED_NOTIFICATION_CHANNEL_COLS = {
    'id',
    'user_id',
    'type',
    'config_json',
    'enabled',
    'key_version',
    'created_at',
}
_EXPECTED_NOTIFICATION_RULE_COLS = {
    'id',
    'user_id',
    'lottery_code',
    'strategy',
    'created_at',
}
_EXPECTED_NOTIFICATION_LOG_COLS = {
    'id',
    'user_id',
    'type',
    'payload',
    'status',
    'sent_at',
    'error',
    'created_at',
}
_EXPECTED_API_SOURCE_HEALTH_COLS = {
    'source',
    'last_success_at',
    'status',
    'error',
    'created_at',
}
_EXPECTED_ADMIN_AUDIT_LOG_COLS = {
    'id',
    'admin_id',
    'action',
    'target_type',
    'target_id',
    'old_values',
    'new_values',
    'created_at',
}
_EXPECTED_APSCHEDULER_JOB_COLS = {
    'id',
    'next_run_time',
    'job_state',
}
# apscheduler_jobs 不得含 created_at（或任何 TimestampMixin 列）：APScheduler
# SQLAlchemyJobStore.insert 只写 (id, next_run_time, job_state)，NOT NULL 的 created_at
# 会导致插入 IntegrityError → 调度器无法持久化任务 → 中奖静默漏通知（spec §4.3）。
_FORBIDDEN_APSCHEDULER_JOB_COLS = {'created_at', 'updated_at'}


def _cols(engine, table: str) -> set[str]:
    return {c['name'] for c in inspect(engine).get_columns(table)}


def test_notification_channel_columns(db_engine):
    assert _EXPECTED_NOTIFICATION_CHANNEL_COLS.issubset(_cols(db_engine, 'notification_channels')), (
        f'notification_channels 缺列: {_EXPECTED_NOTIFICATION_CHANNEL_COLS - _cols(db_engine, "notification_channels")}'
    )


def test_notification_rule_columns(db_engine):
    assert _EXPECTED_NOTIFICATION_RULE_COLS.issubset(_cols(db_engine, 'notification_rules')), (
        f'notification_rules 缺列: {_EXPECTED_NOTIFICATION_RULE_COLS - _cols(db_engine, "notification_rules")}'
    )


def test_notification_log_columns(db_engine):
    assert _EXPECTED_NOTIFICATION_LOG_COLS.issubset(_cols(db_engine, 'notification_logs')), (
        f'notification_logs 缺列: {_EXPECTED_NOTIFICATION_LOG_COLS - _cols(db_engine, "notification_logs")}'
    )


def test_api_source_health_columns(db_engine):
    assert _EXPECTED_API_SOURCE_HEALTH_COLS.issubset(_cols(db_engine, 'api_source_health')), (
        f'api_source_health 缺列: {_EXPECTED_API_SOURCE_HEALTH_COLS - _cols(db_engine, "api_source_health")}'
    )


def test_admin_audit_log_columns(db_engine):
    assert _EXPECTED_ADMIN_AUDIT_LOG_COLS.issubset(_cols(db_engine, 'admin_audit_logs')), (
        f'admin_audit_logs 缺列: {_EXPECTED_ADMIN_AUDIT_LOG_COLS - _cols(db_engine, "admin_audit_logs")}'
    )


def test_apscheduler_job_columns(db_engine):
    assert _EXPECTED_APSCHEDULER_JOB_COLS.issubset(_cols(db_engine, 'apscheduler_jobs')), (
        f'apscheduler_jobs 缺列: {_EXPECTED_APSCHEDULER_JOB_COLS - _cols(db_engine, "apscheduler_jobs")}'
    )
    actual = _cols(db_engine, 'apscheduler_jobs')
    forbidden_present = _FORBIDDEN_APSCHEDULER_JOB_COLS & actual
    assert not forbidden_present, (
        f'apscheduler_jobs 含禁止列 {forbidden_present}（APScheduler jobstore 不写这些列，'
        f'NOT NULL 会致插入失败 → 调度器无法持久化任务）'
    )


# ---------- 外键（spec §6 user 隔离 + lottery 引用） ----------


def test_foreign_keys(db_engine):
    fks = {}
    for tbl in (
        'notification_channels',
        'notification_rules',
        'notification_logs',
        'admin_audit_logs',
    ):
        fks[tbl] = {
            (fk['referred_table'], fk['constrained_columns'][0]) for fk in inspect(db_engine).get_foreign_keys(tbl)
        }
    assert ('users', 'user_id') in fks['notification_channels']
    assert ('users', 'user_id') in fks['notification_rules']
    assert ('lottery_types', 'lottery_code') in fks['notification_rules']
    assert ('users', 'user_id') in fks['notification_logs']
    assert ('users', 'admin_id') in fks['admin_audit_logs']


# ---------- 主键（spec §6 特殊主键） ----------


def test_api_source_health_primary_key(db_engine):
    # SQLAlchemy 2.x 移除了 Inspector.get_primary_keys；改用 get_pk_constraint
    pk = inspect(db_engine).get_pk_constraint('api_source_health')['constrained_columns']
    assert pk == ['source']


def test_apscheduler_job_primary_key(db_engine):
    pk = inspect(db_engine).get_pk_constraint('apscheduler_jobs')['constrained_columns']
    assert pk == ['id']


# ---------- 默认值（spec §6 默认值，须落到 DDL server_default） ----------


def test_defaults(db_engine):
    """Python 端默认值：ORM 在 insert 时应用（spec §6.2 列语义 + Plan T4c 用 Field(default=...)）。
    spec/plan 未要求 DDL server_default；此处验证默认值真实生效，并确认 notification_logs.status NOT NULL。"""
    with Session(db_engine) as s:
        s.add(User(username='u1', password_hash='x', invite_code='INV001'))
        s.add(
            LotteryType(
                code='ssq',
                name='双色球',
                category='welfare',
                spec_json='{}',
                draw_schedule_json='{}',
            )
        )
        s.commit()
        user = s.exec(select(User)).one()

        # channel: enabled / key_version 默认
        ch = NotificationChannel(user_id=user.id, type='bark', config_json='{"k":"v"}')
        s.add(ch)
        s.commit()
        assert s.get(NotificationChannel, ch.id).enabled is True
        assert s.get(NotificationChannel, ch.id).key_version == 1

        # rule: strategy 默认 every
        rule = NotificationRule(user_id=user.id, lottery_code='ssq')
        s.add(rule)
        s.commit()
        assert s.get(NotificationRule, rule.id).strategy == 'every'

        # health: status 默认 unknown
        h = ApiSourceHealth(source='mxnzp')
        s.add(h)
        s.commit()
        assert s.get(ApiSourceHealth, 'mxnzp').status == 'unknown'

    # notification_logs.status 无默认值 → DDL 须 NOT NULL（insert 必须显式给值）
    nl_cols = {c['name']: c for c in inspect(db_engine).get_columns('notification_logs')}
    assert nl_cols['status']['nullable'] is False


# ---------- job_state 列类型（LargeBinary，与 APScheduler jobstore 对齐） ----------


def test_apscheduler_job_state_is_largebinary(db_engine):
    cols = {c['name']: c for c in inspect(db_engine).get_columns('apscheduler_jobs')}
    # SQLite 中 LargeBinary 映射为 BLOB；type 应为 BLOB 或 LARGE_BINARY
    assert cols['job_state']['type'].__class__.__name__ in ('BLOB', 'LargeBinary', 'NullType')


# ---------- 可写回性（端到端：channel → rule → log + health + audit + job） ----------


def test_full_notification_writeback_roundtrip(db_engine):
    """端到端：插 user/lottery → channel/rule/log 可往返。"""
    with Session(db_engine) as s:
        s.add(User(username='bob', password_hash='x', invite_code='INV002'))
        s.add(
            LotteryType(
                code='dlt',
                name='大乐透',
                category='sport',
                spec_json='{}',
                draw_schedule_json='{}',
            )
        )
        s.commit()
        user = s.exec(select(User)).one()
        lottery = s.get(LotteryType, 'dlt')

        # channel
        ch = NotificationChannel(
            user_id=user.id,
            type='bark',
            config_json='{"key":"abc"}',
            enabled=True,
            key_version=1,
        )
        s.add(ch)
        s.commit()
        loaded_ch = s.get(NotificationChannel, ch.id)
        assert loaded_ch.type == 'bark'
        assert loaded_ch.config_json == '{"key":"abc"}'
        assert loaded_ch.key_version == 1

        # rule
        rule = NotificationRule(
            user_id=user.id,
            lottery_code=lottery.code,
            strategy='win_only',
        )
        s.add(rule)
        s.commit()
        loaded_rule = s.get(NotificationRule, rule.id)
        assert loaded_rule.strategy == 'win_only'

        # log
        log = NotificationLog(
            user_id=user.id,
            type='bark',
            payload='test',
            status='sent',
            sent_at=datetime(2026, 6, 22, 7, 0, tzinfo=UTC),
        )
        s.add(log)
        s.commit()
        loaded_log = s.get(NotificationLog, log.id)
        assert loaded_log.status == 'sent'
        assert loaded_log.sent_at is not None


def test_health_and_audit_writeback(db_engine):
    """health 与 audit 可写回。"""
    with Session(db_engine) as s:
        # health
        h = ApiSourceHealth(
            source='mxnzp',
            status='ok',
            last_success_at=datetime(2026, 6, 22, 21, 30, tzinfo=UTC),
        )
        s.add(h)
        s.commit()
        loaded_h = s.get(ApiSourceHealth, 'mxnzp')
        assert loaded_h.status == 'ok'
        assert loaded_h.last_success_at is not None

        # audit
        s.add(User(username='admin', password_hash='x', invite_code='INV003', role='admin'))
        s.commit()
        user = s.exec(select(User)).one()
        audit = AdminAuditLog(
            admin_id=user.id,
            action='verify_draw',
            target_type='draw_result',
            target_id='1',
            old_values='{"verified":false}',
            new_values='{"verified":true}',
        )
        s.add(audit)
        s.commit()
        loaded_audit = s.get(AdminAuditLog, audit.id)
        assert loaded_audit.action == 'verify_draw'
        assert loaded_audit.old_values == '{"verified":false}'


def test_apscheduler_job_writeback(db_engine):
    """apscheduler_jobs 可写回 bytes（pickle blob）。"""
    with Session(db_engine) as s:
        import pickle

        job_state = pickle.dumps({'func': 'app.jobs:fetch', 'args': ['ssq']})
        job = ApschedulerJob(
            id='fetch_ssq_20260622',
            next_run_time=datetime(2026, 6, 22, 21, 30, tzinfo=UTC),
            job_state=job_state,
        )
        s.add(job)
        s.commit()
        loaded = s.get(ApschedulerJob, 'fetch_ssq_20260622')
        assert loaded.job_state == job_state
        assert pickle.loads(loaded.job_state) == {'func': 'app.jobs:fetch', 'args': ['ssq']}
