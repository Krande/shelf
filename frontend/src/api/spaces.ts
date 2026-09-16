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
  /** Only set in an `includeInherited` listing: reached through a
   *  subscription rather than held directly. Always read-only. */
  is_inherited?: boolean;
}

/**
 * Spaces the caller owns plus any they've been added to.
 *
 * `includeInherited` also returns the spaces those subscribe to. Off by
 * default because the space switcher is a list of places you can
 * *work*, and an inherited space isn't one — its items already appear
 * inside the space that subscribes to it. Turn it on where a space is
 * being named rather than moved to, such as scoping an API token.
 */
export function fetchMySpaces(
  opts: { includeInherited?: boolean } = {},
): Promise<Space[]> {
  const qs = opts.includeInherited ? "?include_inherited=true" : "";
  return apiFetch<Space[]>(`/api/me/spaces${qs}`);
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

/** A user who could be added to a space. */
export interface DirectoryUser {
  id: string;
  email: string;
  display_name: string;
}

/**
 * Everyone with an account on this instance, for the member picker.
 *
 * Visible to any signed-in user, not just admins — everyone owns their
 * personal space, so everyone may need to share one.
 */
export function fetchDirectory(): Promise<DirectoryUser[]> {
  return apiFetch<DirectoryUser[]>("/api/users");
}

/**
 * Create a shared space, owned by the caller. Admin-only server-side.
 *
 * `slug` is derived from the name when omitted; it's what appears in
 * URLs and in API-token scopes, so it's worth letting the caller pick.
 */
export function createSpace(name: string, slug?: string): Promise<Space> {
  return apiFetch<Space>("/api/spaces", {
    method: "POST",
    body: JSON.stringify(slug ? { name, slug } : { name }),
  });
}

/**
 * Rename a space, change its slug, or both. Omitted fields are left
 * alone.
 *
 * The space's owner may do this; an instance admin may do it to any
 * *shared* space, which is a label change and grants them no access to
 * what it holds. A personal space's slug is fixed — `is_personal` is
 * derived from its `u-` prefix — though its name can still change.
 *
 * Changing the slug changes the space's URL, with no redirect from the
 * old one. Nothing stored points at a slug, so no access breaks.
 */
export function updateSpace(
  slug: string,
  changes: { name?: string; slug?: string },
): Promise<UpdatedSpace> {
  return apiFetch<UpdatedSpace>(`/api/spaces/${encodeURIComponent(slug)}`, {
    method: "PATCH",
    body: JSON.stringify(changes),
  });
}

/**
 * Like `Space`, but `role` can be null: an instance admin renaming a
 * space they hold no role in gets the new name back and nothing else.
 */
export interface UpdatedSpace extends Omit<Space, "role"> {
  role: SpaceRole | null;
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

// ── Inheritance ──────────────────────────────────────────────────────────────
//
// A space can subscribe to another and read its items without holding a
// copy — the shape a shared "Standards" space wants, with every project
// and every person subscribing to the one copy.
//
// Two sides: the space being read opts in once (`subscribable`), and the
// space doing the reading adds and drops the subscription.

export interface Subscription {
  space_id: string;
  slug: string;
  name: string;
  subscribable: boolean;
}

/** What this space subscribes to. Readable by anyone who can read it. */
export function fetchInherited(slug: string): Promise<Subscription[]> {
  return apiFetch<Subscription[]>(
    `/api/spaces/${encodeURIComponent(slug)}/inherits`,
  );
}

/** Point `slug` at another space, gaining its items read-only. */
export function subscribeToSpace(
  slug: string,
  parentSlug: string,
): Promise<Subscription> {
  return apiFetch<Subscription>(
    `/api/spaces/${encodeURIComponent(slug)}/inherits`,
    { method: "POST", body: JSON.stringify({ parent_slug: parentSlug }) },
  );
}

export function unsubscribeFromSpace(
  slug: string,
  parentSlug: string,
): Promise<void> {
  return apiFetch<void>(
    `/api/spaces/${encodeURIComponent(slug)}/inherits/${encodeURIComponent(parentSlug)}`,
    { method: "DELETE" },
  );
}

/** Which spaces subscribe to this one. Owner only — it's the same
 * question as "who can see my items". */
export function fetchSubscribers(slug: string): Promise<Subscription[]> {
  return apiFetch<Subscription[]>(
    `/api/spaces/${encodeURIComponent(slug)}/subscribers`,
  );
}

export function removeSubscriber(
  slug: string,
  childSlug: string,
): Promise<void> {
  return apiFetch<void>(
    `/api/spaces/${encodeURIComponent(slug)}/subscribers/${encodeURIComponent(childSlug)}`,
    { method: "DELETE" },
  );
}

/**
 * Shared spaces on this instance whose owners have opened them to be
 * inherited. Visible to every signed-in user — that's what makes a
 * Standards space discoverable without being added to it first.
 */
export function fetchSubscribable(): Promise<Subscription[]> {
  return apiFetch<Subscription[]>("/api/spaces/subscribable");
}

/** Owner-only: open this space to subscriptions, or close it to new ones. */
export function setSubscribable(
  slug: string,
  subscribable: boolean,
): Promise<Subscription> {
  return apiFetch<Subscription>(
    `/api/spaces/${encodeURIComponent(slug)}/settings`,
    { method: "PATCH", body: JSON.stringify({ subscribable }) },
  );
}
