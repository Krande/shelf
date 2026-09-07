import { useQuery } from "@tanstack/react-query";
import { listCollections } from "@/api/collections";

/**
 * Read-only multi-select used inside ItemForm. Renders the user's
 * collections as a compact checkbox list; `selected` and `onChange`
 * own the state from the parent form so submit is atomic with the
 * rest of the item.
 */
export default function CollectionPicker({
  slug,
  selected,
  onChange,
  disabled = false,
}: {
  slug: string | null;
  selected: string[];
  onChange: (next: string[]) => void;
  disabled?: boolean;
}) {
  const collections = useQuery({
    queryKey: ["collections", slug],
    queryFn: () => listCollections(slug!),
    enabled: !!slug,
  });

  function toggle(id: string) {
    if (selected.includes(id)) {
      onChange(selected.filter((x) => x !== id));
    } else {
      onChange([...selected, id]);
    }
  }

  if (!slug || collections.isLoading) {
    return (
      <p className="text-xs italic" style={{ color: "var(--color-text-muted)" }}>
        Loading collections…
      </p>
    );
  }
  const data = collections.data ?? [];
  if (data.length === 0) {
    return (
      <p className="text-xs italic" style={{ color: "var(--color-text-muted)" }}>
        No collections yet — create one from the sidebar.
      </p>
    );
  }
  return (
    <div
      className="max-h-40 overflow-y-auto rounded border px-2 py-1"
      style={{
        backgroundColor: "var(--color-surface)",
        borderColor: "var(--color-border)",
      }}
    >
      {[...data]
        .sort((a, b) => a.name.localeCompare(b.name))
        .map((c) => (
          <label
            key={c.id}
            className="flex cursor-pointer items-center gap-2 py-0.5 text-sm"
          >
            <input
              type="checkbox"
              checked={selected.includes(c.id)}
              onChange={() => toggle(c.id)}
              disabled={disabled}
              className="cursor-pointer"
            />
            <span className="truncate">{c.name}</span>
          </label>
        ))}
    </div>
  );
}
