import { apiFetch } from "./client";

export type TokenScope = "upload" | "search" | "download";

export interface ApiToken {
  id: string;
  name: string;
  prefix: string;
  scopes: TokenScope[];
  allowed_collection_ids: string[] | null;
  include_descendants: boolean;
  expires_at: string | null;
  last_used_at: string | null;
  created_at: string;
}

export interface ApiTokenCreated extends ApiToken {
  /** Plaintext, only present in the create response. */
  plaintext: string;
}

export function listTokens(): Promise<ApiToken[]> {
  return apiFetch<ApiToken[]>("/api/me/tokens");
}

export function createToken(payload: {
  name: string;
  scopes: TokenScope[];
  allowed_collection_ids?: string[] | null;
  include_descendants?: boolean;
  expires_at?: string | null;
}): Promise<ApiTokenCreated> {
  return apiFetch<ApiTokenCreated>("/api/me/tokens", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function revokeToken(id: string): Promise<void> {
  return apiFetch<void>(`/api/me/tokens/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}
