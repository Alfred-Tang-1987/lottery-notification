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

const ROLES = ['bootstrap', 'implementor', 'specReview', 'qualityReviewer', 'hunter', 'simplify', 'commit', 'contextFetcher', 'gate', 'finalReport', 'lessonDistiller']

for (const role of ROLES) {
  test(`PROMPTS.${role} identical between lib.js and run-plans.js`, () => {
    assert.equal(promptBody(runSrc, role), promptBody(libSrc, role),
      `PROMPTS.${role} drifted — lib.js 改了 prompt 必须同步 run-plans.js（文件头注释已声明）`)
  })
}

test('run-plans.js inlines the new conditional-render helpers', () => {
  // QC-3: formatLessonsForDistill / applyLessonDecisions / renderLessonEntry 不再 inline
  // （SH2 后 distiller 自读写盘，orchestrator 不调用这些函数）。lib.js 真源保留。
  for (const fn of ['formatReferencePaths', 'formatSilentFailureContext', 'formatFailedApproaches', 'formatLessons', 'formatWriteFilesScope', 'formatSchemaCheck', 'languageChecklist', 'LANGUAGE_CHECKLISTS', 'gateCommands', 'collectReviewFindings', 'formatFindings', 'matchesPlanFilter', 'classifyThrown', 'reviewHaltReason', 'reviewHaltForEmptyFailed', 'haltLikelySource', 'fixModelForRound', 'resolveMaxRounds', 'resolveLessonsAutoDistill', 'distillLessonInput', 'summarizeReviewRound']) {
    assert.match(runSrc, new RegExp(`function ${fn}|const ${fn}`), `missing helper: ${fn}`)
  }
})

// QC-4: 关键 helper 函数体字节比较（防 lib.js 改了实现但忘了同步 run-plans.js inline 副本）。
// 提取函数体：从 `function fn(` 到列 0 的闭合 `}`（top-level 函数末尾），不含函数间注释
// （lib.js 和 run-plans.js 的注释风格不同——后者有 "—— inline 自 lib.js" 后缀——但代码必须一致）。
function extractFunctionBody(src, fnName) {
  const needle = `function ${fnName}(`
  const fnStart = src.indexOf(needle)
  if (fnStart === -1) return null
  // Top-level 函数的闭合 `}` 在列 0（\n 后紧跟 }，无缩进）
  const afterFn = src.slice(fnStart)
  const closeMatch = afterFn.match(/\n\}/)
  if (!closeMatch) return null
  return afterFn.slice(0, closeMatch.index + 2).trim()
}

test('QC-4: 关键 helper 函数体 lib.js ↔ run-plans.js 字节一致', () => {
  // Q7/S5: 扩展覆盖——影响路由/识别/反馈的关键决策函数须字节比较，不仅存在性正则
  // Q8（本轮新增）: validateAmendResult / validateCheckoutResult 纯函数化后纳入字节比较
  // Q9（第 4 轮新增）: findingsOf / summarizeFinding 内部 helper 也 inline 复制，须字节守护
  const fns = [
    'fixModelForRound', 'resolveMaxRounds', 'haltLikelySource', 'reviewHaltReason',
    'reviewHaltForEmptyFailed', 'detectOscillation', 'classifyThrown',
    // Q7 新增：影响 halt 路由 / commit 识别 / plan 过滤 / 反馈聚合 / distiller 输入
    'isQuotaError', 'commitSubject', 'normalizeCompleted', 'matchesPlanFilter',
    'collectReviewFindings', 'summarizeReviewRound', 'formatFindings',
    'resolveLessonsAutoDistill', 'distillLessonInput',
    // Q8 新增：方案 C subagent 返回值校验纯函数（边界条件可 node:test 行为测试）
    'validateAmendResult', 'validateCheckoutResult',
    // Q9 新增：collectReviewFindings / summarizeReviewRound 的内部 helper（inline 复制但未被字节比较守护）
    'findingsOf', 'summarizeFinding',
  ]
  for (const fn of fns) {
    const libBody = extractFunctionBody(libSrc, fn)
    const runBody = extractFunctionBody(runSrc, fn)
    assert.ok(libBody, `lib.js 中找不到函数 ${fn}`)
    assert.ok(runBody, `run-plans.js 中找不到函数 ${fn}`)
    assert.equal(runBody, libBody, `helper ${fn} 函数体字节不一致——lib.js 改了必须同步 run-plans.js inline 副本`)
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
  // Q10: reviewHaltForEmptyFailed 在 runReviewRound helper 内部调用（主轮/simplify 轮/destructive 轮共用）
  assert.match(runSrc, /function runReviewRound/, '须抽 runReviewRound helper（Q10，三处 review 调用共用）')
  assert.match(runSrc, /reviewHaltForEmptyFailed\(spec, qual, hunt\)/, 'runReviewRound helper 内部须调 reviewHaltForEmptyFailed 守卫')
  assert.match(runSrc, /emptyFailed/, '调用方须检查 helper 返回的 emptyFailed 字段')
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
  // 方案 C：simplifyRevertNote 已删除（commit 提前 + git diff 触发 + checkout 回退）
  assert.doesNotMatch(runSrc, /simplifyRevertNote/, '方案 C 后不得残留 simplifyRevertNote')
  assert.doesNotMatch(runSrc, /simplifyFailed/, '方案 C 后不得残留 simplifyFailed 变量')
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

// 方案 C（§5.2）：commit 提前 + git status 触发 review + amend/checkout 回退（Q1-Q6 健壮化）
test('方案 C: simplify 流程健壮化（safeAgent + 返回值验证 + git status + halt）', () => {
  // Q5: diff subagent 用 git status --porcelain（同时检测 staged + unstaged），非 git diff --stat
  // （finalReport prompt 中保留 git diff --stat 用于 blocked.md 显示 diff 摘要是合理的，此处只管 simplify 检测）
  assert.match(runSrc, /safeAgent\('Run `git status --porcelain`/, 'diff subagent 须用 git status --porcelain 检测改动（staged + unstaged）')
  // amend/checkout 仍须存在
  assert.match(runSrc, /git commit --amend/, 'simplify review 全绿后须 amend commit')
  // Q11（第 4 轮）: checkout 须用 git reset --hard HEAD（非 git checkout -- .），同时清理 staged changes
  assert.match(runSrc, /git reset --hard HEAD/, 'simplify review 失败须 git reset --hard HEAD 回退（Q11：清理 staged + untracked）')
  assert.doesNotMatch(runSrc, /git checkout -- \./, '不得用 git checkout -- .（Q11：不清理 staged changes）')
  // Q3: checkout 须同时 git clean -fd（处理 simplify 新建的 untracked 文件）
  assert.match(runSrc, /git clean -fd/, 'checkout 须同时 git clean -fd 处理 untracked 新文件')
  // Q1: amend 后须用 git rev-parse HEAD 独立获取 SHA + 正则校验 40 位 hex
  assert.match(runSrc, /git rev-parse HEAD/, 'amend 后须用 git rev-parse HEAD 独立获取新 SHA')
  assert.match(runSrc, /\[0-9a-f\]\{40\}/, 'amend SHA 须正则校验 40 位 hex（防 agent 返回错误消息被当 SHA）')
  // Q4: diff subagent 返回 null/异常时须 halt（不静默跳过让 simplify 改动留工作树）
  assert.match(runSrc, /simplify diff check failed/, 'diff subagent 失败时须 halt（不静默跳过）')
  // Q1/Q2: amend 失败时须 halt（不静默继续用旧 SHA → gate 在旧 SHA 跑漏检 simplify 改动）
  assert.match(runSrc, /simplify amend failed/, 'amend 失败时须 halt（不静默继续）')
  // Q3: checkout 失败时须 halt（不无条件设 simplify_reverted=true 谎报已回退）
  assert.match(runSrc, /simplify checkout failed/, 'checkout 失败时须 halt（不谎报 simplify_reverted）')
  // 不得信任 simplify 自报 changed
  assert.doesNotMatch(runSrc, /if \(simp\.evidence\.changed\)/, '不得信任 simplify 自报 changed 触发 review')
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

test('Q9: finalReport prompt per_task 结构含 simplify_reverted / destructive_review 字段', () => {
  // Q9: simplify_reverted / destructive_review_failed / destructive_review_findings 须在
  // finalReport prompt 的 per_task 结构中列出，否则 agent 可能丢弃这些字段（manifest 丢失记录）
  for (const src of [libSrc, runSrc]) {
    const p = promptBody(src, 'finalReport')
    assert.match(p, /simplify_reverted/, 'finalReport per_task 须含 simplify_reverted 字段')
    assert.match(p, /destructive_review_failed/, 'finalReport per_task 须含 destructive_review_failed 字段')
    assert.match(p, /destructive_review_findings/, 'finalReport per_task 须含 destructive_review_findings 字段')
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

test('halt 自动提炼 lesson 经独立 distiller agent（非 finalReport 内部调）', () => {
  // 旧 finalReport step5：halt 时 append lesson title:<reason> detail:<last_error> status:active
  // → reason=OSCILLATING 时 title/detail 都是 "OSCILLATING"，无信息量，污染知识库。
  // 新机制（§5.4，SH2 修复）：distiller 是 halt() 中的独立 agent 调用（orchestrator 无 fs，
  // distiller 自己读 lessonsPath + 自己写回）。finalReport step5 仅说明 distiller 已执行。
  for (const [name, src] of [['lib.js', libSrc], ['run-plans.js', runSrc]]) {
    const p = promptBody(src, 'finalReport')
    // 不得直接把 halt reason 当 lesson title（旧废条目根因）
    assert.doesNotMatch(p, /title: <reason>/, `${name} finalReport 不得把 halt reason 当 lesson title`)
    // finalReport 应说明 distiller 已独立执行（而非 finalReport 自己调 distiller）
    assert.match(p, /ALREADY been invoked|distiller.*has.*ALREADY/i, `${name} finalReport 应说明 distiller 已独立执行`)
  }
  // distiller prompt 应指示自己读 lessonsPath（非依赖 orchestrator 传 existingLessons）
  for (const [name, src] of [['lib.js', libSrc], ['run-plans.js', runSrc]]) {
    const p = promptBody(src, 'lessonDistiller')
    assert.match(p, /Read lessonsPath file/, `${name} lessonDistiller 应自己读 lessonsPath`)
    assert.match(p, /lessonsPath=\{\{lessonsPath\}\}/, `${name} lessonDistiller 输入应为 lessonsPath 路径`)
    assert.doesNotMatch(p, /existingLessons=\{\{existingLessons\}\}/, `${name} lessonDistiller 不应依赖 existingLessons 传入`)
  }
})

// ===== 本轮（commit 1057400 后 review）Q2-Q10 健壮化 TDD 断言 =====

test('Q2: perTask 初始化须含 simplify_reverted / destructive_review_* / concerns 字段（manifest 输出稳定）', () => {
  // Q2: 未初始化的字段仅在对应路径触发时赋值 → manifest JSON 序列化时被省略 → schema 不稳定
  // 须在 task 开始时就初始化为 false / [] 默认值
  assert.match(runSrc, /simplify_reverted:\s*false/, 'perTask 初始化须含 simplify_reverted: false')
  assert.match(runSrc, /destructive_review_failed:\s*false/, 'perTask 初始化须含 destructive_review_failed: false')
  assert.match(runSrc, /destructive_review_findings:\s*\[\]/, 'perTask 初始化须含 destructive_review_findings: []')
  assert.match(runSrc, /concerns:\s*\[\]/, 'perTask 初始化须含 concerns: []（done_with_concerns 路径未触发时也要有默认值）')
})

test('Q3: simplify review 失败后 findings 须持久化（不丢，用户无需考古 transcript）', () => {
  // Q3: simplify review 不全绿时 checkout 回退，但 review 发现的具体 issues 完全丢失
  // ——对比 destructive review 失败有 destructive_review_findings，simplify 也须等价持久化
  assert.match(runSrc, /simplify_review_findings/, 'simplify review 失败须持久化 findings 到 perTask.simplify_review_findings')
  // finalReport prompt per_task 结构须含 simplify_review_findings 字段（防 agent 丢弃）
  for (const src of [libSrc, runSrc]) {
    const p = promptBody(src, 'finalReport')
    assert.match(p, /simplify_review_findings/, 'finalReport per_task 须含 simplify_review_findings 字段')
  }
})

test('Q4: checkout subagent 须兜底验证（再跑 git status --porcelain 确认工作树真 clean）', () => {
  // Q4: checkout 返回 ok:true 但可能实际未 clean（权限/只读 fs/.gitignore 异常）→ 须独立验证
  // 用 validateCheckoutResult 纯函数校验 ok + porcelain 双字段（Q8 抽出，可行为测试）
  // Q11（第 4 轮）: checkout 须用 git reset --hard HEAD && git clean -fd（非 git checkout -- .）
  //   —— git checkout -- . 只回退 tracked 工作区修改，不清理 staged changes（simplify 误 git add 时残留）
  assert.match(runSrc, /git reset --hard HEAD && git clean -fd/, 'checkout 命令须含 git reset --hard HEAD && git clean -fd（Q11：同时清理 staged + untracked）')
  assert.doesNotMatch(runSrc, /git checkout -- \. && git clean -fd/, '不得用 git checkout -- .（Q11：不清理 staged changes）')
  assert.match(runSrc, /git status --porcelain/, 'checkout 后须再跑 git status --porcelain 兜底验证（Q4）')
  assert.match(runSrc, /validateCheckoutResult/, '须用 validateCheckoutResult 纯函数校验 checkout 返回值（Q8 抽出）')
  // checkout schema 须含 porcelain 字段（用于兜底验证）
  assert.match(runSrc, /porcelain/, 'checkout schema 须含 porcelain 字段（兜底验证工作树状态）')
})

test('Q6: filesChanged.join 须用换行分隔（避免逗号/空格文件路径歧义）', () => {
  // Q6: join(',') 对含逗号文件路径（如 data,v2.json）歧义；reviewer 用 git diff 作 ground truth，
  // changedHint 仅是聚焦提示，换行分隔更安全
  // Q3（第 4 轮）: destructive review 的 committed_files.join 也须换行分隔（三处统一）
  assert.doesNotMatch(runSrc, /filesChanged\.join\(','\)/, '不得用 join(",") 拼接文件列表（逗号歧义）')
  assert.doesNotMatch(runSrc, /simpFiles\.join\(','\)/, 'simplify 文件列表不得用 join(",")')
  assert.doesNotMatch(runSrc, /committed_files.*\.join\(','\)/, 'destructive 文件列表不得用 join(",")（Q3）')
  assert.match(runSrc, /filesChanged\.join\('\\n'\)/, '主轮文件列表须用 join("\\n") 换行分隔')
  assert.match(runSrc, /simpFiles\.join\('\\n'\)/, 'simplify 文件列表须用 join("\\n") 换行分隔')
  assert.match(runSrc, /committed_files.*\.join\('\\n'\)/, 'destructive 文件列表须用 join("\\n") 换行分隔（Q3）')
})

test('Q6（第 4 轮）: haltLikelySource 正则不得含死分支 simplify reported', () => {
  // Q6: 正则含 'simplify reported' 但无 halt reason 匹配此字符串 → 死分支
  // 两副本字节一致，查 lib.js 即可
  const libHalt = extractFunctionBody(libSrc, 'haltLikelySource')
  assert.doesNotMatch(libHalt, /simplify reported/, 'haltLikelySource 正则不得含死分支 simplify reported（Q6）')
})

test('Q7: runReviewRound helper 须给 spec/qual/hunt 三处都设 phase（UI 分组一致）', () => {
  // Q7: 旧实现仅 specReview 设 phase → /workflows UI 中 qual/hunt 不按阶段分组
  // helper 须给三处 opts 都设 phase（若 phaseLabel 非空）
  const helperStart = runSrc.indexOf('async function runReviewRound')
  const helperEnd = runSrc.indexOf('\n}', helperStart)
  const helperBody = runSrc.slice(helperStart, helperEnd + 2)
  // 三处 label 都应在 helper 内出现（spec/qual/hunt）
  assert.match(helperBody, /spec:/, 'helper 须含 spec label')
  assert.match(helperBody, /qual:/, 'helper 须含 qual label')
  assert.match(helperBody, /hunt:/, 'helper 须含 hunt label')
  // phase 须对三处都设（不能只设 spec）——检查 phaseLabel 传播
  assert.match(helperBody, /phase/, 'helper 须处理 phaseLabel')
  // 不得出现"仅 specOpts 设 phase"的旧模式
  assert.doesNotMatch(helperBody, /if \(phaseLabel\) specOpts\.phase = phaseLabel/, '不得仅给 specOpts 设 phase（须三处都设）')
})

test('Q9: agentWithFallback 返回值须被调用方检查（全链失败时不静默继续）', () => {
  // Q9: finalReportWithFallback 全链失败返回 null 时，halt()/done 路径不感知 → 用户误以为进度已保存
  // 须检查返回值，null 时 log 致命错误
  // Q7（第 4 轮）: finalReportWithFallback / lessonDistillerWithFallback 抽象为 agentWithFallback
  assert.match(runSrc, /agentWithFallback/, '须有 agentWithFallback helper（Q7 抽象）')
  assert.match(runSrc, /const fr = await agentWithFallback\('finalReport'|agentWithFallback\('finalReport'.*\n.*if\s*\(!fr\)/, 'halt/done 路径须检查 agentWithFallback 返回值')
  assert.match(runSrc, /manifest 未写入|manifest.*not.*written|无法保存/i, 'finalReport 返回 null 时须 log 致命错误提示')
  // Q7: 旧 finalReportWithFallback / lessonDistillerWithFallback 函数应被删除
  assert.doesNotMatch(runSrc, /async function finalReportWithFallback/, '不得残留 finalReportWithFallback 函数（Q7 已抽象为 agentWithFallback）')
  assert.doesNotMatch(runSrc, /async function lessonDistillerWithFallback/, '不得残留 lessonDistillerWithFallback 函数（Q7 已抽象为 agentWithFallback）')
})

test('Q10: concerns 须进 finalReport prompt per_task 结构（跨 session 可追溯 implementor 疑虑）', () => {
  // Q10: concerns 存 state.perTask 但 finalReport prompt per_task 未列 → manifest 不记录 → 跨 session 丢失
  for (const src of [libSrc, runSrc]) {
    const p = promptBody(src, 'finalReport')
    assert.match(p, /concerns/, 'finalReport per_task 须含 concerns 字段（implementor 疑虑可追溯）')
  }
})

test('Q1: haltLikelySource 须覆盖本轮新增的 simplify halt reason + commit out_of_scope', () => {
  // Q1: 方案 C 健壮化新增三个 halt reason，haltLikelySource 正则须覆盖
  // ——simplify 改动可能残留工作树是 likely_source 设计的核心场景
  // halt reason 字面量只在 run-plans.js（runtime 字符串）；lib.js 只含 haltLikelySource 正则
  assert.match(runSrc, /simplify diff check failed/, 'run-plans.js 须含 simplify diff check failed halt reason')
  assert.match(runSrc, /simplify amend failed/, 'run-plans.js 须含 simplify amend failed halt reason')
  assert.match(runSrc, /simplify checkout failed/, 'run-plans.js 须含 simplify checkout failed halt reason')
  assert.match(runSrc, /commit out_of_scope/, 'run-plans.js 须含 commit out_of_scope halt reason')
  // haltLikelySource 正则须含新 reason（不能 fall through 到 unknown）—— 两副本字节一致，查 lib.js 即可
  const libHalt = extractFunctionBody(libSrc, 'haltLikelySource')
  assert.match(libHalt, /simplify \(diff check\|amend\|checkout\) failed/, 'haltLikelySource 正则须匹配 simplify diff/amend/checkout failed')
  assert.match(libHalt, /commit out_of_scope/, 'haltLikelySource 正则须匹配 commit out_of_scope')
})

// ===== 第 4 轮 review 修复断言（S1-S4 spec + Q1-Q11 quality）=====

test('S1: orchestrator 不得用 new Date()（§4.3 无 Date.now/Math.random 硬约束）', () => {
  // S1: get-ts agent 失败时 fallback 用 new Date().toISOString() 违反 §4.3 硬约束
  //   orchestrator 是 JS sandbox：无 fs、无 subprocess、无 Date.now/Math.random
  //   修：fallback 用占位符 'unknown-ts'（manifest 仍可写，run_ts 缺失不阻塞）
  assert.doesNotMatch(runSrc, /new Date\(\)/, 'run-plans.js 不得用 new Date()（§4.3 硬约束——ts 由 subagent 写入）')
})

test('S2: manifest 须写入 runs/<run-id>/ 子目录（非 runs/ 根目录）', () => {
  // S2: runsDir='runs' → manifest 写 runs/manifest.json，破坏 bootstrap 的 runs/*/manifest.json glob
  //   修：runsDir 改为 `runs/${state.runTs}`（runTs 即 run-id），bootstrap glob 可匹配
  assert.match(runSrc, /runsDir:\s*`runs\/\$\{state\.runTs\}`/, 'runsDir 须为 runs/<runTs>（runTs 作 run-id，bootstrap glob 可匹配）')
  assert.doesNotMatch(runSrc, /runsDir:\s*'runs'/, "不得用裸 'runs' 作 runsDir（破坏 runs/*/manifest.json glob）")
})

test('S3: blocked.md 须写入 .workflow/blocked.md（非 runs/blocked.md）', () => {
  // S3: spec §8.2 规定 blocked.md 写 .workflow/blocked.md，代码写 runs/blocked.md（runsDir='runs'）
  //   修：finalReport prompt 中 blocked.md 路径改为 .workflow/blocked.md（独立于 runsDir）
  for (const src of [libSrc, runSrc]) {
    const p = promptBody(src, 'finalReport')
    assert.match(p, /\.workflow\/blocked\.md/, 'finalReport prompt 须写 .workflow/blocked.md（§8.2，非 runs/blocked.md）')
  }
})

test('S4: commit prompt destructive 检测须用 git diff HEAD（非 git diff --cached）', () => {
  // S4: commit prompt step 2.6 用 git diff --cached --numstat，但文件未 git add → 永远为空
  //   → destructive review 永不触发。修：改用 git diff HEAD（无需暂存即可对比工作树与 HEAD）
  for (const src of [libSrc, runSrc]) {
    const p = promptBody(src, 'commit')
    // 不得用 git diff --cached（文件未 git add 时永远为空）
    assert.doesNotMatch(p, /git diff --cached --numstat/, 'commit prompt 不得用 git diff --cached（文件未 git add → 永远为空，S4）')
    // 须用 git diff HEAD（对比工作树与 HEAD，无需暂存）
    assert.match(p, /git diff HEAD --numstat/, 'commit prompt 须用 git diff HEAD --numstat（对比工作树与 HEAD，S4）')
  }
})

test('Q1（第 4 轮）: dispatchImpl retry 路径须检查 model_unavailable status', () => {
  // Q1: retry 返回前只检查 impl != null，未检查 status === 'model_unavailable'
  //   若 retryModel 也限额耗尽 → 返回 {status:'model_unavailable'} → 调用方访问 impl.evidence crash
  //   修：retry 路径加 status 检查（与首次调用一致）
  assert.match(runSrc, /if \(impl\?\.status === 'model_unavailable'\) return \{ halted: true, reason: 'model_unavailable'/, 'dispatchImpl retry 路径须检查 model_unavailable status（Q1）')
})

test('Q2（第 4 轮）: perTask 须用 plan-scoped key（非 bare task.id）', () => {
  // Q2: state.perTask[task.id] 用裸 task.id（如 'T1'），跨 plan 同名 task 覆盖
  //   修：perTask key 改为 plan-scoped（与 state.completed 一致）
  assert.doesNotMatch(runSrc, /state\.perTask\[task\.id\]/, 'perTask 不得用 bare task.id 作 key（Q2：跨 plan 覆盖）')
  assert.match(runSrc, /state\.perTask\[taskKey\]/, 'perTask 须用 plan-scoped taskKey（Q2）')
})

test('Q4（第 4 轮）: halt() 须初始化 perTask 默认字段（防 manifest 字段缺失）', () => {
  // Q4: halt() 在 tid='unknown' 时 spread 空对象 → perTask 只有 {status, blocked_info}
  //   修：halt() 开头初始化默认字段（与 runTask 初始化一致）
  assert.match(runSrc, /function ensurePerTaskDefaults|function initPerTask/, '须有 perTask 默认字段初始化 helper（Q4）')
})

test('Q5（第 4 轮）: 状态机注释 simplify 路径须含 review_failed_no_findings', () => {
  // Q5: simplify 路径注释缺 review_failed_no_findings halt reason
  //   修：注释补充（与主 review 路径一致）
  //   注释是 run-plans.js 顶部 ASCII 图，检查 simplify 路径行
  const simpCommentLine = runSrc.match(/└─空响应\/异常──→ halt[^]*?review_empty/)
  assert.ok(simpCommentLine, '须有 simplify 路径状态机注释')
  assert.match(simpCommentLine[0], /review_failed_no_findings/, 'simplify 路径注释须含 review_failed_no_findings（Q5）')
})

test('Q8（第 4 轮）: diff subagent 须校验 files 数组（changed=true 时 files 须为 array）', () => {
  // Q8: diff subagent 只校验 changed 字段，files=undefined 时静默降级为空数组
  //   修：changed=true 时 files 须为 array，否则 halt
  assert.match(runSrc, /diffResult\.changed === true && !Array\.isArray\(diffResult\.files\)/, 'diff subagent 须校验 changed=true 时 files 为 array（Q8）')
})

test('Q10（第 4 轮）: fix-round implementor done_with_concerns 须更新 concerns', () => {
  // Q10: fix-round implementor 返回 done_with_concerns 时 concerns 被丢弃，concernsHint 全程不变
  //   修：加 done_with_concerns 分支，更新 concerns + concernsHint
  assert.match(runSrc, /if \(impl\.status === 'done_with_concerns'\)[\s\S]*?concerns = impl\.diagnostics/, 'fix-round 须有 done_with_concerns 分支更新 concerns（Q10）')
})
