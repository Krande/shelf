/**
 * "The session died under us" signal.
 *
 * Every authenticated route gates on /api/me, but that answer is cached:
 * open the library, read for a day, click a document, and the reader
 * mounts on a 60-second-stale "yes" while the cookie behind it has
 * already expired. The page then fires its own requests, every one comes
 * back 401, and the user is left looking at "Failed to load PDF:
 * Unauthorized" with no way forward.
 *
 * So any 401 from any API call — not just /api/me — flips this flag, and
 * `useAuth` reports anonymous the moment it does. ProtectedRoute takes it
 * from there and sends the user to /login with the page they were on in
 * `?next=`, which is what puts them back where they were afterwards.
 *
 * The flag is deliberately one-way: a 401 from this backend only ever
 * means the session cookie is missing, tampered or expired (see
 * `auth/deps.py`), and signing back in is a full page load, which resets
 * this module along with everything else.
 *
 * Kept free of imports so `api/client` can reach it without an import
 * cycle back through the auth layer.
 */

import { useSyncExternalStore } from "react";

let expired = false;
const listeners = new Set<() => void>();

export function markSessionExpired(): void {
  if (expired) return;
  expired = true;
  for (const l of listeners) l();
}

export function isSessionExpired(): boolean {
  return expired;
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Re-renders the caller when the session is found to have expired. */
export function useSessionExpired(): boolean {
  return useSyncExternalStore(subscribe, isSessionExpired, isSessionExpired);
}

/** Test-only: forget a previous expiry so cases don't leak into each other. */
export function resetSessionExpiryForTests(): void {
  expired = false;
  listeners.clear();
}
