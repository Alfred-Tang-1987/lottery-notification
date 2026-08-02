/** ForgotFlow 组件测试（Plan 08 / T6）：两步状态机 + 倒计时 + 本地校验。 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createApp, nextTick } from "vue";
import ForgotFlow from "./ForgotFlow.vue";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function stubApi(overrides: Record<string, unknown> = {}) {
  return vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const u = String(url);
    if (u === "/auth/csrf") return jsonResponse(200, { csrf_token: "tok" });
    if (method === "POST" && u === "/auth/forgot-password")
      return jsonResponse(200, overrides.forgot ?? { ok: true, message: "若账号存在，验证码已发送至你的邮箱" });
    if (method === "POST" && u === "/auth/reset-password") {
      if (overrides.resetFail)
        return jsonResponse(400, { detail: "验证码错误或已过期" });
      return jsonResponse(200, { ok: true });
    }
    throw new Error(`Unexpected fetch: ${method} ${url}`);
  });
}

describe("ForgotFlow.vue", () => {
  let host: HTMLDivElement;
  let app: ReturnType<typeof createApp> | null = null;
  let fetchMock: ReturnType<typeof vi.fn>;
  let emitted: string[] = [];

  beforeEach(() => {
    host = document.createElement("div");
    document.body.appendChild(host);
    emitted = [];
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
    app = createApp(ForgotFlow, { onDone: () => emitted.push("done") });
    app.mount(host);
    await nextTick();
    return host;
  }

  function setInput(el: Element | null, value: string) {
    const input = el as HTMLInputElement;
    input.value = value;
    input.dispatchEvent(new Event("input"));
  }

  it("步骤 1 渲染用户名输入与发送按钮", async () => {
    await mount();
    expect(host.querySelector("[data-test='forgot-username']")).toBeTruthy();
    expect(host.querySelector("[data-test='forgot-send']")).toBeTruthy();
    expect(host.querySelector("[data-test='forgot-step1']")).toBeTruthy();
  });

  it("发送验证码后进入步骤 2 并显示统一话术", async () => {
    await mount();
    setInput(host.querySelector("[data-test='forgot-username']"), "alice");
    (host.querySelector("[data-test='forgot-send']") as HTMLButtonElement).click();
    await new Promise((r) => setTimeout(r, 0));
    await nextTick();
    expect(host.querySelector("[data-test='forgot-step2']")).toBeTruthy();
    expect(host.textContent).toContain("若账号存在");
  });

  it("发送后按钮进入 60s 倒计时禁用", async () => {
    vi.useFakeTimers();
    try {
      await mount();
      setInput(host.querySelector("[data-test='forgot-username']"), "alice");
      (host.querySelector("[data-test='forgot-send']") as HTMLButtonElement).click();
      await vi.advanceTimersByTimeAsync(0);
      await nextTick();
      const btn = host.querySelector("[data-test='forgot-send']") as HTMLButtonElement;
      expect(btn.disabled).toBe(true);
      expect(btn.textContent).toMatch(/60/);
      await vi.advanceTimersByTimeAsync(61_000);
      await nextTick();
      expect(btn.disabled).toBe(false);
    } finally {
      vi.useRealTimers();
    }
  });

  it("两次密码不一致 → 本地报错不发 reset 请求", async () => {
    await mount();
    setInput(host.querySelector("[data-test='forgot-username']"), "alice");
    (host.querySelector("[data-test='forgot-send']") as HTMLButtonElement).click();
    await new Promise((r) => setTimeout(r, 0));
    await nextTick();
    const resetCallsBefore = fetchMock.mock.calls.filter(
      (c) => String(c[0]) === "/auth/reset-password",
    ).length;
    setInput(host.querySelector("[data-test='forgot-code']"), "123456");
    setInput(host.querySelector("[data-test='forgot-newpass']"), "newpass123");
    setInput(host.querySelector("[data-test='forgot-confirm']"), "different1");
    (host.querySelector("[data-test='forgot-submit']") as HTMLButtonElement).click();
    await nextTick();
    expect(host.textContent).toContain("两次输入的密码不一致");
    const resetCallsAfter = fetchMock.mock.calls.filter(
      (c) => String(c[0]) === "/auth/reset-password",
    ).length;
    expect(resetCallsAfter).toBe(resetCallsBefore);
  });

  it("reset 成功 → emit done", async () => {
    await mount();
    setInput(host.querySelector("[data-test='forgot-username']"), "alice");
    (host.querySelector("[data-test='forgot-send']") as HTMLButtonElement).click();
    await new Promise((r) => setTimeout(r, 0));
    await nextTick();
    setInput(host.querySelector("[data-test='forgot-code']"), "123456");
    setInput(host.querySelector("[data-test='forgot-newpass']"), "newpass123");
    setInput(host.querySelector("[data-test='forgot-confirm']"), "newpass123");
    (host.querySelector("[data-test='forgot-submit']") as HTMLButtonElement).click();
    await new Promise((r) => setTimeout(r, 0));
    await nextTick();
    expect(emitted).toEqual(["done"]);
  });

  it("reset 失败 → 显示 400 文案", async () => {
    await mount({ resetFail: true });
    setInput(host.querySelector("[data-test='forgot-username']"), "alice");
    (host.querySelector("[data-test='forgot-send']") as HTMLButtonElement).click();
    await new Promise((r) => setTimeout(r, 0));
    await nextTick();
    setInput(host.querySelector("[data-test='forgot-code']"), "999999");
    setInput(host.querySelector("[data-test='forgot-newpass']"), "newpass123");
    setInput(host.querySelector("[data-test='forgot-confirm']"), "newpass123");
    (host.querySelector("[data-test='forgot-submit']") as HTMLButtonElement).click();
    await new Promise((r) => setTimeout(r, 0));
    await nextTick();
    expect(host.textContent).toContain("验证码错误或已过期");
    expect(emitted).toEqual([]);
  });
});
