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

// 已被 collectReviewFindings 取代（orchestrator fix-round 用）；保留为通用工具 + 向后兼容。
export function issuesFromReviews(...reviews) {
  const out = []
  for (const r of reviews) if (r && r.status === 'failed') out.push(...(r.diagnostics?.issues || []))
  return out
}

// 收集三类 review 的发现并归一化为结构化数组（orchestrator fix-round 反馈管道）。
// spec/quality 存 diagnostics.issues；hunter 存 diagnostics.silent_failures（不同 key！
// 旧 issuesFromReviews 只读 issues → hunter 发现被完全丢弃，Bug 2）。
// items 可能是 string 或 object → 统一归一化为 {source, severity?, title, file?, fix?}。
export function collectReviewFindings(spec, qual, hunt) {
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

// 把 collectReviewFindings 的结构化数组序列化为 implementor 可读的多行字符串。
// 自描述格式：[source|severity] title — fix: ... (file)。空数组 → 空串（implCtx 约定）。
// 替代旧的 lossy .join('; ')（对象 toString → [object Object]，Bug 1）。
export function formatFindings(findings) {
  if (!Array.isArray(findings) || findings.length === 0) return ''
  return findings.map(f => {
    const tag = f.severity ? `[${f.source}|${f.severity}]` : `[${f.source}]`
    const fix = f.fix ? ` — fix: ${f.fix}` : ''
    const file = f.file ? ` (${f.file})` : ''
    return `${tag} ${f.title}${fix}${file}`
  }).join('\n')
}

// 判断错误是否 model 限额耗尽（§2.4 双重检测的捕获路径）
export function isQuotaError(e) {
  const s = String(e?.message || e || '').toLowerCase()
  return /quota|rate.?limit|429|overloaded|insufficient.*balance|credit|capacity/i.test(s)
}

// 安全提取错误字符串
export function errStr(e) {
  return String(e?.message || e || '').slice(0, 200)
}

// 把 agent() 抛出的异常归类为 review 语义 status：限额→model_unavailable，其余→agent_error。
// 封装 review catch 里重复的三元判断（safeAgent 用）。
export function classifyThrown(e) {
  return isQuotaError(e) ? 'model_unavailable' : 'agent_error'
}

// review status 的合法集合（含 orchestrator-internal sentinel）。
// agent() 带 schema 时内部会重试 StructuredOutput；耗尽后偶发返回 null/空对象——
// 即 thinking-only 空响应（模型在 thinking 块里"以为"调了 StructuredOutput，实际只输出 thinking，
// 无 tool_use 块）。等 safeAgent 看到空返回时 runtime 重试多半已耗尽，故 orchestrator 直接 halt。
const REVIEW_VALID_STATUSES = new Set(['ok', 'failed', 'model_unavailable', 'agent_error'])

// 扫描三类 review 的 status，返回应 halt 的 reason。
// 优先级：agent_error > model_unavailable > review_empty；全合法且非 sentinel → null。
// review_empty：status 缺失/为空/非法（含 thinking-only 空响应 → null/undefined status）。
// 与 agent_error 区分：agent_error 是 agent() 抛非 quota 异常（safeAgent catch 构造）；
// review_empty 是 agent() 静默空返回（无异常、但无有效 review）——瞬态模型 hiccup，
// blocked.md 据此提示"全新跑续即可"，可操作性高于笼统的 agent_error。
export function reviewHaltReason(s, q, h) {
  const statuses = [s?.status, q?.status, h?.status]
  if (statuses.includes('agent_error')) return 'agent_error'
  if (statuses.includes('model_unavailable')) return 'model_unavailable'
  if (statuses.some(st => !st || !REVIEW_VALID_STATUSES.has(st))) return 'review_empty'
  return null
}

// 基于 halt reason 给工作树脏状态的"来源语义"提示（确定性映射，非 dirty 推断）。
// 与 finalReport 的 git status ground truth 并存：用户既有真实状态，也有快速定位线索。
// halt() 填 blocked_info.likely_source，finalReport 写进 blocked.md。
export function haltLikelySource(reason) {
  const r = String(reason || '')
  if (r === 'plan gate failed' || /gate/.test(r)) return 'gate restored'        // gate 已 checkout 回原 HEAD
  if (/^bootstrap /.test(r)) return 'bootstrap frontmatter'                      // bootstrap 可能写了 plan frontmatter
  if (/max rounds|OSCILLATING|fix-round|commit failed|simplify reported|BLOCKED|after (context-fetch|retry)|agent_error|model_unavailable|review_empty/.test(r)) return 'implementor changes'
  return 'unknown'
}

// 把 bootstrap 从 git log 解析的 completed id 归一化为 plan-scoped key "plan-{seq}/T-{id}"。
// 提交约定单一事实源（emission ↔ recognition 对称）。
// 任何 task 的 git 提交消息必须是 feat(plan-XX/TY): <title>——这是 bootstrap 扫 git log
// 识别"已完成 task"的唯一约定。他类 scope（feat(scheduler)/feat(notifications)/无 scope）
// bootstrap 不认 → task 被判未完成 → 重跑 → OSCILLATING halt。
// （根因：plan 模板 Step 5/8 嵌入的 feat(scheduler) 等示意曾被 agent 照抄——见 commitConvention.test）
// 与 normalizeCompleted 共享 plan-scoped key 格式，故 emission 一定可被 recognition 解出。
export function commitSubject(seq, taskId, title) {
  const planIdShort = `plan-${String(seq).padStart(2, '0')}`
  return `feat(${planIdShort}/${taskId}): ${title}`
}

// 从 git 提交消息主体反向解出 plan-scoped task key（bootstrap recognition 侧）。
// 只认 feat(plan-XX/TY): 前缀；其余一律 null（判不可见）。与 normalizeCompleted 归一化结果一致。
export function extractTaskKey(subject) {
  const m = String(subject).match(/^feat\(plan-(\d+)\/(T[\w-]+)\)\s*:/i)
  return m ? `plan-${m[1]}/${m[2]}` : null
}

// 避免跨 plan 同名 task 误跳过：Plan 01/02 都有 T1-T10，若去 plan 前缀，Plan 02 的 T2 会被
// Plan 01 的 T2 误 skip。bootstrap 返回格式不稳定（"01/T2" / "plan-01/T2" / 裸 "T2"）：
// - 带前缀 → 归一化为 "plan-{seq}/T-{id}"
// - 裸 id（无 plan 信息）→ 原样保留；它不匹配任何 plan-scoped 比对 key，故不会误跳过（最坏重跑，安全）
export function normalizeCompleted(ids) {
  return ids.map(id => {
    const m = String(id).match(/^(?:plan-)?(\d+)\/+(T[\w-]+)$/i)
    return m ? `plan-${m[1]}/${m[2]}` : String(id)
  })
}

// args.plan 与 plan.id/plan.seq 的宽松匹配（Bug 10）。
// 容忍 string/number/padded-seq/"plan-" 前缀差异。
// `3`/`"3"`/`"03"`/`"plan-03"` 均匹配 seq="03", id="plan-03"。
export function matchesPlanFilter(plan, planArg) {
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

// ===== 条件渲染 helpers（通用性：彩票特有内容靠 config 驱动，prompt 保持单一模板）=====
// orchestrator 显式传空串（非 undefined），buildPrompt 才会把占位符替换为空而非残留 {{k}}。

// reference_paths → prompt 段落；无则空串（该段消失，通用项目不受约束）。
export function formatReferencePaths(paths) {
  if (!Array.isArray(paths) || paths.length === 0) return ''
  const lines = paths.map(p => `- ${p}`).join('\n')
  return `## Reference Documents (authoritative — match these exactly)
${lines}
Read the relevant section(s) BEFORE implementing/reviewing number/play/prize/rule logic. Deviations from these authoritative rules are bugs (e.g. positional vs partition number comparison).`
}

// 项目特定静默失败纪律（可选 config 注入）——通用 hunter 清单之上，注入本项目反复踩的领域致命点。
// 不填 → 空串 → hunter 退化为通用清单（通用性不破坏）。填了 → hunter 重点核查这些项目特定条款。
export function formatSilentFailureContext(items) {
  if (!Array.isArray(items) || items.length === 0) return ''
  const lines = items.map(it => `- ${it}`).join('\n')
  return `## Project-Specific Silent-Failure Risks (HIGHEST PRIORITY — hunt these first)
This system's core value is "中奖永不静默漏通知". Beyond the generic patterns below, the following project-specific silent-failure traps have caused real misses and MUST be checked explicitly:
${lines}
For each, verify the changed code does not fall into the trap. Report a silent_failure with the specific trap name + file:line + why it violates.`
}

// 按 language 返回 quality review 专项清单；未知 language → 通用清单（不硬编码任何项目架构）。
export const LANGUAGE_CHECKLISTS = {
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
export function languageChecklist(language) {
  return LANGUAGE_CHECKLISTS[language] || LANGUAGE_CHECKLISTS.general
}

// 组装 gate 验证命令序列：full_test_command + lint_command + extra_lint_commands（去重去空）。
// 架构纪律（如 domain-zero-IO）靠 extra_lint_commands 承载，gate 自动强制，不靠 prompt 人眼。
export function gateCommands(config) {
  const cmds = []
  if (config?.full_test_command) cmds.push({ kind: 'test', command: config.full_test_command })
  if (config?.lint_command) cmds.push({ kind: 'lint', command: config.lint_command })
  for (const c of (config?.extra_lint_commands || [])) if (c) cmds.push({ kind: 'lint', command: c })
  return cmds
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
      status: { type: 'string', enum: ['ok', 'done_with_concerns', 'failed', 'blocked', 'needs_context', 'model_unavailable'] },
      evidence: { type: 'object', required: ['tests_exit_code', 'files_changed', 'pytest_summary'],
        properties: { tests_exit_code: { type: 'integer' }, files_changed: { type: 'array' }, pytest_summary: { type: 'string' } } },
      diagnostics: { type: 'object', properties: { blocked_category: { type: 'string' }, last_error: { type: 'string' }, suggested_fix: { type: 'string' }, concerns: { type: 'array' } } },
      summary: { type: 'string' },
    },
  },
  specReview: reviewSchema(),
  qualityReviewer: qualityReviewSchema(),
  hunter: { type: 'object', required: ['status'], additionalProperties: true,
    properties: { status: { type: 'string', enum: ['ok', 'failed', 'model_unavailable'] },
      diagnostics: { type: 'object', properties: { files_touched: { type: 'array' },
        silent_failures: { type: 'array', items: {
          type: 'object', required: ['title', 'fix'],
          properties: { title: { type: 'string' }, severity: { type: 'string', enum: ['critical', 'important', 'minor'] }, file: { type: 'string' }, line: { type: 'integer' }, fix: { type: 'string' } },
        } } } },
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

// 注意：'agent_error' 是 orchestrator-internal sentinel，由 safeAgent 的 catch 块构造、
// 绕过 schema 校验（agent() 抛错时不走 schema），故不入下方 status enum。
// orchestrator 用 reviewHaltReason() 显式判断 agent_error/model_unavailable。入 enum 反而放宽约束。
function reviewSchema() {
  return { type: 'object', required: ['status'], additionalProperties: true,
    properties: {
      status: { type: 'string', enum: ['ok', 'failed', 'model_unavailable'] },
      diagnostics: { type: 'object', properties: { files_touched: { type: 'array' }, issues: { type: 'array' } } },
      summary: { type: 'string' },
    } }
}

// qualityReviewer 单独 schema：issues 元素强制对象 {title, fix, severity, file}（specReview 用字符串模板故走 reviewSchema）。
// items 约束防 LLM 返回纯字符串/缺 fix/用错字段名 → collectReviewFindings 的 it.title||String(it) 兜底为 [object Object]。
function qualityReviewSchema() {
  return { type: 'object', required: ['status'], additionalProperties: true,
    properties: {
      status: { type: 'string', enum: ['ok', 'failed', 'model_unavailable'] },
      diagnostics: { type: 'object', properties: { files_touched: { type: 'array' },
        issues: { type: 'array', items: {
          type: 'object', required: ['title', 'fix'],
          properties: { severity: { type: 'string', enum: ['Critical', 'Important', 'Minor'] }, title: { type: 'string' }, file: { type: 'string' }, fix: { type: 'string' } },
        } } } },
      summary: { type: 'string' },
    } }
}

// 10 类 agent prompt 模板（§13b）。{{key}} 由 buildPrompt(role, ctx) 填充。
export const PROMPTS = {
  bootstrap: `You are the BOOTSTRAP agent for the workflow orchestrator. Read project state and return structured data. You MAY write YAML frontmatter to plan files that lack it (idempotent). Modify no other files.

Inputs: configPath={{configPath}} plansDir={{plansDir}} runTs={{runTs}}

Steps:
1. Read {{configPath}} → {test_command, full_test_command, build_command, lint_command, extra_lint_commands, spec_path, reference_paths, language, silent_failure_context}. extra_lint_commands / reference_paths / silent_failure_context are OPTIONAL (may be absent → treat as [] / [] / []).
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

This is a STATIC READ-ONLY review. You may use 'git diff', 'git status', 'find', 'grep'/'rg', and read files to locate and inspect changes. Do NOT run the test suite, ruff, lint, or any build — spec verification is done by reading code, not by running it. Running tests/builds is the implementor's and gate's job, not yours.

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

This is a STATIC READ-ONLY review. You may use 'git diff', 'git status', 'find', 'grep'/'rg', and read files to locate and inspect changes. Do NOT run the test suite, ruff, lint, or any build — quality review is done by reading code, not by running it. Running tests/builds is the implementor's and gate's job, not yours.

## Calibration
Categorize issues by ACTUAL severity — not everything is Critical. Acknowledge what was done well (strengths) before listing issues; accurate praise helps the implementer trust the rest.

Return {status (ok|failed), diagnostics:{files_touched:[...], issues:[{severity: Critical|Important|Minor, title, file, fix}]}, summary}.
issues 元素 MUST 是 object 且必有 title + fix（severity/file 亦建议）——纯字符串或缺 title/fix 的对象会被 schema 拒绝。
RED FLAG: ok 仅当无 Critical/Important 问题。Critical/Important（架构/安全/正确性）必须 failed；仅 Minor 可 ok（记入 issues）。若遇到 model 限额耗尽（quota/rate-limit/429 错误），返回 status:'model_unavailable'（非 failed），让 orchestrator halt 并保存进度。`,

  hunter: `You are the SILENT-FAILURE-HUNTER. Hunt swallowed errors, bad fallbacks, missing error propagation, swallowed exceptions, except:pass, broad except hiding bugs, default values masking failures. Verdict on CURRENT tree.

Inputs: taskId={{taskId}} changedHint={{filesChanged}}
{{silentFailureContext}}

Steps:
1. Read changed files.
2. If project-specific silent-failure risks are listed above, hunt those FIRST (they are this system's known fatal traps) — then hunt the generic patterns below.
3. Find:
   - try/except that pass or log-only; bare except hiding bugs; errors converted to null/empty with no context
   - fallback returning wrong-type default; default values masking real failure; .catch(() => [])
   - unhandled None; ignored return values; missing await; fire-and-forget without error path
   - network/file/db paths with NO timeout or error handling
   - transactional work with no rollback on failure
   - lost stack traces (rethrow without context); generic rethrows
   - logs with wrong severity / log-and-forget (no handling after logging)
4. Record files_touched.

This is a STATIC READ-ONLY review. You may use 'git status', 'git diff', 'find', 'grep'/'rg', and read files to locate patterns and inspect code. Do NOT run the test suite, ruff, lint, or any build — silent-failure hunting is done by reading code, not by running it. Running tests/builds is the implementor's and gate's job, not yours.

Return {status (ok|failed), diagnostics:{files_touched:[...], silent_failures:[{title, severity (critical|important|minor), file, line?, fix}]}, summary}.
silent_failures 元素 MUST 是 object（必有 title + fix；file 强烈建议；severity 可选默认 important）——纯字符串或不带 fix 的对象会被 schema 拒绝。
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

Inputs: taskId={{taskId}} planId={{planId}} testCommand={{testCommand}} simplifyFailed={{simplifyFailed}} simplifyFiles={{simplifyFiles}} commitMsg={{commitMsg}}

## 提交约定（HARD REQUIREMENT — 违反会导致 OSCILLATING halt）
git 提交消息**必须**严格等于下面这条（orchestrator 已按 feat(plan-XX/TY): title 格式预计算好，原样使用，不要改写 scope、不要自拟标题）：
  {{commitMsg}}
理由：bootstrap 扫 git log 用约定 feat(plan-XX/TY): 识别"已完成 task"。任何他类 scope 都会让该 task 对 bootstrap 不可见 → 被判未完成 → 重跑 → OSCILLATING halt。
**严禁照抄 plan 文件里 Step 5/8 的示意提交消息**（如 feat(scheduler): ... / feat(notifications): ... / 无 scope 的 feat: ...）——那些只是写法的示意，不是真实提交命令。本 task 唯一合法的提交消息就是上面的 {{commitMsg}}。

Steps:
1. If simplifyFailed=true: first git checkout -- each file in simplifyFiles (revert bad simplify), then proceed.
2. git status --porcelain → see staged/unstaged.
3. Run {{testCommand}} on current tree; confirm exit 0. If fail → status=failed (do NOT commit).
4. git add -A; git commit -m "{{commitMsg}}"。
5. **强制校验 + 纠偏**：git log -1 --format=%s 取 HEAD 主体，与 {{commitMsg}} 比对。若不符（任何原因——比如实现 agent 之前已用错误 scope 提交过、或 HEAD 已存在但消息不对）：git commit --amend -m "{{commitMsg}}" 纠正。这是确定性的：无论谁提交、提交了什么，最终 HEAD 消息必为 {{commitMsg}}。
6. git rev-parse HEAD → commit_sha。

Return {status (ok|failed), evidence:{commit_sha, committed_files:[...], tests_at_commit}, summary}.
RED FLAG: tests exit != 0 时绝不 commit（status=failed）。commit_sha 必须真实。HEAD 消息必须等于 {{commitMsg}}（步骤 5 校验，不符必 amend）。若遇到 model 限额耗尽（quota/rate-limit/429 错误），返回 status:'model_unavailable'（非 failed），让 orchestrator halt 并保存进度。`,

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

Inputs: mode={{mode}} state={{stateJson}} blockedInfo={{blockedInfo}} runsDir={{runsDir}} runTs={{runTs}}

Steps:
1. mkdir -p {{runsDir}}.
2. Write {{runsDir}}/manifest.json = {run_ts:{{runTs}}, mode:{{mode}}, plans:[...], per_task:{<taskId>:{status,model,review_rounds,files_touched_per_round,commit_sha,blocked_info}}, result}.
3. If mode=halted: write {{runsDir}}/blocked.md from {{blockedInfo}} (the blocked task's blocked_info JSON — render EACH field human-readably: plan, task, reason, category, last_error, suggested_fix, quota_exhausted, likely_source). Do NOT hunt for these fields in state — they are provided inline in blockedInfo.
4. If mode=halted: run "git status --porcelain" and "git diff --stat". BEST-EFFORT — if git fails (not a repo / index corrupt), skip this section (do NOT block manifest.json write).
   If "git status --porcelain" output is non-empty, append a "## Working Tree (dirty)" section to blocked.md with: the porcelain output (file list) + the diff --stat output (change summary) + 接手指引（implementor 改动未提交，留在工作树。选项：git diff <file> 查看 / git checkout -- <file> 丢弃 / 手动修后 git commit -m "feat(plan-X/T-Y): ..." 再全新跑续，见 USAGE.md §7.1）。
   If output is empty, append "## Working Tree (clean)" — no uncommitted changes（likely_source=gate restored 时预期如此）。
5. Print a digest summary (counts: done/blocked, total tasks, per-plan gate result).

Return {evidence:{manifest_path}, summary: <digest>}.
RED FLAG: manifest 必须真实写入磁盘（你 ls 确认）。stateJson 是 orchestrator 传入的完整状态，照实记录。`,
}
