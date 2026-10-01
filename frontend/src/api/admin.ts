import { apiFetch } from "./client";
import type { Role } from "./me";

export interface AdminUser {
  id: string;
  email: string;
  display_name: string;
  role: Role;
  created_at: string;
}

export function fetchAdminUsers(): Promise<AdminUser[]> {
  return apiFetch<AdminUser[]>("/api/admin/users");
}

export interface CreateUserInput {
  email: string;
  display_name?: string;
  role?: Role;
}

/** Pre-provision an account. Nothing is synced from the identity
 * provider, so a colleague who hasn't signed in yet doesn't exist here —
 * this is how they get into the member picker ahead of their first
 * login, which then links onto the row by email. */
export function createUser(input: CreateUserInput): Promise<AdminUser> {
  return apiFetch<AdminUser>("/api/admin/users", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export interface UpdateUserInput {
  role?: Role;
  display_name?: string;
}

export interface AuditEvent {
  id: string;
  created_at: string;
  action: string;
  /** "web" for the app, "api" for token clients. */
  via: string;
  actor_id: string | null;
  actor_name: string | null;
  actor_email: string | null;
  space_id: string | null;
  space_name: string | null;
  target_type: string | null;
  target_id: string | null;
  target_label: string | null;
  details: Record<string, unknown> | null;
}

export interface AuditPage {
  events: AuditEvent[];
  /** Cursor for the next, older page; null at the end. */
  next: string | null;
}

export interface AuditFilters {
  /** An exact action, or a family ending in "." (`item.`). */
  action?: string;
  actor_id?: string;
  space_id?: string;
  target_id?: string;
}

export function fetchAuditEvents(
  filters: AuditFilters,
  before?: string | null,
): Promise<AuditPage> {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(filters)) if (v) qs.set(k, v);
  if (before) qs.set("before", before);
  return apiFetch<AuditPage>(`/api/admin/audit?${qs.toString()}`);
}

export function fetchAuditActions(): Promise<string[]> {
  return apiFetch<string[]>("/api/admin/audit/actions");
}

/** Partial update — send only what changed. The display name is the one
 * part of an account that's editable, and only by an admin; a later OIDC
 * sign-in won't overwrite it, so a correction here sticks. */
export function updateUser(
  userId: string,
  input: UpdateUserInput,
): Promise<AdminUser> {
  return apiFetch<AdminUser>(`/api/admin/users/${userId}`, {
    method: "PATCH",
    body: JSON.stringify(input),
  });
}
