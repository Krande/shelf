import { apiFetch } from "./client";

export type ExtractionStatusValue =
  | "extracted"
  | "empty"
  | "failed"
  | "pending"
  | "skipped"
  | null;

export interface ExtractionStats {
  extracted: number;
  empty: number;
  failed: number;
  pending: number;
  skipped: number;
  /** Rows whose `extraction_status` is NULL — typically PDFs that
   *  predate the extraction pipeline. */
  missing: number;
  total_pdfs: number;
}

export interface ExtractionAttachment {
  id: string;
  item_id: string;
  item_title: string | null;
  filename: string;
  extraction_status: ExtractionStatusValue;
  text_chars: number | null;
  extracted_at: string | null;
  uploaded_at: string | null;
  size_bytes: number | null;
}

export interface RescanResult {
  selected: number;
  enqueued: number;
}

export function fetchExtractionStats(): Promise<ExtractionStats> {
  return apiFetch<ExtractionStats>("/api/me/extraction/stats");
}

/** Pass `"missing"` to surface rows with extraction_status IS NULL. */
export type ExtractionFilter =
  | "extracted"
  | "empty"
  | "failed"
  | "pending"
  | "skipped"
  | "missing";

export function listExtractionAttachments(
  filter: ExtractionFilter | null = null,
  limit = 100,
  offset = 0,
): Promise<ExtractionAttachment[]> {
  const params = new URLSearchParams();
  if (filter) params.set("status", filter);
  params.set("limit", String(limit));
  params.set("offset", String(offset));
  return apiFetch<ExtractionAttachment[]>(
    `/api/me/extraction/attachments?${params.toString()}`,
  );
}

/**
 * Re-enqueue all attachments matching `statuses`. Pass `null` inside
 * the array to include rows with no status set yet (pre-feature
 * uploads). Omit `statuses` entirely for the default selector
 * (pending + null), which is what the "Rescan needed" button uses.
 */
export function rescanExtraction(
  statuses?: (ExtractionStatusValue | null)[],
): Promise<RescanResult> {
  return apiFetch<RescanResult>("/api/me/extraction/rescan", {
    method: "POST",
    body: JSON.stringify(statuses ? { statuses } : {}),
  });
}

export function rescanOneAttachment(id: string): Promise<RescanResult> {
  return apiFetch<RescanResult>(
    `/api/me/extraction/attachments/${encodeURIComponent(id)}/rescan`,
    { method: "POST" },
  );
}
