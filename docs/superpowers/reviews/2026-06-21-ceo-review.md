# CEO Review Report: 彩票开奖自动核对与通知系统

**Review date**: 2026-06-21  
**Mode**: HOLD SCOPE  
**Plan reviewed**: `docs/superpowers/specs/2026-06-16-lottery-notification-design.md`  
**Branch**: `main`  
**Commit**: `9f250f5`

## Scope Decisions Resolved During Review

| Decision | Choice | Rationale |
|---|---|---|
| D1 direction | A (revised) | Keep full scope, fix contradictions, but gate trend-page selection behind explicit user confirmation. |
| D3 compare trigger | B | Outbox + scheduler polling for decoupling, idempotency, and crash recovery. |
| D4 admin alert channel | A | Use Bark as the fallback admin alert channel; avoid email circular dependency. |
| D5 SQLite migrations | A | Alembic from Day 1. |
| D6 backup strategy | A | Daily `sqlite3 .backup` with 30-day retention. |
| D7 key rotation | B | MVP includes Fernet key rotation with versioned re-encrypt. |
| D8 trend selection gate | A | Modal/drawer with explicit disclaimer sentence. |
| D9 accessibility | A | Include semantic HTML, keyboard nav, contrast AA, 44px touch targets in MVP. |
| T1 trend selection risk | A | Keep modal/drawer gate; do not physically isolate selection to "我的号码". |
| T2 QXC in Phase 1 | A | Implement QXC hybrid compare strategy in Phase 1. |
| D10 admin audit | A | Add `admin_audit_logs` table in MVP. |
| D11 DLT append model | A | Add `append_multiplier` to `PrizeTier`. |

---

## Executive Summary

The spec is mature, well-structured, and architecturally sound (domain layer zero-IO, strategy pattern, compare-once design, API-first). The two most important risks are **compliance optics** and **spec-to-execution gaps**: several review decisions have not yet been written back into the spec text. This report enumerates all findings, required spec amendments, and implementation tasks.

### Critical gaps to fix before implementation
1. **QXC compare strategy is hand-waved** — must be fully specified.
2. **DLT append bet prize math** — needs `append_multiplier` in `PrizeTier`.
3. **Invite code brute force** — 6-digit codes need rate limits, attempt locking, expiration.
4. **Combo expansion DoS** — needs hard limits and cost validation.
5. **Floating prize refill / draw-result correction** — core loop is incomplete without these.
6. **Admin alert circular dependency** — admin alerts must not rely on email.
7. **APScheduler in-memory job store** — must persist jobs to SQLite.

---

## 0E. Temporal Interrogation (Key Decisions Only)

| Hour | Ambiguity | Resolution |
|---|---|---|
| 1 | SQLite WAL? migrations? domain import discipline? | WAL on; Alembic; import-linter rule |
| 2-3 | Compare trigger? float prize refill? | Outbox polling; scheduled refill from data source |
| 4-5 | Dual-source one-has-one-missing? admin alert channel? | Grace window + single-source flag; Bark fallback |
| 6+ | Real historical fixtures? combo limit? | Crawl zhcw snapshots; cap expansion combinations |

---

## Section 1: Architecture

### Augmented architecture diagram

```
┌─────────────────────────────────────────────────────────────┐
│ Vue3 SPA ─REST─▶ FastAPI (auth / users / tickets / queries)  │
│                          │                                   │
│                  ┌───────┴────────┐                          │
│                  │ APScheduler    │  ← persist jobs to SQLite │
│                  │ 21:30 poll     │                             │
│                  │ 07:00 summary  │                             │
│                  └───────┬────────┘                             │
│                          ▼                                      │
│  Fetch → Cross-verify → Store → Compare → Push                   │
│    │          │           │        │       │                    │
│    ▼          ▼           ▼        ▼       ▼                    │
│  source    verified    outbox   strategy  notifier              │
│  timeout   mismatch    row      registry  plugin                │
│  one-miss  false       [GAP]    [GAP]     Bark/Feishu/Email     │
│                                 D11 append                     │
│                          ▼                                      │
│               领域层 pure: LotterySpec / CompareStrategy /     │
│                          PrizeTier / HitResult                 │
│                          ▼                                      │
│              适配层: MXNZP + 聚合数据   基础设施: SQLite(WAL)   │
│                                      ⚠️ single file SPOF        │
└─────────────────────────────────────────────────────────────┘
```

### Findings
- **1a [HIGH] Compare trigger undefined.** §7.1 says compare runs when `draw_results` is first inserted, but the mechanism is unspecified. Resolution: outbox table `pending_comparisons` + APScheduler polling.
- **1b [MEDIUM] APScheduler misfire handling.** Container restarts during draw windows need `misfire_grace_time` and startup backfill.
- **1c [MEDIUM] SQLite writer contention.** FastAPI threads + scheduler threads concurrent write → `database is locked`. Use WAL mode.
- **1d [LOW] Strategy registry unspecified.** Use explicit dict registry, not decorator auto-discovery.
- **1e [HIGH] SQLite file is SPOF.** Mitigated by D6 daily backup.

---

## Section 2: Error & Rescue Map

### Core loop error registry

| CODEPATH | FAILURE | EXCEPTION | RESCUED? | ACTION | USER SEES |
|---|---|---|---|---|---|
| fetch primary | timeout / 429 / 5xx | SourceTimeout / RateLimit | Y | backoff → fallback to secondary | nothing (transparent) |
| fetch primary | malformed JSON | JSONParseError | Y | log → fallback | nothing |
| fetch primary | empty result (not drawn yet) | NotDrawnError | Y | stop, retry next poll | nothing |
| cross-verify | numbers mismatch | SourceMismatchError | Y | store `verified=false`, alert admin | badge on UI |
| cross-verify | one source has result, other does not | PartialSourceError | **GAP** | grace window 5min → single-source flag | badge |
| store | duplicate key | DuplicateDrawError | Y | idempotent skip | nothing |
| store | DB locked | DatabaseLockedError | **GAP** | WAL + retry | transient 503 |
| compare | ticket format invalid | TicketFormatError | **GAP** | isolate bad ticket, continue others | error list |
| compare | combo expansion too large | ComboLimitError | D1 | reject ticket with message | validation error |
| compare | float tier no amount | FloatPendingError | D1 | amount=null, status=pending | "待官方派奖" |
| push | channel HTTP fail | ChannelPushError | Y | retry 3x + next channel | per-channel log |
| push | SMTP misconfigured | SMTPConfigError | **GAP** | health panel red + Bark admin alert | nothing until admin fixes |
| admin alert | email is broken | AlertChannelError | D4 | fallback to Bark | admin push |

### GAPs to fix
- PartialSourceError grace window and single-source flag.
- DatabaseLockedError via WAL + retry.
- TicketFormatError isolation (don't fail whole draw).
- SMTPConfigError visibility.

---

## Section 3: Security & Threat Model

| Threat | Likelihood | Impact | Mitigated? | Action |
|---|---|---|---|---|
| IDOR on `prize_claims` via `comparison_id` | Med | High | No | Always join with `user_id`. |
| Invite code brute force (1M space) | High | High | No | Rate limit + attempt lock + code expiration. |
| Fernet key rotation breaks old configs | Low | High | D7 | Versioned re-encrypt flow. |
| Admin privilege abuse | Low | High | D10 | `admin_audit_logs` + role checks. |
| CSV import DoS / injection | Med | Med | No | File size limit, GBK handling, row-level validation. |
| JWT secret rotation | Low | Med | Partial | Document rotation + session expiry. |
| Admin endpoints missing role check | Med | High | No | Dedicated `RequireAdmin` dependency. |

---

## Section 4: Data Flow & Interaction Edge Cases

### Core loop shadow-path diagram

```
 开奖时间到
    │
    ▼
 fetch(双源) ──nil/空──▶ [NotDrawn?] ──是──▶ 停, 下轮
    │                      └──错误?──▶ 退避→备源→缓存+告警
    ▼
 cross-verify ──不一致──▶ verified=false + admin alert
    │          ──一有一无──▶ grace window → single_source flag
    ▼ 一致
 store(verified=true) ──dup key──▶ 幂等跳过
    │
    ▼  outbox polling triggers
 compare(仅追投) ──格式异常──▶ 隔离坏注, 继续其他
    │              ──爆炸──▶ D1 上限
    ▼
 comparisons ──DB locked──▶ WAL + 重试
    │
    ├──▶ 路径A: 一二等奖 → 即时简讯
    │              └──渠道全败 → Bark admin alert
    └──▶ 路径B: 07:00 汇总
```

### Interaction edge cases
| Interaction | Edge case | Handled? |
|---|---|---|
| Ticket CSV import | Duplicate ticket | No (define detection rule) |
| Ticket CSV import | GBK encoding | No |
| Ticket CSV import | 10,000-row file | No (need size limit) |
| Claim "已领取" | Double-click | No (idempotent guard) |
| Claim "已领取" | Expired then clicked | No (allow late_claim status?) |
| Trend selection | Navigate away mid-selection | Yes (state discardable) |
| Dark mode toggle | Auto-time vs manual override conflict | No (specify precedence) |
| Admin SMTP save | Invalid credentials | No (test-send flow) |

---

## Section 5: Code Quality

- **5a** Strategy registry should be explicit dict.
- **5b** Enforce domain-layer purity with import-linter (`app.domain` must not import `app.adapters` or `app.infrastructure`).

---

## Section 6: Test Review

### Test coverage diagram

```
NEW UX FLOWS:
  - login / register / invite code validation
  - ticket CRUD (manual + CSV import)
  - draw query with verified/unverified states
  - win records + claim marking
  - settings channel config encryption
  - trend page: default-collapsed selection gate

NEW DATA FLOWS:
  - fetch → cross-verify → store → compare → push
  - float prize refill (pending → actual)
  - draw result correction
  - admin audit log writes

NEW CODEPATHS:
  - strategy registry routing per lottery code
  - partition vs positional vs QXC hybrid compare
  - combo expansion validation
  - append multiplier (DLT)

NEW BACKGROUND JOBS:
  - 21:30 polling until draw available
  - 07:00 summary push
  - float prize refill polling
  - expired claim scanner

NEW INTEGRATIONS:
  - MXNZP API
  - 聚合数据 API
  - Bark / Feishu / SMTP

NEW ERROR PATHS:
  - source timeout, mismatch, partial
  - DB locked, duplicate draw
  - channel push failure, SMTP misconfig
```

### Gaps
- Historical draw fixtures source (zhcw snapshots).
- Cross-verify three-branch tests (match / mismatch / partial).
- Compliance test: trend selection panel defaults collapsed.
- APScheduler job-store persistence test.

---

## Section 7: Performance

- **7a** Combo expansion capped (D1).
- **7b** Add composite index `(user_id, is_win, draw_result_id)` on `comparisons`.
- **7c** Add index `(lottery_code, draw_date)` on `draw_results`.
- **7d** Trend default 30 periods; 100-period mode with virtual scrolling.
- **7e** SQLite WAL mode.

---

## Section 8: Observability

- **8a** Admin alert circular dependency resolved by D4 (Bark fallback).
- **8b** Structured logging spec needed: `user_id`, `lottery_code`, `draw_no`, `action`, `duration`, with secrets redacted.
- **8c** Metrics: data-source latency, compare success rate, push success rate by channel.
- **8d** `notification_logs` retention: 90 days.
- **8e** Health panel should include compare/push health, not just data sources.

---

## Section 9: Deployment

- **9a** Alembic migrations (D5).
- **9b** Daily SQLite backup (D6).
- **9c** Startup config validation: `JWT_SECRET`, `CRYPTO_KEY`, required SMTP if email enabled.
- **9d** Smoke test: `python -m app.cli ssq` manual trigger.
- **9e** Timezone/clock validation on startup.
- **9f** Docker healthcheck endpoint.

---

## Section 10: Long-Term Trajectory

- **Reversibility: 4/5.** Domain layer is pure; SQLite→PG is type-compatible. The only semi-one-way door is multi-user auth.
- **Debt**: `spec_json` schema versioning needed; admin audit log is cheap now but hard to add later.
- **Trajectory**: API-first + pure domain layer correctly positions for iOS App and future worker split.

---

## Section 11: Design / UX

- **11a** Loading/empty/error states not fully represented in prototypes.
- **11b** A11y baseline adopted (D9).
- **11c** Trend selection gate: modal/drawer + explicit disclaimer (T1:A).
- **11d** Empty states are trust points — design them deliberately.
- **11e** Responsive breakpoints should be specified (320/375/768/1024).

---

## State Machine

```
draw_results.verified:
  pending ──双源一致──▶ true ──事后发现错误──▶ corrected (重比对)
    │                     │
    └──不一致──────────▶ false (admin alert)

comparisons:
  未生成 ──比对──▶ win / lose (终态)

prize_claims.status:
  pending ──用户标记──▶ claimed
    │
    └──60天过期──▶ expired  (每日调度扫描触发)

float prize:
  pending ──官方数据回流──▶ actual_amount + 可选二次通知
```

---

## Failure Modes Registry

| CODEPATH | FAILURE MODE | RESCUED? | TEST? | USER SEES? | LOGGED? |
|---|---|---|---|---|---|
| fetch primary | timeout | Y | Y | nothing | Y |
| fetch primary | 429 | Y | Y | nothing | Y |
| cross-verify | mismatch | Y | Y | badge | Y |
| cross-verify | partial source | **GAP** | GAP | badge | Y |
| store | duplicate draw | Y | Y | nothing | Y |
| store | DB locked | **GAP** | GAP | 503 | Y |
| compare | bad ticket format | **GAP** | GAP | error list | Y |
| compare | combo explosion | Y | Y | validation | Y |
| push | channel fail | Y | Y | per-channel | Y |
| push | SMTP misconfig | **GAP** | GAP | nothing | Y |
| admin alert | email broken | D4 | GAP | Bark push | Y |
| scheduler | container restart loses job | **GAP** | GAP | missed draw | Y |

**CRITICAL GAPS**: partial source, DB locked, bad ticket format, SMTP misconfig, scheduler job loss.

---

## NOT in Scope

| Item | Rationale |
|---|---|
| iCal export/subscription | Cancelled per D1; calendar stays but no iCal. |
| AI/预测/冷热号/必中 | Compliance red line. |
| 购彩/代购/支付 | Compliance red line. |
| PWA / silent hours / multi-channel redundancy | Deferred to Phase 3. |
| 预算管理 / 盈亏趋势曲线 / 年度报告 | YAGNI, Phase 3 candidate. |
| OCR ticket import | High-value, but not MVP. Deferred to Phase 2/3. |
| iOS native App | Phase 3 independent project; API-first preserves option. |
| Real-time WebSocket pushes | Polling-based summary is sufficient for lottery cadence. |

---

## What Already Exists

No business code exists. Existing assets:
- 9 HTML prototypes (`docs/superpowers/prototypes/`)
- Lottery rules reference (`docs/reference/lottery-rules.md`)
- Design spec (`docs/superpowers/specs/...`)
- Project conventions in `CLAUDE.md`

---

## Dream State Delta

| 12-Month Ideal | This Plan Delivers | Gap |
|---|---|---|
| One-time ticket entry, automatic checking | Yes | — |
| Multi-user family sharing | Yes | — |
| iOS App reuse | API-first positions for it | iOS App not built |
| Trusted, non-gambling UX | Mostly | trend selection needs modal gate (T1:A) |
| Never miss a claim | Yes | expired claim scanner needed |
| Accurate prize amounts | Float prize refill needed | D1 |

---

## Implementation Tasks

Generated from this review. Each task maps to a specific finding above.

- [ ] **T1 (P1)** — Domain — Specify QXC hybrid compare strategy and prize tiers
  - Surfaced by: Section 1 — QXC hand-waved
  - Files: `docs/superpowers/specs/...`, domain implementation
  - Verify: unit test with QXC historical draw

- [ ] **T2 (P1)** — Domain — Add `append_multiplier` to `PrizeTier` and implement DLT append math
  - Surfaced by: Outside voice #1
  - Files: domain models, specs
  - Verify: unit test DLT tier-1 with/without append

- [ ] **T3 (P1)** — Security — Implement invite-code rate limiting, attempt locking, expiration
  - Surfaced by: Section 3
  - Files: auth service, users repo
  - Verify: brute-force test blocked

- [ ] **T4 (P1)** — Domain — Cap combo expansion and validate ticket cost before storage
  - Surfaced by: Section 2 / D1
  - Files: ticket service, domain validators
  - Verify: oversized combo rejected

- [ ] **T5 (P1)** — Core loop — Design and document floating-prize refill flow
  - Surfaced by: Section 2
  - Files: spec §5.3, compare service, scheduler
  - Verify: integration test pending→actual

- [ ] **T6 (P1)** — Core loop — Design and document draw-result correction flow
  - Surfaced by: Section 2
  - Files: spec §7.1, draw service
  - Verify: correction triggers re-compare

- [ ] **T7 (P1)** — Architecture — Implement outbox-based compare trigger
  - Surfaced by: Section 1 — D3
  - Files: draw service, scheduler
  - Verify: repeated trigger is idempotent

- [ ] **T8 (P1)** — Infrastructure — Enable SQLite WAL mode and configure APScheduler SQLAlchemy job store
  - Surfaced by: Section 2 / Outside voice #6
  - Files: DB engine setup, scheduler setup
  - Verify: kill/restart container does not lose jobs

- [ ] **T9 (P1)** — Infrastructure — Add Alembic migrations
  - Surfaced by: Section 9 — D5
  - Files: `alembic/` config, initial migration
  - Verify: `alembic upgrade head` works on fresh NAS

- [ ] **T10 (P1)** — Infrastructure — Daily SQLite backup script + retention
  - Surfaced by: Section 1 — D6
  - Files: backup script, docker cron or scheduler job
  - Verify: restore from backup

- [ ] **T11 (P1)** — Security — Implement Fernet key rotation with versioned re-encrypt
  - Surfaced by: Section 3 — D7
  - Files: crypto service, channel config repo
  - Verify: rotate key, old configs still decrypt

- [ ] **T12 (P1)** — Security — Add `admin_audit_logs` table and write audit records
  - Surfaced by: Section 3 — D10
  - Files: models, admin service, admin UI
  - Verify: admin action appears in log

- [ ] **T13 (P1)** — Security — Scope all queries by `user_id`; fix `prize_claims` IDOR
  - Surfaced by: Section 3
  - Files: repositories
  - Verify: cross-user access test fails

- [ ] **T14 (P1)** — Observability — Add Bark fallback admin alert channel
  - Surfaced by: Section 8 — D4
  - Files: notifier config, health service
  - Verify: disable SMTP, trigger alert → Bark received

- [ ] **T15 (P1)** — Spec — Remove iCal references from §12.2 and prototype
  - Surfaced by: Section 11 / D1
  - Files: spec, `01-dashboard.html`
  - Verify: grep returns no iCal mentions

- [ ] **T16 (P1)** — Spec — Define retailer data source or mark feature data-source-dependent
  - Surfaced by: D1
  - Files: spec §9.2/§12.2
  - Verify: spec names source or fallback

- [ ] **T17 (P1)** — Domain — Fix QLC special-number "without replacement" semantics
  - Surfaced by: Outside voice #3
  - Files: `LotterySpec`, number-range abstraction
  - Verify: QLC draw generation honors constraint

- [ ] **T18 (P1)** — Domain/Stats — Add per-lottery welfare contribution rate and `cost` field on tickets
  - Surfaced by: Outside voice #4 / #19
  - Files: `LotterySpec`, `tickets` model, stats service
  - Verify: welfare card matches official rates

- [ ] **T19 (P1)** — UX — Implement trend-page selection modal/drawer with disclaimer
  - Surfaced by: T1:A / D8
  - Files: `08-trend.html`, Vue component
  - Verify: default collapsed; click shows disclaimer; confirm reveals panel

- [ ] **T20 (P2)** — UX — Add loading/empty/error states across all screens
  - Surfaced by: Section 11
  - Files: all page components
  - Verify: visual regression tests

- [ ] **T21 (P2)** — Testing — Crawl historical draw fixtures from zhcw.com
  - Surfaced by: Section 6
  - Files: `tests/fixtures/`
  - Verify: fixture files committed

- [ ] **T22 (P2)** — Spec — Define weekly/monthly report timing and DND rules
  - Surfaced by: Outside voice
  - Files: spec §8.2
  - Verify: scheduler config matches spec

- [ ] **T23 (P2)** — Spec — Define hit-rate calculation precisely
  - Surfaced by: Outside voice
  - Files: spec §3.1 / §12.2
  - Verify: stats implementation matches definition

---

## Outside Voice Summary

- **Source**: Claude subagent (Codex CLI not installed)
- **Findings**: 30 issues, many overlapping with this review
- **New critical findings adopted**: DLT append multiplier, QXC strategy, QLC without-replacement, welfare rates, ticket cost field, APScheduler job-store persistence, admin audit logs
- **Cross-model tension**: T1 (trend selection isolation) resolved as A (keep modal); T2 (QXC in Phase 1) resolved as A (implement in Phase 1)

---

## Recommendations for Next Reviews

1. Run `/plan-eng-review` on the updated spec before implementation — architecture and tests are the required shipping gate.
2. Run `/plan-design-review` if UI interaction details (modal gate, empty states, responsive breakpoints) need deep visual review.
