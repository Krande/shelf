import { useEffect, useRef, useState } from "react";
import { Library } from "lucide-react";
import type { Space } from "@/api/spaces";

/**
 * Popover that picks which spaces the landing-page search covers.
 *
 * Its sibling, SearchScopePopover, chooses which *fields* to match; this
 * chooses which libraries to look in. The search spans everything the
 * user can read by default — their own shelf, spaces shared with them,
 * and the ones those subscribe to — so this exists to take one back out:
 * a Standards space with ten thousand documents in it is noise when you
 * are looking for a project drawing.
 *
 * Inherited spaces are listed in their own group. They are separate
 * entries rather than folded into the space that subscribes to them
 * because unchecking one has to mean "not this library's documents",
 * which is only expressible if it has a checkbox of its own.
 *
 * Emits the full set of selected slugs on every change; the parent
 * decides whether that differs from "all of them". An empty selection is
 * a deliberate state, not a bug — it matches nothing, the same way
 * switching off every search field does.
 */
export default function SearchSpacePopover({
  spaces,
  selected,
  onChange,
}: {
  spaces: Space[];
  selected: string[];
  onChange: (next: string[]) => void;
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

  const enabled = new Set(selected);
  const narrowed = selected.length < spaces.length;
  const own = spaces.filter((s) => !s.is_inherited);
  const inherited = spaces.filter((s) => s.is_inherited);

  function toggle(slug: string) {
    const next = new Set(enabled);
    if (next.has(slug)) next.delete(slug);
    else next.add(slug);
    // Ordered by the space list rather than by click order, so the URL
    // and the query key stay stable however the user got here.
    onChange(spaces.filter((s) => next.has(s.slug)).map((s) => s.slug));
  }

  function row(space: Space) {
    return (
      <label
        key={space.slug}
        className="flex cursor-pointer items-center gap-2 px-3 py-1.5 text-sm hover:opacity-80"
      >
        <input
          type="checkbox"
          checked={enabled.has(space.slug)}
          onChange={() => toggle(space.slug)}
          className="cursor-pointer"
        />
        <span className="truncate">{space.name}</span>
      </label>
    );
  }

  return (
    <div className="relative" ref={ref}>
      <button
        // Not a submit button: this popover lives inside the landing
        // page's search form, where the default type would submit the
        // search and navigate away instead of opening the filter.
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label="Spaces to search"
        title={
          narrowed
            ? `Searching ${selected.length} of ${spaces.length} spaces`
            : "Searching every space you can read"
        }
        className="rounded p-0.5 hover:opacity-70"
        style={{
          color: narrowed ? "var(--color-accent)" : "var(--color-text-muted)",
        }}
      >
        <Library className="h-3.5 w-3.5" />
      </button>
      {open && (
        <div
          className="absolute right-0 top-full z-30 mt-1 max-h-80 min-w-[220px] overflow-y-auto rounded border shadow-lg"
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
          <div className="py-1">{own.map(row)}</div>
          {inherited.length > 0 && (
            <>
              <div
                className="border-t px-3 py-1.5 text-xs"
                style={{
                  borderColor: "var(--color-border)",
                  color: "var(--color-text-muted)",
                }}
              >
                Subscribed
              </div>
              <div className="pb-1">{inherited.map(row)}</div>
            </>
          )}
          <div
            className="sticky bottom-0 flex items-center justify-between border-t px-3 py-1.5 text-xs"
            style={{
              borderColor: "var(--color-border)",
              backgroundColor: "var(--color-surface)",
            }}
          >
            <button
              type="button"
              onClick={() => onChange(spaces.map((s) => s.slug))}
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
