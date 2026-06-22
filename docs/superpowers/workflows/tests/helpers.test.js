import { test } from 'node:test'
import assert from 'node:assert/strict'
import { allGreen, unionFiles, issuesFromReviews, isQuotaError, errStr } from '../lib.js'

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

test('isQuotaError detects quota/rate-limit/429 keywords', () => {
  assert.equal(isQuotaError(new Error('rate limit exceeded')), true)
  assert.equal(isQuotaError(new Error('quota exhausted for this model')), true)
  assert.equal(isQuotaError(new Error('429 too many requests')), true)
  assert.equal(isQuotaError(new Error('insufficient balance')), true)
  assert.equal(isQuotaError(new Error('model overloaded')), true)
  assert.equal(isQuotaError(new Error('syntax error')), false)
  assert.equal(isQuotaError(new Error('file not found')), false)
})

test('errStr extracts message safely', () => {
  assert.equal(errStr(new Error('hello')), 'hello')
  assert.equal(errStr('plain string'), 'plain string')
  assert.equal(errStr(null), '')
  assert.equal(errStr(undefined), '')
  assert.equal(errStr({}), '[object Object]')
  assert.equal(errStr(new Error('x'.repeat(300))).length, 200)
})
