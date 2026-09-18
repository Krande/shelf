/**
 * Copy this item into another space.
 *
 * Only spaces the caller can write to are offered — copying somewhere
 * you can only read would just 403. The current space is excluded, and
 * so is any space this item already lives in.
 *
 * The note about inheritance is deliberate and stays visible: for a
 * document several spaces need to *read*, subscribing is better than
 * copying in every way that matters, and the moment someone reaches for
 * "copy" on a standard is the moment to say so.
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Copy, Loader2 } from "lucide-react";
import { ApiError } from "@/api/client";
import { copyItem, type CopyItemResult, type Item } from "@/api/items";
import CopyDestination from "./CopyDestination";
import { canEdit, fetchMySpaces, type Space } from "@/api/spaces";

export default function CopyToSpaceMenu({
  item,
  spaceSlug,
}: {
  item: Item;
  /** The space being viewed, excluded from the targets. */
  spaceSlug: string | null;
}) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [target, setTarget] = useState("");
  const [collectionId, setCollectionId] = useState("");
  const [withFiles, setWithFiles] = useState(true);
  const [done, setDone] = useState<CopyItemResult | null>(null);

  const spaces = useQuery<Space[], ApiError>({
    queryKey: ["spaces"],
    queryFn: () => fetchMySpaces(),
    enabled: open,
    retry: (_a, err) => err.status >= 500,
  });

  const targets = (spaces.data ?? []).filter(
    (s) => canEdit(s) && s.slug !== spaceSlug && s.id !== item.space_id,
  );

  const copy = useMutation({
    mutationFn: () =>
      copyItem(item.id, target, {
        includeAttachments: withFiles,
        targetCollectionId: collectionId || undefined,
      }),
    onSuccess: (result) => {
      setDone(result);
      setTarget("");
      setCollectionId("");
      // The target space's listing has a new row in it.
      qc.invalidateQueries({ queryKey: ["items"] });
      qc.invalidateQueries({ queryKey: ["revisions"] });
    },
  });

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        aria-label="Copy to another space"
        title="Copy to another space"
        className="rounded p-1 hover:opacity-70"
        style={{ color: "var(--color-text-muted)" }}
      >
        <Copy className="h-3.5 w-3.5" />
      </button>
    );
  }

  return (
    <div className="relative">
      <div
        className="absolute right-0 top-0 z-20 w-72 rounded border p-3 shadow-lg"
        style={{
          backgroundColor: "var(--color-surface)",
          borderColor: "var(--color-border)",
        }}
      >
        <p className="mb-2 text-xs font-medium">Copy to another space</p>

        {done ? (
          <>
            <p className="flex items-start gap-1 text-xs">
              <Check className="mt-0.5 h-3 w-3 shrink-0" />
              <span>
                Copied to <strong>{done.space_slug}</strong>
                {done.attachments_copied > 0 &&
                  ` with ${done.attachments_copied} file${done.attachments_copied === 1 ? "" : "s"}`}
                .
                {done.linked_to_standard &&
                  " The copy joins the same standard's revision history."}
              </span>
            </p>
            <button
              onClick={() => {
                setDone(null);
                setOpen(false);
              }}
              className="mt-2 rounded border px-2 py-1 text-xs hover:opacity-80"
              style={{ borderColor: "var(--color-border)" }}
            >
              Done
            </button>
          </>
        ) : (
          <>
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
              A real copy — the two diverge from here. If the other space only
              needs to <em>read</em> this, inheriting the space it lives in is
              better: one copy, and corrections reach everyone. Notes and
              highlights stay with their authors and don't come across.
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

            {copy.error && (
              <p className="mt-2 text-xs text-red-600" role="alert">
                {copy.error.message}
              </p>
            )}
          </>
        )}
      </div>
    </div>
  );
}
