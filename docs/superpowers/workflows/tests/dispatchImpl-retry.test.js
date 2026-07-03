import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

// QC-1 修复：旧测试用手写 dispatchImpl 副本（"Copy dispatchImpl logic for testing"），
// 真实 dispatchImpl 漂移时测试假绿。改为源码字面量断言——直接验证 run-plans.js 中
// dispatchImpl 的关键分支存在。逻辑正确性由 helpers.test.js 测 isQuotaError/classifyThrown
// （lib.js 纯函数）间接覆盖。
const __dirname = path.dirname(fileURLToPath(import.meta.url))
const runSrc = fs.readFileSync(path.resolve(__dirname, '../../../../.claude/workflows/run-plans.js'), 'utf8')

// 提取 dispatchImpl 函数体（从定义到下一个函数定义之前）
function extractDispatchImpl(src) {
  const re = /async function dispatchImpl\([\s\S]*?\n(?=\n(?:async )?function |$)/
  const m = src.match(re)
  assert.ok(m, 'run-plans.js 须含 dispatchImpl 定义')
  return m[0]
}

// 提取 agentWithFallback 函数体（Q7（第 4 轮）：finalReportWithFallback / lessonDistillerWithFallback 抽象为统一 helper）
function extractAgentWithFallback(src) {
  const re = /async function agentWithFallback\([\s\S]*?\n(?=\n(?:async )?function |$)/
  const m = src.match(re)
  assert.ok(m, 'run-plans.js 须含 agentWithFallback 定义（Q7 抽象）')
  return m[0]
}

test('dispatchImpl 定义存在且含关键分支', () => {
  const body = extractDispatchImpl(runSrc)
  // QH1: 首次调用注入 model 到 opts（{ ...opts, model }）
  assert.match(body, /\.\.\.opts,\s*model/, 'dispatchImpl 首次调用须注入 model 到 opts（QH1）')
  // quota 检测：调 isQuotaError（非手写正则）
  assert.match(body, /isQuotaError\(e\)/, 'dispatchImpl 须调 isQuotaError（非手写正则）')
  // null guard：agent 返回 null → model_unavailable halt
  assert.match(body, /impl == null/, 'dispatchImpl 须有 null guard')
  assert.match(body, /model_unavailable/, 'dispatchImpl null/限额 → model_unavailable halt')
  // retryModel 逻辑：有 retryModel 且不同于 model → 重试
  assert.match(body, /retryModel && retryModel !== model/, 'dispatchImpl 须有 retryModel 重试条件')
  // 重试也注入 model 到 opts
  assert.match(body, /\.\.\.opts,\s*model:\s*retryModel/, 'dispatchImpl 重试须注入 retryModel 到 opts')
})

test('dispatchImpl-retry 正则与生产 isQuotaError 同步（QH2 遗留）', () => {
  // QH2: 旧测试用 /quota|rate.?limit|429/i 手写正则，缺生产 isQuotaError 的中文关键词。
  // 改为源码断言：dispatchImpl 须调 isQuotaError（生产正则），不得手写正则。
  const body = extractDispatchImpl(runSrc)
  assert.match(body, /isQuotaError/, 'dispatchImpl 须调 isQuotaError（生产正则含中文限额）')
  // 不得含手写 quota 正则（旧副本残留）
  assert.doesNotMatch(body, /\/quota\|rate\.\?limit\|429\/i/, 'dispatchImpl 不得手写 quota 正则（须调 isQuotaError）')
})

test('QC1: tsAgent 调用包 try/catch + unknown-ts 降级兜底（S1 第 4 轮）', () => {
  // S1: orchestrator 不得用 new Date()（§4.3 无 Date.now/Math.random 硬约束）
  //   tsAgent 错误 → catch → 降级用 'unknown-ts' 占位符（不 crash，manifest 仍可写）
  assert.match(runSrc, /let tsAgent/, 'tsAgent 须用 let 声明（便于 catch 重新赋值）')
  assert.match(runSrc, /tsAgent = await agent\([\s\S]*?get-ts/, 'tsAgent 须在 try 块中调用')
  assert.match(runSrc, /tsAgent = 'unknown-ts'/, "tsAgent catch 须降级用 'unknown-ts' 占位符（S1：§4.3 无 new Date 约束）")
  // S1: 不得用 new Date()（§4.3 硬约束）
  assert.doesNotMatch(runSrc, /new Date\(\)/, 'run-plans.js 不得用 new Date()（S1：§4.3 硬约束——ts 由 subagent 写入）')
})

test('QC2: agentWithFallback 末尾 try/catch + 返回 null（Q7 第 4 轮抽象）', () => {
  // Q7: finalReportWithFallback / lessonDistillerWithFallback 抽象为统一 agentWithFallback helper
  //   —— opus→sonnet→haiku 逐一尝试，全链失败用环境默认 model，再失败返回 null
  const body = extractAgentWithFallback(runSrc)
  // 环境默认 model 调用须包 try/catch
  assert.match(body, /label: `\$\{labelPrefix\}:default`/, '须有环境默认 model 兜底调用')
  assert.match(body, /return null/, '全链失败须返回 null（不 crash）')
  // 不得裸 return await agent(...)（旧 QC2 bug：末尾无 try/catch）
  const defaultPart = body.slice(body.indexOf('label: `${labelPrefix}:default`'))
  assert.match(defaultPart, /catch/, '环境默认 model 调用须包 try/catch')
})

test('SH2: distiller 是 halt() 中独立 agent 调用（Q7 第 4 轮：经 agentWithFallback）', () => {
  // Q7: halt() 须调 agentWithFallback('lessonDistiller', ...)（独立 agent，自己读 lessonsPath）
  assert.match(runSrc, /agentWithFallback\('lessonDistiller'/, "halt() 须调 agentWithFallback('lessonDistiller', ...) 传 distillInput")
  assert.match(runSrc, /distillInput/, 'halt() 须构造 distillInput 传给 distiller')
  assert.match(runSrc, /lessonsPath/, 'distiller 须收到 lessonsPath 路径（自己读文件）')
  // finalReport step5 须说明 distiller 已执行（不再自己调 distiller）
  const finalReportPrompt = runSrc.match(/finalReport: `([\s\S]*?)`/)?.[1] || ''
  assert.match(finalReportPrompt, /ALREADY been invoked/i, 'finalReport 须说明 distiller 已独立执行')
})
