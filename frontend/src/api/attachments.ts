import { apiFetch } from "./client";

export interface Attachment {
  id: string;
  item_id: string;
  filename: string;
  content_type: string;
  size_bytes: number | null;
  uploaded_at: string | null;
}

interface RegisterResponse {
  attachment: Attachment;
  upload_url: string;
}

export function listAttachments(itemId: string): Promise<Attachment[]> {
  return apiFetch<Attachment[]>(
    `/api/items/${encodeURIComponent(itemId)}/attachments`,
  );
}

export function registerAttachment(
  itemId: string,
  payload: {
    filename: string;
    content_type: string;
    size_bytes: number | null;
  },
): Promise<RegisterResponse> {
  return apiFetch<RegisterResponse>(
    `/api/items/${encodeURIComponent(itemId)}/attachments`,
    { method: "POST", body: JSON.stringify(payload) },
  );
}

export function deleteAttachment(id: string): Promise<void> {
  return apiFetch<void>(`/api/attachments/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

export function completeAttachment(id: string): Promise<Attachment> {
  return apiFetch<Attachment>(
    `/api/attachments/${encodeURIComponent(id)}/complete`,
    { method: "POST" },
  );
}

export function cleanupOrphanAttachments(
  olderThanMinutes = 60,
): Promise<{ deleted: number }> {
  return apiFetch<{ deleted: number }>(
    `/api/me/attachments/cleanup-orphans?older_than_minutes=${olderThanMinutes}`,
    { method: "POST" },
  );
}

/**
 * Fetch a presigned URL for the PDF.
 *
 * `version` selects which derived (or original) blob to download:
 *   - undefined → server's "current best" (latest outline > latest OCR > original)
 *   - "original" → the untouched upload
 *   - a derivation UUID → that specific run, for compare-versions UX
 */
export async function getDownloadUrl(
  id: string,
  version?: string,
): Promise<string> {
  const qs = version ? `?version=${encodeURIComponent(version)}` : "";
  const r = await apiFetch<{ url: string }>(
    `/api/attachments/${encodeURIComponent(id)}/download${qs}`,
  );
  return r.url;
}

/**
 * Backend-proxied stream URL with Content-Disposition set to the
 * stored filename. Use this for explicit "Download" actions where the
 * browser should save the file under its real name rather than the
 * opaque storage key the presigned URL exposes.
 */
export function getStreamUrl(id: string, version?: string): string {
  const qs = version ? `?version=${encodeURIComponent(version)}` : "";
  return `/api/attachments/${encodeURIComponent(id)}/file${qs}`;
}

export interface AttachmentDerivation {
  id: string;
  kind: string;
  parent_storage_key: string;
  engine: string;
  created_at: string;
}

export interface AttachmentDerivationList {
  derivations: AttachmentDerivation[];
  /** id of the version `getDownloadUrl(id)` returns by default — either a
   * derivation UUID or the literal string "original". */
  current_version: string;
}

export function listDerivations(
  attachmentId: string,
): Promise<AttachmentDerivationList> {
  return apiFetch<AttachmentDerivationList>(
    `/api/attachments/${encodeURIComponent(attachmentId)}/derivations`,
  );
}

export interface AttachmentPageDim {
  page: number;
  width: number | null;
  height: number | null;
}

export interface AttachmentPageDimsResponse {
  pages: AttachmentPageDim[];
}

/**
 * Per-page width/height in PDF user-space (1 pt = 1/72 in) at scale=1.
 * Returned in page-number order. Empty array means the extract worker
 * hasn't run yet — caller should fall back to opening page 1
 * client-side and using its dimensions as a baseline.
 */
export function getPageDims(
  attachmentId: string,
): Promise<AttachmentPageDimsResponse> {
  return apiFetch<AttachmentPageDimsResponse>(
    `/api/attachments/${encodeURIComponent(attachmentId)}/page-dims`,
  );
}

/**
 * Download the PDFs of several items as one ZIP.
 *
 * Server-side assembly (`/api/spaces/{slug}/attachments-zip`) reads
 * each PDF blob and streams back a flat `application/zip`. We fetch it
 * as a blob (rather than a plain `<a href>` navigation) so the caller
 * gets a busy state while the archive is built and a thrown error on
 * failure instead of the browser rendering the JSON error body.
 *
 * The saved filename comes from the response's Content-Disposition,
 * falling back to `<slug>-pdfs.zip`.
 */
export async function downloadItemPdfsZip(
  slug: string,
  itemIds: string[],
): Promise<{ skipped: number }> {
  const qs = itemIds
    .map((id) => `item=${encodeURIComponent(id)}`)
    .join("&");
  const res = await fetch(
    `/api/spaces/${encodeURIComponent(slug)}/attachments-zip?${qs}`,
    { credentials: "include" },
  );
  if (!res.ok) {
    let message = res.statusText;
    try {
      const body = await res.json();
      if (body && typeof body.detail === "string") message = body.detail;
    } catch {
      // non-JSON error body — keep the status text
    }
    throw new Error(message);
  }
  const blob = await res.blob();
  const disposition = res.headers.get("Content-Disposition") ?? "";
  const match = /filename="?([^"]+)"?/.exec(disposition);
  const filename = match ? match[1] : `${slug}-pdfs.zip`;
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
  // The server left out any PDF it couldn't fetch from storage and
  // listed them in _MISSING_FILES.txt; report the count so a partial
  // archive doesn't look complete.
  const skipped = Number(res.headers.get("X-Shelf-Skipped") ?? "0");
  return { skipped: Number.isFinite(skipped) ? skipped : 0 };
}

/**
 * End-to-end upload helper. Three steps:
 *  1. register the attachment (backend mints a presigned PUT URL,
 *     row's uploaded_at is null)
 *  2. PUT the body straight to object storage (no Shelf credentials —
 *     the URL signature carries auth)
 *  3. tell the backend the upload is done so uploaded_at gets stamped
 *
 * On PUT failure the just-registered row is cleaned up so the user
 * doesn't see a phantom attachment.
 */
export async function uploadAttachment(
  itemId: string,
  file: File,
): Promise<Attachment> {
  const { attachment, upload_url } = await registerAttachment(itemId, {
    filename: file.name,
    content_type: file.type || "application/octet-stream",
    size_bytes: file.size,
  });

  // The PUT can fail two ways: a non-2xx Response, or `fetch` itself
  // rejecting (CORS preflight denied, network drop). Both must clean up
  // the just-registered row — otherwise it lingers as a phantom
  // "(pending)" attachment whose object was never stored (→ 404 on view).
  // A single try/catch covers the rejection path the old `if (!res.ok)`
  // check silently skipped.
  try {
    const res = await fetch(upload_url, {
      method: "PUT",
      body: file,
      headers: file.type ? { "Content-Type": file.type } : {},
    });
    if (!res.ok) {
      throw new Error(`Upload failed: ${res.status} ${res.statusText}`);
    }
  } catch (e) {
    try {
      await deleteAttachment(attachment.id);
    } catch {
      // swallow — surfacing the upload failure is more important
    }
    throw e instanceof Error ? e : new Error("Upload failed");
  }
  // Step 3 is best-effort: a missed completion call leaves the row
  // looking pending until the next sweep, but the body is already in
  // the bucket. Don't blow up the visible upload over it.
  try {
    return await completeAttachment(attachment.id);
  } catch {
    return attachment;
  }
}
