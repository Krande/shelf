/**
 * The account popover, shared by the header menu and the "Switch user"
 * button in Settings.
 *
 * Both entry points need identical behaviour — pick a linked identity,
 * add one, sign out — so the logic lives here once and callers supply
 * only the trigger's appearance.
 */

import {
  type CSSProperties,
  type FormEvent,
  type ReactNode,
  useEffect,
  useRef,
  useState,
} from "react";
import { Link } from "react-router";
import { Check, LogOut, Plus, UserPlus, Users } from "lucide-react";
import { startLinkAccount, switchAccount } from "@/api/accounts";
import { apiFetch, logout } from "@/api/client";
import type { Me } from "@/api/me";
import { fetchProviders, providerLabel } from "@/api/providers";
import { goToLogin, reloadAsNewAccount } from "@/lib/navigation";

export default function AccountSwitcher({
  user,
  trigger,
  triggerClassName,
  triggerStyle,
  triggerTitle,
  align = "right",
  showSignOut = true,
  showManageLink = true,
}: {
  user: Me;
  trigger: ReactNode;
  triggerClassName?: string;
  triggerStyle?: CSSProperties;
  triggerTitle?: string;
  align?: "left" | "right";
  showSignOut?: boolean;
  /** "Switch user" link to Settings → Account. Off when the switcher is
   *  already rendered on that page. */
  showManageLink?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [providers, setProviders] = useState<string[]>([]);
  const [devLogin, setDevLogin] = useState(false);
  const [devEmail, setDevEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);

  // Fetched on first open only — the header renders this on every page
  // and nobody needs the provider list until they look.
  useEffect(() => {
    if (!open || providers.length > 0 || devLogin) return;
    fetchProviders()
      .then((r) => {
        setProviders(r.providers);
        setDevLogin(r.dev_login);
      })
      .catch(() => {
        /* switching still works without it */
      });
  }, [open, providers.length, devLogin]);

  useEffect(() => {
    if (!open) return;
    function onPointerDown(e: MouseEvent) {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    }
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  async function onSwitch(userId: string) {
    if (userId === user.id || busy) return;
    setBusy(true);
    setError(null);
    try {
      await switchAccount(userId);
      reloadAsNewAccount();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  }

  async function onDevLink(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await apiFetch("/auth/dev-login", {
        method: "POST",
        body: JSON.stringify({ email: devEmail, link: true }),
      });
      reloadAsNewAccount();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  }

  async function onSignOut() {
    await logout();
    goToLogin();
  }

  /**
   * The provider "Switch user" should jump straight to.
   *
   * Prefer the one the active account actually signed in with, so someone
   * on Entra lands on Entra's account picker rather than a menu asking
   * which provider they meant. Falls back to the only configured provider
   * when the account has no identity yet (a dev-login user on an instance
   * that does have OIDC). Undefined means we can't tell — several
   * providers and no signal — and the link to Settings is the honest
   * answer instead of guessing.
   */
  const activeAccount = user.accounts.find((a) => a.id === user.id);
  const preferredProvider =
    activeAccount?.idps.find((idp) => providers.includes(idp)) ??
    (providers.length === 1 ? providers[0] : undefined);

  // Providers that still need their own "Add …" row. The preferred one is
  // already reachable through "Switch user", so listing it twice is noise.
  const otherProviders = providers.filter((p) => p !== preferredProvider);
  const canAdd = otherProviders.length > 0 || devLogin;

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        title={triggerTitle}
        className={triggerClassName}
        style={triggerStyle}
      >
        {trigger}
      </button>

      {open && (
        <div
          role="menu"
          className={`absolute z-50 mt-1 w-72 rounded-lg border p-1 shadow-lg ${
            align === "right" ? "right-0" : "left-0"
          }`}
          style={{
            backgroundColor: "var(--color-surface)",
            borderColor: "var(--color-border)",
          }}
        >
          {user.accounts.length > 1 && (
            <p
              className="px-2 pb-1 pt-1.5 text-[10px] uppercase tracking-widest"
              style={{ color: "var(--color-text-muted)" }}
            >
              Switch user
            </p>
          )}

          {user.accounts.map((acct) => {
            const active = acct.id === user.id;
            return (
              <button
                key={acct.id}
                role="menuitem"
                type="button"
                disabled={busy}
                onClick={() => onSwitch(acct.id)}
                className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-sm hover:opacity-80 disabled:opacity-50"
              >
                <Check
                  className="h-4 w-4 shrink-0"
                  style={{
                    color: active ? "var(--color-accent)" : "transparent",
                  }}
                />
                <span className="min-w-0 flex-1">
                  <span className="block truncate">{acct.display_name}</span>
                  <span
                    className="block truncate text-xs"
                    style={{ color: "var(--color-text-muted)" }}
                  >
                    {acct.email}
                  </span>
                </span>
              </button>
            );
          })}

          {canAdd && (
            <div
              className="my-1 border-t"
              style={{ borderColor: "var(--color-border)" }}
            />
          )}

          {otherProviders.map((p) => (
            <button
              key={p}
              role="menuitem"
              type="button"
              onClick={() => startLinkAccount(p)}
              className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-sm hover:opacity-80"
            >
              <UserPlus className="h-4 w-4 shrink-0" />
              Add {providerLabel(p)} account
            </button>
          ))}

          {devLogin && (
            <form onSubmit={onDevLink} className="flex gap-1 px-2 py-1.5">
              <input
                type="email"
                required
                value={devEmail}
                onChange={(e) => setDevEmail(e.target.value)}
                placeholder="Add dev account…"
                aria-label="Add a dev-login account"
                className="min-w-0 flex-1 rounded border px-2 py-1 text-xs"
                style={{
                  borderColor: "var(--color-border)",
                  backgroundColor: "var(--color-surface)",
                }}
              />
              <button
                type="submit"
                disabled={busy}
                aria-label="Link this account"
                className="rounded border px-2 hover:opacity-80 disabled:opacity-50"
                style={{ borderColor: "var(--color-border)" }}
              >
                <Plus className="h-3.5 w-3.5" />
              </button>
            </form>
          )}

          {(showSignOut || showManageLink) && (
            <>
              <div
                className="my-1 border-t"
                style={{ borderColor: "var(--color-border)" }}
              />
              <div className="flex gap-1">
                {showManageLink &&
                  (preferredProvider ? (
                    // Straight to the provider's account picker —
                    // /auth/link sends prompt=select_account, so the IdP
                    // offers a choice instead of silently re-using the
                    // account already signed in there.
                    <button
                      role="menuitem"
                      type="button"
                      onClick={() => startLinkAccount(preferredProvider)}
                      title={`Sign in with a different ${providerLabel(
                        preferredProvider,
                      )} account`}
                      className="flex flex-1 items-center gap-2 rounded px-2 py-1.5 text-left text-sm hover:opacity-80"
                    >
                      <Users className="h-4 w-4 shrink-0" />
                      Switch user
                    </button>
                  ) : (
                    <Link
                      role="menuitem"
                      to="/settings/account"
                      onClick={() => setOpen(false)}
                      title="Manage the accounts linked to this session"
                      className="flex flex-1 items-center gap-2 rounded px-2 py-1.5 text-left text-sm hover:opacity-80"
                    >
                      <Users className="h-4 w-4 shrink-0" />
                      Switch user
                    </Link>
                  ))}
                {showSignOut && (
                  <button
                    role="menuitem"
                    type="button"
                    onClick={onSignOut}
                    title={
                      user.accounts.length > 1
                        ? "Signs out of all linked accounts"
                        : undefined
                    }
                    className="flex flex-1 items-center gap-2 rounded px-2 py-1.5 text-left text-sm hover:opacity-80"
                  >
                    <LogOut className="h-4 w-4 shrink-0" />
                    Sign out
                  </button>
                )}
              </div>
            </>
          )}

          {error && (
            <p className="px-2 py-1 text-xs text-red-600" role="alert">
              {error}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
