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
//  ├── a          (one doc)
//  │    └── a1    (one doc)
//  ├── b          (empty, and so is everything under it)
//  │    └── b1    (empty)
//  └── c          (empty itself, but c1 below it has a doc)
//       └── c1    (one doc)
const TREE = [
  coll("a", "A", "root"),
  coll("a1", "A1", "a"),
  coll("b", "B", "root"),
  coll("b1", "B1", "b"),
  coll("c", "C", "root"),
  coll("c1", "C1", "c"),
  coll("other", "Elsewhere", null),
];

describe("buildSubcollectionGroups", () => {
  it("walks the tree in sibling order, depth first", () => {
    const groups = buildSubcollectionGroups(
      "root",
      TREE,
      [doc("d1", ["a"]), doc("d2", ["a1"]), doc("d3", ["c1"])],
    );
    expect(groups.map((g) => g.collection.id)).toEqual([
      "a",
      "a1",
      "c",
      "c1",
    ]);
  });

  it("indents by depth below the open collection", () => {
    const groups = buildSubcollectionGroups("root", TREE, [doc("d", ["a1"])]);
    expect(groups.map((g) => [g.collection.id, g.depth])).toEqual([
      ["a", 0],
      ["a1", 1],
    ]);
  });

  it("prunes a branch that holds nothing at any depth", () => {
    const groups = buildSubcollectionGroups("root", TREE, [doc("d", ["a"])]);
    // B and B1 are empty all the way down, so neither appears.
    expect(groups.map((g) => g.collection.id)).toEqual(["a"]);
  });

  it("keeps an empty folder that carries the path to a full one", () => {
    // C has no documents of its own; dropping it would leave C1 looking
    // like a direct child of the open collection.
    const groups = buildSubcollectionGroups("root", TREE, [doc("d", ["c1"])]);
    expect(groups.map((g) => [g.collection.id, g.items.length])).toEqual([
      ["c", 0],
      ["c1", 1],
    ]);
  });

  it("shows a document under each subcollection it is filed in", () => {
    const groups = buildSubcollectionGroups("root", TREE, [
      doc("both", ["a", "c1"]),
    ]);
    expect(
      groups.map((g) => [g.collection.id, g.items.map((i) => i.id)]),
    ).toEqual([
      ["a", ["both"]],
      ["c", []],
      ["c1", ["both"]],
    ]);
  });

  it("ignores collections outside the open one's subtree", () => {
    const groups = buildSubcollectionGroups("root", TREE, [
      doc("d", ["other"]),
    ]);
    expect(groups).toEqual([]);
  });

  it("returns nothing when there are no documents", () => {
    expect(buildSubcollectionGroups("root", TREE, [])).toEqual([]);
  });
});
