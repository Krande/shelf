import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { Download, X } from "lucide-react";
import type { ArchiveOptions } from "@/api/attachments";

/**
 * Asked before every archive download: which optional parts to include.
 *
 * The PDFs (always the originals, as uploaded) and the metadata, tags and
 * collections are always in. The rest is opt-in because an archive is
 * often made to hand to someone else, and notes -- private ones included
 * -- are personal. Only the downloader's own notes and highlights are
 * ever added; other people's stay with them.
 *
 * The last choice is remembered in this browser.
 */

const STORAGE_KEY = "shelf.archiveOptions";

function remembered(): ArchiveOptions {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    const parsed = raw ? (JSON.parse(raw) as ArchiveOptions) : {};
    return {
      notes: !!parsed.notes,
      annotations: !!parsed.annotations,
      revisions: !!parsed.revisions,
    };
  } catch {
    return {};
  }
}

function remember(o: ArchiveOptions) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(o));
  } catch {
    // private mode or storage blocked: the choice just isn't remembered
  }
}

const CHOICES: { key: keyof ArchiveOptions; label: string; hint: string }[] = [
  {
    key: "notes",
    label: "My notes",
    hint: "Notes you wrote on these documents, private ones included",
  },
  {
    key: "annotations",
    label: "My highlights and pins",
    hint: "Markup you made on the PDFs",
  },
  {
    key: "revisions",
    label: "Standard revision details",
    hint: "Which standard and edition each document is, so revision history survives the move",
  },
];

export default function ArchiveDownloadDialog({
  title,
  onCancel,
  onConfirm,
}: {
  /** What is being downloaded, e.g. `Download "Reports"`. */
  title: string;
  onCancel: () => void;
  onConfirm: (options: ArchiveOptions) => void;
}) {
  const [options, setOptions] = useState<ArchiveOptions>(remembered);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onCancel();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto p-4 sm:p-8"
      style={{ backgroundColor: "rgba(0,0,0,0.5)" }}
      onClick={onCancel}
      role="presentation"
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-md rounded border p-4 shadow-lg"
        style={{
          backgroundColor: "var(--color-surface)",
          borderColor: "var(--color-border)",
          color: "var(--color-text)",
        }}
      >
        <div className="mb-3 flex items-start justify-between">
          <h2 className="text-sm font-medium">{title}</h2>
          <button
            onClick={onCancel}
            aria-label="Close"
            className="rounded p-0.5 hover:opacity-70"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <p className="mb-3 text-xs" style={{ color: "var(--color-text-muted)" }}>
          The original PDFs with their metadata, tags and collections, ready
          to import into any Shelf. Also include:
        </p>
        <div className="mb-4 flex flex-col gap-2">
          {CHOICES.map((c) => (
            <label key={c.key} className="flex cursor-pointer items-start gap-2">
              <input
                type="checkbox"
                className="mt-0.5"
                checked={!!options[c.key]}
                onChange={(e) =>
                  setOptions((o) => ({ ...o, [c.key]: e.target.checked }))
                }
              />
              <span className="text-sm">
                {c.label}
                <span
                  className="block text-xs"
                  style={{ color: "var(--color-text-muted)" }}
                >
                  {c.hint}
                </span>
              </span>
            </label>
          ))}
        </div>
        <div className="flex justify-end gap-2">
          <button
            onClick={onCancel}
            className="rounded border px-3 py-1 text-sm hover:opacity-80"
            style={{ borderColor: "var(--color-border)" }}
          >
            Cancel
          </button>
          <button
            onClick={() => {
              remember(options);
              onConfirm(options);
            }}
            className="flex items-center gap-1 rounded px-3 py-1 text-sm font-medium text-white hover:opacity-90"
            style={{ backgroundColor: "var(--color-accent)" }}
          >
            <Download className="h-3.5 w-3.5" />
            Download
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
