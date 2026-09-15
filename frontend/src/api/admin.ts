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

export function updateUserRole(userId: string, role: Role): Promise<AdminUser> {
  return apiFetch<AdminUser>(`/api/admin/users/${userId}`, {
    method: "PATCH",
    body: JSON.stringify({ role }),
  });
}
