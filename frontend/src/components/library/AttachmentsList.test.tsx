import { describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
import AttachmentsList from "./AttachmentsList";
import { mockFetch, renderWithProviders } from "@/test/utils";

const PDF = {
  id: "att-1",
  item_id: "item-1",
  filename: "paper.pdf",
  content_type: "application/pdf",
  size_bytes: 4700,
  uploaded_at: "2026-01-01T00:00:00+00:00",
};

describe("AttachmentsList", () => {
  it("shows view, download and delete without a hover", async () => {
    mockFetch({ "/api/items/item-1/attachments": { body: [PDF] } });
    renderWithProviders(<AttachmentsList itemId="item-1" />);
    await screen.findByText("paper.pdf");

    // Hover-revealed controls were how nobody found the reader; each
    // action has to be on screen from the start.
    for (const name of ["Open in reader", "Download", "Delete attachment"]) {
      const button = screen.getByRole("button", { name });
      expect(button.className).not.toMatch(/\binvisible\b/);
      expect(button).toHaveAttribute("title", name);
    }
  });

  it("drops delete where the document is read-only", async () => {
    mockFetch({ "/api/items/item-1/attachments": { body: [PDF] } });
    renderWithProviders(<AttachmentsList itemId="item-1" readOnly />);
    await screen.findByText("paper.pdf");

    expect(screen.getByRole("button", { name: "Open in reader" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Download" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete attachment" })).toBeNull();
  });
});
