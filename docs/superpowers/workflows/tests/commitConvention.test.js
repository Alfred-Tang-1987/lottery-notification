import { test } from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { commitSubject, extractTaskKey, normalizeCompleted } from '../lib.js'

// 提交约定一致性测试：emission（COMMIT agent）与 recognition（bootstrap 扫 git log）
// 必须用同一格式 feat(plan-XX/TY):。任何他类 scope（feat(scheduler)/feat(notifications)/
// 无 scope）都会被 bootstrap 漏认 → 该 task 被判为未完成 → 重跑 → OSCILLATING halt。
// 根因复盘：plan 模板 Step 5/8 嵌入了 feat(scheduler)/feat(notifications)/feat: 示意，
// implementor/COMMIT agent 照抄 → bootstrap 不认 → 重跑。见 systematic-debugging 记录。

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const libSrc = fs.readFileSync(path.resolve(__dirname, '../lib.js'), 'utf8')
const runSrc = fs.readFileSync(path.resolve(__dirname, '../../../../.claude/workflows/run-plans.js'), 'utf8')

function promptBody(src, role) {
  const m = src.match(new RegExp(`  ${role}: \\\`([\\s\\S]*?)\\\`,`))
  assert.ok(m, `role ${role} template not found`)
  return m[1]
}

test('commitSubject emits canonical feat(plan-XX/TY): title format', () => {
  assert.equal(commitSubject(4, 'T4', 'Scheduler 接线'), 'feat(plan-04/T4): Scheduler 接线')
  assert.equal(commitSubject(1, 'T4b', '号码 + 开奖 + 比对'), 'feat(plan-01/T4b): 号码 + 开奖 + 比对')
})

test('emission is recognized: extractTaskKey ∘ commitSubject ∘ normalizeCompleted round-trips', () => {
  const subj = commitSubject(4, 'T6', '启动 backfill')
  const key = extractTaskKey(subj)
  assert.equal(key, 'plan-04/T6')
  assert.deepEqual(normalizeCompleted([key]), ['plan-04/T6'])
})

test('REGRESSION: plan-template example scopes are INVISIBLE to bootstrap (OSCILLATING root cause)', () => {
  // 这些是 plan 文件 Step 5/8 的字面示意——agent 曾照抄导致 bootstrap 漏认、重跑、OSCILLATING。
  const badScopes = [
    'feat(scheduler): build_scheduler jobstore共享engine + 全局CST + coalesce/max_instances',
    'feat(scheduler): 注册全部任务（路径A轮询/路径B汇总/浮奖回填/兑奖过期/周月报）',
    'feat(notifications): 路径A即时简讯 + 路径B汇总模板（金额分→元，浮动待派奖）',
    'feat: 应用启动接线 scheduler + 端到端调度推送集成测试'
  ]
  for (const bad of badScopes) {
    assert.equal(extractTaskKey(bad), null, `expected invisible, got key for: ${bad}`)
  }
})

test('COMMIT prompt enforces convention in BOTH copies (lib.js + run-plans.js)', () => {
  for (const [label, src] of [['lib.js', libSrc], ['run-plans.js', runSrc]]) {
    const body = promptBody(src, 'commit')
    assert.match(body, /commitMsg=/, `${label}: commit prompt must receive canonical commitMsg placeholder`)
    assert.match(body, /git commit --amend/, `${label}: commit prompt must verify-and-amend HEAD message`)
    assert.match(body, /plan-XX\/TY/, `${label}: commit prompt must state the feat(plan-XX/TY) convention`)
    assert.match(body, /scheduler|notifications/, `${label}: commit prompt must warn against plan example scopes`)
    assert.match(body, /OSCILLATING|re-run|invisible/i, `${label}: commit prompt must explain WHY (bootstrap recognition)`)
  }
})

test('run-plans.js inlines commitSubject helper (orchestrator uses it to build commitMsg)', () => {
  assert.match(runSrc, /function commitSubject|const commitSubject/, 'run-plans.js must inline commitSubject')
  assert.match(runSrc, /commitSubject\(plan\.seq/, 'orchestrator must call commitSubject(plan.seq, ...) at commit dispatch')
})
