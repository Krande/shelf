import { useEffect, type ReactNode } from "react";
import { beforeEach, describe, expect, it } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { QueryClientProvider } from "@tanstack/react-query";
import ProtectedRoute from "./ProtectedRoute";
import { apiFetch } from "@/api/client";
import { resetSessionExpiryForTests } from "@/auth/expiry";
import { makeMe, makeQueryClient, mockFetch } from "@/test/utils";

/** Renders the router's location, so a redirect target is assertable. */
function LocationProbe() {
  const loc = useLocation();
  return <div data-testid="at">{`${loc.pathname}${loc.search}`}</div>;
}

/**
 * Render a guarded page at `route`, with /login as a probe.
 *
 * The address bar is pointed at the same route: `currentReturnTo` reads
 * `window.location` rather than the router, because the reader's ?page=
 * only ever lands there.
 */
function renderGuarded(children: ReactNode, route = "/library") {
  window.history.replaceState(null, "", route);
  return render(
    <QueryClientProvider client={makeQueryClient()}>
      <MemoryRouter initialEntries={[route]}>
        <Routes>
          <Route path="/login" element={<LocationProbe />} />
          <Route path="*" element={<ProtectedRoute>{children}</ProtectedRoute>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** A page that fetches on mount, like the reader's four opening calls. */
function PageThatFetches({ path }: { path: string }) {
  useEffect(() => {
    apiFetch(path).catch(() => {
      // The component's own error UI is beside the point here.
    });
  }, [path]);
  return <div>reader</div>;
}

beforeEach(() => {
  resetSessionExpiryForTests();
  window.history.replaceState(null, "", "/");
});

describe("ProtectedRoute", () => {
  it("renders the page for a live session", async () => {
    mockFetch({ "/api/me": { body: makeMe() } });
    renderGuarded(<div>library</div>);
    expect(await screen.findByText("library")).toBeInTheDocument();
  });

  it("sends an anonymous visitor to /login with the page to come back to", async () => {
    mockFetch({ "/api/me": { status: 401, body: { detail: "Not authenticated" } } });
    renderGuarded(<div>reader</div>, "/reader/abc?page=57");
    await waitFor(() =>
      expect(screen.getByTestId("at")).toHaveTextContent(
        "/login?next=%2Freader%2Fabc%3Fpage%3D57",
      ),
    );
  });

  it("bounces to login when the page's own requests 401 under a cached session", async () => {
    // The case that used to leave "Failed to load PDF: Unauthorized" on
    // screen: /api/me answers from cache with a session that has since
    // expired, so the guard is satisfied and the reader mounts — then
    // every request it makes comes back 401.
    const fetchMock = mockFetch({
      "/api/me": { body: makeMe() },
      "/api/attachments": { status: 401, body: { detail: "Invalid session" } },
    });
    renderGuarded(
      <PageThatFetches path="/api/attachments/abc/download" />,
      "/reader/abc?page=57",
    );
    // The guard let the page through, and its request is what discovered
    // the session was gone.
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/attachments/abc/download",
        expect.anything(),
      ),
    );
    await waitFor(() =>
      expect(screen.getByTestId("at")).toHaveTextContent(
        "/login?next=%2Freader%2Fabc%3Fpage%3D57",
      ),
    );
  });
});
