import { describe, expect, it, vi } from "vitest";
import type { PDFDocumentProxy, PDFPageProxy } from "pdfjs-dist";
import { destToPage, pageLinks } from "./pdfLinks";

/**
 * A stand-in for the two pdfjs calls destination resolution makes.
 * `named` maps a named destination to an explicit one; `pageIndexOf`
 * maps the page ref at the head of an explicit destination to its
 * 0-based index, and throws for a ref the document doesn't know — which
 * is how pdfjs itself signals a dangling reference.
 */
function makeDoc({
  named = {},
  pageIndexOf = {},
}: {
  named?: Record<string, unknown[]>;
  pageIndexOf?: Record<string, number>;
} = {}) {
  return {
    getDestination: vi.fn(async (name: string) => named[name] ?? null),
    getPageIndex: vi.fn(async (ref: unknown) => {
      const key = String(ref);
      if (!(key in pageIndexOf)) throw new Error("no such page ref");
      return pageIndexOf[key];
    }),
  } as unknown as PDFDocumentProxy;
}

function makePage(annotations: unknown[]) {
  return {
    getAnnotations: vi.fn(async () => annotations),
  } as unknown as PDFPageProxy;
}

describe("destToPage", () => {
  it("resolves an explicit destination to a 1-based page", async () => {
    const doc = makeDoc({ pageIndexOf: { pageRef7: 6 } });
    expect(await destToPage(doc, ["pageRef7", "XYZ", 0, 0, null])).toBe(7);
  });

  it("looks a named destination up first", async () => {
    const doc = makeDoc({
      named: { "chapter.3": ["pageRef12", "Fit"] },
      pageIndexOf: { pageRef12: 11 },
    });
    expect(await destToPage(doc, "chapter.3")).toBe(12);
  });

  it("returns null for a name the document doesn't define", async () => {
    const doc = makeDoc({ named: {} });
    expect(await destToPage(doc, "missing")).toBeNull();
  });

  it("returns null when the page ref dangles", async () => {
    // A ref pointing at a page that isn't in this file — pdfjs throws,
    // and a link nobody can follow is better dropped than rendered.
    const doc = makeDoc({ pageIndexOf: {} });
    expect(await destToPage(doc, ["ghostRef"])).toBeNull();
  });

  it("returns null for no destination at all", async () => {
    const doc = makeDoc();
    expect(await destToPage(doc, null)).toBeNull();
    expect(await destToPage(doc, [])).toBeNull();
  });
});

describe("pageLinks", () => {
  it("turns an internal link into a page jump", async () => {
    const doc = makeDoc({ pageIndexOf: { p4: 3 } });
    const page = makePage([
      { subtype: "Link", rect: [10, 700, 110, 720], dest: ["p4"] },
    ]);
    expect(await pageLinks(doc, page)).toEqual([
      { rect: [10, 700, 100, 20], page: 4, url: null, label: "Page 4" },
    ]);
  });

  it("keeps an external link as a URL", async () => {
    const doc = makeDoc();
    const page = makePage([
      {
        subtype: "Link",
        rect: [0, 0, 50, 10],
        url: "https://example.com/spec",
      },
    ]);
    expect(await pageLinks(doc, page)).toEqual([
      {
        rect: [0, 0, 50, 10],
        page: null,
        url: "https://example.com/spec",
        label: "https://example.com/spec",
      },
    ]);
  });

  it("normalises a rect given as the opposite pair of corners", async () => {
    // PDF rects are any two opposite corners, in either order. Laying
    // one out unnormalised gives a negative width and an invisible link.
    const doc = makeDoc({ pageIndexOf: { p1: 0 } });
    const page = makePage([
      { subtype: "Link", rect: [120, 90, 20, 40], dest: ["p1"] },
    ]);
    expect((await pageLinks(doc, page))[0].rect).toEqual([20, 40, 100, 50]);
  });

  it("drops annotations that aren't links", async () => {
    const doc = makeDoc({ pageIndexOf: { p1: 0 } });
    const page = makePage([
      { subtype: "Widget", rect: [0, 0, 10, 10] },
      { subtype: "Link", rect: [0, 0, 10, 10], dest: ["p1"] },
    ]);
    expect(await pageLinks(doc, page)).toHaveLength(1);
  });

  it("drops a link that goes nowhere we can follow", async () => {
    // A JavaScript action, or a GoToR into a file we don't have: pdfjs
    // gives neither a url nor a resolvable dest. Rendering it would put
    // a clickable rectangle on the page that does nothing.
    const doc = makeDoc();
    const page = makePage([{ subtype: "Link", rect: [0, 0, 10, 10] }]);
    expect(await pageLinks(doc, page)).toEqual([]);
  });

  it("drops a degenerate rect", async () => {
    const doc = makeDoc({ pageIndexOf: { p1: 0 } });
    const page = makePage([
      { subtype: "Link", rect: [5, 5, 5, 40], dest: ["p1"] },
      { subtype: "Link", rect: [5, 5], dest: ["p1"] },
    ]);
    expect(await pageLinks(doc, page)).toEqual([]);
  });
});
