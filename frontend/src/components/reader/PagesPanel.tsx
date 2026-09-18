import { useEffect, useRef, useState } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import type { PDFDocumentProxy } from "pdfjs-dist";
import { RenderQueue } from "@/lib/renderQueue";
import { PageThumbnails } from "@/lib/pageThumbnails";

/** Width of a thumbnail in CSS pixels. */
const THUMB_WIDTH = 132;

/** Height to assume before a page's real proportions are known. */
const ESTIMATE_THUMB_HEIGHT = 186;

const LABEL_HEIGHT = 20;

/**
 * The page list, as pdf.js's sidebar has it.
 *
 * Thumbnails are rendered through the same queue the reader's pages use
 * and at a deliberately worse priority, so a sidebar full of them can
 * never make the page being read wait. They are cached in the same
 * place the reader caches its own, which means opening this panel after
 * reading a while fills it instantly from pages already drawn.
 */
export function PagesPanel({
  doc,
  numPages,
  currentPage,
  onJumpTo,
  queue,
  thumbnails,
}: {
  doc: PDFDocumentProxy | null;
  numPages: number;
  currentPage: number;
  onJumpTo: (page: number) => void;
  queue: RenderQueue;
  thumbnails: PageThumbnails;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);

  const virtualizer = useVirtualizer({
    count: numPages,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ESTIMATE_THUMB_HEIGHT + LABEL_HEIGHT,
    overscan: 4,
  });

  // Follow the reader: the page being read should be the one in view
  // here too, or the panel is a list you have to search.
  useEffect(() => {
    if (currentPage >= 1) {
      virtualizer.scrollToIndex(currentPage - 1, { align: "auto" });
    }
    // Only when the reader moves, not when the virtualizer re-renders.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentPage]);

  return (
    <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto py-2">
      <div
        style={{
          height: `${virtualizer.getTotalSize()}px`,
          position: "relative",
          width: "100%",
        }}
      >
        {virtualizer.getVirtualItems().map((vi) => {
          const page = vi.index + 1;
          return (
            <div
              key={vi.key}
              style={{
                position: "absolute",
                top: 0,
                left: 0,
                width: "100%",
                transform: `translateY(${vi.start}px)`,
              }}
            >
              <Thumbnail
                doc={doc}
                page={page}
                active={page === currentPage}
                onJumpTo={onJumpTo}
                queue={queue}
                thumbnails={thumbnails}
              />
            </div>
          );
        })}
      </div>
    </div>
  );
}

function Thumbnail({
  doc,
  page,
  active,
  onJumpTo,
  queue,
  thumbnails,
}: {
  doc: PDFDocumentProxy | null;
  page: number;
  active: boolean;
  onJumpTo: (page: number) => void;
  queue: RenderQueue;
  thumbnails: PageThumbnails;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [height, setHeight] = useState(ESTIMATE_THUMB_HEIGHT);

  useEffect(() => {
    if (!doc) return;
    const canvas = canvasRef.current;
    if (!canvas) return;

    // A page already read has a thumbnail; show it rather than asking
    // pdfjs for what we have.
    if (thumbnails.paint(page, canvas)) {
      setHeight((canvas.height / Math.max(1, canvas.width)) * THUMB_WIDTH);
      return;
    }

    let cancelled = false;
    // Far worse than any real page, so the reader is never held up by
    // a panel it is not looking at.
    const unqueue = queue.push(
      `thumb-${page}`,
      () => 10_000 + page,
      async () => {
        if (cancelled) return;
        const pdfPage = await doc.getPage(page);
        try {
          if (cancelled) return;
          const base = pdfPage.getViewport({ scale: 1 });
          const scale = THUMB_WIDTH / base.width;
          const viewport = pdfPage.getViewport({ scale });
          const target = canvasRef.current;
          if (!target) return;
          target.width = Math.max(1, Math.round(viewport.width));
          target.height = Math.max(1, Math.round(viewport.height));
          const ctx = target.getContext("2d");
          if (!ctx) return;
          await pdfPage.render({
            canvasContext: ctx,
            viewport,
            canvas: target,
          }).promise;
          if (!cancelled) setHeight(viewport.height);
        } finally {
          pdfPage.cleanup();
        }
      },
    );
    return () => {
      cancelled = true;
      unqueue();
    };
  }, [doc, page, queue, thumbnails]);

  return (
    <button
      type="button"
      onClick={() => onJumpTo(page)}
      aria-label={`Page ${page}`}
      aria-current={active}
      className="flex w-full flex-col items-center gap-1 px-2 py-1"
    >
      <canvas
        ref={canvasRef}
        className="rounded border"
        style={{
          width: `${THUMB_WIDTH}px`,
          height: `${height}px`,
          // Paper, for the moment before it is drawn — the same reason
          // the reader's own pages are white.
          backgroundColor: "#ffffff",
          borderColor: active ? "var(--color-accent)" : "var(--color-border)",
          borderWidth: active ? 2 : 1,
        }}
      />
      <span
        className="text-[10px] tabular-nums"
        style={{
          color: active ? "var(--color-accent)" : "var(--color-text-muted)",
        }}
      >
        {page}
      </span>
    </button>
  );
}
