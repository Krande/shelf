import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import LoginPage from "./LoginPage";
import { resetSessionExpiryForTests } from "@/auth/expiry";
import { mockFetch, renderWithProviders } from "@/test/utils";

vi.mock("@/lib/navigation", () => ({
  hardNavigate: vi.fn(),
  reloadAsNewAccount: vi.fn(),
  goToLogin: vi.fn(),
}));

import { hardNavigate } from "@/lib/navigation";

const NEXT = "/reader/abc?page=57";
const AT_LOGIN = `/login?next=${encodeURIComponent(NEXT)}`;

beforeEach(() => {
  resetSessionExpiryForTests();
  mockFetch({
    "/auth/providers": { body: { providers: ["entra"], dev_login: true } },
    "/api/me": { status: 401, body: { detail: "Not authenticated" } },
    "/auth/dev-login": { body: { user_id: "1" } },
  });
});

describe("LoginPage", () => {
  it("says the session ended when it was bounced from somewhere", async () => {
    renderWithProviders(<LoginPage />, { route: AT_LOGIN });
    expect(
      await screen.findByText(/pick up where you left off/i),
    ).toBeInTheDocument();
  });

  it("just asks for a sign-in when there is nowhere to return to", async () => {
    renderWithProviders(<LoginPage />, { route: "/login" });
    expect(await screen.findByText("Sign in to continue.")).toBeInTheDocument();
  });

  it("hands the provider the page to come back to", async () => {
    renderWithProviders(<LoginPage />, { route: AT_LOGIN });
    await userEvent.click(await screen.findByRole("button", { name: /sign in with/i }));
    expect(hardNavigate).toHaveBeenCalledWith(
      `/auth/login/entra?next=${encodeURIComponent(NEXT)}`,
    );
  });

  it("returns a dev login to the page it came from", async () => {
    renderWithProviders(<LoginPage />, { route: AT_LOGIN });
    await userEvent.click(
      await screen.findByRole("button", { name: /continue without a provider/i }),
    );
    await waitFor(() => expect(hardNavigate).toHaveBeenCalledWith(NEXT));
  });

  it("refuses to be redirected off-site", async () => {
    renderWithProviders(<LoginPage />, {
      route: "/login?next=https%3A%2F%2Fevil.example%2Fphish",
    });
    await userEvent.click(
      await screen.findByRole("button", { name: /continue without a provider/i }),
    );
    await waitFor(() => expect(hardNavigate).toHaveBeenCalledWith("/library"));
  });
});
