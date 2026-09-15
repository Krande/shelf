import { apiFetch } from "./client";

export type AnnotationKind = "highlight" | "note";

/** [x, y, w, h] in PDF user-space (origin bottom-left). */
export type Rect = [number, number, number, number];

export interface Annotation {
  id: string;
  attachment_id: string;
  kind: AnnotationKind;
  page_number: number;
  rects: Rect[];
  color: string;
  text: string | null;
  created_at: string;
  updated_at: string;
}

export interface AnnotationCreate {
  kind: AnnotationKind;
  page_number: number;
  rects: Rect[];
  color?: string;
  text?: string | null;
}

export interface AnnotationUpdate {
  color?: string;
  text?: string | null;
  rects?: Rect[];
}

/**
 * A shareable link to one annotation.
 *
 * An annotation is the one thing inside a document shelf can address
 * precisely: it has a stable id, a page, and rects in PDF user-space
 * that survive zoom and re-render. `?page=` only gets you to the right
 * sheet of paper; this gets you to the passage.
 *
 * The attachment id is in the path because the reader needs it to open
 * the document at all — the annotation id alone would cost a lookup
 * before anything could render.
 *
 * Relative by default so it works under whatever origin the instance is
 * served from; pass `origin` for something pasteable elsewhere.
 */
export function annotationLink(
  attachmentId: string,
  annotationId: string,
  origin?: string,
): string {
  const path =
    `/reader/${encodeURIComponent(attachmentId)}` +
    `?annotation=${encodeURIComponent(annotationId)}`;
  return origin ? `${origin.replace(/\/+$/, "")}${path}` : path;
}

export function listAnnotations(attachmentId: string): Promise<Annotation[]> {
  return apiFetch<Annotation[]>(
    `/api/attachments/${encodeURIComponent(attachmentId)}/annotations`,
  );
}

export function createAnnotation(
  attachmentId: string,
  payload: AnnotationCreate,
): Promise<Annotation> {
  return apiFetch<Annotation>(
    `/api/attachments/${encodeURIComponent(attachmentId)}/annotations`,
    { method: "POST", body: JSON.stringify(payload) },
  );
}

export function updateAnnotation(
  id: string,
  payload: AnnotationUpdate,
): Promise<Annotation> {
  return apiFetch<Annotation>(`/api/annotations/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function deleteAnnotation(id: string): Promise<void> {
  return apiFetch<void>(`/api/annotations/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}
