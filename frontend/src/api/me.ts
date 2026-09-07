import { apiFetch } from "./client";

export interface Me {
  id: string;
  email: string;
  display_name: string;
}

export function fetchMe(): Promise<Me> {
  return apiFetch<Me>("/api/me");
}
