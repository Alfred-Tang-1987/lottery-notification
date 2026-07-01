import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createApp, nextTick } from "vue";
import State from "./State.vue";

describe("State.vue", () => {
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
  });

  async function mount(props: Record<string, unknown>) {
    app = createApp(State, props);
    app.mount(host);
    await nextTick();
    return host;
  }

  it("renders a loading spinner with aria-label", async () => {
    await mount({ type: "loading" });

    const spinner = host.querySelector('[aria-label="加载中"]');
    expect(spinner).not.toBeNull();
    expect(spinner?.classList.contains("spinner")).toBe(true);
  });

  it("renders empty title and CTA button", async () => {
    await mount({ type: "empty", title: "号码池为空", cta: "去选一注" });

    expect(host.textContent).toContain("号码池为空");
    const button = host.querySelector("button");
    expect(button?.textContent).toContain("去选一注");
  });

  it("emits action when empty CTA is clicked", async () => {
    const onAction = vi.fn();
    await mount({ type: "empty", cta: "去选一注", onAction });

    host.querySelector("button")?.click();
    await nextTick();

    expect(onAction).toHaveBeenCalledTimes(1);
  });

  it("does not render a button when empty has no cta", async () => {
    await mount({ type: "empty" });

    expect(host.querySelector("button")).toBeNull();
  });

  it("renders error retry button", async () => {
    await mount({ type: "error" });

    expect(host.textContent).toContain("加载失败");
    const button = host.querySelector("button");
    expect(button?.textContent).toContain("重试");
  });

  it("emits action when error retry is clicked", async () => {
    const onAction = vi.fn();
    await mount({ type: "error", onAction });

    host.querySelector("button")?.click();
    await nextTick();

    expect(onAction).toHaveBeenCalledTimes(1);
  });

  it("uses aria-live assertive for error", async () => {
    await mount({ type: "error" });

    const wrapper = host.querySelector('[role="status"]');
    expect(wrapper?.getAttribute("aria-live")).toBe("assertive");
  });

  it("uses aria-live polite for non-error states", async () => {
    await mount({ type: "loading" });

    const wrapper = host.querySelector('[role="status"]');
    expect(wrapper?.getAttribute("aria-live")).toBe("polite");
  });
});
