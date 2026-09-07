import { useMemo, useState } from "react";
import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import {
  AlertCircle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleSlash,
  Clock,
  FileSearch,
  Loader2,
  RefreshCw,
} from "lucide-react";
import {
  fetchExtractionStats,
  listExtractionAttachments,
  rescanExtraction,
  rescanOneAttachment,
  type ExtractionFilter,
  type ExtractionStats,
} from "@/api/extraction";

interface BucketDef {
  key: ExtractionFilter;
  label: string;
  description: string;
  /** "Action" buckets count toward the default Rescan button. The
   *  rest are status-only and only re-runnable per-row. */
  rescanByDefault: boolean;
  Icon: typeof CheckCircle2;
  tone: "ok" | "warn" | "info" | "muted";
}

const BUCKETS: BucketDef[] = [
  {
    key: "extracted",
    label: "Extracted",
    description: "Body text indexed; searchable via PDF body scope.",
    rescanByDefault: false,
    Icon: CheckCircle2,
    tone: "ok",
  },
  {
    key: "pending",
    label: "Pending",
    description: "Enqueued, waiting for the worker.",
    rescanByDefault: true,
    Icon: Clock,
    tone: "info",
  },
  {
    key: "missing",
    label: "Never extracted",
    description: "Uploaded before extraction shipped — not yet enqueued.",
    rescanByDefault: true,
    Icon: FileSearch,
    tone: "warn",
  },
  {
    key: "empty",
    label: "Empty / scanned",
    description:
      "PDF parsed but no text recovered. Likely a scan needing OCR.",
    rescanByDefault: false,
    Icon: CircleSlash,
    tone: "muted",
  },
  {
    key: "failed",
    label: "Failed",
    description: "Extraction errored after redelivery cap. Inspect, then retry.",
    rescanByDefault: false,
    Icon: AlertCircle,
    tone: "warn",
  },
];

const TONE_COLORS: Record<BucketDef["tone"], string> = {
  ok: "var(--color-accent)",
  warn: "#f59e0b",
  info: "#3b82f6",
  muted: "var(--color-text-muted)",
};

function statValue(stats: ExtractionStats, key: ExtractionFilter): number {
  if (key === "missing") return stats.missing;
  return stats[key] ?? 0;
}

function formatDate(s: string | null): string {
  if (!s) return "—";
  return new Date(s).toLocaleString();
}

/**
 * Settings panel for the PDF body-text extraction pipeline.
 *
 * Surfaces the per-status counts, lets the user drill into any
 * bucket to see the actual rows, and exposes two action buttons:
 * "Rescan needed" (re-enqueues pending + never-extracted) and a
 * per-row rescan that bypasses status checks.
 */
export default function ExtractionSection() {
  const qc = useQueryClient();
  const stats = useQuery({
    queryKey: ["extraction", "stats"],
    queryFn: fetchExtractionStats,
  });
  const [openBucket, setOpenBucket] = useState<ExtractionFilter | null>(null);
  const list = useQuery({
    queryKey: ["extraction", "list", openBucket],
    queryFn: () => listExtractionAttachments(openBucket, 200, 0),
    enabled: openBucket !== null,
  });

  const rescan = useMutation({
    mutationFn: () => rescanExtraction(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["extraction"] });
    },
  });
  const rescanOne = useMutation({
    mutationFn: rescanOneAttachment,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["extraction"] });
    },
  });

  const toRescan = useMemo(() => {
    if (!stats.data) return 0;
    return BUCKETS.filter((b) => b.rescanByDefault).reduce(
      (acc, b) => acc + statValue(stats.data, b.key),
      0,
    );
  }, [stats.data]);

  return (
    <section
      className="mb-6 rounded border p-4"
      style={{
        backgroundColor: "var(--color-surface)",
        borderColor: "var(--color-border)",
      }}
    >
      <div className="mb-3 flex items-center justify-between">
        <div>
          <h2 className="text-sm font-medium">PDF text extraction</h2>
          <p
            className="mt-0.5 text-xs"
            style={{ color: "var(--color-text-muted)" }}
          >
            Body text from each PDF feeds the "PDF body" search scope.
            Empty rows are likely scans that need OCR.
          </p>
        </div>
        <button
          onClick={() => rescan.mutate()}
          disabled={rescan.isPending || toRescan === 0}
          className="flex items-center gap-2 rounded border px-3 py-1.5 text-xs hover:opacity-80 disabled:opacity-50"
          style={{
            borderColor: "var(--color-border)",
            color: "var(--color-text)",
          }}
          title={
            toRescan === 0
              ? "Nothing pending or unscanned"
              : `Re-enqueue ${toRescan} attachment${toRescan === 1 ? "" : "s"}`
          }
        >
          {rescan.isPending ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <RefreshCw className="h-3.5 w-3.5" />
          )}
          Rescan needed
          {toRescan > 0 && ` (${toRescan})`}
        </button>
      </div>

      {rescan.data && (
        <p
          className="mb-3 rounded border px-3 py-1.5 text-xs"
          style={{
            borderColor: "var(--color-border)",
            color: "var(--color-text-muted)",
          }}
        >
          Selected {rescan.data.selected}, enqueued{" "}
          {rescan.data.enqueued}.
          {rescan.data.selected !== rescan.data.enqueued && (
            <>
              {" "}
              The remainder will be picked up automatically — the
              row state was already reset to pending.
            </>
          )}
        </p>
      )}

      {stats.isLoading && (
        <p className="text-xs" style={{ color: "var(--color-text-muted)" }}>
          Loading…
        </p>
      )}
      {stats.error && (
        <p className="text-xs" style={{ color: "#ef4444" }}>
          Failed to load stats: {(stats.error as Error).message}
        </p>
      )}
      {stats.data && (
        <div className="flex flex-col gap-1">
          {BUCKETS.map((b) => {
            const count = statValue(stats.data, b.key);
            const open = openBucket === b.key;
            const Icon = b.Icon;
            return (
              <div key={b.key}>
                <button
                  onClick={() => setOpenBucket(open ? null : b.key)}
                  className="flex w-full items-center justify-between rounded border px-3 py-2 text-left text-sm hover:opacity-90"
                  style={{
                    borderColor: "var(--color-border)",
                    backgroundColor:
                      "color-mix(in srgb, var(--color-text-muted) 4%, transparent)",
                    cursor: count === 0 ? "default" : "pointer",
                  }}
                  disabled={count === 0}
                  aria-expanded={open}
                >
                  <span className="flex items-center gap-2">
                    {count === 0 ? (
                      <span className="inline-block h-3 w-3" />
                    ) : open ? (
                      <ChevronDown className="h-3 w-3" />
                    ) : (
                      <ChevronRight className="h-3 w-3" />
                    )}
                    <Icon
                      className="h-3.5 w-3.5"
                      style={{ color: TONE_COLORS[b.tone] }}
                    />
                    <span>{b.label}</span>
                    <span
                      className="text-xs"
                      style={{ color: "var(--color-text-muted)" }}
                    >
                      — {b.description}
                    </span>
                  </span>
                  <span
                    className="font-mono text-xs"
                    style={{ color: TONE_COLORS[b.tone] }}
                  >
                    {count}
                  </span>
                </button>
                {open && (
                  <div
                    className="mt-1 rounded border"
                    style={{ borderColor: "var(--color-border)" }}
                  >
                    {list.isLoading && (
                      <p
                        className="px-3 py-2 text-xs"
                        style={{ color: "var(--color-text-muted)" }}
                      >
                        Loading attachments…
                      </p>
                    )}
                    {list.error && (
                      <p
                        className="px-3 py-2 text-xs"
                        style={{ color: "#ef4444" }}
                      >
                        Failed: {(list.error as Error).message}
                      </p>
                    )}
                    {list.data && list.data.length === 0 && (
                      <p
                        className="px-3 py-2 text-xs"
                        style={{ color: "var(--color-text-muted)" }}
                      >
                        Nothing in this bucket.
                      </p>
                    )}
                    {list.data && list.data.length > 0 && (
                      <div className="overflow-x-auto">
                      <table className="w-max min-w-full text-xs">
                        <thead
                          className="border-b"
                          style={{ borderColor: "var(--color-border)" }}
                        >
                          <tr style={{ color: "var(--color-text-muted)" }}>
                            <th className="px-3 py-1.5 text-left font-medium">
                              Item / file
                            </th>
                            <th className="px-3 py-1.5 text-left font-medium">
                              Chars
                            </th>
                            <th className="px-3 py-1.5 text-left font-medium">
                              Last attempt
                            </th>
                            <th className="px-3 py-1.5"></th>
                          </tr>
                        </thead>
                        <tbody>
                          {list.data.map((row) => (
                            <tr
                              key={row.id}
                              className="border-b last:border-b-0"
                              style={{ borderColor: "var(--color-border)" }}
                            >
                              <td className="px-3 py-1.5">
                                <div className="font-medium">
                                  {row.item_title || "(untitled)"}
                                </div>
                                <div
                                  style={{
                                    color: "var(--color-text-muted)",
                                  }}
                                >
                                  {row.filename}
                                </div>
                              </td>
                              <td
                                className="px-3 py-1.5 font-mono"
                                style={{ color: "var(--color-text-muted)" }}
                              >
                                {row.text_chars ?? "—"}
                              </td>
                              <td
                                className="px-3 py-1.5"
                                style={{ color: "var(--color-text-muted)" }}
                              >
                                {formatDate(row.extracted_at)}
                              </td>
                              <td className="px-3 py-1.5 text-right">
                                <button
                                  onClick={() => rescanOne.mutate(row.id)}
                                  disabled={
                                    rescanOne.isPending &&
                                    rescanOne.variables === row.id
                                  }
                                  className="inline-flex items-center gap-1 rounded px-2 py-0.5 hover:opacity-80 disabled:opacity-50"
                                  style={{
                                    color: "var(--color-accent)",
                                  }}
                                >
                                  {rescanOne.isPending &&
                                  rescanOne.variables === row.id ? (
                                    <Loader2 className="h-3 w-3 animate-spin" />
                                  ) : (
                                    <RefreshCw className="h-3 w-3" />
                                  )}
                                  Rescan
                                </button>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
