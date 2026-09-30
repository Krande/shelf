/**
 * Which columns the library table shows, and where that choice comes from.
 *
 * Three layers, most specific first:
 *
 *   the reader's own choice   (this browser, per profile — see below)
 *   the profile               (open collection → its ancestors → its space)
 *   the built-in default
 *
 * A profile is a `columns` list on a collection or a space; `null` means
 * "not set here, ask the next one up". A collection a space inherits falls
 * back to the space it *lives* in, so Standards' folders look like
 * Standards wherever they are browsed from.
 *
 * The reader's own choice is keyed by the profile it was made against,
 * not by the collection open at the time: tweaking the columns in one
 * Eurocode folder carries to its siblings that share the profile, and
 * doesn't leak into a paper library next door.
 */

import type { Collection } from "@/api/collections";
import type { Space } from "@/api/spaces";
import { COMMON_FIELDS, ITEM_TYPES, TYPE_FIELDS, labelFor } from "@/api/itemFields";

export const BUILTIN_COLUMNS = [
  "title",
  "creator",
  "type",
  "space",
  "collection",
  "tags",
  "updated",
] as const;
export type BuiltinColumn = (typeof BUILTIN_COLUMNS)[number];

/** A table column: one of the built-ins, or `field:<name>` for any
 *  metadata field (`field:designation`). */
export type ColumnKey = BuiltinColumn | `field:${string}`;

const BUILTIN_LABELS: Record<BuiltinColumn, string> = {
  title: "Title",
  creator: "Creator",
  type: "Type",
  space: "Space",
  collection: "Collection",
  tags: "Tags",
  updated: "Updated",
};

/** Today's table, for a library nobody has profiled. The collection
 *  path can run long, so it stays opt-in. */
export const DEFAULT_COLUMNS: ColumnKey[] = [
  "title",
  "creator",
  "type",
  "space",
  "tags",
  "updated",
];

const FIELD_PREFIX = "field:";

export function isFieldColumn(key: string): key is `field:${string}` {
  return key.startsWith(FIELD_PREFIX);
}

export function fieldOf(key: `field:${string}`): string {
  return key.slice(FIELD_PREFIX.length);
}

export function isColumnKey(key: string): key is ColumnKey {
  return (
    (BUILTIN_COLUMNS as readonly string[]).includes(key) ||
    /^field:[A-Za-z][A-Za-z0-9_]{0,63}$/.test(key)
  );
}

export function columnLabel(key: ColumnKey): string {
  return isFieldColumn(key)
    ? labelFor(fieldOf(key))
    : BUILTIN_LABELS[key as BuiltinColumn];
}

/**
 * Every metadata field some item type has, as a column. Fields of the
 * `preferTypes` (the item types actually on screen) come first, so a
 * Standards library offers Designation before DOI.
 */
export function fieldColumns(preferTypes: Iterable<string> = []): ColumnKey[] {
  const preferred: string[] = [];
  for (const t of preferTypes) {
    for (const f of TYPE_FIELDS[t] ?? []) {
      if (!preferred.includes(f)) preferred.push(f);
    }
  }
  const rest = new Set<string>(COMMON_FIELDS);
  for (const fields of Object.values(TYPE_FIELDS)) {
    for (const f of fields) rest.add(f);
  }
  const others = [...rest]
    .filter((f) => !preferred.includes(f))
    .sort((a, b) => labelFor(a).localeCompare(labelFor(b)));
  return [...preferred, ...others].map((f) => `field:${f}` as const);
}

/**
 * Which item types have `key`'s field, when another field shares its
 * label — "Pages" is a standard's page count and an article's page
 * range, and two identical checkboxes don't say which is which.
 * Undefined when the label is unambiguous.
 */
export function fieldColumnHint(key: ColumnKey): string | undefined {
  if (!isFieldColumn(key)) return undefined;
  const field = fieldOf(key);
  const label = labelFor(field);
  const all = new Set<string>(COMMON_FIELDS);
  for (const fields of Object.values(TYPE_FIELDS)) for (const f of fields) all.add(f);
  const clash = [...all].some((f) => f !== field && labelFor(f) === label);
  if (!clash) return undefined;
  const types = ITEM_TYPES.filter((t) => (TYPE_FIELDS[t.value] ?? []).includes(field)).map(
    (t) => t.label,
  );
  return types.length > 0 ? types.join(", ") : undefined;
}

// ── Profiles ────────────────────────────────────────────────────────────

/** Where the columns in effect were set. */
export type ProfileSource =
  | { kind: "collection"; id: string; name: string }
  | { kind: "space"; id: string; name: string }
  | { kind: "default" };

export interface ResolvedProfile {
  columns: ColumnKey[];
  source: ProfileSource;
}

export function sourceKey(source: ProfileSource): string {
  return source.kind === "default" ? "default" : `${source.kind}:${source.id}`;
}

export function sourceName(source: ProfileSource): string {
  return source.kind === "default" ? "default" : source.name;
}

function clean(columns: string[] | null | undefined): ColumnKey[] | null {
  const valid = (columns ?? []).filter(isColumnKey);
  return valid.length > 0 ? valid : null;
}

/**
 * The profile in effect with `collectionId` open (or none, for the space
 * root). Walks the collection's ancestors, then the space the collection
 * lives in, then the browsed space, then the default.
 */
export function resolveProfile(
  collectionId: string | null,
  collections: Collection[],
  spaces: Space[],
  browsedSpace: Space | null,
): ResolvedProfile {
  const byId = new Map(collections.map((c) => [c.id, c] as const));
  let node = collectionId ? byId.get(collectionId) : undefined;
  const homeSpaceId = node?.space_id;
  // The depth cap only guards against a malformed cycle.
  for (let depth = 0; node && depth < 64; depth++) {
    const cols = clean(node.columns);
    if (cols) {
      return {
        columns: cols,
        source: { kind: "collection", id: node.id, name: node.name },
      };
    }
    node = node.parent_id ? byId.get(node.parent_id) : undefined;
  }
  const home =
    (homeSpaceId && spaces.find((s) => s.id === homeSpaceId)) || browsedSpace;
  for (const space of [home, browsedSpace]) {
    const cols = clean(space?.columns);
    if (space && cols) {
      return {
        columns: cols,
        source: { kind: "space", id: space.id, name: space.name },
      };
    }
  }
  return { columns: DEFAULT_COLUMNS, source: { kind: "default" } };
}

/**
 * The profile a collection would get if its own columns were cleared —
 * what "inherit" means in its editor.
 */
export function inheritedProfile(
  collection: Collection,
  collections: Collection[],
  spaces: Space[],
  browsedSpace: Space | null,
): ResolvedProfile {
  const without = collections.map((c) =>
    c.id === collection.id ? { ...c, columns: null } : c,
  );
  return resolveProfile(collection.id, without, spaces, browsedSpace);
}

// ── The reader's own choice ─────────────────────────────────────────────

const OVERRIDE_PREFIX = "shelf.libraryColumns.v2:";

export function readOverride(source: ProfileSource): ColumnKey[] | null {
  try {
    const raw = window.localStorage.getItem(OVERRIDE_PREFIX + sourceKey(source));
    const parsed: unknown = raw ? JSON.parse(raw) : null;
    if (!Array.isArray(parsed)) return null;
    return clean(parsed.filter((k): k is string => typeof k === "string"));
  } catch {
    return null;
  }
}

export function writeOverride(
  source: ProfileSource,
  columns: ColumnKey[] | null,
): void {
  try {
    const key = OVERRIDE_PREFIX + sourceKey(source);
    if (columns) window.localStorage.setItem(key, JSON.stringify(columns));
    else window.localStorage.removeItem(key);
  } catch {
    // Not remembered, but still applied for this session.
  }
}

/**
 * Turn one column on or off. A column switched on goes where the
 * reference order (the profile's, then the built-in table order) puts
 * it, so ticking Creator back on doesn't park it after Modified.
 */
export function toggleColumn(
  current: ColumnKey[],
  key: ColumnKey,
  reference: ColumnKey[],
): ColumnKey[] {
  if (current.includes(key)) {
    // Title is the row's link to the document; it never goes.
    return key === "title" ? current : current.filter((k) => k !== key);
  }
  const order = [...reference];
  for (const b of BUILTIN_COLUMNS) if (!order.includes(b)) order.push(b);
  const rank = (k: ColumnKey) => {
    const i = order.indexOf(k);
    return i === -1 ? Number.MAX_SAFE_INTEGER : i;
  };
  const next = [...current];
  const at = next.findIndex((k) => rank(k) > rank(key));
  if (at === -1) next.push(key);
  else next.splice(at, 0, key);
  return next;
}

/** Move `key` one place left (-1) or right (+1). */
export function moveColumn(
  current: ColumnKey[],
  key: ColumnKey,
  by: -1 | 1,
): ColumnKey[] {
  const i = current.indexOf(key);
  const j = i + by;
  if (i === -1 || j < 0 || j >= current.length) return current;
  const next = [...current];
  [next[i], next[j]] = [next[j], next[i]];
  return next;
}
