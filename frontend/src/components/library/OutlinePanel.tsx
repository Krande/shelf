import { useEffect, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import type { PDFDocumentProxy } from "pdfjs-dist";
import { destToPage, type PdfDest } from "@/lib/pdfLinks";

interface OutlineNode {
  title: string;
  dest: PdfDest;
  items: OutlineNode[];
}

/**
 * Loads a PDF's outline and renders it.
 *
 * The drawer around it lives in the reader's sidebar, which shows this
 * alongside pages, attachments and layers.
 */
export default function OutlinePanel({
  doc,
  onJumpTo,
}: {
  doc: PDFDocumentProxy | null;
  /** Called with a 1-based page number when the user picks an entry. */
  onJumpTo: (page: number) => void;
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
    <OutlineBody outline={outline} doc={doc} onJumpTo={onJumpTo} />
  );
}

/**
 * Just the tree, without the drawer around it.
 *
 * The reader's sidebar supplies its own frame — a header with tabs and
 * a resize handle — so the content has to be usable on its own.
 */
export function OutlineBody({
  outline,
  doc,
  onJumpTo,
}: {
  outline: OutlineNode[] | null | undefined;
  doc: PDFDocumentProxy | null;
  onJumpTo: (page: number) => void;
}) {
  if (outline === undefined) {
    return <OutlineNote>Loading…</OutlineNote>;
  }
  if (outline === null || outline.length === 0) {
    return <OutlineNote>This PDF has no embedded outline.</OutlineNote>;
  }
  return (
    <div className="min-h-0 flex-1 overflow-y-auto py-1">
      <OutlineList nodes={outline} doc={doc!} onJumpTo={onJumpTo} depth={0} />
    </div>
  );
}

function OutlineNote({ children }: { children: React.ReactNode }) {
  return (
    <div
      className="px-3 py-6 text-center text-xs"
      style={{ color: "var(--color-text-muted)" }}
    >
      {children}
    </div>
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
