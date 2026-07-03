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
  try { impl = await agent(prompt, opts) }
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
})
