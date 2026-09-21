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
  startLinkAccount: vi.fn(),
  switchAccount: vi.fn(async () => undefined),
  unlinkAccount: vi.fn(async () => ({ active_user_id: "someone" })),
}));

import { startLinkAccount, switchAccount, unlinkAccount } from "@/api/accounts";
import { goToLogin, reloadAsNewAccount } from "@/lib/navigation";

/** A Me whose active account signed in with Entra. */
function entraUser() {
  const me = makeMeWithTwoAccounts();
  return {
    ...me,
    accounts: me.accounts.map((a) =>
      a.id === me.id ? { ...a, idps: ["entra"] } : a,
    ),
  };
}

beforeEach(() => {
  mockFetch({
    "/auth/providers": { body: { providers: ["entra"], dev_login: false } },
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

  it("offers Make active only on the accounts that are not active", () => {
    renderWithProviders(<AccountSection user={makeMeWithTwoAccounts()} />);
    const rows = screen.getAllByRole("listitem");
    expect(
      within(rows[0]).queryByRole("button", { name: /make active/i }),
    ).toBeNull();
    expect(
      within(rows[1]).getByRole("button", { name: /make active/i }),
    ).toBeInTheDocument();
  });

  it("switches the active account and reloads under it", async () => {
    renderWithProviders(<AccountSection user={makeMeWithTwoAccounts()} />);
    await userEvent.click(
      screen.getByRole("button", { name: /make active/i }),
    );

    await waitFor(() =>
      expect(switchAccount).toHaveBeenCalledWith(
        "22222222-2222-2222-2222-222222222222",
      ),
    );
    // A router navigate would leave TanStack caches holding the previous
    // account's data — this has to be a full reload.
    await waitFor(() => expect(reloadAsNewAccount).toHaveBeenCalled());
  });

  it("surfaces a failed switch instead of reloading", async () => {
    vi.mocked(switchAccount).mockRejectedValueOnce(new Error("Forbidden"));
    renderWithProviders(<AccountSection user={makeMeWithTwoAccounts()} />);
    await userEvent.click(
      screen.getByRole("button", { name: /make active/i }),
    );

    expect(await screen.findByRole("alert")).toHaveTextContent("Forbidden");
    expect(reloadAsNewAccount).not.toHaveBeenCalled();
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

  it("offers the other providers as add-account shortcuts", async () => {
    mockFetch({
      "/auth/providers": {
        body: { providers: ["authentik", "entra"], dev_login: false },
      },
    });
    renderWithProviders(<AccountSection user={entraUser()} />);
    await userEvent.click(
      await screen.findByRole("button", { name: /add authentik account/i }),
    );
    expect(startLinkAccount).toHaveBeenCalledWith("authentik");
    // Entra is the one "Switch user" already goes to — no duplicate row.
    expect(
      screen.queryByRole("button", { name: /add entra account/i }),
    ).toBeNull();
  });
});

describe("switch user", () => {
  it("goes straight to the provider's sign-in page, not an account list", async () => {
    renderWithProviders(<AccountSection user={makeMeWithTwoAccounts()} />);
    await userEvent.click(
      await screen.findByRole("button", { name: /^switch user$/i }),
    );

    expect(startLinkAccount).toHaveBeenCalledWith("entra");
    // Picking an already-linked identity is the row buttons' job.
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(switchAccount).not.toHaveBeenCalled();
  });

  it("prefers the provider the active account signed in with", async () => {
    mockFetch({
      "/auth/providers": {
        body: { providers: ["authentik", "entra"], dev_login: false },
      },
    });
    renderWithProviders(<AccountSection user={entraUser()} />);
    await userEvent.click(
      await screen.findByRole("button", { name: /^switch user$/i }),
    );
    expect(startLinkAccount).toHaveBeenCalledWith("entra");
  });

  it("offers one button per provider when nothing says which to use", async () => {
    mockFetch({
      "/auth/providers": {
        body: { providers: ["authentik", "entra"], dev_login: false },
      },
    });
    // makeMe's account has no idps, so neither provider is preferred.
    renderWithProviders(<AccountSection user={makeMe()} />);
    expect(
      await screen.findByRole("button", { name: /switch authentik user/i }),
    ).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: /switch entra user/i }),
    );
    expect(startLinkAccount).toHaveBeenCalledWith("entra");
  });
});

describe("actions", () => {
  it("offers Switch user next to Sign out", async () => {
    renderWithProviders(<AccountSection user={makeMe()} />);
    expect(
      await screen.findByRole("button", { name: /switch user/i }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /sign out/i })).toBeInTheDocument();
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
