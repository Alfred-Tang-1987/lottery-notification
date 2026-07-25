import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createApp, nextTick } from "vue";
import { createRouter, createMemoryHistory } from "vue-router";
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
  let dashboardCallCount = 0;
  return vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const u = String(url);
    if (u === "/auth/csrf") return jsonResponse(200, { csrf_token: "tok" });
    if (method === "GET" && u.startsWith("/api/dashboard/calendar")) {
      if (overrides.calendarFail) return jsonResponse(500, { detail: "calendar down" });
      return jsonResponse(200, overrides.calendar ?? DEFAULT_CALENDAR);
    }
    if (method === "GET" && u.startsWith("/api/dashboard/agencies")) {
      if (overrides.agenciesFail) return jsonResponse(500, { detail: "agencies down" });
      return jsonResponse(200, overrides.agencies ?? DEFAULT_AGENCIES);
    }
    if (method === "GET" && u.startsWith("/api/dashboard")) {
      dashboardCallCount += 1;
      // Allow simulating a dashboard failure on a specific call index (e.g. the
      // second call after a period change) while calendar/agencies still resolve.
      const failOnCall = overrides.dashboardFailOnCall as number | undefined;
      if (failOnCall != null && dashboardCallCount === failOnCall) {
        return jsonResponse(500, { detail: "dashboard down" });
      }
      return jsonResponse(200, overrides.dashboard ?? DEFAULT_DASHBOARD);
    }
    return jsonResponse(200, {});
  });
}

function createTestRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", component: { template: "<div>Dashboard</div>" } },
      { path: "/wins", component: { template: "<div>WinRecords</div>" } },
    ],
  });
}

describe("Dashboard.vue (T6g)", () => {
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
    app = createApp(Dashboard);
    app.use(createTestRouter());
    app.mount(host);
    await nextTick();
    await new Promise((r) => setTimeout(r, 0));
    await nextTick();
    return { host, fetchMock };
  }

  function lastCall(urlPrefix: string, method?: string) {
    const calls = fetchMock.mock.calls.filter((c) => {
      const u = String(c[0]);
      const urlMatch = u.startsWith(urlPrefix) && !u.startsWith(`${urlPrefix}/`);
      if (!method) return urlMatch;
      return urlMatch && (c[1] as RequestInit | undefined)?.method === method;
    });
    return calls[calls.length - 1];
  }

  it("renders D5 priority order: claims > hits > summary > draws > calendar > agencies", async () => {
    await mount();
    const titles = Array.from(host.querySelectorAll(".card-title")).map(
      (el) => el.textContent
    );
    expect(titles).toEqual([
      "待兑奖",
      "我的命中",
      "盈亏速览",
      "近期开奖概览",
      "开奖日历",
      "附近代销点",
    ]);
  });

  it("renders welfare contribution as ¥3.6", async () => {
    await mount();
    expect(host.textContent).toContain("公益贡献");
    expect(host.textContent).toContain("¥3.6");
  });

  it("renders calendar content with lottery name and date", async () => {
    await mount();
    expect(host.textContent).toContain("双色球");
    expect(host.textContent).toContain("8月8日");
  });

  it("renders agencies content with name and address", async () => {
    await mount();
    expect(host.textContent).toContain("朝阳路销售厅");
    expect(host.textContent).toContain("北京市朝阳区朝阳路 XX 号");
  });

  it("falls back to ¥0 when welfare contribution is missing", async () => {
    await mount({
      dashboard: {
        ...DEFAULT_DASHBOARD,
        summary: { ...DEFAULT_DASHBOARD.summary, welfare_contribution: null },
      },
    });
    // Welfare card should be hidden entirely when contribution is absent
    expect(host.textContent).not.toContain("公益贡献");
  });

  it("sends period=month to /api/dashboard by default", async () => {
    await mount();
    const call = lastCall("/api/dashboard", "GET");
    expect(call).toBeDefined();
    const url = String(call![0]);
    expect(url).toContain("period=month");
  });

  it("renders a period selector for summary", async () => {
    await mount();
    const select = host.querySelector('[data-testid="dashboard-period"]') as HTMLSelectElement;
    expect(select).toBeTruthy();
    expect(select.value).toBe("month");
  });

  it("reloads dashboard when period changes to year", async () => {
    await mount();
    const select = host.querySelector('[data-testid="dashboard-period"]') as HTMLSelectElement;
    select.value = "year";
    select.dispatchEvent(new Event("change"));
    await nextTick();
    await new Promise((r) => setTimeout(r, 0));
    await nextTick();
    const call = lastCall("/api/dashboard", "GET");
    expect(String(call![0])).toContain("period=year");
  });

  it("passes through custom date range when selected", async () => {
    await mount();
    const select = host.querySelector('[data-testid="dashboard-period"]') as HTMLSelectElement;
    select.value = "custom";
    select.dispatchEvent(new Event("change"));
    await nextTick();

    const fromInput = host.querySelector('[data-testid="dashboard-date-from"]') as HTMLInputElement;
    const toInput = host.querySelector('[data-testid="dashboard-date-to"]') as HTMLInputElement;
    fromInput.value = "2024-01-01";
    toInput.value = "2024-12-31";
    fromInput.dispatchEvent(new Event("input"));
    toInput.dispatchEvent(new Event("input"));
    await nextTick();

    const applyBtn = host.querySelector('[data-testid="dashboard-apply-period"]') as HTMLButtonElement;
    applyBtn.click();
    await nextTick();
    await new Promise((r) => setTimeout(r, 0));
    await nextTick();

    const call = lastCall("/api/dashboard", "GET");
    const url = String(call![0]);
    expect(url).toContain("period=custom");
    expect(url).toContain("date_from=2024-01-01");
    expect(url).toContain("date_to=2024-12-31");
  });

  it("surfaces error and keeps calendar/agencies visible when period change fails (partial degradation)", async () => {
    // Spec §12.4 PARTIAL state for dashboard. When the dashboard API rejects on
    // a period change but calendar/agencies resolve, the user must (a) see that
    // the new period failed (no silent stale data) AND (b) still see the
    // calendar/agencies sections (partial degradation, not all-or-nothing).
    await mount({ dashboardFailOnCall: 2 });
    // First load succeeded; now trigger a period change to fire the 2nd call.
    const periodSelect = host.querySelector('[data-testid="dashboard-period"]') as HTMLSelectElement;
    expect(periodSelect).toBeTruthy();
    periodSelect.value = "year";
    periodSelect.dispatchEvent(new Event("change"));
    await nextTick();
    await new Promise((r) => setTimeout(r, 0));
    await nextTick();
    // Error must be surfaced (no silent stale data).
    const errorTitle = Array.from(host.querySelectorAll(".state-title")).find(
      (el) => el.textContent?.includes("dashboard down")
    );
    expect(errorTitle).toBeTruthy();
    // Calendar and agencies must still render (they resolved independently).
    expect(host.textContent).toContain("开奖日历");
    expect(host.textContent).toContain("附近代销点");
    expect(host.textContent).toContain("双色球"); // calendar item
    expect(host.textContent).toContain("朝阳路销售厅"); // agency item
  });

  it("uses agency name as map query label, not raw lat,lng", async () => {
    // The map pin label should be the human-readable agency name, not
    // "lat,lng" coordinates, so the user sees a meaningful destination.
    await mount();
    const agencyCard = host.querySelector(".agency-card") as HTMLButtonElement;
    expect(agencyCard).toBeTruthy();
    // Spy on window.open to capture the URL without navigating.
    const openedUrls: string[] = [];
    const origOpen = window.open;
    window.open = ((url?: string | URL) => {
      if (typeof url === "string") openedUrls.push(url);
      return null;
    }) as typeof window.open;
    try {
      agencyCard.click();
    } finally {
      window.open = origOpen;
    }
    const mapUrl = openedUrls[openedUrls.length - 1];
    expect(mapUrl).toBeDefined();
    // The q parameter should be the URL-encoded agency name, not "lat,lng".
    const agencyName = "中国福利彩票（朝阳路销售厅）";
    expect(mapUrl).toContain(`q=${encodeURIComponent(agencyName)}`);
    // And must NOT be the raw "lat,lng" string.
    expect(mapUrl).not.toMatch(/q=\d/);
  });

  it("surfaces partial-warn (not blocking error) when calendar fails but dashboard succeeds", async () => {
    // Round 4 hunter + quality finding: secondary failures (calendar/agency)
    // must NOT be swallowed, but also must NOT gate the dashboard main body via
    // the blocking error State (which would hide D5 first-screen 待兑奖).
    // Solution: a non-blocking partialWarn banner + dashboard data still renders.
    await mount({ calendarFail: true });
    // Dashboard main body must render (not replaced by error State).
    expect(host.textContent).toContain("待兑奖");
    // Non-blocking warning banner must be visible.
    const warn = host.querySelector(".partial-warn");
    expect(warn).toBeTruthy();
    expect(warn?.textContent).toContain("开奖日历");
    // Blocking error/empty/loading State must NOT be shown (State.vue uses
    // class="state"; querying it returns null only when no State rendered).
    expect(host.querySelector('.state')).toBeNull();
  });

  it("passes lat/lng to agencies API when geolocation is granted", async () => {
    // Mock navigator.geolocation to simulate user granting location permission.
    const fakePosition = {
      coords: { latitude: 31.2304, longitude: 121.4737 },
    };
    const geoStub = {
      getCurrentPosition: vi.fn((success: (p: typeof fakePosition) => void) => {
        success(fakePosition);
      }),
    };
    Object.defineProperty(globalThis.navigator, "geolocation", {
      value: geoStub,
      configurable: true,
    });

    await mount();
    // Wait for geolocation + agencies request to complete
    await nextTick();
    await new Promise((r) => setTimeout(r, 0));
    await nextTick();

    // Agencies request must include lat/lng query params
    const agencyCall = fetchMock.mock.calls.find((c) =>
      String(c[0]).startsWith("/api/dashboard/agencies?lat=")
    );
    expect(agencyCall).toBeDefined();
    const url = String(agencyCall![0]);
    expect(url).toContain("lat=31.2304");
    expect(url).toContain("lng=121.4737");
    // Subtitle should show "基于当前位置"
    expect(host.textContent).toContain("基于当前位置");
  });

  it("falls back to agencies without lat/lng when geolocation is denied", async () => {
    // Mock navigator.geolocation to simulate user denying location permission.
    const geoStub = {
      getCurrentPosition: vi.fn((_: unknown, error: () => void) => {
        error();
      }),
    };
    Object.defineProperty(globalThis.navigator, "geolocation", {
      value: geoStub,
      configurable: true,
    });

    await mount();
    await nextTick();
    await new Promise((r) => setTimeout(r, 0));
    await nextTick();

    // Agencies request must NOT include lat/lng (falls back to mock)
    const agencyCall = fetchMock.mock.calls.find((c) =>
      String(c[0]).startsWith("/api/dashboard/agencies")
    );
    expect(agencyCall).toBeDefined();
    const url = String(agencyCall![0]);
    expect(url).not.toContain("lat=");
    // Subtitle should show "定位未授权"
    expect(host.textContent).toContain("定位未授权");
  });
});
