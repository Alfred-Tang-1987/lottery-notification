import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createApp, nextTick } from "vue";
import MyNumbers from "../pages/MyNumbers.vue";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function stubApi(tickets: unknown[] = []) {
  return vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const u = String(url);
    if (u === "/auth/csrf") return jsonResponse(200, { csrf_token: "tok" });
    if (u === "/tickets" && method === "GET") return jsonResponse(200, tickets);
    return jsonResponse(200, {});
  });
}

describe("MyNumbers.vue (T7 A11y)", () => {
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

  async function mount(tickets: unknown[] = []) {
    globalThis.fetch = stubApi(tickets) as unknown as typeof fetch;
    app = createApp(MyNumbers);
    app.mount(host);
    await nextTick();
    return host;
  }

  it("modal backdrop is a <button> (no div onclick) per spec §12.4 A11y baseline", async () => {
    await mount();
    // Open the add-ticket modal
    const openBtn = host.querySelector('button.primary') as HTMLButtonElement | null;
    // Find the "+ 添加号码" button by text
    const addBtn = Array.from(host.querySelectorAll("button")).find((b) =>
      b.textContent?.includes("添加号码")
    ) as HTMLButtonElement | undefined;
    addBtn?.click();
    await nextTick();

    const backdrop = host.querySelector(".modal-backdrop");
    expect(backdrop).not.toBeNull();
    // spec §12.4: 交互元素一律 <button>/<a>，禁 div onclick
    expect(backdrop?.tagName.toLowerCase()).toBe("button");
  });

  it("clicking backdrop button closes the modal", async () => {
    await mount();
    const addBtn = Array.from(host.querySelectorAll("button")).find((b) =>
      b.textContent?.includes("添加号码")
    ) as HTMLButtonElement | undefined;
    addBtn?.click();
    await nextTick();
    expect(host.querySelector(".modal")).not.toBeNull();

    const backdrop = host.querySelector(".modal-backdrop") as HTMLButtonElement | null;
    backdrop?.click();
    await nextTick();
    expect(host.querySelector(".modal")).toBeNull();
  });
});
