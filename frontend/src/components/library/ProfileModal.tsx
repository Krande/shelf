import { useMemo, useState } from "react";
import { ArrowDown, ArrowUp, GripVertical, X } from "lucide-react";
import {
  BUILTIN_COLUMNS,
  columnLabel,
  fieldColumnHint,
  fieldColumns,
  moveColumn,
  moveColumnTo,
  sourceName,
  type ColumnKey,
  type ResolvedProfile,
} from "@/lib/libraryColumns";

export interface ProfileDraft {
  description: string;
  /** null: inherit. */
  columns: ColumnKey[] | null;
  /** Only with `identity`: the name and slug as edited. */
  name?: string;
  slug?: string;
}

/** A space's name and slug, edited in the same dialog as its profile. */
export interface ProfileIdentity {
  name: string;
  slug: string;
  /** Renaming is its own permission — the owner, or an instance admin
   *  for a shared space — separate from editing the profile. */
  editable: boolean;
  /** A personal space's slug is fixed. */
  slugFixed: boolean;
}

/**
 * Edit a collection's or a space's profile: its description, and the
 * columns its library table shows by default.
 *
 * Columns are either inherited — named, with the list they'd get, so
 * "inherit" isn't a mystery — or set here, as an ordered list. Setting
 * them starts from the inherited list, which is nearly always one or two
 * columns away from what's wanted.
 */
export default function ProfileModal({
  title,
  kind,
  description,
  columns,
  inherited,
  preferTypes,
  readOnly,
  identity,
  saving,
  error,
  onClose,
  onSave,
}: {
  title: string;
  kind: "collection" | "space";
  description: string | null;
  columns: ColumnKey[] | null;
  /** What clearing `columns` would fall back to. */
  inherited: ResolvedProfile;
  /** Item types on screen; their fields are offered first. */
  preferTypes: string[];
  /** The description and columns are shown, not editable — an inherited
   *  collection seen by a reader. */
  readOnly?: boolean;
  /** A space's name and slug, shown above its profile. */
  identity?: ProfileIdentity;
  saving?: boolean;
  /** Why the last save failed; the dialog stays open to fix it. */
  error?: string | null;
  onClose: () => void;
  onSave: (draft: ProfileDraft) => void;
}) {
  const [desc, setDesc] = useState(description ?? "");
  const [own, setOwn] = useState<ColumnKey[] | null>(columns);
  const [name, setName] = useState(identity?.name ?? "");
  const [slug, setSlug] = useState(identity?.slug ?? "");
  const slugChanged =
    !!identity && !identity.slugFixed && slug.trim() !== identity.slug;
  const canSave = !readOnly || !!identity?.editable;
  const nameMissing = !!identity?.editable && !name.trim();
  // Drag-to-reorder: the row being dragged, and the gap it would drop
  // into (0 = before the first row, own.length = after the last). The
  // arrow buttons stay for keyboards and for one-step nudges.
  const [dragging, setDragging] = useState<ColumnKey | null>(null);
  const [dropAt, setDropAt] = useState<number | null>(null);
  const available = useMemo<ColumnKey[]>(
    () => [...BUILTIN_COLUMNS, ...fieldColumns(preferTypes)],
    [preferTypes],
  );
  const addable = available.filter((k) => !(own ?? []).includes(k));
  const inheritLabel =
    inherited.source.kind === "default"
      ? "Use the default columns"
      : `Inherit from ${sourceName(inherited.source)}`;

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 p-4 sm:p-8"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label={title}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-lg rounded-lg border shadow-xl"
        style={{
          backgroundColor: "var(--color-surface)",
          borderColor: "var(--color-border)",
          color: "var(--color-text)",
        }}
      >
        <div
          className="flex items-center justify-between border-b px-4 py-3"
          style={{ borderColor: "var(--color-border)" }}
        >
          <h2 className="text-base font-semibold">{title}</h2>
          <button
            onClick={onClose}
            aria-label="Close"
            className="rounded p-1 hover:opacity-70"
            style={{ color: "var(--color-text-muted)" }}
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="space-y-5 p-4">
          {identity && (
            <section className="grid gap-3 sm:grid-cols-2">
              <div>
                <label className="mb-1 block text-sm font-medium" htmlFor="profile-name">
                  Name
                </label>
                <input
                  id="profile-name"
                  autoFocus={identity.editable}
                  required
                  readOnly={!identity.editable}
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  className="w-full rounded border px-2 py-1.5 text-sm read-only:opacity-60"
                  style={inputStyle}
                />
              </div>
              <div>
                <label className="mb-1 block text-sm font-medium" htmlFor="profile-slug">
                  Slug
                </label>
                <input
                  id="profile-slug"
                  readOnly={!identity.editable || identity.slugFixed}
                  value={slug}
                  onChange={(e) => setSlug(e.target.value)}
                  className="w-full rounded border px-2 py-1.5 text-sm read-only:opacity-60"
                  style={inputStyle}
                />
              </div>
              <p
                className="text-xs sm:col-span-2"
                style={{ color: slugChanged ? "rgb(217 119 6)" : "var(--color-text-muted)" }}
              >
                {!identity.editable
                  ? "Only the space's owner can rename it."
                  : identity.slugFixed
                    ? "A personal space's slug is fixed, but you can call it whatever you like."
                    : slugChanged
                      ? "Changing the slug changes this space's URL. Links people already have will stop working — nothing else breaks."
                      : "The slug is what appears in URLs."}
              </p>
            </section>
          )}

          <section>
            <label className="mb-1 block text-sm font-medium" htmlFor="profile-desc">
              Description
            </label>
            <textarea
              id="profile-desc"
              autoFocus={!readOnly && !identity?.editable}
              readOnly={readOnly}
              value={desc}
              onChange={(e) => setDesc(e.target.value)}
              rows={4}
              placeholder={`What's this ${kind} for?`}
              className="w-full rounded border px-2 py-1.5 text-sm"
              style={{
                backgroundColor: "var(--color-surface)",
                borderColor: "var(--color-border)",
                color: "var(--color-text)",
              }}
            />
            <p className="mt-1 text-xs" style={{ color: "var(--color-text-muted)" }}>
              Shown in the page header when this {kind} is open.
            </p>
          </section>

          <section>
            <div className="mb-1 text-sm font-medium">Columns</div>
            <p className="mb-2 text-xs" style={{ color: "var(--color-text-muted)" }}>
              What the library table shows here
              {kind === "collection" ? " and in subcollections that don't set their own" : ""}
              . Readers can still change their own view from the Columns menu.
            </p>
            <label className="flex cursor-pointer items-start gap-2 py-1 text-sm">
              <input
                type="radio"
                name="profile-columns"
                disabled={readOnly}
                checked={own === null}
                onChange={() => setOwn(null)}
                className="mt-1"
              />
              <span>
                {inheritLabel}
                <span className="block text-xs" style={{ color: "var(--color-text-muted)" }}>
                  {inherited.columns.map(columnLabel).join(", ")}
                </span>
              </span>
            </label>
            <label className="flex cursor-pointer items-start gap-2 py-1 text-sm">
              <input
                type="radio"
                name="profile-columns"
                disabled={readOnly}
                checked={own !== null}
                onChange={() => setOwn(own ?? inherited.columns)}
                className="mt-1"
              />
              <span>Choose columns for this {kind}</span>
            </label>

            {own !== null && (
              <div
                className="ml-6 mt-2 rounded border"
                style={{ borderColor: "var(--color-border)" }}
              >
                <ol onDragLeave={(e) => {
                  // Leaving the list altogether, not moving between rows.
                  if (!e.currentTarget.contains(e.relatedTarget as Node)) setDropAt(null);
                }}>
                  {own.map((k, i) => (
                    <li
                      key={k}
                      draggable={!readOnly}
                      onDragStart={(e) => {
                        setDragging(k);
                        e.dataTransfer.effectAllowed = "move";
                        // Firefox starts no drag without some data.
                        e.dataTransfer.setData("text/plain", k);
                      }}
                      onDragOver={(e) => {
                        if (!dragging) return;
                        e.preventDefault();
                        e.dataTransfer.dropEffect = "move";
                        const rect = e.currentTarget.getBoundingClientRect();
                        setDropAt(e.clientY < rect.top + rect.height / 2 ? i : i + 1);
                      }}
                      onDrop={(e) => {
                        e.preventDefault();
                        if (dragging && dropAt !== null) {
                          setOwn(moveColumnTo(own, dragging, dropAt));
                        }
                        setDragging(null);
                        setDropAt(null);
                      }}
                      onDragEnd={() => {
                        setDragging(null);
                        setDropAt(null);
                      }}
                      className={`relative flex items-center gap-2 border-b px-2 py-1 text-sm last:border-b-0 ${
                        readOnly ? "" : "cursor-grab active:cursor-grabbing"
                      }`}
                      style={{
                        borderColor: "var(--color-border)",
                        opacity: dragging === k ? 0.4 : undefined,
                      }}
                    >
                      {dragging && dropAt === i && <DropLine edge="top" />}
                      {dragging && dropAt === own.length && i === own.length - 1 && (
                        <DropLine edge="bottom" />
                      )}
                      {!readOnly && (
                        <GripVertical
                          aria-hidden
                          className="h-3.5 w-3.5 shrink-0"
                          style={{ color: "var(--color-text-muted)" }}
                        />
                      )}
                      <span className="flex-1 truncate">{optionLabel(k)}</span>
                      {!readOnly && (
                        <>
                          <IconButton
                            label={`Move ${columnLabel(k)} earlier`}
                            disabled={i === 0}
                            onClick={() => setOwn(moveColumn(own, k, -1))}
                          >
                            <ArrowUp className="h-3.5 w-3.5" />
                          </IconButton>
                          <IconButton
                            label={`Move ${columnLabel(k)} later`}
                            disabled={i === own.length - 1}
                            onClick={() => setOwn(moveColumn(own, k, 1))}
                          >
                            <ArrowDown className="h-3.5 w-3.5" />
                          </IconButton>
                          <IconButton
                            label={`Remove ${columnLabel(k)}`}
                            // Title is how a row opens; it always shows.
                            disabled={k === "title"}
                            onClick={() => setOwn(own.filter((c) => c !== k))}
                          >
                            <X className="h-3.5 w-3.5" />
                          </IconButton>
                        </>
                      )}
                    </li>
                  ))}
                </ol>
                {!readOnly && addable.length > 0 && (
                  <div
                    className="border-t px-2 py-1.5"
                    style={{ borderColor: "var(--color-border)" }}
                  >
                    <select
                      aria-label="Add a column"
                      value=""
                      onChange={(e) => {
                        const k = e.target.value as ColumnKey;
                        if (k) setOwn([...own, k]);
                      }}
                      className="w-full rounded border px-1 py-1 text-sm"
                      style={{
                        backgroundColor: "var(--color-surface)",
                        borderColor: "var(--color-border)",
                        color: "var(--color-text)",
                      }}
                    >
                      <option value="">Add a column…</option>
                      {addable.map((k) => (
                        <option key={k} value={k}>
                          {optionLabel(k)}
                        </option>
                      ))}
                    </select>
                  </div>
                )}
              </div>
            )}
          </section>
        </div>

        <div
          className="flex items-center justify-end gap-2 border-t px-4 py-3"
          style={{ borderColor: "var(--color-border)" }}
        >
          {error && (
            <p className="mr-auto text-xs text-red-600" role="alert">
              {error}
            </p>
          )}
          <button
            type="button"
            onClick={onClose}
            className="rounded px-3 py-1.5 text-sm hover:opacity-70"
            style={{ color: "var(--color-text-muted)" }}
          >
            {canSave ? "Cancel" : "Close"}
          </button>
          {canSave && (
            <button
              type="button"
              disabled={saving || nameMissing}
              onClick={() =>
                onSave({
                  description: desc,
                  columns: own,
                  ...(identity ? { name: name.trim(), slug: slug.trim() } : {}),
                })
              }
              className="rounded px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
              style={{ backgroundColor: "var(--color-accent)" }}
            >
              {saving ? "Saving…" : "Save"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

const inputStyle = {
  backgroundColor: "var(--color-surface)",
  borderColor: "var(--color-border)",
  color: "var(--color-text)",
};

/** A column's name, with the item types it belongs to when another
 *  field shares that name (a standard's Pages vs an article's). */
function optionLabel(k: ColumnKey): string {
  const hint = fieldColumnHint(k);
  return hint ? `${columnLabel(k)} (${hint})` : columnLabel(k);
}

/** Where a dragged column will land: a line along one edge of a row. */
function DropLine({ edge }: { edge: "top" | "bottom" }) {
  return (
    <span
      aria-hidden
      className="pointer-events-none absolute left-1 right-1 h-0.5 rounded-full"
      style={{
        [edge]: "-1px",
        backgroundColor: "var(--color-accent)",
      }}
    />
  );
}

function IconButton({
  label,
  disabled,
  onClick,
  children,
}: {
  label: string;
  disabled?: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      disabled={disabled}
      onClick={onClick}
      className="rounded p-0.5 hover:opacity-70 disabled:opacity-30"
      style={{ color: "var(--color-text-muted)" }}
    >
      {children}
    </button>
  );
}
