import { describe, expect, it } from "vitest";
import { pageRows, rowOfPage } from "./spreads";

describe("pageRows", () => {
  it("gives every page its own row when spreads are off", () => {
    expect(pageRows(4, "none")).toEqual([[1], [2], [3], [4]]);
  });

  it("pairs from page 1 in odd mode", () => {
    expect(pageRows(6, "odd")).toEqual([
      [1, 2],
      [3, 4],
      [5, 6],
    ]);
  });

  it("leaves page 1 alone in even mode, like a bound book", () => {
    expect(pageRows(6, "even")).toEqual([
      [1],
      [2, 3],
      [4, 5],
      [6],
    ]);
  });

  it("gives the last page a row of its own when the count is odd", () => {
    expect(pageRows(5, "odd")).toEqual([[1, 2], [3, 4], [5]]);
    expect(pageRows(4, "even")).toEqual([[1], [2, 3], [4]]);
  });

  it("copes with a one-page document in every mode", () => {
    expect(pageRows(1, "none")).toEqual([[1]]);
    expect(pageRows(1, "odd")).toEqual([[1]]);
    expect(pageRows(1, "even")).toEqual([[1]]);
  });

  it("returns nothing for a document with no pages", () => {
    expect(pageRows(0, "odd")).toEqual([]);
    expect(pageRows(-3, "none")).toEqual([]);
  });

  it("accounts for every page exactly once", () => {
    for (const mode of ["none", "odd", "even"] as const) {
      const seen = pageRows(37, mode).flat();
      expect(seen).toEqual(Array.from({ length: 37 }, (_, i) => i + 1));
    }
  });
});

describe("rowOfPage", () => {
  it("finds the row holding a page", () => {
    const rows = pageRows(6, "even"); // [1], [2,3], [4,5], [6]
    expect(rowOfPage(rows, 1)).toBe(0);
    expect(rowOfPage(rows, 3)).toBe(1);
    expect(rowOfPage(rows, 4)).toBe(2);
    expect(rowOfPage(rows, 6)).toBe(3);
  });

  it("falls back to the first row for a page out of range", () => {
    // Scrolling somewhere is better than throwing mid-render.
    expect(rowOfPage(pageRows(3, "none"), 99)).toBe(0);
  });
});
