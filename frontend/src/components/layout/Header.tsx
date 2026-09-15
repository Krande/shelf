import { Link, useLocation } from "react-router";
import { LibraryBig, Settings } from "lucide-react";
import { type Me } from "@/api/me";
import AccountMenu from "./AccountMenu";

// Admin is a tab inside Settings rather than its own nav entry — it
// belongs with the other instance-level configuration, and keeping the
// header at two items leaves room for it to stay that way as more
// admin-only sections land.
const navItems = [
  { to: "/library", label: "Library", icon: LibraryBig },
  { to: "/settings", label: "Settings", icon: Settings },
];

export default function Header({ user }: { user: Me }) {
  const { pathname } = useLocation();
  return (
    <header
      className="flex items-center justify-between border-b px-4 py-2"
      style={{
        backgroundColor: "var(--color-surface)",
        borderColor: "var(--color-border)",
      }}
    >
      <div className="flex items-center gap-6">
        <Link
          to="/"
          className="font-mono text-sm font-medium uppercase tracking-[0.18em]"
          title="Home"
        >
          Shelf
        </Link>
        <nav className="flex items-center gap-1">
          {navItems.map(({ to, label, icon: Icon }) => {
            const active = pathname.startsWith(to);
            return (
              <Link
                key={to}
                to={to}
                className="flex items-center gap-1.5 rounded px-2 py-1 text-sm hover:opacity-80"
                style={{
                  color: active
                    ? "var(--color-accent)"
                    : "var(--color-text-muted)",
                }}
              >
                <Icon className="h-4 w-4" />
                {label}
              </Link>
            );
          })}
        </nav>
      </div>
      <AccountMenu user={user} />
    </header>
  );
}
