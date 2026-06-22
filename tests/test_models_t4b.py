"""T4b: 号码 + 开奖 + 比对 表 schema 校验（对齐 spec §6 数据模型）。

覆盖 ticket / draw_results / draw_corrections / pending_comparisons /
comparisons / prize_claims 六张表的字段、外键、默认值、唯一约束与可写回性。
"""
from datetime import datetime, timezone

from sqlalchemy import inspect
from sqlmodel import Session, select

from app.models.comparison import Comparison, PrizeClaim
from app.models.draw import DrawCorrection, DrawResult, PendingComparison
from app.models.lottery import LotteryType
from app.models.ticket import Ticket
from app.models.user import User


# ---------- 字段存在性（spec §6 逐列核对） ----------

_EXPECTED_TICKET_COLS = {
    "id", "user_id", "lottery_code", "play_type", "numbers_json", "tuo_json",
    "label", "multiplier", "append", "cost", "enabled", "created_at",
}
_EXPECTED_DRAW_RESULT_COLS = {
    "id", "lottery_code", "draw_no", "draw_date", "numbers_json", "source",
    "fetched_at", "verified", "version", "created_at",
}
_EXPECTED_DRAW_CORRECTION_COLS = {
    "id", "draw_result_id", "old_numbers_json", "new_numbers_json",
    "corrected_at", "reason", "created_at",
}
_EXPECTED_PENDING_COMPARISON_COLS = {
    "id", "draw_result_id", "created_at", "processed_at",
}
_EXPECTED_COMPARISON_COLS = {
    "id", "user_id", "draw_result_id", "ticket_id", "hits_json", "prize_tier",
    "prize_amount", "is_win", "corrected_at", "created_at",
}
_EXPECTED_PRIZE_CLAIM_COLS = {
    "id", "comparison_id", "status", "deadline", "claimed_at", "created_at",
}


def _cols(engine, table: str) -> set[str]:
    return {c["name"] for c in inspect(engine).get_columns(table)}


def test_ticket_columns(db_engine):
    assert _EXPECTED_TICKET_COLS.issubset(_cols(db_engine, "tickets")), (
        f"tickets 缺列: {_EXPECTED_TICKET_COLS - _cols(db_engine, 'tickets')}"
    )


def test_draw_result_columns(db_engine):
    assert _EXPECTED_DRAW_RESULT_COLS.issubset(
        _cols(db_engine, "draw_results")
    ), f"draw_results 缺列: {_EXPECTED_DRAW_RESULT_COLS - _cols(db_engine, 'draw_results')}"


def test_draw_correction_columns_including_corrected_at(db_engine):
    """spec §6 明确 draw_corrections 含 corrected_at。"""
    actual = _cols(db_engine, "draw_corrections")
    assert _EXPECTED_DRAW_CORRECTION_COLS.issubset(actual), (
        f"draw_corrections 缺列: {_EXPECTED_DRAW_CORRECTION_COLS - actual}"
    )


def test_pending_comparison_columns(db_engine):
    actual = _cols(db_engine, "pending_comparisons")
    assert _EXPECTED_PENDING_COMPARISON_COLS.issubset(actual), (
        f"pending_comparisons 缺列: {_EXPECTED_PENDING_COMPARISON_COLS - actual}"
    )


def test_comparison_columns(db_engine):
    actual = _cols(db_engine, "comparisons")
    assert _EXPECTED_COMPARISON_COLS.issubset(actual), (
        f"comparisons 缺列: {_EXPECTED_COMPARISON_COLS - actual}"
    )


def test_prize_claim_columns(db_engine):
    actual = _cols(db_engine, "prize_claims")
    assert _EXPECTED_PRIZE_CLAIM_COLS.issubset(actual), (
        f"prize_claims 缺列: {_EXPECTED_PRIZE_CLAIM_COLS - actual}"
    )


# ---------- 外键（spec §6 user 隔离 + draw/ticket 引用） ----------

def test_foreign_keys(db_engine):
    fks = {}
    for tbl in (
        "tickets", "draw_corrections", "pending_comparisons",
        "comparisons", "prize_claims",
    ):
        fks[tbl] = {
            (fk["referred_table"], fk["constrained_columns"][0])
            for fk in inspect(db_engine).get_foreign_keys(tbl)
        }
    assert ("users", "user_id") in fks["tickets"]
    assert ("lottery_types", "lottery_code") in fks["tickets"]
    assert ("draw_results", "draw_result_id") in fks["draw_corrections"]
    assert ("draw_results", "draw_result_id") in fks["pending_comparisons"]
    assert ("users", "user_id") in fks["comparisons"]
    assert ("draw_results", "draw_result_id") in fks["comparisons"]
    assert ("tickets", "ticket_id") in fks["comparisons"]
    assert ("comparisons", "comparison_id") in fks["prize_claims"]


# ---------- 唯一约束（spec §6 幂等保证） ----------

def test_draw_results_unique_lottery_code_draw_no(db_engine):
    uqs = {
        tuple(c["column_names"])
        for c in inspect(db_engine).get_unique_constraints("draw_results")
    }
    assert ("lottery_code", "draw_no") in uqs


def test_comparisons_unique_draw_result_ticket(db_engine):
    uqs = {
        tuple(c["column_names"])
        for c in inspect(db_engine).get_unique_constraints("comparisons")
    }
    assert ("draw_result_id", "ticket_id") in uqs


# ---------- 默认值（spec §6 默认值，须落到 DDL server_default） ----------
# 关键：server_default 必须在 DDL 层落地（非 None），否则裸 SQL 插入会违反 NOT NULL。
# 这里断言 default is not None（DB 层 DEFAULT 子句存在），不断言具体字面量
# （不同方言/版本字面量形式不同，但只要非 None 即保证 DDL 有 DEFAULT）。

def test_defaults(db_engine):
    dr_cols = {c["name"]: c for c in inspect(db_engine).get_columns("draw_results")}
    assert dr_cols["verified"]["default"] is not None
    assert dr_cols["version"]["default"] is not None

    cmp_cols = {c["name"]: c for c in inspect(db_engine).get_columns("comparisons")}
    assert cmp_cols["is_win"]["default"] is not None

    pc_cols = {c["name"]: c for c in inspect(db_engine).get_columns("prize_claims")}
    assert pc_cols["status"]["default"] is not None

    pend_cols = {
        c["name"]: c for c in inspect(db_engine).get_columns("pending_comparisons")
    }
    assert pend_cols["processed_at"]["nullable"] is True

    tk_cols = {c["name"]: c for c in inspect(db_engine).get_columns("tickets")}
    assert tk_cols["multiplier"]["default"] is not None
    assert tk_cols["append"]["default"] is not None
    assert tk_cols["enabled"]["default"] is not None


def test_raw_sql_insert_honors_defaults(db_engine):
    """行为校验：裸 SQL 插入（绕过 ORM 默认）必须命中 DDL DEFAULT，
    证明 server_default 真正落到 schema（spec §6 NOT NULL 列的兜底）。"""
    from sqlalchemy import text

    with db_engine.begin() as conn:
        # 建前置 user/lottery/draw（裸 SQL，仅必填列；created_at 由 T4a mixin
        # 仅 Python 端默认，故裸 SQL 显式给值，聚焦验证 T4b 列的 server_default）
        conn.execute(text(
            "INSERT INTO users (username, password_hash, role, invite_code, "
            "enabled, created_at) VALUES ('u1', 'h', 'user', 'INV', 1, '2026-01-01')"
        ))
        conn.execute(text(
            "INSERT INTO lottery_types (code, name, category, spec_json, "
            "draw_schedule_json, enabled, schema_version, created_at) "
            "VALUES ('ssq', 'x', 'welfare', '{}', '{}', 1, 1, '2026-01-01')"
        ))
        conn.execute(text(
            "INSERT INTO draw_results (lottery_code, draw_no, draw_date, "
            "numbers_json, source, created_at) "
            "VALUES ('ssq', '001', '2026-01-01', '{}', 'mxnzp', '2026-01-01')"
        ))
        # 插 ticket 不给 multiplier/append/enabled/cost → 应取 DDL DEFAULT
        # （created_at 来自 T4a mixin 的 Python 端默认，裸 SQL 显式给值）
        conn.execute(text(
            "INSERT INTO tickets (user_id, lottery_code, play_type, numbers_json, "
            "created_at) VALUES (1, 'ssq', 'single', '{}', '2026-01-01')"
        ))
        row = conn.execute(text(
            "SELECT multiplier, append, enabled, cost FROM tickets WHERE id = 1"
        )).one()
        assert row[0] == 1   # multiplier DEFAULT 1
        assert row[1] == 0   # append DEFAULT false
        assert row[2] == 1   # enabled DEFAULT true
        assert row[3] == 0   # cost DEFAULT 0
        dr = conn.execute(text(
            "SELECT verified, single_source, version FROM draw_results WHERE id = 1"
        )).one()
        assert dr[0] == 0    # verified DEFAULT false
        assert dr[1] == 0    # single_source DEFAULT false
        assert dr[2] == 1    # version DEFAULT 1


# ---------- 可写回性（端到端：ticket → draw → comparison → claim） ----------

def test_full_compare_writeback_roundtrip(db_engine):
    """端到端：插 user/lottery → ticket/draw → comparison/prize_claim 可往返。"""
    with Session(db_engine) as s:
        s.add(User(username="alice", password_hash="x", invite_code="INV001"))
        s.add(LotteryType(
            code="ssq", name="双色球", category="welfare",
            spec_json="{}", draw_schedule_json="{}",
        ))
        s.commit()
        user = s.exec(select(User)).one()
        lottery = s.get(LotteryType, "ssq")

        s.add(Ticket(
            user_id=user.id, lottery_code=lottery.code, play_type="single",
            numbers_json='{"front":[1,2,3,4,5,6],"back":[7]}',
            multiplier=2, append=False, cost=400,
        ))
        draw = DrawResult(
            lottery_code="ssq", draw_no="2026062",
            draw_date=datetime(2026, 6, 22, tzinfo=timezone.utc),
            numbers_json='{"front":[1,2,3,7,8,9],"back":[7]}',
            source="mxnzp", verified=True,
        )
        s.add(draw)
        s.commit()

        s.add(PendingComparison(draw_result_id=draw.id))
        s.commit()

        ticket = s.exec(select(Ticket)).one()
        comparison = Comparison(
            user_id=user.id, draw_result_id=draw.id, ticket_id=ticket.id,
            hits_json='{"front":3,"back":1}', prize_tier=5,
            prize_amount=300, is_win=True,
        )
        s.add(comparison)
        s.add(DrawCorrection(
            draw_result_id=draw.id, old_numbers_json='{"back":[7]}',
            new_numbers_json='{"back":[8]}', reason="官方更正",
        ))
        s.commit()

        from datetime import timedelta
        deadline = datetime(2026, 8, 21, tzinfo=timezone.utc)
        s.add(PrizeClaim(comparison_id=comparison.id, status="pending", deadline=deadline))
        s.commit()

        # 读回验证
        loaded_cmp = s.get(Comparison, comparison.id)
        assert loaded_cmp.is_win is True
        assert loaded_cmp.prize_amount == 300
        loaded_claim = s.exec(select(PrizeClaim)).one()
        assert loaded_claim.status == "pending"
        loaded_corr = s.exec(select(DrawCorrection)).one()
        assert loaded_corr.reason == "官方更正"
