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

const ROLES = ['bootstrap', 'implementor', 'specReview', 'qualityReviewer', 'hunter', 'simplify', 'commit', 'contextFetcher', 'gate', 'headVerifier', 'finalReport', 'lessonDistiller']

for (const role of ROLES) {
  test(`PROMPTS.${role} identical between lib.js and run-plans.js`, () => {
    assert.equal(promptBody(runSrc, role), promptBody(libSrc, role),
      `PROMPTS.${role} drifted — lib.js 改了 prompt 必须同步 run-plans.js（文件头注释已声明）`)
  })
}

test('run-plans.js inlines the new conditional-render helpers', () => {
  // QC-3: formatLessonsForDistill / applyLessonDecisions / renderLessonEntry 不再 inline
  // （SH2 后 distiller 自读写盘，orchestrator 不调用这些函数）。lib.js 真源保留。
  for (const fn of ['formatBulletSection', 'formatReferencePaths', 'formatSilentFailureContext', 'formatFailedApproaches', 'formatLessons', 'formatUniversalLessons', 'formatDomainLessons', 'formatWriteFilesScope', 'formatSchemaCheck', 'languageChecklist', 'LANGUAGE_CHECKLISTS', 'gateCommands', 'collectReviewFindings', 'formatFindings', 'formatFindingItem', 'matchesPlanFilter', 'classifyThrown', 'checkImplStatus', 'makeHalt', 'reviewHaltReason', 'reviewHaltForEmptyFailed', 'haltLikelySource', 'fixModelForRound', 'resolveMaxRounds', 'resolveLessonsAutoDistill', 'distillLessonInput', 'summarizeReviewRound', 'groupFindingsByFile', 'formatCrossReviewerNote', 'updateFindingsHistory', 'formatFindingsHistory', 'hasRegressed', 'resolveReviewBudget', 'recordReviewRound', 'decideReviewOutcome']) {
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
    'reviewHaltForEmptyFailed', 'detectOscillation', 'classifyThrown', 'checkImplStatus', 'makeHalt',
    // Q7 新增：影响 halt 路由 / commit 识别 / plan 过滤 / 反馈聚合 / distiller 输入
    'isQuotaError', 'commitSubject', 'normalizeCompleted', 'matchesPlanFilter',
    'collectReviewFindings', 'summarizeReviewRound', 'formatFindings', 'formatFindingItem',
    'resolveLessonsAutoDistill', 'distillLessonInput',
    // v3 lessons 两层注入 (2026-07-06): universal silent-failure + domain category
    'formatUniversalLessons', 'formatDomainLessons',
    // S5 (2026-07-07): 通用 bullet section 渲染，5 简单 + 1 复杂 wrapper 复用
    'formatBulletSection', 'formatReferencePaths', 'formatSilentFailureContext', 'formatFailedApproaches', 'formatLessons',
    // Q8 新增：方案 C subagent 返回值校验纯函数（边界条件可 node:test 行为测试）
    'validateAmendResult', 'validateCheckoutResult',
    // Q9 新增：collectReviewFindings / summarizeReviewRound 的内部 helper（inline 复制但未被字节比较守护）
    'findingsOf', 'summarizeFinding',
    // cross-reviewer surfacing (2026-07-05)
    'groupFindingsByFile', 'formatCrossReviewerNote',
    // bootstrap task_id sanitize (2026-07-05): 防 plan-scoped task_id → 双重前缀 + 重跑
    'bareTaskId',
    // S10 (2026-07-07): 统一 task-key 构造，防 padStart 位数不一致
    'taskKey',
    // bootstrap leaf-guard (2026-07-05): 过滤非叶子父 task（T6+T6b 共存 → drop T6）
    'dropParentTasks',
    // bootstrap deterministic-completed (2026-07-05): git log subjects → completed 正则提取（不依赖 LLM）
    'extractCompletedFromSubjects',
    // OSCILLATING opus 升级 + flip-flop 区分 (2026-07-05): 改进 1+2
    'shouldEscalateOnOscillation', 'isFlipFlop',
    // v3 findings 状态机 + OSC budget (2026-07-06): E'
    'updateFindingsHistory', 'formatFindingsHistory', 'hasRegressed', 'resolveReviewBudget',
    // S2 (2026-07-07): review 循环每轮 state 更新（review_rounds/files_touched/review_history/findings_history）
    'recordReviewRound',
    // S2 (2026-07-07): review 循环 10-action 决策（6 halt 子类 + break/escalate/continue/fix）
    'decideReviewOutcome',
    // W1-5b 路径归一 (2026-07-07): 防 reviewer 返回不同路径格式致 cross-reviewer 重叠检测漏报
    'normalizeFilePath', 'unionFiles',
  ]
  for (const fn of fns) {
    const libBody = extractFunctionBody(libSrc, fn)
    const runBody = extractFunctionBody(runSrc, fn)
    assert.ok(libBody, `lib.js 中找不到函数 ${fn}`)
    assert.ok(runBody, `run-plans.js 中找不到函数 ${fn}`)
    assert.equal(runBody, libBody, `helper ${fn} 函数体字节不一致——lib.js 改了必须同步 run-plans.js inline 副本`)
  }
})

test('QC-4b: REVIEW_SOURCES 常量 lib.js ↔ run-plans.js 字节一致（S4 reviewer 三元组单一真源）', () => {
  // S4 (2026-07-07): REVIEW_SOURCES 是 const 数组（非 function），extractFunctionBody 不适用，
  // 故单独提取整块字面量做字节比较。lib.js 为 export const，run-plans.js 为 const，
  // 去掉前导 export 后须字节一致——否则 collectReviewFindings/reviewHaltForEmptyFailed/
  // summarizeReviewRound 三函数虽函数体一致，但所迭代的常量漂移仍会致行为发散。
  function extractReviewSources(src) {
    const m = src.match(/(?:export\s+)?const REVIEW_SOURCES = \[[\s\S]*?\n\]/)
    assert.ok(m, '须含 REVIEW_SOURCES 常量定义')
    return m[0].replace(/^export\s+/, '')
  }
  const libRS = extractReviewSources(libSrc)
  const runRS = extractReviewSources(runSrc)
  assert.equal(runRS, libRS, 'REVIEW_SOURCES 常量字节不一致——lib.js 改了必须同步 run-plans.js inline 副本')
  // 存在性 + shape 守护（与 helpers.test.js 的行为测试互补，此处在源码字面量层守护）
  assert.match(libRS, /\{ name: 'spec', key: 'issues' \}/, 'REVIEW_SOURCES 须含 spec/issues 三元组')
  assert.match(libRS, /\{ name: 'quality', key: 'issues' \}/, 'REVIEW_SOURCES 须含 quality/issues 三元组')
  assert.match(libRS, /\{ name: 'hunter', key: 'silent_failures' \}/, 'REVIEW_SOURCES 须含 hunter/silent_failures 三元组')
})

test('QC-4b AUDIT: AUDIT_DIRECTIVE + AUDIT_REFACTOR_KEYWORDS 两端字节一致', () => {
  // Task 1 (2026-07-08): 两常量 lib.js ↔ run-plans.js 字节一致守护。
  // AUDIT_DIRECTIVE 是 template literal（` 开头到首个 ` 闭合，无内嵌反引号）；
  // AUDIT_REFACTOR_KEYWORDS 是 RegExp literal（/ 开头到行尾 /flags 闭合）。
  // 提取按值定界：template literal 用 `[\s\S]*?` 非贪婪到首个闭合 `，
  // RegExp literal 用 /[^\n]*/[a-z]* 匹配行内正则字面量。
  // 非贪婪 `([\\s\\S]*?)` 配合终止定界符确保只捕获值，不含后续注释（lib.js/run-plans.js 注释风格不同）。
  function extractAuditConst(src, name, withExport) {
    const prefix = withExport ? 'export ' : ''
    const re = new RegExp(`${prefix}const ${name} = ((?:\`[\\s\\S]*?\`)|(?:/[^\\n]*/[a-z]*))`)
    const m = src.match(re)
    return m ? m[1] : null
  }
  for (const name of ['AUDIT_DIRECTIVE', 'AUDIT_REFACTOR_KEYWORDS']) {
    const libVal = extractAuditConst(libSrc, name, true)
    const runVal = extractAuditConst(runSrc, name, false)
    assert.ok(libVal, `lib.js 须含 ${name}`)
    assert.ok(runVal, `run-plans.js 须含 ${name} inline`)
    assert.equal(libVal, runVal, `${name} 字节一致——lib.js 改了必须同步 run-plans.js inline 副本`)
  }
})

test('run-plans.js SCHEMAS mirror key changes', () => {
  // implementor enum 含 done_with_concerns；diagnostics 含 concerns
  assert.match(runSrc, /'done_with_concerns'/)
  assert.match(runSrc, /concerns:\s*\{\s*type:\s*'array'\s*\}/)
  // gate evidence required 含 lint_results + restored_head（P1-1）
  assert.match(runSrc, /required:\s*\['tests_exit_code',\s*'pytest_summary',\s*'lint_results',\s*'restored_head'\]/)
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
  // S2 (2026-07-07): allGreen break + detectOscillation 均抽进 decideReviewOutcome 函数体，
  //   顺序不变量改由 QC-4 字节守护函数体（lib.js ↔ run-plans.js 一致）+ 此处守护函数体内顺序。
  const fnBody = extractFunctionBody(runSrc, 'decideReviewOutcome')
  assert.ok(fnBody, 'run-plans.js 须有 decideReviewOutcome 函数体（QC-4 守护与 lib.js 一致）')
  const allGreenIdx = fnBody.indexOf('if (allGreen(spec, qual, hunt))')
  const oscIdx = fnBody.indexOf('const osc = detectOscillation')
  assert.notEqual(allGreenIdx, -1, 'decideReviewOutcome 体内须有 allGreen break')
  assert.notEqual(oscIdx, -1, 'decideReviewOutcome 体内须有 detectOscillation 调用')
  assert.ok(allGreenIdx < oscIdx, 'allGreen break 必须在 detectOscillation 之前（收敛误报根治）')
})

test('v3 runtime wiring: findings_history update + taskCategories declared + OSC regressed branch', () => {
  // findings_history 在 review loop 内更新，且必须在 halt 检查之前（同 Q15 review_history 顺序）
  // S2 (2026-07-07): review_history.push + findings_history 更新均抽进 recordReviewRound（参数 taskKey），
  //   顺序不变量（push→findings→halt）改由 QC-4 字节守护函数体 + 此处守护调用位置。
  // S2 decideReviewOutcome (2026-07-07): reviewReason/emptyFailed halt 检查抽进 decideReviewOutcome，
  //   顺序不变量改为「recordReviewRound 调用必须在 decideReviewOutcome 调用之前」（halt 轮状态须持久化）。
  const recordIdx = runSrc.indexOf('recordReviewRound(state, tk, round, spec, qual, hunt)')
  const decideIdx = runSrc.indexOf('decideReviewOutcome(state, tk, round, spec, qual, hunt, model, maxRounds, cfg, reviewReason, emptyFailedReason)')
  assert.notEqual(recordIdx, -1, 'run-plans.js 须有 recordReviewRound 调用（S2 抽取后含 push/findings）')
  assert.notEqual(decideIdx, -1, 'run-plans.js 须有 decideReviewOutcome 调用（S2 抽取后含 reviewReason/emptyFailed halt）')
  assert.ok(recordIdx < decideIdx, 'recordReviewRound 须在 decideReviewOutcome 之前（findings_history 更新顺序，halt 轮状态须持久化）')

  // taskCategories 必须在 formatDomainLessons 调用前定义（C1 级阻断：未定义 → ReferenceError）
  const taskCategoriesDeclIdx = runSrc.indexOf("const taskCategories = task.lesson_categories")
  const formatDomainLessonsCallIdx = runSrc.indexOf('formatDomainLessons(state.allLessons || [], taskCategories')
  assert.notEqual(taskCategoriesDeclIdx, -1, 'run-plans.js 必须声明 taskCategories 变量')
  assert.notEqual(formatDomainLessonsCallIdx, -1, 'run-plans.js 必须用 taskCategories 调 formatDomainLessons')
  assert.ok(taskCategoriesDeclIdx < formatDomainLessonsCallIdx, 'taskCategories 声明必须在 formatDomainLessons 调用之前')

  // 独立 regressed halt 分支必须存在
  assert.match(runSrc, /if \(regressed\)/, 'run-plans.js 须有独立的 hasRegressed halt 分支')
  assert.match(runSrc, /regressedFindings/, 'run-plans.js halt diag 须含 regressedFindings')

  // review_not_converging halt reason（budget guard）必须存在
  assert.match(runSrc, /reason: 'review_not_converging'/, 'run-plans.js 须有 review_not_converging halt reason')

  // fix-round 必须调用 formatFindingsHistory（D1 history 主导单源）
  // S2/Task 14 (2026-07-07): fix-round 逻辑抽进 runFixRound(taskKey, ...)，参数名 taskKey
  //   （调用方传 loop 局部 tk 作 taskKey 实参，访问同一 perTask 条目）。
  assert.match(runSrc, /formatFindingsHistory\(state\.perTask\[taskKey\]\.findings_history \|\| \[\], round\)/, 'fix round 须用 formatFindingsHistory 注入历史（Task 14 抽进 runFixRound，参数名 taskKey；调用方传 tk 实参）')

  // H2: review max rounds halt diag 须含 findings_history（接手判断）
  assert.match(runSrc, /reason: 'review max rounds'[\s\S]{0,200}findings_history/, 'review max rounds halt diag 须含 findings_history')

  // H3: finalReport prompt per_task 清单须含 opus_escalated（防 manifest strip 导致重复升级）
  const finalReportPerTaskIdx = libSrc.indexOf('per_task:{<taskKey>:{planId,status,model,review_rounds,files_touched_per_round,review_history,findings_history,oscillation_escalated_at_round,opus_escalated')
  assert.notEqual(finalReportPerTaskIdx, -1, 'lib.js finalReport prompt per_task 清单须含 opus_escalated（LOW-3 补 planId 于清单首位）')

  // H4: formatUniversalLessons inline 副本须与 lib.js 字节一致（容错 silent-failure 变体）
  const libUniversalFn = extractFunctionBody(libSrc, 'function formatUniversalLessons')
  const runUniversalFn = extractFunctionBody(runSrc, 'function formatUniversalLessons')
  assert.equal(libUniversalFn, runUniversalFn, 'formatUniversalLessons inline 副本与 lib.js 不一致（H4 category 容错）')
})

test('W1 移植: implementor Discipline + Task Scope Boundary + Lessons Learned Exemption + 分类 dirty_tree', () => {
  // W1-2 (2026-07-07): implementor prompt 须有 Discipline 段落禁 git commit/add
  assert.match(runSrc, /## Discipline \(HARD REQUIREMENTS/, 'implementor prompt 须有 Discipline 段落（W1-2）')
  assert.match(runSrc, /DO NOT run \\`git commit\\` or \\`git add\\`/, 'implementor Discipline 须禁 git commit/add（W1-2）')

  // W1-5a (2026-07-07): specReview prompt 须有 Task Scope Boundary 段落
  assert.match(runSrc, /## Task Scope Boundary \(critical for multi-task plans\)/,
    'specReview prompt 须有 Task Scope Boundary 段落（W1-5a）')
  assert.match(runSrc, /FUTURE tasks.*are NOT missing.*Do not flag them as MISSING/,
    'Task Scope Boundary 须明确 future task 不算 MISSING')

  // W1-5e (2026-07-07): specReview + qualityReviewer 须有 Lessons Learned Exemption 段落 + lessonsPath 注入
  assert.match(runSrc, /## Lessons Learned Exemption \(防 reviewer ↔ implementor 振荡\)/,
    'specReview prompt 须有 Lessons Learned Exemption 段落（W1-5e）')
  assert.match(runSrc, /## Lessons Learned Exemption \(限定维度硬性豁免/,
    'qualityReviewer prompt 须有限定维度豁免段落（W1-5e）')
  assert.match(runSrc, /不豁免的维度.*命名清晰度.*类型注解.*错误处理/,
    'qualityReviewer 须列出不豁免维度（W1-5e）')
  // runReviewRound 的 specReview/qualityReviewer buildPrompt 调用须传 lessonsPath
  assert.match(runSrc, /buildPrompt\('specReview'[^]*lessonsPath: cfg\.lessons_path/,
    'specReview buildPrompt 须传 lessonsPath（W1-5e）')
  assert.match(runSrc, /buildPrompt\('qualityReviewer'[^]*lessonsPath: cfg\.lessons_path/,
    'qualityReviewer buildPrompt 须传 lessonsPath（W1-5e）')

  // W1-1/W1-4 (2026-07-07): bootstrap step 5 须分类处理 dirty_tree（非一律 reset --hard）
  assert.match(runSrc, /classify and handle each change/,
    'bootstrap step 5 须分类处理 dirty_tree（W1-1/W1-4）')
  assert.match(runSrc, /auto-commit lessons\.md from interrupted run/,
    'bootstrap 须 auto-commit lessons.md（W1-1）')
  assert.match(runSrc, /git checkout -- runs\/ \.workflow\//,
    'bootstrap 须 discard runs/ + .workflow/（W1-1）')

  // W1-1 (2026-07-07): finalReport 须有 step 6 commit lessons.md（halt 后）
  assert.match(runSrc, /Commit lessons\.md \(W1-1, 2026-07-07\)/,
    'finalReport 须有 step 6 commit lessons.md（W1-1）')
  assert.match(runSrc, /auto-commit lessons\.md from run \{\{runTs\}\}/,
    'finalReport commit message 须含 runTs（W1-1）')
})

test('W1 修复: Q-F1 Exemption 编号格式 + Q-F2 implementor 标编号 + H-F1 dirty_tree halt + H-F2 commit <path> + H-F3 空 lessonsPath 防御', () => {
  // Q-F1 (2026-07-07): Exemption 段落编号格式须用 L-<timestamp>（与 lessonDistiller 的 ## L-<ts> 一致），
  //   非 L-YYYYMMDD-NN（OTC 仓库格式，本仓库 lessonDistiller 不用此格式）
  assert.doesNotMatch(runSrc, /L-YYYYMMDD-NN/,
    'Exemption 段落不得用 L-YYYYMMDD-NN 格式（Q-F1：与本仓库 lessonDistiller 的 L-<ts> 不一致）')
  assert.match(runSrc, /L-<timestamp>|L-\d{8}T\d{6}Z/,
    'Exemption 段落须用 L-<timestamp> 格式或示例（Q-F1：与 lessonDistiller 一致）')

  // Q-F2 (2026-07-07): implementor prompt 须要求加固时标 L-xxx 编号
  //   否则 reviewer 找不到编号 → Exemption 永远不触发 → 防振荡闭环失效
  assert.match(runSrc, /lesson.*id|L-<timestamp>|标.*编号|reference.*lesson/i,
    'implementor prompt 须有标 L-xxx 编号指引（Q-F2：Exemption 闭环配套输入）')

  // H-F1 (2026-07-07): orchestrator bootstrap 返回后须检查 dirty_tree，残留 true 则 halt
  //   否则脏工作树上跑 implementor → commit 混入残留改动
  assert.match(runSrc, /boot\.evidence\.dirty_tree|dirty_tree.*halt/i,
    'orchestrator bootstrap 后须检查 dirty_tree（H-F1：残留 true 则 halt）')

  // H-F2 (2026-07-07): bootstrap step 5a 须用 git commit <path>（非 git add && git commit）
  //   防 add 成功 commit 失败后 5b reset --hard 清除 staging → lessons.md 丢失
  assert.doesNotMatch(runSrc, /git add.*&&.*git commit.*lessons/i,
    'bootstrap step 5a 不得用 git add && git commit（H-F2：staging 被 reset 清除）')
  assert.match(runSrc, /git commit.*lessons|git commit.*<lessonsPath>/i,
    'bootstrap step 5a 须用 git commit <path>（H-F2：一步到位不预 staged）')

  // H-F3 (2026-07-07): finalReport step 6 须有空 lessonsPath 防御
  //   空串 → git status --porcelain 查全工作树 → 误 commit 全工作树
  assert.match(runSrc, /empty.*skip|lessonsPath.*empty|若.*lessonsPath.*为空|skip this step/i,
    'finalReport step 6 须有空 lessonsPath 防御（H-F3：空则跳过）')

  // H-F4 (2026-07-07): reviewer Exemption 须有空 lessonsPath 防御
  //   空串 → reviewer 读空路径文件不存在 → Exemption 永远 fall through 到"仍按 EXTRA 报告"
  assert.match(runSrc, /empty.*N\/A|lessonsPath.*empty.*skip|若.*lessonsPath.*为空.*跳过/i,
    'reviewer Exemption 须有空 lessonsPath 防御（H-F4：空则 Exemption N/A）')

  // H-F7 (2026-07-07): finalReport manifest 须含 lessons_committed 字段
  //   否则下次 bootstrap 无从知晓 lessons.md 是否真被 commit → 静默退化
  assert.match(runSrc, /lessons_committed/i,
    'finalReport manifest 须含 lessons_committed 字段（H-F7：供下次 bootstrap 检查）')
})

test('S3: lessonsText must not call formatLessons (replaced by Tier 1 + Tier 2)', () => {
  // S3 (2026-07-06): spec §5.5 改进 B 说"formatLessons 拆成 formatUniversalLessons + formatDomainLessons"（替换语义）。
  //   保留旧 formatLessons 调用会导致 keyword 匹配重复注入——
  //   ① formatLessons(taskLessons[taskKey]) 用 task title keyword 匹配 lessons.md 子集；
  //   ③ formatDomainLessons(allLessons, [], ..., task.title) fallback 也用 task title keyword 匹配 allLessons（排除 silent-failure）。
  //   两者输入同源（lessons.md），匹配逻辑同（task title tokens），仅排除集不同 → 非 silent-failure 且 keyword 重叠的 lesson 被注入两次。
  //   移除 ① formatLessons 调用，只保留 Tier 1 + Tier 2（Tier 2 的 title keyword fallback 已覆盖 legacy 无 category 场景）。
  const lessonsTextIdx = runSrc.indexOf('const lessonsText =')
  assert.notEqual(lessonsTextIdx, -1, 'run-plans.js 须有 lessonsText 构造')
  // 截取 lessonsText 构造行（到行尾），断言不含 formatLessons( 调用
  const lineEnd = runSrc.indexOf('\n', lessonsTextIdx)
  const lessonsTextLine = runSrc.slice(lessonsTextIdx, lineEnd === -1 ? undefined : lineEnd)
  assert.doesNotMatch(lessonsTextLine, /formatLessons\(/, 'lessonsText 不得调用 formatLessons（S3: 替换为 Tier 1 + Tier 2，防 keyword 重复注入）')
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
  assert.match(runSrc, /silentFailureContext: formatSilentFailureContext\(cfg\.silent_failure_context,\s*cfg\.silent_failure_intro\)/, 'hunter 须注入 silentFailureContext（含 intro 配置）')
  assert.match(runSrc, /failedApproaches: formatFailedApproaches/, 'implCtx 须注入 failedApproaches')
  assert.match(runSrc, /failed_approaches/, 'SCHEMAS.bootstrap 须含 failed_approaches')
  assert.match(runSrc, /failed_approach:/, 'halt() 须在 blocked_info 中记录 failed_approach')
  assert.match(runSrc, /writeFilesScope: formatWriteFilesScope/, 'commitCtx 须注入 writeFilesScope')
  assert.match(runSrc, /task_write_files/, 'SCHEMAS.bootstrap 须含 task_write_files')
  assert.match(runSrc, /out_of_scope/, 'SCHEMAS.commit 须含 out_of_scope')
  assert.match(runSrc, /destructive_changes/, 'SCHEMAS.commit 须含 destructive_changes')
  assert.match(runSrc, /destructive_review_failed/, 'orchestrator 须检测 destructive_changes 并记录结果')
  assert.match(runSrc, /lessons: formatLessons|lessons: lessonsText/, 'implCtx 须注入 lessons')
  assert.match(runSrc, /task_lessons/, 'SCHEMAS.bootstrap 须含 task_lessons')
  assert.match(runSrc, /all_lessons/, 'SCHEMAS.bootstrap 须含 all_lessons')
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

// REMOVED: old sync guard that forbid any mention of 'budget' in run-plans.js.
// v3 legitimately uses review_budget config + resolveReviewBudget + diag.budget.
// The original guard was a historical artifact from when 'budget' was a misleading comment reference.

test('review_history 存档：每轮 findings 摘要进 manifest（OSCILLATING halt 后可定位振荡点）', () => {
  // T3 OSCILLATING halt 暴露：review_rounds 只存 int 计数，findings 不持久化 →
  // halt 后无法精确还原哪个 reviewer 在哪点 flip-flop，需考古推断。review_history 修复。
  // summarizeReviewRound 纯函数两副本一致（lib.js 真源 + run-plans.js inline）
  assert.match(libSrc, /export function summarizeReviewRound/, 'lib.js 须有 summarizeReviewRound 真源')
  assert.match(runSrc, /function summarizeReviewRound/, 'run-plans.js 须 inline summarizeReviewRound')
  // perTask 初始化含 review_history: []
  assert.match(runSrc, /review_history:\s*\[\]/, 'perTask 初始化须含 review_history: []')
  // review 循环每轮 push 摘要（须在 allGreen break 之前，确保 halt 轮/收敛轮也被记录）
  // S2 (2026-07-07): push 抽进 recordReviewRound，allGreen break 抽进 decideReviewOutcome；
  //   顺序不变量改为「recordReviewRound 调用必须在 decideReviewOutcome 调用之前」
  //   （push 在 recordReviewRound 体内，allGreen break 在 decideReviewOutcome 体内，QC-4 字节守护）。
  const pushIdx = runSrc.indexOf('review_history.push(summarizeReviewRound(round, spec, qual, hunt))')
  const recordCallIdx = runSrc.indexOf('recordReviewRound(state, tk, round, spec, qual, hunt)')
  const decideCallIdx = runSrc.indexOf('decideReviewOutcome(state, tk, round, spec, qual, hunt, model, maxRounds, cfg, reviewReason, emptyFailedReason)')
  assert.notEqual(pushIdx, -1, 'recordReviewRound 体内须 push summarizeReviewRound')
  assert.notEqual(recordCallIdx, -1, 'review 循环须调用 recordReviewRound')
  assert.notEqual(decideCallIdx, -1, 'review 循环须调用 decideReviewOutcome（含 allGreen break）')
  assert.ok(recordCallIdx < decideCallIdx, 'recordReviewRound 调用必须在 decideReviewOutcome 之前（halt/收敛轮也要被记）')
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
  assert.doesNotMatch(runSrc, /committed_files.*\.join\(','\)/, 'destructive 文件列表不得用 join(",")（Q3）')
  assert.match(runSrc, /filesChanged\.join\('\\n'\)/, '主轮文件列表须用 join("\\n") 换行分隔')
  // Task 16: simpFiles 已抽入 checkSimplifyChanges helper（返回 files 数组），调用点用 diffCheck.files.join('\n')
  assert.match(runSrc, /diffCheck\.files\.join\('\\n'\)/, 'simplify 文件列表须用 join("\\n") 换行分隔（Task 16 后由 checkSimplifyChanges 返回 files，调用点 join）')
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
  // haltLikelySource 须含新 reason（不能 fall through 到 unknown）—— 两副本字节一致，查 lib.js 即可
  // P1-8（第 6 轮）: 改显式 Set 映射后，检查 reason 字面量存在（非大正则）
  const libHalt = extractFunctionBody(libSrc, 'haltLikelySource')
  assert.match(libHalt, /'simplify diff check failed'/, 'haltLikelySource 须含 simplify diff check failed（显式映射）')
  assert.match(libHalt, /'simplify amend failed'/, 'haltLikelySource 须含 simplify amend failed（显式映射）')
  assert.match(libHalt, /'simplify checkout failed'/, 'haltLikelySource 须含 simplify checkout failed（显式映射）')
  assert.match(libHalt, /'commit out_of_scope'/, 'haltLikelySource 须含 commit out_of_scope（显式映射）')
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
  assert.match(runSrc, /state\.perTask\[tk\]/, 'perTask 须用 plan-scoped key（Q2；S10 后局部变量 tk 经 taskKey() 构造）')
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

// ===== 第 5 轮 review 修复断言（S1-S7 spec + Q1-Q15 quality，去重 15 项）=====
// wontfix: S4（hunter agentType——runtime 限制）/ Q10（runTask 拆分——风险高）/
//   Q12（blocked 升级链抽象——参数过多）/ Q16（dead code——spec §393 明确保留）

test('S1（第 5 轮）: failedApproaches 查找须用 plan-scoped taskKey（非裸 task.id）', () => {
  // S1: state.failedApproaches 存储键来自 manifest per_task（plan-scoped，如 plan-01/T2），
  //   implCtx 查找用裸 task.id（T2）→ 永远 undefined → failedApproaches 占位符不注入 implementor prompt
  //   修：implCtx 查找改用 taskKey（与 halt() line 868 的 state.failedApproaches[tid] 一致）
  assert.doesNotMatch(runSrc, /state\.failedApproaches\?\.\[task\.id\]/, 'failedApproaches 不得用裸 task.id 查找（S1：存储键 plan-scoped）')
  assert.match(runSrc, /state\.failedApproaches\?\.\[tk\]/, 'failedApproaches 须用 plan-scoped key 查找（S1；S10 后局部变量 tk 经 taskKey() 构造）')
})

test('S3（第 5 轮）: taskWriteFiles / taskLessons 须用 plan-scoped key（非裸 task.id）', () => {
  // S3: bootstrap 遍历所有 plan 收集 task_write_files/task_lessons，每个 plan 都有 T1-T10
  //   → 后一个 plan 覆盖前一个 plan 同名 task 条目。修：存储和查找都用 plan-scoped key
  assert.doesNotMatch(runSrc, /state\.taskWriteFiles\?\.\[task\.id\]/, 'taskWriteFiles 查找不得用裸 task.id（S3：跨 plan 覆盖）')
  assert.doesNotMatch(runSrc, /state\.taskLessons\?\.\[task\.id\]/, 'taskLessons 查找不得用裸 task.id（S3：跨 plan 覆盖）')
  assert.match(runSrc, /state\.taskWriteFiles\?\.\[tk\]/, 'taskWriteFiles 须用 plan-scoped key 查找（S3；S10 后局部变量 tk 经 taskKey() 构造）')
  // S3 修复（2026-07-06）：taskLessons 不再被 implCtx 查找（formatLessons 调用已移除，由 Tier 1+Tier 2 替代）。
  //   存储仍用 plan-scoped key（bootstrap 填充 state.taskLessons，向后兼容 + 无害）。
  // 存储时也须用 plan-scoped key（bootstrap 返回的 task_id 须归一化为 plan-{seq}/T{id}）
  assert.match(runSrc, /taskWriteFiles\[taskKey\(twf\.plan_seq,\s*twf\.task_id\)\]/, 'taskWriteFiles 存储须用 plan-scoped key（S3，S10 后经 taskKey）')
  assert.match(runSrc, /taskLessons\[taskKey\(tl\.plan_seq,\s*tl\.task_id\)\]/, 'taskLessons 存储须用 plan-scoped key（S3，S10 后经 taskKey）')
})

test('S2（第 5 轮）: get-ts agent 返回非 string 须降级 unknown-ts（防 runsDir="runs/null"）', () => {
  // S2: agent() 返回 null → String(null)="null" → runsDir="runs/null" → manifest 互相覆盖
  //   Q9 扩展：非 string 非 null（对象/数字）→ String({})="[object Object]" 同源问题
  //   修：非 string 或空串均降级为 'unknown-ts' 占位符
  assert.doesNotMatch(runSrc, /String\(tsAgent\)/, '不得用 String(tsAgent) 转换（S2：null→"null"，对象→"[object Object]"）')
  assert.match(runSrc, /typeof tsAgent !== 'string' \|\| !tsAgent\.trim\(\)/, 'get-ts 须校验非 string 或空串（S2）')
  assert.match(runSrc, /tsAgent = 'unknown-ts'/, '非 string 须降级为 unknown-ts 占位符（S2）')
})

test('S5（第 5 轮）: distiller 须用单次 agent 调用（非 agentWithFallback）', () => {
  // S5: spec §2.4 fallback 链 [opus,sonnet,haiku] 仅用于 halt/finalReport 保存进度
  //   distiller 是 lesson 提炼通道，非进度保存；spec §5.4 说 distiller 失败 best-effort 跳过
  //   修：distiller 改用单次 agent() 调用（model: 'opus'），失败即 catch 跳过（符合 §5.4 best-effort）
  assert.doesNotMatch(runSrc, /agentWithFallback\('lessonDistiller'/, 'distiller 不得用 agentWithFallback（S5：偏离 §2.4「仅用于」约束）')
  assert.match(runSrc, /agent\(buildPrompt\('lessonDistiller'/, 'distiller 须用单次 agent 调用（S5）')
})

test('S6（第 5 轮）+ P3（2026-07-05）: SCHEMAS.bootstrap evidence required 须含 9 字段（§13c + deterministic-completed）', () => {
  // S6: spec §13c 标注 8 个 evidence 必填字段，但 schema required 仅 4 个
  //   修：required 补齐 in_progress/failed_approaches/task_lessons/task_write_files
  // P3 (2026-07-05): 加 git_log_subjects（bootstrap 返回原始 commit subjects，
  //   runtime 用 extractCompletedFromSubjects 正则提取 completed，不依赖 LLM）
  for (const src of [libSrc, runSrc]) {
    assert.match(src, /required:\s*\['config',\s*'plans',\s*'completed',\s*'git_log_subjects',\s*'dirty_tree',\s*'in_progress',\s*'failed_approaches',\s*'task_lessons',\s*'task_write_files'\]/, 'SCHEMAS.bootstrap.evidence.required 须含 9 字段（含 git_log_subjects，P3）')
    assert.match(src, /in_progress:\s*\{\s*type:\s*'boolean'\s*\}/, 'SCHEMAS.bootstrap 须含 in_progress 字段定义（S6）')
    assert.match(src, /git_log_subjects:\s*\{\s*type:\s*'array',\s*items:\s*\{\s*type:\s*'string'\s*\}\s*\}/, 'SCHEMAS.bootstrap 须含 git_log_subjects 字段定义（P3 deterministic-completed）')
  }
})

test('S7（第 5 轮）: SCHEMAS.commit evidence required 须含 tests_at_commit（§13c）', () => {
  // S7: spec §13c 标注 commit evidence 3 必填字段，但 schema required 缺 tests_at_commit
  //   修：required 补 tests_at_commit（防 agent 漏返，orchestrator 无法校验 commit 时测试真绿）
  for (const src of [libSrc, runSrc]) {
    assert.match(src, /required:\s*\['commit_sha',\s*'committed_files',\s*'tests_at_commit'\]/, 'SCHEMAS.commit.evidence.required 须含 tests_at_commit（S7，§13c）')
  }
})

test('Q4（第 5 轮）: SCHEMAS 整块须 lib.js ↔ run-plans.js 字节一致', () => {
  // Q4: sync.test.js 对 PROMPTS 做字节比较，对 helper 函数体做字节比较，
  //   但 SCHEMAS 只用 regex 检查个别字段 → lib.js 改 schema 不会被守护
  //   修：提取 SCHEMAS 整块字符串做字节比较（lib.js 为 export const，run-plans.js 为 const）
  function extractSchemas(src) {
    const m = src.match(/(?:export\s+)?const SCHEMAS = \{[\s\S]*?\n\}/)
    assert.ok(m, '须含 SCHEMAS 定义')
    return m[0].replace(/^export\s+/, '')
  }
  assert.equal(extractSchemas(runSrc), extractSchemas(libSrc), 'SCHEMAS 整块须字节一致（Q4）')
})

test('Q5（第 5 轮）: runTask perTask 初始化须用 ensurePerTaskDefaults（DRY）', () => {
  // Q5: runTask line 916 perTask 初始化字段列表与 ensurePerTaskDefaults 重复（12 字段）
  //   字段增删需两处同步，易漂移。修：复用 ensurePerTaskDefaults helper
  assert.match(runSrc, /state\.perTask\[tk\] = ensurePerTaskDefaults\(/, 'perTask 初始化须用 ensurePerTaskDefaults（Q5：DRY；S10 后局部变量 tk 经 taskKey() 构造）')
})

test('Q6（第 5 轮）: dispatchImpl null halt 消息须根据 retryModel 分支', () => {
  // Q6: 无 retryModel 时消息说 "retry with stronger model exhausted" 误导（从未 retry）
  //   修：消息根据是否有 retryModel 分支
  assert.doesNotMatch(runSrc, /retry with stronger model exhausted'\s*\}/, '不得用固定消息 "retry with stronger model exhausted"（Q6：无 retry 时误导）')
  // 须根据 retryModel 分支（有 retry 说 "retry with X also exhausted"，无 retry 说 "quota exhausted or capability failure"）
  assert.match(runSrc, /retryModel \? `[^`]*retry with \$\{retryModel\}[^`]*exhausted[^`]*` : '[^']*quota exhausted[^']*'/, 'dispatchImpl null halt 消息须根据 retryModel 分支（Q6）')
})

test('Q7（第 5 轮）: implementor prompt 中 fetchedContext 占位符只出现一次', () => {
  // Q7: fetchedContext 在 implementor prompt 出现两次（Inputs 行 + 独立段落）→ token 浪费
  //   修：删除 Inputs 行的 fetchedContext={{fetchedContext}}，仅保留独立段落
  for (const src of [libSrc, runSrc]) {
    const p = promptBody(src, 'implementor')
    const count = (p.match(/\{\{fetchedContext\}\}/g) || []).length
    assert.equal(count, 1, `implementor prompt fetchedContext 占位符只出现一次（Q7），实际 ${count} 次`)
    // Inputs 行不得含 fetchedContext（已移到独立段落）
    assert.doesNotMatch(p, /Inputs:.*fetchedContext=\{\{fetchedContext\}\}/, 'Inputs 行不得含 fetchedContext（Q7：移到独立段落）')
  }
})

test('Q8（第 5 轮）: runTask 完成时 perTask.status 须设为 done（终态）', () => {
  // Q8: perTask.status 停在 'committed'，从未设为 'done' → 无法区分"已提交但 simplify/destructive 未完成"与"全流程完成"
  //   修：runTask 末尾 return 前设 status = 'done'
  assert.match(runSrc, /state\.perTask\[tk\]\.status = 'done'/, 'runTask 完成时须设 status=done（Q8：终态语义；S10 后局部变量 tk 经 taskKey() 构造）')
})

test('Q11（第 5 轮）: concernsHint 须抽 formatConcernsHint helper（DRY）', () => {
  // Q11: concernsHint 构造逻辑在初始 dispatch（line 974）和 fix-round done_with_concerns（line 1020）两处重复
  //   修：抽 formatConcernsHint(concerns) helper，两处调用
  assert.match(runSrc, /function formatConcernsHint/, '须抽 formatConcernsHint helper（Q11：DRY）')
  // 两副本字节一致（lib.js 真源 + run-plans.js inline）
  const libBody = extractFunctionBody(libSrc, 'formatConcernsHint')
  const runBody = extractFunctionBody(runSrc, 'formatConcernsHint')
  assert.ok(libBody, 'lib.js 须有 formatConcernsHint 真源')
  assert.ok(runBody, 'run-plans.js 须 inline formatConcernsHint')
  assert.equal(runBody, libBody, 'formatConcernsHint 函数体字节一致（Q11）')
  // 两处调用须用 helper（不得 inline 重复模板字符串）
  const concernsHintCount = (runSrc.match(/\\n## Implementor Concerns \(verify these\)/g) || []).length
  assert.equal(concernsHintCount, 1, `concernsHint 模板字符串只出现 1 次（在 helper 内），实际 ${concernsHintCount} 次（Q11）`)
})

test('Q13（第 5 轮）: halt() 须返回 {result:"halted", reason} 供调用方 return（DRY）', () => {
  // Q13: 7 处 `await halt(...); return {result:'halted', reason:...}` 模式重复
  //   reason 需与传给 halt 的 reason 一致，易写错。修：halt() 返回 {result:'halted', reason: r.reason}
  //   调用方 `return await halt(...)`，DRY 且防 reason 不一致
  assert.match(runSrc, /return await halt\(/, '调用方须 return await halt(...)（Q13：DRY）')
  // halt() 函数体末尾须返回 {result:'halted', reason} 对象
  const haltMatch = runSrc.match(/async function halt\([\s\S]*?\n\}/)
  assert.ok(haltMatch, 'run-plans.js 须有 halt 函数')
  assert.match(haltMatch[0], /return\s*\{\s*result:\s*'halted'[^}]*reason/, 'halt() 须返回 {result:"halted", reason}（Q13）')
})

test('Q14（第 5 轮）: destructive_review_findings 须统一 shape（异常路径与正常路径一致）', () => {
  // Q14: 异常路径存 [{source, title}]，正常路径存 [{source, severity, title, file, fix}]
  //   两种 shape 混在同一字段，manifest 消费者需处理两种结构。修：异常路径统一为完整 shape
  // P1-3（第 13 轮）: severity 统一小写 → 'critical'
  assert.doesNotMatch(runSrc, /\{ source: 'destructive-review', title: dReason \|\| dEmptyFailed \}/, '不得用简化 shape {source, title}（Q14：shape 不一致）')
  assert.match(runSrc, /\{ source: 'destructive-review', severity: 'critical', title: dReason \|\| dEmptyFailed, fix:/, '异常路径须统一为 {source, severity, title, fix} shape（Q14）')
})

test('Q15（第 5 轮）: review_history.push 须在 halt 检查之前（halt 轮状态须持久化）', () => {
  // Q15: review 循环中 push 在 halt 检查之后 → halt 轮的 files_touched/review_history 不持久化
  //   distiller 看不到 halt 轮的 review 状态（如 review_failed_no_findings 的 failed-but-empty 信号）
  //   修：push 移到 halt 检查之前（先记录再判断 halt）
  // S2 (2026-07-07): push/findings_history 抽进 recordReviewRound（参数名 taskKey，loop 内 tk），
  //   Q15 不变量改为「recordReviewRound 调用必须在 halt 检查之前」——函数体内 push 顺序由 QC-4 字节守护。
  // S2 decideReviewOutcome (2026-07-07): reviewReason/emptyFailed halt 检查抽进 decideReviewOutcome，
  //   Q15 不变量改为「recordReviewRound 调用必须在 decideReviewOutcome 调用之前」。
  const recordIdx = runSrc.indexOf('recordReviewRound(state, tk, round, spec, qual, hunt)')
  const decideIdx = runSrc.indexOf('decideReviewOutcome(state, tk, round, spec, qual, hunt, model, maxRounds, cfg, reviewReason, emptyFailedReason)')
  assert.notEqual(recordIdx, -1, '须有 recordReviewRound 调用（S2 抽取后 push/findings 在函数内）')
  assert.notEqual(decideIdx, -1, '须有 decideReviewOutcome 调用（S2 抽取后含 reviewReason/emptyFailed halt）')
  assert.ok(recordIdx < decideIdx, 'recordReviewRound 须在 decideReviewOutcome 之前（Q15：halt 轮状态须持久化）')
})

// ===== 第 6 轮 review 修复断言（P0/P1/P2，共 11 项修复 + 1 项 wontfix）=====
// wontfix: log.ndjson / validate-plans / post-plan（大功能，spec 标注「实现期」，文档标 TODO）

test('P0-1（第 6 轮）: bootstrap prompt 须说明 in_progress 字段（schema required 补齐）', () => {
  // S6 补了 schema required 但 prompt 未说明如何获取 → agent 可能漏返 → schema 校验失败
  // 修：prompt Steps + Return 行补 in_progress（检查 plan 是否有 in_progress task）
  for (const src of [libSrc, runSrc]) {
    const p = promptBody(src, 'bootstrap')
    // Steps 须说明 in_progress 的获取方式
    assert.match(p, /in_progress/, 'bootstrap prompt 须含 in_progress 字段（P0-1）')
    // Return 行须列出 in_progress
    assert.match(p, /Return \{status, evidence:\{[\s\S]*?in_progress/, 'bootstrap Return 行须含 in_progress（P0-1）')
  }
})

test('P0-2（第 6 轮）: state.plans 须写入（finalReport stateJson 须含 plans）', () => {
  // state 未保存 plans 数组 → finalReport 收到的 stateJson 缺 plans → manifest 不完整
  assert.match(runSrc, /state\.plans\s*=\s*boot\.evidence\.plans/, 'state.plans 须赋值（P0-2）')
})

test('P0-3（第 6 轮）: 顶层 catch 须用 agent_error（非 model_unavailable）', () => {
  // 顶层 catch 处理 dispatchImpl 抛出的非 quota 异常（TypeError 等），
  // 旧代码一律标 model_unavailable → 误导用户无效 resume。修：改 agent_error
  // 顶层 catch 位置：bootstrap try/catch、gate try/catch、runTask try/catch
  // 这些 catch 不得用 model_unavailable（除非 dispatchImpl 已归类）
  // 检查：bootstrap catch 块不得有 reason: 'model_unavailable'
  const bootstrapCatchIdx = runSrc.indexOf("boot = await dispatchImpl(buildPrompt('bootstrap'")
  const bootstrapCatchEnd = runSrc.indexOf('}', runSrc.indexOf('catch (e) {', bootstrapCatchIdx)) + 1
  const bootstrapCatch = runSrc.slice(bootstrapCatchIdx, bootstrapCatchEnd)
  assert.doesNotMatch(bootstrapCatch, /reason: 'model_unavailable'/, 'bootstrap 顶层 catch 不得用 model_unavailable（P0-3：非 quota 错误误归类）')
  assert.match(bootstrapCatch, /reason: 'agent_error'/, 'bootstrap 顶层 catch 须用 agent_error（P0-3）')
})

test('P0-4（第 6 轮）: dispatchImpl 非 quota 异常须封装 agent_error（不 throw）', () => {
  // dispatchImpl catch 非 quota 错时 throw e → 被顶层 catch 捕获误判为 model_unavailable
  // 修：封装为 {halted:true, reason:'agent_error'} 返回（不 throw）
  const dispatchMatch = runSrc.match(/async function dispatchImpl[\s\S]*?\n\}/)
  assert.ok(dispatchMatch, '须有 dispatchImpl 函数')
  // 不得有 throw e（非 quota 异常不得抛出）
  assert.doesNotMatch(dispatchMatch[0], /if \(isQuotaError\(e\)\) return[\s\S]*?else throw e|throw e\s*$/, 'dispatchImpl 非 quota 异常不得 throw（P0-4）')
  // 须返回 agent_error（S9 后经 makeHalt('agent_error', ...) 构造，原 reason 字面量移入 makeHalt）
  assert.match(dispatchMatch[0], /makeHalt\('agent_error'/, 'dispatchImpl 非 quota 异常须封装 agent_error（P0-4，S9 后经 makeHalt）')
})

test('P1-5（第 6 轮）: commit/simplify/contextFetcher 须硬编码 sonnet（least-powerful-model）', () => {
  // spec §13b 表注三者用 sonnet，不得用 task model（可能因 BLOCKED 升级为 opus）
  // commit dispatchImpl 调用须用 model: 'sonnet'
  assert.match(runSrc, /buildPrompt\('commit'[\s\S]*?\{[^}]*model: 'sonnet'/, 'commit 须用 sonnet（P1-5：least-powerful-model）')
  assert.match(runSrc, /buildPrompt\('simplify'[\s\S]*?\{[^}]*model: 'sonnet'/, 'simplify 须用 sonnet（P1-5）')
  assert.match(runSrc, /buildPrompt\('contextFetcher'[\s\S]*?\{[^}]*model: 'sonnet'/, 'contextFetcher 须用 sonnet（P1-5）')
})

test('P1-6（第 6 轮）: bootstrap prompt 须含 dirty_tree 自愈逻辑（git reset --hard HEAD）', () => {
  // spec §6.2：dirty_tree=true 时 bootstrap agent 须自愈（git reset --hard HEAD 清理半提交）
  // 不 halt（orchestrator 无 shell，但 bootstrap agent 有 Bash 访问）
  for (const src of [libSrc, runSrc]) {
    const p = promptBody(src, 'bootstrap')
    assert.match(p, /git reset --hard HEAD/, 'bootstrap prompt 须含 git reset --hard HEAD 自愈（P1-6：dirty_tree 不 halt）')
  }
})

test('P1-7（第 6 轮）: buildPrompt undefined 须渲染为空串（非 "undefined"）', () => {
  // buildPrompt 用 String(ctx[k]) → undefined 渲染为 "undefined" 污染 prompt
  // 修：ctx[k] === undefined 时渲染空串
  // S7（2026-07-07）: buildPrompt 合并 defaults（{ quotaHaltNote: QUOTA_HALT_NOTE }）→ ctx 变量
  //   改为 merged。undefined/null 检查仍在（merged[k]），行为不变。正则接受 ctx[k] 或 merged[k]。
  for (const src of [libSrc, runSrc]) {
    const fn = extractFunctionBody(src, 'buildPrompt')
    assert.ok(fn, '须有 buildPrompt 函数')
    // 不得直接 String(ctx[k])（undefined → "undefined"）
    assert.doesNotMatch(fn, /=> \(k in ctx \? String\(ctx\[k\]\)/, 'buildPrompt 不得直接 String(ctx[k])（P1-7：undefined→"undefined"）')
    // 须检查 undefined 或 null（ctx[k] for pre-S7，merged[k] for post-S7 defaults 合并）
    assert.match(fn, /(?:ctx|merged)\[k\] (?:===?\s*undefined|!=?\s*null|!==?\s*undefined)/, 'buildPrompt 须检查 undefined/null（P1-7；S7 后用 merged[k]）')
  }
})

test('P1-8（第 6 轮）: haltLikelySource 须用显式映射（非大正则）', () => {
  // 旧实现用大正则 /max rounds|OSCILLATING|.../ 维护困难、易误匹配
  // 修：改显式 reason→source 映射（Set + startsWith）
  for (const src of [libSrc, runSrc]) {
    const fn = extractFunctionBody(src, 'haltLikelySource')
    assert.ok(fn, '须有 haltLikelySource 函数')
    // 不得用大正则（单行含 | 分隔 5+ 关键词）
    assert.doesNotMatch(fn, /\([a-z_ ]+(?:\|[a-z_ ]+){4,}/, 'haltLikelySource 不得用大正则（P1-8：维护困难）')
    // 须用 Set 或显式映射
    assert.match(fn, /Set\(\[|\.has\(/, 'haltLikelySource 须用显式映射（Set/has，P1-8）')
  }
})

test('P2-9（第 6 轮）: lastSha 须按 plan.tasks 反向查找（非 Object.values 插入顺序）', () => {
  // Object.values 插入顺序依赖 perTask 写入顺序，不保证 plan.tasks 顺序
  // 修：按 plan.tasks 反向查找最后一个有 commit_sha 的 task
  assert.doesNotMatch(runSrc, /Object\.values\(state\.perTask\)\.filter\([^)]*\)\.at\(-1\)/, 'lastSha 不得用 Object.values 插入顺序（P2-9）')
  // 须按 plan.tasks 查找（反向遍历或 reverse）
  const gateIdx = runSrc.indexOf('lastSha')
  const gateCtx = runSrc.slice(gateIdx, gateIdx + 300)
  assert.match(gateCtx, /plan\.tasks/, 'lastSha 须按 plan.tasks 查找（P2-9）')
})

test('P2-10（第 6 轮）: fixModelForRound 不得有 undefined 分支（死代码）', () => {
  // resolveMaxRounds 总返回 number，maxRounds === undefined 分支不可达
  // 修：删除 undefined 分支（死代码清理）
  for (const src of [libSrc, runSrc]) {
    const fn = extractFunctionBody(src, 'fixModelForRound')
    assert.ok(fn, '须有 fixModelForRound 函数')
    assert.doesNotMatch(fn, /maxRounds === undefined/, 'fixModelForRound 不得有 maxRounds === undefined 分支（P2-10 死代码）')
  }
})

test('P1-11（第 6 轮）: implementor prompt 须接入 build_command', () => {
  // config 有 build_command 但无 prompt 使用 → 未验证可构建性
  // 修：implementor prompt 加 buildCommand 占位符 + GREEN 前跑 build 验证
  for (const src of [libSrc, runSrc]) {
    const p = promptBody(src, 'implementor')
    assert.match(p, /buildCommand/, 'implementor prompt 须含 buildCommand 占位符（P1-11）')
  }
  // orchestrator 须传 buildCommand 到 implCtx
  assert.match(runSrc, /buildCommand:/, 'implCtx 须传 buildCommand（P1-11）')
})

// ===== 第 7 轮 TDD red 断言 =====

test('P0-7（第 7 轮）: lastSha key 须用 padStart 归一化（S10 后经 taskKey 统一）', () => {
  // :1284 旧代码 `plan-${plan.seq}` 无 padStart，若 plan.seq=1（数字）→ "plan-1"
  // 其他 4 处用 `plan-${String(plan.seq).padStart(2, '0')}` → "plan-01"
  // 不一致 → perTask 查不到 → gate 在 null SHA 上跑漏检
  // S10 (2026-07-07): 所有 task-key 构造统一经 lib.js taskKey(seq, taskId)，padStart 一致性由该函数保证
  const lastShaIdx = runSrc.indexOf('let lastSha = null')
  assert.notEqual(lastShaIdx, -1, '须有 lastSha 声明')
  const ctx = runSrc.slice(lastShaIdx, lastShaIdx + 400)
  assert.match(ctx, /taskKey\(plan\.seq,\s*plan\.tasks\[i\]\.id\)/,
    'lastSha key 须用 taskKey(plan.seq, plan.tasks[i].id) 归一化（P0-7，S10 后经 taskKey 统一）')
})

test('P2-7（第 7 轮）: args 入口须校验 configPath/plansDir 类型（fail-fast）', () => {
  // 旧代码直接 args.configPath 注入 prompt，未传时 undefined 被 P1-7 渲染为空串
  // bootstrap agent 因 config 路径空而失败，错误信息不直观
  // 修：入口校验 typeof === 'string' && non-empty，否则 throw fail-fast
  assert.match(runSrc, /typeof args\.configPath !== 'string'|!args\.configPath|args\.configPath.*string/,
    '须校验 args.configPath 类型（P2-7 fail-fast）')
  assert.match(runSrc, /typeof args\.plansDir !== 'string'|!args\.plansDir|args\.plansDir.*string/,
    '须校验 args.plansDir 类型（P2-7 fail-fast）')
})

test('P1-7b（第 7 轮）: state 字面量须含 plans 字段（与 spec §4.4 一致）', () => {
  // :834 state 字面量未列 plans，运行时 :1227 补入
  // spec §4.4 已列 plans（P0-2 修过 spec），代码 state 字面量未同步
  // 修：state 字面量加 plans: []
  const stateIdx = runSrc.indexOf('const state = {')
  assert.notEqual(stateIdx, -1, '须有 state 声明')
  const ctx = runSrc.slice(stateIdx, stateIdx + 400)
  assert.match(ctx, /plans:\s*\[\]/, 'state 字面量须含 plans: []（P1-7b，与 spec §4.4 一致）')
})

// ===== 第 8 轮 TDD red 断言 =====

test('P1-8a（第 8 轮）: args 入口须防御 args===undefined（防 TypeError）', () => {
  // 运行时风险：:1199 先访问 args.configPath，若 args===undefined 会在校验前抛 TypeError
  // 修：校验前加 `if (!args)` 独立防御（非 !args.configPath 子串）
  // 用全局正则搜索（CRLF 行尾导致窗口大小不可预测）
  assert.match(runSrc, /if \(!args\) throw/,
    'args 校验前须有独立 if(!args) throw 防御（P1-8a，防 TypeError，非 !args.configPath 子串）')
  // 确保该防御在 args.configPath 校验之前
  const guardIdx = runSrc.indexOf('if (!args) throw')
  const cfgIdx = runSrc.indexOf('typeof args.configPath')
  assert.ok(guardIdx > -1 && guardIdx < cfgIdx, 'if(!args) 防御须在 args.configPath 校验之前')
})

test('P2-8b（第 8 轮）: state 字面量须含 currentPlan（与 spec §4.4 一致）', () => {
  // :835 state 字面量已声明 currentPlan: null，但 spec §4.4 未列
  // 修：spec §4.4 补 currentPlan 字段（仅内存字段，不写入 manifest）
  const stateIdx = runSrc.indexOf('const state = {')
  assert.notEqual(stateIdx, -1, '须有 state 声明')
  const ctx = runSrc.slice(stateIdx, stateIdx + 400)
  assert.match(ctx, /currentPlan/, 'state 字面量须含 currentPlan（P2-8b，与 spec §4.4 一致）')
})

// ===== 第 9 轮 TDD red 断言 =====

test('P2-9a（第 9 轮）: args.tasks 须用 Array.isArray 防御（防字符串误传 .map TypeError）', () => {
  // :1273 旧代码 `(args.tasks && args.tasks.length)` 通过字符串（有 .length）→ .map(String) 抛 TypeError
  // 修：改用 Array.isArray(args.tasks) 严格类型校验
  assert.match(runSrc, /Array\.isArray\(args\.tasks\)/,
    'args.tasks 须用 Array.isArray 防御（P2-9a，防字符串误传 .map TypeError）')
})

// ===== 第 10 轮 TDD red 断言 =====

test('P2-10a（第 10 轮）: state 字面量注释不得用行号引用（防行号漂移）', () => {
  // :835 旧注释 "运行时 :1227 补入" 行号已漂移（实际 :1238）
  // 修：注释改用语义引用（"运行时 bootstrap 阶段补入"），不依赖具体行号
  // 第 11 轮扩展：正则覆盖 :NNNN / :line NNNN / line NNNN 等变体
  const stateIdx = runSrc.indexOf('const state = {')
  const ctx = runSrc.slice(stateIdx, stateIdx + 400)
  assert.doesNotMatch(ctx, /:\s*(line\s*)?\d{3,}|line\s+\d{3,}/i,
    'state 字面量注释不得用 :行号 或 line 行号 引用（P2-10a，防行号漂移，改用语义引用）')
})

// ===== 第 11 轮 TDD red 断言 =====

test('P2-11a（第 11 轮）: perTask 注释须用 taskKey 而非 taskId（对齐实际 key）', () => {
  // :836 旧注释 {taskId: ...} 误导，实际 key 是 plan-scoped taskKey（plan-XX/TY）
  // 修：注释改为 {taskKey: ...}
  const stateIdx = runSrc.indexOf('const state = {')
  const ctx = runSrc.slice(stateIdx, stateIdx + 600)
  assert.match(ctx, /\{taskKey:/,
    'perTask 注释须用 {taskKey: ...} 而非 {taskId: ...}（P2-11a，对齐实际 plan-scoped key）')
  assert.doesNotMatch(ctx, /\{taskId:/,
    'perTask 注释不得用 {taskId: ...}（P2-11a，实际 key 是 plan-scoped taskKey）')
})

test('P2-11b（第 11 轮）: .gitattributes 须为源码文件启用 diff（防 binary 致 git diff 不可读）', () => {
  // 第 10 轮发现：* binary 兜底 + *.js text eol=crlf 未覆盖 diff 属性 → git diff 显示 Bin
  // 修：*.js 等规则加 diff 显式启用（或 !binary 撤销 binary 宏）
  const attrs = fs.readFileSync(path.resolve(__dirname, '../../../../.gitattributes'), 'utf8')
  // *.js 行须含 diff 关键字（启用 text diff）
  assert.match(attrs, /\*\.js\s+text eol=crlf diff/,
    '*.js 规则须含 diff（P2-11b，防 * binary 兜底致 git diff 显示 Bin 不可读）')
})

// ===== 第 12 轮 TDD red 断言 =====

test('P2-12a（第 12 轮）: finalReport prompt 须用 <taskKey> 而非 <taskId>（对齐实际 key）', () => {
  // :770 prompt 写 per_task:{<taskId>:{...}}，实际 key 是 plan-scoped taskKey（plan-XX/TY）
  // 修：prompt 改为 {<taskKey>:{...}}（agent 从 schema 推断正确，但 prompt 用词应精确）
  for (const src of [libSrc, runSrc]) {
    const p = promptBody(src, 'finalReport')
    assert.match(p, /per_task:\{<taskKey>/,
      'finalReport prompt 须用 <taskKey> 而非 <taskId>（P2-12a，对齐实际 plan-scoped key）')
    assert.doesNotMatch(p, /per_task:\{<taskId>/,
      'finalReport prompt 不得用 <taskId>（P2-12a，实际 key 是 plan-scoped taskKey）')
  }
})

test('P2-12b（第 12 轮）: .gitattributes 须为自身启用 text diff（防 * binary 兜底）', () => {
  // .gitattributes 自身被 * binary 兜底 → git diff 显示 Bin 不可读
  // 修：加 .gitattributes text eol=crlf diff 规则
  const attrs = fs.readFileSync(path.resolve(__dirname, '../../../../.gitattributes'), 'utf8')
  assert.match(attrs, /\.gitattributes\s+text eol=crlf diff/,
    '.gitattributes 须有自身规则 text eol=crlf diff（P2-12b，防 * binary 兜底致自身 diff 不可读）')
})

// ===== 第 13 轮 TDD red 断言 =====

test('P1-1a（第 13 轮）: gate prompt 须要求返回 restored_head', () => {
  // gate agent 负责 git checkout - 恢复 HEAD，但 orchestrator 不二次验证
  // 修：prompt 要求 agent 返回 evidence.restored_head（恢复后 git rev-parse HEAD）
  for (const src of [libSrc, runSrc]) {
    const p = promptBody(src, 'gate')
    assert.match(p, /restored_head/,
      'gate prompt 须要求返回 restored_head（P1-1a，供 orchestrator 验证 HEAD 已恢复）')
  }
})

test('P1-1b（第 13 轮）: gate schema 须要求 evidence.restored_head', () => {
  // 修：SCHEMAS.gate.evidence required 增加 restored_head
  // lib.js 是纯函数真源，不含 SCHEMAS；schema 只存在于 run-plans.js
  assert.match(runSrc, /restored_head:\s*\{\s*type:\s*'string'\s*\}/,
    'gate schema 须声明 restored_head: {type: "string"}（P1-1b）')
  assert.match(runSrc, /evidence:\s*\{\s*type:\s*'object',\s*required:\s*\[.*'restored_head'/,
    'gate evidence required 须包含 restored_head（P1-1b）')
})

test('P1-1c（第 13 轮）: gate 返回后须 dispatch 验证当前 HEAD', () => {
  // 修：orchestrator 在 gate ok 后再派 headVerifier subagent，与 restored_head 比对
  assert.match(runSrc, /headVerifier/,
    'run-plans.js 须新增 headVerifier prompt 角色（P1-1c）')
  assert.match(runSrc, /head-verify:/,
    'run-plans.js 主流程须在 gate 后 dispatch head-verify 子 agent（P1-1c）')
  assert.match(runSrc, /gate head restore verification failed/,
    'run-plans.js 须有 gate head restore verification failed  halt reason（P1-1c）')
})

test('P1-2a（第 13 轮）: bootstrap schema 须要求 failed_approaches[].plan_seq', () => {
  // 修：failed_approaches items 增加 plan_seq，供 orchestrator 归一化为 plan-scoped key
  assert.match(runSrc, /failed_approaches:\s*\{\s*type:\s*'array',\s*items:/,
    'run-plans.js bootstrap schema failed_approaches 须为 items 约束数组（P1-2a）')
  assert.match(runSrc, /plan_seq:\s*\{\s*type:\s*'integer'\s*\}/,
    'run-plans.js bootstrap schema 须声明 failed_approaches[].plan_seq（P1-2a）')
})

test('P1-2b（第 13 轮）: failed_approaches 存储须归一化为 plan-scoped key', () => {
  // 修：orchestrator 按 taskKey(fa.plan_seq, fa.task_id) 索引（S10 后统一经 taskKey 构造）
  assert.match(runSrc, /fa\.task_id\.includes\('\/'\)\s*\?\s*fa\.task_id\s*:\s*taskKey\(fa\.plan_seq,\s*fa\.task_id\)/,
    'run-plans.js 须对 failed_approaches task_id 做 plan-scoped 归一化（P1-2b，S10 后用 taskKey）')
})

test('P1-3a（第 13 轮）: qualityReviewer severity enum 须与 hunter 统一为小写', () => {
  // 修：qualityReviewer schema severity enum 从 ['Critical', 'Important', 'Minor'] 改为 ['critical', 'important', 'minor']
  for (const src of [libSrc, runSrc]) {
    assert.doesNotMatch(src, /severity:\s*\{\s*type:\s*'string',\s*enum:\s*\['Critical',\s*'Important',\s*'Minor'\]\s*\}/,
      'qualityReviewer schema 不得再用大写 severity enum（P1-3a）')
    assert.match(src, /severity:\s*\{\s*type:\s*'string',\s*enum:\s*\['critical',\s*'important',\s*'minor'\]\s*\}/,
      'qualityReviewer schema 须用小写 severity enum（P1-3a）')
  }
})

test('P2-1a（第 13 轮）: implementor schema 不强求 evidence 必填', () => {
  // 修：blocked/needs_context 状态下 agent 无真实 tests_exit_code，移除 evidence 的 required 约束
  for (const src of [libSrc, runSrc]) {
    const implMatch = src.match(/implementor:\s*\{\s*type:\s*'object',\s*required:\s*\[([^\]]+)\]/)
    assert.ok(implMatch, '须找到 implementor schema 的 required 字段')
    assert.doesNotMatch(implMatch[1], /'evidence'/,
      'implementor schema required 不得包含 evidence（P2-1a）')
  }
})

test('P2-2a（第 13 轮）: gate lint_results schema 须约束 item shape', () => {
  // 修：lint_results 从 type: 'array' 收紧为 items: { command, exit_code } required
  for (const src of [libSrc, runSrc]) {
    assert.match(src, /lint_results:\s*\{\s*type:\s*'array',\s*items:\s*\{\s*type:\s*'object',\s*required:\s*\['command',\s*'exit_code'\]/,
      'gate schema lint_results 须约束 item shape（P2-2a）')
  }
})

// ===== 第 14 轮 TDD red 断言：清理项目特定示例文本 + silent_failure_intro 配置化 =====

test('P2-3b（第 14 轮）: prompt 模板不得残留彩票领域示例文本', () => {
  // 修：formatReferencePaths / implementor / specReview 中的 number/play/prize/rule/lottery 示例改为中性说明
  for (const src of [libSrc, runSrc]) {
    assert.doesNotMatch(src, /number\/play\/prize\/rule/,
      'prompt 模板不得残留 number/play/prize/rule 等彩票领域示例（P2-3b）')
    assert.doesNotMatch(src, /lottery type's rule/,
      'prompt 模板不得残留 lottery type 等彩票领域示例（P2-3b）')
    assert.doesNotMatch(src, /中奖永不静默漏通知/,
      'prompt 模板不得残留中文彩票口号（P2-3b）')
    assert.doesNotMatch(src, /彩票/,
      '代码注释不得残留“彩票”字样（P2-3b）')
  }
})

test('P2-3c（第 14 轮）: formatSilentFailureContext 支持自定义 intro 且默认中性', () => {
  // 修：函数签名增加 intro 参数，默认标题从“中奖...”改为中性项目特定风险说明
  for (const src of [libSrc, runSrc]) {
    assert.match(src, /function formatSilentFailureContext\(items,\s*intro\)/,
      'formatSilentFailureContext 须支持 intro 参数（P2-3c）')
    assert.match(src, /Project-Specific Silent-Failure Risks/,
      'formatSilentFailureContext 默认标题须中性（P2-3c）')
  }
})

// ===== cross-reviewer surfacing (2026-07-05) =====

test('finalReport prompt 须含 cross-reviewer 分组段落', () => {
  for (const src of [libSrc, runSrc]) {
    const p = promptBody(src, 'finalReport')
    assert.match(p, /Cross-Reviewer/, 'finalReport 须含 cross-reviewer 分组指引')
    assert.match(p, /grouped by file/, 'cross-reviewer 须按文件分组')
  }
})

test('finalReport prompt per_task includes v3 fields', () => {
  for (const src of [libSrc, runSrc]) {
    const p = promptBody(src, 'finalReport')
    assert.match(p, /findings_history/, 'finalReport prompt mentions findings_history')
    assert.match(p, /oscillation_escalated_at_round/, 'finalReport prompt mentions oscillation_escalated_at_round')
  }
})

// ===== Task 14 (2026-07-07): B3-1 runFixRound runtime helper 存在性断言 =====
// S2/D17: runFixRound 是 runtime 胶水（调 dispatchImpl），只能留 run-plans.js（lib.js 纯模块
//   不能调 agent() runtime 全局，§4.3 分层）。可测逻辑已被 lib.js 纯函数覆盖
//   （collectReviewFindings/formatCrossReviewerNote/formatFindingsHistory/fixModelForRound），
//   runFixRound 只是胶水调用——靠 sync 存在性断言 + 全量回归兜底（D17 不加新单测）。
test('Task 14: runFixRound runtime helper 存在 + 关键分支源码字面量守护', () => {
  assert.match(runSrc, /async function runFixRound\(/, 'run-plans.js 须定义 runFixRound runtime helper（Task 14 抽取）')
  // D16: 不用 checkImplStatus——fix-round 的 blocked/failed/needs_context 都 halt，语义不同
  const fnStart = runSrc.indexOf('async function runFixRound(')
  const fnEnd = runSrc.indexOf('\n}', fnStart)
  const body = runSrc.slice(fnStart, fnEnd + 2)
  // fix-round dispatch 须用 fixModelForRound + retryModel='opus'（改进 7.1）
  assert.match(body, /fixModelForRound\(round, model, maxRounds\)/, 'runFixRound 须用 fixModelForRound 决定 fix-round model')
  assert.match(body, /dispatchImpl\([\s\S]*label: `impl:\$\{task\.id\}:fix\$\{round\}`[\s\S]*'opus'\)/, 'runFixRound 须 dispatch implementor with fix label + retryModel opus')
  // impl.halted → { impl, halted: true }（调用方据 reason 缺失 return fixResult.impl）
  assert.match(body, /if \(impl\.halted\) return \{ impl, halted: true \}/, 'runFixRound impl.halted 须返回 { impl, halted: true }（D16 不用 checkImplStatus）')
  // blocked/failed/needs_context → halt with reason
  assert.match(body, /impl\.status === 'blocked' \|\| impl\.status === 'failed' \|\| impl\.status === 'needs_context'/, 'runFixRound 须 inline 判 blocked/failed/needs_context halt（D16）')
  assert.match(body, /reason: `implementor \$\{impl\.status\} in fix-round \$\{round\}`/, 'runFixRound halt reason 须含 fix-round N')
  // done_with_concerns → 更新 concerns + perTask + concernsHint + log（Q10）
  assert.match(body, /impl\.status === 'done_with_concerns'[\s\S]*?concerns = impl\.diagnostics\?\.concerns \|\| concerns/, 'runFixRound done_with_concerns 须更新 concerns（Q10）')
  assert.match(body, /state\.perTask\[taskKey\]\.concerns = concerns/, 'runFixRound done_with_concerns 须写 perTask.concerns')
  assert.match(body, /concernsHint = formatConcernsHint\(concerns\)/, 'runFixRound done_with_concerns 须更新 concernsHint')
  // 正常/done_with_concerns 返回 { impl, halted: false, concerns, concernsHint, filesChanged }
  assert.match(body, /return \{ impl, halted: false, concerns, concernsHint, filesChanged: impl\.evidence\.files_changed \}/, 'runFixRound 正常路径须返回 halted:false + concerns/concernsHint/filesChanged')
  // 调用方须用 tk 作 taskKey 实参（loop 内局部变量）
  assert.match(runSrc, /await runFixRound\(tk, plan, task, round, spec, qual, hunt, state, cfg, implCtx, model, maxRounds, concerns, concernsHint\)/, 'review 循环须调用 runFixRound(tk, ...)（taskKey 实参用 loop 局部 tk）')
  // 调用方 result handling 还原原 inline return 语义
  assert.match(runSrc, /if \(fixResult\.reason\) return \{ halted: true, reason: fixResult\.reason, diag: fixResult\.impl\.diagnostics \}/, '调用方 blocked/failed/needs_context 须 return { halted, reason, diag }（还原原 inline）')
  assert.match(runSrc, /return fixResult\.impl/, '调用方 impl.halted 须 return fixResult.impl（还原原 return impl）')
  assert.match(runSrc, /filesChanged = fixResult\.filesChanged \|\| filesChanged/, '调用方须用 fixResult.filesChanged || filesChanged 保留 fallback（还原原 || filesChanged）')
})

// ===== Task 15 (2026-07-07): B3-2 S3 simplify 三 helper 存在性断言 =====
// S3/D17: 3 个 simplify runtime helper（checkSimplifyChanges/amendSimplifyCommit/
//   revertSimplifyChanges）是 safeAgent 胶水调用（调 agent() runtime 全局），只能留
//   run-plans.js（lib.js 纯模块不能调 runtime，§4.3 分层）。可测逻辑已被 lib.js 纯函数
//   覆盖（validateAmendResult/validateCheckoutResult，helpers.test 已覆盖失败分支），
//   3 helper 剩余只是胶水——靠 sync 存在性断言 + 全量回归兜底（D17 不加新单测，与 runFixRound 同策略）。
//   Task 16 将在 runTask 调用点用这 3 helper 替换 inline 块（line ~1584/1611/1628）。
test('Task 15: B3-2 S3 simplify 三 helper 存在', () => {
  assert.match(runSrc, /async function checkSimplifyChanges\(/, 'checkSimplifyChanges 存在')
  assert.match(runSrc, /async function amendSimplifyCommit\(/, 'amendSimplifyCommit 存在')
  assert.match(runSrc, /async function revertSimplifyChanges\(/, 'revertSimplifyChanges 存在')
})

// ===== HIGH-1 (2026-07-07): lesson_categories bootstrap 提取链（源码字面量断言）=====

test('HIGH-1: bootstrap prompt step 3 含 lesson_categories 提取说明', () => {
  const boot = promptBody(libSrc, 'bootstrap')
  assert.ok(boot.includes('lesson_categories'),
    'bootstrap prompt 须指示提取 lesson_categories（当前未指示 → category 精确匹配分支端到端不可达）')
})

test('HIGH-1: bootstrap Return schema tasks 含 lesson_categories 字段', () => {
  const boot = promptBody(libSrc, 'bootstrap')
  // Return schema 在 prompt 文本内描述为 tasks:[{id, model, title, ...}]
  assert.match(boot, /tasks:\[\{[^}]*lesson_categories/,
    'bootstrap Return schema tasks 须含 lesson_categories 字段')
})

// ===== Task 2 (2026-07-07): MEDIUM-1 trip-wire 守护 + B1-10 通用性守护 =====
// 守护断言（直接 GREEN）：断言当前代码已满足，防未来回归而非验证当前行为（spec §4.2/§4.10 决策）。
// extractFunctionBody（line 41-50）依赖 top-level 函数末尾 `\n}`（列 0 闭合大括号）+ SCHEMAS 块 `\n}` 结尾。
// 若未来某 export function 体内出现 `\n}` 子模式（如嵌套对象字面量换行收尾），会破坏 extractFunctionBody →
// QC-4 字节比较静默失效（lib.js 改了实现但字节比较漏检）。此守护提前捕获该漂移。
//
// 实现注记（D4 同类复核）：初稿 trip-wire 2 用单遍正则 `export function (\w+)\([\s\S]*?\{([\s\S]*?)\n\}`
// 提取函数体，但对 `buildPrompt(role, ctx = {})` 这类含 `= {}` 默认形参的签名，非贪婪 `\{` 会误把形参
// 空对象当函数体开括号 → 大括号"不平衡"假阳性（与已删 trip-wire 1 同类正则假阳性，见 D4 复核修正）。
// 改用 QC-4 同款 extractFunctionBody 提取真实函数体后再校验大括号平衡——直接守护被测不变量，无假阳性。

test('MEDIUM-1 守护：纯函数体不得含顶层 \\n} 子模式（破坏 extractFunctionBody）', () => {
  // 用 QC-4 同款 extractFunctionBody 提取每个 export function 真实函数体，校验大括号平衡。
  // 若函数体含 `\n}` 子模式，extractFunctionBody 会在该处提前截断 → 提取体大括号不平衡（depth != 0）。
  const names = [...libSrc.matchAll(/export function (\w+)/g)].map(m => m[1])
  assert.ok(names.length > 0, 'lib.js 须有 export function')
  for (const name of names) {
    const body = extractFunctionBody(libSrc, name)
    assert.ok(body, `无法用 extractFunctionBody 提取函数 ${name}（可能含 \\n} 子模式破坏提取）`)
    let depth = 0
    for (const ch of body) {
      if (ch === '{') depth++
      else if (ch === '}') depth--
    }
    assert.equal(depth, 0, `函数 ${name} 体大括号不平衡（depth=${depth}，含 \\n} 子模式破坏 extractFunctionBody）`)
  }
})

test('MEDIUM-1 守护：SCHEMAS 块结尾必须是 \\n}（防 extractSchemas 截断）', () => {
  const m = libSrc.match(/const SCHEMAS = \{[\s\S]*?\n\}/)
  assert.ok(m, 'SCHEMAS 块存在且以 \\n} 结尾')
})

test('B1-10 通用性守护：PROMPTS 不得含本项目专有路径/文件名', () => {
  const m = libSrc.match(/const PROMPTS = \{[\s\S]*?\n\}/)
  assert.ok(m, 'PROMPTS 块存在')
  // 项目耦合黑名单（lottery-notification 专有）。项目特定内容应靠 config 驱动注入，
  // 不应硬编码进通用 workflow 的 PROMPTS。
  // 黑名单取项目专有 token：完整仓库名 'lottery-notification' + 彩种领域词 'lottery'。
  // 不含 'notification'（通用英文，commit scope 示例 feat(notifications) 假阳性）/
  // 'lessons.md'（通用文件名示例，非硬编码项目路径）。实施时已清掉 lessonDistiller 示例里的
  // 彩种词 dlt_append（→ item_append），故当前 PROMPTS 无耦合。
  const blacklist = ['lottery', 'lottery-notification']
  for (const bad of blacklist) {
    assert.ok(!m[0].toLowerCase().includes(bad.toLowerCase()),
      `PROMPTS 含本项目专有词 "${bad}"——项目耦合，应改 config 驱动注入`)
  }
})

// Task 2 (2026-07-08): SCHEMAS.implementor needs_audit_fix + audit_reason 守护。
// refactor 类 task AUDIT 阶段发现 brief 缺陷时返回 needs_audit_fix status + audit_reason 字段，
// dispatchImpl 据 audit_reason halt；finalReport prompt 据 audit_reason 分类渲染 blocked.md。
// 注意：AUDIT_DIRECTIVE 常量（Task 1）已含 needs_audit_fix/audit_reason 字符串（prompt 文本），
// 故须用 extractSchemas 提取 SCHEMAS 块后断言（而非全文件 grep）——否则假绿。
test('AUDIT: SCHEMAS.implementor 含 needs_audit_fix + audit_reason', () => {
  function extractSchemas(src) {
    const m = src.match(/(?:export\s+)?const SCHEMAS = \{[\s\S]*?\n\}/)
    assert.ok(m, '须含 SCHEMAS 定义')
    return m[0].replace(/^export\s+/, '')
  }
  const libSchemas = extractSchemas(libSrc)
  const runSchemas = extractSchemas(runSrc)
  // status enum 须含 needs_audit_fix
  assert.match(libSchemas, /needs_audit_fix/, 'lib.js SCHEMAS.implementor status enum 须含 needs_audit_fix')
  assert.match(runSchemas, /needs_audit_fix/, 'run-plans.js SCHEMAS.implementor status enum 须含 needs_audit_fix（inline 同步）')
  // 顶层 audit_reason 字段 + 三个枚举值
  assert.match(libSchemas, /audit_reason:\s*\{\s*type:\s*'string',\s*enum:\s*\['brief_defect',\s*'intentional_variant_unclear',\s*'tool_failure'\]\s*\}/,
    'lib.js SCHEMAS.implementor 须含 audit_reason 顶层字段 + 三枚举值（brief_defect/intentional_variant_unclear/tool_failure）')
  assert.match(runSchemas, /audit_reason:\s*\{\s*type:\s*'string',\s*enum:\s*\['brief_defect',\s*'intentional_variant_unclear',\s*'tool_failure'\]\s*\}/,
    'run-plans.js SCHEMAS.implementor 须含 audit_reason 顶层字段 + 三枚举值（inline 同步）')
})

