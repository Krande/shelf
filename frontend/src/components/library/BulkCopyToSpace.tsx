import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, Loader2, X } from "lucide-react";
import { ApiError } from "@/api/client";
import { fetchMySpaces, canEdit, type Space } from "@/api/spaces";
import { copyItem, type Item } from "@/api/items";
import CopyDestination from "./CopyDestination";

/**
 * Copy every selected item into another space, optionally filing them
 * all under one of its collections.
 *
 * The bulk counterpart to the detail panel's copy button, and the same
 * caveats apply: a real copy that diverges from here, and inheriting the
 * source space is the better answer when the target only needs to read.
 */
export default function BulkCopyToSpace({
  items,
  slug,
  onCopied,
}: {
  items: Item[];
  /** The space being browsed; it is not a target for its own items. */
  slug: string | null;
  onCopied?: () => void;
}) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [target, setTarget] = useState("");
  const [collectionId, setCollectionId] = useState("");
  const [withFiles, setWithFiles] = useState(true);
  const [failed, setFailed] = useState<string[]>([]);
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

  const spaces = useQuery<Space[], ApiError>({
    queryKey: ["spaces"],
    queryFn: () => fetchMySpaces(),
    enabled: open,
    retry: (_a, err) => err.status >= 500,
  });

  const targets = (spaces.data ?? []).filter(
    (s) => canEdit(s) && s.slug !== slug,
  );

  const copy = useMutation({
    mutationFn: async () => {
      const problems: string[] = [];
      // Sequential: each copy duplicates blobs server-side, and a
      // failure that names its item is worth more than a fast burst.
      for (const it of items) {
        try {
          await copyItem(it.id, target, {
            includeAttachments: withFiles,
            targetCollectionId: collectionId || undefined,
          });
        } catch (e) {
          // One refusal shouldn't strand the rest of the selection —
          // an item already in the target is the common case.
          problems.push((e as Error).message);
        }
      }
      return problems;
    },
    onSuccess: (problems) => {
      setFailed(problems);
      qc.invalidateQueries({ queryKey: ["items"] });
      qc.invalidateQueries({ queryKey: ["collections"] });
      qc.invalidateQueries({ queryKey: ["revisions"] });
      if (problems.length === 0) {
        setOpen(false);
        setTarget("");
        setCollectionId("");
        onCopied?.();
      }
    },
  });

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen((v) => !v)}
        disabled={copy.isPending}
        className="flex items-center gap-1 rounded px-2 py-1 text-xs hover:opacity-70 disabled:opacity-50"
        style={{ color: "var(--color-text)" }}
      >
        {copy.isPending ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
        ) : (
          <Copy className="h-3.5 w-3.5" />
        )}
        Copy to space
      </button>
      {open && (
        <div
          className="absolute left-0 top-full z-30 mt-1 w-72 rounded border p-3 shadow-lg"
          style={{
            backgroundColor: "var(--color-surface)",
            borderColor: "var(--color-border)",
          }}
        >
          <div className="mb-2 flex items-center justify-between">
            <span className="text-xs font-medium">
              Copy {items.length} item{items.length === 1 ? "" : "s"} to…
            </span>
            <button
              onClick={() => setOpen(false)}
              aria-label="Close"
              className="rounded p-0.5 hover:opacity-70"
            >
              <X className="h-3 w-3" />
            </button>
          </div>

          <select
            value={target}
            onChange={(e) => {
              setTarget(e.target.value);
              // The previous pick belongs to the previous space.
              setCollectionId("");
            }}
            aria-label="Space to copy into"
            disabled={spaces.isLoading || targets.length === 0}
            className="w-full rounded border px-2 py-1 text-xs disabled:opacity-50"
            style={{
              borderColor: "var(--color-border)",
              backgroundColor: "var(--color-surface)",
            }}
          >
            <option value="">
              {spaces.isLoading
                ? "Loading spaces…"
                : targets.length === 0
                  ? "No other space you can write to"
                  : "Choose a space…"}
            </option>
            {targets.map((s) => (
              <option key={s.id} value={s.slug}>
                {s.name}
              </option>
            ))}
          </select>

          <CopyDestination
            targetSlug={target}
            value={collectionId}
            onChange={setCollectionId}
          />

          <label className="mt-2 flex items-center gap-2 text-xs">
            <input
              type="checkbox"
              checked={withFiles}
              onChange={(e) => setWithFiles(e.target.checked)}
            />
            Copy attached files too
          </label>

          <p
            className="mt-2 text-xs"
            style={{ color: "var(--color-text-muted)" }}
          >
            A real copy — the two diverge from here. Notes and highlights stay
            with their authors and don't come across.
          </p>

          <div className="mt-2 flex gap-1">
            <button
              onClick={() => copy.mutate()}
              disabled={!target || copy.isPending}
              className="flex items-center gap-1 rounded border px-2 py-1 text-xs hover:opacity-80 disabled:opacity-50"
              style={{ borderColor: "var(--color-border)" }}
            >
              {copy.isPending && <Loader2 className="h-3 w-3 animate-spin" />}
              Copy
            </button>
            <button
              onClick={() => setOpen(false)}
              className="rounded border px-2 py-1 text-xs hover:opacity-80"
              style={{ borderColor: "var(--color-border)" }}
            >
              Cancel
            </button>
          </div>

          {failed.length > 0 && (
            <p className="mt-2 text-xs text-red-600" role="alert">
              {failed.length} of {items.length} could not be copied:{" "}
              {failed[0]}
              {failed.length > 1 && " (and others)"}
            </p>
          )}
          {copy.error && (
            <p className="mt-2 text-xs text-red-600" role="alert">
              {copy.error.message}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
