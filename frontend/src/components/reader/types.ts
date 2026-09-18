/**
 * Shapes the reader's pieces pass between them.
 *
 * Here rather than in ReaderPage so a component can be read — and
 * edited — without opening the page that renders it.
 */

/** A page's size at scale 1, in PDF user-space points. */
export interface NativeViewport {
  width: number;
  height: number;
}

/** Where the reader is, in terms that survive a change of scale: a band
 *  and how far into it the viewport's top sits. */
export interface ScrollAnchor {
  index: number;
  within: number;
}

export interface ContinuousListHandle {
  scrollToPage: (page: number) => void;
}

/** Gutter between the two pages of a facing pair. */
export const SPREAD_GAP = 12;

/** Gap below a band. */
export const PAGE_GAP = 16;

/** The scroll container's padding, which the fit scale has to allow for. */
export const SCROLL_PADDING = 32;

/** Stand-in height for a page whose size is not known yet. */
export const ESTIMATE_PAGE_HEIGHT = 1100;
