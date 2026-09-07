import { FolderClosed, Pencil, RotateCcw, Trash2, X } from "lucide-react";
import {
  TEXTAREA_FIELDS,
  fieldsForType,
  itemTypeLabel,
  labelFor,
} from "@/api/itemFields";
import type { Item } from "@/api/items";
import type { Collection } from "@/api/collections";
import AttachmentsList from "./AttachmentsList";
import CollectionAddPopover from "./CollectionAddPopover";
import ExportMenu from "./ExportMenu";
import NotesSection from "./NotesSection";
import TagChips from "./TagChips";

function formatCreator(c: {
  firstName?: string;
  lastName?: string;
  name?: string;
}): string {
  if (c.name) return c.name;
  return [c.firstName, c.lastName].filter(Boolean).join(" ").trim();
}

function formatDate(s: string): string {
  return new Date(s).toLocaleString();
}

/**
 * Right-hand panel showing the selected item's metadata read-only,
 * with Edit + Delete actions. Empty state and the close (X) on narrow
 * viewports both delegate up to the parent so layout state stays
 * single-source.
 */
export default function ItemDetail({
  item,
  collections = [],
  tagNames = [],
  spaceSlug = null,
  onEdit,
  onDelete,
  onClose,
  onTagClick,
  onCollectionClick,
  onRestore,
  onPermanentDelete,
}: {
  item: Item | null;
  collections?: Collection[];
  /** Tag names resolved from item.tag_ids by the parent. Empty if
   *  the tags query is still loading or the item has no tags. */
  tagNames?: string[];
  /** Slug of the active space — needed for cache invalidation in the
   *  inline collection-add popover. */
  spaceSlug?: string | null;
  onEdit: () => void;
  onDelete: () => void;
  onClose?: () => void;
  onTagClick?: (tag: string) => void;
  onCollectionClick?: (id: string) => void;
  /** When set, the action bar swaps Edit/Delete for Restore + Permanent Delete. */
  onRestore?: () => void;
  onPermanentDelete?: () => void;
}) {
  if (!item) {
    return (
      <div className="flex h-full items-center justify-center p-6 text-center">
        <p className="text-sm" style={{ color: "var(--color-text-muted)" }}>
          Select an item to see its details.
        </p>
      </div>
    );
  }

  const fields = fieldsForType(item.item_type);
  const creators = item.data.creators ?? [];
  const tags = tagNames;
  const trashed = item.deleted_at !== null;
  const itemCollections = item.collection_ids
    .map((id) => collections.find((c) => c.id === id))
    .filter((c): c is Collection => c !== undefined);

  return (
    <div className="flex h-full flex-col">
      <div
        className="flex items-center justify-between border-b px-4 py-2"
        style={{ borderColor: "var(--color-border)" }}
      >
        <div className="min-w-0 flex-1">
          <p
            className="text-xs uppercase tracking-wider"
            style={{ color: "var(--color-text-muted)" }}
          >
            {itemTypeLabel(item.item_type)}
          </p>
          <h2 className="truncate text-base font-semibold">
            {item.data.title || "(untitled)"}
          </h2>
        </div>
        <div className="flex items-center gap-1">
          {!trashed && (
            <CollectionAddPopover
              itemId={item.id}
              itemCollectionIds={item.collection_ids}
              collections={collections}
              slug={spaceSlug}
            />
          )}
          {!trashed && <ExportMenu itemId={item.id} />}
          {trashed && onRestore ? (
            <button
              onClick={onRestore}
              className="flex items-center gap-1 rounded px-2 py-1 text-xs hover:opacity-80"
              style={{ color: "var(--color-accent)" }}
            >
              <RotateCcw className="h-3.5 w-3.5" />
              Restore
            </button>
          ) : (
            <button
              onClick={onEdit}
              className="flex items-center gap-1 rounded px-2 py-1 text-xs hover:opacity-80"
              style={{ color: "var(--color-accent)" }}
            >
              <Pencil className="h-3.5 w-3.5" />
              Edit
            </button>
          )}
          {trashed && onPermanentDelete ? (
            <button
              onClick={onPermanentDelete}
              aria-label="Delete permanently"
              className="rounded p-1 text-red-500 hover:bg-red-500/10"
            >
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          ) : (
            <button
              onClick={onDelete}
              aria-label="Move to trash"
              className="rounded p-1 hover:bg-red-500/10"
              style={{ color: "var(--color-text-muted)" }}
            >
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          )}
          {onClose && (
            <button
              onClick={onClose}
              aria-label="Close detail"
              className="rounded p-1 hover:opacity-70"
              style={{ color: "var(--color-text-muted)" }}
            >
              <X className="h-3.5 w-3.5" />
            </button>
          )}
        </div>
      </div>

      <div className="flex-1 overflow-auto p-4">
        <dl className="grid grid-cols-[120px_1fr] gap-x-3 gap-y-2 text-sm">
          {fields.map((field) => {
            const value = item.data[field];
            if (value === undefined || value === null || value === "") return null;
            const display = typeof value === "string" ? value : JSON.stringify(value);
            const isNoteHtml = field === "note" && item.item_type === "note";
            return (
              <div key={field} className="contents">
                <dt
                  className="text-xs"
                  style={{ color: "var(--color-text-muted)" }}
                >
                  {labelFor(field)}
                </dt>
                <dd
                  className={
                    isNoteHtml
                      ? "tiptap-rendered"
                      : TEXTAREA_FIELDS.has(field)
                      ? "whitespace-pre-wrap"
                      : ""
                  }
                >
                  {isNoteHtml ? (
                    // The note HTML is authored only by the item's
                    // owner (no shared editing surface yet), so we
                    // trust it. When we add shared spaces, route this
                    // through DOMPurify before render.
                    <div
                      // eslint-disable-next-line react/no-danger
                      dangerouslySetInnerHTML={{ __html: display }}
                    />
                  ) : (
                    display
                  )}
                </dd>
              </div>
            );
          })}

          {creators.length > 0 && (
            <div className="contents">
              <dt
                className="text-xs"
                style={{ color: "var(--color-text-muted)" }}
              >
                Creators
              </dt>
              <dd>
                <ul className="space-y-0.5">
                  {creators.map((c, i) => (
                    <li key={i} className="text-sm">
                      {formatCreator(c)}
                      {c.creatorType && c.creatorType !== "author" && (
                        <span
                          className="ml-1 text-xs"
                          style={{ color: "var(--color-text-muted)" }}
                        >
                          ({c.creatorType})
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
              </dd>
            </div>
          )}

          {tags.length > 0 && (
            <div className="contents">
              <dt
                className="text-xs"
                style={{ color: "var(--color-text-muted)" }}
              >
                Tags
              </dt>
              <dd>
                <TagChips tags={tags} onClick={onTagClick} />
              </dd>
            </div>
          )}

          {itemCollections.length > 0 && (
            <div className="contents">
              <dt
                className="text-xs"
                style={{ color: "var(--color-text-muted)" }}
              >
                Collections
              </dt>
              <dd>
                <div className="flex flex-wrap gap-1">
                  {itemCollections.map((c) => {
                    const Tag = onCollectionClick ? "button" : "span";
                    return (
                      <Tag
                        key={c.id}
                        type={onCollectionClick ? "button" : undefined}
                        onClick={
                          onCollectionClick
                            ? () => onCollectionClick(c.id)
                            : undefined
                        }
                        className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs ${onCollectionClick ? "cursor-pointer hover:opacity-80" : ""}`}
                        style={{
                          backgroundColor:
                            "color-mix(in srgb, var(--color-text-muted) 12%, transparent)",
                          color: "var(--color-text)",
                        }}
                      >
                        <FolderClosed className="h-3 w-3" />
                        {c.name}
                      </Tag>
                    );
                  })}
                </div>
              </dd>
            </div>
          )}

          <div className="contents">
            <dt
              className="text-xs"
              style={{ color: "var(--color-text-muted)" }}
            >
              Created
            </dt>
            <dd
              className="text-xs"
              style={{ color: "var(--color-text-muted)" }}
            >
              {formatDate(item.created_at)}
            </dd>
          </div>
          <div className="contents">
            <dt
              className="text-xs"
              style={{ color: "var(--color-text-muted)" }}
            >
              Updated
            </dt>
            <dd
              className="text-xs"
              style={{ color: "var(--color-text-muted)" }}
            >
              {formatDate(item.updated_at)}
            </dd>
          </div>
        </dl>

        {!trashed && (
          <div className="mt-4">
            <AttachmentsList itemId={item.id} />
          </div>
        )}

        {!trashed && (
          <div className="mt-4">
            <NotesSection itemId={item.id} />
          </div>
        )}
      </div>
    </div>
  );
}
