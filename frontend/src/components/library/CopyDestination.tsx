import { useQuery } from "@tanstack/react-query";
import { listCollections } from "@/api/collections";

/**
 * Which collection in the target space a copy should land in.
 *
 * Copying does not translate the source's folders — they mean nothing on
 * the other side — so without this a copy arrives unfiled and has to be
 * found and filed by hand. Naming the destination is the same decision,
 * made while the copy is being set up.
 *
 * Only the target's own collections are offered. An inherited one
 * belongs to another space and is read-only here, so filing into it
 * would be a write to somebody else's structure.
 */
export default function CopyDestination({
  targetSlug,
  value,
  onChange,
}: {
  /** Target space, or "" before one is chosen. */
  targetSlug: string;
  value: string;
  onChange: (collectionId: string) => void;
}) {
  const collections = useQuery({
    queryKey: ["collections", targetSlug],
    queryFn: () => listCollections(targetSlug),
    enabled: !!targetSlug,
  });

  const own = (collections.data ?? []).filter((c) => !c.is_inherited);
  const sorted = [...own].sort((a, b) => a.name.localeCompare(b.name));

  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      aria-label="Collection to copy into"
      disabled={!targetSlug || collections.isLoading}
      className="mt-2 w-full rounded border px-2 py-1 text-xs disabled:opacity-50"
      style={{
        borderColor: "var(--color-border)",
        backgroundColor: "var(--color-surface)",
      }}
    >
      <option value="">
        {!targetSlug
          ? "Choose a space first…"
          : collections.isLoading
            ? "Loading collections…"
            : sorted.length === 0
              ? "That space has no collections"
              : "No collection (unfiled)"}
      </option>
      {sorted.map((c) => (
        <option key={c.id} value={c.id}>
          {c.name}
        </option>
      ))}
    </select>
  );
}
