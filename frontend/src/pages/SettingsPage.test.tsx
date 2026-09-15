import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SettingsPage from "./SettingsPage";
import { makeMe, mockFetch, renderAtRoute } from "@/test/utils";

vi.mock("@/lib/navigation", () => ({
  hardNavigate: vi.fn(),
  reloadAsNewAccount: vi.fn(),
  goToLogin: vi.fn(),
}));

// The auth state is the page's only real input; stub it rather than
// standing up a whole /api/me round trip in every case.
const useAuth = vi.hoisted(() => vi.fn());
vi.mock("@/auth/session", () => ({ useAuth }));

// The three pre-existing sections do their own data fetching and are
// tested elsewhere; here we only care that the right one is mounted.
vi.mock("@/components/settings/TokenSection", () => ({
  default: () => <div data-testid="token-section" />,
}));
vi.mock("@/components/settings/ExtractionSection", () => ({
  default: () => <div data-testid="extraction-section" />,
}));
vi.mock("@/components/settings/ProcessingSection", () => ({
  default: () => <div data-testid="processing-section" />,
}));

function renderSettings(route: string) {
  return renderAtRoute("/settings/:tab", <SettingsPage />, { route });
}

function signedInAs(over = {}) {
  useAuth.mockReturnValue({
    status: "authenticated",
    user: makeMe(over),
  });
}

beforeEach(() => {
  mockFetch({
    "/auth/providers": { body: { providers: [], dev_login: false } },
    "/api/admin/users": { body: [] },
  });
  signedInAs();
});

describe("tab visibility", () => {
  it("shows the four user tabs", () => {
    renderSettings("/settings/account");
    for (const label of ["Account", "Appearance", "Documents", "API tokens"]) {
      expect(screen.getByRole("link", { name: label })).toBeInTheDocument();
    }
  });

  it("hides Admin from non-admins", () => {
    renderSettings("/settings/account");
    expect(screen.queryByRole("link", { name: "Admin" })).not.toBeInTheDocument();
  });

  it("shows Admin to admins", () => {
    signedInAs({ role: "admin", is_admin: true });
    renderSettings("/settings/account");
    expect(screen.getByRole("link", { name: "Admin" })).toBeInTheDocument();
  });

  it("renders nothing while auth is still loading", () => {
    useAuth.mockReturnValue({ status: "loading" });
    const { container } = renderSettings("/settings/account");
    expect(container).toBeEmptyDOMElement();
  });
});

describe("tab content", () => {
  it("renders the account tab", () => {
    renderSettings("/settings/account");
    expect(screen.getByText("Signed in as")).toBeInTheDocument();
    expect(screen.getByText("Linked accounts")).toBeInTheDocument();
  });

  it("renders the appearance tab", () => {
    renderSettings("/settings/appearance");
    expect(
      screen.getByRole("radiogroup", { name: "Palette" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("radiogroup", { name: "Theme" })).toBeInTheDocument();
  });

  it("groups the three document sections on one tab", () => {
    renderSettings("/settings/documents");
    expect(screen.getByTestId("extraction-section")).toBeInTheDocument();
    expect(screen.getByTestId("processing-section")).toBeInTheDocument();
    expect(screen.getByText("Storage cleanup")).toBeInTheDocument();
  });

  it("renders the tokens tab", () => {
    renderSettings("/settings/tokens");
    expect(screen.getByTestId("token-section")).toBeInTheDocument();
  });

  it("renders the admin tab for an admin", () => {
    signedInAs({ role: "admin", is_admin: true });
    renderSettings("/settings/admin");
    expect(screen.getByText("Users")).toBeInTheDocument();
  });

  it("shows only the active tab's content", () => {
    renderSettings("/settings/appearance");
    expect(screen.queryByText("Signed in as")).not.toBeInTheDocument();
    expect(screen.queryByTestId("token-section")).not.toBeInTheDocument();
  });
});

describe("fallbacks", () => {
  it("redirects an unknown tab to account", () => {
    renderSettings("/settings/not-a-tab");
    expect(screen.getByText("Signed in as")).toBeInTheDocument();
  });

  it("redirects a non-admin away from the admin tab", () => {
    renderSettings("/settings/admin");
    expect(screen.getByText("Signed in as")).toBeInTheDocument();
    expect(screen.queryByText("Users")).not.toBeInTheDocument();
  });
});

describe("navigation", () => {
  it("switches tabs on click", async () => {
    renderSettings("/settings/account");
    await userEvent.click(screen.getByRole("link", { name: "Appearance" }));
    expect(screen.getByRole("radiogroup", { name: "Theme" })).toBeInTheDocument();
    expect(screen.queryByText("Signed in as")).not.toBeInTheDocument();
  });

  it("marks the active tab for assistive tech", () => {
    renderSettings("/settings/documents");
    expect(screen.getByRole("link", { name: "Documents" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  it("points each tab at its own URL, so a tab is linkable", () => {
    renderSettings("/settings/account");
    expect(screen.getByRole("link", { name: "Appearance" })).toHaveAttribute(
      "href",
      "/settings/appearance",
    );
  });
});
