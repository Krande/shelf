import { ApiError, apiFetch } from "./client";
import { uploadAttachment } from "./attachments";

/**
 * Importing a Shelf archive — the ZIP "Download PDFs" produces (see
 * docs/archive-format.md).
 *
 * The archive never goes to the Shelf server. The browser reads it from
 * disk in slices (the central directory, then one entry at a time), posts
 * only its index.json to `/archive-import`, and uploads each PDF the server
 * asks for through the ordinary presigned upload, straight to object
 * storage. A multi-GB archive costs the server metadata and costs the tab
 * one PDF in memory per upload slot.
 *
 * Re-running an import is safe and picks up where a previous one stopped:
 * the server answers with only what isn't there yet.
 */

export interface ArchiveUpload {
  path: string;
  item_id: string;
  filename: string;
  content_type: string;
  size: number | null;
}

export interface ArchiveImportPlan {
  version: string;
  collections_created: number;
  collections_existing: number;
  items_created: number;
  items_existing: number;
  files_existing: number;
  uploads: ArchiveUpload[];
}

export interface ArchiveImportResult {
  plan: ArchiveImportPlan;
  uploaded: number;
  /** "path: reason" for each PDF that didn't make it. */
  failed: string[];
}

export interface ArchiveImportProgress {
  phase: "reading" | "planning" | "uploading";
  done: number;
  total: number;
}

// PDFs in flight at once. Each holds one file in memory while it uploads;
// a few overlap the round trips without making "which one failed" murky.
const UPLOAD_SLOTS = 3;

export async function planArchiveImport(
  slug: string,
  index: unknown,
  collectionId: string | null,
): Promise<ArchiveImportPlan> {
  try {
    return await apiFetch<ArchiveImportPlan>(
      `/api/spaces/${encodeURIComponent(slug)}/archive-import`,
      {
        method: "POST",
        body: JSON.stringify({ index, collection_id: collectionId }),
      },
    );
  } catch (e) {
    // The server explains a refused archive -- a format version this
    // Shelf can't read, a broken index -- in `detail`; that, not the
    // status text, is what the user needs to see.
    const detail =
      e instanceof ApiError &&
      typeof (e.body as { detail?: unknown } | null)?.detail === "string"
        ? (e.body as { detail: string }).detail
        : null;
    throw detail ? new Error(detail) : e;
  }
}

export async function importArchive(
  slug: string,
  archive: Blob,
  collectionId: string | null,
  onProgress: (p: ArchiveImportProgress) => void = () => {},
): Promise<ArchiveImportResult> {
  onProgress({ phase: "reading", done: 0, total: 0 });
  // Loaded on first use: only people importing pay for it.
  const zip = await import("@zip.js/zip.js");
  // Entries are read on this thread; most are stored, not compressed,
  // so there is little to offload, and workers need a CSP allowance.
  zip.configure({ useWebWorkers: false });
  const reader = new zip.ZipReader(new zip.BlobReader(archive));
  try {
    const files = new Map<string, import("@zip.js/zip.js").FileEntry>();
    for (const entry of await reader.getEntries()) {
      if (!entry.directory) files.set(entry.filename, entry);
    }
    const indexEntry = files.get("index.json");
    if (!indexEntry) {
      throw new Error("Not a Shelf archive: it has no index.json");
    }
    let index: unknown;
    try {
      index = JSON.parse(await indexEntry.getData(new zip.TextWriter()));
    } catch {
      throw new Error("Not a Shelf archive: its index.json isn't valid JSON");
    }

    onProgress({ phase: "planning", done: 0, total: 0 });
    const plan = await planArchiveImport(slug, index, collectionId);

    const failed: string[] = [];
    let uploaded = 0;
    let next = 0;
    const total = plan.uploads.length;
    onProgress({ phase: "uploading", done: 0, total });
    const worker = async () => {
      while (next < total) {
        const up = plan.uploads[next++];
        try {
          const entry = files.get(up.path);
          if (!entry) throw new Error("not in the ZIP");
          const blob = await entry.getData(
            new zip.BlobWriter(up.content_type),
          );
          await uploadAttachment(
            up.item_id,
            new File([blob], up.filename, { type: up.content_type }),
          );
          uploaded += 1;
        } catch (e) {
          failed.push(`${up.path}: ${(e as Error).message}`);
        }
        onProgress({
          phase: "uploading",
          done: uploaded + failed.length,
          total,
        });
      }
    };
    await Promise.all(
      Array.from({ length: Math.min(UPLOAD_SLOTS, total) }, worker),
    );
    return { plan, uploaded, failed };
  } finally {
    await reader.close();
  }
}

/** A one-paragraph account of an import, for an alert. */
export function describeImport(r: ArchiveImportResult): string {
  const p = r.plan;
  const lines = [
    `Documents: ${p.items_created} added` +
      (p.items_existing ? `, ${p.items_existing} already here` : ""),
    `Collections: ${p.collections_created} added` +
      (p.collections_existing ? `, ${p.collections_existing} already here` : ""),
    `PDFs: ${r.uploaded} uploaded` +
      (p.files_existing ? `, ${p.files_existing} already here` : ""),
  ];
  if (r.failed.length > 0) {
    lines.push(
      "",
      `${r.failed.length} PDF${r.failed.length === 1 ? "" : "s"} failed — ` +
        "import the same archive again to retry just those:",
      ...r.failed.slice(0, 20),
    );
    if (r.failed.length > 20) lines.push(`…and ${r.failed.length - 20} more`);
  }
  return lines.join("\n");
}
