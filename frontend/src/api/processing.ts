import { apiFetch } from "./client";

/** Status states the worker writes back. ``untouched`` is the
 *  default for both fields when no row exists yet. */
export type ProcessingStatus =
  | "untouched"
  | "queued"
  | "running"
  | "done"
  | "failed"
  | "cancelled";

export type ProcessingBucket =
  | "needs_ocr"
  | "needs_outline"
  | "ocr_queued"
  | "ocr_running"
  | "ocr_done"
  | "ocr_failed"
  | "outline_queued"
  | "outline_running"
  | "outline_done"
  | "outline_failed"
  | "not_assessed"
  | "all";

export interface ProcessingStats {
  needs_ocr: number;
  needs_outline: number;
  ocr_untouched: number;
  ocr_queued: number;
  ocr_running: number;
  ocr_done: number;
  ocr_failed: number;
  outline_untouched: number;
  outline_queued: number;
  outline_running: number;
  outline_done: number;
  outline_failed: number;
  assessed: number;
  not_assessed: number;
  total_pdfs: number;
}

export interface ProcessingAttachment {
  id: string;
  item_id: string;
  item_title: string | null;
  filename: string;
  page_count: number | null;
  text_chars: number | null;
  chars_per_page: number | null;
  alpha_ratio: number | null;
  replacement_char_ratio: number | null;
  toc_entry_count: number | null;
  needs_ocr: boolean;
  needs_outline: boolean;
  ocr_status: ProcessingStatus;
  ocr_engine: string | null;
  ocr_completed_at: string | null;
  outline_status: ProcessingStatus;
  outline_engine: string | null;
  outline_completed_at: string | null;
  assessed_at: string | null;
  progress_done: number | null;
  progress_total: number | null;
}

/** Per-attachment processing row. Returned by
 *  /api/attachments/{id}/processing — null fields and
 *  status='untouched' when the worker hasn't run yet. */
export interface ProcessingRow {
  attachment_id: string;
  assessed_at: string | null;
  page_count: number | null;
  text_chars: number | null;
  replacement_char_ratio: number | null;
  alpha_ratio: number | null;
  chars_per_page: number | null;
  toc_entry_count: number | null;
  needs_ocr: boolean;
  needs_outline: boolean;
  ocr_status: ProcessingStatus;
  ocr_engine: string | null;
  ocr_completed_at: string | null;
  outline_status: ProcessingStatus;
  outline_engine: string | null;
  outline_completed_at: string | null;
  original_preserved_at: string | null;
  progress_done: number | null;
  progress_total: number | null;
}

export interface TriggerResult {
  attachment_id: string;
  job: "ocr" | "outline";
  enqueued: boolean;
}

export interface RestoreOriginalResult {
  attachment_id: string;
  restored: boolean;
}

export function restoreOriginal(
  attachmentId: string,
): Promise<RestoreOriginalResult> {
  return apiFetch<RestoreOriginalResult>(
    `/api/attachments/${encodeURIComponent(attachmentId)}/processing/restore_original`,
    { method: "POST" },
  );
}

export interface CancelResult {
  attachment_id: string;
  job: "ocr" | "outline";
  previous_status: string;
  cancelled: boolean;
}

export function cancelProcessing(
  attachmentId: string,
  job: "ocr" | "outline",
): Promise<CancelResult> {
  return apiFetch<CancelResult>(
    `/api/attachments/${encodeURIComponent(attachmentId)}/processing/cancel`,
    {
      method: "POST",
      body: JSON.stringify({ job }),
    },
  );
}

export function fetchProcessingStats(): Promise<ProcessingStats> {
  return apiFetch<ProcessingStats>("/api/me/processing/stats");
}

export function listProcessingAttachments(
  bucket: ProcessingBucket = "all",
  limit = 200,
  offset = 0,
): Promise<ProcessingAttachment[]> {
  const params = new URLSearchParams();
  params.set("bucket", bucket);
  params.set("limit", String(limit));
  params.set("offset", String(offset));
  return apiFetch<ProcessingAttachment[]>(
    `/api/me/processing/attachments?${params.toString()}`,
  );
}

export function fetchProcessingRow(
  attachmentId: string,
): Promise<ProcessingRow> {
  return apiFetch<ProcessingRow>(
    `/api/attachments/${encodeURIComponent(attachmentId)}/processing`,
  );
}

export function triggerOcr(attachmentId: string): Promise<TriggerResult> {
  return apiFetch<TriggerResult>(
    `/api/attachments/${encodeURIComponent(attachmentId)}/processing/ocr`,
    { method: "POST" },
  );
}

export function triggerOutline(
  attachmentId: string,
): Promise<TriggerResult> {
  return apiFetch<TriggerResult>(
    `/api/attachments/${encodeURIComponent(attachmentId)}/processing/outline`,
    { method: "POST" },
  );
}

/** Force-enqueue a GPU-tier OCR job (olmOCR / Qwen2.5-VL-7B). The
 *  row's ocr_status is shared with the Tesseract engine, so cancel
 *  and restore continue to work uniformly. */
export function triggerOcrGpu(
  attachmentId: string,
): Promise<TriggerResult> {
  return apiFetch<TriggerResult>(
    `/api/attachments/${encodeURIComponent(attachmentId)}/processing/ocr_gpu`,
    { method: "POST" },
  );
}

/** Server identity: name + version + image tag. The image tag is the
 *  same SHA the API + worker pods are running ("sha-XXXXXXX") so the
 *  UI can correlate failures with deploy commits. */
export interface ServerInfo {
  name: string;
  version: string;
  image_tag: string;
}

export function fetchServerInfo(): Promise<ServerInfo> {
  return apiFetch<ServerInfo>("/api");
}
