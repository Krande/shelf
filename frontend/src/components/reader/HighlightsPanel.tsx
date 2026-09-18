/**
 * The side drawer listing every highlight and note on a PDF.
 */

import { useCallback, useState } from "react";
import { Check, Link2, Trash2, X } from "lucide-react";
import { annotationLink, type Annotation } from "@/api/annotations";

const HIGHLIGHT_PALETTE: ReadonlyArray<{ name: string; hex: string }> = [
  { name: "Yellow", hex: "#ffd400" },
  { name: "Lime", hex: "#a3e635" },
  { name: "Blue", hex: "#60a5fa" },
  { name: "Pink", hex: "#f472b6" },
  { name: "Orange", hex: "#fb923c" },
  { name: "Purple", hex: "#c084fc" },
];

export function HighlightsPanel({
  annotations,
  attachmentId,
  isDeleting,
  onJumpTo,
  onDelete,
  onRecolor,
  onClose,
}: {
  annotations: Annotation[];
  attachmentId: string;
  isDeleting: boolean;
  onJumpTo: (a: Annotation) => void;
  onDelete: (id: string) => void;
  onRecolor: (id: string, color: string) => void;
  onClose: () => void;
}) {
  const [pickerOpenId, setPickerOpenId] = useState<string | null>(null);
  const [copiedId, setCopiedId] = useState<string | null>(null);

  const onCopyLink = useCallback(
    async (a: Annotation) => {
      const url = annotationLink(attachmentId, a.id, window.location.origin);
      try {
        await navigator.clipboard.writeText(url);
      } catch {
        // Clipboard access needs a secure context, so an instance
        // served over plain http has none. Fall back to selecting the
        // text in a prompt, which always works.
        window.prompt("Copy this link", url);
        return;
      }
      setCopiedId(a.id);
      setTimeout(() => setCopiedId((id) => (id === a.id ? null : id)), 1800);
    },
    [attachmentId],
  );
  return (
    <aside
      className="flex w-full flex-col border-l sm:w-80"
      style={{
        borderColor: "var(--color-border)",
        backgroundColor: "var(--color-surface)",
      }}
    >
      <div
        className="flex items-center justify-between border-b px-3 py-2"
        style={{ borderColor: "var(--color-border)" }}
      >
        <span
          className="text-xs font-medium"
          style={{ color: "var(--color-text)" }}
        >
          Highlights ({annotations.length})
        </span>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close highlights panel"
          className="rounded p-1 hover:opacity-70"
          style={{ color: "var(--color-text-muted)" }}
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
      {annotations.length === 0 ? (
        <div
          className="px-3 py-6 text-center text-xs"
          style={{ color: "var(--color-text-muted)" }}
        >
          No highlights yet. Select text in the PDF and tap "Highlight" to
          create one.
        </div>
      ) : (
        <ul className="min-h-0 flex-1 overflow-y-auto">
          {annotations.map((a) => {
            const pickerOpen = pickerOpenId === a.id;
            return (
              <li
                key={a.id}
                className="border-b last:border-b-0"
                style={{ borderColor: "var(--color-border)" }}
              >
                <div className="flex items-start gap-2 px-3 py-2">
                  <button
                    type="button"
                    onClick={() =>
                      setPickerOpenId(pickerOpen ? null : a.id)
                    }
                    aria-label="Change highlight color"
                    title="Change color"
                    aria-expanded={pickerOpen}
                    className="mt-0.5 inline-block h-3.5 w-3.5 shrink-0 rounded-sm border border-black/10 hover:opacity-80"
                    style={{ backgroundColor: a.color }}
                  />
                  <button
                    type="button"
                    onClick={() => onJumpTo(a)}
                    className="min-w-0 flex-1 text-left hover:opacity-80"
                  >
                    <div
                      className="mb-1 text-[10px] uppercase tracking-wide"
                      style={{ color: "var(--color-text-muted)" }}
                    >
                      Page {a.page_number}
                    </div>
                    <div
                      className="line-clamp-3 text-xs"
                      style={{ color: "var(--color-text)" }}
                    >
                      {a.text?.trim() || (
                        <em style={{ color: "var(--color-text-muted)" }}>
                          (no text)
                        </em>
                      )}
                    </div>
                  </button>
                  <button
                    type="button"
                    onClick={() => onCopyLink(a)}
                    aria-label="Copy link to highlight"
                    title={
                      copiedId === a.id
                        ? "Link copied"
                        : "Copy a link to this highlight"
                    }
                    className="rounded p-1 hover:opacity-70"
                    style={{
                      color:
                        copiedId === a.id
                          ? "var(--color-accent)"
                          : "var(--color-text-muted)",
                    }}
                  >
                    {copiedId === a.id ? (
                      <Check className="h-3.5 w-3.5" />
                    ) : (
                      <Link2 className="h-3.5 w-3.5" />
                    )}
                  </button>
                  <button
                    type="button"
                    onClick={() => onDelete(a.id)}
                    disabled={isDeleting}
                    aria-label="Delete highlight"
                    title="Delete highlight"
                    className="rounded p-1 hover:opacity-70 disabled:opacity-30"
                    style={{ color: "var(--color-text-muted)" }}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
                {pickerOpen && (
                  <div
                    className="flex items-center gap-2 px-3 pb-2"
                    role="radiogroup"
                    aria-label="Highlight color"
                  >
                    {HIGHLIGHT_PALETTE.map((c) => {
                      const selected =
                        a.color.toLowerCase() === c.hex.toLowerCase();
                      return (
                        <button
                          key={c.hex}
                          type="button"
                          role="radio"
                          aria-checked={selected}
                          aria-label={c.name}
                          title={c.name}
                          onClick={() => {
                            if (!selected) onRecolor(a.id, c.hex);
                            setPickerOpenId(null);
                          }}
                          className="h-5 w-5 rounded-full border hover:scale-110"
                          style={{
                            backgroundColor: c.hex,
                            borderColor: selected
                              ? "var(--color-text)"
                              : "rgba(0,0,0,0.15)",
                            borderWidth: selected ? 2 : 1,
                          }}
                        />
                      );
                    })}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </aside>
  );
}
