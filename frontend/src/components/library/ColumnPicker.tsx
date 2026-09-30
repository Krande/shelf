import { useEffect, useRef, useState } from "react";
import { Columns3 } from "lucide-react";

export interface ColumnOption {
  key: string;
  label: string;
  checked: boolean;
  /** Shown under the label: what the column holds, or why it's off. */
  hint?: string;
}

/**
 * Popover of the library table's optional columns. The fixed ones
 * (title, creator, type, tags, date) aren't listed; this is only for the
 * columns that are worth their width to some libraries and not others.
 */
export default function ColumnPicker({
  options,
  onToggle,
}: {
  options: ColumnOption[];
  onToggle: (key: string) => void;
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

  if (options.length === 0) return null;

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label="Columns"
        aria-expanded={open}
        title="Choose which optional columns to show"
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
          className="absolute right-0 top-full z-30 mt-1 min-w-[220px] rounded border shadow-lg"
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
            Show columns
          </div>
          <div className="py-1">
            {options.map((o) => (
              <label
                key={o.key}
                className="flex cursor-pointer items-start gap-2 px-3 py-1.5 text-sm hover:opacity-80"
              >
                <input
                  type="checkbox"
                  checked={o.checked}
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
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
