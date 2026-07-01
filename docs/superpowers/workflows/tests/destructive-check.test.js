import { test } from 'node:test'
import assert from 'node:assert/strict'
import { PROMPTS, SCHEMAS } from '../lib.js'

test('PROMPTS.commit includes destructive change detection step', () => {
  assert.match(PROMPTS.commit, /destructive/i)
  assert.match(PROMPTS.commit, /deleted_code/i)
  assert.match(PROMPTS.commit, /signature_change/i)
  assert.match(PROMPTS.commit, /file_deletion/i)
})

test('PROMPTS.commit uses git diff --cached --numstat for deterministic detection', () => {
  assert.match(PROMPTS.commit, /git diff --cached --numstat/)
})

test('SCHEMAS.commit diagnostics includes destructive_changes field', () => {
  const diagProps = SCHEMAS.commit.properties.diagnostics.properties
  assert.ok(diagProps.destructive_changes, 'destructive_changes should be in diagnostics.properties')
  assert.equal(diagProps.destructive_changes.type, 'array')
})
