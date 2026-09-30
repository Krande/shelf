import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SearchScopePopover from "./SearchScopePopover";
import { ALL_SEARCH_SCOPES } from "@/api/items";

describe("SearchScopePopover", () => {
  it("does not submit the form it sits in", async () => {
    // The landing page puts this inside its search form, whose submit
    // navigates to the library. A trigger without an explicit type
    // defaults to submit, so opening the filter left the page.
    const onSubmit = vi.fn((e: React.FormEvent) => e.preventDefault());
    render(
      <form onSubmit={onSubmit}>
        <SearchScopePopover scope={[...ALL_SEARCH_SCOPES]} onChange={() => {}} />
      </form>,
    );

    await userEvent.click(screen.getByRole("button", { name: /search scope/i }));

    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("opens the filter in place", async () => {
    render(
      <SearchScopePopover scope={[...ALL_SEARCH_SCOPES]} onChange={() => {}} />,
    );
    await userEvent.click(screen.getByRole("button", { name: /search scope/i }));
    expect(screen.getByRole("button", { name: /^default$/i })).toBeInTheDocument();
  });

  it("searches designation by default", async () => {
    render(<SearchScopePopover scope={[...ALL_SEARCH_SCOPES]} onChange={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: /search scope/i }));
    expect(screen.getByRole("checkbox", { name: /^designation$/i })).toBeChecked();
  });

  it("adds a metadata field from More fields, keeping the defaults", async () => {
    const onChange = vi.fn();
    render(
      <SearchScopePopover
        scope={[...ALL_SEARCH_SCOPES]}
        onChange={onChange}
        preferTypes={["standard"]}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: /search scope/i }));
    // Folded until asked for, so the common case stays a short list.
    expect(screen.queryByRole("checkbox", { name: /^edition$/i })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: /more fields/i }));
    // A field that has its own scope isn't offered twice.
    expect(screen.getAllByRole("checkbox", { name: /^designation$/i })).toHaveLength(1);
    await userEvent.click(screen.getByRole("checkbox", { name: /^edition$/i }));
    expect(onChange).toHaveBeenCalledWith([...ALL_SEARCH_SCOPES, "field:edition"]);
  });

  it("shows fields already in use without having to unfold them", async () => {
    render(
      <SearchScopePopover
        scope={["title", "field:edition"]}
        onChange={() => {}}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: /search scope/i }));
    expect(screen.getByRole("button", { name: /more fields \(1\)/i })).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /^edition$/i })).toBeChecked();
  });
});
