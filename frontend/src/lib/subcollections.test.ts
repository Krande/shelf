import { describe, expect, it } from "vitest";
import type { Collection } from "@/api/collections";
import type { Item } from "@/api/items";
import { buildSubcollectionGroups } from "./subcollections";

function coll(id: string, name: string, parent: string | null): Collection {
  return {
    id,
    space_id: "s",
    parent_id: parent,
    name,
    description: null,
    position: 0,
  } as Collection;
}

function doc(id: string, collectionIds: string[]): Item {
  return {
    id,
    space_id: "s",
    item_type: "document",
    data: { title: id },
    collection_ids: collectionIds,
    tag_ids: [],
  } as unknown as Item;
}

// root
//  ├── reports
//  │    ├── drafts
//  │    └── final
//  ├── empty
//  │    └── alsoEmpty
//  └── docs
const TREE = [
  coll("reports", "Reports", "root"),
  coll("drafts", "Drafts", "reports"),
  coll("final", "Final", "reports"),
  coll("empty", "Empty", "root"),
  coll("alsoEmpty", "Also Empty", "empty"),
  coll("docs", "Docs", "root"),
  coll("other", "Elsewhere", null),
];

describe("buildSubcollectionGroups", () => {
  it("labels a nested folder with its path from the open collection", () => {
    const groups = buildSubcollectionGroups("root", TREE, [
      doc("d1", ["drafts"]),
      doc("d2", ["final"]),
    ]);
    expect(groups.map((g) => g.path.join(" > "))).toEqual([
      "Reports > Drafts",
      "Reports > Final",
    ]);
  });

  it("walks siblings in order, depth first", () => {
    const groups = buildSubcollectionGroups("root", TREE, [
      doc("d1", ["docs"]),
      doc("d2", ["drafts"]),
    ]);
    expect(groups.map((g) => g.collection.id)).toEqual(["drafts", "docs"]);
  });

  it("omits a folder that holds nothing itself", () => {
    // Reports is only a path here; a heading over nothing would be noise,
    // and its name still reaches the reader via the path below it.
    const groups = buildSubcollectionGroups("root", TREE, [
      doc("d", ["drafts"]),
    ]);
    expect(groups.map((g) => g.collection.id)).toEqual(["drafts"]);
    expect(groups[0].path).toEqual(["Reports", "Drafts"]);
  });

  it("includes a folder that holds documents and has children", () => {
    const groups = buildSubcollectionGroups("root", TREE, [
      doc("onReports", ["reports"]),
      doc("onDrafts", ["drafts"]),
    ]);
    expect(groups.map((g) => [g.path.join(" > "), g.items.length])).toEqual([
      ["Reports", 1],
      ["Reports > Drafts", 1],
    ]);
  });

  it("drops a branch that is empty all the way down", () => {
    const groups = buildSubcollectionGroups("root", TREE, [
      doc("d", ["docs"]),
    ]);
    expect(groups.map((g) => g.collection.id)).toEqual(["docs"]);
  });

  it("shows a document under each subcollection it is filed in", () => {
    const groups = buildSubcollectionGroups("root", TREE, [
      doc("both", ["drafts", "docs"]),
    ]);
    expect(
      groups.map((g) => [g.path.join(" > "), g.items.map((i) => i.id)]),
    ).toEqual([
      ["Reports > Drafts", ["both"]],
      ["Docs", ["both"]],
    ]);
  });

  it("ignores collections outside the open one's subtree", () => {
    expect(
      buildSubcollectionGroups("root", TREE, [doc("d", ["other"])]),
    ).toEqual([]);
  });

  it("returns nothing when there are no documents", () => {
    expect(buildSubcollectionGroups("root", TREE, [])).toEqual([]);
  });
});
