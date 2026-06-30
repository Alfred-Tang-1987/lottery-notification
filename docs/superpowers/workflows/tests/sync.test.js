import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

// 同步护栏：run-plans.js inline 复制 lib.js 的 PROMPTS/SCHEMAS/helpers。
// Workflow runtime 禁止 fs/模块访问，只能 inline 复制——此测试降低漏同步概率（无法根除）。
const __dirname = path.dirname(fileURLToPath(import.meta.url))
const libSrc = fs.readFileSync(path.resolve(__dirname, '../lib.js'), 'utf8')
const runSrc = fs.readFileSync(path.resolve(__dirname, '../../../../.claude/workflows/run-plans.js'), 'utf8')

// 提取 PROMPTS[role] 的模板字面量正文（prompts 不含内嵌反引号/{{}} 之外的 ${}，故首个反引号即闭合）。
function promptBody(src, role) {
  const re = new RegExp(`  ${role}: \\\`([\\s\\S]*?)\\\`,`)
  const m = src.match(re)
  assert.ok(m, `role ${role} template not found`)
  return m[1]
}

const ROLES = ['bootstrap', 'implementor', 'specReview', 'qualityReviewer', 'hunter', 'simplify', 'commit', 'contextFetcher', 'gate', 'finalReport']

for (const role of ROLES) {
  test(`PROMPTS.${role} identical between lib.js and run-plans.js`, () => {
    assert.equal(promptBody(runSrc, role), promptBody(libSrc, role),
      `PROMPTS.${role} drifted — lib.js 改了 prompt 必须同步 run-plans.js（文件头注释已声明）`)
  })
}

test('run-plans.js inlines the new conditional-render helpers', () => {
  for (const fn of ['formatReferencePaths', 'formatSilentFailureContext', 'languageChecklist', 'LANGUAGE_CHECKLISTS', 'gateCommands', 'collectReviewFindings', 'formatFindings', 'matchesPlanFilter', 'classifyThrown', 'reviewHaltReason', 'reviewHaltForEmptyFailed', 'haltLikelySource']) {
    assert.match(runSrc, new RegExp(`function ${fn}|const ${fn}`), `missing helper: ${fn}`)
  }
})

test('run-plans.js SCHEMAS mirror key changes', () => {
  // implementor enum 含 done_with_concerns；diagnostics 含 concerns
  assert.match(runSrc, /'done_with_concerns'/)
  assert.match(runSrc, /concerns:\s*\{\s*type:\s*'array'\s*\}/)
  // gate evidence required 含 lint_results
  assert.match(runSrc, /required:\s*\['tests_exit_code',\s*'pytest_summary',\s*'lint_results'\]/)
})

test('run-plans.js inlines review_empty 空响应守卫 + review_failed_no_findings + quality/hunter items 约束', () => {
  // reviewHaltReason 守卫：空响应 → review_empty sentinel（防 thinking-only 静默哑火）
  assert.match(runSrc, /REVIEW_VALID_STATUSES/, 'run-plans.js 须 inline REVIEW_VALID_STATUSES 集合')
  assert.match(runSrc, /'review_empty'/, "run-plans.js 须 inline review_empty sentinel")
  // 第二道守卫：failed 但 0 findings → review_failed_no_findings（防空修复循环 → max rounds 误 halt）
  assert.match(runSrc, /'review_failed_no_findings'/, 'run-plans.js 须 inline review_failed_no_findings sentinel')
  assert.match(runSrc, /reviewHaltForEmptyFailed\(spec, qual, hunt\)/, '主 review 轮须接 reviewHaltForEmptyFailed 守卫')
  assert.match(runSrc, /reviewHaltForEmptyFailed\(spec2, qual2, hunt2\)/, 'simplify review 轮须接 reviewHaltForEmptyFailed 守卫')
  // qualityReviewer 拆出独立 schema（issues items 强制 {title,fix}）
  assert.match(runSrc, /function qualityReviewSchema/, 'run-plans.js 须 inline qualityReviewSchema')
  // hunter silent_failures items 约束
  assert.match(runSrc, /silent_failures:\s*\{\s*type:\s*'array',\s*items:/, 'hunter silent_failures 须有 items 约束')
  // 同步也要校验 lib.js 真源一致
  assert.match(libSrc, /REVIEW_VALID_STATUSES/)
  assert.match(libSrc, /'review_empty'/)
  assert.match(libSrc, /'review_failed_no_findings'/)
  assert.match(libSrc, /function qualityReviewSchema/)
  assert.match(libSrc, /silent_failures:\s*\{\s*type:\s*'array',\s*items:/)
})

test('run-plans.js orchestrator wires new placeholders + gate lint loop', () => {
  assert.match(runSrc, /referencePaths: formatReferencePaths/)
  assert.match(runSrc, /languageChecklist: languageChecklist\(cfg\.language\)/)
  assert.match(runSrc, /concernsHint/)
  assert.match(runSrc, /gateCommands\(state\.config\)/)
  assert.match(runSrc, /gateCommands: JSON\.stringify\(cmds\)/)
  assert.match(runSrc, /fetchedContext:/)
  assert.match(runSrc, /simplifyRevertNote:/)
  assert.match(runSrc, /silentFailureContext: formatSilentFailureContext\(cfg\.silent_failure_context\)/, 'hunter 须注入 silentFailureContext')
})

test('finalReport prompt 探查工作树脏状态（halt 时记录，防回归）', () => {
  // 两副本（lib.js + run-plans.js）的 finalReport prompt 都应含 git status 探查 + likely_source
  for (const src of [libSrc, runSrc]) {
    const p = promptBody(src, 'finalReport')
    assert.match(p, /git status --porcelain/, 'finalReport 须探查工作树脏状态')
    assert.match(p, /likely_source/, 'finalReport blocked.md 须含 likely_source 语义提示')
    assert.match(p, /blockedInfo=/, 'finalReport 须接收 blockedInfo 独立占位符')
  }
})

test('run-plans.js orchestrator 传 blockedInfo 给 finalReport', () => {
  // halt 传 blockedInfo（halted task 的 blocked_info JSON）；done 传空串（条件渲染段落消失）
  assert.match(runSrc, /blockedInfo, runsDir/, 'halt() 须传 blockedInfo')
  assert.match(runSrc, /blockedInfo: ''/, 'done 模式 finalReport 须传空 blockedInfo')
})

test('no彩票硬编码残留在通用 prompt（bootstrap 中性化 + qualityReviewer 去 domain 纪律）', () => {
  const boot = promptBody(runSrc, 'bootstrap')
  assert.doesNotMatch(boot, /lottery-notification/, 'bootstrap 不得硬编码项目名')
  const qual = promptBody(runSrc, 'qualityReviewer')
  assert.doesNotMatch(qual, /domain-layer-zero-IO/, 'domain 纯度纪律应靠 gate extra_lint_commands 强制，不硬编码进 qualityReviewer')
})
