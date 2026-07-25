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

  it("multiplier=1 (单倍投注) should be accepted — 倍投 1–99 倍", async () => {
    // Bug：前端强制 multiplier ≥ 2，单倍投注无法保存。
    // 业务规则已调整为 1–99 倍（单倍 = 不倍投，合法场景）。
    // 后端 TicketIn/Entry/Repository 均允许 1-99，仅前端 UI 校验过严。
    await mount([]);
    // Open the add-ticket modal (empty state CTA "去选一注")
    const cta = Array.from(host.querySelectorAll("button")).find((b) =>
      b.textContent?.includes("去选一注") || b.textContent?.includes("添加号码")
    ) as HTMLButtonElement | undefined;
    expect(cta).toBeTruthy();
    cta!.click();
    await nextTick();
    // numbers_json input 是 required（CSV 为空时），不填会触发 HTML5 校验阻止 submit。
    // 填入有效 JSON 让表单可提交，聚焦验证 multiplier=1 是否被接受。
    const jsonInput = host.querySelector('input[type="text"]') as HTMLInputElement;
    expect(jsonInput).toBeTruthy();
    const jsonSetter = Object.getOwnPropertyDescriptor(
      HTMLInputElement.prototype,
      "value"
    )!.set!;
    jsonSetter.call(jsonInput, '{"front":[1,2,3,4,5,6],"back":[7]}');
    jsonInput.dispatchEvent(new Event("input", { bubbles: true }));
    await nextTick();
    // Find multiplier input (type="number"). 表单里 multiplier 在 cost 之前，取第一个。
    const numberInputs = host.querySelectorAll('input[type="number"]');
    const multInput = numberInputs[0] as HTMLInputElement;
    expect(multInput).toBeTruthy();
    // Set to 1 (single bet). jsdom 对 input[type=number] 的 valueAsNumber 支持不完整，
    // 直接赋 value 不触发 v-model.number 更新——用 native setter 走真实 input 行为。
    const nativeSetter = Object.getOwnPropertyDescriptor(
      HTMLInputElement.prototype,
      "value"
    )!.set!;
    nativeSetter.call(multInput, "1");
    multInput.dispatchEvent(new Event("input", { bubbles: true }));
    await nextTick();
    // 直接 dispatch submit 事件绕过 jsdom HTML5 form validation（点击 submit 按钮
    // 时 jsdom 会检查 required 字段，可能因同步问题阻止 submit）。
    const formEl = host.querySelector("form") as HTMLFormElement;
    expect(formEl).toBeTruthy();
    formEl.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    await new Promise((r) => setTimeout(r, 50));
    await nextTick();
    // Assert: no "倍投必须是 2–99" validation error (single bet accepted)
    const errorText = host.textContent || "";
    expect(errorText).not.toMatch(/倍投必须是\s*2[\u2013-]99/);
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
