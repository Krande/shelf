/**
 * Which provider "switch user" should send you to.
 *
 * Both the header popover and Settings → Account need the same answer,
 * so the fetch and the preference rule live here once.
 */

import { useEffect, useState } from "react";
import type { Me } from "@/api/me";
import { fetchProviders } from "@/api/providers";

export interface LinkProviders {
  providers: string[];
  devLogin: boolean;
  /**
   * The provider a "switch user" click should jump straight to.
   *
   * Prefer the one the active account actually signed in with, so someone
   * on Entra lands on Entra's account picker rather than a menu asking
   * which provider they meant. Falls back to the only configured provider
   * when the account has no identity yet (a dev-login user on an instance
   * that does have OIDC). Undefined means we can't tell — several
   * providers and no signal — and the caller has to offer a choice.
   */
  preferredProvider: string | undefined;
  /**
   * Providers that still need their own "Add …" affordance. The preferred
   * one is already reachable through "switch user", so listing it twice
   * is noise.
   */
  otherProviders: string[];
}

/**
 * @param enabled Fetch only once it matters — the header renders its
 * switcher on every page, and nobody needs the provider list until the
 * popover opens.
 */
export function useLinkProviders(user: Me, enabled = true): LinkProviders {
  const [providers, setProviders] = useState<string[]>([]);
  const [devLogin, setDevLogin] = useState(false);

  useEffect(() => {
    if (!enabled || providers.length > 0 || devLogin) return;
    fetchProviders()
      .then((r) => {
        setProviders(r.providers);
        setDevLogin(r.dev_login);
      })
      .catch(() => {
        /* switching between already-linked accounts still works */
      });
  }, [enabled, providers.length, devLogin]);

  const activeAccount = user.accounts.find((a) => a.id === user.id);
  const preferredProvider =
    activeAccount?.idps.find((idp) => providers.includes(idp)) ??
    (providers.length === 1 ? providers[0] : undefined);

  return {
    providers,
    devLogin,
    preferredProvider,
    otherProviders: providers.filter((p) => p !== preferredProvider),
  };
}
