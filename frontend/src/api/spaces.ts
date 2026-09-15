import { apiFetch } from "./client";

/** The caller's role in a space. Ranked: viewer < editor < owner. */
export type SpaceRole = "viewer" | "editor" | "owner";

export interface Space {
  id: string;
  slug: string;
  name: string;
  is_personal: boolean;
  /** The *caller's* role here, not the space's own property. */
  role: SpaceRole;
  is_owner: boolean;
}

/** Spaces the caller owns plus any they've been added to. */
export function fetchMySpaces(): Promise<Space[]> {
  return apiFetch<Space[]>("/api/me/spaces");
}

/**
 * Whether the caller may change content in this space.
 *
 * Cosmetic only — the API enforces the same rule and returns 403
 * regardless. Used to hide controls that would just fail.
 */
export function canEdit(space: Space | undefined | null): boolean {
  return space?.role === "editor" || space?.role === "owner";
}

// ── Membership ───────────────────────────────────────────────────────────────

export interface SpaceMember {
  user_id: string;
  email: string;
  display_name: string;
  role: SpaceRole;
  is_owner: boolean;
}

/** Owner-only: an editor can fill a space but not widen access to it. */
export function fetchMembers(slug: string): Promise<SpaceMember[]> {
  return apiFetch<SpaceMember[]>(
    `/api/spaces/${encodeURIComponent(slug)}/members`,
  );
}

export function addMember(
  slug: string,
  email: string,
  role: Exclude<SpaceRole, "owner">,
): Promise<SpaceMember> {
  return apiFetch<SpaceMember>(
    `/api/spaces/${encodeURIComponent(slug)}/members`,
    { method: "POST", body: JSON.stringify({ email, role }) },
  );
}

export function updateMemberRole(
  slug: string,
  userId: string,
  role: Exclude<SpaceRole, "owner">,
): Promise<SpaceMember> {
  return apiFetch<SpaceMember>(
    `/api/spaces/${encodeURIComponent(slug)}/members/${userId}`,
    { method: "PATCH", body: JSON.stringify({ role }) },
  );
}

export function removeMember(slug: string, userId: string): Promise<void> {
  return apiFetch<void>(
    `/api/spaces/${encodeURIComponent(slug)}/members/${userId}`,
    { method: "DELETE" },
  );
}
