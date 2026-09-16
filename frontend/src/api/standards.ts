/**
 * Engineering standards: which editions exist, and which one a space
 * uses.
 *
 * A standard is republished over time, and which edition applies is a
 * project decision. Items that are editions of the same standard are
 * linked to one "family", which is what makes the revision dropdown and
 * the latest-edition badge possible. A space pins the revision it builds
 * to; its library then shows that one and hides the siblings unless
 * asked.
 */

import { apiFetch } from "./client";

export interface StandardFamily {
  id: string;
  /** Whoever publishes it. */
  body: string;
  /** Designation without an edition — "ACME 1234", "ACME-RP-7". */
  designation: string;
  title: string | null;
}

export interface StandardRevision {
  item_id: string;
  space_id: string;
  /** What this edition is called: "2020", "Rev. 5". Display only. */
  label: string;
  /** ISO date, or null when the document doesn't say. Undated
   * revisions sort last and are never "latest". */
  issued_on: string | null;
  superseded: boolean;
  title: string | null;
  /** Newest revision *the caller can read*. At most one per response. */
  is_latest: boolean;
  /** The revision the space in the request pins, if any. */
  is_pinned: boolean;
}

export interface RevisionsResponse {
  family: StandardFamily;
  revisions: StandardRevision[];
  /**
   * False when the instance holds a newer edition than any the caller
   * can see. Lets the UI say "there is a newer one, ask for access"
   * rather than presenting a stale edition as current.
   */
  is_latest_known: boolean;
}

/**
 * Every edition of this item's standard that the caller can read.
 *
 * `space` only decides which revision comes back marked pinned;
 * visibility is the caller's either way.
 */
export function fetchRevisions(
  itemId: string,
  space?: string,
): Promise<RevisionsResponse> {
  const qs = space ? `?space=${encodeURIComponent(space)}` : "";
  return apiFetch<RevisionsResponse>(
    `/api/items/${encodeURIComponent(itemId)}/revisions${qs}`,
  );
}

export interface LinkRevisionInput {
  body: string;
  designation: string;
  label: string;
  issued_on?: string | null;
  title?: string | null;
  superseded?: boolean;
}

/**
 * Declare an item to be one edition of one standard.
 *
 * The family is found-or-created from (body, designation), both matched
 * case-insensitively — so a second upload of the same standard joins the
 * existing revision history instead of forking it.
 */
export function linkRevision(
  itemId: string,
  input: LinkRevisionInput,
): Promise<RevisionsResponse> {
  return apiFetch<RevisionsResponse>(
    `/api/items/${encodeURIComponent(itemId)}/revision`,
    { method: "PUT", body: JSON.stringify(input) },
  );
}

export function unlinkRevision(itemId: string): Promise<void> {
  return apiFetch<void>(`/api/items/${encodeURIComponent(itemId)}/revision`, {
    method: "DELETE",
  });
}

// ── Pins ─────────────────────────────────────────────────────────────────────

export interface StandardPin {
  family: StandardFamily;
  item_id: string;
  label: string;
  issued_on: string | null;
}

/** The revisions this space has chosen, one per standard. */
export function fetchPins(slug: string): Promise<StandardPin[]> {
  return apiFetch<StandardPin[]>(
    `/api/spaces/${encodeURIComponent(slug)}/pins`,
  );
}

/**
 * Choose the revision of one standard that this space uses.
 *
 * Owner-only server-side: which edition a project builds to has
 * consequences outside the library, and it changes what everyone else in
 * the space sees by default.
 */
export function setPin(
  slug: string,
  familyId: string,
  itemId: string,
): Promise<StandardPin> {
  return apiFetch<StandardPin>(
    `/api/spaces/${encodeURIComponent(slug)}/pins/${encodeURIComponent(familyId)}`,
    { method: "PUT", body: JSON.stringify({ item_id: itemId }) },
  );
}

export function clearPin(slug: string, familyId: string): Promise<void> {
  return apiFetch<void>(
    `/api/spaces/${encodeURIComponent(slug)}/pins/${encodeURIComponent(familyId)}`,
    { method: "DELETE" },
  );
}
