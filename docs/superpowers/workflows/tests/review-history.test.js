import { test } from 'node:test'
import assert from 'node:assert/strict'
import { summarizeReviewRound } from '../lib.js'

// review_history 存档：每轮 review 的 findings 摘要进 manifest.per_task.review_history，
// 让 OSCILLATING / 收敛 halt 后能精确定位振荡点（哪个 reviewer 在哪点 flip-flop），
// 无需像 T3 那次考古（review_rounds 只存 int 计数，findings 不持久化）。
// 摘要只留 title + severity（诊断定位所需），丢 fix/file/source（implementor 反馈才用，控 manifest 体积）。

const specBad = {
  status: 'failed',
  diagnostics: { issues: [{ severity: 'Critical', title: 'sql injection', file: 'a.py', fix: 'parameterize' }, 'plain string finding'] },
}
const qualOk = { status: 'ok', diagnostics: {} }
const huntBad = {
  status: 'failed',
  diagnostics: { silent_failures: [{ title: 'swallowed err', severity: 'high', fix: 'propagate' }] },
}

test('summarizeReviewRound: round 透传 + 每 reviewer status 保留', () => {
  const r = summarizeReviewRound(2, specBad, qualOk, huntBad)
  assert.equal(r.round, 2)
  assert.equal(r.spec.status, 'failed')
  assert.equal(r.quality.status, 'ok')
  assert.equal(r.hunter.status, 'failed')
})

test('summarizeReviewRound: findings 只留 title+severity，丢 fix/file/source（控 manifest 体积）', () => {
  const r = summarizeReviewRound(1, specBad, qualOk, huntBad)
  // spec: object item → {title,severity}；string item → {title, severity:undefined}
  assert.deepEqual(r.spec.findings, [
    { title: 'sql injection', severity: 'Critical' },
    { title: 'plain string finding', severity: undefined },
  ])
  // 非 failed → findings 空（复用 findingsOf：只 failed 才收集）
  assert.deepEqual(r.quality.findings, [])
  // hunter 读 silent_failures（不同 key）
  assert.deepEqual(r.hunter.findings, [{ title: 'swallowed err', severity: 'high' }])
  // 关键：摘要不得泄露 fix/file/source（这些是 implementor 反馈字段，manifest 摘要不需要）
  assert.equal(JSON.stringify(r).match(/\b(fix|file|source)\b/g), null,
    'review_history 摘要不得含 fix/file/source —— 控体积且这些字段属 implementor 反馈管道')
})

test('summarizeReviewRound: 缺失 reviewer（null/undefined）→ status undefined + 空 findings，不抛', () => {
  // review agent 偶发 model_unavailable → safeAgent 返回 null；摘要须容忍不抛
  const r = summarizeReviewRound(1, null, undefined, { status: 'ok', diagnostics: {} })
  assert.equal(r.spec.status, undefined)
  assert.deepEqual(r.spec.findings, [])
  assert.equal(r.quality.status, undefined)
  assert.deepEqual(r.quality.findings, [])
  assert.equal(r.hunter.status, 'ok')
})
