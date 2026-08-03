from sqlalchemy import inspect


def test_all_13_tables_created(db_engine):
    insp = inspect(db_engine)
    names = set(insp.get_table_names())
    expected = {
        'users',
        'lottery_types',
        'tickets',
        'draw_results',
        'draw_corrections',
        'pending_comparisons',
        'comparisons',
        'prize_claims',
        'notification_channels',
        'notification_rules',
        'notification_logs',
        'api_source_health',
        'admin_audit_logs',
        'apscheduler_jobs',
    }
    assert expected.issubset(names), f'缺表: {expected - names}'


def test_draw_results_unique_constraint(db_engine):
    insp = inspect(db_engine)
    uqs = {tuple(c['column_names']) for c in insp.get_unique_constraints('draw_results')}
    assert ('lottery_code', 'draw_no') in uqs


def test_comparisons_unique_constraint(db_engine):
    insp = inspect(db_engine)
    uqs = {tuple(c['column_names']) for c in insp.get_unique_constraints('comparisons')}
    assert ('draw_result_id', 'ticket_id') in uqs


def test_draw_costs_table_created(db_engine):
    """draw_costs 表结构（spec §4 期次成本记账）：字段 + 唯一约束兜底幂等。"""
    insp = inspect(db_engine)
    cols = {c['name'] for c in insp.get_columns('draw_costs')}
    assert {'id', 'user_id', 'lottery_code', 'draw_no', 'cost', 'draw_date', 'created_at'} == cols
    uqs = {tuple(c['column_names']) for c in insp.get_unique_constraints('draw_costs')}
    assert ('user_id', 'lottery_code', 'draw_no') in uqs

