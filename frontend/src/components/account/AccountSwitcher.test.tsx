import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AccountSwitcher from "./AccountSwitcher";
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
}));

import { startLinkAccount, switchAccount } from "@/api/accounts";
import { goToLogin, reloadAsNewAccount } from "@/lib/navigation";

const NO_PROVIDERS = {
  "/auth/providers": { body: { providers: [], dev_login: false } },
};

function renderSwitcher(user = makeMeWithTwoAccounts(), props = {}) {
  return renderWithProviders(
    <AccountSwitcher user={user} trigger={<span>Menu</span>} {...props} />,
  );
}

beforeEach(() => {
  mockFetch(NO_PROVIDERS);
});

describe("popover behaviour", () => {
  it("is closed until the trigger is clicked", async () => {
    renderSwitcher();
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /menu/i }));
    expect(screen.getByRole("menu")).toBeInTheDocument();
  });

  it("closes on Escape", async () => {
    renderSwitcher();
    await userEvent.click(screen.getByRole("button", { name: /menu/i }));
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("closes on an outside click", async () => {
    renderSwitcher();
    await userEvent.click(screen.getByRole("button", { name: /menu/i }));
    await userEvent.click(document.body);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("reports expanded state to assistive tech", async () => {
    renderSwitcher();
    const trigger = screen.getByRole("button", { name: /menu/i });
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(trigger);
    expect(trigger).toHaveAttribute("aria-expanded", "true");
  });
});

describe("switching", () => {
  it("lists every linked account", async () => {
    renderSwitcher();
    await userEvent.click(screen.getByRole("button", { name: /menu/i }));
    expect(screen.getByText("Ada")).toBeInTheDocument();
    expect(screen.getByText("Grace")).toBeInTheDocument();
    expect(screen.getByText("b@example.com")).toBeInTheDocument();
  });

  it("switches to another account and reloads", async () => {
    renderSwitcher();
    await userEvent.click(screen.getByRole("button", { name: /menu/i }));
    await userEvent.click(screen.getByText("Grace"));

    await waitFor(() =>
      expect(switchAccount).toHaveBeenCalledWith(
        "22222222-2222-2222-2222-222222222222",
      ),
    );
    // A router navigate would leave TanStack caches holding the previous
    // account's data — this has to be a full reload.
    await waitFor(() => expect(reloadAsNewAccount).toHaveBeenCalled());
  });

  it("does nothing when the active account is clicked", async () => {
    renderSwitcher();
    await userEvent.click(screen.getByRole("button", { name: /menu/i }));
    await userEvent.click(screen.getByText("Ada"));
    expect(switchAccount).not.toHaveBeenCalled();
    expect(reloadAsNewAccount).not.toHaveBeenCalled();
  });

  it("surfaces a failure instead of reloading", async () => {
    vi.mocked(switchAccount).mockRejectedValueOnce(new Error("Forbidden"));
    renderSwitcher();
    await userEvent.click(screen.getByRole("button", { name: /menu/i }));
    await userEvent.click(screen.getByText("Grace"));

    expect(await screen.findByRole("alert")).toHaveTextContent("Forbidden");
    expect(reloadAsNewAccount).not.toHaveBeenCalled();
  });

  it("labels the list only when there is something to switch between", async () => {
    renderSwitcher(makeMe());
    await userEvent.click(screen.getByRole("button", { name: /menu/i }));
    expect(
      screen.queryByText("Switch user", { selector: "p" }),
    ).not.toBeInTheDocument();
  });
});

describe("switch user goes to the right provider", () => {
  const entraUser = () => {
    const me = makeMeWithTwoAccounts();
    return {
      ...me,
      accounts: me.accounts.map((a) =>
        a.id === me.id ? { ...a, idps: ["entra"] } : a,
      ),
    };
  };

  it("jumps straight to the provider the active account signed in with", async () => {
    mockFetch({
      "/auth/providers": {
        body: { providers: ["authentik", "entra"], dev_login: false },
      },
    });
    renderSwitcher(entraUser());
    await userEvent.click(screen.getByRole("button", { name: /menu/i }));

    await userEvent.click(
      await screen.findByRole("menuitem", { name: /switch user/i }),
    );
    // prompt=select_account lives on /auth/link, so this is what makes the
    // IdP offer a picker rather than re-using the signed-in account.
    expect(startLinkAccount).toHaveBeenCalledWith("entra");
  });

  it("does not also list that provider as a separate Add row", async () => {
    mockFetch({
      "/auth/providers": {
        body: { providers: ["authentik", "entra"], dev_login: false },
      },
    });
    renderSwitcher(entraUser());
    await userEvent.click(screen.getByRole("button", { name: /menu/i }));

    expect(
      await screen.findByRole("menuitem", { name: /add authentik account/i }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("menuitem", { name: /add entra account/i }),
    ).not.toBeInTheDocument();
  });

  it("uses the only configured provider when the account has no identity", async () => {
    mockFetch({
      "/auth/providers": { body: { providers: ["entra"], dev_login: true } },
    });
    // A dev-login user (no idps) on an instance that does have OIDC.
    renderSwitcher(makeMe());
    await userEvent.click(screen.getByRole("button", { name: /menu/i }));

    await userEvent.click(
      await screen.findByRole("menuitem", { name: /switch user/i }),
    );
    expect(startLinkAccount).toHaveBeenCalledWith("entra");
  });

  it("asks rather than guesses when several providers and no signal", async () => {
    mockFetch({
      "/auth/providers": {
        body: { providers: ["authentik", "entra"], dev_login: false },
      },
    });
    renderSwitcher(makeMe());
    await userEvent.click(screen.getByRole("button", { name: /menu/i }));

    // Still the Settings link, and both providers get their own row.
    expect(
      await screen.findByRole("menuitem", { name: /add entra account/i }),
    ).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /switch user/i })).toHaveAttribute(
      "href",
      "/settings/account",
    );
  });
});

describe("adding an account", () => {
  it("offers one entry per configured provider", async () => {
    mockFetch({
      "/auth/providers": {
        body: { providers: ["authentik", "entra"], dev_login: false },
      },
    });
    renderSwitcher();
    await userEvent.click(screen.getByRole("button", { name: /menu/i }));

    const button = await screen.findByRole("menuitem", {
      name: /add authentik account/i,
    });
    await userEvent.click(button);
    expect(startLinkAccount).toHaveBeenCalledWith("authentik");
    expect(
      screen.getByRole("menuitem", { name: /add entra account/i }),
    ).toBeInTheDocument();
  });

  it("does not fetch providers until the menu opens", async () => {
    const fetchFn = mockFetch(NO_PROVIDERS);
    renderSwitcher();
    expect(fetchFn).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: /menu/i }));
    await waitFor(() => expect(fetchFn).toHaveBeenCalled());
  });

  it("links a dev account and reloads", async () => {
    const fetchFn = mockFetch({
      "/auth/providers": { body: { providers: [], dev_login: true } },
      "/auth/dev-login": { body: { user_id: "new" } },
    });
    renderSwitcher();
    await userEvent.click(screen.getByRole("button", { name: /menu/i }));

    const input = await screen.findByLabelText(/add a dev-login account/i);
    await userEvent.type(input, "c@example.com");
    await userEvent.click(screen.getByRole("button", { name: /link this account/i }));

    await waitFor(() => {
      const call = fetchFn.mock.calls.find(([url]) =>
        String(url).includes("/auth/dev-login"),
      );
      expect(call).toBeDefined();
      // link:true is what makes this append rather than replace the session.
      expect(JSON.parse((call![1] as RequestInit).body as string)).toEqual({
        email: "c@example.com",
        link: true,
      });
    });
    await waitFor(() => expect(reloadAsNewAccount).toHaveBeenCalled());
  });

  it("still lets you switch when the provider list fails to load", async () => {
    mockFetch({ "/auth/providers": { status: 500 } });
    renderSwitcher();
    await userEvent.click(screen.getByRole("button", { name: /menu/i }));
    expect(screen.getByText("Grace")).toBeInTheDocument();
  });
});

describe("footer actions", () => {
  it("shows Switch user beside Sign out", async () => {
    renderSwitcher();
    await userEvent.click(screen.getByRole("button", { name: /menu/i }));

    expect(
      screen.getByRole("menuitem", { name: /switch user/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("menuitem", { name: /sign out/i }),
    ).toBeInTheDocument();
  });

  it("falls back to Settings when no provider can be determined", async () => {
    renderSwitcher();
    await userEvent.click(screen.getByRole("button", { name: /menu/i }));
    expect(screen.getByRole("menuitem", { name: /switch user/i })).toHaveAttribute(
      "href",
      "/settings/account",
    );
  });

  it("signs out and returns to the login page", async () => {
    mockFetch({ ...NO_PROVIDERS, "/auth/logout": { body: { status: "ok" } } });
    renderSwitcher();
    await userEvent.click(screen.getByRole("button", { name: /menu/i }));
    await userEvent.click(screen.getByRole("menuitem", { name: /sign out/i }));
    await waitFor(() => expect(goToLogin).toHaveBeenCalled());
  });

  it("hides both when the host page already provides them", async () => {
    renderSwitcher(makeMeWithTwoAccounts(), {
      showSignOut: false,
      showManageLink: false,
    });
    await userEvent.click(screen.getByRole("button", { name: /menu/i }));
    expect(
      screen.queryByRole("menuitem", { name: /sign out/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("menuitem", { name: /switch user/i }),
    ).not.toBeInTheDocument();
  });
});
