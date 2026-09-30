import { useEffect, useRef, useState } from "react";
import { Columns3 } from "lucide-react";

export interface ColumnOption {
  key: string;
  label: string;
  checked: boolean;
  /** Shown under the label: what the column holds, or why it's off. */
  hint?: string;
  /** Can't be switched off (Title). */
  locked?: boolean;
}

export interface SaveTarget {
  key: string;
  label: string;
  onSave: () => void;
}

/**
 * Popover choosing the library table's columns.
 *
 * What it starts from is a *profile* — the open collection's, an
 * ancestor's, or the space's — and ticking a box changes only this
 * reader's view, remembered in their browser. "Reset" drops that back to
 * the profile; editors can instead save their view as the profile, for
 * everyone.
 */
export default function ColumnPicker({
  table,
  fields,
  onToggle,
  sourceLabel,
  customised,
  onReset,
  saveTargets,
  saving,
}: {
  /** The table's own columns. */
  table: ColumnOption[];
  /** Metadata fields, those of the types on screen first. */
  fields: ColumnOption[];
  onToggle: (key: string) => void;
  /** Whose profile this view starts from, e.g. "Standards". */
  sourceLabel: string;
  /** The reader has changed the columns from the profile's. */
  customised: boolean;
  onReset: () => void;
  saveTargets: SaveTarget[];
  saving?: boolean;
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
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const muted = { color: "var(--color-text-muted)" };

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label="Columns"
        aria-expanded={open}
        title="Choose which columns to show"
        className="flex items-center gap-1 rounded border px-3 py-1.5 text-sm font-medium"
        style={{
          borderColor: "var(--color-border)",
          color: "var(--color-text)",
        }}
      >
        <Columns3 className="h-4 w-4" />
        <span className="hidden sm:inline">Columns</span>
      </button>
      {open && (
        <div
          className="absolute right-0 top-full z-30 mt-1 flex max-h-[70vh] w-72 flex-col rounded border shadow-lg"
          style={{
            backgroundColor: "var(--color-surface)",
            borderColor: "var(--color-border)",
          }}
        >
          <div
            className="border-b px-3 py-2 text-xs"
            style={{ borderColor: "var(--color-border)", ...muted }}
          >
            <span className="font-medium">Show columns</span>
            <span className="block">
              {customised
                ? `Your view · profile: ${sourceLabel}`
                : `Profile: ${sourceLabel}`}
            </span>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto py-1">
            {table.map((o) => (
              <Option key={o.key} option={o} onToggle={onToggle} />
            ))}
            <div
              className="mt-1 border-t px-3 pb-1 pt-2 text-xs font-medium"
              style={{ borderColor: "var(--color-border)", ...muted }}
            >
              Fields
            </div>
            {fields.map((o) => (
              <Option key={o.key} option={o} onToggle={onToggle} />
            ))}
          </div>
          {(customised || saveTargets.length > 0) && (
            <div
              className="space-y-1 border-t px-3 py-2"
              style={{ borderColor: "var(--color-border)" }}
            >
              {customised && (
                <button
                  type="button"
                  onClick={onReset}
                  className="block w-full rounded px-2 py-1 text-left text-sm hover:opacity-80"
                  style={muted}
                >
                  Reset to {sourceLabel} columns
                </button>
              )}
              {saveTargets.map((t) => (
                <button
                  key={t.key}
                  type="button"
                  disabled={saving}
                  onClick={() => {
                    t.onSave();
                    setOpen(false);
                  }}
                  className="block w-full rounded px-2 py-1 text-left text-sm hover:opacity-80 disabled:opacity-50"
                  style={{ color: "var(--color-accent)" }}
                >
                  {t.label}
                </button>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function Option({
  option: o,
  onToggle,
}: {
  option: ColumnOption;
  onToggle: (key: string) => void;
}) {
  return (
    <label
      className="flex cursor-pointer items-start gap-2 px-3 py-1 text-sm hover:opacity-80"
      style={{ opacity: o.locked ? 0.6 : undefined }}
    >
      <input
        type="checkbox"
        checked={o.checked}
        disabled={o.locked}
        onChange={() => onToggle(o.key)}
        className="mt-1 cursor-pointer"
      />
      <span>
        {o.label}
        {o.hint && (
          <span
            className="block text-xs"
            style={{ color: "var(--color-text-muted)" }}
          >
            {o.hint}
          </span>
        )}
      </span>
    </label>
  );
}
