import { apiFetch } from "./client";

/**
 * Who a note is for.
 *
 * "private" is the author alone. "space" is everyone who can read the
 * space that owns the *item* — which for an inherited standard is the
 * space it lives in, not the one you read it from. That's the point:
 * sharing a note on a standard reaches the other subscribers, not your
 * own shelf where nobody is.
 */
export type NoteVisibility = "private" | "space";

export interface Note {
  id: string;
  item_id: string;
  content_html: string;
  content_text: string;
  created_at: string;
  updated_at: string;
  author_id: string | null;
  visibility: NoteVisibility;
  /** Whether the caller wrote it — only the author may share or retract. */
  is_mine: boolean;
}

export function listNotes(itemId: string): Promise<Note[]> {
  return apiFetch<Note[]>(
    `/api/items/${encodeURIComponent(itemId)}/notes`,
  );
}

/**
 * Write a note. Read access to the item is enough — annotating
 * something you can only read is what notes are for.
 *
 * Omitting `visibility` lets the server decide: private on an inherited
 * item, shared with the space otherwise.
 */
export function createNote(
  itemId: string,
  contentHtml: string,
  visibility?: NoteVisibility,
): Promise<Note> {
  return apiFetch<Note>(`/api/items/${encodeURIComponent(itemId)}/notes`, {
    method: "POST",
    body: JSON.stringify(
      visibility
        ? { content_html: contentHtml, visibility }
        : { content_html: contentHtml },
    ),
  });
}

/** Share a note with the item's space, or take it back. Author only. */
export function setNoteVisibility(
  id: string,
  visibility: NoteVisibility,
): Promise<Note> {
  return apiFetch<Note>(
    `/api/notes/${encodeURIComponent(id)}/visibility`,
    { method: "PATCH", body: JSON.stringify({ visibility }) },
  );
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
