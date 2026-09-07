import { useEffect, useRef, useState } from "react";
import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import {
  Cpu,
  Loader2,
  RotateCcw,
  ScanText,
  Settings2,
  Sparkles,
  XOctagon,
} from "lucide-react";
import {
  cancelProcessing,
  fetchProcessingRow,
  fetchServerInfo,
  restoreOriginal,
  triggerOcr,
  triggerOcrGpu,
  triggerOutline,
  type ProcessingRow,
  type ProcessingStatus,
} from "@/api/processing";

const ACTIVE: ProcessingStatus[] = ["queued", "running"];

/**
 * Small toolbar dropdown for re-OCR / outline regeneration on the
 * currently-open PDF. Polls /processing while a job is queued or
 * running so the badge animates without a manual reload, then stops
 * polling once the row reaches a terminal state.
 */
export default function ProcessingMenu({
  attachmentId,
}: {
  attachmentId: string;
}) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // Cache the API/worker version once per session — it doesn't
  // change without a pod restart, and the menu opens often enough
  // that re-fetching every time would be noisy.
  const server = useQuery({
    queryKey: ["server-info"],
    queryFn: fetchServerInfo,
    staleTime: 1000 * 60 * 60,
  });
  const proc = useQuery({
    queryKey: ["processing", "row", attachmentId],
    queryFn: () => fetchProcessingRow(attachmentId),
    // Poll while a job is queued / running. The interval drops to
    // false once the row is idle so we don't burn requests on a
    // doc that's already done.
    refetchInterval: (q) => {
      const data = q.state.data as ProcessingRow | undefined;
      if (!data) return false;
      if (
        ACTIVE.includes(data.ocr_status) ||
        ACTIVE.includes(data.outline_status)
      ) {
        return 3_000;
      }
      return false;
    },
  });

  const ocr = useMutation({
    mutationFn: () => triggerOcr(attachmentId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["processing"] });
      qc.invalidateQueries({ queryKey: ["derivations", attachmentId] });
      setOpen(false);
    },
  });
  const outline = useMutation({
    mutationFn: () => triggerOutline(attachmentId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["processing"] });
      qc.invalidateQueries({ queryKey: ["derivations", attachmentId] });
      setOpen(false);
    },
  });
  const ocrGpu = useMutation({
    mutationFn: () => triggerOcrGpu(attachmentId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["processing"] });
      qc.invalidateQueries({ queryKey: ["derivations", attachmentId] });
      setOpen(false);
    },
  });
  const cancel = useMutation({
    mutationFn: (job: "ocr" | "outline") =>
      cancelProcessing(attachmentId, job),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["processing"] });
    },
  });
  const restore = useMutation({
    mutationFn: () => restoreOriginal(attachmentId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["processing"] });
      // Force the reader to refetch the PDF body — the storage key
      // is unchanged but the bytes are different, so the stale
      // pdf.js doc is no good. The simplest reliable hammer is a
      // hard reload; refining to a query-key-invalidation only the
      // reader listens for is a follow-up.
      window.location.reload();
    },
  });

  // Outside-click closes the menu.
  useEffect(() => {
    if (!open) return;
    function onDown(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  const data = proc.data;
  const isActive =
    !!data &&
    (ACTIVE.includes(data.ocr_status) ||
      ACTIVE.includes(data.outline_status));

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        aria-label="Processing"
        title="Re-OCR / regenerate outline"
        aria-expanded={open}
        className="flex items-center gap-1 rounded px-2 py-1 text-xs hover:opacity-80"
        style={{
          color: open
            ? "var(--color-accent)"
            : "var(--color-text-muted)",
        }}
      >
        {isActive ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
        ) : (
          <Settings2 className="h-3.5 w-3.5" />
        )}
      </button>
      {open && (
        <div
          /*
           * Desktop: 18rem-wide dropdown anchored to the trigger
           * button's right edge.
           * Mobile (< sm): the toolbar wraps and the trigger can
           * land anywhere on a second row, so a right-anchored
           * absolute dropdown ends up bleeding off the left edge
           * of the viewport. Switch to fixed positioning pinned
           * to the viewport with a small inset on each side and
           * w-auto so the inset-pair sets the width — that way
           * the dropdown is always centred in the visible area
           * regardless of where the trigger sits.
           */
          className="absolute right-0 top-full z-20 mt-1 w-72 rounded border shadow-md max-sm:fixed max-sm:inset-x-2 max-sm:top-14 max-sm:mt-0 max-sm:w-auto"
          style={{
            backgroundColor: "var(--color-surface)",
            borderColor: "var(--color-border)",
          }}
        >
          <div
            className="border-b px-3 py-2 text-[11px]"
            style={{
              borderColor: "var(--color-border)",
              color: "var(--color-text-muted)",
            }}
          >
            <div className="mb-1 flex items-center justify-between">
              <span>OCR</span>
              <StatusText
                value={data?.ocr_status ?? "untouched"}
                flagged={!!data?.needs_ocr}
              />
            </div>
            {data && ACTIVE.includes(data.ocr_status) && (
              <ProgressLine
                done={data.progress_done}
                total={data.progress_total}
                label="pages"
              />
            )}
            {data?.ocr_engine && (
              <div
                className="mb-2 truncate font-mono text-[10px]"
                title={data.ocr_engine}
              >
                {data.ocr_engine}
              </div>
            )}
            <div className="flex items-center justify-between">
              <span>Outline</span>
              <StatusText
                value={data?.outline_status ?? "untouched"}
                flagged={!!data?.needs_outline}
              />
            </div>
            {data && ACTIVE.includes(data.outline_status) && (
              <ProgressLine
                done={data.progress_done}
                total={data.progress_total}
                label="blocks"
              />
            )}
            {data?.outline_engine && (
              <div
                className="truncate font-mono text-[10px]"
                title={data.outline_engine}
              >
                {data.outline_engine}
              </div>
            )}
          </div>
          {ACTIVE.includes(data?.ocr_status ?? "untouched") ? (
            <button
              type="button"
              onClick={() => cancel.mutate("ocr")}
              disabled={cancel.isPending}
              className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs hover:opacity-80 disabled:opacity-50"
              style={{ color: "var(--color-text)" }}
              title="Mark this OCR job cancelled. Tesseract can't be interrupted mid-run, but its result will be discarded."
            >
              {cancel.isPending ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <XOctagon className="h-3.5 w-3.5" />
              )}
              <span className="flex-1">Cancel OCR</span>
            </button>
          ) : (
            <>
              <button
                type="button"
                onClick={() => ocr.mutate()}
                disabled={ocr.isPending}
                className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs hover:opacity-80 disabled:opacity-50"
                style={{ color: "var(--color-text)" }}
                title="Re-OCR with Tesseract on the CPU worker. Fast (~1 page/sec on 2 vCPU). Best for born-digital PDFs whose text layer is broken; weaker on complex layouts."
              >
                {ocr.isPending ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <ScanText className="h-3.5 w-3.5" />
                )}
                <span className="flex-1">Re-OCR (Tesseract, CPU)</span>
              </button>
              <button
                type="button"
                onClick={() => ocrGpu.mutate()}
                disabled={ocrGpu.isPending}
                className="flex w-full items-center gap-2 border-t px-3 py-2 text-left text-xs hover:opacity-80 disabled:opacity-50"
                style={{
                  borderColor: "var(--color-border)",
                  color: "var(--color-text)",
                }}
                title="Re-OCR with olmOCR-2 (Qwen2.5-VL-7B) on the GPU worker. Slower (~5–15 sec/page) but much stronger on complex layouts, math, multi-column papers. Job queues until the GPU pod is online."
              >
                {ocrGpu.isPending ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Cpu className="h-3.5 w-3.5" />
                )}
                <span className="flex-1">Re-OCR (olmOCR, GPU)</span>
              </button>
            </>
          )}
          {ACTIVE.includes(data?.outline_status ?? "untouched") ? (
            <button
              type="button"
              onClick={() => cancel.mutate("outline")}
              disabled={cancel.isPending}
              className="flex w-full items-center gap-2 border-t px-3 py-2 text-left text-xs hover:opacity-80 disabled:opacity-50"
              style={{
                borderColor: "var(--color-border)",
                color: "var(--color-text)",
              }}
              title="Mark this outline job cancelled. Marker can't be interrupted mid-run, but its result will be discarded."
            >
              {cancel.isPending ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <XOctagon className="h-3.5 w-3.5" />
              )}
              <span className="flex-1">Cancel outline</span>
            </button>
          ) : (
            <button
              type="button"
              onClick={() => outline.mutate()}
              disabled={outline.isPending}
              className="flex w-full items-center gap-2 border-t px-3 py-2 text-left text-xs hover:opacity-80 disabled:opacity-50"
              style={{
                borderColor: "var(--color-border)",
                color: "var(--color-text)",
              }}
            >
              {outline.isPending ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Sparkles className="h-3.5 w-3.5" />
              )}
              <span className="flex-1">Regenerate outline</span>
            </button>
          )}
          <button
            type="button"
            onClick={() => {
              if (
                window.confirm(
                  "Restore the originally uploaded PDF? Any OCR or other " +
                    "rewrites this worker has done will be discarded. The " +
                    "row's processing status will reset to untouched.",
                )
              ) {
                restore.mutate();
              }
            }}
            disabled={restore.isPending || !data?.original_preserved_at}
            title={
              data?.original_preserved_at
                ? "Replace the live PDF with the original upload"
                : "No original snapshot exists for this attachment yet"
            }
            className="flex w-full items-center gap-2 border-t px-3 py-2 text-left text-xs hover:opacity-80 disabled:opacity-50"
            style={{
              borderColor: "var(--color-border)",
              color: "var(--color-text)",
            }}
          >
            {restore.isPending ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <RotateCcw className="h-3.5 w-3.5" />
            )}
            <span className="flex-1">Restore original</span>
          </button>
          <div
            className="px-3 py-2 text-[10px]"
            style={{
              color: "var(--color-text-muted)",
              borderTop: "1px solid var(--color-border)",
            }}
          >
            <p>
              Outline regeneration runs on the GPU worker pod when
              one is online; the job stays queued until then.
            </p>
            {server.data && (
              <p className="mt-1 font-mono">
                api/worker: {server.data.image_tag}
              </p>
            )}
          </div>
        </div>
      )}
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

function ProgressLine({
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
      <div className="mb-2 font-mono text-[10px]" style={{ opacity: 0.6 }}>
        starting…
      </div>
    );
  }
  const pct = total != null && total > 0 ? Math.round((done / total) * 100) : null;
  return (
    <div className="mb-2 flex flex-col gap-0.5">
      <div className="font-mono text-[10px]">
        {total != null
          ? `${done} / ${total} ${label}${pct != null ? ` · ${pct}%` : ""}`
          : `${done} ${label}`}
      </div>
      {total != null && total > 0 && (
        <div
          style={{
            height: "3px",
            backgroundColor: "color-mix(in srgb, var(--color-text-muted) 20%, transparent)",
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

function StatusText({
  value,
  flagged,
}: {
  value: string;
  flagged: boolean;
}) {
  return (
    <span
      className="font-mono text-[10px] uppercase tracking-wide"
      style={{ color: STATUS_TONES[value] ?? "var(--color-text-muted)" }}
    >
      {value}
      {flagged && value === "untouched" && (
        <span style={{ color: "#f59e0b" }} title="Heuristic flagged">
          {" "}
          •
        </span>
      )}
    </span>
  );
}
