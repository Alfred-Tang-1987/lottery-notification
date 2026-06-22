import { test } from 'node:test'
import assert from 'node:assert/strict'
import { buildPrompt, PROMPTS } from '../lib.js'

test('buildPrompt fills ctx placeholders', () => {
  const original = PROMPTS.bootstrap
  PROMPTS.bootstrap = 'config={{configPath}} plans={{plansDir}} task={{taskId}}'
  try {
    const out = buildPrompt('bootstrap', { configPath: 'c.json', plansDir: 'p/', taskId: 'T1' })
    assert.equal(out, 'config=c.json plans=p/ task=T1')
  } finally {
    PROMPTS.bootstrap = original
  }
})

test('buildPrompt throws on unknown role', () => {
  assert.throws(() => buildPrompt('nope', {}), /unknown role/)
})
