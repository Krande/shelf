/**
 * The revision panel for an engineering standard.
 *
 * Answers the two questions you have when you open one: what other
 * editions exist, and is this the one I should be reading. When the item
 * isn't filed under a standard yet, it offers the form that files it.
 *
 * "Latest" is relative to the reader — it means the newest edition *they
 * can open*, not the newest row in the database. When the instance holds
 * something newer that they can't reach, the panel says so rather than
 * presenting a stale edition as current.
 */

import { type FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BookMarked, Check, Loader2, Pin, PinOff } from "lucide-react";
import { ApiError } from "@/api/client";
import type { Item } from "@/api/items";
import {
  clearPin,
  fetchRevisions,
  linkRevision,
  setPin,
  unlinkRevision,
  type RevisionsResponse,
} from "@/api/standards";

export default function StandardRevisions({
  item,
  spaceSlug,
  canEdit,
  canPin,
  onSelectItem,
}: {
  item: Item;
  spaceSlug: string | null;
  /** Editor on the item's own space — an inherited copy is read-only. */
  canEdit: boolean;
  /** Owner of the space being viewed; pinning is an owner decision. */
  canPin: boolean;
  onSelectItem?: (itemId: string) => void;
}) {
  const revisions = useQuery<RevisionsResponse, ApiError>({
    queryKey: ["revisions", item.id, spaceSlug],
    queryFn: () => fetchRevisions(item.id, spaceSlug ?? undefined),
    // A 404 means "not filed under a standard", which is a normal state
    // and not worth retrying or reporting as an error.
    retry: (_a, err) => err.status >= 500,
  });

  if (revisions.isLoading) {
    return (
      <p className="text-xs" style={{ color: "var(--color-text-muted)" }}>
        Loading revisions…
      </p>
    );
  }

  if (revisions.error) {
    if (revisions.error.status === 404) {
      return canEdit ? <LinkForm item={item} spaceSlug={spaceSlug} /> : null;
    }
    return (
      <p className="text-xs text-red-600" role="alert">
        Could not load revisions: {revisions.error.message}
      </p>
    );
  }

  const data = revisions.data;
  if (!data) return null;

  return (
    <Panel
      data={data}
      item={item}
      spaceSlug={spaceSlug}
      canEdit={canEdit}
      canPin={canPin}
      onSelectItem={onSelectItem}
    />
  );
}

function Panel({
  data,
  item,
  spaceSlug,
  canEdit,
  canPin,
  onSelectItem,
}: {
  data: RevisionsResponse;
  item: Item;
  spaceSlug: string | null;
  canEdit: boolean;
  canPin: boolean;
  onSelectItem?: (itemId: string) => void;
}) {
  const qc = useQueryClient();
  const current = data.revisions.find((r) => r.item_id === item.id);
  const latest = data.revisions.find((r) => r.is_latest);
  const pinned = data.revisions.find((r) => r.is_pinned);

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["revisions"] });
    qc.invalidateQueries({ queryKey: ["items"] });
    if (spaceSlug) qc.invalidateQueries({ queryKey: ["pins", spaceSlug] });
  };

  const pin = useMutation({
    mutationFn: () => setPin(spaceSlug!, data.family.id, item.id),
    onSuccess: invalidate,
  });
  const unpin = useMutation({
    mutationFn: () => clearPin(spaceSlug!, data.family.id),
    onSuccess: invalidate,
  });
  const unlink = useMutation({
    mutationFn: () => unlinkRevision(item.id),
    onSuccess: invalidate,
  });

  return (
    <section
      className="rounded border p-3"
      style={{ borderColor: "var(--color-border)" }}
    >
      <div className="mb-2 flex items-start gap-2">
        <BookMarked
          className="mt-0.5 h-4 w-4 shrink-0"
          style={{ color: "var(--color-text-muted)" }}
        />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium">
            {data.family.body} {data.family.designation}
          </p>
          {data.family.title && (
            <p className="text-xs" style={{ color: "var(--color-text-muted)" }}>
              {data.family.title}
            </p>
          )}
        </div>
      </div>

      <div className="mb-2 flex flex-wrap items-center gap-1">
        {current?.is_latest ? (
          <Badge tone="good">
            <Check className="h-3 w-3" />
            Latest edition
          </Badge>
        ) : latest ? (
          <Badge tone="warn">
            Superseded — {latest.label} is current
          </Badge>
        ) : null}
        {current?.superseded && <Badge tone="warn">Withdrawn</Badge>}
        {current?.is_pinned && (
          <Badge tone="info">
            <Pin className="h-3 w-3" />
            This project's edition
          </Badge>
        )}
        {!data.is_latest_known && (
          <Badge tone="warn">
            A newer edition exists that you don't have access to
          </Badge>
        )}
      </div>

      <label className="block">
        <span
          className="mb-1 block text-xs"
          style={{ color: "var(--color-text-muted)" }}
        >
          Revisions ({data.revisions.length})
        </span>
        <select
          value={item.id}
          aria-label={`Revisions of ${data.family.designation}`}
          onChange={(e) => onSelectItem?.(e.target.value)}
          disabled={!onSelectItem}
          className="w-full rounded border px-2 py-1 text-sm disabled:opacity-60"
          style={{
            borderColor: "var(--color-border)",
            backgroundColor: "var(--color-surface)",
          }}
        >
          {data.revisions.map((r) => (
            <option key={r.item_id} value={r.item_id}>
              {r.label}
              {r.issued_on ? ` · ${r.issued_on}` : ""}
              {r.is_latest ? " · latest" : ""}
              {r.is_pinned ? " · pinned" : ""}
              {r.superseded ? " · withdrawn" : ""}
            </option>
          ))}
        </select>
      </label>

      {(canPin || canEdit) && (
        <div className="mt-2 flex flex-wrap gap-1">
          {canPin && spaceSlug && !current?.is_pinned && (
            <button
              onClick={() => pin.mutate()}
              disabled={pin.isPending}
              className="flex items-center gap-1 rounded border px-2 py-1 text-xs hover:opacity-80 disabled:opacity-50"
              style={{ borderColor: "var(--color-border)" }}
            >
              {pin.isPending ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <Pin className="h-3 w-3" />
              )}
              Use this edition here
            </button>
          )}
          {canPin && spaceSlug && pinned && (
            <button
              onClick={() => unpin.mutate()}
              disabled={unpin.isPending}
              className="flex items-center gap-1 rounded border px-2 py-1 text-xs hover:opacity-80 disabled:opacity-50"
              style={{ borderColor: "var(--color-border)" }}
            >
              <PinOff className="h-3 w-3" />
              Unpin ({pinned.label})
            </button>
          )}
          {canEdit && (
            <button
              onClick={() => unlink.mutate()}
              disabled={unlink.isPending}
              className="rounded border px-2 py-1 text-xs hover:opacity-80 disabled:opacity-50"
              style={{ borderColor: "var(--color-border)" }}
            >
              Not a standard
            </button>
          )}
        </div>
      )}

      {canPin && pinned && (
        <p className="mt-2 text-xs" style={{ color: "var(--color-text-muted)" }}>
          This space lists only {pinned.label}. Use "Show all revisions" in the
          library toolbar to see the rest.
        </p>
      )}

      {(pin.error || unpin.error || unlink.error) && (
        <p className="mt-2 text-xs text-red-600" role="alert">
          {(pin.error ?? unpin.error ?? unlink.error)?.message}
        </p>
      )}
    </section>
  );
}

function Badge({
  tone,
  children,
}: {
  tone: "good" | "warn" | "info";
  children: React.ReactNode;
}) {
  const color =
    tone === "good"
      ? "var(--color-accent)"
      : tone === "warn"
        ? "#b45309"
        : "var(--color-text-muted)";
  return (
    <span
      className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs"
      style={{
        color,
        backgroundColor: `color-mix(in srgb, ${color} 12%, transparent)`,
      }}
    >
      {children}
    </span>
  );
}

/**
 * File an item under a standard.
 *
 * Prefilled from the item's own metadata fields, which the Engineering
 * Standard item type already asks for — so for anything entered through
 * that form this is usually a single click.
 */
function LinkForm({
  item,
  spaceSlug,
}: {
  item: Item;
  spaceSlug: string | null;
}) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const data = item.data as Record<string, unknown>;
  const str = (key: string) =>
    typeof data[key] === "string" ? (data[key] as string) : "";

  const [body, setBody] = useState(str("standardBody"));
  const [designation, setDesignation] = useState(
    str("designation") || str("title"),
  );
  const [label, setLabel] = useState(str("edition") || str("date"));
  const [issuedOn, setIssuedOn] = useState(str("issuedOn"));

  const link = useMutation({
    mutationFn: () =>
      linkRevision(item.id, {
        body: body.trim(),
        designation: designation.trim(),
        label: label.trim(),
        issued_on: issuedOn.trim() || null,
        title: str("title") || null,
      }),
    onSuccess: () => {
      setOpen(false);
      qc.invalidateQueries({ queryKey: ["revisions"] });
      if (spaceSlug) qc.invalidateQueries({ queryKey: ["pins", spaceSlug] });
    },
  });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!body.trim() || !designation.trim() || !label.trim()) return;
    link.mutate();
  }

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="flex items-center gap-1 rounded border px-2 py-1 text-xs hover:opacity-80"
        style={{ borderColor: "var(--color-border)" }}
      >
        <BookMarked className="h-3.5 w-3.5" />
        File as a standard revision
      </button>
    );
  }

  return (
    <form
      onSubmit={onSubmit}
      className="rounded border p-3"
      style={{ borderColor: "var(--color-border)" }}
    >
      <p className="mb-2 text-xs" style={{ color: "var(--color-text-muted)" }}>
        Links this item to a standard so its editions know about each other.
        Matching is on body + designation, ignoring case — type it the same way
        as the other editions and they'll share one history.
      </p>
      <div className="flex flex-col gap-1">
        <Field
          label="Issuing body"
          value={body}
          onChange={setBody}
          placeholder="Who publishes it"
          required
        />
        <Field
          label="Designation"
          value={designation}
          onChange={setDesignation}
          placeholder="Designation, without the edition"
          required
        />
        <Field
          label="Edition"
          value={label}
          onChange={setLabel}
          placeholder="2020, Rev. 5, Ed. 3"
          required
        />
        <Field
          label="Issued"
          value={issuedOn}
          onChange={setIssuedOn}
          placeholder="2020-11-01 (optional)"
        />
      </div>
      <div className="mt-2 flex gap-1">
        <button
          type="submit"
          disabled={link.isPending}
          className="flex items-center gap-1 rounded border px-2 py-1 text-xs hover:opacity-80 disabled:opacity-50"
          style={{ borderColor: "var(--color-border)" }}
        >
          {link.isPending && <Loader2 className="h-3 w-3 animate-spin" />}
          Save
        </button>
        <button
          type="button"
          onClick={() => setOpen(false)}
          className="rounded border px-2 py-1 text-xs hover:opacity-80"
          style={{ borderColor: "var(--color-border)" }}
        >
          Cancel
        </button>
      </div>
      <p className="mt-1 text-xs" style={{ color: "var(--color-text-muted)" }}>
        An edition with no issue date sorts last and is never marked latest —
        labels like "Rev. 5" and "2020" can't be compared to each other.
      </p>
      {link.error && (
        <p className="mt-1 text-xs text-red-600" role="alert">
          {link.error.message}
        </p>
      )}
    </form>
  );
}

function Field({
  label,
  value,
  onChange,
  placeholder,
  required,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  required?: boolean;
}) {
  return (
    <label className="flex items-center gap-2 text-xs">
      <span className="w-24 shrink-0" style={{ color: "var(--color-text-muted)" }}>
        {label}
      </span>
      <input
        required={required}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        aria-label={label}
        className="min-w-0 flex-1 rounded border px-2 py-1 text-xs"
        style={{
          borderColor: "var(--color-border)",
          backgroundColor: "var(--color-surface)",
        }}
      />
    </label>
  );
}
