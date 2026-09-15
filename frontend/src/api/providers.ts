import { apiFetch } from "./client";

export interface ProvidersResponse {
  providers: string[];
  /** Whether /auth/dev-login will actually mint a session. */
  dev_login: boolean;
}

export function fetchProviders(): Promise<ProvidersResponse> {
  return apiFetch<ProvidersResponse>("/auth/providers");
}

/** Title-case a provider name for display ("authentik" → "Authentik"). */
export function providerLabel(name: string): string {
  return name ? name[0].toUpperCase() + name.slice(1) : name;
}
