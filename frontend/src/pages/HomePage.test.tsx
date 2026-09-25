import { beforeEach, describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import HomePage from "./HomePage";
import ProtectedRoute from "@/components/ProtectedRoute";
import type { Item } from "@/api/items";
import { resetSessionExpiryForTests } from "@/auth/expiry";
import { makeMe, mockFetch, renderWithProviders } from "@/test/utils";

/**
 * Mounted behind the guard, exactly as App.tsx does it.
 *
 * Not a detail: HomePage returns null while auth is still loading, and
 * several of its hooks sit below that return — so rendering it bare and
 * letting auth resolve underneath it changes the hook count between
 * renders and React throws. Behind ProtectedRoute the component only ever
 * mounts once already-authenticated, which is the real arrangement.
 */
function renderHome() {
  return renderWithProviders(
    <ProtectedRoute>
      <HomePage />
    </ProtectedRoute>,
  );
}

const PERSONAL = "sp-personal";
const STANDARDS = "sp-standards";

const SPACES = [
  {
    id: PERSONAL,
    slug: "u-ab12cd34",
    name: "My shelf",
    is_personal: true,
    role: "owner",
    is_owner: true,
    is_inherited: false,
  },
  {
    id: "sp-projects",
    slug: "projects",
    name: "Projects",
    is_personal: false,
    role: "editor",
    is_owner: false,
    is_inherited: false,
  },
  {
    id: STANDARDS,
    slug: "standards",
    name: "Standards",
    is_personal: false,
    role: "viewer",
    is_owner: false,
    is_inherited: true,
  },
];

function item(over: Partial<Item> & { id: string; space_id: string }): Item {
  return {
    item_type: "standard",
    data: { title: "ACME 1234 Widgets" },
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    deleted_at: null,
    collection_ids: [],
    tag_ids: [],
    ...over,
  };
}

/** Every /api/me/items URL the page has asked for, in order. */
function searchUrls(fn: ReturnType<typeof mockFetch>): string[] {
  return fn.mock.calls
    .map((c) => String(c[0]))
    .filter((u) => u.startsWith("/api/me/items"));
}

let fetchMock: ReturnType<typeof mockFetch>;

beforeEach(() => {
  resetSessionExpiryForTests();
  fetchMock = mockFetch({
    "/api/me/spaces": { body: SPACES },
    "/api/me/items": {
      body: {
        items: [item({ id: "it-1", space_id: STANDARDS })],
        total: 1,
      },
    },
    "/api/me": { body: makeMe() },
  });
});

async function typeQuery(text: string) {
  const input = await screen.findByPlaceholderText(/search your spaces/i);
  await userEvent.type(input, text);
  return input;
}

describe("HomePage search", () => {
  it("searches across spaces in one request, not one per space", async () => {
    // A fan-out over the user's spaces is what would list a standard
    // twice when their own shelf subscribes to the space holding it.
    renderHome();
    await typeQuery("acme");
    await waitFor(() => expect(searchUrls(fetchMock).length).toBeGreaterThan(0));
    expect(
      fetchMock.mock.calls.filter((c) => String(c[0]).startsWith("/api/spaces/")),
    ).toEqual([]);
  });

  it("says which space a result came out of", async () => {
    renderHome();
    await typeQuery("acme");
    expect(await screen.findByText("ACME 1234 Widgets")).toBeInTheDocument();
    expect(await screen.findByText(/Standards/)).toBeInTheDocument();
  });

  it("does not label the user's own shelf", async () => {
    // Everything used to come from there; naming it on every row would be
    // noise, so the label is for the results that need explaining.
    fetchMock = mockFetch({
      "/api/me/spaces": { body: SPACES },
      "/api/me/items": {
        body: { items: [item({ id: "it-2", space_id: PERSONAL })], total: 1 },
      },
      "/api/me": { body: makeMe() },
    });
    renderHome();
    await typeQuery("acme");
    expect(await screen.findByText("ACME 1234 Widgets")).toBeInTheDocument();
    expect(screen.queryByText(/My shelf/)).not.toBeInTheDocument();
  });

  it("asks for no space= until a library is filtered out", async () => {
    renderHome();
    await typeQuery("acme");
    await waitFor(() => expect(searchUrls(fetchMock).length).toBeGreaterThan(0));
    for (const url of searchUrls(fetchMock)) {
      expect(url).not.toContain("space=");
    }
  });

  it("drops a filtered-out library from the search", async () => {
    renderHome();
    await typeQuery("acme");
    await userEvent.click(
      await screen.findByRole("button", { name: /spaces to search/i }),
    );
    await userEvent.click(screen.getByLabelText("Standards"));

    await waitFor(() => {
      const last = searchUrls(fetchMock).at(-1) ?? "";
      expect(last).toContain("space=u-ab12cd34");
      expect(last).toContain("space=projects");
      expect(last).not.toContain("space=standards");
    });
  });

  it("stops searching when every library is filtered out", async () => {
    renderHome();
    await typeQuery("acme");
    await userEvent.click(
      await screen.findByRole("button", { name: /spaces to search/i }),
    );
    await userEvent.click(screen.getByRole("button", { name: "None" }));
    expect(
      await screen.findByText(/every space is filtered out/i),
    ).toBeInTheDocument();
  });
});
