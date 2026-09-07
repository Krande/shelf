import { Plus, X } from "lucide-react";
import type { Creator } from "@/api/itemFields";

const CREATOR_TYPES = [
  { value: "author", label: "Author" },
  { value: "editor", label: "Editor" },
  { value: "translator", label: "Translator" },
  { value: "contributor", label: "Contributor" },
  { value: "seriesEditor", label: "Series Editor" },
];

export default function CreatorList({
  creators,
  onChange,
  disabled = false,
}: {
  creators: Creator[];
  onChange: (next: Creator[]) => void;
  disabled?: boolean;
}) {
  function update(idx: number, patch: Partial<Creator>) {
    onChange(creators.map((c, i) => (i === idx ? { ...c, ...patch } : c)));
  }
  function add() {
    onChange([...creators, { creatorType: "author", firstName: "", lastName: "" }]);
  }
  function remove(idx: number) {
    onChange(creators.filter((_, i) => i !== idx));
  }

  return (
    <div className="flex flex-col gap-1">
      {creators.length === 0 && (
        <p className="text-xs italic" style={{ color: "var(--color-text-muted)" }}>
          No creators
        </p>
      )}
      {creators.map((c, i) => (
        <div key={i} className="flex items-center gap-1.5">
          <select
            value={c.creatorType}
            onChange={(e) => update(i, { creatorType: e.target.value })}
            disabled={disabled}
            className="rounded border px-1.5 py-1 text-xs"
            style={{
              backgroundColor: "var(--color-surface)",
              borderColor: "var(--color-border)",
              color: "var(--color-text)",
            }}
          >
            {CREATOR_TYPES.map((t) => (
              <option key={t.value} value={t.value}>
                {t.label}
              </option>
            ))}
            {!CREATOR_TYPES.find((t) => t.value === c.creatorType) && (
              <option value={c.creatorType}>{c.creatorType}</option>
            )}
          </select>
          <input
            type="text"
            placeholder="First name"
            value={c.firstName ?? ""}
            onChange={(e) => update(i, { firstName: e.target.value })}
            disabled={disabled}
            className="flex-1 min-w-0 rounded border px-2 py-1 text-sm"
            style={{
              backgroundColor: "var(--color-surface)",
              borderColor: "var(--color-border)",
              color: "var(--color-text)",
            }}
          />
          <input
            type="text"
            placeholder="Last name"
            value={c.lastName ?? ""}
            onChange={(e) => update(i, { lastName: e.target.value })}
            disabled={disabled}
            className="flex-1 min-w-0 rounded border px-2 py-1 text-sm"
            style={{
              backgroundColor: "var(--color-surface)",
              borderColor: "var(--color-border)",
              color: "var(--color-text)",
            }}
          />
          <button
            type="button"
            onClick={() => remove(i)}
            disabled={disabled}
            aria-label="Remove creator"
            className="rounded p-1 hover:opacity-70 disabled:opacity-30"
            style={{ color: "var(--color-text-muted)" }}
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      ))}
      <button
        type="button"
        onClick={add}
        disabled={disabled}
        className="mt-1 flex items-center gap-1 self-start rounded px-2 py-1 text-xs hover:opacity-70 disabled:opacity-50"
        style={{ color: "var(--color-accent)" }}
      >
        <Plus className="h-3 w-3" />
        Add creator
      </button>
    </div>
  );
}
