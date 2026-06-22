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

// 振荡检测（§13g）。纯数组操作，无 fs。
export function detectOscillation(filesTouchedPerRound) {
  if (filesTouchedPerRound.length < 3) return { oscillating: false }

  // 规则 1：同文件出现在 >=3 个 round → 振荡
  const fileRoundCount = {}
  for (const [i, files] of filesTouchedPerRound.entries()) {
    for (const f of files) {
      (fileRoundCount[f] ||= []).push(i)
    }
  }
  for (const [file, rounds] of Object.entries(fileRoundCount)) {
    if (rounds.length >= 3) {
      return { oscillating: true, reason: `${file} touched in ${rounds.length} rounds`, file, rounds }
    }
  }

  // 规则 2：连续 2 round 的 files 高度重叠（>=2 且完全重叠）→ 振荡
  for (let i = 1; i < filesTouchedPerRound.length; i++) {
    const prev = new Set(filesTouchedPerRound[i - 1])
    const curr = filesTouchedPerRound[i]
    const overlap = curr.filter(f => prev.has(f))
    if (overlap.length >= 2 && overlap.length === curr.length) {
      return { oscillating: true, reason: `consecutive rounds fix same files: ${overlap.join(',')}`, files: overlap }
    }
  }
  return { oscillating: false }
}

export function buildPrompt(role, ctx = {}) {
  const tpl = PROMPTS[role]
  if (!tpl) throw new Error(`unknown role: ${role}`)
  return tpl.replace(/\{\{(\w+)\}\}/g, (_, k) => (k in ctx ? String(ctx[k]) : `{{${k}}}`))
}

export function allGreen(...reviews) {
  return reviews.every(r => r && r.status === 'ok')
}

export function unionFiles(...reviews) {
  const set = new Set()
  for (const r of reviews) for (const f of (r?.diagnostics?.files_touched || [])) set.add(f)
  return [...set]
}

export function issuesFromReviews(...reviews) {
  const out = []
  for (const r of reviews) if (r && r.status === 'failed') out.push(...(r.diagnostics?.issues || []))
  return out
}

export const SCHEMAS = {
  bootstrap: {
    type: 'object', required: ['status', 'evidence'], additionalProperties: true,
    properties: {
      status: { type: 'string', enum: ['ok', 'failed', 'blocked'] },
      evidence: { type: 'object', required: ['config', 'plans', 'completed', 'dirty_tree'],
        properties: { config: { type: 'object' }, plans: { type: 'array' }, completed: { type: 'array' }, dirty_tree: { type: 'boolean' } } },
      diagnostics: { type: 'object' }, summary: { type: 'string' },
    },
  },
  implementor: {
    type: 'object', required: ['status', 'evidence'], additionalProperties: true,
    properties: {
      status: { type: 'string', enum: ['ok', 'failed', 'blocked', 'needs_context'] },
      evidence: { type: 'object', required: ['tests_exit_code', 'files_changed', 'pytest_summary'],
        properties: { tests_exit_code: { type: 'integer' }, files_changed: { type: 'array' }, pytest_summary: { type: 'string' } } },
      diagnostics: { type: 'object', properties: { blocked_category: { type: 'string' }, last_error: { type: 'string' }, suggested_fix: { type: 'string' } } },
      summary: { type: 'string' },
    },
  },
  specReview: reviewSchema(),
  qualityReviewer: reviewSchema(),
  hunter: { type: 'object', required: ['status'], additionalProperties: true,
    properties: { status: { type: 'string', enum: ['ok', 'failed'] },
      diagnostics: { type: 'object', properties: { files_touched: { type: 'array' }, silent_failures: { type: 'array' } } },
      summary: { type: 'string' } } },
  simplify: { type: 'object', required: ['evidence'], additionalProperties: true,
    properties: { evidence: { type: 'object', required: ['changed', 'files_changed'],
      properties: { changed: { type: 'boolean' }, files_changed: { type: 'array' } } }, summary: { type: 'string' } } },
  commit: { type: 'object', required: ['status', 'evidence'], additionalProperties: true,
    properties: { status: { type: 'string', enum: ['ok', 'failed'] },
      evidence: { type: 'object', required: ['commit_sha', 'committed_files'],
        properties: { commit_sha: { type: 'string' }, committed_files: { type: 'array' }, tests_at_commit: { type: 'integer' } } },
      diagnostics: { type: 'object' }, summary: { type: 'string' } } },
  contextFetcher: { type: 'object', required: ['diagnostics'], additionalProperties: true,
    properties: { diagnostics: { type: 'object', required: ['context'], properties: { context: { type: 'string' } } }, summary: { type: 'string' } } },
  gate: { type: 'object', required: ['status', 'evidence'], additionalProperties: true,
    properties: { status: { type: 'string', enum: ['ok', 'failed'] },
      evidence: { type: 'object', required: ['tests_exit_code', 'pytest_summary'],
        properties: { tests_exit_code: { type: 'integer' }, pytest_summary: { type: 'string' } } }, summary: { type: 'string' } } },
  finalReport: { type: 'object', required: ['summary'], additionalProperties: true,
    properties: { evidence: { type: 'object', properties: { manifest_path: { type: 'string' } } }, summary: { type: 'string' } } },
}

function reviewSchema() {
  return { type: 'object', required: ['status'], additionalProperties: true,
    properties: {
      status: { type: 'string', enum: ['ok', 'failed'] },
      diagnostics: { type: 'object', properties: { files_touched: { type: 'array' }, issues: { type: 'array' } } },
      summary: { type: 'string' },
    } }
}

export const PROMPTS = {} // Task 6
