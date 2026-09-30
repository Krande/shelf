import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ChevronDown,
  ChevronRight,
  Copy,
  Pencil,
  Plus,
  Trash2,
} from "lucide-react";
import {
  type ApiToken,
  type TokenScope,
  createToken,
  listTokens,
  revokeToken,
  updateToken,
} from "@/api/tokens";
import { fetchMySpaces, type Space } from "@/api/spaces";
import { type Collection, listCollections } from "@/api/collections";

const ALL_SCOPES: TokenScope[] = ["upload", "search", "download"];

function formatDate(s: string | null): string {
  if (!s) return "never";
  return new Date(s).toLocaleString();
}

/** `expires_at` → the `YYYY-MM-DD` a date input holds, in local time. */
function toDateInput(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/** A picked date expires at the end of that day, local time. */
function fromDateInput(value: string): string | null {
  return value ? new Date(`${value}T23:59:59`).toISOString() : null;
}

/** What the form hands back; the same shape create and update take. */
export interface TokenFormValues {
  name: string;
  scopes: TokenScope[];
  allowed_space_ids: string[] | null;
  allowed_collection_ids: string[] | null;
  include_descendants: boolean;
  expires_at: string | null;
}

/**
 * Settings tab: list / create / edit / revoke API tokens for scripts and
 * importers. The plaintext is shown exactly once after create — there
 * is no other path to it. Editing changes what a token may do and keeps
 * its secret, so the scripts holding it carry on working.
 */
export default function TokenSection() {
  const qc = useQueryClient();
  const tokens = useQuery({ queryKey: ["tokens"], queryFn: listTokens });
  // Inherited spaces included: scoping a token to the shared Standards
  // space its owner subscribes to is one of the obvious things to want,
  // and that space never appears in the plain listing.
  const spaces = useQuery({
    queryKey: ["spaces", "with-inherited"],
    queryFn: () => fetchMySpaces({ includeInherited: true }),
  });

  const [creating, setCreating] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [plaintext, setPlaintext] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: createToken,
    onSuccess: (t) => {
      setPlaintext(t.plaintext);
      setCreating(false);
      qc.invalidateQueries({ queryKey: ["tokens"] });
    },
    onError: (e: Error) => setError(e.message),
  });

  const update = useMutation({
    mutationFn: ({ id, values }: { id: string; values: TokenFormValues }) =>
      updateToken(id, values),
    onSuccess: () => {
      setEditingId(null);
      qc.invalidateQueries({ queryKey: ["tokens"] });
    },
    onError: (e: Error) => setError(e.message),
  });

  const remove = useMutation({
    mutationFn: revokeToken,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["tokens"] }),
  });

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
            onClick={() => {
              setCreating(true);
              setEditingId(null);
              setError(null);
            }}
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
        <div className="mb-3">
          <TokenForm
            spaces={spaces.data ?? []}
            submitLabel="Create token"
            pendingLabel="Creating…"
            pending={create.isPending}
            error={error}
            setError={setError}
            onSubmit={(values) => create.mutate(values)}
            onCancel={() => {
              setCreating(false);
              setError(null);
            }}
          />
        </div>
      )}

      <TokenList
        tokens={tokens.data ?? []}
        loading={tokens.isLoading}
        spaces={spaces.data ?? []}
        editingId={editingId}
        renderEditor={(t) => (
          <TokenForm
            initial={t}
            spaces={spaces.data ?? []}
            submitLabel="Save changes"
            pendingLabel="Saving…"
            pending={update.isPending}
            error={error}
            setError={setError}
            onSubmit={(values) => update.mutate({ id: t.id, values })}
            onCancel={() => {
              setEditingId(null);
              setError(null);
            }}
          />
        )}
        onEdit={(id) => {
          setEditingId(id);
          setCreating(false);
          setError(null);
        }}
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

/**
 * The fields of a token, blank for a new one or filled from `initial`
 * when editing. Checks what the API would refuse before sending, so the
 * message can name the fix.
 */
function TokenForm({
  initial,
  spaces,
  submitLabel,
  pendingLabel,
  pending,
  error,
  setError,
  onSubmit,
  onCancel,
}: {
  initial?: ApiToken;
  spaces: Space[];
  submitLabel: string;
  pendingLabel: string;
  pending: boolean;
  error: string | null;
  setError: (e: string | null) => void;
  onSubmit: (values: TokenFormValues) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [scopes, setScopes] = useState<Set<TokenScope>>(
    new Set(initial?.scopes ?? ALL_SCOPES),
  );
  const [spaceScoped, setSpaceScoped] = useState(
    !!initial?.allowed_space_ids,
  );
  const [spaceIds, setSpaceIds] = useState<Set<string>>(
    new Set(initial?.allowed_space_ids ?? []),
  );
  const [scoped, setScoped] = useState(!!initial?.allowed_collection_ids);
  const [collIds, setCollIds] = useState<Set<string>>(
    new Set(initial?.allowed_collection_ids ?? []),
  );
  const [includeDescendants, setIncludeDescendants] = useState(
    initial?.include_descendants ?? false,
  );
  const [expires, setExpires] = useState(
    toDateInput(initial?.expires_at ?? null),
  );

  // Collections are per-space, so the picker has to follow whichever
  // space the token is being pointed at. Falls back to the personal one
  // when the token isn't space-restricted, which is the old behaviour.
  const collectionSlug = useMemo(() => {
    const chosen = spaceScoped
      ? spaces.find((s) => spaceIds.has(s.id))
      : undefined;
    return (
      chosen?.slug ??
      spaces.find((s) => s.is_personal)?.slug ??
      spaces[0]?.slug ??
      null
    );
  }, [spaces, spaceScoped, spaceIds]);

  const collections = useQuery({
    queryKey: ["collections", collectionSlug],
    queryFn: () => listCollections(collectionSlug!),
    enabled: !!collectionSlug,
  });

  function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!name.trim()) return;
    if (scopes.size === 0) {
      setError("Pick at least one scope");
      return;
    }
    // The API refuses an empty allow-list rather than minting a token
    // that can reach nothing; say so here instead of round-tripping.
    if (spaceScoped && spaceIds.size === 0) {
      setError("Pick at least one space, or untick the restriction");
      return;
    }
    if (scoped && collIds.size === 0) {
      setError("Pick at least one collection, or untick the restriction");
      return;
    }
    onSubmit({
      name: name.trim(),
      scopes: ALL_SCOPES.filter((s) => scopes.has(s)),
      allowed_space_ids: spaceScoped ? [...spaceIds] : null,
      allowed_collection_ids: scoped ? [...collIds] : null,
      include_descendants: scoped && includeDescendants,
      expires_at: fromDateInput(expires),
    });
  }

  return (
    <form
      onSubmit={submit}
      className="rounded border p-3"
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
          placeholder="my-import-script"
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
            checked={spaceScoped}
            onChange={(e) => setSpaceScoped(e.target.checked)}
          />
          <span>Restrict to specific spaces</span>
        </label>
        {!spaceScoped ? (
          <p
            className="mt-1 text-xs"
            style={{ color: "var(--color-text-muted)" }}
          >
            Otherwise the token reaches every space you can — your own,
            any shared with you, and any those inherit.
          </p>
        ) : (
          <div className="mt-2 flex flex-col gap-1">
            {spaces.map((s) => (
              <label key={s.id} className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={spaceIds.has(s.id)}
                  onChange={() => {
                    const next = new Set(spaceIds);
                    if (next.has(s.id)) next.delete(s.id);
                    else next.add(s.id);
                    setSpaceIds(next);
                  }}
                />
                <span className="min-w-0 truncate">
                  {s.name}
                  <span
                    className="ml-2 text-xs"
                    style={{ color: "var(--color-text-muted)" }}
                  >
                    {s.is_personal
                      ? "personal"
                      : s.is_inherited
                        ? "inherited · read-only"
                        : s.role}
                  </span>
                </span>
              </label>
            ))}
            {spaces.length === 0 && (
              <p
                className="text-xs"
                style={{ color: "var(--color-text-muted)" }}
              >
                No spaces yet.
              </p>
            )}
          </div>
        )}
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

      <label className="mb-2 block">
        <span
          className="mb-1 block text-xs font-medium"
          style={{ color: "var(--color-text-muted)" }}
        >
          Expires (optional)
        </span>
        <span className="flex items-center gap-2">
          <input
            type="date"
            value={expires}
            onChange={(e) => setExpires(e.target.value)}
            className="rounded border px-2 py-1 text-sm"
            style={{
              backgroundColor: "var(--color-surface)",
              borderColor: "var(--color-border)",
              color: "var(--color-text)",
            }}
          />
          {expires && (
            <button
              type="button"
              onClick={() => setExpires("")}
              className="text-xs hover:opacity-70"
              style={{ color: "var(--color-text-muted)" }}
            >
              Never expire
            </button>
          )}
        </span>
      </label>

      {error && <p className="mb-2 text-xs text-red-500">{error}</p>}

      <div className="flex justify-end gap-2">
        <button
          type="button"
          onClick={onCancel}
          className="rounded border px-3 py-1 text-sm hover:opacity-80"
          style={{ borderColor: "var(--color-border)" }}
        >
          Cancel
        </button>
        <button
          type="submit"
          disabled={pending}
          className="rounded px-3 py-1 text-sm font-medium text-white disabled:opacity-50"
          style={{ backgroundColor: "var(--color-accent)" }}
        >
          {pending ? pendingLabel : submitLabel}
        </button>
      </div>
    </form>
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
  spaces,
  editingId,
  renderEditor,
  onEdit,
  onRevoke,
}: {
  tokens: ApiToken[];
  loading: boolean;
  spaces: Space[];
  editingId: string | null;
  renderEditor: (t: ApiToken) => React.ReactNode;
  onEdit: (id: string) => void;
  onRevoke: (id: string, name: string) => void;
}) {
  const spaceName = (id: string) =>
    spaces.find((s) => s.id === id)?.name ?? "unknown space";
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
      {tokens.map((t) =>
        t.id === editingId ? (
          <li key={t.id}>{renderEditor(t)}</li>
        ) : (
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
                {t.allowed_space_ids
                  ? t.allowed_space_ids.map(spaceName).join(", ")
                  : "all spaces"}
              </span>
              <span className="mr-2">
                {t.allowed_collection_ids
                  ? `${t.allowed_collection_ids.length} collection${t.allowed_collection_ids.length === 1 ? "" : "s"}${t.include_descendants ? " + nested" : ""}`
                  : "all collections"}
              </span>
              {t.expires_at && (
                <span className="mr-2">
                  {new Date(t.expires_at) < new Date() ? "expired" : "expires"}{" "}
                  {new Date(t.expires_at).toLocaleDateString()}
                </span>
              )}
              <span>last used {formatDate(t.last_used_at)}</span>
            </p>
          </div>
          <button
            onClick={() => onEdit(t.id)}
            aria-label="Edit token"
            title="Edit name, scopes, reach or expiry — the secret stays the same"
            className="rounded p-1 hover:opacity-70"
            style={{ color: "var(--color-text-muted)" }}
          >
            <Pencil className="h-4 w-4" />
          </button>
          <button
            onClick={() => onRevoke(t.id, t.name)}
            aria-label="Revoke token"
            className="rounded p-1 hover:bg-red-500/10"
            style={{ color: "var(--color-text-muted)" }}
          >
            <Trash2 className="h-4 w-4" />
          </button>
        </li>
        ),
      )}
    </ul>
  );
}
