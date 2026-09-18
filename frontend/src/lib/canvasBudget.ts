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
  // Never below 1: at the floor the page is rendered at its CSS size,
  // which is soft on a HiDPI screen but always drawable.
  return Math.max(1, Math.min(wanted, bySide, byArea));
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
    return Math.max(1, Math.min(wanted, byBudget));
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
