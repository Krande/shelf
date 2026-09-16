import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Lock, Pencil, Plus, Trash2, Users } from "lucide-react";
import {
  type Note,
  createNote,
  deleteNote,
  listNotes,
  setNoteVisibility,
  updateNote,
} from "@/api/notes";
import RichTextEditor from "./RichTextEditor";

/**
 * Per-item notes panel. Shows the existing notes (rendered HTML)
 * with edit + delete inline; a "+ Note" button opens the editor in
 * create mode. Notes are not part of `item.data` — they live in
 * the normalised `notes` table and are loaded on demand.
 *
 * The list holds notes shared with the item's space plus the caller's
 * own private ones. A note on an inherited document starts private; the
 * author shares it deliberately, and sharing reaches everyone who can
 * read the space that owns the document — the other subscribers, not
 * just whoever can see the reader's own space.
 */
export default function NotesSection({ itemId }: { itemId: string }) {
  const qc = useQueryClient();
  const notesQuery = useQuery({
    queryKey: ["notes", itemId],
    queryFn: () => listNotes(itemId),
  });

  // editingId: null = no edit; "" = create new; "<id>" = editing existing.
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");

  const create = useMutation({
    mutationFn: (html: string) => createNote(itemId, html),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["notes", itemId] });
      setEditingId(null);
      setDraft("");
    },
  });

  const update = useMutation({
    mutationFn: ({ id, html }: { id: string; html: string }) =>
      updateNote(id, html),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["notes", itemId] });
      setEditingId(null);
      setDraft("");
    },
  });

  const remove = useMutation({
    mutationFn: (id: string) => deleteNote(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["notes", itemId] }),
  });

  const share = useMutation({
    mutationFn: ({ id, next }: { id: string; next: Note["visibility"] }) =>
      setNoteVisibility(id, next),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["notes", itemId] }),
  });

  const notes = notesQuery.data ?? [];

  function startCreate() {
    setEditingId("");
    setDraft("");
  }
  function startEdit(n: Note) {
    setEditingId(n.id);
    setDraft(n.content_html);
  }
  function cancel() {
    setEditingId(null);
    setDraft("");
  }
  function save() {
    if (editingId === "") {
      create.mutate(draft);
    } else if (editingId) {
      update.mutate({ id: editingId, html: draft });
    }
  }

  const saving = create.isPending || update.isPending;

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <span
          className="text-xs uppercase tracking-wider"
          style={{ color: "var(--color-text-muted)" }}
        >
          Notes ({notes.length})
        </span>
        {editingId === null && (
          <button
            type="button"
            onClick={startCreate}
            className="flex items-center gap-1 rounded px-2 py-1 text-xs hover:opacity-80"
            style={{ color: "var(--color-accent)" }}
          >
            <Plus className="h-3.5 w-3.5" />
            Note
          </button>
        )}
      </div>

      {editingId === "" && (
        <div className="space-y-2">
          <RichTextEditor value={draft} onChange={setDraft} disabled={saving} />
          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={cancel}
              disabled={saving}
              className="rounded border px-2 py-1 text-xs hover:opacity-80"
              style={{
                borderColor: "var(--color-border)",
                color: "var(--color-text)",
              }}
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={save}
              disabled={saving || draft.trim() === "" || draft === "<p></p>"}
              className="rounded px-3 py-1 text-xs font-medium text-white hover:opacity-90 disabled:opacity-50"
              style={{ backgroundColor: "var(--color-accent)" }}
            >
              {saving ? "Saving…" : "Save"}
            </button>
          </div>
        </div>
      )}

      <ul className="space-y-2">
        {notes.map((n) => {
          const editing = editingId === n.id;
          return (
            <li
              key={n.id}
              className="rounded border"
              style={{
                borderColor: "var(--color-border)",
                backgroundColor: "var(--color-surface)",
              }}
            >
              {editing ? (
                <div className="space-y-2 p-2">
                  <RichTextEditor
                    value={draft}
                    onChange={setDraft}
                    disabled={saving}
                  />
                  <div className="flex justify-end gap-2">
                    <button
                      type="button"
                      onClick={cancel}
                      disabled={saving}
                      className="rounded border px-2 py-1 text-xs hover:opacity-80"
                      style={{
                        borderColor: "var(--color-border)",
                        color: "var(--color-text)",
                      }}
                    >
                      Cancel
                    </button>
                    <button
                      type="button"
                      onClick={save}
                      disabled={saving}
                      className="rounded px-3 py-1 text-xs font-medium text-white hover:opacity-90 disabled:opacity-50"
                      style={{ backgroundColor: "var(--color-accent)" }}
                    >
                      {saving ? "Saving…" : "Save"}
                    </button>
                  </div>
                </div>
              ) : (
                <div className="p-2">
                  <div className="flex items-start gap-2">
                    <div
                      className="tiptap-rendered min-w-0 flex-1 text-sm"
                      // Authored by someone who can read this item, which
                      // since inheritance can be a wider set than before.
                      // Worth routing through DOMPurify when there's a
                      // reason to; the editor still only emits its own
                      // TipTap subset.
                      // eslint-disable-next-line react/no-danger
                      dangerouslySetInnerHTML={{ __html: n.content_html }}
                    />
                    {n.is_mine && (
                      <div className="flex shrink-0 items-center gap-0.5">
                        <button
                          type="button"
                          onClick={() => startEdit(n)}
                          aria-label="Edit note"
                          title="Edit note"
                          className="rounded p-1 hover:opacity-70"
                          style={{ color: "var(--color-text-muted)" }}
                        >
                          <Pencil className="h-3.5 w-3.5" />
                        </button>
                        <button
                          type="button"
                          onClick={() => {
                            if (
                              window.confirm(
                                "Delete this note? Cannot be undone.",
                              )
                            ) {
                              remove.mutate(n.id);
                            }
                          }}
                          aria-label="Delete note"
                          title="Delete note"
                          className="rounded p-1 hover:bg-red-500/10"
                          style={{ color: "var(--color-text-muted)" }}
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    )}
                  </div>

                  <div className="mt-1 flex items-center gap-2 text-xs">
                    {n.visibility === "private" ? (
                      <span
                        className="inline-flex items-center gap-1"
                        style={{ color: "var(--color-text-muted)" }}
                      >
                        <Lock className="h-3 w-3" />
                        Only you
                      </span>
                    ) : (
                      <span
                        className="inline-flex items-center gap-1"
                        style={{ color: "var(--color-text-muted)" }}
                      >
                        <Users className="h-3 w-3" />
                        Shared with this space
                      </span>
                    )}
                    {n.is_mine && (
                      <button
                        type="button"
                        onClick={() =>
                          share.mutate({
                            id: n.id,
                            next:
                              n.visibility === "private" ? "space" : "private",
                          })
                        }
                        disabled={share.isPending}
                        className="rounded border px-1.5 py-0.5 text-xs hover:opacity-80 disabled:opacity-50"
                        style={{ borderColor: "var(--color-border)" }}
                      >
                        {n.visibility === "private"
                          ? "Share"
                          : "Make private"}
                      </button>
                    )}
                  </div>
                </div>
              )}
            </li>
          );
        })}
      </ul>

      {share.error && (
        <p className="px-1 text-xs text-red-600" role="alert">
          Could not change who sees that note: {share.error.message}
        </p>
      )}

      {notes.length === 0 && editingId !== "" && (
        <div
          className="px-1 text-xs"
          style={{ color: "var(--color-text-muted)" }}
        >
          No notes yet.
        </div>
      )}
    </div>
  );
}

