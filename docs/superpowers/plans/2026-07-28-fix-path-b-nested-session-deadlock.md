# 修复方案 A：消除 path_b / period_summary 的嵌套 Session 死锁

## 根因（已复现验证）

`_path_b_summary`（jobs.py:274）和 `_push_period_summary`（jobs.py:333）在
`with Session(engine) as s:` 循环里调用 `notifier.notify_path_b` /
`notify_period_summary`，而这两个 notifier 方法内部又各开一个
`with Session(self._engine) as s`。两个 Session 共用同一个 `pool_size=1` 的
engine：外层 session 持有唯一连接，内层 session 借不到 -> 30s 超时 ->
`sqlalchemy.exc.TimeoutError: QueuePool limit of size 1 overflow 0 reached` ->
被 per-user try/except 吞成 `path_b_user_failed` ->
`notification_logs` 一条没写 -> **用户收不到任何推送（含中奖）**。

NAS 容器实测：07-27、07-28 两次 07:00 path_b 全失败。手动直接调
`notify_path_b` 0.1s 成功（无外层 session），经 `_path_b_summary` job 入口调
30s 超时复现。

## 修复策略（方案 A）

去掉外层 `with Session` 的长持有，改为「短读 session 取用户列表 ->
关闭 -> 循环调 notifier（notifier 内部开自己的短 session）」。
嵌套消除，死锁解除。这是最小语义改动，不改变 per-user 隔离 / DND 顺延 /
notifier 内部事务边界。

`_path_b_summary` 与 `_push_period_summary` 同构，一并修。

## TDD 步骤

### RED-1：新增回归测试 `test_path_b_summary_does_not_deadlock_on_real_notifier`

`tests/scheduler/test_jobs.py` 新增。**关键**：用真实 `Notifier`（带真实
engine，而非 MagicMock），让其内部真的开 Session，断言 `_path_b_summary`
在 ≤ 某阈值（如 5s，远小于 30s 死锁）内完成且不抛 TimeoutError。建一个 ssq
ticket + 一期 draw_result + comparison，让 `_collect_user_results` 有活干。

期望：修复前该测试 30s 后超时失败（复现死锁）；修复后 < 1s 通过。

### RED-2：新增 `test_weekly_report_does_not_deadlock_on_real_notifier`

同构覆盖 `_push_period_summary`（周报/月报共用入口）。用真实 Notifier +
`notify_period_summary` 走真实 Session。

### GREEN：改 jobs.py

`_path_b_summary`：
```python
yesterday = (datetime.now(_CST).date() - timedelta(days=1)).isoformat()
# 短读 session：取用户列表后立即关闭，不在循环期间持有连接。
# 否则外层 session 持有 pool_size=1 的唯一连接，内层 notify_path_b
# 再开 Session 借不到 -> 30s TimeoutError -> notification_logs 不写 ->
# 中奖静默漏通知（2026-07-28 NAS 实测复现）。
with Session(engine) as s:
    user_ids = [u.id for u in s.exec(select(User).where(User.enabled == True)).all()]
for uid in user_ids:
    try:
        notifier.notify_path_b(user_id=uid, date_str=yesterday)
    except Exception:
        logger.error('path_b_user_failed user_id=%s', uid, exc_info=True)
```

`_push_period_summary` 同构改法：短读 session 取 user_ids，关闭后循环调
`notifier.notify_period_summary`。per-user try/except 保留。

### REFACTOR：抽公共「遍历启用用户」helper

`_iter_enabled_user_ids(engine) -> list[int]`，path_b / period_summary 共用，
消除重复。保留 per-user 隔离注释。

## 验证

- 新测试通过（< 1s，无 TimeoutError）
- 全量 `uv run pytest` 不回归（554 existing green）
- 容器内手动跑 `_path_b_summary(str(e.url))`：< 1s 返回，不再 30s 超时
- 明天 07:00 path_b 实跑后 `notification_logs` 有行、status=sent 或 failed（有内容即证明链路通了）

## 不改的边界

- 不动 `notify_path_b` / `notify_period_summary` 内部 session（它们是正确的
  短 session 边界，且 path_a 也用同款模式）
- 不动 per-user try/except 隔离（silent-failure 纪律）
- 不动 DND 顺延逻辑
- 不动 pool_size=1（SQLite 单写连接纪律，CLAUDE.md）

## commit 计划

- `fix: prevent path_b/period_summary nested-session deadlock on pool_size=1 engine`
  RED 测试 + GREEN 修复 + REFACTOR 抽 helper，单 commit（TDD 一轮闭环）

## 部署

修复后需在 NAS 重建镜像并重启容器，让明天 07:00 path_b 用上新代码。
（用户确认后再执行 deploy，不在本计划内擅自 push/部署）
