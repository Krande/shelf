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

/**
 * Resolve tag names to ids in one space, creating any tag that doesn't
 * exist yet (matched case-insensitively). The server's CITEXT unique
 * constraint means a concurrent creator could win the race; on a failed
 * create we re-fetch and pick up the row that got there first instead of
 * erroring. `known` skips the initial listing when the caller already
 * has the space's tags. Returns the ids plus the tags it created, so the
 * caller can put those in its cache.
 */
export async function resolveTagNames(
  slug: string,
  names: string[],
  known?: Tag[],
): Promise<{ ids: string[]; created: Tag[] }> {
  if (names.length === 0) return { ids: [], created: [] };
  const existing = known ?? (await listTags(slug));
  const byName = new Map<string, Tag>();
  for (const t of existing) byName.set(t.name.toLowerCase(), t);
  const ids: string[] = [];
  const created: Tag[] = [];
  for (const raw of names) {
    const name = raw.trim();
    if (!name) continue;
    const hit = byName.get(name.toLowerCase());
    if (hit) {
      if (!ids.includes(hit.id)) ids.push(hit.id);
      continue;
    }
    try {
      const t = await createTag(slug, { name });
      byName.set(t.name.toLowerCase(), t);
      created.push(t);
      ids.push(t.id);
    } catch {
      // Lost the race; refresh and try once more from cache.
      const refreshed = await listTags(slug);
      for (const t of refreshed) byName.set(t.name.toLowerCase(), t);
      const hit2 = byName.get(name.toLowerCase());
      if (hit2 && !ids.includes(hit2.id)) ids.push(hit2.id);
    }
  }
  return { ids, created };
}

export function setItemTags(itemId: string, tagIds: string[]): Promise<string[]> {
  return apiFetch<string[]>(
    `/api/items/${encodeURIComponent(itemId)}/tags`,
    { method: "PUT", body: JSON.stringify({ tag_ids: tagIds }) },
  );
}
