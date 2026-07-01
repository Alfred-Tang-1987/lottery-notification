import { test } from 'node:test'
import assert from 'node:assert/strict'
import { formatSchemaCheck, PROMPTS, SCHEMAS } from '../lib.js'

test('formatSchemaCheck: empty schemaTool → empty string (check skipped, backward compatible)', () => {
  assert.equal(formatSchemaCheck('', [], []), '')
  assert.equal(formatSchemaCheck('', ['app/models/'], ['alembic/versions/']), '')
})

test('formatSchemaCheck: non-empty schemaTool → readable section with check instructions', () => {
  const out = formatSchemaCheck('alembic', ['app/models/'], ['alembic/versions/'])
  assert.match(out, /## Schema Migration Check/)
  assert.match(out, /git diff --name-only HEAD~1\.\.HEAD/)
  assert.match(out, /app\/models\//)
  assert.match(out, /alembic\/versions\//)
  assert.match(out, /migration_missing/)
})

test('PROMPTS.gate includes {{schemaCheck}} placeholder on its own line', () => {
  assert.match(PROMPTS.gate, /\{\{schemaCheck\}\}/)
})

test('SCHEMAS.gate evidence includes migration_missing boolean field', () => {
  assert.match(JSON.stringify(SCHEMAS.gate), /migration_missing/)
  assert.match(JSON.stringify(SCHEMAS.gate), /boolean/)
})
