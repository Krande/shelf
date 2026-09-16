import { apiFetch } from "./client";

export type TokenScope = "upload" | "search" | "download";

export interface ApiToken {
  id: string;
  name: string;
  prefix: string;
  scopes: TokenScope[];
  /**
   * Space ids this token is limited to, or null for "every space the
   * user can reach" — which includes spaces shared with them and spaces
   * theirs subscribe to. Never widens access: it's intersected with what
   * the user can read at request time, so revoking a membership or
   * dropping a subscription takes the token's reach with it.
   */
  allowed_space_ids: string[] | null;
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
  allowed_space_ids?: string[] | null;
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
