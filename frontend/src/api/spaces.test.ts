import { describe, expect, it } from "vitest";
import { canEdit, type Space } from "./spaces";

function space(role: Space["role"]): Space {
  return {
    id: "s1",
    slug: "team",
    name: "Team",
    is_personal: false,
    role,
    is_owner: role === "owner",
  };
}

describe("canEdit", () => {
  it("is false for a viewer", () => {
    expect(canEdit(space("viewer"))).toBe(false);
  });

  it("is true for an editor and an owner", () => {
    expect(canEdit(space("editor"))).toBe(true);
    expect(canEdit(space("owner"))).toBe(true);
  });

  it("is false when the space is not loaded yet", () => {
    // The library renders before /api/me/spaces resolves; defaulting to
    // "cannot edit" keeps a write control from flashing up and then
    // disabling itself.
    expect(canEdit(undefined)).toBe(false);
    expect(canEdit(null)).toBe(false);
  });
});
