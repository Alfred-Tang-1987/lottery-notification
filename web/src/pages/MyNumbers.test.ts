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
    // 点击「机选一注」自动选号（填充 padFront/padBack → syncPadToJson → numbers_json）
    const randomBtn = Array.from(host.querySelectorAll("button")).find((b) =>
      b.textContent?.includes("机选一注")
    ) as HTMLButtonElement | undefined;
    expect(randomBtn).toBeTruthy();
    randomBtn!.click();
    await nextTick();
    // Find multiplier input (type="number")。表单里只有 multiplier 是 number input（cost 已改为只读展示）。
    const multInput = host.querySelector('input[type="number"]') as HTMLInputElement;
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
    // 直接 dispatch submit 事件绕过 jsdom HTML5 form validation。
    const formEl = host.querySelector("form") as HTMLFormElement;
    expect(formEl).toBeTruthy();
    formEl.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    await new Promise((r) => setTimeout(r, 50));
    await nextTick();
    // Assert: no "倍投必须是 2–99" validation error (single bet accepted)
    const errorText = host.textContent || "";
    expect(errorText).not.toMatch(/倍投必须是\s*2[\u2013-]99/);
  });

  it("cost 自动计算：ssq 单式 1 倍 = 2.00 元", async () => {
    await mount([]);
    const cta = Array.from(host.querySelectorAll("button")).find((b) =>
      b.textContent?.includes("去选一注") || b.textContent?.includes("添加号码")
    ) as HTMLButtonElement | undefined;
    cta!.click();
    await nextTick();
    // 机选一注（ssq 默认 6+1）
    const randomBtn = Array.from(host.querySelectorAll("button")).find((b) =>
      b.textContent?.includes("机选一注")
    ) as HTMLButtonElement;
    randomBtn.click();
    await nextTick();
    // 倍投默认 1，cost 应显示 2.00 元
    const costEl = host.querySelector(".cost-display") as HTMLElement;
    expect(costEl).toBeTruthy();
    expect(costEl.textContent?.trim()).toBe("2.00 元");
  });

  it("cost 自动计算：倍投 5 倍 = 10.00 元", async () => {
    await mount([]);
    const cta = Array.from(host.querySelectorAll("button")).find((b) =>
      b.textContent?.includes("去选一注") || b.textContent?.includes("添加号码")
    ) as HTMLButtonElement | undefined;
    cta!.click();
    await nextTick();
    const randomBtn = Array.from(host.querySelectorAll("button")).find((b) =>
      b.textContent?.includes("机选一注")
    ) as HTMLButtonElement;
    randomBtn.click();
    await nextTick();
    // 倍投改 5
    const multInput = host.querySelector('input[type="number"]') as HTMLInputElement;
    const nativeSetter = Object.getOwnPropertyDescriptor(
      HTMLInputElement.prototype,
      "value"
    )!.set!;
    nativeSetter.call(multInput, "5");
    multInput.dispatchEvent(new Event("input", { bubbles: true }));
    await nextTick();
    const costEl = host.querySelector(".cost-display") as HTMLElement;
    expect(costEl.textContent?.trim()).toBe("10.00 元");
  });

  it("「保存并继续」按钮：保存后不关 modal，清空号码盘", async () => {
    await mount([]);
    const cta = Array.from(host.querySelectorAll("button")).find((b) =>
      b.textContent?.includes("去选一注") || b.textContent?.includes("添加号码")
    ) as HTMLButtonElement | undefined;
    cta!.click();
    await nextTick();
    // 机选一注
    const randomBtn = Array.from(host.querySelectorAll("button")).find((b) =>
      b.textContent?.includes("机选一注")
    ) as HTMLButtonElement;
    randomBtn.click();
    await nextTick();
    // 点击「保存并继续」
    const continueBtn = Array.from(host.querySelectorAll("button")).find((b) =>
      b.textContent?.includes("保存并继续")
    ) as HTMLButtonElement;
    expect(continueBtn).toBeTruthy();
    continueBtn!.click();
    await new Promise((r) => setTimeout(r, 50));
    await nextTick();
    // modal 仍打开
    expect(host.querySelector(".modal")).not.toBeNull();
    // 号码盘已清空（机选按钮还在，但选中的号码 button.selected 应为 0）
    const selectedNums = host.querySelectorAll(".num-btn.selected");
    expect(selectedNums.length).toBe(0);
  });

  it("「确认选号」按钮已删除（不再存在）", async () => {
    await mount([]);
    const cta = Array.from(host.querySelectorAll("button")).find((b) =>
      b.textContent?.includes("去选一注") || b.textContent?.includes("添加号码")
    ) as HTMLButtonElement | undefined;
    cta!.click();
    await nextTick();
    const confirmBtn = Array.from(host.querySelectorAll("button")).find((b) =>
      b.textContent?.includes("确认选号")
    );
    expect(confirmBtn).toBeUndefined();
  });

  it("numbers_json 输入框已隐藏（type=hidden）", async () => {
    await mount([]);
    const cta = Array.from(host.querySelectorAll("button")).find((b) =>
      b.textContent?.includes("去选一注") || b.textContent?.includes("添加号码")
    ) as HTMLButtonElement | undefined;
    cta!.click();
    await nextTick();
    // numbers_json 是 hidden input，不应有 type="text" 的 numbers_json 输入框
    const hiddenJson = host.querySelector('input[type="hidden"]') as HTMLInputElement;
    expect(hiddenJson).toBeTruthy();
    // 不应有可见的 numbers_json 输入框（label 含「号码 JSON」）
    const visibleJsonLabel = Array.from(host.querySelectorAll(".field-label")).find((el) =>
      el.textContent?.includes("号码 JSON")
    );
    expect(visibleJsonLabel).toBeUndefined();
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
