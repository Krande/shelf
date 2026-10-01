import { useMemo, useState } from "react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { Loader2, X } from "lucide-react";
import {
  fetchAdminUsers,
  fetchAuditActions,
  fetchAuditEvents,
  type AuditEvent,
  type AuditFilters,
  type AuditPage,
} from "@/api/admin";
import { ApiError } from "@/api/client";

/** How each action reads in the table. Anything missing falls back to
 *  the raw action name, so a new server-side action still shows. */
const ACTION_LABELS: Record<string, string> = {
  "space.create": "Created space",
  "space.update": "Changed space",
  "space.member.add": "Added member",
  "space.member.role": "Changed member role",
  "space.member.remove": "Removed member",
  "space.subscribe": "Subscribed",
  "space.unsubscribe": "Unsubscribed",
  "space.subscriber.remove": "Removed subscriber",
  "item.create": "Added document",
  "item.update": "Edited document",
  "item.trash": "Moved to trash",
  "item.restore": "Restored from trash",
  "item.delete": "Deleted permanently",
  "item.copy": "Copied document",
  "item.revision": "New revision",
  "attachment.upload": "Uploaded file",
  "attachment.delete": "Deleted file",
  "attachment.view": "Viewed file",
  "attachment.download": "Downloaded file",
  "collection.download": "Downloaded collection",
  "export.zip": "Downloaded ZIP",
  "export.item": "Exported document",
  "export.space": "Exported space",
  "collection.create": "Created collection",
  "collection.update": "Changed collection",
  "collection.delete": "Deleted collection",
  "item.collections": "Filed document",
  "tag.create": "Created tag",
  "tag.update": "Changed tag",
  "tag.delete": "Deleted tag",
  "item.tags": "Tagged document",
  "standard.revision.set": "Set standard revision",
  "standard.revision.delete": "Removed standard revision",
  "standard.pin.set": "Pinned standard",
  "standard.pin.remove": "Unpinned standard",
  "user.create": "Created user",
  "user.update": "Changed user",
  "processing.ocr": "Ran OCR",
  "processing.outline": "Rebuilt outline",
  "processing.restore": "Restored original",
  "processing.cancel": "Cancelled processing",
};

/** The groups offered as "all of …" in the action filter. */
const FAMILIES: { prefix: string; label: string }[] = [
  { prefix: "space.", label: "Spaces & sharing" },
  { prefix: "item.", label: "Documents" },
  { prefix: "attachment.", label: "Files & downloads" },
  { prefix: "export.", label: "Exports" },
  { prefix: "collection.", label: "Collections" },
  { prefix: "tag.", label: "Tags" },
  { prefix: "standard.", label: "Standards" },
  { prefix: "user.", label: "Users" },
  { prefix: "processing.", label: "Processing" },
];

export function actionLabel(action: string): string {
  return ACTION_LABELS[action] ?? action;
}

/** A filter applied by clicking a value in the table, shown as a chip
 *  so it reads as what it is rather than as a bare id. */
type Chip = { key: "actor_id" | "space_id" | "target_id"; label: string };

export default function AuditSection() {
  const [action, setAction] = useState("");
  const [chips, setChips] = useState<Record<string, Chip & { value: string }>>(
    {},
  );

  const filters: AuditFilters = useMemo(() => {
    const f: AuditFilters = {};
    if (action) f.action = action;
    for (const c of Object.values(chips)) f[c.key] = c.value;
    return f;
  }, [action, chips]);

  const events = useInfiniteQuery<AuditPage, ApiError>({
    queryKey: ["admin", "audit", filters],
    queryFn: ({ pageParam }) =>
      fetchAuditEvents(filters, pageParam as string | null),
    initialPageParam: null,
    getNextPageParam: (last) => last.next,
    retry: (_attempt, err) => err.status >= 500,
  });
  const actions = useQuery({
    queryKey: ["admin", "audit", "actions"],
    queryFn: fetchAuditActions,
    staleTime: Infinity,
  });
  const users = useQuery({
    queryKey: ["admin", "users"],
    queryFn: fetchAdminUsers,
  });

  function addChip(key: Chip["key"], value: string | null, label: string) {
    if (!value) return;
    setChips((prev) => ({ ...prev, [key]: { key, value, label } }));
  }
  function removeChip(key: string) {
    setChips((prev) => {
      const next = { ...prev };
      delete next[key];
      return next;
    });
  }

  const rows = events.data?.pages.flatMap((p) => p.events) ?? [];

  return (
    <section
      className="mb-6 rounded border p-4"
      style={{
        backgroundColor: "var(--color-surface)",
        borderColor: "var(--color-border)",
      }}
    >
      <h2 className="mb-1 text-sm font-medium">Audit log</h2>
      <p className="mb-3 text-xs" style={{ color: "var(--color-text-muted)" }}>
        Who did what, newest first: sharing changes, uploads, views and
        downloads, edits and deletions across every space. Click a person,
        space or object to narrow the list to it.
      </p>

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <select
          value={action}
          onChange={(e) => setAction(e.target.value)}
          aria-label="Filter by action"
          className="rounded border px-2 py-1 text-xs"
          style={{
            borderColor: "var(--color-border)",
            backgroundColor: "var(--color-surface)",
          }}
        >
          <option value="">All actions</option>
          <optgroup label="Groups">
            {FAMILIES.map((f) => (
              <option key={f.prefix} value={f.prefix}>
                All {f.label.toLowerCase()}
              </option>
            ))}
          </optgroup>
          <optgroup label="Actions">
            {(actions.data ?? []).map((a) => (
              <option key={a} value={a}>
                {actionLabel(a)}
              </option>
            ))}
          </optgroup>
        </select>
        <select
          value={chips.actor_id?.value ?? ""}
          onChange={(e) => {
            const u = users.data?.find((x) => x.id === e.target.value);
            if (u) addChip("actor_id", u.id, u.display_name);
            else removeChip("actor_id");
          }}
          aria-label="Filter by person"
          className="rounded border px-2 py-1 text-xs"
          style={{
            borderColor: "var(--color-border)",
            backgroundColor: "var(--color-surface)",
          }}
        >
          <option value="">Everyone</option>
          {(users.data ?? []).map((u) => (
            <option key={u.id} value={u.id}>
              {u.display_name}
            </option>
          ))}
        </select>
        {Object.values(chips)
          .filter((c) => c.key !== "actor_id")
          .map((c) => (
            <span
              key={c.key}
              className="flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs"
              style={{ borderColor: "var(--color-border)" }}
            >
              {c.key === "space_id" ? "Space: " : ""}
              {c.label}
              <button
                type="button"
                onClick={() => removeChip(c.key)}
                aria-label={`Remove filter ${c.label}`}
                className="rounded-full hover:opacity-70"
              >
                <X className="h-3 w-3" />
              </button>
            </span>
          ))}
      </div>

      {events.isLoading && (
        <p className="text-sm" style={{ color: "var(--color-text-muted)" }}>
          Loading…
        </p>
      )}
      {events.error && (
        <p className="text-sm text-red-600" role="alert">
          {events.error.status === 403
            ? "You no longer have the admin role."
            : `Could not load the audit log: ${events.error.message}`}
        </p>
      )}
      {events.data && rows.length === 0 && (
        <p
          className="text-sm italic"
          style={{ color: "var(--color-text-muted)" }}
        >
          Nothing recorded{action || Object.keys(chips).length ? " matching these filters" : " yet"}.
        </p>
      )}

      {rows.length > 0 && (
        <div
          className="overflow-x-auto rounded border"
          style={{ borderColor: "var(--color-border)" }}
        >
          <table className="w-full text-sm">
            <thead>
              <tr
                className="border-b text-left text-xs uppercase tracking-widest"
                style={{
                  borderColor: "var(--color-border)",
                  color: "var(--color-text-muted)",
                }}
              >
                <th className="px-3 py-2 font-normal">When</th>
                <th className="px-3 py-2 font-normal">Who</th>
                <th className="px-3 py-2 font-normal">What</th>
                <th className="px-3 py-2 font-normal">Space</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((e) => (
                <EventRow key={e.id} event={e} onFilter={addChip} />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {events.hasNextPage && (
        <button
          type="button"
          onClick={() => events.fetchNextPage()}
          disabled={events.isFetchingNextPage}
          className="mt-3 flex items-center gap-1 rounded border px-2 py-1 text-xs hover:opacity-80 disabled:opacity-50"
          style={{ borderColor: "var(--color-border)" }}
        >
          {events.isFetchingNextPage && (
            <Loader2 className="h-3 w-3 animate-spin" />
          )}
          Load older
        </button>
      )}
    </section>
  );
}

function EventRow({
  event: e,
  onFilter,
}: {
  event: AuditEvent;
  onFilter: (key: Chip["key"], value: string | null, label: string) => void;
}) {
  const when = new Date(e.created_at);
  const details = formatDetails(e.details);
  const linkStyle = "text-left hover:underline";
  return (
    <tr
      className="border-b align-top last:border-b-0"
      style={{ borderColor: "var(--color-border)" }}
    >
      <td
        className="whitespace-nowrap px-3 py-2 text-xs"
        style={{ color: "var(--color-text-muted)" }}
        title={when.toISOString()}
      >
        {when.toLocaleString()}
      </td>
      <td className="px-3 py-2">
        {e.actor_id ? (
          <button
            type="button"
            className={linkStyle}
            onClick={() =>
              onFilter("actor_id", e.actor_id, e.actor_name ?? "Unknown")
            }
            title={e.actor_email ?? undefined}
          >
            {e.actor_name ?? e.actor_email}
          </button>
        ) : (
          <span style={{ color: "var(--color-text-muted)" }}>
            {e.actor_email ?? "System"}
          </span>
        )}
        {e.via === "api" && (
          <span
            className="ml-1 rounded px-1 text-[10px] uppercase"
            style={{
              color: "var(--color-text-muted)",
              border: "1px solid var(--color-border)",
            }}
            title="Through an API token"
          >
            API
          </span>
        )}
      </td>
      <td className="px-3 py-2">
        <span>{actionLabel(e.action)}</span>
        {e.target_label && (
          <>
            {" "}
            {e.target_id ? (
              <button
                type="button"
                className={`${linkStyle} font-medium`}
                onClick={() =>
                  onFilter("target_id", e.target_id, e.target_label ?? "")
                }
                title="Show this object's history"
              >
                {e.target_label}
              </button>
            ) : (
              <span className="font-medium">{e.target_label}</span>
            )}
          </>
        )}
        {details && (
          <span
            className="block text-xs"
            style={{ color: "var(--color-text-muted)" }}
          >
            {details}
          </span>
        )}
      </td>
      <td className="px-3 py-2 text-xs">
        {e.space_id && (
          <button
            type="button"
            className={linkStyle}
            onClick={() =>
              onFilter("space_id", e.space_id, e.space_name ?? "Space")
            }
          >
            {e.space_name ?? "(deleted space)"}
          </button>
        )}
      </td>
    </tr>
  );
}

/** One line from the details object. A two-item array is a change
 *  (`old → new`); other arrays are lists; the rest prints as is. */
export function formatDetails(
  details: Record<string, unknown> | null,
): string | null {
  if (!details) return null;
  // Ids are kept in the row for cross-referencing, but a UUID in the
  // table tells a reader nothing — the names next to them do.
  const shown = Object.entries(details).filter(
    ([key, value]) =>
      !key.endsWith("_id") && !(typeof value === "string" && UUID.test(value)),
  );
  const parts = shown.map(([key, value]) => {
    const name = key.replace(/_/g, " ");
    if (Array.isArray(value)) {
      if (
        value.length === 2 &&
        key !== "fields" &&
        key !== "added" &&
        key !== "removed"
      ) {
        return `${name}: ${show(value[0])} → ${show(value[1])}`;
      }
      return `${name}: ${value.map(show).join(", ")}`;
    }
    return `${name}: ${show(value)}`;
  });
  return parts.length ? parts.join(" · ") : null;
}

const UUID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function show(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}
