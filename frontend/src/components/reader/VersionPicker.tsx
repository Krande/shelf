import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router";
import { ChevronDown, GitBranch, Sparkles, ScanText, FileText } from "lucide-react";
import {
  type AttachmentDerivation,
  listDerivations,
} from "@/api/attachments";

const KIND_ICON: Record<string, typeof ScanText> = {
  ocr: ScanText,
  outline: Sparkles,
};

/**
 * Reader toolbar control for switching between derived versions of
 * a PDF (an OCR run, an outline run, …) plus the untouched original.
 *
 * The current selection lives in the URL as `?version=<id>|original`
 * so it survives reloads and can be linked. Omitting the param means
 * "current best" — the server picks latest-outline > latest-ocr >
 * original. We show that selection as "Latest" so it's distinguishable
 * from a deliberate pick of the same row.
 *
 * Polls every 5s while the menu is open so a fresh derivation
 * landing on the server (e.g. an OCR run finishing) shows up without
 * the user reopening the dropdown. Closed: cached behaviour from
 * the surrounding query client.
 */
export default function VersionPicker({
  attachmentId,
}: {
  attachmentId: string;
}) {
  const [searchParams, setSearchParams] = useSearchParams();
  const selected = searchParams.get("version");
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  const list = useQuery({
    queryKey: ["derivations", attachmentId],
    queryFn: () => listDerivations(attachmentId),
    refetchInterval: open ? 5_000 : false,
  });

  // Click-outside to close — same shape as ProcessingMenu.
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const setVersion = (v: string | null) => {
    const next = new URLSearchParams(searchParams);
    if (v === null) next.delete("version");
    else next.set("version", v);
    // Drop ?page= when switching versions — the page numbering can
    // diverge between OCR runs (re-pagination is rare but possible),
    // and "show me the same page in the other version" isn't always
    // meaningful. Reader will fall back to page 1.
    next.delete("page");
    setSearchParams(next, { replace: false });
    setOpen(false);
  };

  const data = list.data;
  const total = data?.derivations.length ?? 0;
  const ocrCount = data?.derivations.filter((d) => d.kind === "ocr").length ?? 0;
  const outlineCount =
    data?.derivations.filter((d) => d.kind === "outline").length ?? 0;

  // What to show in the trigger button. If no version is pinned,
  // reflect the server's current_version so the user knows what
  // they're looking at.
  let triggerLabel = "Latest";
  if (selected === "original") {
    triggerLabel = "Original";
  } else if (selected) {
    const row = data?.derivations.find((d) => d.id === selected);
    if (row) {
      triggerLabel = `${row.kind} · ${formatTime(row.created_at)}`;
    } else {
      triggerLabel = "Pinned";
    }
  } else if (data?.current_version && data.current_version !== "original") {
    const row = data.derivations.find((d) => d.id === data.current_version);
    if (row) triggerLabel = `Latest (${row.kind})`;
  }

  // Hide entirely when there are no derivations and no pinned override —
  // the dropdown would be a single "Original" entry with nothing to
  // compare against. Once a derivation lands the picker appears.
  if (total === 0 && !selected) return null;

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="menu"
        aria-expanded={open}
        title={`Version: ${triggerLabel}`}
        className="flex items-center gap-1 rounded px-2 py-1 text-xs hover:opacity-80"
        style={{ color: "var(--color-text-muted)" }}
      >
        <GitBranch className="h-3.5 w-3.5" />
        <span className="max-w-[10rem] truncate">{triggerLabel}</span>
        <ChevronDown className="h-3 w-3" />
      </button>

      {open && (
        <div
          role="menu"
          className="absolute right-0 z-20 mt-1 w-72 rounded border shadow-md"
          style={{
            background: "var(--color-bg-card, #fff)",
            borderColor: "var(--color-border, #e5e7eb)",
          }}
        >
          <button
            type="button"
            onClick={() => setVersion(null)}
            role="menuitem"
            className={`flex w-full items-start gap-2 px-3 py-2 text-left text-xs hover:opacity-80 ${
              selected === null ? "font-semibold" : ""
            }`}
          >
            <Sparkles className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <span className="flex-1">
              <div>Latest (server pick)</div>
              <div
                className="text-[10px]"
                style={{ color: "var(--color-text-muted)" }}
              >
                Outline if any, else OCR, else original
              </div>
            </span>
          </button>

          <div
            className="border-t"
            style={{ borderColor: "var(--color-border, #e5e7eb)" }}
          />

          <button
            type="button"
            onClick={() => setVersion("original")}
            role="menuitem"
            className={`flex w-full items-start gap-2 px-3 py-2 text-left text-xs hover:opacity-80 ${
              selected === "original" ? "font-semibold" : ""
            }`}
          >
            <FileText className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <span className="flex-1">
              <div>Original</div>
              <div
                className="text-[10px]"
                style={{ color: "var(--color-text-muted)" }}
              >
                Untouched upload
              </div>
            </span>
          </button>

          {(ocrCount > 0 || outlineCount > 0) && (
            <>
              <div
                className="border-t"
                style={{ borderColor: "var(--color-border, #e5e7eb)" }}
              />
              <DerivationGroup
                title="Outlines"
                rows={data?.derivations.filter((d) => d.kind === "outline") ?? []}
                selected={selected}
                onPick={setVersion}
              />
              <DerivationGroup
                title="OCR runs"
                rows={data?.derivations.filter((d) => d.kind === "ocr") ?? []}
                selected={selected}
                onPick={setVersion}
              />
            </>
          )}
        </div>
      )}
    </div>
  );
}

function DerivationGroup({
  title,
  rows,
  selected,
  onPick,
}: {
  title: string;
  rows: AttachmentDerivation[];
  selected: string | null;
  onPick: (id: string) => void;
}) {
  if (rows.length === 0) return null;
  return (
    <div className="py-1">
      <div
        className="px-3 pb-1 pt-1 text-[10px] uppercase tracking-wide"
        style={{ color: "var(--color-text-muted)" }}
      >
        {title}
      </div>
      {rows.map((row) => {
        const Icon = KIND_ICON[row.kind] ?? GitBranch;
        const active = selected === row.id;
        return (
          <button
            key={row.id}
            type="button"
            onClick={() => onPick(row.id)}
            role="menuitem"
            className={`flex w-full items-start gap-2 px-3 py-2 text-left text-xs hover:opacity-80 ${
              active ? "font-semibold" : ""
            }`}
          >
            <Icon className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <span className="flex-1 min-w-0">
              <div className="truncate">{formatTime(row.created_at)}</div>
              <div
                className="truncate text-[10px]"
                style={{ color: "var(--color-text-muted)" }}
                title={row.engine}
              >
                {row.engine}
              </div>
            </span>
          </button>
        );
      })}
    </div>
  );
}

function formatTime(iso: string): string {
  // Just enough to distinguish runs in a list. Locale-formatted time
  // when same-day, full date+time otherwise.
  const d = new Date(iso);
  const now = new Date();
  const sameDay =
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate();
  if (sameDay) {
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }
  return d.toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
