import { test } from 'node:test'
import assert from 'node:assert/strict'
import { leafTasks } from '../lib.js'

test('parent task with children yields only children (Plan 01 式嵌套)', () => {
  const md = [
    '## Task 1: uv 初始化',
    'body',
    '## Task 4: SQLModel schema',
    '### Task 4a: 基类',
    '### Task 4b: 号码',
    '### Task 4c: 通知',
    '## Task 5: 种子',
  ].join('\n')
  assert.deepEqual(leafTasks(md), ['T1', 'T4a', 'T4b', 'T4c', 'T5'])
})

test('task without children yields itself', () => {
  assert.deepEqual(leafTasks('## Task 3: DB engine'), ['T3'])
})

test('multi-digit task numbers', () => {
  assert.deepEqual(leafTasks('## Task 12: foo'), ['T12'])
})

test('ignores non-task headings', () => {
  const md = '## File Structure\n## Task 1: a\n### Subsection\n## Task 2: b'
  assert.deepEqual(leafTasks(md), ['T1', 'T2'])
})
