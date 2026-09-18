import { useEffect, useMemo, useRef, useState } from "react";
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
  Inbox,
  LibraryBig,
  Link2,
  MoreHorizontal,
  Pencil,
  Plus,
  Text,
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
}: {
  slug: string | null;
  selection: Selection;
  onSelect: (s: Selection) => void;
}) {
  const qc = useQueryClient();
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [adding, setAdding] = useState(false);
  const [draftName, setDraftName] = useState("");

  // Editing state — only one row can be in a mode at a time.
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [descEditTarget, setDescEditTarget] = useState<Collection | null>(null);
  const [moveTarget, setMoveTarget] = useState<Collection | null>(null);

  const collections = useQuery({
    queryKey: ["collections", slug],
    queryFn: () => listCollections(slug!),
    enabled: !!slug,
  });

  // "Download PDFs" on a folder. Server-assembled, the same archive the
  // bulk-select action produces -- the collection id goes over rather
  // than an id per item, so a big folder doesn't build a query string
  // long enough to be refused.
  const [downloadingId, setDownloadingId] = useState<string | null>(null);
  const download = useMutation({
    mutationFn: (c: Collection) => {
      setDownloadingId(c.id);
      return downloadCollectionPdfsZip(slug!, c.id);
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
    mutationFn: (name: string) => createCollection(slug!, { name }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["collections", slug] });
      setAdding(false);
      setDraftName("");
    },
  });

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
            onClick={() => setAdding(true)}
            aria-label="New collection"
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
                  setDraftName("");
                }
              }}
              placeholder="Collection name"
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
            onEditDescription={(c) => setDescEditTarget(c)}
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
                // Everything below is a write, and an inherited
                // collection belongs to another space — the API refuses
                // all of them, so the row offers none of them.
                readOnly
                renamingId={null}
                onStartRename={() => {}}
                onCancelRename={() => {}}
                onCommitRename={() => {}}
                onEditDescription={() => {}}
                onMoveInto={() => {}}
                onMoveBy={() => {}}
                onDrop={() => {}}
                onDelete={() => {}}
                onDownload={() => {}}
                downloadingId={null}
              />
            ))}
          </div>
        ))}
      </div>

      {descEditTarget && (
        <DescriptionModal
          collection={descEditTarget}
          onClose={() => setDescEditTarget(null)}
          onSave={(desc) => {
            update.mutate({
              id: descEditTarget.id,
              patch: { description: desc },
            });
            setDescEditTarget(null);
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
  onEditDescription,
  onMoveInto,
  onMoveBy,
  onDrop,
  onDelete,
  onDownload,
  downloadingId,
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
  onEditDescription: (c: Collection) => void;
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
  /** Inherited from another space: browsable, but every write the row
   *  would otherwise offer is refused by the API, so none are shown. */
  readOnly?: boolean;
}) {
  const hasChildren = node.children.length > 0;
  const isOpen = expanded.has(node.id);
  const active = activeId === node.id;
  const renaming = renamingId === node.id;
  const [dropZone, setDropZone] = useState<
    "before" | "after" | "into" | null
  >(null);

  function handleDragOver(e: React.DragEvent) {
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
        {!renaming && !readOnly && (
          <NodeMenu
            node={node}
            isFirst={siblingIndex === 0}
            isLast={siblingIndex === siblingCount - 1}
            onRename={() => onStartRename(node.id)}
            onEditDescription={() => onEditDescription(node)}
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
              onEditDescription={onEditDescription}
              onMoveInto={onMoveInto}
              onMoveBy={onMoveBy}
              onDrop={onDrop}
              onDelete={onDelete}
              onDownload={onDownload}
              downloadingId={downloadingId}
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

function NodeMenu({
  node,
  isFirst,
  isLast,
  onRename,
  onEditDescription,
  onMoveInto,
  onMoveUp,
  onMoveDown,
  onDownload,
  downloading,
  onDelete,
}: {
  node: Collection;
  isFirst: boolean;
  isLast: boolean;
  onRename: () => void;
  onEditDescription: () => void;
  onMoveInto: () => void;
  onMoveUp: () => void;
  onMoveDown: () => void;
  onDownload: () => void;
  /** A zip is being built for this folder; the entry says so and stops
   *  a second click starting another. */
  downloading: boolean;
  onDelete: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ top: number; right: number } | null>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  // Portal-rendered to document.body so the row's hover-opacity
  // doesn't cascade into the menu. The CSS ``opacity`` property
  // multiplies through descendants — on mobile, sticky hover after a
  // tap was making the menu near-unreadable.
  useEffect(() => {
    if (!open) return;
    function place() {
      const rect = buttonRef.current?.getBoundingClientRect();
      if (!rect) return;
      setPos({
        top: rect.bottom + 4,
        right: window.innerWidth - rect.right,
      });
    }
    place();
    function onClickOutside(e: MouseEvent) {
      const target = e.target as Node;
      if (buttonRef.current?.contains(target)) return;
      if (menuRef.current?.contains(target)) return;
      setOpen(false);
    }
    document.addEventListener("mousedown", onClickOutside);
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => {
      document.removeEventListener("mousedown", onClickOutside);
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [open]);

  // Item runs the action and closes the menu — saves a click everywhere.
  function run(fn: () => void) {
    return (e: React.MouseEvent) => {
      e.stopPropagation();
      setOpen(false);
      fn();
    };
  }

  return (
    <>
      <button
        ref={buttonRef}
        onClick={(e) => {
          e.stopPropagation();
          setOpen((v) => !v);
        }}
        aria-label={`More actions for ${node.name}`}
        className="rounded p-0.5 hover:opacity-70"
        style={{ color: "var(--color-text-muted)" }}
      >
        <MoreHorizontal className="h-3.5 w-3.5" />
      </button>
      {open &&
        pos &&
        createPortal(
          <div
            ref={menuRef}
            className="fixed z-50 min-w-[180px] rounded border shadow-lg"
            style={{
              top: pos.top,
              right: pos.right,
              backgroundColor: "var(--color-surface)",
              borderColor: "var(--color-border)",
              color: "var(--color-text)",
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <MenuItem icon={Pencil} label="Rename" onClick={run(onRename)} />
            <MenuItem
              icon={Text}
              label="Edit description"
              onClick={run(onEditDescription)}
            />
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
            <div
              className="my-1 border-t"
              style={{ borderColor: "var(--color-border)" }}
            />
            <MenuItem
              icon={Download}
              label={downloading ? "Zipping…" : "Download PDFs"}
              disabled={downloading}
              onClick={run(onDownload)}
            />
            <div
              className="my-1 border-t"
              style={{ borderColor: "var(--color-border)" }}
            />
            <MenuItem
              icon={Trash2}
              label="Delete"
              destructive
              onClick={run(onDelete)}
            />
          </div>,
          document.body,
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

function DescriptionModal({
  collection,
  onClose,
  onSave,
}: {
  collection: Collection;
  onClose: () => void;
  onSave: (description: string) => void;
}) {
  const [value, setValue] = useState(collection.description ?? "");
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
            Description · {collection.name}
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
        <div className="p-4">
          <textarea
            autoFocus
            value={value}
            onChange={(e) => setValue(e.target.value)}
            rows={6}
            placeholder="What's this collection for?"
            className="w-full rounded border px-2 py-1.5 text-sm"
            style={{
              backgroundColor: "var(--color-surface)",
              borderColor: "var(--color-border)",
              color: "var(--color-text)",
            }}
          />
          <p
            className="mt-2 text-xs"
            style={{ color: "var(--color-text-muted)" }}
          >
            Shown in the page header when this collection is selected.
          </p>
        </div>
        <div
          className="flex items-center justify-end gap-2 border-t px-4 py-3"
          style={{ borderColor: "var(--color-border)" }}
        >
          <button
            type="button"
            onClick={onClose}
            className="rounded px-3 py-1.5 text-sm hover:opacity-70"
            style={{ color: "var(--color-text-muted)" }}
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={() => onSave(value)}
            className="rounded px-3 py-1.5 text-sm font-medium text-white"
            style={{ backgroundColor: "var(--color-accent)" }}
          >
            Save
          </button>
        </div>
      </div>
    </div>
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
