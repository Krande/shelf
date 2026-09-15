/**
 * Header account control — who's active, plus the switcher popover.
 * All the behaviour lives in AccountSwitcher; this only supplies the
 * trigger's chrome.
 */

import { ChevronDown } from "lucide-react";
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
          <span
            className="max-w-[16ch] truncate"
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
