import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ShortcutsHelp from "./ShortcutsHelp";

describe("ShortcutsHelp", () => {
  it("stays out of the way until asked", () => {
    render(<ShortcutsHelp />);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("lists the shortcuts that are otherwise invisible", async () => {
    render(<ShortcutsHelp />);
    await userEvent.click(
      screen.getByRole("button", { name: /keyboard shortcuts/i }),
    );

    const dialog = await screen.findByRole("dialog");
    // The ones nobody discovers by looking at the screen.
    expect(dialog).toHaveTextContent(/Ctrl \/ ⌘ \+ click a row/);
    expect(dialog).toHaveTextContent(/Backspace/);
    expect(dialog).toHaveTextContent(/Drag a row onto a collection/);
    expect(dialog).toHaveTextContent(/find bar already filled in/i);
  });

  it("closes on Escape", async () => {
    render(<ShortcutsHelp />);
    await userEvent.click(
      screen.getByRole("button", { name: /keyboard shortcuts/i }),
    );
    await screen.findByRole("dialog");

    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("closes on the close button", async () => {
    render(<ShortcutsHelp />);
    await userEvent.click(
      screen.getByRole("button", { name: /keyboard shortcuts/i }),
    );
    await screen.findByRole("dialog");

    await userEvent.click(screen.getByRole("button", { name: /close/i }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
