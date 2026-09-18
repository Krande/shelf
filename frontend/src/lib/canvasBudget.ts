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

