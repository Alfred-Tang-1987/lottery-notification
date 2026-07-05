# T6f Halt 根因分析 — kimi-k2.7 token limit

> 日期：2026-07-05 ｜ Run: `wf_1e6df57a-5da` (task `wcrjmlqn9`) ｜ Halted: `plan-06/T6f`

## 1. 现象

plan-06 跑到 T6f implementor → halt。T6e 正确 skip（deterministic-completed 验证通过），
T6f 是首次 dispatch 即失败。

`.workflow/blocked.md` 标 `reason: model_unavailable`，error `agent returned null`。
**但实际不是限额**——router 返 400 token limit：

```
impl:T6f failed: API Error: 400
{"message":"Invalid request: Your request exceeded model token limit: 262144 (requested: 262533)"}
No fallback model group found for original model_group=anthropic/kimi-k2.7
Fallbacks=[{'anthropic/glm-4.7': ['anthropic/glm-4.7-fallback']}]
```

## 2. 根因链

| 步 | 事件 | 备注 |
|---|---|---|
| 1 | T6f implementor prompt **262533 token** > kimi-k2.7 router limit **262144**（超 389） | prompt 太大 |
| 2 | router fallback（glm-4.7）→ 同 prompt 也超 limit → 400 | fallback 无效（同 limit） |
| 3 | `agent()` 返回 null（router 全链 400） | |
| 4 | `dispatchImpl` catch null → `model_unavailable` halt | **CLAUDE.md「dispatchImpl null guard」逻辑** |
| 5 | implementor **无 retryModel**（USAGE §7.2：仅 bootstrap 启用 `'opus'`） | 不升级重试 |

**halt reason 标 `model_unavailable` 是误导**（像限额），实际是 **prompt 太大撞 model token limit**（非限额、非能力）。

## 3. 为什么 prompt 262k

T6f implementor prompt 组成（`buildPrompt('implementor', implCtx)`）：plan T6f spec + spec §12.2 row 9 + referencePaths + silentFailureContext + fixIssues（fix-round）+ fetchedContext（needs_context 时）+ failedApproaches。

262k 异常大的可能来源（agent `a99ecae4` JSONL 1.2MB，最大，含多轮 dispatch）：
- **fetchedContext**（contextFetcher 返回大量 code/spec 段，最可能）
- **fix-round 累积**（前轮 findings + cross-reviewer note）
- T6f 范围大（后端 7 endpoint + 前端 Admin.vue + 测试，plan T6f 15 行但功能多）

**结构性问题**：T6f 范围大 → implementor 多轮 → prompt 累积超 limit。

## 4. T6f 部分实现（已 stash）

`git stash push -u`（stash@{0}），保留：
- `app/api/admin_ext.py`（462 行，7 endpoints：smtp-config/smtp-test/invite-codes POST+GET/lotteries toggle/push-logs/audit-logs）
- `tests/api/test_admin_t6f.py`（262 行，12 tests）
- `app/main.py`（注册 admin_ext router）

测试结果：**6 passed / 6 failed**
- ✅ 6 passed：基础 endpoint 功能
- ❌ 6 failed：
  - push-logs ×4：`TypeError: list indices must be integers or slices, not str`（test 期望 `resp['items']` 但实际返回 list——response 结构不一致）
  - invite-codes csrf / non-admin forbidden：`QueuePool TimeoutError` size 1 overflow 0（pool_size=1 + admin_ext session 嵌套 deadlock）

task #35/#36/#37（in_progress/completed）显示 implementor 正在修这些 bug 时被 token limit halt。

## 5. 处理

**stash（不 commit WIP）**。理由：
- 红测试不能 commit `feat(plan-06/T6f)`——会被 `extractCompletedFromSubjects`（deterministic-completed）误判完成 → 下次全新跑 skip T6f
- stash 保留部分实现，working tree clean，bootstrap 不识别 T6f（next 跑从 T6f 重做）

恢复：`git stash pop stash@{0}`（手动收尾时）

## 6. 后续收尾选项（按推荐度）

### A. 手动收尾（参照 T6e，推荐）
1. `git stash pop` → 修 6 失败（push-logs response 结构对齐 + admin_ext session 修正防 pool deadlock）
2. pytest 绿 → 派 spec/quality/hunter re-review → allGreen → commit `feat(plan-06/T6f): backend`
3. T6f 前端（Admin.vue）单独处理

### B. 拆 T6f（参照 T6b-T6g，根治 prompt 大）
T6f-1（SMTP+invite）/ T6f-2（lotteries+audit）/ T6f-3（push-logs filter）—— 每子 task prompt 小，不超 limit。

### C. 升 opus（治标）
plan-06 frontmatter `T6f: sonnet` → `T6f: opus`（glm-5.2[1M]，1M context）。能装 262k，但 prompt 效率低 + opus 贵。

## 7. workflow 改进建议（避免再触发）

| # | 建议 | 效果 | 复杂度 |
|---|---|---|---|
| 7.1 | **implementor 启用 retryModel='opus'**（dispatchImpl 第 4 参） | token limit 时升 opus（1M）重试，不 halt | 低（接线） |
| 7.2 | **isQuotaError 认 token limit**（正则加 `token.*limit\|exceeded.*token`） | halt reason 标 `token_limit`（区分限额），blocked.md 提示拆 task | 低 |
| 7.3 | **dispatchImpl 区分 token-limit vs quota**：前者升 opus，后者 halt | 精准路由（不一律 model_unavailable） | 中 |
| 7.4 | **prompt 大小预检 + 自动砍 context**：> limit 时砍 fetchedContext/failedApproaches（最远先丢） | 根治 prompt 膨胀 | 高 |

最性价比：**7.1 + 7.2**（implementor retryModel='opus' + isQuotaError 认 token limit）。下次 T6f 类大 task token limit 时，升 opus 跑通 + halt reason 清晰。

## 8. 本次改进 1+2 未触发

改进 1（OSCILLATING 升 opus）/ 改进 2（flip-flop 区分）针对 **review 循环振荡**。T6f 是 **initial implementor dispatch** 失败（token limit），没进 review 循环，所以未触发。改进 1+2 对 token-limit halt 无效——需 7.1（implementor retryModel）覆盖此场景。

## 9. 关键文件

- stash：`git stash list` → `stash@{0}`
- run transcript: `subagents/workflows/wf_1e6df57a-5da/`
- 相关 commit: `5e07c83`（改进 1+2）、`0cd42c6`（deterministic-completed）
