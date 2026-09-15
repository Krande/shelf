/**
 * Account switching.
 *
 * One browser session can hold several identities — useful when the same
 * person has more than one OIDC account. The linked set lives in the
 * session cookie and is reported by /api/me; these are the operations
 * that change it.
 */

import { apiFetch } from "./client";
import { hardNavigate } from "@/lib/navigation";

/**
 * Start a code flow that adds an identity to the current session.
 *
 * Full-page redirect, like `startLogin`: the OAuth dance has to happen
 * in the top-level browsing context, not inside a fetch.
 */
export function startLinkAccount(provider: string): void {
  hardNavigate(`/auth/link/${encodeURIComponent(provider)}`);
}

export async function switchAccount(userId: string): Promise<void> {
  await apiFetch<{ user_id: string }>("/auth/switch", {
    method: "POST",
    body: JSON.stringify({ user_id: userId }),
  });
}

export async function unlinkAccount(
  userId: string,
): Promise<{ active_user_id: string | null }> {
  return apiFetch<{ active_user_id: string | null }>("/auth/accounts/unlink", {
    method: "POST",
    body: JSON.stringify({ user_id: userId }),
  });
}
