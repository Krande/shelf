import { apiFetch } from "./client";

export interface Collection {
  id: string;
  space_id: string;
  parent_id: string | null;
  name: string;
  description: string | null;
  position: number;
  created_at: string;
  updated_at: string;
  /**
   * Belongs to a space this one inherits. Read-only here: browsable and
   * filterable, but not renameable, reorderable, or a place to file
   * things. Rendered as its own group rather than mixed in with the
   * space's own folders.
   */
  is_inherited?: boolean;
  /** Which space it came from. Only set when `is_inherited`. */
  space_name?: string | null;
}

export function listCollections(slug: string): Promise<Collection[]> {
  return apiFetch<Collection[]>(
    `/api/spaces/${encodeURIComponent(slug)}/collections`,
  );
}

export function createCollection(
  slug: string,
  payload: {
    name: string;
    parent_id?: string | null;
    description?: string | null;
  },
): Promise<Collection> {
  return apiFetch<Collection>(
    `/api/spaces/${encodeURIComponent(slug)}/collections`,
    { method: "POST", body: JSON.stringify(payload) },
  );
}

/**
 * Partial update. Send only the fields you want changed. To clear a
 * description pass an empty string; to move to root pass parent_id
 * `null` explicitly (the server distinguishes "field unset" from
 * "field set to null" via the JSON payload).
 */
export function updateCollection(
  id: string,
  payload: {
    name?: string;
    description?: string | null;
    parent_id?: string | null;
    position?: number;
  },
): Promise<Collection> {
  return apiFetch<Collection>(`/api/collections/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function deleteCollection(id: string): Promise<void> {
  return apiFetch<void>(`/api/collections/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

export function setItemCollections(
  itemId: string,
  collectionIds: string[],
): Promise<string[]> {
  return apiFetch<string[]>(
    `/api/items/${encodeURIComponent(itemId)}/collections`,
    {
      method: "PUT",
      body: JSON.stringify({ collection_ids: collectionIds }),
    },
  );
}
