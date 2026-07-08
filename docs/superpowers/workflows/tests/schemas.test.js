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

// quality/hunter/specReview 的 issues/silent_failures 元素强制对象 {title, fix}。
// 防 LLM 返回纯字符串/缺 fix/用错字段名 → collectReviewFindings 兜底为 [object Object]。
// specReview（S7, 2026-07-08）从 reviewSchema() 迁出至 specReviewSchema()，加 dimension 字段。
// qualityReviewer/hunter severity 加 required（S7, 2026-07-08）：防 LLM 省略 severity → formatFindingsHistory 排序失效。
test('qualityReviewer issues items require title + fix + severity', () => {
  const items = SCHEMAS.qualityReviewer.properties.diagnostics.properties.issues.items
  assert.equal(items.type, 'object')
  assert.deepEqual(items.required.sort(), ['fix', 'severity', 'title'])
  assert.ok(items.properties.title, 'qualityReviewer issues item needs title prop')
  assert.ok(items.properties.fix, 'qualityReviewer issues item needs fix prop')
})

test('hunter silent_failures items require title + fix + severity', () => {
  const items = SCHEMAS.hunter.properties.diagnostics.properties.silent_failures.items
  assert.equal(items.type, 'object')
  assert.deepEqual(items.required.sort(), ['fix', 'severity', 'title'])
  assert.ok(items.properties.title, 'hunter silent_failures item needs title prop')
  assert.ok(items.properties.fix, 'hunter silent_failures item needs fix prop')
})

test('specReview issues items require title + fix + dimension（S7, 2026-07-08 拆出独立 schema）', () => {
  const items = SCHEMAS.specReview.properties.diagnostics.properties.issues.items
  assert.equal(items.type, 'object')
  assert.deepEqual(items.required.sort(), ['fix', 'title'])
  assert.ok(items.properties.title, 'specReview issues item needs title prop')
  assert.ok(items.properties.fix, 'specReview issues item needs fix prop')
  assert.ok(items.properties.dimension, 'specReview issues item needs dimension prop')
  assert.deepEqual(items.properties.dimension.enum, ['MISSING', 'EXTRA', 'MISUNDERSTANDING'])
})
test('implementor evidence requires tests_exit_code + files_changed', () => {
  const req = SCHEMAS.implementor.properties.evidence.required
  for (const f of ['tests_exit_code', 'files_changed', 'pytest_summary']) {
    assert.ok(req.includes(f), `implementor missing ${f}`)
  }
})
