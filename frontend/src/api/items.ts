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
