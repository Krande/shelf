/**
 * The scrolling list of bands.
 *
 * A band is a group of rows laid out together — one row everywhere
 * except wrapped scrolling, which fits as many across as the width
 * allows. Keeping one shape means the virtualizer, the scroll anchor
 * and the page indicator do not each need to know which mode is on.
 */

import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useLayoutEffect,
  useRef,
} from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import type { PDFDocumentProxy } from "pdfjs-dist";
import type { Annotation, Rect } from "@/api/annotations";
import { CanvasBudget } from "@/lib/canvasBudget";
import { RenderQueue } from "@/lib/renderQueue";
import { PageThumbnails } from "@/lib/pageThumbnails";
import { PageCanvas } from "./PageCanvas";
import {
  PAGE_GAP,
  SPREAD_GAP,
  type ContinuousListHandle,
  type NativeViewport,
  type ScrollAnchor,
} from "./types";

export const ContinuousList = forwardRef<
  ContinuousListHandle,
  {
    doc: PDFDocumentProxy;
    scrollRef: React.RefObject<HTMLDivElement | null>;
    estimateSize: (index: number) => number;
    bands: number[][][];
    horizontal: boolean;
    rowScaleFor: (pages: number[]) => number;
    pageNativeRef: React.RefObject<Map<number, NativeViewport>>;
    contentRef: React.RefObject<HTMLDivElement | null>;
    findQuery: string;
    currentMatchInfo: { page: number; occurrence: number } | null;
    annotationsByPage: Map<number, Annotation[]>;
    focusedAnnotationId: string | null;
    debugText: boolean;
    queue: RenderQueue;
    budget: CanvasBudget;
    thumbnails: PageThumbnails;
    tool: "highlight" | "text" | null;
    onCreateNote: (pageNumber: number, x: number, y: number) => void;
    pageRef: React.RefObject<number>;
    pinchScaleRef: React.RefObject<number>;
    lastScrolledTo: React.RefObject<string | null>;
    onCreateHighlight: (
      pageNumber: number,
      rects: Rect[],
      text: string,
    ) => void;
    onFollowLink: (page: number) => void;
    onVisiblePageChange: (page: number) => void;
  }
>(function ContinuousList(
  {
    doc,
    scrollRef,
    estimateSize,
    bands,
    horizontal,
    rowScaleFor,
    pageNativeRef,
    contentRef,
    findQuery,
    currentMatchInfo,
    annotationsByPage,
    focusedAnnotationId,
    debugText,
    queue,
    budget,
    thumbnails,
    tool,
    onCreateNote,
    pageRef,
    pinchScaleRef,
    lastScrolledTo,
    onCreateHighlight,
    onFollowLink,
    onVisiblePageChange,
  },
  ref,
) {
  const virtualizer = useVirtualizer({
    count: bands.length,
    getScrollElement: () => scrollRef.current,
    estimateSize,
    overscan: 3,
    horizontal,
  });

  // A page telling us it is a different size than we assumed. Rare
  // after the first read of a document, so re-measuring here is cheap;
  // leaving it wrong is not, since every page below it sits at the
  // wrong offset.
  // Zoom changes every row's height, and the virtualizer caches what it
  // measured at the old one.
  //
  // This is two effects on purpose. The virtualizer memoises its
  // measurements on deps that do not include estimateSize, so a new
  // scale does not invalidate them: only measure() does, and measure()
  // schedules a re-render rather than updating the DOM in place.
  // Restoring the reader's place in this same commit would therefore
  // read the offsets it is about to replace — writing back roughly the
  // scroll position already there, just before the document changes
  // height underneath it. Zooming out then drifted further down the
  // document with every step.
  useLayoutEffect(() => {
    virtualizer.measure();
    // pageScaleFor closes over zoom, so estimateSize changing identity
    // is the signal that the scale moved.
  }, [virtualizer, estimateSize]);

  // Where the reader is, kept up to date as they scroll: a row, and how
  // far into it the viewport's top sits. A proportion, so it still
  // means the same thing once that row is a different height.
  const anchorRef = useRef<ScrollAnchor | null>(null);
  // Set while we are the ones moving the scroll, so the scroll event
  // that follows does not overwrite the anchor with the position we
  // just derived from it.
  const restoringRef = useRef(false);

  // ...and the restore waits for the commit that carries the new
  // heights, which is the first one where the total size differs.
  const totalSize = virtualizer.getTotalSize();
  useLayoutEffect(() => {
    const anchor = anchorRef.current;
    if (!anchor) return;
    const el = scrollRef.current;
    const offset = virtualizer.getOffsetForIndex(anchor.index, "start");
    if (!el || !offset) return;
    const size = virtualizer.measurementsCache[anchor.index]?.size ?? 0;
    const target = offset[0] + anchor.within * size;
    // Sub-pixel corrections are not worth a scroll write, and writing
    // one would only invite the rounding to accumulate.
    if (Math.abs(el.scrollTop - target) < 1) return;
    restoringRef.current = true;
    el.scrollTop = target;
    requestAnimationFrame(() => {
      restoringRef.current = false;
    });
  }, [totalSize, virtualizer, scrollRef]);

  const applyNativeSize = useCallback(
    (page: number, size: NativeViewport) => {
      pageNativeRef.current.set(page, size);
      virtualizer.measure();
    },
    [pageNativeRef, virtualizer],
  );

  useImperativeHandle(
    ref,
    () => ({
      scrollToPage: (page: number) => {
        const at = bands.findIndex((band) =>
          band.some((row) => row.includes(page)),
        );
        virtualizer.scrollToIndex(at < 0 ? 0 : at, { align: "start" });
      },
    }),
    [virtualizer, scrollRef, bands],
  );

  const items = virtualizer.getVirtualItems();

  // Scroll-driven page indicator. Each scroll tick we pick the page
  // whose top edge has just passed the viewport's "current page"
  // line — a small offset below the top so a page only switches once
  // it's clearly the dominant one on screen. The previous build had
  // no listener here, so the page number was frozen at whatever the
  // toolbar / outline / URL last set it to.
  const lastReportedRef = useRef<number>(0);
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const compute = () => {
      const offset =
        virtualizer.scrollOffset ??
        (horizontal ? el.scrollLeft : el.scrollTop);
      const viewportH = horizontal ? el.clientWidth : el.clientHeight;
      // Threshold: a page becomes "current" once its top has scrolled
      // about a third of the viewport past the top edge.
      const line = offset + viewportH * 0.3;
      const visible = virtualizer.getVirtualItems();
      if (visible.length === 0) return;
      if (!restoringRef.current) {
        // The row the viewport's top edge is inside, for putting the
        // reader back after anything changes the layout's height.
        const top =
          visible.find((v) => v.start <= offset && v.end > offset) ??
          visible[0];
        anchorRef.current = {
          index: top.index,
          within: (offset - top.start) / Math.max(1, top.size),
        };
      }
      let pick = visible[0].index;
      for (const v of visible) {
        if (v.start <= line) pick = v.index;
        else break;
      }
      // The first page of the band, which is the one a reader would
      // name if asked where they are.
      const page = bands[pick]?.[0]?.[0] ?? pick + 1;
      if (page !== lastReportedRef.current) {
        lastReportedRef.current = page;
        onVisiblePageChange(page);
      }
    };
    // No initial compute() — at mount scrollTop=0 would always
    // resolve to page 1, which would clobber a deep-link ?page=N
    // before the URL-jump effect has scrolled to N.
    el.addEventListener("scroll", compute, { passive: true });
    return () => el.removeEventListener("scroll", compute);
  }, [scrollRef, virtualizer, onVisiblePageChange, bands]);

  return (
    <div
      ref={contentRef}
      style={{
        // fit-content, not 100%: at a zoom past fit-width the pages are
        // wider than the viewport, and a 100% box would clip them
        // instead of giving the container something to scroll to.
        width: horizontal ? `${virtualizer.getTotalSize()}px` : "fit-content",
        minWidth: horizontal ? undefined : "100%",
        position: "relative",
        height: horizontal ? "100%" : `${virtualizer.getTotalSize()}px`,
      }}
    >
      {items.map((vi) => {
        const band = bands[vi.index] ?? [];
        return (
          <div
            key={vi.key}
            data-index={vi.index}
            style={{
              position: "absolute",
              top: 0,
              left: 0,
              width: horizontal ? undefined : "100%",
              height: horizontal ? "100%" : undefined,
              transform: horizontal
                ? `translateX(${vi.start}px)`
                : `translateY(${vi.start}px)`,
              paddingBottom: horizontal ? undefined : `${PAGE_GAP}px`,
              paddingRight: horizontal ? `${PAGE_GAP}px` : undefined,
              display: "flex",
              gap: `${SPREAD_GAP}px`,
              // Top, so the two pages of a spread share a baseline even
              // when one is taller.
              alignItems: "flex-start",
              // `safe`: centred while it fits, start-aligned when it
              // does not. Plain centring pushes the inline-start
              // overflow outside the scrollable region, so at any zoom
              // past fit the left of every page was unreachable.
              justifyContent: "safe center",
            }}
          >
            {band.flatMap((row) =>
              row.map((pageNumber) => (
              <PageCanvas
                key={pageNumber}
                doc={doc}
                pageNumber={pageNumber}
                renderScale={rowScaleFor(row)}
                native={pageNativeRef.current.get(pageNumber)}
                findQuery={findQuery}
                currentOccurrence={
                  currentMatchInfo && currentMatchInfo.page === pageNumber
                    ? currentMatchInfo.occurrence
                    : null
                }
                annotations={annotationsByPage.get(pageNumber) ?? []}
                focusedAnnotationId={focusedAnnotationId}
                debugText={debugText}
                onNativeSize={applyNativeSize}
                queue={queue}
                budget={budget}
                thumbnails={thumbnails}
                tool={tool}
                onCreateNote={onCreateNote}
                pageRef={pageRef}
                pinchScaleRef={pinchScaleRef}
                lastScrolledTo={lastScrolledTo}
                onCreateHighlight={onCreateHighlight}
                onFollowLink={onFollowLink}
              />
              )),
            )}
          </div>
        );
      })}
    </div>
  );
});
