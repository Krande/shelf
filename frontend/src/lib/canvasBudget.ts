/**
 * Largest pixel multiplier a page can be rendered at.
 *
 * Browsers cap both a canvas's longest side and its total area, and a
 * canvas over either limit is not an error: it hands back a working
 * context and paints nothing. Everything else here multiplies --
 * fit-width scale, device pixel ratio, oversample -- so the product has
 * to be clamped rather than any one input.
 */
const MAX_CANVAS_SIDE = 8192;
const MAX_CANVAS_PIXELS = 20e6;
/**
 * Floor on the multiplier. Not 1 — under layout zoom the CSS box is
 * itself scaled, so a page legitimately renders smaller than its box
 * and the browser upscales it. But not 0 either: that is a zero-sized
 * canvas, which is the blank page this whole file exists to prevent.
 */
const MIN_MULTIPLIER = 0.1;

export function clampPixelMultiplier(
  wanted: number,
  cssWidth: number,
  cssHeight: number,
): number {
  if (!(cssWidth > 0 && cssHeight > 0)) return 1;
  const bySide = Math.min(
    MAX_CANVAS_SIDE / cssWidth,
    MAX_CANVAS_SIDE / cssHeight,
  );
  const byArea = Math.sqrt(MAX_CANVAS_PIXELS / (cssWidth * cssHeight));
  // Deliberately allowed below 1. Under layout zoom the CSS box is
  // itself native x fit x zoom, so at a high zoom on a wide window the
  // box alone is past what a browser will back -- a floor of 1 could
  // only shave the dpr contribution and would hand back a canvas that
  // paints nothing. Below 1 the canvas is smaller than its box and the
  // browser upscales: soft, which is what pdf.js's maxCanvasPixels
  // does, and always visible.
  return Math.max(MIN_MULTIPLIER, Math.min(wanted, bySide, byArea));
}



/**
 * How many pixels all rendered pages may hold between them.
 *
 * Per-page clamping bounds one canvas; it does not bound nine of them,
 * and the number mounted is decided by the viewport and the overscan
 * rather than by anything about memory. This is the second bound: a
 * page that would push the total over budget renders at a lower pixel
 * multiplier instead of being refused, so it is softer rather than
 * absent.
 *
 * 150 MP is roughly 600 MB of backing store, which is a lot for a tab
 * and still several full-quality pages on a large screen.
 */
export const TOTAL_PIXEL_BUDGET = 150e6;

export class CanvasBudget {
  private held = new Map<string, number>();

  /** Pixels currently accounted for. */
  get used(): number {
    let total = 0;
    for (const n of this.held.values()) total += n;
    return total;
  }

  /**
   * The multiplier `key` may render at, given what everything else is
   * already holding. Never below 1: a page always renders.
   */
  allow(
    key: string,
    wanted: number,
    cssWidth: number,
    cssHeight: number,
  ): number {
    const area = cssWidth * cssHeight;
    if (!(area > 0)) return 1;
    // What this page already holds is not competition with itself.
    const others = this.used - (this.held.get(key) ?? 0);
    const spare = Math.max(0, TOTAL_PIXEL_BUDGET - others);
    const byBudget = Math.sqrt(spare / area);
    // Also allowed below 1, for the same reason: the budget cannot be
    // enforced by a function that can never return less than a full
    // resolution render.
    return Math.max(MIN_MULTIPLIER, Math.min(wanted, byBudget));
  }

  /** Record what a page ended up holding. */
  set(key: string, pixels: number): void {
    this.held.set(key, pixels);
  }

  /** Forget a page, on unmount. */
  release(key: string): void {
    this.held.delete(key);
  }
}
