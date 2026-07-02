import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createApp, nextTick } from "vue";
import NumberPad from "./NumberPad.vue";

describe("NumberPad.vue", () => {
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
    app = createApp(NumberPad, props);
    app.mount(host);
    await nextTick();
    return host;
  }

  it("renders pool buttons for ssq front (1-33, 33 buttons)", async () => {
    await mount({ numbers: [], zone: "front", lotteryCode: "ssq" });
    const buttons = host.querySelectorAll(".num-btn");
    expect(buttons.length).toBe(33);
    expect(buttons[0]?.textContent?.trim()).toBe("01");
    expect(buttons[32]?.textContent?.trim()).toBe("33");
  });

  it("renders pool buttons for ssq back (1-16, 16 buttons)", async () => {
    await mount({ numbers: [], zone: "back", lotteryCode: "ssq" });
    const buttons = host.querySelectorAll(".num-btn");
    expect(buttons.length).toBe(16);
  });

  it("renders pool for fc3d positional (0-9, 10 buttons)", async () => {
    await mount({ numbers: [], zone: "front", lotteryCode: "fc3d" });
    const buttons = host.querySelectorAll(".num-btn");
    expect(buttons.length).toBe(10);
    expect(buttons[0]?.textContent?.trim()).toBe("00");
    expect(buttons[9]?.textContent?.trim()).toBe("09");
  });

  it("marks selected numbers with .selected class + aria-pressed", async () => {
    await mount({ numbers: [1, 7], zone: "front", lotteryCode: "ssq" });
    const buttons = host.querySelectorAll(".num-btn");
    // button[0] = "01" should be selected
    expect(buttons[0]?.classList.contains("selected")).toBe(true);
    expect(buttons[0]?.getAttribute("aria-pressed")).toBe("true");
    // button[6] = "07" should be selected
    expect(buttons[6]?.classList.contains("selected")).toBe(true);
    // button[1] = "02" should not be selected
    expect(buttons[1]?.classList.contains("selected")).toBe(false);
    expect(buttons[1]?.getAttribute("aria-pressed")).toBe("false");
  });

  it("emits update:numbers with sorted array when clicking unselected number", async () => {
    const onUpdate = vi.fn();
    await mount({
      numbers: [3],
      zone: "front",
      lotteryCode: "ssq",
      "onUpdate:numbers": onUpdate,
    });
    // Click "01" (first button, index 0)
    const buttons = host.querySelectorAll(".num-btn");
    buttons[0]?.dispatchEvent(new Event("click"));
    await nextTick();
    expect(onUpdate).toHaveBeenCalled();
    const emitted = onUpdate.mock.calls[0][0] as number[];
    expect(emitted).toEqual([1, 3]); // sorted
  });

  it("emits update:numbers removing number when clicking selected number", async () => {
    const onUpdate = vi.fn();
    await mount({
      numbers: [1, 3],
      zone: "front",
      lotteryCode: "ssq",
      "onUpdate:numbers": onUpdate,
    });
    // Click "01" (first button, index 0, currently selected)
    const buttons = host.querySelectorAll(".num-btn");
    buttons[0]?.dispatchEvent(new Event("click"));
    await nextTick();
    const emitted = onUpdate.mock.calls[0][0] as number[];
    expect(emitted).toEqual([3]);
  });

  it("renders qxc back zone with 0-14 (15 buttons)", async () => {
    await mount({ numbers: [], zone: "back", lotteryCode: "qxc" });
    const buttons = host.querySelectorAll(".num-btn");
    expect(buttons.length).toBe(15);
    expect(buttons[0]?.textContent?.trim()).toBe("00");
    expect(buttons[14]?.textContent?.trim()).toBe("14");
  });
});
