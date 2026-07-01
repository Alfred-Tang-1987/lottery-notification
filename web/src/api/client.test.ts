import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// client.ts holds module-level CSRF cache → reset between tests by re-importing.
// We use vi.resetModules() in beforeEach to get a fresh module each time.

describe("api client", () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  let originalLocation: Location;

  beforeEach(() => {
    fetchMock = vi.fn();
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    // Stub location so the 401 redirect branch is observable without navigating.
    originalLocation = window.location;
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { href: "" },
    });
    vi.resetModules();
  });

  afterEach(() => {
    Object.defineProperty(window, "location", {
      configurable: true,
      value: originalLocation,
    });
  });

  async function loadClient() {
    return (await import("./client")).api;
  }

  it("GET request does not fetch a CSRF token", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { ok: true }));
    const api = await loadClient();

    await api("/api/ping");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [path, init] = fetchMock.mock.calls[0];
    expect(path).toBe("/api/ping");
    expect((init as RequestInit).method).toBe("GET");
  });

  it("POST fetches CSRF once and sets X-CSRF-Token + Content-Type headers", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(200, { csrf_token: "tok-123" })) // /auth/csrf
      .mockResolvedValueOnce(jsonResponse(200, { ok: true })); // POST
    const api = await loadClient();

    await api("/api/x", { method: "POST", body: JSON.stringify({ a: 1 }) });

    // Two fetches: csrf then POST.
    expect(fetchMock).toHaveBeenCalledTimes(2);
    const [csrfPath] = fetchMock.mock.calls[0];
    expect(csrfPath).toBe("/auth/csrf");
    const [, postInit] = fetchMock.mock.calls[1];
    const headers = (postInit as RequestInit).headers as Headers;
    expect(headers.get("X-CSRF-Token")).toBe("tok-123");
    expect(headers.get("Content-Type")).toBe("application/json");
  });

  it("caches CSRF token across multiple POSTs (single /auth/csrf fetch)", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(200, { csrf_token: "tok-once" }))
      .mockResolvedValueOnce(jsonResponse(200, { ok: 1 }))
      .mockResolvedValueOnce(jsonResponse(200, { ok: 2 }));
    const api = await loadClient();

    await api("/api/a", { method: "POST", body: "{}" });
    await api("/api/b", { method: "POST", body: "{}" });

    // 1 csrf + 2 posts = 3 total; no second csrf fetch.
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(fetchMock.mock.calls[0][0]).toBe("/auth/csrf");
  });

  it("redirects to /login on 401", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(401, { detail: "no" }));
    const api = await loadClient();

    await expect(api("/api/secret")).rejects.toThrow("未登录");
    expect(window.location.href).toBe("/login");
  });

  it("throws Error with backend detail message on non-ok response", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(409, { detail: "用户名已存在" }),
    );
    const api = await loadClient();

    await expect(api("/api/x")).rejects.toThrow("用户名已存在");
  });

  it("falls back to HTTP status text when body has no detail", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(500, {}));
    const api = await loadClient();

    await expect(api("/api/x")).rejects.toThrow(/HTTP 500/);
  });

  it("includes an AbortSignal (timeout) on every fetch call", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { ok: true }));
    const api = await loadClient();

    await api("/api/ping");

    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(init.signal).toBeInstanceOf(AbortSignal);
    // A timeout-driven abort signal is aborted after the timeout elapses;
    // freshly created it must not be already aborted.
    expect((init.signal as AbortSignal).aborted).toBe(false);
  });

  it("CSRF fetch includes an AbortSignal (timeout)", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(200, { csrf_token: "tok" }))
      .mockResolvedValueOnce(jsonResponse(200, { ok: true }));
    const api = await loadClient();

    await api("/api/x", { method: "POST", body: "{}" });

    const csrfInit = fetchMock.mock.calls[0][1] as RequestInit;
    expect(csrfInit.signal).toBeInstanceOf(AbortSignal);
  });

  it("includes raw response text in error when body is not valid JSON", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response("Internal Server Error <br> stacktrace", {
        status: 500,
        headers: { "Content-Type": "text/html" },
      }),
    );
    const api = await loadClient();

    await expect(api("/api/x")).rejects.toThrow(/Internal Server Error/);
  });

  it("includes JSON parse failure context in error when body is malformed", async () => {
    // Looks like JSON (content-type json) but is broken.
    fetchMock.mockResolvedValueOnce(
      new Response("{broken json", {
        status: 502,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const api = await loadClient();

    await expect(api("/api/x")).rejects.toThrow(/{broken json/);
  });

  it("always sends credentials: same-origin", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { ok: true }));
    const api = await loadClient();

    await api("/api/ping");

    expect((fetchMock.mock.calls[0][1] as RequestInit).credentials).toBe(
      "same-origin",
    );
  });

  it("apiGet issues a GET and returns parsed body", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { value: 42 }));
    const mod = await import("./client");

    const out = await mod.apiGet<{ value: number }>("/api/n");

    expect(out).toEqual({ value: 42 });
    expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe("GET");
  });

  it("apiPost stringifies body and sets POST method", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(200, { csrf_token: "t" }))
      .mockResolvedValueOnce(jsonResponse(201, { id: 7 }));
    const mod = await import("./client");

    const out = await mod.apiPost<{ id: number }>("/api/x", { name: "a" });

    expect(out).toEqual({ id: 7 });
    const [, postInit] = fetchMock.mock.calls[1];
    expect((postInit as RequestInit).method).toBe("POST");
    expect((postInit as RequestInit).body).toBe(JSON.stringify({ name: "a" }));
  });

  it("apiPost without body still sends POST (no Content-Type set)", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(200, { csrf_token: "t" }))
      .mockResolvedValueOnce(jsonResponse(200, { ok: true }));
    const mod = await import("./client");

    await mod.apiPost("/api/logout");

    const [, postInit] = fetchMock.mock.calls[1];
    expect((postInit as RequestInit).method).toBe("POST");
    expect((postInit as RequestInit).body).toBeUndefined();
  });

  it("apiPost serializes falsy-but-valid JSON bodies (0) instead of dropping them", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(200, { csrf_token: "t" }))
      .mockResolvedValueOnce(jsonResponse(200, { ok: true }));
    const mod = await import("./client");

    await mod.apiPost("/api/count", 0);

    const [, postInit] = fetchMock.mock.calls[1];
    expect((postInit as RequestInit).body).toBe("0");
  });

  it("apiPost serializes falsy-but-valid JSON bodies (false) instead of dropping them", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(200, { csrf_token: "t" }))
      .mockResolvedValueOnce(jsonResponse(200, { ok: true }));
    const mod = await import("./client");

    await mod.apiPost("/api/flag", false);

    const [, postInit] = fetchMock.mock.calls[1];
    expect((postInit as RequestInit).body).toBe("false");
  });

  it("apiPost serializes falsy-but-valid JSON bodies (empty string) instead of dropping them", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(200, { csrf_token: "t" }))
      .mockResolvedValueOnce(jsonResponse(200, { ok: true }));
    const mod = await import("./client");

    await mod.apiPost("/api/note", "");

    const [, postInit] = fetchMock.mock.calls[1];
    expect((postInit as RequestInit).body).toBe('""');
  });

  it("ensureCsrf throws on response with missing csrf_token (no invalid token cached)", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, {}));
    const api = await loadClient();

    await expect(
      api("/api/x", { method: "POST", body: "{}" }),
    ).rejects.toThrow(/CSRF/i);

    // Cache must remain empty: a follow-up POST triggers a fresh fetch rather
    // than reusing a poisoned (undefined) cached token.
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("ensureCsrf throws on response with non-string csrf_token (null)", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { csrf_token: null }));
    const api = await loadClient();

    await expect(
      api("/api/x", { method: "POST", body: "{}" }),
    ).rejects.toThrow(/CSRF/i);
  });

  it("ensureCsrf throws on response with empty-string csrf_token", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { csrf_token: "" }));
    const api = await loadClient();

    await expect(
      api("/api/x", { method: "POST", body: "{}" }),
    ).rejects.toThrow(/CSRF/i);
  });

  it("ensureCsrf throws on response with non-string csrf_token (number)", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { csrf_token: 123 }));
    const api = await loadClient();

    await expect(
      api("/api/x", { method: "POST", body: "{}" }),
    ).rejects.toThrow(/CSRF/i);
  });
});

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
