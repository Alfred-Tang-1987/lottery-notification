import { test } from 'node:test'
import assert from 'node:assert/strict'
import { PROMPTS, SCHEMAS } from '../lib.js'

test('PROMPTS.commit includes destructive change detection step', () => {
  assert.match(PROMPTS.commit, /destructive/i)
  assert.match(PROMPTS.commit, /deleted_code/i)
  assert.match(PROMPTS.commit, /signature_change/i)
  assert.match(PROMPTS.commit, /file_deletion/i)
})

test('PROMPTS.commit uses git diff HEAD --numstat for deterministic detection (S4)', () => {
  // S4（第 4 轮）: 须用 git diff HEAD（非 git diff --cached）——文件未 git add 时 --cached 永远为空，
  //   destructive review 永不触发。git diff HEAD 对比工作树与 HEAD，无需暂存即可检测改动。
  assert.match(PROMPTS.commit, /git diff HEAD --numstat/)
  assert.doesNotMatch(PROMPTS.commit, /git diff --cached --numstat/, '不得用 git diff --cached（S4：文件未 git add → 永远为空）')
})

test('SCHEMAS.commit diagnostics includes destructive_changes field', () => {
  const diagProps = SCHEMAS.commit.properties.diagnostics.properties
  assert.ok(diagProps.destructive_changes, 'destructive_changes should be in diagnostics.properties')
  assert.equal(diagProps.destructive_changes.type, 'array')
})
