import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { DocumentInfoPanel } from "./DocumentInfoPanel";
import type { Item } from "@/api/items";
import type { SpaceRole } from "@/api/spaces";
import { mockFetch, renderWithProviders } from "@/test/utils";

const ITEM: Item = {
  id: "item-1",
  space_id: "space-1",
  item_type: "journalArticle",
  data: { title: "Fatigue of welded joints", publicationTitle: "J. Struct." },
  created_at: "2026-01-01T00:00:00+00:00",
  updated_at: "2026-01-01T00:00:00+00:00",
  deleted_at: null,
  collection_ids: [],
  tag_ids: ["tag-1"],
};

function stub(role: SpaceRole) {
  return mockFetch({
    "/api/attachments/att-1": {
      body: { id: "att-1", item_id: "item-1", filename: "f.pdf" },
    },
    "/api/items/item-1": { body: ITEM },
    "/api/items/item-1/attachments": { body: [] },
    "/api/items/item-1/notes": { body: [] },
    "/api/items/item-1/revisions": { status: 404, body: { detail: "none" } },
    "/api/me/spaces": {
      body: [
        {
          id: "space-1",
          slug: "project",
          name: "Project",
          is_personal: false,
          role,
          is_owner: role === "owner",
        },
      ],
    },
    "/api/spaces/project/collections": { body: [] },
    "/api/spaces/project/tags": {
      body: [{ id: "tag-1", space_id: "space-1", name: "fatigue", color: null }],
    },
  });
}

function render(onClose = vi.fn()) {
  renderWithProviders(
    <DocumentInfoPanel attachmentId="att-1" onClose={onClose} />,
  );
  return onClose;
}

describe("DocumentInfoPanel", () => {
  it("shows the details of the item the open PDF belongs to", async () => {
    stub("editor");
    render();
    expect(
      await screen.findByRole("heading", { name: "Fatigue of welded joints" }),
    ).toBeInTheDocument();
    expect(screen.getByText("J. Struct.")).toBeInTheDocument();
    // Tag ids resolved to names through the item's own space.
    expect(await screen.findByText("fatigue")).toBeInTheDocument();
  });

  it("offers editing to someone who can edit where the item lives", async () => {
    stub("editor");
    render();
    expect(await screen.findByRole("button", { name: /edit/i })).toBeInTheDocument();
  });

  it("is read-only for a viewer", async () => {
    stub("viewer");
    render();
    await screen.findByRole("heading", { name: "Fatigue of welded joints" });
    expect(screen.queryByRole("button", { name: /^edit$/i })).toBeNull();
    expect(screen.queryByRole("button", { name: "Move to trash" })).toBeNull();
  });

  it("closes from the panel's own close button", async () => {
    stub("viewer");
    const onClose = render();
    await userEvent.click(await screen.findByRole("button", { name: "Close detail" }));
    expect(onClose).toHaveBeenCalled();
  });
});
