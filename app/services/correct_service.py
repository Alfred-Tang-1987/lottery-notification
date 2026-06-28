import json

from sqlalchemy.engine import Engine
from sqlmodel import Session

from app.models import DrawCorrection, DrawResult, PendingComparison


class DrawCorrectService:
    """开奖结果官方更正（spec §7.1）。"""

    def __init__(self, engine: Engine):
        self._engine = engine

    def correct(
        self, *, draw_result_id: int, new_front: tuple[int, ...], new_back: tuple[int, ...] | None, reason: str
    ) -> None:
        with Session(self._engine) as s:
            dr = s.get(DrawResult, draw_result_id)
            if dr is None:
                raise ValueError(f'draw_result {draw_result_id} 不存在')
            new_json = {'front': list(new_front), 'back': list(new_back) if new_back else None}
            new_numbers_json = json.dumps(new_json)
            # 记录更正历史（old_numbers_json 读当前 dr.numbers_json，须在覆盖前构造）
            s.add(
                DrawCorrection(
                    draw_result_id=draw_result_id,
                    old_numbers_json=dr.numbers_json,
                    new_numbers_json=new_numbers_json,
                    reason=reason,
                )
            )
            # 原地更新号码 + version++
            dr.numbers_json = new_numbers_json
            dr.version += 1
            # 重新生成 outbox（触发重比，CompareService._upsert_comparison 原地更新）
            s.add(PendingComparison(draw_result_id=draw_result_id))
            s.commit()
