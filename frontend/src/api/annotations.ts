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
