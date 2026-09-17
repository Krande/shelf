import { useEffect, useState } from "react";
import { ChevronDown, ChevronRight, X } from "lucide-react";
import type { PDFDocumentProxy } from "pdfjs-dist";
import { destToPage, type PdfDest } from "@/lib/pdfLinks";

interface OutlineNode {
  title: string;
  dest: PdfDest;
  items: OutlineNode[];
}

export default function OutlinePanel({
  doc,
  onJumpTo,
  onClose,
}: {
  doc: PDFDocumentProxy | null;
  /** Called with a 1-based page number when the user picks an entry. */
  onJumpTo: (page: number) => void;
  onClose: () => void;
}) {
  const [outline, setOutline] = useState<OutlineNode[] | null | undefined>(
    undefined,
  );
  // undefined = loading, null = none, [] = empty.

  useEffect(() => {
    if (!doc) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await doc.getOutline();
        if (!cancelled) setOutline((res as OutlineNode[]) ?? null);
      } catch {
        if (!cancelled) setOutline(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [doc]);

  return (
    <aside
      className="flex w-full flex-col border-l sm:w-72"
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
          Outline
        </span>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close outline"
          className="rounded p-1 hover:opacity-70"
          style={{ color: "var(--color-text-muted)" }}
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
      {outline === undefined ? (
        <div
          className="px-3 py-6 text-center text-xs"
          style={{ color: "var(--color-text-muted)" }}
        >
          Loading…
        </div>
      ) : outline === null || outline.length === 0 ? (
        <div
          className="px-3 py-6 text-center text-xs"
          style={{ color: "var(--color-text-muted)" }}
        >
          This PDF has no embedded outline.
        </div>
      ) : (
        <div className="min-h-0 flex-1 overflow-y-auto py-1">
          <OutlineList nodes={outline} doc={doc!} onJumpTo={onJumpTo} depth={0} />
        </div>
      )}
    </aside>
  );
}

function OutlineList({
  nodes,
  doc,
  onJumpTo,
  depth,
}: {
  nodes: OutlineNode[];
  doc: PDFDocumentProxy;
  onJumpTo: (page: number) => void;
  depth: number;
}) {
  return (
    <ul>
      {nodes.map((n, i) => (
        <OutlineEntry
          key={`${depth}-${i}-${n.title}`}
          node={n}
          doc={doc}
          onJumpTo={onJumpTo}
          depth={depth}
        />
      ))}
    </ul>
  );
}

function OutlineEntry({
  node,
  doc,
  onJumpTo,
  depth,
}: {
  node: OutlineNode;
  doc: PDFDocumentProxy;
  onJumpTo: (page: number) => void;
  depth: number;
}) {
  const hasChildren = node.items && node.items.length > 0;
  // Nested headings collapsed by default beyond the first level so a
  // long outline doesn't overwhelm the panel; click the chevron to
  // open. Top-level entries stay expanded for at-a-glance scanning.
  const [open, setOpen] = useState(depth < 1);

  async function jump() {
    const page = await destToPage(doc, node.dest);
    if (page != null) onJumpTo(page);
  }

  return (
    <li>
      <div
        className="flex items-start gap-1 px-2 py-1 hover:opacity-80"
        style={{ paddingLeft: `${8 + depth * 12}px` }}
      >
        {hasChildren ? (
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            aria-label={open ? "Collapse" : "Expand"}
            className="mt-0.5 shrink-0 rounded hover:opacity-70"
            style={{ color: "var(--color-text-muted)" }}
          >
            {open ? (
              <ChevronDown className="h-3.5 w-3.5" />
            ) : (
              <ChevronRight className="h-3.5 w-3.5" />
            )}
          </button>
        ) : (
          <span className="mt-0.5 inline-block w-3.5 shrink-0" />
        )}
        <button
          type="button"
          onClick={jump}
          className="min-w-0 flex-1 text-left text-xs hover:underline"
          style={{ color: "var(--color-text)" }}
          title={node.title}
        >
          {node.title || "(untitled)"}
        </button>
      </div>
      {hasChildren && open && (
        <OutlineList
          nodes={node.items}
          doc={doc}
          onJumpTo={onJumpTo}
          depth={depth + 1}
        />
      )}
    </li>
  );
}
