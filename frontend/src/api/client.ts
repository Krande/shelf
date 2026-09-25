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
 * Every 401 means the same thing here — no session, or one that has
 * expired (see the backend's `auth/deps.py`) — so `apiFetch` reports it
 * to `auth/expiry` as well as throwing. That is what turns a page full
 * of "Unauthorized" errors into a trip to /login and back: see
 * `useAuth` and ProtectedRoute. Callers still get the ApiError and can
 * render it, for the moment before the redirect takes over.
 */

import { markSessionExpired } from "@/auth/expiry";

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
    if (res.status === 401) markSessionExpired();
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
