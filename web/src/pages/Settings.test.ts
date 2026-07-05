import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createApp, nextTick } from "vue";
import Settings from "../pages/Settings.vue";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const DEFAULT_SETTINGS = {
  master_enable: true,
  path_a_enable: true,
  summary_time: "07:00",
  new_numbers_default_enabled: true,
};
const DEFAULT_DND = { enabled: false, start: "22:00", end: "07:00" };
const DEFAULT_TEMPLATES = {
  path_a: { title: "a", body: "A" },
  path_b: { title: "b", body: "B" },
};

// URL + method 路由的 fetch mock（无顺序依赖）。
// Settings.vue onMounted 用 Promise.all 并发 6 个 GET，mockResolvedValueOnce 顺序 stub 会错位；
// 改用按 URL 路由，无论 fetch 次序/并发都返回正确响应。
function stubApi(overrides: Record<string, unknown> = {}) {
  return vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const u = String(url);
    if (u === "/auth/csrf") return jsonResponse(200, { csrf_token: "tok" });
    // 写入响应（PUT/POST 后 load() 重读 GET，由下方 GET 分支返回更新值）
    if (method === "PUT" && u === "/channels/rules")
      return jsonResponse(200, overrides.ruleResponse ?? { id: 1, lottery_code: "ssq", strategy: "win_only" });
    if (method === "POST" && u === "/channels/dnd")
      return jsonResponse(200, overrides.dndResponse ?? { enabled: true, start: "22:00", end: "07:00" });
    if (method === "POST" && u === "/channels/preferences")
      return jsonResponse(200, overrides.preferencesResponse ?? { theme: "dark" });
    // GET 响应
    if (u === "/channels") return jsonResponse(200, overrides.channels ?? []);
    if (u === "/channels/rules") return jsonResponse(200, overrides.rules ?? []);
    if (u === "/channels/settings") return jsonResponse(200, overrides.settings ?? DEFAULT_SETTINGS);
    if (u === "/channels/templates") return jsonResponse(200, overrides.templates ?? DEFAULT_TEMPLATES);
    if (u === "/channels/dnd") return jsonResponse(200, overrides.dnd ?? DEFAULT_DND);
    if (u === "/channels/preferences") return jsonResponse(200, overrides.preferences ?? { theme: "auto" });
    return jsonResponse(200, {});
  });
}

describe("Settings.vue", () => {
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
    app = createApp(Settings);
    app.mount(host);
    await nextTick();
    await new Promise((r) => setTimeout(r, 0));
    await nextTick();
    return host;
  }

  function findCall(url: string, method: string) {
    return fetchMock.mock.calls.find(
      (c) => c[0] === url && (c[1] as RequestInit | undefined)?.method === method,
    );
  }

  it("renders per-lottery strategy form after loading", async () => {
    await mount();
    const cards = host.querySelectorAll(".card");
    expect(cards.length).toBeGreaterThanOrEqual(4);
  });

  it("loads existing rules and renders strategy selects", async () => {
    await mount({
      rules: [
        { id: 1, lottery_code: "ssq", strategy: "win_only" },
        { id: 2, lottery_code: "dlt", strategy: "every" },
      ],
    });
    const rows = host.querySelectorAll(".rule-row");
    expect(rows.length).toBe(7);
    const ssqSelect = host.querySelector('[data-lottery="ssq"] select') as HTMLSelectElement;
    expect(ssqSelect.value).toBe("win_only");
  });

  it("saves rule on strategy change via PUT /channels/rules", async () => {
    await mount();
    const ssqSelect = host.querySelector('[data-lottery="ssq"] select') as HTMLSelectElement;
    ssqSelect.value = "win_only";
    ssqSelect.dispatchEvent(new Event("change"));
    await nextTick();
    await new Promise((r) => setTimeout(r, 0));

    const putCall = findCall("/channels/rules", "PUT");
    expect(putCall).toBeDefined();
    expect(JSON.parse((putCall![1] as RequestInit).body as string)).toMatchObject({
      lottery_code: "ssq",
      strategy: "win_only",
    });
  });

  it("persists DND enable via POST /channels/dnd", async () => {
    await mount();
    const toggle = host.querySelector('input[type="checkbox"].dnd-toggle') as HTMLInputElement;
    toggle.checked = true;
    toggle.dispatchEvent(new Event("change"));
    await nextTick();
    await new Promise((r) => setTimeout(r, 0));

    const postCall = findCall("/channels/dnd", "POST");
    expect(postCall).toBeDefined();
    expect(JSON.parse((postCall![1] as RequestInit).body as string)).toMatchObject({
      enabled: true,
      start: "22:00",
      end: "07:00",
    });
  });

  it("persists summary_time via PUT /channels/settings", async () => {
    await mount();
    // 次日汇总时间是首个 type=time input（DND 的 start/end 在后面 section）
    const timeInputs = host.querySelectorAll('input[type="time"]');
    const summaryInput = timeInputs[0] as HTMLInputElement;
    summaryInput.value = "08:30";
    summaryInput.dispatchEvent(new Event("change"));
    await nextTick();
    await new Promise((r) => setTimeout(r, 0));

    const putCall = findCall("/channels/settings", "PUT");
    expect(putCall).toBeDefined();
    expect(JSON.parse((putCall![1] as RequestInit).body as string).summary_time).toBe("08:30");
  });

  it("persists master_enable toggle via PUT /channels/settings", async () => {
    await mount();
    // 推送时机 section 的 .timing-row 内首个 checkbox = 总开关 master_enable
    const toggles = host.querySelectorAll('.timing-row input[type="checkbox"]');
    const masterToggle = toggles[0] as HTMLInputElement;
    masterToggle.checked = false;
    masterToggle.dispatchEvent(new Event("change"));
    await nextTick();
    await new Promise((r) => setTimeout(r, 0));

    const putCall = findCall("/channels/settings", "PUT");
    expect(putCall).toBeDefined();
    expect(JSON.parse((putCall![1] as RequestInit).body as string).master_enable).toBe(false);
  });

  it("persists theme via POST /channels/preferences", async () => {
    await mount();
    const themeBtns = host.querySelectorAll(".theme-btn");
    // 浅色/深色/自动 → 深色是第 2 个（index 1）
    (themeBtns[1] as HTMLButtonElement).click();
    await nextTick();
    await new Promise((r) => setTimeout(r, 0));

    const postCall = findCall("/channels/preferences", "POST");
    expect(postCall).toBeDefined();
    expect(JSON.parse((postCall![1] as RequestInit).body as string).theme).toBe("dark");
  });
});
