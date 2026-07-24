// STATIC: 此文件由 esbuild 生成，请勿手动编辑。
// DO NOT EDIT — generated from workflow-engine@98af8030955f4d2e92d1076c227b5d7a98fd715008292302e59e9de7e6144090 by sync.mjs
// 源文件：src/index.js
// 生成命令：npm run build

export const meta = {
  name: "run-plans",
  description: "\u81EA\u52A8\u6267\u884C implementation plans\uFF1A\u6BCF task implementor\u2192review chain\u2192commit\uFF0Cplan \u7EA7\u72EC\u7ACB gate",
  phases: [
    { title: "Bootstrap", detail: "\u8BFB config/plan/git log + \u751F\u6210 frontmatter" },
    { title: "Plan", detail: "\u4E32\u884C task + review rounds + simplify + commit + plan gate" },
    { title: "Finalize", detail: "\u5199 manifest + digest" }
  ]
};

// src/lib/errors.js
function isQuotaError(e) {
  const s = String(e?.message || e || "").toLowerCase();
  return /quota|rate.?limit|429|overloaded|insufficient.*balance|credit|capacity|使用上限|限额|额度|超出.*限制/i.test(s);
}
function errStr(e) {
  return String(e?.message || e || "").slice(0, 200);
}
function makeHalt(reason, model, error) {
  return { halted: true, reason, diag: { model, error: errStr(error) } };
}
function classifyThrown(e) {
  return isQuotaError(e) ? "model_unavailable" : "agent_error";
}
function checkImplStatus(impl, allowed = ["ok", "done_with_concerns"], reasonTemplate = "implementor {status}") {
  if (impl.halted) return impl;
  if (!allowed.includes(impl.status)) {
    return { halted: true, reason: reasonTemplate.replace("{status}", impl.status), diag: impl.diagnostics };
  }
  return null;
}
function haltLikelySource(reason) {
  const r = String(reason || "");
  if (r.includes("head restore")) return "gate head mismatch";
  if (r === "plan gate failed" || r.includes("gate")) return "gate restored";
  if (r.startsWith("bootstrap ")) return "bootstrap frontmatter";
  if (r === "plan lint failed") return "planParser frontmatter";
  const implReasons = /* @__PURE__ */ new Set([
    "model_unavailable",
    "agent_error",
    "opus BLOCKED",
    "opus BLOCKED after context-fetch",
    "OSCILLATING",
    "review max rounds",
    "commit failed",
    "commit out_of_scope",
    "simplify diff check failed",
    "simplify amend failed",
    "simplify checkout failed",
    "review_empty",
    "review_failed_no_findings",
    "review_concerns_unaddressed",
    "review_concern_confirmed_without_issue",
    "upstream_defect_halt",
    "unverified_critical_halt"
    // Hunter #5 修复（2026-07-19）：unverified+critical 立即 halt
  ]);
  if (implReasons.has(r)) return "implementor changes";
  if (r.startsWith("implementor ")) return "implementor changes";
  return "unknown";
}

// src/agents/dispatch.js
async function safeAgent(prompt, opts) {
  try {
    return await agent(prompt, opts);
  } catch (e) {
    return { status: classifyThrown(e), diagnostics: { error: errStr(e) } };
  }
}
async function dispatchImpl(prompt, opts, model, retryModel = null) {
  let impl;
  try {
    impl = await agent(prompt, { ...opts, model });
  } catch (e) {
    if (isQuotaError(e)) return makeHalt("model_unavailable", model, e);
    return makeHalt("agent_error", model, e);
  }
  if (impl?.status === "needs_audit_fix") return { halted: true, reason: "audit fix needed", diag: { ...impl.diagnostics, audit_reason: impl.audit_reason, taskKey: impl.taskKey } };
  if (impl?.status === "model_unavailable") return { halted: true, reason: "model_unavailable", diag: impl.diagnostics };
  if (impl == null) {
    if (retryModel && retryModel !== model) {
      log(`\u26A0 ${opts?.label || "unknown"}: ${model} returned null (capability failure likely), retry with ${retryModel}`);
      try {
        impl = await agent(prompt, { ...opts, model: retryModel });
        if (impl != null) {
          if (impl?.status === "needs_audit_fix") return { halted: true, reason: "audit fix needed", diag: { ...impl.diagnostics, audit_reason: impl.audit_reason, taskKey: impl.taskKey } };
          if (impl?.status === "model_unavailable") return { halted: true, reason: "model_unavailable", diag: impl.diagnostics };
          return impl;
        }
      } catch (e) {
        if (isQuotaError(e)) return makeHalt("model_unavailable", retryModel, e);
        return makeHalt("agent_error", retryModel, e);
      }
    }
    const nullErr = retryModel ? `agent returned null (quota exhausted or capability failure \u2014 retry with ${retryModel} also exhausted)` : "agent returned null (quota exhausted or capability failure)";
    return { halted: true, reason: "model_unavailable", diag: { model, error: nullErr } };
  }
  return impl;
}

// src/lib/constants.js
var QUOTA_HALT_NOTE = `\u82E5\u9047\u5230 model \u9650\u989D\u8017\u5C3D\uFF08quota/rate-limit/429 \u9519\u8BEF\uFF09\uFF0C\u8FD4\u56DE status:'model_unavailable'\uFF08\u975E failed\uFF09\uFF0C\u8BA9 orchestrator halt \u5E76\u4FDD\u5B58\u8FDB\u5EA6\u3002`;
function STATIC_READONLY_NOTE(reviewType) {
  return `This is a STATIC READ-ONLY review. You may use 'git diff', 'git status', 'find', 'grep'/'rg', and read files to locate and inspect changes. Do NOT run the test suite, ruff, lint, or any build \u2014 ${reviewType} is done by reading code, not by running it. Running tests/builds is the implementor's and gate's job, not yours. Do NOT run any git write operation that modifies working tree, index, or HEAD (checkout/reset/clean/commit/revert/amend) \u2014 your checkout is read-only.`;
}
var AUDIT_REFACTOR_KEYWORDS = /(替换|去重|抽取|行为不变|逐字对齐|N 处可替换|refactor|extract)/i;
var AUDIT_DIRECTIVE = `## Pre-RED Audit\uFF08\u6B64 task \u6807\u8BB0\u4E3A refactor \u7C7B\uFF09
\u5728\u5199 RED \u6D4B\u8BD5\u4E4B\u524D\uFF0C\u5148\u7528\u5DE5\u5177\u6838\u67E5 brief \u5BF9\u73B0\u72B6\u4EE3\u7801\u7684\u5047\u8BBE\u3002\u5BF9\u4E0B\u8868\u6BCF\u9879\u6267\u884C\u6838\u67E5 + \u586B\u300C\u5B9E\u9645\u300D\uFF0C\u4EA7\u51FA\u5199\u5230 .audit/<taskKey>.md\uFF08\u8986\u76D6\u5199\u5165\uFF1B\u82E5 AUDIT \u9002\u7528\u4F46\u62A5\u544A\u7F3A\u5931\uFF0C\u4E0D\u5F97\u8FDB\u5165 RED\uFF09\uFF1A

| \u9879 | \u6838\u67E5\u52A8\u4F5C\uFF08\u987B\u7528\u6307\u5B9A\u5DE5\u5177\uFF09 | \u4EC0\u4E48\u7B97\u5DEE\u5F02 |
|---|---|---|
| A1 site \u6570 | \u7528 Grep \u5DE5\u5177\u7CBE\u786E\u641C\u7D22 brief \u58F0\u79F0\u7684 pattern\uFF08\u5982 Grep "bareTaskId" \u5728\u76EE\u6807\u6587\u4EF6\uFF09\uFF0C\u6570\u5B9E\u9645\u547D\u4E2D | brief \u8BF4 N \u5904\uFF0C\u5B9E\u9645 M \u5904\uFF08M\u2260N\uFF09\u2192 \u5DEE\u5F02 |
| A2 \u6587\u672C\u4E00\u81F4 | \u7528 Read \u5DE5\u5177\u8BFB\u53D6\u5404 site \u540E diff \u5F85\u53BB\u91CD\u6587\u672C | \u591A\u7C7B\u53D8\u4F53 \u2192 \u5DEE\u5F02 |
| A3 \u63A7\u5236\u6D41 | \u5217\u51FA\u91CD\u6784\u6D89\u53CA\u7684\u63A7\u5236\u6D41\u5173\u952E\u8DEF\u5F84\uFF08if/return/continue/break/\u77ED\u8DEF/await \u987A\u5E8F\uFF0C\u6216\u88AB\u8C03\u51FD\u6570\u8FD4\u56DE\u503C\u5F71\u54CD\u5206\u652F\uFF09\uFF0Ctrace \u91CD\u6784\u524D\u540E\uFF1B**\u7528 Read \u5DE5\u5177\u8BFB\u53D6\u88AB\u8C03\u51FD\u6570\u5B9A\u4E49\u5E76\u6458\u5F55\u76F8\u5173\u6CE8\u91CA**\u3002**\u4E0D\u7BA1\u5224\u65AD\u4E00\u81F4\u4E0E\u5426\uFF0CA3 \u63A8\u7406\u8FC7\u7A0B\u5FC5\u987B\u5199\u8FDB\u62A5\u544A\uFF08\u542B brief \u58F0\u660E + \u6CE8\u91CA\u6458\u8981 + \u4F60\u7684\u5224\u65AD\uFF09** | brief \u58F0\u660E\u7684\u63A7\u5236\u6D41\u4E0E\u5B9E\u9645\u4E0D\u7B26 \u2192 \u5DEE\u5F02 |
| A4 \u884C\u53F7/\u7B7E\u540D | \u7528 Grep \u641C\u7D22 brief \u63D0\u5230\u7684\u51FD\u6570\u540D/\u7B7E\u540D\uFF0C\u6838\u5BF9\u884C\u53F7 + \u53C2\u6570 | \u4EC5\u884C\u53F7\u6F02\u79FB \u2192 \u65E0\u5BB3\uFF08\u8BB0\u5F55\u5373\u53EF\uFF09\uFF1B\u7B26\u53F7\u4E0D\u5B58\u5728 \u2192 \u7F3A\u9677\uFF08\u6309 A1 \u5904\u7406\uFF09 |
| A5 \u5B57\u9762\u91CF | \u7528 Read \u5DE5\u5177\u63D0\u53D6 brief \u7ED9\u7684\u76EE\u6807\u5B57\u9762\u91CF\uFF08reason/diag/string\uFF09\uFF0C\u4E0E\u73B0\u72B6\u5BF9\u5E94\u5B57\u6BB5 diff | \u5B57\u9762\u91CF\u4F4D\u7F6E/\u5185\u5BB9\u4E0D\u7B26 \u2192 \u5DEE\u5F02 |

\u5DE5\u5177\u7EA6\u675F\uFF1A\u5FC5\u987B\u4F7F\u7528 Grep\uFF08\u7CBE\u786E\u641C\u7D22\uFF09\u548C Read\uFF08\u8BFB\u51FD\u6570\u5B9A\u4E49\uFF09\uFF1B\u4E0D\u5F97\u7528 shell \u505A\u5B57\u7B26\u4E32\u5904\u7406\uFF08\u8DE8\u5E73\u53F0/\u5B89\u5168\uFF09\u3002

\u5DEE\u5F02\u5206\u7EA7\u54CD\u5E94\uFF1A
- \u65E0\u5DEE\u5F02 / \u4EC5 A4 \u884C\u53F7\u6F02\u79FB \u2192 \u8FDB RED\u3002
- A1/A2/A5 \u5DEE\u5F02\u4E14\u4F60\u80FD\u5224\u5B9A\u4E3A\u300C\u6709\u610F\u53D8\u4F53\u300D\u2014\u2014**\u5FC5\u987B\u6709\u8BC1\u636E**\uFF08\u7528 Read \u8BFB\u5230\u7684 schema \u5B57\u6BB5/\u6CE8\u91CA/\u4EE3\u7801\u903B\u8F91\uFF0C\u80FD\u89E3\u91CA\u4E3A\u4F55 brief \u7B80\u5316\u8BF4\u6CD5\u4E0E\u73B0\u72B6\u4E0D\u4E00\u81F4\u4F46\u4ECD\u5408\u7406\uFF1B\u4EC5\u51ED\u611F\u89C9\u4E0D\u7B97\uFF09\u2192 \u62A5\u544A\u6807\u6CE8\u300C\u6709\u610F\u53D8\u4F53 + \u8BC1\u636E\u300D\u2192 \u8FDB RED\u3002
- A1/A2/A3/A5 \u5DEE\u5F02\u4E14\u5224\u5B9A\u4E3A\u300Cbrief \u7F3A\u9677\u300D\u2192 STOP\uFF0Cstatus='needs_audit_fix'\uFF0Cdiag \u542B audit_reason='brief_defect' + \u5DEE\u5F02\u6E05\u5355\u3002
- \u62FF\u4E0D\u51C6\u662F\u6709\u610F\u53D8\u4F53\u8FD8\u662F\u7F3A\u9677 \u2192 STOP\uFF0Cstatus='needs_audit_fix'\uFF0Caudit_reason='intentional_variant_unclear'\uFF08\u62FF\u4E0D\u51C6\u65F6\u963B\u65AD\u6BD4\u5F3A\u884C\u5B9E\u73B0\u5B89\u5168\uFF09\u3002
- \u5DE5\u5177\u6267\u884C\u5931\u8D25\uFF08Grep/Read \u62A5\u9519\uFF09\u6216 .audit/ \u5199\u5165\u5931\u8D25 \u2192 STOP\uFF0Cstatus='needs_audit_fix'\uFF0Caudit_reason='tool_failure'\uFF08\u65E0\u6CD5\u6838\u67E5\u65F6\u4E0D\u80FD\u76F2\u8DD1 RED\uFF09\u3002
\u56DE\u4F20\uFF1Astatus='needs_audit_fix' \u65F6\uFF0Cresponse \u9876\u5C42\u987B\u5E26 taskKey \u5B57\u6BB5\uFF08plan-scoped key\uFF0C\u5982 plan-04/T4\uFF0C\u4E0E\u5199\u5165 .audit/<taskKey>.md \u7684\u8DEF\u5F84\u540C\u6E90\uFF09\uFF0C\u4F9B blocked.md \u5B9A\u4F4D\u62A5\u544A\u3002`;

// src/prompts/templates/bootstrap.md
var bootstrap_default = "You are the BOOTSTRAP agent for the workflow orchestrator. Read project state and return structured data. You MAY write YAML frontmatter to plan files that lack it (idempotent). Modify no other files.\n\nInputs: configPath={{configPath}} plansDir={{plansDir}} runTs={{runTs}}\n\nSteps:\n1. Read {{configPath}} \u2192 {test_command, full_test_command, build_command, lint_command, extra_lint_commands, spec_path, reference_paths, language, silent_failure_context, silent_failure_intro, lessons_path, smoke_command}. extra_lint_commands / reference_paths / silent_failure_context / silent_failure_intro / lessons_path / smoke_command are OPTIONAL (may be absent \u2192 treat as [] / [] / [] / '' / '' / ''). If config contains lessons_path, read that file. Extract all entries as all_lessons: [{id, title, detail, category, source}].   - category inference: if an entry lacks category, infer from title/detail. If it is clearly about silent-failure/savepoint/transaction/datetime/null/empty/etc., set category='silent-failure'; otherwise set category='other' or infer a domain category.   - source: include the source location if known (e.g., 'plan-06/T1' or lessons.md filename), otherwise empty string.   Return matched lessons per task in evidence as task_lessons (backward-compatible keyword matching, same shape as before): [{task_id, plan_seq, lessons:[{id, title, detail}]}].   Additionally, return all_lessons: the full list of all lessons parsed from lessonsPath as [{id, title, detail, category, source}] (include category field even if inferred). This feeds v3 two-tier injection (Tier 1 silent-failure always + Tier 2 domain by category). Absent lessons_path \u2192 both arrays empty.\n2. Config smoke: run test_command with --collect-only. \u5224\u65AD\uFF1A\u547D\u4EE4\u672C\u8EAB\u4E0D\u5B58\u5728\uFF08command not found / No such file: pytest\uFF09\u2192 status=failed\uFF08\u73AF\u5883/typo\uFF09\uFF1B\u547D\u4EE4\u5B58\u5728\u4F46 collect \u5931\u8D25\uFF08no module named pytest / pyproject.toml \u4E0D\u5B58\u5728 / no tests collected / \u4E1A\u52A1\u4EE3\u7801\u672A\u521D\u59CB\u5316\uFF09\u2192 \u8BB0\u5F55 'project not yet initialized' \u5230 summary\uFF0Cstatus \u4ECD ok\uFF08\u4E1A\u52A1\u4EE3\u7801\u7531\u540E\u7EED task \u521B\u5EFA\uFF0C\u9884\u671F\uFF09\u3002\u82E5\u914D\u7F6E\u542B smoke_command\uFF1A\u4E0D\u6267\u884C\u5B83\uFF08\u53EF\u80FD\u8D77\u670D\u52A1\uFF09\uFF0C\u4EC5\u68C0\u67E5\u5176\u9996\u4E2A token \u7684\u53EF\u6267\u884C\u6027\uFF08`which` \u6216\u7B49\u4EF7\uFF09\uFF1B\u4E0D\u5B58\u5728 \u2192 \u8BB0 warning \u5230 summary\uFF08\u4E0D fail\u2014\u2014smoke \u662F\u53EF\u9009\u589E\u5F3A\uFF0C\u5176\u914D\u7F6E\u9519\u8BEF\u4E0D\u5E94\u963B\u585E workflow\uFF0Cgate \u4F1A\u4EE5\u771F\u5B9E\u5931\u8D25\u515C\u5E95\uFF09\u3002\n3. git log \u2192 \u8FD0\u884C git log --format=%s -n 200\uFF0C**\u539F\u6837\u590D\u5236**\u6BCF\u4E2A commit subject \u7B2C\u4E00\u884C\u5230 git_log_subjects\u3002\u540C\u65F6\u8FD0\u884C git rev-parse HEAD \u2192 current_head_sha\uFF08plan \u5FAA\u73AF\u5F00\u59CB\u524D\u7684 HEAD SHA\uFF0C\u4F9B\u5BBD\u5BA1 diff \u57FA\u7EBF\u4F7F\u7528\uFF09\u3002**\u4E0D\u8981\u89E3\u6790\u3001\u63D0\u53D6\u3001\u8F6C\u6362\u3001\u8FC7\u6EE4\u3001\u53BB\u91CD** git_log_subjects\u2014\u2014orchestrator \u7528\u6B63\u5219\u4ECE subjects \u786E\u5B9A\u6027\u63D0\u53D6 completed\uFF0C\u4F60\u53EA\u8D1F\u8D23\u5FE0\u5B9E\u590D\u5236 git log \u8F93\u51FA\u3002\n4. git status --porcelain \u2192 dirty_tree. If dirty_tree=true (uncommitted changes from a crashed previous run, \xA76.2 \u534A\u63D0\u4EA4\u81EA\u6108), classify and handle each change (W1-1/W1-4, 2026-07-07):\n   a. Workflow artifact changes:\n      - lessons.md (path = lessons_path read in step 1) has changes \u2192 git commit -m \"chore(workflow): auto-commit lessons.md from interrupted run\" <lessonsPath> (preserve knowledge base, best-effort; H-F2 2026-07-07: \u7528 git commit <path> \u4E00\u6B65\u5230\u4F4D\u4E0D\u9884 staged, \u9632 add \u6210\u529F commit \u5931\u8D25\u540E 5b reset --hard \u6E05\u9664 staging).\n      - runs/ and .workflow/ changes \u2192 git checkout -- runs/ .workflow/ (discard, regenerable).\n   b. Remaining changes = implementor half-done \u2192 git reset --hard HEAD to clean.\n   c. Re-run git status --porcelain to confirm clean; set dirty_tree=false in evidence.\n   d. If any step fails \u2192 leave dirty_tree=true and record the error in summary.\n   Rationale: old logic ran git reset --hard HEAD unconditionally, discarding lessons.md updates (knowledge base). New logic preserves lessons.md, discards regenerable artifacts, cleans implementor half-done code.\n5. For each leaf task return its model (sonnet|opus|undefined\u2192sonnet) and title (the description text from the Task header).\n6. If runs/ directory exists: scan runs/*/manifest.json files. For each, read per_task object. For each task_id in per_task that has blocked_info, extract {task_id, plan_seq (the plan sequence this task belongs to, from the task_id prefix 'plan-<seq>/T-Y' or from the plan context), reason (from blocked_info.reason), error (from blocked_info.last_error)}. Filter to task_ids that match leaf tasks in the current plans. Return as failed_approaches in evidence. Also check if any task has status='in_progress' \u2192 in_progress=true (else false). If runs/ does not exist \u2192 failed_approaches=[], in_progress=false.\n\nReturn {status, evidence:{config, completed, git_log_subjects, current_head_sha, dirty_tree, in_progress, failed_approaches, all_lessons}, summary}.\nRED FLAG: evidence \u5FC5\u987B\u662F\u771F\u5B9E\u8BFB\u53D6\u7ED3\u679C\uFF0C\u7EDD\u4E0D\u7F16\u9020\u3002";

// src/prompts/templates/implementor.md
var implementor_default = "You are the IMPLEMENTOR for {{taskId}} (plan {{planId}}). TDD strict (RED\u2192GREEN\u2192REFACTOR). {{retryNote}}\n{{auditDirective}}\n## Discipline (HARD REQUIREMENTS \u2014 \u8FDD\u53CD\u4F1A\u5BFC\u81F4 workflow \u72B6\u6001\u6DF7\u4E71)\n- DO NOT run `git commit` or `git add`. Committing is handled by a separate COMMIT agent after review passes.\n- Your job is to write code + tests only. Leave changes in the working tree uncommitted.\n- If you think committing is necessary, report status=blocked instead.\n- When applying a lesson from {{lessons}} to harden code (W1-5e, 2026-07-07), add a comment on the hardened line(s) referencing the lesson id (e.g. `// L-20260701T103320Z: guard null per lesson`). This lets the orchestrator apply Lessons Learned Exemption and not flag your hardening as EXTRA.\n\nInputs: specPath={{specPath}} testCommand={{testCommand}} buildCommand={{buildCommand}} planFile={{planFilePath}} taskId={{taskId}} fixIssues={{fixIssues}}\n{{constraintsNote}}\n{{interfacesNote}}\n{{planLintNote}}\n{{referencePaths}}\n{{failedApproaches}}\n{{lessons}}\n{{fetchedContext}}\n\nSteps:\n1. Read {{planFilePath}}, locate {{taskId}} section: files to create/modify, tests to write. For a large file, first run `grep -n '^#' {{planFilePath}}` to get the section index, then read only your task's section.\n2. Read {{specPath}} relevant section (large file: first `grep -n '^#' {{specPath}}` for the section index); implement to spec. If reference documents are listed above, read the relevant rule section BEFORE writing domain-specific logic.\n3. RED: write ONE minimal failing test for one behavior. Run {{testCommand}}; CONFIRM it fails \u2014 and fails for the RIGHT reason (feature missing), not a typo/import error. A test that passes immediately proves nothing (you may be testing existing behavior) \u2014 fix the test. Record this failing output verbatim (truncate to \u2264500 chars) into evidence.red_phase_output \u2014 the reviewer verifies a behavioral initial implementation really went through RED.\n4. GREEN: minimal code to pass the test. Don't add features or refactor beyond the test. If {{buildCommand}} is non-empty, run it before tests to verify the project builds.\n5. REFACTOR: clean up (dedupe, better names, extract helpers). Tests stay green.\n6. Self-review (see checklist below).\n7. Run {{testCommand}}; record pytest summary + exit code. If fixIssues non-empty, this round fixes them (review findings from reviewer/hunter). If fetchedContext non-empty, it is REFERENCE MATERIAL to read \u2014 do NOT modify or \"fix\" it; use it to unblock.\nIn your evidence, return lesson_ids_used: an array of L-xxx IDs you referenced in code comments (e.g., ['L-20260701T103320Z']). If you didn't reference any lessons, return empty array.\n\n## Good Tests\n- One behavior per test (\"and\" in the name \u2192 split it)\n- Clear name describing behavior\n- Real code, not mocks (unless unavoidable)\n- Name the break: for each test ask \"would this go red if the behavior broke?\" \u2014 an always-true assertion is not a test\n- Assert behavior contract, not implementation: no mirror assertions (expected copied from code under test), no change detectors (locks incidental internals)\n\n## Self-Review Checklist (before reporting)\n- Completeness: every spec requirement implemented? edge cases handled? nothing missed?\n- Quality: best work? names match what things do? clean & maintainable?\n- Discipline: avoided overbuilding (YAGNI)? built only what was requested? followed existing patterns?\n- Testing: tests verify real behavior (not mock behavior)? comprehensive?\n\n## 6-Dimension Quick Check (before reporting)\n- Cognitive Overload: any function > 50 lines or nesting > 3 levels?\n- Change Propagation: did you change files unrelated to this task?\n- Knowledge Duplication: did you paste similar logic in 2+ places?\n- Accidental Complexity: did you add abstraction not needed by current requirements?\n- Dependency Disorder: any business layer importing infrastructure implementation?\n- Domain Distortion: are variable names domain terms, not generic (data/item/info)?\n\n## Concern Triggers (when to report done_with_concerns instead of ok)\nSelf-review can fix most issues. But some doubts CANNOT be resolved by you alone \u2014 they need reviewer judgment or spec clarification. For these, report status=done_with_concerns with a concern per trigger (do NOT silently pick an interpretation and report ok):\n\n- **Spec ambiguity you resolved by choosing one interpretation** (the spec allowed \u22652 readings; you picked one; the other reading might be what was intended).\n- **Security / auth / crypto / secrets** touched and you are not certain the handling is correct.\n- **Data migration / schema change** with an unclear or absent rollback path.\n- **Shared mutable state / concurrency** where ordering or race correctness is uncertain.\n- **Deleted or replaced** existing code and you are unsure whether anything still depends on it.\n- **Edge case the tests do not cover** but you suspect matters (e.g., empty input, very large input, unicode, timezone).\n- **A lesson ({{lessons}}) seemed relevant but you were unsure how to apply it**, or you deliberately did NOT apply one that looked relevant.\n\nThis list is not exhaustive \u2014 any unresolved doubt about correctness or scope is a concern. Reporting a concern is never penalized; a missed real issue is.\n\nIf self-review finds issues you CAN fix, fix them now. If a doubt CANNOT be resolved by code changes alone (it needs reviewer judgment or spec clarification), do not guess \u2014 report it as a concern (see Concern Triggers) and still finish the fixable parts.\n\nReturn {status, evidence:{tests_exit_code, files_changed:[...], pytest_summary, red_phase_output, lesson_ids_used:[...]}, diagnostics:{blocked_category, last_error, suggested_fix, concerns} (diagnostics only if blocked/done_with_concerns), summary}.\n- status=ok: done, tests_exit_code=0. MUST provide evidence with real tests_exit_code / files_changed / pytest_summary.\n- status=done_with_concerns: done (tests green) but you have unresolved doubts about correctness/scope \u2192 fill diagnostics.concerns: [{severity, text}]. severity guide: critical = may break correctness/security/data; important = may not match spec intent or a likely real edge case; minor = code-smell-level doubt. MUST provide evidence as in ok.\n- status=failed: tests failed after retry. evidence is OPTIONAL (record real tests_exit_code if available); diagnostics may contain last_error/suggested_fix.\n- status=blocked: \u969C\u788D (interface|file|spec|dependency|external) \u2192 fill diagnostics. evidence is OPTIONAL (no real test run).\n- status=needs_context: missing info \u2192 fill diagnostics.blocked_category + last_error. evidence is OPTIONAL.\nRED FLAG: tests_exit_code \u5FC5\u987B\u771F\u5B9E\uFF0C\u7EDD\u4E0D\u7F16\u9020 0\u3002\u7EDD\u4E0D\u8DF3\u8FC7\u6D4B\u8BD5\u3002\u9047\u969C\u788D\u5B81\u53EF blocked \u4E5F\u4E0D\u8981\u4F2A\u9020\u901A\u8FC7\u3002\u82E5\u9047\u5230 model \u9650\u989D\u8017\u5C3D\uFF08quota/rate-limit/429 \u9519\u8BEF\uFF09\uFF0C\u8FD4\u56DE status:'model_unavailable'\uFF08\u975E failed/blocked\uFF09\uFF0C\u8BA9 orchestrator halt \u5E76\u4FDD\u5B58\u8FDB\u5EA6\u3002";

// src/prompts/templates/reviewer.md
var reviewer_default = "You are the REVIEWER (model opus). Verify implementor built EXACTLY what was requested AND code quality is acceptable. Verdict on CURRENT working tree (HEAD or staged).\n\nInputs: specPath={{specPath}} taskId={{taskId}} planFile={{planFilePath}} changedHint={{filesChanged}}\n{{concernsHint}}\n{{constraintsNote}}\n{{interfacesNote}}\n{{implementorEvidenceNote}}\n{{referencePaths}}\n{{languageChecklist}}\n{{applicableStandardsNote}}\nlessonsPath={{lessonsPath}}\n\n## Task Scope Boundary (critical for multi-task plans)\nEach task implements ONLY what its plan section requests. Methods/interfaces/fields needed by FUTURE tasks (documented in DESIGN.md or spec but NOT in this task's plan section) are NOT missing \u2014 they belong to their respective future tasks. Do not flag them as MISSING.\nConversely, if the implementor adds methods/tests NOT in this task's plan section, that IS over-build \u2014 flag it as EXTRA.\n\n## \u26A0\uFE0F Cannot-Verify Channel\nFor suspicions that live OUTSIDE this task's diff scope (in unchanged code, or crossing into another task's territory): do NOT flag them \u274C and do NOT expand your search. Report them as issues with confidence='unverified' (+ your best severity guess). The orchestrator defers them to the broad reviewer, who sees the full plan diff. An \u26A0\uFE0F entry alone does not require status=failed \u2014 if your only findings are unverified, status=ok with the \u26A0\uFE0F entries recorded is correct.\n\n## Review Discipline\n- Do NOT trust the implementor's self-report. Read the actual code and verify independently.\n- If the implementor claims \"done\" or \"fixed\", verify the code actually reflects the claim.\n- Rationale from the implementor never downgrades severity \u2014 if you find a real issue, the implementor's explanation does not make it less severe.\n- When in doubt, flag it. False negatives are worse than false positives \u2014 a flagged issue can be discussed, a missed one ships.\n\n## Checks\n\n### Spec Compliance (MISSING / EXTRA / MISUNDERSTANDING)\n1. MISSING requirements: anything in spec not implemented? claimed-working but not actually done?\n2. EXTRA / over-build (YAGNI): anything built that spec did NOT request? unrequested features, over-engineering, \"nice to haves\"? This is critical \u2014 flag any functionality the spec forbids or didn't ask for.\n3. MISUNDERSTANDING: requirement interpreted differently than intended? right feature wrong way?\n\n### Implementor Concerns (MANDATORY \u2014 address each by idx)\nThe Implementor Concerns section above lists doubts the implementor flagged.\nFor EACH concern (by its [idx]), return a concerns_addressed entry:\n- {idx, verdict: 'confirmed', note}: you verified it IS a real problem \u2192 you MUST also add a matching issue with concern_idx=idx.\n- {idx, verdict: 'dismissed', note}: you verified it is NOT a problem (explain why in note).\n- {idx, verdict: 'fixed', note}: the concern is real but already resolved by this change \u2192 add a matching issue with concern_idx=idx noting it is resolved.\nIf there are NO Implementor Concerns above, omit concerns_addressed entirely.\nLeaving any concern unaddressed, or marking confirmed/fixed without a matching issue (concern_idx), FAILS the review.\n\n### Code Quality\n- \u51FD\u6570 <50 \u884C, \u6587\u4EF6 <800 \u884C, \u65E0\u6DF1\u5C42\u5D4C\u5957 (>4), \u9519\u8BEF\u663E\u5F0F\u5904\u7406, \u65E0 mutation, \u65E0\u786C\u7F16\u7801\u503C, \u547D\u540D\u6E05\u6670.\n- Each file has one clear responsibility; units decomposed so they can be tested independently.\n- Did this change create new large files or significantly grow existing ones? (Don't flag pre-existing sizes \u2014 focus on what this change contributed.)\n- Architectural discipline like layer-purity is enforced automatically by the gate's lint commands \u2014 focus on code a human must judge; do NOT invent layer rules not in the checklist.\n\n### Test Evidence & Effectiveness\n- RED evidence (see Implementor Evidence above): if Stage is 'initial implementation' AND this is a behavioral task AND red_phase_output is '(not provided)' \u2192 report minor. Fix rounds never require RED evidence. \"Behavioral\" = the applicableStandardsNote above does NOT contain any of refactor / docs-only / config-only / test-only (absent section \u2192 treat as behavioral).\n- Pristine test output: pytest_summary contains warnings (DeprecationWarning/UserWarning/PytestWarning etc.) \u2192 report minor.\n- Tautological test: a test that passes regardless of implementation (asserts only mock behavior, or its assertion is always true) \u2192 report minor.\n- Mirror assertion: expected value copied from the implementation's literal instead of derived from spec/behavior \u2192 report minor. Example: `assertEqual(calc(x), 42)` where `42` was copied from the code under test rather than derived from the spec's expected output for input `x`.\n- Change detector: test locks incidental implementation detail (snapshot of internals, exact call sequence) rather than observable behavior \u2192 report minor. Example: asserting a function calls three helpers in a specific order, or snapshotting a private data structure's shape, rather than asserting the observable return value.\n- Lesson usage honesty: if lesson_ids_used lists L-xxx IDs (see Implementor Evidence above), spot-check the diff for the corresponding `// L-xxx` comments AND verify the related code has actual changes (not just a comment). claimed-but-absent \u2192 report minor.\n\n## Steps\n1. git diff (or read changed files) for this task.\n2. Read {{specPath}} section governing {{taskId}} (large file: first `grep -n '^#' {{specPath}}` for the section index, read only the relevant section). If reference documents are listed above, verify domain-specific logic and rules match them exactly.\n3. Verify spec compliance (MISSING/EXTRA/MISUNDERSTANDING) \u2014 don't trust the implementer report, read the actual code.\n4. Check code quality (architecture, boundaries, types, error handling, naming) per the checklist above.\n5. Record files_touched (files in the diff).\n\n## Calibration\nCategorize issues by ACTUAL severity \u2014 not everything is Critical. Acknowledge what was done well (strengths) before listing issues; accurate praise helps the implementer trust the rest.\n\n{{staticReadonlyNote}}\n\nReturn {status (ok|failed), diagnostics:{files_touched:[...], issues:[{dimension?: MISSING|EXTRA|MISUNDERSTANDING, severity: critical|important|minor, confidence?: 'unverified', certainty?: high|medium|low, ownership?: local|upstream|unclear, title, file, fix, concern_idx?}], concerns_addressed:[{idx, verdict, note?}]}, summary}.\nissues \u5143\u7D20 MUST \u662F object \u4E14\u5FC5\u6709 title + fix\uFF08severity/file \u4EA6\u5EFA\u8BAE\uFF09\u2014\u2014\u7EAF\u5B57\u7B26\u4E32\u6216\u7F3A title/fix \u7684\u5BF9\u8C61\u4F1A\u88AB schema \u62D2\u7EDD\u3002\ncertainty\uFF1A\u4F60\u5BF9\u8BE5 finding \u7684\u786E\u4FE1\u5EA6\u81EA\u8BC4\uFF08low = \u63A8\u6D4B/\u672A\u5B8C\u5168\u6838\u5B9E\uFF09\u2014\u2014orchestrator \u540E\u7F6E\u89C4\u5219\u4F1A\u964D\u7EA7\u4F4E\u7F6E\u4FE1\u9879\uFF08low+important\u2192minor\uFF09\uFF0Clow+critical \u4FDD\u7559\u4F46\u6807\u6CE8\u300C\u6838\u5B9E\u540E\u518D\u6539\u300D\u3002\u52FF\u4E3A\u9AD8 severity \u51D1\u6570\u800C\u62D4\u9AD8 certainty\u3002\nownership\uFF1A\u6839\u56E0\u5C5E\u4E8E\u4E0A\u6E38/\u5171\u4EAB\u5E73\u53F0\uFF08\u672C\u4ED3\u5E93\u4EE3\u7801\u4FEE\u4E0D\u4E86\uFF09\u2192 'upstream'\uFF08upstream+critical \u4F1A halt \u800C\u975E\u672C\u5C42\u6253\u8865\u4E01\uFF09\uFF1B\u4E0D\u786E\u5B9A \u2192 'unclear'\uFF08orchestrator \u6309 local \u5904\u7406\uFF09\uFF1B\u672C\u5C42\u95EE\u9898\u7701\u7565\u6216 'local'\u3002\nRED FLAG: ok \u4EC5\u5F53\u65E0 critical/important \u95EE\u9898 AND spec \u4E09\u7EF4\u5EA6\u5168\u6E05\u3002critical/important\uFF08\u67B6\u6784/\u5B89\u5168/\u6B63\u786E\u6027/spec \u4E0D\u7B26\uFF09\u5FC5\u987B failed\uFF1B\u4EC5 minor \u53EF ok\uFF08\u8BB0\u5165 issues\uFF09\u3002\u7EDD\u4E0D\u6A21\u7CCA\u901A\u8FC7\u3002\u8D8A\u754C\uFF08spec \u672A\u8981\u6C42\u7684\u529F\u80FD\uFF0C\u5C24\u5176\u662F\u5408\u89C4\u7EA2\u7EBF\u7981\u6B62\u7C7B\uFF09\u5FC5\u987B failed\u3002issues \u8981\u5177\u4F53\uFF08title \u5199\u54EA\u6761 spec + \u4EE3\u7801\u54EA\u91CC\u4E0D\u7B26/\u8D8A\u754C\uFF0Cfile \u5199 file:line\uFF0Cfix \u5199\u4FEE\u6CD5\uFF09\u3002{{quotaHaltNote}}\n";

// src/prompts/templates/hunter.md
var hunter_default = `You are the SILENT-FAILURE-HUNTER. Hunt swallowed errors, bad fallbacks, missing error propagation, swallowed exceptions, except:pass, broad except hiding bugs, default values masking failures. Verdict on CURRENT tree.

Inputs: taskId={{taskId}} changedHint={{filesChanged}}
{{silentFailureContext}}

## Review Discipline
- Do NOT trust the implementor's self-report. Read the actual code and verify independently.
- If the implementor claims "done" or "fixed", verify the code actually reflects the claim.
- Rationale from the implementor never downgrades severity \u2014 if you find a real issue, the implementor's explanation does not make it less severe.
- When in doubt, flag it. False negatives are worse than false positives \u2014 a flagged issue can be discussed, a missed one ships.

This is a STATIC READ-ONLY hunt: do not run tests/builds, and do NOT run any git write operation (checkout/reset/clean/commit) \u2014 inspect the current tree only.

{{applicableStandardsNote}}

Steps:
1. Read changed files.
2. If project-specific silent-failure risks are listed above, hunt those FIRST (they are this system's known fatal traps) \u2014 then hunt the generic patterns below.
3. Find:
   - try/except that pass or log-only; bare except hiding bugs; errors converted to null/empty with no context
   - fallback returning wrong-type default; default values masking real failure; .catch(() => [])
   - unhandled None; ignored return values; missing await; fire-and-forget without error path
   - network/file/db paths with NO timeout or error handling
   - transactional work with no rollback on failure
   - lost stack traces (rethrow without context); generic rethrows
   - logs with wrong severity / log-and-forget (no handling after logging)
4. Record files_touched.

This is a STATIC READ-ONLY review. You may use 'git status', 'git diff', 'find', 'grep'/'rg', and read files to locate patterns and inspect code. Do NOT run the test suite, ruff, lint, or any build \u2014 silent-failure hunting is done by reading code, not by running it. Running tests/builds is the implementor's and gate's job, not yours.

Return {status (ok|failed), diagnostics:{files_touched:[...], silent_failures:[{title, severity (critical|important|minor), file, line?, fix}]}, summary}.
silent_failures \u5143\u7D20 MUST \u662F object\uFF08\u5FC5\u6709 title + fix\uFF1Bfile \u5F3A\u70C8\u5EFA\u8BAE\uFF1Bseverity \u53EF\u9009\u9ED8\u8BA4 important\uFF09\u2014\u2014\u7EAF\u5B57\u7B26\u4E32\u6216\u4E0D\u5E26 fix \u7684\u5BF9\u8C61\u4F1A\u88AB schema \u62D2\u7EDD\u3002
RED FLAG: \u53EA\u62A5\u771F\u6B63\u7684\u9759\u9ED8\u5931\u8D25\uFF08\u4F1A\u5BFC\u81F4 bug \u88AB\u9690\u85CF\uFF09\uFF0C\u4E0D\u62A5\u523B\u610F\u7684\u4F18\u96C5\u964D\u7EA7\uFF08\u6709\u65E5\u5FD7+\u5408\u7406 fallback\uFF09\u3002
**STATUS DETERMINATION (HARD RULE \u2014 2026-07-07 W3): silent_failures \u6570\u7EC4\u975E\u7A7A \u2192 status=failed\uFF1Bsilent_failures \u6570\u7EC4\u4E3A\u7A7A \u2192 status=ok\u3002severity \u4E0D\u5F71\u54CD status \u5224\u5B9A\u2014\u2014critical/important/minor \u4EFB\u4E00 finding \u90FD\u89E6\u53D1 failed\u3002\u7981\u6B62"\u62A5\u4E86 finding \u5374 status=ok"\u7684\u77DB\u76FE\u8F93\u51FA\u3002**
**\u4F18\u96C5\u964D\u7EA7\u5224\u5B9A\uFF08\u4E0E\u9759\u9ED8\u5931\u8D25\u7684\u533A\u5206\uFF09**\uFF1A\u523B\u610F\u7684\u4F18\u96C5\u964D\u7EA7\u987B\u540C\u65F6\u6EE1\u8DB3\uFF1A\u2460 \u6709\u663E\u5F0F\u65E5\u5FD7\uFF08log.warning/error\uFF0C\u975E\u6CE8\u91CA/print \u5230 stdout\uFF09\uFF1B\u2461 fallback \u503C\u7C7B\u578B\u6B63\u786E\u4E14\u5BF9\u8C03\u7528\u65B9\u6709\u610F\u4E49\uFF08\u5982 1.0x \u4E58\u6570\u4FDD\u7559\u57FA\u7840\u91D1\u989D\uFF09\u3002\u4E24\u8005\u7F3A\u4E00\u5373\u4E3A\u9759\u9ED8\u5931\u8D25\u3002\u4F8B\u5982 "if not mapping: return 1.0" \u65E0\u65E5\u5FD7 \u2192 \u9759\u9ED8\u5931\u8D25\uFF08\u975E\u4F18\u96C5\u964D\u7EA7\uFF09\u3002{{quotaHaltNote}}`;

// src/prompts/templates/simplify.md
var simplify_default = `You are SIMPLIFY. Reduce code: dedupe, remove dead code, tighten naming, lower complexity. Behavior MUST be preserved (tests still pass).

Inputs: taskId={{taskId}} filesChanged={{filesChanged}}

## Scope
ONLY modify files in filesChanged above. Reading other files for context is OK; editing them is forbidden.

## Principles
1. clarity over cleverness
2. consistency with EXISTING repo style (match surrounding code's conventions)
3. preserve behavior exactly
4. simplify only where the result is demonstrably easier to maintain

## Targets

### Structure
- extract deeply nested logic into named functions
- replace complex conditionals with early returns where clearer
- simplify callback chains with async/await
- remove dead code and unused imports
- UNWIND over-abstracted single-use helpers (collapse back inline if the abstraction serves only one caller)
- altitude alignment: keep each block at one level of abstraction (don't mix high-level orchestration with low-level detail in the same function)

### Readability
- prefer descriptive names; avoid nested ternaries
- break long chains into intermediate vars when clearer
- use destructuring when it clarifies access
- tighten naming

### Quality
- remove commented-out code & stray debug logs
- consolidate duplicated logic

## Forbidden
- Do NOT change public API signatures or export names (renaming "for clarity" breaks callers)
- Do NOT delete defensive/guard code (looks redundant, handles edge cases)
- Do NOT modify test files (tests are the behavior contract)
- Do NOT introduce new dependencies
- Do NOT refactor across files (local simplification within a file OK; cross-file extraction/migration is out of scope)
- When in doubt, do NOT change it \u2014 be conservative

## Steps
1. Read changed files.
2. Apply only safe simplifications (behavior-preserving).
3. Verify behavior preservation BEFORE reporting done:
   - Before deleting any symbol: grep for references across the repo (including dynamic imports) \u2014 confirm zero callers
   - For renames: grep all references updated in lockstep
   - For extracted functions: parameter/return/side-effects match original inline logic
   - For conditional simplification: truth-table equivalence
   - You CANNOT run tests (orchestrator re-runs review); rely on grep + logical equivalence
4. HONESTLY report changed (bool) + files_changed.

Return {evidence:{changed, files_changed:[...]}, summary}.
RED FLAG: changed \u5FC5\u987B\u5982\u5B9E\u3002orchestrator \u7528 git diff --stat \u72EC\u7ACB\u9A8C\u8BC1\u4F60\u662F\u5426\u6539\u4E86\u4EE3\u7801\uFF08\u4E0D\u4FE1\u4EFB\u81EA\u62A5\uFF09\uFF0C\u6709\u6539\u52A8\u5219\u89E6\u53D1 review\u3002`;

// src/prompts/templates/commit.md
var commit_default = `You are COMMIT. Create one atomic commit for task {{taskId}}.

Inputs: taskId={{taskId}} planId={{planId}} testCommand={{testCommand}} commitMsg={{commitMsg}} writeFilesScope={{writeFilesScope}}

## \u63D0\u4EA4\u7EA6\u5B9A\uFF08HARD REQUIREMENT \u2014 \u8FDD\u53CD\u4F1A\u5BFC\u81F4 OSCILLATING halt\uFF09
git \u63D0\u4EA4\u6D88\u606F**\u5FC5\u987B**\u4E25\u683C\u7B49\u4E8E\u4E0B\u9762\u8FD9\u6761\uFF08orchestrator \u5DF2\u6309 feat(plan-XX/TY): title \u683C\u5F0F\u9884\u8BA1\u7B97\u597D\uFF0C\u539F\u6837\u4F7F\u7528\uFF0C\u4E0D\u8981\u6539\u5199 scope\u3001\u4E0D\u8981\u81EA\u62DF\u6807\u9898\uFF09\uFF1A
  {{commitMsg}}
\u7406\u7531\uFF1Abootstrap \u626B git log \u7528\u7EA6\u5B9A feat(plan-XX/TY): \u8BC6\u522B"\u5DF2\u5B8C\u6210 task"\u3002\u4EFB\u4F55\u4ED6\u7C7B scope \u90FD\u4F1A\u8BA9\u8BE5 task \u5BF9 bootstrap \u4E0D\u53EF\u89C1 \u2192 \u88AB\u5224\u672A\u5B8C\u6210 \u2192 \u91CD\u8DD1 \u2192 OSCILLATING halt\u3002
**\u4E25\u7981\u7167\u6284 plan \u6587\u4EF6\u91CC Step 5/8 \u7684\u793A\u610F\u63D0\u4EA4\u6D88\u606F**\uFF08\u5982 feat(scheduler): ... / feat(notifications): ... / \u65E0 scope \u7684 feat: ...\uFF09\u2014\u2014\u90A3\u4E9B\u53EA\u662F\u5199\u6CD5\u7684\u793A\u610F\uFF0C\u4E0D\u662F\u771F\u5B9E\u63D0\u4EA4\u547D\u4EE4\u3002\u672C task \u552F\u4E00\u5408\u6CD5\u7684\u63D0\u4EA4\u6D88\u606F\u5C31\u662F\u4E0A\u9762\u7684 {{commitMsg}}\u3002

Steps:
1. git status --porcelain \u2192 see staged/unstaged.
2. Run {{testCommand}} on current tree; confirm exit 0. If fail \u2192 status=failed (do NOT commit).
2.5. If writeFilesScope is non-empty: run git diff --name-only. Compare with writeFilesScope. If any file is out of scope \u2192 status=failed, diagnostics.out_of_scope=[<files>]. Do NOT commit.
2.6. Destructive Change Detection: run git diff HEAD --numstat. For each file:
  S4\uFF08\u7B2C 4 \u8F6E\uFF09: \u987B\u7528 git diff HEAD\uFF08\u975E git diff --cached\uFF09\u2014\u2014\u6587\u4EF6\u672A git add \u65F6 --cached \u6C38\u8FDC\u4E3A\u7A7A\uFF0C
    destructive review \u6C38\u4E0D\u89E6\u53D1\u3002git diff HEAD \u5BF9\u6BD4\u5DE5\u4F5C\u6811\u4E0E HEAD\uFF0C\u65E0\u9700\u6682\u5B58\u5373\u53EF\u68C0\u6D4B\u6539\u52A8\u3002
  a. If column 2 (deletions) >= 5 AND file is not a test file \u2192 record {type:'deleted_code', file, detail:'<N> lines deleted'}
  b. If file is deleted (git diff HEAD --name-status shows D) \u2192 record {type:'file_deletion', file, detail:'file deleted'}
  c. For exported symbol signature changes: read the diff hunks. If a function/class exported symbol's params or return type changed \u2192 record {type:'signature_change', file, detail:'<symbol> signature changed'}
  If any hit \u2192 record in diagnostics.destructive_changes: [{type, file, detail}]. Still proceed to commit (status=ok), but orchestrator will trigger an extra review round.
3. git add -A; git commit -m "{{commitMsg}}"\u3002
4. **\u5F3A\u5236\u6821\u9A8C + \u7EA0\u504F**\uFF1Agit log -1 --format=%s \u53D6 HEAD \u4E3B\u4F53\uFF0C\u4E0E {{commitMsg}} \u6BD4\u5BF9\u3002\u82E5\u4E0D\u7B26\uFF08\u4EFB\u4F55\u539F\u56E0\u2014\u2014\u6BD4\u5982\u5B9E\u73B0 agent \u4E4B\u524D\u5DF2\u7528\u9519\u8BEF scope \u63D0\u4EA4\u8FC7\u3001\u6216 HEAD \u5DF2\u5B58\u5728\u4F46\u6D88\u606F\u4E0D\u5BF9\uFF09\uFF1Agit commit --amend -m "{{commitMsg}}" \u7EA0\u6B63\u3002\u8FD9\u662F\u786E\u5B9A\u6027\u7684\uFF1A\u65E0\u8BBA\u8C01\u63D0\u4EA4\u3001\u63D0\u4EA4\u4E86\u4EC0\u4E48\uFF0C\u6700\u7EC8 HEAD \u6D88\u606F\u5FC5\u4E3A {{commitMsg}}\u3002
5. git rev-parse HEAD \u2192 commit_sha\u3002

Return {status (ok|failed), evidence:{commit_sha, committed_files:[...], tests_at_commit}, summary}.
RED FLAG: tests exit != 0 \u65F6\u7EDD\u4E0D commit\uFF08status=failed\uFF09\u3002commit_sha \u5FC5\u987B\u771F\u5B9E\u3002HEAD \u6D88\u606F\u5FC5\u987B\u7B49\u4E8E {{commitMsg}}\uFF08\u6B65\u9AA4 4 \u6821\u9A8C\uFF0C\u4E0D\u7B26\u5FC5 amend\uFF09\u3002{{quotaHaltNote}}`;

// src/prompts/templates/context-fetcher.md
var context_fetcher_default = 'You are CONTEXT-FETCHER. The implementor requested context (NEEDS_CONTEXT). Find and return it. Read-only.\n\nInputs: needType={{needType}} query={{query}} specPath={{specPath}} workdir={{workdir}}\n\nSteps by needType:\n- file/path: grep/glob workdir for query, return paths.\n- interface: LSP or regex extract function/class signatures.\n- spec/doc: read {{specPath}} or named doc, extract relevant section.\n- dependency: read prior task code, extract key impl.\n- external: Context7 or WebSearch query.\n- diff_files: run `git diff --name-only {{query}}` (query = SHA range like abc123..def456), return file paths (one per line) as context.\n- head_sha: run `git rev-parse HEAD` and return the SHA string as context.\nReturn {diagnostics:{context: <findings text>}, summary}.\nRED FLAG: context \u5FC5\u987B\u662F\u771F\u5B9E\u67E5\u5230\u7684\uFF0C\u7EDD\u4E0D\u7F16\u9020\u3002\u67E5\u4E0D\u5230 \u2192 context="not found: <query>"\u3002';

// src/prompts/templates/gate.md
var gate_default = 'You are PLAN-GATE. Independently re-run verification on the committed SHA (do NOT trust implementor self-report). Run EVERY command below, record real exit codes. Then restore HEAD.\n\nInputs: sha={{sha}}\nCommands to run (JSON array, in order): {{gateCommands}}\nEach item is {kind: "test"|"lint"|"smoke", command}. kind:"smoke" = runtime liveness probe (e.g. start service + hit health endpoint, or CLI `--help`) \u2014 catches "tests pass but boot crashes". Run ALL of them on the checked-out SHA. If a smoke command hangs, terminate it after ~180 seconds and record a non-zero exit_code.\n{{schemaCheck}}\n\nSteps:\n1. git checkout {{sha}}.\n2. For EACH command in the array: run it, record {command, exit_code, summary}. tests_exit_code = exit code of the FIRST kind:"test" command (0 if none).\n3. git checkout - (restore previous HEAD). CRITICAL: must restore or downstream tasks break.\n4. If step 3 fails, git checkout <previous-branch> explicitly.\n\nReturn {status (ok|failed), evidence:{tests_exit_code, pytest_summary, lint_results:[{command, exit_code}], restored_head}, summary}.\n- restored_head: \u6B65\u9AA4 3/4 \u6062\u590D\u540E\u6267\u884C \'git rev-parse HEAD\' \u7684 40 \u4F4D SHA\uFF0C\u4F9B orchestrator \u9A8C\u8BC1\u57FA\u7EBF\u5DF2\u6062\u590D\u3002\n- status=ok ONLY if EVERY command exit_code == 0 AND restored_head \u975E\u7A7A\u3002\nRED FLAG: every exit_code \u5FC5\u987B\u771F\u5B9E\uFF08\u4F60\u5728 committed SHA \u4E0A\u4EB2\u8DD1\uFF09\u3002\u5FC5\u987B checkout \u56DE\u539F HEAD \u5E76\u8BB0\u5F55 restored_head\u3002\u4EFB\u4E00 exit != 0 \u2192 status=failed\uFF08\u5305\u62EC lint \u547D\u4EE4\u2014\u2014\u67B6\u6784\u7EAA\u5F8B\u5982\u5C42\u7EAF\u5EA6\u7531 lint \u5F3A\u5236\uFF09\u3002{{quotaHaltNote}}';

// src/prompts/templates/head-verifier.md
var head_verifier_default = 'You are HEAD-VERIFIER. Read-only. Run "git rev-parse HEAD" in the repository root and return the current HEAD SHA.\n\nReturn {status:"ok", evidence:{head:"<40-char-sha>"}, summary}.\nRED FLAG: head must be the actual output of git rev-parse HEAD, never fabricated.';

// src/prompts/templates/final-report.md
var final_report_default = `You are FINAL-REPORT (mode={{mode}} done|halted). Write the run manifest (the ONLY on-disk write in this workflow) and emit a digest.

Inputs: mode={{mode}} state={{stateJson}} blockedInfo={{blockedInfo}} runsDir={{runsDir}} runTs={{runTs}} lessonsPath={{lessonsPath}}

Steps:
1. mkdir -p {{runsDir}}.
2. Write {{runsDir}}/manifest.json = {run_ts:{{runTs}}, mode:{{mode}}, plans:[...], per_task:{<taskKey>:{planId,status,model,audit_required,review_rounds,files_touched_per_round,review_history,findings_history,oscillation_escalated_at_round,opus_escalated,commit_sha,simplify_reverted,simplify_review_findings,destructive_review_failed,destructive_review_findings,concerns,blocked_info}}, lessons_committed:false, result}. per_task.<task> \u5FC5\u987B\u4FDD\u7559 stateJson \u4E2D per_task \u7684**\u5168\u90E8\u5B57\u6BB5**\uFF08\u542B v3 \u65B0\u589E\u5B57\u6BB5 findings_history / oscillation_escalated_at_round / opus_escalated\uFF09\uFF0C\u4E0D\u5F97\u4EE5\u6E05\u5355\u672A\u5217\u4E3A\u7531 strip \u4EFB\u4F55\u5B57\u6BB5\uFF1B\u6CE8\uFF1A\u6E05\u5355\u4EC5\u4F5C\u53EF\u8BFB\u8BF4\u660E\uFF0C\u4EE5 stateJson \u5168\u5B57\u6BB5\u4E3A\u51C6\uFF08ensurePerTaskDefaults \u5171 17 \u5B57\u6BB5\uFF1AplanId/status/model/audit_required/review_rounds/files_touched_per_round/review_history/findings_history/oscillation_escalated_at_round/commit_sha/opus_escalated/simplify_reverted/simplify_review_findings/destructive_review_failed/destructive_review_findings/concerns/blocked_info\uFF09\u3002findings_history \u662F findings \u72B6\u6001\u673A\u8F68\u8FF9 [{title, status, first_seen, last_seen, rounds, fixed_at_round}]\uFF1Boscillation_escalated_at_round \u662F opus \u5347\u7EA7\u8F6E round \u6570\u6216 null\uFF1Bopus_escalated \u662F\u5E03\u5C14\u503C\u3002**lessons_committed**\uFF08H-F7, 2026-07-07\uFF09\uFF1A\u5E03\u5C14\u503C\uFF0C\u521D\u59CB false\uFF1Bstep 6 \u6210\u529F commit lessons.md \u540E\u987B\u91CD\u5199 manifest.json \u5C06\u6B64\u5B57\u6BB5\u6539\u4E3A true\u3002\u4F9B\u4E0B\u6B21 bootstrap \u68C0\u67E5 lessons.md \u662F\u5426\u771F\u88AB\u6301\u4E45\u5316\uFF08\u9632 best-effort \u5931\u8D25\u540E\u9759\u9ED8\u9000\u5316\uFF09\u3002
3. If mode=halted: write .workflow/blocked.md from {{blockedInfo}} (the blocked task's blocked_info JSON \u2014 render EACH field human-readably: plan, task, reason, category, last_error, suggested_fix, quota_exhausted, likely_source, failed_approach). For failed_approach, render as: "Failed Approach: <failed_approach.task_id>: <failed_approach.reason> \u2014 <failed_approach.error>". If blocked_info contains \`regressedFindings\` (v3 findings state machine detected regressions), render separately as a readable list: each item showing title + first_seen + last_seen + fixed_at_round + file + fix, to help locate regression points quickly. Do NOT hunt for these fields in state \u2014 they are provided inline in blockedInfo.
   If blocked_info.diag contains root_cause_category\uFF08\u65B9\u5411 16 debugger \u6839\u56E0\u5206\u7C7B\uFF09: \u5728\u57FA\u7840\u5B57\u6BB5\u4E4B\u540E\u3001\u5176\u4ED6\u5206\u7C7B\u6E32\u67D3\u4E4B\u524D\u7F6E\u9876\u6E32\u67D3\u7C7B\u522B\u4E0E\u6307\u5F15\uFF1A
   - plan_defect \u2192 "Root cause: plan_defect \u2014 \u5EFA\u8BAE\uFF1A\u4FEE\u6B63 plan\uFF08task \u62C6\u89E3/\u987A\u5E8F/spec \u51B2\u7A81\uFF09\u540E\u5168\u65B0\u8DD1"
   - implementor_issue \u2192 "Root cause: implementor_issue \u2014 \u5EFA\u8BAE\uFF1A\u5168\u65B0\u8DD1\u7EED\u8DD1\uFF1B\u53CD\u590D\u5931\u8D25\u5219\u628A task \u62C6\u5C0F"
   - reviewer_conflict \u2192 "Root cause: reviewer_conflict \u2014 \u5EFA\u8BAE\uFF1A\u4EBA\u5DE5\u88C1\u5B9A reviewer \u5206\u6B67\u70B9\uFF08spec/\u89C4\u5219\u51B2\u7A81\uFF09\u540E\u5168\u65B0\u8DD1"
   - environment \u2192 "Root cause: environment \u2014 \u5EFA\u8BAE\uFF1A\u5148\u4FEE\u6D4B\u8BD5\u73AF\u5883/\u4F9D\u8D56\u518D\u5168\u65B0\u8DD1"
   - unclear \u2192 "Root cause: unclear\uFF08\u8BC1\u636E\u4E0D\u8DB3\uFF0C\u6309\u901A\u7528 halt \u6307\u5F15\u6392\u67E5\uFF09"
   If blocked_info.reason === 'audit fix needed'\uFF08refactor task AUDIT \u9636\u6BB5\u53D1\u73B0 brief \u7F3A\u9677\uFF09: \u6309 blocked_info.diag.audit_reason \u5206\u7C7B\u6E32\u67D3\uFF08\u8FFD\u52A0\u5230 blocked.md\uFF0C\u7D27\u8DDF\u57FA\u7840\u5B57\u6BB5\u540E\uFF09\uFF1A
   - brief_defect: "## AUDIT: Brief \u4E0E\u73B0\u72B6\u4EE3\u7801\u4E0D\u4E00\u81F4
\u5DEE\u5F02\u6E05\u5355: <blocked_info.diag \u4E2D\u7684\u5DEE\u5F02\u9879>\u3002
Action: \u4FEE\u6B63 plan brief \u540E resume\uFF0Cbootstrap \u4F1A\u91CD\u8BFB\u91CD\u5BA1\u3002"
   - intentional_variant_unclear: "## AUDIT: \u65E0\u6CD5\u5224\u5B9A\u662F\u5426\u6709\u610F\u53D8\u4F53
\u5DEE\u5F02: <blocked_info.diag \u4E2D\u7684\u5DEE\u5F02\u9879>\u3002
Action: \u786E\u8BA4\u662F\u6709\u610F\u53D8\u4F53\uFF08\u5728 brief \u6807\u6CE8\u7406\u7531\uFF09\u8FD8\u662F\u7F3A\u9677\uFF08\u4FEE brief\uFF09\uFF0Cresume\u3002"
   - tool_failure: "## AUDIT: \u6838\u67E5\u5DE5\u5177\u6267\u884C\u5931\u8D25
\u5931\u8D25\u539F\u56E0: <blocked_info.diag>\u3002
Action: \u68C0\u67E5\u6587\u4EF6\u7CFB\u7EDF/\u5DE5\u5177\u53EF\u7528\u6027\u540E resume\u3002"
   S3\uFF08\u7B2C 4 \u8F6E\uFF09: blocked.md \u8DEF\u5F84\u56FA\u5B9A\u4E3A .workflow/blocked.md\uFF08\xA78.2\uFF09\uFF0C\u72EC\u7ACB\u4E8E {{runsDir}}\u2014\u2014
   blocked.md \u662F\u7528\u6237\u63A5\u624B\u5165\u53E3\uFF0C\u8DEF\u5F84\u987B\u7A33\u5B9A\u53EF\u9884\u6D4B\uFF08runsDir \u4F1A\u968F runTs \u53D8\u5316\uFF0C\u7528\u6237\u96BE\u5B9A\u4F4D\uFF09\u3002
4. If mode=halted: run "git status --porcelain" and "git diff --stat". BEST-EFFORT \u2014 if git fails (not a repo / index corrupt), skip this section (do NOT block manifest.json write).
   If "git status --porcelain" output is non-empty, append a "## Working Tree (dirty)" section to .workflow/blocked.md with: the porcelain output (file list) + the diff --stat output (change summary) + \u63A5\u624B\u6307\u5F15\uFF08implementor \u6539\u52A8\u672A\u63D0\u4EA4\uFF0C\u7559\u5728\u5DE5\u4F5C\u6811\u3002\u9009\u9879\uFF1Agit diff <file> \u67E5\u770B / git checkout -- <file> \u4E22\u5F03 / \u624B\u52A8\u4FEE\u540E git commit -m "feat(plan-X/T-Y): ..." \u518D\u5168\u65B0\u8DD1\u7EED\uFF0C\u89C1 USAGE.md \xA77.1\uFF09\u3002
   If output is empty, append "## Working Tree (clean)" \u2014 no uncommitted changes\uFF08likely_source=gate restored \u65F6\u9884\u671F\u5982\u6B64\uFF09\u3002
   Also include: if the halt was due to a failed review round (not model_unavailable/agent_error/gate/commit), add a "## Cross-Reviewer Findings (grouped by file)" section to blocked.md: group all findings from the halted task's blocked_info by file, and highlight files where \u22652 reviewers reported findings with \u26A0 CROSS-REVIEWER markers. This helps spot reviewer disagreements at a glance. Use the blockedInfo.raw field to extract reviewer findings \u2014 the raw field contains the diagnostics from spec/quality/hunter reviews.
5. Lessons ({{lessonsAutoDistill}}): If lessonsAutoDistill=true AND mode=halted: lesson-distiller agent has ALREADY been invoked by orchestrator before this finalReport call \u2014 it read lessonsPath, extracted reusable root causes, and updated lessons.md itself. You do NOT need to touch lessonsPath. If distiller failed (quota/error), orchestrator logged it and proceeded \u2014 lessonsPath may be stale but manifest write must proceed. If lessonsAutoDistill=false or mode=done: lessonsPath untouched.
6. Commit lessons.md (W1-1, 2026-07-07): If mode=halted AND {{lessonsPath}} is non-empty (H-F3 2026-07-07: \u7A7A lessonsPath \u5219 skip this step entirely \u2014 no lessonsPath configured \u2192 nothing to commit; \u7A7A lessonsPath \u4F1A\u8BA9 git status --porcelain \u67E5\u5168\u5DE5\u4F5C\u6811 \u2192 \u8BEF commit \u5168\u5DE5\u4F5C\u6811), after step 5, check if {{lessonsPath}} has uncommitted changes: run "git status --porcelain {{lessonsPath}}". If output is non-empty \u2192 git commit -m "chore(workflow): auto-commit lessons.md from run {{runTs}}" {{lessonsPath}} (H-F2: \u7528 git commit <path> \u4E00\u6B65\u5230\u4F4D\u4E0D\u9884 staged). This ensures the knowledge base is persisted (bootstrap reads it to inject implementor). BEST-EFFORT \u2014 if git commit fails, do NOT block manifest write; record the error in summary. H-F7 (2026-07-07): if commit succeeded (git commit exit code 0) \u2192 rewrite {{runsDir}}/manifest.json with lessons_committed:true (overwrite the false default from step 2). If step 6 was skipped (mode=done / empty lessonsPath / no uncommitted changes / commit failed) \u2192 leave lessons_committed:false.
7. Print a digest summary (counts: done/blocked, total tasks, per-plan gate result).

Return {evidence:{manifest_path}, summary: <digest>}.
RED FLAG: manifest \u5FC5\u987B\u771F\u5B9E\u5199\u5165\u78C1\u76D8\uFF08\u4F60 ls \u786E\u8BA4\uFF09\u3002stateJson \u662F orchestrator \u4F20\u5165\u7684\u5B8C\u6574\u72B6\u6001\uFF0C\u7167\u5B9E\u8BB0\u5F55\u3002`;

// src/prompts/templates/lesson-distiller.md
var lesson_distiller_default = `You are the LESSON-DISTILLER (model opus). Extract REUSABLE knowledge from a halted workflow run and update lessons.md. You are invoked by orchestrator (halt path) when mode=halted and lessons_auto_distill=true.

Inputs: distillInput={{distillInput}} lessonsPath={{lessonsPath}}

## Your task
1. Read distillInput: halt_info (reason, last_error, blocked task) + review_history (per-round findings) + failed_approaches (cross-run repeated failures).
2. Read lessonsPath file (current lessons.md). If file missing/empty, treat as no existing lessons. Parse entries: ## L-<id> followed by title/detail/source?/category?/status fields.
3. Identify REUSABLE knowledge \u2014 root causes that, if known beforehand, would have prevented the halt or guided the implementor differently. Categories:
   - silent-failure: swallowed error / bad fallback / missing transaction (e.g. DB split-commit must be single-transaction)
   - dependency: task ordering / cross-layer contract (e.g. frontend field name must match backend schema)
   - convention: commit message / naming / format violations causing bootstrap misrecognition
   - test-strategy: testing scope / framework / coverage gaps
   - other: anything reusable that doesn't fit above
4. FILTER OUT transient events (action=skip): review_empty, model_unavailable, single-occurrence hiccups. These are NOT reusable knowledge \u2014 they are\u77AC\u6001 model/runtime hiccups. ONLY\u63D0\u70BC root causes.
   Exception: if failed_approaches shows the SAME task halted with the SAME root cause across multiple runs (cross-run repeat),\u63D0\u70BC it even if the reason label looks transient \u2014 the repetition signals a systemic trap.
5. DEDUP against existing lessons: if a new finding semantically overlaps an existing entry \u2192 action=update (set update_target_id=existing id, refine title/detail with new evidence); if\u5168\u65B0 \u2192 action=append; if nothing reusable \u2192 action=skip.

## Apply decisions to lessonsPath (you write the file)
After deciding, APPLY decisions to lessonsPath yourself (you have fs access):
- append: add new entry at end of file. Format:
  ## L-<ts>
  title: <title>
  detail: <detail>
  source: <plan-X/T-Y@<run_ts>>
  category: <category>
  status: active
  last_verified: <YYYY-MM-DD>\uFF08\u672C\u6B21 run \u65E5\u671F\uFF09
- update: replace the existing entry\u6BB5\u843D (## L-<update_target_id> to next ## L- or EOF) with new content. Preserve update_target_id as id (or use new id if replacing).
\u65B0\u9C9C\u5EA6\u89C4\u5219\uFF08\u65B9\u5411 26b\uFF09\uFF1Aappend/update \u7684\u6761\u76EE\u5FC5\u987B\u5199 last_verified: <YYYY-MM-DD>\u3002\u5BF9\u660E\u663E\u9648\u65E7\u6761\u76EE\uFF08last_verified \u6216 source \u65E5\u671F >90 \u5929\u4E14\u672A\u88AB\u8FD1\u671F run \u518D\u6B21\u9A8C\u8BC1\uFF09\uFF0C\u5728\u5176\u6761\u76EE\u6807 status: stale\u2014\u2014stale \u662F\u63D0\u793A\u6807\u8BB0\uFF08bootstrap \u6CE8\u5165\u6682\u4E0D\u8FC7\u6EE4\uFF0C\u7559\u540E\u7EED\uFF09\u3002
- skip: no change.
Ensure file starts with '# Lessons Learned' header. Entries separated by blank lines.

## Quality bar (RED FLAG)
lesson \u5FC5\u987B\u662F\u53EF\u590D\u7528\u77E5\u8BC6\uFF0C\u975E\u4E8B\u4EF6\u6807\u7B7E\u3002
- \u274C title: "OSCILLATING" (event label \u2014 not reusable)
- \u274C title: "review_empty" (transient hiccup \u2014 not reusable)
- \u274C title: "halt" (too vague)
- \u2705 title: "\u540C\u6587\u4EF6 \u22653 round \u632F\u8361\u65F6\uFF0C\u68C0\u67E5 reviewer \u662F\u5426\u5BF9\u540C\u4E00 spec \u6761\u6B3E\u53CD\u5411\u62A5" (reusable root cause)
- \u2705 title: "DB \u5199 split-commit\uFF08DrawResult + outbox\uFF09\u5FC5\u987B\u5355\u4E8B\u52A1\uFF0C\u4E8C\u6B21 commit \u5931\u8D25\u5BFC\u81F4 outbox \u6C38\u4E0D\u8865" (reusable root cause)
- \u2705 title: "\u524D\u7AEF\u5B57\u6BB5\u540D\u5FC5\u987B\u4E0E\u540E\u7AEF pydantic schema \u4E00\u81F4\uFF08\u5982 item_append vs append\uFF09" (reusable cross-layer contract)

title \u5E94\u662F\u53EF\u72EC\u7ACB\u7406\u89E3\u7684\u7ED3\u8BBA\uFF1Bdetail \u542B\u6839\u56E0+\u573A\u666F+\u4FEE\u6CD5\uFF1Bsource \u662F task@run_ts \u4FBF\u4E8E\u8FFD\u6EAF\u3002

## Schema
Return {decisions: [{action, id, title, detail, source?, category?, update_target_id?}], summary}.
- action=append: id \u5FC5\u586B\uFF08\u65B0 L-<ts>\uFF09\uFF0Ctitle/detail \u5FC5\u586B\uFF0Csource/category \u5EFA\u8BAE\u586B\u3002
- action=update: update_target_id \u5FC5\u586B\uFF08existing id\uFF09\uFF0Cid \u53EF\u540C update_target_id\uFF08\u539F\u5730\u66F4\u65B0\uFF09\u6216\u65B0 id\uFF08\u66FF\u6362\uFF09\u3002title/detail \u5FC5\u586B\u3002
- action=skip: \u4EC5 id+title+detail \u5360\u4F4D\u5373\u53EF\uFF08\u4E0D\u4F1A\u88AB\u5199\u5165\uFF09\u3002
\u82E5\u6574\u4E2A run \u65E0\u53EF\u590D\u7528\u77E5\u8BC6 \u2192 decisions: [{action:'skip', id:'none', title:'no reusable knowledge', detail:'transient event only'}].
\u82E5\u9047\u5230 model \u9650\u989D\u8017\u5C3D\uFF08quota/rate-limit/429 \u9519\u8BEF\uFF09\uFF0C\u8FD4\u56DE decisions: [{action:'skip', id:'quota', title:'distiller quota exhausted', detail:'skip lesson update'}]\uFF08orchestrator \u4F1A best-effort \u8DF3\u8FC7\uFF09\u3002`;

// src/prompts/templates/broad-reviewer.md
var broad_reviewer_default = "You are the BROAD-REVIEWER (model opus). Review the COMPLETE plan diff for cross-task integration issues, scope conflicts, and standards violations that per-task reviewers cannot see.\n\nInputs: planId={{planId}} mergeBaseSha={{mergeBaseSha}} headSha={{headSha}}\n{{applicableStandardsNote}}\n{{deferredFindingsNote}}\n\n## Scope\nRun: git diff {{mergeBaseSha}}..{{headSha}}\nReview the ENTIRE diff holistically \u2014 not per-task, but as a unified change.\n\n## What to check\n1. Cross-task integration: Do interfaces between tasks match? Are import paths consistent?\n2. Scope conflicts: Does one task's change undo or conflict with another's?\n3. Standards violations: Are project-wide conventions violated anywhere in the diff?\n4. Missing integration: Are there files that should have been changed but weren't?\n\n## For each issue\nSet needsFix=true if code changes are required.\nSet needsFix=false if it is an observation or design debt note.\n\n## Deferred Findings Adjudication (if section present above)\nFor EACH deferred finding above, decide: must it be fixed before merge?\n- Yes \u2192 report it as an issue with needsFix=true AND severity upgraded to important or critical (by actual impact). A minor/unverified item with needsFix=true but still severity=minor will NOT be treated as actionable by the orchestrator.\n- No \u2192 do not report it (explicitly waived).\n\u26A0\uFE0F unverified entries are per-task reviewer suspicions beyond that task's diff scope \u2014 verify against the full plan diff and either confirm (report needsFix=true + severity\u5347\u7EA7) or dismiss.\n\nReturn {status (ok|failed), diagnostics:{files_touched:[...], issues:[{severity, title, file, fix, needsFix}]}, summary}.\nRED FLAG: ok only if no critical/important issues with needsFix=true. {{quotaHaltNote}}\n";

// src/prompts/templates/plan-parser.md
var plan_parser_default = `You are the PLAN-PARSER. Read plan markdown files and extract leaf tasks with metadata. Read-only except: you MAY write YAML frontmatter to plan files that lack it (idempotent).

Inputs: plansDir={{plansDir}}

Steps:
1. For each {{plansDir}}/*.md: if frontmatter (starts with ---) read task models; else generate \u2014 extract LEAF ids \u2014 **CRITICAL: \u5FC5\u987B\u8FD4\u56DE frontmatter models: \u7684\u6BCF\u4E00\u4E2A key\uFF08\u542B\u6700\u5927\u7684 N\uFF0C\u5982 T10\uFF09\uFF0C\u4E00\u4E2A\u4E0D\u6F0F\uFF1Bbody \u91CC ## Task N \u82E5\u6709 ### Task NX \u5B50 task \u2192 \u53EA\u53D6\u5B50 task\uFF08NX\uFF09\uFF0C\u5B50 task \u4E0D\u53EF\u9057\u6F0F\uFF1B## Task N \u65E0\u5B50 task \u2192 \u53D6 N \u672C\u8EAB**\uFF08leaf-first: ## Task N with ### Task NX children \u2192 only NX; else N), modelHint (title contains \u5B89\u5168|\u52A0\u5BC6|\u8BA4\u8BC1|JWT|CSRF|Fernet|\u7B97\u6CD5|\u6BD4\u5BF9|\u7B56\u7565|\u8FB9\u754C|\u96C6\u6210|\u63A5\u53E3 \u2192 opus, else omit), write frontmatter at file top. Idempotent. Record each plan's file (full path) and seq (last two digits of filename, e.g. 01). Also read write_files from frontmatter if present. Return as task_write_files in evidence: [{task_id, plan_seq, files:[...]}] (plan_seq = this plan's seq). Absent \u2192 empty array. Also extract "lesson_categories" from frontmatter if present. Return per task as "lesson_categories" array (absent \u2192 empty array). Also read each task's "Type" field from frontmatter (if present), normalize via trim().toLowerCase(), and scan the task's brief text for refactor keywords. audit_required = (type === 'refactor') OR (brief matches keyword regex). Return per task as "audit_required" (boolean, default false). Also scan each plan's FULL text (body included) for placeholder markers \u2014 mechanical extraction only: return every line containing any of these literals VERBATIM: TBD, FIXME, \u5F85\u8865\u5145, \u5F85\u586B\u5199, \u5F85\u5B9A, \u5F85\u5B8C\u5584, \u7A0D\u540E\u8865\u5145, add appropriate, add proper, as appropriate, similar to Task N. Return per plan as "placeholder_hits" (array of matched lines verbatim; no matches \u2192 empty array). Also read plan-level frontmatter "constraints" if present (array of strings; absent \u2192 omit). Also read frontmatter "interfaces" (map keyed by task id, values {consumes, produces}) and "depends_on" (map keyed by task id, values array of task ids); return each task's entry as "interfaces" (object) / "depends_on" (array); absent \u2192 omit.
2. For each leaf task return its model (sonnet|opus|undefined\u2192sonnet), title (the description text from the Task header), and brief (the task's body text, first 200 chars \u2014 for orchestrator lesson matching).

Return {status, evidence:{plans:[{id, file, seq, tasks:[{id, model, title, brief, lesson_categories, audit_required, interfaces?, depends_on?}], placeholder_hits?, constraints?}], task_write_files:[{task_id, plan_seq, files:[...]}]}, summary}.
RED FLAG: evidence \u5FC5\u987B\u662F\u771F\u5B9E\u8BFB\u53D6\u7ED3\u679C\uFF0C\u7EDD\u4E0D\u7F16\u9020\u3002
`;

// src/prompts/templates/debugger.md
var debugger_default = "You are the DEBUGGER (fresh context). A task just halted after repeated review-fix oscillation. Classify the ROOT CAUSE of the halt \u2014 this is a READ-ONLY analysis, do NOT modify code.\n\nInputs: taskId={{taskId}} haltReason={{reason}}\n\nFindings history (review rounds accumulated):\n{{findingsHistory}}\n\nTask brief: {{taskBrief}}\n\nFiles touched per round: {{filesTouched}}\n\n## Your task\nDecide the root-cause category of this oscillation halt:\n- **plan_defect**: the plan itself is wrong \u2014 bad task decomposition, wrong task ordering, plan brief conflicting with reality, or spec self-contradiction. The right move is to rework the plan, NOT to patch code harder.\n- **implementor_issue**: the plan is fine but the implementor keeps mis-implementing, reverting correct fixes, or misunderstanding requirements.\n- **reviewer_conflict**: reviewers contradict each other or contradict the spec (same finding flagged then un-flagged across rounds).\n- **environment**: test environment / dependency / tooling failure \u2014 not a code problem.\n- **unclear**: evidence is insufficient to classify.\n\nYou may read the plan file and spec for context (read-only). Keep analysis \u2264300 chars and suggested_action \u2264200 chars (concrete, actionable).\n\nReturn {status (ok|failed), diagnostics:{root_cause_category: plan_defect|implementor_issue|reviewer_conflict|environment|unclear, analysis, suggested_action}, summary}.\n{{quotaHaltNote}}\n";

// src/prompts/index.js
var PROMPTS = {
  bootstrap: bootstrap_default,
  implementor: implementor_default,
  reviewer: reviewer_default,
  hunter: hunter_default,
  simplify: simplify_default,
  commit: commit_default,
  contextFetcher: context_fetcher_default,
  gate: gate_default,
  headVerifier: head_verifier_default,
  finalReport: final_report_default,
  lessonDistiller: lesson_distiller_default,
  broadReviewer: broad_reviewer_default,
  planParser: plan_parser_default,
  debugger: debugger_default
};
function buildPrompt(role, ctx = {}) {
  const tpl = PROMPTS[role];
  if (!tpl) throw new Error(`unknown role: ${role}`);
  const defaults = { quotaHaltNote: QUOTA_HALT_NOTE, auditDirective: "", applicableStandardsNote: "", planLintNote: "", interfacesNote: "", constraintsNote: "", implementorEvidenceNote: "", deferredFindingsNote: "" };
  const merged = { ...defaults, ...ctx };
  return tpl.replace(/\{\{(\w+)\}\}/g, (_, k) => {
    if (!(k in merged)) return `{{${k}}}`;
    if (merged[k] === void 0 || merged[k] === null) return "";
    return String(merged[k]);
  });
}

// src/schemas/index.js
var SCHEMAS = {
  bootstrap: {
    type: "object",
    required: ["status", "evidence"],
    additionalProperties: true,
    properties: {
      status: { type: "string", enum: ["ok", "failed", "blocked"] },
      evidence: {
        type: "object",
        required: ["config", "completed", "git_log_subjects", "dirty_tree", "in_progress", "failed_approaches", "current_head_sha"],
        properties: { config: { type: "object" }, plans: { type: "array" }, completed: { type: "array" }, git_log_subjects: { type: "array", items: { type: "string" } }, current_head_sha: { type: "string" }, dirty_tree: { type: "boolean" }, in_progress: { type: "boolean" }, failed_approaches: { type: "array", items: { type: "object", required: ["task_id", "plan_seq", "reason", "error"], properties: { task_id: { type: "string" }, plan_seq: { type: "integer" }, reason: { type: "string" }, error: { type: "string" } } } }, task_write_files: { type: "array" }, task_lessons: { type: "array" }, all_lessons: { type: "array" } }
      },
      diagnostics: { type: "object" },
      summary: { type: "string" }
    }
  },
  implementor: {
    type: "object",
    required: ["status"],
    additionalProperties: true,
    properties: {
      status: { type: "string", enum: ["ok", "done_with_concerns", "failed", "blocked", "needs_context", "needs_audit_fix", "model_unavailable"] },
      evidence: {
        type: "object",
        required: ["tests_exit_code", "files_changed", "pytest_summary"],
        properties: {
          tests_exit_code: { type: "integer" },
          files_changed: { type: "array" },
          pytest_summary: { type: "string" },
          red_phase_output: { type: "string", description: "RED \u9636\u6BB5\u5931\u8D25\u8F93\u51FA\u539F\u6587\uFF08\u622A\u65AD \u2264500 \u5B57\u7B26\uFF09" },
          lesson_ids_used: { type: "array", items: { type: "string" }, description: "L-xxx \u7F16\u53F7\u5217\u8868\uFF08implementor \u4EE3\u7801\u6CE8\u91CA\u4E2D\u5F15\u7528\u7684 lesson IDs\uFF09" }
        }
      },
      diagnostics: { type: "object", properties: { blocked_category: { type: "string" }, last_error: { type: "string" }, suggested_fix: { type: "string" }, concerns: { type: "array", items: { type: "object", required: ["severity", "text"], properties: { severity: { type: "string", enum: ["critical", "important", "minor"] }, text: { type: "string" } } } } } },
      audit_reason: { type: "string", enum: ["brief_defect", "intentional_variant_unclear", "tool_failure"] },
      taskKey: { type: "string", description: "plan-scoped task key (e.g. plan-04/T4); echo back on needs_audit_fix so blocked.md can locate .audit/<taskKey>.md" },
      summary: { type: "string" }
    }
  },
  reviewer: {
    type: "object",
    required: ["status"],
    additionalProperties: true,
    properties: {
      status: { type: "string", enum: ["ok", "failed", "model_unavailable", "agent_error"] },
      diagnostics: { type: "object", properties: {
        files_touched: { type: "array" },
        issues: { type: "array", items: {
          type: "object",
          required: ["title", "fix"],
          properties: {
            dimension: { type: "string", enum: ["MISSING", "EXTRA", "MISUNDERSTANDING"] },
            severity: { type: "string", enum: ["critical", "important", "minor"] },
            title: { type: "string" },
            file: { type: "string" },
            fix: { type: "string" },
            concern_idx: { type: "integer" },
            confidence: { type: "string", enum: ["unverified"] },
            certainty: { type: "string", enum: ["high", "medium", "low"] },
            ownership: { type: "string", enum: ["local", "upstream", "unclear"] }
          }
        } },
        concerns_addressed: { type: "array", items: { type: "object", required: ["idx", "verdict"], properties: { idx: { type: "integer" }, verdict: { type: "string", enum: ["confirmed", "dismissed", "fixed"] }, note: { type: "string" } } } }
      } },
      summary: { type: "string" }
    }
  },
  hunter: {
    type: "object",
    required: ["status"],
    additionalProperties: true,
    properties: {
      status: { type: "string", enum: ["ok", "failed", "model_unavailable"] },
      diagnostics: { type: "object", properties: {
        files_touched: { type: "array" },
        silent_failures: { type: "array", items: {
          // severity 加 required（S7, 2026-07-08）：防 LLM 省略 severity → formatFindingsHistory L128 severity 排序失效
          type: "object",
          required: ["title", "fix", "severity"],
          properties: { title: { type: "string" }, severity: { type: "string", enum: ["critical", "important", "minor"] }, file: { type: "string" }, line: { type: "integer" }, fix: { type: "string" } }
        } }
      } },
      summary: { type: "string" }
    }
  },
  broadReviewer: {
    type: "object",
    required: ["status"],
    additionalProperties: true,
    properties: {
      status: { type: "string", enum: ["ok", "failed", "model_unavailable"] },
      diagnostics: { type: "object", properties: {
        files_touched: { type: "array" },
        issues: { type: "array", items: {
          type: "object",
          required: ["title", "fix", "severity"],
          properties: {
            severity: { type: "string", enum: ["critical", "important", "minor"] },
            title: { type: "string" },
            file: { type: "string" },
            fix: { type: "string" },
            needsFix: { type: "boolean" }
          }
        } }
      } },
      summary: { type: "string" }
    }
  },
  planParser: {
    type: "object",
    required: ["status", "evidence"],
    additionalProperties: true,
    properties: {
      status: { type: "string", enum: ["ok", "failed", "blocked"] },
      evidence: {
        type: "object",
        required: ["plans", "task_write_files"],
        properties: {
          plans: { type: "array", items: {
            type: "object",
            required: ["id", "file", "seq", "tasks"],
            properties: {
              id: { type: "string" },
              file: { type: "string" },
              seq: { type: "string" },
              placeholder_hits: { type: "array", items: { type: "string" } },
              constraints: { type: "array", items: { type: "string" } },
              tasks: { type: "array", items: {
                type: "object",
                required: ["id", "model", "title"],
                properties: {
                  id: { type: "string" },
                  model: { type: "string" },
                  title: { type: "string" },
                  brief: { type: "string" },
                  lesson_categories: { type: "array" },
                  audit_required: { type: "boolean" },
                  interfaces: { type: "object", properties: { consumes: { type: "string" }, produces: { type: "string" } } },
                  depends_on: { type: "array", items: { type: "string" } }
                }
              } }
            }
          } },
          task_write_files: { type: "array" }
        }
      },
      summary: { type: "string" }
    }
  },
  simplify: {
    type: "object",
    required: ["evidence"],
    additionalProperties: true,
    properties: { evidence: {
      type: "object",
      required: ["changed", "files_changed"],
      properties: { changed: { type: "boolean" }, files_changed: { type: "array" } }
    }, summary: { type: "string" } }
  },
  commit: {
    type: "object",
    required: ["status", "evidence"],
    additionalProperties: true,
    properties: {
      status: { type: "string", enum: ["ok", "failed", "model_unavailable"] },
      evidence: {
        type: "object",
        required: ["commit_sha", "committed_files", "tests_at_commit"],
        properties: { commit_sha: { type: "string" }, committed_files: { type: "array" }, tests_at_commit: { type: "integer" } }
      },
      diagnostics: { type: "object", properties: { out_of_scope: { type: "array" }, destructive_changes: { type: "array" } } },
      summary: { type: "string" }
    }
  },
  contextFetcher: {
    type: "object",
    required: ["diagnostics"],
    additionalProperties: true,
    properties: { diagnostics: { type: "object", required: ["context"], properties: { context: { type: "string" } } }, summary: { type: "string" } }
  },
  gate: {
    type: "object",
    required: ["status", "evidence"],
    additionalProperties: true,
    properties: {
      status: { type: "string", enum: ["ok", "failed", "model_unavailable"] },
      evidence: {
        type: "object",
        required: ["tests_exit_code", "pytest_summary", "lint_results", "restored_head"],
        properties: { tests_exit_code: { type: "integer" }, pytest_summary: { type: "string" }, lint_results: { type: "array", items: { type: "object", required: ["command", "exit_code"], properties: { command: { type: "string" }, exit_code: { type: "integer" }, summary: { type: "string" } } } }, migration_missing: { type: "boolean" }, restored_head: { type: "string" } }
      },
      summary: { type: "string" }
    }
  },
  finalReport: {
    type: "object",
    required: ["summary"],
    additionalProperties: true,
    properties: { evidence: { type: "object", properties: { manifest_path: { type: "string" } } }, summary: { type: "string" } }
  },
  lessonDistiller: {
    type: "object",
    required: ["decisions"],
    additionalProperties: true,
    properties: {
      decisions: { type: "array", items: {
        type: "object",
        required: ["action", "title", "detail"],
        properties: {
          action: { type: "string", enum: ["append", "update", "skip"] },
          id: { type: "string" },
          title: { type: "string" },
          detail: { type: "string" },
          source: { type: "string" },
          category: { type: "string", enum: ["silent-failure", "dependency", "convention", "test-strategy", "other"] },
          update_target_id: { type: "string" },
          last_verified: { type: "string" }
        }
      } },
      summary: { type: "string" }
    }
  },
  debugger: {
    type: "object",
    required: ["status"],
    additionalProperties: true,
    properties: {
      status: { type: "string", enum: ["ok", "failed", "model_unavailable", "agent_error"] },
      diagnostics: { type: "object", properties: {
        root_cause_category: { type: "string", enum: ["plan_defect", "implementor_issue", "reviewer_conflict", "environment", "unclear"] },
        analysis: { type: "string" },
        suggested_action: { type: "string" }
      } },
      summary: { type: "string" }
    }
  }
};

// src/agents/fallback.js
async function agentWithFallback(role, ctx, labelPrefix) {
  for (const m of ["opus", "sonnet", "haiku"]) {
    try {
      return await agent(
        buildPrompt(role, ctx),
        { schema: SCHEMAS[role], model: m, label: `${labelPrefix}:${m}` }
      );
    } catch (e) {
      log(`${labelPrefix} ${m} \u4E0D\u53EF\u7528: ${errStr(e)}, \u8BD5\u4E0B\u4E00\u4E2A`);
    }
  }
  log("fallback \u94FE\u5168\u5931\u8D25\uFF0C\u7528\u73AF\u5883\u9ED8\u8BA4 model \u4FDD\u5B58");
  try {
    return await agent(
      buildPrompt(role, ctx),
      { schema: SCHEMAS[role], label: `${labelPrefix}:default` }
    );
  } catch (e) {
    log(`\u2717 \u73AF\u5883\u9ED8\u8BA4 model \u4E5F\u5931\u8D25\uFF0C${labelPrefix} \u65E0\u6CD5\u4FDD\u5B58: ${errStr(e)}`);
    return null;
  }
}
async function finalReportWithFallback(ctx) {
  return agentWithFallback("finalReport", ctx, "final-report");
}

// src/lib/formatting.js
function allGreen(...reviews) {
  return reviews.every((r) => r && r.status === "ok");
}
function unionFiles(...reviews) {
  const set = /* @__PURE__ */ new Set();
  for (const r of reviews) for (const f of r?.diagnostics?.files_touched || []) set.add(normalizeFilePath(f));
  return [...set];
}
function normalizeFilePath(p) {
  if (typeof p !== "string" || !p) return p;
  return p.replace(/\\/g, "/").replace(/^.*?\/(src|tests|docs|data|logs|lib|app|internal|cmd|\.claude|scripts|bin|tools|config|public|static|templates|utils|api|server|client|web|\.github)\//i, "$1/");
}
function formatConcernsHint(concerns) {
  if (!Array.isArray(concerns) || concerns.length === 0) return "";
  return `
## Implementor Concerns (address EACH below in concerns_addressed by idx)
` + concerns.map((c, i) => `- [${i}] [${c.severity || "important"}] ${c.text || ""}`).join("\n");
}
function coerceConcerns(concerns) {
  if (!Array.isArray(concerns)) return [];
  return concerns.map((c) => {
    if (typeof c === "string") return { severity: "important", text: c };
    if (c && typeof c === "object") return { severity: c.severity || "important", text: c.text || "" };
    return { severity: "important", text: String(c) };
  });
}
function formatConcernsFeedback(reviewer, implConcerns) {
  const n = Array.isArray(implConcerns) ? implConcerns.length : 0;
  if (n === 0) return "";
  const addressed = reviewer?.diagnostics?.concerns_addressed || [];
  const byIdx = new Map(addressed.map((a) => [a.idx, a]));
  const lines = implConcerns.map((c, i) => {
    const a = byIdx.get(i);
    if (!a) return `- [${i}] [${c.severity || "important"}] ${c.text || ""} \u2192 (reviewer did not adjudicate)`;
    if (a.verdict === "confirmed" || a.verdict === "fixed") return `- [${i}] [${c.severity || "important"}] ${c.text || ""} \u2192 ${a.verdict.toUpperCase()} (see matching issue above)`;
    return `- [${i}] [${c.severity || "important"}] ${c.text || ""} \u2192 ${String(a.verdict || "").toUpperCase()}${a.note ? ": " + a.note : ""}`;
  });
  return `
## Implementor Concerns Verdict (from reviewer)
${lines.join("\n")}`;
}
function formatFindingItem(f, { withFile = true, prefix = "" } = {}) {
  const tag = f.severity ? `[${f.source}|${f.severity}]` : `[${f.source}]`;
  const fix = f.fix ? ` \u2014 fix: ${f.fix}` : "";
  const file = withFile && f.file ? ` (${f.file})` : "";
  return `${prefix}${tag} ${f.title}${fix}${file}`;
}
function formatBulletSection(heading, intro, items, renderItem, outro = "") {
  if (!Array.isArray(items) || items.length === 0) return "";
  const lines = items.map(renderItem).join("\n");
  let out = `## ${heading}
`;
  if (intro) out += `${intro}
`;
  out += lines;
  if (outro) out += `
${outro}`;
  return out;
}
function formatReferencePaths(paths) {
  return formatBulletSection(
    "Reference Documents (authoritative \u2014 match these exactly)",
    "",
    paths,
    (p) => `- ${p}`,
    "Read the relevant section(s) BEFORE implementing/reviewing domain-specific logic or rules. Deviations from these authoritative rules are bugs."
  );
}
function formatSilentFailureContext(items, intro) {
  const heading = intro || "Project-Specific Silent-Failure Risks (HIGHEST PRIORITY \u2014 hunt these first)";
  return formatBulletSection(
    heading,
    "Beyond the generic silent-failure patterns below, the following project-specific traps have caused real misses and MUST be checked explicitly:",
    items,
    (it) => `- ${it}`,
    "For each, verify the changed code does not fall into the trap. Report a silent_failure with the specific trap name + file:line + why it violates."
  );
}
function formatFailedApproaches(items) {
  return formatBulletSection(
    "Prior Failed Approaches (do not repeat)",
    "",
    items,
    (it) => `- ${it.task_id}: ${it.reason} \u2014 ${it.error}`,
    "If your plan is similar to any above, explicitly state the difference."
  );
}
function formatUniversalLessons(allLessons) {
  if (!Array.isArray(allLessons) || allLessons.length === 0) return "";
  const universal = allLessons.filter((l) => l && /^(silent[-_]?failure)$/i.test(String(l.category).trim()));
  if (universal.length === 0) return "";
  return formatBulletSection(
    "Universal Discipline (silent-failure \u2014 always apply)",
    "",
    universal,
    (l) => `- [${l.id}] ${l.title} \u2014 ${l.detail}`,
    "These are project-wide silent-failure disciplines. Before reporting done, verify your code does not violate any of them (savepoint isolation, naive-UTC datetime, single-transaction commits, etc.)."
  );
}
function formatDomainLessons(allLessons, taskCategories, currentPlanSeq, taskTitle) {
  if (!Array.isArray(allLessons) || allLessons.length === 0) return "";
  const candidates = allLessons.filter((l) => l && !/^(silent[-_]?failure)$/i.test(String(l.category).trim()));
  let matched = [];
  if (Array.isArray(taskCategories) && taskCategories.length > 0) {
    matched = candidates.filter((l) => taskCategories.includes(l.category));
  } else if (taskTitle) {
    const tokens = String(taskTitle).toLowerCase().split(/[\s,，、]+/).filter((t) => t.length > 1);
    matched = candidates.filter((l) => {
      const text = `${l.title || ""} ${l.detail || ""}`.toLowerCase();
      return tokens.some((t) => text.includes(t));
    });
  }
  if (matched.length === 0) return "";
  if (currentPlanSeq) {
    matched.sort((a, b) => {
      const aSame = a.source && String(a.source).includes(currentPlanSeq) ? 0 : 1;
      const bSame = b.source && String(b.source).includes(currentPlanSeq) ? 0 : 1;
      return aSame - bSame;
    });
  }
  const capped = matched.slice(0, 5);
  return formatBulletSection(
    "Domain Lessons (check against these before implementing)",
    "",
    capped,
    (l) => `- [${l.id}] ${l.title} \u2014 ${l.detail}`,
    "If your plan is similar to any lesson above, explicitly state why your approach differs."
  );
}
function formatWriteFilesScope(files) {
  if (!Array.isArray(files) || files.length === 0) return "";
  const lines = files.map((f) => `- ${f}`).join("\n");
  return `## Write Files Boundary (commit agent will verify)
${lines}
Before committing, run git diff --name-only. If any file is NOT in the list above, you MUST either: 1. revert the out-of-scope change, or 2. report status=failed with out_of_scope in diagnostics.`;
}
function formatConstraintsNote(constraints) {
  return formatBulletSection(
    "Global Constraints (plan-level, authoritative \u2014 apply to EVERY task)",
    "",
    constraints,
    (c) => `- ${c}`,
    "These constraints come from the plan frontmatter and override defaults. Verify your code complies."
  );
}
function formatInterfacesNote(interfaces) {
  if (!interfaces || !interfaces.consumes && !interfaces.produces) return "";
  const lines = [];
  if (interfaces.consumes) lines.push(`- Consumes: ${interfaces.consumes}`);
  if (interfaces.produces) lines.push(`- Produces: ${interfaces.produces}`);
  return `## Task Interfaces (from plan frontmatter \u2014 contract with neighboring tasks)
${lines.join("\n")}
Match these signatures exactly; deviations break cross-task integration.`;
}
function formatPlanLintNote(warnings) {
  return formatBulletSection(
    "Plan Lint Warnings (deterministic pre-execution audit)",
    "",
    warnings,
    // S-fmt-undef（2026-07-18 silent-failure-hunter #9）：rule 缺失回退 'unknown'，
    // 防调用方误传缺字段的 warning 时渲染 [undefined] 污染 prompt。
    (w) => `- [${w.rule || "unknown"}] ${w.detail}`,
    "These are advisory signals about the plan. If one indicates a real plan defect that blocks correct implementation, report status=blocked instead of guessing."
  );
}
function formatImplementorEvidenceNote(evidence, stage) {
  if (!evidence || typeof evidence !== "object") return "";
  const trunc500 = (v) => {
    if (typeof v !== "string" || v.trim() === "") return "(not provided)";
    return v.length > 500 ? v.slice(0, 500) + "...(truncated)" : v;
  };
  const stageLabel = stage === void 0 || stage === null || stage === "" ? "unknown stage" : String(stage);
  const lessonLine = Array.isArray(evidence.lesson_ids_used) && evidence.lesson_ids_used.length ? evidence.lesson_ids_used.join(", ") : "(none)";
  return `## Implementor Evidence (self-reported \u2014 verify against code)
- Stage: ${stageLabel}
- tests_exit_code: ${evidence.tests_exit_code ?? "(not provided)"}
- pytest_summary: ${trunc500(evidence.pytest_summary)}
- lesson_ids_used: ${lessonLine}
- red_phase_output: ${trunc500(evidence.red_phase_output)}`;
}
function formatSchemaCheck(schemaTool, modelPaths, migrationPaths) {
  if (!schemaTool) return "";
  const mp = Array.isArray(modelPaths) ? modelPaths.join(", ") : "";
  const xp = Array.isArray(migrationPaths) ? migrationPaths.join(", ") : "";
  return `## Schema Migration Check (gate agent must verify)
1. Run git diff --name-only HEAD~1..HEAD \u2014 you are already checked out to the committed SHA, so HEAD~1 is the parent commit.
2. Filter changed files by model_paths: ${mp}
3. Filter changed files by migration_paths: ${xp}
4. If model files changed but NO migration files changed \u2192 status=failed, evidence.migration_missing=true`;
}
function groupFindingsByFile(findings) {
  const groups = {};
  for (const f of findings) {
    if (!f.file) continue;
    const normFile = normalizeFilePath(f.file);
    if (!groups[normFile]) groups[normFile] = { file: normFile, sources: /* @__PURE__ */ new Set(), findings: [] };
    groups[normFile].sources.add(f.source);
    groups[normFile].findings.push(f);
  }
  return Object.values(groups);
}
function formatCrossReviewerNote(findings) {
  const groups = groupFindingsByFile(findings).filter((g) => g.sources.size >= 2);
  if (groups.length === 0) return "";
  let out = "\n## \u26A0 Cross-Reviewer Overlap (\u22652 reviewers flagged same file \u2014 check for conflicts)\n";
  for (const g of groups) {
    const srcs = [...g.sources].sort().join("/");
    out += `
### ${g.file} (flagged by: ${srcs})
`;
    for (const f of g.findings) {
      out += formatFindingItem(f, { withFile: false, prefix: "- " }) + "\n";
    }
  }
  return out;
}
function formatDeferredFindingsNote(entries) {
  if (!Array.isArray(entries) || entries.length === 0) return "";
  const trunc200 = (v) => {
    const s = String(v ?? "");
    return s.length > 200 ? s.slice(0, 200) + "...(truncated)" : s;
  };
  const render = (f) => `- [${f.taskKey || "?"}] [${f.severity || "minor"}] ${f.title || ""}${f.file ? ` (${f.file})` : ""}${f.fix ? ` \u2014 fix: ${trunc200(f.fix)}` : ""}`;
  const unverified = entries.filter((e) => e && e.kind === "unverified");
  const minors = entries.filter((e) => e && e.kind === "minor");
  const sections = [];
  if (unverified.length) sections.push(`### \u26A0\uFE0F Unverified (per-task reviewer \u8D85\u51FA diff \u89C6\u91CE\u7684\u7591\u70B9\u2014\u2014\u5BF9\u5168\u91CF diff \u6838\u5B9E\u540E\u786E\u8BA4\u6216\u9A73\u56DE)
${unverified.map(render).join("\n")}`);
  if (minors.length) sections.push(`### Minor (per-task review \u653E\u884C\u7684\u6B21\u8981\u9879)
${minors.map(render).join("\n")}`);
  if (sections.length === 0) return "";
  return `## Deferred Findings (per-task review \u6512\u5B58\uFF0C\u9010\u6761\u88C1\u5B9A\u2014\u2014\u89C1\u4E0B\u65B9 Adjudication \u6307\u4EE4)

${sections.join("\n\n")}`;
}

// src/lib/oscillation.js
function detectOscillation(filesTouchedPerRound) {
  if (filesTouchedPerRound.length < 3) return { oscillating: false };
  const fileRoundCount = {};
  for (const [i, files] of filesTouchedPerRound.entries()) {
    for (const f of files) {
      (fileRoundCount[f] ||= []).push(i);
    }
  }
  for (const [file, rounds] of Object.entries(fileRoundCount)) {
    if (rounds.length >= 3) {
      return { oscillating: true, reason: `${file} touched in ${rounds.length} rounds`, file, rounds };
    }
  }
  for (let i = 1; i < filesTouchedPerRound.length; i++) {
    const prev = new Set(filesTouchedPerRound[i - 1]);
    const curr = filesTouchedPerRound[i];
    const overlap = curr.filter((f) => prev.has(f));
    if (overlap.length >= 2 && overlap.length === curr.length) {
      return { oscillating: true, reason: `consecutive rounds fix same files: ${overlap.join(",")}`, files: overlap };
    }
  }
  return { oscillating: false };
}
function shouldEscalateOnOscillation(currentModel, alreadyEscalated) {
  if (alreadyEscalated) return false;
  return currentModel !== "opus";
}
function resolveReviewBudget(config) {
  const v = config?.review_budget;
  if (typeof v !== "number" || !Number.isFinite(v) || v <= 0) return 5;
  return v;
}
function isFlipFlop(reviewHistory) {
  if (!Array.isArray(reviewHistory) || reviewHistory.length < 2) return false;
  const last = reviewHistory[reviewHistory.length - 1];
  const prevTitles = /* @__PURE__ */ new Set();
  for (let i = 0; i < reviewHistory.length - 1; i++) {
    const round = reviewHistory[i];
    for (const r of [round?.reviewer, round?.hunter]) {
      for (const f of r?.findings || []) if (f?.title) prevTitles.add(f.title);
    }
  }
  for (const r of [last?.reviewer, last?.hunter]) {
    for (const f of r?.findings || []) {
      if (f?.title && prevTitles.has(f.title)) return true;
    }
  }
  return false;
}

// src/lib/findings.js
function findingsOf(r, source, key) {
  if (!r || r.status !== "failed") return [];
  const out = [];
  for (const it of r.diagnostics?.[key] || []) {
    if (it && typeof it === "object") out.push({ source, severity: it.severity, dimension: it.dimension, concern_idx: it.concern_idx, confidence: it.confidence, certainty: it.certainty, ownership: it.ownership, title: it.title || JSON.stringify(it), file: normalizeFilePath(it.file), fix: it.fix });
    else out.push({ source, title: String(it) });
  }
  return out;
}
var REVIEW_SOURCES = [
  { name: "reviewer", key: "issues" },
  { name: "hunter", key: "silent_failures" }
];
function collectReviewFindings(reviewer, hunt) {
  const reviews = [reviewer, hunt];
  return REVIEW_SOURCES.flatMap((s, i) => findingsOf(reviews[i], s.name, s.key));
}
function reviewHaltForEmptyFailed(reviewer, hunt) {
  const reviews = [reviewer, hunt];
  for (let i = 0; i < REVIEW_SOURCES.length; i++) {
    const { name, key } = REVIEW_SOURCES[i];
    const r = reviews[i];
    if (r && r.status === "failed" && findingsOf(r, name, key).length === 0) return "review_failed_no_findings";
  }
  return null;
}
function reviewHaltForUnaddressedConcerns(reviewer, implConcerns) {
  const n = Array.isArray(implConcerns) ? implConcerns.length : 0;
  if (n === 0) return null;
  const addressed = reviewer?.diagnostics?.concerns_addressed;
  if (!Array.isArray(addressed)) return "review_concerns_unaddressed";
  const validIdxs = new Set(Array.from({ length: n }, (_, i) => i));
  const addressedIdxs = new Set(addressed.map((a) => a?.idx).filter((x) => Number.isInteger(x)));
  if (addressedIdxs.size !== n || ![...validIdxs].every((i) => addressedIdxs.has(i))) {
    return "review_concerns_unaddressed";
  }
  return null;
}
function reviewHaltForConcernsWithoutIssue(reviewer, implConcerns) {
  const n = Array.isArray(implConcerns) ? implConcerns.length : 0;
  if (n === 0) return null;
  const addressed = reviewer?.diagnostics?.concerns_addressed || [];
  const needsIssue = addressed.filter((a) => a?.verdict === "confirmed" || a?.verdict === "fixed");
  if (needsIssue.length === 0) return null;
  const validIdxs = new Set(Array.from({ length: n }, (_, i) => i));
  const issues = reviewer?.diagnostics?.issues || [];
  const linkedIdxs = new Set(issues.map((i) => i?.concern_idx).filter((x) => Number.isInteger(x) && validIdxs.has(x)));
  const missing = needsIssue.filter((a) => !linkedIdxs.has(a.idx));
  if (missing.length > 0) return "review_concern_confirmed_without_issue";
  return null;
}
function describeConcernHalt(reviewer, implConcerns) {
  const n = Array.isArray(implConcerns) ? implConcerns.length : 0;
  if (n === 0) return "";
  const addressed = reviewer?.diagnostics?.concerns_addressed || [];
  const validIdxs = new Set(Array.from({ length: n }, (_, i) => i));
  const seen = /* @__PURE__ */ new Map();
  const outOfRange = [], duplicates = [];
  for (const a of addressed) {
    const i = a?.idx;
    if (!Number.isInteger(i)) continue;
    if (!validIdxs.has(i)) outOfRange.push(i);
    else if (seen.has(i)) duplicates.push(i);
    else seen.set(i, true);
  }
  const missing = [...validIdxs].filter((i) => !seen.has(i));
  const parts = [];
  if (outOfRange.length) parts.push(`idx ${outOfRange.join(",")} out of range [0,${n})`);
  if (duplicates.length) parts.push(`duplicate idx ${duplicates.join(",")}`);
  if (missing.length) parts.push(`missing idx ${missing.join(",")} (addressed ${seen.size}/${n})`);
  const issues = reviewer?.diagnostics?.issues || [];
  const linkedIdxs = new Set(issues.map((it) => it?.concern_idx).filter((x) => Number.isInteger(x) && validIdxs.has(x)));
  const needsIssue = addressed.filter((a) => (a?.verdict === "confirmed" || a?.verdict === "fixed") && !linkedIdxs.has(a?.idx));
  if (needsIssue.length) parts.push(`confirmed/fixed verdict${needsIssue.length > 1 ? "s" : ""} at idx ${needsIssue.map((a) => a.idx).join(",")} lack a matching issue with concern_idx`);
  return parts.join("; ");
}
function summarizeFinding(r, source, key) {
  return { status: r?.status, findings: findingsOf(r, source, key).map((f) => ({ title: f.title, severity: f.severity })) };
}
function summarizeReviewRound(round, reviewer, hunt) {
  const reviews = [reviewer, hunt];
  return Object.fromEntries([
    ["round", round],
    ...REVIEW_SOURCES.map((s, i) => [s.name, summarizeFinding(reviews[i], s.name, s.key)])
  ]);
}
var REVIEW_VALID_STATUSES = /* @__PURE__ */ new Set(["ok", "failed", "model_unavailable", "agent_error"]);
function reviewHaltReason(reviewer, hunt) {
  const statuses = [reviewer?.status, hunt?.status];
  if (statuses.includes("agent_error")) return "agent_error";
  if (statuses.includes("model_unavailable")) return "model_unavailable";
  if (statuses.some((st) => !st || !REVIEW_VALID_STATUSES.has(st))) return "review_empty";
  return null;
}
function updateFindingsHistory(history, currentFindings, round) {
  if (!Array.isArray(history)) history = [];
  const current = Array.isArray(currentFindings) ? currentFindings : [];
  const currentTitles = new Set(current.map((f) => f?.title).filter(Boolean));
  const result = history.map((h) => {
    const stillPresent = currentTitles.has(h.title);
    if (stillPresent) {
      const status = h.status === "open" ? "open" : "regressed";
      return {
        ...h,
        last_seen: round,
        rounds: [...h.rounds, round],
        status,
        // fixed→regressed 时保留 fixed_at_round（diag 用）；open/regressed 不变
        fixed_at_round: h.fixed_at_round
      };
    }
    if (h.status === "open" || h.status === "regressed") {
      return { ...h, status: "fixed", fixed_at_round: round };
    }
    return h;
  });
  const existingTitles = new Set(history.map((h) => h.title));
  for (const f of current) {
    if (f?.title && !existingTitles.has(f.title)) {
      result.push({
        title: f.title,
        severity: f.severity,
        kind: f.kind,
        fix: f.fix,
        file: f.file,
        first_seen: round,
        last_seen: round,
        rounds: [round],
        status: "open"
      });
    }
  }
  return result;
}
function hasRegressed(history) {
  if (!Array.isArray(history)) return false;
  return history.some((h) => h?.status === "regressed");
}
function formatFindingsHistory(history, currentRound) {
  if (!Array.isArray(history) || history.length === 0) return "";
  const open = history.filter((h) => h.status === "open");
  const fixed = history.filter((h) => h.status === "fixed");
  const sections = [];
  if (open.length > 0) {
    const sevRank = { critical: 0, important: 1, minor: 2 };
    const sortedOpen = [...open].sort((a, b) => (sevRank[a.severity] ?? 9) - (sevRank[b.severity] ?? 9));
    const lines = sortedOpen.map((h) => {
      const sev = h.severity ? `[${h.severity}]` : "";
      const isNew = currentRound !== void 0 && h.last_seen === currentRound;
      const seen = isNew ? "\u2605\u672C\u8F6E\u65B0\u589E" : `(seen: r${h.first_seen}-${h.last_seen}, ${h.rounds.length}\u8F6E)`;
      const verifyNote = h.kind === "verifyFirst" ? "\uFF08\u4F4E\u7F6E\u4FE1\u2014\u2014\u6838\u5B9E\u540E\u518D\u6539\uFF09" : "";
      const file = h.file ? `, file: ${h.file}` : "";
      const fix = h.fix ? ` \u2014 fix: ${h.fix}` : "";
      return `- ${sev} ${h.title}${verifyNote} ${seen}${file}${fix}`;
    }).join("\n");
    sections.push(`### [OPEN] \u672C\u8F6E\u4ECD\u5B58\u5728 \u2014 \u5FC5\u987B\u4FEE\u5B8C\uFF08\u2605 = \u672C\u8F6E\u65B0\u589E\uFF0C\u4F18\u5148\u4FEE\uFF09
${lines}`);
  }
  if (fixed.length > 0) {
    const lines = fixed.map((h) => {
      const sev = h.severity ? `[${h.severity}]` : "";
      const file = h.file ? `, file: ${h.file}` : "";
      const fix = h.fix ? ` \u2014 fix: ${h.fix}` : "";
      return `- ${sev} ${h.title} (fixed r${h.fixed_at_round}${file})${fix}`;
    }).join("\n");
    sections.push(`### [FIXED] \u5DF2\u4FEE\u597D\u7684 \u2014 \u4FEE\u65B0\u95EE\u9898\u65F6\u6838\u5BF9\u8FD9\u91CC\u5217\u51FA\u7684 fix \u4ECD\u5B58\u5728\uFF08\u82E5 [OPEN] \u4E0E [FIXED] \u540C\u6587\u4EF6\uFF0C\u53EA\u52A8 [OPEN] \u63CF\u8FF0\u7684\u4EE3\u7801\uFF0C\u4E0D\u8981\u56DE\u9000 [FIXED] \u5BF9\u5E94\u7684\u4FEE\u6539\uFF09
${lines}`);
  }
  if (sections.length === 0) return "";
  return `## Findings History (\u5168\u8F6E\u7D2F\u79EF)
${sections.join("\n\n")}`;
}
function recordReviewRound(state2, taskKey2, round, reviewer, hunt, findingsOverride = null) {
  state2.perTask[taskKey2].review_rounds = round;
  state2.perTask[taskKey2].files_touched_per_round.push(unionFiles(reviewer, hunt));
  state2.perTask[taskKey2].review_history.push(summarizeReviewRound(round, reviewer, hunt));
  const currentFindings = findingsOverride || collectReviewFindings(reviewer, hunt);
  state2.perTask[taskKey2].findings_history = updateFindingsHistory(
    state2.perTask[taskKey2].findings_history,
    currentFindings,
    round
  );
  return { currentFindings };
}
function decideReviewOutcome(state2, taskKey2, round, reviewer, hunt, model, maxRounds, cfg, reviewReason, emptyFailedReason) {
  if (reviewReason) return { action: "halt", reason: reviewReason, diag: { reviewer: reviewer?.diagnostics, hunt: hunt?.diagnostics } };
  if (emptyFailedReason) return { action: "halt", reason: emptyFailedReason, diag: { reviewer: reviewer?.diagnostics, hunt: hunt?.diagnostics } };
  if (allGreen(reviewer, hunt)) return { action: "break" };
  const osc = detectOscillation(state2.perTask[taskKey2].files_touched_per_round);
  const flipFlop = isFlipFlop(state2.perTask[taskKey2].review_history || []);
  const regressed = hasRegressed(state2.perTask[taskKey2].findings_history || []);
  if (regressed) return { action: "halt", reason: "OSCILLATING", diag: { ...osc, flipFlop, regressed, regressedFindings: state2.perTask[taskKey2].findings_history.filter((h) => h.status === "regressed"), model } };
  let action = "fix";
  if (osc.oscillating) {
    if (flipFlop) return { action: "halt", reason: "OSCILLATING", diag: { ...osc, flipFlop, regressed, model } };
    if (shouldEscalateOnOscillation(model, state2.perTask[taskKey2].opus_escalated)) {
      action = "escalate";
    } else {
      action = "continue";
    }
  }
  if (maxRounds === 0) {
    const budget = resolveReviewBudget(cfg);
    if (round >= budget) return { action: "halt", reason: "review_not_converging", diag: { round, budget, findings_history: state2.perTask[taskKey2].findings_history, reviewer: reviewer?.diagnostics, hunt: hunt?.diagnostics } };
  } else if (round === maxRounds) {
    return { action: "halt", reason: "review max rounds", diag: { round, findings_history: state2.perTask[taskKey2].findings_history, reviewer: reviewer?.diagnostics, hunt: hunt?.diagnostics } };
  }
  return action === "escalate" ? { action, model: "opus" } : { action };
}

// src/lib/config.js
function validateAmendResult(result) {
  const sha = String(result?.sha || "").trim();
  if (!result?.ok || !/^[0-9a-f]{40}$/.test(sha)) {
    return { valid: false, error: result?.error || result?.sha || "invalid sha" };
  }
  return { valid: true, sha };
}
function validateCheckoutResult(result) {
  if (!result?.ok) {
    return { valid: false, error: result?.error || "checkout failed" };
  }
  const porcelain = String(result?.porcelain || "").trim();
  if (porcelain !== "") {
    return { valid: false, error: `working tree not clean after checkout: ${porcelain}` };
  }
  return { valid: true };
}
function fixModelForRound(round, baseModel, maxRounds) {
  const max = maxRounds ?? 3;
  if (max === 0) return round >= 4 ? "opus" : baseModel;
  if (round === max - 1) return "opus";
  return baseModel;
}
function resolveMaxRounds(config) {
  const v = config?.review_max_rounds;
  if (v === void 0 || v === null) return 4;
  if (typeof v !== "number" || !Number.isFinite(v)) return 4;
  if (v <= 0) return 0;
  return Math.floor(v);
}
function resolveLessonsAutoDistill(config) {
  const v = config?.lessons_auto_distill;
  if (v === false) return false;
  return true;
}
function distillLessonInput(mode, haltInfo, reviewHistory, failedApproaches) {
  return {
    mode,
    halt_info: haltInfo || null,
    review_history: Array.isArray(reviewHistory) ? reviewHistory : [],
    failed_approaches: Array.isArray(failedApproaches) ? failedApproaches : []
  };
}
var LANGUAGE_CHECKLISTS = {
  python: `## Language-specific checks (Python / FastAPI / SQLModel)
- SQL injection: f-strings/concat in queries \u2192 parameterized queries
- Command injection: unvalidated input in shell \u2192 subprocess with list arguments
- Bare except / except: pass \u2192 catch specific exceptions
- Swallowed exceptions / silent failures \u2192 log + handle explicitly
- Mutable default arguments (def f(x=[])) \u2192 use None sentinel
- value == None \u2192 use value is None
- Shadowing builtins (list, dict, str, id)
- Missing type hints on public functions; Any overuse; missing Optional for nullable
- Blocking calls inside async (FastAPI: no sync IO in async handlers \u2014 offload or use sync def)
- N+1 queries in loops \u2192 batch / select_related
- Missing context managers (with) for files/DB/resources
- print() instead of logging; from module import *`,
  general: `## Quality checks (general)
- Clean separation of concerns; proper error handling; type safety where applicable
- DRY without premature abstraction; edge cases handled`
};
function languageChecklist(language) {
  return LANGUAGE_CHECKLISTS[language] || LANGUAGE_CHECKLISTS.general;
}
function gateCommands(config) {
  const cmds = [];
  if (config?.full_test_command) cmds.push({ kind: "test", command: config.full_test_command });
  if (config?.lint_command) cmds.push({ kind: "lint", command: config.lint_command });
  for (const c of config?.extra_lint_commands || []) if (c) cmds.push({ kind: "lint", command: c });
  if (config?.smoke_command) cmds.push({ kind: "smoke", command: config.smoke_command });
  return cmds;
}

// src/review/chain.js
async function runReviewRound(taskId, cfg, plan, fc, concernsHint, labelSuffix, phaseLabel, applicableStandardsNote, implConcerns = [], extraCtx = {}) {
  const commonOpts = phaseLabel ? { phase: phaseLabel } : {};
  const maxRetries = 1;
  const huntPromise = safeAgent(buildPrompt("hunter", {
    taskId,
    filesChanged: fc,
    silentFailureContext: formatSilentFailureContext(cfg.silent_failure_context, cfg.silent_failure_intro)
  }), { schema: SCHEMAS.hunter, model: "sonnet", ...commonOpts, label: `hunt:${taskId}${labelSuffix}` });
  let reviewer = null, concernHalt = null;
  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    const corrective = attempt > 0 ? `

CORRECTION (previous attempt failed: ${concernHalt}): Your concerns_addressed must cover EVERY Implementor Concern above by idx \u2014 exactly idx 0..N-1, each used once, no duplicates, no out-of-range. For confirmed/fixed verdicts, add a matching issue with concern_idx=<that idx>. Specific failure: ${describeConcernHalt(reviewer, implConcerns) || concernHalt}.` : "";
    reviewer = await safeAgent(buildPrompt("reviewer", {
      specPath: cfg.spec_path,
      taskId,
      planFilePath: plan.file,
      filesChanged: fc,
      concernsHint: (concernsHint || "") + corrective,
      referencePaths: formatReferencePaths(cfg.reference_paths),
      languageChecklist: languageChecklist(cfg.language),
      applicableStandardsNote: applicableStandardsNote || "",
      lessonsPath: cfg.lessons_path || "",
      staticReadonlyNote: STATIC_READONLY_NOTE("review"),
      ...extraCtx
    }), { schema: SCHEMAS.reviewer, model: "opus", ...commonOpts, label: `review:${taskId}${labelSuffix}${attempt > 0 ? `:retry${attempt}` : ""}` });
    if (!["ok", "failed"].includes(reviewer?.status)) break;
    concernHalt = reviewHaltForUnaddressedConcerns(reviewer, implConcerns) ?? reviewHaltForConcernsWithoutIssue(reviewer, implConcerns);
    if (!concernHalt) break;
  }
  const hunt = await huntPromise;
  const haltReason = reviewHaltReason(reviewer, hunt) ?? concernHalt;
  const emptyFailed = haltReason ? null : reviewHaltForEmptyFailed(reviewer, hunt);
  return { reviewer, hunt, haltReason, emptyFailed };
}
async function runFixRound(taskKey2, plan, task, round, reviewer, hunt, state2, cfg, implCtx, model, maxRounds, concerns, concernsHint, processed = null) {
  if (!processed) {
    throw new Error("runFixRound: processed parameter is required (Hunter #9 \u2014 removed fallback self-compute path to prevent drift from main loop)");
  }
  const p = processed;
  if (p.halted) return { impl: { halted: true, reason: p.reason, diag: { reviewer: reviewer?.diagnostics, hunt: hunt?.diagnostics } }, halted: true, reason: p.reason };
  const findings = p.finalFindings;
  const crossReviewerNote = formatCrossReviewerNote(findings);
  const findingsHistoryText = formatFindingsHistory(state2.perTask[taskKey2].findings_history || [], round);
  const fullFixIssues = findingsHistoryText ? `${findingsHistoryText}
${crossReviewerNote}` : crossReviewerNote;
  const concernsFeedback = formatConcernsFeedback(reviewer, concerns);
  const fullFixWithConcerns = concernsFeedback ? `${fullFixIssues}
${concernsFeedback}` : fullFixIssues;
  const oscEscRound = state2.perTask[taskKey2].oscillation_escalated_at_round;
  const retryNote = oscEscRound === round ? `## \u5347\u7EA7\u5230 opus\uFF0C\u672C\u8F6E\u5FC5\u987B\u4FEE\u5B8C\u6240\u6709 [OPEN]
- \u9010\u6761\u6838\u5BF9 [OPEN]\uFF0C\u6BCF\u6761\u8981\u4E48\u4FEE\u5B8C\uFF0C\u8981\u4E48\u8BF4\u660E\u4E0D\u4FEE\u7684\u539F\u56E0\uFF08\u2605 \u6807\u672C\u8F6E\u65B0\u589E\u7684\u4F18\u5148\u4FEE\uFF09
- \u4FEE\u5B8C\u540E\uFF0C\u6838\u5BF9 [FIXED] \u5217\u8868\u7684 fix \u5728\u4F60\u7684\u6539\u52A8\u540E\u4ECD\u7136\u5B58\u5728\uFF1B\u82E5 [OPEN] \u4E0E [FIXED] \u540C\u6587\u4EF6\uFF0C\u53EA\u52A8 [OPEN] \u63CF\u8FF0\u7684\u4EE3\u7801\uFF0C\u4E0D\u8981\u56DE\u9000 [FIXED] \u5BF9\u5E94\u7684\u4FEE\u6539
- \u4E0D\u8981\u7559\u5230\u4E0B\u4E00\u8F6E\uFF0C\u4E0B\u4E00\u8F6E\u4E0D\u518D\u6709\u5347\u7EA7\u7A7A\u95F4
- \u622A\u81F3 r${round} review \u7D2F\u8BA1\u672A\u4FEE findings \u5982\u4E0A` : `\u4FEE\u590D review round ${round} \u95EE\u9898\uFF08${findings.length} \u9879\u53D1\u73B0\uFF1B\u2605 \u6807\u672C\u8F6E\u65B0\u589E\uFF09\u3002\u4F18\u5148\u8FD0\u884C\u4E0E\u4FEE\u6539\u6587\u4EF6\u76F8\u5173\u7684\u6D4B\u8BD5\u6587\u4EF6\uFF08\u5982 \`pytest tests/test_xxx.py\` / \`node --test tests/xxx.test.js\`\uFF09\u5FEB\u901F\u8FED\u4EE3\uFF1B\u5168\u91CF\u6D4B\u8BD5\u547D\u4EE4\uFF08${cfg.test_command ?? "(not provided)"}\uFF09\u4EC5\u5728\u6700\u7EC8\u786E\u8BA4\u65F6\u8DD1\u2014\u2014plan gate \u4F1A\u5728 committed SHA \u4E0A\u5168\u91CF\u91CD\u8DD1\u515C\u5E95\u3002`;
  const fixModel = fixModelForRound(round, model, maxRounds);
  const impl = await dispatchImpl(buildPrompt("implementor", implCtx(fullFixWithConcerns, retryNote)), { schema: SCHEMAS.implementor, model: fixModel, label: `impl:${task.id}:fix${round}` }, fixModel, "opus");
  if (impl.halted) return { impl, halted: true };
  if (impl.status === "blocked" || impl.status === "failed" || impl.status === "needs_context") {
    return { impl, halted: true, reason: `implementor ${impl.status} in fix-round ${round}` };
  }
  if (impl.status === "done_with_concerns") {
    concerns = coerceConcerns(impl.diagnostics?.concerns || concerns);
    state2.perTask[taskKey2].concerns = concerns;
    concernsHint = formatConcernsHint(concerns);
    log(`\u26A0 ${task.id} fix-round ${round} done_with_concerns: ${concerns.map((c) => `[${c.severity}] ${c.text}`).join("; ") || "(no detail)"}`);
  }
  return { impl, halted: false, concerns, concernsHint, filesChanged: impl.evidence.files_changed };
}

// src/state/per-task.js
function ensurePerTaskDefaults(entry) {
  return {
    planId: null,
    status: "in_progress",
    model: "sonnet",
    audit_required: false,
    review_rounds: 0,
    files_touched_per_round: [],
    review_history: [],
    findings_history: [],
    // v3: 状态机
    oscillation_escalated_at_round: null,
    // v3 F: 升级轮标记
    commit_sha: null,
    opus_escalated: false,
    simplify_reverted: false,
    simplify_review_findings: [],
    destructive_review_failed: false,
    destructive_review_findings: [],
    concerns: [],
    concernVerdicts: [],
    blocked_info: null,
    lessonIdsUsed: [],
    deferredLedger: [],
    // W3（2026-07-18）: deferred findings（minor/unverified）攒存，broad review 裁定
    ...entry || {}
  };
}

// src/state/global.js
var state = {
  runTs: null,
  config: null,
  completed: [],
  plans: [],
  currentPlan: null,
  currentTask: null,
  perTask: {},
  perPlan: {},
  failedApproaches: {},
  taskWriteFiles: {},
  taskLessons: {},
  allLessons: [],
  planLintWarnings: {},
  planLint: null
};

// src/lib/leaf-tasks.js
function commitSubject(seq, taskId, title) {
  const planIdShort = `plan-${String(seq).padStart(2, "0")}`;
  return `feat(${planIdShort}/${taskId}): ${title}`;
}
function extractCompletedFromSubjects(subjects) {
  const out = /* @__PURE__ */ new Set();
  for (const s of Array.isArray(subjects) ? subjects : []) {
    const m = String(s).match(/^(?:feat|fix|refactor)\(plan-(\d+)\/(T[\w-]+)\)\s*:/i);
    if (m) out.add(`plan-${m[1]}/${m[2]}`);
  }
  return [...out];
}
function normalizeCompleted(ids) {
  return ids.map((id) => {
    const m = String(id).match(/^(?:plan-)?(\d+)[\/\-]+(T[\w-]+)$/i);
    return m ? `plan-${m[1]}/${m[2]}` : String(id);
  });
}
function bareTaskId(id) {
  return String(id).replace(/^plan-\d+\/+/i, "");
}
function taskKey(seq, taskId) {
  return `plan-${String(seq).padStart(2, "0")}/${taskId}`;
}
function dropParentTasks(tasks) {
  return tasks.filter((t) => {
    const m = String(t.id).match(/^T(\d+)$/);
    if (!m) return true;
    const re = new RegExp(`^T${m[1]}[a-z]`);
    return !tasks.some((x) => re.test(String(x.id)));
  });
}
function matchesPlanFilter(plan, planArg) {
  if (!planArg) return true;
  const a = String(planArg);
  if (a === plan.id || a === plan.seq) return true;
  const n = Number(a);
  if (!Number.isNaN(n)) {
    if (Number(plan.seq) === n) return true;
    const idNum = Number(String(plan.id).replace(/^plan-/i, ""));
    if (!Number.isNaN(idNum) && idNum === n) return true;
  }
  return false;
}

// src/lib/scope.js
function inferApplicableStandards(writeFiles, brief) {
  const standards = [];
  const all = Array.isArray(writeFiles) ? writeFiles : [];
  const text = (brief || "").toLowerCase();
  if (all.length === 0) return standards;
  const isTest = (f) => /(^|\/)(test|tests|__tests?)\/|\.spec\.|\.test\./.test(f);
  const isDoc = (f) => /\.(md|txt|rst)$/.test(f) || f.startsWith("docs/");
  const isConfig = (f) => /\.(json|yaml|yml|toml|ini|env)$/.test(f);
  if (all.length > 0 && all.every(isTest)) standards.push("test-only");
  if (all.length > 0 && all.every(isDoc)) standards.push("docs-only");
  if (all.length > 0 && all.every(isConfig)) standards.push("config-only");
  if (all.some((f) => /migration|alembic|prisma/i.test(f))) standards.push("db-migration");
  if (all.some((f) => /Makefile|build\.|esbuild|webpack|Dockerfile|\.github/i.test(f))) standards.push("build-infra");
  if (all.some((f) => /^(src|lib|internal)\//.test(f) && !/\.(test|spec)\./.test(f))) standards.push("src-core");
  if (all.some((f) => /(index\.js|main\.py|app\.py|__init__\.py)$/.test(f))) standards.push("entry-point");
  if (AUDIT_REFACTOR_KEYWORDS.test(text)) standards.push("refactor");
  if (/security|安全|加密|认证|jwt|csrf/i.test(text)) standards.push("security");
  return standards;
}
function formatStandardsNote(standards) {
  if (!standards || standards.length === 0) return "";
  const descriptions = {
    "test-only": "this task primarily modifies test files \u2014 do not flag missing production code features that tests themselves do not need",
    "docs-only": "this task only modifies documentation \u2014 do not flag code architecture or runtime issues",
    "config-only": "this task only modifies configuration files \u2014 do not flag code architecture issues",
    "db-migration": "database migration files are involved \u2014 check migration safety and rollback",
    "build-infra": "build/CI infrastructure is involved \u2014 check build pipeline correctness",
    "src-core": "core source code is involved \u2014 architecture and dependency standards apply",
    "entry-point": "entry point files are involved \u2014 check initialization order and side effects",
    "refactor": "this is a refactoring task \u2014 behavior should be preserved, flag any behavioral change",
    "security": "security-sensitive code is involved \u2014 apply security review standards"
  };
  const lines = standards.map((s) => `- ${s}: ${descriptions[s] || s}`);
  return `This task is scoped to:
${lines.join("\n")}`;
}
function arbitrateScopeConflicts(findings, standards) {
  if (!standards || standards.length === 0) return findings;
  return findings.map((f) => {
    if (f.dimension !== "EXTRA" || f.severity !== "important") return f;
    if (standards.includes("test-only")) {
      return { ...f, severity: "minor", _scopeDowngraded: true };
    }
    if (standards.includes("docs-only")) {
      return { ...f, severity: "minor", _scopeDowngraded: true };
    }
    if (standards.includes("config-only")) {
      return { ...f, severity: "minor", _scopeDowngraded: true };
    }
    return f;
  });
}
function filterLessonsExemption(findings, lessonIdsUsed, allLessons) {
  if (!lessonIdsUsed || lessonIdsUsed.length === 0) return findings;
  if (!Array.isArray(allLessons) || allLessons.length === 0) return findings;
  const usedSet = new Set(lessonIdsUsed);
  const usedLessons = allLessons.filter((l) => usedSet.has(l.id));
  if (usedLessons.length === 0) return findings;
  return findings.map((f) => {
    if (f.dimension !== "EXTRA") return f;
    if (f.severity === "minor") return f;
    const text = ((f.title || "") + " " + (f.fix || "")).toLowerCase();
    const isLessonRelated = usedLessons.some((lesson) => {
      const lessonTitle = (lesson.title || "").toLowerCase();
      const keywords = lessonTitle.split(/\s+/).filter((w) => w.length > 3);
      return keywords.some((kw) => text.includes(kw));
    });
    if (isLessonRelated) {
      return { ...f, severity: "minor", _lessonExempted: true };
    }
    return f;
  });
}

// src/lib/plan-lint.js
var PLAN_PLACEHOLDER_PATTERNS_A = [
  /\bTBD\b/i,
  /\bFIXME\b/i,
  /待补充|待填写|稍后补充/
];
var PLAN_PLACEHOLDER_PATTERNS_B = [
  /add appropriate|add proper|as appropriate/i,
  /similar to task \w+/i,
  /待定|待完善/
];
var NEGATION_MARKERS = /(无|没有|不含|不存在|不包含|并非|非\s*\b|未出现|未含|no\s|without\b|absent\b|none\b)/i;
function matchPlaceholderClass(text) {
  for (const line of String(text).split("\n")) {
    const hitA = PLAN_PLACEHOLDER_PATTERNS_A.some((re) => re.test(line));
    const hitB = !hitA && PLAN_PLACEHOLDER_PATTERNS_B.some((re) => re.test(line));
    if ((hitA || hitB) && !NEGATION_MARKERS.test(line)) return hitA ? "L1a" : "L1b";
  }
  return null;
}
function lintPlans(plans, taskWriteFiles, { allLessons = [] } = {}) {
  const defects = [];
  const warnings = [];
  let phRaw = 0;
  let phFiltered = 0;
  const seen = /* @__PURE__ */ new Set();
  const push = (bucket, item) => {
    const k = `${bucket === defects ? "d" : "w"}|${item.rule}|${item.taskKey || ""}|${item.detail}`;
    if (seen.has(k)) return;
    seen.add(k);
    bucket.push(item);
  };
  const safePlans = Array.isArray(plans) ? plans : [];
  for (const plan of safePlans) {
    const tasks = Array.isArray(plan?.tasks) ? plan.tasks : [];
    for (const hit of Array.isArray(plan?.placeholder_hits) ? plan.placeholder_hits : []) {
      phRaw++;
      const cls = matchPlaceholderClass(String(hit));
      if (!cls) {
        phFiltered++;
        continue;
      }
      push(cls === "L1a" ? defects : warnings, { plan: plan.id, rule: cls, detail: `\u5360\u4F4D\u7B26\u547D\u4E2D\uFF08plan \u5168\u6587\uFF09: ${String(hit).slice(0, 120)}` });
    }
    for (const t of tasks) {
      const brief = String(t?.brief || "");
      if (!brief) continue;
      const cls = matchPlaceholderClass(brief);
      if (cls) push(cls === "L1a" ? defects : warnings, { plan: plan.id, taskKey: taskKey(plan.seq, t.id), rule: cls, detail: `\u5360\u4F4D\u7B26\u547D\u4E2D\uFF08${t.id} brief\uFF09: ${brief.slice(0, 120)}` });
    }
    const nums = /* @__PURE__ */ new Set();
    for (const t of tasks) {
      const m = String(t?.id || "").match(/^T(\d+)([a-z]?)$/i);
      if (m) nums.add(Number(m[1]));
    }
    if (nums.size) {
      const maxN = Math.max(...nums);
      for (let n = 1; n <= maxN; n++) {
        if (!nums.has(n)) push(warnings, { plan: plan.id, rule: "L2", detail: `task \u7F16\u53F7\u65AD\u6863\uFF1AT${n} \u7F3A\u5931\uFF08planParser \u6F0F\u6293\u6216 authoring \u8DF3\u53F7\uFF09` });
      }
    }
    const idCount = {};
    for (const t of tasks) {
      const id = String(t?.id || "");
      if (id) idCount[id] = (idCount[id] || 0) + 1;
    }
    for (const [id, c] of Object.entries(idCount)) {
      if (c > 1) push(defects, { plan: plan.id, rule: "L3", detail: `task id \u91CD\u590D\uFF1A${id} \u51FA\u73B0 ${c} \u6B21` });
    }
    if (Array.isArray(allLessons) && allLessons.length) {
      const cats = new Set(allLessons.map((l) => l && l.category).filter(Boolean));
      for (const t of tasks) {
        for (const c of Array.isArray(t?.lesson_categories) ? t.lesson_categories : []) {
          if (!cats.has(c)) push(defects, { plan: plan.id, taskKey: taskKey(plan.seq, t.id), rule: "L4", detail: `lesson_categories '${c}' \u5728 allLessons \u4E2D\u65E0\u5339\u914D\uFF08lesson \u6CE8\u5165\u5C06\u9759\u9ED8\u5931\u6548\uFF09` });
        }
      }
    }
    const idSet = new Set(tasks.map((t) => String(t?.id || "")));
    for (const t of tasks) {
      for (const dep of Array.isArray(t?.depends_on) ? t.depends_on : []) {
        if (!idSet.has(String(dep))) push(defects, { plan: plan.id, taskKey: taskKey(plan.seq, t.id), rule: "L5", detail: `depends_on \u5F15\u7528\u4E0D\u5B58\u5728\u7684 task\uFF1A${dep}` });
      }
    }
    for (const t of tasks) {
      const model = t?.model;
      if (model === void 0 || model === null || model === "") continue;
      if (!/^(sonnet|opus)$/i.test(String(model).trim())) push(defects, { plan: plan.id, taskKey: taskKey(plan.seq, t.id), rule: "L7", detail: `model \u975E\u5E38\u89C4\u503C '${model}'\uFF08\u5408\u6CD5\uFF1Asonnet|opus\uFF09` });
    }
  }
  const twf = Array.isArray(taskWriteFiles) ? taskWriteFiles : [];
  const byPlanSeq = {};
  for (const e of twf) {
    if (!e) continue;
    const seq = String(e.plan_seq ?? "");
    (byPlanSeq[seq] = byPlanSeq[seq] || []).push(e);
  }
  const planBySeq = new Map(safePlans.map((p) => [String(p?.seq ?? ""), p]));
  for (const [seq, entries] of Object.entries(byPlanSeq)) {
    const plan = planBySeq.get(seq);
    const fileOwners = {};
    for (const e of entries) {
      for (const f of Array.isArray(e.files) ? e.files : []) {
        const nf = normalizeFilePath(f).toLowerCase();
        (fileOwners[nf] = fileOwners[nf] || /* @__PURE__ */ new Set()).add(String(e.task_id));
      }
    }
    for (const [file, owners] of Object.entries(fileOwners)) {
      if (owners.size < 2) continue;
      for (const owner of owners) {
        const others = [...owners].filter((o) => o !== owner).join(", ");
        push(warnings, { plan: plan?.id ?? seq, taskKey: taskKey(seq, owner), rule: "L6", detail: `\u6587\u4EF6 ${file} \u4E5F\u88AB ${others} \u58F0\u660E\u2014\u2014\u4E0D\u8981\u56DE\u9000/\u8986\u76D6\u5176\u4ED6 task \u7684\u6539\u52A8` });
      }
    }
  }
  const by_rule = {};
  for (const f of [...defects, ...warnings]) by_rule[f.rule] = (by_rule[f.rule] || 0) + 1;
  return { defects, warnings, stats: { by_rule, placeholder_hits: { raw: phRaw, filtered: phFiltered } } };
}

// src/lib/findings-metadata.js
function normalizeFindingMetadata(f) {
  if (!f || typeof f !== "object") return f;
  const out = { ...f };
  if (out.confidence !== void 0 && out.confidence !== "unverified") delete out.confidence;
  if (out.certainty !== void 0 && !["high", "medium", "low"].includes(out.certainty)) delete out.certainty;
  if (out.ownership !== void 0 && !["local", "upstream", "unclear"].includes(out.ownership)) delete out.ownership;
  return out;
}
function partitionUnverified(findings) {
  const safe = Array.isArray(findings) ? findings : [];
  const verified = [];
  const unverified = [];
  for (const f of safe) (f?.confidence === "unverified" ? unverified : verified).push(f);
  return { verified, unverified };
}
function applyCertaintyRules(findings) {
  const safe = Array.isArray(findings) ? findings : [];
  return safe.map((f) => {
    if (!f || typeof f !== "object") return f;
    if (f.certainty !== "low") return f;
    if (f.dimension === "MISSING") return f;
    if (f.severity === "important") return { ...f, severity: "minor", kind: "certaintyDowngraded" };
    if (f.severity === "critical") return { ...f, kind: "verifyFirst" };
    return f;
  });
}
function collectLedgerCandidates(reviewer) {
  const empty = { minors: [], unverified: [] };
  if (!reviewer || reviewer.status !== "ok") return empty;
  const issues = reviewer.diagnostics?.issues;
  if (!Array.isArray(issues)) return empty;
  const minors = [];
  const unverified = [];
  for (const it of issues) {
    if (!it || typeof it !== "object") continue;
    const entry = { severity: it.severity, title: it.title || JSON.stringify(it), file: it.file, fix: it.fix };
    if (it.confidence === "unverified") unverified.push(entry);
    else if (it.severity === "minor") minors.push(entry);
  }
  return { minors, unverified };
}
function hasActionableFindings(findings) {
  const safe = Array.isArray(findings) ? findings : [];
  return safe.some((f) => f && (f.severity === "critical" || f.severity === "important" || f.severity === "minor" && f.dimension === "MISSING"));
}
function findUpstreamCritical(findings) {
  const safe = Array.isArray(findings) ? findings : [];
  return safe.find((f) => f && f.ownership === "upstream" && f.severity === "critical") || null;
}
function findUnverifiedCritical(allUnverified) {
  const safe = Array.isArray(allUnverified) ? allUnverified : [];
  return safe.find((f) => f && f.severity === "critical") || null;
}
function stripInternalMetadata(findings) {
  const safe = Array.isArray(findings) ? findings : [];
  return safe.map((f) => {
    if (!f || typeof f !== "object") return f;
    const { _scopeDowngraded, _lessonExempted, confidence, certainty, ownership, ...rest } = f;
    return rest;
  });
}
function processReviewFindings(reviewer, hunt, { applicableStandards = [], lessonIdsUsed = [], allLessons = [] } = {}) {
  const empty = { halted: false, effective: [], finalFindings: [], unverified: [], okMinors: [], okUnverified: [], hasActionable: false, upstreamCritical: null, unverifiedCritical: null, metadataActive: false };
  const haltReason = reviewHaltReason(reviewer, hunt);
  if (haltReason) return { ...empty, halted: true, reason: haltReason };
  const raw = (collectReviewFindings(reviewer, hunt) || []).map(normalizeFindingMetadata);
  const okDeferred = collectLedgerCandidates(reviewer);
  const { verified, unverified } = partitionUnverified(raw);
  const effective = applyCertaintyRules(verified);
  const scoped = arbitrateScopeConflicts(effective, applicableStandards);
  const filtered = filterLessonsExemption(scoped, lessonIdsUsed, allLessons);
  const allUnverified = [...unverified, ...okDeferred.unverified];
  return {
    halted: false,
    effective,
    finalFindings: stripInternalMetadata(filtered),
    unverified,
    okMinors: okDeferred.minors,
    okUnverified: okDeferred.unverified,
    hasActionable: hasActionableFindings(filtered),
    upstreamCritical: findUpstreamCritical(filtered),
    unverifiedCritical: findUnverifiedCritical(allUnverified),
    metadataActive: raw.some((f) => f && (f.confidence === "unverified" || f.certainty !== void 0))
  };
}
function enforceBroadSeverity(issues) {
  const safe = Array.isArray(issues) ? issues : [];
  return safe.map((i) => {
    if (!i || typeof i !== "object") return i;
    if (i.needsFix === true && i.severity === "minor") {
      return { ...i, severity: "important", _severityUpgraded: true };
    }
    return i;
  });
}

// src/state/machine.js
var TaskState = {
  PENDING: "pending",
  IN_PROGRESS: "in_progress",
  IN_REVIEW: "in_review",
  IN_FIX: "in_fix",
  COMMITTING: "committing",
  COMMITTED: "committed",
  BLOCKED: "blocked",
  HALTED: "halted"
};
var TaskEvent = {
  START: "start",
  IMPLEMENTOR_OK: "implementor_ok",
  IMPLEMENTOR_BLOCKED: "implementor_blocked",
  IMPLEMENTOR_FAILED: "implementor_failed",
  MODEL_UNAVAILABLE: "model_unavailable",
  REVIEW_ALL_GREEN: "review_all_green",
  REVIEW_HAS_FINDINGS: "review_has_findings",
  REVIEW_HALTED: "review_halted",
  OSCILLATION_DETECTED: "oscillation_detected",
  COMMIT_DONE: "commit_done"
};
var transitions = {
  [TaskState.PENDING]: {
    [TaskEvent.START]: TaskState.IN_PROGRESS
  },
  [TaskState.IN_PROGRESS]: {
    [TaskEvent.IMPLEMENTOR_OK]: TaskState.IN_REVIEW,
    [TaskEvent.IMPLEMENTOR_BLOCKED]: TaskState.BLOCKED,
    [TaskEvent.IMPLEMENTOR_FAILED]: TaskState.BLOCKED,
    [TaskEvent.MODEL_UNAVAILABLE]: TaskState.HALTED
  },
  [TaskState.IN_REVIEW]: {
    [TaskEvent.REVIEW_ALL_GREEN]: TaskState.COMMITTING,
    [TaskEvent.REVIEW_HAS_FINDINGS]: TaskState.IN_FIX,
    [TaskEvent.REVIEW_HALTED]: TaskState.HALTED,
    [TaskEvent.OSCILLATION_DETECTED]: TaskState.HALTED
  },
  [TaskState.IN_FIX]: {
    [TaskEvent.IMPLEMENTOR_OK]: TaskState.IN_REVIEW,
    [TaskEvent.IMPLEMENTOR_BLOCKED]: TaskState.BLOCKED,
    [TaskEvent.MODEL_UNAVAILABLE]: TaskState.HALTED
  },
  [TaskState.COMMITTING]: {
    [TaskEvent.COMMIT_DONE]: TaskState.COMMITTED
  }
};
function transition(currentState, event) {
  const next = transitions[currentState]?.[event];
  if (!next) throw new Error(`Invalid transition: ${currentState} + ${event}`);
  return next;
}
function createMachine(initialState, onTransition) {
  let state2 = initialState;
  return {
    send(event) {
      const prev = state2;
      state2 = transition(state2, event);
      onTransition?.({ prev, next: state2, event });
      return state2;
    },
    get state() {
      return state2;
    }
  };
}

// src/index.js
await (async () => {
  async function halt(plan, task, r) {
    const tid = plan && task?.id ? taskKey(plan.seq, task.id) : task?.id || "unknown";
    state.perTask[tid] = ensurePerTaskDefaults({
      ...state.perTask[tid] || {},
      status: "blocked",
      blocked_info: {
        plan: plan?.id,
        task: tid,
        reason: r.reason,
        category: r.diag?.blocked_category || r.diag?.file || r.diag?.reason || null,
        last_error: r.diag?.last_error || r.diag?.summary || r.reason,
        suggested_fix: r.diag?.suggested_fix || null,
        quota_exhausted: r.reason === "model_unavailable",
        likely_source: haltLikelySource(r.reason),
        failed_approach: { task_id: tid, reason: r.reason, error: r.diag?.last_error || r.reason },
        raw: r.diag || {},
        // diag 与 raw 同源异职：raw=全量 dump 兜底（保留现有消费方），diag=与 finalReport 模板 blocked_info.diag.audit_reason 读取路径对齐
        diag: r.diag || {}
      }
    });
    phase("Finalize");
    const blockedInfo = JSON.stringify(state.perTask[tid].blocked_info);
    const lessonsAutoDistill = resolveLessonsAutoDistill(state.config);
    const lessonsPath = state.config?.lessons_path || "";
    if (lessonsAutoDistill && lessonsPath) {
      const haltInfo = state.perTask[tid].blocked_info;
      const reviewHistory = state.perTask[tid]?.review_history || [];
      const failedApproaches = state.failedApproaches[tid] || [];
      const distillInput = JSON.stringify(distillLessonInput("halted", haltInfo, reviewHistory, failedApproaches));
      try {
        const distillResult = await agent(buildPrompt("lessonDistiller", { distillInput, lessonsPath }), { schema: SCHEMAS.lessonDistiller, model: "opus", label: "lesson-distiller" });
        if (distillResult?.decisions) {
          const applied = distillResult.decisions.filter((d) => d.action !== "skip").length;
          log(`\u{1F4CB} lesson distiller: ${applied} \u6761 lesson \u5DF2\u66F4\u65B0\uFF08append/update\uFF09`);
        } else {
          log("\u26A0 lesson distiller \u8FD4\u56DE\u7A7A\uFF0C\u8DF3\u8FC7 lesson \u66F4\u65B0");
        }
      } catch (e) {
        log(`\u26A0 lesson distiller \u5931\u8D25\uFF08best-effort \u8DF3\u8FC7\uFF09: ${errStr(e)}`);
      }
    }
    const fr = await finalReportWithFallback({ mode: "halted", stateJson: JSON.stringify(state), blockedInfo, runsDir: `runs/${state.runTs}`, runTs: state.runTs, lessonsPath, lessonsAutoDistill: String(lessonsAutoDistill) });
    if (!fr) log("\u2717\u2717 \u81F4\u547D\uFF1AfinalReport \u5168\u94FE\u5931\u8D25\uFF0Cmanifest \u672A\u5199\u5165\uFF01\u8BF7\u624B\u52A8\u68C0\u67E5 runs/ \u76EE\u5F55");
    log(`\u2717 HALT: ${r.reason} (plan ${plan?.id}, task ${tid})`);
    return { result: "halted", reason: r.reason };
  }
  async function checkSimplifyChanges(taskId) {
    const diffSchema = { type: "object", required: ["changed", "files"], properties: { changed: { type: "boolean" }, files: { type: "array", items: { type: "string" } } } };
    const diffResult = await safeAgent('Run `git status --porcelain` in the current working directory. If output is empty, return {"changed": false, "files": []}. Otherwise return {"changed": true, "files": [<list of file paths from porcelain output>]}.', { schema: diffSchema, label: `diff:${taskId}` });
    if (!diffResult || typeof diffResult !== "object" || typeof diffResult.changed !== "boolean" || diffResult.changed === true && !Array.isArray(diffResult.files)) {
      return { error: true, reason: "simplify diff check failed", diag: { task: taskId, diffResult: diffResult || null } };
    }
    return { error: false, changed: diffResult.changed === true, files: Array.isArray(diffResult.files) ? diffResult.files : [] };
  }
  async function amendSimplifyCommit(taskId, commitSha) {
    const amendSchema = { type: "object", required: ["ok"], properties: { ok: { type: "boolean" }, sha: { type: "string" }, error: { type: "string" } } };
    const amendResult = await safeAgent('Run `git add -A && git commit --amend --no-edit`. Then run `git rev-parse HEAD` and return JSON {"ok": true, "sha": "<40-char-hex>"}. If amend failed (e.g. pre-commit hook blocked), return {"ok": false, "sha": "", "error": "<message>"}.', { schema: amendSchema, label: `amend:${taskId}` });
    const amendCheck = validateAmendResult(amendResult);
    if (!amendCheck.valid) {
      return { error: true, reason: "simplify amend failed", diag: { task: taskId, amendError: amendCheck.error, commitSha } };
    }
    return { error: false, sha: amendCheck.sha };
  }
  async function revertSimplifyChanges(taskId, commitSha) {
    const checkoutSchema = { type: "object", required: ["ok"], properties: { ok: { type: "boolean" }, porcelain: { type: "string" }, error: { type: "string" } } };
    const checkoutResult = await safeAgent('Run `git reset --hard HEAD && git clean -fd` to discard simplify changes (both tracked modifications, staged changes, and untracked new files). Then run `git status --porcelain` to verify the working tree is clean. Return JSON {"ok": true, "porcelain": "<porcelain output>"} on success or {"ok": false, "porcelain": "<output>", "error": "<message>"} on failure.', { schema: checkoutSchema, label: `checkout:${taskId}` });
    const checkoutCheck = validateCheckoutResult(checkoutResult);
    if (!checkoutCheck.valid) {
      return { error: true, reason: "simplify checkout failed", diag: { task: taskId, checkoutError: checkoutCheck.error, commitSha } };
    }
    return { error: false };
  }
  async function runTask(plan, task) {
    state.currentTask = task.id;
    const cfg = state.config;
    const planIdShort = `plan-${String(plan.seq).padStart(2, "0")}`;
    const tk = taskKey(plan.seq, task.id);
    state.perTask[tk] = ensurePerTaskDefaults({ planId: plan.id, status: "in_progress", model: task.model || "sonnet", audit_required: task.audit_required || false });
    const _writeFiles = state.taskWriteFiles?.[tk] || [];
    const _brief = task.title || task.brief || "";
    state.perTask[tk].applicableStandards = inferApplicableStandards(_writeFiles, _brief);
    const _standardsNote = formatStandardsNote(state.perTask[tk].applicableStandards);
    log(`\u25B6 ${task.id} (${task.model || "sonnet"}): \u6D3E\u53D1 implementor \u2014 TDD \u53EF\u80FD\u542B\u957F\u547D\u4EE4(uv sync/build/\u5168\u91CF\u6D4B\u8BD5)\uFF0C\u6B63\u5E38\u8017\u65F6\u8BF7\u7B49\u5F85\uFF1B/workflows \u53EF\u770B\u5B9E\u65F6\u5DE5\u5177\u8C03\u7528`);
    const machine = createMachine(TaskState.PENDING, ({ prev, next, event }) => {
      log(`  ${task.id}: ${prev} \u2192 ${next} (${event})`);
    });
    machine.send(TaskEvent.START);
    let model = task.model || "sonnet";
    const taskCategories = task.lesson_categories || [];
    const lessonsText = formatUniversalLessons(state.allLessons || []) + formatDomainLessons(state.allLessons || [], taskCategories, planIdShort, task.title || "");
    const constraintsNote = formatConstraintsNote(plan.constraints);
    const interfacesNote = formatInterfacesNote(task.interfaces);
    const planLintNote = formatPlanLintNote(state.planLintWarnings?.[tk] || []);
    const implCtx = (fix, note, ctx = "") => ({ planId: plan.id, taskId: task.id, planFilePath: plan.file, specPath: cfg.spec_path, testCommand: cfg.test_command, buildCommand: cfg.build_command || "", fixIssues: fix, retryNote: note, fetchedContext: ctx, referencePaths: formatReferencePaths(cfg.reference_paths), failedApproaches: formatFailedApproaches(state.failedApproaches?.[tk] || []), lessons: lessonsText, constraintsNote, interfacesNote, planLintNote });
    const reviewExtraCtx = { constraintsNote, interfacesNote };
    let impl;
    const briefText = `${task.title || ""} ${task.model || ""}`;
    const auditRequired = state.perTask[tk].audit_required || AUDIT_REFACTOR_KEYWORDS.test(briefText);
    state.perTask[tk].audit_required = auditRequired;
    impl = await dispatchImpl(buildPrompt("implementor", { ...implCtx("", ""), auditDirective: auditRequired ? AUDIT_DIRECTIVE : "" }), { schema: SCHEMAS.implementor, model, label: `impl:${task.id}` }, model, "opus");
    if (impl.halted) return impl;
    if (impl.status === "blocked") {
      if (model === "opus") {
        machine.send(TaskEvent.IMPLEMENTOR_BLOCKED);
        return { halted: true, reason: "opus BLOCKED", diag: impl.diagnostics };
      }
      model = "opus";
      impl = await dispatchImpl(buildPrompt("implementor", implCtx("", "\u4E0A\u4E00\u8F6E sonnet BLOCKED\uFF0C\u5347\u7EA7 opus \u91CD\u8BD5\u3002")), { schema: SCHEMAS.implementor, model: "opus", label: `impl:${task.id}:opus` }, "opus");
      if (impl.halted) return impl;
      if (impl.status === "blocked") {
        machine.send(TaskEvent.IMPLEMENTOR_BLOCKED);
        return { halted: true, reason: "opus BLOCKED", diag: impl.diagnostics };
      }
    }
    if (impl.status === "needs_context") {
      const ctxr = await dispatchImpl(buildPrompt("contextFetcher", {
        needType: impl.diagnostics?.blocked_category || "file",
        query: impl.diagnostics?.last_error || impl.diagnostics?.suggested_fix || "",
        specPath: cfg.spec_path,
        workdir: "."
      }), { schema: SCHEMAS.contextFetcher, label: `ctx:${task.id}` }, "sonnet");
      if (ctxr.halted) return ctxr;
      const fetchedCtx = ctxr.diagnostics?.context || "";
      impl = await dispatchImpl(buildPrompt("implementor", implCtx("", `\u8865\u5145\u4E0A\u4E0B\u6587\u540E\u91CD\u8BD5\u3002`, fetchedCtx)), { schema: SCHEMAS.implementor, model, label: `impl:${task.id}:ctx` }, model, "opus");
      if (impl.halted) return impl;
      if (impl.status === "blocked") {
        if (model === "opus") return { halted: true, reason: "opus BLOCKED after context-fetch", diag: impl.diagnostics };
        model = "opus";
        impl = await dispatchImpl(buildPrompt("implementor", implCtx("", "\u4E0A\u4E0B\u6587\u8865\u5145\u540E sonnet \u4ECD BLOCKED\uFF0C\u5347\u7EA7 opus \u91CD\u8BD5\u3002", fetchedCtx)), { schema: SCHEMAS.implementor, model: "opus", label: `impl:${task.id}:ctx:opus` }, "opus");
        if (impl.halted) return impl;
        if (impl.status === "blocked") return { halted: true, reason: "opus BLOCKED after context-fetch", diag: impl.diagnostics };
      }
      if (impl.status === "failed") {
        impl = await dispatchImpl(buildPrompt("implementor", implCtx("", "\u4E0A\u4E0B\u6587\u8865\u5145\u540E\u4ECD failed\uFF0C\u91CD\u8BD5\u4E00\u6B21\u3002", fetchedCtx)), { schema: SCHEMAS.implementor, model, label: `impl:${task.id}:ctx:retry` }, model, "opus");
        if (impl.halted) return impl;
        const h1 = checkImplStatus(impl, void 0, "implementor {status} after context-fetch retry");
        if (h1) return h1;
      }
      const h2 = checkImplStatus(impl, void 0, "implementor {status} after context-fetch");
      if (h2) return h2;
    }
    if (impl.status === "failed") {
      impl = await dispatchImpl(buildPrompt("implementor", implCtx("", "\u4E0A\u6B21 failed\uFF0C\u91CD\u8BD5\u4E00\u6B21\u3002")), { schema: SCHEMAS.implementor, model, label: `impl:${task.id}:retry` }, model, "opus");
      if (impl.halted) return impl;
      const h3 = checkImplStatus(impl, void 0, "implementor {status} after retry");
      if (h3) return h3;
    }
    let concerns = [];
    if (impl.status === "done_with_concerns") {
      concerns = coerceConcerns(impl.diagnostics?.concerns || []);
      state.perTask[tk].concerns = concerns;
      log(`\u26A0 ${task.id} done_with_concerns: ${concerns.map((c) => `[${c.severity}] ${c.text}`).join("; ") || "(no detail)"}`);
    }
    let concernsHint = formatConcernsHint(concerns);
    let filesChanged = impl.evidence.files_changed || [];
    let latestImplEvidence = impl.evidence || {};
    state.perTask[tk].lessonIdsUsed = impl.evidence?.lesson_ids_used || [];
    machine.send(TaskEvent.IMPLEMENTOR_OK);
    const maxRounds = resolveMaxRounds(cfg);
    for (let round = 1; maxRounds === 0 ? true : round <= maxRounds; round++) {
      const fc = filesChanged.join("\n");
      const { reviewer, hunt, haltReason: reviewReason, emptyFailed: emptyFailedReason } = await runReviewRound(task.id, cfg, plan, fc, concernsHint, `:r${round}`, `Plan ${plan.id}`, _standardsNote, concerns, { ...reviewExtraCtx, implementorEvidenceNote: formatImplementorEvidenceNote(latestImplEvidence, round === 1 ? "initial implementation" : `fix round ${round - 1}`) });
      const processed = processReviewFindings(reviewer, hunt, {
        applicableStandards: state.perTask[tk]?.applicableStandards || [],
        lessonIdsUsed: state.perTask[tk]?.lessonIdsUsed,
        allLessons: state.allLessons
      });
      const metaRound = !reviewReason && !emptyFailedReason;
      const ledgerAppend = (items, kind) => {
        const ledger = state.perTask[tk].deferredLedger;
        for (const f of items) {
          if (ledger.some((e) => e.kind === kind && e.title === f.title)) continue;
          ledger.push({ kind, severity: f.severity, title: f.title, file: f.file, fix: f.fix, source: f.source || "reviewer", taskKey: tk, sourceRound: round, originalTitle: f.title, originalFix: f.fix });
        }
      };
      if (metaRound) {
        if (processed.unverifiedCritical) {
          log(`\u26A0 unverified-critical-halt: task ${task.id} \u2014 ${processed.unverifiedCritical.title} (reviewer \u4E0D\u53EF\u6838\u5B9E\u4F46 severity=critical\uFF0C\u9700\u4EBA\u5DE5\u5BA1\u67E5)`);
          machine.send(TaskEvent.REVIEW_HALTED);
          return { halted: true, reason: "unverified_critical_halt", diag: { finding: processed.unverifiedCritical } };
        }
        ledgerAppend(processed.unverified, "unverified");
        ledgerAppend(processed.okUnverified, "unverified");
        ledgerAppend(processed.okMinors, "minor");
        if (processed.upstreamCritical) {
          log(`\u26A0 upstream-defect-halt: task ${task.id} \u2014 ${processed.upstreamCritical.title}`);
          machine.send(TaskEvent.REVIEW_HALTED);
          return { halted: true, reason: "upstream_defect_halt", diag: { finding: processed.upstreamCritical } };
        }
      }
      const { currentFindings } = recordReviewRound(state, tk, round, reviewer, hunt, metaRound ? processed.effective : null);
      if (reviewer?.diagnostics?.concerns_addressed) {
        state.perTask[tk].concernVerdicts = reviewer.diagnostics.concerns_addressed;
      }
      const outcome = decideReviewOutcome(state, tk, round, reviewer, hunt, model, maxRounds, cfg, reviewReason, emptyFailedReason);
      if (outcome.action === "halt") {
        if (["OSCILLATING", "review_not_converging", "review max rounds"].includes(outcome.reason)) {
          const fhist = formatFindingsHistory(state.perTask[tk].findings_history || [], round);
          let findingsHistoryText = fhist;
          if (outcome.diag.regressedFindings?.length) {
            const regressedMd = outcome.diag.regressedFindings.map((h) => `- [REGRESSED] ${h.title} (r${h.first_seen}\u2192r${h.last_seen}${h.file ? `, ${h.file}` : ""})`).join("\n");
            findingsHistoryText = `${fhist ? fhist + "\n\n" : ""}### [REGRESSED] \u89E6\u53D1 halt \u7684\u56DE\u5F52\u9879
${regressedMd}`;
          }
          findingsHistoryText = (findingsHistoryText || JSON.stringify(state.perTask[tk].findings_history || [])).slice(0, 3e3);
          const filesTouchedText = (state.perTask[tk].files_touched_per_round || []).map((files, i) => `round ${i + 1}: ${(files || []).join(", ")}`).join("; ").slice(0, 500);
          const taskBriefText = (task.brief || task.title || "").slice(0, 500);
          const dbg = await safeAgent(buildPrompt("debugger", { taskId: task.id, reason: outcome.reason, findingsHistory: findingsHistoryText, taskBrief: taskBriefText, filesTouched: filesTouchedText, quotaHaltNote: "" }), { schema: SCHEMAS.debugger, model: "sonnet", label: `debug:${task.id}` });
          if (dbg?.status === "ok") {
            outcome.diag.root_cause_category = dbg.diagnostics?.root_cause_category;
            outcome.diag.debugger_analysis = dbg.diagnostics?.analysis;
            if (outcome.diag.suggested_fix === void 0) outcome.diag.suggested_fix = dbg.diagnostics?.suggested_action;
          } else {
            log(`\u26A0 ${task.id}: debugger \u6839\u56E0\u5206\u7C7B\u5931\u8D25\uFF08${dbg?.status || "no response"}\uFF09\u2014\u2014\u6309\u539F halt \u7EE7\u7EED\uFF08best-effort\uFF09`);
          }
        }
        if (outcome.reason === "OSCILLATING") {
          machine.send(TaskEvent.OSCILLATION_DETECTED);
          if (Array.isArray(outcome.diag.regressedFindings)) log(`\u26A0 ${task.id}: r${round} OSCILLATING halt \u2014 regressed finding(s) reappeared after being fixed (v3)`);
          else log(`\u26A0 ${task.id}: r${round} OSCILLATING halt \u2014 reviewer flip-flop detected (same finding title reappears across rounds) (v3)`);
        } else {
          machine.send(TaskEvent.REVIEW_HALTED);
        }
        return { halted: true, reason: outcome.reason, diag: outcome.diag };
      }
      if (outcome.action === "break") {
        machine.send(TaskEvent.REVIEW_ALL_GREEN);
        break;
      }
      if (metaRound && processed.metadataActive && !processed.hasActionable) {
        const bMinors = processed.finalFindings.filter((f) => f.severity === "minor" || !f.severity);
        ledgerAppend(bMinors, "minor");
        log(`B-break: task ${task.id} at round ${round} \u2014 no actionable findings; ${bMinors.length} minor entries deferred`);
        machine.send(TaskEvent.REVIEW_ALL_GREEN);
        break;
      }
      machine.send(TaskEvent.REVIEW_HAS_FINDINGS);
      if (outcome.action === "escalate") {
        state.perTask[tk].opus_escalated = true;
        state.perTask[tk].oscillation_escalated_at_round = round;
        model = outcome.model;
        log(`\u26A0 ${task.id}: r${round} OSCILLATING (new-findings \u8865\u5145, flipFlop=false) \u2014 escalate to opus, continue (v3)`);
      } else if (outcome.action === "continue") {
        log(`\u26A0 ${task.id}: r${round} OSCILLATING (flipFlop=false, opus already escalated) \u2014 continue until budget guard (v3)`);
      }
      const fixResult = await runFixRound(tk, plan, task, round, reviewer, hunt, state, cfg, implCtx, model, maxRounds, concerns, concernsHint, processed);
      if (fixResult.halted) {
        if (fixResult.reason) return { halted: true, reason: fixResult.reason, diag: fixResult.impl.diagnostics };
        return fixResult.impl;
      }
      concerns = fixResult.concerns;
      concernsHint = fixResult.concernsHint;
      filesChanged = fixResult.filesChanged || filesChanged;
      latestImplEvidence = fixResult.impl?.evidence || latestImplEvidence;
      machine.send(TaskEvent.IMPLEMENTOR_OK);
    }
    const commit = await dispatchImpl(buildPrompt("commit", { taskId: task.id, planId: plan.id, planIdShort, commitMsg: commitSubject(plan.seq, task.id, task.title || task.id), testCommand: cfg.test_command, writeFilesScope: formatWriteFilesScope(state.taskWriteFiles?.[tk] || []) }), { schema: SCHEMAS.commit, label: `commit:${task.id}` }, "sonnet");
    if (commit.halted) return commit;
    if (commit.status === "failed" && Array.isArray(commit.diagnostics?.out_of_scope) && commit.diagnostics.out_of_scope.length) return { halted: true, reason: "commit out_of_scope", diag: commit.diagnostics };
    if (commit.status !== "ok") return { halted: true, reason: "commit failed", diag: commit.diagnostics };
    state.perTask[tk].status = "committed";
    state.perTask[tk].commit_sha = commit.evidence.commit_sha;
    machine.send(TaskEvent.COMMIT_DONE);
    log(`\u2713 ${task.id} committed @ ${commit.evidence.commit_sha}`);
    const simp = await dispatchImpl(buildPrompt("simplify", { taskId: task.id, filesChanged: filesChanged.join("\n") }), { schema: SCHEMAS.simplify, label: `simp:${task.id}` }, "sonnet");
    if (simp.halted) return simp;
    const diffCheck = await checkSimplifyChanges(task.id);
    if (diffCheck.error) return { halted: true, reason: diffCheck.reason, diag: diffCheck.diag };
    if (diffCheck.changed) {
      const fc = diffCheck.files.join("\n");
      const { reviewer: reviewer2, hunt: hunt2, haltReason: simpReviewReason, emptyFailed: simpEmptyFailed } = await runReviewRound(task.id, cfg, plan, fc, "", ":simp", "", _standardsNote, [], reviewExtraCtx);
      if (simpReviewReason) return { halted: true, reason: simpReviewReason, diag: { reviewer2: reviewer2?.diagnostics, hunt2: hunt2?.diagnostics } };
      if (simpEmptyFailed) return { halted: true, reason: simpEmptyFailed, diag: { reviewer2: reviewer2?.diagnostics, hunt2: hunt2?.diagnostics } };
      if (allGreen(reviewer2, hunt2)) {
        const amend = await amendSimplifyCommit(task.id, commit.evidence.commit_sha);
        if (amend.error) return { halted: true, reason: amend.reason, diag: amend.diag };
        state.perTask[tk].commit_sha = amend.sha;
        log(`\u2713 ${task.id} simplify review green \u2014 amended commit @ ${amend.sha}`);
      } else {
        const revert = await revertSimplifyChanges(task.id, commit.evidence.commit_sha);
        if (revert.error) return { halted: true, reason: revert.reason, diag: revert.diag };
        log(`\u26A0 ${task.id} simplify review NOT green \u2014 reverted simplify changes (HEAD unchanged @ ${commit.evidence.commit_sha})`);
        state.perTask[tk].simplify_reverted = true;
        const rawSimpFindings = collectReviewFindings(reviewer2, hunt2);
        const scopedSimpFindings = arbitrateScopeConflicts(rawSimpFindings, state.perTask[tk]?.applicableStandards || []);
        state.perTask[tk].simplify_review_findings = filterLessonsExemption(scopedSimpFindings, state.perTask[tk]?.lessonIdsUsed, state.allLessons);
      }
    }
    const destructive = commit.diagnostics?.destructive_changes;
    if (Array.isArray(destructive) && destructive.length) {
      log(`\u26A0 ${task.id} destructive_changes detected (${destructive.length}): ${destructive.map((d) => `${d.type}:${d.file}`).join(", ")} \u2014 \u89E6\u53D1\u989D\u5916 review round`);
      const fc = (commit.evidence.committed_files || []).join("\n");
      const { reviewer: dReviewer, hunt: dHunt, haltReason: dReason, emptyFailed: dEmptyFailed } = await runReviewRound(task.id, cfg, plan, fc, "", ":destructive", `Plan ${plan.id}`, _standardsNote, [], reviewExtraCtx);
      if (dReason || dEmptyFailed) {
        state.perTask[tk].destructive_review_failed = true;
        state.perTask[tk].destructive_review_findings = [{ source: "destructive-review", severity: "critical", title: dReason || dEmptyFailed, fix: "investigate review agent failure" }];
        log(`\u26A0 ${task.id} destructive review \u5F02\u5E38 (${dReason || dEmptyFailed}) \u2014 \u8BB0\u5F55\u5E76\u7EE7\u7EED`);
      } else if (!allGreen(dReviewer, dHunt)) {
        state.perTask[tk].destructive_review_failed = true;
        const rawDestructiveFindings = collectReviewFindings(dReviewer, dHunt);
        const scopedDestructiveFindings = arbitrateScopeConflicts(rawDestructiveFindings, state.perTask[tk]?.applicableStandards || []);
        state.perTask[tk].destructive_review_findings = filterLessonsExemption(scopedDestructiveFindings, state.perTask[tk]?.lessonIdsUsed, state.allLessons);
        log(`\u26A0 ${task.id} destructive review NOT green \u2014 \u8BB0\u5F55 ${state.perTask[tk].destructive_review_findings.length} \u9879 findings \u5E76\u7EE7\u7EED\uFF08\u4E0D halt\uFF09`);
      } else {
        log(`\u2713 ${task.id} destructive review green \u2014 \u7EE7\u7EED\u6B63\u5E38\u6D41\u7A0B`);
      }
    }
    state.perTask[tk].status = "done";
    return { halted: false };
  }
  phase("Bootstrap");
  if (!args) throw new Error("args must be a non-null object (Workflow runtime contract)");
  if (typeof args === "string") {
    try {
      args = JSON.parse(args);
    } catch (parseErr) {
      throw new Error(`args was a string but failed JSON.parse: ${parseErr.message}`);
    }
  }
  if (typeof args.configPath !== "string" || !args.configPath.trim()) {
    throw new Error("args.configPath must be a non-empty string (workflow.config.json path)");
  }
  if (typeof args.plansDir !== "string" || !args.plansDir.trim()) {
    throw new Error("args.plansDir must be a non-empty string (plans directory path)");
  }
  let tsAgent;
  try {
    tsAgent = await agent("Run `date -u +%Y%m%dT%H%M%SZ` and return ONLY the timestamp string, nothing else.", { label: "get-ts" });
  } catch (e) {
    log(`\u26A0 get-ts agent \u629B\u9519\uFF08\u975E quota\uFF09\uFF0C\u964D\u7EA7\u7528 'unknown-ts' \u5360\u4F4D\u7B26: ${errStr(e)}`);
    tsAgent = "unknown-ts";
  }
  if (typeof tsAgent !== "string" || !tsAgent.trim()) tsAgent = "unknown-ts";
  state.runTs = tsAgent.trim();
  let boot, parsed;
  try {
    ;
    [boot, parsed] = await parallel([
      async () => dispatchImpl(
        buildPrompt("bootstrap", { configPath: args.configPath, plansDir: args.plansDir, runTs: state.runTs }),
        { schema: SCHEMAS.bootstrap, label: "bootstrap" },
        "sonnet",
        "opus"
      ),
      async () => dispatchImpl(
        buildPrompt("planParser", { plansDir: args.plansDir }),
        { schema: SCHEMAS.planParser, label: "plan-parser" },
        "sonnet",
        "opus"
      )
    ]);
  } catch (e) {
    return await halt(null, null, { reason: "agent_error", diag: { model: "sonnet", error: errStr(e) } });
  }
  if (boot.halted) {
    return await halt(null, null, { reason: boot.reason, diag: boot.diag });
  }
  if (boot.status !== "ok") {
    return await halt(null, null, { reason: `bootstrap ${boot.status}`, diag: boot.diagnostics });
  }
  if (!parsed || parsed.status === "failed" || parsed.status === "model_unavailable" || parsed.halted) {
    log(`  bootstrap: planParser \u5931\u8D25 (${parsed?.status || "no response"})\uFF0C\u65E0\u6CD5\u7EE7\u7EED`);
    return await halt(null, null, { reason: `planParser ${parsed?.status || "no response"}`, diag: parsed?.diagnostics || {} });
  }
  if (boot.evidence?.dirty_tree) {
    return await halt(null, null, { reason: "bootstrap dirty_tree cleanup failed", diag: { summary: boot.summary || "dirty_tree=true after bootstrap step 5 classification" } });
  }
  state.config = boot.evidence.config;
  state.currentHeadSha = boot.evidence.current_head_sha || "";
  state.plans = parsed.evidence.plans;
  for (const p of state.plans || []) {
    if (Array.isArray(p.tasks)) {
      for (const t of p.tasks) t.id = bareTaskId(t.id);
      p.tasks = dropParentTasks(p.tasks);
    }
  }
  for (const fa of boot.evidence.failed_approaches || []) fa.task_id = bareTaskId(fa.task_id);
  for (const twf of parsed.evidence.task_write_files || []) twf.task_id = bareTaskId(twf.task_id);
  const _regexCompleted = Array.isArray(boot.evidence.git_log_subjects) && boot.evidence.git_log_subjects.length ? extractCompletedFromSubjects(boot.evidence.git_log_subjects) : [];
  const _llmCompleted = Array.isArray(boot.evidence.completed) ? boot.evidence.completed : [];
  const _argsCompleted = Array.isArray(args.completed) ? args.completed : [];
  const _rawCompleted = [.../* @__PURE__ */ new Set([..._argsCompleted, ..._regexCompleted, ..._llmCompleted])];
  state.completed = normalizeCompleted(_rawCompleted);
  log(`bootstrap: completed merge \u2014 args=${_argsCompleted.length}, regex=${_regexCompleted.length}, llm=${_llmCompleted.length} \u2192 total=${state.completed.length}`);
  if (Array.isArray(boot.evidence.failed_approaches)) {
    for (const fa of boot.evidence.failed_approaches) {
      const faKey = fa.task_id.includes("/") ? fa.task_id : taskKey(fa.plan_seq, fa.task_id);
      if (!state.failedApproaches[faKey]) state.failedApproaches[faKey] = [];
      state.failedApproaches[faKey].push(fa);
    }
  }
  if (Array.isArray(parsed.evidence.task_write_files)) {
    for (const twf of parsed.evidence.task_write_files) {
      state.taskWriteFiles[taskKey(twf.plan_seq, twf.task_id)] = twf.files || [];
    }
  }
  if (Array.isArray(boot.evidence.all_lessons)) {
    state.allLessons = boot.evidence.all_lessons;
  }
  const lintResult = lintPlans(state.plans, parsed.evidence.task_write_files || [], { allLessons: state.allLessons || [] });
  state.planLint = { defects: lintResult.defects, warnings: lintResult.warnings, ...lintResult.stats };
  const lintPlanById = new Map((state.plans || []).map((p) => [p.id, p]));
  for (const w of lintResult.warnings) {
    const targets = w.taskKey ? [w.taskKey] : (lintPlanById.get(w.plan)?.tasks || []).map((t) => taskKey(lintPlanById.get(w.plan).seq, t.id));
    for (const k of targets) (state.planLintWarnings[k] = state.planLintWarnings[k] || []).push({ rule: w.rule, detail: w.detail });
  }
  if (lintResult.warnings.length) log(`\u26A0 plan lint: ${lintResult.warnings.length} warning(s) ${JSON.stringify(lintResult.stats.by_rule)}`);
  if (lintResult.defects.length) {
    log(`\u2717 plan lint failed: ${lintResult.defects.length} defect(s) ${JSON.stringify(lintResult.stats.by_rule)}`);
    return await halt(null, null, { reason: "plan lint failed", diag: { defects: lintResult.defects } });
  }
  for (const plan of state.plans) {
    state.perPlan[plan.id] = {
      startSha: null,
      broadCommitSha: null,
      broadReviewHistory: [],
      broadFindingsHistory: [],
      broadFindings: [],
      broadSeverityUpgrades: 0,
      // Hunter #3 修复（2026-07-19）：broad review needsFix+minor 升级 important 计数（manifest 审计）
      deferredLedgerUnadjudicatedCount: 0
      // Hunter #4 修复（2026-07-19）：broad review 失败时未裁定 deferred 项数（manifest 审计）
    };
  }
  for (const plan of state.plans) {
    if (!matchesPlanFilter(plan, args.plan)) continue;
    state.currentPlan = plan.id;
    phase(`Plan ${plan.id}`);
    state.perPlan[plan.id].startSha = state.currentHeadSha || "";
    const want = Array.isArray(args.tasks) && args.tasks.length ? new Set(args.tasks.map(String)) : null;
    const tasks = plan.tasks.filter((t) => !want || want.has(t.id));
    for (const task of tasks) {
      const tk = taskKey(plan.seq, task.id);
      if (state.completed.includes(tk)) {
        log(`skip ${tk} (already committed)`);
        continue;
      }
      let r;
      try {
        r = await runTask(plan, task);
      } catch (e) {
        const reason = isQuotaError(e) ? "model_unavailable" : "agent_error";
        r = { halted: true, reason, diag: { model: task.model || "sonnet", error: errStr(e) } };
      }
      if (r.halted) {
        return await halt(plan, { id: task.id }, r);
      }
    }
    const planStartSha = state.perPlan[plan.id]?.startSha || state.currentHeadSha || "";
    let lastSha = null;
    for (let i = plan.tasks.length - 1; i >= 0; i--) {
      const tk = taskKey(plan.seq, plan.tasks[i].id);
      if (state.perTask[tk]?.commit_sha) {
        lastSha = state.perTask[tk].commit_sha;
        break;
      }
    }
    if (lastSha) {
      const cfg = state.config;
      const maxBroadRounds = 2;
      let broadHalt = false;
      let broadHaltReason = "";
      const planPrefix = `plan-${plan.seq}/`;
      const planLedger = Object.entries(state.perTask).filter(([k]) => k.startsWith(planPrefix)).flatMap(([, v]) => v.deferredLedger || []);
      state.perPlan[plan.id].planDeferredLedger = planLedger;
      const broad0 = await safeAgent(
        buildPrompt("broadReviewer", {
          planId: plan.id,
          mergeBaseSha: planStartSha,
          headSha: lastSha,
          applicableStandardsNote: "",
          deferredFindingsNote: formatDeferredFindingsNote(planLedger),
          quotaHaltNote: QUOTA_HALT_NOTE
        }),
        { schema: SCHEMAS.broadReviewer, model: "opus", label: `broad:${plan.id}:0`, phase: `Plan ${plan.id}` }
      );
      if (!broad0 || broad0.status !== "ok" && broad0.status !== "failed") {
        state.perPlan[plan.id].broadReviewFailed = true;
        state.perPlan[plan.id].deferredLedgerUnadjudicatedCount = planLedger.length;
        log(`\u26A0 broad review round 0 failed (${broad0?.status || "no response"}) for ${plan.id} \u2014 ${planLedger.length} deferred items unadjudicated, recorded in manifest, continuing`);
      }
      let broadActionable = [];
      if (broad0?.status === "failed") {
        let broad0Issues = broad0.diagnostics?.issues || [];
        const broad0Upgrades = broad0Issues.filter((i) => i?.needsFix === true && i?.severity === "minor").length;
        if (broad0Upgrades > 0) {
          broad0Issues = enforceBroadSeverity(broad0Issues);
          state.perPlan[plan.id].broadSeverityUpgrades = (state.perPlan[plan.id].broadSeverityUpgrades || 0) + broad0Upgrades;
          log(`\u26A0 broad review round 0 for ${plan.id}: ${broad0Upgrades} needsFix+minor upgraded to important (Hunter #3)`);
        }
        broadActionable = broad0Issues.filter((i) => i.needsFix !== false && (i.severity === "critical" || i.severity === "important"));
      }
      if (broadActionable.length > 0) {
        for (let broadRound = 1; broadRound <= maxBroadRounds; broadRound++) {
          log(`\u25B6 broad review fix round ${broadRound}/${maxBroadRounds} for ${plan.id}`);
          const prevSha = broadRound === 1 ? planStartSha : state.perPlan[plan.id].broadCommitSha;
          const broadActionableForPrompt = broadActionable.map(({ _severityUpgraded, ...rest }) => rest);
          const broadFixIssues = formatFindingsHistory(state.perPlan[plan.id].broadFindingsHistory, broadRound) || JSON.stringify(broadActionableForPrompt);
          const broadImplCtx = (fix, note) => ({
            planId: plan.id,
            taskId: `${plan.id}-broad-fix`,
            planFilePath: plan.file,
            specPath: cfg.spec_path,
            testCommand: cfg.test_command,
            buildCommand: cfg.build_command || "",
            fixIssues: fix,
            retryNote: note,
            fetchedContext: "",
            referencePaths: formatReferencePaths(cfg.reference_paths),
            failedApproaches: "",
            lessons: ""
          });
          const broadFixImpl = await dispatchImpl(
            buildPrompt("implementor", broadImplCtx(broadFixIssues, `broad review round ${broadRound} \u2014 fix cross-task integration issues`)),
            { schema: SCHEMAS.implementor, model: "sonnet", label: `broad-fix:${plan.id}:${broadRound}` },
            "sonnet",
            "opus"
          );
          if (broadFixImpl.halted) {
            broadHalt = true;
            broadHaltReason = broadFixImpl.reason || "broad fix implementor halted";
            break;
          }
          if (broadFixImpl.status === "blocked" || broadFixImpl.status === "failed" || broadFixImpl.status === "needs_context") {
            broadHalt = true;
            broadHaltReason = `broad fix implementor ${broadFixImpl.status}`;
            break;
          }
          const broadCommit = await dispatchImpl(
            buildPrompt("commit", {
              taskId: `${plan.id}-broad-fix-${broadRound}`,
              planId: plan.id,
              commitMsg: `fix: broad review round ${broadRound}`,
              testCommand: cfg.test_command,
              writeFilesScope: "",
              quotaHaltNote: QUOTA_HALT_NOTE
            }),
            { schema: SCHEMAS.commit, label: `broad-commit:${plan.id}:${broadRound}` },
            "sonnet"
          );
          if (broadCommit.halted) {
            broadHalt = true;
            broadHaltReason = broadCommit.reason;
            break;
          }
          if (broadCommit.status !== "ok") {
            broadHalt = true;
            broadHaltReason = `broad commit ${broadCommit.status}`;
            break;
          }
          state.perPlan[plan.id].broadCommitSha = broadCommit.evidence?.commit_sha;
          log(`\u2713 broad fix round ${broadRound} committed @ ${state.perPlan[plan.id].broadCommitSha}`);
          const diffQuery = `${prevSha}..${state.perPlan[plan.id].broadCommitSha}`;
          const diffResult = await safeAgent(
            buildPrompt("contextFetcher", { needType: "diff_files", query: diffQuery, specPath: "", workdir: "" }),
            { schema: SCHEMAS.contextFetcher, model: "sonnet", label: `diff-scan:${plan.id}:${broadRound}` }
          );
          const broadFilesChanged = (diffResult?.diagnostics?.context || "").trim().split("\n").filter(Boolean);
          const broadFc = broadFilesChanged.join("\n");
          const { reviewer: bReviewer, hunt: bHunt, haltReason: bHaltReason, emptyFailed: bEmptyFailed } = await runReviewRound(`${plan.id}-broad`, cfg, plan, broadFc, "", `:broad${broadRound}`, `Plan ${plan.id}`, "");
          if (bHaltReason || bEmptyFailed) {
            broadHalt = true;
            broadHaltReason = bHaltReason || bEmptyFailed;
            break;
          }
          state.perPlan[plan.id].broadReviewHistory.push(summarizeReviewRound(broadRound, bReviewer, bHunt));
          const narrowFindings = collectReviewFindings(bReviewer, bHunt);
          state.perPlan[plan.id].broadFindingsHistory = updateFindingsHistory(
            state.perPlan[plan.id].broadFindingsHistory,
            narrowFindings,
            broadRound
          );
          const flipFlop = isFlipFlop(state.perPlan[plan.id].broadReviewHistory);
          const regressed = hasRegressed(state.perPlan[plan.id].broadFindingsHistory);
          if (flipFlop || regressed) {
            broadHalt = true;
            broadHaltReason = "OSCILLATING";
            break;
          }
          const broadRe = await safeAgent(
            buildPrompt("broadReviewer", {
              planId: plan.id,
              mergeBaseSha: planStartSha,
              headSha: state.perPlan[plan.id].broadCommitSha,
              applicableStandardsNote: "",
              quotaHaltNote: QUOTA_HALT_NOTE
            }),
            { schema: SCHEMAS.broadReviewer, model: "opus", label: `broad:${plan.id}:${broadRound}`, phase: `Plan ${plan.id}` }
          );
          if (broadRe?.status === "ok") {
            log(`\u2713 broad review round ${broadRound} green for ${plan.id}`);
            break;
          }
          const reIssuesRaw = broadRe?.diagnostics?.issues || [];
          const reUpgrades = reIssuesRaw.filter((i) => i?.needsFix === true && i?.severity === "minor").length;
          let reIssues = reIssuesRaw;
          if (reUpgrades > 0) {
            reIssues = enforceBroadSeverity(reIssuesRaw);
            state.perPlan[plan.id].broadSeverityUpgrades = (state.perPlan[plan.id].broadSeverityUpgrades || 0) + reUpgrades;
            log(`\u26A0 broad review round ${broadRound} for ${plan.id}: ${reUpgrades} needsFix+minor upgraded to important (Hunter #3)`);
          }
          const reActionable = reIssues.filter((i) => i.needsFix !== false && (i.severity === "critical" || i.severity === "important"));
          state.perPlan[plan.id].broadFindings = reIssues;
          if (reActionable.length === 0) {
            log(`\u26A0 broad review round ${broadRound}: only minor issues, continuing to plan gate`);
            break;
          }
          if (broadRound === maxBroadRounds) {
            broadHalt = true;
            broadHaltReason = "broad review max rounds";
            break;
          }
          broadActionable = reActionable;
        }
      } else {
        state.perPlan[plan.id].broadFindings = broad0?.diagnostics?.issues || [];
      }
      if (broadHalt) {
        return await halt(plan, null, { reason: broadHaltReason, diag: { broadReview: true, planId: plan.id } });
      }
      if (state.perPlan[plan.id].broadCommitSha) {
        const headResult = await safeAgent(
          buildPrompt("contextFetcher", { needType: "head_sha", query: "", specPath: "", workdir: "" }),
          { schema: SCHEMAS.contextFetcher, model: "sonnet", label: `head-sha:${plan.id}` }
        );
        const broadHeadSha = (headResult?.diagnostics?.context || "").trim();
        if (broadHeadSha && /^[0-9a-f]{40}$/i.test(broadHeadSha)) {
          lastSha = broadHeadSha;
        } else {
          lastSha = state.perPlan[plan.id].broadCommitSha;
        }
      }
    }
    let gateSha = lastSha;
    if (!gateSha) {
      const headResult = await safeAgent(
        buildPrompt("contextFetcher", { needType: "head_sha", query: "", specPath: "", workdir: "" }),
        { schema: SCHEMAS.contextFetcher, model: "sonnet", label: `head-sha:${plan.id}` }
      );
      const headSha = (headResult?.diagnostics?.context || "").trim();
      if (headSha && /^[0-9a-f]{40}$/i.test(headSha) && headSha !== planStartSha) {
        gateSha = headSha;
        log(`plan ${plan.id}: no new task commits, but HEAD advanced to ${headSha.slice(0, 8)} since plan start \u2014 re-running gate to verify non-task fixes`);
      }
    }
    if (gateSha) {
      const cmds = gateCommands(state.config);
      let gate;
      try {
        gate = await dispatchImpl(buildPrompt("gate", { sha: gateSha, gateCommands: JSON.stringify(cmds), schemaCheck: formatSchemaCheck(state.config?.schema_tool || "", state.config?.model_paths || [], state.config?.migration_paths || []) }), { schema: SCHEMAS.gate, label: `gate:${plan.id}`, phase: `Plan ${plan.id}` }, "sonnet");
      } catch (e) {
        return await halt(plan, null, { reason: "agent_error", diag: { model: "sonnet", error: errStr(e) } });
      }
      if (gate.halted) {
        return await halt(plan, null, { reason: gate.reason, diag: gate.diag });
      }
      if (gate.status !== "ok" || gate.evidence?.migration_missing) {
        const failedCmds = (gate.evidence?.lint_results || []).filter((r) => r && r.exit_code !== 0);
        const gateSummary = failedCmds.length ? failedCmds.map((r) => `${r.command} \u2192 exit ${r.exit_code}${r.summary ? `: ${r.summary}` : ""}`).join("; ") : gate.evidence?.pytest_summary;
        return await halt(plan, null, { reason: "plan gate failed", diag: { sha: gateSha, tests_exit_code: gate.evidence?.tests_exit_code, summary: gateSummary, lint_results: gate.evidence?.lint_results, migration_missing: gate.evidence?.migration_missing } });
      }
      if (state.config?.smoke_command) {
        const lintResults = gate.evidence?.lint_results || [];
        if (!lintResults.some((r) => r.command === state.config.smoke_command)) {
          return await halt(plan, null, { reason: "gate incomplete: smoke missing", diag: { sha: gateSha, smoke_command: state.config.smoke_command, lint_results: lintResults } });
        }
      }
      const headVerify = await dispatchImpl(buildPrompt("headVerifier", {}), { schema: { type: "object", required: ["status", "evidence"], additionalProperties: true, properties: { status: { type: "string", enum: ["ok"] }, evidence: { type: "object", required: ["head"], properties: { head: { type: "string" } } }, summary: { type: "string" } } }, label: `head-verify:${plan.id}`, phase: `Plan ${plan.id}` }, "sonnet");
      if (headVerify.halted || headVerify.status !== "ok" || headVerify.evidence?.head !== gate.evidence?.restored_head) {
        return await halt(plan, null, { reason: "gate head restore verification failed", diag: { expected: gate.evidence?.restored_head, actual: headVerify.evidence?.head, sha: gateSha } });
      }
      log(`\u2713 plan ${plan.id} gate green @ ${gateSha} (${cmds.length} cmd${cmds.length === 1 ? "" : "s"})`);
    } else {
      log(`plan ${plan.id}: no new commits and HEAD unchanged since plan start, gate skipped`);
    }
  }
  phase("Finalize");
  const frDone = await finalReportWithFallback({ mode: "done", stateJson: JSON.stringify(state), blockedInfo: "", runsDir: `runs/${state.runTs}`, runTs: state.runTs, lessonsPath: state.config?.lessons_path || "", lessonsAutoDistill: String(resolveLessonsAutoDistill(state.config)) });
  if (!frDone) log("\u2717\u2717 \u81F4\u547D\uFF1AfinalReport \u5168\u94FE\u5931\u8D25\uFF0Cmanifest \u672A\u5199\u5165\uFF01\u8BF7\u624B\u52A8\u68C0\u67E5 runs/ \u76EE\u5F55");
  log("\u2713 workflow done");
  return { result: "done", perTask: state.perTask };
})();
