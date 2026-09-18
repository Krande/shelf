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
      screen.getByRole("button", { name: /add subcollection/i }),
    );
    await userEvent.type(screen.getByRole("textbox"), "Working{Enter}");

    await waitFor(() => {
      expect(createdBody(fetchFn)).toEqual({
        name: "Working",
        parent_id: REPORTS.id,
      });
    });
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});
