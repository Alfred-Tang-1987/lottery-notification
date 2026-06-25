// workflow orchestrator —— 多 plan 自动执行（workflow-design.md §4/§5/§13）
// 纯函数/SCHEMAS/PROMPTS inline 自 docs/superpowers/workflows/lib.js —— 改 lib 必须同步改这里。
// 顶层 await = Workflow 入口；agent/parallel/phase/log/args/budget 为 Workflow runtime 注入的全局。

export const meta = {
  name: 'run-plans',
  description: '自动执行 implementation plans：每 task implementor→review chain→commit，plan 级独立 gate',
  phases: [
    { title: 'Bootstrap', detail: '读 config/plan/git log + 生成 frontmatter' },
    { title: 'Plan', detail: '串行 task + review rounds + simplify + commit + plan gate' },
    { title: 'Finalize', detail: '写 manifest + digest' },
  ],
}

// ===== 纯函数（inline 自 lib.js Task 2-4，逐字复制）=====
function detectOscillation(filesTouchedPerRound) {
  if (filesTouchedPerRound.length < 3) return { oscillating: false }
  const cnt = {}
  for (const [i, files] of filesTouchedPerRound.entries()) for (const f of files) (cnt[f] ||= []).push(i)
  for (const [file, rounds] of Object.entries(cnt)) if (rounds.length >= 3) return { oscillating: true, reason: `${file} touched in ${rounds.length} rounds`, file, rounds }
  for (let i = 1; i < filesTouchedPerRound.length; i++) {
    const prev = new Set(filesTouchedPerRound[i - 1]); const curr = filesTouchedPerRound[i]
    const overlap = curr.filter(f => prev.has(f))
    if (overlap.length >= 2 && overlap.length === curr.length) return { oscillating: true, reason: `consecutive rounds fix same files: ${overlap.join(',')}`, files: overlap }
  }
  return { oscillating: false }
}
function buildPrompt(role, ctx = {}) {
  const tpl = PROMPTS[role]; if (!tpl) throw new Error(`unknown role: ${role}`)
  return tpl.replace(/\{\{(\w+)\}\}/g, (_, k) => (k in ctx ? String(ctx[k]) : `{{${k}}}`))
}
function allGreen(...reviews) { return reviews.every(r => r && r.status === 'ok') }
function unionFiles(...reviews) {
  const set = new Set(); for (const r of reviews) for (const f of (r?.diagnostics?.files_touched || [])) set.add(f); return [...set]
}
// 已被 collectReviewFindings 取代（orchestrator fix-round 用）；保留为通用工具 + 向后兼容。
function issuesFromReviews(...reviews) {
  const out = []; for (const r of reviews) if (r && r.status === 'failed') out.push(...(r.diagnostics?.issues || [])); return out
}

// 收集三类 review 的发现并归一化为结构化数组（orchestrator fix-round 反馈管道）—— inline 自 lib.js
// spec/quality 存 diagnostics.issues；hunter 存 diagnostics.silent_failures（不同 key！
// 旧 issuesFromReviews 只读 issues → hunter 发现被完全丢弃，Bug 2）。
// items 可能是 string 或 object → 统一归一化为 {source, severity?, title, file?, fix?}。
function collectReviewFindings(spec, qual, hunt) {
  const out = []
  const push = (r, source, key) => {
    if (!r || r.status !== 'failed') return
    for (const it of (r.diagnostics?.[key] || [])) {
      if (it && typeof it === 'object') out.push({ source, severity: it.severity, title: it.title || String(it), file: it.file, fix: it.fix })
      else out.push({ source, title: String(it) })
    }
  }
  push(spec, 'spec', 'issues')
  push(qual, 'quality', 'issues')
  push(hunt, 'hunter', 'silent_failures')
  return out
}

// 把 collectReviewFindings 的结构化数组序列化为 implementor 可读的多行字符串 —— inline 自 lib.js
// 自描述格式：[source|severity] title — fix: ... (file)。空数组 → 空串（implCtx 约定）。
// 替代旧的 lossy .join('; ')（对象 toString → [object Object]，Bug 1）。
function formatFindings(findings) {
  if (!Array.isArray(findings) || findings.length === 0) return ''
  return findings.map(f => {
    const tag = f.severity ? `[${f.source}|${f.severity}]` : `[${f.source}]`
    const fix = f.fix ? ` — fix: ${f.fix}` : ''
    const file = f.file ? ` (${f.file})` : ''
    return `${tag} ${f.title}${fix}${file}`
  }).join('\n')
}

// 判断错误是否 model 限额耗尽（§2.4 双重检测的捕获路径）—— inline 自 lib.js
function isQuotaError(e) {
  const s = String(e?.message || e || '').toLowerCase()
  return /quota|rate.?limit|429|overloaded|insufficient.*balance|credit|capacity/i.test(s)
}
function errStr(e) {
  return String(e?.message || e || '').slice(0, 200)
}

// 把 completed id 归一化为 plan-scoped key "plan-{seq}/T-{id}"（inline 自 lib.js）。
// 避免跨 plan 同名 task 误跳过：去 plan 前缀会让 Plan 02 的 T2 被 Plan 01 的 T2 误 skip。
function normalizeCompleted(ids) {
  return ids.map(id => {
    const m = String(id).match(/^(?:plan-)?(\d+)\/+(T[\w-]+)$/i)
    return m ? `plan-${m[1]}/${m[2]}` : String(id)
  })
}

// args.plan 与 plan.id/plan.seq 的宽松匹配（Bug 10）—— inline 自 lib.js
// 容忍 string/number/padded-seq/"plan-" 前缀差异。
function matchesPlanFilter(plan, planArg) {
  if (!planArg) return true
  const a = String(planArg)
  if (a === plan.id || a === plan.seq) return true
  const n = Number(a)
  if (!Number.isNaN(n)) {
    if (Number(plan.seq) === n) return true
    const idNum = Number(String(plan.id).replace(/^plan-/i, ''))
    if (!Number.isNaN(idNum) && idNum === n) return true
  }
  return false
}

// ===== 条件渲染 helpers（inline 自 lib.js；通用性：彩票特有内容靠 config 驱动，prompt 保持单一模板）=====
// orchestrator 显式传空串（非 undefined），buildPrompt 才会把占位符替换为空而非残留 {{k}}。
function formatReferencePaths(paths) {
  if (!Array.isArray(paths) || paths.length === 0) return ''
  const lines = paths.map(p => `- ${p}`).join('\n')
  return `## Reference Documents (authoritative — match these exactly)
${lines}
Read the relevant section(s) BEFORE implementing/reviewing number/play/prize/rule logic. Deviations from these authoritative rules are bugs (e.g. positional vs partition number comparison).`
}
const LANGUAGE_CHECKLISTS = {
  python: `## Language-specific checks (Python / FastAPI / SQLModel)
- SQL injection: f-strings/concat in queries → parameterized queries
- Command injection: unvalidated input in shell → subprocess with list args
- Bare except / except: pass → catch specific exceptions
- Swallowed exceptions / silent failures → log + handle explicitly
- Mutable default args (def f(x=[])) → use None sentinel
- value == None → use value is None
- Shadowing builtins (list, dict, str, id)
- Missing type hints on public functions; Any overuse; missing Optional for nullable
- Blocking calls inside async (FastAPI: no sync IO in async handlers — offload or use sync def)
- N+1 queries in loops → batch / select_related
- Missing context managers (with) for files/DB/resources
- print() instead of logging; from module import *`,
  general: `## Quality checks (general)
- Clean separation of concerns; proper error handling; type safety where applicable
- DRY without premature abstraction; edge cases handled`,
}
function languageChecklist(language) {
  return LANGUAGE_CHECKLISTS[language] || LANGUAGE_CHECKLISTS.general
}
// 组装 gate 验证命令序列：full_test_command + lint_command + extra_lint_commands（去重去空）。
// 架构纪律（如 domain-zero-IO）靠 extra_lint_commands 承载，gate 自动强制，不靠 prompt 人眼。
function gateCommands(config) {
  const cmds = []
  if (config?.full_test_command) cmds.push({ kind: 'test', command: config.full_test_command })
  if (config?.lint_command) cmds.push({ kind: 'lint', command: config.lint_command })
  for (const c of (config?.extra_lint_commands || [])) if (c) cmds.push({ kind: 'lint', command: c })
  return cmds
}

// ===== SCHEMAS（inline 自 lib.js Task 5，去 export）=====
function reviewSchema() {
  return { type: 'object', required: ['status'], additionalProperties: true,
    properties: {
      status: { type: 'string', enum: ['ok', 'failed', 'model_unavailable'] },
      diagnostics: { type: 'object', properties: { files_touched: { type: 'array' }, issues: { type: 'array' } } },
      summary: { type: 'string' },
    } }
}

const SCHEMAS = {
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
      status: { type: 'string', enum: ['ok', 'done_with_concerns', 'failed', 'blocked', 'needs_context', 'model_unavailable'] },
      evidence: { type: 'object', required: ['tests_exit_code', 'files_changed', 'pytest_summary'],
        properties: { tests_exit_code: { type: 'integer' }, files_changed: { type: 'array' }, pytest_summary: { type: 'string' } } },
      diagnostics: { type: 'object', properties: { blocked_category: { type: 'string' }, last_error: { type: 'string' }, suggested_fix: { type: 'string' }, concerns: { type: 'array' } } },
      summary: { type: 'string' },
    },
  },
  specReview: reviewSchema(),
  qualityReviewer: reviewSchema(),
  hunter: { type: 'object', required: ['status'], additionalProperties: true,
    properties: { status: { type: 'string', enum: ['ok', 'failed', 'model_unavailable'] },
      diagnostics: { type: 'object', properties: { files_touched: { type: 'array' }, silent_failures: { type: 'array' } } },
      summary: { type: 'string' } } },
  simplify: { type: 'object', required: ['evidence'], additionalProperties: true,
    properties: { evidence: { type: 'object', required: ['changed', 'files_changed'],
      properties: { changed: { type: 'boolean' }, files_changed: { type: 'array' } } }, summary: { type: 'string' } } },
  commit: { type: 'object', required: ['status', 'evidence'], additionalProperties: true,
    properties: { status: { type: 'string', enum: ['ok', 'failed', 'model_unavailable'] },
      evidence: { type: 'object', required: ['commit_sha', 'committed_files'],
        properties: { commit_sha: { type: 'string' }, committed_files: { type: 'array' }, tests_at_commit: { type: 'integer' } } },
      diagnostics: { type: 'object' }, summary: { type: 'string' } } },
  contextFetcher: { type: 'object', required: ['diagnostics'], additionalProperties: true,
    properties: { diagnostics: { type: 'object', required: ['context'], properties: { context: { type: 'string' } } }, summary: { type: 'string' } } },
  gate: { type: 'object', required: ['status', 'evidence'], additionalProperties: true,
    properties: { status: { type: 'string', enum: ['ok', 'failed', 'model_unavailable'] },
      evidence: { type: 'object', required: ['tests_exit_code', 'pytest_summary', 'lint_results'],
        properties: { tests_exit_code: { type: 'integer' }, pytest_summary: { type: 'string' }, lint_results: { type: 'array' } } }, summary: { type: 'string' } } },
  finalReport: { type: 'object', required: ['summary'], additionalProperties: true,
    properties: { evidence: { type: 'object', properties: { manifest_path: { type: 'string' } } }, summary: { type: 'string' } } },
}

// ===== PROMPTS（inline 自 lib.js Task 6，增强版 bootstrap，去 export）=====
const PROMPTS = {
  bootstrap: `You are the BOOTSTRAP agent for the workflow orchestrator. Read project state and return structured data. You MAY write YAML frontmatter to plan files that lack it (idempotent). Modify no other files.

Inputs: configPath={{configPath}} plansDir={{plansDir}} runTs={{runTs}}

Steps:
1. Read {{configPath}} → {test_command, full_test_command, build_command, lint_command, extra_lint_commands, spec_path, reference_paths, language}. extra_lint_commands / reference_paths are OPTIONAL (may be absent → treat as [] / []).
2. Config smoke: run test_command with --collect-only. 判断：命令本身不存在（command not found / No such file: pytest）→ status=failed（环境/typo）；命令存在但 collect 失败（no module named pytest / pyproject.toml 不存在 / no tests collected / 业务代码未初始化）→ 记录 'project not yet initialized' 到 summary，status 仍 ok（业务代码由后续 task 创建，预期）。
3. For each {{plansDir}}/*.md: if frontmatter (starts with ---) read task models; else generate — extract LEAF ids (## Task N with ### Task NX children → only NX; else N), modelHint (title contains 安全|加密|认证|JWT|CSRF|Fernet|算法|比对|策略|边界|集成|接口 → opus, else omit), write frontmatter at file top. Idempotent. Record each plan's file (full path) and seq (last two digits of filename, e.g. 01).
4. git log → completed task ids via convention feat(plan-X/T-Y).
5. git status --porcelain → dirty_tree.
6. For each leaf task return its model (sonnet|opus|undefined→sonnet) and title (the description text from the Task header).

Return {status, evidence:{config (include ALL fields read in step 1, even optional ones if present), plans:[{id, file, seq, tasks:[{id, model, title}]}], completed:[...], dirty_tree}, summary}.
RED FLAG: evidence 必须是真实读取结果，绝不编造。`,

  implementor: `You are the IMPLEMENTOR for {{taskId}} (plan {{planId}}). TDD strict (RED→GREEN→REFACTOR). {{retryNote}}

Inputs: specPath={{specPath}} testCommand={{testCommand}} planFile={{planFilePath}} taskId={{taskId}} fixIssues={{fixIssues}} fetchedContext={{fetchedContext}}
{{referencePaths}}
{{fetchedContext}}

Steps:
1. Read {{planFilePath}}, locate {{taskId}} section: files to create/modify, tests to write.
2. Read {{specPath}} relevant section; implement to spec. If reference documents are listed above, read the relevant rule section BEFORE writing number/play/prize logic.
3. RED: write ONE minimal failing test for one behavior. Run {{testCommand}}; CONFIRM it fails — and fails for the RIGHT reason (feature missing), not a typo/import error. A test that passes immediately proves nothing (you may be testing existing behavior) — fix the test.
4. GREEN: minimal code to pass the test. Don't add features or refactor beyond the test.
5. REFACTOR: clean up (dedupe, better names, extract helpers). Tests stay green.
6. Self-review (see checklist below).
7. Run {{testCommand}}; record pytest summary + exit code. If fixIssues non-empty, this round fixes them (review findings from spec/quality/hunter). If fetchedContext non-empty, it is REFERENCE MATERIAL to read — do NOT modify or "fix" it; use it to unblock.

## Good Tests
- One behavior per test ("and" in the name → split it)
- Clear name describing behavior
- Real code, not mocks (unless unavoidable)

## Self-Review Checklist (before reporting)
- Completeness: every spec requirement implemented? edge cases handled? nothing missed?
- Quality: best work? names match what things do? clean & maintainable?
- Discipline: avoided overbuilding (YAGNI)? built only what was requested? followed existing patterns?
- Testing: tests verify real behavior (not mock behavior)? comprehensive?

If self-review finds issues, fix them now.

Return {status, evidence:{tests_exit_code, files_changed:[...], pytest_summary}, diagnostics:{blocked_category, last_error, suggested_fix, concerns} (diagnostics only if blocked/done_with_concerns), summary}.
- status=ok: done, tests_exit_code=0.
- status=done_with_concerns: done (tests green) but you have doubts about correctness/scope → fill diagnostics.concerns (array). Orchestrator records them and proceeds to review.
- status=blocked: 障碍 (interface|file|spec|dependency|external) → fill diagnostics.
- status=needs_context: missing info → fill diagnostics.blocked_category + last_error.
RED FLAG: tests_exit_code 必须真实，绝不编造 0。绝不跳过测试。遇障碍宁可 blocked 也不要伪造通过。若遇到 model 限额耗尽（quota/rate-limit/429 错误），返回 status:'model_unavailable'（非 failed/blocked），让 orchestrator halt 并保存进度。`,

  specReview: `You are the SPEC-REVIEWER (model opus). Verify implementor built EXACTLY what was requested — nothing missing, nothing extra, no misunderstanding. Verdict on CURRENT working tree (HEAD or staged).

Inputs: specPath={{specPath}} taskId={{taskId}} planFile={{planFilePath}} changedHint={{filesChanged}}{{concernsHint}}
{{referencePaths}}

Steps:
1. git diff (or read changed files) for this task.
2. Read {{specPath}} section governing {{taskId}}. If reference documents are listed above, verify number/play/prize/rule logic matches them (e.g. positional vs partition comparison must match the lottery type's rule).
3. Verify THREE dimensions (don't trust the implementer report — read the actual code):
   a. MISSING requirements: anything in spec not implemented? claimed-working but not actually done?
   b. EXTRA / over-build (YAGNI): anything built that spec did NOT request? unrequested features, over-engineering, "nice to haves"? This is critical — flag any functionality the spec forbids or didn't ask for.
   c. MISUNDERSTANDING: requirement interpreted differently than intended? right feature wrong way?
4. Record files_touched (files in the diff).

Return {status (ok|failed), diagnostics:{files_touched:[...], issues:[<dimension>: <spec requirement>: <code gap or over-build>]}, summary}.
RED FLAG: ok 仅当三维度全清——逐条 spec 全符合 AND 无越界。绝不模糊通过。越界（spec 未要求的功能，尤其是合规红线禁止类如预测/推荐）必须 failed。issues 要具体（哪条 spec + 代码哪里不符/越界 + file:line）。若遇到 model 限额耗尽（quota/rate-limit/429 错误），返回 status:'model_unavailable'（非 failed），让 orchestrator halt 并保存进度。`,

  qualityReviewer: `You are the QUALITY-REVIEWER (model opus). Review code quality: architecture, boundaries, types, immutability, error handling, naming. Verdict on CURRENT tree.

Inputs: taskId={{taskId}} changedHint={{filesChanged}}
{{languageChecklist}}

## Universal quality checks
- 函数 <50 行, 文件 <800 行, 无深层嵌套 (>4), 错误显式处理, 无 mutation, 无硬编码值, 命名清晰.
- Each file has one clear responsibility; units decomposed so they can be tested independently.
- Did this change create new large files or significantly grow existing ones? (Don't flag pre-existing sizes — focus on what this change contributed.)

## Steps
1. Read changed files.
2. Check universal checks + the language-specific checklist above. (Note: architectural discipline like layer-purity is enforced automatically by the gate's lint commands — you focus on code a human must judge; do NOT invent layer rules not in the checklist.)
3. Record files_touched.

## Calibration
Categorize issues by ACTUAL severity — not everything is Critical. Acknowledge what was done well (strengths) before listing issues; accurate praise helps the implementer trust the rest.

Return {status (ok|failed), diagnostics:{files_touched:[...], issues:[{severity: Critical|Important|Minor, title, file, fix}]}, summary}.
RED FLAG: ok 仅当无 Critical/Important 问题。Critical/Important（架构/安全/正确性）必须 failed；仅 Minor 可 ok（记入 issues）。若遇到 model 限额耗尽（quota/rate-limit/429 错误），返回 status:'model_unavailable'（非 failed），让 orchestrator halt 并保存进度。`,

  hunter: `You are the SILENT-FAILURE-HUNTER. Hunt swallowed errors, bad fallbacks, missing error propagation, swallowed exceptions, except:pass, broad except hiding bugs, default values masking failures. Verdict on CURRENT tree.

Inputs: taskId={{taskId}} changedHint={{filesChanged}}

Steps:
1. Read changed files.
2. Find:
   - try/except that pass or log-only; bare except hiding bugs; errors converted to null/empty with no context
   - fallback returning wrong-type default; default values masking real failure; .catch(() => [])
   - unhandled None; ignored return values; missing await; fire-and-forget without error path
   - network/file/db paths with NO timeout or error handling
   - transactional work with no rollback on failure
   - lost stack traces (rethrow without context); generic rethrows
   - logs with wrong severity / log-and-forget (no handling after logging)
3. Record files_touched.

Return {status (ok|failed), diagnostics:{files_touched:[...], silent_failures:[...]}, summary}.
RED FLAG: 只报真正的静默失败（会导致 bug 被隐藏），不报刻意的优雅降级（有日志+合理 fallback）。若遇到 model 限额耗尽（quota/rate-limit/429 错误），返回 status:'model_unavailable'（非 failed），让 orchestrator halt 并保存进度。`,

  simplify: `You are SIMPLIFY. Reduce code: dedupe, remove dead code, tighten naming, lower complexity. Behavior MUST be preserved (tests still pass). Be honest about whether you changed anything.

Inputs: taskId={{taskId}} filesChanged={{filesChanged}} simplifyFailed={{simplifyFailed}}

## Principles
1. clarity over cleverness
2. consistency with EXISTING repo style (match surrounding code's conventions)
3. preserve behavior exactly
4. simplify only where the result is demonstrably easier to maintain

## Targets
- dedupe; remove dead code & unused imports; remove commented-out code & stray debug logs
- tighten naming; avoid nested ternaries; break long chains into intermediate vars when clearer
- extract deeply nested logic into named functions; replace complex conditionals with early returns where clearer
- UNWIND over-abstracted single-use helpers (collapse back inline if the abstraction serves only one caller)
- altitude alignment: keep each block at one level of abstraction (don't mix high-level orchestration with low-level detail in the same function)

## Steps
1. Read changed files.
2. Apply only safe simplifications (behavior-preserving).
3. Run tests mentally or note you cannot (orchestrator will re-run review).
4. HONESTLY report changed (bool) + files_changed.

Return {evidence:{changed, files_changed:[...]}, summary}.
RED FLAG: changed 必须如实。orchestrator 不信任自报，会无条件重跑 review。若 simplifyFailed=true，跳过（orchestrator 已回退你的上一轮）。`,

  commit: `You are COMMIT. Create one atomic commit for task {{taskId}}. {{simplifyRevertNote}}

Inputs: taskId={{taskId}} planId={{planId}} testCommand={{testCommand}} simplifyFailed={{simplifyFailed}} simplifyFiles={{simplifyFiles}}

Steps:
1. If simplifyFailed=true: first git checkout -- each file in simplifyFiles (revert bad simplify), then proceed.
2. git status --porcelain → see staged/unstaged.
3. Run {{testCommand}} on current tree; confirm exit 0. If fail → status=failed (do NOT commit).
4. git add -A; git commit -m "feat({{planIdShort}}/{{taskId}}): {{taskTitle}}" (planIdShort = plan-01 etc).
5. git rev-parse HEAD → commit_sha.

Return {status (ok|failed), evidence:{commit_sha, committed_files:[...], tests_at_commit}, summary}.
RED FLAG: tests exit != 0 时绝不 commit（status=failed）。commit_sha 必须真实。若遇到 model 限额耗尽（quota/rate-limit/429 错误），返回 status:'model_unavailable'（非 failed），让 orchestrator halt 并保存进度。`,

  contextFetcher: `You are CONTEXT-FETCHER. The implementor requested context (NEEDS_CONTEXT). Find and return it. Read-only.

Inputs: needType={{needType}} query={{query}} specPath={{specPath}} workdir={{workdir}}

Steps by needType:
- file/path: grep/glob workdir for query, return paths.
- interface: LSP or regex extract function/class signatures.
- spec/doc: read {{specPath}} or named doc, extract relevant section.
- dependency: read prior task code, extract key impl.
- external: Context7 or WebSearch query.

Return {diagnostics:{context: <findings text>}, summary}.
RED FLAG: context 必须是真实查到的，绝不编造。查不到 → context="not found: <query>"。`,

  gate: `You are PLAN-GATE. Independently re-run verification on the committed SHA (do NOT trust implementor self-report). Run EVERY command below, record real exit codes. Then restore HEAD.

Inputs: sha={{sha}}
Commands to run (JSON array, in order): {{gateCommands}}
Each item is {kind: "test"|"lint", command}. Run ALL of them on the checked-out SHA.

Steps:
1. git checkout {{sha}}.
2. For EACH command in the array: run it, record {command, exit_code, summary}. tests_exit_code = exit code of the FIRST kind:"test" command (0 if none).
3. git checkout - (restore previous HEAD). CRITICAL: must restore or downstream tasks break.
4. If step 3 fails, git checkout <previous-branch> explicitly.

Return {status (ok|failed), evidence:{tests_exit_code, pytest_summary, lint_results:[{command, exit_code}]}, summary}.
- status=ok ONLY if EVERY command exit_code == 0.
RED FLAG: every exit_code 必须真实（你在 committed SHA 上亲跑）。必须 checkout 回原 HEAD。任一 exit != 0 → status=failed（包括 lint 命令——架构纪律如层纯度由 lint 强制）。若遇到 model 限额耗尽（quota/rate-limit/429 错误），返回 status:'model_unavailable'（非 failed），让 orchestrator halt 并保存进度。`,

  finalReport: `You are FINAL-REPORT (mode={{mode}} done|halted). Write the run manifest (the ONLY on-disk write in this workflow) and emit a digest.

Inputs: mode={{mode}} state={{stateJson}} runsDir={{runsDir}} runTs={{runTs}}

Steps:
1. mkdir -p {{runsDir}}.
2. Write {{runsDir}}/manifest.json = {run_ts:{{runTs}}, mode:{{mode}}, plans:[...], per_task:{<taskId>:{status,model,review_rounds,files_touched_per_round,commit_sha,blocked_info}}, result}.
3. If mode=halted: also write {{runsDir}}/blocked.md (human-readable: which task, category, last_error, suggested_fix).
4. Print a digest summary (counts: done/blocked, total tasks, per-plan gate result).

Return {evidence:{manifest_path}, summary: <digest>}.
RED FLAG: manifest 必须真实写入磁盘（你 ls 确认）。stateJson 是 orchestrator 传入的完整状态，照实记录。`,
}

// ===== state（§4.4）=====
const state = {
  runTs: null, config: null, completed: [], currentPlan: null, currentTask: null,
  perTask: {},  // {taskId: {planId, status, model, review_rounds, files_touched_per_round, commit_sha, blocked_info}}
}

// ===== halt（§13a：累积 blocked_info → finalReport halted 模式写盘 + surface）=====
async function finalReportWithFallback(ctx) {
  for (const m of ['opus', 'sonnet', 'haiku']) {
    try {
      return await agent(buildPrompt('finalReport', ctx),
        { schema: SCHEMAS.finalReport, model: m, label: `final-report:${m}` })
    } catch (e) { log(`finalReport ${m} 不可用: ${errStr(e)}, 试下一个`) }
  }
  log('fallback 链全失败，用环境默认 model 保存')
  return await agent(buildPrompt('finalReport', ctx),
    { schema: SCHEMAS.finalReport, label: 'final-report:default' })
}

async function halt(plan, task, r) {
  const tid = task?.id || 'unknown'
  state.perTask[tid] = { ...(state.perTask[tid] || {}), status: 'blocked',
    blocked_info: {
      plan: plan?.id, task: tid, reason: r.reason,
      category: r.diag?.blocked_category || r.diag?.file || r.diag?.reason || null,
      last_error: r.diag?.last_error || r.diag?.summary || r.reason,
      suggested_fix: r.diag?.suggested_fix || null,
      quota_exhausted: r.reason === 'model_unavailable',
      raw: r.diag || {},
    } }
  phase('Finalize')
  await finalReportWithFallback({ mode: 'halted', stateJson: JSON.stringify(state), runsDir: 'runs', runTs: state.runTs })
  log(`✗ HALT: ${r.reason} (plan ${plan?.id}, task ${tid})`)
}

// ===== runTask（§13a：implementor + 升级链 + review rounds + simplify + commit）=====
async function runTask(plan, task) {
  state.currentTask = task.id
  const cfg = state.config
  const planIdShort = `plan-${plan.seq}`
  state.perTask[task.id] = { planId: plan.id, status: 'in_progress', model: task.model || 'sonnet', review_rounds: 0, files_touched_per_round: [], commit_sha: null, blocked_info: null }
  log(`▶ ${task.id} (${task.model || 'sonnet'}): 派发 implementor — TDD 可能含长命令(uv sync/build/全量测试)，正常耗时请等待；/workflows 可看实时工具调用`)

  // —— implementor + BLOCKED 升级链（§2.3）——
  let model = task.model || 'sonnet'
  const implCtx = (fix, note, ctx = '') => ({ planId: plan.id, taskId: task.id, planFilePath: plan.file, specPath: cfg.spec_path, testCommand: cfg.test_command, fixIssues: fix, retryNote: note, fetchedContext: ctx, referencePaths: formatReferencePaths(cfg.reference_paths) })
  let impl
  try {
    impl = await agent(buildPrompt('implementor', implCtx('', '')), { schema: SCHEMAS.implementor, model, label: `impl:${task.id}` })
  } catch (e) {
    if (isQuotaError(e)) return { halted: true, reason: 'model_unavailable', diag: { model, error: errStr(e) } }
    throw e
  }
  if (impl.status === 'model_unavailable') return { halted: true, reason: 'model_unavailable', diag: impl.diagnostics }
  if (impl.status === 'blocked') {
    if (model === 'opus') return { halted: true, reason: 'opus BLOCKED', diag: impl.diagnostics }
    model = 'opus'
    try {
      impl = await agent(buildPrompt('implementor', implCtx('', '上一轮 sonnet BLOCKED，升级 opus 重试。')), { schema: SCHEMAS.implementor, model: 'opus', label: `impl:${task.id}:opus` })
    } catch (e) {
      if (isQuotaError(e)) return { halted: true, reason: 'model_unavailable', diag: { model: 'opus', error: errStr(e) } }
      throw e
    }
    if (impl.status === 'model_unavailable') return { halted: true, reason: 'model_unavailable', diag: impl.diagnostics }
    if (impl.status === 'blocked') return { halted: true, reason: 'opus BLOCKED', diag: impl.diagnostics }
  }
  // —— needs_context: dispatch contextFetcher, retry implementor with context (§8.1) ——
  if (impl.status === 'needs_context') {
    let ctxr
    try {
      ctxr = await agent(buildPrompt('contextFetcher', {
        needType: impl.diagnostics?.blocked_category || 'file',
        query: impl.diagnostics?.last_error || impl.diagnostics?.suggested_fix || '',
        specPath: cfg.spec_path, workdir: '.',
      }), { schema: SCHEMAS.contextFetcher, label: `ctx:${task.id}` })
    } catch (e) {
      if (isQuotaError(e)) return { halted: true, reason: 'model_unavailable', diag: { model, error: errStr(e) } }
      throw e
    }
    try {
      const fetchedCtx = ctxr.diagnostics?.context || ''
      impl = await agent(buildPrompt('implementor', implCtx('', `补充上下文后重试。`, fetchedCtx)),
                         { schema: SCHEMAS.implementor, model, label: `impl:${task.id}:ctx` })
    } catch (e) {
      if (isQuotaError(e)) return { halted: true, reason: 'model_unavailable', diag: { model, error: errStr(e) } }
      throw e
    }
    if (impl.status === 'model_unavailable') return { halted: true, reason: 'model_unavailable', diag: impl.diagnostics }
    // Bug 8: needs_context → blocked 时先升 opus 再 halt（mirror 初始路径 433-444）
    if (impl.status === 'blocked') {
      if (model === 'opus') return { halted: true, reason: 'opus BLOCKED after context-fetch', diag: impl.diagnostics }
      model = 'opus'
      try {
        impl = await agent(buildPrompt('implementor', implCtx('', '上下文补充后 sonnet 仍 BLOCKED，升级 opus 重试。', fetchedCtx)), { schema: SCHEMAS.implementor, model: 'opus', label: `impl:${task.id}:ctx:opus` })
      } catch (e) {
        if (isQuotaError(e)) return { halted: true, reason: 'model_unavailable', diag: { model: 'opus', error: errStr(e) } }
        throw e
      }
      if (impl.status === 'model_unavailable') return { halted: true, reason: 'model_unavailable', diag: impl.diagnostics }
      if (impl.status === 'blocked') return { halted: true, reason: 'opus BLOCKED after context-fetch', diag: impl.diagnostics }
    }
    // Bug 9: needs_context → failed 时允许一次重试（mirror 初始路径 467-477），非直接 halt
    if (impl.status === 'failed') {
      try {
        impl = await agent(buildPrompt('implementor', implCtx('', '上下文补充后仍 failed，重试一次。', fetchedCtx)), { schema: SCHEMAS.implementor, model, label: `impl:${task.id}:ctx:retry` })
      } catch (e) {
        if (isQuotaError(e)) return { halted: true, reason: 'model_unavailable', diag: { model, error: errStr(e) } }
        throw e
      }
      if (impl.status === 'model_unavailable') return { halted: true, reason: 'model_unavailable', diag: impl.diagnostics }
      if (impl.status !== 'ok' && impl.status !== 'done_with_concerns') return { halted: true, reason: `implementor ${impl.status} after context-fetch retry`, diag: impl.diagnostics }
    }
    if (impl.status !== 'ok' && impl.status !== 'done_with_concerns') return { halted: true, reason: `implementor ${impl.status} after context-fetch`, diag: impl.diagnostics }
  }
  // —— failed: retry once → halt (§4.4) ——
  if (impl.status === 'failed') {
    try {
      impl = await agent(buildPrompt('implementor', implCtx('', '上次 failed，重试一次。')),
                         { schema: SCHEMAS.implementor, model, label: `impl:${task.id}:retry` })
    } catch (e) {
      if (isQuotaError(e)) return { halted: true, reason: 'model_unavailable', diag: { model, error: errStr(e) } }
      throw e
    }
    if (impl.status === 'model_unavailable') return { halted: true, reason: 'model_unavailable', diag: impl.diagnostics }
    if (impl.status !== 'ok' && impl.status !== 'done_with_concerns') return { halted: true, reason: `implementor ${impl.status} after retry`, diag: impl.diagnostics }
  }
  // done_with_concerns: 记录疑虑，继续进 review（不 halt）；轻量透传给 specReview 作 focusHint。
  let concerns = []
  if (impl.status === 'done_with_concerns') {
    concerns = impl.diagnostics?.concerns || []
    state.perTask[task.id].concerns = concerns
    log(`⚠ ${task.id} done_with_concerns: ${concerns.join('; ') || '(no detail)'}`)
  }
  const concernsHint = concerns.length ? `\n## Implementor Concerns (verify these)\n${concerns.map(c => '- ' + c).join('\n')}` : ''
  let filesChanged = impl.evidence.files_changed || []

  // —— review rounds（max 3，§5）——
  for (let round = 1; round <= 3; round++) {
    state.perTask[task.id].review_rounds = round
    const fc = filesChanged.join(',')
    const [spec, qual, hunt] = await parallel([
      async () => { try { return await agent(buildPrompt('specReview', { taskId: task.id, specPath: cfg.spec_path, planFilePath: plan.file, filesChanged: fc, concernsHint, referencePaths: formatReferencePaths(cfg.reference_paths) }), { schema: SCHEMAS.specReview, model: 'opus', phase: `Plan ${plan.id}`, label: `spec:${task.id}:r${round}` }) } catch (e) { return { status: isQuotaError(e) ? 'model_unavailable' : 'agent_error', diagnostics: { error: errStr(e) } } } },
      async () => { try { return await agent(buildPrompt('qualityReviewer', { taskId: task.id, filesChanged: fc, languageChecklist: languageChecklist(cfg.language) }), { schema: SCHEMAS.qualityReviewer, model: 'opus', label: `qual:${task.id}:r${round}` }) } catch (e) { return { status: isQuotaError(e) ? 'model_unavailable' : 'agent_error', diagnostics: { error: errStr(e) } } } },
      async () => { try { return await agent(buildPrompt('hunter', { taskId: task.id, filesChanged: fc }), { schema: SCHEMAS.hunter, model: 'sonnet', label: `hunt:${task.id}:r${round}` }) } catch (e) { return { status: isQuotaError(e) ? 'model_unavailable' : 'agent_error', diagnostics: { error: errStr(e) } } } },
    ])
    if (spec?.status === 'agent_error' || qual?.status === 'agent_error' || hunt?.status === 'agent_error') {
      return { halted: true, reason: 'agent_error', diag: { spec: spec?.diagnostics, qual: qual?.diagnostics, hunt: hunt?.diagnostics } }
    }
    if (spec?.status === 'model_unavailable' || qual?.status === 'model_unavailable' || hunt?.status === 'model_unavailable') {
      return { halted: true, reason: 'model_unavailable', diag: { spec: spec?.diagnostics, qual: qual?.diagnostics, hunt: hunt?.diagnostics } }
    }
    state.perTask[task.id].files_touched_per_round.push(unionFiles(spec, qual, hunt))
    const osc = detectOscillation(state.perTask[task.id].files_touched_per_round)
    if (osc.oscillating) return { halted: true, reason: 'OSCILLATING', diag: osc }
    if (allGreen(spec, qual, hunt)) break
    if (round === 3) return { halted: true, reason: 'review max rounds', diag: { spec: spec.diagnostics, qual: qual.diagnostics, hunt: hunt.diagnostics } }
    const findings = collectReviewFindings(spec, qual, hunt)
    try {
      impl = await agent(buildPrompt('implementor', implCtx(formatFindings(findings), `修复 review round ${round} 问题（${findings.length} 项发现：spec/quality/hunter）。`)), { schema: SCHEMAS.implementor, model, label: `impl:${task.id}:fix${round}` })
    } catch (e) {
      if (isQuotaError(e)) return { halted: true, reason: 'model_unavailable', diag: { model, error: errStr(e) } }
      throw e
    }
    if (impl.status === 'model_unavailable') return { halted: true, reason: 'model_unavailable', diag: impl.diagnostics }
    // Bug 4: fix-round implementor 返回 blocked/failed/needs_context 时不能静默忽略——
    // 否则 orchestrator 在 stale code 上继续下一轮 review，必然重复发现同样问题 → 浪费轮次。
    // 初始 dispatch 已有 opus 升级链 + context-fetch 路径；fix-round 内 halt 暴露问题而非静默循环。
    if (impl.status === 'blocked' || impl.status === 'failed' || impl.status === 'needs_context') {
      return { halted: true, reason: `implementor ${impl.status} in fix-round ${round}`, diag: impl.diagnostics }
    }
    filesChanged = impl.evidence.files_changed || filesChanged
  }

  // —— simplify（max 1，§5.2：无条件重跑 review；失败则回退）——
  let simplifyFailed = false, simplifyFiles = []
  let simp
  try {
    simp = await agent(buildPrompt('simplify', { taskId: task.id, filesChanged: filesChanged.join(','), simplifyFailed: 'false' }), { schema: SCHEMAS.simplify, label: `simp:${task.id}` })
  } catch (e) {
    if (isQuotaError(e)) return { halted: true, reason: 'model_unavailable', diag: { model, error: errStr(e) } }
    throw e
  }
  if (simp.evidence.changed) {
    const fc = (simp.evidence.files_changed || []).join(',')
    const [spec2, qual2, hunt2] = await parallel([
      async () => { try { return await agent(buildPrompt('specReview', { taskId: task.id, specPath: cfg.spec_path, planFilePath: plan.file, filesChanged: fc, concernsHint: '', referencePaths: formatReferencePaths(cfg.reference_paths) }), { schema: SCHEMAS.specReview, model: 'opus', label: `spec:${task.id}:simp` }) } catch (e) { return { status: isQuotaError(e) ? 'model_unavailable' : 'agent_error', diagnostics: { error: errStr(e) } } } },
      async () => { try { return await agent(buildPrompt('qualityReviewer', { taskId: task.id, filesChanged: fc, languageChecklist: languageChecklist(cfg.language) }), { schema: SCHEMAS.qualityReviewer, model: 'opus', label: `qual:${task.id}:simp` }) } catch (e) { return { status: isQuotaError(e) ? 'model_unavailable' : 'agent_error', diagnostics: { error: errStr(e) } } } },
      async () => { try { return await agent(buildPrompt('hunter', { taskId: task.id, filesChanged: fc }), { schema: SCHEMAS.hunter, model: 'sonnet', label: `hunt:${task.id}:simp` }) } catch (e) { return { status: isQuotaError(e) ? 'model_unavailable' : 'agent_error', diagnostics: { error: errStr(e) } } } },
    ])
    if (spec2?.status === 'agent_error' || qual2?.status === 'agent_error' || hunt2?.status === 'agent_error') {
      return { halted: true, reason: 'agent_error', diag: { spec2: spec2?.diagnostics, qual2: qual2?.diagnostics, hunt2: hunt2?.diagnostics } }
    }
    if (spec2?.status === 'model_unavailable' || qual2?.status === 'model_unavailable' || hunt2?.status === 'model_unavailable') {
      return { halted: true, reason: 'model_unavailable', diag: { spec2: spec2?.diagnostics, qual2: qual2?.diagnostics, hunt2: hunt2?.diagnostics } }
    }
    if (!allGreen(spec2, qual2, hunt2)) { simplifyFailed = true; simplifyFiles = simp.evidence.files_changed || [] }
  }
  if (simplifyFailed && simplifyFiles.length === 0) return { halted: true, reason: 'simplify reported changed but no files', diag: simp.evidence }

  // —— commit（§5：状态原子转换；simplify 回退委托此 agent）——
  let commit
  try {
    commit = await agent(buildPrompt('commit', { taskId: task.id, planId: plan.id, planIdShort, taskTitle: task.title || task.id, testCommand: cfg.test_command, simplifyFailed: String(simplifyFailed), simplifyFiles: simplifyFiles.join(','), simplifyRevertNote: simplifyFailed ? `Simplify 回退：重跑 review 失败，还原 ${simplifyFiles.length} 个文件。` : '' }), { schema: SCHEMAS.commit, label: `commit:${task.id}` })
  } catch (e) {
    if (isQuotaError(e)) return { halted: true, reason: 'model_unavailable', diag: { model, error: errStr(e) } }
    throw e
  }
  if (commit.status === 'model_unavailable') return { halted: true, reason: 'model_unavailable', diag: commit.diagnostics }
  if (commit.status !== 'ok') return { halted: true, reason: 'commit failed', diag: commit.diagnostics }
  state.perTask[task.id].status = 'committed'
  state.perTask[task.id].commit_sha = commit.evidence.commit_sha
  log(`✓ ${task.id} committed @ ${commit.evidence.commit_sha}`)
  return { halted: false }
}

// ===== 顶层编排（Workflow 入口）=====
phase('Bootstrap')
const tsAgent = await agent('Run `date -u +%Y%m%dT%H%M%SZ` and return ONLY the timestamp string, nothing else.', { label: 'get-ts' })
state.runTs = typeof tsAgent === 'string' ? tsAgent.trim() : String(tsAgent)
let boot
try {
  boot = await agent(buildPrompt('bootstrap', { configPath: args.configPath, plansDir: args.plansDir, runTs: state.runTs }), { schema: SCHEMAS.bootstrap, label: 'bootstrap' })
} catch (e) {
  if (isQuotaError(e)) { await halt(null, null, { reason: 'model_unavailable', diag: { model: 'sonnet', error: errStr(e) } }); return { result: 'halted', reason: 'model_unavailable' } }
  throw e
}
if (boot.status !== 'ok') { await halt(null, null, { reason: `bootstrap ${boot.status}`, diag: boot.diagnostics }); return { result: 'halted', reason: `bootstrap ${boot.status}` } }
state.config = boot.evidence.config
// plan-scoped completed：bootstrap 从 git log 解析的 id（"01/T1" / "plan-01/T1" / 裸 "T1"）
// 归一化为 "plan-{seq}/T-{id}"。run-2 旧逻辑【去】plan 前缀→单 plan 内能匹配，但跨 plan 同名
// task（Plan 01/02 都有 T1-T10）会让 Plan 02 的 T2 被 Plan 01 的 T2 误 skip → domain layer 残缺。
// plan-scoped key 修复：见下方比对 `plan-${plan.seq}/${task.id}`。
// args.completed 可手动覆盖（resume 时显式传已 commit 的 plan-scoped id 列表，双保险）。
const _rawCompleted = (Array.isArray(args.completed) && args.completed.length ? args.completed : boot.evidence.completed) || []
state.completed = normalizeCompleted(_rawCompleted)

for (const plan of boot.evidence.plans) {
  if (!matchesPlanFilter(plan, args.plan)) continue
  state.currentPlan = plan.id
  phase(`Plan ${plan.id}`)
  const want = (args.tasks && args.tasks.length) ? new Set(args.tasks.map(String)) : null
  const tasks = plan.tasks.filter(t => !want || want.has(t.id))
  for (const task of tasks) {
    const taskKey = `plan-${plan.seq}/${task.id}`  // plan-scoped：跨 plan 同名 task 不误跳过
    if (state.completed.includes(taskKey)) { log(`skip ${taskKey} (already committed)`); continue }
    let r
    try {
      r = await runTask(plan, task)
    } catch (e) {
      // §2.4：uncaught error 视同 model_unavailable——本环境 agent 抛错（含 429 落 router stderr、不在 Error.message）≈ model 不可用。
      // halt + 保存进度（finalReportWithFallback 依次试 opus/sonnet/haiku），等用户指令 resume，不降级继续开发。
      r = { halted: true, reason: 'model_unavailable', diag: { error: errStr(e) } }
    }
    if (r.halted) { await halt(plan, { id: task.id }, r); return { result: 'halted', reason: r.reason } }
  }
  // plan 级独立 gate（§3）：本 plan 最后 commit SHA 上重跑 test + lint_command + extra_lint_commands
  const lastSha = Object.values(state.perTask).filter(p => p.planId === plan.id && p.commit_sha).at(-1)?.commit_sha
  if (lastSha) {
    const cmds = gateCommands(state.config)
    let gate
    try {
      gate = await agent(buildPrompt('gate', { sha: lastSha, gateCommands: JSON.stringify(cmds) }), { schema: SCHEMAS.gate, label: `gate:${plan.id}`, phase: `Plan ${plan.id}` })
    } catch (e) {
      if (isQuotaError(e)) { await halt(plan, null, { reason: 'model_unavailable', diag: { model: 'sonnet', error: errStr(e) } }); return { result: 'halted', reason: 'model_unavailable' } }
      throw e
    }
    if (gate.status === 'model_unavailable') { await halt(plan, null, { reason: 'model_unavailable', diag: gate.diagnostics }); return { result: 'halted', reason: 'model_unavailable' } }
    if (gate.status !== 'ok') {
      await halt(plan, null, { reason: 'plan gate failed', diag: { sha: lastSha, tests_exit_code: gate.evidence?.tests_exit_code, summary: gate.evidence?.pytest_summary, lint_results: gate.evidence?.lint_results } })
      return { result: 'halted', reason: 'plan gate failed' }
    }
    log(`✓ plan ${plan.id} gate green @ ${lastSha} (${cmds.length} cmd${cmds.length === 1 ? '' : 's'})`)
  } else {
    log(`plan ${plan.id}: no new commits, gate skipped`)
  }
}

phase('Finalize')
await finalReportWithFallback({ mode: 'done', stateJson: JSON.stringify(state), runsDir: 'runs', runTs: state.runTs })
log('✓ workflow done')
return { result: 'done', perTask: state.perTask }
