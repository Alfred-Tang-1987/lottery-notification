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
    assert.deepEqual(s.enum.sort(), ['failed', 'model_unavailable', 'ok'])
  }
})
test('implementor evidence requires tests_exit_code + files_changed', () => {
  const req = SCHEMAS.implementor.properties.evidence.required
  for (const f of ['tests_exit_code', 'files_changed', 'pytest_summary']) {
    assert.ok(req.includes(f), `implementor missing ${f}`)
  }
})
