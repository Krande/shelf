import { apiFetch } from "./client";

export interface Tag {
  id: string;
  space_id: string;
  name: string;
  color: string | null;
  created_at: string;
  updated_at: string;
}

export interface TagCreate {
  name: string;
  color?: string | null;
}

export interface TagUpdate {
  name?: string;
  color?: string | null;
}

export function listTags(slug: string, q?: string): Promise<Tag[]> {
  const params = new URLSearchParams();
  if (q && q.trim()) params.set("q", q.trim());
  const qs = params.toString();
  return apiFetch<Tag[]>(
    `/api/spaces/${encodeURIComponent(slug)}/tags${qs ? `?${qs}` : ""}`,
  );
}

export function createTag(slug: string, payload: TagCreate): Promise<Tag> {
  return apiFetch<Tag>(`/api/spaces/${encodeURIComponent(slug)}/tags`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateTag(id: string, payload: TagUpdate): Promise<Tag> {
  return apiFetch<Tag>(`/api/tags/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function deleteTag(id: string): Promise<void> {
  return apiFetch<void>(`/api/tags/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

export function setItemTags(itemId: string, tagIds: string[]): Promise<string[]> {
  return apiFetch<string[]>(
    `/api/items/${encodeURIComponent(itemId)}/tags`,
    { method: "PUT", body: JSON.stringify({ tag_ids: tagIds }) },
  );
}
