import { useCallback, useEffect, useRef, useState } from "react";

export interface PinchZoomState {
  scale: number;
  translateX: number;
  translateY: number;
}

// MIN_SCALE is below 1 so pinch/wheel can zoom *out* past the
// fit-width baseline — necessary to reach a "see whole page" or
// thumbnail-style overview by gesture alone (the Fit Page button is
// a snap shortcut to the same territory, not the only way in).
const MIN_SCALE = 0.4;
const MAX_SCALE = 5;

// Wheel-zoom feel. The clamp is what separates a mouse notch from a
// trackpad pinch: a notch saturates it and lands on a ~10% step
// (exp(0.105)), a pinch's small deltas pass through untouched and stay
// fine-grained.
const WHEEL_CLAMP = 14;
const WHEEL_SENSITIVITY = 0.0075;
// Rough CSS pixels per unit for the non-pixel deltaModes.
const LINE_HEIGHT = 16;
const PAGE_HEIGHT = 800;

/**
 * How much one wheel event should multiply the zoom by.
 *
 * Exported for the test that pins the feel: a mouse notch is a ~10%
 * step, and a trackpad pinch's much smaller deltas stay fine-grained
 * rather than being dragged up to the same size.
 */
export function wheelScaleFactor(deltaY: number, deltaMode: number): number {
  const perUnit =
    deltaMode === 1 ? LINE_HEIGHT : deltaMode === 2 ? PAGE_HEIGHT : 1;
  const dy = Math.max(-WHEEL_CLAMP, Math.min(WHEEL_CLAMP, deltaY * perUnit));
  // Negative deltaY = spread fingers / wheel up = zoom in.
  return Math.exp(-dy * WHEEL_SENSITIVITY);
}

/** Left + right padding of the scroll container, in CSS pixels. */
function horizontalPadding(el: HTMLElement): number {
  const style = window.getComputedStyle(el);
  return (
    (parseFloat(style.paddingLeft) || 0) + (parseFloat(style.paddingRight) || 0)
  );
}

/**
 * Pinch-to-zoom hook for touch devices, ported from the webui's
 * version which already had real-world tuning. CSS-only — the
 * inner content's `transform: translate() scale()` does the live
 * zoom; nothing about the underlying pdfjs render scale changes.
 *
 * - Two-finger pinch zooms; the content point under the pinch
 *   midpoint stays fixed.
 * - Single-finger pan fires at any non-identity scale (otherwise
 *   the native vertical scroll handles it).
 * - Trackpad two-finger horizontal swipe pans translateX while
 *   zoomed; vertical swipes fall through to native scroll.
 * - Double-tap resets to scale 1.
 * - Touch-action flips between `pan-y` (native scroll allowed) and
 *   `none` (JS owns all touches) so the browser doesn't fight us.
 */
export function usePinchZoom(
  containerRef: React.RefObject<HTMLElement | null>,
  options?: { enabled?: boolean },
) {
  const enabled = options?.enabled ?? true;
  const [state, setState] = useState<PinchZoomState>({
    scale: 1,
    translateX: 0,
    translateY: 0,
  });

  const gestureRef = useRef({
    active: false,
    initialDistance: 0,
    initialScale: 1,
    initialMidX: 0,
    initialMidY: 0,
    panActive: false,
    panStartX: 0,
    panStartY: 0,
    initialTranslateX: 0,
    initialTranslateY: 0,
  });

  const stateRef = useRef(state);
  stateRef.current = state;

  const lastTapRef = useRef(0);

  const resetZoom = useCallback(() => {
    setState({ scale: 1, translateX: 0, translateY: 0 });
  }, []);

  useEffect(() => {
    const el = containerRef.current;
    if (!el || !enabled) return;

    function getDistance(t1: Touch, t2: Touch): number {
      const dx = t1.clientX - t2.clientX;
      const dy = t1.clientY - t2.clientY;
      return Math.sqrt(dx * dx + dy * dy);
    }

    function handleTouchStart(e: TouchEvent): void {
      if (e.touches.length === 2) {
        e.preventDefault();
        const g = gestureRef.current;
        const s = stateRef.current;
        const rect = el!.getBoundingClientRect();
        g.active = true;
        g.panActive = false;
        g.initialDistance = getDistance(e.touches[0], e.touches[1]);
        g.initialScale = s.scale;
        g.initialMidX =
          (e.touches[0].clientX + e.touches[1].clientX) / 2 -
          rect.left +
          el!.scrollLeft;
        g.initialMidY =
          (e.touches[0].clientY + e.touches[1].clientY) / 2 -
          rect.top +
          el!.scrollTop;
        g.initialTranslateX = s.translateX;
        g.initialTranslateY = s.translateY;
      } else if (e.touches.length === 1 && stateRef.current.scale !== 1) {
        e.preventDefault();
        const g = gestureRef.current;
        const s = stateRef.current;
        g.panActive = true;
        g.active = false;
        g.panStartX = e.touches[0].clientX;
        g.panStartY = e.touches[0].clientY;
        g.initialTranslateX = s.translateX;
        g.initialTranslateY = s.translateY;
      }

      if (e.touches.length === 1) {
        const now = Date.now();
        if (now - lastTapRef.current < 300) {
          e.preventDefault();
          setState({ scale: 1, translateX: 0, translateY: 0 });
          lastTapRef.current = 0;
        } else {
          lastTapRef.current = now;
        }
      }
    }

    function handleTouchMove(e: TouchEvent): void {
      const g = gestureRef.current;

      if (g.active && e.touches.length === 2) {
        e.preventDefault();
        const dist = getDistance(e.touches[0], e.touches[1]);
        const rawScale = g.initialScale * (dist / g.initialDistance);
        const scale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, rawScale));

        const rect = el!.getBoundingClientRect();
        const midX =
          (e.touches[0].clientX + e.touches[1].clientX) / 2 -
          rect.left +
          el!.scrollLeft;
        const midY =
          (e.touches[0].clientY + e.touches[1].clientY) / 2 -
          rect.top +
          el!.scrollTop;

        // Keep the content point under the initial pinch midpoint
        // fixed: midX = cx * scale + translateX, where cx is the
        // pinch midpoint in unscaled content coordinates.
        const cx = (g.initialMidX - g.initialTranslateX) / g.initialScale;
        const cy = (g.initialMidY - g.initialTranslateY) / g.initialScale;
        const translateX = midX - cx * scale;
        const translateY = midY - cy * scale;

        setState({ scale, translateX, translateY });
      } else if (g.panActive && e.touches.length === 1) {
        e.preventDefault();
        const dx = e.touches[0].clientX - g.panStartX;
        const dy = e.touches[0].clientY - g.panStartY;
        setState((s) => ({
          ...s,
          translateX: g.initialTranslateX + dx,
          translateY: g.initialTranslateY + dy,
        }));
      }
    }

    function handleTouchEnd(e: TouchEvent): void {
      const g = gestureRef.current;
      if (e.touches.length < 2) g.active = false;
      if (e.touches.length === 0) {
        g.panActive = false;
        // Only snap back to scale 1 when the user is *very* close —
        // a deliberate zoom-out to ~0.7 (overview mode) should stick,
        // not get yanked back to fit-width.
        const s = stateRef.current;
        if (Math.abs(s.scale - 1) < 0.04) {
          setState({ scale: 1, translateX: 0, translateY: 0 });
        }
      }
    }

    // Trackpad pinch and laptop-touchscreen pinch come through as
    // `wheel` events with `ctrlKey` set (or `metaKey` on some
    // configurations), not as TouchEvents — the OS / browser
    // translates the gesture to a wheel scroll the browser would
    // normally use for page zoom. We intercept it here so the
    // zoom applies to the PDF wrapper instead of the entire web
    // page. Without this, two-finger pinch on a Mac/Win/Linux
    // laptop just zoomed the whole UI, which surprised users.
    function handleWheel(e: WheelEvent): void {
      const s = stateRef.current;
      if (e.ctrlKey || e.metaKey) {
        e.preventDefault();
        const rect = el!.getBoundingClientRect();
        const y = e.clientY - rect.top + el!.scrollTop;

        // A trackpad pinch arrives as a stream of small deltas; a mouse
        // notch as one big one. wheelScaleFactor normalises the units
        // and clamps, so a notch is a ~10% step instead of the 170% an
        // unclamped exponential gave it.
        const factor = wheelScaleFactor(e.deltaY, e.deltaMode);
        const newScale = Math.min(
          MAX_SCALE,
          Math.max(MIN_SCALE, s.scale * factor),
        );
        if (newScale === s.scale) return;

        // Horizontally the page stays centred rather than following the
        // cursor. The page is centred in its box at rest, and a wheel
        // zoom that walks it sideways off the viewport is the thing
        // every other PDF viewer avoids; there is only one page across,
        // so there is nothing to the side worth anchoring on.
        const contentWidth = el!.clientWidth - horizontalPadding(el!);
        // Vertically the point under the cursor stays put, which is
        // what makes zooming into a figure land on that figure.
        const cy = (y - s.translateY) / s.scale;
        setState({
          scale: newScale,
          translateX: (contentWidth * (1 - newScale)) / 2,
          translateY: y - cy * newScale,
        });
        return;
      }
      // Trackpad two-finger horizontal swipe → pan translateX when
      // zoomed. The scroll container is overflow-y only, so without
      // this the user has no way to reach content that the transform
      // pushed off the left/right edges (especially after a pinch
      // anchored off-centre, or after zooming out below fit-width).
      if (s.scale !== 1 && e.deltaX !== 0) {
        e.preventDefault();
        setState({ ...s, translateX: s.translateX - e.deltaX });
      }
    }

    el.addEventListener("touchstart", handleTouchStart, { passive: false });
    el.addEventListener("touchmove", handleTouchMove, { passive: false });
    el.addEventListener("touchend", handleTouchEnd, { passive: true });
    el.addEventListener("wheel", handleWheel, { passive: false });

    return () => {
      el.removeEventListener("touchstart", handleTouchStart);
      el.removeEventListener("touchmove", handleTouchMove);
      el.removeEventListener("touchend", handleTouchEnd);
      el.removeEventListener("wheel", handleWheel);
    };
  }, [containerRef, enabled]);

  const contentStyle: React.CSSProperties = {
    // Always emit a transform — even at the identity (scale=1,
    // translate=0). Toggling between `undefined` and a value was
    // promoting/demoting the GPU compositor layer at the start and
    // end of every gesture, which the user saw as a one-frame
    // flicker. With `will-change: transform` and a stable transform
    // string, the layer is allocated up-front and the gesture is
    // smooth.
    transform: `translate(${state.translateX}px, ${state.translateY}px) scale(${state.scale})`,
    transformOrigin: "0 0",
    willChange: "transform",
    // touch-action policy:
    //   - select-mode (enabled=false): `none` so the scroll
    //     container doesn't compete with drag-select for touches.
    //     The browser still honours `user-select: text` on the
    //     pdfjs text spans, so a finger drag selects text.
    //   - scale !== 1 (zoomed in *or* out): `none` so the JS pan
    //     owns all touches.
    //   - default (scale=1, enabled): `auto` because both iOS Safari
    //     and Android Chromium need an unrestricted touch-action for
    //     long-press text selection on the pdfjs text layer to fire
    //     reliably (`pan-y` blocks long-press on iOS, `manipulation`
    //     blocks it on Android). The otherwise-unwanted defaults
    //     (native pinch, double-tap zoom) are already blocked by the
    //     reader's viewport meta lock.
    touchAction: !enabled || state.scale !== 1 ? "none" : "auto",
  };

  return { ...state, contentStyle, resetZoom };
}
