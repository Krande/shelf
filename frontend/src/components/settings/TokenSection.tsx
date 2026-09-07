import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, Copy, Plus, Trash2 } from "lucide-react";
import {
  type ApiToken,
  type TokenScope,
  createToken,
  listTokens,
  revokeToken,
} from "@/api/tokens";
import { fetchMySpaces } from "@/api/spaces";
import { type Collection, listCollections } from "@/api/collections";

const ALL_SCOPES: TokenScope[] = ["upload", "search", "download"];

function formatDate(s: string | null): string {
  if (!s) return "never";
  return new Date(s).toLocaleString();
}

/**
 * Settings tab: list / create / revoke API tokens for scripts and
 * importers. The plaintext is shown exactly once after create — there
 * is no other path to it. Tokens can optionally be scoped to a subset
 * of the user's collections.
 */
export default function TokenSection() {
  const qc = useQueryClient();
  const tokens = useQuery({ queryKey: ["tokens"], queryFn: listTokens });
  const spaces = useQuery({ queryKey: ["spaces"], queryFn: fetchMySpaces });
  const personalSlug = useMemo(
    () => spaces.data?.find((s) => s.is_personal)?.slug ?? spaces.data?.[0]?.slug ?? null,
    [spaces.data],
  );
  const collections = useQuery({
    queryKey: ["collections", personalSlug],
    queryFn: () => listCollections(personalSlug!),
    enabled: !!personalSlug,
  });

  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [scopes, setScopes] = useState<Set<TokenScope>>(new Set(ALL_SCOPES));
  const [scoped, setScoped] = useState(false);
  const [collIds, setCollIds] = useState<Set<string>>(new Set());
  const [includeDescendants, setIncludeDescendants] = useState(false);
  const [plaintext, setPlaintext] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: createToken,
    onSuccess: (t) => {
      setPlaintext(t.plaintext);
      setName("");
      setScopes(new Set(ALL_SCOPES));
      setScoped(false);
      setCollIds(new Set());
      setIncludeDescendants(false);
      setCreating(false);
      qc.invalidateQueries({ queryKey: ["tokens"] });
    },
    onError: (e: Error) => setError(e.message),
  });

  const remove = useMutation({
    mutationFn: revokeToken,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["tokens"] }),
  });

  function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!name.trim()) return;
    if (scopes.size === 0) {
      setError("Pick at least one scope");
      return;
    }
    create.mutate({
      name: name.trim(),
      scopes: [...scopes],
      allowed_collection_ids: scoped ? [...collIds] : null,
      include_descendants: scoped && includeDescendants,
    });
  }

  return (
    <section
      className="mb-6 rounded border p-4"
      style={{
        backgroundColor: "var(--color-surface)",
        borderColor: "var(--color-border)",
      }}
    >
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-medium">API tokens</h2>
        {!creating && (
          <button
            onClick={() => setCreating(true)}
            className="flex items-center gap-1 rounded px-2 py-1 text-xs font-medium text-white"
            style={{ backgroundColor: "var(--color-accent)" }}
          >
            <Plus className="h-3.5 w-3.5" />
            New token
          </button>
        )}
      </div>

      {plaintext && (
        <div
          className="mb-3 rounded border p-3"
          style={{
            borderColor: "var(--color-accent)",
            backgroundColor:
              "color-mix(in srgb, var(--color-accent) 8%, transparent)",
          }}
        >
          <p className="mb-2 text-xs font-medium">
            Copy this token now — it won't be shown again.
          </p>
          <div className="flex items-center gap-2">
            <code
              className="flex-1 truncate rounded border px-2 py-1 font-mono text-xs"
              style={{
                backgroundColor: "var(--color-surface)",
                borderColor: "var(--color-border)",
              }}
            >
              {plaintext}
            </code>
            <button
              onClick={() => navigator.clipboard.writeText(plaintext)}
              className="flex items-center gap-1 rounded border px-2 py-1 text-xs hover:opacity-80"
              style={{ borderColor: "var(--color-border)" }}
            >
              <Copy className="h-3 w-3" />
              Copy
            </button>
            <button
              onClick={() => setPlaintext(null)}
              className="text-xs hover:opacity-70"
              style={{ color: "var(--color-text-muted)" }}
            >
              Dismiss
            </button>
          </div>
        </div>
      )}

      {creating && (
        <form
          onSubmit={submit}
          className="mb-3 rounded border p-3"
          style={{ borderColor: "var(--color-border)" }}
        >
          <label className="mb-2 block">
            <span
              className="mb-1 block text-xs font-medium"
              style={{ color: "var(--color-text-muted)" }}
            >
              Name
            </span>
            <input
              type="text"
              autoFocus
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="zotero-import-script"
              className="w-full rounded border px-2 py-1 text-sm"
              style={{
                backgroundColor: "var(--color-surface)",
                borderColor: "var(--color-border)",
                color: "var(--color-text)",
              }}
            />
          </label>

          <fieldset className="mb-2">
            <legend
              className="mb-1 text-xs font-medium"
              style={{ color: "var(--color-text-muted)" }}
            >
              Scopes
            </legend>
            <div className="flex flex-wrap gap-3">
              {ALL_SCOPES.map((s) => (
                <label key={s} className="flex items-center gap-1 text-sm">
                  <input
                    type="checkbox"
                    checked={scopes.has(s)}
                    onChange={() => {
                      const next = new Set(scopes);
                      if (next.has(s)) next.delete(s);
                      else next.add(s);
                      setScopes(next);
                    }}
                  />
                  {s}
                </label>
              ))}
            </div>
          </fieldset>

          <fieldset className="mb-2">
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={scoped}
                onChange={(e) => setScoped(e.target.checked)}
              />
              <span>Restrict to specific collections</span>
            </label>
            {scoped && (
              <div className="mt-2">
                <CollectionTreePicker
                  collections={collections.data ?? []}
                  selected={collIds}
                  onToggle={(id) => {
                    const next = new Set(collIds);
                    if (next.has(id)) next.delete(id);
                    else next.add(id);
                    setCollIds(next);
                  }}
                />
                <label className="mt-2 flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={includeDescendants}
                    onChange={(e) => setIncludeDescendants(e.target.checked)}
                  />
                  <span>Include all nested subcollections</span>
                </label>
                {includeDescendants && (
                  <p
                    className="mt-1 text-xs"
                    style={{ color: "var(--color-text-muted)" }}
                  >
                    Resolved at request time, so subcollections added
                    later are covered automatically.
                  </p>
                )}
              </div>
            )}
          </fieldset>

          {error && (
            <p className="mb-2 text-xs text-red-500">{error}</p>
          )}

          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={() => {
                setCreating(false);
                setError(null);
              }}
              className="rounded border px-3 py-1 text-sm hover:opacity-80"
              style={{ borderColor: "var(--color-border)" }}
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={create.isPending}
              className="rounded px-3 py-1 text-sm font-medium text-white disabled:opacity-50"
              style={{ backgroundColor: "var(--color-accent)" }}
            >
              {create.isPending ? "Creating…" : "Create token"}
            </button>
          </div>
        </form>
      )}

      <TokenList
        tokens={tokens.data ?? []}
        loading={tokens.isLoading}
        onRevoke={(id, name) => {
          if (
            window.confirm(
              `Revoke "${name}"? Any script using this token will stop working.`,
            )
          ) {
            remove.mutate(id);
          }
        }}
      />
    </section>
  );
}

interface TreeNode extends Collection {
  children: TreeNode[];
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
  const sortRec = (nodes: TreeNode[]) => {
    nodes.sort((a, b) => a.name.localeCompare(b.name));
    for (const n of nodes) sortRec(n.children);
  };
  sortRec(roots);
  return roots;
}

function CollectionTreePicker({
  collections,
  selected,
  onToggle,
}: {
  collections: Collection[];
  selected: Set<string>;
  onToggle: (id: string) => void;
}) {
  const tree = useMemo(() => buildTree(collections), [collections]);
  // Default to roots only — user expands the levels they care about.
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  function toggleExpanded(id: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  if (collections.length === 0) {
    return (
      <div
        className="rounded border px-2 py-2"
        style={{
          backgroundColor: "var(--color-surface)",
          borderColor: "var(--color-border)",
        }}
      >
        <p
          className="text-xs italic"
          style={{ color: "var(--color-text-muted)" }}
        >
          No collections yet — uncheck the box above to leave the
          token unrestricted.
        </p>
      </div>
    );
  }

  return (
    <div
      className="max-h-56 overflow-y-auto rounded border px-1 py-1"
      style={{
        backgroundColor: "var(--color-surface)",
        borderColor: "var(--color-border)",
      }}
    >
      {tree.map((node) => (
        <PickerNode
          key={node.id}
          node={node}
          depth={0}
          expanded={expanded}
          onToggleExpanded={toggleExpanded}
          selected={selected}
          onToggleSelected={onToggle}
        />
      ))}
    </div>
  );
}

function PickerNode({
  node,
  depth,
  expanded,
  onToggleExpanded,
  selected,
  onToggleSelected,
}: {
  node: TreeNode;
  depth: number;
  expanded: Set<string>;
  onToggleExpanded: (id: string) => void;
  selected: Set<string>;
  onToggleSelected: (id: string) => void;
}) {
  const hasChildren = node.children.length > 0;
  const isOpen = expanded.has(node.id);

  return (
    <div>
      <div
        className="flex items-center gap-1 rounded py-0.5 text-sm"
        style={{ paddingLeft: `${depth * 12 + 2}px` }}
      >
        {hasChildren ? (
          <button
            type="button"
            onClick={() => onToggleExpanded(node.id)}
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
        <label className="flex flex-1 cursor-pointer items-center gap-2 truncate">
          <input
            type="checkbox"
            checked={selected.has(node.id)}
            onChange={() => onToggleSelected(node.id)}
          />
          <span className="truncate">{node.name}</span>
        </label>
      </div>
      {hasChildren && isOpen && (
        <div>
          {node.children.map((child) => (
            <PickerNode
              key={child.id}
              node={child}
              depth={depth + 1}
              expanded={expanded}
              onToggleExpanded={onToggleExpanded}
              selected={selected}
              onToggleSelected={onToggleSelected}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function TokenList({
  tokens,
  loading,
  onRevoke,
}: {
  tokens: ApiToken[];
  loading: boolean;
  onRevoke: (id: string, name: string) => void;
}) {
  if (loading) {
    return (
      <p className="text-xs italic" style={{ color: "var(--color-text-muted)" }}>
        Loading tokens…
      </p>
    );
  }
  if (tokens.length === 0) {
    return (
      <p className="text-xs italic" style={{ color: "var(--color-text-muted)" }}>
        No tokens yet.
      </p>
    );
  }
  return (
    <ul className="flex flex-col gap-2">
      {tokens.map((t) => (
        <li
          key={t.id}
          className="flex items-start gap-3 rounded border px-3 py-2"
          style={{ borderColor: "var(--color-border)" }}
        >
          <div className="min-w-0 flex-1">
            <p className="font-medium">{t.name}</p>
            <p
              className="font-mono text-xs"
              style={{ color: "var(--color-text-muted)" }}
            >
              {t.prefix}…
            </p>
            <p
              className="mt-1 text-xs"
              style={{ color: "var(--color-text-muted)" }}
            >
              <span className="mr-2">{t.scopes.join(" · ") || "no scopes"}</span>
              <span className="mr-2">
                {t.allowed_collection_ids
                  ? `${t.allowed_collection_ids.length} collection${t.allowed_collection_ids.length === 1 ? "" : "s"}${t.include_descendants ? " + nested" : ""}`
                  : "all collections"}
              </span>
              <span>last used {formatDate(t.last_used_at)}</span>
            </p>
          </div>
          <button
            onClick={() => onRevoke(t.id, t.name)}
            aria-label="Revoke token"
            className="rounded p-1 hover:bg-red-500/10"
            style={{ color: "var(--color-text-muted)" }}
          >
            <Trash2 className="h-4 w-4" />
          </button>
        </li>
      ))}
    </ul>
  );
}
