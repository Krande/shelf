/**
 * Header account control — who's active, plus the switcher popover.
 * All the behaviour lives in AccountSwitcher; this only supplies the
 * trigger's chrome.
 */

import { ChevronDown, UserRound } from "lucide-react";
import AccountSwitcher from "@/components/account/AccountSwitcher";
import type { Me } from "@/api/me";

export default function AccountMenu({ user }: { user: Me }) {
  const others = user.accounts.length - 1;

  return (
    <AccountSwitcher
      user={user}
      align="right"
      triggerTitle={user.email}
      triggerClassName="flex items-center gap-1.5 rounded px-2 py-1 text-xs hover:opacity-80"
      trigger={
        <>
          {/* The name is the widest thing in the header; below sm it
              costs more than it says, so the icon stands in for it. The
              trigger keeps its title={email}, so the identity is still
              one hover (or one tap of the menu) away. */}
          <UserRound
            className="h-4 w-4 sm:hidden"
            style={{ color: "var(--color-text-muted)" }}
            aria-label={user.display_name}
          />
          <span
            className="hidden max-w-[16ch] truncate sm:inline"
            style={{ color: "var(--color-text-muted)" }}
          >
            {user.display_name}
          </span>
          {others > 0 && (
            <span
              className="rounded-full px-1.5 py-0.5 text-[10px] leading-none"
              style={{
                backgroundColor: "var(--color-border)",
                color: "var(--color-text-muted)",
              }}
              title={`${others} other account${others > 1 ? "s" : ""} linked`}
            >
              +{others}
            </span>
          )}
          <ChevronDown
            className="h-3.5 w-3.5"
            style={{ color: "var(--color-text-muted)" }}
          />
        </>
      }
    />
  );
}
