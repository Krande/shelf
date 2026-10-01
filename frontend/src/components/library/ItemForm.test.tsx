import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ItemForm from "./ItemForm";
import { mockFetch, renderWithProviders } from "@/test/utils";

function render(props: Partial<Parameters<typeof ItemForm>[0]> = {}) {
  const onClose = vi.fn();
  renderWithProviders(
    <ItemForm
      open
      title="Edit Item"
      slug="my-space"
      initial={{ item_type: "document", data: { title: "Eurocode 3" } }}
      onSubmit={() => {}}
      onClose={onClose}
      {...props}
    />,
  );
  return { onClose, dialog: screen.getByRole("dialog", { name: "Edit Item" }) };
}

beforeEach(() => {
  mockFetch({ "/api/spaces/my-space/collections": { body: [] } });
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the edit window", () => {
  it("scrolls its own fields, so a long form stays on screen", () => {
    const { dialog } = render();
    expect(dialog.style.maxHeight).toMatch(/^calc\(100vh/);
    const body = dialog.querySelector(".overflow-y-auto");
    expect(body).not.toBeNull();
    expect(body).toContainElement(screen.getByDisplayValue("Eurocode 3"));
  });

  it("is resizable", () => {
    const { dialog } = render();
    expect(dialog.style.resize).toBe("both");
  });

  it("moves when its title bar is dragged", () => {
    const { dialog } = render();
    const left = parseFloat(dialog.style.left);
    const top = parseFloat(dialog.style.top);
    const bar = screen.getByText("Edit Item").parentElement!;

    fireEvent.pointerDown(bar, { clientX: 100, clientY: 50, pointerId: 1 });
    fireEvent.pointerMove(bar, { clientX: 160, clientY: 90, pointerId: 1 });
    fireEvent.pointerUp(bar, { clientX: 160, clientY: 90, pointerId: 1 });

    expect(parseFloat(dialog.style.left)).toBe(left + 60);
    expect(parseFloat(dialog.style.top)).toBe(top + 40);
  });

  it("closes on an outside click when modal", async () => {
    const { onClose, dialog } = render();
    await userEvent.click(dialog.parentElement!);
    expect(onClose).toHaveBeenCalled();
  });

  it("leaves the page behind usable when modeless", async () => {
    const { onClose, dialog } = render({ modal: false });
    const layer = dialog.parentElement!;
    // No dimming backdrop, and the layer lets clicks through to the PDF.
    expect(layer.className).not.toMatch(/bg-black/);
    expect(layer.className).toMatch(/pointer-events-none/);
    expect(dialog.className).toMatch(/pointer-events-auto/);
    await userEvent.click(layer);
    expect(onClose).not.toHaveBeenCalled();
  });
});
