/**
 * Settings, split into tabs by topic.
 *
 * The tab is a route segment (`/settings/appearance`) rather than local
 * state, so a tab is linkable, survives a reload, and the back button
 * steps through them. That matters most for the account tab, which the
 * switcher reloads the page from.
 */

import { Navigate, NavLink, useParams } from "react-router";
import {
  FileText,
  KeyRound,
  Library,
  Palette,
  ShieldCheck,
  UserRound,
} from "lucide-react";
import AppShell from "@/components/layout/AppShell";
import AccountSection from "@/components/settings/AccountSection";
import AdminSection from "@/components/settings/AdminSection";
import AppearanceSection from "@/components/settings/AppearanceSection";
import ExtractionSection from "@/components/settings/ExtractionSection";
import ProcessingSection from "@/components/settings/ProcessingSection";
import SpacesSection from "@/components/settings/SpacesSection";
import StorageCleanupSection from "@/components/settings/StorageCleanupSection";
import TokenSection from "@/components/settings/TokenSection";
import { useAuth } from "@/auth/session";

const TABS = [
  { id: "account", label: "Account", Icon: UserRound, adminOnly: false },
  { id: "spaces", label: "Spaces", Icon: Library, adminOnly: false },
  { id: "appearance", label: "Appearance", Icon: Palette, adminOnly: false },
  { id: "documents", label: "Documents", Icon: FileText, adminOnly: false },
  { id: "tokens", label: "API tokens", Icon: KeyRound, adminOnly: false },
  { id: "admin", label: "Admin", Icon: ShieldCheck, adminOnly: true },
] as const;

type TabId = (typeof TABS)[number]["id"];

const DEFAULT_TAB: TabId = "account";

export default function SettingsPage() {
  const auth = useAuth();
  const { tab } = useParams<{ tab: string }>();

  if (auth.status !== "authenticated") return null;
  const me = auth.user;

  const visible = TABS.filter((t) => !t.adminOnly || me.is_admin);
  const active = visible.find((t) => t.id === tab);

  // An unknown tab, or the admin tab without the role, falls back rather
  // than rendering an empty frame. Covers both a stale bookmark and the
  // moment a role is revoked while the tab is open.
  if (!active) return <Navigate to={`/settings/${DEFAULT_TAB}`} replace />;

  return (
    <AppShell>
      <div className="mx-auto w-full max-w-2xl p-6">
        <h1 className="mb-4 text-lg font-semibold">Settings</h1>

        {/* overflow-y-hidden is load-bearing: setting only overflow-x to
            auto makes the y axis compute to auto as well, and the active
            tab's 2px underline sitting on a -1px margin is just enough to
            overflow — which showed up as a stray vertical scrollbar. */}
        <nav
          className="mb-6 flex gap-1 overflow-x-auto overflow-y-hidden border-b"
          style={{ borderColor: "var(--color-border)" }}
          aria-label="Settings sections"
        >
          {visible.map(({ id, label, Icon }) => (
            <NavLink
              key={id}
              to={`/settings/${id}`}
              replace
              className="flex shrink-0 items-center gap-1.5 whitespace-nowrap px-3 py-2 text-sm"
              style={({ isActive }) => ({
                color: isActive
                  ? "var(--color-accent)"
                  : "var(--color-text-muted)",
                // -1px pulls the indicator onto the nav's own bottom
                // border rather than sitting below it.
                borderBottom: isActive
                  ? "2px solid var(--color-accent)"
                  : "2px solid transparent",
                marginBottom: "-1px",
              })}
            >
              <Icon className="h-4 w-4" />
              {label}
            </NavLink>
          ))}
        </nav>

        {active.id === "account" && <AccountSection user={me} />}
        {active.id === "spaces" && <SpacesSection user={me} />}
        {active.id === "appearance" && <AppearanceSection />}
        {active.id === "documents" && (
          <>
            <ExtractionSection />
            <ProcessingSection />
            <StorageCleanupSection />
          </>
        )}
        {active.id === "tokens" && <TokenSection />}
        {active.id === "admin" && <AdminSection user={me} />}

        <p className="mt-6 text-xs" style={{ color: "var(--color-text-muted)" }}>
          Shelf {__APP_VERSION__}
        </p>
      </div>
    </AppShell>
  );
}
