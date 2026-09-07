import { apiFetch } from "./client";

export interface Space {
  id: string;
  slug: string;
  name: string;
  is_personal: boolean;
}

export function fetchMySpaces(): Promise<Space[]> {
  return apiFetch<Space[]>("/api/me/spaces");
}
