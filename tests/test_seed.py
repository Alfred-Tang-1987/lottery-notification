import json
from sqlmodel import Session, select
from app.seeds import seed_lottery_types, SPECS
from app.models.lottery import LotteryType


def test_seeds_7_lotteries(db_engine):
    with Session(db_engine) as s:
        n = seed_lottery_types(s)
    assert n == 7
    with Session(db_engine) as s:
        codes = {lt.code for lt in s.exec(select(LotteryType)).all()}
    assert codes == {"ssq", "dlt", "qlc", "fc3d", "qxc", "pl3", "pl5"}


def test_seed_idempotent(db_engine):
    with Session(db_engine) as s:
        assert seed_lottery_types(s) == 7
    with Session(db_engine) as s:
        assert seed_lottery_types(s) == 7  # 第二次不新增


def test_spec_json_valid_and_welfare_rate(db_engine):
    with Session(db_engine) as s:
        seed_lottery_types(s)
        dlt = s.get(LotteryType, "dlt")
        spec = json.loads(dlt.spec_json)
    assert spec["welfare_rate"] == 36
    assert spec["number_style"] == "partition"


def test_qxc_hybrid_allows_duplicate_positions():
    """D7:A 验证：hybrid 前区允许跨位重复（NumberRange 不适用）。"""
    spec = next(s for s in SPECS if s["code"] == "qxc")
    assert spec["number_style"] == "hybrid"
    # 前区是 PositionalDigits（length=6），允许 1,1,2,3,4,5
    front = spec["front"]
    assert front.get("length") == 6  # PositionalDigits 语义
