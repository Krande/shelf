import { afterEach, describe, expect, it } from "vitest";
import type { Collection } from "@/api/collections";
import type { Space } from "@/api/spaces";
import {
  DEFAULT_COLUMNS,
  fieldColumns,
  inheritedProfile,
  moveColumn,
  readOverride,
  resolveProfile,
  toggleColumn,
  writeOverride,
  type ColumnKey,
} from "./libraryColumns";

function space(id: string, columns: string[] | null = null): Space {
  return {
    id,
    slug: id,
    name: id[0].toUpperCase() + id.slice(1),
    is_personal: false,
    role: "editor",
    is_owner: false,
    columns,
  };
}

function coll(
  id: string,
  parent: string | null,
  columns: string[] | null = null,
  spaceId = "project",
): Collection {
  return {
    id,
    space_id: spaceId,
    parent_id: parent,
    name: id,
    description: null,
    columns,
    position: 0,
  } as Collection;
}

const STANDARD: ColumnKey[] = ["title", "field:designation", "field:edition"];

// project (browsed)            standards (inherited, has a profile)
//  ├── reports                   └── eurocodes
//  │    └── drafts                    └── part-1
//  └── specs [title, tags]
const project = space("project");
const standards = space("standards", STANDARD);
const collections = [
  coll("reports", null),
  coll("drafts", "reports"),
  coll("specs", null, ["title", "tags"]),
  coll("eurocodes", null, null, "standards"),
  coll("part-1", "eurocodes", null, "standards"),
];
const spaces = [project, standards];

describe("resolveProfile", () => {
  it("falls to the default when nothing is set", () => {
    const p = resolveProfile("drafts", collections, spaces, project);
    expect(p.columns).toEqual(DEFAULT_COLUMNS);
    expect(p.source).toEqual({ kind: "default" });
  });

  it("uses a collection's own columns", () => {
    const p = resolveProfile("specs", collections, spaces, project);
    expect(p.columns).toEqual(["title", "tags"]);
    expect(p.source).toMatchObject({ kind: "collection", id: "specs" });
  });

  it("inherits from the nearest ancestor", () => {
    const withParent = collections.map((c) =>
      c.id === "reports" ? { ...c, columns: ["title", "creator"] } : c,
    );
    const p = resolveProfile("drafts", withParent, spaces, project);
    expect(p.columns).toEqual(["title", "creator"]);
    expect(p.source).toMatchObject({ kind: "collection", id: "reports" });
  });

  it("gives an inherited collection its own space's profile", () => {
    const p = resolveProfile("part-1", collections, spaces, project);
    expect(p.columns).toEqual(STANDARD);
    expect(p.source).toMatchObject({ kind: "space", id: "standards" });
  });

  it("uses the browsed space's profile at the root", () => {
    const profiled = space("project", ["title", "type"]);
    const p = resolveProfile(null, collections, [profiled, standards], profiled);
    expect(p.columns).toEqual(["title", "type"]);
    expect(p.source).toMatchObject({ kind: "space", id: "project" });
  });

  it("ignores columns it doesn't recognise", () => {
    const odd = [coll("x", null, ["bogus", "field:designation"])];
    const p = resolveProfile("x", odd, spaces, project);
    expect(p.columns).toEqual(["field:designation"]);
  });

  it("tells a collection what inheriting would give it", () => {
    const specs = collections.find((c) => c.id === "specs")!;
    const p = inheritedProfile(specs, collections, spaces, project);
    expect(p.source).toEqual({ kind: "default" });
  });
});

describe("toggleColumn", () => {
  it("puts a column back where the reference order has it", () => {
    const next = toggleColumn(["title", "type", "updated"], "creator", DEFAULT_COLUMNS);
    expect(next).toEqual(["title", "creator", "type", "updated"]);
  });

  it("appends a field column after the built-ins", () => {
    const next = toggleColumn(["title", "type"], "field:designation", DEFAULT_COLUMNS);
    expect(next).toEqual(["title", "type", "field:designation"]);
  });

  it("removes a column, but never Title", () => {
    expect(toggleColumn(["title", "type"], "type", DEFAULT_COLUMNS)).toEqual(["title"]);
    expect(toggleColumn(["title", "type"], "title", DEFAULT_COLUMNS)).toEqual([
      "title",
      "type",
    ]);
  });
});

describe("moveColumn", () => {
  it("swaps with a neighbour and stops at the ends", () => {
    expect(moveColumn(["title", "type", "tags"], "type", -1)).toEqual([
      "type",
      "title",
      "tags",
    ]);
    expect(moveColumn(["title", "type"], "title", -1)).toEqual(["title", "type"]);
  });
});

describe("fieldColumns", () => {
  it("offers the fields of the types on screen first", () => {
    const cols = fieldColumns(["standard"]);
    expect(cols[0]).toBe("field:standardBody");
    expect(cols).toContain("field:DOI");
    expect(new Set(cols).size).toBe(cols.length);
  });
});

describe("overrides", () => {
  afterEach(() => window.localStorage.clear());

  it("are kept per profile", () => {
    const a = { kind: "space", id: "standards", name: "Standards" } as const;
    const b = { kind: "collection", id: "specs", name: "specs" } as const;
    writeOverride(a, ["title", "field:designation"]);
    expect(readOverride(a)).toEqual(["title", "field:designation"]);
    expect(readOverride(b)).toBeNull();
    writeOverride(a, null);
    expect(readOverride(a)).toBeNull();
  });
});
