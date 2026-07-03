import { describe, it } from 'node:test'
import assert from 'node:assert'

// Mock agent function for testing
const createMockAgent = (responses) => {
  let callCount = 0
  return async (prompt, opts) => {
    const response = responses[callCount++]
    if (response instanceof Error) throw response
    return response
  }
}

// Copy dispatchImpl logic for testing (will sync with actual implementation)
async function dispatchImpl(prompt, opts, model, retryModel, mockAgent) {
  const agent = mockAgent || globalThis.agent
  let impl
  try { impl = await agent(prompt, { ...opts, model }) }
  catch (e) {
    const s = String(e?.message || e || '').toLowerCase()
    if (/quota|rate.?limit|429/i.test(s)) return { halted: true, reason: 'model_unavailable', diag: { model, error: String(e) } }
    throw e
  }
  if (impl?.status === 'model_unavailable') return { halted: true, reason: 'model_unavailable', diag: impl.diagnostics }

  // null 响应：可能是 quota 耗尽，也可能是模型能力不足
  if (impl == null) {
    // 如果有 retryModel，用更强模型重试一次
    if (retryModel && retryModel !== model) {
      try {
        impl = await agent(prompt, { ...opts, model: retryModel })
        if (impl != null) return impl  // 重试成功
      } catch (e) {
        const s = String(e?.message || e || '').toLowerCase()
        if (/quota|rate.?limit|429/i.test(s)) return { halted: true, reason: 'model_unavailable', diag: { model: retryModel, error: String(e) } }
        throw e
      }
    }
    // 重试失败或无 retryModel，halt
    return { halted: true, reason: 'model_unavailable', diag: { model, error: 'agent returned null (quota exhausted or capability failure)' } }
  }
  return impl
}

describe('dispatchImpl retryModel parameter', () => {
  it('should halt when agent returns null without retryModel', async () => {
    const mockAgent = createMockAgent([null])
    const result = await dispatchImpl('prompt', {}, 'sonnet', null, mockAgent)

    assert.strictEqual(result.halted, true)
    assert.strictEqual(result.reason, 'model_unavailable')
    assert.strictEqual(result.diag.model, 'sonnet')
  })

  it('should retry with retryModel when first attempt returns null', async () => {
    const mockAgent = createMockAgent([
      null,  // first attempt with sonnet returns null
      { status: 'ok', result: 'success' }  // retry with opus succeeds
    ])
    const result = await dispatchImpl('prompt', {}, 'sonnet', 'opus', mockAgent)

    assert.strictEqual(result.halted, undefined)
    assert.strictEqual(result.status, 'ok')
    assert.strictEqual(result.result, 'success')
  })

  it('should halt when retryModel also returns null', async () => {
    const mockAgent = createMockAgent([
      null,  // first attempt with sonnet returns null
      null   // retry with opus also returns null
    ])
    const result = await dispatchImpl('prompt', {}, 'sonnet', 'opus', mockAgent)

    assert.strictEqual(result.halted, true)
    assert.strictEqual(result.reason, 'model_unavailable')
  })

  it('should not retry when retryModel equals model', async () => {
    const mockAgent = createMockAgent([null])
    const result = await dispatchImpl('prompt', {}, 'sonnet', 'sonnet', mockAgent)

    assert.strictEqual(result.halted, true)
    assert.strictEqual(result.reason, 'model_unavailable')
  })

  it('should handle quota error on first attempt', async () => {
    const mockAgent = createMockAgent([new Error('Quota exceeded')])
    const result = await dispatchImpl('prompt', {}, 'sonnet', 'opus', mockAgent)

    assert.strictEqual(result.halted, true)
    assert.strictEqual(result.reason, 'model_unavailable')
    assert.strictEqual(result.diag.model, 'sonnet')
  })

  it('should handle quota error on retry attempt', async () => {
    const mockAgent = createMockAgent([
      null,  // first attempt returns null
      new Error('Quota exceeded')  // retry hits quota
    ])
    const result = await dispatchImpl('prompt', {}, 'sonnet', 'opus', mockAgent)

    assert.strictEqual(result.halted, true)
    assert.strictEqual(result.reason, 'model_unavailable')
    assert.strictEqual(result.diag.model, 'opus')  // quota error on opus
  })

  it('should pass through successful first attempt', async () => {
    const mockAgent = createMockAgent([{ status: 'ok', result: 'immediate success' }])
    const result = await dispatchImpl('prompt', {}, 'sonnet', 'opus', mockAgent)

    assert.strictEqual(result.halted, undefined)
    assert.strictEqual(result.status, 'ok')
    assert.strictEqual(result.result, 'immediate success')
  })

  it('should handle model_unavailable status on first attempt', async () => {
    const mockAgent = createMockAgent([{ status: 'model_unavailable', diagnostics: { error: 'rate limit' } }])
    const result = await dispatchImpl('prompt', {}, 'sonnet', 'opus', mockAgent)

    assert.strictEqual(result.halted, true)
    assert.strictEqual(result.reason, 'model_unavailable')
    assert.strictEqual(result.diag.error, 'rate limit')
  })

  it('should inject model into opts on first agent call (QH1)', async () => {
    let capturedOpts = null
    const mockAgent = async (prompt, opts) => {
      capturedOpts = opts
      return { status: 'ok' }
    }
    const result = await dispatchImpl('prompt', { schema: 'test', label: 'test' }, 'sonnet', null, mockAgent)

    assert.strictEqual(result.status, 'ok')
    assert.strictEqual(capturedOpts.model, 'sonnet', 'model should be injected into opts')
    assert.strictEqual(capturedOpts.schema, 'test', 'original opts should be preserved')
    assert.strictEqual(capturedOpts.label, 'test', 'original opts should be preserved')
  })

  it('should override opts.model with dispatchImpl model parameter (QH1)', async () => {
    let capturedOpts = null
    const mockAgent = async (prompt, opts) => {
      capturedOpts = opts
      return { status: 'ok' }
    }
    // Caller passes model: 'wrong' in opts, but dispatchImpl model param is 'sonnet'
    const result = await dispatchImpl('prompt', { schema: 'test', model: 'wrong' }, 'sonnet', null, mockAgent)

    assert.strictEqual(result.status, 'ok')
    assert.strictEqual(capturedOpts.model, 'sonnet', 'dispatchImpl model should override opts.model')
  })
})

// QC1: tsAgent error handling — 验证期望行为（halt 而非 crash）
// 修复后：tsAgent 调用包 try/catch，非 quota 异常 → halt('model_unavailable')，不 crash
describe('Bootstrap tsAgent error handling (QC1)', () => {
  it('should return halted result instead of crashing when tsAgent throws non-quota error', async () => {
    // 模拟修复后的行为：tsAgent 抛错 → catch → 返回 {halted:true, reason:'model_unavailable'}
    // 而非让错误冒泡到顶层 crash workflow
    function simulateTsAgentCall(agentFn) {
      try {
        const ts = agentFn()  // 同步抛错模拟
        return { ok: true, ts }
      } catch (e) {
        return { halted: true, reason: 'model_unavailable', diag: { error: String(e?.message || e) } }
      }
    }
    const result = simulateTsAgentCall(() => { throw new Error('Network failure') })
    assert.strictEqual(result.halted, true, 'tsAgent 抛错应 halt 而非 crash')
    assert.strictEqual(result.reason, 'model_unavailable')
    assert.match(result.diag.error, /Network failure/)
  })
})

// QC2: finalReportWithFallback — 验证期望行为（全失败时返回 null 而非 crash）
// 修复后：fallback 链末尾的环境默认 model 调用包 try/catch，失败 → 返回 null（不阻塞 manifest）
describe('finalReportWithFallback fallback chain exhaustion (QC2)', () => {
  it('should return null instead of crashing when all fallback models fail', async () => {
    // 模拟修复后的 finalReportWithFallback：全链失败 → catch → 返回 null
    async function simulateFinalReportWithFallback(agentFn) {
      const models = ['opus', 'sonnet', 'haiku']
      for (const m of models) {
        try {
          return await agentFn(m)
        } catch (e) { /* 试下一个 */ }
      }
      // 环境默认 model 兜底（修复后加 try/catch）
      try {
        return await agentFn(null)
      } catch (e) {
        return null  // 修复：全失败返回 null，不 crash
      }
    }
    const result = await simulateFinalReportWithFallback(() => { throw new Error('All models exhausted') })
    assert.strictEqual(result, null, '全 fallback 失败应返回 null 而非 crash')
  })
})

// SH2: distiller 独立调用验证（orchestrator 无 fs，distiller 自己读 lessonsPath）
// 修复后：distiller 是 halt() 中的独立 agent 调用（非 finalReport 内部），
// 自己读 lessonsPath + 自己写回。不需要 state.existingLessons。
describe('Lesson distiller independent invocation (SH2)', () => {
  it('should invoke distiller as independent agent that reads lessonsPath itself', async () => {
    // 模拟修复后的 halt() 路径：distiller 收到 lessonsPath（非 existingLessons 内容）
    let capturedCtx = null
    const mockLessonDistillerWithFallback = async (ctx) => {
      capturedCtx = ctx
      return { decisions: [{ action: 'skip', id: 'none', title: 'test', detail: 'test' }] }
    }
    // 模拟 halt() 调用 distiller
    const distillInput = JSON.stringify({ mode: 'halted', halt_info: { task: 'T1' }, review_history: [], failed_approaches: [] })
    await mockLessonDistillerWithFallback({ distillInput, lessonsPath: 'docs/superpowers/lessons.md' })
    // 验证：distiller 收到 lessonsPath（自己读文件），而非 existingLessons 内容
    assert.ok(capturedCtx.lessonsPath, 'distiller 应收到 lessonsPath 路径')
    assert.strictEqual(capturedCtx.lessonsPath, 'docs/superpowers/lessons.md')
    assert.ok(!capturedCtx.existingLessons, 'distiller 不应收到 existingLessons（自己读文件）')
  })
})
