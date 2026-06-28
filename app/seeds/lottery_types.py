import json

from app.seeds.spec_schema import LotterySpecModel

# 7 大彩种规格（spec §5.1）。draw_days 用 Python weekday: 周一=0 … 周日=6
SPECS: list[dict] = [
    {
        'code': 'ssq',
        'name': '双色球',
        'category': 'welfare',
        'number_style': 'partition',
        'front': {'min': 1, 'max': 33, 'count': 6},
        'back': {'min': 1, 'max': 16, 'count': 1},
        'draw_days': [1, 3, 6],  # 二/四/日
        'play_types': ['single', 'fushi', 'dantuo'],
        'welfare_rate': 36,
        'price_per_bet': 200,
    },
    {
        'code': 'dlt',
        'name': '大乐透',
        'category': 'sport',
        'number_style': 'partition',
        'front': {'min': 1, 'max': 35, 'count': 5},
        'back': {'min': 1, 'max': 12, 'count': 2},
        'draw_days': [0, 2, 5],  # 一/三/六
        'play_types': ['single', 'fushi', 'dantuo'],
        'welfare_rate': 36,
        'price_per_bet': 200,
    },
    {
        'code': 'qlc',
        'name': '七乐彩',
        'category': 'welfare',
        'number_style': 'partition',
        'front': {'min': 1, 'max': 30, 'count': 7},
        'back': {'min': 1, 'max': 30, 'count': 1},  # 特别号，同池无放回
        'draw_days': [0, 2, 4],  # 一/三/五
        'play_types': ['single', 'fushi', 'dantuo'],
        'welfare_rate': 36,
        'price_per_bet': 200,
    },
    {
        'code': 'fc3d',
        'name': '福彩3D',
        'category': 'welfare',
        'number_style': 'positional',
        'front': {'min': 0, 'max': 9, 'length': 3},
        'back': None,
        'draw_days': [0, 1, 2, 3, 4, 5, 6],  # 每日
        'play_types': ['danxuan', 'zuxuan3', 'zuxuan6'],
        'welfare_rate': 34,
        'price_per_bet': 200,
    },
    {
        'code': 'qxc',
        'name': '七星彩',
        'category': 'sport',
        'number_style': 'hybrid',
        'front': {'min': 0, 'max': 9, 'length': 6},  # 按位 6 位
        'back': {'min': 0, 'max': 14, 'count': 1},
        'draw_days': [1, 4, 6],  # 二/五/日
        'play_types': ['single', 'fushi', 'dantuo'],
        'welfare_rate': 37,
        'price_per_bet': 200,
    },
    {
        'code': 'pl3',
        'name': '排列3',
        'category': 'sport',
        'number_style': 'positional',
        'front': {'min': 0, 'max': 9, 'length': 3},
        'back': None,
        'draw_days': [0, 1, 2, 3, 4, 5, 6],
        'play_types': ['zhixuan', 'zuxuan3', 'zuxuan6'],
        'welfare_rate': 34,
        'price_per_bet': 200,
    },
    {
        'code': 'pl5',
        'name': '排列5',
        'category': 'sport',
        'number_style': 'positional',
        'front': {'min': 0, 'max': 9, 'length': 5},
        'back': None,
        'draw_days': [0, 1, 2, 3, 4, 5, 6],
        'play_types': ['zhixuan'],
        'welfare_rate': 34,
        'price_per_bet': 200,
    },
]

# 全部校验
for _s in SPECS:
    LotterySpecModel(**_s)  # 启动时即校验，错即崩


def seed_lottery_types(session) -> int:
    """幂等写入 7 彩种到 lottery_types。返回写入/更新条数。"""
    from app.models.lottery import LotteryType

    count = 0
    for spec in SPECS:
        existing = session.get(LotteryType, spec['code'])
        spec_json = json.dumps(spec, ensure_ascii=False)
        sched = json.dumps({'draw_days': spec['draw_days']})
        if existing is None:
            session.add(
                LotteryType(
                    code=spec['code'],
                    name=spec['name'],
                    category=spec['category'],
                    spec_json=spec_json,
                    draw_schedule_json=sched,
                    enabled=True,
                )
            )
            count += 1
        else:
            existing.spec_json = spec_json
            existing.draw_schedule_json = sched
            count += 1
    session.commit()
    return count
