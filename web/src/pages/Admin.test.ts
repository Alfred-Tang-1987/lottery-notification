import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createApp, nextTick } from "vue";
import Admin from "../pages/Admin.vue";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

// URL+method 路由的 fetch mock（无顺序依赖）。
// Admin.vue onMounted 用 Promise.all 并发多个 GET，mockResolvedValueOnce 顺序 stub 会错位；
// 改用按 URL 路由，无论 fetch 次序/并发都返回正确响应。
function stubApi(overrides: Record<string, unknown> = {}) {
  return vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const u = String(url);
    if (u === "/auth/csrf") return jsonResponse(200, { csrf_token: "tok" });

    // 写入响应
    if (method === "POST" && u === "/admin/invite-codes")
      return jsonResponse(201, overrides.newInvite ?? {
        code: "654321", created_by: 1, used_by: null, used_at: null,
        expires_at: "2026-08-05T00:00:00", attempts: 0, locked_at: null,
        created_at: "2026-07-06T00:00:00",
      });
    if (method === "POST" && u === "/admin/smtp-test")
      return jsonResponse(200, overrides.smtpTest ?? { ok: true, message: "测试邮件已发送" });
    if (method === "PATCH" && u.startsWith("/admin/lotteries/"))
      return jsonResponse(200, overrides.toggle ?? { code: "ssq", enabled: true });

    // GET 响应
    if (u === "/admin/users") return jsonResponse(200, overrides.users ?? []);
    if (u === "/admin/health")
      return jsonResponse(200, overrides.health ?? { sources: [] });
    if (u === "/admin/smtp-config")
      return jsonResponse(200, overrides.smtp ?? {
        smtp_host: "smtp.qq.com", smtp_port: 465, smtp_encryption: "ssl",
        smtp_user: "u", smtp_from: "u@q.com", configured: true,
      });
    if (u === "/admin/invite-codes")
      return jsonResponse(200, overrides.invites ?? []);
    if (u === "/admin/lotteries")
      return jsonResponse(200, overrides.lotteries ?? [
        { code: "ssq", name: "双色球", category: "welfare", enabled: true, draw_days: [1, 3, 6] },
        { code: "dlt", name: "大乐透", category: "sport", enabled: true, draw_days: [0, 2, 5] },
        { code: "qlc", name: "七乐彩", category: "welfare", enabled: true, draw_days: [0, 2, 4] },
        { code: "fc3d", name: "福彩3D", category: "welfare", enabled: true, draw_days: [0, 1, 2, 3, 4, 5, 6] },
        { code: "qxc", name: "七星彩", category: "sport", enabled: true, draw_days: [1, 4, 6] },
        { code: "pl3", name: "排列3", category: "sport", enabled: true, draw_days: [0, 1, 2, 3, 4, 5, 6] },
        { code: "pl5", name: "排列5", category: "sport", enabled: true, draw_days: [0, 1, 2, 3, 4, 5, 6] },
      ]);
    if (u.startsWith("/admin/audit-logs"))
      return jsonResponse(200, overrides.audit ?? { total: 0, page: 1, page_size: 20, items: [] });
    if (u.startsWith("/admin/push-logs"))
      return jsonResponse(200, overrides.pushLogs ?? {
        total: 0, page: 1, page_size: 20, items: [],
      });
    throw new Error(`Unexpected fetch: ${method} ${url}`);
  });
}

describe("Admin.vue (T6f)", () => {
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
    app = createApp(Admin);
    app.mount(host);
    await nextTick();
    await new Promise((r) => setTimeout(r, 0));
    await nextTick();
    return host;
  }

  function findCall(urlPrefix: string, method: string) {
    return fetchMock.mock.calls.find(
      (c) => String(c[0]).startsWith(urlPrefix) &&
        (c[1] as RequestInit | undefined)?.method === method,
    );
  }

  function lastCall(urlPrefix: string, method?: string) {
    const calls = fetchMock.mock.calls.filter(
      (c) => String(c[0]).startsWith(urlPrefix) &&
        (method ? (c[1] as RequestInit | undefined)?.method === method : true),
    );
    return calls[calls.length - 1];
  }

  it("renders SMTP config card with test-send button", async () => {
    await mount();
    expect(host.textContent).toContain("SMTP");
    const testBtn = host.querySelector(".smtp-test-btn") as HTMLButtonElement;
    expect(testBtn).toBeTruthy();
  });

  it("renders invite-code create button and lists invite codes", async () => {
    await mount({
      invites: [
        { code: "123456", created_by: 1, used_by: null, used_at: null,
          expires_at: "2026-08-05T00:00:00", attempts: 0, locked_at: null,
          created_at: "2026-07-06T00:00:00" },
      ],
    });
    expect(host.textContent).toContain("123456");
    const createBtn = host.querySelector(".invite-create-btn") as HTMLButtonElement;
    expect(createBtn).toBeTruthy();
  });

  it("creates invite code via POST /admin/invite-codes on button click", async () => {
    await mount();
    const createBtn = host.querySelector(".invite-create-btn") as HTMLButtonElement;
    createBtn.click();
    await nextTick();
    await new Promise((r) => setTimeout(r, 0));

    const call = findCall("/admin/invite-codes", "POST");
    expect(call).toBeDefined();
  });

  it("renders lottery toggle controls for 7 lotteries", async () => {
    await mount();
    const toggles = host.querySelectorAll(".lottery-toggle");
    expect(toggles.length).toBe(7);
  });

  it("sends PATCH /admin/lotteries/{code}/enabled on toggle change", async () => {
    await mount();
    const toggles = host.querySelectorAll(".lottery-toggle") as NodeListOf<HTMLInputElement>;
    const ssq = Array.from(toggles).find((t) => t.dataset.lottery === "ssq") as HTMLInputElement;
    ssq.checked = true;
    ssq.dispatchEvent(new Event("change"));
    await nextTick();
    await new Promise((r) => setTimeout(r, 0));

    const call = findCall("/admin/lotteries/ssq/enabled", "PATCH");
    expect(call).toBeDefined();
    expect(String(call![0])).toContain("enabled=true");
  });

  it("renders push-log 6-dim filter controls", async () => {
    await mount();
    // 6 维: 日期(从/到) / 用户 / 彩种 / 渠道 / 类型 / 状态
    expect(host.querySelector(".filter-date-from")).toBeTruthy();
    expect(host.querySelector(".filter-date-to")).toBeTruthy();
    expect(host.querySelector(".filter-user-id")).toBeTruthy();
    expect(host.querySelector(".filter-lottery")).toBeTruthy();
    expect(host.querySelector(".filter-channel")).toBeTruthy();
    expect(host.querySelector(".filter-type")).toBeTruthy();
    expect(host.querySelector(".filter-status")).toBeTruthy();
  });

  it("applies push-log filters and sends query params on apply", async () => {
    await mount();
    const statusSel = host.querySelector(".filter-status") as HTMLSelectElement;
    statusSel.value = "failed";
    statusSel.dispatchEvent(new Event("change"));
    const lotterySel = host.querySelector(".filter-lottery") as HTMLSelectElement;
    lotterySel.value = "ssq";
    lotterySel.dispatchEvent(new Event("change"));
    await nextTick();
    const applyBtn = host.querySelector(".filter-apply-btn") as HTMLButtonElement;
    applyBtn.click();
    await nextTick();
    await new Promise((r) => setTimeout(r, 0));

    const call = lastCall("/admin/push-logs", "GET");
    expect(call).toBeDefined();
    const url = String(call![0]);
    expect(url).toContain("status=failed");
    expect(url).toContain("lottery_code=ssq");
  });

  it("paginates push-logs via page param", async () => {
    await mount({
      pushLogs: {
        total: 50, page: 1, page_size: 20,
        items: Array.from({ length: 1 }, (_, i) => ({
          id: i + 1, user_id: 1, username: "u", type: "path_a",
          status: "sent", sent_at: null, error: null,
        })),
      },
    });
    const nextBtn = host.querySelector(".pager-next") as HTMLButtonElement;
    expect(nextBtn).toBeTruthy();
    nextBtn.click();
    await nextTick();
    await new Promise((r) => setTimeout(r, 0));

    const call = lastCall("/admin/push-logs", "GET");
    expect(call).toBeDefined();
    expect(String(call![0])).toContain("page=2");
  });

  it("renders audit log table", async () => {
    await mount({
      audit: {
        total: 1, page: 1, page_size: 20,
        items: [{
          id: 1, admin_id: 1, admin_username: "root", action: "smtp_test",
          target_type: "system", target_id: "smtp",
          old_values: null, new_values: { ok: true },
          created_at: "2026-07-06T00:00:00",
        }],
      },
    });
    expect(host.textContent).toContain("smtp_test");
    expect(host.textContent).toContain("root");
  });

  it("triggers SMTP test send on test button click", async () => {
    await mount();
    const testBtn = host.querySelector(".smtp-test-btn") as HTMLButtonElement;
    testBtn.click();
    await nextTick();
    await new Promise((r) => setTimeout(r, 0));

    const call = findCall("/admin/smtp-test", "POST");
    expect(call).toBeDefined();
  });

  // ─── T6f fix round 1: spec §12.2 row 9 ───

  it("renders lottery toggles bound to real backend enabled state", async () => {
    // dlt disabled from backend stub → checkbox NOT checked
    await mount({
      lotteries: [
        { code: "ssq", name: "双色球", category: "welfare", enabled: true, draw_days: [1, 3, 6] },
        { code: "dlt", name: "大乐透", category: "sport", enabled: false, draw_days: [0, 2, 5] },
      ],
    });
    const toggles = host.querySelectorAll(".lottery-toggle") as NodeListOf<HTMLInputElement>;
    const dlt = Array.from(toggles).find((t) => t.dataset.lottery === "dlt") as HTMLInputElement;
    expect(dlt.checked).toBe(false);
    const ssq = Array.from(toggles).find((t) => t.dataset.lottery === "ssq") as HTMLInputElement;
    expect(ssq.checked).toBe(true);
  });

  it("renders draw_days for each lottery from backend", async () => {
    await mount({
      lotteries: [
        { code: "ssq", name: "双色球", category: "welfare", enabled: true, draw_days: [1, 3, 6] },
      ],
    });
    expect(host.textContent).toContain("开奖日");
    // draw_days 1,3,6 = 二/四/日
    expect(host.textContent).toContain("二");
  });

  it("sends POST /admin/smtp-config with provider+account+auth_code on save", async () => {
    await mount();
    const providerSel = host.querySelector(".smtp-provider") as HTMLSelectElement;
    providerSel.value = "qq";
    providerSel.dispatchEvent(new Event("change"));
    const accountInput = host.querySelector(".smtp-account") as HTMLInputElement;
    accountInput.value = "u@qq.com";
    accountInput.dispatchEvent(new Event("input"));
    const authInput = host.querySelector(".smtp-auth-code") as HTMLInputElement;
    authInput.value = "secret";
    authInput.dispatchEvent(new Event("input"));
    await nextTick();
    const saveBtn = host.querySelector(".smtp-save-btn") as HTMLButtonElement;
    saveBtn.click();
    await nextTick();
    await new Promise((r) => setTimeout(r, 0));

    const call = findCall("/admin/smtp-config", "POST");
    expect(call).toBeDefined();
    const body = JSON.parse((call![1] as RequestInit).body as string);
    expect(body.provider).toBe("qq");
    expect(body.account).toBe("u@qq.com");
    expect(body.auth_code).toBe("secret");
  });

  it("renders user table with note column", async () => {
    await mount({
      users: [
        { id: 1, username: "admin", role: "admin", enabled: true, note: "" },
        { id: 2, username: "alice", role: "user", enabled: true, note: "家庭用户" },
      ],
    });
    expect(host.textContent).toContain("备注");
    expect(host.textContent).toContain("家庭用户");
  });

  it("renders audit log pagination controls", async () => {
    await mount({
      audit: {
        total: 50, page: 1, page_size: 20,
        items: [{
          id: 1, admin_id: 1, admin_username: "root", action: "smtp_test",
          target_type: "system", target_id: "smtp",
          old_values: null, new_values: null, created_at: "2026-07-06T00:00:00",
        }],
      },
    });
    const auditPager = host.querySelector(".audit-pager");
    expect(auditPager).toBeTruthy();
    const prevBtn = host.querySelector(".audit-pager .pager-prev") as HTMLButtonElement;
    const nextBtn = host.querySelector(".audit-pager .pager-next") as HTMLButtonElement;
    expect(prevBtn).toBeTruthy();
    expect(nextBtn).toBeTruthy();
  });

  it("shows global error message independent of users list", async () => {
    // users 非空但 loadAudit 失败 → error 应展示（不应被 users.length>0 吞掉）
    await mount({
      users: [{ id: 1, username: "admin", role: "admin", enabled: true, note: "" }],
      audit: { __throw: new Error("audit load failed") },
    });
    // 顶部错误区或 error state 应包含错误信息
    const errorEl = host.querySelector(".global-error, .error-banner");
    const hasErrorText = !!errorEl || host.textContent?.includes("audit load failed");
    expect(hasErrorText).toBe(true);
  });

  it("default fetch stub throws on unexpected URLs (no silent 200)", async () => {
    fetchMock = stubApi({});
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    await expect(
      fetch("/admin/unknown-endpoint"),
    ).rejects.toThrow(/Unexpected fetch/);
  });
});
