/**
 * A pane whose width the reader can drag, remembered per browser.
 *
 * Returns `null` until someone actually drags, so the pane keeps
 * whatever width its CSS gives it — which is how "the current size is
 * the default" stays true, including the responsive step-up at `lg`,
 * without hard-coding either number here.
 */

import { useCallback, useEffect, useRef, useState } from "react";

export interface ResizableWidth {
  /** Inline width to apply, or null to leave the CSS default alone. */
  width: number | null;
  /** Bind to the drag handle. */
  onPointerDown: (e: React.PointerEvent) => void;
  /** Arrow keys nudge, Home resets — the handle is focusable. */
  onKeyDown: (e: React.KeyboardEvent) => void;
  /** Double-click the handle to go back to the default. */
  reset: () => void;
  dragging: boolean;
}

export function useResizableWidth(
  storageKey: string,
  {
    min = 280,
    // A fraction of the viewport rather than a fixed number: the point
    // of dragging this out is usually to read a long abstract on a wide
    // screen, and a 640px cap would make that pointless at 2560px.
    maxFraction = 0.6,
    step = 24,
  }: { min?: number; maxFraction?: number; step?: number } = {},
): ResizableWidth {
  const [width, setWidth] = useState<number | null>(() => read(storageKey));
  const [dragging, setDragging] = useState(false);
  // Read inside listeners without re-subscribing them on every pixel.
  const latest = useRef<number | null>(width);
  latest.current = width;

  const clamp = useCallback(
    (value: number) =>
      Math.round(
        Math.min(Math.max(value, min), window.innerWidth * maxFraction),
      ),
    [min, maxFraction],
  );

  const commit = useCallback(
    (value: number | null) => {
      setWidth(value);
      try {
        if (value === null) localStorage.removeItem(storageKey);
        else localStorage.setItem(storageKey, String(value));
      } catch {
        // Private windows and blocked site data throw on write. The
        // pane still resizes for this session; it just won't be
        // remembered, which is a smaller loss than crashing the drag.
      }
    },
    [storageKey],
  );

  const onPointerDown = useCallback(
    (e: React.PointerEvent) => {
      e.preventDefault();
      const handle = e.currentTarget as HTMLElement;
      // The pane sits to the *right* of the handle, so dragging left
      // (decreasing clientX) makes it wider.
      const startX = e.clientX;
      const startWidth =
        latest.current ??
        handle.parentElement?.getBoundingClientRect().width ??
        min;

      setDragging(true);
      handle.setPointerCapture(e.pointerId);

      const move = (ev: PointerEvent) => {
        commit(clamp(startWidth + (startX - ev.clientX)));
      };
      const up = (ev: PointerEvent) => {
        setDragging(false);
        handle.releasePointerCapture?.(ev.pointerId);
        handle.removeEventListener("pointermove", move);
        handle.removeEventListener("pointerup", up);
        handle.removeEventListener("pointercancel", up);
      };

      handle.addEventListener("pointermove", move);
      handle.addEventListener("pointerup", up);
      handle.addEventListener("pointercancel", up);
    },
    [clamp, commit, min],
  );

  const onKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      const current =
        latest.current ??
        (e.currentTarget as HTMLElement).parentElement?.getBoundingClientRect()
          .width ??
        min;
      if (e.key === "ArrowLeft") {
        e.preventDefault();
        commit(clamp(current + step));
      } else if (e.key === "ArrowRight") {
        e.preventDefault();
        commit(clamp(current - step));
      } else if (e.key === "Home") {
        e.preventDefault();
        commit(null);
      }
    },
    [clamp, commit, min, step],
  );

  // A width saved on a wide monitor can exceed the cap on a laptop.
  // Re-clamp on resize rather than leaving the pane wider than its own
  // maximum, which would squeeze the list to nothing.
  useEffect(() => {
    const onResize = () => {
      if (latest.current !== null) commit(clamp(latest.current));
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [clamp, commit]);

  return {
    width,
    onPointerDown,
    onKeyDown,
    reset: () => commit(null),
    dragging,
  };
}

function read(key: string): number | null {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return null;
    const parsed = Number(raw);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
  } catch {
    // Some contexts throw on read (blocked site data, thumbnailers).
    return null;
  }
}
