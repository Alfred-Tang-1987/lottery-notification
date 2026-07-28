"""推送链路诊断脚本（systematic-debugging Phase 1 取证）。

用法（NAS 宿主机，项目根目录）::

    python3 scripts/diag_push.py
    # 或指定 DB 路径
    LOTTERY_DB=./data/lottery.db python3 scripts/diag_push.py

用法（容器内，最贴近生产运行态）::

    docker compose exec app python /app/scripts/diag_push.py
    # 注意：scripts/ 未在 Dockerfile COPY 范围，需先 docker cp：
    docker cp scripts/diag_push.py lottery-notification:/tmp/diag_push.py
    docker compose exec app python /tmp/diag_push.py

脚本只读 DB，不写不改。解密 channel config 仅用于判断「密钥是否失配」，
绝不打印密钥明文（key 脱敏为前4后4）。

输出按推送链路顺序，每节标注「✓ 正常 / ⚠️ 异常 / ℹ️ 提示」便于定位断点。
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path


def _db_path() -> Path:
    env = os.environ.get('LOTTERY_DB')
    if env:
        return Path(env)
    # 默认相对项目根的 data/lottery.db
    here = Path(__file__).resolve().parent
    return here.parent / 'data' / 'lottery.db'


def _connect(path: Path) -> sqlite3.Connection:
    if not path.exists():
        print(f'❌ DB 不存在: {path}')
        print('   确认在 NAS 项目目录运行（./data/lottery.db），或设 LOTTERY_DB 环境变量。')
        sys.exit(2)
    # URI 模式 + readonly，双保险不改库
    conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _section(title: str) -> None:
    print(f'\n{"=" * 70}\n{title}\n{"=" * 70}')


def _table(rows: list[sqlite3.Row], cols: list[str], max_rows: int = 20) -> None:
    if not rows:
        print('  (空)')
        return
    shown = rows[:max_rows]
    widths = {c: max(len(c), max((len(str(r[c])) for r in shown if r[c] is not None), default=0)) for c in cols}
    header = ' | '.join(c.ljust(widths[c]) for c in cols)
    print(f'  {header}')
    print(f'  {"-" * len(header)}')
    for r in shown:
        print('  ' + ' | '.join(str(r[c] if r[c] is not None else '').ljust(widths[c]) for c in cols))
    if len(rows) > max_rows:
        print(f'  ... 共 {len(rows)} 行，仅显示前 {max_rows}')


# --------------------------------------------------------------------------- crypto
def _try_decrypt(config_json: str, key_version: int) -> tuple[bool, str]:
    """尝试解密 channel config，返回 (是否成功, 摘要)。

    成功只报字段名 + key 脱敏；失败报失败原因（密钥失配/密文损坏）。
    """
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        return False, 'cryptography 未安装(无法验证解密)'

    # 读 .env 的 CRYPTO_KEY_V1/V2
    env_path = Path(__file__).resolve().parent.parent / '.env'
    keys: dict[int, str] = {}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line.startswith('CRYPTO_KEY_V1='):
                keys[1] = line.split('=', 1)[1].strip().strip('"').strip("'")
            elif line.startswith('CRYPTO_KEY_V2='):
                v = line.split('=', 1)[1].strip().strip('"').strip("'")
                if v:
                    keys[2] = v
    if not keys:
        return False, '.env 未找到 CRYPTO_KEY(宿主机跑可能无 .env；容器内跑更准)'

    try:
        raw = json.loads(config_json)
    except Exception as e:
        return False, f'config_json 非 JSON: {e}'
    if 'ct' not in raw:
        return False, 'config 是明文(无 ct 字段，Notifier 会拒绝)'

    key = keys.get(key_version)
    if key is None:
        return False, f'key_version={key_version} 但 .env 无对应 key(轮换错位?)'
    try:
        pt = Fernet(key.encode()).decrypt(raw['ct'].encode()).decode()
        cfg = json.loads(pt)
        # 脱敏：url 全显，key 脱敏
        masked = {k: (v[:4] + '****' + v[-4:] if isinstance(v, str) and len(v) > 10 and k == 'key' else v) for k, v in cfg.items()}
        return True, f'解密成功 fields={list(cfg.keys())} cfg={masked}'
    except Exception as e:
        return False, f'解密失败 key_version={key_version}: {type(e).__name__}: {e}'


# --------------------------------------------------------------------------- probes
def probe_draw_results(conn: sqlite3.Connection) -> None:
    _section('[1] draw_results — ssq 最近 5 期（抓取是否落库 + verified）')
    rows = conn.execute(
        "SELECT draw_no, draw_date, verified, single_source, source, version "
        "FROM draw_results WHERE lottery_code='ssq' ORDER BY draw_date DESC LIMIT 5"
    ).fetchall()
    _table(rows, ['draw_no', 'draw_date', 'verified', 'single_source', 'source', 'version'])
    if rows:
        unverified = [r for r in rows if not r['verified']]
        if unverified:
            print(f'  ⚠️ 有 {len(unverified)} 期 verified=0 → 比对会被跳过(compare_service 只比 verified 的)')
        else:
            print('  ✓ 抓取落库正常且 verified')


def probe_pending(conn: sqlite3.Connection) -> None:
    _section('[2] pending_comparisons outbox — 比对是否被触发')
    pending = conn.execute(
        "SELECT COUNT(*) c FROM pending_comparisons WHERE processed_at IS NULL"
    ).fetchone()['c']
    total = conn.execute("SELECT COUNT(*) c FROM pending_comparisons").fetchone()['c']
    print(f'  未处理(processed_at IS NULL): {pending}')
    print(f'  总数: {total}')
    if pending > 0:
        print('  ⚠️ 有未认领的 outbox → CompareService 没跑或跑了没认领成功(job 没执行?)')
    else:
        print('  ✓ outbox 已认领（或为空——若 total=0 看 [1] 是否有 verified 行）')


def probe_users(conn: sqlite3.Connection) -> None:
    _section('[3] users — 账户状态 + DND 配置')
    rows = conn.execute(
        "SELECT id, username, role, enabled, dnd_json FROM users ORDER BY id"
    ).fetchall()
    _table(rows, ['id', 'username', 'role', 'enabled', 'dnd_json'])
    for r in rows:
        if not r['enabled']:
            print(f"  ⚠️ 用户 {r['username']} enabled=0 → _path_b_summary 跳过该用户")
        dnd = r['dnd_json']
        if dnd:
            try:
                d = json.loads(dnd)
                print(f"  ℹ️ 用户 {r['username']} dnd_json={d}（注: Notifier 当前未读此字段，DND 用硬编码 22-7）")
            except Exception:
                print(f"  ⚠️ 用户 {r['username']} dnd_json 非法 JSON")


def probe_tickets(conn: sqlite3.Connection) -> None:
    _section('[4] tickets — ssq 选号（5 组？enabled？lottery_code 对吗？）')
    rows = conn.execute(
        "SELECT id, user_id, lottery_code, play_type, enabled, multiplier, append, numbers_json "
        "FROM tickets WHERE lottery_code='ssq' ORDER BY id"
    ).fetchall()
    _table(rows, ['id', 'user_id', 'lottery_code', 'play_type', 'enabled', 'multiplier', 'append', 'numbers_json'])
    print(f'  ssq 注数: {len(rows)}')
    if not rows:
        print('  ❌ 没有 ssq 的 ticket！比对的输入是空的 → comparisons 必空 → path_b wins=0 loses=0 不推')
        # 看是否有别的 lottery_code 的注（用户可能选错彩种）
        others = conn.execute(
            "SELECT lottery_code, COUNT(*) c FROM tickets GROUP BY lottery_code"
        ).fetchall()
        if others:
            print('  ℹ️ 实际有的注:')
            _table(others, ['lottery_code', 'c'])
    disabled = [r for r in rows if not r['enabled']]
    if disabled:
        print(f'  ⚠️ {len(disabled)} 注 enabled=0 → 比对/推送都跳过')


def probe_comparisons(conn: sqlite3.Connection) -> None:
    _section('[5] comparisons — 比对是否产生结果（path_b 的数据源）')
    rows = conn.execute(
        "SELECT c.id, c.user_id, c.draw_result_id, d.draw_no, c.is_win, c.prize_tier, "
        "c.prize_amount, c.unresolved "
        "FROM comparisons c JOIN draw_results d ON c.draw_result_id=d.id "
        "WHERE d.lottery_code='ssq' ORDER BY c.id DESC LIMIT 20"
    ).fetchall()
    _table(rows, ['id', 'user_id', 'draw_result_id', 'draw_no', 'is_win', 'prize_tier', 'prize_amount', 'unresolved'])
    total = conn.execute(
        "SELECT COUNT(*) c FROM comparisons c JOIN draw_results d ON c.draw_result_id=d.id WHERE d.lottery_code='ssq'"
    ).fetchone()['c']
    print(f'  ssq comparisons 总数: {total}')
    if not rows:
        print('  ❌ comparisons 为空 → path_b 查无结果 → wins=0 loses=0 → 不写 log 不推送')
        print('     根因在比对环节上游: ticket 没匹配 / draw 未 verified / outbox 没跑')
    else:
        wins = sum(1 for r in rows if r['is_win'])
        print(f'  最近 {len(rows)} 行中中奖 {wins} 行')


def probe_rules(conn: sqlite3.Connection) -> None:
    _section('[6] notification_rules — ssq 推送策略（every / win_only）')
    rows = conn.execute(
        "SELECT user_id, lottery_code, strategy FROM notification_rules WHERE lottery_code='ssq'"
    ).fetchall()
    _table(rows, ['user_id', 'lottery_code', 'strategy'])
    if not rows:
        print('  ℹ️ 无显式 rule → 默认 every（每期推）。若你期望 win_only 但没建 rule，默认是 every')
    for r in rows:
        if r['strategy'] == 'win_only':
            print(f"  ℹ️ 用户 {r['user_id']} ssq=win_only → 未中奖期不计入汇总（但 every 才是每期推）")


def probe_channels(conn: sqlite3.Connection) -> None:
    _section('[7] notification_channels — Bark 渠道是否启用 + config 能否解密')
    rows = conn.execute(
        "SELECT id, user_id, type, enabled, key_version, config_json FROM notification_channels ORDER BY id"
    ).fetchall()
    print('  渠道配置（config 不打印明文，仅验证可解密性）:')
    if not rows:
        print('  ❌ 无任何渠道配置 → _load_channels 返回空 → "no channels" FAILED + admin 告警')
        return
    for r in rows:
        ok, msg = _try_decrypt(r['config_json'], r['key_version'])
        flag = '✓' if (r['enabled'] and ok) else '⚠️'
        print(f"  {flag} ch#{r['id']} user={r['user_id']} type={r['type']} enabled={r['enabled']} kv={r['key_version']}")
        print(f"      {msg}")
    bark = [r for r in rows if r['type'] == 'bark']
    if not bark:
        print('  ⚠️ 没有 type=bark 的渠道')
    bark_enabled = [r for r in bark if r['enabled']]
    if bark and not bark_enabled:
        print('  ⚠️ 有 bark 渠道但 enabled=0')


def probe_logs(conn: sqlite3.Connection) -> None:
    _section('[8] notification_logs — 推送是否被触发 + 发送结果（最直接证据）')
    rows = conn.execute(
        "SELECT id, user_id, type, status, error, sent_at, created_at "
        "FROM notification_logs ORDER BY id DESC LIMIT 15"
    ).fetchall()
    _table(rows, ['id', 'user_id', 'type', 'status', 'error', 'sent_at', 'created_at'])
    if not rows:
        print('  ❌ 完全没有 notification_logs → notify_path_b 根本没被调用')
        print('     → 要么 path_b_summary job 没跑(看 [9])，要么 wins=loses=0 提前 return(看 [5])')
        return
    # 状态分布
    dist = conn.execute("SELECT status, COUNT(*) c FROM notification_logs GROUP BY status").fetchall()
    print('  状态分布:')
    for r in dist:
        print(f"    {r['status']}: {r['c']}")
    pending = [r for r in rows if r['status'] == 'pending']
    failed = [r for r in rows if r['status'] == 'failed']
    if pending:
        print(f'  ⚠️ {len(pending)} 条 pending → 发送后状态没回写(发送卡住或崩溃)')
    if failed:
        print(f'  ⚠️ {len(failed)} 条 failed(最近显示在最上)，看 error 字段定位:')
        for r in failed[:3]:
            print(f"      log#{r['id']} error: {r['error']}")


def probe_jobs(conn: sqlite3.Connection) -> None:
    _section('[9] apscheduler_jobs — 调度任务是否注册 + 下次运行时间')
    rows = conn.execute(
        "SELECT id, next_run_time FROM apscheduler_jobs ORDER BY next_run_time"
    ).fetchall()
    _table(rows, ['id', 'next_run_time'])
    if not rows:
        print('  ❌ 无任何调度任务 → 调度器没注册 job 或 jobstore 没持久化')
        print('     → sched.start() 可能因 pickle 失败崩溃，或 SCHEDULER_ENABLED=false')
        return
    ids = {r['id'] for r in rows}
    must_have = {'path_b_summary', 'path_a_poll_evening', 'float_refill', 'claim_expire_scan'}
    missing = must_have - ids
    if missing:
        print(f'  ⚠️ 缺失关键 job: {missing}')
    else:
        print('  ✓ 关键 job 已注册')
    # path_b 下次运行
    pb = next((r for r in rows if r['id'] == 'path_b_summary'), None)
    if pb:
        print(f"  ℹ️ path_b_summary next_run_time={pb['next_run_time']}（07:00 CST；若为 null 说明被禁用）")
    else:
        print('  ⚠️ 无 path_b_summary job → 次日汇总永不触发')


def probe_settings(conn: sqlite3.Connection) -> None:
    _section('[10] notification_settings — 全局开关')
    try:
        rows = conn.execute(
            "SELECT user_id, master_enable, path_a_enable, summary_time, new_numbers_default_enabled "
            "FROM notification_settings"
        ).fetchall()
    except sqlite3.OperationalError:
        print('  ℹ️ 表不存在（旧版本）→ 跳过')
        return
    _table(rows, ['user_id', 'master_enable', 'path_a_enable', 'summary_time', 'new_numbers_default_enabled'])
    for r in rows:
        if not r['master_enable']:
            print(f"  ℹ️ 用户 {r['user_id']} master_enable=0（注: 当前 notify_path_b 代码未检查此字段，不影响——但语义上应是关）")


def main() -> None:
    path = _db_path()
    print(f'推送链路诊断 — DB: {path}')
    if not path.exists():
        print('❌ DB 文件不存在。在 NAS 项目目录运行，或设 LOTTERY_DB 指向 data/lottery.db')
        sys.exit(2)
    conn = _connect(path)
    probes = [
        probe_draw_results, probe_pending, probe_users, probe_tickets,
        probe_comparisons, probe_rules, probe_channels, probe_logs,
        probe_jobs, probe_settings,
    ]
    for p in probes:
        try:
            p(conn)
        except Exception as e:
            print(f'\n{"=" * 70}\n[!] {p.__name__} 执行出错: {type(e).__name__}: {e}\n{"=" * 70}')

    _section('诊断完成 — 根据上述 ⚠️/❌ 标记定位断点')
    print('把完整输出贴回来，我据此定位根因后再动手修复（不盲改）。')
    conn.close()


if __name__ == '__main__':
    main()
