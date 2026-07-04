import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const runSrc = fs.readFileSync(path.resolve(__dirname, '../../../../.claude/workflows/run-plans.js'), 'utf8')

const ENTRY_START = "// ===== 顶层编排（Workflow 入口）====="
const ENTRY_END = "// Q8（第 5 轮）: runTask 全流程完成"

function extractEntry(src) {
  const startIdx = src.indexOf(ENTRY_START)
  const endIdx = src.indexOf(ENTRY_END)
  assert.ok(startIdx >= 0, 'run-plans.js 须含 Workflow 入口标记')
  // 入口到 runTask 定义之间
  const upper = endIdx > startIdx ? endIdx : src.length
  return src.slice(startIdx, upper)
}

test('WAI1: Workflow 入口防御 args 被 runtime 序列化为 JSON 字符串（issue #72248）', () => {
  const entry = extractEntry(runSrc)
  // 必须识别字符串 args 并尝试 JSON.parse
  assert.match(entry, /typeof args === ['"]string['"]/, '入口须检查 typeof args === "string"')
  assert.match(entry, /JSON\.parse\(args\)/, '入口须尝试 JSON.parse(args)')
  // 解析失败不得静默吞掉——必须继续 throw 或走后续校验失败（这里要求保留 args，让后续校验自然 throw）
  assert.match(entry, /catch\s*\([\s\S]*?\)\s*\{[\s\S]*?throw\s+/, 'JSON.parse 失败必须 throw')
})

test('WAI1b: 解析后的 args 仍需通过 configPath/plansDir 校验', () => {
  const entry = extractEntry(runSrc)
  // JSON.parse 后必须重新对 args.configPath / args.plansDir 做 string/trim 检查
  assert.match(entry, /typeof args\.configPath !== ['"]string['"]/, '入口仍须校验 args.configPath 类型')
  assert.match(entry, /typeof args\.plansDir !== ['"]string['"]/, '入口仍须校验 args.plansDir 类型')
})
