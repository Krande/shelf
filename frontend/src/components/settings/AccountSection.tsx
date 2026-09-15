import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { LogOut, Users } from "lucide-react";
import AccountSwitcher from "@/components/account/AccountSwitcher";
import { unlinkAccount } from "@/api/accounts";
import { logout } from "@/api/client";
import type { Me } from "@/api/me";
import { goToLogin, reloadAsNewAccount } from "@/lib/navigation";

export default function AccountSection({ user: me }: { user: Me }) {
  const [busy, setBusy] = useState(false);

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

  async function onLogout() {
    setBusy(true);
    try {
      await logout();
    } finally {
      goToLogin();
    }
  }

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
          {me.accounts.map((acct) => (
            <li
              key={acct.id}
              className="flex items-center gap-2 rounded border px-2 py-1.5 text-sm"
              style={{ borderColor: "var(--color-border)" }}
            >
              <span className="min-w-0 flex-1">
                <span className="block truncate">
                  {acct.display_name}
                  {acct.id === me.id && (
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
              <button
                onClick={() => unlink.mutate(acct.id)}
                disabled={unlink.isPending}
                className="shrink-0 rounded border px-2 py-1 text-xs hover:opacity-80 disabled:opacity-50"
                style={{ borderColor: "var(--color-border)" }}
              >
                Unlink
              </button>
            </li>
          ))}
        </ul>
        {unlink.error && (
          <p className="mt-2 text-xs text-red-600" role="alert">
            {unlink.error.message}
          </p>
        )}
      </section>

      <div className="flex flex-wrap items-start gap-2">
        {/* Same popover as the header menu, so "switch user" means the
            same thing wherever you reach for it. Sign-out and the
            manage link are off: the button beside it already signs out,
            and the link would point at this very page. */}
        <AccountSwitcher
          user={me}
          align="left"
          showSignOut={false}
          showManageLink={false}
          triggerClassName="flex items-center gap-2 rounded border px-3 py-2 text-sm hover:opacity-80"
          triggerStyle={{
            borderColor: "var(--color-border)",
            color: "var(--color-text)",
          }}
          trigger={
            <>
              <Users className="h-4 w-4" />
              Switch user
            </>
          }
        />
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
