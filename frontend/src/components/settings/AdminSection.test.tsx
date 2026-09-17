import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AdminSection from "./AdminSection";
import { makeMe, mockFetch, renderWithProviders } from "@/test/utils";

const ME = makeMe({ role: "admin", is_admin: true });

const USERS = [
  {
    id: ME.id,
    email: "a@example.com",
    display_name: "Ada",
    role: "admin",
    created_at: "2026-01-02T00:00:00+00:00",
  },
  {
    id: "22222222-2222-2222-2222-222222222222",
    email: "b@example.com",
    display_name: "Grace",
    role: "user",
    created_at: "2026-03-04T00:00:00+00:00",
  },
];

function rowFor(email: string) {
  return screen.getByText(email).closest("tr")!;
}

beforeEach(() => {
  mockFetch({ "/api/admin/users": { body: USERS } });
});

describe("listing", () => {
  it("renders a row per user", async () => {
    renderWithProviders(<AdminSection user={ME} />);
    expect(await screen.findByText("Ada")).toBeInTheDocument();
    expect(screen.getByText("Grace")).toBeInTheDocument();
  });

  it("marks the caller's own row", async () => {
    renderWithProviders(<AdminSection user={ME} />);
    await screen.findByText("Ada");
    expect(within(rowFor("a@example.com")).getByText("you")).toBeInTheDocument();
    expect(within(rowFor("b@example.com")).queryByText("you")).toBeNull();
  });

  it("shows each user's current role", async () => {
    renderWithProviders(<AdminSection user={ME} />);
    await screen.findByText("Ada");
    expect(
      screen.getByRole("combobox", { name: /role for a@example\.com/i }),
    ).toHaveValue("admin");
    expect(
      screen.getByRole("combobox", { name: /role for b@example\.com/i }),
    ).toHaveValue("user");
  });

  it("explains a lost role rather than showing a bare error", async () => {
    mockFetch({ "/api/admin/users": { status: 403 } });
    renderWithProviders(<AdminSection user={ME} />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /no longer have the admin role/i,
    );
  });

  it("reports other load failures", async () => {
    // 4xx, not 5xx: the component retries 5xx by design, so a 500 here
    // would sit in backoff instead of surfacing.
    mockFetch({ "/api/admin/users": { status: 400 } });
    renderWithProviders(<AdminSection user={ME} />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /could not load users/i,
    );
  });

  it("retries a 5xx rather than giving up on a blip", async () => {
    let calls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        calls += 1;
        return calls === 1
          ? new Response("{}", { status: 503 })
          : new Response(JSON.stringify(USERS), {
              status: 200,
              headers: { "Content-Type": "application/json" },
            });
      }),
    );
    renderWithProviders(<AdminSection user={ME} />);
    expect(await screen.findByText("Grace")).toBeInTheDocument();
    expect(calls).toBeGreaterThan(1);
  });

  it("does not retry a 403", async () => {
    const fetchFn = mockFetch({ "/api/admin/users": { status: 403 } });
    renderWithProviders(<AdminSection user={ME} />);
    await screen.findByRole("alert");
    expect(fetchFn).toHaveBeenCalledTimes(1);
  });
});

describe("adding a user", () => {
  async function openForm() {
    renderWithProviders(<AdminSection user={ME} />);
    await screen.findByText("Ada");
    await userEvent.click(screen.getByRole("button", { name: /add user/i }));
  }

  it("POSTs the address, name and role", async () => {
    const fetchFn = mockFetch({
      "/api/admin/users": { body: USERS },
    });
    await openForm();

    await userEvent.type(
      screen.getByRole("textbox", { name: /email address for the new user/i }),
      "  new@example.com  ",
    );
    await userEvent.type(
      screen.getByRole("textbox", { name: /display name for the new user/i }),
      "New Person",
    );
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: /role for the new user/i }),
      "admin",
    );
    await userEvent.click(screen.getByRole("button", { name: /^add$/i }));

    await waitFor(() => {
      const call = fetchFn.mock.calls.find(
        ([, init]) => (init as RequestInit)?.method === "POST",
      );
      expect(call).toBeDefined();
      expect(JSON.parse((call![1] as RequestInit).body as string)).toEqual({
        email: "new@example.com",
        display_name: "New Person",
        role: "admin",
      });
    });
  });

  it("omits an empty display name so the server derives one", async () => {
    const fetchFn = mockFetch({ "/api/admin/users": { body: USERS } });
    await openForm();

    await userEvent.type(
      screen.getByRole("textbox", { name: /email address for the new user/i }),
      "new@example.com",
    );
    await userEvent.click(screen.getByRole("button", { name: /^add$/i }));

    await waitFor(() => {
      const call = fetchFn.mock.calls.find(
        ([, init]) => (init as RequestInit)?.method === "POST",
      );
      expect(call).toBeDefined();
      expect(
        JSON.parse((call![1] as RequestInit).body as string).display_name,
      ).toBeUndefined();
    });
  });

  it("explains a duplicate address in plain language", async () => {
    // Hand-rolled rather than mockFetch: the listing and the create share
    // a path and only differ by method, which the helper doesn't split on.
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: RequestInfo | URL, init?: RequestInit) =>
        init?.method === "POST"
          ? new Response(JSON.stringify({ detail: "already has an account" }), {
              status: 409,
              headers: { "Content-Type": "application/json" },
            })
          : new Response(JSON.stringify(USERS), {
              status: 200,
              headers: { "Content-Type": "application/json" },
            }),
      ),
    );
    await openForm();

    await userEvent.type(
      screen.getByRole("textbox", { name: /email address for the new user/i }),
      "b@example.com",
    );
    await userEvent.click(screen.getByRole("button", { name: /^add$/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      /already has an account/i,
    );
  });

  it("closes the form once the user is created", async () => {
    mockFetch({ "/api/admin/users": { body: USERS } });
    await openForm();

    await userEvent.type(
      screen.getByRole("textbox", { name: /email address for the new user/i }),
      "new@example.com",
    );
    await userEvent.click(screen.getByRole("button", { name: /^add$/i }));

    await waitFor(() => {
      expect(
        screen.queryByRole("textbox", {
          name: /email address for the new user/i,
        }),
      ).toBeNull();
    });
  });
});

describe("changing a role", () => {
  it("PATCHes the selected role", async () => {
    const fetchFn = mockFetch({
      "/api/admin/users": { body: USERS },
      [`/api/admin/users/${USERS[1].id}`]: {
        body: { ...USERS[1], role: "admin" },
      },
    });
    renderWithProviders(<AdminSection user={ME} />);
    await screen.findByText("Grace");

    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: /role for b@example\.com/i }),
      "admin",
    );

    await waitFor(() => {
      const call = fetchFn.mock.calls.find(
        ([url, init]) =>
          String(url).includes(USERS[1].id) &&
          (init as RequestInit)?.method === "PATCH",
      );
      expect(call).toBeDefined();
      expect(JSON.parse((call![1] as RequestInit).body as string)).toEqual({
        role: "admin",
      });
    });
  });

  it("explains the last-admin refusal in plain language", async () => {
    mockFetch({
      "/api/admin/users": { body: USERS },
      [`/api/admin/users/${ME.id}`]: {
        status: 409,
        body: { detail: "Cannot demote the last remaining admin" },
      },
    });
    renderWithProviders(<AdminSection user={ME} />);
    await screen.findByText("Ada");

    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: /role for a@example\.com/i }),
      "user",
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      /last admin — promote someone else first/i,
    );
  });

  it("reports an unexpected failure", async () => {
    mockFetch({
      "/api/admin/users": { body: USERS },
      [`/api/admin/users/${USERS[1].id}`]: { status: 500 },
    });
    renderWithProviders(<AdminSection user={ME} />);
    await screen.findByText("Grace");

    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: /role for b@example\.com/i }),
      "admin",
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      /could not change the role/i,
    );
  });
});

describe("renaming a user", () => {
  async function openRename(email: string) {
    renderWithProviders(<AdminSection user={ME} />);
    await screen.findByText("Grace");
    await userEvent.click(
      screen.getByRole("button", { name: new RegExp(`rename ${email}`, "i") }),
    );
  }

  it("PATCHes only the display name", async () => {
    const fetchFn = mockFetch({
      "/api/admin/users": { body: USERS },
      [`/api/admin/users/${USERS[1].id}`]: {
        body: { ...USERS[1], display_name: "Grace Hopper" },
      },
    });
    await openRename("b@example.com");

    const input = screen.getByRole("textbox", {
      name: /display name for b@example\.com/i,
    });
    await userEvent.clear(input);
    await userEvent.type(input, "  Grace Hopper  ");
    await userEvent.click(
      screen.getByRole("button", { name: /save name for b@example\.com/i }),
    );

    await waitFor(() => {
      const call = fetchFn.mock.calls.find(
        ([url, init]) =>
          String(url).includes(USERS[1].id) &&
          (init as RequestInit)?.method === "PATCH",
      );
      expect(call).toBeDefined();
      expect(JSON.parse((call![1] as RequestInit).body as string)).toEqual({
        display_name: "Grace Hopper",
      });
    });
  });

  it("seeds the field with the current name", async () => {
    await openRename("b@example.com");
    expect(
      screen.getByRole("textbox", { name: /display name for b@example\.com/i }),
    ).toHaveValue("Grace");
  });

  it("leaves the other rows alone", async () => {
    await openRename("b@example.com");
    expect(
      screen.queryByRole("textbox", {
        name: /display name for a@example\.com/i,
      }),
    ).toBeNull();
  });

  it("closes without a request when the name is unchanged", async () => {
    const fetchFn = mockFetch({ "/api/admin/users": { body: USERS } });
    await openRename("b@example.com");
    await userEvent.click(
      screen.getByRole("button", { name: /save name for b@example\.com/i }),
    );

    await waitFor(() => {
      expect(
        screen.queryByRole("textbox", {
          name: /display name for b@example\.com/i,
        }),
      ).toBeNull();
    });
    expect(
      fetchFn.mock.calls.some(
        ([, init]) => (init as RequestInit)?.method === "PATCH",
      ),
    ).toBe(false);
  });

  it("discards the edit on cancel", async () => {
    await openRename("b@example.com");
    const input = screen.getByRole("textbox", {
      name: /display name for b@example\.com/i,
    });
    await userEvent.clear(input);
    await userEvent.type(input, "Typo");
    await userEvent.click(
      screen.getByRole("button", { name: /cancel renaming b@example\.com/i }),
    );

    expect(await screen.findByText("Grace")).toBeInTheDocument();
    expect(screen.queryByText("Typo")).toBeNull();
  });

  it("closes the editor once the rename lands", async () => {
    mockFetch({
      "/api/admin/users": { body: USERS },
      [`/api/admin/users/${USERS[1].id}`]: {
        body: { ...USERS[1], display_name: "Grace Hopper" },
      },
    });
    await openRename("b@example.com");

    const input = screen.getByRole("textbox", {
      name: /display name for b@example\.com/i,
    });
    await userEvent.clear(input);
    await userEvent.type(input, "Grace Hopper");
    await userEvent.click(
      screen.getByRole("button", { name: /save name for b@example\.com/i }),
    );

    await waitFor(() => {
      expect(
        screen.queryByRole("textbox", {
          name: /display name for b@example\.com/i,
        }),
      ).toBeNull();
    });
  });

  it("reports a failed rename and keeps the editor open", async () => {
    mockFetch({
      "/api/admin/users": { body: USERS },
      [`/api/admin/users/${USERS[1].id}`]: { status: 500 },
    });
    await openRename("b@example.com");

    const input = screen.getByRole("textbox", {
      name: /display name for b@example\.com/i,
    });
    await userEvent.clear(input);
    await userEvent.type(input, "Grace Hopper");
    await userEvent.click(
      screen.getByRole("button", { name: /save name for b@example\.com/i }),
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      /could not rename/i,
    );
    expect(
      screen.getByRole("textbox", { name: /display name for b@example\.com/i }),
    ).toBeInTheDocument();
  });
});
