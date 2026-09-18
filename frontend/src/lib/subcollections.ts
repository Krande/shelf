import type { Collection } from "@/api/collections";
import type { Item } from "@/api/items";

export interface SubcollectionGroup {
  collection: Collection;
  /** Names from just below the open collection down to this one, so a
   *  nested folder can be labelled "Reports > Drafts" on one line. */
  path: string[];
  /** Documents filed directly in this collection. Never empty. */
  items: Item[];
}

/**
 * Group what is filed below a collection, one entry per folder that
 * holds something, in the order the rail draws them.
 *
 * A folder with no documents of its own never appears: it would be a
 * heading over nothing. Its name still shows, as part of the path on
 * the folders beneath it — "Reports > Drafts" says where Drafts lives
 * without spending a row on Reports.
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
  function walk(parentId: string, path: string[]): void {
    for (const child of byParent.get(parentId) ?? []) {
      const here = [...path, child.name];
      const mine = itemsByCollection.get(child.id) ?? [];
      if (mine.length > 0) {
        out.push({ collection: child, path: here, items: mine });
      }
      walk(child.id, here);
    }
  }
  walk(rootId, []);
  return out;
}
