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
  for (const fn of ['formatReferencePaths', 'formatSilentFailureContext', 'formatFailedApproaches', 'formatLessons', 'formatWriteFilesScope', 'formatSchemaCheck', 'languageChecklist', 'LANGUAGE_CHECKLISTS', 'gateCommands', 'collectReviewFindings', 'formatFindings', 'matchesPlanFilter', 'classifyThrown', 'reviewHaltReason', 'reviewHaltForEmptyFailed', 'haltLikelySource', 'summarizeReviewRound']) {
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

test('REGRESSION: allGreen break 在 detectOscillation 之前（防收敛误报 OSCILLATING）', () => {
  // r3 三 reviewer 全 ok 时，若 detectOscillation（核心文件被审 ≥3 轮）先判 halt，
  // allGreen break 永远轮不到 → 收敛误报（T2 invite / T5 channels）。顺序断言保证
  // allGreen 提前放行 review 共识，真矛盾（不全绿）才落进 detectOscillation halt。
  const allGreenIdx = runSrc.indexOf('if (allGreen(spec, qual, hunt)) break')
  const oscIdx = runSrc.indexOf('const osc = detectOscillation')
  assert.notEqual(allGreenIdx, -1, 'run-plans.js 须有 allGreen break')
  assert.notEqual(oscIdx, -1, 'run-plans.js 须有 detectOscillation 调用')
  assert.ok(allGreenIdx < oscIdx, 'allGreen break 必须在 detectOscillation 之前（收敛误报根治）')
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
  assert.match(runSrc, /failedApproaches: formatFailedApproaches/, 'implCtx 须注入 failedApproaches')
  assert.match(runSrc, /failed_approaches/, 'SCHEMAS.bootstrap 须含 failed_approaches')
  assert.match(runSrc, /failed_approach:/, 'halt() 须在 blocked_info 中记录 failed_approach')
  assert.match(runSrc, /writeFilesScope: formatWriteFilesScope/, 'commitCtx 须注入 writeFilesScope')
  assert.match(runSrc, /task_write_files/, 'SCHEMAS.bootstrap 须含 task_write_files')
  assert.match(runSrc, /out_of_scope/, 'SCHEMAS.commit 须含 out_of_scope')
  assert.match(runSrc, /destructive_changes/, 'SCHEMAS.commit 须含 destructive_changes')
  assert.match(runSrc, /destructive_review_failed/, 'orchestrator 须检测 destructive_changes 并记录结果')
  assert.match(runSrc, /lessons: formatLessons/, 'implCtx 须注入 lessons')
  assert.match(runSrc, /task_lessons/, 'SCHEMAS.bootstrap 须含 task_lessons')
  assert.match(runSrc, /lessonsPath: state\.config\?\.lessons_path \|\| ''/, 'finalReportWithFallback 须传 lessonsPath（done + halted 两模式）')
  assert.match(runSrc, /schemaCheck: formatSchemaCheck/, 'gate ctx 须注入 schemaCheck')
  assert.match(runSrc, /migration_missing/, 'SCHEMAS.gate + orchestrator 须含 migration_missing 检查')
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

test('行尾一致性：lib.js 与 run-plans.js 不得含 bare LF（CRLF 根因防护）', () => {
  // T5 曾因 LF 混入导致 promptBody 正则失配 → sync 测试误报。.gitattributes 强制 CRLF 后，
  // 此测试守护"两文件均无 bare LF"——任一文件出现 \n 未配对 \r 即回归。
  for (const [name, src] of [['lib.js', libSrc], ['run-plans.js', runSrc]]) {
    const bareLf = (src.match(/(?<!\r)\n/g) || []).length
    assert.equal(bareLf, 0, `${name} 含 ${bareLf} 个 bare LF（应全 CRLF）——检查 .gitattributes 是否生效 / 编辑器是否混入 LF`)
  }
})

test('run-plans.js 不得残留 budget 死引用（runtime 未注入此全局，提及会误导维护者）', () => {
  // budget 在代码中未使用，文件头注释提及会让人误以为 runtime 注入了它。
  assert.doesNotMatch(runSrc, /\bbudget\b/, 'run-plans.js 残留 budget 死引用——runtime 未注入此全局，应从注释删除')
})

test('review_history 存档：每轮 findings 摘要进 manifest（OSCILLATING halt 后可定位振荡点）', () => {
  // T3 OSCILLATING halt 暴露：review_rounds 只存 int 计数，findings 不持久化 →
  // halt 后无法精确还原哪个 reviewer 在哪点 flip-flop，需考古推断。review_history 修复。
  // summarizeReviewRound 纯函数两副本一致（lib.js 真源 + run-plans.js inline）
  assert.match(libSrc, /export function summarizeReviewRound/, 'lib.js 须有 summarizeReviewRound 真源')
  assert.match(runSrc, /function summarizeReviewRound/, 'run-plans.js 须 inline summarizeReviewRound')
  // perTask 初始化含 review_history: []
  assert.match(runSrc, /review_history:\s*\[\]/, 'perTask 初始化须含 review_history: []')
  // review 循环每轮 push 摘要（须在 allGreen break 之前，确保 halt 轮/收敛轮也被记录）
  const pushIdx = runSrc.indexOf('review_history.push(summarizeReviewRound(round, spec, qual, hunt))')
  const allGreenIdx = runSrc.indexOf('if (allGreen(spec, qual, hunt)) break')
  assert.notEqual(pushIdx, -1, 'review 循环须每轮 push summarizeReviewRound')
  assert.ok(pushIdx < allGreenIdx, 'review_history.push 必须在 allGreen break 之前（halt/收敛轮也要被记）')
  // finalReport prompt per_task 结构含 review_history
  const finalP = promptBody(runSrc, 'finalReport')
  assert.match(finalP, /review_history/, 'finalReport manifest per_task 须含 review_history 字段')
})

test('halt 不再自动写 lesson（废条目根因：把 halt reason 当 lesson title）', () => {
  // 旧 finalReport step5：halt 时 append lesson title:<reason> detail:<last_error> status:active
  // → reason=OSCILLATING 时 title/detail 都是 "OSCILLATING"，无信息量，且混进 active lesson 污染知识库。
  // 修复：halt 事件由 blocked.md 完整记录；lesson 只由人/复盘提炼。删除自动追加逻辑。
  for (const [name, src] of [['lib.js', libSrc], ['run-plans.js', runSrc]]) {
    const p = promptBody(src, 'finalReport')
    assert.doesNotMatch(p, /append a new lesson entry/, `${name} finalReport 不得再 halt 自动写 lesson`)
    assert.doesNotMatch(p, /title: <reason>/, `${name} finalReport 不得把 halt reason 当 lesson title`)
  }
})
