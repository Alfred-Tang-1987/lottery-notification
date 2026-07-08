import { test } from 'node:test'
import assert from 'node:assert/strict'
import { allGreen, unionFiles, issuesFromReviews, collectReviewFindings, formatFindings, formatFindingItem, isQuotaError, errStr, makeHalt, matchesPlanFilter, classifyThrown, reviewHaltReason, reviewHaltForEmptyFailed, haltLikelySource, fixModelForRound, resolveMaxRounds, detectOscillation, distillLessonInput, applyLessonDecisions, formatLessonsForDistill, validateAmendResult, validateCheckoutResult, groupFindingsByFile, formatCrossReviewerNote, bareTaskId, taskKey, dropParentTasks, extractCompletedFromSubjects, extractTaskKey, shouldEscalateOnOscillation, resolveReviewBudget, isFlipFlop, formatLessons, formatUniversalLessons, formatDomainLessons, updateFindingsHistory, formatFindingsHistory, hasRegressed, normalizeFilePath, REVIEW_SOURCES, checkImplStatus, formatBulletSection, buildPrompt, QUOTA_HALT_NOTE, STATIC_READONLY_NOTE, recordReviewRound, decideReviewOutcome, AUDIT_DIRECTIVE, AUDIT_REFACTOR_KEYWORDS } from '../lib.js'

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

test('findingsOf 缺 title 对象兜底不再 [object Object]，输出可读 JSON（用户 prompt 实测 2026-07-08）', () => {
  // specReview 返缺 title 的 issue 对象（schema 漏拦或 runtime 边界 case）
  // 旧兜底 String(it) = "[object Object]"（零信息）→ formatFindingsHistory 渲染 "-  [object Object] ★本轮新增"
  // 新兜底 JSON.stringify(it) → 至少 implementor 能读到 dimension/desc 等字段尝试修复
  const specBadObj = { status: 'failed', diagnostics: { issues: [
    { dimension: 'EXTRA', severity: 'minor', fix: 'remove unused helper' },  // 缺 title
    { dimension: 'MISSING', desc: 'spec req Y not implemented', fix: 'add Y' },  // 缺 title，有 desc
  ] } }
  const out = collectReviewFindings(specBadObj, { status: 'ok' }, { status: 'ok' })
  assert.equal(out.length, 2)
  // 不再 [object Object]
  assert.ok(!out[0].title.includes('[object Object]'), '缺 title 对象不得兜底为 [object Object]')
  assert.ok(!out[1].title.includes('[object Object]'), '缺 title 对象不得兜底为 [object Object]')
  // 须是可读 JSON（含原对象字段）
  assert.match(out[0].title, /"dimension"\s*:\s*"EXTRA"/, '兜底 JSON 须含 dimension 字段')
  assert.match(out[1].title, /"desc"\s*:\s*"spec req Y/, '兜底 JSON 须含 desc 字段')
})

test('findingsOf 字符串 issue 仍原样返回（不 JSON.stringify 字符串）', () => {
  // 既有 specBad fixture 用字符串 'MISSING: spec req X (a.py:10)'
  // 字符串须原样返回，不得 JSON.stringify 成 '"MISSING: ..."'（带引号）
  const specStr = { status: 'failed', diagnostics: { issues: ['MISSING: spec req X (a.py:10)'] } }
  const out = collectReviewFindings(specStr, { status: 'ok' }, { status: 'ok' })
  assert.equal(out[0].title, 'MISSING: spec req X (a.py:10)', '字符串 issue 须原样返回，不得 JSON.stringify')
})

test('formatFindings empty → empty string', () => {
  assert.equal(formatFindings([]), '')
})

// —— formatFindingItem（S6, 2026-07-07）：统一 finding 格式化 ——
test('S6 formatFindingItem: 有 severity + fix + file', () => {
  const f = { source: 'spec', severity: 'critical', title: 'bug', fix: 'patch', file: 'a.ts' }
  assert.equal(formatFindingItem(f), '[spec|critical] bug — fix: patch (a.ts)')
})

test('S6 formatFindingItem: 无 severity 无 file', () => {
  const f = { source: 'quality', title: 'issue' }
  assert.equal(formatFindingItem(f), '[quality] issue')
})

test('S6 formatFindingItem: withFile=false prefix', () => {
  const f = { source: 'spec', severity: 'high', title: 't', file: 'b.ts' }
  assert.equal(formatFindingItem(f, { withFile: false, prefix: '- ' }), '- [spec|high] t')
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

test('S9 makeHalt: 构造 halt 对象', () => {
  const h = makeHalt('model_unavailable', 'opus', new Error('quota'))
  assert.equal(h.halted, true)
  assert.equal(h.reason, 'model_unavailable')
  assert.equal(h.diag.model, 'opus')
  assert.equal(h.diag.error, 'quota')
})

test('S9 makeHalt: error 为 null/字符串', () => {
  assert.equal(makeHalt('x', 'm', null).diag.error, '')
  assert.equal(makeHalt('x', 'm', 'msg').diag.error, 'msg')
})

// —— checkImplStatus（S1, 2026-07-07）：implementor dispatch 后的状态检查 helper ——
test('S1 checkImplStatus: halted 透传', () => {
  const impl = { halted: true, reason: 'x', diag: {} }
  assert.equal(checkImplStatus(impl), impl)
})

test('S1 checkImplStatus: status 不在 allowed 返回 halt', () => {
  const impl = { status: 'failed', diagnostics: { e: 1 } }
  const h = checkImplStatus(impl, ['ok'], 'implementor {status}')
  assert.equal(h.halted, true)
  assert.equal(h.reason, 'implementor failed')
  assert.equal(h.diag.e, 1)
})

test('S1 checkImplStatus: status 在 allowed 返回 null', () => {
  const impl = { status: 'ok', diagnostics: {} }
  assert.equal(checkImplStatus(impl), null)
})

test('S1 checkImplStatus: 默认 allowed 含 done_with_concerns', () => {
  const impl = { status: 'done_with_concerns', diagnostics: {} }
  assert.equal(checkImplStatus(impl), null)
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

// —— shouldEscalateOnOscillation (v3: 仅判断"是否升级 opus"，halt 决策上移) ——

test('shouldEscalateOnOscillation returns true when non-opus and not yet escalated', () => {
  assert.equal(shouldEscalateOnOscillation('sonnet', false), true)
})

test('shouldEscalateOnOscillation returns false when already escalated', () => {
  // v3: 已升级 → return false（但 halt 决策已上移到 OSC 分支，不再意味 halt）
  assert.equal(shouldEscalateOnOscillation('opus', true), false)
})

test('shouldEscalateOnOscillation returns false when already opus', () => {
  assert.equal(shouldEscalateOnOscillation('opus', false), false)
})

test('shouldEscalateOnOscillation returns false when non-opus but already escalated', () => {
  // 已升过（无论当前模型）不再重复升级
  assert.equal(shouldEscalateOnOscillation('sonnet', true), false)
})

// —— resolveReviewBudget ——

test('resolveReviewBudget returns 5 default when unconfigured', () => {
  assert.equal(resolveReviewBudget({}), 5)
  assert.equal(resolveReviewBudget(undefined), 5)
  assert.equal(resolveReviewBudget({ review_budget: null }), 5)
})

test('resolveReviewBudget returns configured positive integer', () => {
  assert.equal(resolveReviewBudget({ review_budget: 10 }), 10)
  assert.equal(resolveReviewBudget({ review_budget: 6 }), 6)
})

test('resolveReviewBudget returns default 5 for non-number', () => {
  assert.equal(resolveReviewBudget({ review_budget: '8' }), 5)
  assert.equal(resolveReviewBudget({ review_budget: 'eight' }), 5)
})

test('resolveReviewBudget returns default 5 for NaN or Infinity', () => {
  assert.equal(resolveReviewBudget({ review_budget: NaN }), 5)
  assert.equal(resolveReviewBudget({ review_budget: Infinity }), 5)
})

test('resolveReviewBudget returns 5 for zero or negative (use default)', () => {
  // 0/负数无意义（budget 必须正）→ 用默认 5
  assert.equal(resolveReviewBudget({ review_budget: 0 }), 5)
  assert.equal(resolveReviewBudget({ review_budget: -1 }), 5)
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

test('LOW-4: haltLikelySource 对 head restore verification failed 返回 gate head mismatch', () => {
  // headVerifier 验证 HEAD != restored_head → halt reason 'gate head restore verification failed'
  // 此 reason 含 'gate' 子串，但语义是「验证失败」非「已恢复」——须在 gate 分支前单独归类为 gate head mismatch
  assert.equal(haltLikelySource('gate head restore verification failed'), 'gate head mismatch')
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

// —— normalizeFilePath (W1-5b) ——

test('normalizeFilePath returns input as-is for falsy', () => {
  assert.equal(normalizeFilePath(''), '')
  assert.equal(normalizeFilePath(null), null)
  assert.equal(normalizeFilePath(undefined), undefined)
})

test('normalizeFilePath strips absolute prefix to whitelisted dir', () => {
  // Windows 绝对路径 + 反斜杠 → 相对路径
  assert.equal(normalizeFilePath('C:\\Users\\alfred\\proj\\src\\app.py'), 'src/app.py')
  assert.equal(normalizeFilePath('C:/Users/alfred/proj/tests/test_app.py'), 'tests/test_app.py')
  // Unix 绝对路径
  assert.equal(normalizeFilePath('/home/alfred/proj/src/app.py'), 'src/app.py')
  assert.equal(normalizeFilePath('/home/alfred/proj/docs/workflow-design.md'), 'docs/workflow-design.md')
  // 混合分隔符
  assert.equal(normalizeFilePath('C:\\proj\\src\\sub\\file.py'), 'src/sub/file.py')
})

test('normalizeFilePath leaves relative path unchanged (already normalized)', () => {
  assert.equal(normalizeFilePath('src/app.py'), 'src/app.py')
  assert.equal(normalizeFilePath('tests/test_app.py'), 'tests/test_app.py')
  // 无白名单目录前缀 → 原样返回（防误裁剪）
  assert.equal(normalizeFilePath('scripts/run.sh'), 'scripts/run.sh')
})

test('normalizeFilePath covers extended whitelist (.claude/lib/app/internal/cmd/data/logs)', () => {
  assert.equal(normalizeFilePath('/x/.claude/workflows/run-plans.js'), '.claude/workflows/run-plans.js')
  assert.equal(normalizeFilePath('/x/lib/helper.js'), 'lib/helper.js')
  assert.equal(normalizeFilePath('/x/app/server.js'), 'app/server.js')
  assert.equal(normalizeFilePath('/x/internal/db.py'), 'internal/db.py')
  assert.equal(normalizeFilePath('/x/cmd/main.go'), 'cmd/main.go')
  assert.equal(normalizeFilePath('/x/data/seed.json'), 'data/seed.json')
  assert.equal(normalizeFilePath('/x/logs/run.log'), 'logs/run.log')
})

test('normalizeFilePath is case-insensitive on whitelist dirs', () => {
  assert.equal(normalizeFilePath('/x/SRC/app.py'), 'SRC/app.py')
  assert.equal(normalizeFilePath('/x/Tests/test.py'), 'Tests/test.py')
})

// —— Minor 修复（2026-07-07 三维复核）——

test('Q-F3/H-F5: normalizeFilePath 白名单须含 scripts/bin/tools/config 等常见目录', () => {
  // 当前白名单遗漏 scripts/bin/tools/config/public/static/templates/utils/api/server/client/web/.github
  // reviewer 返回绝对路径如 C:\proj\scripts\run.sh → 不匹配 → 原样返回带绝对前缀
  // cross-reviewer 重叠检测把 C:/.../scripts/run.sh 和 scripts/run.sh 当不同文件 → 漏报
  assert.equal(normalizeFilePath('C:\\proj\\scripts\\run.sh'), 'scripts/run.sh',
    'scripts/ 须在白名单（Q-F3/H-F5）')
  assert.equal(normalizeFilePath('/home/proj/bin/tool'), 'bin/tool',
    'bin/ 须在白名单')
  assert.equal(normalizeFilePath('/x/tools/gen.py'), 'tools/gen.py',
    'tools/ 须在白名单')
  assert.equal(normalizeFilePath('/x/config/app.yml'), 'config/app.yml',
    'config/ 须在白名单')
  assert.equal(normalizeFilePath('/x/.github/workflows/ci.yml'), '.github/workflows/ci.yml',
    '.github/ 须在白名单')
})

test('Q-F5: normalizeFilePath 多白名单嵌套路径取第一个（非贪婪）', () => {
  // /home/src/old/src/app.py → 非贪婪 .*? 取第一个 /src/ → src/old/src/app.py
  assert.equal(normalizeFilePath('/home/src/old/src/app.py'), 'src/old/src/app.py',
    '多白名单嵌套须取第一个（非贪婪）')
})

test('H-F6: normalizeFilePath 对非字符串 falsy（0/false/NaN）原样返回不强制 String 化', () => {
  // 当前 if (!p) return p 对 0/false/NaN 返回原值（truthy 检查误当 falsy）
  // 严格化后 typeof p !== 'string' → 原样返回（不强制 String(0)='0'）
  assert.equal(normalizeFilePath(0), 0, '0 原样返回（非字符串不强制 String 化）')
  assert.equal(normalizeFilePath(false), false, 'false 原样返回')
  assert.ok(Number.isNaN(normalizeFilePath(NaN)), 'NaN 原样返回')
  // 对象/数组也不强制 String 化（避免 [object Object] 污染 groupFindingsByFile）
  const obj = { x: 1 }
  assert.equal(normalizeFilePath(obj), obj, '对象原样返回（不 String 化为 [object Object]）')
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

test('formatUniversalLessons tolerates LLM category inference variants', () => {
  const variants = [
    { id: 'L1', title: 'savepoint', detail: 'use savepoint', category: 'silent-failure' },
    { id: 'L2', title: 'transaction', detail: 'single commit', category: 'silent_failure' },
    { id: 'L3', title: 'datetime', detail: 'naive UTC', category: 'Silent-Failure' },
    { id: 'L4', title: 'empty', detail: 'guard null', category: '  silent-failure  ' },
  ]
  const out = formatUniversalLessons(variants)
  assert.ok(out.includes('savepoint'))
  assert.ok(out.includes('transaction'))
  assert.ok(out.includes('datetime'))
  assert.ok(out.includes('empty'))
})

test('updateFindingsHistory allows regressed finding to return to fixed when resolved', () => {
  // r1 open, r2 fixed, r3 regressed, r4 absent → 二次修好 → fixed
  let history = updateFindingsHistory([], [{ title: 'A', severity: 'important', file: 'a', fix: '' }], 1)
  history = updateFindingsHistory(history, [], 2)
  assert.equal(history[0].status, 'fixed')
  assert.equal(history[0].fixed_at_round, 2)
  history = updateFindingsHistory(history, [{ title: 'A', severity: 'important', file: 'a', fix: '' }], 3)
  assert.equal(history[0].status, 'regressed')
  assert.equal(history[0].fixed_at_round, 2)
  history = updateFindingsHistory(history, [], 4)
  assert.equal(history[0].status, 'fixed')
  assert.equal(history[0].fixed_at_round, 4)
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

test('formatDomainLessons excludes silent-failure variants symmetric with formatUniversalLessons (Q1)', () => {
  // Q1 (2026-07-06): formatUniversalLessons 用正则容错 silent_failure/Silent-Failure 变体，
  //   formatDomainLessons 排除时也须对称容错，否则变体 lesson 会被 Tier 2 重复注入
  //   （Tier 1 已注入 + Tier 2 未排除 → 同一 lesson 在 prompt 中出现两次）。
  const all = [
    { id: 'L1', title: 'savepoint', detail: 'use savepoint', category: 'silent_failure' },      // 下划线变体
    { id: 'L2', title: 'transaction', detail: 'single commit', category: 'Silent-Failure' },    // 大写变体
    { id: 'L3', title: '  silent-failure  ', detail: 'guard', category: '  silent-failure  ' },  // 带空格变体
    { id: 'L4', title: 'csv', detail: 'use comma', category: 'test-strategy' },
  ]
  // 即使 task 声明这些变体，Tier 2 也不重复注入（Tier 1 正则已兜底）
  const out = formatDomainLessons(all, ['silent_failure', 'Silent-Failure', '  silent-failure  ', 'test-strategy'])
  assert.ok(!out.includes('savepoint'), 'silent_failure variant must be excluded (Tier 1 already injected via regex)')
  assert.ok(!out.includes('transaction'), 'Silent-Failure variant must be excluded')
  assert.ok(!out.includes('guard'), 'whitespace-padded silent-failure variant must be excluded')
  assert.ok(out.includes('csv'), 'test-strategy lesson should match')
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

// —— S10 taskKey（统一 padStart 2 位 task-key 构造，防 P0-7 位数不一致 bug 复发）——

test('S10 taskKey: 单位 seq padStart 为 01', () => {
  assert.equal(taskKey(1, 'T1'), 'plan-01/T1')
})

test('S10 taskKey: 双位 seq 不补零', () => {
  assert.equal(taskKey(10, 'T10a'), 'plan-10/T10a')
})

// —— S4 REVIEW_SOURCES 常量（消除 reviewer 三元组在 3 处硬编码，2026-07-07）——

test('S4 REVIEW_SOURCES: 3 个 reviewer 来源', () => {
  assert.ok(Array.isArray(REVIEW_SOURCES))
  assert.equal(REVIEW_SOURCES.length, 3)
  assert.deepEqual(REVIEW_SOURCES.map(s => s.name), ['spec', 'quality', 'hunter'])
  assert.deepEqual(REVIEW_SOURCES.map(s => s.key), ['issues', 'issues', 'silent_failures'])
})

// —— S5 formatBulletSection（通用 bullet section 渲染，6 个 format* 复用，2026-07-07）——
// 注：brief test 3 原期望 outro 前有空行（\n\n），但 brief 的 formatBulletSection 代码
// `if (outro) out += \`\n${outro}\`` 产生单 \n（与 6 个原 format* 的 bullet↔outro 间距一致）。
// 代码与原行为一致是行为保持的硬约束，故 test 3 期望改为单 \n（无空行）匹配代码实际输出。

test('S5 formatBulletSection: 空数组返回空串', () => {
  assert.equal(formatBulletSection('H', '', [], () => ''), '')
})

test('S5 formatBulletSection: 基本渲染', () => {
  const out = formatBulletSection('Heading', '', ['a', 'b'], x => `- ${x}`)
  assert.equal(out, '## Heading\n- a\n- b')
})

test('S5 formatBulletSection: 含 intro + outro（多行）', () => {
  const out = formatBulletSection('H', 'intro line', ['x'], x => `- ${x}`, 'outro line 1\noutro line 2')
  assert.equal(out, '## H\nintro line\n- x\noutro line 1\noutro line 2')
})

// —— S7 QUOTA_HALT_NOTE 常量 + buildPrompt 默认注入（5 prompt 去重，2026-07-07）——
// 5 个 prompt（specReview/qualityReviewer/hunter/commit/gate）的限额说明文本完全相同，
// 抽 QUOTA_HALT_NOTE 常量统一真源，PROMPTS 模板用 {{quotaHaltNote}} 占位符，buildPrompt
// 默认注入常量（opt-out via empty string）。implementor/lessonDistiller 是变体，不替换。

test('S7 QUOTA_HALT_NOTE: 常量导出 + 内容', () => {
  assert.equal(typeof QUOTA_HALT_NOTE, 'string')
  assert.ok(QUOTA_HALT_NOTE.includes('model_unavailable'), '常量须含 model_unavailable')
  assert.ok(QUOTA_HALT_NOTE.includes('quota'), '常量须含 quota')
})

test('S7 QUOTA_HALT_NOTE: buildPrompt 默认注入限额说明（specReview target）', () => {
  // specReview 是 5 个替换 prompt 之一；GREEN 后其 PROMPTS 模板含 {{quotaHaltNote}} 占位，
  // buildPrompt 默认注入 QUOTA_HALT_NOTE。GREEN 前是硬编码内联文本（与常量字面相同），
  // 故此断言对常量子串 `（非 failed），让 orchestrator` 须 GREEN。RED：占位符未替换前 buildPrompt
  // 输出含字面常量文本（也通过）→ 改测占位符机制：GREEN 后传 quotaHaltNote:'MARKER_X' 应见 MARKER_X。
  const out = buildPrompt('specReview', { quotaHaltNote: 'MARKER_QUOTA_TEST' })
  assert.ok(out.includes('MARKER_QUOTA_TEST'), 'specReview 须有 {{quotaHaltNote}} 占位 + buildPrompt 注入')
})

test('S7 QUOTA_HALT_NOTE: 调用方可 opt-out（传空串）', () => {
  const out = buildPrompt('specReview', { quotaHaltNote: '' })
  assert.ok(!out.includes('MARKER_QUOTA_TEST'), '传空串应关闭默认注入')
  assert.ok(!out.includes(QUOTA_HALT_NOTE), 'opt-out 后常量文本不应出现')
})

// —— S8 STATIC_READONLY_NOTE 函数 + buildPrompt 注入（2 reviewer 去重，2026-07-07）——
// specReview/qualityReviewer 的 STATIC READ-ONLY 纪律段唯一差异是 reviewType
// （'spec verification' / 'quality review'），抽 STATIC_READONLY_NOTE(reviewType) 函数复用。
// hunter 文本不同（git status/git diff 顺序 + silent-failure hunting 措辞），不替换。
// 三轮复核修正：原决策以为是 3 reviewer 共享常量；核查仅 specReview/qualityReviewer 近似。

test('S8 STATIC_READONLY_NOTE: reviewType 插值', () => {
  assert.equal(
    STATIC_READONLY_NOTE('spec verification'),
    `This is a STATIC READ-ONLY review. You may use 'git diff', 'git status', 'find', 'grep'/'rg', and read files to locate and inspect changes. Do NOT run the test suite, ruff, lint, or any build — spec verification is done by reading code, not by running it. Running tests/builds is the implementor's and gate's job, not yours.`
  )
  assert.ok(STATIC_READONLY_NOTE('quality review').includes('quality review is done by reading code'))
})

test('S8 STATIC_READONLY_NOTE: buildPrompt 注入（specReview target）', () => {
  const out = buildPrompt('specReview', { staticReadonlyNote: STATIC_READONLY_NOTE('spec verification') })
  assert.ok(out.includes('STATIC READ-ONLY'), 'specReview prompt 应含 STATIC READ-ONLY 段')
  assert.ok(out.includes('spec verification'), '应含 reviewType 插值')
})

// —— S2 recordReviewRound（review 循环每轮 state 更新抽取, 2026-07-07）——
// run-plans.js review loop 内 12 行 state 更新（review_rounds/files_touched_per_round/review_history/
// findings_history）抽成纯决策函数，lib.js 真源 + run-plans.js inline 副本。
test('S2 recordReviewRound: state 正确更新', () => {
  const state = { perTask: { 'plan-01/T1': { files_touched_per_round: [], review_history: [], findings_history: [] } } }
  const spec = { status: 'ok', diagnostics: { files_touched: ['a.ts'], issues: [] } }
  recordReviewRound(state, 'plan-01/T1', 1, spec, null, null)
  assert.equal(state.perTask['plan-01/T1'].review_rounds, 1)
  assert.equal(state.perTask['plan-01/T1'].files_touched_per_round.length, 1)
  assert.equal(state.perTask['plan-01/T1'].review_history.length, 1)
})

test('S2 recordReviewRound: findings_history 通过 updateFindingsHistory 更新', () => {
  const state = { perTask: { 'plan-01/T1': { files_touched_per_round: [], review_history: [], findings_history: [] } } }
  const spec = { status: 'failed', diagnostics: { files_touched: [], issues: [{ title: 'bug', severity: 'critical' }] } }
  recordReviewRound(state, 'plan-01/T1', 1, spec, null, null)
  assert.ok(state.perTask['plan-01/T1'].findings_history.length > 0, 'findings_history 应被更新')
})

test('S2 recordReviewRound: 返回 currentFindings', () => {
  const state = { perTask: { 'plan-01/T1': { files_touched_per_round: [], review_history: [], findings_history: [] } } }
  const spec = { status: 'failed', diagnostics: { files_touched: [], issues: [{ title: 'bug' }] } }
  const result = recordReviewRound(state, 'plan-01/T1', 1, spec, null, null)
  assert.ok(Array.isArray(result.currentFindings))
  assert.equal(result.currentFindings.length, 1)
})

// —— S2 decideReviewOutcome（review 循环 10-action 决策抽取, 2026-07-07）——
// run-plans.js review loop 内 ~70 行决策块（6 halt 子类 + break/escalate/continue/fix）抽成纯决策函数。
// lib.js 真源 + run-plans.js inline 副本。控制流修正：osc.oscillating 的 escalate/continue 不早 return，
// 须 fall through 到 budget guard（无限模式兜底，防已升 opus + 新 findings 不收敛无限跑）。
//
// mkState: 构造 review loop 用的 perTask state slice。参数覆盖 10 个分支所需的 history 状态。
//   - filesTouched: files_touched_per_round（detectOscillation 输入）
//   - reviewHistory: review_history（isFlipFlop 输入）
//   - findingsHistory: findings_history（hasRegressed 输入）
//   - opusEscalated: opus_escalated（shouldEscalateOnOscillation 输入）
function mkState({ filesTouched = [], reviewHistory = [], findingsHistory = [], opusEscalated = false } = {}) {
  return { perTask: { 'plan-01/T1': { files_touched_per_round: filesTouched, review_history: reviewHistory, findings_history: findingsHistory, opus_escalated: opusEscalated } } }
}

// 失败 review（触发非 break/非 halt-reviewReason 路径）：status='failed' + 有 findings
const failedSpec = { status: 'failed', diagnostics: { files_touched: ['a.ts'], issues: [{ title: 'bug', severity: 'critical' }] } }

// 6 halt 子类
test('S2 decideReviewOutcome: reviewReason → halt (reason=reviewReason)', () => {
  const state = mkState()
  const out = decideReviewOutcome(state, 'plan-01/T1', 1, { status: 'failed' }, null, null, 'sonnet', 4, {}, 'review_failed', null)
  assert.equal(out.action, 'halt')
  assert.equal(out.reason, 'review_failed')
  assert.deepEqual(out.diag, { spec: undefined, qual: undefined, hunt: undefined })
})

test('S2 decideReviewOutcome: emptyFailedReason → halt (reason=emptyFailedReason)', () => {
  const state = mkState()
  const out = decideReviewOutcome(state, 'plan-01/T1', 1, failedSpec, null, null, 'sonnet', 4, {}, null, 'review_failed_no_findings')
  assert.equal(out.action, 'halt')
  assert.equal(out.reason, 'review_failed_no_findings')
})

test('S2 decideReviewOutcome: regressed → halt OSCILLATING (hasRegressed=true)', () => {
  // findings_history 含 status='regressed' 条目 → hasRegressed=true → 立即 halt（独立于文件振荡）
  const state = mkState({
    filesTouched: [['a.ts'], ['a.ts'], ['a.ts']],
    findingsHistory: [{ title: 'bug', status: 'regressed', first_seen: 1, last_seen: 3, rounds: [1, 2, 3] }],
  })
  const out = decideReviewOutcome(state, 'plan-01/T1', 3, failedSpec, null, null, 'sonnet', 4, {}, null, null)
  assert.equal(out.action, 'halt')
  assert.equal(out.reason, 'OSCILLATING')
  assert.deepEqual(out.diag.regressedFindings, [{ title: 'bug', status: 'regressed', first_seen: 1, last_seen: 3, rounds: [1, 2, 3] }])
  assert.equal(out.diag.model, 'sonnet')
})

test('S2 decideReviewOutcome: osc + flipFlop → halt OSCILLATING', () => {
  // osc.oscillating=true（同文件 a.ts 在 3 round）+ flipFlop=true（last 轮 finding title 在前轮出现过）
  const state = mkState({
    filesTouched: [['a.ts'], ['a.ts'], ['a.ts']],
    reviewHistory: [
      { round: 1, spec: { status: 'failed', findings: [{ title: 'same-bug' }] } },
      { round: 2, spec: { status: 'failed', findings: [{ title: 'other' }] } },
      { round: 3, spec: { status: 'failed', findings: [{ title: 'same-bug' }] } },
    ],
  })
  const out = decideReviewOutcome(state, 'plan-01/T1', 3, failedSpec, null, null, 'sonnet', 4, {}, null, null)
  assert.equal(out.action, 'halt')
  assert.equal(out.reason, 'OSCILLATING')
  assert.equal(out.diag.flipFlop, true)
  assert.equal(out.diag.model, 'sonnet')
})

test('S2 decideReviewOutcome: maxRounds=0 budget guard → halt review_not_converging', () => {
  // 无限模式 maxRounds=0 + round>=budget(默认 5) → halt review_not_converging
  // review.diagnostics 可选（schema 非强制），budget/maxRounds halt 分支用 ?. 兜底（与 reviewReason/emptyFailed 一致）
  const state = mkState({ filesTouched: [['a.ts']] })
  const out = decideReviewOutcome(state, 'plan-01/T1', 5, failedSpec, failedSpec, failedSpec, 'opus', 0, {}, null, null)
  assert.equal(out.action, 'halt')
  assert.equal(out.reason, 'review_not_converging')
  assert.equal(out.diag.round, 5)
  assert.equal(out.diag.budget, 5)
})

test('S2 decideReviewOutcome: round===maxRounds (finite) → halt review max rounds', () => {
  // 有限模式 round===maxRounds → halt review max rounds
  // review.diagnostics 可选（schema 非强制），budget/maxRounds halt 分支用 ?. 兜底（与 reviewReason/emptyFailed 一致）
  const state = mkState({ filesTouched: [['a.ts']] })
  const out = decideReviewOutcome(state, 'plan-01/T1', 4, failedSpec, failedSpec, failedSpec, 'sonnet', 4, {}, null, null)
  assert.equal(out.action, 'halt')
  assert.equal(out.reason, 'review max rounds')
  assert.equal(out.diag.round, 4)
})

test('P1-5: decideReviewOutcome budget halt 对 null/无 diagnostics 的 review 不 TypeError（防御性 ?.）', () => {
  // review agent 偶尔未运行 → spec/qual/hunt 可能为 null；SCHEMAS 中 diagnostics 非强制（LLM 偶省略）。
  // budget/maxRounds halt 须用 ?.（与 reviewReason/emptyFailed 分支 spec?.diagnostics 一致），
  // 防 TypeError 被顶层 catch 吞为 agent_error，丢失真实 review_not_converging/review max rounds reason。
  const state = mkState({ filesTouched: [['a.ts']] })
  const reviewNoDiag = { status: 'failed' }  // 无 diagnostics 字段
  // spec 传真实对象（allGreen=false → 落入 budget guard），qual/hunt 传 null（模拟 review agent 未运行）
  const out = decideReviewOutcome(state, 'plan-01/T1', 5, reviewNoDiag, null, null,
    'sonnet', 0, {}, null, null)  // maxRounds=0, round>=budget(5)
  assert.equal(out.action, 'halt')
  assert.equal(out.reason, 'review_not_converging')
  assert.equal(out.diag.spec, undefined)  // reviewNoDiag.diagnostics === undefined（无该字段）
  assert.equal(out.diag.qual, undefined)  // null?.diagnostics === undefined（?. 兜底，非 TypeError）
  assert.equal(out.diag.hunt, undefined)
})

// 4 非 halt
test('S2 decideReviewOutcome: allGreen → break', () => {
  // spec/qual/hunt 全 ok → break（须在 detectOscillation 之前，防收敛误报）
  const state = mkState({ filesTouched: [['a.ts'], ['a.ts'], ['a.ts']] })
  const okReview = { status: 'ok', diagnostics: { files_touched: ['a.ts'] } }
  const out = decideReviewOutcome(state, 'plan-01/T1', 3, okReview, okReview, okReview, 'sonnet', 4, {}, null, null)
  assert.equal(out.action, 'break')
})

test('S2 decideReviewOutcome: osc + flipFlop=false + shouldEscalate → escalate (model=opus)', () => {
  // osc.oscillating=true + flipFlop=false + 非 opus + 未升级 → escalate
  // round<maxRounds 防 budget guard 触发（fall-through 到 budget guard 不 halt 才返回 escalate）
  const state = mkState({
    filesTouched: [['a.ts'], ['a.ts'], ['a.ts']],
    reviewHistory: [
      { round: 1, spec: { status: 'failed', findings: [{ title: 'X' }] } },
      { round: 2, spec: { status: 'failed', findings: [{ title: 'Y' }] } },
      { round: 3, spec: { status: 'failed', findings: [{ title: 'Z' }] } },
    ],
    opusEscalated: false,
  })
  const out = decideReviewOutcome(state, 'plan-01/T1', 3, failedSpec, null, null, 'sonnet', 4, {}, null, null)
  assert.equal(out.action, 'escalate')
  assert.equal(out.model, 'opus')
})

test('S2 decideReviewOutcome: osc + flipFlop=false + alreadyEscalated → continue', () => {
  // osc.oscillating=true + flipFlop=false + 已升 opus → continue（budget guard 不触发：round<maxRounds）
  const state = mkState({
    filesTouched: [['a.ts'], ['a.ts'], ['a.ts']],
    reviewHistory: [
      { round: 1, spec: { status: 'failed', findings: [{ title: 'X' }] } },
      { round: 2, spec: { status: 'failed', findings: [{ title: 'Y' }] } },
      { round: 3, spec: { status: 'failed', findings: [{ title: 'Z' }] } },
    ],
    opusEscalated: true,
  })
  const out = decideReviewOutcome(state, 'plan-01/T1', 3, failedSpec, null, null, 'opus', 4, {}, null, null)
  assert.equal(out.action, 'continue')
  // continue 不带 model 字段（调用方不重设 model）
  assert.equal(out.model, undefined)
})

test('S2 decideReviewOutcome: else (正常未收敛, 无 osc) → fix', () => {
  // 非 allGreen + 无 osc（files_touched_per_round 不足以触发振荡）+ round<maxRounds → fix
  const state = mkState({ filesTouched: [['a.ts']] })
  const out = decideReviewOutcome(state, 'plan-01/T1', 1, failedSpec, null, null, 'sonnet', 4, {}, null, null)
  assert.equal(out.action, 'fix')
  assert.equal(out.model, undefined)
})

test('S2 decideReviewOutcome: osc + alreadyEscalated + 无限模式 round>=budget → halt review_not_converging (fall-through 关键路径)', () => {
  // 锁定 Task 13 控制流修正的关键不变式：osc.oscillating 块内 escalate/continue
  // 不早 return，须 fall through 到 budget guard。否则无限模式下持续振荡（已升 opus
  // + 新 findings 不收敛）会无限跑（resolveReviewBudget 注释：升 opus 后跑直到 budget 耗尽）。
  // 此用例：osc=true + flipFlop=false + alreadyEscalated(opus) + maxRounds=0(无限) + round>=budget(5)
  // 期望：halt review_not_converging（非 continue）。若有人「简化」回早 return，此测试 FAIL。
  const state = mkState({
    filesTouched: [['a.ts'], ['a.ts'], ['a.ts']],
    reviewHistory: [
      { round: 1, spec: { status: 'failed', findings: [{ title: 'X' }] } },
      { round: 2, spec: { status: 'failed', findings: [{ title: 'Y' }] } },
      { round: 3, spec: { status: 'failed', findings: [{ title: 'Z' }] } },
    ],
    opusEscalated: true,
  })
  const out = decideReviewOutcome(state, 'plan-01/T1', 5, failedSpec, failedSpec, failedSpec, 'opus', 0, {}, null, null)
  assert.equal(out.action, 'halt', 'fall-through 到 budget guard 须 halt，非 continue')
  assert.equal(out.reason, 'review_not_converging')
  assert.equal(out.diag.budget, 5)
  assert.equal(out.diag.round, 5)
})

// ===== Task 1 (2026-07-08): AUDIT_DIRECTIVE + AUDIT_REFACTOR_KEYWORDS 常量 + haltLikelySource 注释 =====

test('AUDIT_DIRECTIVE: 常量导出 + 内容关键词', () => {
  assert.equal(typeof AUDIT_DIRECTIVE, 'string')
  assert.ok(AUDIT_DIRECTIVE.length > 100, '须是完整指令非空串')
  for (const k of ['A1', 'A2', 'A3', 'A4', 'A5']) assert.ok(AUDIT_DIRECTIVE.includes(k), `须含 ${k}`)
  assert.ok(AUDIT_DIRECTIVE.includes('needs_audit_fix'), '须含 needs_audit_fix 状态')
  assert.ok(AUDIT_DIRECTIVE.includes('.audit/'), '须指示写 .audit/ 报告')
  // 工具约束（D17）
  assert.ok(AUDIT_DIRECTIVE.includes('Grep'), '须指定 Grep 工具')
  assert.ok(AUDIT_DIRECTIVE.includes('Read'), '须指定 Read 工具')
  // 工具/写入失败也阻断（D11）
  assert.ok(AUDIT_DIRECTIVE.includes('工具') && AUDIT_DIRECTIVE.includes('失败'), '须含工具失败分级')
})

test('AUDIT_REFACTOR_KEYWORDS: 命中 refactor 词 + 不命中 feature 词', () => {
  assert.ok(AUDIT_REFACTOR_KEYWORDS.test('替换 4 处重复'), '命中 替换')
  assert.ok(AUDIT_REFACTOR_KEYWORDS.test('refactor the helper'), '命中 refactor')
  assert.ok(AUDIT_REFACTOR_KEYWORDS.test('extract pure function'), '命中 extract')
  assert.ok(!AUDIT_REFACTOR_KEYWORDS.test('add new login feature'), '不命中 feature 词')
})

test('AUDIT_DIRECTIVE: haltLikelySource audit fix needed → unknown', () => {
  assert.equal(haltLikelySource('audit fix needed'), 'unknown')
})

// ===== Task 3 (2026-07-08): implementor PROMPTS {{auditDirective}} 占位 + buildPrompt defaults 空串 =====
// PROMPTS.implementor 加 {{auditDirective}} 占位（retryNote 后、## Discipline 前）；
// buildPrompt defaults 加 auditDirective: ''（非 refactor task opt-out，零影响）。
// 调用方传 AUDIT_DIRECTIVE 常量启用（refactor 类 task）。不测 `{}` 占位符保留——与 defaults 冲突，
// 见 spec §6.1 修正说明：auditDirective 进 defaults 后永远被替换（空串或常量），无第三态。

test('AUDIT: buildPrompt implementor 传 auditDirective 注入', () => {
  const out = buildPrompt('implementor', { taskId: 'T1', planId: '01', retryNote: '', fixIssues: '', auditDirective: 'MARKER_AUDIT_TEST' })
  assert.ok(out.includes('MARKER_AUDIT_TEST'), '传 auditDirective 应注入占位')
})

test('AUDIT: buildPrompt implementor auditDirective 默认空串（非 refactor 无残留）', () => {
  // auditDirective 进 defaults 为空串 → {auditDirective:''} 或 {} 都渲染为空串，无 {{auditDirective}} 残留
  const out = buildPrompt('implementor', { taskId: 'T1', planId: '01', retryNote: '', fixIssues: '' })
  assert.ok(!out.includes('Pre-RED Audit'), '默认不应含 AUDIT 指令')
  assert.ok(!out.includes('{{auditDirective}}'), '默认无占位符残留（prompt 清洁）')
})
