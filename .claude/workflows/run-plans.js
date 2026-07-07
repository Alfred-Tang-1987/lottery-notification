// workflow orchestrator —— 多 plan 自动执行（workflow-design.md §4/§5/§13）
// 纯函数/SCHEMAS/PROMPTS inline 自 docs/superpowers/workflows/lib.js —— 改 lib 必须同步改这里。
// 顶层 await = Workflow 入口；agent/parallel/phase/log/args 为 Workflow runtime 注入的全局。
// 分层：纯决策（classifyThrown/reviewHaltReason/collectReviewFindings 等）进 lib.js 可 node:test 测；
//   runtime 胶水（safeAgent/dispatchImpl，调 agent()）只能留此文件（lib.js 是纯模块不能调 runtime 全局）。

export const meta = {
  name: 'run-plans',
  description: '自动执行 implementation plans：每 task implementor→review chain→commit，plan 级独立 gate',
  phases: [
    { title: 'Bootstrap', detail: '读 config/plan/git log + 生成 frontmatter' },
    { title: 'Plan', detail: '串行 task + review rounds + simplify + commit + plan gate' },
    { title: 'Finalize', detail: '写 manifest + digest' },
  ],
}

await (async () => {


// ===== 纯函数（inline 自 lib.js Task 2-4，逐字复制）=====
function detectOscillation(filesTouchedPerRound) {
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
// v3 (2026-07-06, §5.5): shouldEscalateOnOscillation 仅判断「是否升级 opus」。
// halt 决策上移到 run-plans.js OSC 分支（flipFlop OR hasRegressed → halt；else 继续 + budget guard）。
// 旧逻辑「已升级→return false→halt」已移除（那是纯计数 halt 的根因，浪费 opus 推进力）。
function shouldEscalateOnOscillation(currentModel, alreadyEscalated) {
  if (alreadyEscalated) return false  // 已升级过不再重复升级（不影响 halt 决策）
  return currentModel !== 'opus'      // 非 opus → 升级
}
// 改进 2 (2026-07-05): 区分 flip-flop（同 title 跨轮反复）vs 补充（新 title）—— inline 自 lib.js。
function isFlipFlop(reviewHistory) {
  if (!Array.isArray(reviewHistory) || reviewHistory.length < 2) return false
  const last = reviewHistory[reviewHistory.length - 1]
  const prevTitles = new Set()
  for (let i = 0; i < reviewHistory.length - 1; i++) {
    const round = reviewHistory[i]
    for (const r of [round?.spec, round?.quality, round?.hunter]) {
      for (const f of (r?.findings || [])) if (f?.title) prevTitles.add(f.title)
    }
  }
  for (const r of [last?.spec, last?.quality, last?.hunter]) {
    for (const f of (r?.findings || [])) {
      if (f?.title && prevTitles.has(f.title)) return true
    }
  }
  return false
}
// —— v3 findings 状态机（inline 自 lib.js，sync.test 字节守护）——
function updateFindingsHistory(history, currentFindings, round) {
  if (!Array.isArray(history)) history = []
  const current = Array.isArray(currentFindings) ? currentFindings : []
  const currentTitles = new Set(current.map(f => f?.title).filter(Boolean))
  const result = history.map(h => {
    const stillPresent = currentTitles.has(h.title)
    if (stillPresent) {
      // 仍存在：open→open / fixed→regressed / regressed→regressed
      const status = h.status === 'open' ? 'open' : 'regressed'
      return {
        ...h,
        last_seen: round,
        rounds: [...h.rounds, round],
        status,
        // fixed→regressed 时保留 fixed_at_round（diag 用）；open/regressed 不变
        fixed_at_round: h.fixed_at_round,
      }
    }
    // 不存在：open→fixed；regressed 二次修好后也回到 fixed（保留 regression 历史供 diag），fixed 保持
    if (h.status === 'open' || h.status === 'regressed') {
      return { ...h, status: 'fixed', fixed_at_round: round }
    }
    return h
  })
  // 新 finding（title 在 history 无）：首次出现 → open
  const existingTitles = new Set(history.map(h => h.title))
  for (const f of current) {
    if (f?.title && !existingTitles.has(f.title)) {
      result.push({
        title: f.title,
        severity: f.severity,
        fix: f.fix,
        file: f.file,
        first_seen: round,
        last_seen: round,
        rounds: [round],
        status: 'open',
      })
    }
  }
  return result
}
function hasRegressed(history) {
  if (!Array.isArray(history)) return false
  return history.some(h => h?.status === 'regressed')
}
function formatFindingsHistory(history, currentRound) {
  if (!Array.isArray(history) || history.length === 0) return ''
  const open = history.filter(h => h.status === 'open')
  const fixed = history.filter(h => h.status === 'fixed')
  const sections = []
  if (open.length > 0) {
    // DX medium: 按 severity 排序（critical > important > minor），防弱模型先修容易的 minor 漏 critical
    const sevRank = { critical: 0, important: 1, minor: 2 }
    const sortedOpen = [...open].sort((a, b) => (sevRank[a.severity] ?? 9) - (sevRank[b.severity] ?? 9))
    const lines = sortedOpen.map(h => {
      const sev = h.severity ? `[${h.severity}]` : ''
      // D1: 本轮新增加 ★ 标记（last_seen === currentRound），否则显式 seen 信息
      const isNew = currentRound !== undefined && h.last_seen === currentRound
      const seen = isNew ? '★本轮新增' : `(seen: r${h.first_seen}-${h.last_seen}, ${h.rounds.length}轮)`
      const file = h.file ? `, file: ${h.file}` : ''
      const fix = h.fix ? ` — fix: ${h.fix}` : ''
      return `- ${sev} ${h.title} ${seen}${file}${fix}`
    }).join('\n')
    sections.push(`### [OPEN] 本轮仍存在 — 必须修完（★ = 本轮新增，优先修）\n${lines}`)
  }
  if (fixed.length > 0) {
    const lines = fixed.map(h => {
      const sev = h.severity ? `[${h.severity}]` : ''
      const file = h.file ? `, file: ${h.file}` : ''
      const fix = h.fix ? ` — fix: ${h.fix}` : ''
      return `- ${sev} ${h.title} (fixed r${h.fixed_at_round}${file})${fix}`
    }).join('\n')
    sections.push(`### [FIXED] 已修好的 — 修新问题时核对这里列出的 fix 仍存在（若 [OPEN] 与 [FIXED] 同文件，只动 [OPEN] 描述的代码，不要回退 [FIXED] 对应的修改）\n${lines}`)
  }
  // [REGRESSED] 不注入（触发即 halt，implementor 看不到）
  if (sections.length === 0) return ''
  return `## Findings History (全轮累积)\n${sections.join('\n\n')}`
}
// —— v3 OSCILLATING budget guard（inline 自 lib.js）——
function resolveReviewBudget(config) {
  const v = config?.review_budget
  if (typeof v !== 'number' || !Number.isFinite(v) || v <= 0) return 5
  return v
}
function buildPrompt(role, ctx = {}) {
  const tpl = PROMPTS[role]
  if (!tpl) throw new Error(`unknown role: ${role}`)
  // P1-7（第 6 轮）: undefined/null 值渲染为空串（防 "undefined" 污染 prompt）。
  //   key 缺失（k in ctx=false）保留 {{k}} 占位符（debug 用）；key 存在但值为 undefined → 空串。
  return tpl.replace(/\{\{(\w+)\}\}/g, (_, k) => {
    if (!(k in ctx)) return `{{${k}}}`
    if (ctx[k] === undefined || ctx[k] === null) return ''
    return String(ctx[k])
  })
}
function allGreen(...reviews) { return reviews.every(r => r && r.status === 'ok') }
function unionFiles(...reviews) {
  const set = new Set(); for (const r of reviews) for (const f of (r?.diagnostics?.files_touched || [])) set.add(normalizeFilePath(f)); return [...set]
}
// 文件路径归一化（W1-5b, 2026-07-07）：统一 Windows 绝对路径 / 反斜杠 / 大小写为相对路径。
// 防止 reviewer 返回 C:\...\src\... / C:/.../src\... / src/... 三种格式致 groupFindingsByFile
// 按字符串比对失效（cross-reviewer 重叠检测漏报）。
// 白名单覆盖常见顶层目录（src/tests/docs/data/logs/lib/app/internal/cmd/.claude），
// 匹配首个白名单目录后保留相对路径；无匹配则原样返回（防误裁剪）。
function normalizeFilePath(p) {
  if (!p) return p
  return String(p).replace(/\\/g, '/').replace(/^.*?\/(src|tests|docs|data|logs|lib|app|internal|cmd|\.claude)\//i, '$1/')
}
// 收集单个 failed review 的 findings（内部 helper，collectReviewFindings 与
// reviewHaltForEmptyFailed 共用，避免两处重复 push 逻辑漂移）—— inline 自 lib.js
// 非 failed 或无 diagnostics → 返回 []。items 归一化同 collectReviewFindings。
// W1-5b: file 字段经 normalizeFilePath 归一化，防跨 reviewer 路径格式差异致重叠检测漏报。
function findingsOf(r, source, key) {
  if (!r || r.status !== 'failed') return []
  const out = []
  for (const it of (r.diagnostics?.[key] || [])) {
    if (it && typeof it === 'object') out.push({ source, severity: it.severity, title: it.title || String(it), file: normalizeFilePath(it.file), fix: it.fix })
    else out.push({ source, title: String(it) })
  }
  return out
}

// 收集三类 review 的发现并归一化为结构化数组（orchestrator fix-round 反馈管道）—— inline 自 lib.js
// spec/quality 存 diagnostics.issues；hunter 存 diagnostics.silent_failures（不同 key！
// 旧实现只读 issues → hunter 发现被完全丢弃，Bug 2）。
// items 可能是 string 或 object → 统一归一化为 {source, severity?, title, file?, fix?}。
function collectReviewFindings(spec, qual, hunt) {
  return [...findingsOf(spec, 'spec', 'issues'), ...findingsOf(qual, 'quality', 'issues'), ...findingsOf(hunt, 'hunter', 'silent_failures')]
}

// 格式化 implementor concerns 为 review prompt 的 focusHint 段落（Q11 抽出，消除两处重复模板）。
// 空数组 → 空串（该段消失，review 不收 focusHint）；非空 → 多行 bullet 列表。
// 初始 dispatch 路径与 fix-round done_with_concerns 路径共用此 helper，防模板字符串漂移。
// inline 自 lib.js（sync.test QC-4 字节比较守护）。
function formatConcernsHint(concerns) {
  if (!Array.isArray(concerns) || concerns.length === 0) return ''
  return `\n## Implementor Concerns (verify these)\n${concerns.map(c => '- ' + c).join('\n')}`
}

// 第二道静默失败守卫（reviewHaltReason 之后）：任一 review status==='failed' 但产出 0 项 findings
// → 返回 'review_failed_no_findings'—— inline 自 lib.js
// 防「合法 failed + 空 diagnostics」漏过 reviewHaltReason（status 合法 → 不 halt）→
// collectReviewFindings 空 → implementor 收「0 项发现」跑空修复 → max rounds 误 halt。
// 与 review_empty 区分：review_empty 是 status 缺失（agent 静默空返回）；
// review_failed_no_findings 是 agent 明确判 failed 却没给可执行发现（issues/silent_failures 空）。
// 优先级在 reviewHaltReason 之后：先排除空 status，再查 failed-no-findings。
function reviewHaltForEmptyFailed(spec, qual, hunt) {
  const checks = [
    [spec, 'spec', 'issues'],
    [qual, 'quality', 'issues'],
    [hunt, 'hunter', 'silent_failures'],
  ]
  for (const [r, source, key] of checks) {
    if (r && r.status === 'failed' && findingsOf(r, source, key).length === 0) return 'review_failed_no_findings'
  }
  return null
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

// review_history 存档：单轮 review findings → manifest 摘要（只留 title+severity）—— inline 自 lib.js
// 复用 findingsOf；丢 fix/file/source。OSCILLATING halt 后凭此定位振荡点，无需考古。
function summarizeFinding(r, source, key) {
  return { status: r?.status, findings: findingsOf(r, source, key).map(f => ({ title: f.title, severity: f.severity })) }
}

// 单轮三类 review 摘要（进 manifest.per_task.<task>.review_history）—— inline 自 lib.js
function summarizeReviewRound(round, spec, qual, hunt) {
  return {
    round,
    spec: summarizeFinding(spec, 'spec', 'issues'),
    quality: summarizeFinding(qual, 'quality', 'issues'),
    hunter: summarizeFinding(hunt, 'hunter', 'silent_failures'),
  }
}

// 判断错误是否 model 限额耗尽（§2.4 双重检测的捕获路径）—— inline 自 lib.js
function isQuotaError(e) {
  const s = String(e?.message || e || '').toLowerCase()
  // 含中文 router 限额错误（本机 router 返回 "已达到 5 小时的使用上限" / "额度" / "限额"）。
  // 不认则 dispatchImpl catch 不归类 model_unavailable → 走 throw → 顶层 uncaught crash。
  return /quota|rate.?limit|429|overloaded|insufficient.*balance|credit|capacity|使用上限|限额|额度|超出.*限制/i.test(s)
}
function errStr(e) {
  return String(e?.message || e || '').slice(0, 200)
}
// 把 agent() 抛出的异常归类为 review 语义 status —— inline 自 lib.js
function classifyThrown(e) {
  return isQuotaError(e) ? 'model_unavailable' : 'agent_error'
}
// review status 的合法集合（含 orchestrator-internal sentinel）—— inline 自 lib.js
// agent() 带 schema 时内部会重试 StructuredOutput；耗尽后偶发返回 null/空对象——
// 即 thinking-only 空响应（模型在 thinking 块里"以为"调了 StructuredOutput，实际只输出 thinking，
// 无 tool_use 块）。等 safeAgent 看到空返回时 runtime 重试多半已耗尽，故 orchestrator 直接 halt。
const REVIEW_VALID_STATUSES = new Set(['ok', 'failed', 'model_unavailable', 'agent_error'])

// 扫描三类 review 的 status，返回应 halt 的 reason—— inline 自 lib.js
// 优先级：agent_error > model_unavailable > review_empty；全合法且非 sentinel → null。
// review_empty：status 缺失/为空/非法（含 thinking-only 空响应 → null/undefined status）。
// 与 agent_error 区分：agent_error 是 agent() 抛非 quota 异常（safeAgent catch 构造）；
// review_empty 是 agent() 静默空返回（无异常、但无有效 review）——瞬态模型 hiccup，
// blocked.md 据此提示"全新跑续即可"，可操作性高于笼统的 agent_error。
function reviewHaltReason(s, q, h) {
  const statuses = [s?.status, q?.status, h?.status]
  if (statuses.includes('agent_error')) return 'agent_error'
  if (statuses.includes('model_unavailable')) return 'model_unavailable'
  if (statuses.some(st => !st || !REVIEW_VALID_STATUSES.has(st))) return 'review_empty'
  return null
}
// 基于 halt reason 给工作树脏状态的"来源语义"提示（确定性映射，非 dirty 推断）—— inline 自 lib.js
// 与 finalReport 的 git status ground truth 并存：halt() 填 blocked_info.likely_source。
function haltLikelySource(reason) {
  const r = String(reason || '')
  if (r === 'plan gate failed' || r.includes('gate')) return 'gate restored'        // gate 已 checkout 回原 HEAD
  if (r.startsWith('bootstrap ')) return 'bootstrap frontmatter'                      // bootstrap 可能写了 plan frontmatter
  // P1-8（第 6 轮）: 显式 reason→source 映射（替代大正则，防误匹配 + 易维护）。
  //   静态 reason 用 Set；动态 reason（implementor ${status} after/in fix-round）用 startsWith。
  const implReasons = new Set([
    'model_unavailable', 'agent_error',
    'opus BLOCKED', 'opus BLOCKED after context-fetch',
    'OSCILLATING', 'review max rounds',
    'commit failed', 'commit out_of_scope',
    'simplify diff check failed', 'simplify amend failed', 'simplify checkout failed',
    'review_empty', 'review_failed_no_findings',
  ])
  if (implReasons.has(r)) return 'implementor changes'
  if (r.startsWith('implementor ')) return 'implementor changes'
  return 'unknown'
}

// 校验 amend subagent 返回值（Q1/Q8：边界条件纯函数化，可 node:test 行为测试）—— inline 自 lib.js
// amend 须返回 {ok:true, sha:"<40-hex>"}；ok:false / 空 sha / 非 40 位 / 非 hex → invalid。
// 调用方据此 halt（不静默继续用旧 SHA → gate 在旧 SHA 跑漏检 simplify 改动）。
function validateAmendResult(result) {
  const sha = String(result?.sha || '').trim()
  if (!result?.ok || !/^[0-9a-f]{40}$/.test(sha)) {
    return { valid: false, error: result?.error || result?.sha || 'invalid sha' }
  }
  return { valid: true, sha }
}

// 校验 checkout subagent 返回值（Q4/Q8：兜底验证工作树真 clean，防 ok:true 谎报）—— inline 自 lib.js
// checkout 须返回 {ok:true, porcelain:""}——porcelain 非空表示工作树仍有残留（权限/只读 fs 异常）。
// ok:false / porcelain 非空 / null → invalid，调用方 halt（不无条件设 simplify_reverted=true）。
function validateCheckoutResult(result) {
  if (!result?.ok) {
    return { valid: false, error: result?.error || 'checkout failed' }
  }
  const porcelain = String(result?.porcelain || '').trim()
  if (porcelain !== '') {
    return { valid: false, error: `working tree not clean after checkout: ${porcelain}` }
  }
  return { valid: true }
}

// fix-round implementor 的 model 选择（§5.1 难度递增：最后 1 轮 fix 用最强 model）—— inline 自 lib.js
// 有限模式（maxRounds > 0）：round === maxRounds - 1 是最后 1 轮 fix，强制 opus（默认 maxRounds=4 → round=3 升级）。
// 无限模式（maxRounds=0）：前 3 轮用 baseModel；round>=4 强制 opus（前 3 轮没修好说明问题复杂）。
// maxRounds 未传（向后兼容）→ 默认 3（round=2 升级 opus）。已是 opus 返回 'opus'（语义等价）。
function fixModelForRound(round, baseModel, maxRounds) {
  // P2-10（第 6 轮）: 删除 maxRounds 未传显式分支（resolveMaxRounds 总返回 number，死代码）。
  //   保留 ?? 3 容错（直接调用时默认 3，向后兼容 helpers.test.js）。
  const max = maxRounds ?? 3
  if (max === 0) return round >= 4 ? 'opus' : baseModel   // 无限模式：round>=4 升级 opus
  if (round === max - 1) return 'opus'                     // 有限模式：最后 1 轮 fix 强制 opus
  return baseModel
}

// 从 config 解析 review max rounds—— inline 自 lib.js
// 默认 4。0/负数 → 0（无限模式）。非数字/null/未配 → 4（容错默认）。
// 无限模式靠 detectOscillation（同文件 ≥3 round）独立防线 halt，防无限循环。
function resolveMaxRounds(config) {
  const v = config?.review_max_rounds
  if (v === undefined || v === null) return 4              // 未配 → 默认 4
  if (typeof v !== 'number' || !Number.isFinite(v)) return 4  // 非数字 → 默认 4（容错）
  if (v <= 0) return 0                                      // 0/负数 → 无限
  return Math.floor(v)
}

// 从 config 解析 lessons_auto_distill 开关。未配 → true（默认启用自动提炼）。
// 显式 false → 关闭。非布尔值 → true（容错：宁可多提炼，distiller 自身有 skip 决策兜底）。
function resolveLessonsAutoDistill(config) {
  const v = config?.lessons_auto_distill
  if (v === false) return false                            // 显式 false → 关闭
  return true                                              // 未配/true/非布尔 → 启用
}

// lesson 自动提炼：构造 distiller agent 输入上下文—— inline 自 lib.js（§5.4）
function distillLessonInput(mode, haltInfo, reviewHistory, failedApproaches) {
  return {
    mode,
    halt_info: haltInfo || null,
    review_history: Array.isArray(reviewHistory) ? reviewHistory : [],
    failed_approaches: Array.isArray(failedApproaches) ? failedApproaches : [],
  }
}

// ===== runtime helper（调 agent()，故留此文件不进 lib.js；决策逻辑走上面的纯函数）=====
// review agent 的统一派发：异常归类为 model_unavailable/agent_error sentinel（绕过 schema，orchestrator 显式判断）。
async function safeAgent(prompt, opts) {
  try { return await agent(prompt, opts) }
  catch (e) { return { status: classifyThrown(e), diagnostics: { error: errStr(e) } } }
}
// implementor/commit/bootstrap/gate 等带 status 的 agent 统一派发：
// catch quota→halt；agent 自报 model_unavailable→halt；否则返回 impl 供调用方判断 blocked/failed/needs_context。
// 返回 {halted:true, reason, diag} 或 impl（非 halted）。model 仅用于 halt 诊断。
// retryModel：当 agent 返回 null（能力不足 / 模型反复重试同一 tool call 被 runtime 400 中断）时，
// 用更强模型重试一次。不重试 quota 错误（isQuotaError 在第一层 catch 已 halt）。
// 典型场景：bootstrap 用 sonnet（qwen3.7-plus）跑复杂任务被 Repetitive tool calls 400 打断 → 升级 opus（qwen3.7-max）重试。
async function dispatchImpl(prompt, opts, model, retryModel = null) {
  let impl
  // QH1: 首次调用注入 model 到 opts（之前 opts 缺 model → agent 用环境默认 model，
  // 与 dispatchImpl 的 model 参数不一致，retryModel 逻辑也失效）。
  try { impl = await agent(prompt, { ...opts, model }) }
  catch (e) {
    // P0-4（第 6 轮）: 非 quota 异常须封装 agent_error 返回（不 throw）。
    //   旧代码 throw e → 被顶层 catch 捕获误判为 model_unavailable → 用户无效 resume。
    //   quota → model_unavailable；其余 → agent_error（TypeError/ReferenceError 等真实 bug）。
    if (isQuotaError(e)) return { halted: true, reason: 'model_unavailable', diag: { model, error: errStr(e) } }
    return { halted: true, reason: 'agent_error', diag: { model, error: errStr(e) } }
  }
  if (impl?.status === 'model_unavailable') return { halted: true, reason: 'model_unavailable', diag: impl.diagnostics }
  // agent() 返回 null：可能是限额耗尽（router 中文错误如"已达到 5 小时的使用上限"常被 runtime
  // 吞为空响应），可能是 thinking-only 空响应，也可能是模型能力不足（400 Repetitive tool calls 等
  // 被 runtime 吞为 null）。如果有 retryModel，用更强模型重试一次；否则 halt 等 resume。
  // 不能放任 null 流到调用方导致 `boot.halted`/`impl.halted` crash（observed wf_a80ebbf1）。
  if (impl == null) {
    if (retryModel && retryModel !== model) {
      log(`⚠ ${opts?.label || 'unknown'}: ${model} returned null (capability failure likely), retry with ${retryModel}`)
      try {
        impl = await agent(prompt, { ...opts, model: retryModel })
        // Q1（第 4 轮）: retry 路径须检查 model_unavailable status（与首次调用一致）
        //   retryModel 也限额耗尽 → 返回 {status:'model_unavailable'} → 调用方访问 impl.evidence crash
        if (impl != null) {
          if (impl?.status === 'model_unavailable') return { halted: true, reason: 'model_unavailable', diag: impl.diagnostics }
          return impl
        }
      } catch (e) {
        if (isQuotaError(e)) return { halted: true, reason: 'model_unavailable', diag: { model: retryModel, error: errStr(e) } }
        // P0-4（第 6 轮）: retry 路径同样封装 agent_error（不 throw）
        return { halted: true, reason: 'agent_error', diag: { model: retryModel, error: errStr(e) } }
      }
    }
    // Q6（第 5 轮）: 消息须根据是否有 retryModel 分支——无 retry 时说 "retry exhausted" 误导（从未 retry）。
    //   有 retry：说明 retry with ${retryModel} 也耗尽；无 retry：仅说明首次 agent 返回 null。
    const nullErr = retryModel ? `agent returned null (quota exhausted or capability failure — retry with ${retryModel} also exhausted)` : 'agent returned null (quota exhausted or capability failure)'
    return { halted: true, reason: 'model_unavailable', diag: { model, error: nullErr } }
  }
  return impl
}

// 跑一轮 spec‖qual‖hunt 并行 review + 双守卫（Q10 抽取，主轮/simplify 轮/destructive 轮共用）。
// 只抽"并行调用 + reviewHaltReason + reviewHaltForEmptyFailed"——后续逻辑（halt/amend/记录）由调用方决定。
// concernsHint：主轮传 implementor 疑虑，simplify/destructive 传空串。
// labelSuffix：区分轮次（`:r${round}` / `:simp` / `:destructive`）。
// phaseLabel：主轮+destructive 传 `Plan ${plan.id}`（Workflow UI 显示阶段），simplify 传空。
// 返回 { spec, qual, hunt, haltReason, emptyFailed }。haltReason 非空时 emptyFailed=null（短路）。
async function runReviewRound(taskId, cfg, plan, fc, concernsHint, labelSuffix, phaseLabel) {
  // Q7: 三处 opts 都设 phase（若 phaseLabel 非空）——/workflows UI 中 spec/qual/hunt 按阶段分组一致
  const commonOpts = phaseLabel ? { phase: phaseLabel } : {}
  const [spec, qual, hunt] = await parallel([
    async () => safeAgent(buildPrompt('specReview', { taskId, specPath: cfg.spec_path, planFilePath: plan.file, filesChanged: fc, concernsHint, referencePaths: formatReferencePaths(cfg.reference_paths), lessonsPath: cfg.lessons_path || '' }), { schema: SCHEMAS.specReview, model: 'opus', ...commonOpts, label: `spec:${taskId}${labelSuffix}` }),
    async () => safeAgent(buildPrompt('qualityReviewer', { taskId, filesChanged: fc, languageChecklist: languageChecklist(cfg.language), lessonsPath: cfg.lessons_path || '' }), { schema: SCHEMAS.qualityReviewer, model: 'opus', ...commonOpts, label: `qual:${taskId}${labelSuffix}` }),
    async () => safeAgent(buildPrompt('hunter', { taskId, filesChanged: fc, silentFailureContext: formatSilentFailureContext(cfg.silent_failure_context, cfg.silent_failure_intro) }), { schema: SCHEMAS.hunter, model: 'sonnet', ...commonOpts, label: `hunt:${taskId}${labelSuffix}` }),
  ])
  const haltReason = reviewHaltReason(spec, qual, hunt)
  const emptyFailed = haltReason ? null : reviewHaltForEmptyFailed(spec, qual, hunt)
  return { spec, qual, hunt, haltReason, emptyFailed }
}

// 提交约定单一事实源（emission ↔ recognition 对称）（inline 自 lib.js）。
// feat(plan-XX/TY): 是 bootstrap 识别已完成 task 的唯一约定；他类 scope 会令 task 不可见 → 重跑 → OSCILLATING。
function commitSubject(seq, taskId, title) {
  const planIdShort = `plan-${String(seq).padStart(2, '0')}`
  return `feat(${planIdShort}/${taskId}): ${title}`
}

// 把 completed id 归一化为 plan-scoped key "plan-{seq}/T-{id}"（inline 自 lib.js）。
// 避免跨 plan 同名 task 误跳过：去 plan 前缀会让 Plan 02 的 T2 被 Plan 01 的 T2 误 skip。
// 分隔符容忍 `/` 与 `-`：bootstrap agent 返回格式不稳定，偶用连字符（"01-T2"），
// 只认 `/` 会让其原样漏过 → 与 taskKey("plan-01/T2") 不等 → 已完成 task 误判 pending → 重做。
function normalizeCompleted(ids) {
  return ids.map(id => {
    const m = String(id).match(/^(?:plan-)?(\d+)[\/\-]+(T[\w-]+)$/i)
    return m ? `plan-${m[1]}/${m[2]}` : String(id)
  })
}

// Strip plan-XX/ 前缀，返回裸 task id —— inline 自 lib.js（与 normalizeCompleted 同源，sync QC-4 守护）。
// bootstrap 偶返 plan-scoped task_id → taskKey/commitSubject 双重前缀 + completed 误判 → 重跑（2026-07-05）。
function bareTaskId(id) {
  return String(id).replace(/^plan-\d+\/+/i, '')
}

// 过滤非叶子父 task（T{N} 与 T{N}{letter} 共存 → drop T{N}）—— inline 自 lib.js（sync QC-4 守护）。
// bootstrap 偶不遵循 leaf-first → 返回 ## Task N 父说明段 → implementor 跑说明段混乱（wf_3e729d02 T6）。
function dropParentTasks(tasks) {
  return tasks.filter(t => {
    const m = String(t.id).match(/^T(\d+)$/)
    if (!m) return true
    const re = new RegExp(`^T${m[1]}[a-z]`)
    return !tasks.some(x => re.test(String(x.id)))
  })
}

// 从 git log subjects 正则提取 completed task keys —— inline 自 lib.js（sync QC-4 守护）。
// 把"提取 completed"从 LLM 拿走交给正则（kimi-k2.7 偶漏 task 如 plan-06/T6d）。自包含，便于 inline。
function extractCompletedFromSubjects(subjects) {
  const out = new Set()
  for (const s of (Array.isArray(subjects) ? subjects : [])) {
    const m = String(s).match(/^(?:feat|fix|refactor)\(plan-(\d+)\/(T[\w-]+)\)\s*:/i)
    if (m) out.add(`plan-${m[1]}/${m[2]}`)
  }
  return [...out]
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

// ===== 条件渲染 helpers（inline 自 lib.js；通用性：项目特有内容靠 config 驱动，prompt 保持单一模板）=====
// orchestrator 显式传空串（非 undefined），buildPrompt 才会把占位符替换为空而非残留 {{k}}。
function formatReferencePaths(paths) {
  if (!Array.isArray(paths) || paths.length === 0) return ''
  const lines = paths.map(p => `- ${p}`).join('\n')
  return `## Reference Documents (authoritative — match these exactly)
${lines}
Read the relevant section(s) BEFORE implementing/reviewing domain-specific logic or rules. Deviations from these authoritative rules are bugs.`
}
// 项目特定静默失败纪律（可选 config 注入）——通用 hunter 清单之上，注入本项目反复踩的领域致命点。
// 不填 → 空串 → hunter 退化为通用清单（通用性不破坏）。填了 → hunter 重点核查这些项目特定条款。
function formatSilentFailureContext(items, intro) {
  if (!Array.isArray(items) || items.length === 0) return ''
  const lines = items.map(it => `- ${it}`).join('\n')
  const heading = intro || 'Project-Specific Silent-Failure Risks (HIGHEST PRIORITY — hunt these first)'
  return `## ${heading}
Beyond the generic silent-failure patterns below, the following project-specific traps have caused real misses and MUST be checked explicitly:
${lines}
For each, verify the changed code does not fall into the trap. Report a silent_failure with the specific trap name + file:line + why it violates.`
}
// 跨 session 失败方案追踪：bootstrap 扫 runs/*/manifest.json 提取历史失败方案，
// 注入 implementor prompt 防止重复相同失败路径。不填 → 空串 → prompt 段消失。
function formatFailedApproaches(items) {
  if (!Array.isArray(items) || items.length === 0) return ''
  const lines = items.map(it => `- ${it.task_id}: ${it.reason} — ${it.error}`).join('\n')
  return `## Prior Failed Approaches (do not repeat)
${lines}
If your plan is similar to any above, explicitly state the difference.`
}

// LESSONS.md 跨任务失败知识库：config 可选声明 lessons_path，
// bootstrap 读取并匹配 task 关键词注入 implementor。不填 → 空串 → prompt 段消失。
function formatLessons(items) {
  if (!Array.isArray(items) || items.length === 0) return ''
  const lines = items.map(it => `- [${it.id}] ${it.title} — ${it.detail}`).join('\n')
  return `## Lessons Learned (check against these before implementing)
${lines}
If your plan is similar to any lesson above, explicitly state why your approach differs.`
}

// —— v3 lessons 两层注入（inline 自 lib.js，sync.test 字节守护）——
function formatUniversalLessons(allLessons) {
  if (!Array.isArray(allLessons) || allLessons.length === 0) return ''
  const universal = allLessons.filter(l => l && /^(silent[-_]?failure)$/i.test(String(l.category).trim()))
  if (universal.length === 0) return ''
  const lines = universal.map(l => `- [${l.id}] ${l.title} — ${l.detail}`).join('\n')
  return `## Universal Discipline (silent-failure — always apply)
${lines}
These are project-wide silent-failure disciplines. Before reporting done, verify your code does not violate any of them (savepoint isolation, naive-UTC datetime, single-transaction commits, etc.).`
}

function formatDomainLessons(allLessons, taskCategories, currentPlanSeq, taskTitle) {
  if (!Array.isArray(allLessons) || allLessons.length === 0) return ''
  // 排除 silent-failure（Tier 1 已注入）。Q1 修复（2026-07-06）：须与 formatUniversalLessons
  //   的正则容错对称——否则变体（silent_failure/Silent-Failure/带空格）会被 Tier 2 重复匹配注入。
  const candidates = allLessons.filter(l => l && !/^(silent[-_]?failure)$/i.test(String(l.category).trim()))
  let matched = []
  if (Array.isArray(taskCategories) && taskCategories.length > 0) {
    // category 匹配
    matched = candidates.filter(l => taskCategories.includes(l.category))
  } else if (taskTitle) {
    // fallback: title 关键词重叠（旧行为）
    const tokens = String(taskTitle).toLowerCase().split(/[\s,，、]+/).filter(t => t.length > 1)
    matched = candidates.filter(l => {
      const text = `${l.title || ''} ${l.detail || ''}`.toLowerCase()
      return tokens.some(t => text.includes(t))
    })
  }
  if (matched.length === 0) return ''
  // 同 plan 优先（source 含 currentPlanSeq 排前）
  if (currentPlanSeq) {
    matched.sort((a, b) => {
      const aSame = a.source && String(a.source).includes(currentPlanSeq) ? 0 : 1
      const bSame = b.source && String(b.source).includes(currentPlanSeq) ? 0 : 1
      return aSame - bSame
    })
  }
  const capped = matched.slice(0, 5)
  const lines = capped.map(l => `- [${l.id}] ${l.title} — ${l.detail}`).join('\n')
  return `## Domain Lessons (check against these before implementing)
${lines}
If your plan is similar to any lesson above, explicitly state why your approach differs.`
}

// write_files 边界控制：plan frontmatter 可选声明 write_files，
// commit agent 提交前检查 git diff 是否越界。不声明 → 空串 → 检查跳过。
function formatWriteFilesScope(files) {
  if (!Array.isArray(files) || files.length === 0) return ''
  const lines = files.map(f => `- ${f}`).join('\n')
  return `## Write Files Boundary (commit agent will verify)
${lines}
Before committing, run git diff --name-only. If any file is NOT in the list above, you MUST either: 1. revert the out-of-scope change, or 2. report status=failed with out_of_scope in diagnostics.`
}

// schema 迁移一致性检查：config 可选声明 schema_tool + model_paths + migration_paths，
// gate agent 在 committed SHA 上检查 model 文件有变更但无对应迁移文件。不声明 → 空串 → 检查跳过。
function formatSchemaCheck(schemaTool, modelPaths, migrationPaths) {
  if (!schemaTool) return ''
  const mp = Array.isArray(modelPaths) ? modelPaths.join(', ') : ''
  const xp = Array.isArray(migrationPaths) ? migrationPaths.join(', ') : ''
  return `## Schema Migration Check (gate agent must verify)
1. Run git diff --name-only HEAD~1..HEAD — you are already checked out to the committed SHA, so HEAD~1 is the parent commit.
2. Filter changed files by model_paths: ${mp}
3. Filter changed files by migration_paths: ${xp}
4. If model files changed but NO migration files changed → status=failed, evidence.migration_missing=true`
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

// 跨 reviewer 文件重叠检测：按 file 分组 findings → 返回分组数组。
// 纯函数，不依赖任何映射表或 agent 调用。spec §3.1。—— inline 自 lib.js
// W1-5b: file 经 normalizeFilePath 归一化后再分组，防路径格式差异致同文件分到不同 group。
function groupFindingsByFile(findings) {
  const groups = {}
  for (const f of findings) {
    if (!f.file) continue
    const normFile = normalizeFilePath(f.file)
    if (!groups[normFile]) groups[normFile] = { file: normFile, sources: new Set(), findings: [] }
    groups[normFile].sources.add(f.source)
    groups[normFile].findings.push(f)
  }
  return Object.values(groups)
}

// 格式化跨 reviewer 文件重叠为 implementor 可读的注入文本。
// 仅当某文件有 ≥2 个不同 reviewer 标记时才输出该段。spec §3.1。—— inline 自 lib.js
function formatCrossReviewerNote(findings) {
  const groups = groupFindingsByFile(findings).filter(g => g.sources.size >= 2)
  if (groups.length === 0) return ''

  let out = '\n## ⚠ Cross-Reviewer Overlap (≥2 reviewers flagged same file — check for conflicts)\n'
  for (const g of groups) {
    const srcs = [...g.sources].sort().join('/')
    out += `\n### ${g.file} (flagged by: ${srcs})\n`
    for (const f of g.findings) {
      out += `- [${f.source}${f.severity ? '|' + f.severity : ''}] ${f.title}${f.fix ? ' — fix: ' + f.fix : ''}\n`
    }
  }
  return out
}

// ===== SCHEMAS（inline 自 lib.js Task 5，去 export）=====
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
          properties: { severity: { type: 'string', enum: ['critical', 'important', 'minor'] }, title: { type: 'string' }, file: { type: 'string' }, fix: { type: 'string' } },
        } } } },
      summary: { type: 'string' },
    } }
}

const SCHEMAS = {
  bootstrap: {
    type: 'object', required: ['status', 'evidence'], additionalProperties: true,
    properties: {
      status: { type: 'string', enum: ['ok', 'failed', 'blocked'] },
      evidence: { type: 'object', required: ['config', 'plans', 'completed', 'git_log_subjects', 'dirty_tree', 'in_progress', 'failed_approaches', 'task_lessons', 'task_write_files'],
        properties: { config: { type: 'object' }, plans: { type: 'array' }, completed: { type: 'array' }, git_log_subjects: { type: 'array', items: { type: 'string' } }, dirty_tree: { type: 'boolean' }, in_progress: { type: 'boolean' }, failed_approaches: { type: 'array', items: { type: 'object', required: ['task_id', 'plan_seq', 'reason', 'error'], properties: { task_id: { type: 'string' }, plan_seq: { type: 'integer' }, reason: { type: 'string' }, error: { type: 'string' } } } }, task_write_files: { type: 'array' }, task_lessons: { type: 'array' }, all_lessons: { type: 'array' } } },
      diagnostics: { type: 'object' }, summary: { type: 'string' },
    },
  },
  implementor: {
    type: 'object', required: ['status'], additionalProperties: true,
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
      evidence: { type: 'object', required: ['commit_sha', 'committed_files', 'tests_at_commit'],
        properties: { commit_sha: { type: 'string' }, committed_files: { type: 'array' }, tests_at_commit: { type: 'integer' } } },
      diagnostics: { type: 'object', properties: { out_of_scope: { type: 'array' }, destructive_changes: { type: 'array' } } }, summary: { type: 'string' } } },
  contextFetcher: { type: 'object', required: ['diagnostics'], additionalProperties: true,
    properties: { diagnostics: { type: 'object', required: ['context'], properties: { context: { type: 'string' } } }, summary: { type: 'string' } } },
  gate: { type: 'object', required: ['status', 'evidence'], additionalProperties: true,
    properties: { status: { type: 'string', enum: ['ok', 'failed', 'model_unavailable'] },
      evidence: { type: 'object', required: ['tests_exit_code', 'pytest_summary', 'lint_results', 'restored_head'],
        properties: { tests_exit_code: { type: 'integer' }, pytest_summary: { type: 'string' }, lint_results: { type: 'array', items: { type: 'object', required: ['command', 'exit_code'], properties: { command: { type: 'string' }, exit_code: { type: 'integer' }, summary: { type: 'string' } } } }, migration_missing: { type: 'boolean' }, restored_head: { type: 'string' } } }, summary: { type: 'string' } } },
  finalReport: { type: 'object', required: ['summary'], additionalProperties: true,
    properties: { evidence: { type: 'object', properties: { manifest_path: { type: 'string' } } }, summary: { type: 'string' } } },
  lessonDistiller: { type: 'object', required: ['decisions'], additionalProperties: true,
    properties: {
      decisions: { type: 'array', items: {
        type: 'object', required: ['action', 'title', 'detail'],
        properties: {
          action: { type: 'string', enum: ['append', 'update', 'skip'] },
          id: { type: 'string' },
          title: { type: 'string' },
          detail: { type: 'string' },
          source: { type: 'string' },
          category: { type: 'string', enum: ['silent-failure', 'dependency', 'convention', 'test-strategy', 'other'] },
          update_target_id: { type: 'string' },
        },
      } },
      summary: { type: 'string' },
    } },
}

// ===== PROMPTS（inline 自 lib.js Task 6，增强版 bootstrap，去 export）=====
const PROMPTS = {
  bootstrap: `You are the BOOTSTRAP agent for the workflow orchestrator. Read project state and return structured data. You MAY write YAML frontmatter to plan files that lack it (idempotent). Modify no other files.

Inputs: configPath={{configPath}} plansDir={{plansDir}} runTs={{runTs}}

Steps:
1. Read {{configPath}} → {test_command, full_test_command, build_command, lint_command, extra_lint_commands, spec_path, reference_paths, language, silent_failure_context, silent_failure_intro, lessons_path}. extra_lint_commands / reference_paths / silent_failure_context / silent_failure_intro / lessons_path are OPTIONAL (may be absent → treat as [] / [] / [] / '' / ''). If config contains lessons_path, read that file. Extract all entries as all_lessons: [{id, title, detail, category, source}].   - category inference: if an entry lacks category, infer from title/detail. If it is clearly about silent-failure/savepoint/transaction/datetime/null/empty/etc., set category='silent-failure'; otherwise set category='other' or infer a domain category.   - source: include the source location if known (e.g., 'plan-06/T1' or lessons.md filename), otherwise empty string.   Return matched lessons per task in evidence as task_lessons (backward-compatible keyword matching, same shape as before): [{task_id, plan_seq, lessons:[{id, title, detail}]}].   Additionally, return all_lessons: the full list of all lessons parsed from lessonsPath as [{id, title, detail, category, source}] (include category field even if inferred). This feeds v3 two-tier injection (Tier 1 silent-failure always + Tier 2 domain by category). Absent lessons_path → both arrays empty.
2. Config smoke: run test_command with --collect-only. 判断：命令本身不存在（command not found / No such file: pytest）→ status=failed（环境/typo）；命令存在但 collect 失败（no module named pytest / pyproject.toml 不存在 / no tests collected / 业务代码未初始化）→ 记录 'project not yet initialized' 到 summary，status 仍 ok（业务代码由后续 task 创建，预期）。
3. For each {{plansDir}}/*.md: if frontmatter (starts with ---) read task models; else generate — extract LEAF ids — **CRITICAL: 必须返回 frontmatter models: 的每一个 key（含最大的 N，如 T10），一个不漏；body 里 ## Task N 若有 ### Task NX 子 task → 只取子 task（NX），子 task 不可遗漏；## Task N 无子 task → 取 N 本身**（leaf-first: ## Task N with ### Task NX children → only NX; else N), modelHint (title contains 安全|加密|认证|JWT|CSRF|Fernet|算法|比对|策略|边界|集成|接口 → opus, else omit), write frontmatter at file top. Idempotent. Record each plan's file (full path) and seq (last two digits of filename, e.g. 01). Also read write_files from frontmatter if present (format: "write_files:\n  T1:\n    - src/a.py\n    - src/b.py"). Return as task_write_files in evidence: [{task_id, plan_seq, files:[...]}] (plan_seq = this plan's seq). Absent → empty array.
4. git log → 运行 git log --format=%s -n 200，**原样复制**每个 commit subject 第一行到 git_log_subjects（string[]，最多 200 条）。**不要解析、提取、转换、过滤、去重**——orchestrator 用正则从 subjects 确定性提取 completed，你只负责忠实复制 git log 输出。同时仍返回 completed（你最好的 task-id 提取，作 fallback，但不再作为单一事实源）。
5. git status --porcelain → dirty_tree. If dirty_tree=true (uncommitted changes from a crashed previous run, §6.2 半提交自愈), classify and handle each change (W1-1/W1-4, 2026-07-07):
   a. Workflow artifact changes:
      - lessons.md (path = lessons_path read in step 1) has changes → git commit -m "chore(workflow): auto-commit lessons.md from interrupted run" <lessonsPath> (preserve knowledge base, best-effort; H-F2 2026-07-07: 用 git commit <path> 一步到位不预 staged, 防 add 成功 commit 失败后 5b reset --hard 清除 staging).
      - runs/ and .workflow/ changes → git checkout -- runs/ .workflow/ (discard, regenerable).
   b. Remaining changes = implementor half-done → git reset --hard HEAD to clean.
   c. Re-run git status --porcelain to confirm clean; set dirty_tree=false in evidence.
   d. If any step fails → leave dirty_tree=true and record the error in summary.
   Rationale: old logic ran git reset --hard HEAD unconditionally, discarding lessons.md updates (knowledge base). New logic preserves lessons.md, discards regenerable artifacts, cleans implementor half-done code.
6. For each leaf task return its model (sonnet|opus|undefined→sonnet) and title (the description text from the Task header).
7. If runs/ directory exists: scan runs/*/manifest.json files. For each, read per_task object. For each task_id in per_task that has blocked_info, extract {task_id, plan_seq (the plan sequence this task belongs to, from the task_id prefix 'plan-<seq>/T-Y' or from the plan context), reason (from blocked_info.reason), error (from blocked_info.last_error)}. Filter to task_ids that match leaf tasks in the current plans. Return as failed_approaches in evidence. Also check if any task has status='in_progress' → in_progress=true (else false). If runs/ does not exist → failed_approaches=[], in_progress=false.

Return {status, evidence:{config (include ALL fields read in step 1, even optional ones if present), plans:[{id, file, seq, tasks:[{id, model, title}]}], completed:[...], dirty_tree, in_progress, failed_approaches:[{task_id, plan_seq, reason, error}], task_write_files:[{task_id, plan_seq, files:[...]}], task_lessons:[{task_id, plan_seq, lessons:[{id, title, detail}]}], all_lessons:[{id, title, detail, category, source}]}, summary}.
RED FLAG: evidence 必须是真实读取结果，绝不编造。`,

  implementor: `You are the IMPLEMENTOR for {{taskId}} (plan {{planId}}). TDD strict (RED→GREEN→REFACTOR). {{retryNote}}

## Discipline (HARD REQUIREMENTS — 违反会导致 workflow 状态混乱)
- DO NOT run \`git commit\` or \`git add\`. Committing is handled by a separate COMMIT agent after review passes.
- Your job is to write code + tests only. Leave changes in the working tree uncommitted.
- If you think committing is necessary, report status=blocked instead.
- When applying a lesson from {{lessons}} to harden code (W1-5e, 2026-07-07), add a comment on the hardened line(s) referencing the lesson id (e.g. \`// L-20260701T103320Z: guard null per lesson\`). This lets spec-reviewer apply Lessons Learned Exemption and not flag your hardening as EXTRA.

Inputs: specPath={{specPath}} testCommand={{testCommand}} buildCommand={{buildCommand}} planFile={{planFilePath}} taskId={{taskId}} fixIssues={{fixIssues}}
{{referencePaths}}
{{failedApproaches}}
{{lessons}}
{{fetchedContext}}

Steps:
1. Read {{planFilePath}}, locate {{taskId}} section: files to create/modify, tests to write.
2. Read {{specPath}} relevant section; implement to spec. If reference documents are listed above, read the relevant rule section BEFORE writing domain-specific logic.
3. RED: write ONE minimal failing test for one behavior. Run {{testCommand}}; CONFIRM it fails — and fails for the RIGHT reason (feature missing), not a typo/import error. A test that passes immediately proves nothing (you may be testing existing behavior) — fix the test.
4. GREEN: minimal code to pass the test. Don't add features or refactor beyond the test. If {{buildCommand}} is non-empty, run it before tests to verify the project builds.
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

## 6-Dimension Quick Check (before reporting)
- Cognitive Overload: any function > 50 lines or nesting > 3 levels?
- Change Propagation: did you change files unrelated to this task?
- Knowledge Duplication: did you paste similar logic in 2+ places?
- Accidental Complexity: did you add abstraction not needed by current requirements?
- Dependency Disorder: any business layer importing infrastructure implementation?
- Domain Distortion: are variable names domain terms, not generic (data/item/info)?

If self-review finds issues, fix them now.

Return {status, evidence:{tests_exit_code, files_changed:[...], pytest_summary}, diagnostics:{blocked_category, last_error, suggested_fix, concerns} (diagnostics only if blocked/done_with_concerns), summary}.
- status=ok: done, tests_exit_code=0. MUST provide evidence with real tests_exit_code / files_changed / pytest_summary.
- status=done_with_concerns: done (tests green) but you have doubts about correctness/scope → fill diagnostics.concerns (array). MUST provide evidence as in ok.
- status=failed: tests failed after retry. evidence is OPTIONAL (record real tests_exit_code if available); diagnostics may contain last_error/suggested_fix.
- status=blocked: 障碍 (interface|file|spec|dependency|external) → fill diagnostics. evidence is OPTIONAL (no real test run).
- status=needs_context: missing info → fill diagnostics.blocked_category + last_error. evidence is OPTIONAL.
RED FLAG: tests_exit_code 必须真实，绝不编造 0。绝不跳过测试。遇障碍宁可 blocked 也不要伪造通过。若遇到 model 限额耗尽（quota/rate-limit/429 错误），返回 status:'model_unavailable'（非 failed/blocked），让 orchestrator halt 并保存进度。`,

  specReview: `You are the SPEC-REVIEWER (model opus). Verify implementor built EXACTLY what was requested — nothing missing, nothing extra, no misunderstanding. Verdict on CURRENT working tree (HEAD or staged).

Inputs: specPath={{specPath}} taskId={{taskId}} planFile={{planFilePath}} changedHint={{filesChanged}}{{concernsHint}}
{{referencePaths}}
lessonsPath={{lessonsPath}}

## Task Scope Boundary (critical for multi-task plans)
Each task implements ONLY what its plan section requests. Methods/interfaces/fields needed by FUTURE tasks (documented in DESIGN.md or spec but NOT in this task's plan section) are NOT missing — they belong to their respective future tasks. Do not flag them as MISSING.
Conversely, if the implementor adds methods/tests NOT in this task's plan section, that IS over-build — flag it as EXTRA.

## Lessons Learned Exemption (防 reviewer ↔ implementor 振荡)
W1-5e (2026-07-07): workflow 自主学习闭环——implementor 按 {{lessonsPath}} 中记录的 lesson 加固代码时，该加固虽不在 plan section 字面要求内，但 NOT EXTRA。否则 reviewer 报 EXTRA → implementor 删 → 下轮 implementor 又按 lesson 加回来 → OSCILLATING halt。
判定流程（疑似 EXTRA 时执行）：
1. 查 commit message / 代码注释是否有 L-<timestamp> 编号（如 L-20260701T103320Z，与 lessons.md 中 ## L-<ts> 条目格式一致）。
2. 有 L-xxx 编号 → 读 {{lessonsPath}} 中该 lesson 条目，核对加固是否 minimal 且 on-target（只修 lesson 描述的问题，未越界扩展）。
3. minimal + on-target → NOT EXTRA（lessons learned 闭环修复）。
4. 无 L-xxx 编号 / 加固超出 lesson 范围 / lesson 不存在 → 仍按 EXTRA 报告。

Steps:
1. git diff (or read changed files) for this task.
2. Read {{specPath}} section governing {{taskId}}. If reference documents are listed above, verify domain-specific logic and rules match them exactly.
3. Verify THREE dimensions (don't trust the implementer report — read the actual code):
   a. MISSING requirements: anything in spec not implemented? claimed-working but not actually done?
   b. EXTRA / over-build (YAGNI): anything built that spec did NOT request? unrequested features, over-engineering, "nice to haves"? This is critical — flag any functionality the spec forbids or didn't ask for.
   c. MISUNDERSTANDING: requirement interpreted differently than intended? right feature wrong way?
4. Record files_touched (files in the diff).

This is a STATIC READ-ONLY review. You may use 'git diff', 'git status', 'find', 'grep'/'rg', and read files to locate and inspect changes. Do NOT run the test suite, ruff, lint, or any build — spec verification is done by reading code, not by running it. Running tests/builds is the implementor's and gate's job, not yours.

Return {status (ok|failed), diagnostics:{files_touched:[...], issues:[<dimension>: <spec requirement>: <code gap or over-build>]}, summary}.
RED FLAG: ok 仅当三维度全清——逐条 spec 全符合 AND 无越界（lessons learned 修复经 Exemption 判定后不算越界）。绝不模糊通过。越界（spec 未要求的功能，尤其是合规红线禁止类如预测/推荐）必须 failed。issues 要具体（哪条 spec + 代码哪里不符/越界 + file:line）。若遇到 model 限额耗尽（quota/rate-limit/429 错误），返回 status:'model_unavailable'（非 failed），让 orchestrator halt 并保存进度。`,

  qualityReviewer: `You are the QUALITY-REVIEWER (model opus). Review code quality: architecture, boundaries, types, immutability, error handling, naming. Verdict on CURRENT tree.

Inputs: taskId={{taskId}} changedHint={{filesChanged}}
{{languageChecklist}}
lessonsPath={{lessonsPath}}

## Universal quality checks
- 函数 <50 行, 文件 <800 行, 无深层嵌套 (>4), 错误显式处理, 无 mutation, 无硬编码值, 命名清晰.
- Each file has one clear responsibility; units decomposed so they can be tested independently.
- Did this change create new large files or significantly grow existing ones? (Don't flag pre-existing sizes — focus on what this change contributed.)

## Lessons Learned Exemption (限定维度硬性豁免，防 reviewer ↔ implementor 振荡)
W1-5e (2026-07-07): implementor 按 {{lessonsPath}} 中记录的 lesson 加固代码时（commit message / 代码注释含 L-<timestamp> 编号，如 L-20260701T103320Z），以下维度**不报 finding**：
- over-engineering / single-use helper（lesson 加固常加 helper）
- 函数超 50 行（lesson 加固常让函数变长）
- helper / abstraction 数量（lesson 加固常引入新抽象）
判定流程（疑似上述维度 finding 时执行）：
1. 查 commit message / 代码注释是否有 L-xxx 编号。
2. 有 L-xxx 编号 → 读 {{lessonsPath}} 核对加固是否 minimal 且 on-target → 满足则**不报**该维度 finding。
3. 无 L-xxx 编号 / 加固超出 lesson 范围 → 正常报告。
**不豁免的维度**（仍正常判断）：命名清晰度、类型注解、错误处理、深层嵌套、mutation、硬编码值。lesson 加固不应损害这些维度。

## Steps
1. Read changed files.
2. Check universal checks + the language-specific checklist above. (Note: architectural discipline like layer-purity is enforced automatically by the gate's lint commands — you focus on code a human must judge; do NOT invent layer rules not in the checklist.)
3. Record files_touched.

This is a STATIC READ-ONLY review. You may use 'git diff', 'git status', 'find', 'grep'/'rg', and read files to locate and inspect changes. Do NOT run the test suite, ruff, lint, or any build — quality review is done by reading code, not by running it. Running tests/builds is the implementor's and gate's job, not yours.

## Calibration
Categorize issues by ACTUAL severity — not everything is Critical. Acknowledge what was done well (strengths) before listing issues; accurate praise helps the implementer trust the rest.

Return {status (ok|failed), diagnostics:{files_touched:[...], issues:[{severity: critical|important|minor, title, file, fix}]}, summary}.
issues 元素 MUST 是 object 且必有 title + fix（severity/file 亦建议）——纯字符串或缺 title/fix 的对象会被 schema 拒绝。
RED FLAG: ok 仅当无 critical/important 问题。critical/important（架构/安全/正确性）必须 failed；仅 minor 可 ok（记入 issues）。若遇到 model 限额耗尽（quota/rate-limit/429 错误），返回 status:'model_unavailable'（非 failed），让 orchestrator halt 并保存进度。`,

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

Inputs: taskId={{taskId}} filesChanged={{filesChanged}}

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
RED FLAG: changed 必须如实。orchestrator 用 git diff --stat 独立验证你是否改了代码（不信任自报），有改动则触发 review。`,

  commit: `You are COMMIT. Create one atomic commit for task {{taskId}}.

Inputs: taskId={{taskId}} planId={{planId}} testCommand={{testCommand}} commitMsg={{commitMsg}} writeFilesScope={{writeFilesScope}}

## 提交约定（HARD REQUIREMENT — 违反会导致 OSCILLATING halt）
git 提交消息**必须**严格等于下面这条（orchestrator 已按 feat(plan-XX/TY): title 格式预计算好，原样使用，不要改写 scope、不要自拟标题）：
  {{commitMsg}}
理由：bootstrap 扫 git log 用约定 feat(plan-XX/TY): 识别"已完成 task"。任何他类 scope 都会让该 task 对 bootstrap 不可见 → 被判未完成 → 重跑 → OSCILLATING halt。
**严禁照抄 plan 文件里 Step 5/8 的示意提交消息**（如 feat(scheduler): ... / feat(notifications): ... / 无 scope 的 feat: ...）——那些只是写法的示意，不是真实提交命令。本 task 唯一合法的提交消息就是上面的 {{commitMsg}}。

Steps:
1. git status --porcelain → see staged/unstaged.
2. Run {{testCommand}} on current tree; confirm exit 0. If fail → status=failed (do NOT commit).
2.5. If writeFilesScope is non-empty: run git diff --name-only. Compare with writeFilesScope. If any file is out of scope → status=failed, diagnostics.out_of_scope=[<files>]. Do NOT commit.
2.6. Destructive Change Detection: run git diff HEAD --numstat. For each file:
  S4（第 4 轮）: 须用 git diff HEAD（非 git diff --cached）——文件未 git add 时 --cached 永远为空，
    destructive review 永不触发。git diff HEAD 对比工作树与 HEAD，无需暂存即可检测改动。
  a. If column 2 (deletions) >= 5 AND file is not a test file → record {type:'deleted_code', file, detail:'<N> lines deleted'}
  b. If file is deleted (git diff HEAD --name-status shows D) → record {type:'file_deletion', file, detail:'file deleted'}
  c. For exported symbol signature changes: read the diff hunks. If a function/class exported symbol's params or return type changed → record {type:'signature_change', file, detail:'<symbol> signature changed'}
  If any hit → record in diagnostics.destructive_changes: [{type, file, detail}]. Still proceed to commit (status=ok), but orchestrator will trigger an extra review round.
3. git add -A; git commit -m "{{commitMsg}}"。
4. **强制校验 + 纠偏**：git log -1 --format=%s 取 HEAD 主体，与 {{commitMsg}} 比对。若不符（任何原因——比如实现 agent 之前已用错误 scope 提交过、或 HEAD 已存在但消息不对）：git commit --amend -m "{{commitMsg}}" 纠正。这是确定性的：无论谁提交、提交了什么，最终 HEAD 消息必为 {{commitMsg}}。
5. git rev-parse HEAD → commit_sha。

Return {status (ok|failed), evidence:{commit_sha, committed_files:[...], tests_at_commit}, summary}.
RED FLAG: tests exit != 0 时绝不 commit（status=failed）。commit_sha 必须真实。HEAD 消息必须等于 {{commitMsg}}（步骤 4 校验，不符必 amend）。若遇到 model 限额耗尽（quota/rate-limit/429 错误），返回 status:'model_unavailable'（非 failed），让 orchestrator halt 并保存进度。`,

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
{{schemaCheck}}

Steps:
1. git checkout {{sha}}.
2. For EACH command in the array: run it, record {command, exit_code, summary}. tests_exit_code = exit code of the FIRST kind:"test" command (0 if none).
3. git checkout - (restore previous HEAD). CRITICAL: must restore or downstream tasks break.
4. If step 3 fails, git checkout <previous-branch> explicitly.

Return {status (ok|failed), evidence:{tests_exit_code, pytest_summary, lint_results:[{command, exit_code}], restored_head}, summary}.
- restored_head: 步骤 3/4 恢复后执行 'git rev-parse HEAD' 的 40 位 SHA，供 orchestrator 验证基线已恢复。
- status=ok ONLY if EVERY command exit_code == 0 AND restored_head 非空。
RED FLAG: every exit_code 必须真实（你在 committed SHA 上亲跑）。必须 checkout 回原 HEAD 并记录 restored_head。任一 exit != 0 → status=failed（包括 lint 命令——架构纪律如层纯度由 lint 强制）。若遇到 model 限额耗尽（quota/rate-limit/429 错误），返回 status:'model_unavailable'（非 failed），让 orchestrator halt 并保存进度。`,

  headVerifier: `You are HEAD-VERIFIER. Read-only. Run "git rev-parse HEAD" in the repository root and return the current HEAD SHA.

Return {status:"ok", evidence:{head:"<40-char-sha>"}, summary}.
RED FLAG: head must be the actual output of git rev-parse HEAD, never fabricated.`,

  finalReport: `You are FINAL-REPORT (mode={{mode}} done|halted). Write the run manifest (the ONLY on-disk write in this workflow) and emit a digest.

Inputs: mode={{mode}} state={{stateJson}} blockedInfo={{blockedInfo}} runsDir={{runsDir}} runTs={{runTs}} lessonsPath={{lessonsPath}}

Steps:
1. mkdir -p {{runsDir}}.
2. Write {{runsDir}}/manifest.json = {run_ts:{{runTs}}, mode:{{mode}}, plans:[...], per_task:{<taskKey>:{status,model,review_rounds,files_touched_per_round,review_history,findings_history,oscillation_escalated_at_round,opus_escalated,commit_sha,simplify_reverted,simplify_review_findings,destructive_review_failed,destructive_review_findings,concerns,blocked_info}}, result}. per_task.<task> 必须保留 stateJson 中 per_task 的**全部字段**（含 v3 新增字段 findings_history / oscillation_escalated_at_round / opus_escalated），不得以清单未列为由 strip 任何字段；清单仅作可读说明。findings_history 是 findings 状态机轨迹 [{title, status, first_seen, last_seen, rounds, fixed_at_round}]；oscillation_escalated_at_round 是 opus 升级轮 round 数或 null；opus_escalated 是布尔值。
3. If mode=halted: write .workflow/blocked.md from {{blockedInfo}} (the blocked task's blocked_info JSON — render EACH field human-readably: plan, task, reason, category, last_error, suggested_fix, quota_exhausted, likely_source, failed_approach). For failed_approach, render as: "Failed Approach: <failed_approach.task_id>: <failed_approach.reason> — <failed_approach.error>". If blocked_info contains \`regressedFindings\` (v3 findings state machine detected regressions), render separately as a readable list: each item showing title + first_seen + last_seen + fixed_at_round + file + fix, to help locate regression points quickly. Do NOT hunt for these fields in state — they are provided inline in blockedInfo.
   S3（第 4 轮）: blocked.md 路径固定为 .workflow/blocked.md（§8.2），独立于 {{runsDir}}——
   blocked.md 是用户接手入口，路径须稳定可预测（runsDir 会随 runTs 变化，用户难定位）。
4. If mode=halted: run "git status --porcelain" and "git diff --stat". BEST-EFFORT — if git fails (not a repo / index corrupt), skip this section (do NOT block manifest.json write).
   If "git status --porcelain" output is non-empty, append a "## Working Tree (dirty)" section to .workflow/blocked.md with: the porcelain output (file list) + the diff --stat output (change summary) + 接手指引（implementor 改动未提交，留在工作树。选项：git diff <file> 查看 / git checkout -- <file> 丢弃 / 手动修后 git commit -m "feat(plan-X/T-Y): ..." 再全新跑续，见 USAGE.md §7.1）。
   If output is empty, append "## Working Tree (clean)" — no uncommitted changes（likely_source=gate restored 时预期如此）。
   Also include: if the halt was due to a failed review round (not model_unavailable/agent_error/gate/commit), add a "## Cross-Reviewer Findings (grouped by file)" section to blocked.md: group all findings from the halted task's blocked_info by file, and highlight files where ≥2 reviewers reported findings with ⚠ CROSS-REVIEWER markers. This helps spot reviewer disagreements at a glance. Use the blockedInfo.raw field to extract reviewer findings — the raw field contains the diagnostics from spec/quality/hunter reviews.
5. Lessons ({{lessonsAutoDistill}}): If lessonsAutoDistill=true AND mode=halted: lesson-distiller agent has ALREADY been invoked by orchestrator before this finalReport call — it read lessonsPath, extracted reusable root causes, and updated lessons.md itself. You do NOT need to touch lessonsPath. If distiller failed (quota/error), orchestrator logged it and proceeded — lessonsPath may be stale but manifest write must proceed. If lessonsAutoDistill=false or mode=done: lessonsPath untouched.
6. Commit lessons.md (W1-1, 2026-07-07): If mode=halted AND {{lessonsPath}} is non-empty (H-F3 2026-07-07: 空 lessonsPath 则 skip this step entirely — no lessonsPath configured → nothing to commit; 空 lessonsPath 会让 git status --porcelain 查全工作树 → 误 commit 全工作树), after step 5, check if {{lessonsPath}} has uncommitted changes: run "git status --porcelain {{lessonsPath}}". If output is non-empty → git commit -m "chore(workflow): auto-commit lessons.md from run {{runTs}}" {{lessonsPath}} (H-F2: 用 git commit <path> 一步到位不预 staged). This ensures the knowledge base is persisted (bootstrap reads it to inject implementor). BEST-EFFORT — if git commit fails, do NOT block manifest write; record the error in summary.
7. Print a digest summary (counts: done/blocked, total tasks, per-plan gate result).

Return {evidence:{manifest_path}, summary: <digest>}.
RED FLAG: manifest 必须真实写入磁盘（你 ls 确认）。stateJson 是 orchestrator 传入的完整状态，照实记录。`,

  lessonDistiller: `You are the LESSON-DISTILLER (model opus). Extract REUSABLE knowledge from a halted workflow run and update lessons.md. You are invoked by orchestrator (halt path) when mode=halted and lessons_auto_distill=true.

Inputs: distillInput={{distillInput}} lessonsPath={{lessonsPath}}

## Your task
1. Read distillInput: halt_info (reason, last_error, blocked task) + review_history (per-round findings) + failed_approaches (cross-run repeated failures).
2. Read lessonsPath file (current lessons.md). If file missing/empty, treat as no existing lessons. Parse entries: ## L-<id> followed by title/detail/source?/category?/status fields.
3. Identify REUSABLE knowledge — root causes that, if known beforehand, would have prevented the halt or guided the implementor differently. Categories:
   - silent-failure: swallowed error / bad fallback / missing transaction (e.g. DB split-commit must be single-transaction)
   - dependency: task ordering / cross-layer contract (e.g. frontend field name must match backend schema)
   - convention: commit message / naming / format violations causing bootstrap misrecognition
   - test-strategy: testing scope / framework / coverage gaps
   - other: anything reusable that doesn't fit above
4. FILTER OUT transient events (action=skip): review_empty, model_unavailable, single-occurrence hiccups. These are NOT reusable knowledge — they are瞬态 model/runtime hiccups. ONLY提炼 root causes.
   Exception: if failed_approaches shows the SAME task halted with the SAME root cause across multiple runs (cross-run repeat),提炼 it even if the reason label looks transient — the repetition signals a systemic trap.
5. DEDUP against existing lessons: if a new finding semantically overlaps an existing entry → action=update (set update_target_id=existing id, refine title/detail with new evidence); if全新 → action=append; if nothing reusable → action=skip.

## Apply decisions to lessonsPath (you write the file)
After deciding, APPLY decisions to lessonsPath yourself (you have fs access):
- append: add new entry at end of file. Format:
  ## L-<ts>
  title: <title>
  detail: <detail>
  source: <plan-X/T-Y@<run_ts>>
  category: <category>
  status: active
- update: replace the existing entry段落 (## L-<update_target_id> to next ## L- or EOF) with new content. Preserve update_target_id as id (or use new id if replacing).
- skip: no change.
Ensure file starts with '# Lessons Learned' header. Entries separated by blank lines.

## Quality bar (RED FLAG)
lesson 必须是可复用知识，非事件标签。
- ❌ title: "OSCILLATING" (event label — not reusable)
- ❌ title: "review_empty" (transient hiccup — not reusable)
- ❌ title: "halt" (too vague)
- ✅ title: "同文件 ≥3 round 振荡时，检查 reviewer 是否对同一 spec 条款反向报" (reusable root cause)
- ✅ title: "DB 写 split-commit（DrawResult + outbox）必须单事务，二次 commit 失败导致 outbox 永不补" (reusable root cause)
- ✅ title: "前端字段名必须与后端 pydantic schema 一致（如 dlt_append vs append）" (reusable cross-layer contract)

title 应是可独立理解的结论；detail 含根因+场景+修法；source 是 task@run_ts 便于追溯。

## Schema
Return {decisions: [{action, id, title, detail, source?, category?, update_target_id?}], summary}.
- action=append: id 必填（新 L-<ts>），title/detail 必填，source/category 建议填。
- action=update: update_target_id 必填（existing id），id 可同 update_target_id（原地更新）或新 id（替换）。title/detail 必填。
- action=skip: 仅 id+title+detail 占位即可（不会被写入）。
若整个 run 无可复用知识 → decisions: [{action:'skip', id:'none', title:'no reusable knowledge', detail:'transient event only'}].
若遇到 model 限额耗尽（quota/rate-limit/429 错误），返回 decisions: [{action:'skip', id:'quota', title:'distiller quota exhausted', detail:'skip lesson update'}]（orchestrator 会 best-effort 跳过）。`,
}

// ===== state（§4.4）=====
const state = {
  runTs: null, config: null, completed: [], plans: [], currentPlan: null, currentTask: null,  // P1-7b（第 7 轮）: plans 字面量列出（与 spec §4.4 一致，运行时 bootstrap 阶段补入）
  perTask: {},  // P2-11a（第 11 轮）: {taskKey: {planId, status, model, review_rounds, files_touched_per_round, commit_sha, blocked_info}}（taskKey = plan-XX/TY，plan-scoped）
  failedApproaches: {},  // {taskKey: [{task_id, reason, error}]}（taskKey plan-scoped）
  taskWriteFiles: {},  // {taskKey: [files]} — write_files 边界控制（taskKey plan-scoped）
  taskLessons: {},  // {taskKey: [{id, title, detail}]} — LESSONS.md 跨任务失败知识库（taskKey plan-scoped）
  allLessons: [],  // v3: bootstrap 解析的全量 lessons（含 category），供两层注入
}

// ===== halt（§13a：累积 blocked_info → finalReport halted 模式写盘 + surface）=====
// Q7（第 4 轮）: finalReport / lessonDistiller fallback 链控制流同构，抽象为统一 helper。
// opus→sonnet→haiku 逐一尝试，全链失败用环境默认 model，再失败返回 null（调用方检查）。
// fallback 链仅用于 halt/finalReport 保存进度——runTask 主 agent 调用不用 fallback（不降级继续开发）。
async function agentWithFallback(role, ctx, labelPrefix) {
  for (const m of ['opus', 'sonnet', 'haiku']) {
    try {
      return await agent(buildPrompt(role, ctx),
        { schema: SCHEMAS[role], model: m, label: `${labelPrefix}:${m}` })
    } catch (e) { log(`${labelPrefix} ${m} 不可用: ${errStr(e)}, 试下一个`) }
  }
  // QC2: 环境默认 model 兜底也包 try/catch——全链失败时返回 null，不 crash
  // halt 场景下 crash 会丢 manifest/blocked.md，比 finalReport 失败更严重
  log('fallback 链全失败，用环境默认 model 保存')
  try {
    return await agent(buildPrompt(role, ctx),
      { schema: SCHEMAS[role], label: `${labelPrefix}:default` })
  } catch (e) {
    log(`✗ 环境默认 model 也失败，${labelPrefix} 无法保存: ${errStr(e)}`)
    return null
  }
}

// Q4（第 4 轮）: perTask 默认字段初始化 helper。halt() 在 tid='unknown'（bootstrap/gate halt）
// 时 spread 空对象 → perTask 只有 {status, blocked_info}，manifest JSON 序列化缺字段 → schema 不稳定。
// 须确保所有持久化字段有默认值（与 runTask 初始化一致）。
function ensurePerTaskDefaults(entry) {
  return {
    planId: null, status: 'in_progress', model: 'sonnet', review_rounds: 0,
    files_touched_per_round: [], review_history: [],
    findings_history: [],              // v3: 状态机
    oscillation_escalated_at_round: null,  // v3 F: 升级轮标记
    commit_sha: null, opus_escalated: false,
    simplify_reverted: false, simplify_review_findings: [],
    destructive_review_failed: false, destructive_review_findings: [],
    concerns: [], blocked_info: null,
    ...(entry || {}),
  }
}

async function halt(plan, task, r) {
  // Q2（第 4 轮）: tid 须 plan-scoped（与 runTask 的 taskKey 一致），防跨 plan 同名 task 覆盖。
  // plan 可用 → `plan-{seq}/{task.id}`；plan=null（bootstrap halt）→ 裸 task.id 或 'unknown'。
  const tid = (plan && task?.id) ? `plan-${String(plan.seq).padStart(2, '0')}/${task.id}` : (task?.id || 'unknown')
  state.perTask[tid] = ensurePerTaskDefaults({ ...(state.perTask[tid] || {}), status: 'blocked',
    blocked_info: {
      plan: plan?.id, task: tid, reason: r.reason,
      category: r.diag?.blocked_category || r.diag?.file || r.diag?.reason || null,
      last_error: r.diag?.last_error || r.diag?.summary || r.reason,
      suggested_fix: r.diag?.suggested_fix || null,
      quota_exhausted: r.reason === 'model_unavailable',
      likely_source: haltLikelySource(r.reason),
      failed_approach: { task_id: tid, reason: r.reason, error: r.diag?.last_error || r.reason },
      raw: r.diag || {},
    }
  })
  phase('Finalize')
  const blockedInfo = JSON.stringify(state.perTask[tid].blocked_info)
  // SH2 修复（§5.4）：distiller 是独立 agent 调用（非 finalReport 内部调）。
  // orchestrator 无 fs 不能读 lessons.md，故 distiller 自己读 lessonsPath + 自己写回。
  // distiller 失败/限额 → best-effort 跳过，不阻塞 finalReport manifest 写入。
  const lessonsAutoDistill = resolveLessonsAutoDistill(state.config)
  const lessonsPath = state.config?.lessons_path || ''
  if (lessonsAutoDistill && lessonsPath) {
    const haltInfo = state.perTask[tid].blocked_info
    const reviewHistory = state.perTask[tid]?.review_history || []
    const failedApproaches = state.failedApproaches[tid] || []
    const distillInput = JSON.stringify(distillLessonInput('halted', haltInfo, reviewHistory, failedApproaches))
    try {
      // S5（第 5 轮）: spec §2.4 fallback 链 [opus,sonnet,haiku] 仅用于 finalReport 保存进度；
      //   distiller 是 lesson 提炼通道（非进度保存），改用单次 agent() 调用（model: 'opus'），
      //   失败即 catch 跳过（符合 §5.4 best-effort 语义，非逐一尝试 3 个 model）。
      const distillResult = await agent(buildPrompt('lessonDistiller', { distillInput, lessonsPath }), { schema: SCHEMAS.lessonDistiller, model: 'opus', label: 'lesson-distiller' })
      if (distillResult?.decisions) {
        const applied = distillResult.decisions.filter(d => d.action !== 'skip').length
        log(`📋 lesson distiller: ${applied} 条 lesson 已更新（append/update）`)
      } else {
        log('⚠ lesson distiller 返回空，跳过 lesson 更新')
      }
    } catch (e) {
      log(`⚠ lesson distiller 失败（best-effort 跳过）: ${errStr(e)}`)
    }
  }
  const fr = await agentWithFallback('finalReport', { mode: 'halted', stateJson: JSON.stringify(state), blockedInfo, runsDir: `runs/${state.runTs}`, runTs: state.runTs, lessonsPath, lessonsAutoDistill: String(lessonsAutoDistill) }, 'final-report')
  if (!fr) log('✗✗ 致命：finalReport 全链失败，manifest 未写入！请手动检查 runs/ 目录')
  log(`✗ HALT: ${r.reason} (plan ${plan?.id}, task ${tid})`)
  // Q13（第 5 轮）: halt() 返回 {result:'halted', reason} 供调用方 return（DRY：7 处 `await halt(...); return {result, reason}` 模式重复，reason 易写错）
  return { result: 'halted', reason: r.reason }
}

// ===== runTask（§13a：implementor + 升级链 + review rounds + simplify + commit）=====
// 状态机（halt reason 枚举见各分支）：
//   IMPL(初始) ──blocked──→ 升级 opus ──blocked──→ halt 'opus BLOCKED'
//              └─needs_context──→ CTX(contextFetcher) ──→ IMPL(ctx) ──blocked──→ 升级 opus ─→ halt 'opus BLOCKED after context-fetch'
//                                                       └─failed──→ retry ─→ halt 'implementor X after context-fetch retry'
//              └─failed──→ retry once ─→ halt 'implementor X after retry'
//              └─ok/done_with_concerns──→ REVIEW rounds(spec‖qual‖hunt 并行)
//   REVIEW ──全绿──→ COMMIT ──非 ok──→ halt 'commit failed'；out_of_scope──→ halt 'commit out_of_scope'
//         └─任一❌──→ IMPL(fix-round, 最后 1 轮强制 opus) ──blocked/failed/needs_context──→ halt 'implementor X in fix-round N'
//         └─任一 review 空响应/异常──→ halt 'agent_error'/'model_unavailable'/'review_empty'（reviewHaltReason）
//         └─任一 review failed 但 0 findings──→ halt 'review_failed_no_findings'（reviewHaltForEmptyFailed）
//         └─max N 轮（默认 4，可配 0=无限）──→ halt 'review max rounds'；振荡──→ halt 'OSCILLATING'
//   COMMIT ──→ SIMPLIFY ──git status --porcelain 有改动──→ re-review ──全绿──→ git commit --amend（validateAmendResult 校验 SHA）
//              │                                └─失败──→ git reset --hard HEAD + git clean -fd（validateCheckoutResult 兜底验证 porcelain 空）
//              │                                              └─失败──→ halt 'simplify checkout failed'
//              │                                └─空响应/异常──→ halt 'agent_error'/'model_unavailable'/'review_failed_no_findings'/'review_empty'
//              └─diff subagent 失败──→ halt 'simplify diff check failed'
//              └─amend 失败/SHA 格式错──→ halt 'simplify amend failed'
//              └─无改动──→ 跳过 review（省成本）
//   destructive_changes 非空──→ 额外 review round（:destructive）──失败/异常不 halt，记 destructive_review_failed + findings
// 任一 agent 限额/异常──→ halt 'model_unavailable' / 'agent_error'（reviewHaltReason 判定）
async function runTask(plan, task) {
  state.currentTask = task.id
  const cfg = state.config
  const planIdShort = `plan-${String(plan.seq).padStart(2, '0')}`
  // Q2（第 4 轮）: perTask key 须 plan-scoped（与外层 state.completed 一致），防跨 plan 同名 task 覆盖
  //   （Plan 01/02 都有 T1-T10 → 裸 task.id 作 key → Plan 02 的 T2 覆盖 Plan 01 的 T2 perTask）。
  //   halt() 已用 plan-scoped tid；runTask 内所有 perTask 访问也须用 taskKey 保持一致。
  const taskKey = `plan-${String(plan.seq).padStart(2, '0')}/${task.id}`
  // Q5（第 5 轮）: 复用 ensurePerTaskDefaults helper（DRY：字段列表与 halt() 的 ensurePerTaskDefaults 重复，字段增删需两处同步易漂移）
  state.perTask[taskKey] = ensurePerTaskDefaults({ planId: plan.id, status: 'in_progress', model: task.model || 'sonnet' })
  log(`▶ ${task.id} (${task.model || 'sonnet'}): 派发 implementor — TDD 可能含长命令(uv sync/build/全量测试)，正常耗时请等待；/workflows 可看实时工具调用`)

  // —— implementor + BLOCKED 升级链（§2.3）——
  let model = task.model || 'sonnet'
  // S1（第 5 轮）: failedApproaches 查找须用 taskKey（存储键来自 manifest per_task，已 plan-scoped）。
  //   S3（第 5 轮）: taskLessons 查找同须用 taskKey（存储键已 plan-scoped，防跨 plan 同名 task 覆盖）。
  //   旧代码用裸 task.id → 查找永远 undefined → failedApproaches/lessons 占位符不注入 implementor prompt。
  // P1-11（第 6 轮）: implCtx 传 buildCommand（implementor GREEN 前跑 build 验证可构建性）。
  // v3: lesson_categories 来自 plan frontmatter（可选）；未声明则 domain lessons 回退到 title 关键词匹配。
  // S3 修复（2026-07-06）：移除旧 formatLessons 调用——它与 formatDomainLessons 的 title keyword fallback
  //   匹配逻辑同源（task title tokens vs lessons.md），保留会导致非 silent-failure 且 keyword 重叠的 lesson 被注入两次。
  //   Tier 2 的 title keyword fallback 已覆盖 legacy 无 category 场景（helpers.test 有测试）。
  const taskCategories = task.lesson_categories || []
  const lessonsText = formatUniversalLessons(state.allLessons || []) + formatDomainLessons(state.allLessons || [], taskCategories, planIdShort, task.title || '')
  const implCtx = (fix, note, ctx = '') => ({ planId: plan.id, taskId: task.id, planFilePath: plan.file, specPath: cfg.spec_path, testCommand: cfg.test_command, buildCommand: cfg.build_command || '', fixIssues: fix, retryNote: note, fetchedContext: ctx, referencePaths: formatReferencePaths(cfg.reference_paths), failedApproaches: formatFailedApproaches(state.failedApproaches?.[taskKey] || []), lessons: lessonsText })
  let impl
  impl = await dispatchImpl(buildPrompt('implementor', implCtx('', '')), { schema: SCHEMAS.implementor, model, label: `impl:${task.id}` }, model, 'opus')
  if (impl.halted) return impl
  // —— blocked 升级链：sonnet→opus→halt（§2.3）——
  if (impl.status === 'blocked') {
    if (model === 'opus') return { halted: true, reason: 'opus BLOCKED', diag: impl.diagnostics }
    model = 'opus'
    impl = await dispatchImpl(buildPrompt('implementor', implCtx('', '上一轮 sonnet BLOCKED，升级 opus 重试。')), { schema: SCHEMAS.implementor, model: 'opus', label: `impl:${task.id}:opus` }, 'opus')
    if (impl.halted) return impl
    if (impl.status === 'blocked') return { halted: true, reason: 'opus BLOCKED', diag: impl.diagnostics }
  }
  // —— needs_context: dispatch contextFetcher, retry implementor with context (§8.1) ——
  if (impl.status === 'needs_context') {
    let ctxr
    ctxr = await dispatchImpl(buildPrompt('contextFetcher', {
      needType: impl.diagnostics?.blocked_category || 'file',
      query: impl.diagnostics?.last_error || impl.diagnostics?.suggested_fix || '',
      specPath: cfg.spec_path, workdir: '.',
    }), { schema: SCHEMAS.contextFetcher, label: `ctx:${task.id}` }, 'sonnet')
    if (ctxr.halted) return ctxr
    const fetchedCtx = ctxr.diagnostics?.context || ''
    impl = await dispatchImpl(buildPrompt('implementor', implCtx('', `补充上下文后重试。`, fetchedCtx)), { schema: SCHEMAS.implementor, model, label: `impl:${task.id}:ctx` }, model, 'opus')
    if (impl.halted) return impl
    // Bug 8: needs_context → blocked 时先升 opus 再 halt（mirror 初始 blocked 升级链）
    if (impl.status === 'blocked') {
      if (model === 'opus') return { halted: true, reason: 'opus BLOCKED after context-fetch', diag: impl.diagnostics }
      model = 'opus'
      impl = await dispatchImpl(buildPrompt('implementor', implCtx('', '上下文补充后 sonnet 仍 BLOCKED，升级 opus 重试。', fetchedCtx)), { schema: SCHEMAS.implementor, model: 'opus', label: `impl:${task.id}:ctx:opus` }, 'opus')
      if (impl.halted) return impl
      if (impl.status === 'blocked') return { halted: true, reason: 'opus BLOCKED after context-fetch', diag: impl.diagnostics }
    }
    // Bug 9: needs_context → failed 时允许一次重试（mirror 初始 failed 路径），非直接 halt
    if (impl.status === 'failed') {
      impl = await dispatchImpl(buildPrompt('implementor', implCtx('', '上下文补充后仍 failed，重试一次。', fetchedCtx)), { schema: SCHEMAS.implementor, model, label: `impl:${task.id}:ctx:retry` }, model, 'opus')
      if (impl.halted) return impl
      if (impl.status !== 'ok' && impl.status !== 'done_with_concerns') return { halted: true, reason: `implementor ${impl.status} after context-fetch retry`, diag: impl.diagnostics }
    }
    if (impl.status !== 'ok' && impl.status !== 'done_with_concerns') return { halted: true, reason: `implementor ${impl.status} after context-fetch`, diag: impl.diagnostics }
  }
  // —— failed: retry once → halt (§4.4) ——
  if (impl.status === 'failed') {
    impl = await dispatchImpl(buildPrompt('implementor', implCtx('', '上次 failed，重试一次。')), { schema: SCHEMAS.implementor, model, label: `impl:${task.id}:retry` }, model, 'opus')
    if (impl.halted) return impl
    if (impl.status !== 'ok' && impl.status !== 'done_with_concerns') return { halted: true, reason: `implementor ${impl.status} after retry`, diag: impl.diagnostics }
  }
  // done_with_concerns: 记录疑虑，继续进 review（不 halt）；轻量透传给 specReview 作 focusHint。
  let concerns = []
  if (impl.status === 'done_with_concerns') {
    concerns = impl.diagnostics?.concerns || []
    state.perTask[taskKey].concerns = concerns
    log(`⚠ ${task.id} done_with_concerns: ${concerns.join('; ') || '(no detail)'}`)
  }
  let concernsHint = formatConcernsHint(concerns)
  let filesChanged = impl.evidence.files_changed || []

  // —— review rounds（max 可配，默认 4；0=无限靠 detectOscillation 防线，§5）——
  const maxRounds = resolveMaxRounds(cfg)
  for (let round = 1; maxRounds === 0 ? true : round <= maxRounds; round++) {
    state.perTask[taskKey].review_rounds = round
    const fc = filesChanged.join('\n')
    const { spec, qual, hunt, haltReason: reviewReason, emptyFailed: emptyFailedReason } = await runReviewRound(task.id, cfg, plan, fc, concernsHint, `:r${round}`, `Plan ${plan.id}`)
    // Q15（第 5 轮）: push 须在 halt 检查之前——halt 轮的 files_touched/review_history 也须持久化，
    //   否则 distiller 看不到 halt 轮 review 状态（如 review_failed_no_findings 的 failed-but-empty 信号）。
    state.perTask[taskKey].files_touched_per_round.push(unionFiles(spec, qual, hunt))
    state.perTask[taskKey].review_history.push(summarizeReviewRound(round, spec, qual, hunt))
    // v3: findings 状态机更新（在 halt 检查之前，halt 轮也须持久化）
    const currentFindings = collectReviewFindings(spec, qual, hunt)
    state.perTask[taskKey].findings_history = updateFindingsHistory(
      state.perTask[taskKey].findings_history, currentFindings, round
    )
    if (reviewReason) {
      return { halted: true, reason: reviewReason, diag: { spec: spec?.diagnostics, qual: qual?.diagnostics, hunt: hunt?.diagnostics } }
    }
    // 第二道守卫：failed 但 0 findings（防「合法 failed + 空 diagnostics」跑空修复循环 → max rounds 误 halt）
    if (emptyFailedReason) {
      return { halted: true, reason: emptyFailedReason, diag: { spec: spec?.diagnostics, qual: qual?.diagnostics, hunt: hunt?.diagnostics } }
    }
    // allGreen 必须在 detectOscillation 之前：否则 r3 三 reviewer 全 ok 时，先被
    // OSCILLATING（核心文件被审 ≥3 轮）截胡 halt，allGreen break 永远轮不到 → 收敛误报
    // （T2 invite / T5 channels）。真矛盾（reviewer 持续分歧，如 T7 claims 时区）不会全绿，
    // 自然落进 detectOscillation 正确 halt 让人介入。单轮全绿即 review 共识，足以放行。
    if (allGreen(spec, qual, hunt)) break
    const osc = detectOscillation(state.perTask[taskKey].files_touched_per_round)
    const flipFlop = isFlipFlop(state.perTask[taskKey].review_history || [])
    const regressed = hasRegressed(state.perTask[taskKey].findings_history || [])

    // v3 (§5.5): 任一 finding 回归（fixed→regressed）→ 立即 halt（独立于文件振荡）
    if (regressed) {
      log(`⚠ ${task.id}: r${round} OSCILLATING halt — regressed finding(s) reappeared after being fixed (v3)`)
      return {
        halted: true,
        reason: 'OSCILLATING',
        diag: {
          ...osc,
          flipFlop,
          regressed,
          regressedFindings: state.perTask[taskKey].findings_history.filter(h => h.status === 'regressed'),
          model,
        },
      }
    }

    if (osc.oscillating) {
      // v3 (§5.5): flipFlop → 真振荡（reviewer 反向分歧）→ halt
      if (flipFlop) {
        log(`⚠ ${task.id}: r${round} OSCILLATING halt — reviewer flip-flop detected (same finding title reappears across rounds) (v3)`)
        return {
          halted: true,
          reason: 'OSCILLATING',
          diag: {
            ...osc,
            flipFlop,
            regressed,
            model,
          },
        }
      }
      // flipFlop=false 且无 regressed（每轮新 findings = 在推进）
      if (shouldEscalateOnOscillation(model, state.perTask[taskKey].opus_escalated)) {
        state.perTask[taskKey].opus_escalated = true
        state.perTask[taskKey].oscillation_escalated_at_round = round  // v3 F: 升级轮次
        model = 'opus'
        log(`⚠ ${task.id}: r${round} OSCILLATING (new-findings 补充, flipFlop=false) — escalate to opus, continue (v3)`)
      } else {
        // 已升 opus，继续跑（new findings = 在推进），由 budget guard 兜底
        log(`⚠ ${task.id}: r${round} OSCILLATING (flipFlop=false, opus already escalated) — continue until budget guard (v3)`)
      }
    }
    // v3: 无限模式（maxRounds=0）budget guard——flipFlop=false 持续推进的兜底
    //   防 reviewer 同义变体（改 title）让 [REGRESSED] 漏报后无限跑
    if (maxRounds === 0) {
      const budget = resolveReviewBudget(cfg)
      if (round >= budget) {
        // D4 决策：halt reason 改可操作——blocked.md 建议拆 task
        return { halted: true, reason: 'review_not_converging', diag: { round, budget, findings_history: state.perTask[taskKey].findings_history, spec: spec.diagnostics, qual: qual.diagnostics, hunt: hunt.diagnostics } }
      }
    } else if (round === maxRounds) {
      // 有限模式仍用 maxRounds 硬上限；diagnostics 也带 findings_history，便于接手判断是重复问题还是新问题
      return { halted: true, reason: 'review max rounds', diag: { round, findings_history: state.perTask[taskKey].findings_history, spec: spec.diagnostics, qual: qual.diagnostics, hunt: hunt.diagnostics } }
    }
    const findings = collectReviewFindings(spec, qual, hunt)
    const crossReviewerNote = formatCrossReviewerNote(findings)
    // D1: history 主导单源——formatFindingsHistory 已含本轮 [OPEN]（标★本轮新增），
    // 不再单独注入 formatFindings(本轮) 避免重复。cross-reviewer note 按 file 聚合保留。
    const findingsHistoryText = formatFindingsHistory(state.perTask[taskKey].findings_history || [], round)
    const fullFixIssues = findingsHistoryText ? `${findingsHistoryText}\n${crossReviewerNote}` : crossReviewerNote
    // v3 F: opus 升级轮强化 retryNote（DX: 移除中英混杂 + 双重否定，正向陈述；文本修正 r{round} 而非 r{round-1}）
    const oscEscRound = state.perTask[taskKey].oscillation_escalated_at_round
    const retryNote = oscEscRound === round
      ? `## 升级到 opus，本轮必须修完所有 [OPEN]\n- 逐条核对 [OPEN]，每条要么修完，要么说明不修的原因（★ 标本轮新增的优先修）\n- 修完后，核对 [FIXED] 列表的 fix 在你的改动后仍然存在；若 [OPEN] 与 [FIXED] 同文件，只动 [OPEN] 描述的代码，不要回退 [FIXED] 对应的修改\n- 不要留到下一轮，下一轮不再有升级空间\n- 截至 r${round} review 累计未修 findings 如上`
      : `修复 review round ${round} 问题（${findings.length} 项发现；★ 标本轮新增）。`
    // 最后 1 轮 fix 强制 opus（有限模式 round===maxRounds-1 / 无限模式 round>=4）：
    // 难度递增，最后机会用最强 model 降低 halt 概率。maxRounds 未传向后兼容默认 3（round=2 升级）。
    const fixModel = fixModelForRound(round, model, maxRounds)
    impl = await dispatchImpl(buildPrompt('implementor', implCtx(fullFixIssues, retryNote)), { schema: SCHEMAS.implementor, model: fixModel, label: `impl:${task.id}:fix${round}` }, fixModel, 'opus')
    if (impl.halted) return impl
    // Bug 4: fix-round implementor 返回 blocked/failed/needs_context 时不能静默忽略——
    // 否则 orchestrator 在 stale code 上继续下一轮 review，必然重复发现同样问题 → 浪费轮次。
    // 初始 dispatch 已有 opus 升级链 + context-fetch 路径；fix-round 内 halt 暴露问题而非静默循环。
    if (impl.status === 'blocked' || impl.status === 'failed' || impl.status === 'needs_context') {
      return { halted: true, reason: `implementor ${impl.status} in fix-round ${round}`, diag: impl.diagnostics }
    }
    // Q10（第 4 轮）: fix-round implementor 也可能返回 done_with_concerns（修了 review 问题但新增疑虑）。
    //   旧代码只处理初始 dispatch 的 done_with_concerns → fix-round 的 concerns 被丢弃，
    //   concernsHint 全程不变 → 后续 review round 收不到新疑虑作 focusHint。
    //   须在此分支更新 concerns + concernsHint + perTask，与初始 dispatch 路径一致。
    if (impl.status === 'done_with_concerns') {
      concerns = impl.diagnostics?.concerns || concerns
      state.perTask[taskKey].concerns = concerns
      concernsHint = formatConcernsHint(concerns)
      log(`⚠ ${task.id} fix-round ${round} done_with_concerns: ${concerns.join('; ') || '(no detail)'}`)
    }
    filesChanged = impl.evidence.files_changed || filesChanged
  }

  // —— 方案 C（§5.2）：commit 提前 + git diff 触发 review + amend/checkout 回退 ——
  // 旧流程：simplify → commit（simplify 失败则委托 commit agent 回退文件）。
  // 新流程：commit → simplify → git diff --stat 独立验证 → 有改动则 review →
  //   全绿 git commit --amend（合并 simplify 改动到 HEAD）/ 失败 git reset --hard HEAD 回退 simplify 改动。
  // 不信任 simplify 自报 changed（旧 changed 条件分支已删）——commit 后工作树
  // 本应 clean，simplify 若动代码 → git diff 非空 → 触发 review。省成本：simplify 没动代码则跳过 review。

  // —— commit（提前到 simplify 前；§5 状态原子转换）——
  let commit
  // P1-5（第 6 轮）: commit/simplify/contextFetcher 硬编码 sonnet（spec §13b least-powerful-model；
  //   task model 可能因 BLOCKED 升级为 opus，commit/simplify 不应跟随升级，保持 sonnet 控成本）。
  commit = await dispatchImpl(buildPrompt('commit', { taskId: task.id, planId: plan.id, planIdShort, commitMsg: commitSubject(plan.seq, task.id, task.title || task.id), testCommand: cfg.test_command, writeFilesScope: formatWriteFilesScope(state.taskWriteFiles?.[taskKey] || []) }), { schema: SCHEMAS.commit, label: `commit:${task.id}` }, 'sonnet')
  if (commit.halted) return commit
  if (commit.status === 'failed' && Array.isArray(commit.diagnostics?.out_of_scope) && commit.diagnostics.out_of_scope.length) return { halted: true, reason: 'commit out_of_scope', diag: commit.diagnostics }
  if (commit.status !== 'ok') return { halted: true, reason: 'commit failed', diag: commit.diagnostics }
  state.perTask[taskKey].status = 'committed'
  state.perTask[taskKey].commit_sha = commit.evidence.commit_sha
  log(`✓ ${task.id} committed @ ${commit.evidence.commit_sha}`)

  // —— simplify（max 1，§5.2 方案 C：git diff 独立验证是否动代码）——
  let simp
  simp = await dispatchImpl(buildPrompt('simplify', { taskId: task.id, filesChanged: filesChanged.join('\n') }), { schema: SCHEMAS.simplify, label: `simp:${task.id}` }, 'sonnet')
  if (simp.halted) return simp
  // commit 后工作树 clean → git status --porcelain 非空即 simplify 动了代码（不信任 simp.evidence.changed 自报）。
  // Q5: 用 git status --porcelain 替代 git diff --stat——同时检测 staged + unstaged，
  //   防 simplify 误 `git add` 后 staged 改动被 git diff --stat 漏检。
  // Q6: 三个 subagent（diff/amend/checkout）均用 safeAgent 包装，异常归类为 sentinel 而非裸冒泡。
  // Q4: diff 返回 null/异常 → halt（不静默跳过让 simplify 改动留工作树未 review 未回退）。
  const diffSchema = { type: 'object', required: ['changed', 'files'], properties: { changed: { type: 'boolean' }, files: { type: 'array', items: { type: 'string' } } } }
  const diffResult = await safeAgent('Run `git status --porcelain` in the current working directory. If output is empty, return {"changed": false, "files": []}. Otherwise return {"changed": true, "files": [<list of file paths from porcelain output>]}.', { schema: diffSchema, label: `diff:${task.id}` })
  // Q4: diff subagent 返回 null/异常/格式错 → halt（不静默跳过）
  // Q8（第 4 轮）: changed=true 时 files 须为 array——agent 偶发返回 {changed:true} 漏 files 字段，
  //   旧代码用 Array.isArray(diffResult.files) ? diffResult.files : [] 静默降级为空 →
  //   simpFiles=[] → review 收空 fc → 漏审 simplify 改动。须 halt 暴露问题而非掩盖。
  if (!diffResult || typeof diffResult !== 'object' || typeof diffResult.changed !== 'boolean' || (diffResult.changed === true && !Array.isArray(diffResult.files))) {
    return { halted: true, reason: 'simplify diff check failed', diag: { task: task.id, diffResult: diffResult || null } }
  }
  const simpChanged = diffResult.changed === true
  const simpFiles = Array.isArray(diffResult.files) ? diffResult.files : []
  if (simpChanged) {
    const fc = simpFiles.join('\n')
    const { spec: spec2, qual: qual2, hunt: hunt2, haltReason: simpReviewReason, emptyFailed: simpEmptyFailed } = await runReviewRound(task.id, cfg, plan, fc, '', ':simp', '')
    if (simpReviewReason) {
      // model_unavailable/agent_error/review_empty：不 amend 也不 checkout，直接 halt。
      // simplify 改动留在工作树，blocked.md 记录脏状态 + likely_source=implementor changes。
      return { halted: true, reason: simpReviewReason, diag: { spec2: spec2?.diagnostics, qual2: qual2?.diagnostics, hunt2: hunt2?.diagnostics } }
    }
    // 第二道守卫（同主 review 轮）：failed 但 0 findings → halt，防空诊断静默回退
    if (simpEmptyFailed) {
      return { halted: true, reason: simpEmptyFailed, diag: { spec2: spec2?.diagnostics, qual2: qual2?.diagnostics, hunt2: hunt2?.diagnostics } }
    }
    if (allGreen(spec2, qual2, hunt2)) {
      // review 全绿 → amend commit（合并 simplify 改动到 HEAD，保持原子性）
      // Q1/Q8: amend 后用 git rev-parse HEAD 独立获取 SHA + validateAmendResult 纯函数校验
      // Q2: amend 失败（pre-commit hook 阻断等）→ staged 区域可能残留 → halt（不静默继续用旧 SHA）
      const amendSchema = { type: 'object', required: ['ok'], properties: { ok: { type: 'boolean' }, sha: { type: 'string' }, error: { type: 'string' } } }
      const amendResult = await safeAgent('Run `git add -A && git commit --amend --no-edit`. Then run `git rev-parse HEAD` and return JSON {"ok": true, "sha": "<40-char-hex>"}. If amend failed (e.g. pre-commit hook blocked), return {"ok": false, "sha": "", "error": "<message>"}.', { schema: amendSchema, label: `amend:${task.id}` })
      const amendCheck = validateAmendResult(amendResult)
      if (!amendCheck.valid) {
        // Q1/Q2/Q8: amend 失败或 SHA 格式错 → halt（防 gate 在旧 SHA 跑漏检 simplify 改动）
        return { halted: true, reason: 'simplify amend failed', diag: { task: task.id, amendError: amendCheck.error, commitSha: commit.evidence.commit_sha } }
      }
      state.perTask[taskKey].commit_sha = amendCheck.sha
      log(`✓ ${task.id} simplify review green — amended commit @ ${amendCheck.sha}`)
    } else {
      // review 失败 → git reset --hard HEAD + git clean -fd 回退 simplify 改动（HEAD 不变，保留原 commit）
      // Q11（第 4 轮）: 须用 git reset --hard HEAD（非旧 git-checkout 回退）——同时清理 staged changes
      //   （simplify 误 git add 时 staged 区域残留，旧 git-checkout 只回退 tracked 工作区修改不清理 staged）
      // Q3: checkout 须同时 git clean -fd（处理 simplify 新建的 untracked 文件）
      // Q4/Q8: checkout 后再跑 git status --porcelain 兜底验证工作树真 clean + validateCheckoutResult 纯函数校验
      // Q3: checkout 失败或兜底验证非空 → halt（不无条件设 simplify_reverted=true 谎报已回退）
      // Q3: simplify review findings 须持久化到 simplify_review_findings（不丢，用户无需考古 transcript）
      const checkoutSchema = { type: 'object', required: ['ok'], properties: { ok: { type: 'boolean' }, porcelain: { type: 'string' }, error: { type: 'string' } } }
      const checkoutResult = await safeAgent('Run `git reset --hard HEAD && git clean -fd` to discard simplify changes (both tracked modifications, staged changes, and untracked new files). Then run `git status --porcelain` to verify the working tree is clean. Return JSON {"ok": true, "porcelain": "<porcelain output>"} on success or {"ok": false, "porcelain": "<output>", "error": "<message>"} on failure.', { schema: checkoutSchema, label: `checkout:${task.id}` })
      const checkoutCheck = validateCheckoutResult(checkoutResult)
      if (!checkoutCheck.valid) {
        // Q3/Q4/Q8: checkout 失败或兜底验证工作树仍脏 → halt（simplify 改动残留，不谎报 simplify_reverted）
        return { halted: true, reason: 'simplify checkout failed', diag: { task: task.id, checkoutError: checkoutCheck.error, commitSha: commit.evidence.commit_sha } }
      }
      log(`⚠ ${task.id} simplify review NOT green — reverted simplify changes (HEAD unchanged @ ${commit.evidence.commit_sha})`)
      state.perTask[taskKey].simplify_reverted = true
      state.perTask[taskKey].simplify_review_findings = collectReviewFindings(spec2, qual2, hunt2)
    }
  }

  // —— Destructive Change Detection 触发额外 review round（T4 sync）——
  // commit agent 在 step 3.6 已把 deleted_code/file_deletion/signature_change 写入
  // diagnostics.destructive_changes。非空 → 触发 spec+quality+hunter 并行额外 review。
  // 不计入 review_rounds 限额；失败不 halt，记录 destructive_review_failed + findings 继续。
  const destructive = commit.diagnostics?.destructive_changes
  if (Array.isArray(destructive) && destructive.length) {
    log(`⚠ ${task.id} destructive_changes detected (${destructive.length}): ${destructive.map(d => `${d.type}:${d.file}`).join(', ')} — 触发额外 review round`)
    // Q3（第 4 轮）: committed_files 须用换行分隔（与 filesChanged / simpFiles 一致），
    //   逗号对含逗号文件路径（如 data,v2.json）歧义——reviewer 用 git diff 作 ground truth，
    //   fc 仅是聚焦提示，换行分隔更安全
    const fc = (commit.evidence.committed_files || []).join('\n')
    const { spec: dSpec, qual: dQual, hunt: dHunt, haltReason: dReason, emptyFailed: dEmptyFailed } = await runReviewRound(task.id, cfg, plan, fc, '', ':destructive', `Plan ${plan.id}`)
    if (dReason || dEmptyFailed) {
      // model 不可用/空响应：不 halt，记录失败继续（destructive review 是增强保护，非阻断）
      state.perTask[taskKey].destructive_review_failed = true
      // Q14（第 5 轮）: 异常路径 shape 须与正常路径一致（[{source, severity, title, fix}]），
      //   旧 [{source, title}] 简化 shape 混在同字段 → manifest 消费者需处理两种结构
      state.perTask[taskKey].destructive_review_findings = [{ source: 'destructive-review', severity: 'critical', title: dReason || dEmptyFailed, fix: 'investigate review agent failure' }]
      log(`⚠ ${task.id} destructive review 异常 (${dReason || dEmptyFailed}) — 记录并继续`)
    } else if (!allGreen(dSpec, dQual, dHunt)) {
      // 不全绿：不 halt，记录 destructive_review_failed + findings 到 perTask，继续下一 task
      state.perTask[taskKey].destructive_review_failed = true
      state.perTask[taskKey].destructive_review_findings = collectReviewFindings(dSpec, dQual, dHunt)
      log(`⚠ ${task.id} destructive review NOT green — 记录 ${state.perTask[taskKey].destructive_review_findings.length} 项 findings 并继续（不 halt）`)
    } else {
      log(`✓ ${task.id} destructive review green — 继续正常流程`)
    }
  }

  // Q8（第 5 轮）: runTask 全流程完成（commit + simplify + destructive review）须设终态 status='done'。
  //   旧代码停在 'committed' → 无法区分"已提交但 simplify/destructive 未完成"与"全流程完成"。
  state.perTask[taskKey].status = 'done'
  return { halted: false }
}

// ===== 顶层编排（Workflow 入口）=====
phase('Bootstrap')
// P1-8a（第 8 轮）: 防御 args===undefined。runtime 总是注入 args，但彻底防御零成本。
if (!args) throw new Error('args must be a non-null object (Workflow runtime contract)')
// WAI1（2026-07-04）: Workflow runtime 偶发把 args 序列化为 JSON 字符串注入（Claude issue #72248/#68969/#73899）。
//   若字符串化，入口校验 typeof args.configPath 失败，误报 args 不合法。防御：先尝试 JSON.parse，
//   失败仍 throw，让真实错误清晰暴露；不吞异常。
if (typeof args === 'string') {
  try {
    args = JSON.parse(args)
  } catch (parseErr) {
    throw new Error(`args was a string but failed JSON.parse: ${parseErr.message}`)
  }
}
// P2-7（第 7 轮）: args 入口校验 fail-fast。旧代码直接注入 args.configPath/plansDir，未传时 undefined
//   被 P1-7 渲染为空串 → bootstrap agent 因 config 路径空而失败，错误信息不直观。改：入口校验类型,
//   非字符串或空串 → throw fail-fast（Workflow runtime 会 surface 错误，用户立即知晓）。
if (typeof args.configPath !== 'string' || !args.configPath.trim()) {
  throw new Error('args.configPath must be a non-empty string (workflow.config.json path)')
}
if (typeof args.plansDir !== 'string' || !args.plansDir.trim()) {
  throw new Error('args.plansDir must be a non-empty string (plans directory path)')
}
// QC1: tsAgent 调用包 try/catch——非 quota 异常（网络/runtime 内部错误）不应 crash 整个 workflow
// 而无 manifest/blocked.md 丢失进度。
// S1（第 4 轮）: 旧 fallback 用 Date API 违反 §4.3 硬约束——orchestrator 是 JS sandbox，
//   无 fs、无 subprocess、无 Date.now/Math.random。改用占位符 'unknown-ts'（manifest 仍可写，
//   run_ts 缺失不阻塞；用户可从 git log / blocked.md 还原时间）。
let tsAgent
try {
  tsAgent = await agent('Run `date -u +%Y%m%dT%H%M%SZ` and return ONLY the timestamp string, nothing else.', { label: 'get-ts' })
} catch (e) {
  log(`⚠ get-ts agent 抛错（非 quota），降级用 'unknown-ts' 占位符: ${errStr(e)}`)
  tsAgent = 'unknown-ts'
}
// S2（第 5 轮）: 非 string（null/对象/数字）均须降级 'unknown-ts'——
//   Q3: null → String(null)="null" → runsDir="runs/null" → manifest 互相覆盖
//   Q9: 对象 → String({})="[object Object]" → runsDir="runs/[object Object]" 同源问题
//   旧代码 `typeof tsAgent === 'string' ? tsAgent.trim() : <强转>` 把非 string 强转 → 上述脏值。
//   修：非 string 或空串均降级 'unknown-ts' 占位符（manifest 仍可写，run_ts 缺失不阻塞）。
if (typeof tsAgent !== 'string' || !tsAgent.trim()) tsAgent = 'unknown-ts'
state.runTs = tsAgent.trim()
let boot
try {
  boot = await dispatchImpl(buildPrompt('bootstrap', { configPath: args.configPath, plansDir: args.plansDir, runTs: state.runTs }), { schema: SCHEMAS.bootstrap, label: 'bootstrap' }, 'sonnet', 'opus')
} catch (e) {
  // P0-3（第 6 轮）: dispatchImpl 已封装 agent_error（不 throw），此处兜底仅防 runtime 异常。
  //   旧代码一律标 model_unavailable → 误导用户无效 resume。改 agent_error（真实 bug 语义）。
  return await halt(null, null, { reason: 'agent_error', diag: { model: 'sonnet', error: errStr(e) } })
}
if (boot.halted) { return await halt(null, null, { reason: boot.reason, diag: boot.diag }) }
if (boot.status !== 'ok') { return await halt(null, null, { reason: `bootstrap ${boot.status}`, diag: boot.diagnostics }) }
// H-F1 (2026-07-07): bootstrap step 5d 分类处理失败时 leave dirty_tree=true，orchestrator 须检查并 halt
// 否则脏工作树上跑 implementor → commit 混入残留改动 → gate 在含残留的 SHA 上验证
if (boot.evidence?.dirty_tree) { return await halt(null, null, { reason: 'bootstrap dirty_tree cleanup failed', diag: { summary: boot.summary || 'dirty_tree=true after bootstrap step 5 classification' } }) }
// P1-bootstrap-sanitize (2026-07-05): bootstrap agent 偶返 plan-scoped task_id（"plan-06/T1"）而非裸 "T1"。
// taskKey/commitSubject 会再拼一层 plan 前缀 → feat(plan-06/plan-06/T1) + completed 比对 key
// (plan-06/plan-06/T1) 不匹配 state.completed (plan-06/T1) → 已完成 task 误判 pending → 重跑（实战 aae0ce2 bug）。
// 源头归一化：strip 所有 task_id 的 plan-XX/ 前缀，下游统一用裸 id 拼出正确 plan-scoped key。
for (const p of (boot.evidence.plans || [])) {
  if (Array.isArray(p.tasks)) {
    for (const t of p.tasks) t.id = bareTaskId(t.id)
    p.tasks = dropParentTasks(p.tasks)  // P2-leaf-guard: 过滤非叶子父 task（T6+T6b 共存 → drop T6）
  }
}
for (const fa of (boot.evidence.failed_approaches || [])) fa.task_id = bareTaskId(fa.task_id)
for (const twf of (boot.evidence.task_write_files || [])) twf.task_id = bareTaskId(twf.task_id)
for (const tl of (boot.evidence.task_lessons || [])) tl.task_id = bareTaskId(tl.task_id)
state.config = boot.evidence.config
// P0-2（第 6 轮）: state.plans 须写入（finalReport stateJson 须含 plans，manifest 完整性）
state.plans = boot.evidence.plans
// 归一化为 "plan-{seq}/T-{id}"。run-2 旧逻辑【去】plan 前缀→单 plan 内能匹配，但跨 plan 同名
// task（Plan 01/02 都有 T1-T10）会让 Plan 02 的 T2 被 Plan 01 的 T2 误 skip → domain layer 残缺。
// plan-scoped key 修复：见下方比对 `plan-${plan.seq}/${task.id}`。
// args.completed 可手动覆盖（resume 时显式传已 commit 的 plan-scoped id 列表，双保险）。
// P3-deterministic-completed (2026-07-05): completed 提取从 LLM 拿走交给正则。
// 优先 args.completed（手动覆盖）；其次 extractCompletedFromSubjects(git_log_subjects)
// （确定性正则，bootstrap 返回原始 subjects）；最后 boot.evidence.completed（LLM 提取，仅 fallback）。
// P3-union-fallback (2026-07-06): 正则提取与 boot.evidence.completed 取【并集】而非二选一。
//   根因：bootstrap agent（kimi-k2.7）复制 git log subjects 时偶漏某条（如 plan-06/T2 的
//   feat commit 在 git log 第 68 条，bootstrap 返回的 193 条 subjects 里独缺 T2）→ 正则输入缺
//   → extractCompletedFromSubjects 输出缺 T2 → state.completed 不含 T2 → T2 被当 pending 重跑
//   → OSCILLATING。boot.evidence.completed（LLM 提取）此时反而含 T2，但旧逻辑因正则成功返回
//   （非空）就不走 LLM fallback → 漏。并集：任一来源识别到的 task 都视为 completed，互为兜底。
//   正则仍是主源（确定性），LLM completed 补漏（覆盖 bootstrap 复制 subjects 时的随机漏条）。
const _regexCompleted = (Array.isArray(boot.evidence.git_log_subjects) && boot.evidence.git_log_subjects.length
  ? extractCompletedFromSubjects(boot.evidence.git_log_subjects)
  : [])
const _llmCompleted = (Array.isArray(boot.evidence.completed) ? boot.evidence.completed : [])
const _rawCompleted = (Array.isArray(args.completed) && args.completed.length
  ? args.completed
  : [...new Set([..._regexCompleted, ..._llmCompleted])]) || []
state.completed = normalizeCompleted(_rawCompleted)
// 跨 session 失败方案追踪：按 plan-scoped taskKey 索引存入 state，供 implCtx 注入 implementor prompt
// P1-2（第 13 轮）: bootstrap 返回的 task_id 可能是裸 T1 或 plan-scoped；统一归一化为 plan-scoped key，防跨 plan 同名 task 查找失败
if (Array.isArray(boot.evidence.failed_approaches)) {
  for (const fa of boot.evidence.failed_approaches) {
    const faKey = fa.task_id.includes('/') ? fa.task_id : `plan-${String(fa.plan_seq).padStart(2, '0')}/${fa.task_id}`
    if (!state.failedApproaches[faKey]) state.failedApproaches[faKey] = []
    state.failedApproaches[faKey].push(fa)
  }
}
// write_files 边界控制：按 plan-scoped key 索引存入 state，供 commit agent 边界检查
// S3（第 5 轮）: 存储须用 plan-{seq}/{task_id}（与 perTask/failedApproaches 同 key 空间），
//   bootstrap 遍历所有 plan 收集 task_write_files，每个 plan 都有 T1-T10 → 裸 task_id 跨 plan 覆盖。
//   bootstrap prompt 已改为返回 plan_seq 字段，此处归一化为 plan-scoped key。
if (Array.isArray(boot.evidence.task_write_files)) {
  for (const twf of boot.evidence.task_write_files) {
    state.taskWriteFiles[`plan-${String(twf.plan_seq).padStart(2, '0')}/${twf.task_id}`] = twf.files || []
  }
}
// LESSONS.md 跨任务失败知识库：按 plan-scoped key 索引存入 state，供 implCtx 注入 implementor prompt
// S3（第 5 轮）: 同 taskWriteFiles，存储须用 plan-scoped key（防跨 plan 同名 task 覆盖）
if (Array.isArray(boot.evidence.task_lessons)) {
  for (const tl of boot.evidence.task_lessons) {
    state.taskLessons[`plan-${String(tl.plan_seq).padStart(2, '0')}/${tl.task_id}`] = tl.lessons || []
  }
}
// v3: bootstrap 额外返回 all_lessons（全量，含 category），存 state.allLessons
if (Array.isArray(boot.evidence.all_lessons)) {
  state.allLessons = boot.evidence.all_lessons
}

for (const plan of boot.evidence.plans) {
  if (!matchesPlanFilter(plan, args.plan)) continue
  state.currentPlan = plan.id
  phase(`Plan ${plan.id}`)
  const want = (Array.isArray(args.tasks) && args.tasks.length) ? new Set(args.tasks.map(String)) : null  // P2-9a（第 9 轮）: Array.isArray 防御字符串误传（字符串有 .length，.map(String) 会 TypeError）
  const tasks = plan.tasks.filter(t => !want || want.has(t.id))
  for (const task of tasks) {
    const taskKey = `plan-${String(plan.seq).padStart(2, '0')}/${task.id}`  // plan-scoped：跨 plan 同名 task 不误跳过，seq 归一化为 2 位填充
    if (state.completed.includes(taskKey)) { log(`skip ${taskKey} (already committed)`); continue }
    let r
    try {
      r = await runTask(plan, task)
    } catch (e) {
      // P0-3（第 6 轮）: uncaught error 须按 quota 与否分流（旧代码一律 model_unavailable 误导）。
      //   quota（429/限额）→ model_unavailable；其余 → agent_error（TypeError 等真实 bug）。
      //   halt + 保存进度（finalReportWithFallback 依次试 opus/sonnet/haiku），等用户指令 resume。
      const reason = isQuotaError(e) ? 'model_unavailable' : 'agent_error'
      r = { halted: true, reason, diag: { model: task.model || 'sonnet', error: errStr(e) } }
    }
    if (r.halted) { return await halt(plan, { id: task.id }, r) }
  }
  // plan 级独立 gate（§3）：本 plan 最后 commit SHA 上重跑 test + lint_command + extra_lint_commands
  // P2-9（第 6 轮）: lastSha 须按 plan.tasks 反向查找（非 Object.values 插入顺序，后者依赖 perTask 写入顺序）。
  //   plan.tasks 是 plan frontmatter 定义的 task 顺序，反向遍历找最后一个有 commit_sha 的 task。
  // P0-7（第 7 轮）: tk 须用 padStart 归一化（与其他 4 处一致），否则 plan.seq=1（数字）→ "plan-1" 查不到 "plan-01" 的 perTask。
  let lastSha = null
  for (let i = plan.tasks.length - 1; i >= 0; i--) {
    const tk = `plan-${String(plan.seq).padStart(2, '0')}/${plan.tasks[i].id}`
    if (state.perTask[tk]?.commit_sha) { lastSha = state.perTask[tk].commit_sha; break }
  }
  if (lastSha) {
    const cmds = gateCommands(state.config)
    let gate
    try {
      gate = await dispatchImpl(buildPrompt('gate', { sha: lastSha, gateCommands: JSON.stringify(cmds), schemaCheck: formatSchemaCheck(state.config?.schema_tool || '', state.config?.model_paths || [], state.config?.migration_paths || []) }), { schema: SCHEMAS.gate, label: `gate:${plan.id}`, phase: `Plan ${plan.id}` }, 'sonnet')
    } catch (e) {
      // P0-3（第 6 轮）: dispatchImpl 已封装 agent_error，此处兜底改 agent_error（非 model_unavailable）
      return await halt(plan, null, { reason: 'agent_error', diag: { model: 'sonnet', error: errStr(e) } })
    }
    if (gate.halted) { return await halt(plan, null, { reason: gate.reason, diag: gate.diag }) }
    if (gate.status !== 'ok' || gate.evidence?.migration_missing) {
      return await halt(plan, null, { reason: 'plan gate failed', diag: { sha: lastSha, tests_exit_code: gate.evidence?.tests_exit_code, summary: gate.evidence?.pytest_summary, lint_results: gate.evidence?.lint_results, migration_missing: gate.evidence?.migration_missing } })
    }
    // P1-1（第 13 轮）: gate agent 自称恢复 HEAD 后，orchestrator 独立验证当前 HEAD 与 restored_head 一致，防静默在错误基线继续
    const headVerify = await dispatchImpl(buildPrompt('headVerifier', {}), { schema: { type: 'object', required: ['status', 'evidence'], additionalProperties: true, properties: { status: { type: 'string', enum: ['ok'] }, evidence: { type: 'object', required: ['head'], properties: { head: { type: 'string' } } }, summary: { type: 'string' } } }, label: `head-verify:${plan.id}`, phase: `Plan ${plan.id}` }, 'sonnet')
    if (headVerify.halted || headVerify.status !== 'ok' || headVerify.evidence?.head !== gate.evidence?.restored_head) {
      return await halt(plan, null, { reason: 'gate head restore verification failed', diag: { expected: gate.evidence?.restored_head, actual: headVerify.evidence?.head, sha: lastSha } })
    }
    log(`✓ plan ${plan.id} gate green @ ${lastSha} (${cmds.length} cmd${cmds.length === 1 ? '' : 's'})`)
  } else {
    log(`plan ${plan.id}: no new commits, gate skipped`)
  }
}

phase('Finalize')
const frDone = await agentWithFallback('finalReport', { mode: 'done', stateJson: JSON.stringify(state), blockedInfo: '', runsDir: `runs/${state.runTs}`, runTs: state.runTs, lessonsPath: state.config?.lessons_path || '', lessonsAutoDistill: String(resolveLessonsAutoDistill(state.config)) }, 'final-report')
if (!frDone) log('✗✗ 致命：finalReport 全链失败，manifest 未写入！请手动检查 runs/ 目录')
log('✓ workflow done')
return { result: 'done', perTask: state.perTask }

})()
