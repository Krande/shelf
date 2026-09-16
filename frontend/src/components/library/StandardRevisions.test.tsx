import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import StandardRevisions from "./StandardRevisions";
import type { Item } from "@/api/items";
import { mockFetch, renderWithProviders } from "@/test/utils";

const ITEM: Item = {
  id: "item-2007",
  space_id: "space-1",
  item_type: "standard",
  data: { title: "ACME 1234:2007" },
  created_at: "2026-01-01T00:00:00+00:00",
  updated_at: "2026-01-01T00:00:00+00:00",
  deleted_at: null,
  collection_ids: [],
  tag_ids: [],
};

function revision(over: Record<string, unknown> = {}) {
  return {
    item_id: "item-2007",
    space_id: "space-1",
    label: "2007",
    issued_on: "2007-12-01",
    superseded: false,
    title: "ACME 1234:2007",
    is_latest: false,
    is_pinned: false,
    ...over,
  };
}

const BODY = {
  family: {
    id: "fam-1",
    body: "ACME",
    designation: "ACME 1234",
    title: "Widget design guidance",
  },
  revisions: [
    revision({ item_id: "item-2020", label: "2020", issued_on: "2020-11-01", is_latest: true }),
    revision(),
  ],
  is_latest_known: true,
};

function render(props: Partial<Parameters<typeof StandardRevisions>[0]> = {}) {
  return renderWithProviders(
    <StandardRevisions
      item={ITEM}
      spaceSlug="project"
      canEdit={false}
      canPin={false}
      {...props}
    />,
  );
}

beforeEach(() => {
  mockFetch({ "/api/items": { body: BODY } });
});

describe("revision list", () => {
  it("names the standard", async () => {
    render();
    expect(await screen.findByText("ACME ACME 1234")).toBeInTheDocument();
  });

  it("lists every edition in the dropdown", async () => {
    render();
    const select = await screen.findByRole("combobox", {
      name: /revisions of ACME 1234/i,
    });
    expect(select).toHaveValue("item-2007");
    expect(
      screen.getByRole("option", { name: /2020 · 2020-11-01 · latest/i }),
    ).toBeInTheDocument();
  });

  it("says when the open edition is superseded", async () => {
    render();
    expect(
      await screen.findByText(/superseded — 2020 is current/i),
    ).toBeInTheDocument();
  });

  it("marks the current edition as latest", async () => {
    mockFetch({
      "/api/items": {
        body: {
          ...BODY,
          revisions: [revision({ is_latest: true })],
        },
      },
    });
    render();
    expect(await screen.findByText(/latest edition/i)).toBeInTheDocument();
  });

  it("warns when a newer edition exists out of reach", async () => {
    // The honest case: don't present a stale edition as current just
    // because it's the newest one this reader can open.
    mockFetch({
      "/api/items": {
        body: {
          ...BODY,
          revisions: [revision({ is_latest: true })],
          is_latest_known: false,
        },
      },
    });
    render();
    expect(
      await screen.findByText(/newer edition exists that you don't have access to/i),
    ).toBeInTheDocument();
  });

  it("selecting another edition asks the parent to switch", async () => {
    const onSelectItem = vi.fn();
    render({ onSelectItem });
    const select = await screen.findByRole("combobox", {
      name: /revisions of ACME 1234/i,
    });
    await userEvent.selectOptions(select, "item-2020");
    expect(onSelectItem).toHaveBeenCalledWith("item-2020");
  });
});

describe("pinning", () => {
  it("is hidden from someone who does not own the space", async () => {
    render({ canPin: false });
    await screen.findByText("ACME ACME 1234");
    expect(
      screen.queryByRole("button", { name: /use this edition here/i }),
    ).toBeNull();
  });

  it("PUTs the pin for the space being viewed", async () => {
    const fetchFn = mockFetch({ "/api/items": { body: BODY }, "/api/spaces": {} });
    render({ canPin: true });
    await userEvent.click(
      await screen.findByRole("button", { name: /use this edition here/i }),
    );

    await waitFor(() => {
      const call = fetchFn.mock.calls.find(
        ([url, init]) =>
          String(url).includes("/api/spaces/project/pins/fam-1") &&
          (init as RequestInit)?.method === "PUT",
      );
      expect(call).toBeDefined();
      expect(JSON.parse((call![1] as RequestInit).body as string)).toEqual({
        item_id: "item-2007",
      });
    });
  });

  it("explains that the space is filtered once something is pinned", async () => {
    mockFetch({
      "/api/items": {
        body: { ...BODY, revisions: [revision({ is_pinned: true })] },
      },
    });
    render({ canPin: true });
    expect(
      await screen.findByText(/this space lists only 2007/i),
    ).toBeInTheDocument();
  });
});

describe("filing an item under a standard", () => {
  it("offers the form when the item isn't linked yet", async () => {
    mockFetch({ "/api/items": { status: 404 } });
    render({ canEdit: true });
    expect(
      await screen.findByRole("button", { name: /file as a standard revision/i }),
    ).toBeInTheDocument();
  });

  it("stays quiet for someone who cannot edit the item", async () => {
    // An inherited copy is read-only; offering the form would just 403.
    mockFetch({ "/api/items": { status: 404 } });
    const { container } = render({ canEdit: false });
    await waitFor(() => expect(container).toBeEmptyDOMElement());
  });

  it("prefills from the item's own standard fields", async () => {
    mockFetch({ "/api/items": { status: 404 } });
    render({
      canEdit: true,
      item: {
        ...ITEM,
        data: {
          title: "Widget design guidance",
          standardBody: "ACME",
          designation: "ACME 1234",
          edition: "2007",
        },
      },
    });
    await userEvent.click(
      await screen.findByRole("button", { name: /file as a standard revision/i }),
    );
    expect(screen.getByLabelText("Issuing body")).toHaveValue("ACME");
    expect(screen.getByLabelText("Designation")).toHaveValue("ACME 1234");
    expect(screen.getByLabelText("Edition")).toHaveValue("2007");
  });
});
