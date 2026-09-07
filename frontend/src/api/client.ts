/**
 * Thin fetch wrapper for the Shelf API.
 *
 * The backend authenticates via an httpOnly session cookie issued by
 * /auth/callback/{provider}. The browser sends it automatically when
 * the SPA is served from the same origin (production) or when Vite's
 * dev-server proxy forwards to FastAPI on :8000 (dev). Both paths just
 * need `credentials: "include"` to keep the cookie attached on
 * cross-fetch.
 *
 * On 401, callers can decide whether to redirect to login or to surface
 * the error in the UI. The default `apiFetch` throws ApiError; pages
 * that gate on auth state listen for status === 401 and bounce to the
 * configured OIDC provider via /auth/login/{provider}.
 */

export class ApiError extends Error {
  status: number;
  body: unknown;
  constructor(status: number, message: string, body: unknown) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

export async function apiFetch<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const res = await fetch(path, {
    credentials: "include",
    headers: {
      Accept: "application/json",
      ...(init.body && !(init.body instanceof FormData)
        ? { "Content-Type": "application/json" }
        : {}),
      ...init.headers,
    },
    ...init,
  });

  if (!res.ok) {
    let body: unknown = null;
    try {
      body = await res.json();
    } catch {
      // non-JSON error bodies are fine
    }
    throw new ApiError(res.status, res.statusText, body);
  }

  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export function loginUrl(provider: string): string {
  return `/auth/login/${encodeURIComponent(provider)}`;
}

export async function logout(): Promise<void> {
  await apiFetch<{ status: string }>("/auth/logout", { method: "POST" });
}
