import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import CollectionRail from "./CollectionRail";
import { mockFetch, renderWithProviders } from "@/test/utils";

const REPORTS = {
  id: "11111111-1111-1111-1111-111111111111",
  space_id: "s",
  parent_id: null,
  name: "Reports",
  description: null,
  position: 0,
  is_inherited: false,
};

const DRAFTS = {
  ...REPORTS,
  id: "22222222-2222-2222-2222-222222222222",
  parent_id: REPORTS.id,
  name: "Drafts",
  position: 0,
};

function render(collection: string | null) {
  return renderWithProviders(
    <CollectionRail
      slug="my-space"
      selection={{ view: "library", collection }}
      onSelect={() => {}}
      onDropItems={() => {}}
    />,
  );
}

/** Body of the POST that created a collection, if one was sent. */
function createdBody(fetchFn: ReturnType<typeof mockFetch>) {
  const call = fetchFn.mock.calls.find(
    ([url, init]) =>
      String(url).includes("/collections") &&
      (init as RequestInit)?.method === "POST",
  );
  return call ? JSON.parse((call[1] as RequestInit).body as string) : null;
}

beforeEach(() => {
  mockFetch({ "/api/spaces/my-space/collections": { body: [REPORTS, DRAFTS] } });
});

describe("new collection placement", () => {
  it("nests it in the collection currently open", async () => {
    const fetchFn = mockFetch({
      "/api/spaces/my-space/collections": { body: [REPORTS, DRAFTS] },
    });
    render(REPORTS.id);
    await screen.findByText("Reports");

    await userEvent.click(
      screen.getByRole("button", { name: /new collection in Reports/i }),
    );
    await userEvent.type(screen.getByRole("textbox"), "Working{Enter}");

    await waitFor(() => {
      expect(createdBody(fetchFn)).toEqual({
        name: "Working",
        parent_id: REPORTS.id,
      });
    });
  });

  it("puts it at the root when no collection is open", async () => {
    const fetchFn = mockFetch({
      "/api/spaces/my-space/collections": { body: [REPORTS, DRAFTS] },
    });
    render(null);
    await screen.findByText("Reports");

    await userEvent.click(
      screen.getByRole("button", { name: /^new collection$/i }),
    );
    await userEvent.type(screen.getByRole("textbox"), "Loose{Enter}");

    await waitFor(() => {
      // No parent_id at all, rather than an explicit null — the API
      // treats the field's absence as "root".
      expect(createdBody(fetchFn)).toEqual({ name: "Loose" });
    });
  });

  it("puts it at the root when Unfiled is open", async () => {
    const fetchFn = mockFetch({
      "/api/spaces/my-space/collections": { body: [REPORTS, DRAFTS] },
    });
    render("unfiled");
    await screen.findByText("Reports");

    await userEvent.click(
      screen.getByRole("button", { name: /^new collection$/i }),
    );
    await userEvent.type(screen.getByRole("textbox"), "Loose{Enter}");

    await waitFor(() => {
      expect(createdBody(fetchFn)).toEqual({ name: "Loose" });
    });
  });
});

describe("the folder menu", () => {
  it("offers adding a subcollection, which nests under that folder", async () => {
    const fetchFn = mockFetch({
      "/api/spaces/my-space/collections": { body: [REPORTS, DRAFTS] },
    });
    // Nothing open: without the menu this would land at the root.
    render(null);
    await screen.findByText("Reports");

    await userEvent.click(
      screen.getByRole("button", { name: /more actions for Reports/i }),
    );
    await userEvent.click(
      screen.getByRole("menuitem", { name: /add subcollection/i }),
    );
    await userEvent.type(screen.getByRole("textbox"), "Working{Enter}");

    await waitFor(() => {
      expect(createdBody(fetchFn)).toEqual({
        name: "Working",
        parent_id: REPORTS.id,
      });
    });
  });

  it("opens on a right-click too", async () => {
    render(null);
    const row = await screen.findByText("Reports");
    await userEvent.pointer({ keys: "[MouseRight]", target: row });
    expect(screen.getByRole("menuitem", { name: /rename/i })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /profile/i })).toBeInTheDocument();
  });
});

// ── Profiles ─────────────────────────────────────────────────────────────────

const MY_SPACE = {
  id: "s",
  slug: "my-space",
  name: "My space",
  is_personal: false,
  role: "owner",
  is_owner: true,
  description: null,
  columns: null,
};

const STANDARDS = {
  id: "std",
  slug: "standards",
  name: "Standards",
  is_personal: false,
  // Reached through the subscription: read-only here.
  role: "viewer",
  is_owner: false,
  is_inherited: true,
  description: null,
  columns: ["title", "field:designation"],
};

const EUROCODES = {
  ...REPORTS,
  id: "33333333-3333-3333-3333-333333333333",
  space_id: "std",
  name: "Eurocodes",
  is_inherited: true,
  space_name: "Standards",
};

describe("profiles", () => {
  it("saves a collection's own columns", async () => {
    const fetchFn = mockFetch({
      "/api/spaces/my-space/collections": { body: [REPORTS, DRAFTS] },
      "/api/me/spaces": { body: [MY_SPACE, STANDARDS] },
      "/api/collections/": { body: REPORTS },
    });
    render(null);
    await userEvent.click(
      await screen.findByRole("button", { name: /more actions for Reports/i }),
    );
    await userEvent.click(screen.getByRole("menuitem", { name: /edit profile/i }));

    const dialog = screen.getByRole("dialog", { name: /profile · reports/i });
    expect(dialog).toHaveTextContent(/use the default columns/i);
    await userEvent.click(screen.getByRole("radio", { name: /choose columns/i }));
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: /add a column/i }),
      "field:reportNumber",
    );
    await userEvent.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() => {
      const call = fetchFn.mock.calls.find(
        ([url, init]) =>
          String(url).includes(`/api/collections/${REPORTS.id}`) &&
          (init as RequestInit)?.method === "PATCH",
      );
      expect(call).toBeDefined();
      const body = JSON.parse((call![1] as RequestInit).body as string);
      expect(body.columns).toContain("field:reportNumber");
      expect(body.columns[0]).toBe("title");
    });
  });

  it("gives an inherited collection a menu without the structural writes", async () => {
    mockFetch({
      "/api/spaces/my-space/collections": { body: [REPORTS, EUROCODES] },
      "/api/me/spaces": { body: [MY_SPACE, STANDARDS] },
    });
    render(null);
    await userEvent.click(
      await screen.findByRole("button", { name: /more actions for Eurocodes/i }),
    );
    // Viewer in Standards: the profile can be read, not changed.
    await waitFor(() =>
      expect(
        screen.getByRole("menuitem", { name: /view profile/i }),
      ).toBeInTheDocument(),
    );
    expect(screen.getByRole("menuitem", { name: /download pdfs/i })).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: /rename/i })).toBeNull();
    expect(screen.queryByRole("menuitem", { name: /delete/i })).toBeNull();

    await userEvent.click(screen.getByRole("menuitem", { name: /view profile/i }));
    // Falls back to the space it lives in, not the one browsed.
    expect(screen.getByRole("dialog")).toHaveTextContent(/inherit from standards/i);
    expect(screen.queryByRole("button", { name: /^save$/i })).toBeNull();
  });

});

describe("resizing", () => {
  it("widens from the keyboard, remembers it, and resets on Home", async () => {
    localStorage.removeItem("shelf.collectionRailWidth");
    render(null);
    await screen.findByText("Reports");
    const handle = screen.getByRole("separator", {
      name: /resize the collections panel/i,
    });
    const rail = handle.parentElement!;
    expect(rail.style.width).toBe("");

    handle.focus();
    // jsdom lays nothing out, so the rail measures 0 and the nudge
    // lands on the minimum.
    await userEvent.keyboard("{ArrowRight}");
    expect(rail.style.width).toBe("160px");
    expect(localStorage.getItem("shelf.collectionRailWidth")).toBe("160");

    await userEvent.keyboard("{Home}");
    expect(rail.style.width).toBe("");
    expect(localStorage.getItem("shelf.collectionRailWidth")).toBeNull();
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});
