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
function issuesFromReviews(...reviews) {
  const out = []; for (const r of reviews) if (r && r.status === 'failed') out.push(...(r.diagnostics?.issues || [])); return out
}

// ===== SCHEMAS（inline 自 lib.js Task 5，去 export）=====
function reviewSchema() {
  return { type: 'object', required: ['status'], additionalProperties: true,
    properties: {
      status: { type: 'string', enum: ['ok', 'failed'] },
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

// ===== PROMPTS（inline 自 lib.js Task 6，增强版 bootstrap，去 export）=====
const PROMPTS = {
  bootstrap: `You are the BOOTSTRAP agent for the lottery-notification workflow orchestrator. Read project state and return structured data. You MAY write YAML frontmatter to plan files that lack it (idempotent). Modify no other files.

Inputs: configPath={{configPath}} plansDir={{plansDir}} runTs={{runTs}}

Steps:
1. Read {{configPath}} → {test_command, full_test_command, build_command, lint_command, spec_path, language}.
2. Config smoke: run test_command with --collect-only. If command-not-found or config typo → status=failed with error.
3. For each {{plansDir}}/*.md: if frontmatter (starts with ---) read task models; else generate — extract LEAF ids (## Task N with ### Task NX children → only NX; else N), modelHint (title contains 安全|加密|认证|JWT|CSRF|Fernet|算法|比对|策略|边界|集成|接口 → opus, else omit), write frontmatter at file top. Idempotent. Record each plan's file (full path) and seq (last two digits of filename, e.g. 01).
4. git log → completed task ids via convention feat(plan-X/T-Y).
5. git status --porcelain → dirty_tree.
6. For each leaf task return its model (sonnet|opus|undefined→sonnet) and title (the description text from the Task header).

Return {status, evidence:{config, plans:[{id, file, seq, tasks:[{id, model, title}]}], completed:[...], dirty_tree}, summary}.
RED FLAG: evidence 必须是真实读取结果，绝不编造。`,

  implementor: `You are the IMPLEMENTOR for {{taskId}} (plan {{planId}}). TDD strict (RED→GREEN→REFACTOR). {{retryNote}}

Inputs: specPath={{specPath}} testCommand={{testCommand}} planFile={{planFilePath}} taskId={{taskId}} fixIssues={{fixIssues}}

Steps:
1. Read {{planFilePath}}, locate {{taskId}} section: files to create/modify, tests to write.
2. Read {{specPath}} relevant section; implement to spec.
3. RED: write failing test; run {{testCommand}}; confirm fail.
4. GREEN: minimal impl passing.
5. REFACTOR: clean; tests still green.
6. Self-review vs spec.
7. Run {{testCommand}}; record pytest summary + exit code. If fixIssues non-empty, this round fixes them.

Return {status, evidence:{tests_exit_code, files_changed:[...], pytest_summary}, diagnostics:{blocked_category, last_error, suggested_fix} (only if blocked), summary}.
- status=ok: done, tests_exit_code=0. - status=blocked: 障碍 (interface|file|spec|dependency|external) → fill diagnostics.
RED FLAG: tests_exit_code 必须真实，绝不编造 0。绝不跳过测试。遇障碍宁可 blocked 也不要伪造通过。`,

  specReview: `You are the SPEC-REVIEWER (model opus). Compare implementor's code against spec line-by-line. Verdict on CURRENT working tree (HEAD or staged).

Inputs: specPath={{specPath}} taskId={{taskId}} planFile={{planFilePath}} changedHint={{filesChanged}}

Steps:
1. git diff (or read changed files) for this task.
2. Read {{specPath}} section governing {{taskId}}.
3. For each spec requirement, verify code implements it. Record mismatches.
4. Record files_touched (files in the diff).

Return {status (ok|failed), diagnostics:{files_touched:[...], issues:[<spec requirement>: <code gap>]}, summary}.
RED FLAG: ok 仅当逐条 spec 全符合。绝不模糊通过。issues 要具体（哪条 spec + 代码哪里不符）。`,

  qualityReviewer: `You are the QUALITY-REVIEWER (model opus). Review code quality: architecture, boundaries, types, immutability, error handling, naming. Verdict on CURRENT tree.

Inputs: taskId={{taskId}} changedHint={{filesChanged}}

Steps:
1. Read changed files.
2. Check: 函数 <50 行, 文件 <800 行, 无深层嵌套 (>4), 错误显式处理, 无 mutation, 无硬编码值, 命名清晰.
3. Check domain-layer-zero-IO discipline (app/domain 无 DB/network import) if applicable.
4. Record files_touched.

Return {status (ok|failed), diagnostics:{files_touched:[...], issues:[...]}, summary}.
RED FLAG: ok 仅当无 HIGH 级问题。架构/安全/正确性问题必须 failed。`,

  hunter: `You are the SILENT-FAILURE-HUNTER. Hunt swallowed errors, bad fallbacks, missing error propagation, swallowed exceptions, except:pass, broad except hiding bugs, default values masking failures. Verdict on CURRENT tree.

Inputs: taskId={{taskId}} changedHint={{filesChanged}}

Steps:
1. Read changed files.
2. Find: try/except that pass or log-only, bare except, fallback returning wrong-type default, unhandled None, ignored return values, missing await, fire-and-forget without error path.
3. Record files_touched.

Return {status (ok|failed), diagnostics:{files_touched:[...], silent_failures:[...]}, summary}.
RED FLAG: 只报真正的静默失败（会导致 bug 被隐藏），不报刻意的优雅降级（有日志+合理 fallback）。`,

  simplify: `You are SIMPLIFY. Reduce code: dedupe, remove dead code, tighten naming, lower complexity. Behavior MUST be preserved (tests still pass). Be honest about whether you changed anything.

Inputs: taskId={{taskId}} filesChanged={{filesChanged}} simplifyFailed={{simplifyFailed}}

Steps:
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
RED FLAG: tests exit != 0 时绝不 commit（status=failed）。commit_sha 必须真实。`,

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

  gate: `You are PLAN-GATE. Independently re-run the full test suite on the committed SHA (do NOT trust implementor self-report). Then restore HEAD.

Inputs: sha={{sha}} fullTestCommand={{fullTestCommand}}

Steps:
1. git checkout {{sha}}.
2. Run {{fullTestCommand}}. Record REAL exit code + summary.
3. git checkout - (restore previous HEAD). CRITICAL: must restore or downstream tasks break.
4. If step 3 fails, git checkout <previous-branch> explicitly.

Return {status (ok|failed), evidence:{tests_exit_code, pytest_summary}, summary}.
RED FLAG: tests_exit_code 必须真实（你在 committed SHA 上亲跑）。必须 checkout 回原 HEAD。exit != 0 → status=failed。`,

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
async function halt(plan, task, r) {
  const tid = task?.id || 'unknown'
  state.perTask[tid] = { ...(state.perTask[tid] || {}), status: 'blocked',
    blocked_info: {
      plan: plan?.id, task: tid, reason: r.reason,
      category: r.diag?.blocked_category || r.diag?.file || r.diag?.reason || null,
      last_error: r.diag?.last_error || r.diag?.summary || r.reason,
      suggested_fix: r.diag?.suggested_fix || null,
      raw: r.diag || {},
    } }
  phase('Finalize')
  await agent(buildPrompt('finalReport', { mode: 'halted', stateJson: JSON.stringify(state), runsDir: 'runs', runTs: state.runTs }),
    { schema: SCHEMAS.finalReport, label: 'final-report:halted' })
  log(`✗ HALT: ${r.reason} (plan ${plan?.id}, task ${tid})`)
}

// ===== runTask（§13a：implementor + 升级链 + review rounds + simplify + commit）=====
async function runTask(plan, task) {
  state.currentTask = task.id
  const cfg = state.config
  const planIdShort = `plan-${plan.seq}`
  state.perTask[task.id] = { planId: plan.id, status: 'in_progress', model: task.model || 'sonnet', review_rounds: 0, files_touched_per_round: [], commit_sha: null, blocked_info: null }

  // —— implementor + BLOCKED 升级链（§2.3）——
  let model = task.model || 'sonnet'
  const implCtx = (fix, note) => ({ planId: plan.id, taskId: task.id, planFilePath: plan.file, specPath: cfg.spec_path, testCommand: cfg.test_command, fixIssues: fix, retryNote: note })
  let impl = await agent(buildPrompt('implementor', implCtx('', '')), { schema: SCHEMAS.implementor, model, label: `impl:${task.id}` })
  if (impl.status === 'blocked') {
    if (model === 'opus') return { halted: true, reason: 'opus BLOCKED', diag: impl.diagnostics }
    model = 'opus'
    impl = await agent(buildPrompt('implementor', implCtx('', '上一轮 sonnet BLOCKED，升级 opus 重试。')), { schema: SCHEMAS.implementor, model: 'opus', label: `impl:${task.id}:opus` })
    if (impl.status === 'blocked') return { halted: true, reason: 'opus BLOCKED', diag: impl.diagnostics }
  }
  // —— needs_context: dispatch contextFetcher, retry implementor with context (§8.1) ——
  if (impl.status === 'needs_context') {
    const ctxr = await agent(buildPrompt('contextFetcher', {
      needType: impl.diagnostics?.blocked_category || 'file',
      query: impl.diagnostics?.last_error || impl.diagnostics?.suggested_fix || '',
      specPath: cfg.spec_path, workdir: '.',
    }), { schema: SCHEMAS.contextFetcher, label: `ctx:${task.id}` })
    impl = await agent(buildPrompt('implementor', implCtx(ctxr.diagnostics?.context || '', `补充上下文后重试。context: ${ctxr.diagnostics?.context || ''}`)),
                       { schema: SCHEMAS.implementor, model, label: `impl:${task.id}:ctx` })
    if (impl.status !== 'ok') return { halted: true, reason: `implementor ${impl.status} after context-fetch`, diag: impl.diagnostics }
  }
  // —— failed: retry once → halt (§4.4) ——
  if (impl.status === 'failed') {
    impl = await agent(buildPrompt('implementor', implCtx('', '上次 failed，重试一次。')),
                       { schema: SCHEMAS.implementor, model, label: `impl:${task.id}:retry` })
    if (impl.status !== 'ok') return { halted: true, reason: `implementor ${impl.status} after retry`, diag: impl.diagnostics }
  }
  let filesChanged = impl.evidence.files_changed || []

  // —— review rounds（max 3，§5）——
  for (let round = 1; round <= 3; round++) {
    state.perTask[task.id].review_rounds = round
    const fc = filesChanged.join(',')
    const [spec, qual, hunt] = await parallel([
      () => agent(buildPrompt('specReview', { taskId: task.id, specPath: cfg.spec_path, planFilePath: plan.file, filesChanged: fc }), { schema: SCHEMAS.specReview, model: 'opus', phase: `Plan ${plan.id}`, label: `spec:${task.id}:r${round}` }),
      () => agent(buildPrompt('qualityReviewer', { taskId: task.id, filesChanged: fc }), { schema: SCHEMAS.qualityReviewer, model: 'opus', label: `qual:${task.id}:r${round}` }),
      () => agent(buildPrompt('hunter', { taskId: task.id, filesChanged: fc }), { schema: SCHEMAS.hunter, label: `hunt:${task.id}:r${round}` }),
    ])
    state.perTask[task.id].files_touched_per_round.push(unionFiles(spec, qual, hunt))
    const osc = detectOscillation(state.perTask[task.id].files_touched_per_round)
    if (osc.oscillating) return { halted: true, reason: 'OSCILLATING', diag: osc }
    if (allGreen(spec, qual, hunt)) break
    if (round === 3) return { halted: true, reason: 'review max rounds', diag: { spec: spec.diagnostics, qual: qual.diagnostics, hunt: hunt.diagnostics } }
    impl = await agent(buildPrompt('implementor', implCtx(issuesFromReviews(spec, qual, hunt).join('; '), `修复 review round ${round} 问题。`)), { schema: SCHEMAS.implementor, model, label: `impl:${task.id}:fix${round}` })
    filesChanged = impl.evidence.files_changed || filesChanged
  }

  // —— simplify（max 1，§5.2：无条件重跑 review；失败则回退）——
  let simplifyFailed = false, simplifyFiles = []
  const simp = await agent(buildPrompt('simplify', { taskId: task.id, filesChanged: filesChanged.join(','), simplifyFailed: 'false' }), { schema: SCHEMAS.simplify, label: `simp:${task.id}` })
  if (simp.evidence.changed) {
    const fc = (simp.evidence.files_changed || []).join(',')
    const [spec2, qual2, hunt2] = await parallel([
      () => agent(buildPrompt('specReview', { taskId: task.id, specPath: cfg.spec_path, planFilePath: plan.file, filesChanged: fc }), { schema: SCHEMAS.specReview, model: 'opus', label: `spec:${task.id}:simp` }),
      () => agent(buildPrompt('qualityReviewer', { taskId: task.id, filesChanged: fc }), { schema: SCHEMAS.qualityReviewer, model: 'opus', label: `qual:${task.id}:simp` }),
      () => agent(buildPrompt('hunter', { taskId: task.id, filesChanged: fc }), { schema: SCHEMAS.hunter, label: `hunt:${task.id}:simp` }),
    ])
    if (!allGreen(spec2, qual2, hunt2)) { simplifyFailed = true; simplifyFiles = simp.evidence.files_changed || [] }
  }

  // —— commit（§5：状态原子转换；simplify 回退委托此 agent）——
  const commit = await agent(buildPrompt('commit', { taskId: task.id, planId: plan.id, planIdShort, taskTitle: task.title || task.id, testCommand: cfg.test_command, simplifyFailed: String(simplifyFailed), simplifyFiles: simplifyFiles.join(',') }), { schema: SCHEMAS.commit, label: `commit:${task.id}` })
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
const boot = await agent(buildPrompt('bootstrap', { configPath: args.configPath, plansDir: args.plansDir, runTs: state.runTs }), { schema: SCHEMAS.bootstrap, label: 'bootstrap' })
state.config = boot.evidence.config
state.completed = boot.evidence.completed

for (const plan of boot.evidence.plans) {
  if (args.plan && plan.id !== args.plan) continue
  state.currentPlan = plan.id
  phase(`Plan ${plan.id}`)
  const want = (args.tasks && args.tasks.length) ? new Set(args.tasks) : null
  const tasks = plan.tasks.filter(t => !want || want.has(t.id))
  for (const task of tasks) {
    if (state.completed.includes(task.id)) { log(`skip ${task.id} (already committed)`); continue }
    const r = await runTask(plan, task)
    if (r.halted) { await halt(plan, { id: task.id }, r); return { result: 'halted', reason: r.reason } }
  }
  // plan 级独立 gate（§3）：本 plan 最后 commit SHA 上重跑 full_test_command
  const lastSha = Object.values(state.perTask).filter(p => p.planId === plan.id && p.commit_sha).at(-1)?.commit_sha
  if (lastSha) {
    const gate = await agent(buildPrompt('gate', { sha: lastSha, fullTestCommand: state.config.full_test_command }), { schema: SCHEMAS.gate, label: `gate:${plan.id}`, phase: `Plan ${plan.id}` })
    if (gate.evidence.tests_exit_code !== 0) { await halt(plan, null, { reason: 'plan gate failed', diag: { sha: lastSha, summary: gate.evidence.pytest_summary } }); return { result: 'halted', reason: 'plan gate failed' } }
    log(`✓ plan ${plan.id} gate green @ ${lastSha}`)
  } else {
    log(`plan ${plan.id}: no new commits, gate skipped`)
  }
}

phase('Finalize')
await agent(buildPrompt('finalReport', { mode: 'done', stateJson: JSON.stringify(state), runsDir: 'runs', runTs: state.runTs }), { schema: SCHEMAS.finalReport, label: 'final-report' })
log('✓ workflow done')
return { result: 'done', perTask: state.perTask }
