import { test } from 'node:test'
import assert from 'node:assert/strict'
import { normalizeCompleted } from '../lib.js'

test('seq 前缀格式 "01/T2" → "plan-01/T2"', () => {
  assert.deepEqual(normalizeCompleted(['01/T2']), ['plan-01/T2'])
})

test('完整前缀 "plan-01/T4b" → "plan-01/T4b"（幂等）', () => {
  assert.deepEqual(normalizeCompleted(['plan-01/T4b']), ['plan-01/T4b'])
})

test('裸 id 无 plan 信息 → 原样保留（不误匹配 plan-scoped key）', () => {
  assert.deepEqual(normalizeCompleted(['T2']), ['T2'])
})

test('多位 seq 与多位 task 号', () => {
  assert.deepEqual(normalizeCompleted(['10/T12']), ['plan-10/T12'])
})

test('混合输入统一归一化', () => {
  assert.deepEqual(
    normalizeCompleted(['01/T2', 'plan-01/T3', 'T4', '02/T10']),
    ['plan-01/T2', 'plan-01/T3', 'T4', 'plan-02/T10'],
  )
})

test('跨 plan 同名 task 不互相污染（核心修复意图）', () => {
  // Plan 01 已 commit 的 task 经归一化
  const completed = normalizeCompleted(['01/T2', '01/T6'])
  // 跑 Plan 02 的 T2 时比对 plan-02/T2 —— 不在 completed 中 → 不跳过（正确，避免误 skip）
  assert.equal(completed.includes('plan-02/T2'), false)
  // Plan 01 resume 跑 T2 时比对 plan-01/T2 —— 在 completed 中 → 跳过（正确）
  assert.equal(completed.includes('plan-01/T2'), true)
})

test('空数组', () => {
  assert.deepEqual(normalizeCompleted([]), [])
})
