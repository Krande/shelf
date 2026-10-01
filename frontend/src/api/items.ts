import { apiFetch } from "./client";
import { labelFor, type Creator } from "./itemFields";

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
  /** Id of the user who created the item, and their current display name. */
  created_by?: string | null;
  created_by_name?: string | null;
}

export type ItemStatus = "active" | "trashed" | "all";
export type ItemSort = "updated" | "created" | "title" | "type";
export type SortDirection = "asc" | "desc";
/** The fields a search covers by default. */
export type BuiltinSearchScope =
  | "title"
  | "designation"
  | "creators"
  | "abstract"
  | "extra"
  | "fulltext";

/** A built-in scope, or any other metadata field opted into as
 *  `field:<name>` (`field:edition`). Field scopes are never on by default:
 *  each is one more comparison per row, so the plain search stays cheap. */
export type SearchScope = BuiltinSearchScope | `field:${string}`;

/** The default search, in the server's rank order: a title hit outranks
 *  a designation hit, which outranks a creator hit, and so on down to the
 *  PDF body. */
export const ALL_SEARCH_SCOPES: BuiltinSearchScope[] = [
  "title",
  "designation",
  "creators",
  "abstract",
  "extra",
  "fulltext",
];

export const SEARCH_SCOPE_LABELS: Record<BuiltinSearchScope, string> = {
  title: "Title",
  designation: "Designation",
  creators: "Creators",
  abstract: "Abstract",
  extra: "Extra",
  fulltext: "PDF body",
};

/** The server's cap on `field:` scopes in one search. */
export const MAX_FIELD_SCOPES = 20;

/** Metadata fields that already have a built-in scope, so aren't offered
 *  again as `field:` ones. */
export const FIELDS_WITH_BUILTIN_SCOPE = new Set([
  "title",
  "designation",
  "creators",
  "abstractNote",
  "extra",
]);

export function isBuiltinScope(s: string): s is BuiltinSearchScope {
  return (ALL_SEARCH_SCOPES as string[]).includes(s);
}

export function isSearchScope(s: string): s is SearchScope {
  return isBuiltinScope(s) || /^field:[A-Za-z][A-Za-z0-9_]{0,63}$/.test(s);
}

export function scopeLabel(s: SearchScope): string {
  return isBuiltinScope(s) ? SEARCH_SCOPE_LABELS[s] : labelFor(s.slice("field:".length));
}

/** Whether `scope` is exactly the default search — every built-in, no
 *  extra fields — in which case no `scope=` needs sending at all. */
export function isDefaultScope(scope: SearchScope[]): boolean {
  return (
    scope.length === ALL_SEARCH_SCOPES.length &&
    ALL_SEARCH_SCOPES.every((s) => scope.includes(s))
  );
}

/**
 * The order results are grouped in: the built-ins in rank order, then any
 * opted-in fields (which the server ranks below the built-in metadata but
 * above the PDF body), then the body.
 */
export function scopeRankOrder(scope: SearchScope[]): SearchScope[] {
  const fields = scope.filter((s) => !isBuiltinScope(s));
  const builtins = ALL_SEARCH_SCOPES.filter((s) => s !== "fulltext");
  return [...builtins, ...fields, "fulltext"];
}

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
    /** "subcollections" lists what is filed below `collection` instead
     *  of in it. Omitted means the collection's own items. */
    collectionScope?: "direct" | "subcollections";
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
  // Only emit scope= when it differs from the default search. Equal-set
  // short-circuit keeps URLs tidy.
  if (opts.scope && opts.scope.length > 0 && !isDefaultScope(opts.scope)) {
    for (const s of opts.scope) params.append("scope", s);
  }
  if (opts.collection) params.set("collection", opts.collection);
  if (opts.collectionScope && opts.collectionScope !== "direct") {
    params.set("collection_scope", opts.collectionScope);
  }
  // Only when overriding: "pinned" is the server default and sending it
  // would put a redundant param in every URL and every query key.
  if (opts.revisions === "all") params.set("revisions", "all");
  const qs = params.toString();
  return apiFetch<ListItemsResponse>(
    `/api/spaces/${encodeURIComponent(slug)}/items${qs ? `?${qs}` : ""}`,
  );
}

/**
 * Search every space the caller can read, in one request.
 *
 * The per-space listing answers "what is in this library"; this answers
 * "where is that document", which is the landing page's question and has
 * no single space to ask it of. The scope is what
 * `fetchMySpaces({ includeInherited: true })` lists: spaces you own,
 * spaces shared with you, and the ones those subscribe to.
 *
 * Each item comes back once however many of those reach it — a standard
 * in a shared Standards space your own shelf also subscribes to included.
 * That's the reason this is one endpoint and not a per-space fan-out in
 * here: searching each space separately is what produces the duplicate.
 *
 * `spaces` narrows by slug, selecting on where an item *lives*. Inherited
 * spaces are listed on their own in that same set, so leaving one out
 * removes exactly its items. An empty array means "every space filtered
 * out" and returns nothing, mirroring how `scope: []` means "no fields".
 */
export function searchMyItems(
  opts: {
    limit?: number;
    offset?: number;
    q?: string;
    spaces?: string[];
    status?: ItemStatus;
    sort?: ItemSort;
    direction?: SortDirection;
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
  if (opts.scope && opts.scope.length > 0 && !isDefaultScope(opts.scope)) {
    for (const s of opts.scope) params.append("scope", s);
  }
  if (opts.spaces) {
    if (opts.spaces.length === 0) {
      // A single empty value, so "nothing selected" survives the trip as
      // a filter rather than vanishing into "no space= param at all",
      // which the server would read as "all of them".
      params.append("space", "");
    } else {
      for (const s of opts.spaces) params.append("space", s);
    }
  }
  const qs = params.toString();
  return apiFetch<ListItemsResponse>(`/api/me/items${qs ? `?${qs}` : ""}`);
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
  /** Collection the copy was filed under, null if left unfiled. */
  collection_id: string | null;
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
  opts: { includeAttachments?: boolean; targetCollectionId?: string } = {},
): Promise<CopyItemResult> {
  return apiFetch<CopyItemResult>(`/api/items/${encodeURIComponent(id)}/copy`, {
    method: "POST",
    body: JSON.stringify({
      target_slug: targetSlug,
      include_attachments: opts.includeAttachments ?? true,
      // Omitted rather than null when unset, so the copy lands unfiled.
      ...(opts.targetCollectionId
        ? { target_collection_id: opts.targetCollectionId }
        : {}),
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
