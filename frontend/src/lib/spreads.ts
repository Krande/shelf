/**
 * Grouping pages into the rows a viewer lays out.
 *
 * Mirrors pdf.js's spread modes, including which side the odd pages
 * land on — that is the whole point of the setting, and getting the
 * parity backwards puts every facing pair on the wrong side of the
 * gutter.
 */

export type SpreadMode = "none" | "odd" | "even";

export const SPREAD_LABELS: Record<SpreadMode, string> = {
  none: "No spreads",
  odd: "Odd spreads",
  even: "Even spreads",
};

/**
 * The rows for a document, each holding one or two page numbers.
 *
 * - `none`: every page on its own.
 * - `odd`: pairs start on odd pages — (1,2), (3,4), … Page 1 shares a
 *   row, which is what you want for a document with a cover on the
 *   left.
 * - `even`: page 1 stands alone and pairs start on even pages — (1),
 *   (2,3), (4,5), … the shape of a bound book whose first leaf is a
 *   right-hand page.
 */
export function pageRows(numPages: number, spread: SpreadMode): number[][] {
  if (numPages <= 0) return [];
  if (spread === "none") {
    return Array.from({ length: numPages }, (_, i) => [i + 1]);
  }
  const rows: number[][] = [];
  let page = 1;
  if (spread === "even") {
    rows.push([1]);
    page = 2;
  }
  for (; page <= numPages; page += 2) {
    rows.push(
      page + 1 <= numPages ? [page, page + 1] : [page],
    );
  }
  return rows;
}

/** Which row a page is in, for scrolling to it. */
export function rowOfPage(rows: number[][], page: number): number {
  for (let i = 0; i < rows.length; i++) {
    if (rows[i].includes(page)) return i;
  }
  return 0;
}
