import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SpaceInheritance from "./SpaceInheritance";
import type { Space } from "@/api/spaces";
import { renderWithProviders } from "@/test/utils";

const PROJECT: Space = {
  id: "space-project",
  slug: "project-x",
  name: "Project X",
  is_personal: false,
  role: "owner",
  is_owner: true,
};

const PERSONAL: Space = {
  ...PROJECT,
  id: "space-personal",
  slug: "u-abc123",
  name: "Ada's shelf",
  is_personal: true,
};

const STANDARDS = {
  space_id: "space-standards",
  slug: "standards",
  name: "Standards",
  subscribable: true,
};

/**
 * Stub the three GETs this component fans out to, keyed by path, with
 * mutations answered generically. `mockFetch` matches on path only, and
 * these routes differ by path, so a map is enough.
 */
function stub(
  over: {
    inherits?: unknown;
    subscribable?: unknown;
    subscribers?: unknown;
  } = {},
) {
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const json = (body: unknown) =>
      new Response(JSON.stringify(body), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    if (init?.method && init.method !== "GET") return json({});
    if (url.includes("/inherits")) return json(over.inherits ?? []);
    if (url.includes("/subscribers")) return json(over.subscribers ?? []);
    if (url.includes("/subscribable")) return json(over.subscribable ?? [STANDARDS]);
    return json({});
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

beforeEach(() => {
  stub();
});

describe("what this space inherits", () => {
  it("offers the spaces that are open to be inherited", async () => {
    renderWithProviders(<SpaceInheritance space={PROJECT} />);
    // findBy on the option, not the select: the combobox renders straight
    // away with a "Loading spaces…" placeholder in it.
    expect(
      await screen.findByRole("option", { name: /Standards \(standards\)/i }),
    ).toBeInTheDocument();
  });

  it("POSTs the chosen parent", async () => {
    const fetchFn = stub();
    renderWithProviders(<SpaceInheritance space={PROJECT} />);
    await screen.findByRole("option", { name: /Standards \(standards\)/i });
    await userEvent.selectOptions(
      screen.getByRole("combobox", {
        name: /space for Project X to inherit/i,
      }),
      "standards",
    );
    await userEvent.click(screen.getByRole("button", { name: /^inherit$/i }));

    await waitFor(() => {
      const call = fetchFn.mock.calls.find(
        ([url, init]) =>
          String(url).includes("/api/spaces/project-x/inherits") &&
          (init as RequestInit)?.method === "POST",
      );
      expect(call).toBeDefined();
      expect(JSON.parse((call![1] as RequestInit).body as string)).toEqual({
        parent_slug: "standards",
      });
    });
  });

  it("does not offer a space it already inherits", async () => {
    stub({ inherits: [STANDARDS] });
    renderWithProviders(<SpaceInheritance space={PROJECT} />);
    // Wait for both queries to settle before asserting an absence.
    await screen.findByText(/standards · read-only here/i);
    const select = await screen.findByRole("combobox", {
      name: /space for Project X to inherit/i,
    });
    await waitFor(() =>
      expect(select).toHaveDisplayValue(/no spaces are open to be inherited/i),
    );
    expect(
      within(select).queryByRole("option", { name: /Standards \(standards\)/i }),
    ).toBeNull();
  });

  it("lists current subscriptions as read-only", async () => {
    stub({ inherits: [STANDARDS] });
    renderWithProviders(<SpaceInheritance space={PROJECT} />);
    expect(await screen.findByText(/standards · read-only here/i)).toBeInTheDocument();
  });

  it("says so when nothing is open to inherit", async () => {
    stub({ subscribable: [] });
    renderWithProviders(<SpaceInheritance space={PROJECT} />);
    expect(
      await screen.findByRole("option", {
        name: /no spaces are open to be inherited/i,
      }),
    ).toBeInTheDocument();
  });

  it("DELETEs a dropped subscription", async () => {
    const fetchFn = stub({ inherits: [STANDARDS] });
    renderWithProviders(<SpaceInheritance space={PROJECT} />);
    await userEvent.click(
      await screen.findByRole("button", { name: /stop inheriting Standards/i }),
    );
    await waitFor(() => {
      expect(
        fetchFn.mock.calls.find(
          ([url, init]) =>
            String(url).includes("/api/spaces/project-x/inherits/standards") &&
            (init as RequestInit)?.method === "DELETE",
        ),
      ).toBeDefined();
    });
  });
});

describe("opening this space to others", () => {
  it("is offered for a shared space", async () => {
    renderWithProviders(<SpaceInheritance space={PROJECT} />);
    expect(
      await screen.findByRole("checkbox", {
        name: /let other spaces inherit this one/i,
      }),
    ).toBeInTheDocument();
  });

  it("is hidden for a personal space", async () => {
    // Inheriting someone's personal shelf isn't the gesture this is for,
    // and the API refuses it.
    renderWithProviders(<SpaceInheritance space={PERSONAL} />);
    await screen.findByRole("combobox", {
      name: /space for Ada's shelf to inherit/i,
    });
    expect(
      screen.queryByRole("checkbox", {
        name: /let other spaces inherit this one/i,
      }),
    ).toBeNull();
  });

  it("PATCHes the flag", async () => {
    const fetchFn = stub();
    renderWithProviders(<SpaceInheritance space={PROJECT} />);
    await userEvent.click(
      await screen.findByRole("checkbox", {
        name: /let other spaces inherit this one/i,
      }),
    );
    await waitFor(() => {
      const call = fetchFn.mock.calls.find(
        ([url, init]) =>
          String(url).includes("/api/spaces/project-x/settings") &&
          (init as RequestInit)?.method === "PATCH",
      );
      expect(call).toBeDefined();
      expect(JSON.parse((call![1] as RequestInit).body as string)).toEqual({
        subscribable: true,
      });
    });
  });

  it("shows who subscribes, so the owner can revoke", async () => {
    stub({
      subscribers: [
        { space_id: "s2", slug: "u-ada", name: "Ada's shelf", subscribable: false },
      ],
    });
    renderWithProviders(<SpaceInheritance space={PROJECT} />);
    expect(await screen.findByText("Inherited by")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /revoke Ada's shelf/i }),
    ).toBeInTheDocument();
  });
});
