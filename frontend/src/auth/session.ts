/**
 * Browser-side helpers for the cookie-session auth model. The backend
 * does the OIDC handshake; this file just bounces the user there and
 * gates the UI on the result of /api/me.
 */

import { useQuery } from "@tanstack/react-query";
import { ApiError } from "../api/client";
import { fetchMe, type Me } from "../api/me";
import { hardNavigate } from "@/lib/navigation";
import { useSessionExpired } from "./expiry";
import { safeReturnTo } from "./returnTo";

/**
 * Send the browser off to the provider.
 *
 * `next` rides along to the backend, which stashes it across the OAuth
 * handshake and redirects there instead of "/" once the callback lands —
 * so an expired session drops the user back on the page they were
 * reading rather than on the library.
 */
export function startLogin(provider: string, next?: string | null): void {
  const target = safeReturnTo(next);
  const qs = target ? `?next=${encodeURIComponent(target)}` : "";
  // Full-page redirect — authlib needs the OAuth dance to happen at the
  // top-level browser context, not via fetch.
  hardNavigate(`/auth/login/${encodeURIComponent(provider)}${qs}`);
}

export type AuthState =
  | { status: "loading" }
  | { status: "anonymous" }
  | { status: "authenticated"; user: Me };

export function useAuth(): AuthState {
  // A 401 from *any* request means this session is gone, even if the
  // cached /api/me still says otherwise — which it does for up to
  // `staleTime` after the cookie expires. Checked before the query so
  // the UI stops trusting stale data immediately.
  const expired = useSessionExpired();
  const q = useQuery<Me, ApiError>({
    queryKey: ["me"],
    queryFn: fetchMe,
    retry: (_attempt, err) => err.status >= 500,
    staleTime: 60_000,
  });

  if (expired) return { status: "anonymous" };
  if (q.isLoading) return { status: "loading" };
  if (q.error instanceof ApiError && q.error.status === 401) {
    return { status: "anonymous" };
  }
  if (q.data) return { status: "authenticated", user: q.data };
  return { status: "loading" };
}
