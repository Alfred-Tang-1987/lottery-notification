import { test } from 'node:test'
import assert from 'node:assert/strict'
import { allGreen, unionFiles, issuesFromReviews, collectReviewFindings, formatFindings, isQuotaError, errStr, matchesPlanFilter, classifyThrown, reviewHaltReason, reviewHaltForEmptyFailed, haltLikelySource, fixModelForRound, resolveMaxRounds, detectOscillation, distillLessonInput, applyLessonDecisions, formatLessonsForDistill, validateAmendResult, validateCheckoutResult, groupFindingsByFile, formatCrossReviewerNote, bareTaskId, dropParentTasks, extractCompletedFromSubjects, extractTaskKey, shouldEscalateOnOscillation, isFlipFlop, formatLessons, formatUniversalLessons, formatDomainLessons, updateFindingsHistory, formatFindingsHistory, hasRegressed } from '../lib.js'

const ok = { status: 'ok', diagnostics: { files_touched: ['a.py'] } }
const ok2 = { status: 'ok', diagnostics: { files_touched: ['b.py'] } }
const bad = { status: 'failed', diagnostics: { files_touched: ['a.py'], issues: ['bug'] } }

test('allGreen true only if every review ok', () => {
  assert.equal(allGreen(ok, ok, ok), true)
  assert.equal(allGreen(ok, bad, ok), false)
})

test('unionFiles dedupes across reviews', () => {
  assert.deepEqual(unionFiles(ok, ok2, bad).sort(), ['a.py', 'b.py'])
})

test('issuesFromReviews collects issues from failed reviews', () => {
  assert.deepEqual(issuesFromReviews(ok, bad, ok2), ['bug'])
})

// —— collectReviewFindings + formatFindings（Bug 1/2/3 修复）——
const specBad = { status: 'failed', diagnostics: { issues: ['MISSING: spec req X (a.py:10)'] } }
const qualBad = { status: 'failed', diagnostics: { issues: [{ severity: 'Critical', title: 'sql injection', file: 'a.py', fix: 'parameterize' }] } }
const huntBad = { status: 'failed', diagnostics: { silent_failures: ['except:pass in b.py:5'] } }

test('collectReviewFindings reads issues from spec+quality AND silent_failures from hunter', () => {
  const out = collectReviewFindings(specBad, qualBad, huntBad)
  assert.equal(out.length, 3)
  assert.equal(out[0].source, 'spec')
  assert.equal(out[1].source, 'quality')
  assert.equal(out[1].severity, 'Critical')
  assert.equal(out[2].source, 'hunter')           // Bug 2: hunter 不再被丢弃
})

test('collectReviewFindings skips ok reviews and empty diagnostics', () => {
  assert.deepEqual(collectReviewFindings(ok, ok2, { status: 'ok' }), [])
})

test('formatFindings never emits [object Object] and is readable', () => {
  const s = formatFindings(collectReviewFindings(specBad, qualBad, huntBad))
  assert.doesNotMatch(s, /\[object Object\]/)      // Bug 1
  assert.match(s, /\[spec\]/)
  assert.match(s, /\[quality\|Critical\]/)
  assert.match(s, /sql injection/)
  assert.match(s, /\(a\.py\)/)
})

test('formatFindings empty → empty string', () => {
  assert.equal(formatFindings([]), '')
})

test('isQuotaError detects quota/rate-limit/429 keywords', () => {
  assert.equal(isQuotaError(new Error('rate limit exceeded')), true)
  assert.equal(isQuotaError(new Error('quota exhausted for this model')), true)
  assert.equal(isQuotaError(new Error('429 too many requests')), true)
  assert.equal(isQuotaError(new Error('insufficient balance')), true)
  assert.equal(isQuotaError(new Error('model overloaded')), true)
  assert.equal(isQuotaError(new Error('syntax error')), false)
  assert.equal(isQuotaError(new Error('file not found')), false)
})

test('REGRESSION: isQuotaError 认中文 router 限额错误（dispatchImpl 归类 model_unavailable 的前提）', () => {
  // 本机 router 返回 "[1308][已达到 5 小时的使用上限]"——旧正则不认中文 → 不归类
  // model_unavailable → throw → 顶层 uncaught crash（observed wf_a80ebbf1 bootstrap）。
  assert.equal(isQuotaError(new Error('API Error: [1308][已达到 5 小时的使用上限。您的限额将在 2026-07-01 04:06:44 重置。]')), true)
  assert.equal(isQuotaError(new Error('额度不足')), true)
  assert.equal(isQuotaError(new Error('超出调用限制')), true)
})

test('errStr extracts message safely', () => {
  assert.equal(errStr(new Error('hello')), 'hello')
  assert.equal(errStr('plain string'), 'plain string')
  assert.equal(errStr(null), '')
  assert.equal(errStr(undefined), '')
  assert.equal(errStr({}), '[object Object]')
  assert.equal(errStr(new Error('x'.repeat(300))).length, 200)
})

// —— matchesPlanFilter（Bug 10 修复）——
test('matchesPlanFilter: no arg → all plans pass', () => {
  assert.equal(matchesPlanFilter({ id: 'plan-01', seq: '01' }, undefined), true)
  assert.equal(matchesPlanFilter({ id: 'plan-01', seq: '01' }, ''), true)
})

test('matchesPlanFilter: exact string match on id or seq', () => {
  assert.equal(matchesPlanFilter({ id: 'plan-03', seq: '03' }, 'plan-03'), true)
  assert.equal(matchesPlanFilter({ id: 'plan-03', seq: '03' }, '03'), true)
})

test('matchesPlanFilter: number 3 matches padded seq "03" and id "plan-03" (Bug 10)', () => {
  assert.equal(matchesPlanFilter({ id: 'plan-03', seq: '03' }, 3), true)
  assert.equal(matchesPlanFilter({ id: 'plan-03', seq: '03' }, '3'), true)
})

test('matchesPlanFilter: non-matching arg → false', () => {
  assert.equal(matchesPlanFilter({ id: 'plan-01', seq: '01' }, '02'), false)
  assert.equal(matchesPlanFilter({ id: 'plan-01', seq: '01' }, 5), false)
})

// —— bareTaskId（bootstrap task_id 源头归一化，防双重 plan 前缀）——
test('bareTaskId: strip plan-XX/ 前缀（plan-06/T1 → T1）', () => {
  assert.equal(bareTaskId('plan-06/T1'), 'T1')
  assert.equal(bareTaskId('plan-01/T6b'), 'T6b')
  assert.equal(bareTaskId('plan-12/T10'), 'T10')
})
test('bareTaskId: 裸 id 原样返回（T1 → T1）', () => {
  assert.equal(bareTaskId('T1'), 'T1')
  assert.equal(bareTaskId('T6b'), 'T6b')
})
test('bareTaskId: 非 string 输入先 String 化再 strip（防 toString 报错）', () => {
  assert.equal(bareTaskId(123), '123')
  assert.equal(bareTaskId(null), 'null')
})
test('REGRESSION (2026-07-05): plan-scoped task_id 须 strip，否则 taskKey 拼成 plan-06/plan-06/T1', () => {
  // bootstrap 实战返回 "plan-06/T1"；runTask taskKey = `plan-${seq}/${task.id}`
  // 若不 strip → taskKey = "plan-06/plan-06/T1" ≠ state.completed["plan-06/T1"] → 误判 pending → 重跑
  const sanitized = bareTaskId('plan-06/T1')
  const taskKey = `plan-${String(6).padStart(2, '0')}/${sanitized}`
  assert.equal(taskKey, 'plan-06/T1', 'taskKey 须为单层 plan 前缀，与 completed 同形')
})

// —— dropParentTasks（bootstrap leaf-guard，过滤非叶子父 task）——
test('dropParentTasks: T6 + T6b/T6c 共存 → drop T6（父说明段，非叶子）', () => {
  const tasks = ['T6', 'T6b', 'T6c', 'T6d'].map(id => ({ id }))
  assert.deepEqual(dropParentTasks(tasks).map(t => t.id), ['T6b', 'T6c', 'T6d'])
})
test('dropParentTasks: T6 无子 task → 保留（真叶子）', () => {
  const tasks = ['T1', 'T2', 'T6'].map(id => ({ id }))
  assert.deepEqual(dropParentTasks(tasks).map(t => t.id), ['T1', 'T2', 'T6'])
})
test('dropParentTasks: 子 task (T6b) 永远保留（不被当父）', () => {
  const tasks = ['T6', 'T6b', 'T6g'].map(id => ({ id }))
  assert.deepEqual(dropParentTasks(tasks).map(t => t.id), ['T6b', 'T6g'])
})
test('REGRESSION (2026-07-05): bootstrap 实战返回 T6+T6b..T6g+T7..T9 → drop T6，保留其余', () => {
  // wf_3e729d02 实战：bootstrap 返回 T1,T2,T3,T4,T5,T6,T6b,T6c,T6d,T6e,T6f,T6g,T7,T8,T9
  // T6 是「9 页面 UI 基础已完成、拆 6b-6g」父说明段，派 implementor 跑会混乱
  const tasks = ['T1','T2','T3','T4','T5','T6','T6b','T6c','T6d','T6e','T6f','T6g','T7','T8','T9'].map(id => ({ id }))
  const kept = dropParentTasks(tasks).map(t => t.id)
  assert.ok(!kept.includes('T6'), 'T6 须被 drop')
  assert.deepEqual(kept, ['T1','T2','T3','T4','T5','T6b','T6c','T6d','T6e','T6f','T6g','T7','T8','T9'])
})

// —— extractCompletedFromSubjects（git log subjects → completed，确定性正则提取）——
test('extractCompletedFromSubjects: 认 feat|fix|refactor 三种 type（去重）', () => {
  const subjects = [
    'feat(plan-06/T6d): MyStats 双饼图',
    'fix(plan-06/T6d): address review',   // fix 也认（T6d 既有 feat 也有 fix）
    'feat(plan-06/T6e): Settings',
    'refactor(plan-05/T3): rename',
    'chore(plan-06/T1): x',                // chore 不认
    'feat(plan-06/T6d): duplicate',        // 去重
  ]
  assert.deepEqual(extractCompletedFromSubjects(subjects).sort(),
    ['plan-05/T3', 'plan-06/T6d', 'plan-06/T6e'])
})
test('extractCompletedFromSubjects: 空数组/非数组 → []', () => {
  assert.deepEqual(extractCompletedFromSubjects([]), [])
  assert.deepEqual(extractCompletedFromSubjects(null), [])
  assert.deepEqual(extractCompletedFromSubjects('not array'), [])
})
test('extractCompletedFromSubjects: 无 (plan-X/T-Y) scope 的 commit 忽略', () => {
  assert.deepEqual(extractCompletedFromSubjects(['Merge pull request #1', 'docs: update', 'feat: no scope']), [])
})
test('REGRESSION (2026-07-05): plan-06/T6d feat+fix commit 都识别（bootstrap LLM 漏 T6d）', () => {
  // bootstrap agent (kimi-k2.7) evidence.completed 漏 T6d，但 git log 有 feat+fix commit。
  // runtime 改用 extractCompletedFromSubjects(git_log_subjects) 后，T6d 由正则确定性识别。
  const subjects = [
    'feat(plan-06/T6d): MyStats 双饼图+月柱图+中奖率/公益卡（ECharts dispose）',
    'fix(plan-06/T6d): pending_amount display + datetime UTC alignment',
    'feat(plan-06/T6e): Settings',
  ]
  const completed = extractCompletedFromSubjects(subjects)
  assert.ok(completed.includes('plan-06/T6d'), 'T6d 须被正则识别（不依赖 LLM）')
  assert.ok(completed.includes('plan-06/T6e'))
})

// —— extractTaskKey（commit subject → task key，扩展认 fix|refactor）——
test('extractTaskKey: feat|fix|refactor 都认', () => {
  assert.equal(extractTaskKey('feat(plan-06/T1): x'), 'plan-06/T1')
  assert.equal(extractTaskKey('fix(plan-06/T6d): y'), 'plan-06/T6d')
  assert.equal(extractTaskKey('refactor(plan-05/T3): z'), 'plan-05/T3')
})
test('extractTaskKey: chore/docs/无 type → null', () => {
  assert.equal(extractTaskKey('chore(plan-06/T1): x'), null)
  assert.equal(extractTaskKey('docs: update'), null)
  assert.equal(extractTaskKey('plan-06/T1: no type'), null)
})

// —— shouldEscalateOnOscillation（改进1: OSCILLATING 触发时升级 opus 一搏）——
test('shouldEscalateOnOscillation: 当前非 opus 且未升级过 → true（升级 opus 一搏）', () => {
  assert.equal(shouldEscalateOnOscillation('sonnet', false), true)
  assert.equal(shouldEscalateOnOscillation('haiku', false), true)
})
test('shouldEscalateOnOscillation: 已 opus → false（opus 也振荡，真 halt）', () => {
  assert.equal(shouldEscalateOnOscillation('opus', false), false)
})
test('shouldEscalateOnOscillation: 已升级过 opus 仍振荡 → false（不重复升级）', () => {
  assert.equal(shouldEscalateOnOscillation('opus', true), false)
  assert.equal(shouldEscalateOnOscillation('sonnet', true), false)
})

// —— isFlipFlop（改进2: 区分 flip-flop vs 补充）——
test('isFlipFlop: 同 title 跨轮反复出现 → true（flip-flop，真振荡）', () => {
  const mk = (specFindings) => ({ spec: { status: 'failed', findings: specFindings }, quality: { status: 'ok', findings: [] }, hunter: { status: 'ok', findings: [] } })
  const history = [
    mk([{ title: 'missing X', severity: 'Medium' }]),
    mk([{ title: 'missing Y', severity: 'Medium' }]),
    mk([{ title: 'missing X', severity: 'Medium' }]), // X 重复 → flip-flop
  ]
  assert.equal(isFlipFlop(history), true)
})
test('isFlipFlop: 每轮新 title（补充）→ false（非振荡，继续）', () => {
  const mk = (t) => ({ spec: { status: 'failed', findings: [{ title: t }] }, quality: { status: 'ok', findings: [] }, hunter: { status: 'ok', findings: [] } })
  const history = [mk('A'), mk('B'), mk('C')]
  assert.equal(isFlipFlop(history), false)
})
test('isFlipFlop: history < 2 → false（不够判定）', () => {
  assert.equal(isFlipFlop([]), false)
  assert.equal(isFlipFlop([{ spec: {}, quality: {}, hunter: {} }]), false)
})
test('isFlipFlop: 跨 reviewer 的 title 也算（quality title 在 prev.spec 出现过）', () => {
  const history = [
    { spec: { status: 'failed', findings: [{ title: 'shared issue' }] }, quality: { status: 'ok', findings: [] }, hunter: { status: 'ok', findings: [] } },
    { spec: { status: 'ok', findings: [] }, quality: { status: 'failed', findings: [{ title: 'shared issue' }] }, hunter: { status: 'ok', findings: [] } },
  ]
  assert.equal(isFlipFlop(history), true)
})

// —— classifyThrown（review catch 归类）——
test('classifyThrown: quota keywords → model_unavailable, 其余 → agent_error', () => {
  assert.equal(classifyThrown(new Error('quota exhausted')), 'model_unavailable')
  assert.equal(classifyThrown(new Error('429 too many requests')), 'model_unavailable')
  assert.equal(classifyThrown(new Error('rate limited')), 'model_unavailable')
  assert.equal(classifyThrown(new Error('syntax error')), 'agent_error')
  assert.equal(classifyThrown(new Error('network timeout')), 'agent_error')
})

// —— reviewHaltReason（review 后优先级检查；复用顶部 ok fixture）——
test('reviewHaltReason: 全 ok → null', () => {
  assert.equal(reviewHaltReason(ok, ok, ok), null)
})

test('reviewHaltReason: agent_error 优先于 model_unavailable', () => {
  assert.equal(reviewHaltReason({ status: 'agent_error' }, { status: 'model_unavailable' }, ok), 'agent_error')
  assert.equal(reviewHaltReason(ok, { status: 'agent_error' }, { status: 'model_unavailable' }), 'agent_error')
})

test('reviewHaltReason: 仅 model_unavailable → model_unavailable', () => {
  assert.equal(reviewHaltReason({ status: 'model_unavailable' }, ok, ok), 'model_unavailable')
  assert.equal(reviewHaltReason(ok, ok, { status: 'model_unavailable' }), 'model_unavailable')
})

// —— review_empty（thinking-only 空响应：agent() 静默空返回，无异常但无有效 review status）——
// 与 agent_error 区分：agent_error 是抛异常（safeAgent catch 构造）；review_empty 是空返回（瞬态模型 hiccup）。
// 守卫：status 缺失/为空/非法 → halt 'review_empty'，而非漏过 → 不静默继续跑空修复循环。
test('reviewHaltReason: 空/null/undefined review → review_empty（防 thinking-only 空响应静默哑火）', () => {
  assert.equal(reviewHaltReason(ok, ok, {}), 'review_empty')             // status 缺失
  assert.equal(reviewHaltReason(ok, ok, null), 'review_empty')           // 整个 review 为 null
  assert.equal(reviewHaltReason(ok, ok, undefined), 'review_empty')      // 整个 review 为 undefined
  assert.equal(reviewHaltReason(ok, ok, { status: '' }), 'review_empty') // status 空串
  assert.equal(reviewHaltReason(ok, ok, { status: 'weird' }), 'review_empty') // status 非法
  assert.equal(reviewHaltReason({}, {}, {}), 'review_empty')             // 三个全空
})

test('reviewHaltReason: 优先级 agent_error > model_unavailable > review_empty', () => {
  // agent_error 优先于 review_empty
  assert.equal(reviewHaltReason({ status: 'agent_error' }, ok, {}), 'agent_error')
  // model_unavailable 优先于 review_empty
  assert.equal(reviewHaltReason({ status: 'model_unavailable' }, ok, null), 'model_unavailable')
})

// —— reviewHaltForEmptyFailed（第二道守卫：failed 但 0 findings → halt，防空修复循环）——
// 防「合法 failed + 空 diagnostics」漏过 reviewHaltReason → collectReviewFindings 空 →
// implementor 收「0 项发现」跑空修复 → max rounds 误 halt。
const failedWithFindings = { status: 'failed', diagnostics: { issues: [{ severity: 'Critical', title: 'bug', file: 'a.py', fix: 'x' }] } }
const hunterFailedWithFindings = { status: 'failed', diagnostics: { silent_failures: [{ title: 'except:pass', file: 'b.py', fix: 'log' }] } }
const failedNoDiag = { status: 'failed' }                                         // 无 diagnostics 字段
const failedEmptyDiag = { status: 'failed', diagnostics: {} }                     // diagnostics 空对象
const failedEmptyIssues = { status: 'failed', diagnostics: { issues: [] } }       // issues 空数组（spec/quality 用 issues key）
const hunterFailedNoFindings = { status: 'failed', diagnostics: { silent_failures: [] } } // hunter 用 silent_failures key

test('reviewHaltForEmptyFailed: 任一 failed 但 0 findings → review_failed_no_findings', () => {
  assert.equal(reviewHaltForEmptyFailed(failedNoDiag, ok, ok), 'review_failed_no_findings')
  assert.equal(reviewHaltForEmptyFailed(ok, failedEmptyDiag, ok), 'review_failed_no_findings')
  assert.equal(reviewHaltForEmptyFailed(ok, failedEmptyIssues, ok), 'review_failed_no_findings')  // spec/quality issues 空
  assert.equal(reviewHaltForEmptyFailed(ok, ok, hunterFailedNoFindings), 'review_failed_no_findings') // hunter silent_failures 空
  // hunter failed 但只填了 issues（用错 key）→ silent_failures 空 → 仍判 no-findings（key 不匹配）
  assert.equal(reviewHaltForEmptyFailed(ok, ok, failedEmptyIssues), 'review_failed_no_findings')
})

test('reviewHaltForEmptyFailed: failed 且有 findings → null（正常进 fix-round）', () => {
  assert.equal(reviewHaltForEmptyFailed(failedWithFindings, ok, ok), null)
  assert.equal(reviewHaltForEmptyFailed(ok, failedWithFindings, hunterFailedWithFindings), null)
})

test('reviewHaltForEmptyFailed: 全 ok → null', () => {
  assert.equal(reviewHaltForEmptyFailed(ok, ok, ok), null)
})

test('reviewHaltForEmptyFailed: 不误判 failed+findings 与 ok 混合（仅 failed 行无 findings 才 halt）', () => {
  // spec failed 有 findings，quality ok，hunter failed 无 findings → halt（hunter 那行空）
  assert.equal(reviewHaltForEmptyFailed(failedWithFindings, ok, failedEmptyIssues), 'review_failed_no_findings')
})

// —— haltLikelySource（halt reason → 工作树脏状态来源语义）——
test('haltLikelySource: implementor 路径 → implementor changes', () => {
  assert.equal(haltLikelySource('review max rounds'), 'implementor changes')
  assert.equal(haltLikelySource('OSCILLATING'), 'implementor changes')
  assert.equal(haltLikelySource('implementor blocked in fix-round 2'), 'implementor changes')
  assert.equal(haltLikelySource('commit failed'), 'implementor changes')
  assert.equal(haltLikelySource('opus BLOCKED after context-fetch'), 'implementor changes')
  assert.equal(haltLikelySource('model_unavailable'), 'implementor changes')
  // Q1（本轮新增）: 方案 C 三个 simplify halt reason + commit out_of_scope 须映射到 implementor changes
  // ——simplify 改动可能残留工作树（diff 失败未回退 / amend 失败留 staged / checkout 失败留改动）
  assert.equal(haltLikelySource('simplify diff check failed'), 'implementor changes')
  assert.equal(haltLikelySource('simplify amend failed'), 'implementor changes')
  assert.equal(haltLikelySource('simplify checkout failed'), 'implementor changes')
  assert.equal(haltLikelySource('commit out_of_scope'), 'implementor changes')
})

// —— validateAmendResult / validateCheckoutResult（Q8：边界条件纯函数化，可 node:test 行为测试）——
// 从 run-plans.js 抽出的纯决策函数：subagent 返回值校验。sync.test QC-4 字节比较守护一致性。
test('validateAmendResult: ok=true + 40 位 hex sha → valid', () => {
  const r = validateAmendResult({ ok: true, sha: 'a'.repeat(40) })
  assert.equal(r.valid, true)
  assert.equal(r.sha, 'a'.repeat(40))
})

test('validateAmendResult: ok=true + 空 sha → invalid', () => {
  const r = validateAmendResult({ ok: true, sha: '' })
  assert.equal(r.valid, false)
  assert.match(r.error, /invalid sha/i)
})

test('validateAmendResult: ok=true + 短 sha（非 40 位）→ invalid', () => {
  const r = validateAmendResult({ ok: true, sha: 'abc123' })
  assert.equal(r.valid, false)
})

test('validateAmendResult: ok=true + sha 含非 hex 字符 → invalid', () => {
  const r = validateAmendResult({ ok: true, sha: 'z'.repeat(40) })
  assert.equal(r.valid, false)
})

test('validateAmendResult: ok=false → invalid，error 透传', () => {
  const r = validateAmendResult({ ok: false, sha: '', error: 'pre-commit hook blocked' })
  assert.equal(r.valid, false)
  assert.equal(r.error, 'pre-commit hook blocked')
})

test('validateAmendResult: null/undefined → invalid', () => {
  assert.equal(validateAmendResult(null).valid, false)
  assert.equal(validateAmendResult(undefined).valid, false)
})

test('validateCheckoutResult: ok=true + porcelain 空 → valid（工作树真 clean）', () => {
  const r = validateCheckoutResult({ ok: true, porcelain: '' })
  assert.equal(r.valid, true)
})

test('validateCheckoutResult: ok=true + porcelain 含残留文件 → invalid（Q4 兜底验证）', () => {
  // Q4: checkout 返回 ok:true 但 git status --porcelain 非空 → 实际未 clean，须 halt
  const r = validateCheckoutResult({ ok: true, porcelain: ' M app/foo.py' })
  assert.equal(r.valid, false)
  assert.match(r.error, /working tree not clean|porcelain/i)
})

test('validateCheckoutResult: ok=false → invalid，error 透传', () => {
  const r = validateCheckoutResult({ ok: false, error: 'permission denied' })
  assert.equal(r.valid, false)
  assert.equal(r.error, 'permission denied')
})

test('validateCheckoutResult: null/undefined → invalid', () => {
  assert.equal(validateCheckoutResult(null).valid, false)
  assert.equal(validateCheckoutResult(undefined).valid, false)
})

test('haltLikelySource: gate 路径 → gate restored（已 checkout 回原 HEAD）', () => {
  assert.equal(haltLikelySource('plan gate failed'), 'gate restored')
})

test('haltLikelySource: bootstrap 路径 → bootstrap frontmatter', () => {
  assert.equal(haltLikelySource('bootstrap failed'), 'bootstrap frontmatter')
  assert.equal(haltLikelySource('bootstrap blocked'), 'bootstrap frontmatter')
})

test('haltLikelySource: 未知 reason → unknown', () => {
  assert.equal(haltLikelySource('some novel reason'), 'unknown')
  assert.equal(haltLikelySource(''), 'unknown')
})

// —— fixModelForRound（最后 1 轮 fix 固定 opus；§5.1 难度递增，最后机会用最强 model）——
// review rounds 循环：round 1 failed → fix(用 baseModel) → ... → round N-1 failed → fix(最后 1 次，升级 opus) → round N failed → halt（无 fix）
// 故「最后 1 轮 fix」对应 round === maxRounds - 1。maxRounds 默认 4（即 round=3 的 fix 升级 opus）。
// maxRounds=0 表示无限：无「最后 1 轮」概念，永不升级 opus（防止无限 opus 烧钱）。
// 已是 opus 的 task 返回 'opus'（语义等价，不重复升级）。
test('fixModelForRound: 非最后轮用 baseModel（不升级）', () => {
  assert.equal(fixModelForRound(1, 'sonnet', 4), 'sonnet')
  assert.equal(fixModelForRound(2, 'sonnet', 4), 'sonnet')
  assert.equal(fixModelForRound(1, 'opus', 4), 'opus')
})

test('fixModelForRound: 最后 1 轮 fix（round === maxRounds - 1）强制 opus', () => {
  // maxRounds=4 → round=3 是最后 1 轮 fix
  assert.equal(fixModelForRound(3, 'sonnet', 4), 'opus')   // sonnet task 升级
  assert.equal(fixModelForRound(3, 'opus', 4), 'opus')     // 已是 opus 不重复升级
  // maxRounds=3 → round=2 是最后 1 轮 fix（兼容旧行为）
  assert.equal(fixModelForRound(2, 'sonnet', 3), 'opus')
  // maxRounds=5 → round=4 是最后 1 轮 fix
  assert.equal(fixModelForRound(4, 'sonnet', 5), 'opus')
})

test('fixModelForRound: maxRounds=0（无限模式）round<4 用 baseModel', () => {
  // 无限模式前 3 轮用 baseModel，给 sonnet 充分尝试机会
  assert.equal(fixModelForRound(1, 'sonnet', 0), 'sonnet')
  assert.equal(fixModelForRound(2, 'sonnet', 0), 'sonnet')
  assert.equal(fixModelForRound(3, 'sonnet', 0), 'sonnet')
  assert.equal(fixModelForRound(1, 'opus', 0), 'opus')
})

test('fixModelForRound: maxRounds=0（无限模式）round>=4 强制 opus', () => {
  // 无限模式从第 4 轮起升级 opus：前 3 轮 sonnet 没修好说明问题复杂，
  // 后续每轮用 opus 提升修复质量，直到 detectOscillation halt 或全绿
  assert.equal(fixModelForRound(4, 'sonnet', 0), 'opus')
  assert.equal(fixModelForRound(5, 'sonnet', 0), 'opus')
  assert.equal(fixModelForRound(10, 'sonnet', 0), 'opus')
  assert.equal(fixModelForRound(4, 'opus', 0), 'opus')   // 已是 opus 不重复升级
})

test('fixModelForRound: maxRounds 未传（向后兼容，默认 3）', () => {
  // 旧调用方未传 maxRounds → 默认 3 → round=2 升级 opus（旧行为）
  assert.equal(fixModelForRound(1, 'sonnet'), 'sonnet')
  assert.equal(fixModelForRound(2, 'sonnet'), 'opus')
})

// —— resolveMaxRounds（从 config 解析 max rounds，0/负数/非数字 → 0=无限）——
test('resolveMaxRounds: 默认 4（config 未配）', () => {
  assert.equal(resolveMaxRounds(undefined), 4)
  assert.equal(resolveMaxRounds({}), 4)
})

test('resolveMaxRounds: 显式配置覆盖默认', () => {
  assert.equal(resolveMaxRounds({ review_max_rounds: 3 }), 3)
  assert.equal(resolveMaxRounds({ review_max_rounds: 5 }), 5)
  assert.equal(resolveMaxRounds({ review_max_rounds: 10 }), 10)
})

test('resolveMaxRounds: 0/负数 → 0（无限模式）；非数字/null → 4（容错默认）', () => {
  assert.equal(resolveMaxRounds({ review_max_rounds: 0 }), 0)
  assert.equal(resolveMaxRounds({ review_max_rounds: -1 }), 0)
  assert.equal(resolveMaxRounds({ review_max_rounds: 'abc' }), 4)   // 非数字 → 默认 4
  assert.equal(resolveMaxRounds({ review_max_rounds: null }), 4)    // null → 默认 4
})

// —— detectOscillation（与 maxRounds 解耦：独立阈值，无限模式也有效）——
test('detectOscillation: 同文件 <3 round 不振荡', () => {
  assert.equal(detectOscillation([['a'], ['a']]).oscillating, false)
})

test('detectOscillation: 同文件 ≥3 round 振荡（与 maxRounds 无关）', () => {
  // 即便 maxRounds=10，振荡检测仍以 3 为阈值（独立防线）
  const r = detectOscillation([['a'], ['a'], ['a']])
  assert.equal(r.oscillating, true)
  assert.match(r.reason, /a touched in 3 rounds/)
})

test('detectOscillation: 连续两轮完全重叠 ≥2 文件振荡（需先满足 length >= 3）', () => {
  // 现有实现：length < 3 直接返回 false，故需 3 轮才检测连续重叠
  const r = detectOscillation([['a', 'b'], ['a', 'b'], ['a', 'b']])
  assert.equal(r.oscillating, true)
  // 同文件 ≥3 round 优先匹配，故 reason 是 touched in 3 rounds
  assert.match(r.reason, /touched in 3 rounds|consecutive rounds fix same files/)
})

test('detectOscillation: length < 3 不振荡（现有行为，无限模式靠此阈值）', () => {
  assert.equal(detectOscillation([]).oscillating, false)
  assert.equal(detectOscillation([['a']]).oscillating, false)
  assert.equal(detectOscillation([['a'], ['a']]).oscillating, false)
})

// —— lesson 自动提炼（distiller 输入构造 / 决策应用 / 现有 lessons 解析）——

test('distillLessonInput: halt 模式构造完整输入（haltInfo/reviewHistory/failedApproaches 字段映射）', () => {
  const haltInfo = { plan: 'plan-06', task: 'T6b', reason: 'OSCILLATING', last_error: 'files touched in 3 rounds' }
  const reviewHistory = [{ round: 1, spec: { status: 'failed', findings: [{ title: 'qxc 分类错误', severity: 'Important' }] } }]
  const failedApproaches = [{ task_id: 'T6b', reason: 'OSCILLATING', error: 'same files' }]
  const out = distillLessonInput('halted', haltInfo, reviewHistory, failedApproaches)
  assert.equal(out.mode, 'halted')
  assert.equal(out.halt_info.task, 'T6b')
  assert.equal(out.halt_info.reason, 'OSCILLATING')
  assert.deepEqual(out.review_history, reviewHistory)
  assert.deepEqual(out.failed_approaches, failedApproaches)
})

test('distillLessonInput: done 模式也接受（distiller 自行决定 skip）', () => {
  const out = distillLessonInput('done', null, [], [])
  assert.equal(out.mode, 'done')
  assert.equal(out.halt_info, null)
  assert.deepEqual(out.review_history, [])
  assert.deepEqual(out.failed_approaches, [])
})

test('distillLessonInput: halt_info 缺失字段容错（不 crash）', () => {
  const out = distillLessonInput('halted', {}, undefined, undefined)
  assert.equal(out.mode, 'halted')
  assert.equal(out.halt_info.task, undefined)
  assert.deepEqual(out.review_history, [])
  assert.deepEqual(out.failed_approaches, [])
})

test('applyLessonDecisions: append 新条目到现有 lessons.md 末尾', () => {
  const existing = `# Lessons Learned

## L-20260701T103320Z
title: 旧条目
detail: 旧内容
status: active
`
  const decisions = [{
    action: 'append',
    id: 'L-20260703T150000Z',
    title: 'DB split-commit 必须单事务',
    detail: 'DrawResult + outbox 分两次 commit，第二次失败导致 outbox 永不补',
    source: 'plan-06/T6b@20260702T132012Z',
    category: 'silent-failure',
  }]
  const out = applyLessonDecisions(existing, decisions)
  assert.match(out, /## L-20260701T103320Z[\s\S]*status: active/)
  assert.match(out, /## L-20260703T150000Z[\s\S]*title: DB split-commit 必须单事务[\s\S]*category: silent-failure/)
  // 原有条目仍在
  assert.match(out, /旧条目/)
})

test('applyLessonDecisions: update 替换指定 id 段落（保留其他条目）', () => {
  const existing = `# Lessons Learned

## L-20260701T103320Z
title: 旧条目
detail: 旧内容
status: active

## L-20260702T080000Z
title: 另一条目
detail: 另一内容
status: active
`
  const decisions = [{
    action: 'update',
    update_target_id: 'L-20260701T103320Z',
    id: 'L-20260701T103320Z',
    title: '更新后的标题',
    detail: '更新后的内容',
    source: 'plan-06/T6b@20260702T132012Z',
    category: 'convention',
  }]
  const out = applyLessonDecisions(existing, decisions)
  assert.match(out, /## L-20260701T103320Z[\s\S]*title: 更新后的标题[\s\S]*category: convention/)
  assert.doesNotMatch(out, /旧条目/)
  // 其他条目保留
  assert.match(out, /## L-20260702T080000Z[\s\S]*另一内容/)
})

test('applyLessonDecisions: skip 不修改任何内容', () => {
  const existing = `# Lessons Learned

## L-20260701T103320Z
title: 旧条目
detail: 旧内容
status: active
`
  const decisions = [{ action: 'skip', id: 'L-x', title: '应被忽略', detail: '应被忽略' }]
  const out = applyLessonDecisions(existing, decisions)
  assert.equal(out, existing)
})

test('applyLessonDecisions: 多决策组合（append + update + skip）按序应用', () => {
  const existing = `# Lessons Learned

## L-OLD
title: 旧
detail: 旧内容
status: active
`
  const decisions = [
    { action: 'update', update_target_id: 'L-OLD', id: 'L-OLD', title: '更新', detail: '更新内容', category: 'convention' },
    { action: 'append', id: 'L-NEW', title: '新增', detail: '新内容', category: 'silent-failure' },
    { action: 'skip', id: 'L-SKIP', title: '忽略', detail: '忽略' },
  ]
  const out = applyLessonDecisions(existing, decisions)
  assert.match(out, /## L-OLD[\s\S]*title: 更新/)
  assert.match(out, /## L-NEW[\s\S]*title: 新增/)
  assert.doesNotMatch(out, /旧内容/)
  assert.doesNotMatch(out, /忽略/)
})

test('applyLessonDecisions: update_target_id 不存在 → 回退 append（不丢条目）', () => {
  const existing = `# Lessons Learned

## L-EXISTING
title: 存在
detail: 内容
status: active
`
  const decisions = [{
    action: 'update',
    update_target_id: 'L-NOT-EXIST',
    id: 'L-FALLBACK',
    title: '回退追加',
    detail: '目标不存在时回退 append',
    category: 'other',
  }]
  const out = applyLessonDecisions(existing, decisions)
  // 原有条目保留
  assert.match(out, /## L-EXISTING[\s\S]*存在/)
  // 新条目以 L-FALLBACK 追加（非 L-NOT-EXIST）
  assert.match(out, /## L-FALLBACK[\s\S]*title: 回退追加/)
})

test('applyLessonDecisions: 空决策数组 → 原文不变', () => {
  const existing = '# Lessons Learned\n'
  assert.equal(applyLessonDecisions(existing, []), existing)
})

test('applyLessonDecisions: 空现有 lessons → append 创建文件骨架', () => {
  const decisions = [{
    action: 'append',
    id: 'L-FIRST',
    title: '首条',
    detail: '首条内容',
    source: 'plan-01/T1@20260701T000000Z',
    category: 'convention',
  }]
  const out = applyLessonDecisions('', decisions)
  assert.match(out, /^# Lessons Learned/)
  assert.match(out, /## L-FIRST[\s\S]*title: 首条/)
})

test('formatLessonsForDistill: 解析现有 lessons.md 为结构化数组（含 source/category 新字段）', () => {
  const md = `# Lessons Learned

## L-20260701T103320Z
title: DB split-commit 必须单事务
detail: DrawResult + outbox 分两次 commit，第二次失败导致 outbox 永不补
source: plan-06/T6b@20260702T132012Z
category: silent-failure
status: active

## L-20260702T080000Z
title: 旧格式条目（无 source/category）
detail: 旧内容
status: active
`
  const out = formatLessonsForDistill(md)
  assert.equal(out.length, 2)
  assert.equal(out[0].id, 'L-20260701T103320Z')
  assert.equal(out[0].title, 'DB split-commit 必须单事务')
  assert.equal(out[0].source, 'plan-06/T6b@20260702T132012Z')
  assert.equal(out[0].category, 'silent-failure')
  // 旧格式兼容：source/category 缺失 → undefined
  assert.equal(out[1].id, 'L-20260702T080000Z')
  assert.equal(out[1].title, '旧格式条目（无 source/category）')
  assert.equal(out[1].source, undefined)
  assert.equal(out[1].category, undefined)
})

test('formatLessonsForDistill: 空字符串 → 空数组', () => {
  assert.deepEqual(formatLessonsForDistill(''), [])
  assert.deepEqual(formatLessonsForDistill('# Lessons Learned\n'), [])
})

test('formatLessonsForDistill: 仅 header 无条目 → 空数组', () => {
  const md = `# Lessons Learned

（暂无）`
  assert.deepEqual(formatLessonsForDistill(md), [])
})

// —— groupFindingsByFile ——

test('groupFindingsByFile groups findings by file', () => {
  const findings = [
    { source: 'spec', title: 'missing X', file: 'a.py' },
    { source: 'quality', title: 'wrong way', file: 'a.py' },
    { source: 'hunter', title: 'swallowed error', file: 'b.py' },
  ]
  const groups = groupFindingsByFile(findings)
  assert.equal(groups.length, 2)
  const a = groups.find(g => g.file === 'a.py')
  const b = groups.find(g => g.file === 'b.py')
  assert.equal(a.findings.length, 2)
  assert.equal(b.findings.length, 1)
  assert.deepEqual([...a.sources].sort(), ['quality', 'spec'])
})

test('groupFindingsByFile skips findings without file', () => {
  const findings = [
    { source: 'spec', title: 'no file finding' },
    { source: 'quality', title: 'has file', file: 'a.py' },
  ]
  const groups = groupFindingsByFile(findings)
  assert.equal(groups.length, 1)
  assert.equal(groups[0].file, 'a.py')
})

test('groupFindingsByFile empty array → empty array', () => {
  assert.deepEqual(groupFindingsByFile([]), [])
})

// —— formatCrossReviewerNote ——

test('formatCrossReviewerNote produces output when ≥2 sources flag same file', () => {
  const findings = [
    { source: 'spec', severity: 'critical', title: 'missing feature X', file: 'a.py', fix: 'add it' },
    { source: 'quality', severity: 'important', title: 'wrong approach', file: 'a.py', fix: 'use pattern Y' },
  ]
  const out = formatCrossReviewerNote(findings)
  assert.match(out, /Cross-Reviewer Overlap/)
  assert.match(out, /a\.py.*flagged by:.*quality.*spec/)
  assert.match(out, /spec\|critical/)
  assert.match(out, /quality\|important/)
})

test('formatCrossReviewerNote empty when only one source per file', () => {
  const findings = [
    { source: 'quality', title: 'issue 1', file: 'a.py' },
    { source: 'quality', title: 'issue 2', file: 'a.py' },
    { source: 'hunter', title: 'issue 3', file: 'b.py' },
  ]
  assert.equal(formatCrossReviewerNote(findings), '')
})

test('formatCrossReviewerNote empty for empty findings', () => {
  assert.equal(formatCrossReviewerNote([]), '')
})

test('formatCrossReviewerNote handles findings without severity gracefully', () => {
  const findings = [
    { source: 'spec', title: 'missing X', file: 'a.py' },
    { source: 'hunter', title: 'bad fallback', file: 'a.py' },
  ]
  const out = formatCrossReviewerNote(findings)
  assert.match(out, /spec\] missing X/)
  assert.match(out, /hunter\] bad fallback/)
})

test('formatCrossReviewerNote handles findings without fix gracefully', () => {
  const findings = [
    { source: 'spec', title: 'missing X', file: 'a.py' },
    { source: 'quality', title: 'wrong way', file: 'a.py', fix: 'use Y' },
  ]
  const out = formatCrossReviewerNote(findings)
  assert.match(out, /spec\] missing X\n/)
  assert.match(out, /quality\].*— fix: use Y/)
})

test('formatCrossReviewerNote multiple overlap groups', () => {
  const findings = [
    { source: 'spec', title: 'missing X', file: 'a.py' },
    { source: 'quality', title: 'wrong way', file: 'a.py' },
    { source: 'quality', title: 'bare except', file: 'b.py' },
    { source: 'hunter', title: 'swallowed error', file: 'b.py' },
  ]
  const out = formatCrossReviewerNote(findings)
  assert.match(out, /a\.py.*flagged by:/)
  assert.match(out, /b\.py.*flagged by:/)
  const aIdx = out.indexOf('a.py')
  const bIdx = out.indexOf('b.py')
  assert.ok(aIdx > -1 && bIdx > -1)
})

// —— formatUniversalLessons (Tier 1: silent-failure 始终注入) ——

test('formatUniversalLessons returns non-empty when silent-failure present', () => {
  const all = [
    { id: 'L1', title: 'savepoint', detail: 'use savepoint', category: 'silent-failure' },
    { id: 'L2', title: 'csv format', detail: 'use comma', category: 'test-strategy' },
  ]
  // 有 silent-failure → 非空
  assert.ok(formatUniversalLessons(all).length > 0)
})

test('formatUniversalLessons returns empty string when no silent-failure category', () => {
  const all = [
    { id: 'L2', title: 'csv format', detail: 'use comma', category: 'test-strategy' },
    { id: 'L3', title: 'no category', detail: 'legacy' },  // 无 category 字段
  ]
  assert.equal(formatUniversalLessons(all), '')
})

test('formatUniversalLessons includes only silent-failure category lessons', () => {
  const all = [
    { id: 'L1', title: 'savepoint', detail: 'use savepoint', category: 'silent-failure' },
    { id: 'L2', title: 'timezone', detail: 'naive UTC', category: 'silent-failure' },
    { id: 'L3', title: 'csv format', detail: 'use comma', category: 'test-strategy' },
  ]
  const out = formatUniversalLessons(all)
  assert.ok(out.includes('savepoint'))
  assert.ok(out.includes('timezone'))
  assert.ok(!out.includes('csv format'))
})

// —— formatDomainLessons (Tier 2: 按 task category 匹配，cap 5，同 plan 优先) ——

test('formatDomainLessons returns empty string when taskCategories empty', () => {
  const all = [{ id: 'L1', title: 'csv', detail: 'use comma', category: 'test-strategy' }]
  assert.equal(formatDomainLessons(all, []), '')
})

test('formatDomainLessons matches lessons by taskCategories', () => {
  const all = [
    { id: 'L1', title: 'csv format', detail: 'use comma', category: 'test-strategy' },
    { id: 'L2', title: 'no category', detail: 'legacy' },
    { id: 'L3', title: 'timezone', detail: 'naive UTC', category: 'silent-failure' },
  ]
  // task 声明 test-strategy → 只匹配 L1（L3 是 silent-failure 由 Tier 1 注入，不重复）
  const out = formatDomainLessons(all, ['test-strategy'])
  assert.ok(out.includes('csv format'))
  assert.ok(!out.includes('timezone'))  // silent-failure 不进 Tier 2
  assert.ok(!out.includes('no category'))  // 无 category 不匹配
})

test('formatDomainLessons excludes silent-failure (Tier 1 已注入，防重复)', () => {
  const all = [
    { id: 'L1', title: 'savepoint', detail: 'use savepoint', category: 'silent-failure' },
    { id: 'L2', title: 'csv', detail: 'use comma', category: 'test-strategy' },
  ]
  // 即使 task 声明 silent-failure，Tier 2 也不重复注入（Tier 1 已兜底始终注入）
  const out = formatDomainLessons(all, ['silent-failure', 'test-strategy'])
  assert.ok(!out.includes('savepoint'))
  assert.ok(out.includes('csv'))
})

test('formatDomainLessons caps at 5 lessons, same-plan source first', () => {
  const all = []
  for (let i = 1; i <= 8; i++) {
    all.push({ id: `L${i}`, title: `lesson ${i}`, detail: `d${i}`, category: 'test-strategy', source: i <= 3 ? 'plan-06/T1@x' : 'plan-05/T1@x' })
  }
  const out = formatDomainLessons(all, ['test-strategy'], 'plan-06')
  // cap 5 + 同 plan（plan-06）优先 → L1,L2,L3（同 plan）+ L4,L5（其他）
  const ids = (out.match(/L\d+/g) || [])
  assert.equal(ids.length, 5, `expected 5 lessons after cap, got ${ids.length}`)
  assert.ok(ids.includes('L1') && ids.includes('L2') && ids.includes('L3'), 'same-plan lessons first')
})

test('formatDomainLessons falls back to title keyword match when taskCategories absent', () => {
  // taskCategories null/undefined → 旧行为：按 title 关键词（用 task title 当唯一关键词）
  const all = [
    { id: 'L1', title: 'CSV import format', detail: 'use comma', category: 'test-strategy' },
    { id: 'L2', title: 'timezone', detail: 'naive UTC', category: 'silent-failure' },
  ]
  // 无 category 维度，用 taskTitle='CSV 批量导入' 关键词匹配 → L1（CSV）
  const out = formatDomainLessons(all, null, 'plan-06', 'CSV 批量导入')
  assert.ok(out.includes('CSV import format'))
  assert.ok(!out.includes('timezone'))  // silent-failure 不进 Tier 2
})

// —— updateFindingsHistory (状态机转换) ——

test('updateFindingsHistory returns empty array for first round', () => {
  const current = [{ source: 'spec', title: 'bug A', severity: 'important', file: 'a.py', fix: 'fix A' }]
  const h = updateFindingsHistory([], current, 1)
  assert.equal(h.length, 1)
  assert.equal(h[0].status, 'open')
  assert.equal(h[0].first_seen, 1)
  assert.equal(h[0].last_seen, 1)
  assert.deepEqual(h[0].rounds, [1])
})

test('updateFindingsHistory marks absent open finding as fixed', () => {
  const history = [{ title: 'bug A', severity: 'important', fix: 'fix A', file: 'a.py', first_seen: 1, last_seen: 1, rounds: [1], status: 'open' }]
  // round 2: bug A 不再出现 → fixed
  const h = updateFindingsHistory(history, [], 2)
  assert.equal(h[0].status, 'fixed')
  assert.equal(h[0].fixed_at_round, 2)
  assert.deepEqual(h[0].rounds, [1])  // rounds 不追加（本轮未出现）
})

test('updateFindingsHistory keeps open finding open with updated last_seen', () => {
  const history = [{ title: 'bug A', severity: 'important', fix: 'fix A', file: 'a.py', first_seen: 1, last_seen: 1, rounds: [1], status: 'open' }]
  const current = [{ source: 'spec', title: 'bug A', severity: 'important', file: 'a.py', fix: 'fix A' }]
  const h = updateFindingsHistory(history, current, 2)
  assert.equal(h[0].status, 'open')
  assert.equal(h[0].last_seen, 2)
  assert.deepEqual(h[0].rounds, [1, 2])
})

test('updateFindingsHistory marks regressed when fixed finding reappears', () => {
  const history = [{ title: 'bug A', severity: 'important', fix: 'fix A', file: 'a.py', first_seen: 1, last_seen: 1, rounds: [1], status: 'fixed', fixed_at_round: 2 }]
  const current = [{ source: 'spec', title: 'bug A', severity: 'important', file: 'a.py', fix: 'fix A' }]
  const h = updateFindingsHistory(history, current, 3)
  assert.equal(h[0].status, 'regressed')
  assert.equal(h[0].last_seen, 3)
  assert.deepEqual(h[0].rounds, [1, 3])
  assert.equal(h[0].fixed_at_round, 2)  // 保留修好的轮次（diag 用）
})

test('updateFindingsHistory is immutable (does not mutate input)', () => {
  const history = [{ title: 'bug A', severity: 'important', fix: '', file: 'a.py', first_seen: 1, last_seen: 1, rounds: [1], status: 'open' }]
  const current = [{ source: 'spec', title: 'bug A', severity: 'important', file: 'a.py', fix: 'fix' }]
  const h = updateFindingsHistory(history, current, 2)
  assert.notEqual(h, history)  // 新数组
  assert.equal(history[0].status, 'open')  // 原数组未变
  assert.deepEqual(history[0].rounds, [1])
})

test('updateFindingsHistory preserves regressed status (idempotent on re-regression)', () => {
  const history = [{ title: 'bug A', severity: 'important', fix: '', file: 'a', first_seen: 1, last_seen: 3, rounds: [1, 3], status: 'regressed', fixed_at_round: 2 }]
  const current = [{ source: 'spec', title: 'bug A', severity: 'important', file: 'a', fix: '' }]
  const h = updateFindingsHistory(history, current, 4)
  assert.equal(h[0].status, 'regressed')
  assert.equal(h[0].last_seen, 4)
  assert.deepEqual(h[0].rounds, [1, 3, 4])
})

test('updateFindingsHistory matches by title (cross-reviewer dedup)', () => {
  // round 1 quality 报 "stub URL"，round 2 spec 报同 title → 视为同 finding（不新增）
  const history = [{ title: 'stub URL', severity: 'important', fix: '', file: 'a', first_seen: 1, last_seen: 1, rounds: [1], status: 'open' }]
  const current = [{ source: 'spec', title: 'stub URL', severity: 'minor', file: 'a', fix: 'use startsWith' }]
  const h = updateFindingsHistory(history, current, 2)
  assert.equal(h.length, 1)
  assert.equal(h[0].status, 'open')
  assert.equal(h[0].last_seen, 2)
})

// —— hasRegressed ——

test('hasRegressed returns true when any finding is regressed', () => {
  const history = [{ title: 'A', status: 'open' }, { title: 'B', status: 'regressed' }]
  assert.equal(hasRegressed(history), true)
})

test('hasRegressed returns false when no regressed', () => {
  const history = [{ title: 'A', status: 'open' }, { title: 'B', status: 'fixed' }]
  assert.equal(hasRegressed(history), false)
})

test('hasRegressed returns false for empty history', () => {
  assert.equal(hasRegressed([]), false)
})

// —— formatFindingsHistory（D1 决策：history 主导，本轮新增加 ★ 标记，去重单源）——
// 签名 formatFindingsHistory(history, currentRound?)：currentRound 用于标 ★本轮新增（last_seen===currentRound）

test('formatFindingsHistory lists [OPEN] findings as must-fix', () => {
  const history = [
    { title: 'bug A', severity: 'important', fix: 'fix A', file: 'a.py', first_seen: 1, last_seen: 2, rounds: [1, 2], status: 'open' },
  ]
  const out = formatFindingsHistory(history, 2)  // round 2 调用
  assert.ok(out.includes('[OPEN]'))
  assert.ok(out.includes('bug A'))
  assert.ok(out.includes('fix A'))
})

test('formatFindingsHistory marks last_seen===currentRound with ★ (本轮新增)', () => {
  // D1: 本轮新发现标 ★ 让 implementor 分辨紧急度（DX finding low 顺势解决）
  const history = [
    { title: 'old bug', severity: 'minor', fix: '', file: 'a', first_seen: 1, last_seen: 1, rounds: [1], status: 'open' },
    { title: 'new bug', severity: 'important', fix: 'fix it', file: 'b', first_seen: 2, last_seen: 2, rounds: [2], status: 'open' },
  ]
  const out = formatFindingsHistory(history, 2)  // round 2 调用
  assert.ok(out.includes('★'))  // new bug 标 ★
  // old bug 不标 ★（last_seen=1 ≠ currentRound=2）
  const lines = out.split('\n')
  const oldLine = lines.find(l => l.includes('old bug'))
  assert.ok(oldLine, 'old bug line exists')
  assert.ok(!oldLine.includes('★'), 'old bug not marked ★')
})

test('formatFindingsHistory lists [FIXED] findings as do-not-touch', () => {
  const history = [
    { title: 'bug A', severity: 'minor', fix: 'fix A', file: 'a.py', first_seen: 1, last_seen: 1, rounds: [1], status: 'fixed', fixed_at_round: 2 },
  ]
  const out = formatFindingsHistory(history, 3)  // round 3 调用（已 fixed 在 r2）
  assert.ok(out.includes('[FIXED]'))
  assert.ok(out.includes('bug A'))
  assert.ok(out.includes('a.py'))  // 文件路径标注
  assert.ok(out.includes('r2'))  // fixed_at_round
})

test('formatFindingsHistory omits [REGRESSED] (triggers halt, not injected)', () => {
  const history = [
    { title: 'bug A', severity: 'important', fix: '', file: 'a', first_seen: 1, last_seen: 3, rounds: [1, 3], status: 'regressed', fixed_at_round: 2 },
  ]
  const out = formatFindingsHistory(history, 3)
  // regressed 不注入（触发即 halt，implementor 永远看不到）
  assert.ok(!out.includes('bug A'))
  assert.ok(!out.includes('[REGRESSED]'))
})

test('formatFindingsHistory returns empty string when no open or fixed', () => {
  const history = [{ title: 'A', status: 'regressed' }]
  assert.equal(formatFindingsHistory(history, 3), '')
})

test('formatFindingsHistory empty history returns empty string', () => {
  assert.equal(formatFindingsHistory([], 1), '')
})

test('formatFindingsHistory sorts [OPEN] by severity (critical first)', () => {
  // DX finding medium: 弱模型在长 prompt 下倾向先修容易的 minor，按 severity 排序让 critical 在前
  const history = [
    { title: 'minor bug', severity: 'minor', fix: '', file: 'a', first_seen: 1, last_seen: 1, rounds: [1], status: 'open' },
    { title: 'critical bug', severity: 'critical', fix: 'urgent', file: 'b', first_seen: 1, last_seen: 1, rounds: [1], status: 'open' },
    { title: 'important bug', severity: 'important', fix: '', file: 'c', first_seen: 1, last_seen: 1, rounds: [1], status: 'open' },
  ]
  const out = formatFindingsHistory(history, 1)
  const criticalPos = out.indexOf('critical bug')
  const importantPos = out.indexOf('important bug')
  const minorPos = out.indexOf('minor bug')
  assert.ok(criticalPos < importantPos, 'critical before important')
  assert.ok(importantPos < minorPos, 'important before minor')
})
