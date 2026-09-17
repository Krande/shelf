/**
 * The links a PDF carries in itself: outline entries, cross-references,
 * and outbound URLs.
 *
 * Outline entries and link annotations both point at a page through the
 * same `dest` shapes, so both resolve it the same way — hence one module
 * rather than a copy in the panel and a copy in the reader. Kept apart
 * from the components so the fiddly parts (named vs explicit
 * destinations, PDF rects given as either pair of opposite corners) can
 * be tested without a rendered page.
 */

import type { PDFDocumentProxy, PDFPageProxy } from "pdfjs-dist";

import type { Rect } from "@/api/annotations";

/** What pdfjs calls a destination: a named one, or an explicit array. */
export type PdfDest = string | unknown[] | null;

/**
 * Resolve a pdfjs destination to a 1-based page number.
 *
 * pdfjs `dest` is either a *named* destination (string — needs a
 * second lookup via getDestination) or already an *explicit*
 * destination array. Either way the first element of the explicit
 * array is the page object reference, which getPageIndex turns into
 * a 0-based index. Returns null if anything along the chain is
 * missing or malformed.
 */
export async function destToPage(
  doc: PDFDocumentProxy,
  dest: PdfDest,
): Promise<number | null> {
  if (dest == null) return null;
  let resolved: unknown[] | null = null;
  if (typeof dest === "string") {
    const d = await doc.getDestination(dest);
    resolved = d as unknown[] | null;
  } else if (Array.isArray(dest)) {
    resolved = dest;
  }
  if (!resolved || resolved.length === 0) return null;
  try {
    const idx = await doc.getPageIndex(resolved[0] as never);
    return idx + 1;
  } catch {
    return null;
  }
}

/** One hyperlink baked into the PDF. Exactly one of `page` / `url`. */
export interface PageLink {
  /** [x, y, w, h] in PDF user-space, origin bottom-left. */
  rect: Rect;
  /** Target page for an internal link (1-based), null for an external one. */
  page: number | null;
  /** Target URL for an external link, null for an internal one. */
  url: string | null;
  /** What the tooltip says, so the reader knows where it goes first. */
  label: string;
}

/**
 * Pull the followable links out of one page's annotations.
 *
 * A PDF link is either a *destination* inside the same file — the shape
 * cross-references and authored tables of contents take — or a URL. The
 * destination resolves through exactly the machinery the outline panel
 * already uses, since a link annotation's `dest` and an outline node's
 * `dest` are the same thing. Anything that is neither (a JavaScript
 * action, a GoToR into another file we don't have) is dropped rather
 * than rendered as a rectangle that does nothing when clicked.
 */
export async function pageLinks(
  doc: PDFDocumentProxy,
  pdfPage: PDFPageProxy,
): Promise<PageLink[]> {
  const raw = (await pdfPage.getAnnotations({ intent: "display" })) as Array<{
    subtype?: string;
    rect?: number[];
    url?: string;
    dest?: PdfDest;
  }>;
  const out: PageLink[] = [];
  for (const a of raw) {
    if (a.subtype !== "Link") continue;
    const r = a.rect;
    if (!r || r.length < 4) continue;
    // PDF rects are two opposite corners in either order; normalise to
    // origin + size before anything tries to lay one out.
    const [x1, y1, x2, y2] = r;
    const rect: Rect = [
      Math.min(x1, x2),
      Math.min(y1, y2),
      Math.abs(x2 - x1),
      Math.abs(y2 - y1),
    ];
    if (rect[2] <= 0 || rect[3] <= 0) continue;

    if (typeof a.url === "string" && a.url) {
      out.push({ rect, page: null, url: a.url, label: a.url });
      continue;
    }
    const page = await destToPage(doc, a.dest ?? null);
    if (page != null) {
      out.push({ rect, page, url: null, label: `Page ${page}` });
    }
  }
  return out;
}
