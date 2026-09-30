import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import TokenSection from "./TokenSection";
import { renderWithProviders } from "@/test/utils";

const PERSONAL = {
  id: "sp-1",
  slug: "u-abc",
  name: "My shelf",
  is_personal: true,
  role: "owner",
  is_owner: true,
  is_inherited: false,
};
const PROJECT = {
  id: "sp-2",
  slug: "project",
  name: "Project X",
  is_personal: false,
  role: "editor",
  is_owner: false,
  is_inherited: false,
};
const STANDARDS = {
  id: "sp-3",
  slug: "standards",
  name: "Standards",
  is_personal: false,
  role: "viewer",
  is_owner: false,
  is_inherited: true,
};

function stub(spaces: unknown[] = [PERSONAL, PROJECT, STANDARDS]) {
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const json = (body: unknown, status = 200) =>
      new Response(JSON.stringify(body), {
        status,
        headers: { "Content-Type": "application/json" },
      });
    if (init?.method === "POST") {
      return json({ ...PERSONAL, plaintext: "shelf_abc", scopes: [] }, 201);
    }
    if (url.includes("/api/me/spaces")) return json(spaces);
    if (url.includes("/api/me/tokens")) return json([]);
    if (url.includes("/collections")) return json([]);
    return json({});
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

async function openForm() {
  renderWithProviders(<TokenSection />);
  await userEvent.click(
    await screen.findByRole("button", { name: /new token/i }),
  );
}

beforeEach(() => {
  stub();
});

describe("space scoping", () => {
  it("asks for the inherited spaces too", async () => {
    // Scoping a token to a subscribed Standards space is one of the
    // obvious things to want, and it never appears in the plain listing.
    const fetchFn = stub();
    await openForm();
    await waitFor(() => {
      expect(
        fetchFn.mock.calls.some(([url]) =>
          String(url).includes("include_inherited=true"),
        ),
      ).toBe(true);
    });
  });

  it("says what an unrestricted token reaches", async () => {
    await openForm();
    expect(
      await screen.findByText(/reaches every space you can/i),
    ).toBeInTheDocument();
  });

  it("lists every space once restriction is ticked", async () => {
    await openForm();
    await userEvent.click(
      screen.getByRole("checkbox", { name: /restrict to specific spaces/i }),
    );
    expect(
      await screen.findByRole("checkbox", { name: /Project X/ }),
    ).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /My shelf/ })).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /Standards/ })).toBeInTheDocument();
  });

  it("marks an inherited space as read-only", async () => {
    await openForm();
    await userEvent.click(
      screen.getByRole("checkbox", { name: /restrict to specific spaces/i }),
    );
    expect(
      await screen.findByText(/inherited · read-only/i),
    ).toBeInTheDocument();
  });

  it("POSTs the selected space ids", async () => {
    const fetchFn = stub();
    await openForm();
    await userEvent.type(
      screen.getByPlaceholderText(/my-import-script/i),
      "importer",
    );
    await userEvent.click(
      screen.getByRole("checkbox", { name: /restrict to specific spaces/i }),
    );
    await userEvent.click(
      await screen.findByRole("checkbox", { name: /Standards/ }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: /create token/i }),
    );

    await waitFor(() => {
      const call = fetchFn.mock.calls.find(
        ([, init]) => (init as RequestInit)?.method === "POST",
      );
      expect(call).toBeDefined();
      const body = JSON.parse((call![1] as RequestInit).body as string);
      expect(body.allowed_space_ids).toEqual(["sp-3"]);
    });
  });

  it("sends null when unrestricted", async () => {
    const fetchFn = stub();
    await openForm();
    await userEvent.type(
      screen.getByPlaceholderText(/my-import-script/i),
      "importer",
    );
    await userEvent.click(
      screen.getByRole("button", { name: /create token/i }),
    );

    await waitFor(() => {
      const call = fetchFn.mock.calls.find(
        ([, init]) => (init as RequestInit)?.method === "POST",
      );
      expect(call).toBeDefined();
      const body = JSON.parse((call![1] as RequestInit).body as string);
      expect(body.allowed_space_ids).toBeNull();
    });
  });

  it("refuses to submit an empty restriction rather than round-tripping", async () => {
    // The API rejects an empty allow-list; catching it here saves a
    // request and gives a message that names the fix.
    await openForm();
    await userEvent.type(
      screen.getByPlaceholderText(/my-import-script/i),
      "importer",
    );
    await userEvent.click(
      screen.getByRole("checkbox", { name: /restrict to specific spaces/i }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: /create token/i }),
    );
    expect(
      await screen.findByText(/pick at least one space/i),
    ).toBeInTheDocument();
  });
});

describe("editing", () => {
  const TOKEN = {
    id: "tok-1",
    name: "importer",
    prefix: "shelf_abcdef",
    scopes: ["search"],
    allowed_space_ids: ["sp-3"],
    allowed_collection_ids: null,
    include_descendants: false,
    expires_at: null,
    last_used_at: null,
    created_at: "2026-01-01T00:00:00Z",
  };

  function stubWithToken() {
    const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const json = (body: unknown, status = 200) =>
        new Response(JSON.stringify(body), {
          status,
          headers: { "Content-Type": "application/json" },
        });
      if (init?.method === "PATCH") return json(TOKEN);
      if (url.includes("/api/me/spaces"))
        return json([PERSONAL, PROJECT, STANDARDS]);
      if (url.includes("/api/me/tokens")) return json([TOKEN]);
      if (url.includes("/collections")) return json([]);
      return json({});
    });
    vi.stubGlobal("fetch", fn);
    return fn;
  }

  it("opens the form prefilled and PATCHes the changes", async () => {
    const fetchFn = stubWithToken();
    renderWithProviders(<TokenSection />);
    // The space restriction is listed by name on the row.
    expect(await screen.findByText("Standards")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /edit token/i }));

    const nameInput = screen.getByPlaceholderText(/my-import-script/i);
    expect(nameInput).toHaveValue("importer");
    expect(
      screen.getByRole("checkbox", { name: /restrict to specific spaces/i }),
    ).toBeChecked();
    expect(screen.getByRole("checkbox", { name: /Standards/ })).toBeChecked();

    await userEvent.click(screen.getByRole("checkbox", { name: "upload" }));
    await userEvent.click(
      screen.getByRole("button", { name: /save changes/i }),
    );

    await waitFor(() => {
      const call = fetchFn.mock.calls.find(
        ([, init]) => (init as RequestInit)?.method === "PATCH",
      );
      expect(call).toBeDefined();
      expect(String(call![0])).toContain("/api/me/tokens/tok-1");
      const body = JSON.parse((call![1] as RequestInit).body as string);
      expect(body.scopes).toEqual(["upload", "search"]);
      expect(body.allowed_space_ids).toEqual(["sp-3"]);
      expect(body.name).toBe("importer");
    });
  });
});
