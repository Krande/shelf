/** Shared test helpers: render wrappers and fetch stubbing. */

import type { ReactElement, ReactNode } from "react";
import { MemoryRouter, Route, Routes } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, type RenderResult } from "@testing-library/react";
import { vi } from "vitest";
import type { LinkedAccount, Me, Role } from "@/api/me";

/**
 * A QueryClient with retries off and caching disabled.
 *
 * `retry: false` is only a default — a component that sets its own retry
 * policy (AdminSection retries 5xx) still retries, so `retryDelay: 0`
 * keeps that from spending the real exponential backoff and blowing past
 * a test's assertion timeout. A shared cache would leak between tests in
 * the same file, hence gcTime/staleTime at zero.
 */
export function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, retryDelay: 0, gcTime: 0, staleTime: 0 },
      mutations: { retry: false, retryDelay: 0 },
    },
  });
}

export function renderWithProviders(
  ui: ReactElement,
  { route = "/" }: { route?: string } = {},
): RenderResult {
  const qc = makeQueryClient();
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[route]}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

/**
 * Render a component at a parameterised route, so `useParams` sees real
 * values — the settings tabs read `:tab` off the URL.
 */
export function renderAtRoute(
  path: string,
  element: ReactNode,
  { route }: { route: string },
): RenderResult {
  const qc = makeQueryClient();
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[route]}>
        <Routes>
          <Route path={path} element={element} />
          {/* Catch redirects so a test can assert where it landed. */}
          <Route path="*" element={<RedirectProbe />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** Renders the path it was reached at, so redirect targets are assertable. */
function RedirectProbe() {
  return <div data-testid="redirected-to">{window.location.pathname}</div>;
}

// ── Fixtures ────────────────────────────────────────────────────────────────

export function makeAccount(over: Partial<LinkedAccount> = {}): LinkedAccount {
  return {
    id: "11111111-1111-1111-1111-111111111111",
    email: "a@example.com",
    display_name: "Ada",
    idps: [],
    ...over,
  };
}

export function makeMe(over: Partial<Me> = {}): Me {
  const base = makeAccount();
  return {
    ...base,
    role: "user" as Role,
    is_admin: false,
    accounts: [base],
    ...over,
  };
}

/** A Me with two linked accounts, the first active. */
export function makeMeWithTwoAccounts(): Me {
  const a = makeAccount();
  const b = makeAccount({
    id: "22222222-2222-2222-2222-222222222222",
    email: "b@example.com",
    display_name: "Grace",
  });
  return { ...a, role: "user", is_admin: false, accounts: [a, b] };
}

// ── fetch stubbing ──────────────────────────────────────────────────────────

export interface RouteStub {
  status?: number;
  body?: unknown;
}

/**
 * Stub `fetch` with a path → response map.
 *
 * Keys are matched by `startsWith` against the request path, so
 * "/api/admin/users" also catches "/api/admin/users/<id>". An unmatched
 * request rejects loudly rather than returning undefined and failing
 * somewhere further along.
 */
export function mockFetch(routes: Record<string, RouteStub>): ReturnType<typeof vi.fn> {
  const fn = vi.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    const key = Object.keys(routes)
      // Longest match wins, so a specific path beats a prefix of it.
      .sort((a, b) => b.length - a.length)
      .find((k) => url.startsWith(k));
    if (key === undefined) {
      throw new Error(`Unstubbed request: ${url}`);
    }
    const { status = 200, body = {} } = routes[key];
    return new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

/** Body of the nth call to the stubbed fetch, parsed. */
export function nthRequestBody(fn: ReturnType<typeof vi.fn>, n = 0): unknown {
  const init = fn.mock.calls[n]?.[1] as RequestInit | undefined;
  return init?.body ? JSON.parse(init.body as string) : undefined;
}
