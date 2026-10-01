import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AuditSection, { formatDetails } from "./AuditSection";
import { mockFetch, renderWithProviders } from "@/test/utils";

const ADA = "11111111-1111-1111-1111-111111111111";
const SPACE = "33333333-3333-3333-3333-333333333333";
const ITEM = "44444444-4444-4444-4444-444444444444";

const EVENT = {
  id: "e1",
  created_at: "2026-10-01T10:00:00+00:00",
  action: "attachment.download",
  via: "web",
  actor_id: ADA,
  actor_name: "Ada",
  actor_email: "a@example.com",
  space_id: SPACE,
  space_name: "Structural",
  target_type: "attachment",
  target_id: ITEM,
  target_label: "EN 1993-1-1.pdf",
  details: null,
};

function stub(next: string | null = null) {
  return mockFetch({
    "/api/admin/audit/actions": {
      body: ["attachment.download", "item.create"],
    },
    "/api/admin/audit": { body: { events: [EVENT], next } },
    "/api/admin/users": {
      body: [
        {
          id: ADA,
          email: "a@example.com",
          display_name: "Ada",
          role: "admin",
          created_at: "2026-01-01T00:00:00+00:00",
        },
      ],
    },
  });
}

/** Query strings of every audit-list request made so far. */
function auditQueries(fn: ReturnType<typeof vi.fn>): URLSearchParams[] {
  return fn.mock.calls
    .map(([url]) => String(url))
    .filter((u) => u.startsWith("/api/admin/audit?"))
    .map((u) => new URLSearchParams(u.split("?")[1]));
}

let fetchFn: ReturnType<typeof vi.fn>;
beforeEach(() => {
  fetchFn = stub();
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the audit log", () => {
  it("reads each event as a sentence", async () => {
    renderWithProviders(<AuditSection />);
    const target = await screen.findByRole("button", { name: "EN 1993-1-1.pdf" });
    expect(target.closest("td")).toHaveTextContent("Downloaded file");
    expect(screen.getByRole("button", { name: "Ada" })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "EN 1993-1-1.pdf" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Structural" })).toBeInTheDocument();
  });

  it("narrows to an object when its name is clicked", async () => {
    renderWithProviders(<AuditSection />);
    await userEvent.click(
      await screen.findByRole("button", { name: "EN 1993-1-1.pdf" }),
    );
    await waitFor(() => {
      expect(auditQueries(fetchFn).at(-1)?.get("target_id")).toBe(ITEM);
    });
    // And the chip takes it off again.
    await userEvent.click(
      screen.getByRole("button", { name: /remove filter EN 1993-1-1.pdf/i }),
    );
    await waitFor(() => {
      expect(auditQueries(fetchFn).at(-1)?.get("target_id")).toBeNull();
    });
  });

  it("filters by a family of actions", async () => {
    renderWithProviders(<AuditSection />);
    await screen.findByRole("button", { name: "EN 1993-1-1.pdf" });
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: /filter by action/i }),
      "space.",
    );
    await waitFor(() => {
      expect(auditQueries(fetchFn).at(-1)?.get("action")).toBe("space.");
    });
  });

  it("loads older pages with the cursor", async () => {
    fetchFn = stub("cursor-1");
    renderWithProviders(<AuditSection />);
    await userEvent.click(
      await screen.findByRole("button", { name: /load older/i }),
    );
    await waitFor(() => {
      expect(auditQueries(fetchFn).at(-1)?.get("before")).toBe("cursor-1");
    });
  });
});

describe("formatDetails", () => {
  it("shows a change as old → new and a list as a list", () => {
    expect(
      formatDetails({ role: ["user", "admin"], fields: ["title", "date"] }),
    ).toBe("role: user → admin · fields: title, date");
  });

  it("leaves ids out", () => {
    expect(
      formatDetails({
        item_id: ITEM,
        from_item: ITEM,
        size_bytes: 1200,
      }),
    ).toBe("size bytes: 1200");
  });

  it("is null when there is nothing to show", () => {
    expect(formatDetails(null)).toBeNull();
    expect(formatDetails({})).toBeNull();
  });
});
