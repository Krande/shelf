import { useMemo, useState } from "react";
import { ArrowDown, ArrowUp, X } from "lucide-react";
import {
  BUILTIN_COLUMNS,
  columnLabel,
  fieldColumnHint,
  fieldColumns,
  moveColumn,
  sourceName,
  type ColumnKey,
  type ResolvedProfile,
} from "@/lib/libraryColumns";

export interface ProfileDraft {
  description: string;
  /** null: inherit. */
  columns: ColumnKey[] | null;
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
  /** Shown, not editable — an inherited collection seen by a reader. */
  readOnly?: boolean;
  onClose: () => void;
  onSave: (draft: ProfileDraft) => void;
}) {
  const [desc, setDesc] = useState(description ?? "");
  const [own, setOwn] = useState<ColumnKey[] | null>(columns);
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
          <section>
            <label className="mb-1 block text-sm font-medium" htmlFor="profile-desc">
              Description
            </label>
            <textarea
              id="profile-desc"
              autoFocus={!readOnly}
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
                <ol>
                  {own.map((k, i) => (
                    <li
                      key={k}
                      className="flex items-center gap-2 border-b px-2 py-1 text-sm last:border-b-0"
                      style={{ borderColor: "var(--color-border)" }}
                    >
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
          <button
            type="button"
            onClick={onClose}
            className="rounded px-3 py-1.5 text-sm hover:opacity-70"
            style={{ color: "var(--color-text-muted)" }}
          >
            {readOnly ? "Close" : "Cancel"}
          </button>
          {!readOnly && (
            <button
              type="button"
              onClick={() => onSave({ description: desc, columns: own })}
              className="rounded px-3 py-1.5 text-sm font-medium text-white"
              style={{ backgroundColor: "var(--color-accent)" }}
            >
              Save
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

/** A column's name, with the item types it belongs to when another
 *  field shares that name (a standard's Pages vs an article's). */
function optionLabel(k: ColumnKey): string {
  const hint = fieldColumnHint(k);
  return hint ? `${columnLabel(k)} (${hint})` : columnLabel(k);
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
