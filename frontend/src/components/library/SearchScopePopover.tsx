import { useEffect, useRef, useState } from "react";
import { Sliders } from "lucide-react";
import {
  ALL_SEARCH_SCOPES,
  SEARCH_SCOPE_LABELS,
  type SearchScope,
} from "@/api/items";

/**
 * Popover that toggles which fields the ?q= search runs against.
 * Emits the full scope set on every change; the parent decides
 * whether the value differs from the default and pushes it onto the
 * URL accordingly. The button glows when the user has narrowed past
 * the default so it's obvious a filter is in effect.
 */
export default function SearchScopePopover({
  scope,
  onChange,
}: {
  scope: SearchScope[];
  onChange: (next: SearchScope[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [open]);

  const narrowed = scope.length < ALL_SEARCH_SCOPES.length;
  const enabled = new Set(scope);

  function toggle(s: SearchScope) {
    const next = new Set(enabled);
    if (next.has(s)) next.delete(s);
    else next.add(s);
    onChange(ALL_SEARCH_SCOPES.filter((v) => next.has(v)));
  }

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen((v) => !v)}
        aria-label="Search scope"
        title={
          narrowed
            ? `Searching: ${scope.map((s) => SEARCH_SCOPE_LABELS[s]).join(", ")}`
            : "Search scope (all fields)"
        }
        className="rounded p-0.5 hover:opacity-70"
        style={{
          color: narrowed
            ? "var(--color-accent)"
            : "var(--color-text-muted)",
        }}
      >
        <Sliders className="h-3.5 w-3.5" />
      </button>
      {open && (
        <div
          className="absolute right-0 top-full z-30 mt-1 min-w-[180px] rounded border shadow-lg"
          style={{
            backgroundColor: "var(--color-surface)",
            borderColor: "var(--color-border)",
          }}
        >
          <div
            className="border-b px-3 py-2 text-xs font-medium"
            style={{
              borderColor: "var(--color-border)",
              color: "var(--color-text-muted)",
            }}
          >
            Search in
          </div>
          <div className="py-1">
            {ALL_SEARCH_SCOPES.map((s) => (
              <label
                key={s}
                className="flex cursor-pointer items-center gap-2 px-3 py-1.5 text-sm hover:opacity-80"
              >
                <input
                  type="checkbox"
                  checked={enabled.has(s)}
                  onChange={() => toggle(s)}
                  className="cursor-pointer"
                />
                {SEARCH_SCOPE_LABELS[s]}
              </label>
            ))}
          </div>
          <div
            className="flex items-center justify-between border-t px-3 py-1.5 text-xs"
            style={{ borderColor: "var(--color-border)" }}
          >
            <button
              type="button"
              onClick={() => onChange([...ALL_SEARCH_SCOPES])}
              className="hover:opacity-70"
              style={{ color: "var(--color-text-muted)" }}
            >
              All
            </button>
            <button
              type="button"
              onClick={() => onChange([])}
              className="hover:opacity-70"
              style={{ color: "var(--color-text-muted)" }}
            >
              None
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
