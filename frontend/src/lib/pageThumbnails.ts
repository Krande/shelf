/**
 * A small bitmap of each page, kept so a page that comes back into view
 * has something to show immediately.
 *
 * pdf.js's viewer never destroys a page: it builds a view for every page
 * up front and only *renders* the visible ones, so zooming or scrolling
 * can never leave a page with nothing on it. A virtualized list cannot
 * do that — pages really are unmounted — so a page returning to view
 * starts blank, and at a zoom step that means several blank pages at
 * once. That is the flicker.
 *
 * Holding every page's full canvas would cost hundreds of megabytes.
 * Holding a thumbnail costs a few hundred kilobytes for the whole
 * document and is enough to fill the page while the real render is
 * queued: blurry for a moment, then sharp, which is the behaviour the
 * Mozilla viewer appears to have and is far less noticeable than a
 * flash of empty paper.
 */

/** Longest side of a cached thumbnail, in device pixels. */
const THUMB_MAX_SIDE = 320;

/** How many pages to keep. Beyond this the least recently shown goes. */
const MAX_THUMBS = 80;

export class PageThumbnails {
  // Map iteration order is insertion order, which makes it an LRU as
  // long as a touch re-inserts.
  private cache = new Map<number, HTMLCanvasElement>();

  /** Remember a page, downscaled, from whatever was just rendered. */
  store(page: number, source: HTMLCanvasElement): void {
    if (source.width === 0 || source.height === 0) return;
    const scale = Math.min(
      1,
      THUMB_MAX_SIDE / Math.max(source.width, source.height),
    );
    const thumb = document.createElement("canvas");
    thumb.width = Math.max(1, Math.round(source.width * scale));
    thumb.height = Math.max(1, Math.round(source.height * scale));
    const ctx = thumb.getContext("2d");
    if (!ctx) return;
    ctx.drawImage(source, 0, 0, thumb.width, thumb.height);

    this.cache.delete(page);
    this.cache.set(page, thumb);
    while (this.cache.size > MAX_THUMBS) {
      const oldest = this.cache.keys().next();
      if (oldest.done) break;
      const evicted = this.cache.get(oldest.value);
      if (evicted) evicted.width = evicted.height = 0;
      this.cache.delete(oldest.value);
    }
  }

  /**
   * Paint the remembered page into a canvas, stretched to fill it.
   * Returns false when there is nothing to show.
   */
  paint(page: number, target: HTMLCanvasElement): boolean {
    const thumb = this.cache.get(page);
    if (!thumb) return false;
    // Re-insert so it counts as recently used.
    this.cache.delete(page);
    this.cache.set(page, thumb);
    // The backing store is the thumbnail's size; CSS stretches it to the
    // page box, which is what makes this cost nothing to draw.
    target.width = thumb.width;
    target.height = thumb.height;
    const ctx = target.getContext("2d");
    if (!ctx) return false;
    ctx.drawImage(thumb, 0, 0);
    return true;
  }

  has(page: number): boolean {
    return this.cache.has(page);
  }

  clear(): void {
    for (const c of this.cache.values()) c.width = c.height = 0;
    this.cache.clear();
  }
}
