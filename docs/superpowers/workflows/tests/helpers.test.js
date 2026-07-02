import { test } from 'node:test'
import assert from 'node:assert/strict'
import { allGreen, unionFiles, issuesFromReviews, collectReviewFindings, formatFindings, isQuotaError, errStr, matchesPlanFilter, classifyThrown, reviewHaltReason, reviewHaltForEmptyFailed, haltLikelySource, fixModelForRound, resolveMaxRounds, detectOscillation } from '../lib.js'

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
