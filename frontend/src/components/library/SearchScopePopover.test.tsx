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
    expect(screen.getByRole("button", { name: /^all$/i })).toBeInTheDocument();
  });
});
