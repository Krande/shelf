import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  ArrowDown,
  ArrowUp,
  ChevronDown,
  ChevronRight,
  Download,
  FolderClosed,
  FolderInput,
  FolderOpen,
  FolderPlus,
  Inbox,
  LibraryBig,
  Link2,
  MoreHorizontal,
  Pencil,
  Plus,
  SlidersHorizontal,
  Trash2,
  X,
} from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  type Collection,
  createCollection,
  deleteCollection,
  listCollections,
  updateCollection,
} from "@/api/collections";
import { downloadCollectionPdfsZip } from "@/api/attachments";
import { canEdit, fetchMySpaces } from "@/api/spaces";
import {
  inheritedProfile,
  isColumnKey,
  type ColumnKey,
} from "@/lib/libraryColumns";
import ProfileModal from "./ProfileModal";

/** Drag payload: a JSON array of item ids being filed into a folder. */
export const ITEM_DRAG_TYPE = "application/x-shelf-items";

interface Selection {
  collection: string | null; // collection id, "unfiled", or null = All Items
  view: "library" | "trash";
}

interface TreeNode extends Collection {
  children: TreeNode[];
}

/**
 * Left-rail navigation: special "All Items" / "Unfiled" / "Trash"
 * entries above the collection tree, then the user's collections in
 * per-parent ``position`` order. Each row gets a kebab menu with
 * rename / description / reorder / reparent / delete; desktop users
 * can also drag-and-drop to reorder + restack.
 */
export default function CollectionRail({
  slug,
  selection,
  onSelect,
  onDropItems,
  itemTypes = [],
}: {
  slug: string | null;
  selection: Selection;
  onSelect: (s: Selection) => void;
  /** Documents dragged out of the table and dropped on a folder. Owned
   *  by the page, which is what holds the items and their current
   *  memberships. */
  onDropItems: (collectionId: string, itemIds: string[]) => void;
  /** Item types on screen, so the profile editor offers their fields
   *  first. */
  itemTypes?: string[];
}) {
  const qc = useQueryClient();
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [adding, setAdding] = useState(false);
  // Parent for the collection being drafted: an id nests it, null puts
  // it at the root. Tracked separately from `adding` so the placeholder
  // can name where it will land.
  const [addParent, setAddParent] = useState<string | null>(null);
  const [draftName, setDraftName] = useState("");

  // Editing state — only one row can be in a mode at a time.
  const [renamingId, setRenamingId] = useState<string | null>(null);
  // A space's own profile is edited in Settings → Spaces; the rail only
  // handles collections'.
  const [profileTarget, setProfileTarget] = useState<{
    kind: "collection";
    collection: Collection;
  } | null>(null);
  const [moveTarget, setMoveTarget] = useState<Collection | null>(null);

  const collections = useQuery({
    queryKey: ["collections", slug],
    queryFn: () => listCollections(slug!),
    enabled: !!slug,
  });

  // Every space this one can read, inherited ones included: a profile
  // falls back to the space its collection lives in, and whether it may
  // be edited is the caller's role *there*. Same query key as the
  // library page's, so this costs no extra request.
  const spaces = useQuery({
    queryKey: ["spaces", "with-inherited"],
    queryFn: () => fetchMySpaces({ includeInherited: true }),
  });
  const browsedSpace = useMemo(
    () => spaces.data?.find((s) => s.slug === slug) ?? null,
    [spaces.data, slug],
  );
  const homeSpace = useCallback(
    (c: Collection) => spaces.data?.find((s) => s.id === c.space_id) ?? null,
    [spaces.data],
  );
  const canEditProfile = useCallback(
    // An inherited space is listed with the caller's role through the
    // subscription (viewer); someone who edits Standards itself holds
    // it directly and is listed as editor.
    (c: Collection) => canEdit(homeSpace(c)),
    [homeSpace],
  );

  // "Download PDFs" on a folder. Server-assembled, the same archive the
  // bulk-select action produces -- the collection id goes over rather
  // than an id per item, so a big folder doesn't build a query string
  // long enough to be refused.
  const [downloadingId, setDownloadingId] = useState<string | null>(null);
  const download = useMutation({
    mutationFn: (c: Collection) => {
      setDownloadingId(c.id);
      // Zipped by the space the folder lives in: the archive is built
      // from that space's items, and an inherited folder's aren't this
      // one's.
      return downloadCollectionPdfsZip(homeSpace(c)?.slug ?? slug!, c.id);
    },
    onSettled: () => setDownloadingId(null),
    onSuccess: ({ skipped }) => {
      if (skipped > 0) {
        window.alert(
          `${skipped} PDF${skipped === 1 ? "" : "s"} could not be fetched ` +
            "from storage and were left out (see _MISSING_FILES.txt in the " +
            "ZIP).",
        );
      }
    },
    onError: (e: Error) =>
      window.alert(
        e.message === "Not Found"
          ? "Nothing to download — no PDFs in that collection."
          : `Download failed: ${e.message}`,
      ),
  });

  // Own collections and inherited ones are built into separate trees and
  // rendered as separate groups. Merging them would put a borrowed
  // "Structural" next to your own with nothing to tell them apart, and
  // the borrowed one can't be renamed, reordered or filed into — a row
  // that looks identical but behaves differently is worse than two lists.
  const tree = useMemo(
    () => buildTree((collections.data ?? []).filter((c) => !c.is_inherited)),
    [collections.data],
  );

  /** Inherited collections, grouped by the space they came from. */
  const inheritedGroups = useMemo(() => {
    const borrowed = (collections.data ?? []).filter((c) => c.is_inherited);
    const bySpace = new Map<string, { name: string; items: Collection[] }>();
    for (const c of borrowed) {
      const key = c.space_id;
      const group = bySpace.get(key) ?? {
        name: c.space_name || "Inherited",
        items: [],
      };
      group.items.push(c);
      bySpace.set(key, group);
    }
    return [...bySpace.values()].map((g) => ({
      name: g.name,
      tree: buildTree(g.items),
    }));
  }, [collections.data]);
  const byId = useMemo(() => {
    const m = new Map<string, Collection>();
    for (const c of collections.data ?? []) m.set(c.id, c);
    return m;
  }, [collections.data]);

  const create = useMutation({
    mutationFn: (name: string) =>
      createCollection(slug!, {
        name,
        ...(addParent ? { parent_id: addParent } : {}),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["collections", slug] });
      // Open the parent, or the new child lands inside a folded folder
      // and looks like nothing happened.
      if (addParent) {
        const parent = addParent;
        setExpanded((prev) => new Set(prev).add(parent));
      }
      setAdding(false);
      setAddParent(null);
      setDraftName("");
    },
  });

  /** The collection the rail is showing, when it is one this space can
   *  nest inside. New collections go in it: making a folder while
   *  inside another almost always means making it there.
   *
   *  An inherited collection is excluded — it belongs to another space
   *  and the API refuses a child, so the new one goes to the root
   *  rather than failing. */
  const selectedCollectionId =
    selection.view === "library" &&
    selection.collection &&
    selection.collection !== "unfiled" &&
    !byId.get(selection.collection)?.is_inherited
      ? selection.collection
      : null;

  function startAdding(parentId: string | null) {
    setAddParent(parentId);
    setDraftName("");
    setAdding(true);
    if (parentId) setExpanded((prev) => new Set(prev).add(parentId));
  }

  const remove = useMutation({
    mutationFn: (id: string) => deleteCollection(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["collections", slug] });
      qc.invalidateQueries({ queryKey: ["items", slug] });
    },
  });

  const update = useMutation({
    mutationFn: (vars: {
      id: string;
      patch: Parameters<typeof updateCollection>[1];
    }) => updateCollection(vars.id, vars.patch),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["collections", slug] });
    },
  });

  const ownColumns = (cols: string[] | null | undefined): ColumnKey[] | null => {
    const valid = (cols ?? []).filter(isColumnKey);
    return valid.length > 0 ? valid : null;
  };

  function toggleExpanded(id: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  // Auto-reveal the active collection: when the selection changes (or
  // collections finish loading), walk up the parent chain and expand
  // every ancestor so the active row is visible in the tree. Leaves
  // any user-driven expansions intact — this only ever adds nodes.
  useEffect(() => {
    if (selection.view !== "library") return;
    const id = selection.collection;
    if (!id || id === "unfiled") return;
    const start = byId.get(id);
    if (!start) return;
    const toOpen: string[] = [];
    let cursor = start.parent_id;
    const seen = new Set<string>();
    while (cursor && !seen.has(cursor)) {
      seen.add(cursor);
      toOpen.push(cursor);
      cursor = byId.get(cursor)?.parent_id ?? null;
    }
    if (toOpen.length === 0) return;
    setExpanded((prev) => {
      let changed = false;
      const next = new Set(prev);
      for (const pid of toOpen) {
        if (!next.has(pid)) {
          next.add(pid);
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [selection.view, selection.collection, byId]);

  function isActive(s: Selection): boolean {
    return s.view === selection.view && s.collection === selection.collection;
  }

  function siblingsOf(parentId: string | null): TreeNode[] {
    if (parentId === null) return tree;
    // Walk the tree to find the parent and return its children.
    const stack: TreeNode[] = [...tree];
    while (stack.length) {
      const n = stack.pop()!;
      if (n.id === parentId) return n.children;
      stack.push(...n.children);
    }
    return [];
  }

  function moveBy(node: Collection, delta: number) {
    const siblings = siblingsOf(node.parent_id);
    const idx = siblings.findIndex((s) => s.id === node.id);
    if (idx === -1) return;
    const target = idx + delta;
    if (target < 0 || target >= siblings.length) return;
    update.mutate({ id: node.id, patch: { position: target } });
  }

  // Build a quick descendant lookup so Move-into can disallow dropping
  // a node into itself or one of its own children.
  function isDescendant(ancestorId: string, candidateId: string): boolean {
    if (ancestorId === candidateId) return true;
    const stack = [ancestorId];
    while (stack.length) {
      const id = stack.pop()!;
      const children = (collections.data ?? []).filter(
        (c) => c.parent_id === id,
      );
      for (const child of children) {
        if (child.id === candidateId) return true;
        stack.push(child.id);
      }
    }
    return false;
  }

  function handleDrop(
    draggedId: string,
    targetId: string | null,
    position: "before" | "after" | "into",
  ) {
    if (draggedId === targetId) return;
    const dragged = byId.get(draggedId);
    if (!dragged) return;
    if (targetId && isDescendant(draggedId, targetId)) return;

    if (position === "into") {
      // Append into target as a child.
      const targetNode = targetId ? byId.get(targetId) : null;
      update.mutate({
        id: draggedId,
        patch: {
          parent_id: targetNode ? targetNode.id : null,
        },
      });
      return;
    }

    // Reorder relative to target.
    const targetNode = targetId ? byId.get(targetId) : null;
    const newParent = targetNode ? targetNode.parent_id : null;
    const siblings = siblingsOf(newParent);
    let idx = targetId
      ? siblings.findIndex((s) => s.id === targetId)
      : siblings.length;
    if (idx === -1) idx = siblings.length;
    if (position === "after") idx += 1;
    // If moving within the same parent and dropping after the original
    // slot, the removal shifts the index down by one.
    if (dragged.parent_id === newParent) {
      const oldIdx = siblings.findIndex((s) => s.id === draggedId);
      if (oldIdx !== -1 && oldIdx < idx) idx -= 1;
    }
    update.mutate({
      id: draggedId,
      patch: { parent_id: newParent, position: idx },
    });
  }

  return (
    <nav
      className="flex h-full w-56 shrink-0 flex-col overflow-y-auto border-r text-sm"
      style={{
        borderColor: "var(--color-border)",
        backgroundColor: "var(--color-bg)",
      }}
    >
      <div className="px-2 pt-3">
        <SpecialEntry
          icon={LibraryBig}
          label="All Items"
          active={isActive({ view: "library", collection: null })}
          onClick={() => onSelect({ view: "library", collection: null })}
        />
        <SpecialEntry
          icon={Inbox}
          label="Unfiled"
          active={isActive({ view: "library", collection: "unfiled" })}
          onClick={() =>
            onSelect({ view: "library", collection: "unfiled" })
          }
        />
        <SpecialEntry
          icon={Trash2}
          label="Trash"
          active={isActive({ view: "trash", collection: null })}
          onClick={() => onSelect({ view: "trash", collection: null })}
        />
      </div>

      <div
        className="my-2 border-t"
        style={{ borderColor: "var(--color-border)" }}
      />

      <div className="flex-1 px-2">
        <div
          className="flex items-center justify-between px-1 py-1 text-xs uppercase tracking-wider"
          style={{ color: "var(--color-text-muted)" }}
        >
          <span>Collections</span>
          <button
            onClick={() => startAdding(selectedCollectionId)}
            aria-label={
              selectedCollectionId
                ? `New collection in ${byId.get(selectedCollectionId)?.name ?? "the open collection"}`
                : "New collection"
            }
            title={
              selectedCollectionId
                ? `New collection in ${byId.get(selectedCollectionId)?.name ?? ""}`
                : "New collection"
            }
            className="rounded p-0.5 hover:opacity-70"
            disabled={!slug}
          >
            <Plus className="h-3.5 w-3.5" />
          </button>
        </div>
        {adding && (
          <form
            className="mb-1 px-1"
            onSubmit={(e) => {
              e.preventDefault();
              if (draftName.trim()) create.mutate(draftName.trim());
            }}
          >
            <input
              type="text"
              autoFocus
              value={draftName}
              onChange={(e) => setDraftName(e.target.value)}
              onBlur={() => {
                if (!draftName.trim()) {
                  setAdding(false);
                  setAddParent(null);
                  setDraftName("");
                }
              }}
              placeholder={
                addParent
                  ? `New collection in ${byId.get(addParent)?.name ?? "…"}`
                  : "Collection name"
              }
              className="w-full rounded border px-2 py-1 text-sm"
              style={{
                backgroundColor: "var(--color-surface)",
                borderColor: "var(--color-border)",
                color: "var(--color-text)",
              }}
            />
          </form>
        )}
        {collections.data && collections.data.length === 0 && !adding && (
          <p
            className="px-1 py-2 text-xs italic"
            style={{ color: "var(--color-text-muted)" }}
          >
            No collections yet.
          </p>
        )}
        {tree.map((node, idx) => (
          <CollectionNode
            key={node.id}
            node={node}
            depth={0}
            siblingIndex={idx}
            siblingCount={tree.length}
            expanded={expanded}
            onToggle={toggleExpanded}
            activeId={
              selection.view === "library" &&
              selection.collection !== "unfiled"
                ? selection.collection
                : null
            }
            onSelect={(id) =>
              onSelect({ view: "library", collection: id })
            }
            renamingId={renamingId}
            onStartRename={(id) => setRenamingId(id)}
            onCancelRename={() => setRenamingId(null)}
            onCommitRename={(id, name) => {
              setRenamingId(null);
              const trimmed = name.trim();
              if (!trimmed) return;
              update.mutate({ id, patch: { name: trimmed } });
            }}
            onEditProfile={(c) =>
              setProfileTarget({ kind: "collection", collection: c })
            }
            canEditProfile={canEditProfile}
            onMoveInto={(c) => setMoveTarget(c)}
            onMoveBy={moveBy}
            onDrop={handleDrop}
            onDelete={(id, name) => {
              if (
                window.confirm(
                  `Delete collection "${name}"? Items inside will be unfiled but not deleted.`,
                )
              ) {
                remove.mutate(id);
              }
            }}
            onDownload={(c) => download.mutate(c)}
            downloadingId={download.isPending ? downloadingId : null}
            onDropItems={onDropItems}
            onAddChild={startAdding}
          />
        ))}

        {inheritedGroups.map((group) => (
          <div key={group.name} className="mt-3">
            <div
              className="flex items-center gap-1 px-1 py-1 text-xs uppercase tracking-wider"
              style={{ color: "var(--color-text-muted)" }}
              title={`Inherited from ${group.name} — read-only here`}
            >
              <Link2 className="h-3 w-3 shrink-0" />
              <span className="truncate">{group.name}</span>
            </div>
            {group.tree.map((node, idx) => (
              <CollectionNode
                key={node.id}
                node={node}
                depth={0}
                siblingIndex={idx}
                siblingCount={group.tree.length}
                expanded={expanded}
                onToggle={toggleExpanded}
                activeId={
                  selection.view === "library" &&
                  selection.collection !== "unfiled"
                    ? selection.collection
                    : null
                }
                onSelect={(id) => onSelect({ view: "library", collection: id })}
                // The structural writes belong to the collection's own
                // space and the API refuses them from here, so the row
                // offers none of them. Its profile and its PDFs are still
                // reachable.
                readOnly
                renamingId={null}
                onStartRename={() => {}}
                onCancelRename={() => {}}
                onCommitRename={() => {}}
                onEditProfile={(c) =>
                  setProfileTarget({ kind: "collection", collection: c })
                }
                canEditProfile={canEditProfile}
                onMoveInto={() => {}}
                onMoveBy={() => {}}
                onDrop={() => {}}
                onDelete={() => {}}
                onDownload={(c) => download.mutate(c)}
                downloadingId={download.isPending ? downloadingId : null}
                onDropItems={() => {}}
                onAddChild={() => {}}
              />
            ))}
          </div>
        ))}
      </div>

      {profileTarget?.kind === "collection" && (
        <ProfileModal
          title={`Profile · ${profileTarget.collection.name}`}
          kind="collection"
          description={profileTarget.collection.description}
          columns={ownColumns(profileTarget.collection.columns)}
          inherited={inheritedProfile(
            profileTarget.collection,
            collections.data ?? [],
            spaces.data ?? [],
            browsedSpace,
          )}
          preferTypes={itemTypes}
          readOnly={!canEditProfile(profileTarget.collection)}
          onClose={() => setProfileTarget(null)}
          onSave={(draft) => {
            update.mutate({
              id: profileTarget.collection.id,
              patch: { description: draft.description, columns: draft.columns },
            });
            setProfileTarget(null);
          }}
        />
      )}


      {moveTarget && (
        <MoveIntoModal
          collection={moveTarget}
          allCollections={collections.data ?? []}
          tree={tree}
          isDescendant={isDescendant}
          onClose={() => setMoveTarget(null)}
          onPick={(parentId) => {
            update.mutate({
              id: moveTarget.id,
              patch: { parent_id: parentId },
            });
            setMoveTarget(null);
          }}
        />
      )}
    </nav>
  );
}

function SpecialEntry({
  icon: Icon,
  label,
  active,
  onClick,
}: {
  icon: typeof LibraryBig;
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className="mb-0.5 flex w-full items-center gap-2 rounded px-2 py-1 text-left hover:opacity-90"
      style={{
        backgroundColor: active
          ? "color-mix(in srgb, var(--color-accent) 15%, transparent)"
          : "transparent",
        color: active ? "var(--color-accent)" : "var(--color-text)",
      }}
    >
      <Icon className="h-4 w-4" />
      {label}
    </button>
  );
}

function buildTree(rows: Collection[]): TreeNode[] {
  const byId = new Map<string, TreeNode>(
    rows.map((r) => [r.id, { ...r, children: [] }]),
  );
  const roots: TreeNode[] = [];
  for (const node of byId.values()) {
    if (node.parent_id && byId.has(node.parent_id)) {
      byId.get(node.parent_id)!.children.push(node);
    } else {
      roots.push(node);
    }
  }
  // Sort each level by position; the server already returns rows in
  // position order, but defensively re-sort so position changes that
  // haven't round-tripped yet (optimistic) still render correctly.
  const sortRec = (nodes: TreeNode[]) => {
    nodes.sort((a, b) => a.position - b.position);
    for (const n of nodes) sortRec(n.children);
  };
  sortRec(roots);
  return roots;
}

function CollectionNode({
  node,
  depth,
  siblingIndex,
  siblingCount,
  expanded,
  onToggle,
  activeId,
  onSelect,
  renamingId,
  onStartRename,
  onCancelRename,
  onCommitRename,
  onEditProfile,
  onMoveInto,
  onMoveBy,
  onDrop,
  onDelete,
  onDownload,
  downloadingId,
  onDropItems,
  onAddChild,
  canEditProfile,
  readOnly = false,
}: {
  node: TreeNode;
  depth: number;
  siblingIndex: number;
  siblingCount: number;
  expanded: Set<string>;
  onToggle: (id: string) => void;
  activeId: string | null;
  onSelect: (id: string) => void;
  renamingId: string | null;
  onStartRename: (id: string) => void;
  onCancelRename: () => void;
  onCommitRename: (id: string, name: string) => void;
  onEditProfile: (c: Collection) => void;
  onMoveInto: (c: Collection) => void;
  onMoveBy: (c: Collection, delta: number) => void;
  onDrop: (
    draggedId: string,
    targetId: string | null,
    position: "before" | "after" | "into",
  ) => void;
  onDelete: (id: string, name: string) => void;
  onDownload: (c: Collection) => void;
  /** Collection whose zip is currently being built, if any. */
  downloadingId: string | null;
  /** Documents dragged from the table onto this folder. */
  onDropItems: (collectionId: string, itemIds: string[]) => void;
  /** Start drafting a collection nested inside this one. */
  onAddChild: (parentId: string) => void;
  /** Whether the caller may change this collection's profile — decided
   *  by their role in the space it lives in, which for an inherited
   *  collection isn't the one being browsed. */
  canEditProfile: (c: Collection) => boolean;
  /** Inherited from another space: browsable, and its profile and PDFs
   *  are reachable, but the structural writes (rename, move, delete,
   *  filing) are refused by the API, so none are offered. */
  readOnly?: boolean;
}) {
  const hasChildren = node.children.length > 0;
  const isOpen = expanded.has(node.id);
  const active = activeId === node.id;
  const renaming = renamingId === node.id;
  const [menuAt, setMenuAt] = useState<MenuAnchor | null>(null);
  const [dropZone, setDropZone] = useState<
    "before" | "after" | "into" | null
  >(null);

  function handleDragOver(e: React.DragEvent) {
    // Documents dropped on a folder only ever mean "file them here",
    // so there is no before/after to aim at — the whole row is one
    // target. An inherited folder takes neither: it belongs to another
    // space and the API refuses the write.
    if (e.dataTransfer.types.includes(ITEM_DRAG_TYPE)) {
      if (readOnly) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = "copy";
      setDropZone("into");
      return;
    }
    if (!e.dataTransfer.types.includes("application/x-shelf-collection")) {
      return;
    }
    e.preventDefault();
    // Map the cursor's vertical position within the row to a zone.
    // Top 25% → before, bottom 25% → after, middle → into.
    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
    const y = e.clientY - rect.top;
    const h = rect.height;
    if (y < h * 0.25) setDropZone("before");
    else if (y > h * 0.75) setDropZone("after");
    else setDropZone("into");
  }

  function handleDrop(e: React.DragEvent) {
    const dropped = e.dataTransfer.getData(ITEM_DRAG_TYPE);
    if (dropped) {
      setDropZone(null);
      if (readOnly) return;
      e.preventDefault();
      try {
        const ids: unknown = JSON.parse(dropped);
        if (Array.isArray(ids) && ids.length > 0) {
          onDropItems(node.id, ids as string[]);
        }
      } catch {
        // Not ours, or mangled in transit — drop it on the floor
        // rather than throwing inside a drag handler.
      }
      return;
    }
    const draggedId = e.dataTransfer.getData(
      "application/x-shelf-collection",
    );
    setDropZone(null);
    if (!draggedId || !dropZone) return;
    e.preventDefault();
    onDrop(draggedId, node.id, dropZone);
  }

  return (
    <div>
      <div
        draggable={!renaming && !readOnly}
        onDragStart={(e) => {
          e.dataTransfer.setData(
            "application/x-shelf-collection",
            node.id,
          );
          e.dataTransfer.effectAllowed = "move";
        }}
        onDragOver={handleDragOver}
        onDragLeave={() => setDropZone(null)}
        onDrop={handleDrop}
        onClick={() => !renaming && onSelect(node.id)}
        onContextMenu={(e) => {
          if (renaming) return;
          e.preventDefault();
          setMenuAt(anchorAtPointer(e));
        }}
        className="group relative flex cursor-pointer items-center gap-1 rounded px-1 py-1 hover:opacity-90"
        style={{
          paddingLeft: `${depth * 12 + 4}px`,
          backgroundColor:
            dropZone === "into"
              ? "color-mix(in srgb, var(--color-accent) 25%, transparent)"
              : active
                ? "color-mix(in srgb, var(--color-accent) 15%, transparent)"
                : "transparent",
          color: active ? "var(--color-accent)" : "var(--color-text)",
        }}
      >
        {dropZone === "before" && <DropIndicator position="top" />}
        {dropZone === "after" && <DropIndicator position="bottom" />}
        {hasChildren ? (
          <button
            onClick={(e) => {
              e.stopPropagation();
              onToggle(node.id);
            }}
            aria-label={isOpen ? "Collapse" : "Expand"}
            className="rounded p-0.5 hover:opacity-70"
          >
            {isOpen ? (
              <ChevronDown className="h-3 w-3" />
            ) : (
              <ChevronRight className="h-3 w-3" />
            )}
          </button>
        ) : (
          <span className="w-4" />
        )}
        {hasChildren && isOpen ? (
          <FolderOpen className="h-4 w-4" />
        ) : (
          <FolderClosed className="h-4 w-4" />
        )}
        {renaming ? (
          <RenameInput
            initial={node.name}
            onCancel={onCancelRename}
            onCommit={(name) => onCommitRename(node.id, name)}
          />
        ) : (
          <span
            className="flex-1 truncate"
            title={node.description ?? undefined}
          >
            {node.name}
          </span>
        )}
        {!renaming && (
          <NodeMenu
            node={node}
            at={menuAt}
            setAt={setMenuAt}
            readOnly={readOnly}
            canEditProfile={canEditProfile(node)}
            isFirst={siblingIndex === 0}
            isLast={siblingIndex === siblingCount - 1}
            onAddChild={() => onAddChild(node.id)}
            onRename={() => onStartRename(node.id)}
            onEditProfile={() => onEditProfile(node)}
            onMoveInto={() => onMoveInto(node)}
            onMoveUp={() => onMoveBy(node, -1)}
            onMoveDown={() => onMoveBy(node, +1)}
            onDownload={() => onDownload(node)}
            downloading={downloadingId === node.id}
            onDelete={() => onDelete(node.id, node.name)}
          />
        )}
      </div>
      {hasChildren && isOpen && (
        <div>
          {node.children.map((child, idx) => (
            <CollectionNode
              key={child.id}
              node={child}
              depth={depth + 1}
              siblingIndex={idx}
              siblingCount={node.children.length}
              expanded={expanded}
              onToggle={onToggle}
              activeId={activeId}
              onSelect={onSelect}
              renamingId={renamingId}
              onStartRename={onStartRename}
              onCancelRename={onCancelRename}
              onCommitRename={onCommitRename}
              onEditProfile={onEditProfile}
              onMoveInto={onMoveInto}
              onMoveBy={onMoveBy}
              onDrop={onDrop}
              onDelete={onDelete}
              onDownload={onDownload}
              downloadingId={downloadingId}
              onDropItems={onDropItems}
              onAddChild={onAddChild}
              canEditProfile={canEditProfile}
              readOnly={readOnly}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function DropIndicator({ position }: { position: "top" | "bottom" }) {
  return (
    <span
      aria-hidden
      className="pointer-events-none absolute left-2 right-2 h-0.5 rounded-full"
      style={{
        top: position === "top" ? "-1px" : undefined,
        bottom: position === "bottom" ? "-1px" : undefined,
        backgroundColor: "var(--color-accent)",
      }}
    />
  );
}

function RenameInput({
  initial,
  onCancel,
  onCommit,
}: {
  initial: string;
  onCancel: () => void;
  onCommit: (name: string) => void;
}) {
  const [value, setValue] = useState(initial);
  return (
    <input
      type="text"
      autoFocus
      value={value}
      onChange={(e) => setValue(e.target.value)}
      onClick={(e) => e.stopPropagation()}
      onKeyDown={(e) => {
        if (e.key === "Enter") onCommit(value);
        else if (e.key === "Escape") onCancel();
      }}
      onBlur={() => {
        // Commit on blur so tapping away on touch saves the edit.
        if (value.trim() && value.trim() !== initial) onCommit(value);
        else onCancel();
      }}
      className="flex-1 rounded border px-1 py-0.5 text-sm"
      style={{
        backgroundColor: "var(--color-surface)",
        borderColor: "var(--color-border)",
        color: "var(--color-text)",
      }}
    />
  );
}

/** Where a menu opens: under the kebab (right-aligned to it), or at the
 *  pointer for a right-click. */
type MenuAnchor = { top: number; left?: number; right?: number };

/** Right-click position, nudged so a menu near the window's edge opens
 *  inward rather than off-screen. */
function anchorAtPointer(e: React.MouseEvent): MenuAnchor {
  return {
    top: Math.min(e.clientY, window.innerHeight - 340),
    left: Math.min(e.clientX, window.innerWidth - 200),
  };
}

/**
 * A popup of actions, portal-rendered to document.body so the row's
 * hover-opacity doesn't cascade into it. The CSS ``opacity`` property
 * multiplies through descendants — on mobile, sticky hover after a tap
 * was making the menu near-unreadable.
 *
 * Closes on an outside click, Escape, a resize or a scroll: a menu left
 * floating where its row used to be is worse than one that goes away.
 */
function ActionMenu({
  at,
  onClose,
  ignore,
  children,
}: {
  at: MenuAnchor;
  onClose: () => void;
  /** The button that toggles the menu: a click on it is its own toggle,
   *  not an outside click. */
  ignore?: React.RefObject<HTMLElement | null>;
  children: React.ReactNode;
}) {
  const menuRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    function onClickOutside(e: MouseEvent) {
      const target = e.target as Node;
      if (ignore?.current?.contains(target)) return;
      if (menuRef.current?.contains(target)) return;
      onClose();
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("mousedown", onClickOutside);
    document.addEventListener("keydown", onKey);
    window.addEventListener("resize", onClose);
    window.addEventListener("scroll", onClose, true);
    return () => {
      document.removeEventListener("mousedown", onClickOutside);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", onClose);
      window.removeEventListener("scroll", onClose, true);
    };
  }, [onClose, ignore]);

  return createPortal(
    <div
      ref={menuRef}
      role="menu"
      className="fixed z-50 min-w-[180px] rounded border py-1 shadow-lg"
      style={{
        top: at.top,
        left: at.left,
        right: at.right,
        backgroundColor: "var(--color-surface)",
        borderColor: "var(--color-border)",
        color: "var(--color-text)",
      }}
      onClick={(e) => e.stopPropagation()}
      onContextMenu={(e) => e.preventDefault()}
    >
      {children}
    </div>,
    document.body,
  );
}

function MenuDivider() {
  return (
    <div className="my-1 border-t" style={{ borderColor: "var(--color-border)" }} />
  );
}

/**
 * A collection's actions, from its kebab or a right-click on the row.
 *
 * An inherited collection gets the menu too, trimmed to what makes sense
 * for a folder that belongs to another space: its profile (editable by
 * that space's editors, readable by everyone else) and a download.
 */
function NodeMenu({
  node,
  at,
  setAt,
  readOnly,
  canEditProfile,
  isFirst,
  isLast,
  onAddChild,
  onRename,
  onEditProfile,
  onMoveInto,
  onMoveUp,
  onMoveDown,
  onDownload,
  downloading,
  onDelete,
}: {
  node: Collection;
  at: MenuAnchor | null;
  setAt: (at: MenuAnchor | null) => void;
  readOnly: boolean;
  canEditProfile: boolean;
  isFirst: boolean;
  isLast: boolean;
  onAddChild: () => void;
  onRename: () => void;
  onEditProfile: () => void;
  onMoveInto: () => void;
  onMoveUp: () => void;
  onMoveDown: () => void;
  onDownload: () => void;
  /** A zip is being built for this folder; the entry says so and stops
   *  a second click starting another. */
  downloading: boolean;
  onDelete: () => void;
}) {
  const buttonRef = useRef<HTMLButtonElement>(null);
  const close = useCallback(() => setAt(null), [setAt]);

  // Item runs the action and closes the menu — saves a click everywhere.
  function run(fn: () => void) {
    return (e: React.MouseEvent) => {
      e.stopPropagation();
      setAt(null);
      fn();
    };
  }

  const profile = (
    <MenuItem
      icon={SlidersHorizontal}
      label={canEditProfile ? "Edit profile…" : "View profile…"}
      onClick={run(onEditProfile)}
    />
  );
  const download = (
    <MenuItem
      icon={Download}
      label={downloading ? "Zipping…" : "Download PDFs"}
      disabled={downloading}
      onClick={run(onDownload)}
    />
  );

  return (
    <>
      <button
        ref={buttonRef}
        onClick={(e) => {
          e.stopPropagation();
          if (at) {
            setAt(null);
            return;
          }
          const rect = e.currentTarget.getBoundingClientRect();
          setAt({ top: rect.bottom + 4, right: window.innerWidth - rect.right });
        }}
        aria-label={`More actions for ${node.name}`}
        aria-haspopup="menu"
        aria-expanded={!!at}
        className="rounded p-0.5 hover:opacity-70"
        style={{ color: "var(--color-text-muted)" }}
      >
        <MoreHorizontal className="h-3.5 w-3.5" />
      </button>
      {at && (
        <ActionMenu at={at} onClose={close} ignore={buttonRef}>
          {readOnly ? (
            <>
              {profile}
              <MenuDivider />
              {download}
            </>
          ) : (
            <>
              <MenuItem
                icon={FolderPlus}
                label="Add subcollection"
                onClick={run(onAddChild)}
              />
              <MenuItem icon={Pencil} label="Rename" onClick={run(onRename)} />
              {profile}
              <MenuItem
                icon={ArrowUp}
                label="Move up"
                disabled={isFirst}
                onClick={run(onMoveUp)}
              />
              <MenuItem
                icon={ArrowDown}
                label="Move down"
                disabled={isLast}
                onClick={run(onMoveDown)}
              />
              <MenuItem
                icon={FolderInput}
                label="Move into…"
                onClick={run(onMoveInto)}
              />
              <MenuDivider />
              {download}
              <MenuDivider />
              <MenuItem
                icon={Trash2}
                label="Delete"
                destructive
                onClick={run(onDelete)}
              />
            </>
          )}
        </ActionMenu>
      )}
    </>
  );
}

function MenuItem({
  icon: Icon,
  label,
  onClick,
  disabled,
  destructive,
}: {
  icon: typeof Pencil;
  label: string;
  onClick: (e: React.MouseEvent) => void;
  disabled?: boolean;
  destructive?: boolean;
}) {
  return (
    <button
      type="button"
      role="menuitem"
      disabled={disabled}
      onClick={onClick}
      className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm hover:opacity-80 disabled:opacity-40"
      style={{
        color: destructive ? "rgb(239 68 68)" : "var(--color-text)",
      }}
    >
      <Icon className="h-3.5 w-3.5" />
      {label}
    </button>
  );
}

function MoveIntoModal({
  collection,
  allCollections,
  tree,
  isDescendant,
  onClose,
  onPick,
}: {
  collection: Collection;
  allCollections: Collection[];
  tree: TreeNode[];
  isDescendant: (ancestorId: string, candidateId: string) => boolean;
  onClose: () => void;
  onPick: (parentId: string | null) => void;
}) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 p-4 sm:p-8"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-md rounded-lg border shadow-xl"
        style={{
          backgroundColor: "var(--color-surface)",
          borderColor: "var(--color-border)",
        }}
      >
        <div
          className="flex items-center justify-between border-b px-4 py-3"
          style={{ borderColor: "var(--color-border)" }}
        >
          <h2 className="text-base font-semibold">
            Move "{collection.name}" into…
          </h2>
          <button
            onClick={onClose}
            aria-label="Close"
            className="rounded p-1 hover:opacity-70"
            style={{ color: "var(--color-text-muted)" }}
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="max-h-[60vh] overflow-y-auto p-2">
          <button
            type="button"
            disabled={collection.parent_id === null}
            onClick={() => onPick(null)}
            className="mb-1 flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-sm hover:opacity-80 disabled:opacity-40"
          >
            <LibraryBig className="h-4 w-4" />
            <span>Root level</span>
          </button>
          <div
            className="my-1 border-t"
            style={{ borderColor: "var(--color-border)" }}
          />
          {allCollections.length === 0 && (
            <p
              className="px-2 py-2 text-xs italic"
              style={{ color: "var(--color-text-muted)" }}
            >
              No other collections.
            </p>
          )}
          {tree.map((node) => (
            <MoveIntoNode
              key={node.id}
              node={node}
              depth={0}
              isDisabled={(id) =>
                isDescendant(collection.id, id) || id === collection.parent_id
              }
              onPick={onPick}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

function MoveIntoNode({
  node,
  depth,
  isDisabled,
  onPick,
}: {
  node: TreeNode;
  depth: number;
  isDisabled: (id: string) => boolean;
  onPick: (parentId: string) => void;
}) {
  const disabled = isDisabled(node.id);
  return (
    <div>
      <button
        type="button"
        disabled={disabled}
        onClick={() => onPick(node.id)}
        className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-sm hover:opacity-80 disabled:opacity-40"
        style={{ paddingLeft: `${depth * 14 + 8}px` }}
      >
        <FolderClosed className="h-4 w-4" />
        <span className="truncate">{node.name}</span>
      </button>
      {node.children.map((child) => (
        <MoveIntoNode
          key={child.id}
          node={child}
          depth={depth + 1}
          isDisabled={isDisabled}
          onPick={onPick}
        />
      ))}
    </div>
  );
}
