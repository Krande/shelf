import { afterEach, describe, expect, it, vi } from "vitest";
import { BlobWriter, TextReader, ZipWriter } from "@zip.js/zip.js";
import { mockFetch, nthRequestBody } from "@/test/utils";

const uploads: { itemId: string; name: string; type: string; text: string }[] =
  [];

vi.mock("./attachments", () => ({
  uploadAttachment: vi.fn(async (itemId: string, file: File) => {
    if (file.name === "broken.pdf") throw new Error("PUT failed");
    uploads.push({
      itemId,
      name: file.name,
      type: file.type,
      text: await file.text(),
    });
    return { id: `att-${file.name}`, item_id: itemId, filename: file.name };
  }),
}));

const { describeImport, importArchive } = await import("./archive");

async function makeZip(entries: Record<string, string>): Promise<Blob> {
  const writer = new ZipWriter(new BlobWriter("application/zip"), {
    useWebWorkers: false,
  });
  for (const [name, text] of Object.entries(entries)) {
    await writer.add(name, new TextReader(text));
  }
  return writer.close();
}

const INDEX = {
  format: "shelf.archive",
  version: "1.0",
  items: [],
};

afterEach(() => {
  uploads.length = 0;
  vi.unstubAllGlobals();
});

describe("importArchive", () => {
  it("posts the index, then uploads exactly what the server asks for", async () => {
    const zip = await makeZip({
      "a.pdf": "%PDF a",
      "Sub/b.pdf": "%PDF b",
      "already.pdf": "%PDF here",
      "index.json": JSON.stringify(INDEX),
    });
    const fetch = mockFetch({
      "/api/spaces/lib/archive-import": {
        body: {
          version: "1.0",
          collections_created: 1,
          collections_existing: 0,
          items_created: 2,
          items_existing: 1,
          files_existing: 1,
          uploads: [
            {
              path: "a.pdf",
              item_id: "i-a",
              filename: "a.pdf",
              content_type: "application/pdf",
              size: 6,
            },
            {
              path: "Sub/b.pdf",
              item_id: "i-b",
              filename: "b.pdf",
              content_type: "application/pdf",
              size: 6,
            },
          ],
        },
      },
    });
    const phases: string[] = [];

    const result = await importArchive("lib", zip, "coll-1", (p) =>
      phases.push(p.phase),
    );

    expect(nthRequestBody(fetch)).toEqual({
      index: INDEX,
      collection_id: "coll-1",
    });
    expect(uploads.sort((x, y) => x.name.localeCompare(y.name))).toEqual([
      { itemId: "i-a", name: "a.pdf", type: "application/pdf", text: "%PDF a" },
      { itemId: "i-b", name: "b.pdf", type: "application/pdf", text: "%PDF b" },
    ]);
    expect(result.uploaded).toBe(2);
    expect(result.failed).toEqual([]);
    expect(phases).toEqual(
      expect.arrayContaining(["reading", "planning", "uploading"]),
    );
  });

  it("carries on past a failed upload and reports it", async () => {
    const zip = await makeZip({
      "ok.pdf": "%PDF ok",
      "broken.pdf": "%PDF broken",
      "index.json": JSON.stringify(INDEX),
    });
    const up = (path: string) => ({
      path,
      item_id: "i",
      filename: path,
      content_type: "application/pdf",
      size: null,
    });
    mockFetch({
      "/api/spaces/lib/archive-import": {
        body: {
          version: "1.0",
          collections_created: 0,
          collections_existing: 0,
          items_created: 3,
          items_existing: 0,
          files_existing: 0,
          uploads: [up("ok.pdf"), up("broken.pdf"), up("vanished.pdf")],
        },
      },
    });

    const result = await importArchive("lib", zip, null);

    expect(result.uploaded).toBe(1);
    expect(result.failed.sort()).toEqual([
      "broken.pdf: PUT failed",
      "vanished.pdf: not in the ZIP",
    ]);
    expect(describeImport(result)).toContain("import the same archive again");
  });

  it("restores each PDF's highlights onto its new attachment", async () => {
    const zip = await makeZip({
      "a.pdf": "%PDF a",
      "b.pdf": "%PDF b",
      "index.json": JSON.stringify(INDEX),
    });
    const highlight = {
      kind: "highlight",
      page_number: 2,
      rects: [[1, 2, 3, 4]],
      color: "#ffd400",
      text: "clause",
      visibility: "private",
    };
    const up = (path: string, annotations: unknown[]) => ({
      path,
      item_id: "i",
      filename: path,
      content_type: "application/pdf",
      size: null,
      annotations,
    });
    const fetch = mockFetch({
      "/api/spaces/lib/archive-import": {
        body: {
          version: "1.0",
          collections_created: 0,
          collections_existing: 0,
          items_created: 2,
          items_existing: 0,
          files_existing: 0,
          notes_created: 1,
          revisions_linked: 0,
          uploads: [up("a.pdf", [highlight]), up("b.pdf", [])],
        },
      },
      "/api/attachments/att-a.pdf/annotations/bulk": {
        status: 201,
        body: { created: 1 },
      },
    });

    const result = await importArchive("lib", zip, null);

    const bulk = fetch.mock.calls.filter(([url]) =>
      String(url).endsWith("/annotations/bulk"),
    );
    // Only the PDF that had any, and after its upload.
    expect(bulk.map(([url]) => url)).toEqual([
      "/api/attachments/att-a.pdf/annotations/bulk",
    ]);
    expect(JSON.parse(bulk[0][1].body as string)).toEqual({
      annotations: [highlight],
    });
    expect(result.uploaded).toBe(2);
    expect(result.unannotated).toEqual([]);
    expect(describeImport(result)).toContain("Notes: 1 restored");
  });

  it("reports a PDF whose highlights didn't restore without failing it", async () => {
    const zip = await makeZip({
      "a.pdf": "%PDF a",
      "index.json": JSON.stringify(INDEX),
    });
    mockFetch({
      "/api/spaces/lib/archive-import": {
        body: {
          version: "1.0",
          collections_created: 0,
          collections_existing: 0,
          items_created: 1,
          items_existing: 0,
          files_existing: 0,
          notes_created: 0,
          revisions_linked: 0,
          uploads: [
            {
              path: "a.pdf",
              item_id: "i",
              filename: "a.pdf",
              content_type: "application/pdf",
              size: null,
              annotations: [{ kind: "note", page_number: 1, rects: [[0, 0, 1, 1]] }],
            },
          ],
        },
      },
      "/api/attachments/att-a.pdf/annotations/bulk": { status: 500 },
    });

    const result = await importArchive("lib", zip, null);

    expect(result.uploaded).toBe(1);
    expect(result.failed).toEqual([]);
    expect(result.unannotated).toHaveLength(1);
    expect(describeImport(result)).toContain("uploaded without their highlights");
  });

  it("refuses a ZIP without an index", async () => {
    const zip = await makeZip({ "a.pdf": "%PDF" });
    const fetch = mockFetch({});
    await expect(importArchive("lib", zip, null)).rejects.toThrow(
      "no index.json",
    );
    expect(fetch).not.toHaveBeenCalled();
  });

  it("surfaces the server's refusal of an unreadable version", async () => {
    const zip = await makeZip({
      "index.json": JSON.stringify({ ...INDEX, version: "2.0" }),
    });
    mockFetch({
      "/api/spaces/lib/archive-import": {
        status: 422,
        body: { detail: "Archive format 2.0 can't be read by this Shelf" },
      },
    });
    await expect(importArchive("lib", zip, null)).rejects.toThrow(
      "Archive format 2.0 can't be read by this Shelf",
    );
  });
});
