import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, ChevronRight, Sliders } from "lucide-react";
import {
  ALL_SEARCH_SCOPES,
  FIELDS_WITH_BUILTIN_SCOPE,
  MAX_FIELD_SCOPES,
  isBuiltinScope,
  isDefaultScope,
  scopeLabel,
  type SearchScope,
} from "@/api/items";
import { fieldColumns, fieldColumnHint } from "@/lib/libraryColumns";

/**
 * Popover that toggles which fields the ?q= search runs against.
 * Emits the full scope set on every change; the parent decides
 * whether the value differs from the default and pushes it onto the
 * URL accordingly. The button glows when the scope differs from the
 * default so it's obvious a filter is in effect.
 *
 * The built-in scopes are listed first. Every other metadata field
 * (Edition, Issuing Body, DOI, …) sits under "More fields": never on by
 * default, since each one is another comparison per row, but there for
 * the search that needs it.
 */
export default function SearchScopePopover({
  scope,
  onChange,
  preferTypes = [],
}: {
  scope: SearchScope[];
  onChange: (next: SearchScope[]) => void;
  /** Item types on screen; their fields are listed first. */
  preferTypes?: string[];
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

  const fields = useMemo<SearchScope[]>(
    () =>
      fieldColumns(preferTypes).filter(
        (k) => !FIELDS_WITH_BUILTIN_SCOPE.has(k.slice("field:".length)),
      ) as SearchScope[],
    [preferTypes],
  );
  const enabled = new Set(scope);
  const fieldsOn = scope.filter((s) => !isBuiltinScope(s));
  const atCap = fieldsOn.length >= MAX_FIELD_SCOPES;
  const changed = !isDefaultScope(scope);
  // Open by default when a field is already in use, so what's selected
  // is visible rather than hidden behind a fold.
  const [moreOpen, setMoreOpen] = useState(fieldsOn.length > 0);

  function toggle(s: SearchScope) {
    const next = new Set(enabled);
    if (next.has(s)) next.delete(s);
    else next.add(s);
    // Built-ins in their fixed order, then fields in list order, so the
    // URL and the query key don't depend on click order.
    onChange([...ALL_SEARCH_SCOPES, ...fields].filter((v) => next.has(v)));
  }

  return (
    <div className="relative" ref={ref}>
      <button
        // Without this it defaults to submit, and the landing page puts
        // this popover inside its search form — so opening the filter
        // submitted the search and navigated to the library instead.
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label="Search scope"
        title={
          changed
            ? `Searching: ${scope.map(scopeLabel).join(", ")}`
            : "Search scope (default fields)"
        }
        className="rounded p-0.5 hover:opacity-70"
        style={{
          color: changed ? "var(--color-accent)" : "var(--color-text-muted)",
        }}
      >
        <Sliders className="h-3.5 w-3.5" />
      </button>
      {open && (
        <div
          className="absolute right-0 top-full z-30 mt-1 flex max-h-[70vh] min-w-[220px] flex-col rounded border shadow-lg"
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
          <div className="min-h-0 flex-1 overflow-y-auto py-1">
            {ALL_SEARCH_SCOPES.map((s) => (
              <ScopeOption
                key={s}
                label={scopeLabel(s)}
                checked={enabled.has(s)}
                onToggle={() => toggle(s)}
              />
            ))}
            <button
              type="button"
              onClick={() => setMoreOpen((v) => !v)}
              aria-expanded={moreOpen}
              className="mt-1 flex w-full items-center gap-1 border-t px-3 pb-1 pt-2 text-left text-xs font-medium hover:opacity-80"
              style={{
                borderColor: "var(--color-border)",
                color: "var(--color-text-muted)",
              }}
            >
              {moreOpen ? (
                <ChevronDown className="h-3 w-3" />
              ) : (
                <ChevronRight className="h-3 w-3" />
              )}
              More fields
              {fieldsOn.length > 0 && ` (${fieldsOn.length})`}
            </button>
            {moreOpen &&
              fields.map((s) => (
                <ScopeOption
                  key={s}
                  label={scopeLabel(s)}
                  hint={fieldColumnHint(s as `field:${string}`)}
                  checked={enabled.has(s)}
                  disabled={atCap && !enabled.has(s)}
                  onToggle={() => toggle(s)}
                />
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
              Default
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

function ScopeOption({
  label,
  hint,
  checked,
  disabled,
  onToggle,
}: {
  label: string;
  hint?: string;
  checked: boolean;
  disabled?: boolean;
  onToggle: () => void;
}) {
  return (
    <label
      className="flex cursor-pointer items-start gap-2 px-3 py-1 text-sm hover:opacity-80"
      style={{ opacity: disabled ? 0.5 : undefined }}
    >
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={onToggle}
        className="mt-1 cursor-pointer"
      />
      <span>
        {label}
        {hint && (
          <span className="block text-xs" style={{ color: "var(--color-text-muted)" }}>
            {hint}
          </span>
        )}
      </span>
    </label>
  );
}
