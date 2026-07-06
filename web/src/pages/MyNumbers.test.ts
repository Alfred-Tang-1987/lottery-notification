import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createApp, nextTick } from "vue";
import MyNumbers from "../pages/MyNumbers.vue";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function stubApi(tickets: unknown[] = [], opts: { postFailOnRow?: number } = {}) {
  let postCount = 0;
  return vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const u = String(url);
    if (u === "/auth/csrf") return jsonResponse(200, { csrf_token: "tok" });
    if (u === "/tickets" && method === "GET") return jsonResponse(200, tickets);
    if (u === "/tickets" && method === "POST") {
      postCount++;
      // Simulate a single-row API failure (e.g. duplicate) at the configured row.
      // Other rows succeed; the batch must continue (per-row isolation).
      if (opts.postFailOnRow && postCount === opts.postFailOnRow) {
        return jsonResponse(400, { detail: "号码已存在" });
      }
      return jsonResponse(200, { id: postCount });
    }
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

  async function mount(tickets: unknown[] = [], opts: { postFailOnRow?: number } = {}) {
    globalThis.fetch = stubApi(tickets, opts) as unknown as typeof fetch;
    app = createApp(MyNumbers);
    app.mount(host);
    await nextTick();
    return host;
  }

  it("modal backdrop is a <button> (no div onclick) per spec §12.4 A11y baseline", async () => {
    await mount();
    // Open the add-ticket modal
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

  it("CSV import isolates per-row failure: one bad row does NOT abort the batch", async () => {
    // Hunter round 4: CSV 批量导入未隔离单条失败 → 整条循环被中断。
    // Fix: per-row try/catch around apiPost; failures recorded, batch continues.
    await mount([], { postFailOnRow: 2 }); // 3 rows, row 2 fails
    // Open the "添加号码" modal (CSV textarea lives inside v-if="showForm").
    const addBtn = Array.from(host.querySelectorAll("button")).find((b) =>
      b.textContent?.includes("添加号码")
    ) as HTMLButtonElement | undefined;
    expect(addBtn).toBeTruthy();
    addBtn!.click();
    await nextTick();
    // Expand the <details> CSV batch-import section.
    const details = host.querySelector("details") as HTMLDetailsElement | null;
    expect(details).toBeTruthy();
    details!.open = true;
    details!.dispatchEvent(new Event("toggle"));
    await nextTick();
    const ta = host.querySelector("textarea") as HTMLTextAreaElement;
    expect(ta).toBeTruthy();
    // 3 valid CSV rows (comma-separated numbers per parseCsvLine format);
    // the 2nd will hit the simulated API failure.
    ta.value = "ssq,1,2,3,4,5,6,7\ndlt,1,2,3,4,5,6,7\nfc3d,1,2,3";
    ta.dispatchEvent(new Event("input"));
    await nextTick();
    const importBtn = Array.from(host.querySelectorAll("button")).find((b) =>
      b.textContent?.includes("导入 CSV")
    ) as HTMLButtonElement | undefined;
    expect(importBtn).toBeTruthy();
    importBtn!.click();
    // Wait for the async loop + final load() to settle.
    await new Promise((r) => setTimeout(r, 50));
    await nextTick();
    // Row 1 + Row 3 succeeded → 2 imported (row 2 failed but did not abort).
    const successEl = host.querySelector(".csv-success") as HTMLElement | null;
    expect(successEl?.textContent || "").toMatch(/成功导入 2 条/); // exact count
    // Error surfaced for the failed row (not swallowed).
    const errEl = host.querySelector(".csv-error") as HTMLElement | null;
    expect(errEl?.textContent || "").toContain("行 2");
  });
});
