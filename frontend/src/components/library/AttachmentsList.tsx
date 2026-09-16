import { useRef, useState, type DragEvent, type ChangeEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router";
import {
  BookOpen,
  Download,
  Loader2,
  Paperclip,
  Trash2,
  Upload,
} from "lucide-react";
import {
  type Attachment,
  deleteAttachment,
  getStreamUrl,
  listAttachments,
  uploadAttachment,
} from "@/api/attachments";

function isPdf(att: Attachment): boolean {
  return (
    att.content_type === "application/pdf" ||
    att.filename.toLowerCase().endsWith(".pdf")
  );
}

function formatSize(bytes: number | null): string {
  if (bytes === null) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

/**
 * Per-item attachment list with drag-drop + file-picker upload. Each
 * row gets a download (presigned URL minted on click, then opened in
 * a new tab) and a delete button. Upload state is local — no global
 * progress because the typical attachment is a single PDF.
 */
export default function AttachmentsList({
  itemId,
  readOnly = false,
}: {
  itemId: string;
  /**
   * Viewing this item somewhere it is only borrowed from another space.
   * Files can be opened and downloaded; adding and removing them is
   * done in the space that owns the document.
   *
   * A view-level rule, not an authorization one: someone who owns the
   * source space genuinely may change it, and the API lets them — they
   * just switch to that space to do it, rather than editing a shared
   * document from inside their own shelf by accident.
   */
  readOnly?: boolean;
}) {
  const qc = useQueryClient();
  const nav = useNavigate();
  const [dragActive, setDragActive] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const attachments = useQuery({
    queryKey: ["attachments", itemId],
    queryFn: () => listAttachments(itemId),
  });

  const upload = useMutation({
    mutationFn: async (files: File[]) => {
      for (const f of files) {
        await uploadAttachment(itemId, f);
      }
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["attachments", itemId] });
      setError(null);
    },
    onError: (e: Error) => setError(e.message),
  });

  const remove = useMutation({
    mutationFn: (id: string) => deleteAttachment(id),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["attachments", itemId] }),
  });

  function pickFiles(e: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(e.target.files ?? []);
    if (files.length === 0) return;
    e.target.value = "";
    upload.mutate(files);
  }

  function onDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragActive(false);
    const files = Array.from(e.dataTransfer.files ?? []);
    if (files.length > 0) upload.mutate(files);
  }

  function onDownload(att: Attachment) {
    // The backend stream endpoint responds with
    // Content-Disposition: attachment; filename="<original>", so the
    // browser saves the file under the user-visible name instead of
    // the opaque storage key. A throwaway <a download> avoids
    // navigating away from the library page.
    const a = document.createElement("a");
    a.href = getStreamUrl(att.id);
    a.rel = "noopener";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  }

  return (
    <section
      onDragOver={(e) => {
        if (readOnly) return;
        e.preventDefault();
        setDragActive(true);
      }}
      onDragLeave={() => setDragActive(false)}
      onDrop={readOnly ? undefined : onDrop}
      className="rounded border p-2"
      style={{
        borderColor: dragActive
          ? "var(--color-accent)"
          : "var(--color-border)",
        backgroundColor: dragActive
          ? "color-mix(in srgb, var(--color-accent) 6%, transparent)"
          : "transparent",
      }}
    >
      <div className="mb-1 flex items-center justify-between">
        <span
          className="flex items-center gap-1 text-xs font-medium"
          style={{ color: "var(--color-text-muted)" }}
        >
          <Paperclip className="h-3.5 w-3.5" />
          Attachments
        </span>
        {readOnly ? (
          <span
            className="text-xs"
            style={{ color: "var(--color-text-muted)" }}
            title="Add or remove files in the space that owns this document"
          >
            read-only here
          </span>
        ) : (
          <>
            <button
              type="button"
              onClick={() => fileInput.current?.click()}
              disabled={upload.isPending}
              className="flex items-center gap-1 rounded px-2 py-0.5 text-xs hover:opacity-70 disabled:opacity-50"
              style={{ color: "var(--color-accent)" }}
            >
              {upload.isPending ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <Upload className="h-3 w-3" />
              )}
              {upload.isPending ? "Uploading…" : "Upload"}
            </button>
            <input
              ref={fileInput}
              type="file"
              multiple
              onChange={pickFiles}
              className="hidden"
            />
          </>
        )}
      </div>

      {error && (
        <p className="mb-1 text-xs text-red-500" role="alert">
          {error}
        </p>
      )}

      {attachments.isLoading && (
        <p className="text-xs italic" style={{ color: "var(--color-text-muted)" }}>
          Loading…
        </p>
      )}
      {attachments.data && attachments.data.length === 0 && !upload.isPending && (
        <p
          className="py-2 text-xs italic"
          style={{ color: "var(--color-text-muted)" }}
        >
          {dragActive ? "Drop to upload" : "Drop a file here, or click Upload."}
        </p>
      )}
      {attachments.data && attachments.data.length > 0 && (
        <ul className="flex flex-col gap-1">
          {attachments.data.map((a) => {
            const pdf = isPdf(a);
            const pending = a.uploaded_at === null;
            const primaryAction = () => {
              if (pending) return;
              if (pdf) nav(`/reader/${encodeURIComponent(a.id)}`);
              else void onDownload(a);
            };
            return (
              <li
                key={a.id}
                className="group flex items-center gap-2 rounded px-1 py-1 text-sm hover:bg-black/5"
              >
                <Paperclip
                  className="h-3.5 w-3.5"
                  style={{ color: "var(--color-text-muted)" }}
                />
                <button
                  type="button"
                  onClick={primaryAction}
                  disabled={pending}
                  className="flex-1 truncate text-left hover:underline disabled:cursor-not-allowed disabled:no-underline disabled:opacity-50"
                  title={
                    pending
                      ? `${a.filename} (upload pending)`
                      : pdf
                      ? `Open ${a.filename}`
                      : a.filename
                  }
                >
                  {a.filename}
                  {pending && (
                    <span
                      className="ml-1 text-xs italic"
                      style={{ color: "var(--color-text-muted)" }}
                    >
                      (pending)
                    </span>
                  )}
                </button>
                <span
                  className="text-xs"
                  style={{ color: "var(--color-text-muted)" }}
                >
                  {formatSize(a.size_bytes)}
                </span>
                {pdf && !pending && (
                  <button
                    type="button"
                    onClick={() =>
                      nav(`/reader/${encodeURIComponent(a.id)}`)
                    }
                    aria-label="Open in reader"
                    // sm+ keeps the desktop hover-reveal behaviour;
                    // touch devices (no `hover:` event) need the
                    // buttons always visible.
                    className="rounded p-0.5 hover:opacity-70 sm:invisible sm:group-hover:visible"
                    style={{ color: "var(--color-accent)" }}
                  >
                    <BookOpen className="h-3.5 w-3.5" />
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => onDownload(a)}
                  disabled={pending}
                  aria-label="Download"
                  className="rounded p-0.5 hover:opacity-70 disabled:opacity-30 sm:invisible sm:group-hover:visible"
                  style={{ color: "var(--color-text-muted)" }}
                >
                  <Download className="h-3.5 w-3.5" />
                </button>
                {!readOnly && (
                  <button
                    type="button"
                    onClick={() => {
                      if (window.confirm(`Delete attachment "${a.filename}"?`)) {
                        remove.mutate(a.id);
                      }
                    }}
                    aria-label="Delete attachment"
                    className="rounded p-0.5 hover:bg-red-500/10 sm:invisible sm:group-hover:visible"
                    style={{ color: "var(--color-text-muted)" }}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
