/**
 * One rendered page: its canvas, its text layer, and the overlays that
 * sit on top of them.
 *
 * Split out of ReaderPage, which had grown past the point where an edit
 * could be anchored reliably. Nothing here changed in the move.
 */

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Loader2 } from "lucide-react";
import type { PDFPageProxy, PDFDocumentProxy, RenderTask } from "pdfjs-dist";
import { pdfjs } from "@/api/pdfWorkerSetup";
import type { Annotation, Rect } from "@/api/annotations";
import { pageLinks, type PageLink } from "@/lib/pdfLinks";
import { CanvasBudget, clampPixelMultiplier } from "@/lib/canvasBudget";
import { RenderQueue } from "@/lib/renderQueue";
import { PageThumbnails } from "@/lib/pageThumbnails";
import type { NativeViewport, OcConfig } from "./types";

export function PageCanvas({
  doc,
  pageNumber,
  renderScale,
  native,
  findQuery,
  currentOccurrence,
  annotations,
  focusedAnnotationId,
  debugText,
  queue,
  budget,
  thumbnails,
  ocConfigRef,
  layerVersion,
  tool,
  onCreateNote,
  pageRef,
  pinchScaleRef,
  lastScrolledTo,
  onCreateHighlight,
  onFollowLink,
  onNativeSize,
}: {
  doc: PDFDocumentProxy;
  pageNumber: number;
  renderScale: number;
  native?: NativeViewport;
  findQuery: string;
  currentOccurrence: number | null;
  annotations: Annotation[];
  focusedAnnotationId: string | null;
  debugText: boolean;
  /** Serialises rendering across pages. */
  queue: RenderQueue;
  /** Shared pixel ceiling across every rendered page. */
  budget: CanvasBudget;
  /** Small bitmaps of pages already seen, to fill a page instantly. */
  thumbnails: PageThumbnails;
  /** Which of the PDF's layers are drawn. Mutated in place by pdfjs, so
   *  `layerVersion` is what says it changed. */
  ocConfigRef: React.RefObject<OcConfig | null>;
  layerVersion: number;
  /** The tool in hand, or null while reading. */
  tool: "highlight" | "text" | null;
  /** Leave a note at a point on a page, in PDF user-space. */
  onCreateNote: (pageNumber: number, x: number, y: number) => void;
  /** The page in view, for queue priority. */
  pageRef: React.RefObject<number>;
  pinchScaleRef: React.RefObject<number>;
  /** Which match the reader last scrolled to, shared across pages: a
   *  virtualized list remounts them constantly, and a per-page ref
   *  would forget and scroll again on every remount. */
  lastScrolledTo: React.RefObject<string | null>;
  onCreateHighlight: (
    pageNumber: number,
    rects: Rect[],
    text: string,
  ) => void;
  /** Follow an internal link — scroll the reader to that page. */
  onFollowLink: (page: number) => void;
  /** The size pdfjs actually lays this page out at, reported once it is
   *  known. The estimate it replaces comes from the server, which may
   *  predate the CropBox/rotation fix, or from page 1 standing in for
   *  a document of mixed sizes. */
  onNativeSize?: (page: number, size: NativeViewport) => void;
}) {
  const wrapperRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const textLayerRef = useRef<HTMLDivElement>(null);
  const [textLayerVersion, setTextLayerVersion] = useState(0);
  // An unpainted canvas is a blank box. Tracked so the placeholder can
  // say which page it is and that it is coming.
  const [painted, setPainted] = useState(false);

  // Whether anything is actually drawn. Not canvas.width: a canvas
  // that has never been drawn to is 300x150, not 0, so its dimensions
  // cannot answer this.
  const hasPixels = useRef(false);

  // Before any render is queued, and synchronously with the mount so
  // there is no frame in which the page is empty.
  useLayoutEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || hasPixels.current) return;
    if (thumbnails.paint(pageNumber, canvas)) {
      hasPixels.current = true;
      setPainted(true);
    }
  }, [pageNumber, thumbnails]);
  // What is currently drawn. A re-run of the render effect that would
  // produce the same pixels is skipped: the list starting and stopping
  // is not a reason to redraw the page.
  const drawnKey = useRef<string | null>(null);

  const cssW = native ? native.width * renderScale : undefined;
  const cssH = native ? native.height * renderScale : undefined;

  // Pending selection for the "Highlight" floating button. Rects are
  // in *layout* coords within the wrapper (un-pinched, un-rendered)
  // so the button can position itself with the same coord system as
  // the wrapper. Conversion to PDF user-space happens on commit.
  const [pendingHighlight, setPendingHighlight] = useState<{
    layoutRects: { x: number; y: number; w: number; h: number }[];
    pdfRects: Rect[];
    text: string;
  } | null>(null);

  // The PDF's own hyperlinks on this page. Independent of the canvas
  // render effect because it doesn't depend on scale — the rects come
  // back in PDF user-space and the overlay scales them itself, so a
  // zoom change re-lays-out the same links instead of re-reading them.
  const [links, setLinks] = useState<PageLink[]>([]);
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const pdfPage = await doc.getPage(pageNumber);
      try {
        if (cancelled) return;
        const found = await pageLinks(doc, pdfPage);
        if (!cancelled) setLinks(found);
      } finally {
        // Always, including the cancelled path. getPage resolves after
        // the cleanup has run, so without this every page scrolled past
        // during a drag keeps its worker-side resources.
        pdfPage.cleanup();
      }
    })().catch(() => {
      // A page whose annotations won't parse just has no links.
    });
    return () => {
      cancelled = true;
    };
  }, [doc, pageNumber]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const wrapper = wrapperRef.current;
    const textLayer = textLayerRef.current;
    if (!canvas || !wrapper || !textLayer) return;
    if (renderScale <= 0) return;

    let cancelled = false;
    let task: RenderTask | null = null;
    let pdfPage: PDFPageProxy | null = null;
    let textLayerTask: { cancel: () => void } | null = null;

    const unqueue = queue.push(
      `page-${pageNumber}`,
      // Distance from what is on screen, recomputed each time the queue
      // picks its next job.
      () => Math.abs(pageNumber - pageRef.current),
      async () => {
      if (cancelled) return;
      const wantKey = `${pageNumber}@${renderScale.toFixed(4)}@${layerVersion}`;
      if (drawnKey.current === wantKey) return;
      // getPage resolves after the cleanup has run, so the cleanup's
      // own pdfPage?.cleanup() sees null. Released here instead.
      const opened = await doc.getPage(pageNumber);
      pdfPage = opened;
      try {
      if (cancelled) return;
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      // pixelMultiplier puts more detail in the canvas's pixel
      // buffer than its CSS box demands. dpr handles HiDPI;
      // oversample handles "user has pinch-zoomed and now wants
      // crisp text". The CSS box stays at renderScale × native, so
      // the wrapper layout (and thus the virtualizer geometry) is
      // unaffected.
      //
      // Clamped, because all three multiply: renderScale is the
      // fit-width scale (~2 on a desktop container), dpr is up to 2,
      // and oversample up to MAX_OVERSAMPLE. Unclamped that reached
      // canvases of hundreds of megapixels -- past what a browser will
      // back, and a canvas it cannot back hands you a valid context
      // and paints nothing, which is the black page.
      // What pdfjs will actually lay this page out at. Compared
      // against the estimate driving the virtualizer, and reported when
      // they disagree by more than a rounding error -- a wrong estimate
      // means the scrollbar does not match the document.
      const trueNative = pdfPage.getViewport({ scale: 1 });
      if (
        onNativeSize &&
        (!native ||
          Math.abs(native.width - trueNative.width) > 1 ||
          Math.abs(native.height - trueNative.height) > 1)
      ) {
        onNativeSize(pageNumber, {
          width: trueNative.width,
          height: trueNative.height,
        });
      }

      const base = pdfPage.getViewport({ scale: renderScale });
      const budgetKey = `page-${pageNumber}`;
      const pixelMultiplier = budget.allow(
        budgetKey,
        clampPixelMultiplier(dpr, base.width, base.height),
        base.width,
        base.height,
      );
      const viewport = pdfPage.getViewport({
        scale: renderScale * pixelMultiplier,
      });

      // Drawn offscreen, then swapped in. Resizing a canvas clears it,
      // so rendering straight to the visible one blanks the page for
      // the length of the render -- which on a zoom step is a flash on
      // every page at once. Renders are serialised by the queue, so the
      // extra allocation is one canvas at a time rather than one per
      // mounted page, which is what made this too expensive before.
      const offscreen = document.createElement("canvas");
      offscreen.width = viewport.width;
      offscreen.height = viewport.height;
      const offCtx = offscreen.getContext("2d");
      if (!offCtx) return;
      task = pdfPage.render({
        canvasContext: offCtx,
        viewport,
        canvas: offscreen,
        // Which layers are drawn. Omitted, pdfjs uses the document's
        // own defaults, so a page rendered before a toggle would keep
        // showing what was turned off.
        ...(ocConfigRef.current
          ? {
              optionalContentConfigPromise: Promise.resolve(
                ocConfigRef.current,
              ),
            }
          : {}),
      });

      // Nothing on this page yet: show it building rather than holding
      // a blank until the whole page is done. pdfjs calls onContinue
      // between chunks, and its own viewer paints partial results at
      // exactly this point. A re-render is deliberately excluded --
      // there the page already has pixels, and replacing them with a
      // half-drawn page would be a downgrade, so those swap in one go
      // when the render completes.
      // Only when there is genuinely nothing on the page. With a
      // thumbnail up, replacing it with a half-drawn page is a
      // downgrade -- better to wait and swap the finished render in.
      const firstPaint = drawnKey.current === null && !hasPixels.current;
      if (firstPaint) {
        canvas.width = viewport.width;
        canvas.height = viewport.height;
        let lastShown = 0;
        task.onContinue = (cont: () => void) => {
          // Throttled: a partial blit per chunk on a dense page is its
          // own cost, and pdfjs paces its own at half a second.
          const now = Date.now();
          if (now - lastShown > 150) {
            lastShown = now;
            canvas.getContext("2d")?.drawImage(offscreen, 0, 0);
            hasPixels.current = true;
            setPainted(true);
          }
          cont();
        };
      }
      try {
        await task.promise;
      } catch (e) {
        if ((e as Error).name !== "RenderingCancelledException") throw e;
        return;
      } finally {
        if (cancelled) {
          // Let the abandoned buffer go now rather than at GC's leisure.
          offscreen.width = 0;
          offscreen.height = 0;
        }
      }
      if (cancelled) return;

      canvas.width = viewport.width;
      canvas.height = viewport.height;
      canvas.getContext("2d")?.drawImage(offscreen, 0, 0);
      hasPixels.current = true;
      thumbnails.store(pageNumber, offscreen);
      offscreen.width = 0;
      offscreen.height = 0;
      drawnKey.current = wantKey;
      budget.set(budgetKey, canvas.width * canvas.height);
      setPainted(true);

      // pdfjs reads `--total-scale-factor` from the container (or
      // an ancestor) when computing per-span font-size + transforms.
      // Without it, every span defaults to scale=1 and per-character
      // positions don't line up with the rendered glyphs — that was
      // the source of the find-highlight offset.
      textLayer.replaceChildren();
      const cssViewport = pdfPage.getViewport({ scale: renderScale });
      textLayer.style.setProperty(
        "--total-scale-factor",
        String(cssViewport.scale),
      );
      const layer = new pdfjs.TextLayer({
        textContentSource: pdfPage.streamTextContent(),
        container: textLayer,
        viewport: cssViewport,
      });
      textLayerTask = layer;
      try {
        await layer.render();
        if (!cancelled) {
          // Only now is this page really drawn. Claiming it after the
          // canvas but before the text layer meant a re-run in between
          // was skipped by the guard, leaving the page with no text
          // layer at all -- no selection, no find highlights, and no
          // sign anything was wrong.
          drawnKey.current = wantKey;
          setTextLayerVersion((v) => v + 1);
        }
      } catch {
        // selection layer is best-effort
      }
      } finally {
        opened.cleanup();
        pdfPage = null;
      }
      },
    );

    return () => {
      cancelled = true;
      unqueue();
      task?.cancel();
      // Otherwise streamTextContent keeps flowing for a page nobody is
      // looking at any more.
      textLayerTask?.cancel();
      pdfPage?.cleanup();
    };
  }, [
    doc,
    pageNumber,
    renderScale,
    native,
    layerVersion,
    ocConfigRef,
    queue,
    budget,
    thumbnails,
    pageRef,
  ]);

  // Release the canvas when the page really goes away. Deliberately not
  // in the render effect's cleanup: that runs whenever its inputs
  // change, including when the list starts moving, and zeroing a canvas
  // there made every visible page blink on every scroll.
  useEffect(() => {
    const canvas = canvasRef.current;
    const key = `page-${pageNumber}`;
    return () => {
      budget.release(key);
      hasPixels.current = false;
      if (canvas) {
        canvas.width = 0;
        canvas.height = 0;
      }
    };
  }, [pageNumber, budget]);

  // Capture the user's text selection. Listens at the document
  // level for `selectionchange` (debounced ~180ms) so we catch the
  // selection no matter where the user's finger ends up — Android's
  // selection handles fire pointer events on the document body, not
  // on the page wrapper, so the older wrapper-only `pointerup`
  // listener missed most mobile selections. Debouncing means the
  // floating button only appears after the selection settles, not
  // during handle drag. The pending state drives the floating
  // "Highlight" button; clicking it commits via onCreateHighlight.
  useEffect(() => {
    const wrapper = wrapperRef.current;
    if (!wrapper || !native) return;

    function check(): void {
      if (!wrapper) return;
      const sel = window.getSelection();
      if (!sel || sel.isCollapsed || sel.rangeCount === 0) {
        setPendingHighlight(null);
        return;
      }
      const range = sel.getRangeAt(0);
      // Only capture selections that are entirely within this page.
      if (!wrapper.contains(range.commonAncestorContainer)) {
        setPendingHighlight(null);
        return;
      }
      const rects = Array.from(range.getClientRects());
      if (rects.length === 0) {
        setPendingHighlight(null);
        return;
      }

      const wrapperRect = wrapper.getBoundingClientRect();
      const s = pinchScaleRef.current || 1;
      const layoutRects = rects.map((r) => ({
        x: (r.left - wrapperRect.left) / s,
        y: (r.top - wrapperRect.top) / s,
        w: r.width / s,
        h: r.height / s,
      }));
      // PDF user-space: divide by renderScale to undo the canvas
      // scale, flip y so origin is bottom-left.
      const nativeH = native!.height;
      const pdfRects: Rect[] = layoutRects.map((r) => {
        const pdfX = r.x / renderScale;
        const pdfH = r.h / renderScale;
        const pdfW = r.w / renderScale;
        const pdfY = nativeH - r.y / renderScale - pdfH;
        return [pdfX, pdfY, pdfW, pdfH];
      });
      if (tool === "highlight") {
        // The tool is the confirmation. Clear the selection so the same
        // passage is not marked twice by a stray re-fire.
        sel.removeAllRanges();
        setPendingHighlight(null);
        onCreateHighlight(pageNumber, pdfRects, sel.toString());
        return;
      }
      setPendingHighlight({
        layoutRects,
        pdfRects,
        text: sel.toString(),
      });
    }

    let timer: number | null = null;
    function schedule(): void {
      if (timer != null) window.clearTimeout(timer);
      // 180ms is short enough to feel responsive after a release
      // but long enough to skip the per-character storm while the
      // user is actively dragging a selection handle on Android.
      timer = window.setTimeout(check, 180);
    }

    document.addEventListener("selectionchange", schedule);
    // pointerup/touchend at the document level catches the case
    // where the selection's final state is established by a
    // gesture-end without a trailing selectionchange (e.g. tap to
    // collapse the selection, or release after handle drag).
    document.addEventListener("pointerup", schedule);
    document.addEventListener("touchend", schedule);
    return () => {
      if (timer != null) window.clearTimeout(timer);
      document.removeEventListener("selectionchange", schedule);
      document.removeEventListener("pointerup", schedule);
      document.removeEventListener("touchend", schedule);
    };
  }, [native, renderScale, pinchScaleRef, tool, pageNumber, onCreateHighlight]);

  // Per-occurrence find highlight via DOM Range geometry. The
  // earlier "wrap matches in <mark>" approach was double-broken on
  // pdfjs text layers — both size and position. pdfjs renders each
  // text item as one span using a *system* font, then applies a CSS
  // scale transform so the span's overall width matches the canvas
  // glyph width. Per-character positions inside the span don't map
  // to the real glyph positions; the system font has its own
  // metrics. So a <mark> at characters N..M sat at character-flow
  // positions of a different font, scaled by the span's transform —
  // visually misaligned and mis-sized.
  // Range.getClientRects on the text node returns the *actual*
  // rendered DOM rectangles, transformed by the browser; same
  // geometry as the rendered glyphs. We draw absolute-positioned
  // overlay divs at those rects. Same approach pdfjs's own viewer
  // uses.
  useEffect(() => {
    const textLayer = textLayerRef.current;
    if (!textLayer) return;

    // Drop any prior overlay divs from the previous query so we
    // start clean.
    textLayer
      .querySelectorAll(".shelf-find-rect")
      .forEach((el) => el.remove());

    const needle = findQuery.trim();
    if (!needle || textLayerVersion === 0) {
      // Nothing highlighted, so the next match to be drawn is worth
      // scrolling to even if it is the one we scrolled to last time --
      // searching the same word again should still take you there.
      lastScrolledTo.current = null;
      return;
    }

    const lowerNeedle = needle.toLowerCase();
    const spans = Array.from(
      textLayer.querySelectorAll<HTMLSpanElement>("span"),
    );
    const layerRect = textLayer.getBoundingClientRect();

    let occ = 0;
    let currentEl: HTMLElement | null = null;

    for (const span of spans) {
      const text = span.textContent ?? "";
      const lowerText = text.toLowerCase();
      const node = span.firstChild;
      if (!node || node.nodeType !== Node.TEXT_NODE) continue;

      let from = 0;
      while (true) {
        const idx = lowerText.indexOf(lowerNeedle, from);
        if (idx === -1) break;
        const end = idx + lowerNeedle.length;

        const range = document.createRange();
        try {
          range.setStart(node, idx);
          range.setEnd(node, end);
        } catch {
          from = end;
          occ += 1;
          continue;
        }

        const isCurrent = occ === currentOccurrence;
        // getClientRects returns *visual* (post-transform) viewport
        // coords. The new overlay div lives inside the textLayer,
        // which is itself inside the pinch wrapper's CSS scale
        // transform — anything we set via style.left gets multiplied
        // by the pinch scale on render. Divide the visual diff by
        // the current pinch.scale so the *layout-coord* placement
        // composes back into the right visual position. The pinch
        // hook then transforms the divs along with the glyphs, so
        // they stay aligned through subsequent pinch changes
        // without re-running this effect every frame.
        const s = pinchScaleRef.current || 1;
        for (const rect of Array.from(range.getClientRects())) {
          const div = document.createElement("div");
          div.className = isCurrent
            ? "shelf-find-rect shelf-find-current"
            : "shelf-find-rect shelf-find-match";
          div.style.left = `${(rect.left - layerRect.left) / s}px`;
          div.style.top = `${(rect.top - layerRect.top) / s}px`;
          div.style.width = `${rect.width / s}px`;
          div.style.height = `${rect.height / s}px`;
          textLayer.appendChild(div);
          if (isCurrent && !currentEl) currentEl = div;
        }

        from = end;
        occ += 1;
      }
    }

    // Only when the target actually moved. This effect also re-runs
    // whenever the text layer is rebuilt, which happens on any change
    // of render scale -- and closing the find bar resizes the scroll
    // container, so it re-rendered every page and then scrolled back to
    // the match the user had just finished with. Escaping out of a
    // search should leave you where you are reading.
    const target = `${needle}:${currentOccurrence}`;
    if (currentEl && lastScrolledTo.current !== target) {
      lastScrolledTo.current = target;
      currentEl.scrollIntoView({ behavior: "smooth", block: "center" });
    }
    // pinchScaleRef is read inside the loop above. Listing it here
    // would be a no-op since refs don't drive re-runs, but the
    // closure does need to capture the ref; depending on it costs
    // nothing.
  }, [findQuery, currentOccurrence, textLayerVersion, pinchScaleRef]);

  return (
    <div
      ref={wrapperRef}
      onClick={(e) => {
        if (tool !== "text" || !native) return;
        const rect = e.currentTarget.getBoundingClientRect();
        // Into PDF user-space: undo the render scale, and flip the
        // y-axis, which runs up from the bottom of the page.
        const x = (e.clientX - rect.left) / renderScale;
        const y = native.height - (e.clientY - rect.top) / renderScale;
        onCreateNote(pageNumber, x, y);
      }}
      className="relative rounded border shadow-md"
      style={{
        borderColor: "var(--color-border)",
        // Paper, not the app's surface colour. A page that has not been
        // drawn yet is a blank canvas, and on a dark theme the surface
        // behind it reads as a black hole -- which is what a zoom out
        // looks like, since it brings more pages into view at once than
        // the queue can have drawn. White is also what the page is
        // about to be, so the fill stops being a state of its own.
        backgroundColor: "#ffffff",
        display: "inline-block",
        cursor: tool === "text" ? "crosshair" : undefined,
        // A flex item shrinks by default. Past 100% the page is wider
        // than its row, so it was being squeezed horizontally while its
        // explicit height stayed -- the page came out stretched
        // vertically. It must keep the size its scale gives it and let
        // the row overflow, which is what the container scrolls to.
        flexShrink: 0,
        width: cssW != null ? `${cssW}px` : undefined,
        height: cssH != null ? `${cssH}px` : undefined,
      }}
    >
      {/* h-full w-full, not intrinsic size: the wrapper is sized
          from renderScale, so when the scale changes the pixels
          already drawn stretch to the new box immediately and are
          replaced by a crisp render when the queue gets to it.
          Without this the canvas kept its old size while its box
          changed, which is a visible jump on every zoom step. */}
      <canvas ref={canvasRef} className="absolute inset-0 h-full w-full" />
      {!painted && (
        <div
          className="pointer-events-none absolute inset-0 flex items-center justify-center gap-2 text-xs"
          // Against the paper fill above, not the app's surface.
          style={{ color: "#9a9a9a" }}
        >
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
          Page {pageNumber}
        </div>
      )}
      {/*
        Both pdfjs's `.textLayer` styles (font/transform vars,
        per-span sizing) and our shelf-specific positioning kick in
        from this composite class.
      */}
      <div
        ref={textLayerRef}
        className={`textLayer shelf-textlayer${
          debugText ? " shelf-debug-text" : ""
        }`}
      />
      {native && cssH != null && links.length > 0 && (
        <LinkOverlay
          links={links}
          renderScale={renderScale}
          pageHeight={native.height}
          onFollowLink={onFollowLink}
        />
      )}
      {native && cssH != null && annotations.length > 0 && (
        <AnnotationOverlay
          annotations={annotations}
          focusedAnnotationId={focusedAnnotationId}
          renderScale={renderScale}
          pageHeight={native.height}
        />
      )}
      {pendingHighlight && (
        <HighlightSelectionButton
          layoutRects={pendingHighlight.layoutRects}
          onClick={() =>
            onCreateHighlight(
              pageNumber,
              pendingHighlight.pdfRects,
              pendingHighlight.text,
            )
          }
        />
      )}
    </div>
  );
}

/**
 * The PDF's own hyperlinks, drawn over the page.
 *
 * Inert until Ctrl (or Cmd) is held — see `.shelf-links` in index.css
 * for why. External links open in a new tab; internal ones scroll the
 * reader.
 */
function LinkOverlay({
  links,
  renderScale,
  pageHeight,
  onFollowLink,
}: {
  links: PageLink[];
  renderScale: number;
  /** Native (scale=1) page height in PDF user-space; needed to flip
   *  the y-axis from PDF (origin bottom-left) to CSS (origin top). */
  pageHeight: number;
  onFollowLink: (page: number) => void;
}) {
  return (
    <div className="shelf-links">
      {links.map(({ rect: [x, y, w, h], page, url, label }, idx) => {
        const style = {
          left: `${x * renderScale}px`,
          top: `${(pageHeight - y - h) * renderScale}px`,
          width: `${w * renderScale}px`,
          height: `${h * renderScale}px`,
        };
        const title = `Ctrl+click to open — ${label}`;
        if (url) {
          return (
            <a
              key={idx}
              className="shelf-link"
              style={style}
              href={url}
              target="_blank"
              // noreferrer as well as noopener: an outbound link in an
              // uploaded PDF shouldn't learn which instance opened it.
              rel="noopener noreferrer"
              title={title}
              aria-label={title}
            />
          );
        }
        return (
          <button
            key={idx}
            type="button"
            className="shelf-link"
            style={style}
            title={title}
            aria-label={title}
            onClick={() => onFollowLink(page!)}
          />
        );
      })}
    </div>
  );
}

function HighlightSelectionButton({
  layoutRects,
  onClick,
}: {
  layoutRects: { x: number; y: number; w: number; h: number }[];
  onClick: () => void;
}) {
  // Position the button at the bottom-right corner of the last
  // rect — that's the natural "selection end" for left-to-right
  // text.
  const last = layoutRects[layoutRects.length - 1];
  const left = last.x + last.w;
  const top = last.y + last.h;
  return (
    <button
      type="button"
      onClick={onClick}
      // Stop propagation so the wrapper's pointerup handler
      // doesn't immediately re-evaluate (and clear) the selection.
      onPointerDown={(e) => e.stopPropagation()}
      onMouseDown={(e) => e.stopPropagation()}
      className="shelf-highlight-button"
      style={{ left: `${left}px`, top: `${top}px` }}
    >
      Highlight
    </button>
  );
}

function AnnotationOverlay({
  annotations,
  focusedAnnotationId,
  renderScale,
  pageHeight,
}: {
  annotations: Annotation[];
  /** Ringed briefly after a deep link or a jump from the panel, so the
   *  eye lands on the passage rather than just the right page. */
  focusedAnnotationId: string | null;
  renderScale: number;
  /** Native (scale=1) page height in PDF user-space; needed to flip
   *  the y-axis from PDF (origin bottom-left) to CSS (origin top). */
  pageHeight: number;
}) {
  return (
    <div className="shelf-annotations">
      {annotations.map((a) => {
        const focused = a.id === focusedAnnotationId;
        if (a.kind === "note") {
          // Notes draw a single pin centred on the rect's origin.
          const r = a.rects[0];
          if (!r) return null;
          const cssX = r[0] * renderScale;
          const cssY = (pageHeight - r[1]) * renderScale;
          return (
            <button
              key={a.id}
              type="button"
              className={
                "shelf-annotation-note" +
                (focused ? " shelf-annotation-focused" : "")
              }
              style={{
                left: `${cssX}px`,
                top: `${cssY}px`,
                backgroundColor: a.color,
              }}
              title={a.text ?? ""}
              aria-label={a.text ?? "Note"}
            />
          );
        }
        // Highlight: one tinted rect per quad.
        return a.rects.map(([x, y, w, h], idx) => {
          const cssX = x * renderScale;
          const cssY = (pageHeight - y - h) * renderScale;
          return (
            <div
              key={`${a.id}-${idx}`}
              className={
                "shelf-annotation-rect" +
                (focused ? " shelf-annotation-focused" : "")
              }
              style={{
                left: `${cssX}px`,
                top: `${cssY}px`,
                width: `${w * renderScale}px`,
                height: `${h * renderScale}px`,
                backgroundColor: a.color,
                opacity: focused ? 0.65 : 0.45,
              }}
              title={a.text ?? ""}
            />
          );
        });
      })}
    </div>
  );
}
