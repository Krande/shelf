import { useEffect, useState } from "react";
import { X } from "lucide-react";
import {
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
}) {
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

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 p-4 sm:p-8"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
    >
      <form
        onClick={(e) => e.stopPropagation()}
        onSubmit={handleSubmit}
        className="w-full max-w-2xl rounded-lg border shadow-xl"
        style={{
          backgroundColor: "var(--color-surface)",
          borderColor: "var(--color-border)",
        }}
      >
        <div
          className="flex items-center justify-between border-b px-4 py-3"
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

        <div className="space-y-3 p-4">
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
                  <input
                    type="text"
                    value={value}
                    onChange={(e) => setField(field, e.target.value)}
                    disabled={loading}
                    className="w-full rounded border px-2 py-1.5 text-sm"
                    style={{
                      backgroundColor: "var(--color-surface)",
                      borderColor: "var(--color-border)",
                      color: "var(--color-text)",
                    }}
                  />
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
          className="flex items-center justify-end gap-2 border-t px-4 py-3"
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
    </div>
  );
}
