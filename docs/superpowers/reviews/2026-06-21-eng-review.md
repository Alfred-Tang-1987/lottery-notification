# Eng Review Report: 彩票开奖自动核对与通知系统

**Review date**: 2026-06-21
**Mode**: FULL_REVIEW (scope accepted, CEO review HOLD prior)
**Plan reviewed**: `docs/superpowers/specs/2026-06-16-lottery-notification-design.md` @ commit `e1869c3`
**Branch**: `main`

## Decisions Resolved

| Decision | Choice | Rationale |
|---|---|---|
| D1 SQLite write concurrency | A | Single-writer connection (pool_size=1, busy_timeout); scheduler uses independent connection sharing the same engine (per outside voice #1/#17). |
| D2 Auth/session model | A | httpOnly cookie + SameSite + CSRF token via `/auth/csrf` GET (per outside voice #16); CORS `allow_credentials` + explicit origins (per outside voice #12). |
| D3 Compound expansion timing | A | Compare-time expansion with cache, BUT expansion logic also runs at write-time for cost validation (per D6). |
| D4 Auth library | A | Hand-written PyJWT + passlib (explicit, auditable). |
| D5 Correction: comparisons | A | In-place update + `corrected_at` timestamp; history via `draw_corrections`. Avoids stats double-count. |
| D6 tickets.cost | A | Expansion logic front-loaded to create-time for cost + MAX_COMBINATIONS validation; single `expand(entry)` in domain layer (DRY). |
| D7 Number abstraction | A | `NumberRange` (partition, dedup) + `PositionalDigits` (positional/hybrid, ordered, allows dup). Type expresses invariant. |
| T1 (cross-model) single-writer | refined | Scheduler independent connection, not sharing request pool — avoids deadlock (outside voice #1). |

---

## Step 0 Scope Challenge

- Complexity check triggers (8+ files, 2+ classes) but greenfield system spec — not a feature delta. Scope accepted per CEO HOLD.
- Search check: APScheduler `SQLAlchemyJobStore` [L1], SQLModel+Alembic [L1], Fernet [L1], import-linter [L1] all use built-ins. Outbox + strategy pattern [L3] justified by domain.
- No TODOS.md. Domain-layer 95% coverage target realistic under AI compression.

---

## §1 Architecture Findings (9)

- E1 [P1] Domain hydration boundary (adapter hydrates spec_json → LotterySpec with pydantic).
- E2 [P1] SQLite write serialization (single writer + scheduler independent connection).
- E3 [P1] Outbox claim protocol + comparisons unique constraint.
- E4 [P1] Auth/session model (D2:A resolved).
- E5 [P2] Correction version query (D5:A resolved — in-place + corrected_at).
- E6 [P2] SPA history catch-all in FastAPI.
- E7 [P2] Compound expansion timing (D3+D6 resolved).
- E8 [P2] Repository abstraction form (per-aggregate repos).
- E9 [P3] Retailer data source integration (CORS/keys/quota).

## §2 Code Quality Findings (4)

- E10 [P2] Per-aggregate repositories, user_id in constructor.
- E11 [P2] LotterySpec hydration validation (fail-fast on malformed spec_json).
- E12 [P3] Domain error class hierarchy (DomainError + SourceError), no catch-all.
- E13 [P2] Cents-integrity guard (lint, no float in domain/storage).

## §3 Test Findings

```
CODE PATHS                                          USER FLOWS
[+] domain/ (95% target)                           [+] login/register
  ├── PartitionCompare  [GAP]                          ├── [GAP][E2E] rate limit/invite
  ├── PositionalCompare [GAP]                          └── [GAP][E2E] multi-device
  ├── QxcHybridCompare  [GAP] (new)                 [+] ticket CRUD
  ├── append_multiplier [GAP] (new)                    ├── [GAP][E2E] CRUD
  ├── expand() cost     [GAP] (new)                    ├── [GAP] CSV import (per lottery)
  └── MAX_COMBINATIONS  [GAP]                          └── [GAP] multiplier/append
[+] services/                                      [+] claim/expire
  ├── fetch(双源+部分源+退避) [GAP]                    └── [GAP][E2E] double-click
  ├── cross-verify(期号映射) [GAP]                 [+] push
  ├── outbox claim(幂等)     [GAP][E2E]               └── [GAP] channel degrade/SMTP-down
  ├── 浮奖回填(终止/补推)    [GAP]                [+] admin audit
  ├── 结果更正(原地+corrected) [GAP]              [+] trend gate
  ├── Path A 异步推送        [GAP]                    └── [GAP][E2E] confirm gate
  └── DND defer 触发         [GAP]
[+] infra/
  ├── Fernet multi-key rotate [GAP]
  ├── APScheduler engine共享/时区 [GAP]
  └── Alembic + Phase 1.0 顺序 [GAP]
[+] purity 护栏(import-linter+meta) [GAP]
[+] CORS/CSRF(/auth/csrf)   [GAP]
[+] NumberRange vs PositionalDigits [GAP]

COVERAGE: ~0% (no code) | GAPS: ~30 (7 E2E)
```

Key test requirements:
- E14 [P1] Domain purity guard (import-linter + meta-test).
- E15 [P2] Property tests (Hypothesis) for each compare strategy — well-formed HitResult for any valid input.
- E16 [P2] Immutable versioned fixtures.
- E17 [P1] Outbox idempotency trio (dup-trigger, correction re-compare, crash backfill).
- E18 [P2] E2E critical flows (≥5 Playwright).

## §4 Performance Findings (4)

- E19 [P1] SQLite PRAGMA (WAL + synchronous=NORMAL + busy_timeout=5000 + single writer).
- E20 [P2] Indexes: comparisons(user_id, is_win, draw_result_id); draw_results(lottery_code, draw_date).
- E21 [P2] comparisons growth/archival policy.
- E22 [P3] Trend pagination + virtual scroll.

---

## Outside Voice (Claude subagent, opus) — 25 execution findings

Adopted (obvious-correct, folded into spec): #1/#17/#18 (APScheduler engine/tz), #2 (coalesce/max_instances), #3 (claim + unique constraint), #5 (verified=false recovery), #8 (Path A async), #9 (DND defer), #10 (fetch backfill + grace), #12 (dev/prod CORS), #13 (multi-key env + audit sanitize), #14 (invite single-use/expiry/bootstrap), #16 (CSRF endpoint), #19 (backup mechanism), #20 (fetch backoff/jitter), #21 (audit retention), #24 (CSV format per lottery), #4 (draw_no mapping).

Resolved as decisions: #7 → D5:A, #15 → D6:A, #23 → D7:A.

Highest-leverage fixes before coding (per outside voice):
- Path A push off compare transaction (#8).
- Stats treat `is_win AND prize_amount IS NULL` as 待派奖 (#22).
- APScheduler+SQLite+single-writer connection sharing (#1/#17/#18).
- NumberRange/PositionalDigits split (#23).

---

## Failure Modes Registry

| CODEPATH | FAILURE | RESCUED? | TEST? | USER SEES | LOGGED? |
|---|---|---|---|---|---|
| fetch | rate limit / timeout | Y | GAP | nothing | Y |
| fetch | empty (not drawn) | Y | GAP | nothing | Y |
| cross-verify | mismatch | Y | GAP | badge | Y |
| cross-verify | draw_no semantic mismatch | **GAP** | GAP | badge | Y |
| cross-verify | partial source grace | Y | GAP | yellow badge | Y |
| store | duplicate draw | Y | Y | nothing | Y |
| store | DB locked | Y(WAL+retry) | GAP | transient 503 | Y |
| outbox claim | double-pick | Y(claim SQL) | GAP | nothing | Y |
| compare | bad ticket | Y(isolate) | GAP | error list | Y |
| compare | combo explosion | Y(limit) | Y | validation | Y |
| compare | Path A push blocks writer | Y(async) | GAP | nothing | Y |
| push | channel fail | Y | Y | per-channel | Y |
| push | SMTP down | Y | GAP | nothing→Bark | Y |
| verify=false | stuck period | Y(admin force) | GAP | badge | Y |
| float refill | never published | Y(cap N days) | GAP | 待派奖 | Y |
| correction | re-compare | Y(in-place) | GAP | updated | Y |
| stats | null prize | Y(待派奖 UI) | GAP | pending label | Y |
| scheduler | restart loses job | Y(jobstore) | GAP | backfill | Y |
| scheduler | tz drift | Y(global CST) | GAP | — | Y |
| key rotation | decrypt old rows | Y(multi-key) | GAP | — | Y |

**No critical gaps** after adopted fixes (all GAPs are test-coverage gaps to write, not missing error handling).

---

## NOT in Scope

| Item | Rationale |
|---|---|
| Worker container split / queue push | Single container MVP; domain layer zero-IO keeps future split cheap. |
| PostgreSQL | SQLite + WAL sufficient for family scale; types PG-compatible. |
| iOS native App | Phase 3; API-first preserves option. |
| OCR ticket import | Phase 2/3 candidate. |
| Real-time WebSocket | Polling summary sufficient for lottery cadence. |
| PWA / silent-hours redundancy | YAGNI Phase 3. |
| 实时奖池余额 | Data-source dependent; conditional Phase 1. |

## What Already Exists

No business code. Assets: 9 HTML prototypes, lottery-rules.md, design spec, CLAUDE.md conventions. All reused, nothing rebuilt.

## Phase 1.0 Bootstrap Ordering (outside voice #25)

```
1. Alembic init (migration infra first)
2. Schema migration #1 (all tables incl APScheduler jobstore table pre-created, not auto-created)
3. Crypto service (multi-key env, key_version)
4. Seed lottery_types (7 specs, spec_json validated)
5. Domain layer (LotterySpec/NumberRange/PositionalDigits/Entry/PrizeTier/CompareStrategy — no deps)
6. Repositories (user_id in constructor)
7. Compare engine + outbox claim
8. Scheduler (SQLAlchemyJobStore sharing engine, global CST, coalesce/max_instances)
9. Fetch (双源 + 退避 + 期号映射)
10. Push (async Path A, channel plugins)
11. Auth (cookie + CSRF + CORS)
12. Web UI (per prototype + A11y + responsive + empty states)
13. Smoke: `python -m app.cli ssq` end-to-end
```

## Parallelization Strategy

| Lane | Modules | Depends on |
|---|---|---|
| A (domain) | domain/ (specs, strategies, expand, prize) | Phase 1.0 steps 1-5 |
| B (infra) | infra/ (db, crypto, scheduler, alembic) | Phase 1.0 steps 1-3 |
| C (services) | services/ (fetch, compare, push, stats) | A + B |
| D (web) | web/ (Vue3 SPA) | C (API contract) |

Launch A + B in parallel (domain/ and infra/ share no modules). C after A+B. D after C API stable.
Conflict flags: A and B both may touch `domain/specs` seed — coordinate step 4-5.

---

## Implementation Tasks

- [ ] **T1 (P1)** — domain — Split NumberRange (partition/dedup) vs PositionalDigits (positional/hybrid/ordered/dup-ok)
  - Surfaced by: §2 E7 + outside voice #23 (D7:A)
  - Files: domain specs/models
  - Verify: QXC front allows `1,1,2,3,4,5`; SSQ red rejects dup
- [ ] **T2 (P1)** — domain — Front-load `expand(entry)` to create-time for cost + MAX_COMBINATIONS validation; compare reuses cached expansion
  - Surfaced by: §2 E7 + outside voice #15 (D6:A)
  - Files: domain/expand, ticket service
  - Verify: create 胆拖 computes cost; oversize rejected
- [ ] **T3 (P1)** — core loop — Path A instant push MUST be async/off compare transaction
  - Surfaced by: outside voice #8
  - Files: compare service, push service
  - Verify: slow SMTP does not stall Path B/fetch
- [ ] **T4 (P1)** — infra — APScheduler: SQLAlchemyJobStore shares engine, global CST tz, coalesce=True, max_instances=1
  - Surfaced by: outside voice #1/#17/#18/#2 (D1:A refined)
  - Files: scheduler setup, db engine
  - Verify: restart does not double-fire jobs; tz-correct 21:30 polling
- [ ] **T5 (P1)** — core loop — Outbox claim protocol (UPDATE...RETURNING) + comparisons unique(draw_result_id, ticket_id)
  - Surfaced by: §1 E3 + outside voice #3
  - Files: compare service, comparisons migration
  - Verify: concurrent ticks compare once
- [ ] **T6 (P1)** — core loop — Correction: in-place comparisons update + corrected_at; history via draw_corrections
  - Surfaced by: outside voice #7 (D5:A)
  - Files: draw service, compare service
  - Verify: re-compare updates same row; stats no double-count
- [ ] **T7 (P1)** — stats — Treat `is_win AND prize_amount IS NULL` as 待派奖 in stats UI (not zero)
  - Surfaced by: outside voice #22
  - Files: stats service, stats UI
  - Verify: pre-refill tier-1 shows 待派奖 not huge loss
- [ ] **T8 (P1)** — core loop — verified=false recovery: admin force-verify + re-fetch cap
  - Surfaced by: outside voice #5
  - Files: draw service, admin API
  - Verify: stuck period can be force-resolved
- [ ] **T9 (P1)** — fetch — Backoff + jitter + max attempts + rate-limit handling; draw_no semantic mapping between sources
  - Surfaced by: outside voice #20 + #4
  - Files: fetch adapter, source adapters
  - Verify: rate-limited source not hammered; period mapping normalized
- [ ] **T10 (P1)** — auth — httpOnly cookie + SameSite + `/auth/csrf` GET + CORS allow_credentials + dev/prod origins
  - Surfaced by: §1 E4 + outside voice #16/#12 (D2:A)
  - Files: auth service, FastAPI middleware
  - Verify: dev Vite(5173)→FastAPI(8000) cookie works; prod same-origin
- [ ] **T11 (P1)** — crypto — Multi-key env format (CRYPTO_KEY_V1/V2) + re-encrypt flow; audit log sanitizes channel configs
  - Surfaced by: outside voice #13 (D7 crypto, key_version)
  - Files: crypto service, admin_audit
  - Verify: rotate key, old rows decrypt; audit has no plaintext secrets
- [ ] **T12 (P1)** — domain purity — import-linter rule (domain ↛ infra/adapters) + meta-test
  - Surfaced by: §3 E14
  - Files: import-linter config, tests
  - Verify: CI fails if domain imports infra
- [ ] **T13 (P1)** — infra — SQLite PRAGMA (WAL/synchronous=NORMAL/busy_timeout) + single writer + scheduler independent connection
  - Surfaced by: §4 E19 + outside voice #1
  - Files: db engine setup
  - Verify: concurrent write no locked; scheduler no deadlock
- [ ] **T14 (P2)** — repository — Per-aggregate repos, user_id in constructor; IDOR-safe joins
  - Surfaced by: §2 E10 + CEO T13
  - Files: repositories
  - Verify: cross-user access test fails
- [ ] **T15 (P2)** — scheduler — DND defer mechanism (re-scheduled job, not collision); weekly/monthly report timing
  - Surfaced by: outside voice #9
  - Files: scheduler, push service
  - Verify: DND-suppressed push fires at DND end; no job collision
- [ ] **T16 (P2)** — infra — Backup mechanism (sqlite3 .backup API in-process or host CLI + WAL checkpoint); 30-day retention; audit_logs retention
  - Surfaced by: outside voice #19/#21
  - Files: backup script, retention job
  - Verify: restore from backup; audit rotates
- [ ] **T17 (P2)** — test — Property tests (Hypothesis) for each compare strategy; immutable versioned fixtures; outbox idempotency trio; E2E ≥5
  - Surfaced by: §3 E15/E16/E17/E18
  - Files: tests/
  - Verify: pytest green; Playwright flows pass
- [ ] **T18 (P2)** — import — CSV format per lottery (partition/positional/hybrid columns); GBK; size limit; per-row validation
  - Surfaced by: outside voice #24
  - Files: ticket import service
  - Verify: import per lottery type validates correctly
- [ ] **T19 (P2)** — security — Invite code single-use + expiry + attempt lock; admin-only generation; no default bootstrap code
  - Surfaced by: outside voice #14
  - Files: invite service, admin API
  - Verify: expired/used code rejected
- [ ] **T20 (P2)** — web — FastAPI SPA catch-all (history mode); loading/empty/error states; A11y baseline; responsive breakpoints
  - Surfaced by: §1 E6 + design §11
  - Files: FastAPI static route, Vue components
  - Verify: refresh /my-numbers works; A11y audit passes
- [ ] **T21 (P3)** — perf — comparisons archival policy; trend pagination + virtual scroll
  - Surfaced by: §4 E21/E22
  - Files: stats/trend APIs
  - Verify: 100-period trend renders on mobile

---

## Completion Summary

- Step 0: Scope accepted (greenfield system, CEO HOLD prior).
- Architecture: 9 findings.
- Code Quality: 4 findings.
- Test: diagram produced, ~30 gaps.
- Performance: 4 findings.
- NOT in scope: written (7 items).
- What already exists: written.
- Failure modes: 21 rows, 0 critical gaps after fixes.
- Outside voice: ran (Claude subagent, opus); 25 findings, 3 resolved as D5/D6/D7, rest folded.
- Parallelization: 4 lanes (A domain + B infra parallel; C services after; D web last).
- Lake Score: all recommendations chose complete option.
