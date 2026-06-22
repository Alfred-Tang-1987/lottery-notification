// lottery-workflow-lib —— workflow orchestrator 纯函数真源
// 此文件被 node --test 测试；run-plans.js inline 复制其中的函数。

// 从 plan markdown 提取叶子 task ID（§13e 叶子优先规则）。
// 规则：## Task N 下若有 ### Task NX 子 task → 只取子 task；否则取 Task N 本身。
export function leafTasks(markdown) {
  const tops = []          // {id, children:[]}
  let current = null
  for (const line of markdown.split('\n')) {
    const m1 = line.match(/^##\s+Task\s+(\d+)\b/)
    if (m1) { current = { id: 'T' + m1[1], children: [] }; tops.push(current); continue }
    const m2 = line.match(/^###\s+Task\s+(\d+)([a-z])\b/)
    if (m2 && current) { current.children.push('T' + m2[1] + m2[2]) }
  }
  return tops.flatMap(t => t.children.length ? t.children : [t.id])
}

export function detectOscillation(filesTouchedPerRound) {
  throw new Error('not implemented') // Task 3
}

export function buildPrompt(role, ctx) {
  throw new Error('not implemented') // Task 4
}

export function allGreen(...reviews) {
  throw new Error('not implemented') // Task 4
}

export function unionFiles(...reviews) {
  throw new Error('not implemented') // Task 4
}

export function issuesFromReviews(...reviews) {
  throw new Error('not implemented') // Task 4
}

export const SCHEMAS = {} // Task 5
export const PROMPTS = {} // Task 6
