/**
 * Browser-side helpers for the cookie-session auth model. The backend
 * does the OIDC handshake; this file just bounces the user there and
 * gates the UI on the result of /api/me.
 */

import { useQuery } from "@tanstack/react-query";
import { ApiError } from "../api/client";
import { fetchMe, type Me } from "../api/me";

export function startLogin(provider: string): void {
  // Full-page redirect — authlib needs the OAuth dance to happen at the
  // top-level browser context, not via fetch.
  window.location.assign(`/auth/login/${encodeURIComponent(provider)}`);
}

export type AuthState =
  | { status: "loading" }
  | { status: "anonymous" }
  | { status: "authenticated"; user: Me };

export function useAuth(): AuthState {
  const q = useQuery<Me, ApiError>({
    queryKey: ["me"],
    queryFn: fetchMe,
    retry: (_attempt, err) => err.status >= 500,
    staleTime: 60_000,
  });

  if (q.isLoading) return { status: "loading" };
  if (q.error instanceof ApiError && q.error.status === 401) {
    return { status: "anonymous" };
  }
  if (q.data) return { status: "authenticated", user: q.data };
  return { status: "loading" };
}
