import { useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, FolderPlus, X } from "lucide-react";
import {
  type Collection,
  setItemCollections,
} from "@/api/collections";

/**
 * One-click "Add to collection" affordance for the detail panel
 * header. Opens a small popover with a checkbox list of the user's
 * collections; toggling a checkbox immediately persists the new
 * membership set via PUT /api/items/{id}/collections (no save button,
 * no edit-mode round-trip).
 *
 * The form's full collection picker stays — that one is the right
 * surface for "edit everything else at the same time".
 */
export default function CollectionAddPopover({
  itemId,
  itemCollectionIds,
  collections,
  slug,
}: {
  itemId: string;
  itemCollectionIds: string[];
  collections: Collection[];
  /** Used to invalidate the right items-query key so the row refreshes. */
  slug: string | null;
}) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // Local state for snappier toggle feedback while the mutation lands.
  const [pendingIds, setPendingIds] = useState<Set<string>>(
    new Set(itemCollectionIds),
  );
  useEffect(() => {
    setPendingIds(new Set(itemCollectionIds));
  }, [itemCollectionIds]);

  // Click-outside to close.
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

  const persist = useMutation({
    mutationFn: (ids: string[]) => setItemCollections(itemId, ids),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["items", slug] });
    },
  });

  function toggle(id: string) {
    setPendingIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      // Fire the mutation with the resolved set; it'll either replace
      // an in-flight call or queue behind it. Either way the last one
      // wins.
      persist.mutate([...next]);
      return next;
    });
  }

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen((v) => !v)}
        aria-label="Add to collection"
        title="Add to collection"
        className="rounded p-1 hover:opacity-70"
        style={{ color: "var(--color-text-muted)" }}
      >
        <FolderPlus className="h-3.5 w-3.5" />
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
            className="flex items-center justify-between border-b px-3 py-2 text-xs font-medium"
            style={{
              borderColor: "var(--color-border)",
              color: "var(--color-text-muted)",
            }}
          >
            <span>Add to collection</span>
            <button
              onClick={() => setOpen(false)}
              aria-label="Close"
              className="rounded p-0.5 hover:opacity-70"
            >
              <X className="h-3 w-3" />
            </button>
          </div>
          <div className="max-h-64 overflow-y-auto py-1">
            {collections.length === 0 && (
              <p
                className="px-3 py-2 text-xs italic"
                style={{ color: "var(--color-text-muted)" }}
              >
                No collections — create one in the sidebar first.
              </p>
            )}
            {[...collections]
              .sort((a, b) => a.name.localeCompare(b.name))
              .map((c) => {
                const checked = pendingIds.has(c.id);
                return (
                  <button
                    key={c.id}
                    type="button"
                    onClick={() => toggle(c.id)}
                    className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm hover:opacity-80"
                  >
                    <span
                      className="flex h-4 w-4 shrink-0 items-center justify-center rounded border"
                      style={{
                        backgroundColor: checked
                          ? "var(--color-accent)"
                          : "transparent",
                        borderColor: checked
                          ? "var(--color-accent)"
                          : "var(--color-border)",
                      }}
                    >
                      {checked && <Check className="h-3 w-3 text-white" />}
                    </span>
                    <span className="truncate">{c.name}</span>
                  </button>
                );
              })}
          </div>
        </div>
      )}
    </div>
  );
}
