import { useState } from "react";
import { Images, Layers, ListTree, Paperclip, X } from "lucide-react";
import type { PDFDocumentProxy } from "pdfjs-dist";
import OutlinePanel from "@/components/library/OutlinePanel";
import { useResizableWidth } from "@/hooks/useResizableWidth";
import { RenderQueue } from "@/lib/renderQueue";
import { PageThumbnails } from "@/lib/pageThumbnails";
import { PagesPanel } from "./PagesPanel";
import { AttachmentsPanel } from "./AttachmentsPanel";
import { LayersPanel, type LayerGroup } from "./LayersPanel";

type Tab = "outline" | "pages" | "attachments" | "layers";

const TABS: { id: Tab; label: string; Icon: typeof ListTree }[] = [
  { id: "outline", label: "Outline", Icon: ListTree },
  { id: "pages", label: "Pages", Icon: Images },
  { id: "attachments", label: "Attachments", Icon: Paperclip },
  { id: "layers", label: "Layers", Icon: Layers },
];

/**
 * The reader's side drawer: the same four tabs pdf.js's sidebar has.
 *
 * Outline leads because it is the one that answers "where in this
 * document is the thing I want" — pages answer "what does it look
 * like", which is a different question and a rarer one.
 */
export function ReaderSidebar({
  doc,
  numPages,
  currentPage,
  onJumpTo,
  onClose,
  queue,
  thumbnails,
  layers,
  onToggleLayer,
}: {
  doc: PDFDocumentProxy | null;
  numPages: number;
  currentPage: number;
  onJumpTo: (page: number) => void;
  onClose: () => void;
  queue: RenderQueue;
  thumbnails: PageThumbnails;
  layers: LayerGroup[] | undefined;
  onToggleLayer: (id: string, visible: boolean) => void;
}) {
  const [tab, setTab] = useState<Tab>("outline");
  // `end`: the drawer is to the handle's left, so dragging right widens
  // it. Its own key, so it is remembered separately from the library's
  // detail pane.
  const size = useResizableWidth("shelf.readerSidebarWidth", {
    min: 180,
    maxFraction: 0.5,
    edge: "end",
  });

  return (
    <aside
      className="relative flex w-full flex-col border-r sm:w-72"
      style={{
        borderColor: "var(--color-border)",
        backgroundColor: "var(--color-surface)",
        width: size.width != null ? `${size.width}px` : undefined,
        flexShrink: 0,
      }}
    >
      <div
        className="flex items-center gap-0.5 border-b px-1 py-1"
        style={{ borderColor: "var(--color-border)" }}
      >
        {TABS.map(({ id, label, Icon }) => (
          <button
            key={id}
            type="button"
            onClick={() => setTab(id)}
            aria-label={label}
            aria-pressed={tab === id}
            title={label}
            className="flex-1 rounded p-1.5 hover:opacity-80"
            style={{
              color:
                tab === id ? "var(--color-accent)" : "var(--color-text-muted)",
              backgroundColor:
                tab === id
                  ? "color-mix(in srgb, var(--color-accent) 12%, transparent)"
                  : "transparent",
            }}
          >
            <Icon className="mx-auto h-4 w-4" />
          </button>
        ))}
        <button
          type="button"
          onClick={onClose}
          aria-label="Close sidebar"
          className="rounded p-1.5 hover:opacity-70"
          style={{ color: "var(--color-text-muted)" }}
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>

      {tab === "outline" && <OutlinePanel doc={doc} onJumpTo={onJumpTo} />}
      {tab === "pages" && (
        <PagesPanel
          doc={doc}
          numPages={numPages}
          currentPage={currentPage}
          onJumpTo={onJumpTo}
          queue={queue}
          thumbnails={thumbnails}
        />
      )}
      {tab === "attachments" && <AttachmentsPanel doc={doc} />}
      {tab === "layers" && (
        <LayersPanel layers={layers} onToggle={onToggleLayer} />
      )}

      {/* The drag handle, on the drawer's own edge. Focusable, so the
          arrow keys nudge it and Home puts it back. */}
      <div
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize sidebar"
        tabIndex={0}
        onPointerDown={size.onPointerDown}
        onKeyDown={size.onKeyDown}
        onDoubleClick={size.reset}
        className="absolute inset-y-0 right-0 hidden w-1.5 cursor-col-resize sm:block"
        style={{
          backgroundColor: size.dragging
            ? "var(--color-accent)"
            : "transparent",
        }}
      />
    </aside>
  );
}
