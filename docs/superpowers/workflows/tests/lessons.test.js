import { test } from 'node:test'
import assert from 'node:assert/strict'
import { PROMPTS, SCHEMAS, formatLessons } from '../lib.js'

// --- formatLessons helper ---

test('formatLessons: empty array returns empty string', () => {
  assert.equal(formatLessons([]), '')
})

test('formatLessons: non-empty returns readable section', () => {
  const result = formatLessons([{ id: 'L-001', title: 'split-commit silent miss', detail: 'DrawResult+outbox must single-commit' }])
  assert.match(result, /Lessons Learned/)
  assert.match(result, /L-001/)
  assert.match(result, /split-commit silent miss/)
  assert.match(result, /DrawResult\+outbox must single-commit/)
  assert.match(result, /explicitly state why your approach differs/)
})

// --- bootstrap prompt ---

test('PROMPTS.bootstrap mentions lessons_path reading', () => {
  assert.match(PROMPTS.bootstrap, /lessons_path/)
  assert.match(PROMPTS.bootstrap, /task_lessons/)
})

test('SCHEMAS.bootstrap evidence includes task_lessons field', () => {
  const evidenceProps = SCHEMAS.bootstrap.properties.evidence.properties
  assert.ok(evidenceProps.task_lessons, 'task_lessons should be in evidence.properties')
  assert.equal(evidenceProps.task_lessons.type, 'array')
})

// --- implementor prompt ---

test('PROMPTS.implementor includes {{lessons}} placeholder', () => {
  assert.match(PROMPTS.implementor, /\{\{lessons\}\}/)
})

// --- finalReport prompt ---

test('PROMPTS.finalReport includes lessonsPath and lesson append instruction', () => {
  assert.match(PROMPTS.finalReport, /\{\{lessonsPath\}\}/)
  assert.match(PROMPTS.finalReport, /lessonsPath/)
  assert.match(PROMPTS.finalReport, /append.*lesson/i)
})
