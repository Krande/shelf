import { apiFetch } from "./client";
import type { Creator } from "./itemFields";

/**
 * Item.data is opaque JSON on the wire; this typed view captures the
 * shape the form + detail panel expect. Unknown keys still round-trip
 * cleanly because `data` extends Record<string, unknown>.
 *
 * Tags are *not* in `data` — they live in the normalised `tags`
 * table and ride alongside the item as `tag_ids`. See api/tags.ts.
 */
export interface ItemData extends Record<string, unknown> {
  title?: string;
  creators?: Creator[];
  date?: string;
  abstractNote?: string;
  url?: string;
  extra?: string;
  note?: string;
}

export interface Item {
  id: string;
  space_id: string;
  item_type: string;
  data: ItemData;
  created_at: string;
  updated_at: string;
  deleted_at: string | null;
  collection_ids: string[];
  tag_ids: string[];
  /**
   * Reached this listing through an inheritance link rather than living
   * in the space that was asked for. Read-only here — `space_id` says
   * where it actually lives. Only set by the per-space listing; the
   * single-item routes have no "here" to be inherited into.
   */
  is_inherited?: boolean;
}

export type ItemStatus = "active" | "trashed" | "all";
export type ItemSort = "updated" | "created" | "title" | "type";
export type SortDirection = "asc" | "desc";
export type SearchScope =
  | "title"
  | "creators"
  | "abstract"
  | "extra"
  | "fulltext";

export const ALL_SEARCH_SCOPES: SearchScope[] = [
  "title",
  "creators",
  "abstract",
  "extra",
  "fulltext",
];

export const SEARCH_SCOPE_LABELS: Record<SearchScope, string> = {
  title: "Title",
  creators: "Creators",
  abstract: "Abstract",
  extra: "Extra",
  fulltext: "PDF body",
};

/**
 * What to do with standards this space has pinned a revision of.
 *
 * "pinned" (the server default) shows the chosen revision and hides its
 * siblings; "all" reveals them. Standards with no pin are unaffected.
 */
export type RevisionFilter = "pinned" | "all";

export interface ItemCreate {
  item_type: string;
  data?: ItemData;
}

export interface ItemUpdate {
  item_type?: string;
  data?: ItemData;
}

export interface ListItemsResponse {
  items: Item[];
  /** Total matches for the current filter set, independent of paging. */
  total: number;
}

export function listItems(
  slug: string,
  opts: {
    limit?: number;
    offset?: number;
    q?: string;
    tags?: string[];
    status?: ItemStatus;
    sort?: ItemSort;
    direction?: SortDirection;
    collection?: string;
    scope?: SearchScope[];
    revisions?: RevisionFilter;
  } = {},
): Promise<ListItemsResponse> {
  const params = new URLSearchParams();
  if (opts.limit != null) params.set("limit", String(opts.limit));
  if (opts.offset != null) params.set("offset", String(opts.offset));
  if (opts.q && opts.q.trim()) params.set("q", opts.q.trim());
  if (opts.status && opts.status !== "active") params.set("status", opts.status);
  if (opts.sort && opts.sort !== "updated") params.set("sort", opts.sort);
  if (opts.direction && opts.direction !== "desc") {
    params.set("direction", opts.direction);
  }
  if (opts.tags) {
    for (const t of opts.tags) {
      const trimmed = t.trim();
      if (trimmed) params.append("tag", trimmed);
    }
  }
  // Only emit scope= when the caller has narrowed past the default
  // (all four). Equal-set short-circuit keeps URLs tidy.
  if (
    opts.scope &&
    opts.scope.length > 0 &&
    opts.scope.length < ALL_SEARCH_SCOPES.length
  ) {
    for (const s of opts.scope) params.append("scope", s);
  }
  if (opts.collection) params.set("collection", opts.collection);
  // Only when overriding: "pinned" is the server default and sending it
  // would put a redundant param in every URL and every query key.
  if (opts.revisions === "all") params.set("revisions", "all");
  const qs = params.toString();
  return apiFetch<ListItemsResponse>(
    `/api/spaces/${encodeURIComponent(slug)}/items${qs ? `?${qs}` : ""}`,
  );
}

export function getItem(id: string): Promise<Item> {
  return apiFetch<Item>(`/api/items/${encodeURIComponent(id)}`);
}

export function createItem(slug: string, payload: ItemCreate): Promise<Item> {
  return apiFetch<Item>(`/api/spaces/${encodeURIComponent(slug)}/items`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateItem(id: string, payload: ItemUpdate): Promise<Item> {
  return apiFetch<Item>(`/api/items/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function deleteItem(id: string): Promise<void> {
  return apiFetch<void>(`/api/items/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

export function permanentDeleteItem(id: string): Promise<void> {
  return apiFetch<void>(
    `/api/items/${encodeURIComponent(id)}?permanent=true`,
    { method: "DELETE" },
  );
}

export function restoreItem(id: string): Promise<Item> {
  return apiFetch<Item>(`/api/items/${encodeURIComponent(id)}/restore`, {
    method: "POST",
  });
}

export interface CopyItemResult {
  item_id: string;
  space_id: string;
  space_slug: string;
  attachments_copied: number;
  linked_to_standard: boolean;
}

/**
 * Copy an item into another space — a real copy, bytes included.
 *
 * For a document many spaces need to *read*, subscribing to the space
 * that owns it is better: one copy, one place to fix a mistake. This is
 * for when the target genuinely wants its own.
 *
 * Notes and highlights deliberately don't come across; they belong to
 * whoever wrote them, under the visibility they chose.
 */
export function copyItem(
  id: string,
  targetSlug: string,
  opts: { includeAttachments?: boolean } = {},
): Promise<CopyItemResult> {
  return apiFetch<CopyItemResult>(`/api/items/${encodeURIComponent(id)}/copy`, {
    method: "POST",
    body: JSON.stringify({
      target_slug: targetSlug,
      include_attachments: opts.includeAttachments ?? true,
    }),
  });
}

export interface FulltextHit {
  page_number: number;
  // Server-rendered HTML — only ts_headline's <mark> tags are
  // emitted; the surrounding text is escaped by Postgres before the
  // tags are inserted. Render via dangerouslySetInnerHTML.
  snippet_html: string;
}

export interface FulltextHitsAttachment {
  attachment_id: string;
  filename: string;
  hits: FulltextHit[];
}

export function fetchFulltextHits(
  itemId: string,
  q: string,
): Promise<FulltextHitsAttachment[]> {
  const params = new URLSearchParams({ q });
  return apiFetch<FulltextHitsAttachment[]>(
    `/api/items/${encodeURIComponent(itemId)}/fulltext-hits?${params}`,
  );
}
