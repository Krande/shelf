import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SearchSpacePopover from "./SearchSpacePopover";
import type { Space } from "@/api/spaces";

function space(over: Partial<Space> & { slug: string }): Space {
  return {
    id: `id-${over.slug}`,
    name: over.slug,
    is_personal: false,
    role: "viewer",
    is_owner: false,
    ...over,
  };
}

const SPACES: Space[] = [
  space({ slug: "u-ab12", name: "My shelf", is_personal: true, role: "owner", is_owner: true }),
  space({ slug: "projects", name: "Projects", role: "editor" }),
  space({ slug: "standards", name: "Standards", is_inherited: true }),
];

/** The popover is controlled; this holds its state like HomePage does. */
function Harness({ onChange }: { onChange?: (next: string[]) => void } = {}) {
  const [selected, setSelected] = useState<string[]>(SPACES.map((s) => s.slug));
  return (
    <SearchSpacePopover
      spaces={SPACES}
      selected={selected}
      onChange={(next) => {
        setSelected(next);
        onChange?.(next);
      }}
    />
  );
}

async function open() {
  await userEvent.click(screen.getByRole("button", { name: /spaces to search/i }));
}

describe("SearchSpacePopover", () => {
  it("lists subscribed spaces apart from the user's own", async () => {
    // They get their own checkbox because unchecking one has to mean "not
    // this library's documents" — which only works if it has one.
    render(<Harness />);
    await open();
    expect(screen.getByLabelText("My shelf")).toBeChecked();
    expect(screen.getByLabelText("Standards")).toBeChecked();
    expect(screen.getByText("Subscribed")).toBeInTheDocument();
  });

  it("filters one library out", async () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    await open();
    await userEvent.click(screen.getByLabelText("Standards"));
    expect(onChange).toHaveBeenLastCalledWith(["u-ab12", "projects"]);
  });

  it("reports the remaining set in space-list order, not click order", async () => {
    // The parent uses this as a query key; ordering it by the list keeps
    // the same selection from producing two different keys.
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    await open();
    await userEvent.click(screen.getByLabelText("My shelf"));
    await userEvent.click(screen.getByLabelText("Projects"));
    await userEvent.click(screen.getByLabelText("My shelf"));
    expect(onChange).toHaveBeenLastCalledWith(["u-ab12", "standards"]);
  });

  it("clears and restores the whole set", async () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    await open();
    await userEvent.click(screen.getByRole("button", { name: "None" }));
    expect(onChange).toHaveBeenLastCalledWith([]);
    await userEvent.click(screen.getByRole("button", { name: "All" }));
    expect(onChange).toHaveBeenLastCalledWith(["u-ab12", "projects", "standards"]);
  });

  it("says when the search has been narrowed", async () => {
    render(<Harness />);
    const button = screen.getByRole("button", { name: /spaces to search/i });
    expect(button).toHaveAttribute(
      "title",
      "Searching every space you can read",
    );
    await open();
    await userEvent.click(screen.getByLabelText("Standards"));
    expect(button).toHaveAttribute("title", "Searching 2 of 3 spaces");
  });
});
