import { test } from 'node:test'
import assert from 'node:assert/strict'
import { detectOscillation } from '../lib.js'

test('fewer than 3 rounds → not oscillating', () => {
  assert.equal(detectOscillation([['f1'], ['f1']]).oscillating, false)
  assert.equal(detectOscillation([]).oscillating, false)
})

test('same file in >=3 rounds → oscillating', () => {
  const r = detectOscillation([['f1', 'f2'], ['f2', 'f3'], ['f1', 'f3'], ['f1', 'f4']])
  assert.equal(r.oscillating, true)
  assert.match(r.reason, /f[1234]/)
})

test('consecutive rounds fix same files completely → oscillating', () => {
  const r = detectOscillation([['a', 'b'], ['a', 'b'], ['x']])
  assert.equal(r.oscillating, true)
})

test('healthy progression → not oscillating', () => {
  assert.equal(detectOscillation([['f1'], ['f2'], ['f3']]).oscillating, false)
})
