import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createApp, nextTick } from "vue";
import WinRecords from "../pages/WinRecords.vue";

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
    deadline: new Date(Date.now() + 10 * 24 * 60 * 60 * 1000).toISOString(), // 10 days left
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
    prize_amount: 12_000_000, // 12万元, exceeds tax threshold
    is_win: true,
    created_at: "2024-08-06T10:00:00",
    claim_status: "pending",
    claim_id: 102,
    deadline: new Date(Date.now() + 20 * 24 * 60 * 60 * 1000).toISOString(), // 20 days left
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
  return vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const u = String(url);
    if (u === "/auth/csrf") return jsonResponse(200, { csrf_token: "tok" });
    if (method === "POST" && u.startsWith("/claims/")) return jsonResponse(200, { ok: true });
    if (method === "GET" && u.startsWith("/api/comparisons")) {
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

  async function mount(overrides: Record<string, unknown> = {}) {
    fetchMock = stubApi(overrides);
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    app = createApp(WinRecords);
    app.mount(host);
    await nextTick();
    await new Promise((r) => setTimeout(r, 0));
    await nextTick();
    return host;
  }

  function lastCall(urlPrefix: string, method?: string) {
    const calls = fetchMock.mock.calls.filter((c) => {
      const urlMatch = String(c[0]).startsWith(urlPrefix);
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
    // 3 records total, 2 pending, 1 claimed, 0 expired
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
    // Only 2 pending records
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
});
