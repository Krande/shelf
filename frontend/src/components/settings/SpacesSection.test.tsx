import { beforeEach, describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SpacesSection from "./SpacesSection";
import { makeMe, mockFetch, renderWithProviders } from "@/test/utils";

const ADMIN = makeMe({ role: "admin", is_admin: true });

const OWNED = {
  id: "s1",
  slug: "u-abc",
  name: "My shelf",
  is_personal: true,
  role: "owner",
  is_owner: true,
};
const SHARED_VIEWER = {
  id: "s2",
  slug: "team",
  name: "Team shelf",
  is_personal: false,
  role: "viewer",
  is_owner: false,
};
const SHARED_EDITOR = { ...SHARED_VIEWER, id: "s3", slug: "proj", role: "editor" };

const MEMBERS = [
  {
    user_id: "u1",
    email: "owner@example.com",
    display_name: "Ada",
    role: "owner",
    is_owner: true,
  },
  {
    user_id: "u2",
    email: "grace@example.com",
    display_name: "Grace",
    role: "viewer",
    is_owner: false,
  },
];

// In the order /api/users returns them — it sorts by display name, and
// the picker trusts that rather than re-sorting.
const DIRECTORY = [
  { id: "u1", email: "owner@example.com", display_name: "Ada" },
  { id: "u3", email: "alan@example.com", display_name: "Alan" },
  { id: "u2", email: "grace@example.com", display_name: "Grace" },
];

function stub(spaces: unknown[] = [OWNED, SHARED_VIEWER, SHARED_EDITOR]) {
  return mockFetch({
    "/api/me/spaces": { body: spaces },
    "/api/spaces/u-abc/members": { body: MEMBERS },
    "/api/users": { body: DIRECTORY },
  });
}

beforeEach(() => {
  stub();
});

describe("listing", () => {
  it("lists every space with the caller's role", async () => {
    renderWithProviders(<SpacesSection user={makeMe()} />);
    expect(await screen.findByText("My shelf")).toBeInTheDocument();
    expect(screen.getByText(/u-abc · you are owner/)).toBeInTheDocument();
    expect(screen.getByText(/team · you are viewer/)).toBeInTheDocument();
    expect(screen.getByText(/proj · you are editor/)).toBeInTheDocument();
  });

  it("marks the personal space", async () => {
    renderWithProviders(<SpacesSection user={makeMe()} />);
    await screen.findByText("My shelf");
    expect(screen.getByText("personal")).toBeInTheDocument();
  });

  it("offers sharing only on spaces the caller owns", async () => {
    renderWithProviders(<SpacesSection user={makeMe()} />);
    await screen.findByText("My shelf");
    // One button, for the one owned space — an editor cannot widen access.
    expect(screen.getAllByRole("button", { name: /sharing/i })).toHaveLength(1);
  });

  it("reports a load failure", async () => {
    mockFetch({ "/api/me/spaces": { status: 400 } });
    renderWithProviders(<SpacesSection user={makeMe()} />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /could not load spaces/i,
    );
  });
});

describe("members", () => {
  async function openSharing() {
    renderWithProviders(<SpacesSection user={makeMe()} />);
    await screen.findByText("My shelf");
    await userEvent.click(screen.getByRole("button", { name: /sharing/i }));
  }

  it("is collapsed until asked for", async () => {
    renderWithProviders(<SpacesSection user={makeMe()} />);
    await screen.findByText("My shelf");
    expect(screen.queryByText("grace@example.com")).not.toBeInTheDocument();
  });

  it("lists members with the owner marked", async () => {
    await openSharing();
    expect(await screen.findByText("Grace")).toBeInTheDocument();
    const ownerRow = screen.getByText("owner@example.com").closest("li")!;
    expect(within(ownerRow).getByText("owner")).toBeInTheDocument();
    // The owner has no role select and no remove button.
    expect(within(ownerRow).queryByRole("combobox")).toBeNull();
    expect(
      within(ownerRow).queryByRole("button", { name: /remove/i }),
    ).toBeNull();
  });

  it("adds a member picked from the directory", async () => {
    const fetchFn = stub();
    await openSharing();
    await screen.findByText("Grace");

    await userEvent.selectOptions(
      await screen.findByLabelText(/person to add/i),
      "alan@example.com",
    );
    await userEvent.selectOptions(
      screen.getByLabelText(/role for the new member/i),
      "editor",
    );
    await userEvent.click(screen.getByRole("button", { name: /^add$/i }));

    await waitFor(() => {
      const call = fetchFn.mock.calls.find(
        ([url, init]) =>
          String(url).endsWith("/members") &&
          (init as RequestInit)?.method === "POST",
      );
      expect(call).toBeDefined();
      expect(JSON.parse((call![1] as RequestInit).body as string)).toEqual({
        email: "alan@example.com",
        role: "editor",
      });
    });
  });

  it("defaults a new member to viewer", async () => {
    const fetchFn = stub();
    await openSharing();
    await screen.findByText("Grace");
    await userEvent.selectOptions(
      await screen.findByLabelText(/person to add/i),
      "alan@example.com",
    );
    await userEvent.click(screen.getByRole("button", { name: /^add$/i }));

    await waitFor(() => {
      const call = fetchFn.mock.calls.find(
        ([url, init]) =>
          String(url).endsWith("/members") &&
          (init as RequestInit)?.method === "POST",
      );
      expect(JSON.parse((call![1] as RequestInit).body as string).role).toBe(
        "viewer",
      );
    });
  });

  it("offers only people who aren't already in the space", async () => {
    await openSharing();
    await screen.findByText("Grace");
    const picker = await screen.findByLabelText(/person to add/i);
    const options = within(picker)
      .getAllByRole("option")
      .map((o) => o.getAttribute("value"));
    // Ada owns it and Grace is a member; only Alan is left.
    expect(options).toEqual(["", "alan@example.com"]);
  });

  it("says so when there is nobody left to add", async () => {
    mockFetch({
      "/api/me/spaces": { body: [OWNED] },
      "/api/spaces/u-abc/members": { body: MEMBERS },
      // Just the owner and the existing member — nobody left to add.
      "/api/users": { body: [DIRECTORY[0], DIRECTORY[2]] },
    });
    renderWithProviders(<SpacesSection user={makeMe()} />);
    await screen.findByText("My shelf");
    await userEvent.click(screen.getByRole("button", { name: /sharing/i }));
    await screen.findByText("Grace");

    const picker = await screen.findByLabelText(/person to add/i);
    expect(picker).toBeDisabled();
    expect(picker).toHaveTextContent(/everyone already has access/i);
  });

  it("still lists people when the space has only its owner", async () => {
    mockFetch({
      "/api/me/spaces": { body: [OWNED] },
      "/api/spaces/u-abc/members": { body: [MEMBERS[0]] },
      "/api/users": { body: DIRECTORY },
    });
    renderWithProviders(<SpacesSection user={makeMe()} />);
    await screen.findByText("My shelf");
    await userEvent.click(screen.getByRole("button", { name: /sharing/i }));

    const picker = await screen.findByLabelText(/person to add/i);
    const options = within(picker)
      .getAllByRole("option")
      .map((o) => o.getAttribute("value"));
    // Ada owns it, so she's excluded; the other two remain, in the order
    // the directory returned them.
    expect(options).toEqual(["", "alan@example.com", "grace@example.com"]);
  });

  it("changes a member's role", async () => {
    const fetchFn = stub();
    await openSharing();
    await screen.findByText("Grace");

    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: /role for grace@example\.com/i }),
      "editor",
    );

    await waitFor(() => {
      const call = fetchFn.mock.calls.find(
        ([, init]) => (init as RequestInit)?.method === "PATCH",
      );
      expect(call).toBeDefined();
      expect(String(call![0])).toContain("/members/u2");
      expect(JSON.parse((call![1] as RequestInit).body as string)).toEqual({
        role: "editor",
      });
    });
  });

  it("removes a member", async () => {
    const fetchFn = stub();
    await openSharing();
    await screen.findByText("Grace");

    await userEvent.click(
      screen.getByRole("button", { name: /remove grace@example\.com/i }),
    );

    await waitFor(() => {
      const call = fetchFn.mock.calls.find(
        ([, init]) => (init as RequestInit)?.method === "DELETE",
      );
      expect(call).toBeDefined();
      expect(String(call![0])).toContain("/members/u2");
    });
  });

  it("never offers owner as an assignable role", async () => {
    await openSharing();
    await screen.findByText("Grace");
    // Role selects only — the person picker is a combobox too.
    const roleSelects = screen
      .getAllByRole("combobox")
      .filter((s) => /role/i.test(s.getAttribute("aria-label") ?? ""));
    expect(roleSelects.length).toBeGreaterThan(0);
    for (const select of roleSelects) {
      const options = within(select).getAllByRole("option").map((o) => o.textContent);
      expect(options).toEqual(["viewer", "editor"]);
    }
  });
});

describe("creating a space", () => {
  it("is offered to admins only", async () => {
    renderWithProviders(<SpacesSection user={makeMe()} />);
    await screen.findByText("My shelf");
    expect(
      screen.queryByRole("button", { name: /new space/i }),
    ).not.toBeInTheDocument();

    renderWithProviders(<SpacesSection user={ADMIN} />);
    expect(
      await screen.findByRole("button", { name: /new space/i }),
    ).toBeInTheDocument();
  });

  it("starts collapsed", async () => {
    renderWithProviders(<SpacesSection user={ADMIN} />);
    await screen.findByRole("button", { name: /new space/i });
    expect(screen.queryByLabelText(/name for the new space/i)).toBeNull();
  });

  it("creates a space by name", async () => {
    const fetchFn = stub();
    renderWithProviders(<SpacesSection user={ADMIN} />);
    await userEvent.click(
      await screen.findByRole("button", { name: /new space/i }),
    );
    await userEvent.type(
      screen.getByLabelText(/name for the new space/i),
      "Engineering",
    );
    await userEvent.click(screen.getByRole("button", { name: /^create$/i }));

    await waitFor(() => {
      const call = fetchFn.mock.calls.find(
        ([url, init]) =>
          String(url).endsWith("/api/spaces") &&
          (init as RequestInit)?.method === "POST",
      );
      expect(call).toBeDefined();
      // Slug omitted entirely, so the server derives it.
      expect(JSON.parse((call![1] as RequestInit).body as string)).toEqual({
        name: "Engineering",
      });
    });
  });

  it("passes an explicit slug when given one", async () => {
    const fetchFn = stub();
    renderWithProviders(<SpacesSection user={ADMIN} />);
    await userEvent.click(
      await screen.findByRole("button", { name: /new space/i }),
    );
    await userEvent.type(
      screen.getByLabelText(/name for the new space/i),
      "Engineering",
    );
    await userEvent.type(screen.getByLabelText(/slug for the new space/i), "eng");
    await userEvent.click(screen.getByRole("button", { name: /^create$/i }));

    await waitFor(() => {
      const call = fetchFn.mock.calls.find(
        ([url, init]) =>
          String(url).endsWith("/api/spaces") &&
          (init as RequestInit)?.method === "POST",
      );
      expect(JSON.parse((call![1] as RequestInit).body as string)).toEqual({
        name: "Engineering",
        slug: "eng",
      });
    });
  });

  it("explains a slug clash", async () => {
    mockFetch({
      "/api/me/spaces": { body: [OWNED] },
      "/api/spaces": { status: 409 },
    });
    renderWithProviders(<SpacesSection user={ADMIN} />);
    await userEvent.click(
      await screen.findByRole("button", { name: /new space/i }),
    );
    await userEvent.type(
      screen.getByLabelText(/name for the new space/i),
      "My shelf",
    );
    await userEvent.click(screen.getByRole("button", { name: /^create$/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      /slug already exists/i,
    );
  });

  it("can be dismissed", async () => {
    renderWithProviders(<SpacesSection user={ADMIN} />);
    await userEvent.click(
      await screen.findByRole("button", { name: /new space/i }),
    );
    await userEvent.click(screen.getByRole("button", { name: /cancel/i }));
    expect(screen.queryByLabelText(/name for the new space/i)).toBeNull();
  });
});
