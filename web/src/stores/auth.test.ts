import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { useAuthStore } from "./auth";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("auth store fetchMe", () => {
  let originalLocation: Location;

  beforeEach(() => {
    setActivePinia(createPinia());
    originalLocation = window.location;
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { href: "" },
    });
    globalThis.fetch = vi.fn();
  });

  afterEach(() => {
    Object.defineProperty(window, "location", {
      configurable: true,
      value: originalLocation,
    });
  });

  it("sets user to null on 401 and marks initialized", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      jsonResponse(401, { detail: "未登录" }),
    );
    const auth = useAuthStore();

    await auth.fetchMe();

    expect(auth.user).toBeNull();
    expect(auth.initialized).toBe(true);
    expect(auth.loading).toBe(false);
    expect(auth.isLoggedIn).toBe(false);
    expect(window.location.href).toBe("/login");
  });

  it("sets user to null on 403 and marks initialized", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      jsonResponse(403, { detail: "禁止访问" }),
    );
    const auth = useAuthStore();

    await auth.fetchMe();

    expect(auth.user).toBeNull();
    expect(auth.initialized).toBe(true);
    expect(auth.loading).toBe(false);
    expect(auth.isLoggedIn).toBe(false);
  });

  it("preserves existing user and rethrows on 5xx server error", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      jsonResponse(500, {}),
    );
    const auth = useAuthStore();
    const existingUser = { id: 1, username: "alice", role: "user" as const };
    auth.user = existingUser;

    await expect(auth.fetchMe()).rejects.toThrow(/HTTP 500/);

    expect(auth.user).toStrictEqual(existingUser);
    expect(auth.initialized).toBe(true);
    expect(auth.loading).toBe(false);
  });

  it("preserves existing user and rethrows on network failure", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockRejectedValueOnce(
      new TypeError("Failed to fetch"),
    );
    const auth = useAuthStore();
    const existingUser = { id: 2, username: "bob", role: "admin" as const };
    auth.user = existingUser;

    await expect(auth.fetchMe()).rejects.toThrow("Failed to fetch");

    expect(auth.user).toStrictEqual(existingUser);
    expect(auth.initialized).toBe(true);
    expect(auth.loading).toBe(false);
  });
});
