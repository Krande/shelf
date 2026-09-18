/**
 * The reader's top bar.
 *
 * Grouped the way it reads: where you are and how to get elsewhere on
 * the left, how the document is laid out in the middle, what you leave
 * on it at the right. The props are grouped the same way, so a control
 * and the state behind it are never far apart.
 */

import {
  ArrowLeft,
  Bookmark,
  ChevronLeft,
  ChevronRight,
  Eye,
  EyeOff,
  Highlighter,
  ListTree,
  Search,
  Type,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import ProcessingMenu from "@/components/reader/ProcessingMenu";
import VersionPicker from "@/components/reader/VersionPicker";
import {
  SCROLL_MODE_LABELS,
  type ScrollMode,
  type SpreadPref,
} from "@/auth/prefs";
import { SPREAD_LABELS } from "@/lib/spreads";
import { MAX_ZOOM, MIN_ZOOM } from "@/hooks/useZoomGestures";

/** Multiplier per zoom button press; matches pdf.js's own viewer. */
const ZOOM_STEP = 1.1;

/** The zoom menu's fixed sizes, as pdf.js offers them. */
const ZOOM_PRESETS = [0.5, 0.75, 1, 1.25, 1.5, 2, 3, 4];

export interface ReaderToolbarProps {
  /** Where you are, and getting elsewhere. */
  navigation: {
    onBack: () => void;
    page: number;
    numPages: number;
    goPrev: () => void;
    goNext: () => void;
    setPage: (n: number) => void;
    outlineOpen: boolean;
    setOutlineOpen: (v: boolean) => void;
    findOpen: boolean;
    setFindOpen: (v: boolean) => void;
  };
  /** How the document is laid out. */
  view: {
    scrollMode: ScrollMode;
    setMode: (m: ScrollMode) => void;
    spread: SpreadPref;
    setSpread: (s: SpreadPref) => void;
    zoom: number;
    applyZoom: (z: number) => void;
    zoomChoice: string;
    applyZoomChoice: (c: string) => void;
    isNamedZoom: boolean;
    absoluteZoom: number;
  };
  /** What you leave on it. */
  marks: {
    tool: "highlight" | "text" | null;
    setTool: (t: "highlight" | "text" | null) => void;
    highlightsOpen: boolean;
    setHighlightsOpen: (v: boolean) => void;
    annotationCount: number;
    attachmentId: string | undefined;
    debugText: boolean;
    setDebugText: (v: boolean) => void;
    selectMode: boolean;
    setSelectMode: (v: boolean) => void;
    isCoarsePointer: boolean;
  };
}

export function ReaderToolbar({
  navigation,
  view,
  marks,
}: ReaderToolbarProps) {
  const {
    onBack,
    page,
    numPages,
    goPrev,
    goNext,
    setPage,
    outlineOpen,
    setOutlineOpen,
    findOpen,
    setFindOpen,
  } = navigation;
  const {
    scrollMode,
    setMode,
    spread,
    setSpread,
    zoom,
    applyZoom,
    zoomChoice,
    applyZoomChoice,
    isNamedZoom,
    absoluteZoom,
  } = view;
  const {
    tool,
    setTool,
    highlightsOpen,
    setHighlightsOpen,
    annotationCount,
    attachmentId,
    debugText,
    setDebugText,
    selectMode,
    setSelectMode,
    isCoarsePointer,
  } = marks;

  return (
    <div
      className="flex flex-wrap items-center gap-2 border-b px-3 py-2"
      style={{ borderColor: "var(--color-border)" }}
    >
      {/* Where you are, and getting elsewhere. */}
      <span className="flex flex-wrap items-center gap-2">
            <button
              onClick={onBack}
              aria-label="Back"
              className="flex items-center gap-1 rounded px-2 py-1 text-xs hover:opacity-80"
              style={{ color: "var(--color-text-muted)" }}
            >
              <ArrowLeft className="h-3.5 w-3.5" />
              Back
            </button>

            <button
              onClick={() => {
                const next = !outlineOpen;
                setOutlineOpen(next);
                // One drawer at a time; two would leave no document.
                if (next) setHighlightsOpen(false);
              }}
              aria-label="Outline"
              title="Show document outline"
              className="flex items-center gap-1 rounded px-2 py-1 text-xs hover:opacity-80"
              style={{
                color: outlineOpen
                  ? "var(--color-accent)"
                  : "var(--color-text-muted)",
              }}
            >
              <ListTree className="h-3.5 w-3.5" />
            </button>

            <div className="ml-auto flex items-center gap-1">
              <button
                onClick={goPrev}
                disabled={page <= 1}
                aria-label="Previous page"
                className="rounded p-1 hover:opacity-70 disabled:opacity-30"
                style={{ color: "var(--color-text-muted)" }}
              >
                <ChevronLeft className="h-4 w-4" />
              </button>
              <input
                type="number"
                min={1}
                max={numPages || 1}
                value={page}
                onChange={(e) => {
                  const v = Number(e.target.value);
                  if (Number.isNaN(v) || !numPages) return;
                  setPage(Math.max(1, Math.min(numPages, v)));
                }}
                className="w-14 rounded border px-2 py-0.5 text-center text-xs"
                style={{
                  backgroundColor: "var(--color-surface)",
                  borderColor: "var(--color-border)",
                  color: "var(--color-text)",
                }}
              />
              <span className="text-xs" style={{ color: "var(--color-text-muted)" }}>
                / {numPages || "—"}
              </span>
              <button
                onClick={goNext}
                disabled={!numPages || page >= numPages}
                aria-label="Next page"
                className="rounded p-1 hover:opacity-70 disabled:opacity-30"
                style={{ color: "var(--color-text-muted)" }}
              >
                <ChevronRight className="h-4 w-4" />
              </button>
            </div>

            <button
              onClick={() => setFindOpen(!findOpen)}
              aria-label="Find in document"
              title="Find in document"
              className="flex items-center gap-1 rounded px-2 py-1 text-xs hover:opacity-80"
              style={{
                color: findOpen
                  ? "var(--color-accent)"
                  : "var(--color-text-muted)",
              }}
            >
              <Search className="h-3.5 w-3.5" />
            </button>
      </span>

      {/* How the document is laid out. */}
      <span className="flex flex-wrap items-center gap-2 mx-auto">
            <select
              value={scrollMode}
              onChange={(e) => setMode(e.target.value as ScrollMode)}
              aria-label="Scroll mode"
              title="How pages are laid out and scrolled"
              className="rounded border-0 bg-transparent px-1 py-1 text-xs"
              style={{ color: "var(--color-text-muted)" }}
            >
              {(
                ["page", "vertical", "horizontal", "wrapped"] as ScrollMode[]
              ).map((m) => (
                <option key={m} value={m}>
                  {SCROLL_MODE_LABELS[m]}
                </option>
              ))}
            </select>

            <select
              value={spread}
              onChange={(e) => setSpread(e.target.value as typeof spread)}
              aria-label="Spread mode"
              title="Show pages singly or as facing pairs"
              className="rounded border-0 bg-transparent px-1 py-1 text-xs"
              style={{ color: "var(--color-text-muted)" }}
            >
              {(["none", "odd", "even"] as const).map((m) => (
                <option key={m} value={m}>
                  {SPREAD_LABELS[m]}
                </option>
              ))}
            </select>

            <span className="flex items-center gap-0.5">
              <button
                onClick={() => applyZoom(zoom / ZOOM_STEP)}
                disabled={zoom <= MIN_ZOOM + 1e-6}
                aria-label="Zoom out"
                title="Zoom out"
                className="rounded p-1 hover:opacity-80 disabled:opacity-30"
                style={{ color: "var(--color-text-muted)" }}
              >
                <ZoomOut className="h-3.5 w-3.5" />
              </button>
              <select
                // Value is only ever one of the named modes; a percentage
                // reached by wheel or button shows as an extra entry rather
                // than snapping the reader to the nearest preset.
                value={zoomChoice}
                onChange={(e) => applyZoomChoice(e.target.value)}
                aria-label="Zoom"
                className="rounded border-0 bg-transparent px-1 py-1 text-xs"
                style={{ color: "var(--color-text-muted)" }}
              >
                <option value="auto">Automatic zoom</option>
                <option value="page-actual">Actual size</option>
                <option value="page-fit">Page fit</option>
                <option value="page-width">Page width</option>
                {!isNamedZoom && (
                  <option value={String(absoluteZoom)}>
                    {Math.round(absoluteZoom * 100)}%
                  </option>
                )}
                {ZOOM_PRESETS.map((z) => (
                  <option key={z} value={String(z)}>
                    {Math.round(z * 100)}%
                  </option>
                ))}
              </select>
              <button
                onClick={() => applyZoom(zoom * ZOOM_STEP)}
                disabled={zoom >= MAX_ZOOM - 1e-6}
                aria-label="Zoom in"
                title="Zoom in"
                className="rounded p-1 hover:opacity-80 disabled:opacity-30"
                style={{ color: "var(--color-text-muted)" }}
              >
                <ZoomIn className="h-3.5 w-3.5" />
              </button>
            </span>
      </span>

      {/* What you leave on it. */}
      <span className="flex flex-wrap items-center gap-2">
            {isCoarsePointer && (
              <button
                onClick={() => setSelectMode(!selectMode)}
                aria-label="Select text"
                title={
                  selectMode
                    ? "Exit text-select mode"
                    : "Enable text-select mode (drag to select)"
                }
                className="flex items-center gap-1 rounded px-2 py-1 text-xs hover:opacity-80"
                style={{
                  color: selectMode
                    ? "var(--color-accent)"
                    : "var(--color-text-muted)",
                }}
              >
                <Highlighter className="h-3.5 w-3.5" />
                {selectMode ? "Selecting" : "Select"}
              </button>
            )}

            <button
              onClick={() => setTool(tool === "highlight" ? null : "highlight")}
              aria-label="Highlight"
              aria-pressed={tool === "highlight"}
              title="Highlight — selecting text marks it, without the extra click"
              className="flex items-center gap-1 rounded px-2 py-1 text-xs hover:opacity-80"
              style={{
                color:
                  tool === "highlight"
                    ? "var(--color-accent)"
                    : "var(--color-text-muted)",
              }}
            >
              <Highlighter className="h-3.5 w-3.5" />
              Highlight
            </button>

            <button
              onClick={() => setTool(tool === "text" ? null : "text")}
              aria-label="Text"
              aria-pressed={tool === "text"}
              title="Text — click a page to leave a note on it"
              className="flex items-center gap-1 rounded px-2 py-1 text-xs hover:opacity-80"
              style={{
                color:
                  tool === "text"
                    ? "var(--color-accent)"
                    : "var(--color-text-muted)",
              }}
            >
              <Type className="h-3.5 w-3.5" />
              Text
            </button>

            <button
              onClick={() => {
                const next = !highlightsOpen;
                setHighlightsOpen(next);
                if (next) setOutlineOpen(false);
              }}
              aria-label="Highlights"
              title="Show highlights for this PDF"
              className="flex items-center gap-1 rounded px-2 py-1 text-xs hover:opacity-80"
              style={{
                color: highlightsOpen
                  ? "var(--color-accent)"
                  : "var(--color-text-muted)",
              }}
            >
              <Bookmark className="h-3.5 w-3.5" />
              {annotationCount > 0 && (
                <span className="tabular-nums">{annotationCount}</span>
              )}
            </button>

            {attachmentId && (
              <ProcessingMenu attachmentId={attachmentId} />
            )}

            {attachmentId && (
              <VersionPicker attachmentId={attachmentId} />
            )}

            <button
              onClick={() => setDebugText(!debugText)}
              aria-label="Toggle text-layer debug"
              title={
                debugText
                  ? "Hide text-layer overlay"
                  : "Show text-layer overlay (debug)"
              }
              className="flex items-center gap-1 rounded px-2 py-1 text-xs hover:opacity-80"
              style={{
                color: debugText
                  ? "var(--color-accent)"
                  : "var(--color-text-muted)",
              }}
            >
              {debugText ? (
                <Eye className="h-3.5 w-3.5" />
              ) : (
                <EyeOff className="h-3.5 w-3.5" />
              )}
            </button>
      </span>
    </div>
  );
}
