/**
 * Plan 06 / T3：API client（fetch + cookie + CSRF double-submit + 错误）。
 *
 * Spec §4.3：httpOnly cookie 认证（浏览器自动带 session cookie）；
 * SPA 读不到 httpOnly cookie，故经 GET /auth/csrf 取 CSRF token，
 * 非 GET 请求带 X-CSRF-Token header 做 double-submit（与 cookie csrf_token 比对）。
 *
 * 后端契约（Plan 05）：错误响应体形如 {"detail": "..."}（FastAPI HTTPException）。
 *
 * 静默失败防护：
 * - 每个 fetch 带 AbortSignal.timeout(DEFAULT_TIMEOUT_MS)，避免后端挂起导致 UI 永久 pending。
 * - 非 2xx 响应体先读 text 再尝试 JSON.parse，把原始响应文本保留进抛出的 Error，
 *   不用 `.catch(() => ({}))` 丢弃原始错误上下文（防运维/排障时丢失线索）。
 */
const CSRF_HEADER = "X-CSRF-Token";

/** 单次请求挂起上限（ms）。家庭 NAS 局域网足够宽裕，超过即视为挂起并 reject。 */
export const DEFAULT_TIMEOUT_MS = 15_000;

let _csrf: string | null = null;

async function ensureCsrf(): Promise<string> {
  if (_csrf) return _csrf;
  const r = await fetch("/auth/csrf", {
    credentials: "same-origin",
    signal: AbortSignal.timeout(DEFAULT_TIMEOUT_MS),
  });
  if (!r.ok) {
    throw new Error(`获取 CSRF token 失败 (HTTP ${r.status})`);
  }
  const data = (await r.json()) as { csrf_token?: unknown };
  // Validate csrf_token before caching: a missing/non-string/empty value would
  // otherwise be cached as an invalid token and poison every subsequent
  // non-GET request with persistent 403s.
  if (typeof data.csrf_token !== "string" || data.csrf_token.length === 0) {
    throw new Error("CSRF token 响应无效：缺失或非字符串");
  }
  _csrf = data.csrf_token;
  return _csrf;
}

/**
 * 带 HTTP status 的 API 错误，供调用方区分认证/授权错误与瞬态故障。
 */
export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

/**
 * 解析非 2xx 响应体，返回携带原始上下文的错误消息字符串。
 *
 * 策略：先 r.text()（永不抛错）再尝试 JSON.parse；解析失败或无 detail 时，
 * 把原始响应文本（截断防巨量日志）附进错误消息，不静默归为 `{}`。
 */
async function errorMessageFromResponse(r: Response): Promise<string> {
  const status = r.status;
  let raw = "";
  try {
    raw = (await r.text()).trim();
  } catch {
    // body 读取本身失败（极少见，如流已被消费）—— raw 保持空。
  }

  if (raw) {
    try {
      const parsed = JSON.parse(raw) as { detail?: unknown };
      if (typeof parsed.detail === "string" && parsed.detail) {
        return parsed.detail;
      }
    } catch {
      // 非 JSON 响应体（HTML 错误页、纯文本等）—— 下方附 raw。
    }
  }

  const snippet = raw ? `: ${raw.slice(0, 200)}` : "";
  return `HTTP ${status}${snippet}`;
}

export async function api<T = unknown>(
  path: string,
  opts: RequestInit & { skipAuthRedirect?: boolean } = {},
): Promise<T> {
  const method = (opts.method || "GET").toUpperCase();
  const headers = new Headers(opts.headers);
  // 探测性请求（如 fetchMe）的 401 是合法响应（未登录态），不应触发跳转。
  // 提取后从 opts 中删除，避免传给 fetch（fetch 不认识自定义字段）。
  const skipAuthRedirect = opts.skipAuthRedirect === true;

  if (method !== "GET") {
    const csrf = await ensureCsrf();
    headers.set(CSRF_HEADER, csrf);
    if (opts.body && !headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }
  }

  // 调用方可传自定义 signal；未传则挂超时 signal，防后端挂起导致 UI 永久 pending。
  const signal = opts.signal ?? AbortSignal.timeout(DEFAULT_TIMEOUT_MS);

  const r = await fetch(path, {
    ...opts,
    method,
    headers,
    credentials: "same-origin",
    signal,
  });

  if (r.status === 401) {
    // 受保护资源 401 → 回登录页（spec §12.x：受保护资源 401 统一重定向）。
    // 但探测性请求（fetchMe 在 /login 也无条件调用）的 401 不跳转，否则死循环：
    // /login → mount UserMenu → fetchMe → 401 → 跳 /login → 重新 mount → ...
    if (!skipAuthRedirect) {
      window.location.href = "/login";
    }
    throw new ApiError(401, "未登录");
  }

  if (!r.ok) {
    throw new ApiError(r.status, await errorMessageFromResponse(r));
  }

  return (await r.json()) as T;
}

export const apiGet = <T = unknown>(
  p: string,
  opts?: RequestInit & { skipAuthRedirect?: boolean },
) => api<T>(p, opts);

export const apiPost = <T = unknown>(p: string, body?: unknown) =>
  api<T>(p, {
    method: "POST",
    // Use explicit undefined check (not truthiness): valid falsy JSON bodies
    // like 0 / false / "" must be serialized, not silently dropped.
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });

export const apiPut = <T = unknown>(p: string, body?: unknown) =>
  api<T>(p, {
    method: "PUT",
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });

export const apiPatch = <T = unknown>(p: string, body?: unknown) =>
  api<T>(p, {
    method: "PATCH",
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });

export const apiDelete = <T = unknown>(p: string) => api<T>(p, { method: "DELETE" });
