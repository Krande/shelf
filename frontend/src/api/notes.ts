import { apiFetch } from "./client";

export interface Note {
  id: string;
  item_id: string;
  content_html: string;
  content_text: string;
  created_at: string;
  updated_at: string;
}

export function listNotes(itemId: string): Promise<Note[]> {
  return apiFetch<Note[]>(
    `/api/items/${encodeURIComponent(itemId)}/notes`,
  );
}

export function createNote(
  itemId: string,
  contentHtml: string,
): Promise<Note> {
  return apiFetch<Note>(`/api/items/${encodeURIComponent(itemId)}/notes`, {
    method: "POST",
    body: JSON.stringify({ content_html: contentHtml }),
  });
}

export function updateNote(id: string, contentHtml: string): Promise<Note> {
  return apiFetch<Note>(`/api/notes/${encodeURIComponent(id)}`, {
    method: "PUT",
    body: JSON.stringify({ content_html: contentHtml }),
  });
}

export function deleteNote(id: string): Promise<void> {
  return apiFetch<void>(`/api/notes/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}
