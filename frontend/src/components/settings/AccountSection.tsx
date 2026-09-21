import { useState, type FormEvent } from "react";
import { useMutation } from "@tanstack/react-query";
import { LogOut, Plus, UserPlus, Users } from "lucide-react";
import { startLinkAccount, switchAccount, unlinkAccount } from "@/api/accounts";
import { apiFetch, logout } from "@/api/client";
import type { Me } from "@/api/me";
import { providerLabel } from "@/api/providers";
import { useLinkProviders } from "@/components/account/useLinkProviders";
import { goToLogin, reloadAsNewAccount } from "@/lib/navigation";

export default function AccountSection({ user: me }: { user: Me }) {
  const [busy, setBusy] = useState(false);
  const [devEmail, setDevEmail] = useState("");
  const { devLogin, providers, preferredProvider, otherProviders } =
    useLinkProviders(me);

  const unlink = useMutation({
    mutationFn: (userId: string) => unlinkAccount(userId),
    onSuccess: ({ active_user_id }) => {
      // Unlinking the last account ends the session; otherwise the active
      // account may have changed. Either way a reload is the honest way
      // to re-fetch everything under whatever identity is left.
      if (active_user_id === null) goToLogin();
      else reloadAsNewAccount();
    },
  });

  const makeActive = useMutation({
    mutationFn: (userId: string) => switchAccount(userId),
    onSuccess: () => reloadAsNewAccount(),
  });

  const devLink = useMutation({
    mutationFn: (email: string) =>
      apiFetch("/auth/dev-login", {
        method: "POST",
        body: JSON.stringify({ email, link: true }),
      }),
    onSuccess: () => reloadAsNewAccount(),
  });

  function onDevLink(e: FormEvent) {
    e.preventDefault();
    devLink.mutate(devEmail);
  }

  async function onLogout() {
    setBusy(true);
    try {
      await logout();
    } finally {
      goToLogin();
    }
  }

  const rowBusy = unlink.isPending || makeActive.isPending;
  const error = unlink.error ?? makeActive.error ?? devLink.error;

  return (
    <>
      <section
        className="mb-6 rounded border p-4"
        style={{
          backgroundColor: "var(--color-surface)",
          borderColor: "var(--color-border)",
        }}
      >
        <h2 className="mb-2 text-sm font-medium">Signed in as</h2>
        {/*
          min-w-0 + break-all on the value cells so long emails / UUIDs
          wrap to the next line on narrow screens instead of pushing the
          parent container past the viewport edge. Without min-w-0, grid
          children would refuse to shrink below their intrinsic width.
        */}
        <dl className="grid grid-cols-[100px_minmax(0,1fr)] gap-y-1 text-sm">
          <dt style={{ color: "var(--color-text-muted)" }}>Display name</dt>
          <dd className="min-w-0 break-words">{me.display_name}</dd>
          <dt style={{ color: "var(--color-text-muted)" }}>Email</dt>
          <dd className="min-w-0 break-all">{me.email}</dd>
          <dt style={{ color: "var(--color-text-muted)" }}>User ID</dt>
          <dd className="min-w-0 break-all font-mono text-xs">{me.id}</dd>
          <dt style={{ color: "var(--color-text-muted)" }}>Role</dt>
          <dd className="min-w-0">{me.role}</dd>
        </dl>
      </section>

      <section
        className="mb-6 rounded border p-4"
        style={{
          backgroundColor: "var(--color-surface)",
          borderColor: "var(--color-border)",
        }}
      >
        <h2 className="mb-2 text-sm font-medium">Linked accounts</h2>
        <p className="mb-3 text-xs" style={{ color: "var(--color-text-muted)" }}>
          Identities you can switch between without signing out — useful if you
          have more than one account on the same provider. They last as long as
          this browser session.
        </p>
        <ul className="flex flex-col gap-1">
          {me.accounts.map((acct) => {
            const active = acct.id === me.id;
            return (
              <li
                key={acct.id}
                className="flex items-center gap-2 rounded border px-2 py-1.5 text-sm"
                style={{ borderColor: "var(--color-border)" }}
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate">
                    {acct.display_name}
                    {active && (
                      <span
                        className="ml-2 text-xs"
                        style={{ color: "var(--color-accent)" }}
                      >
                        active
                      </span>
                    )}
                  </span>
                  <span
                    className="block break-all text-xs"
                    style={{ color: "var(--color-text-muted)" }}
                  >
                    {acct.email}
                  </span>
                </span>
                {/* Swapping which linked identity is active is a local
                    session change — no provider round-trip, so it belongs
                    on the row rather than behind "Switch user". */}
                {!active && (
                  <button
                    onClick={() => makeActive.mutate(acct.id)}
                    disabled={rowBusy}
                    className="shrink-0 rounded border px-2 py-1 text-xs hover:opacity-80 disabled:opacity-50"
                    style={{ borderColor: "var(--color-border)" }}
                  >
                    Make active
                  </button>
                )}
                <button
                  onClick={() => unlink.mutate(acct.id)}
                  disabled={rowBusy}
                  className="shrink-0 rounded border px-2 py-1 text-xs hover:opacity-80 disabled:opacity-50"
                  style={{ borderColor: "var(--color-border)" }}
                >
                  Unlink
                </button>
              </li>
            );
          })}
        </ul>

        {/* Providers the "Switch user" button below doesn't already cover,
            plus the dev-login shortcut on instances that have it. */}
        {(otherProviders.length > 0 || devLogin) && (
          <div className="mt-3 flex flex-wrap items-center gap-2">
            {otherProviders.map((p) => (
              <button
                key={p}
                onClick={() => startLinkAccount(p)}
                className="flex items-center gap-2 rounded border px-2 py-1 text-xs hover:opacity-80"
                style={{ borderColor: "var(--color-border)" }}
              >
                <UserPlus className="h-3.5 w-3.5" />
                Add {providerLabel(p)} account
              </button>
            ))}
            {devLogin && (
              <form onSubmit={onDevLink} className="flex gap-1">
                <input
                  type="email"
                  required
                  value={devEmail}
                  onChange={(e) => setDevEmail(e.target.value)}
                  placeholder="Add dev account…"
                  aria-label="Add a dev-login account"
                  className="min-w-0 rounded border px-2 py-1 text-xs"
                  style={{
                    borderColor: "var(--color-border)",
                    backgroundColor: "var(--color-surface)",
                  }}
                />
                <button
                  type="submit"
                  disabled={devLink.isPending}
                  aria-label="Link this account"
                  className="rounded border px-2 hover:opacity-80 disabled:opacity-50"
                  style={{ borderColor: "var(--color-border)" }}
                >
                  <Plus className="h-3.5 w-3.5" />
                </button>
              </form>
            )}
          </div>
        )}

        {error && (
          <p className="mt-2 text-xs text-red-600" role="alert">
            {error.message}
          </p>
        )}
      </section>

      <div className="flex flex-wrap items-start gap-2">
        {/* Same meaning as the header's "Switch user": straight to the
            provider's account picker — /auth/link sends
            prompt=select_account, so the IdP offers a choice instead of
            silently re-using the account already signed in there.
            Picking between identities already linked here is what the
            "Make active" buttons above are for. */}
        {preferredProvider ? (
          <SwitchUserButton
            provider={preferredProvider}
            label="Switch user"
            title={`Sign in with a different ${providerLabel(
              preferredProvider,
            )} account`}
          />
        ) : (
          // Several providers and nothing to say which one this account
          // came from — offer the choice rather than guessing.
          providers.map((p) => (
            <SwitchUserButton
              key={p}
              provider={p}
              label={`Switch ${providerLabel(p)} user`}
              title={`Sign in with a different ${providerLabel(p)} account`}
            />
          ))
        )}
        <button
          onClick={onLogout}
          disabled={busy}
          className="flex items-center gap-2 rounded border px-3 py-2 text-sm hover:opacity-80 disabled:opacity-50"
          style={{
            borderColor: "var(--color-border)",
            color: "var(--color-text)",
          }}
        >
          <LogOut className="h-4 w-4" />
          {busy ? "Signing out…" : "Sign out"}
        </button>
      </div>
      <p className="mt-2 text-xs" style={{ color: "var(--color-text-muted)" }}>
        Signing out clears every linked account, not just the active one.
      </p>
    </>
  );
}

function SwitchUserButton({
  provider,
  label,
  title,
}: {
  provider: string;
  label: string;
  title: string;
}) {
  return (
    <button
      onClick={() => startLinkAccount(provider)}
      title={title}
      className="flex items-center gap-2 rounded border px-3 py-2 text-sm hover:opacity-80"
      style={{
        borderColor: "var(--color-border)",
        color: "var(--color-text)",
      }}
    >
      <Users className="h-4 w-4" />
      {label}
    </button>
  );
}
