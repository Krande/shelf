import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AccountSection from "./AccountSection";
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
vi.mock("@/api/accounts", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/accounts")>()),
  unlinkAccount: vi.fn(async () => ({ active_user_id: "someone" })),
}));

import { unlinkAccount } from "@/api/accounts";
import { goToLogin, reloadAsNewAccount } from "@/lib/navigation";

beforeEach(() => {
  mockFetch({
    "/auth/providers": { body: { providers: [], dev_login: false } },
    "/auth/logout": { body: { status: "ok" } },
  });
});

describe("identity", () => {
  it("shows the active account's details including its role", () => {
    renderWithProviders(
      <AccountSection user={makeMe({ role: "admin", is_admin: true })} />,
    );
    // Scoped: the name and email also appear in the linked-accounts list
    // below, which is a different assertion's business.
    const identity = screen.getByText("Signed in as").closest("section")!;
    expect(within(identity).getByText("Ada")).toBeInTheDocument();
    expect(within(identity).getByText("a@example.com")).toBeInTheDocument();
    expect(within(identity).getByText("admin")).toBeInTheDocument();
    expect(within(identity).getByText(makeMe().id)).toBeInTheDocument();
  });
});

describe("linked accounts", () => {
  it("lists them and marks the active one", () => {
    renderWithProviders(<AccountSection user={makeMeWithTwoAccounts()} />);
    const list = screen.getByRole("list");
    expect(within(list).getByText("Ada")).toBeInTheDocument();
    expect(within(list).getByText("Grace")).toBeInTheDocument();
    expect(within(list).getByText("active")).toBeInTheDocument();
  });

  it("unlinks an account and reloads under whoever is left", async () => {
    renderWithProviders(<AccountSection user={makeMeWithTwoAccounts()} />);
    const rows = screen.getAllByRole("listitem");
    await userEvent.click(
      within(rows[1]).getByRole("button", { name: /unlink/i }),
    );

    await waitFor(() =>
      expect(unlinkAccount).toHaveBeenCalledWith(
        "22222222-2222-2222-2222-222222222222",
      ),
    );
    await waitFor(() => expect(reloadAsNewAccount).toHaveBeenCalled());
  });

  it("returns to login when the last account is unlinked", async () => {
    vi.mocked(unlinkAccount).mockResolvedValueOnce({ active_user_id: null });
    renderWithProviders(<AccountSection user={makeMe()} />);
    await userEvent.click(screen.getByRole("button", { name: /unlink/i }));

    await waitFor(() => expect(goToLogin).toHaveBeenCalled());
    expect(reloadAsNewAccount).not.toHaveBeenCalled();
  });

  it("surfaces an unlink failure", async () => {
    vi.mocked(unlinkAccount).mockRejectedValueOnce(new Error("Forbidden"));
    renderWithProviders(<AccountSection user={makeMe()} />);
    await userEvent.click(screen.getByRole("button", { name: /unlink/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Forbidden");
  });
});

describe("actions", () => {
  it("offers Switch user next to Sign out", () => {
    renderWithProviders(<AccountSection user={makeMe()} />);
    expect(
      screen.getByRole("button", { name: /switch user/i }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /sign out/i })).toBeInTheDocument();
  });

  it("opens the switcher popover from Switch user", async () => {
    renderWithProviders(<AccountSection user={makeMeWithTwoAccounts()} />);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /switch user/i }));
    expect(screen.getByRole("menu")).toBeInTheDocument();
  });

  it("does not repeat sign-out or a self-link inside that popover", async () => {
    renderWithProviders(<AccountSection user={makeMeWithTwoAccounts()} />);
    await userEvent.click(screen.getByRole("button", { name: /switch user/i }));
    const menu = screen.getByRole("menu");
    expect(within(menu).queryByRole("menuitem", { name: /sign out/i })).toBeNull();
    expect(
      within(menu).queryByRole("menuitem", { name: /switch user/i }),
    ).toBeNull();
  });

  it("signs out and returns to login", async () => {
    renderWithProviders(<AccountSection user={makeMe()} />);
    await userEvent.click(screen.getByRole("button", { name: /sign out/i }));
    await waitFor(() => expect(goToLogin).toHaveBeenCalled());
  });

  it("warns that signing out drops every linked account", () => {
    renderWithProviders(<AccountSection user={makeMeWithTwoAccounts()} />);
    expect(
      screen.getByText(/clears every linked account/i),
    ).toBeInTheDocument();
  });
});
