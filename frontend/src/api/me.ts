import { apiFetch } from "./client";

export type Role = "admin" | "user";

/** One identity linked to the current browser session. */
export interface LinkedAccount {
  id: string;
  email: string;
  display_name: string;
  /**
   * OIDC providers this account has an identity with. Used to send
   * "switch user" straight to the right provider's account picker.
   * Empty for dev-login users, who have no identity row.
   */
  idps: string[];
}

export interface Me {
  id: string;
  email: string;
  display_name: string;
  role: Role;
  is_admin: boolean;
  /** Every linked account, the active one included. */
  accounts: LinkedAccount[];
}

export function fetchMe(): Promise<Me> {
  return apiFetch<Me>("/api/me");
}
