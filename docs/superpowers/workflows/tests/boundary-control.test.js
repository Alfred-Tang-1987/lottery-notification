import { test } from 'node:test'
import assert from 'node:assert/strict'
import { PROMPTS, SCHEMAS, formatWriteFilesScope } from '../lib.js'

// --- formatWriteFilesScope helper ---

test('formatWriteFilesScope: empty array returns empty string', () => {
  assert.equal(formatWriteFilesScope([]), '')
})

test('formatWriteFilesScope: non-empty returns readable section with boundary check', () => {
  const result = formatWriteFilesScope(['src/a.py', 'src/b.py'])
  assert.match(result, /Write Files Boundary/)
  assert.match(result, /src\/a\.py/)
  assert.match(result, /src\/b\.py/)
  assert.match(result, /git diff.*name-only/)
  assert.match(result, /out_of_scope/)
})

// --- bootstrap prompt ---

test('PROMPTS.bootstrap mentions write_files frontmatter', () => {
  assert.match(PROMPTS.bootstrap, /write_files/)
  assert.match(PROMPTS.bootstrap, /task_write_files/)
})

test('SCHEMAS.bootstrap evidence includes task_write_files field', () => {
  const evidenceProps = SCHEMAS.bootstrap.properties.evidence.properties
  assert.ok(evidenceProps.task_write_files, 'task_write_files should be in evidence.properties')
  assert.equal(evidenceProps.task_write_files.type, 'array')
})

// --- commit prompt ---

test('PROMPTS.commit includes {{writeFilesScope}} placeholder', () => {
  assert.match(PROMPTS.commit, /\{\{writeFilesScope\}\}/)
})

test('PROMPTS.commit includes out_of_scope boundary check instruction', () => {
  assert.match(PROMPTS.commit, /out_of_scope/)
})

test('SCHEMAS.commit diagnostics includes out_of_scope field', () => {
  const diagProps = SCHEMAS.commit.properties.diagnostics.properties
  assert.ok(diagProps.out_of_scope, 'out_of_scope should be in diagnostics.properties')
  assert.equal(diagProps.out_of_scope.type, 'array')
})
