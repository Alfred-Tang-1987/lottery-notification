import { test } from 'node:test'
import assert from 'node:assert/strict'
import { formatReferencePaths, languageChecklist, LANGUAGE_CHECKLISTS, gateCommands } from '../lib.js'

test('formatReferencePaths: empty/absent → empty string (prompt section disappears)', () => {
  assert.equal(formatReferencePaths(undefined), '')
  assert.equal(formatReferencePaths([]), '')
  assert.equal(formatReferencePaths(null), '')
})

test('formatReferencePaths: lists each path as bullet + authoritative header', () => {
  const out = formatReferencePaths(['docs/reference/lottery-rules.md'])
  assert.match(out, /Reference Documents/)
  assert.match(out, /- docs\/reference\/lottery-rules\.md/)
  assert.match(out, /authoritative/i)
})

test('languageChecklist: python returns Python/FastAPI checklist; unknown → general', () => {
  assert.match(languageChecklist('python'), /SQL injection/)
  assert.match(languageChecklist('python'), /Mutable default args/)
  assert.match(languageChecklist('python'), /Blocking calls inside async/)
  assert.equal(languageChecklist('python'), LANGUAGE_CHECKLISTS.python)
  assert.equal(languageChecklist('typescript'), LANGUAGE_CHECKLISTS.general)
  assert.equal(languageChecklist(undefined), LANGUAGE_CHECKLISTS.general)
})

test('gateCommands: assembles test + lint + extra_lint in order, dedupes empties', () => {
  const cmds = gateCommands({
    full_test_command: 'uv run pytest -v',
    lint_command: 'uv run ruff check .',
    extra_lint_commands: ['uv run lint-imports'],
  })
  assert.deepEqual(cmds, [
    { kind: 'test', command: 'uv run pytest -v' },
    { kind: 'lint', command: 'uv run ruff check .' },
    { kind: 'lint', command: 'uv run lint-imports' },
  ])
})

test('gateCommands: optional fields absent → only test (backward compatible)', () => {
  assert.deepEqual(gateCommands({ full_test_command: 'pytest' }), [{ kind: 'test', command: 'pytest' }])
  assert.deepEqual(gateCommands({}), [])
  // extra_lint with empty-string entries filtered out
  assert.deepEqual(gateCommands({ lint_command: 'ruff', extra_lint_commands: ['', 'mypy'] }),
    [{ kind: 'lint', command: 'ruff' }, { kind: 'lint', command: 'mypy' }])
})
