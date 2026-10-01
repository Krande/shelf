import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import ArchiveDownloadDialog from "./ArchiveDownloadDialog";

afterEach(() => localStorage.clear());

describe("ArchiveDownloadDialog", () => {
  it("includes nothing optional until asked", () => {
    const onConfirm = vi.fn();
    render(
      <ArchiveDownloadDialog
        title="Download things"
        onCancel={() => {}}
        onConfirm={onConfirm}
      />,
    );
    for (const box of screen.getAllByRole("checkbox")) {
      expect(box).not.toBeChecked();
    }
    fireEvent.click(screen.getByRole("button", { name: /download/i }));
    expect(onConfirm).toHaveBeenCalledWith({
      notes: false,
      annotations: false,
      revisions: false,
    });
  });

  it("passes the choices on and remembers them next time", () => {
    const onConfirm = vi.fn();
    const { unmount } = render(
      <ArchiveDownloadDialog title="D" onCancel={() => {}} onConfirm={onConfirm} />,
    );
    fireEvent.click(screen.getByLabelText(/my notes/i));
    fireEvent.click(screen.getByLabelText(/standard revision/i));
    fireEvent.click(screen.getByRole("button", { name: /download/i }));
    expect(onConfirm).toHaveBeenCalledWith({
      notes: true,
      annotations: false,
      revisions: true,
    });
    unmount();

    render(<ArchiveDownloadDialog title="D" onCancel={() => {}} onConfirm={vi.fn()} />);
    expect(screen.getByLabelText(/my notes/i)).toBeChecked();
    expect(screen.getByLabelText(/highlights/i)).not.toBeChecked();
  });

  it("cancels on Escape", () => {
    const onCancel = vi.fn();
    render(<ArchiveDownloadDialog title="D" onCancel={onCancel} onConfirm={vi.fn()} />);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onCancel).toHaveBeenCalled();
  });
});
