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

export function updateUserRole(userId: string, role: Role): Promise<AdminUser> {
  return apiFetch<AdminUser>(`/api/admin/users/${userId}`, {
    method: "PATCH",
    body: JSON.stringify({ role }),
  });
}
