import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createApp, nextTick } from "vue";
import Settings from "../pages/Settings.vue";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("Settings.vue notification rules", () => {
  let host: HTMLDivElement;
  let app: ReturnType<typeof createApp> | null = null;
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    host = document.createElement("div");
    document.body.appendChild(host);
    fetchMock = vi.fn();
    globalThis.fetch = fetchMock as unknown as typeof fetch;
  });

  afterEach(() => {
    app?.unmount();
    host.remove();
    app = null;
    vi.restoreAllMocks();
  });

  async function mount() {
    app = createApp(Settings);
    app.mount(host);
    await nextTick();
    await new Promise((r) => setTimeout(r, 0));
    await nextTick();
    return host;
  }

  function stubLoad(channels: unknown[] = [], rules: unknown[] = []) {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(200, channels))
      .mockResolvedValueOnce(jsonResponse(200, rules))
      .mockResolvedValueOnce(
        jsonResponse(200, {
          path_a: { title: "a", body: "A" },
          path_b: { title: "b", body: "B" },
        }),
      )
      .mockResolvedValueOnce(jsonResponse(200, { enabled: false, start: "22:00", end: "07:00" }));
  }

  it("renders per-lottery strategy form after loading", async () => {
    stubLoad([], []);
    await mount();

    const cards = host.querySelectorAll(".card");
    expect(cards.length).toBeGreaterThanOrEqual(4);
  });

  it("loads existing rules and renders strategy selects", async () => {
    stubLoad([], [
      { id: 1, lottery_code: "ssq", strategy: "win_only", timing: "21:30" },
      { id: 2, lottery_code: "dlt", strategy: "every", timing: "07:00" },
    ]);
    await mount();

    const rows = host.querySelectorAll(".rule-row");
    expect(rows.length).toBe(7);
    const ssqSelect = host.querySelector('[data-lottery="ssq"] select') as HTMLSelectElement;
    expect(ssqSelect.value).toBe("win_only");
  });

  it("saves rule on strategy change", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(200, []))
      .mockResolvedValueOnce(jsonResponse(200, []))
      .mockResolvedValueOnce(
        jsonResponse(200, {
          path_a: { title: "a", body: "A" },
          path_b: { title: "b", body: "B" },
        }),
      )
      .mockResolvedValueOnce(jsonResponse(200, { enabled: false, start: "22:00", end: "07:00" }))
      .mockResolvedValueOnce(jsonResponse(200, { csrf_token: "tok" }))
      .mockResolvedValueOnce(jsonResponse(200, { id: 3, lottery_code: "ssq", strategy: "win_only", timing: null }));

    await mount();

    const ssqSelect = host.querySelector('[data-lottery="ssq"] select') as HTMLSelectElement;
    ssqSelect.value = "win_only";
    ssqSelect.dispatchEvent(new Event("change"));
    await nextTick();
    await new Promise((r) => setTimeout(r, 0));

    const putCall = fetchMock.mock.calls.find((call) => call[1]?.method === "PUT");
    expect(putCall).toBeDefined();
    expect(JSON.parse(putCall![1].body)).toEqual({ lottery_code: "ssq", strategy: "win_only" });
  });
});

describe("Settings.vue DND", () => {
  let host: HTMLDivElement;
  let app: ReturnType<typeof createApp> | null = null;
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    host = document.createElement("div");
    document.body.appendChild(host);
    fetchMock = vi.fn();
    globalThis.fetch = fetchMock as unknown as typeof fetch;
  });

  afterEach(() => {
    app?.unmount();
    host.remove();
    app = null;
    vi.restoreAllMocks();
  });

  async function mount() {
    app = createApp(Settings);
    app.mount(host);
    await nextTick();
    await new Promise((r) => setTimeout(r, 0));
    await nextTick();
    return host;
  }

  it("persists DND enable via POST /channels/dnd", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(200, []))
      .mockResolvedValueOnce(jsonResponse(200, []))
      .mockResolvedValueOnce(
        jsonResponse(200, {
          path_a: { title: "a", body: "A" },
          path_b: { title: "b", body: "B" },
        }),
      )
      .mockResolvedValueOnce(jsonResponse(200, { enabled: false, start: "22:00", end: "07:00" }))
      .mockResolvedValueOnce(jsonResponse(200, { csrf_token: "tok" }))
      .mockResolvedValueOnce(jsonResponse(200, { enabled: true, start: "22:00", end: "07:00" }));

    await mount();

    const toggle = host.querySelector('input[type="checkbox"].dnd-toggle') as HTMLInputElement;
    toggle.checked = true;
    toggle.dispatchEvent(new Event("change"));
    await nextTick();
    await new Promise((r) => setTimeout(r, 0));

    const postCall = fetchMock.mock.calls.find((call) => call[1]?.method === "POST" && call[0] === "/channels/dnd");
    expect(postCall).toBeDefined();
    expect(JSON.parse(postCall![1].body)).toEqual({ enabled: true, start: "22:00", end: "07:00" });
  });
});
