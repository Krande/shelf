import { useRef, useState, type KeyboardEvent } from "react";
import { X } from "lucide-react";

/**
 * Chip-style tag editor. Type → Enter / comma / blur to commit a chip;
 * Backspace deletes the last chip when the input is empty. Tags are
 * lowercased and de-duplicated on commit so the wire shape stays
 * predictable; round-trip with the backend keeps user-typed casing
 * only if you want to relax this later.
 */
export default function TagsInput({
  tags,
  onChange,
  disabled = false,
  placeholder = "Add a tag…",
}: {
  tags: string[];
  onChange: (next: string[]) => void;
  disabled?: boolean;
  placeholder?: string;
}) {
  const [draft, setDraft] = useState("");
  const ref = useRef<HTMLInputElement>(null);

  function commit(raw: string) {
    const v = raw.trim().toLowerCase();
    if (!v) return;
    if (tags.includes(v)) {
      setDraft("");
      return;
    }
    onChange([...tags, v]);
    setDraft("");
  }

  function remove(idx: number) {
    onChange(tags.filter((_, i) => i !== idx));
  }

  function onKey(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      commit(draft);
    } else if (e.key === "Backspace" && draft === "" && tags.length > 0) {
      remove(tags.length - 1);
    }
  }

  return (
    <div
      className="flex min-h-[34px] flex-wrap items-center gap-1 rounded border px-2 py-1"
      style={{
        backgroundColor: "var(--color-surface)",
        borderColor: "var(--color-border)",
      }}
      onClick={() => ref.current?.focus()}
    >
      {tags.map((t, i) => (
        <span
          key={t}
          className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs"
          style={{
            backgroundColor:
              "color-mix(in srgb, var(--color-accent) 15%, transparent)",
            color: "var(--color-accent)",
          }}
        >
          {t}
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              remove(i);
            }}
            disabled={disabled}
            aria-label={`Remove tag ${t}`}
            className="rounded-full hover:opacity-70 disabled:opacity-40"
          >
            <X className="h-3 w-3" />
          </button>
        </span>
      ))}
      <input
        ref={ref}
        type="text"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={onKey}
        onBlur={() => commit(draft)}
        disabled={disabled}
        placeholder={tags.length === 0 ? placeholder : ""}
        className="min-w-[120px] flex-1 bg-transparent text-sm outline-none"
        style={{ color: "var(--color-text)" }}
      />
    </div>
  );
}
