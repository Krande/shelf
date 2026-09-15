import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Header from "./Header";
import {
  makeMe,
  makeMeWithTwoAccounts,
  mockFetch,
  renderWithProviders,
} from "@/test/utils";

vi.mock("@/lib/navigation", () => ({
  hardNavigate: vi.fn(),
  reloadAsNewAccount: vi.fn(),
  goToLogin: vi.fn(),
}));

beforeEach(() => {
  mockFetch({ "/auth/providers": { body: { providers: [], dev_login: false } } });
});

describe("navigation", () => {
  it("links to Library and Settings", () => {
    renderWithProviders(<Header user={makeMe()} />);
    expect(screen.getByRole("link", { name: /library/i })).toHaveAttribute(
      "href",
      "/library",
    );
    expect(screen.getByRole("link", { name: /settings/i })).toHaveAttribute(
      "href",
      "/settings",
    );
  });

  it("has no top-level Admin entry — it moved into Settings", () => {
    renderWithProviders(
      <Header user={makeMe({ role: "admin", is_admin: true })} />,
    );
    expect(screen.queryByRole("link", { name: /^admin$/i })).not.toBeInTheDocument();
  });
});

describe("account menu", () => {
  it("shows the active account's display name", () => {
    renderWithProviders(<Header user={makeMe()} />);
    expect(screen.getByRole("button", { name: /ada/i })).toBeInTheDocument();
  });

  it("badges how many other accounts are linked", () => {
    renderWithProviders(<Header user={makeMeWithTwoAccounts()} />);
    expect(screen.getByText("+1")).toBeInTheDocument();
  });

  it("shows no badge with a single account", () => {
    renderWithProviders(<Header user={makeMe()} />);
    expect(screen.queryByText(/^\+\d+$/)).not.toBeInTheDocument();
  });

  it("opens the switcher", async () => {
    renderWithProviders(<Header user={makeMeWithTwoAccounts()} />);
    await userEvent.click(screen.getByRole("button", { name: /ada/i }));
    expect(screen.getByRole("menu")).toBeInTheDocument();
    expect(
      screen.getByRole("menuitem", { name: /switch user/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("menuitem", { name: /sign out/i }),
    ).toBeInTheDocument();
  });
});
