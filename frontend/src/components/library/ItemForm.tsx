import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import {
  FIELD_SUGGESTIONS,
  ITEM_TYPES,
  TEXTAREA_FIELDS,
  fieldsForType,
  labelFor,
  type ItemType,
  type Creator,
} from "@/api/itemFields";
import type { ItemData } from "@/api/items";
import CreatorList from "./CreatorList";
import TagsInput from "./TagsInput";
import CollectionPicker from "./CollectionPicker";
import RichTextEditor from "./RichTextEditor";

export interface ItemFormSubmission {
  item_type: ItemType;
  data: ItemData;
  collection_ids: string[];
  /** Tag names entered in the form. The submitter resolves them to
   *  tag IDs (creating any missing tags) before PUTing item tags. */
  tag_names: string[];
}

/**
 * Modal form for creating or editing an item. Renders a type picker
 * and the field set returned by `fieldsForType` (title is always first;
 * creators get their own row; common fields fill in below the type-
 * specific ones).
 *
 * `initial` populates the form when editing. The wire shape is the
 * same for create and update — `onSubmit` receives `{item_type, data}`
 * and the caller decides which mutation to run.
 */
export default function ItemForm({
  open,
  initial,
  title,
  slug,
  onSubmit,
  onClose,
  loading = false,
  modal = true,
}: {
  open: boolean;
  initial?: {
    item_type: ItemType;
    data: ItemData;
    collection_ids?: string[];
    tag_names?: string[];
  };
  title: string;
  slug: string | null;
  onSubmit: (payload: ItemFormSubmission) => void;
  onClose: () => void;
  loading?: boolean;
  /** False for a form that floats over a page the user still wants to
   *  read — the reader, where the PDF holds the answers being typed in.
   *  No backdrop, and a click outside doesn't close it. */
  modal?: boolean;
}) {
  const [pos, setPos] = useState(initialPosition);
  // Read at drag start without re-creating the handler on every move.
  const posRef = useRef(pos);
  posRef.current = pos;

  // Back to the default spot on each open, rather than wherever the last
  // one was dragged — possibly off a since-shrunk window.
  useEffect(() => {
    if (open) setPos(initialPosition());
  }, [open]);

  /** Drag the window by its title bar. Clamped so the bar stays on
   *  screen — a window dragged out of reach can't be dragged back. */
  const startDrag = useCallback((e: React.PointerEvent) => {
    if ((e.target as HTMLElement).closest("button")) return;
    e.preventDefault();
    const bar = e.currentTarget as HTMLElement;
    const startX = e.clientX;
    const startY = e.clientY;
    const origin = posRef.current;
    bar.setPointerCapture?.(e.pointerId);
    const move = (ev: PointerEvent) => {
      const width = bar.getBoundingClientRect().width;
      setPos({
        x: Math.min(
          Math.max(origin.x + ev.clientX - startX, 48 - width),
          window.innerWidth - 48,
        ),
        y: Math.min(
          Math.max(origin.y + ev.clientY - startY, 0),
          window.innerHeight - 48,
        ),
      });
    };
    const up = (ev: PointerEvent) => {
      bar.releasePointerCapture?.(ev.pointerId);
      bar.removeEventListener("pointermove", move);
      bar.removeEventListener("pointerup", up);
      bar.removeEventListener("pointercancel", up);
    };
    bar.addEventListener("pointermove", move);
    bar.addEventListener("pointerup", up);
    bar.addEventListener("pointercancel", up);
  }, []);

  const [itemType, setItemType] = useState<ItemType>(
    initial?.item_type ?? "document",
  );
  const [data, setData] = useState<ItemData>(initial?.data ?? {});
  const [creators, setCreators] = useState<Creator[]>(initial?.data.creators ?? []);
  const [tags, setTags] = useState<string[]>(initial?.tag_names ?? []);
  const [collectionIds, setCollectionIds] = useState<string[]>(
    initial?.collection_ids ?? [],
  );

  // Reset form state when reopened with new initial values.
  useEffect(() => {
    if (open) {
      setItemType(initial?.item_type ?? "document");
      setData(initial?.data ?? {});
      setCreators(initial?.data.creators ?? []);
      setTags(initial?.tag_names ?? []);
      setCollectionIds(initial?.collection_ids ?? []);
    }
  }, [open, initial]);

  if (!open) return null;

  const fields = fieldsForType(itemType);

  function setField(field: string, value: string) {
    setData((d) => ({ ...d, [field]: value }));
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const payload: ItemData = { ...data };
    if (creators.length > 0) payload.creators = creators;
    else delete payload.creators;
    // Tags moved out of `data` into the normalised tags table; strip
    // the legacy key on save so older items get cleaned up on next
    // edit.
    delete (payload as Record<string, unknown>).tags;
    // Strip empty strings so updates don't litter the JSON with "".
    for (const key of Object.keys(payload)) {
      if (payload[key] === "") delete payload[key];
    }
    onSubmit({
      item_type: itemType,
      data: payload,
      collection_ids: collectionIds,
      tag_names: tags.map((t) => t.trim()).filter((t) => t.length > 0),
    });
  }

  const form = (
    <form
      onClick={(e) => e.stopPropagation()}
      onSubmit={handleSubmit}
      role="dialog"
      aria-modal={modal}
      aria-label={title}
      // `resize: both` gives the native corner grip; it needs an
      // overflow other than visible, so the window clips and its body
      // scrolls instead. The max-height keeps a long field set (a
      // Manual has dozens) inside the viewport wherever it was moved.
      className="pointer-events-auto fixed flex flex-col overflow-hidden rounded-lg border shadow-xl"
      style={{
        left: pos.x,
        top: pos.y,
        width: `min(42rem, calc(100vw - 2rem))`,
        maxHeight: `calc(100vh - ${pos.y}px - 1rem)`,
        minWidth: 320,
        minHeight: 200,
        resize: "both",
        backgroundColor: "var(--color-surface)",
        borderColor: "var(--color-border)",
      }}
    >
      <div
        // The title bar is the drag handle.
        onPointerDown={startDrag}
        className="flex shrink-0 cursor-move touch-none select-none items-center justify-between border-b px-4 py-3"
        style={{ borderColor: "var(--color-border)" }}
      >
        <h2 className="text-base font-semibold">{title}</h2>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close"
          className="rounded p-1 hover:opacity-70"
          style={{ color: "var(--color-text-muted)" }}
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4">
        <label className="block">
          <span
            className="mb-1 block text-xs font-medium"
            style={{ color: "var(--color-text-muted)" }}
          >
            Item Type
          </span>
          <select
            value={itemType}
            onChange={(e) => setItemType(e.target.value as ItemType)}
            disabled={loading}
            className="w-full rounded border px-2 py-1.5 text-sm"
            style={{
              backgroundColor: "var(--color-surface)",
              borderColor: "var(--color-border)",
              color: "var(--color-text)",
            }}
          >
            {ITEM_TYPES.map((t) => (
              <option key={t.value} value={t.value}>
                {t.label}
              </option>
            ))}
          </select>
        </label>

        {fields.map((field) => {
          const isTextarea = TEXTAREA_FIELDS.has(field);
          const value = (data[field] as string | undefined) ?? "";
          // Notes get a TipTap rich-text editor that emits HTML
          // straight into data.note; everything else stays as
          // plain inputs / textareas.
          const isRichNote = field === "note" && itemType === "note";
          return (
            <label key={field} className="block">
              <span
                className="mb-1 block text-xs font-medium"
                style={{ color: "var(--color-text-muted)" }}
              >
                {labelFor(field)}
              </span>
              {isRichNote ? (
                <RichTextEditor
                  value={value}
                  onChange={(html) => setField(field, html)}
                  disabled={loading}
                />
              ) : isTextarea ? (
                <textarea
                  rows={field === "note" ? 6 : 3}
                  value={value}
                  onChange={(e) => setField(field, e.target.value)}
                  disabled={loading}
                  className="w-full resize-y rounded border px-2 py-1.5 text-sm"
                  style={{
                    backgroundColor: "var(--color-surface)",
                    borderColor: "var(--color-border)",
                    color: "var(--color-text)",
                  }}
                />
              ) : (
                <>
                  <input
                    type="text"
                    value={value}
                    onChange={(e) => setField(field, e.target.value)}
                    disabled={loading}
                    list={FIELD_SUGGESTIONS[field] ? `suggest-${field}` : undefined}
                    className="w-full rounded border px-2 py-1.5 text-sm"
                    style={{
                      backgroundColor: "var(--color-surface)",
                      borderColor: "var(--color-border)",
                      color: "var(--color-text)",
                    }}
                  />
                  {FIELD_SUGGESTIONS[field] && (
                    <datalist id={`suggest-${field}`}>
                      {FIELD_SUGGESTIONS[field].map((s) => (
                        <option key={s} value={s} />
                      ))}
                    </datalist>
                  )}
                </>
              )}
            </label>
          );
        })}

        {itemType !== "note" && (
          <div>
            <span
              className="mb-1 block text-xs font-medium"
              style={{ color: "var(--color-text-muted)" }}
            >
              Creators
            </span>
            <CreatorList
              creators={creators}
              onChange={setCreators}
              disabled={loading}
            />
          </div>
        )}

        <div>
          <span
            className="mb-1 block text-xs font-medium"
            style={{ color: "var(--color-text-muted)" }}
          >
            Tags
          </span>
          <TagsInput tags={tags} onChange={setTags} disabled={loading} />
        </div>

        <div>
          <span
            className="mb-1 block text-xs font-medium"
            style={{ color: "var(--color-text-muted)" }}
          >
            Collections
          </span>
          <CollectionPicker
            slug={slug}
            selected={collectionIds}
            onChange={setCollectionIds}
            disabled={loading}
          />
        </div>
      </div>

      <div
        className="flex shrink-0 items-center justify-end gap-2 border-t px-4 py-3"
        style={{ borderColor: "var(--color-border)" }}
      >
        <button
          type="button"
          onClick={onClose}
          disabled={loading}
          className="rounded border px-3 py-1.5 text-sm hover:opacity-80 disabled:opacity-50"
          style={{
            borderColor: "var(--color-border)",
            color: "var(--color-text)",
          }}
        >
          Cancel
        </button>
        <button
          type="submit"
          disabled={loading}
          className="rounded px-3 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
          style={{ backgroundColor: "var(--color-accent)" }}
        >
          {loading ? "Saving…" : "Save"}
        </button>
      </div>
    </form>
  );

  // Portalled to the body: rendered in place, the window's `fixed`
  // positioning is at the mercy of whatever panel opened it, which in
  // the reader left a tall form running off the bottom of the screen.
  return createPortal(
    modal ? (
      <div className="fixed inset-0 z-50 bg-black/40" onClick={onClose}>
        {form}
      </div>
    ) : (
      // No backdrop: the point of a modeless form is reading the page
      // behind it. The layer lets clicks through; only the window
      // itself takes them.
      <div className="pointer-events-none fixed inset-0 z-50">{form}</div>
    ),
    document.body,
  );
}

/** Where the window opens: centred horizontally, near the top. */
function initialPosition(): { x: number; y: number } {
  const width = Math.min(672, window.innerWidth - 32);
  return { x: Math.max(16, (window.innerWidth - width) / 2), y: 32 };
}
