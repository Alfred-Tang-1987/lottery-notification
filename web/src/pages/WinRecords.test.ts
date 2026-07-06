import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createApp, nextTick } from "vue";
import { createRouter, createMemoryHistory } from "vue-router";
import WinRecords from "../pages/WinRecords.vue";

// WinRecords reads route.query.claim (Dashboard deep-link). A memory-mode router
// is installed per-test so useRoute() resolves; the query is set via
// router.replace AFTER isReady() so it survives router initialization.

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const DEFAULT_RECORDS = [
  {
    id: 1,
    lottery_code: "ssq",
    lottery_name: "双色球",
    draw_no: "2024090",
    draw_date: "2024-08-04",
    numbers_json: "[1,2,3,4,5,6]",
    ticket_label: "自选",
    hits_json: '{"front": [6], "back": [1]}',
    prize_tier: 6,
    prize_amount: 500,
    is_win: true,
    created_at: "2024-08-04T10:00:00",
    claim_status: "pending",
    claim_id: 101,
    deadline: new Date(Date.now() + 10 * 24 * 60 * 60 * 1000).toISOString(),
  },
  {
    id: 2,
    lottery_code: "ssq",
    lottery_name: "双色球",
    draw_no: "2024091",
    draw_date: "2024-08-06",
    numbers_json: "[1,2,3,4,5,6]",
    ticket_label: "自选",
    hits_json: '{"front": [6], "back": [1]}',
    prize_tier: 1,
    prize_amount: 12_000_000,
    is_win: true,
    created_at: "2024-08-06T10:00:00",
    claim_status: "pending",
    claim_id: 102,
    deadline: new Date(Date.now() + 20 * 24 * 60 * 60 * 1000).toISOString(),
  },
  {
    id: 3,
    lottery_code: "dlt",
    lottery_name: "大乐透",
    draw_no: "2024080",
    draw_date: "2024-07-20",
    numbers_json: "[1,2,3,4,5+6,7]",
    ticket_label: "自选",
    hits_json: '{"front": [5], "back": [2]}',
    prize_tier: 6,
    prize_amount: 500,
    is_win: true,
    created_at: "2024-07-20T10:00:00",
    claim_status: "claimed",
    claim_id: null,
    deadline: null,
  },
];

function stubApi(overrides: Record<string, unknown> = {}) {
  let claimCallCount = 0;
  return vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const u = String(url);
    if (u === "/auth/csrf") return jsonResponse(200, { csrf_token: "tok" });
    if (method === "POST" && u.startsWith("/claims/")) {
      claimCallCount += 1;
      if (overrides.claimPostFailure) {
        return jsonResponse(422, { detail: overrides.claimPostFailure });
      }
      return jsonResponse(200, { ok: true });
    }
    if (method === "GET" && u.startsWith("/api/comparisons")) {
      // After a successful claim, optionally fail the refresh so we can assert
      // the user still sees a success-oriented message.
      if (claimCallCount > 0 && typeof overrides.loadAfterClaim === "function") {
        return (overrides.loadAfterClaim as (url: string) => Response)(u);
      }
      return jsonResponse(200, overrides.records ?? DEFAULT_RECORDS);
    }
    return jsonResponse(200, {});
  });
}

describe("WinRecords.vue (T6g)", () => {
  let host: HTMLDivElement;
  let app: ReturnType<typeof createApp> | null = null;
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    host = document.createElement("div");
    document.body.appendChild(host);
  });

  afterEach(() => {
    app?.unmount();
    host.remove();
    app = null;
    vi.restoreAllMocks();
  });

  async function mount(overrides: Record<string, unknown> = {}, query: Record<string, string> = {}) {
    fetchMock = stubApi(overrides);
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: "/wins", component: { template: "<div/>" } }],
    });
    app = createApp(WinRecords);
    app.use(router);
    await router.isReady();
    // Replace AFTER ready so the new query is the current route when WinRecords
    // mounts; replacing before isReady() gets overwritten by router init.
    if (Object.keys(query).length > 0) {
      await router.replace({ path: "/wins", query });
    }
    app.mount(host);
    await nextTick();
    await new Promise((r) => setTimeout(r, 0));
    await nextTick();
    return host;
  }

  function lastCall(urlPrefix: string, method?: string) {
    const calls = fetchMock.mock.calls.filter((c) => {
      const u = String(c[0]);
      // Match exact path (with query) but exclude deeper subpaths (e.g. matching
      // "/api/comparisons" must not also catch "/api/comparisons/123" which is a
      // different resource). This mirrors the guard used in Dashboard.test.ts.
      const urlMatch =
        u.startsWith(urlPrefix) && !u.startsWith(`${urlPrefix}/`);
      if (!method) return urlMatch;
      return urlMatch && (c[1] as RequestInit | undefined)?.method === method;
    });
    return calls[calls.length - 1];
  }

  it("renders 4 stat cards with amount and count", async () => {
    await mount();
    expect(host.textContent).toContain("累计中奖");
    expect(host.textContent).toContain("待兑奖");
    expect(host.textContent).toContain("已领取");
    expect(host.textContent).toContain("已过期");
    expect(host.textContent).toContain("3 笔");
  });

  it("shows tax hint for prizes >= 1万元", async () => {
    await mount();
    expect(host.textContent).toContain("单笔中奖超 1 万元");
    expect(host.textContent).toContain("实得约");
  });

  it("filters by status via client-side buttons", async () => {
    await mount();
    const pendingBtn = host.querySelector('[data-testid="filter-status-pending"]') as HTMLButtonElement;
    expect(pendingBtn).toBeTruthy();
    pendingBtn.click();
    await nextTick();
    await new Promise((r) => setTimeout(r, 0));
    await nextTick();
    const cards = host.querySelectorAll(".record-card");
    expect(cards.length).toBe(2);
  });

  it("sends period=month to backend by default", async () => {
    await mount();
    const call = lastCall("/api/comparisons", "GET");
    expect(call).toBeDefined();
    const url = String(call![0]);
    expect(url).toContain("win_only=true");
    expect(url).toContain("period=month");
  });

  it("sends custom date range when selected", async () => {
    await mount();
    const periodSelect = host.querySelector('[data-testid="filter-period"]') as HTMLSelectElement;
    expect(periodSelect).toBeTruthy();
    periodSelect.value = "custom";
    periodSelect.dispatchEvent(new Event("change"));
    await nextTick();

    const fromInput = host.querySelector('[data-testid="filter-date-from"]') as HTMLInputElement;
    const toInput = host.querySelector('[data-testid="filter-date-to"]') as HTMLInputElement;
    fromInput.value = "2024-01-01";
    toInput.value = "2024-12-31";
    fromInput.dispatchEvent(new Event("input"));
    toInput.dispatchEvent(new Event("input"));
    await nextTick();

    const applyBtn = host.querySelector('[data-testid="apply-period"]') as HTMLButtonElement;
    applyBtn.click();
    await nextTick();
    await new Promise((r) => setTimeout(r, 0));
    await nextTick();

    const call = lastCall("/api/comparisons", "GET");
    const url = String(call![0]);
    expect(url).toContain("period=custom");
    expect(url).toContain("date_from=2024-01-01");
    expect(url).toContain("date_to=2024-12-31");
  });

  it("updates stats cards when status filter changes", async () => {
    await mount();
    const pendingBtn = host.querySelector('[data-testid="filter-status-pending"]') as HTMLButtonElement;
    pendingBtn.click();
    await nextTick();
    await new Promise((r) => setTimeout(r, 0));
    await nextTick();
    // Two pending records: amounts 500 + 12,000,000 = 12,000,500 cents
    const pendingAmount = Array.from(host.querySelectorAll(".stat-mini-value"))
      .find((el) => el.previousElementSibling?.textContent === "待兑奖");
    expect(pendingAmount).toBeTruthy();
    expect(pendingAmount!.textContent).toContain("¥120,005");
    const pendingCount = Array.from(host.querySelectorAll(".stat-mini-sub"))
      .find((el) => el.previousElementSibling?.previousElementSibling?.textContent === "待兑奖");
    expect(pendingCount).toBeTruthy();
    expect(pendingCount!.textContent).toContain("2 笔");
  });

  it("does not render 0 days for invalid deadline", async () => {
    await mount({
      records: [
        {
          ...DEFAULT_RECORDS[0],
          deadline: "invalid-date",
        },
      ],
    });
    expect(host.textContent).not.toContain("剩余 0 天");
    expect(host.textContent).not.toContain("天后过期");
  });

  it("counts backend-expired records in the 已过期 stat card", async () => {
    // The 07:30 scheduler marks records claim_status='expired' (a persisted
    // terminal status, distinct from client-side deadline derivation). Such a
    // record must appear in the 已过期 stat card, not silently fall through.
    const expiredRecord = {
      id: 99,
      lottery_code: "ssq",
      lottery_name: "双色球",
      draw_no: "2024070",
      draw_date: "2024-06-01",
      numbers_json: "[1,2,3,4,5,6]",
      ticket_label: "自选",
      hits_json: '{"front": [6], "back": [1]}',
      prize_tier: 6,
      prize_amount: 500,
      is_win: true,
      created_at: "2024-06-01T10:00:00",
      claim_status: "expired",
      claim_id: null,
      deadline: new Date(Date.now() - 5 * 24 * 60 * 60 * 1000).toISOString(),
    };
    await mount({ records: [...DEFAULT_RECORDS, expiredRecord] });
    // 已过期 card count should be 1 (the backend-expired record)
    const expiredCard = Array.from(host.querySelectorAll(".stat-mini")).find(
      (el) => el.querySelector(".stat-mini-label")?.textContent === "已过期"
    );
    expect(expiredCard).toBeTruthy();
    expect(expiredCard!.querySelector(".stat-mini-sub")!.textContent).toContain("1 笔");
    expect(expiredCard!.querySelector(".stat-mini-value")!.textContent).toContain("¥5");
  });

  it("exposes an 已过期 status filter button that surfaces backend-expired records", async () => {
    const expiredRecord = {
      id: 99,
      lottery_code: "ssq",
      lottery_name: "双色球",
      draw_no: "2024070",
      draw_date: "2024-06-01",
      numbers_json: "[1,2,3,4,5,6]",
      ticket_label: "自选",
      hits_json: '{"front": [6], "back": [1]}',
      prize_tier: 6,
      prize_amount: 500,
      is_win: true,
      created_at: "2024-06-01T10:00:00",
      claim_status: "expired",
      claim_id: null,
      deadline: new Date(Date.now() - 5 * 24 * 60 * 60 * 1000).toISOString(),
    };
    await mount({ records: [...DEFAULT_RECORDS, expiredRecord] });
    const expiredBtn = host.querySelector('[data-testid="filter-status-expired"]') as HTMLButtonElement;
    expect(expiredBtn).toBeTruthy();
    expiredBtn.click();
    await nextTick();
    await new Promise((r) => setTimeout(r, 0));
    await nextTick();
    const cards = host.querySelectorAll(".record-card");
    expect(cards.length).toBe(1);
  });

  it("renders expired badge label and class for backend-expired records", async () => {
    // Round 4 quality finding: expired records fell through to '无兑奖' label and
    // had no .expired CSS class (only pending/claimed/unknown were styled).
    const expiredRecord = {
      id: 99,
      lottery_code: "ssq",
      lottery_name: "双色球",
      draw_no: "2024070",
      draw_date: "2024-06-01",
      numbers_json: "[1,2,3,4,5,6]",
      ticket_label: "自选",
      hits_json: '{"front": [6], "back": [1]}',
      prize_tier: 6,
      prize_amount: 500,
      is_win: true,
      created_at: "2024-06-01T10:00:00",
      claim_status: "expired",
      claim_id: null,
      deadline: new Date(Date.now() - 5 * 24 * 60 * 60 * 1000).toISOString(),
    };
    await mount({ records: [expiredRecord] });
    const badge = host.querySelector(".status-badge") as HTMLElement;
    expect(badge).toBeTruthy();
    expect(badge.textContent).toBe("已过期");
    expect(badge.classList.contains("expired")).toBe(true);
    expect(badge.classList.contains("unknown")).toBe(false);
  });

  it("highlights the record referenced by ?claim=<id> deep-link", async () => {
    // Round 4 quality finding: Dashboard pushes /wins?claim=<PrizeClaim.id> but
    // WinRecords never consumed route.query.claim. Now it scrolls to + outlines
    // the record matching claim_id (fixture record id=2 has claim_id=102).
    await mount({}, { claim: "102" });
    const highlighted = host.querySelector(".record-card.highlight") as HTMLElement;
    expect(highlighted).toBeTruthy();
    expect(highlighted.id).toBe("claim-102");
  });

  it("shows a non-blocking notice when ?claim=<id> deep-link target is not found", async () => {
    // Hunter follow-up: deep-link must not silently fail when the record is
    // filtered out or absent. A non-blocking notice surfaces the mismatch.
    await mount({}, { claim: "99999" });
    const notice = host.querySelector(".notice");
    expect(notice).toBeTruthy();
    expect(notice?.textContent).toContain("未定位到该中奖记录");
    expect(host.querySelector(".record-card.highlight")).toBeNull();
  });

  it("does not show tax hint for prize exactly at 1万元 (10000元=1_000_000分)", async () => {
    // Tax law taxes prizes exceeding 1万元; the hint wording says "超 1 万元"
    // (exceeds 1万元). Exactly 1万元 must NOT show the hint.
    await mount({
      records: [
        {
          ...DEFAULT_RECORDS[0],
          prize_amount: 1_000_000, // 10000元 exactly
        },
      ],
    });
    expect(host.textContent).not.toContain("单笔中奖超 1 万元");
  });

  it("shows tax hint for prize above 1万元 (1_000_001分)", async () => {
    await mount({
      records: [
        {
          ...DEFAULT_RECORDS[0],
          prize_amount: 1_000_001, // just above 10000元
        },
      ],
    });
    expect(host.textContent).toContain("单笔中奖超 1 万元");
  });

  it("does not reuse error banner for claim success message", async () => {
    // Claim success must not be rendered as an error State (which would hide the
    // refreshed list behind an error banner for 1.5s). The refreshed list with
    // the record's new 已领取 badge is sufficient feedback.
    await mount();
    const claimBtn = host.querySelector(".claim-btn") as HTMLButtonElement;
    expect(claimBtn).toBeTruthy();
    claimBtn.click();
    await nextTick();
    await new Promise((r) => setTimeout(r, 0));
    await nextTick();
    // No State element should display "领取成功" as its title.
    const stateTitles = Array.from(host.querySelectorAll(".state-title")).map(
      (el) => el.textContent
    );
    expect(stateTitles).not.toContain("领取成功");
    // The record list should still be visible (not hidden behind an error banner).
    expect(host.querySelector(".record-list")).toBeTruthy();
  });

  it("keeps success message when refresh fails after claim", async () => {
    await mount({
      loadAfterClaim: () => jsonResponse(500, { detail: "refresh down" }),
    });
    const claimBtn = host.querySelector(".claim-btn") as HTMLButtonElement;
    expect(claimBtn).toBeTruthy();
    claimBtn.click();
    await nextTick();
    await new Promise((r) => setTimeout(r, 0));
    await nextTick();
    expect(host.textContent).toContain("领取成功但刷新失败");
  });
});
