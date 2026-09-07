import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from "react";
import { useNavigate, useParams, useSearchParams } from "react-router";
import {
  ArrowLeft,
  Bookmark,
  ChevronLeft,
  ChevronRight,
  Eye,
  EyeOff,
  FileText,
  Highlighter,
  List,
  ListTree,
  Loader2,
  Maximize,
  MoveHorizontal,
  Search,
  Trash2,
  X,
} from "lucide-react";
import type {
  PDFDocumentProxy,
  PDFPageProxy,
  RenderTask,
} from "pdfjs-dist";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useVirtualizer } from "@tanstack/react-virtual";
import { pdfjs } from "@/api/pdfWorkerSetup";
import { useAuth } from "@/auth/session";
import { getDownloadUrl, getPageDims } from "@/api/attachments";
import {
  type Annotation,
  type Rect,
  createAnnotation,
  deleteAnnotation,
  listAnnotations,
  updateAnnotation,
} from "@/api/annotations";
import { PREF_READER_FIT, PREF_READER_MODE, usePref } from "@/auth/prefs";
import { useDebounce } from "@/hooks/useDebounce";
import { usePinchZoom } from "@/hooks/usePinchZoom";
import OutlinePanel from "@/components/library/OutlinePanel";
import ProcessingMenu from "@/components/reader/ProcessingMenu";
import VersionPicker from "@/components/reader/VersionPicker";

const PAGE_GAP = 16;
// p-4 padding on the scroll container = 16px each side, 32px total
// horizontal — pages render to fit the inner content width.
const SCROLL_PADDING = 32;
const ESTIMATE_PAGE_HEIGHT = 1100;
// Cap how much extra pixel detail we ask pdfjs to bake at zoom.
// Each step squares memory usage per page (e.g. 3× → 9× pixels);
// 4 keeps a 1 MP base page under ~16 MP which is workable for a
// few mounted virtual rows. The user sees diminishing returns past
// the device's effective DPI anyway.
const MAX_OVERSAMPLE = 4;
// Wait this long after the last pinch-scale change before kicking
// off the higher-resolution re-render. Avoids re-rendering during
// the gesture itself — pdfjs render is CPU-heavy.
const OVERSAMPLE_SETTLE_MS = 1500;

interface NativeViewport {
  width: number;
  height: number;
}


/**
 * PDF reader. Layout pattern lifted directly from the webui's
 * PdfViewer:
 *
 *   - The scroll container has overflow-Y only — pages never
 *     overflow horizontally, because they're always rendered at
 *     a per-page scale that fits the container width (or, in
 *     fit-page mode, the container's smaller dimension).
 *   - Pinch-zoom is the only way to scale up: a CSS `transform:
 *     translate() scale()` on the content wrapper. Pinch handles
 *     both the zoom AND the pan via translate, so we don't need
 *     native horizontal scroll. Single-finger drag scrolls
 *     vertically when at scale=1; usePinchZoom takes over both
 *     axes when zoomed.
 *   - Heights come from a pre-computed pageNativeRef × the
 *     fit-derived scale, so the virtualizer's estimateSize is
 *     deterministic (no measureElement, no ResizeObserver feedback).
 *   - Rows are position:absolute width:100% display:flex
 *     justify-center — same shape as the webui.
 */
export default function ReaderPage() {
  const auth = useAuth();
  const params = useParams<{ attachmentId: string }>();
  const [searchParams] = useSearchParams();
  const nav = useNavigate();

  const [doc, setDoc] = useState<PDFDocumentProxy | null>(null);
  // numPages is intentionally separate from doc.numPages: it stays
  // 0 until the height pre-compute finishes, so the virtualizer
  // doesn't start with bad estimates and cache them. Matches the
  // webui's gating exactly.
  const [numPages, setNumPages] = useState<number>(0);
  const [page, setPage] = useState<number>(
    Number(searchParams.get("page") || 1),
  );
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [mode, setMode] = usePref(PREF_READER_MODE);
  const [fit, setFit] = usePref(PREF_READER_FIT);
  // Diagnostic — colour the text-layer spans so we can see where
  // pdfjs places them vs the rendered glyphs.
  const [debugText, setDebugText] = useState(false);

  // Mobile-only "select text" mode. Long-press text-selection on
  // touch devices fights the scroll container — any finger movement
  // hands the touch to scroll before the long-press timer fires. In
  // select mode we disable pinch + scroll so a finger drag goes
  // straight to text selection. Toggle is hidden on coarse-pointer
  // = false (i.e. desktop with mouse) since drag-select already
  // works there with the default cursor.
  const [selectMode, setSelectMode] = useState(false);
  // Side drawer listing all highlights for this PDF.
  const [highlightsOpen, setHighlightsOpen] = useState(false);
  // Side drawer with the PDF's embedded outline (table of contents).
  const [outlineOpen, setOutlineOpen] = useState(false);
  const isCoarsePointer = useMemo(
    () =>
      typeof window !== "undefined" &&
      typeof window.matchMedia === "function" &&
      window.matchMedia("(pointer: coarse)").matches,
    [],
  );

  const scrollRef = useRef<HTMLDivElement>(null);
  const pageNativeRef = useRef<Map<number, NativeViewport>>(new Map());
  const [heightsReady, setHeightsReady] = useState(false);

  // Track container size so estimateSize + per-page scale react to
  // viewport resize / device rotation without a manual reload.
  const [containerSize, setContainerSize] = useState<{
    width: number;
    height: number;
  }>({ width: 0, height: 0 });
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      const r = entries[0]?.contentRect;
      if (r) setContainerSize({ width: r.width, height: r.height });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // ── Find ─────────────────────────────────────────────────────────
  // Seed from `?find=` once at mount so deep links from the library's
  // fulltext-hit list (Page N · …match…) open the find toolbar with
  // the query already populated. Reads via initializer rather than an
  // effect so the user's later edits aren't clobbered on re-render.
  const initialFind = searchParams.get("find") ?? "";
  const [findOpen, setFindOpen] = useState<boolean>(initialFind.length > 0);
  const [findInput, setFindInput] = useState<string>(initialFind);
  const findQuery = useDebounce(findInput, 250);
  const [matches, setMatches] = useState<
    Array<{ page: number; occurrence: number }>
  >([]);
  const [currentMatch, setCurrentMatch] = useState<number>(0);
  const [findScanning, setFindScanning] = useState(false);

  // The version of the PDF currently rendered: ?version=<derivation
  // uuid> | "original", or null for "server's current best." Lifted
  // out of the useEffect deps so a version switch re-fetches without
  // re-running unrelated effects.
  const version = searchParams.get("version");

  // Load + parse the document. numPages is set *after* the height
  // pre-compute so the virtualizer never sees a count > 0 with
  // missing pageNativeRef entries; that's what was capping the
  // continuous scroll at the first overscan window.
  useEffect(() => {
    if (auth.status !== "authenticated" || !params.attachmentId) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    pageNativeRef.current.clear();
    setHeightsReady(false);
    setNumPages(0);

    (async () => {
      try {
        // Server-side dims and the PDF body are independent — fetch
        // both in parallel so the slow one (the PDF) covers the
        // fast one rather than serialising. The dims call is cheap
        // even on a large library and short-circuits the per-page
        // getPage() walk that used to dominate cold-load time.
        const url = await getDownloadUrl(
          params.attachmentId!,
          version ?? undefined,
        );
        const dimsPromise = getPageDims(params.attachmentId!).catch(() => ({
          pages: [],
        }));
        const task = pdfjs.getDocument({ url });
        const loaded = await task.promise;
        if (cancelled) {
          await loaded.destroy();
          return;
        }
        const dims = await dimsPromise;
        const haveServerDims =
          dims.pages.length > 0 &&
          dims.pages.some((p) => p.width != null && p.height != null);
        if (haveServerDims) {
          for (const p of dims.pages) {
            if (p.width != null && p.height != null) {
              pageNativeRef.current.set(p.page, {
                width: p.width,
                height: p.height,
              });
            }
          }
          // Server dims may not cover every page (e.g. one had a
          // bad mediabox at extract time). Fill any holes with
          // page 1's dimensions as a baseline.
          const firstFilled = dims.pages.find(
            (p) => p.width != null && p.height != null,
          );
          if (firstFilled?.width && firstFilled.height) {
            for (let i = 1; i <= loaded.numPages; i++) {
              if (!pageNativeRef.current.has(i)) {
                pageNativeRef.current.set(i, {
                  width: firstFilled.width,
                  height: firstFilled.height,
                });
              }
            }
          }
        } else {
          // Server hasn't extracted dims yet (pre-feature row, or
          // extract worker hasn't run). Open just page 1 to get a
          // baseline and apply it to every page; the per-page
          // render path corrects each page's height as it actually
          // renders.
          const p = await loaded.getPage(1);
          const vp = p.getViewport({ scale: 1 });
          for (let i = 1; i <= loaded.numPages; i++) {
            pageNativeRef.current.set(i, {
              width: vp.width,
              height: vp.height,
            });
          }
          p.cleanup();
        }
        if (cancelled) return;
        setDoc(loaded);
        setHeightsReady(true);
        setNumPages(loaded.numPages);
      } catch (e) {
        if (!cancelled) setError((e as Error).message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [auth.status, params.attachmentId, version]);

  // Whole-document text scan when the find query changes.
  useEffect(() => {
    if (!doc) return;
    const needle = findQuery.trim().toLowerCase();
    if (!needle) {
      setMatches([]);
      setCurrentMatch(0);
      return;
    }
    let cancelled = false;
    setFindScanning(true);

    (async () => {
      const found: Array<{ page: number; occurrence: number }> = [];
      for (let n = 1; n <= doc.numPages; n++) {
        if (cancelled) return;
        const p = await doc.getPage(n);
        const tc = await p.getTextContent();
        const text = tc.items
          .map((it) => ("str" in it ? (it as { str: string }).str : ""))
          .join(" ")
          .toLowerCase();
        let from = 0;
        let occ = 0;
        while (true) {
          const idx = text.indexOf(needle, from);
          if (idx === -1) break;
          found.push({ page: n, occurrence: occ });
          occ += 1;
          from = idx + needle.length;
        }
        p.cleanup();
      }
      if (!cancelled) {
        setMatches(found);
        setCurrentMatch(0);
        setFindScanning(false);
      }
    })().catch(() => {
      if (!cancelled) setFindScanning(false);
    });

    return () => {
      cancelled = true;
    };
  }, [doc, findQuery]);

  // Sync ?page= so reload + back/forward restore the position.
  useEffect(() => {
    const url = new URL(window.location.href);
    if (page > 1) url.searchParams.set("page", String(page));
    else url.searchParams.delete("page");
    window.history.replaceState(null, "", url.toString());
  }, [page]);

  // Scroll the continuous virtualizer to whatever page= the URL is
  // currently pointing at, both on initial mount and on subsequent
  // same-doc navigations (e.g. clicking another fulltext-hit page
  // while the reader is already mounted). Single-page mode doesn't
  // need this — PageCanvas only renders `page` directly.
  //
  // We only react to *react-router* searchParams changes, not to the
  // ?page= writeback effect above (which uses history.replaceState
  // and so doesn't notify react-router). That's how this stays out
  // of a loop with manual scroll-driven page updates.
  useEffect(() => {
    if (mode !== "continuous") return;
    if (!heightsReady || numPages === 0) return;
    const target = Number(searchParams.get("page") || 0);
    if (!(target > 1 && target <= numPages)) return;
    setPage(target);
    // Defer past the first paint so the keyed ContinuousList has
    // mounted, the virtualizer has measured the container, and the
    // imperative handle is exposed.
    const t = setTimeout(() => {
      continuousRef.current?.scrollToPage(target);
    }, 50);
    return () => clearTimeout(t);
  }, [mode, heightsReady, numPages, searchParams]);

  // Per-page base render scale. Default is fit-to-width (renders the
  // page to fill the container's inner width); "page" mode clamps
  // additionally to inner height so the whole page is visible at
  // once — needed for tall PDFs where the user otherwise can't see
  // the bottom without scrolling and pinch can only zoom further in,
  // not out. With containerSize.width = 0 (initial mount, before
  // ResizeObserver fires) we return 1 as a safe fallback; the
  // virtualizer is remounted on width/height/fit change below so the
  // wrong heights don't get cached.
  const pageScaleFor = useCallback(
    (n: number): number => {
      const native = pageNativeRef.current.get(n);
      if (!native || containerSize.width === 0) return 1;
      const innerW = Math.max(0, containerSize.width - SCROLL_PADDING);
      const widthScale = innerW / native.width;
      if (fit === "page" && containerSize.height > 0) {
        const innerH = Math.max(0, containerSize.height - SCROLL_PADDING);
        if (innerH > 0) {
          const heightScale = innerH / native.height;
          return Math.min(widthScale, heightScale);
        }
      }
      return widthScale;
    },
    [containerSize.width, containerSize.height, fit],
  );

  const estimateSize = useCallback(
    (index: number) => {
      const native = pageNativeRef.current.get(index + 1);
      if (!native) return ESTIMATE_PAGE_HEIGHT + PAGE_GAP;
      return native.height * pageScaleFor(index + 1) + PAGE_GAP;
    },
    [pageScaleFor],
  );

  // useVirtualizer is moved into ContinuousList (a child component
  // that gets key'd on the relevant inputs), so we can guarantee the
  // virtualizer's internal cache resets when those inputs change.
  // Keying the wrapper alone wasn't enough because the hook lived
  // here and survived the remount — its cached estimateSize results
  // from the first paint kept totalSize stuck small, capping how
  // far you could scroll.
  const virtualKey = `${numPages}-${containerSize.width}-${containerSize.height}-${heightsReady}-${fit}`;

  // Imperative bridge so the toolbar's prev/next/page-input can
  // command scrolling on the (key'd, possibly remounted) child.
  const continuousRef = useRef<ContinuousListHandle>(null);

  const goPrev = useCallback(() => {
    setPage((p) => {
      const n = Math.max(1, p - 1);
      if (mode === "continuous") {
        continuousRef.current?.scrollToPage(n);
      }
      return n;
    });
  }, [mode]);

  const goNext = useCallback(() => {
    setPage((p) => {
      if (!numPages) return p;
      const n = Math.min(numPages, p + 1);
      if (mode === "continuous") {
        continuousRef.current?.scrollToPage(n);
      }
      return n;
    });
  }, [numPages, mode]);

  function jumpToMatch(idx: number): void {
    if (matches.length === 0) return;
    const wrapped = ((idx % matches.length) + matches.length) % matches.length;
    setCurrentMatch(wrapped);
    const target = matches[wrapped];
    setPage(target.page);
    // Reset pinch before scrolling. While pinched, the visible
    // viewport is driven by translateY on the wrapper rather than
    // scrollTop on the container, so the virtualizer's scrollToIndex
    // moves a "scroll" position the user can't see — the target
    // page never enters the mount window and the user lands on
    // background. Resetting drops translate/scale to identity so
    // scrollTop and visible viewport agree again.
    pinch.resetZoom();
    if (mode === "continuous") {
      continuousRef.current?.scrollToPage(target.page);
    }
  }

  // Keyboard nav.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "f") {
        e.preventDefault();
        setFindOpen(true);
        return;
      }
      if (e.target instanceof HTMLInputElement) return;
      if (e.key === "ArrowLeft" || e.key === "PageUp") {
        e.preventDefault();
        goPrev();
      } else if (
        e.key === "ArrowRight" ||
        e.key === "PageDown" ||
        e.key === " "
      ) {
        e.preventDefault();
        goNext();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [goPrev, goNext]);

  // Block document-level pinch-zoom while the reader is mounted.
  useEffect(() => {
    const meta = document.querySelector('meta[name="viewport"]');
    const original = meta?.getAttribute("content") ?? null;
    meta?.setAttribute(
      "content",
      "width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no",
    );
    return () => {
      if (meta && original !== null) meta.setAttribute("content", original);
    };
  }, []);

  // Pinch on the scroll container — handles zoom + pan via CSS
  // translate. When state.scale === 1, touchAction falls back to
  // pan-y so native vertical scroll works. Disabled while in
  // select mode so finger drags select text instead of pinching.
  const pinch = usePinchZoom(scrollRef, { enabled: !selectMode });
  // Mirror pinch.scale into a ref so the find-highlight effect (in
  // PageCanvas) can read the current value without listing
  // pinch.scale as a dependency. We don't want the effect to re-run
  // every pinch frame — getClientRects + DOM updates per frame
  // would tank the gesture's frame rate. Reading via ref means the
  // effect picks up the latest value at re-run time (when query /
  // textLayer changes) and the divs get placed in *layout*
  // coordinates so the parent transform composes them correctly
  // through any subsequent pinch.
  const pinchScaleRef = useRef(pinch.scale);
  useEffect(() => {
    pinchScaleRef.current = pinch.scale;
  }, [pinch.scale]);

  // Annotations for this PDF.
  const qc = useQueryClient();
  const annotationsQuery = useQuery({
    queryKey: ["annotations", params.attachmentId],
    queryFn: () => listAnnotations(params.attachmentId!),
    enabled: auth.status === "authenticated" && !!params.attachmentId,
  });
  const annotationsByPage = useMemo(() => {
    const map = new Map<number, Annotation[]>();
    for (const a of annotationsQuery.data ?? []) {
      const arr = map.get(a.page_number);
      if (arr) arr.push(a);
      else map.set(a.page_number, [a]);
    }
    return map;
  }, [annotationsQuery.data]);
  // Flat list for the side panel: all annotations, sorted by page
  // ascending then by y descending (PDF y-origin is bottom-left, so
  // larger y = nearer the top of the page).
  const annotationsSorted = useMemo(() => {
    const list = [...(annotationsQuery.data ?? [])];
    list.sort((a, b) => {
      if (a.page_number !== b.page_number) {
        return a.page_number - b.page_number;
      }
      const ay = a.rects[0]?.[1] ?? 0;
      const by = b.rects[0]?.[1] ?? 0;
      return by - ay;
    });
    return list;
  }, [annotationsQuery.data]);

  const createHighlight = useMutation({
    mutationFn: (input: { pageNumber: number; rects: Rect[]; text: string }) =>
      createAnnotation(params.attachmentId!, {
        kind: "highlight",
        page_number: input.pageNumber,
        rects: input.rects,
        text: input.text,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["annotations", params.attachmentId] });
    },
  });

  const onCreateHighlight = useCallback(
    (pageNumber: number, rects: Rect[], text: string) => {
      // Drop the OS selection so the floating button doesn't linger
      // and so a re-tap on the same word doesn't show stale state.
      window.getSelection()?.removeAllRanges();
      createHighlight.mutate({ pageNumber, rects, text });
      // Exit mobile select-mode after a successful highlight so the
      // user can scroll/pinch again without an extra tap.
      setSelectMode(false);
    },
    [createHighlight],
  );

  const removeAnnotation = useMutation({
    mutationFn: (id: string) => deleteAnnotation(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["annotations", params.attachmentId] });
    },
  });

  const recolorAnnotation = useMutation({
    mutationFn: ({ id, color }: { id: string; color: string }) =>
      updateAnnotation(id, { color }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["annotations", params.attachmentId] });
    },
  });

  // Generic "scroll the reader to page N" — used by every panel
  // that wants to navigate (annotations, outline, fulltext).
  const goToPage = useCallback(
    (n: number) => {
      // Reset pinch first — when zoomed the visible viewport is
      // driven by translate, not scrollTop, so scrollToPage won't
      // bring the target into view.
      pinch.resetZoom();
      setPage(n);
      if (mode === "continuous") {
        // Defer past the pinch-reset / setPage render flush so the
        // virtualizer measures with the identity transform in place;
        // without this, scrollToIndex computed against a pinched
        // layout would silently land on the wrong scrollTop and the
        // viewer would stay on whatever page was already visible.
        setTimeout(() => {
          continuousRef.current?.scrollToPage(n);
        }, 0);
      }
    },
    [pinch, mode],
  );

  const jumpToAnnotation = useCallback(
    (a: Annotation) => {
      goToPage(a.page_number);
      // Auto-close the drawer on coarse-pointer devices so the user
      // can see the highlight without an extra tap.
      if (isCoarsePointer) setHighlightsOpen(false);
    },
    [goToPage, isCoarsePointer],
  );

  const jumpFromOutline = useCallback(
    (page: number) => {
      goToPage(page);
      if (isCoarsePointer) setOutlineOpen(false);
    },
    [goToPage, isCoarsePointer],
  );

  // Once the user holds a zoom level still for OVERSAMPLE_SETTLE_MS,
  // we ask pdfjs to re-render the visible pages at higher pixel
  // detail (canvas pixel buffer × oversample, CSS box unchanged) so
  // text and vector strokes turn crisp again. While the user is
  // actively pinching, pinch.scale changes per frame, the timer
  // keeps resetting, and pdfjs stays out of the way.
  const [oversample, setOversample] = useState(1);
  useEffect(() => {
    const target = Math.max(1, Math.min(MAX_OVERSAMPLE, pinch.scale));
    if (Math.abs(target - oversample) < 0.01) return;
    const t = setTimeout(() => {
      setOversample(target);
    }, OVERSAMPLE_SETTLE_MS);
    return () => clearTimeout(t);
  }, [pinch.scale, oversample]);

  const currentMatchInfo = matches[currentMatch] ?? null;

  return (
    <div
      className="flex h-dvh flex-col"
      style={{ backgroundColor: "var(--color-bg)" }}
    >
      <div
        className="flex flex-wrap items-center gap-2 border-b px-3 py-2"
        style={{ borderColor: "var(--color-border)" }}
      >
        <button
          onClick={() => nav(-1)}
          aria-label="Back"
          className="flex items-center gap-1 rounded px-2 py-1 text-xs hover:opacity-80"
          style={{ color: "var(--color-text-muted)" }}
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          Back
        </button>

        <button
          onClick={() => setFindOpen((v) => !v)}
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

        {isCoarsePointer && (
          <button
            onClick={() => setSelectMode((v) => !v)}
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
          onClick={() => {
            setOutlineOpen((v) => {
              const next = !v;
              if (next) setHighlightsOpen(false);
              return next;
            });
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

        <button
          onClick={() => {
            setHighlightsOpen((v) => {
              const next = !v;
              if (next) setOutlineOpen(false);
              return next;
            });
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
          {annotationsSorted.length > 0 && (
            <span className="tabular-nums">{annotationsSorted.length}</span>
          )}
        </button>

        <button
          onClick={() => setDebugText((v) => !v)}
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

        <button
          onClick={() =>
            setMode(mode === "single" ? "continuous" : "single")
          }
          aria-label="Toggle reader mode"
          title={
            mode === "single"
              ? "Switch to continuous (all pages)"
              : "Switch to single-page"
          }
          className="flex items-center gap-1 rounded px-2 py-1 text-xs hover:opacity-80"
          style={{ color: "var(--color-text-muted)" }}
        >
          {mode === "single" ? (
            <>
              <FileText className="h-3.5 w-3.5" />
              Single
            </>
          ) : (
            <>
              <List className="h-3.5 w-3.5" />
              Continuous
            </>
          )}
        </button>

        <button
          onClick={() => {
            // Reset any pinch zoom so the new base scale is what the
            // user actually sees — otherwise a pinched-in view would
            // hide the fit change.
            pinch.resetZoom();
            setFit(fit === "width" ? "page" : "width");
          }}
          aria-label="Toggle fit mode"
          title={
            fit === "width"
              ? "Fit page to viewport height"
              : "Fit page to viewport width"
          }
          className="flex items-center gap-1 rounded px-2 py-1 text-xs hover:opacity-80"
          style={{ color: "var(--color-text-muted)" }}
        >
          {fit === "width" ? (
            <>
              <MoveHorizontal className="h-3.5 w-3.5" />
              Fit width
            </>
          ) : (
            <>
              <Maximize className="h-3.5 w-3.5" />
              Fit page
            </>
          )}
        </button>

        {params.attachmentId && (
          <ProcessingMenu attachmentId={params.attachmentId} />
        )}

        {params.attachmentId && (
          <VersionPicker attachmentId={params.attachmentId} />
        )}

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
              const clamped = Math.max(1, Math.min(numPages, v));
              setPage(clamped);
              if (mode === "continuous") {
                continuousRef.current?.scrollToPage(clamped);
              }
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
      </div>

      {findOpen && (
        <div
          className="flex items-center gap-2 border-b px-3 py-2"
          style={{ borderColor: "var(--color-border)" }}
        >
          <Search
            className="h-3.5 w-3.5"
            style={{ color: "var(--color-text-muted)" }}
          />
          <input
            type="search"
            autoFocus
            value={findInput}
            onChange={(e) => setFindInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                jumpToMatch(currentMatch + (e.shiftKey ? -1 : 1));
              } else if (e.key === "Escape") {
                e.preventDefault();
                setFindOpen(false);
              }
            }}
            placeholder="Find in PDF…"
            className="min-w-0 flex-1 rounded border bg-transparent px-2 py-1 text-sm outline-none"
            style={{
              borderColor: "var(--color-border)",
              color: "var(--color-text)",
            }}
          />
          <span
            className="text-xs tabular-nums"
            style={{ color: "var(--color-text-muted)" }}
          >
            {findScanning
              ? "scanning…"
              : matches.length === 0
              ? findInput.trim()
                ? "0"
                : ""
              : `${currentMatch + 1} / ${matches.length}`}
          </span>
          <button
            onClick={() => jumpToMatch(currentMatch - 1)}
            disabled={matches.length === 0}
            aria-label="Previous match"
            className="rounded p-1 hover:opacity-70 disabled:opacity-30"
            style={{ color: "var(--color-text-muted)" }}
          >
            <ChevronLeft className="h-4 w-4" />
          </button>
          <button
            onClick={() => jumpToMatch(currentMatch + 1)}
            disabled={matches.length === 0}
            aria-label="Next match"
            className="rounded p-1 hover:opacity-70 disabled:opacity-30"
            style={{ color: "var(--color-text-muted)" }}
          >
            <ChevronRight className="h-4 w-4" />
          </button>
          <button
            onClick={() => {
              setFindOpen(false);
              setFindInput("");
            }}
            aria-label="Close find"
            className="rounded p-1 hover:opacity-70"
            style={{ color: "var(--color-text-muted)" }}
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      )}

      <div className="flex min-h-0 flex-1">
      <div
        ref={scrollRef}
        // overflow-y only — pinch handles horizontal pan via
        // translate, no native horizontal scroll. touchAction
        // toggles inside usePinchZoom (pan-y at scale=1, none when
        // zoomed).
        className={`flex-1 overflow-y-auto p-4${
          (highlightsOpen || outlineOpen) && isCoarsePointer
            ? " hidden sm:block"
            : ""
        }`}
        style={{
          touchAction: pinch.contentStyle.touchAction,
          overscrollBehavior: "contain",
        }}
      >
        {loading && (
          <div className="flex h-full items-center justify-center">
            <Loader2
              className="h-6 w-6 animate-spin"
              style={{ color: "var(--color-text-muted)" }}
            />
          </div>
        )}
        {error && (
          <div
            className="mx-auto max-w-md rounded border p-4 text-sm text-red-500"
            style={{ borderColor: "var(--color-border)" }}
          >
            Failed to load PDF: {error}
          </div>
        )}
        {doc && mode === "single" && (
          <div
            style={{
              ...pinch.contentStyle,
              width: "100%",
              display: "flex",
              justifyContent: "center",
            }}
          >
            <PageCanvas
              doc={doc}
              pageNumber={page}
              renderScale={pageScaleFor(page)}
              oversample={oversample}
              native={pageNativeRef.current.get(page)}
              findQuery={findQuery}
              currentOccurrence={
                currentMatchInfo && currentMatchInfo.page === page
                  ? currentMatchInfo.occurrence
                  : null
              }
              annotations={annotationsByPage.get(page) ?? []}
              debugText={debugText}
              pinchScaleRef={pinchScaleRef}
              onCreateHighlight={onCreateHighlight}
            />
          </div>
        )}
        {doc && mode === "continuous" && (
          <ContinuousList
            // Keying on virtualKey forces a fresh useVirtualizer
            // call when numPages / containerSize / heightsReady
            // change — the only way to definitively reset the
            // hook's internal measurement cache.
            key={virtualKey}
            ref={continuousRef}
            doc={doc}
            numPages={numPages}
            scrollRef={scrollRef}
            estimateSize={estimateSize}
            pageScaleFor={pageScaleFor}
            pageNativeRef={pageNativeRef}
            pinchStyle={pinch.contentStyle}
            oversample={oversample}
            findQuery={findQuery}
            currentMatchInfo={currentMatchInfo}
            annotationsByPage={annotationsByPage}
            debugText={debugText}
            pinchScaleRef={pinchScaleRef}
            onCreateHighlight={onCreateHighlight}
            onVisiblePageChange={setPage}
          />
        )}
      </div>
        {outlineOpen && (
          <OutlinePanel
            doc={doc}
            onJumpTo={jumpFromOutline}
            onClose={() => setOutlineOpen(false)}
          />
        )}
        {highlightsOpen && (
          <HighlightsPanel
            annotations={annotationsSorted}
            isDeleting={removeAnnotation.isPending}
            onJumpTo={jumpToAnnotation}
            onDelete={(id) => removeAnnotation.mutate(id)}
            onRecolor={(id, color) => recolorAnnotation.mutate({ id, color })}
            onClose={() => setHighlightsOpen(false)}
          />
        )}
      </div>
    </div>
  );
}

// Standard highlight palette — yellow first matches the create-time
// default. Anything outside the set still renders correctly via the
// stored hex; the palette only governs what users can pick from.
const HIGHLIGHT_PALETTE: ReadonlyArray<{ name: string; hex: string }> = [
  { name: "Yellow", hex: "#ffd400" },
  { name: "Lime", hex: "#a3e635" },
  { name: "Blue", hex: "#60a5fa" },
  { name: "Pink", hex: "#f472b6" },
  { name: "Orange", hex: "#fb923c" },
  { name: "Purple", hex: "#c084fc" },
];

function HighlightsPanel({
  annotations,
  isDeleting,
  onJumpTo,
  onDelete,
  onRecolor,
  onClose,
}: {
  annotations: Annotation[];
  isDeleting: boolean;
  onJumpTo: (a: Annotation) => void;
  onDelete: (id: string) => void;
  onRecolor: (id: string, color: string) => void;
  onClose: () => void;
}) {
  const [pickerOpenId, setPickerOpenId] = useState<string | null>(null);
  return (
    <aside
      className="flex w-full flex-col border-l sm:w-80"
      style={{
        borderColor: "var(--color-border)",
        backgroundColor: "var(--color-surface)",
      }}
    >
      <div
        className="flex items-center justify-between border-b px-3 py-2"
        style={{ borderColor: "var(--color-border)" }}
      >
        <span
          className="text-xs font-medium"
          style={{ color: "var(--color-text)" }}
        >
          Highlights ({annotations.length})
        </span>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close highlights panel"
          className="rounded p-1 hover:opacity-70"
          style={{ color: "var(--color-text-muted)" }}
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
      {annotations.length === 0 ? (
        <div
          className="px-3 py-6 text-center text-xs"
          style={{ color: "var(--color-text-muted)" }}
        >
          No highlights yet. Select text in the PDF and tap "Highlight" to
          create one.
        </div>
      ) : (
        <ul className="min-h-0 flex-1 overflow-y-auto">
          {annotations.map((a) => {
            const pickerOpen = pickerOpenId === a.id;
            return (
              <li
                key={a.id}
                className="border-b last:border-b-0"
                style={{ borderColor: "var(--color-border)" }}
              >
                <div className="flex items-start gap-2 px-3 py-2">
                  <button
                    type="button"
                    onClick={() =>
                      setPickerOpenId(pickerOpen ? null : a.id)
                    }
                    aria-label="Change highlight color"
                    title="Change color"
                    aria-expanded={pickerOpen}
                    className="mt-0.5 inline-block h-3.5 w-3.5 shrink-0 rounded-sm border border-black/10 hover:opacity-80"
                    style={{ backgroundColor: a.color }}
                  />
                  <button
                    type="button"
                    onClick={() => onJumpTo(a)}
                    className="min-w-0 flex-1 text-left hover:opacity-80"
                  >
                    <div
                      className="mb-1 text-[10px] uppercase tracking-wide"
                      style={{ color: "var(--color-text-muted)" }}
                    >
                      Page {a.page_number}
                    </div>
                    <div
                      className="line-clamp-3 text-xs"
                      style={{ color: "var(--color-text)" }}
                    >
                      {a.text?.trim() || (
                        <em style={{ color: "var(--color-text-muted)" }}>
                          (no text)
                        </em>
                      )}
                    </div>
                  </button>
                  <button
                    type="button"
                    onClick={() => onDelete(a.id)}
                    disabled={isDeleting}
                    aria-label="Delete highlight"
                    title="Delete highlight"
                    className="rounded p-1 hover:opacity-70 disabled:opacity-30"
                    style={{ color: "var(--color-text-muted)" }}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
                {pickerOpen && (
                  <div
                    className="flex items-center gap-2 px-3 pb-2"
                    role="radiogroup"
                    aria-label="Highlight color"
                  >
                    {HIGHLIGHT_PALETTE.map((c) => {
                      const selected =
                        a.color.toLowerCase() === c.hex.toLowerCase();
                      return (
                        <button
                          key={c.hex}
                          type="button"
                          role="radio"
                          aria-checked={selected}
                          aria-label={c.name}
                          title={c.name}
                          onClick={() => {
                            if (!selected) onRecolor(a.id, c.hex);
                            setPickerOpenId(null);
                          }}
                          className="h-5 w-5 rounded-full border hover:scale-110"
                          style={{
                            backgroundColor: c.hex,
                            borderColor: selected
                              ? "var(--color-text)"
                              : "rgba(0,0,0,0.15)",
                            borderWidth: selected ? 2 : 1,
                          }}
                        />
                      );
                    })}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </aside>
  );
}

interface ContinuousListHandle {
  scrollToPage: (page: number) => void;
}

const ContinuousList = forwardRef<
  ContinuousListHandle,
  {
    doc: PDFDocumentProxy;
    numPages: number;
    scrollRef: React.RefObject<HTMLDivElement | null>;
    estimateSize: (index: number) => number;
    pageScaleFor: (n: number) => number;
    pageNativeRef: React.RefObject<Map<number, NativeViewport>>;
    pinchStyle: React.CSSProperties;
    oversample: number;
    findQuery: string;
    currentMatchInfo: { page: number; occurrence: number } | null;
    annotationsByPage: Map<number, Annotation[]>;
    debugText: boolean;
    pinchScaleRef: React.RefObject<number>;
    onCreateHighlight: (
      pageNumber: number,
      rects: Rect[],
      text: string,
    ) => void;
    onVisiblePageChange: (page: number) => void;
  }
>(function ContinuousList(
  {
    doc,
    numPages,
    scrollRef,
    estimateSize,
    pageScaleFor,
    pageNativeRef,
    pinchStyle,
    oversample,
    findQuery,
    currentMatchInfo,
    annotationsByPage,
    debugText,
    pinchScaleRef,
    onCreateHighlight,
    onVisiblePageChange,
  },
  ref,
) {
  const virtualizer = useVirtualizer({
    count: numPages,
    getScrollElement: () => scrollRef.current,
    estimateSize,
    overscan: 3,
  });

  useImperativeHandle(
    ref,
    () => ({
      scrollToPage: (page: number) => {
        virtualizer.scrollToIndex(page - 1, { align: "start" });
      },
    }),
    [virtualizer],
  );

  const items = virtualizer.getVirtualItems();

  // Scroll-driven page indicator. Each scroll tick we pick the page
  // whose top edge has just passed the viewport's "current page"
  // line — a small offset below the top so a page only switches once
  // it's clearly the dominant one on screen. The previous build had
  // no listener here, so the page number was frozen at whatever the
  // toolbar / outline / URL last set it to.
  const lastReportedRef = useRef<number>(0);
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const compute = () => {
      const offset = virtualizer.scrollOffset ?? el.scrollTop;
      const viewportH = el.clientHeight;
      // Threshold: a page becomes "current" once its top has scrolled
      // about a third of the viewport past the top edge.
      const line = offset + viewportH * 0.3;
      const visible = virtualizer.getVirtualItems();
      if (visible.length === 0) return;
      let pick = visible[0].index;
      for (const v of visible) {
        if (v.start <= line) pick = v.index;
        else break;
      }
      const page = pick + 1;
      if (page !== lastReportedRef.current) {
        lastReportedRef.current = page;
        onVisiblePageChange(page);
      }
    };
    // No initial compute() — at mount scrollTop=0 would always
    // resolve to page 1, which would clobber a deep-link ?page=N
    // before the URL-jump effect has scrolled to N.
    el.addEventListener("scroll", compute, { passive: true });
    return () => el.removeEventListener("scroll", compute);
  }, [scrollRef, virtualizer, onVisiblePageChange]);

  return (
    <div
      style={{
        ...pinchStyle,
        width: "100%",
        position: "relative",
        height: `${virtualizer.getTotalSize()}px`,
      }}
    >
      {items.map((vi) => {
        const pageNumber = vi.index + 1;
        return (
          <div
            key={vi.key}
            data-index={vi.index}
            style={{
              position: "absolute",
              top: 0,
              left: 0,
              width: "100%",
              transform: `translateY(${vi.start}px)`,
              paddingBottom: `${PAGE_GAP}px`,
              display: "flex",
              justifyContent: "center",
            }}
          >
            <PageCanvas
              doc={doc}
              pageNumber={pageNumber}
              renderScale={pageScaleFor(pageNumber)}
              oversample={oversample}
              native={pageNativeRef.current.get(pageNumber)}
              findQuery={findQuery}
              currentOccurrence={
                currentMatchInfo && currentMatchInfo.page === pageNumber
                  ? currentMatchInfo.occurrence
                  : null
              }
              annotations={annotationsByPage.get(pageNumber) ?? []}
              debugText={debugText}
              pinchScaleRef={pinchScaleRef}
              onCreateHighlight={onCreateHighlight}
            />
          </div>
        );
      })}
    </div>
  );
});

function PageCanvas({
  doc,
  pageNumber,
  renderScale,
  oversample,
  native,
  findQuery,
  currentOccurrence,
  annotations,
  debugText,
  pinchScaleRef,
  onCreateHighlight,
}: {
  doc: PDFDocumentProxy;
  pageNumber: number;
  renderScale: number;
  /** Pixel-buffer multiplier on top of dpr. CSS box stays at
   *  renderScale × native; the extra pixels are spent on detail
   *  visible only when the wrapper is CSS-scaled (pinch zoom).
   *  Default 1 means "no oversample". */
  oversample: number;
  native?: NativeViewport;
  findQuery: string;
  currentOccurrence: number | null;
  annotations: Annotation[];
  debugText: boolean;
  pinchScaleRef: React.RefObject<number>;
  onCreateHighlight: (
    pageNumber: number,
    rects: Rect[],
    text: string,
  ) => void;
}) {
  const wrapperRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const textLayerRef = useRef<HTMLDivElement>(null);
  const [textLayerVersion, setTextLayerVersion] = useState(0);

  const cssW = native ? native.width * renderScale : undefined;
  const cssH = native ? native.height * renderScale : undefined;

  // Pending selection for the "Highlight" floating button. Rects are
  // in *layout* coords within the wrapper (un-pinched, un-rendered)
  // so the button can position itself with the same coord system as
  // the wrapper. Conversion to PDF user-space happens on commit.
  const [pendingHighlight, setPendingHighlight] = useState<{
    layoutRects: { x: number; y: number; w: number; h: number }[];
    pdfRects: Rect[];
    text: string;
  } | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const wrapper = wrapperRef.current;
    const textLayer = textLayerRef.current;
    if (!canvas || !wrapper || !textLayer) return;
    if (renderScale <= 0) return;

    let cancelled = false;
    let task: RenderTask | null = null;
    let pdfPage: PDFPageProxy | null = null;

    (async () => {
      pdfPage = await doc.getPage(pageNumber);
      if (cancelled || !pdfPage) return;
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      // pixelMultiplier puts more detail in the canvas's pixel
      // buffer than its CSS box demands. dpr handles HiDPI;
      // oversample handles "user has pinch-zoomed and now wants
      // crisp text". The CSS box stays at renderScale × native, so
      // the wrapper layout (and thus the virtualizer geometry) is
      // unaffected.
      const pixelMultiplier = dpr * oversample;
      const viewport = pdfPage.getViewport({
        scale: renderScale * pixelMultiplier,
      });

      const offscreen = document.createElement("canvas");
      offscreen.width = viewport.width;
      offscreen.height = viewport.height;
      const offCtx = offscreen.getContext("2d");
      if (!offCtx) return;
      task = pdfPage.render({
        canvasContext: offCtx,
        viewport,
        canvas: offscreen,
      });
      try {
        await task.promise;
      } catch (e) {
        if ((e as Error).name !== "RenderingCancelledException") throw e;
        return;
      }
      if (cancelled) return;

      canvas.width = viewport.width;
      canvas.height = viewport.height;
      canvas.style.width = `${viewport.width / pixelMultiplier}px`;
      canvas.style.height = `${viewport.height / pixelMultiplier}px`;
      const ctx = canvas.getContext("2d");
      ctx?.drawImage(offscreen, 0, 0);

      // pdfjs reads `--total-scale-factor` from the container (or
      // an ancestor) when computing per-span font-size + transforms.
      // Without it, every span defaults to scale=1 and per-character
      // positions don't line up with the rendered glyphs — that was
      // the source of the find-highlight offset.
      textLayer.replaceChildren();
      const cssViewport = pdfPage.getViewport({ scale: renderScale });
      textLayer.style.setProperty(
        "--total-scale-factor",
        String(cssViewport.scale),
      );
      const layer = new pdfjs.TextLayer({
        textContentSource: pdfPage.streamTextContent(),
        container: textLayer,
        viewport: cssViewport,
      });
      try {
        await layer.render();
        if (!cancelled) setTextLayerVersion((v) => v + 1);
      } catch {
        // selection layer is best-effort
      }
    })().catch(() => {
      // per-page errors don't crash the reader
    });

    return () => {
      cancelled = true;
      task?.cancel();
      pdfPage?.cleanup();
    };
  }, [doc, pageNumber, renderScale, oversample]);

  // Capture the user's text selection. Listens at the document
  // level for `selectionchange` (debounced ~180ms) so we catch the
  // selection no matter where the user's finger ends up — Android's
  // selection handles fire pointer events on the document body, not
  // on the page wrapper, so the older wrapper-only `pointerup`
  // listener missed most mobile selections. Debouncing means the
  // floating button only appears after the selection settles, not
  // during handle drag. The pending state drives the floating
  // "Highlight" button; clicking it commits via onCreateHighlight.
  useEffect(() => {
    const wrapper = wrapperRef.current;
    if (!wrapper || !native) return;

    function check(): void {
      if (!wrapper) return;
      const sel = window.getSelection();
      if (!sel || sel.isCollapsed || sel.rangeCount === 0) {
        setPendingHighlight(null);
        return;
      }
      const range = sel.getRangeAt(0);
      // Only capture selections that are entirely within this page.
      if (!wrapper.contains(range.commonAncestorContainer)) {
        setPendingHighlight(null);
        return;
      }
      const rects = Array.from(range.getClientRects());
      if (rects.length === 0) {
        setPendingHighlight(null);
        return;
      }

      const wrapperRect = wrapper.getBoundingClientRect();
      const s = pinchScaleRef.current || 1;
      const layoutRects = rects.map((r) => ({
        x: (r.left - wrapperRect.left) / s,
        y: (r.top - wrapperRect.top) / s,
        w: r.width / s,
        h: r.height / s,
      }));
      // PDF user-space: divide by renderScale to undo the canvas
      // scale, flip y so origin is bottom-left.
      const nativeH = native!.height;
      const pdfRects: Rect[] = layoutRects.map((r) => {
        const pdfX = r.x / renderScale;
        const pdfH = r.h / renderScale;
        const pdfW = r.w / renderScale;
        const pdfY = nativeH - r.y / renderScale - pdfH;
        return [pdfX, pdfY, pdfW, pdfH];
      });
      setPendingHighlight({
        layoutRects,
        pdfRects,
        text: sel.toString(),
      });
    }

    let timer: number | null = null;
    function schedule(): void {
      if (timer != null) window.clearTimeout(timer);
      // 180ms is short enough to feel responsive after a release
      // but long enough to skip the per-character storm while the
      // user is actively dragging a selection handle on Android.
      timer = window.setTimeout(check, 180);
    }

    document.addEventListener("selectionchange", schedule);
    // pointerup/touchend at the document level catches the case
    // where the selection's final state is established by a
    // gesture-end without a trailing selectionchange (e.g. tap to
    // collapse the selection, or release after handle drag).
    document.addEventListener("pointerup", schedule);
    document.addEventListener("touchend", schedule);
    return () => {
      if (timer != null) window.clearTimeout(timer);
      document.removeEventListener("selectionchange", schedule);
      document.removeEventListener("pointerup", schedule);
      document.removeEventListener("touchend", schedule);
    };
  }, [native, renderScale, pinchScaleRef]);

  // Per-occurrence find highlight via DOM Range geometry. The
  // earlier "wrap matches in <mark>" approach was double-broken on
  // pdfjs text layers — both size and position. pdfjs renders each
  // text item as one span using a *system* font, then applies a CSS
  // scale transform so the span's overall width matches the canvas
  // glyph width. Per-character positions inside the span don't map
  // to the real glyph positions; the system font has its own
  // metrics. So a <mark> at characters N..M sat at character-flow
  // positions of a different font, scaled by the span's transform —
  // visually misaligned and mis-sized.
  // Range.getClientRects on the text node returns the *actual*
  // rendered DOM rectangles, transformed by the browser; same
  // geometry as the rendered glyphs. We draw absolute-positioned
  // overlay divs at those rects. Same approach pdfjs's own viewer
  // uses.
  useEffect(() => {
    const textLayer = textLayerRef.current;
    if (!textLayer) return;

    // Drop any prior overlay divs from the previous query so we
    // start clean.
    textLayer
      .querySelectorAll(".shelf-find-rect")
      .forEach((el) => el.remove());

    const needle = findQuery.trim();
    if (!needle || textLayerVersion === 0) return;

    const lowerNeedle = needle.toLowerCase();
    const spans = Array.from(
      textLayer.querySelectorAll<HTMLSpanElement>("span"),
    );
    const layerRect = textLayer.getBoundingClientRect();

    let occ = 0;
    let currentEl: HTMLElement | null = null;

    for (const span of spans) {
      const text = span.textContent ?? "";
      const lowerText = text.toLowerCase();
      const node = span.firstChild;
      if (!node || node.nodeType !== Node.TEXT_NODE) continue;

      let from = 0;
      while (true) {
        const idx = lowerText.indexOf(lowerNeedle, from);
        if (idx === -1) break;
        const end = idx + lowerNeedle.length;

        const range = document.createRange();
        try {
          range.setStart(node, idx);
          range.setEnd(node, end);
        } catch {
          from = end;
          occ += 1;
          continue;
        }

        const isCurrent = occ === currentOccurrence;
        // getClientRects returns *visual* (post-transform) viewport
        // coords. The new overlay div lives inside the textLayer,
        // which is itself inside the pinch wrapper's CSS scale
        // transform — anything we set via style.left gets multiplied
        // by the pinch scale on render. Divide the visual diff by
        // the current pinch.scale so the *layout-coord* placement
        // composes back into the right visual position. The pinch
        // hook then transforms the divs along with the glyphs, so
        // they stay aligned through subsequent pinch changes
        // without re-running this effect every frame.
        const s = pinchScaleRef.current || 1;
        for (const rect of Array.from(range.getClientRects())) {
          const div = document.createElement("div");
          div.className = isCurrent
            ? "shelf-find-rect shelf-find-current"
            : "shelf-find-rect shelf-find-match";
          div.style.left = `${(rect.left - layerRect.left) / s}px`;
          div.style.top = `${(rect.top - layerRect.top) / s}px`;
          div.style.width = `${rect.width / s}px`;
          div.style.height = `${rect.height / s}px`;
          textLayer.appendChild(div);
          if (isCurrent && !currentEl) currentEl = div;
        }

        from = end;
        occ += 1;
      }
    }

    if (currentEl) {
      currentEl.scrollIntoView({ behavior: "smooth", block: "center" });
    }
    // pinchScaleRef is read inside the loop above. Listing it here
    // would be a no-op since refs don't drive re-runs, but the
    // closure does need to capture the ref; depending on it costs
    // nothing.
  }, [findQuery, currentOccurrence, textLayerVersion, pinchScaleRef]);

  return (
    <div
      ref={wrapperRef}
      className="relative rounded border shadow-md"
      style={{
        borderColor: "var(--color-border)",
        backgroundColor: "var(--color-surface)",
        display: "inline-block",
        width: cssW != null ? `${cssW}px` : undefined,
        height: cssH != null ? `${cssH}px` : undefined,
      }}
    >
      <canvas ref={canvasRef} className="absolute inset-0" />
      {/*
        Both pdfjs's `.textLayer` styles (font/transform vars,
        per-span sizing) and our shelf-specific positioning kick in
        from this composite class.
      */}
      <div
        ref={textLayerRef}
        className={`textLayer shelf-textlayer${
          debugText ? " shelf-debug-text" : ""
        }`}
      />
      {native && cssH != null && annotations.length > 0 && (
        <AnnotationOverlay
          annotations={annotations}
          renderScale={renderScale}
          pageHeight={native.height}
        />
      )}
      {pendingHighlight && (
        <HighlightSelectionButton
          layoutRects={pendingHighlight.layoutRects}
          onClick={() =>
            onCreateHighlight(
              pageNumber,
              pendingHighlight.pdfRects,
              pendingHighlight.text,
            )
          }
        />
      )}
    </div>
  );
}

function HighlightSelectionButton({
  layoutRects,
  onClick,
}: {
  layoutRects: { x: number; y: number; w: number; h: number }[];
  onClick: () => void;
}) {
  // Position the button at the bottom-right corner of the last
  // rect — that's the natural "selection end" for left-to-right
  // text.
  const last = layoutRects[layoutRects.length - 1];
  const left = last.x + last.w;
  const top = last.y + last.h;
  return (
    <button
      type="button"
      onClick={onClick}
      // Stop propagation so the wrapper's pointerup handler
      // doesn't immediately re-evaluate (and clear) the selection.
      onPointerDown={(e) => e.stopPropagation()}
      onMouseDown={(e) => e.stopPropagation()}
      className="shelf-highlight-button"
      style={{ left: `${left}px`, top: `${top}px` }}
    >
      Highlight
    </button>
  );
}

function AnnotationOverlay({
  annotations,
  renderScale,
  pageHeight,
}: {
  annotations: Annotation[];
  renderScale: number;
  /** Native (scale=1) page height in PDF user-space; needed to flip
   *  the y-axis from PDF (origin bottom-left) to CSS (origin top). */
  pageHeight: number;
}) {
  return (
    <div className="shelf-annotations">
      {annotations.map((a) => {
        if (a.kind === "note") {
          // Notes draw a single pin centred on the rect's origin.
          const r = a.rects[0];
          if (!r) return null;
          const cssX = r[0] * renderScale;
          const cssY = (pageHeight - r[1]) * renderScale;
          return (
            <button
              key={a.id}
              type="button"
              className="shelf-annotation-note"
              style={{
                left: `${cssX}px`,
                top: `${cssY}px`,
                backgroundColor: a.color,
              }}
              title={a.text ?? ""}
              aria-label={a.text ?? "Note"}
            />
          );
        }
        // Highlight: one tinted rect per quad.
        return a.rects.map(([x, y, w, h], idx) => {
          const cssX = x * renderScale;
          const cssY = (pageHeight - y - h) * renderScale;
          return (
            <div
              key={`${a.id}-${idx}`}
              className="shelf-annotation-rect"
              style={{
                left: `${cssX}px`,
                top: `${cssY}px`,
                width: `${w * renderScale}px`,
                height: `${h * renderScale}px`,
                backgroundColor: a.color,
                opacity: 0.45,
              }}
              title={a.text ?? ""}
            />
          );
        });
      })}
    </div>
  );
}
