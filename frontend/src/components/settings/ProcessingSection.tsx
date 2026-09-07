import { useState } from "react";
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
  Clock,
  FileSearch,
  Loader2,
  ScanText,
  Sparkles,
  XOctagon,
} from "lucide-react";
import {
  cancelProcessing,
  fetchProcessingStats,
  listProcessingAttachments,
  triggerOcr,
  triggerOutline,
  type ProcessingAttachment,
  type ProcessingBucket,
  type ProcessingStats,
} from "@/api/processing";

const ACTIVE_STATUSES = new Set(["queued", "running"]);

interface BucketDef {
  key: ProcessingBucket;
  label: string;
  description: string;
  Icon: typeof CheckCircle2;
  tone: "ok" | "warn" | "info" | "muted" | "bad";
  /** Counter selector for this bucket. */
  count: (s: ProcessingStats) => number;
}

const BUCKETS: BucketDef[] = [
  {
    key: "needs_ocr",
    label: "Needs OCR",
    description:
      "Sparse / garbage text-layer detected. Run OCR to make it searchable.",
    Icon: ScanText,
    tone: "warn",
    count: (s) => s.needs_ocr,
  },
  {
    key: "needs_outline",
    label: "Needs outline",
    description: "Long doc with no embedded ToC. Generate a heading outline.",
    Icon: Sparkles,
    tone: "info",
    count: (s) => s.needs_outline,
  },
  {
    key: "ocr_queued",
    label: "OCR queued",
    description: "Job published; waiting for the OCR worker.",
    Icon: Clock,
    tone: "info",
    count: (s) => s.ocr_queued,
  },
  {
    key: "ocr_running",
    label: "OCR running",
    description: "Worker is OCRing this PDF right now.",
    Icon: Loader2,
    tone: "info",
    count: (s) => s.ocr_running,
  },
  {
    key: "ocr_failed",
    label: "OCR failed",
    description: "Tesseract / OCRmyPDF gave up. Inspect, then retry.",
    Icon: AlertCircle,
    tone: "bad",
    count: (s) => s.ocr_failed,
  },
  {
    key: "outline_queued",
    label: "Outline queued",
    description: "Job published; waiting for the outline (GPU) worker.",
    Icon: Clock,
    tone: "info",
    count: (s) => s.outline_queued,
  },
  {
    key: "outline_running",
    label: "Outline running",
    description: "Marker is processing this PDF.",
    Icon: Loader2,
    tone: "info",
    count: (s) => s.outline_running,
  },
  {
    key: "outline_failed",
    label: "Outline failed",
    description: "Outline generation crashed past the redelivery cap.",
    Icon: AlertCircle,
    tone: "bad",
    count: (s) => s.outline_failed,
  },
  {
    key: "not_assessed",
    label: "Not assessed",
    description: "Extract worker hasn't run against this row yet.",
    Icon: FileSearch,
    tone: "muted",
    count: (s) => s.not_assessed,
  },
];

const TONE_COLORS: Record<BucketDef["tone"], string> = {
  ok: "var(--color-accent)",
  warn: "#f59e0b",
  info: "#3b82f6",
  muted: "var(--color-text-muted)",
  bad: "#ef4444",
};

function formatDate(s: string | null): string {
  if (!s) return "—";
  return new Date(s).toLocaleString();
}

function formatRatio(n: number | null): string {
  if (n == null) return "—";
  return `${(n * 100).toFixed(1)}%`;
}

/**
 * Settings panel for the post-extraction pipeline (OCR + outline).
 *
 * Per-bucket counts come from /api/me/processing/stats; clicking a
 * bucket loads the attachment list for it and exposes per-row
 * "Re-OCR" / "Generate outline" buttons that hit the manual trigger
 * endpoints. Useful when the auto-trigger missed a doc or after a
 * worker pod failure.
 */
export default function ProcessingSection() {
  const qc = useQueryClient();
  const stats = useQuery({
    queryKey: ["processing", "stats"],
    queryFn: fetchProcessingStats,
  });
  const [openBucket, setOpenBucket] = useState<ProcessingBucket | null>(null);
  const list = useQuery({
    queryKey: ["processing", "list", openBucket],
    queryFn: () => listProcessingAttachments(openBucket ?? "all", 200, 0),
    enabled: openBucket !== null,
  });

  const ocrMutate = useMutation({
    mutationFn: triggerOcr,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["processing"] });
    },
  });
  const outlineMutate = useMutation({
    mutationFn: triggerOutline,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["processing"] });
    },
  });
  const cancelMutate = useMutation({
    mutationFn: ({
      id,
      job,
    }: {
      id: string;
      job: "ocr" | "outline";
    }) => cancelProcessing(id, job),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["processing"] });
    },
  });

  return (
    <section
      className="mb-6 rounded border p-4"
      style={{
        backgroundColor: "var(--color-surface)",
        borderColor: "var(--color-border)",
      }}
    >
      <div className="mb-3">
        <h2 className="text-sm font-medium">PDF post-processing</h2>
        <p
          className="mt-0.5 text-xs"
          style={{ color: "var(--color-text-muted)" }}
        >
          OCR re-runs (Tesseract) and outline generation (Marker) for
          PDFs whose extracted text is sparse or whose embedded ToC is
          missing. The extract worker auto-flags candidates; use the
          buttons here to force a run on anything it skipped.
        </p>
      </div>

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
            const count = b.count(stats.data);
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
                      className={`h-3.5 w-3.5${
                        b.key === "ocr_running" || b.key === "outline_running"
                          ? " animate-spin"
                          : ""
                      }`}
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
                      <ProcessingTable
                        rows={list.data}
                        ocrPending={ocrMutate.isPending ? ocrMutate.variables : null}
                        outlinePending={
                          outlineMutate.isPending ? outlineMutate.variables : null
                        }
                        cancelPending={
                          cancelMutate.isPending
                            ? cancelMutate.variables?.id
                            : null
                        }
                        onOcr={(id) => ocrMutate.mutate(id)}
                        onOutline={(id) => outlineMutate.mutate(id)}
                        onCancel={(id, job) => cancelMutate.mutate({ id, job })}
                      />
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

function ProcessingTable({
  rows,
  ocrPending,
  outlinePending,
  cancelPending,
  onOcr,
  onOutline,
  onCancel,
}: {
  rows: ProcessingAttachment[];
  ocrPending: string | null | undefined;
  outlinePending: string | null | undefined;
  cancelPending: string | null | undefined;
  onOcr: (id: string) => void;
  onOutline: (id: string) => void;
  onCancel: (id: string, job: "ocr" | "outline") => void;
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-max min-w-full text-xs">
      <thead
        className="border-b"
        style={{ borderColor: "var(--color-border)" }}
      >
        <tr style={{ color: "var(--color-text-muted)" }}>
          <th className="px-3 py-1.5 text-left font-medium">Item / file</th>
          <th className="px-3 py-1.5 text-left font-medium">Pages</th>
          <th className="px-3 py-1.5 text-left font-medium">Chars/pg</th>
          <th className="px-3 py-1.5 text-left font-medium">α</th>
          <th className="px-3 py-1.5 text-left font-medium">OCR</th>
          <th className="px-3 py-1.5 text-left font-medium">Outline</th>
          <th className="px-3 py-1.5 text-right font-medium">Actions</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr
            key={row.id}
            className="border-b last:border-b-0"
            style={{ borderColor: "var(--color-border)" }}
          >
            <td className="px-3 py-1.5">
              <div className="font-medium">
                {row.item_title || "(untitled)"}
              </div>
              <div style={{ color: "var(--color-text-muted)" }}>
                {row.filename}
              </div>
            </td>
            <td
              className="px-3 py-1.5 font-mono"
              style={{ color: "var(--color-text-muted)" }}
            >
              {row.page_count ?? "—"}
            </td>
            <td
              className="px-3 py-1.5 font-mono"
              style={{ color: "var(--color-text-muted)" }}
            >
              {row.chars_per_page == null
                ? "—"
                : Math.round(row.chars_per_page)}
            </td>
            <td
              className="px-3 py-1.5 font-mono"
              style={{ color: "var(--color-text-muted)" }}
              title={`replacement chars: ${formatRatio(row.replacement_char_ratio)}`}
            >
              {formatRatio(row.alpha_ratio)}
            </td>
            <td className="px-3 py-1.5">
              <StatusPill status={row.ocr_status} flagged={row.needs_ocr} />
              {ACTIVE_STATUSES.has(row.ocr_status) && (
                <Progress
                  done={row.progress_done}
                  total={row.progress_total}
                  label="pages"
                />
              )}
              {row.ocr_completed_at && (
                <div
                  className="text-[10px]"
                  style={{ color: "var(--color-text-muted)" }}
                >
                  {formatDate(row.ocr_completed_at)}
                </div>
              )}
              {row.ocr_engine && (
                <div
                  className="truncate font-mono text-[10px]"
                  title={row.ocr_engine}
                  style={{
                    maxWidth: "16rem",
                    color: "var(--color-text-muted)",
                  }}
                >
                  {row.ocr_engine}
                </div>
              )}
            </td>
            <td className="px-3 py-1.5">
              <StatusPill
                status={row.outline_status}
                flagged={row.needs_outline}
              />
              {ACTIVE_STATUSES.has(row.outline_status) && (
                <Progress
                  done={row.progress_done}
                  total={row.progress_total}
                  label="blocks"
                />
              )}
              {row.outline_completed_at && (
                <div
                  className="text-[10px]"
                  style={{ color: "var(--color-text-muted)" }}
                >
                  {formatDate(row.outline_completed_at)}
                </div>
              )}
              {row.outline_engine && (
                <div
                  className="truncate font-mono text-[10px]"
                  title={row.outline_engine}
                  style={{
                    maxWidth: "16rem",
                    color: "var(--color-text-muted)",
                  }}
                >
                  {row.outline_engine}
                </div>
              )}
            </td>
            <td className="px-3 py-1.5 text-right">
              <div className="flex justify-end gap-2">
                {ACTIVE_STATUSES.has(row.ocr_status) ? (
                  <button
                    onClick={() => onCancel(row.id, "ocr")}
                    disabled={cancelPending === row.id}
                    title="Cancel this OCR job. Tesseract can't be interrupted, but the result will be discarded."
                    className="inline-flex items-center gap-1 rounded border px-2 py-0.5 hover:opacity-80 disabled:opacity-50"
                    style={{
                      borderColor: "var(--color-border)",
                      color: "#ef4444",
                    }}
                  >
                    {cancelPending === row.id ? (
                      <Loader2 className="h-3 w-3 animate-spin" />
                    ) : (
                      <XOctagon className="h-3 w-3" />
                    )}
                    Cancel
                  </button>
                ) : (
                  <button
                    onClick={() => onOcr(row.id)}
                    disabled={ocrPending === row.id}
                    title="Force re-OCR via OCRmyPDF + Tesseract"
                    className="inline-flex items-center gap-1 rounded border px-2 py-0.5 hover:opacity-80 disabled:opacity-50"
                    style={{
                      borderColor: "var(--color-border)",
                      color: "var(--color-text)",
                    }}
                  >
                    {ocrPending === row.id ? (
                      <Loader2 className="h-3 w-3 animate-spin" />
                    ) : (
                      <ScanText className="h-3 w-3" />
                    )}
                    OCR
                  </button>
                )}
                {ACTIVE_STATUSES.has(row.outline_status) ? (
                  <button
                    onClick={() => onCancel(row.id, "outline")}
                    disabled={cancelPending === row.id}
                    title="Cancel this outline job. Marker can't be interrupted, but the result will be discarded."
                    className="inline-flex items-center gap-1 rounded border px-2 py-0.5 hover:opacity-80 disabled:opacity-50"
                    style={{
                      borderColor: "var(--color-border)",
                      color: "#ef4444",
                    }}
                  >
                    {cancelPending === row.id ? (
                      <Loader2 className="h-3 w-3 animate-spin" />
                    ) : (
                      <XOctagon className="h-3 w-3" />
                    )}
                    Cancel
                  </button>
                ) : (
                  <button
                    onClick={() => onOutline(row.id)}
                    disabled={outlinePending === row.id}
                    title="Generate heading outline via Marker"
                    className="inline-flex items-center gap-1 rounded border px-2 py-0.5 hover:opacity-80 disabled:opacity-50"
                    style={{
                      borderColor: "var(--color-border)",
                      color: "var(--color-text)",
                    }}
                  >
                    {outlinePending === row.id ? (
                      <Loader2 className="h-3 w-3 animate-spin" />
                    ) : (
                      <Sparkles className="h-3 w-3" />
                    )}
                    Outline
                  </button>
                )}
              </div>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
    </div>
  );
}

const STATUS_TONES: Record<string, string> = {
  untouched: "var(--color-text-muted)",
  queued: "#3b82f6",
  running: "#3b82f6",
  done: "var(--color-accent)",
  failed: "#ef4444",
};

function Progress({
  done,
  total,
  label,
}: {
  done: number | null;
  total: number | null;
  label: string;
}) {
  if (done == null) {
    return (
      <div
        className="mt-0.5 font-mono text-[10px]"
        style={{ color: "var(--color-text-muted)" }}
      >
        starting…
      </div>
    );
  }
  const pct =
    total != null && total > 0 ? Math.round((done / total) * 100) : null;
  return (
    <div className="mt-0.5">
      <div
        className="font-mono text-[10px]"
        style={{ color: "var(--color-text-muted)" }}
      >
        {total != null
          ? `${done} / ${total} ${label}${pct != null ? ` · ${pct}%` : ""}`
          : `${done} ${label}`}
      </div>
      {total != null && total > 0 && (
        <div
          style={{
            height: "3px",
            width: "120px",
            backgroundColor:
              "color-mix(in srgb, var(--color-text-muted) 20%, transparent)",
            borderRadius: "2px",
            overflow: "hidden",
          }}
        >
          <div
            style={{
              height: "100%",
              width: `${Math.min(100, Math.max(0, (done / total) * 100))}%`,
              backgroundColor: "var(--color-accent)",
              transition: "width 0.4s ease-out",
            }}
          />
        </div>
      )}
    </div>
  );
}

function StatusPill({ status, flagged }: { status: string; flagged: boolean }) {
  return (
    <span
      className="inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[10px] uppercase tracking-wide"
      style={{
        borderColor: "var(--color-border)",
        color: STATUS_TONES[status] ?? "var(--color-text-muted)",
      }}
    >
      {status}
      {flagged && status === "untouched" && (
        <span
          title="Heuristic flagged this — auto-trigger should run"
          style={{ color: "#f59e0b" }}
        >
          •
        </span>
      )}
    </span>
  );
}
