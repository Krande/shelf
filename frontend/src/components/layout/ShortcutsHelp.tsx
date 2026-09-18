import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { Info, X } from "lucide-react";

/**
 * What the keyboard and the mouse can do, in one place.
 *
 * Most of these are invisible until someone tries them — Ctrl+click on
 * a row, Backspace out of the reader, the modifier that arms a PDF's
 * own hyperlinks — so the shortcut that nobody finds is the one that
 * may as well not exist. Kept as a plain table rather than scattered
 * tooltips so it can be read through once.
 */

interface Shortcut {
  keys: string;
  what: string;
}

const SEARCH: Shortcut[] = [
  { keys: "↑ / ↓", what: "Move through the results" },
  {
    keys: "Enter / click",
    what: "Open the result's PDF — at the matching page, when the row is a passage from one",
  },
  {
    keys: "Shift + Enter / Shift + click",
    what: "Open the item's details in the library instead",
  },
  {
    keys: "→",
    what: "Show the pages a full-text result matched on",
  },
  { keys: "←", what: "Fold those away again, or leave the list" },
  { keys: "Escape", what: "Leave the list, keeping what you typed" },
];

const LIBRARY: Shortcut[] = [
  { keys: "↑ / ↓", what: "Move the selection through the list" },
  { keys: "Enter", what: "Open the selected document's PDF" },
  { keys: "Ctrl / ⌘ + click a row", what: "Open that document's PDF" },
  {
    keys: "Drag a row onto a collection",
    what: "File it there, keeping the collections it is already in. Dragging a row that is part of the checked selection carries the whole selection",
  },
  {
    keys: "Click a page hit under a search result",
    what: "Open the PDF at that page with the find bar already filled in",
  },
];

const READER: Shortcut[] = [
  { keys: "→ / Page Down / Space", what: "Next page" },
  { keys: "← / Page Up", what: "Previous page" },
  { keys: "Backspace", what: "Back to the library, on the document you were reading" },
  { keys: "Ctrl / ⌘ + F", what: "Find in this PDF" },
  { keys: "Enter / Shift + Enter", what: "Next / previous match, while the find bar has focus" },
  { keys: "Escape", what: "Close the find bar" },
  {
    keys: "Hold Ctrl / ⌘",
    what: "Arm the PDF's own hyperlinks — they tint, and a click follows one. Held down so a drag across a cross-reference still selects text",
  },
];

function Section({ title, rows }: { title: string; rows: Shortcut[] }) {
  return (
    <>
      <h3
        className="mb-1 mt-4 text-xs uppercase tracking-widest first:mt-0"
        style={{ color: "var(--color-text-muted)" }}
      >
        {title}
      </h3>
      <dl className="mb-2">
        {rows.map((s) => (
          <div
            key={s.keys}
            className="flex flex-col gap-0.5 border-b py-1.5 last:border-b-0 sm:flex-row sm:gap-4"
            style={{ borderColor: "var(--color-border)" }}
          >
            <dt className="shrink-0 font-mono text-xs sm:w-56">{s.keys}</dt>
            <dd className="text-xs" style={{ color: "var(--color-text-muted)" }}>
              {s.what}
            </dd>
          </div>
        ))}
      </dl>
    </>
  );
}

export default function ShortcutsHelp() {
  const [open, setOpen] = useState(false);

  // Escape closes it, wherever the focus happens to be.
  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        aria-label="Keyboard shortcuts"
        title="Keyboard shortcuts"
        className="flex items-center rounded px-2 py-1 hover:opacity-80"
        style={{ color: "var(--color-text-muted)" }}
      >
        <Info className="h-4 w-4" />
      </button>
      {open &&
        createPortal(
          <div
            className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto p-4 sm:p-8"
            style={{ backgroundColor: "rgba(0,0,0,0.5)" }}
            onClick={() => setOpen(false)}
            role="presentation"
          >
            <div
              role="dialog"
              aria-modal="true"
              aria-label="Keyboard shortcuts"
              onClick={(e) => e.stopPropagation()}
              className="w-full max-w-2xl rounded border p-4 shadow-lg"
              style={{
                backgroundColor: "var(--color-surface)",
                borderColor: "var(--color-border)",
                color: "var(--color-text)",
              }}
            >
              <div className="mb-2 flex items-start justify-between">
                <h2 className="text-sm font-medium">Keyboard &amp; mouse</h2>
                <button
                  onClick={() => setOpen(false)}
                  aria-label="Close"
                  className="rounded p-0.5 hover:opacity-70"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>
              <Section title="Search" rows={SEARCH} />
              <Section title="Library" rows={LIBRARY} />
              <Section title="PDF reader" rows={READER} />
            </div>
          </div>,
          document.body,
        )}
    </>
  );
}
