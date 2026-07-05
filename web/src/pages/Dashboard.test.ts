import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createApp, nextTick } from "vue";
import Dashboard from "../pages/Dashboard.vue";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const DEFAULT_DASHBOARD = {
  latest_draws: [
    {
      lottery_code: "ssq",
      lottery_name: "双色球",
      draw_no: "2024090",
      draw_date: "2024-08-04",
      numbers_json: '{"front": [1, 2, 3, 4, 5, 6], "back": [7]}',
      verified: true,
      single_source: false,
    },
  ],
  pending_claims: [
    {
      id: 1,
      comparison_id: 101,
      lottery_code: "ssq",
      lottery_name: "双色球",
      draw_no: "2024090",
      prize_tier: 6,
      prize_amount: 500,
      deadline: "2024-10-03",
      status: "pending",
      days_left: 10,
    },
  ],
  recent_hits: [
    {
      id: 201,
      lottery_name: "双色球",
      draw_no: "2024090",
      prize_tier: 6,
      prize_amount: 500,
      claim_status: "pending",
    },
  ],
  summary: {
    total_cost: 1000,
    total_prize: 500,
    pending_amount: 0,
    net: -500,
    win_count: 1,
    ticket_count: 5,
    win_rate: 0.2,
    welfare_contribution: 360,
  },
};

const DEFAULT_CALENDAR = [
  {
    lottery_code: "ssq",
    lottery_name: "双色球",
    category: "welfare",
    draw_days: [1, 3, 6],
    next_draw_date: "2024-08-08",
  },
];

const DEFAULT_AGENCIES = [
  {
    name: "中国福利彩票（朝阳路销售厅）",
    address: "北京市朝阳区朝阳路 XX 号",
    category: "welfare",
    lat: 39.9242,
    lng: 116.4987,
    distance_m: 320,
  },
];

function stubApi(overrides: Record<string, unknown> = {}) {
  return vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const u = String(url);
    if (u === "/auth/csrf") return jsonResponse(200, { csrf_token: "tok" });
    if (method === "GET" && u === "/api/dashboard") {
      return jsonResponse(200, overrides.dashboard ?? DEFAULT_DASHBOARD);
    }
    if (method === "GET" && u === "/api/dashboard/calendar") {
      return jsonResponse(200, overrides.calendar ?? DEFAULT_CALENDAR);
    }
    if (method === "GET" && u === "/api/dashboard/agencies") {
      return jsonResponse(200, overrides.agencies ?? DEFAULT_AGENCIES);
    }
    return jsonResponse(200, {});
  });
}

describe("Dashboard.vue (T6g)", () => {
  let host: HTMLDivElement;
  let app: ReturnType<typeof createApp> | null = null;

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
    const fetchMock = stubApi(overrides);
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    app = createApp(Dashboard);
    // Provide a mock router injection to suppress "Symbol(router) not found" warnings.
    app.provide(
      Symbol.for("vue-router-router"),
      { push: vi.fn(() => Promise.resolve()) },
    );
    app.mount(host);
    await nextTick();
    await new Promise((r) => setTimeout(r, 0));
    await nextTick();
    return { host, fetchMock };
  }

  it("renders D5 priority order: claims > hits > summary > draws > calendar/agencies", async () => {
    await mount();
    const cards = host.querySelectorAll(".card");
    const texts = Array.from(cards).map((c) => c.querySelector(".card-title")?.textContent ?? "");
    expect(texts[0]).toContain("待兑奖");
    expect(texts[1]).toContain("我的命中");
    expect(texts[2]).toContain("盈亏速览");
    expect(texts[3]).toContain("近期开奖概览");
    expect(texts[4]).toContain("开奖日历");
    expect(texts[5]).toContain("附近代销点");
  });

  it("renders welfare contribution in summary", async () => {
    await mount();
    expect(host.textContent).toContain("公益贡献");
    expect(host.textContent).toContain("¥3.6"); // 360 cents
  });

  it("renders calendar section", async () => {
    await mount();
    expect(host.textContent).toContain("开奖日历");
    expect(host.textContent).toContain("双色球");
    expect(host.textContent).toContain("8月8日");
  });

  it("renders nearby agencies section", async () => {
    await mount();
    expect(host.textContent).toContain("附近代销点");
    expect(host.textContent).toContain("朝阳路销售厅");
  });

  it("renders empty welfare as 0 when missing", async () => {
    await mount({
      dashboard: {
        ...DEFAULT_DASHBOARD,
        summary: { ...DEFAULT_DASHBOARD.summary, welfare_contribution: 0 },
      },
    });
    expect(host.textContent).toContain("¥0");
  });
});
