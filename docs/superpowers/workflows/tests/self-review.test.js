import { test } from 'node:test'
import assert from 'node:assert/strict'
import { PROMPTS } from '../lib.js'

test('PROMPTS.implementor includes 6-Dimension Quick Check section', () => {
  const prompt = PROMPTS.implementor
  assert.match(prompt, /6-Dimension Quick Check/)
})

test('PROMPTS.implementor includes all 6 dimensions with actionable questions', () => {
  const prompt = PROMPTS.implementor
  assert.match(prompt, /Cognitive Overload/i)
  assert.match(prompt, /Change Propagation/i)
  assert.match(prompt, /Knowledge Duplication/i)
  assert.match(prompt, /Accidental Complexity/i)
  assert.match(prompt, /Dependency Disorder/i)
  assert.match(prompt, /Domain Distortion/i)
  // Each dimension should have a question mark (actionable check)
  const dimSection = prompt.match(/6-Dimension Quick Check[\s\S]*?(?=\n\n|\nReturn|\nIf self-review)/)
  assert.ok(dimSection, '6-Dimension Quick Check section should exist')
  // At least 6 question marks in the section
  const questions = dimSection[0].match(/\?/g)
  assert.ok(questions && questions.length >= 6, 'Each dimension should have a question')
})
