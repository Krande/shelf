import { useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { FolderPlus, X } from "lucide-react";
import {
  type Collection,
  setItemCollections,
} from "@/api/collections";
import type { Item } from "@/api/items";

/**
 * Bulk-action variant of CollectionAddPopover: picks one collection
 * and adds it to every selected item, preserving each item's existing
 * membership. Different semantics from the single-item popover, which
 * replaces the full membership set on toggle.
 */
export default function BulkAddToCollection({
  items,
  collections,
  slug,
  onAdded,
}: {
  items: Item[];
  collections: Collection[];
  slug: string | null;
  onAdded?: () => void;
}) {
  const qc = useQueryClient();
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

  const add = useMutation({
    mutationFn: async (collectionId: string) => {
      await Promise.all(
        items.map((it) => {
          if (it.collection_ids.includes(collectionId)) return null;
          return setItemCollections(it.id, [
            ...it.collection_ids,
            collectionId,
          ]);
        }),
      );
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["items", slug] });
      setOpen(false);
      onAdded?.();
    },
  });

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen((v) => !v)}
        disabled={add.isPending}
        className="flex items-center gap-1 rounded px-2 py-1 text-xs hover:opacity-70 disabled:opacity-50"
        style={{ color: "var(--color-text)" }}
      >
        <FolderPlus className="h-3.5 w-3.5" />
        Add to collection
      </button>
      {open && (
        <div
          className="absolute left-0 top-full z-30 mt-1 min-w-[220px] rounded border shadow-lg"
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
            <span>
              Add {items.length} item{items.length === 1 ? "" : "s"} to…
            </span>
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
              .map((c) => (
                <button
                  key={c.id}
                  type="button"
                  disabled={add.isPending}
                  onClick={() => add.mutate(c.id)}
                  className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm hover:opacity-80 disabled:opacity-50"
                >
                  <span className="truncate">{c.name}</span>
                </button>
              ))}
          </div>
        </div>
      )}
    </div>
  );
}
