import type { Collection } from "@/api/collections";
import type { Item } from "@/api/items";

export interface SubcollectionGroup {
  collection: Collection;
  /** 0 for a direct child of the open collection. Drives indentation. */
  depth: number;
  /** Documents filed directly in this collection. May be empty when the
   *  group is only here to carry the path down to one that isn't. */
  items: Item[];
}

/**
 * Group what is filed below a collection, as the rail draws it: one
 * entry per folder, in sibling order, depth first.
 *
 * Branches holding nothing at any depth are pruned — an empty folder is
 * noise in a list of documents. A folder that is empty itself while a
 * child has documents is kept, because dropping it would detach the
 * child from the path that explains where it lives.
 *
 * A document filed in two subcollections appears under both: the count
 * on the section header is of distinct documents, this is of where they
 * are.
 */
export function buildSubcollectionGroups(
  rootId: string,
  collections: Collection[],
  items: Item[],
): SubcollectionGroup[] {
  if (items.length === 0) return [];

  const byParent = new Map<string, Collection[]>();
  for (const c of collections) {
    if (!c.parent_id) continue;
    const siblings = byParent.get(c.parent_id);
    if (siblings) siblings.push(c);
    else byParent.set(c.parent_id, [c]);
  }

  const itemsByCollection = new Map<string, Item[]>();
  for (const it of items) {
    for (const cid of it.collection_ids) {
      const held = itemsByCollection.get(cid);
      if (held) held.push(it);
      else itemsByCollection.set(cid, [it]);
    }
  }

  const out: SubcollectionGroup[] = [];
  function walk(parentId: string, depth: number): boolean {
    let anyHere = false;
    for (const child of byParent.get(parentId) ?? []) {
      const mine = itemsByCollection.get(child.id) ?? [];
      const at = out.length;
      out.push({ collection: child, depth, items: mine });
      const deeper = walk(child.id, depth + 1);
      if (mine.length === 0 && !deeper) {
        // Nothing here and nothing below. Everything this branch pushed
        // has already been pruned, so its own header is the last entry.
        out.splice(at, 1);
      } else {
        anyHere = true;
      }
    }
    return anyHere;
  }
  walk(rootId, 0);
  return out;
}
