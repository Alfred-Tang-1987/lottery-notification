import { test } from 'node:test'
import assert from 'node:assert/strict'
import { SCHEMAS } from '../lib.js'

const ROLES = ['bootstrap', 'implementor', 'specReview', 'qualityReviewer',
  'hunter', 'simplify', 'commit', 'contextFetcher', 'gate', 'finalReport']

test('every role has a schema', () => {
  for (const r of ROLES) assert.ok(SCHEMAS[r], `missing schema: ${r}`)
})
test('evidence-bearing roles require evidence field', () => {
  for (const r of ['bootstrap', 'implementor', 'commit', 'gate']) {
    assert.ok(SCHEMAS[r].properties.evidence, `${r} needs evidence prop`)
  }
})
test('review schemas require status enum', () => {
  for (const r of ['specReview', 'qualityReviewer', 'hunter']) {
    const s = SCHEMAS[r].properties.status
    // 故意不含 'agent_error'：它是 orchestrator-internal sentinel（safeAgent catch 构造，
    // 绕过 schema 校验），orchestrator 用 reviewHaltReason() 显式判断。入 enum 反而放宽约束。
    assert.deepEqual(s.enum.sort(), ['failed', 'model_unavailable', 'ok'])
  }
})

// quality/hunter 的 issues/silent_failures 元素强制对象 {title, fix}（specReview 保持字符串模板故走 reviewSchema）。
// 防 LLM 返回纯字符串/缺 fix/用错字段名 → collectReviewFindings 兜底为 [object Object]。
test('qualityReviewer issues items require title + fix', () => {
  const items = SCHEMAS.qualityReviewer.properties.diagnostics.properties.issues.items
  assert.equal(items.type, 'object')
  assert.deepEqual(items.required.sort(), ['fix', 'title'])
  assert.ok(items.properties.title, 'qualityReviewer issues item needs title prop')
  assert.ok(items.properties.fix, 'qualityReviewer issues item needs fix prop')
})

test('hunter silent_failures items require title + fix', () => {
  const items = SCHEMAS.hunter.properties.diagnostics.properties.silent_failures.items
  assert.equal(items.type, 'object')
  assert.deepEqual(items.required.sort(), ['fix', 'title'])
  assert.ok(items.properties.title, 'hunter silent_failures item needs title prop')
  assert.ok(items.properties.fix, 'hunter silent_failures item needs fix prop')
})

test('specReview issues 保持无 items 约束（字符串模板，不强制对象）', () => {
  const issues = SCHEMAS.specReview.properties.diagnostics.properties.issues
  assert.equal(issues.items, undefined, 'specReview issues 是自由字符串模板，不应强制对象结构')
})
test('implementor evidence requires tests_exit_code + files_changed', () => {
  const req = SCHEMAS.implementor.properties.evidence.required
  for (const f of ['tests_exit_code', 'files_changed', 'pytest_summary']) {
    assert.ok(req.includes(f), `implementor missing ${f}`)
  }
})
