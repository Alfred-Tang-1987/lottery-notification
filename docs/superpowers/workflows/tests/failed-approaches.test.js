import { test } from 'node:test'
import assert from 'node:assert/strict'
import { PROMPTS, SCHEMAS, formatFailedApproaches } from '../lib.js'

// --- formatFailedApproaches helper ---

test('formatFailedApproaches: empty array returns empty string', () => {
  assert.equal(formatFailedApproaches([]), '')
})

test('formatFailedApproaches: non-empty returns readable section', () => {
  const result = formatFailedApproaches([{ task_id: 'T1', reason: 'implementor failed after retry', error: 'timeout' }])
  assert.match(result, /Prior Failed Approaches/)
  assert.match(result, /T1/)
  assert.match(result, /implementor failed after retry/)
  assert.match(result, /timeout/)
  assert.match(result, /explicitly state the difference/)
})

// --- bootstrap prompt ---

test('PROMPTS.bootstrap includes manifest.json scan step', () => {
  assert.match(PROMPTS.bootstrap, /manifest\.json/)
  assert.match(PROMPTS.bootstrap, /failed_approaches/)
})

test('SCHEMAS.bootstrap evidence includes failed_approaches field', () => {
  const evidenceProps = SCHEMAS.bootstrap.properties.evidence.properties
  assert.ok(evidenceProps.failed_approaches, 'failed_approaches should be in evidence.properties')
  assert.equal(evidenceProps.failed_approaches.type, 'array')
})

// --- implementor prompt ---

test('PROMPTS.implementor includes {{failedApproaches}} placeholder on its own line', () => {
  assert.match(PROMPTS.implementor, /\{\{failedApproaches\}\}/)
})

// --- finalReport prompt ---

test('PROMPTS.finalReport includes failed_approach in blocked_info rendering', () => {
  assert.match(PROMPTS.finalReport, /failed_approach/)
})
