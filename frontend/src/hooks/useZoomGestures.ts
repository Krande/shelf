import { useEffect, useRef } from "react";
import { wheelScaleFactor } from "./usePinchZoom";

export const MIN_ZOOM = 0.25;
export const MAX_ZOOM = 5;

export function clampZoom(z: number): number {
  return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, z));
}

/**
 * Turns wheel and pinch gestures into a zoom level.
 *
 * Zoom is a number the page layout is built from — each page's box is
 * `native × fitScale × zoom`, and the canvas is rendered to match — so
 * the document's scroll height grows with it and native scrolling
 * reaches every part of a zoomed page. That is the whole reason this
 * replaced a CSS transform over the list: a transform leaves layout
 * alone, so the scroll range stayed the size of the unzoomed document
 * and everything that reasoned about scroll position had to be told
 * about the transform separately.
 *
 * A pinch still needs to track the fingers at 60fps, which re-rendering
 * every page cannot. So during a pinch the content carries a transient
 * CSS scale written straight to the DOM — no React state, no re-render
 * — and the real zoom is committed once the fingers lift. The wheel
 * commits immediately: a notch is a discrete step, and the render queue
 * makes one step cheap.
 */
export function useZoomGestures(
  containerRef: React.RefObject<HTMLElement | null>,
  contentRef: React.RefObject<HTMLElement | null>,
  {
    zoom,
    onZoom,
    enabled = true,
  }: {
    zoom: number;
    /** Commit a new zoom level. Where the reader ends up is the
     *  caller's business: it anchors on a row, which survives the rows
     *  changing height, rather than on a point in a viewport whose
     *  contents are about to be re-laid out. */
    onZoom: (next: number) => void;
    enabled?: boolean;
  },
) {
  const zoomRef = useRef(zoom);
  zoomRef.current = zoom;
  const onZoomRef = useRef(onZoom);
  onZoomRef.current = onZoom;

  /** Live scale of the transient pinch preview; 1 whenever idle. */
  const previewRef = useRef(1);

  useEffect(() => {
    const el = containerRef.current;
    if (!el || !enabled) return;

    const gesture = {
      active: false,
      startDistance: 0,
      startZoom: 1,
      midX: 0,
      midY: 0,
    };

    function distance(a: Touch, b: Touch): number {
      return Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
    }

    function setPreview(scale: number, originX: number, originY: number) {
      previewRef.current = scale;
      const content = contentRef.current;
      if (!content) return;
      content.style.transformOrigin = `${originX}px ${originY}px`;
      content.style.transform = scale === 1 ? "" : `scale(${scale})`;
    }

    function clearPreview() {
      previewRef.current = 1;
      const content = contentRef.current;
      if (content) {
        content.style.transform = "";
        content.style.transformOrigin = "";
      }
    }

    function onWheel(e: WheelEvent) {
      if (!(e.ctrlKey || e.metaKey)) return;
      e.preventDefault();
      const next = clampZoom(
        zoomRef.current * wheelScaleFactor(e.deltaY, e.deltaMode),
      );
      if (next === zoomRef.current) return;
      onZoomRef.current(next);
    }

    function onTouchStart(e: TouchEvent) {
      if (e.touches.length !== 2) return;
      const rect = el!.getBoundingClientRect();
      gesture.active = true;
      gesture.startDistance = distance(e.touches[0], e.touches[1]);
      gesture.startZoom = zoomRef.current;
      // Relative to the content box, so the preview scales about the
      // point between the fingers rather than the content's corner.
      const content = contentRef.current;
      const contentRect = content?.getBoundingClientRect() ?? rect;
      gesture.midX =
        (e.touches[0].clientX + e.touches[1].clientX) / 2 - contentRect.left;
      gesture.midY =
        (e.touches[0].clientY + e.touches[1].clientY) / 2 - contentRect.top;
    }

    function onTouchMove(e: TouchEvent) {
      if (!gesture.active || e.touches.length !== 2) return;
      e.preventDefault();
      const d = distance(e.touches[0], e.touches[1]);
      if (gesture.startDistance <= 0) return;
      const wanted = clampZoom(
        gesture.startZoom * (d / gesture.startDistance),
      );
      // The preview is relative to the committed zoom, not absolute.
      setPreview(wanted / zoomRef.current, gesture.midX, gesture.midY);
    }

    function onTouchEnd(e: TouchEvent) {
      if (!gesture.active || e.touches.length >= 2) return;
      gesture.active = false;
      const preview = previewRef.current;
      clearPreview();
      if (Math.abs(preview - 1) < 0.01) return;
      onZoomRef.current(clampZoom(zoomRef.current * preview));
    }

    el.addEventListener("wheel", onWheel, { passive: false });
    el.addEventListener("touchstart", onTouchStart, { passive: true });
    el.addEventListener("touchmove", onTouchMove, { passive: false });
    el.addEventListener("touchend", onTouchEnd, { passive: true });
    el.addEventListener("touchcancel", onTouchEnd, { passive: true });
    return () => {
      el.removeEventListener("wheel", onWheel);
      el.removeEventListener("touchstart", onTouchStart);
      el.removeEventListener("touchmove", onTouchMove);
      el.removeEventListener("touchend", onTouchEnd);
      el.removeEventListener("touchcancel", onTouchEnd);
      clearPreview();
    };
  }, [containerRef, contentRef, enabled]);

  return { previewRef };
}
