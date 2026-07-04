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

// 收集单个 failed review 的 findings（内部 helper，collectReviewFindings 与
// reviewHaltForEmptyFailed 共用，避免两处重复 push 逻辑漂移）。
// 非 failed 或无 diagnostics → 返回 []。items 归一化同 collectReviewFindings。
function findingsOf(r, source, key) {
  if (!r || r.status !== 'failed') return []
  const out = []
  for (const it of (r.diagnostics?.[key] || [])) {
    if (it && typeof it === 'object') out.push({ source, severity: it.severity, title: it.title || String(it), file: it.file, fix: it.fix })
    else out.push({ source, title: String(it) })
  }
  return out
}

// 收集三类 review 的发现并归一化为结构化数组（orchestrator fix-round 反馈管道）。
// spec/quality 存 diagnostics.issues；hunter 存 diagnostics.silent_failures（不同 key！
// 旧 issuesFromReviews 只读 issues → hunter 发现被完全丢弃，Bug 2）。
// items 可能是 string 或 object → 统一归一化为 {source, severity?, title, file?, fix?}。
export function collectReviewFindings(spec, qual, hunt) {
  return [...findingsOf(spec, 'spec', 'issues'), ...findingsOf(qual, 'quality', 'issues'), ...findingsOf(hunt, 'hunter', 'silent_failures')]
}

// 格式化 implementor concerns 为 review prompt 的 focusHint 段落（Q11 抽出，消除两处重复模板）。
// 空数组 → 空串（该段消失，review 不收 focusHint）；非空 → 多行 bullet 列表。
// 初始 dispatch 路径与 fix-round done_with_concerns 路径共用此 helper，防模板字符串漂移。
export function formatConcernsHint(concerns) {
  if (!Array.isArray(concerns) || concerns.length === 0) return ''
  return `\n## Implementor Concerns (verify these)\n${concerns.map(c => '- ' + c).join('\n')}`
}

// 第二道静默失败守卫（reviewHaltReason 之后）：任一 review status==='failed' 但产出 0 项 findings
// → 返回 'review_failed_no_findings'。防「合法 failed + 空 diagnostics」漏过 reviewHaltReason
// （status 合法 → 不 halt）→ collectReviewFindings 空 → implementor 收「0 项发现」跑空修复 →
// max rounds 误 halt。与 review_empty 区分：review_empty 是 status 缺失（agent 静默空返回）；
// review_failed_no_findings 是 agent 明确判 failed 却没给可执行发现（issues/silent_failures 空）。
// 优先级在 reviewHaltReason 之后：先排除空 status，再查 failed-no-findings。
export function reviewHaltForEmptyFailed(spec, qual, hunt) {
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

// review_history 存档：单轮 review 的 findings 归一化为 manifest 摘要（只留 title+severity）。
// 复用 findingsOf 归一化；丢 fix/file/source（implementor 反馈字段，manifest 摘要控体积不需要）。
// OSCILLATING/收敛 halt 后凭此精确定位振荡点（哪个 reviewer 在哪点 flip-flop），无需考古
// （旧 review_rounds 只存 int 计数，T3 halt 时无法还原分歧点）。
function summarizeFinding(r, source, key) {
  return { status: r?.status, findings: findingsOf(r, source, key).map(f => ({ title: f.title, severity: f.severity })) }
}

// 单轮三类 review 的摘要（进 manifest.per_task.<task>.review_history）。
export function summarizeReviewRound(round, spec, qual, hunt) {
  return {
    round,
    spec: summarizeFinding(spec, 'spec', 'issues'),
    quality: summarizeFinding(qual, 'quality', 'issues'),
    hunter: summarizeFinding(hunt, 'hunter', 'silent_failures'),
  }
}

// 判断错误是否 model 限额耗尽（§2.4 双重检测的捕获路径）
export function isQuotaError(e) {
  const s = String(e?.message || e || '').toLowerCase()
  // 含中文 router 限额错误（本机 router 返回 "已达到 5 小时的使用上限" / "额度" / "限额"）。
  // 不认则 dispatchImpl catch 不归类 model_unavailable → 走 throw → 顶层 uncaught crash。
  return /quota|rate.?limit|429|overloaded|insufficient.*balance|credit|capacity|使用上限|限额|额度|超出.*限制/i.test(s)
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
  if (/max rounds|OSCILLATING|fix-round|commit failed|commit out_of_scope|simplify (diff check|amend|checkout) failed|BLOCKED|after (context-fetch|retry)|agent_error|model_unavailable|review_empty|review_failed_no_findings/.test(r)) return 'implementor changes'
  return 'unknown'
}

// 校验 amend subagent 返回值（Q1/Q8：边界条件纯函数化，可 node:test 行为测试）。
// amend 须返回 {ok:true, sha:"<40-hex>"}；ok:false / 空 sha / 非 40 位 / 非 hex → invalid。
// run-plans.js 调用方据此 halt（不静默继续用旧 SHA → gate 在旧 SHA 跑漏检 simplify 改动）。
export function validateAmendResult(result) {
  const sha = String(result?.sha || '').trim()
  if (!result?.ok || !/^[0-9a-f]{40}$/.test(sha)) {
    return { valid: false, error: result?.error || result?.sha || 'invalid sha' }
  }
  return { valid: true, sha }
}

// 校验 checkout subagent 返回值（Q4/Q8：兜底验证工作树真 clean，防 ok:true 谎报）。
// checkout 须返回 {ok:true, porcelain:""}——porcelain 非空表示工作树仍有残留（权限/只读 fs 异常）。
// ok:false / porcelain 非空 / null → invalid，调用方 halt（不无条件设 simplify_reverted=true）。
export function validateCheckoutResult(result) {
  if (!result?.ok) {
    return { valid: false, error: result?.error || 'checkout failed' }
  }
  const porcelain = String(result?.porcelain || '').trim()
  if (porcelain !== '') {
    return { valid: false, error: `working tree not clean after checkout: ${porcelain}` }
  }
  return { valid: true }
}

// fix-round implementor 的 model 选择（§5.1 难度递增：最后 1 轮 fix 用最强 model）。
// 有限模式（maxRounds > 0）：round === maxRounds - 1 是最后 1 轮 fix，强制 opus。
//   默认 maxRounds=4 → round=3 的 fix 升级 opus。
// 无限模式（maxRounds=0）：前 3 轮用 baseModel 给 sonnet 充分尝试；从第 4 轮起强制 opus
//   （前 3 轮没修好说明问题复杂，后续用 opus 提升修复质量，直到 detectOscillation halt 或全绿）。
// maxRounds 未传（向后兼容）→ 默认 3（旧行为：round=2 升级 opus）。
// 已是 opus 的 task 返回 'opus'（语义等价，不重复升级）。是升级而非降级，与 §2.4「限额 halt 不降级」纪律一致。
export function fixModelForRound(round, baseModel, maxRounds) {
  const max = (maxRounds === undefined) ? 3 : maxRounds
  if (max === 0) return round >= 4 ? 'opus' : baseModel   // 无限模式：round>=4 升级 opus
  if (round === max - 1) return 'opus'                     // 有限模式：最后 1 轮 fix 强制 opus
  return baseModel
}

// 从 config 解析 review max rounds。默认 4。0/负数 → 0（无限模式）。非数字/null/未配 → 4。
// 无限模式靠 detectOscillation（同文件 ≥3 round）独立防线 halt，防无限循环。
export function resolveMaxRounds(config) {
  const v = config?.review_max_rounds
  if (v === undefined || v === null) return 4              // 未配 → 默认 4
  if (typeof v !== 'number' || !Number.isFinite(v)) return 4  // 非数字 → 默认 4（容错）
  if (v <= 0) return 0                                      // 0/负数 → 无限
  return Math.floor(v)
}

// 从 config 解析 lessons_auto_distill 开关。未配 → true（默认启用自动提炼）。
// 显式 false → 关闭。非布尔值 → true（容错：宁可多提炼，distiller 自身有 skip 决策兜底）。
export function resolveLessonsAutoDistill(config) {
  const v = config?.lessons_auto_distill
  if (v === false) return false                            // 显式 false → 关闭
  return true                                              // 未配/true/非布尔 → 启用
}

// lesson 自动提炼：构造 distiller agent 的输入上下文（§5.4）。
// halt 模式传 haltInfo + reviewHistory + failedApproaches；done 模式 haltInfo=null。
// 字段缺失容错（不 crash），distiller 据此决定 append/update/skip。
export function distillLessonInput(mode, haltInfo, reviewHistory, failedApproaches) {
  return {
    mode,
    halt_info: haltInfo || null,
    review_history: Array.isArray(reviewHistory) ? reviewHistory : [],
    failed_approaches: Array.isArray(failedApproaches) ? failedApproaches : [],
  }
}

// 把现有 lessons.md 解析为结构化数组（供 distiller 语义对比去重）。
// 条目格式：## L-<id> 后跟 title/detail/source?/category?/status 字段（每行一个）。
// 旧格式（无 source/category）兼容：对应字段为 undefined。
// 无条目 → 空数组。
export function formatLessonsForDistill(md) {
  if (!md || typeof md !== 'string') return []
  const entries = []
  // 按 ## L- 开头分段
  const blocks = md.split(/^## (L-[^\n]+)$/m)
  // split 后：[前置文本, id1, body1, id2, body2, ...]
  for (let i = 1; i < blocks.length; i += 2) {
    const id = blocks[i]
    const body = blocks[i + 1] || ''
    const fields = {}
    for (const line of body.split('\n')) {
      const m = line.match(/^(\w+):\s?(.*)$/)
      if (m) fields[m[1]] = m[2]
    }
    entries.push({
      id,
      title: fields.title,
      detail: fields.detail,
      source: fields.source,
      category: fields.category,
      status: fields.status,
    })
  }
  return entries
}

// 渲染单个 lesson 条目为 markdown 段落（append/update 共用）。
function renderLessonEntry(d) {
  const lines = [`## ${d.id}`, `title: ${d.title}`, `detail: ${d.detail}`]
  if (d.source) lines.push(`source: ${d.source}`)
  if (d.category) lines.push(`category: ${d.category}`)
  lines.push('status: active')
  return lines.join('\n')
}

// 应用 distiller 的 decisions 到现有 lessons.md 文本（finalReport 调用，唯一写盘点）。
// action: append → 追加新条目；update → 替换 update_target_id 段落（不存在则回退 append）；
// skip → 忽略。空决策数组 → 原文不变。空现有 lessons → append 时创建文件骨架。
export function applyLessonDecisions(existingMd, decisions) {
  if (!Array.isArray(decisions) || decisions.length === 0) return existingMd
  let md = existingMd || ''
  // 确保有 header
  if (!md) md = '# Lessons Learned\n'
  if (!md.startsWith('# Lessons Learned')) md = '# Lessons Learned\n\n' + md

  for (const d of decisions) {
    if (!d || d.action === 'skip') continue

    if (d.action === 'append') {
      // 追加到末尾，确保与前一段有空行分隔
      const sep = md.endsWith('\n\n') ? '' : (md.endsWith('\n') ? '\n' : '\n\n')
      md += sep + renderLessonEntry(d) + '\n'
      continue
    }

    if (d.action === 'update') {
      const targetId = d.update_target_id || d.id
      // 定位目标段落：## <targetId> 到下一个 ## L- 或文件末尾。
      // 不用 multiline $（会匹配每行末尾），用 [\s\S]*? non-greedy + lookahead 下一个 ## L- 或 string end。
      const escaped = targetId.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
      const pattern = new RegExp(`## ${escaped}\\n[\\s\\S]*?(?=\\n## L-|$)`, '')
      if (pattern.test(md)) {
        md = md.replace(pattern, renderLessonEntry(d))
      } else {
        // 目标不存在 → 回退 append（不丢条目）
        const sep = md.endsWith('\n\n') ? '' : (md.endsWith('\n') ? '\n' : '\n\n')
        md += sep + renderLessonEntry(d) + '\n'
      }
      continue
    }
  }
  return md
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
// Plan 01 的 T2 误 skip。bootstrap 返回格式不稳定（"01/T2" / "01-T2" / "plan-01/T2" / 裸 "T2"）：
// - 带前缀 → 归一化为 "plan-{seq}/T-{id}"（分隔符容忍 `/` 与 `-`，bootstrap 偶用连字符）
// - 裸 id（无 plan 信息）→ 原样保留；它不匹配任何 plan-scoped 比对 key，故不会误跳过（最坏重跑，安全）
export function normalizeCompleted(ids) {
  return ids.map(id => {
    const m = String(id).match(/^(?:plan-)?(\d+)[\/\-]+(T[\w-]+)$/i)
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

// 跨 session 失败方案追踪：bootstrap 扫 runs/*/manifest.json 提取历史失败方案，
// 注入 implementor prompt 防止重复相同失败路径。不填 → 空串 → prompt 段消失。
export function formatFailedApproaches(items) {
  if (!Array.isArray(items) || items.length === 0) return ''
  const lines = items.map(it => `- ${it.task_id}: ${it.reason} — ${it.error}`).join('\n')
  return `## Prior Failed Approaches (do not repeat)
${lines}
If your plan is similar to any above, explicitly state the difference.`
}

// LESSONS.md 跨任务失败知识库：config 可选声明 lessons_path，
// bootstrap 读取并匹配 task 关键词注入 implementor。不填 → 空串 → prompt 段消失。
export function formatLessons(items) {
  if (!Array.isArray(items) || items.length === 0) return ''
  const lines = items.map(it => `- [${it.id}] ${it.title} — ${it.detail}`).join('\n')
  return `## Lessons Learned (check against these before implementing)
${lines}
If your plan is similar to any lesson above, explicitly state why your approach differs.`
}

// write_files 边界控制：plan frontmatter 可选声明 write_files，
// commit agent 提交前检查 git diff 是否越界。不声明 → 空串 → 检查跳过。
export function formatWriteFilesScope(files) {
  if (!Array.isArray(files) || files.length === 0) return ''
  const lines = files.map(f => `- ${f}`).join('\n')
  return `## Write Files Boundary (commit agent will verify)
${lines}
Before committing, run git diff --name-only. If any file is NOT in the list above, you MUST either: 1. revert the out-of-scope change, or 2. report status=failed with out_of_scope in diagnostics.`
}

// schema 迁移一致性检查：config 可选声明 schema_tool + model_paths + migration_paths，
// gate agent 在 committed SHA 上检查 model 文件有变更但无对应迁移文件。不声明 → 空串 → 检查跳过。
export function formatSchemaCheck(schemaTool, modelPaths, migrationPaths) {
  if (!schemaTool) return ''
  const mp = Array.isArray(modelPaths) ? modelPaths.join(', ') : ''
  const xp = Array.isArray(migrationPaths) ? migrationPaths.join(', ') : ''
  return `## Schema Migration Check (gate agent must verify)
1. Run git diff --name-only HEAD~1..HEAD — you are already checked out to the committed SHA, so HEAD~1 is the parent commit.
2. Filter changed files by model_paths: ${mp}
3. Filter changed files by migration_paths: ${xp}
4. If model files changed but NO migration files changed → status=failed, evidence.migration_missing=true`
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
      evidence: { type: 'object', required: ['config', 'plans', 'completed', 'dirty_tree', 'in_progress', 'failed_approaches', 'task_lessons', 'task_write_files'],
        properties: { config: { type: 'object' }, plans: { type: 'array' }, completed: { type: 'array' }, dirty_tree: { type: 'boolean' }, in_progress: { type: 'boolean' }, failed_approaches: { type: 'array' }, task_write_files: { type: 'array' }, task_lessons: { type: 'array' } } },
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
      evidence: { type: 'object', required: ['commit_sha', 'committed_files', 'tests_at_commit'],
        properties: { commit_sha: { type: 'string' }, committed_files: { type: 'array' }, tests_at_commit: { type: 'integer' } } },
      diagnostics: { type: 'object', properties: { out_of_scope: { type: 'array' }, destructive_changes: { type: 'array' } } }, summary: { type: 'string' } } },
  contextFetcher: { type: 'object', required: ['diagnostics'], additionalProperties: true,
    properties: { diagnostics: { type: 'object', required: ['context'], properties: { context: { type: 'string' } } }, summary: { type: 'string' } } },
  gate: { type: 'object', required: ['status', 'evidence'], additionalProperties: true,
    properties: { status: { type: 'string', enum: ['ok', 'failed', 'model_unavailable'] },
      evidence: { type: 'object', required: ['tests_exit_code', 'pytest_summary', 'lint_results'],
        properties: { tests_exit_code: { type: 'integer' }, pytest_summary: { type: 'string' }, lint_results: { type: 'array' }, migration_missing: { type: 'boolean' } } }, summary: { type: 'string' } } },
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
1. Read {{configPath}} → {test_command, full_test_command, build_command, lint_command, extra_lint_commands, spec_path, reference_paths, language, silent_failure_context, lessons_path}. extra_lint_commands / reference_paths / silent_failure_context / lessons_path are OPTIONAL (may be absent → treat as [] / [] / [] / ''). If config contains lessons_path, read that file. Extract entries (each has id, title, detail). For each task in the current plan, match lessons whose title/detail keywords overlap with the task's title. Return matched lessons per task in evidence as task_lessons: [{task_id, plan_seq, lessons:[{id, title, detail}]}] (plan_seq = the plan's seq from step 3). Absent lessons_path → empty array.
2. Config smoke: run test_command with --collect-only. 判断：命令本身不存在（command not found / No such file: pytest）→ status=failed（环境/typo）；命令存在但 collect 失败（no module named pytest / pyproject.toml 不存在 / no tests collected / 业务代码未初始化）→ 记录 'project not yet initialized' 到 summary，status 仍 ok（业务代码由后续 task 创建，预期）。
3. For each {{plansDir}}/*.md: if frontmatter (starts with ---) read task models; else generate — extract LEAF ids (## Task N with ### Task NX children → only NX; else N), modelHint (title contains 安全|加密|认证|JWT|CSRF|Fernet|算法|比对|策略|边界|集成|接口 → opus, else omit), write frontmatter at file top. Idempotent. Record each plan's file (full path) and seq (last two digits of filename, e.g. 01). Also read write_files from frontmatter if present (format: "write_files:\n  T1:\n    - src/a.py\n    - src/b.py"). Return as task_write_files in evidence: [{task_id, plan_seq, files:[...]}] (plan_seq = this plan's seq). Absent → empty array.
4. git log → completed task ids via convention feat(plan-X/T-Y).
5. git status --porcelain → dirty_tree.
6. For each leaf task return its model (sonnet|opus|undefined→sonnet) and title (the description text from the Task header).
7. If runs/ directory exists: scan runs/*/manifest.json files. For each, read per_task object. For each task_id in per_task that has blocked_info, extract {task_id, reason (from blocked_info.reason), error (from blocked_info.last_error)}. Filter to task_ids that match leaf tasks in the current plans. Return as failed_approaches in evidence. If runs/ does not exist → failed_approaches=[].

Return {status, evidence:{config (include ALL fields read in step 1, even optional ones if present), plans:[{id, file, seq, tasks:[{id, model, title}]}], completed:[...], dirty_tree, failed_approaches:[{task_id, reason, error}], task_write_files:[{task_id, plan_seq, files:[...]}], task_lessons:[{task_id, plan_seq, lessons:[{id, title, detail}]}]}, summary}.
RED FLAG: evidence 必须是真实读取结果，绝不编造。`,

  implementor: `You are the IMPLEMENTOR for {{taskId}} (plan {{planId}}). TDD strict (RED→GREEN→REFACTOR). {{retryNote}}

Inputs: specPath={{specPath}} testCommand={{testCommand}} planFile={{planFilePath}} taskId={{taskId}} fixIssues={{fixIssues}}
{{referencePaths}}
{{failedApproaches}}
{{lessons}}
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

## 6-Dimension Quick Check (before reporting)
- Cognitive Overload: any function > 50 lines or nesting > 3 levels?
- Change Propagation: did you change files unrelated to this task?
- Knowledge Duplication: did you paste similar logic in 2+ places?
- Accidental Complexity: did you add abstraction not needed by current requirements?
- Dependency Disorder: any business layer importing infrastructure implementation?
- Domain Distortion: are variable names domain terms, not generic (data/item/info)?

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

Return {status (ok|failed), evidence:{tests_exit_code, pytest_summary, lint_results:[{command, exit_code}]}, summary}.
- status=ok ONLY if EVERY command exit_code == 0.
RED FLAG: every exit_code 必须真实（你在 committed SHA 上亲跑）。必须 checkout 回原 HEAD。任一 exit != 0 → status=failed（包括 lint 命令——架构纪律如层纯度由 lint 强制）。若遇到 model 限额耗尽（quota/rate-limit/429 错误），返回 status:'model_unavailable'（非 failed），让 orchestrator halt 并保存进度。`,

  finalReport: `You are FINAL-REPORT (mode={{mode}} done|halted). Write the run manifest (the ONLY on-disk write in this workflow) and emit a digest.

Inputs: mode={{mode}} state={{stateJson}} blockedInfo={{blockedInfo}} runsDir={{runsDir}} runTs={{runTs}} lessonsPath={{lessonsPath}}

Steps:
1. mkdir -p {{runsDir}}.
2. Write {{runsDir}}/manifest.json = {run_ts:{{runTs}}, mode:{{mode}}, plans:[...], per_task:{<taskId>:{status,model,review_rounds,files_touched_per_round,review_history,commit_sha,simplify_reverted,simplify_review_findings,destructive_review_failed,destructive_review_findings,concerns,blocked_info}}, result}.
3. If mode=halted: write .workflow/blocked.md from {{blockedInfo}} (the blocked task's blocked_info JSON — render EACH field human-readably: plan, task, reason, category, last_error, suggested_fix, quota_exhausted, likely_source, failed_approach). For failed_approach, render as: "Failed Approach: <failed_approach.task_id>: <failed_approach.reason> — <failed_approach.error>". Do NOT hunt for these fields in state — they are provided inline in blockedInfo.
   S3（第 4 轮）: blocked.md 路径固定为 .workflow/blocked.md（§8.2），独立于 {{runsDir}}——
   blocked.md 是用户接手入口，路径须稳定可预测（runsDir 会随 runTs 变化，用户难定位）。
4. If mode=halted: run "git status --porcelain" and "git diff --stat". BEST-EFFORT — if git fails (not a repo / index corrupt), skip this section (do NOT block manifest.json write).
   If "git status --porcelain" output is non-empty, append a "## Working Tree (dirty)" section to .workflow/blocked.md with: the porcelain output (file list) + the diff --stat output (change summary) + 接手指引（implementor 改动未提交，留在工作树。选项：git diff <file> 查看 / git checkout -- <file> 丢弃 / 手动修后 git commit -m "feat(plan-X/T-Y): ..." 再全新跑续，见 USAGE.md §7.1）。
   If output is empty, append "## Working Tree (clean)" — no uncommitted changes（likely_source=gate restored 时预期如此）。
5. Lessons ({{lessonsAutoDistill}}): If lessonsAutoDistill=true AND mode=halted: lesson-distiller agent has ALREADY been invoked by orchestrator before this finalReport call — it read lessonsPath, extracted reusable root causes, and updated lessons.md itself. You do NOT need to touch lessonsPath. If distiller failed (quota/error), orchestrator logged it and proceeded — lessonsPath may be stale but manifest write must proceed. If lessonsAutoDistill=false or mode=done: lessonsPath untouched.
6. Print a digest summary (counts: done/blocked, total tasks, per-plan gate result).

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
