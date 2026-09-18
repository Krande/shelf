import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  useLocation,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router";
import {
  ArrowLeft,
  Bookmark,
  Check,
  ChevronLeft,
  ChevronRight,
  Eye,
  EyeOff,
  FileText,
  Highlighter,
  Link2,
  List,
  ListTree,
  Loader2,
  Maximize,
  MoveHorizontal,
  Search,
  Trash2,
  X,
  ZoomIn,
  ZoomOut,
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
  annotationLink,
  type Rect,
  createAnnotation,
  deleteAnnotation,
  listAnnotations,
  updateAnnotation,
} from "@/api/annotations";
import {
  PREF_READER_FIT,
  PREF_READER_MODE,
  PREF_READER_SPREAD,
  usePref,
} from "@/auth/prefs";
import { useDebounce } from "@/hooks/useDebounce";
import {
  clampZoom,
  MAX_ZOOM,
  MIN_ZOOM,
  useZoomGestures,
} from "@/hooks/useZoomGestures";
import { pageLinks, type PageLink } from "@/lib/pdfLinks";
import { CanvasBudget, clampPixelMultiplier } from "@/lib/canvasBudget";
import { RenderQueue } from "@/lib/renderQueue";
import { pageRows, rowOfPage, SPREAD_LABELS } from "@/lib/spreads";
import OutlinePanel from "@/components/library/OutlinePanel";
import ProcessingMenu from "@/components/reader/ProcessingMenu";
import VersionPicker from "@/components/reader/VersionPicker";

/** Multiplier per zoom button press; matches pdf.js's own viewer. */
const ZOOM_STEP = 1.1;

/** Gutter between the two pages of a facing pair. */
const SPREAD_GAP = 12;

const PAGE_GAP = 16;
// p-4 padding on the scroll container = 16px each side, 32px total
// horizontal — pages render to fit the inner content width.
const SCROLL_PADDING = 32;
const ESTIMATE_PAGE_HEIGHT = 1100;

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
  const location = useLocation();
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
      if (!r) return;
      // Rounded: sub-pixel churn would otherwise re-run the scale
      // calculation, and so every page's render, for a change nobody
      // can see.
      const width = Math.round(r.width);
      const height = Math.round(r.height);
      setContainerSize((prev) =>
        prev.width === width && prev.height === height
          ? prev
          : { width, height },
      );
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
          // baseline and apply it to every page. Each page corrects
          // itself on first render — see onNativeSize — so a document
          // of mixed page sizes converges as it is read rather than
          // staying wrong.
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

  // Page text, kept for the life of the document. Pulling it is the
  // expensive half of a search and it never changes, so the second
  // search of a document costs nothing on the worker -- which matters
  // because the query is debounced, not final: "fatigue" is searched
  // after "fati" and "fatig" have been.
  const pageText = useRef(new Map<number, string>());
  useEffect(() => {
    pageText.current = new Map();
  }, [doc]);

  // Whole-document text scan when the find query changes.
  //
  // Results are published as they are found rather than at the end, so
  // a hit on page 3 is usable while page 300 is still being read, and
  // the scan yields between pages so it shares the worker with page
  // rendering instead of starving it.
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
    // Before the first partial publish, or the new list is indexed with
    // the old query's match and the reader is yanked to whatever that
    // happens to point at.
    setCurrentMatch(0);

    (async () => {
      const found: Array<{ page: number; occurrence: number }> = [];
      for (let n = 1; n <= doc.numPages; n++) {
        if (cancelled) return;
        let text = pageText.current.get(n);
        if (text === undefined) {
          const p = await doc.getPage(n);
          try {
            const tc = await p.getTextContent();
            text = tc.items
              .map((it) => ("str" in it ? (it as { str: string }).str : ""))
              .join(" ")
              .toLowerCase();
            pageText.current.set(n, text);
          } finally {
            p.cleanup();
          }
        }
        if (cancelled) return;
        let from = 0;
        let occ = 0;
        while (true) {
          const idx = text.indexOf(needle, from);
          if (idx === -1) break;
          found.push({ page: n, occurrence: occ });
          occ += 1;
          from = idx + needle.length;
        }
        // Publish what we have so far, and hand the event loop back so
        // the page the reader is looking at can render. Every 8 pages
        // rather than every page: a re-render per page of a 357-page
        // document is its own stall.
        if (found.length > 0 && n % 8 === 0) {
          setMatches([...found]);
        }
        if (n % 8 === 0) await new Promise((r) => setTimeout(r, 0));
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
  //
  // Trailing-debounced: `page` follows the scroll, so a drag through a
  // few hundred pages would otherwise be a few hundred history
  // mutations in a couple of seconds. Chrome and Firefox both throttle
  // same-document history writes and start dropping them, and the URL
  // only has to be right once the scrolling stops.
  useEffect(() => {
    const t = setTimeout(() => {
      const url = new URL(window.location.href);
      if (page > 1) url.searchParams.set("page", String(page));
      else url.searchParams.delete("page");
      window.history.replaceState(null, "", url.toString());
    }, 300);
    return () => clearTimeout(t);
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
  //
  // Honoured once per navigation, tracked by location key. The effect
  // also depends on layout state that changes long afterwards --
  // opening or closing the find bar resizes the scroll container, which
  // remounts the virtualizer and flips heightsReady -- and react-router
  // never learns about the ?page= writeback above, since that uses
  // replaceState. So its copy of the param stays frozen at whatever
  // brought us here, and without this guard any later relayout would
  // re-apply it: follow a link to page 40, close the find bar, and the
  // reader would throw you back to the page the deep link named.
  const handledPageNav = useRef<string | null>(null);
  useEffect(() => {
    if (mode !== "continuous") return;
    if (!heightsReady || numPages === 0) return;
    const raw = searchParams.get("page");
    const target = Number(raw || 0);
    if (!(target > 1 && target <= numPages)) return;
    // Keyed on the navigation, not the value, so arriving at the same
    // page twice from two different search hits still scrolls.
    const nav = `${location.key}:${raw}`;
    if (handledPageNav.current === nav) return;
    handledPageNav.current = nav;
    setPage(target);
    // Defer past the first paint so the keyed ContinuousList has
    // mounted, the virtualizer has measured the container, and the
    // imperative handle is exposed.
    const t = setTimeout(() => {
      continuousRef.current?.scrollToPage(target);
    }, 50);
    return () => clearTimeout(t);
  }, [mode, heightsReady, numPages, searchParams, location.key]);

  // Per-page base render scale. Default is fit-to-width (renders the
  // page to fill the container's inner width); "page" mode clamps
  // additionally to inner height so the whole page is visible at
  // once — needed for tall PDFs where the user otherwise can't see
  // the bottom without scrolling and pinch can only zoom further in,
  // not out. With containerSize.width = 0 (initial mount, before
  // ResizeObserver fires) we return 1 as a safe fallback; the
  // virtualizer is remounted on width/height/fit change below so the
  // wrong heights don't get cached.
  // Zoom multiplies the fit scale rather than transforming what the
  // fit scale produced. Everything downstream — the page box, the
  // canvas, the virtualizer's geometry — is built from the product, so
  // the document's scroll height grows with the zoom and native
  // scrolling reaches all of a zoomed page. pdf.js's own viewer works
  // this way, and so does every other viewer that behaves.
  const [zoom, setZoom] = useState(1);
  const [spread, setSpread] = usePref(PREF_READER_SPREAD);

  // How the document is laid out: a row per page, or facing pairs.
  const rows = useMemo(() => pageRows(numPages, spread), [numPages, spread]);

  /**
   * The scale a row renders at.
   *
   * Per row rather than per page, because a facing pair has to fit the
   * width between them — so a spread is drawn at roughly half the scale
   * a lone page would be, which is what makes it a spread rather than
   * two pages overflowing. Fit-page measures the tallest page in the
   * row, since that is the one that has to clear the viewport.
   */
  const rowScaleFor = useCallback(
    (pages: number[]): number => {
      if (pages.length === 0 || containerSize.width === 0) return 1;
      const natives = pages.map((n) => pageNativeRef.current.get(n));
      if (natives.some((n) => !n)) return 1;
      const totalW = natives.reduce((sum, n) => sum + n!.width, 0);
      const maxH = Math.max(...natives.map((n) => n!.height));
      const gutter = (pages.length - 1) * SPREAD_GAP;
      const innerW = Math.max(
        1,
        containerSize.width - SCROLL_PADDING - gutter,
      );
      const widthScale = innerW / totalW;
      if (fit === "page" && containerSize.height > 0) {
        const innerH = Math.max(0, containerSize.height - SCROLL_PADDING);
        if (innerH > 0) return Math.min(widthScale, innerH / maxH) * zoom;
      }
      return widthScale * zoom;
    },
    [containerSize.width, containerSize.height, fit, zoom],
  );

  // Single-page mode draws one page, so it asks about a row of one
  // rather than carrying a second scale function.
  const pageScaleFor = useCallback(
    (n: number): number => rowScaleFor([n]),
    [rowScaleFor],
  );

  const estimateSize = useCallback(
    (index: number) => {
      const pages = rows[index];
      if (!pages) return ESTIMATE_PAGE_HEIGHT + PAGE_GAP;
      const natives = pages.map((n) => pageNativeRef.current.get(n));
      if (natives.some((n) => !n)) return ESTIMATE_PAGE_HEIGHT + PAGE_GAP;
      const tallest = Math.max(...natives.map((n) => n!.height));
      return tallest * rowScaleFor(pages) + PAGE_GAP;
    },
    [rows, rowScaleFor],
  );

  // useVirtualizer is moved into ContinuousList (a child component
  // that gets key'd on the relevant inputs), so we can guarantee the
  // virtualizer's internal cache resets when those inputs change.
  // Keying the wrapper alone wasn't enough because the hook lived
  // here and survived the remount — its cached estimateSize results
  // from the first paint kept totalSize stuck small, capping how
  // far you could scroll.
  // Deliberately excludes the container size and the zoom: those
  // change often and are handled by re-measuring in place. Only the
  // things that invalidate the virtualizer wholesale remain.
  const virtualKey = `${numPages}-${heightsReady}`;

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
      } else if (e.key === "Backspace") {
        // Counterpart to Enter in the library. Same history pop the
        // toolbar's Back does, so the selection comes back with it.
        // preventDefault so a browser that still maps Backspace to
        // history navigation doesn't do it twice.
        e.preventDefault();
        nav(-1);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [goPrev, goNext, nav]);

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
  // The element the transient pinch preview is applied to, and which
  // holds the pages. Only touched during a live gesture.
  const contentRef = useRef<HTMLDivElement>(null);

  /**
   * Change zoom while holding the reader's place.
   *
   * The anchor is a row and how far into it the viewport sits, not a
   * fraction of the document: the virtualizer only estimates its total
   * height and revises it as pages render, so a fraction of that total
   * means something different a moment later. A row and an offset
   * within it survive the rows changing height, which is the one thing
   * a zoom is guaranteed to do.
   *
   * Restoring happens in ContinuousList, straight after it re-measures.
   * Doing it here would run before that and use the old geometry, which
   * is what made zooming out scroll down the document.
   */
  // Single-page mode has no virtualizer to correct, but it still has to
  // record the true page size: that height is the origin the
  // selection-to-PDF conversion flips around, so a stale one stores
  // highlights at the wrong coordinates everywhere else.
  const applyNativeSizeSingle = useCallback(
    (n: number, size: NativeViewport) => {
      pageNativeRef.current.set(n, size);
    },
    [],
  );

  const applyZoom = useCallback((next: number) => {
    setZoom(clampZoom(next));
  }, []);

  const { previewRef } = useZoomGestures(scrollRef, contentRef, {
    zoom,
    onZoom: applyZoom,
    enabled: !selectMode,
  });
  // Mirror pinch.scale into a ref so the find-highlight effect (in
  // PageCanvas) can read the current value without listing
  // pinch.scale as a dependency. We don't want the effect to re-run
  // every pinch frame — getClientRects + DOM updates per frame
  // would tank the gesture's frame rate. Reading via ref means the
  // effect picks up the latest value at re-run time (when query /
  // textLayer changes) and the divs get placed in *layout*
  // coordinates so the parent transform composes them correctly
  // through any subsequent pinch.
  const pinchScaleRef = previewRef;

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

  const mutateHighlight = createHighlight.mutate;
  const onCreateHighlight = useCallback(
    (pageNumber: number, rects: Rect[], text: string) => {
      // Drop the OS selection so the floating button doesn't linger
      // and so a re-tap on the same word doesn't show stale state.
      window.getSelection()?.removeAllRanges();
      mutateHighlight({ pageNumber, rects, text });
      // Exit mobile select-mode after a successful highlight so the
      // user can scroll/pinch again without an extra tap.
      setSelectMode(false);
    },
    // `.mutate` is stable across renders; the mutation object it hangs
    // off is not, and depending on that rebuilt this callback -- and
    // re-rendered every mounted page -- on every render of the reader.
    [mutateHighlight],
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

  // Links are clickable only while Ctrl/Cmd is held. Written to the DOM
  // rather than state so a keypress doesn't re-render every visible
  // page. Cleared on blur: tab away mid-Ctrl and the keyup lands in
  // another window.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const set = (on: boolean) => {
      el.dataset.followLinks = on ? "true" : "false";
    };
    const onKey = (e: KeyboardEvent) => set(e.ctrlKey || e.metaKey);
    const clear = () => set(false);
    set(false);
    window.addEventListener("keydown", onKey);
    window.addEventListener("keyup", onKey);
    window.addEventListener("blur", clear);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("keyup", onKey);
      window.removeEventListener("blur", clear);
    };
  }, []);

  // Generic "scroll the reader to page N" — used by every panel
  // that wants to navigate (annotations, outline, fulltext).
  const goToPage = useCallback(
    (n: number) => {
      setPage(n);
      if (mode === "continuous") {
        // Deferred past the setPage render flush so the virtualizer has
        // the current geometry. Zoom no longer enters into it: the
        // layout is the zoom, so scrollToIndex is always in the same
        // coordinates the user sees.
        setTimeout(() => {
          continuousRef.current?.scrollToPage(n);
        }, 0);
      }
    },
    [mode],
  );

  // The annotation to ring, if any. Set by a deep link or by clicking
  // through from the panel, and cleared on a timer so the emphasis
  // fades rather than sticking to the page forever.
  const [focusedAnnotationId, setFocusedAnnotationId] = useState<string | null>(
    null,
  );
  const focusTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const focusAnnotation = useCallback((id: string) => {
    setFocusedAnnotationId(id);
    if (focusTimer.current) clearTimeout(focusTimer.current);
    focusTimer.current = setTimeout(() => setFocusedAnnotationId(null), 2600);
  }, []);

  useEffect(
    () => () => {
      if (focusTimer.current) clearTimeout(focusTimer.current);
    },
    [],
  );

  const jumpToAnnotation = useCallback(
    (a: Annotation) => {
      goToPage(a.page_number);
      focusAnnotation(a.id);
      // Auto-close the drawer on coarse-pointer devices so the user
      // can see the highlight without an extra tap.
      if (isCoarsePointer) setHighlightsOpen(false);
    },
    [goToPage, focusAnnotation, isCoarsePointer],
  );

  // `?annotation=<id>` — open at that annotation and ring it.
  //
  // Waits for the annotation list *and* for the page heights the
  // virtualizer needs: scrolling before it can measure lands on the
  // wrong offset, the same reason the ?page= effect gates on
  // heightsReady. Runs once per id, so a later scroll doesn't yank the
  // reader back.
  const deepLinkedId = searchParams.get("annotation");
  const handledDeepLink = useRef<string | null>(null);
  useEffect(() => {
    if (!deepLinkedId || handledDeepLink.current === deepLinkedId) return;
    if (!heightsReady) return;
    const target = (annotationsQuery.data ?? []).find(
      (a) => a.id === deepLinkedId,
    );
    if (!target) {
      // Still loading, or it was deleted since the link was made. Only
      // give up once the query has actually settled.
      if (annotationsQuery.isFetched) handledDeepLink.current = deepLinkedId;
      return;
    }
    handledDeepLink.current = deepLinkedId;
    goToPage(target.page_number);
    focusAnnotation(target.id);
  }, [
    deepLinkedId,
    heightsReady,
    annotationsQuery.data,
    annotationsQuery.isFetched,
    goToPage,
    focusAnnotation,
  ]);

  const jumpFromOutline = useCallback(
    (page: number) => {
      goToPage(page);
      if (isCoarsePointer) setOutlineOpen(false);
    },
    [goToPage, isCoarsePointer],
  );

  // Which find match the view last scrolled to. Shared by every page
  // so a remount does not re-scroll to it; see PageCanvas.
  const lastScrolledTo = useRef<string | null>(null);

  // Page renders run one at a time, nearest the viewport first. One
  // queue for the document, so the single pdfjs worker is never asked
  // for nine pages at once.
  const renderQueue = useRef(new RenderQueue()).current;
  // Shared pixel ceiling across every rendered page. Per-page clamping
  // bounds one canvas; how many are mounted is decided by the viewport,
  // not by anything about memory.
  const canvasBudget = useRef(new CanvasBudget()).current;
  // Read by the queue's priority function without re-subscribing it.
  const pageRef = useRef(page);
  pageRef.current = page;

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
          <button
            onClick={() => applyZoom(1)}
            aria-label="Reset zoom"
            title="Reset zoom to the fit scale"
            className="min-w-[3.5rem] rounded px-1 py-1 text-center text-xs tabular-nums hover:opacity-80"
            style={{ color: "var(--color-text-muted)" }}
          >
            {Math.round(zoom * 100)}%
          </button>
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

        <button
          onClick={() => {
            // Back to 1:1 so the new fit is what is actually seen; a
            // zoomed-in view would otherwise hide the change. Through
            // applyZoom, so it holds the reader's place like any other
            // change of scale.
            applyZoom(1);
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
                // Clear as well as close, like the X does. A query left
                // behind keeps its highlights on the page with no
                // visible control left to clear them.
                setFindOpen(false);
                setFindInput("");
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
        // Both axes: with layout zoom a zoomed page is genuinely
        // wider than the viewport, so the browser can scroll to the
        // rest of it. The transform model had to pan it by hand.
        className={`flex-1 overflow-auto p-4${
          (highlightsOpen || outlineOpen) && isCoarsePointer
            ? " hidden sm:block"
            : ""
        }`}
        style={{
          // `none` only while drag-select owns the touches; otherwise
          // the browser scrolls both ways and the pinch handler takes
          // two-finger gestures via preventDefault.
          // `pan-x pan-y` keeps native one-finger scrolling while
          // reserving multi-finger gestures for the pinch handler;
          // `auto` let the compositor claim them, which made
          // preventDefault a no-op and pinch-to-zoom dead.
          touchAction: selectMode ? "none" : "pan-x pan-y",
          overscrollBehavior: "contain",
          // Hold the scrollbar's space open. Without it, zooming past
          // the viewport width brings a scrollbar in, which shrinks the
          // container, which changes the fit scale, which can push the
          // content back under the width and take the scrollbar away
          // again -- a loop the reader sees as flicker.
          scrollbarGutter: "stable",
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
          <div ref={contentRef} style={{ width: "fit-content", margin: "0 auto" }}>
            <PageCanvas
              doc={doc}
              pageNumber={page}
              renderScale={pageScaleFor(page)}
              native={pageNativeRef.current.get(page)}
              findQuery={findQuery}
              currentOccurrence={
                currentMatchInfo && currentMatchInfo.page === page
                  ? currentMatchInfo.occurrence
                  : null
              }
              annotations={annotationsByPage.get(page) ?? []}
              focusedAnnotationId={focusedAnnotationId}
              debugText={debugText}
              onNativeSize={applyNativeSizeSingle}
              queue={renderQueue}
              budget={canvasBudget}
              pageRef={pageRef}
              pinchScaleRef={pinchScaleRef}
              lastScrolledTo={lastScrolledTo}
              onCreateHighlight={onCreateHighlight}
              onFollowLink={goToPage}
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
            scrollRef={scrollRef}
            estimateSize={estimateSize}
            rows={rows}
            rowScaleFor={rowScaleFor}
            pageNativeRef={pageNativeRef}
            contentRef={contentRef}
            findQuery={findQuery}
            currentMatchInfo={currentMatchInfo}
            annotationsByPage={annotationsByPage}
            focusedAnnotationId={focusedAnnotationId}
            debugText={debugText}
            queue={renderQueue}
            budget={canvasBudget}
            pageRef={pageRef}
            pinchScaleRef={pinchScaleRef}
            lastScrolledTo={lastScrolledTo}
            onCreateHighlight={onCreateHighlight}
            onFollowLink={goToPage}
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
            attachmentId={params.attachmentId!}
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
  attachmentId,
  isDeleting,
  onJumpTo,
  onDelete,
  onRecolor,
  onClose,
}: {
  annotations: Annotation[];
  attachmentId: string;
  isDeleting: boolean;
  onJumpTo: (a: Annotation) => void;
  onDelete: (id: string) => void;
  onRecolor: (id: string, color: string) => void;
  onClose: () => void;
}) {
  const [pickerOpenId, setPickerOpenId] = useState<string | null>(null);
  const [copiedId, setCopiedId] = useState<string | null>(null);

  const onCopyLink = useCallback(
    async (a: Annotation) => {
      const url = annotationLink(attachmentId, a.id, window.location.origin);
      try {
        await navigator.clipboard.writeText(url);
      } catch {
        // Clipboard access needs a secure context, so an instance
        // served over plain http has none. Fall back to selecting the
        // text in a prompt, which always works.
        window.prompt("Copy this link", url);
        return;
      }
      setCopiedId(a.id);
      setTimeout(() => setCopiedId((id) => (id === a.id ? null : id)), 1800);
    },
    [attachmentId],
  );
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
                    onClick={() => onCopyLink(a)}
                    aria-label="Copy link to highlight"
                    title={
                      copiedId === a.id
                        ? "Link copied"
                        : "Copy a link to this highlight"
                    }
                    className="rounded p-1 hover:opacity-70"
                    style={{
                      color:
                        copiedId === a.id
                          ? "var(--color-accent)"
                          : "var(--color-text-muted)",
                    }}
                  >
                    {copiedId === a.id ? (
                      <Check className="h-3.5 w-3.5" />
                    ) : (
                      <Link2 className="h-3.5 w-3.5" />
                    )}
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

/** Where the reader is, in terms that survive a change of scale: a row
 *  and how far into it the viewport's top sits. */
interface ScrollAnchor {
  index: number;
  within: number;
}

interface ContinuousListHandle {
  scrollToPage: (page: number) => void;
}

const ContinuousList = forwardRef<
  ContinuousListHandle,
  {
    doc: PDFDocumentProxy;
    scrollRef: React.RefObject<HTMLDivElement | null>;
    estimateSize: (index: number) => number;
    rows: number[][];
    rowScaleFor: (pages: number[]) => number;
    pageNativeRef: React.RefObject<Map<number, NativeViewport>>;
    contentRef: React.RefObject<HTMLDivElement | null>;
    findQuery: string;
    currentMatchInfo: { page: number; occurrence: number } | null;
    annotationsByPage: Map<number, Annotation[]>;
    focusedAnnotationId: string | null;
    debugText: boolean;
    queue: RenderQueue;
    budget: CanvasBudget;
    pageRef: React.RefObject<number>;
    pinchScaleRef: React.RefObject<number>;
    lastScrolledTo: React.RefObject<string | null>;
    onCreateHighlight: (
      pageNumber: number,
      rects: Rect[],
      text: string,
    ) => void;
    onFollowLink: (page: number) => void;
    onVisiblePageChange: (page: number) => void;
  }
>(function ContinuousList(
  {
    doc,
    scrollRef,
    estimateSize,
    rows,
    rowScaleFor,
    pageNativeRef,
    contentRef,
    findQuery,
    currentMatchInfo,
    annotationsByPage,
    focusedAnnotationId,
    debugText,
    queue,
    budget,
    pageRef,
    pinchScaleRef,
    lastScrolledTo,
    onCreateHighlight,
    onFollowLink,
    onVisiblePageChange,
  },
  ref,
) {
  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize,
    overscan: 3,
  });

  // A page telling us it is a different size than we assumed. Rare
  // after the first read of a document, so re-measuring here is cheap;
  // leaving it wrong is not, since every page below it sits at the
  // wrong offset.
  // Zoom changes every row's height, and the virtualizer caches what it
  // measured at the old one.
  //
  // This is two effects on purpose. The virtualizer memoises its
  // measurements on deps that do not include estimateSize, so a new
  // scale does not invalidate them: only measure() does, and measure()
  // schedules a re-render rather than updating the DOM in place.
  // Restoring the reader's place in this same commit would therefore
  // read the offsets it is about to replace — writing back roughly the
  // scroll position already there, just before the document changes
  // height underneath it. Zooming out then drifted further down the
  // document with every step.
  useLayoutEffect(() => {
    virtualizer.measure();
    // pageScaleFor closes over zoom, so estimateSize changing identity
    // is the signal that the scale moved.
  }, [virtualizer, estimateSize]);

  // Where the reader is, kept up to date as they scroll: a row, and how
  // far into it the viewport's top sits. A proportion, so it still
  // means the same thing once that row is a different height.
  const anchorRef = useRef<ScrollAnchor | null>(null);
  // Set while we are the ones moving the scroll, so the scroll event
  // that follows does not overwrite the anchor with the position we
  // just derived from it.
  const restoringRef = useRef(false);

  // ...and the restore waits for the commit that carries the new
  // heights, which is the first one where the total size differs.
  const totalSize = virtualizer.getTotalSize();
  useLayoutEffect(() => {
    const anchor = anchorRef.current;
    if (!anchor) return;
    const el = scrollRef.current;
    const offset = virtualizer.getOffsetForIndex(anchor.index, "start");
    if (!el || !offset) return;
    const size = virtualizer.measurementsCache[anchor.index]?.size ?? 0;
    const target = offset[0] + anchor.within * size;
    // Sub-pixel corrections are not worth a scroll write, and writing
    // one would only invite the rounding to accumulate.
    if (Math.abs(el.scrollTop - target) < 1) return;
    restoringRef.current = true;
    el.scrollTop = target;
    requestAnimationFrame(() => {
      restoringRef.current = false;
    });
  }, [totalSize, virtualizer, scrollRef]);

  const applyNativeSize = useCallback(
    (page: number, size: NativeViewport) => {
      pageNativeRef.current.set(page, size);
      virtualizer.measure();
    },
    [pageNativeRef, virtualizer],
  );

  useImperativeHandle(
    ref,
    () => ({
      scrollToPage: (page: number) => {
        virtualizer.scrollToIndex(rowOfPage(rows, page), { align: "start" });
      },
    }),
    [virtualizer, scrollRef, rows],
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
      if (!restoringRef.current) {
        // The row the viewport's top edge is inside, for putting the
        // reader back after anything changes the layout's height.
        const top =
          visible.find((v) => v.start <= offset && v.end > offset) ??
          visible[0];
        anchorRef.current = {
          index: top.index,
          within: (offset - top.start) / Math.max(1, top.size),
        };
      }
      let pick = visible[0].index;
      for (const v of visible) {
        if (v.start <= line) pick = v.index;
        else break;
      }
      // The first page of the row, which is the one a reader would
      // name if asked where they are.
      const page = rows[pick]?.[0] ?? pick + 1;
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
  }, [scrollRef, virtualizer, onVisiblePageChange, rows]);

  return (
    <div
      ref={contentRef}
      style={{
        // fit-content, not 100%: at a zoom past fit-width the pages are
        // wider than the viewport, and a 100% box would clip them
        // instead of giving the container something to scroll to.
        width: "fit-content",
        minWidth: "100%",
        position: "relative",
        height: `${virtualizer.getTotalSize()}px`,
      }}
    >
      {items.map((vi) => {
        const pages = rows[vi.index] ?? [];
        const scale = rowScaleFor(pages);
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
              gap: `${SPREAD_GAP}px`,
              // Top, so the two pages of a spread share a baseline even
              // when one is taller.
              alignItems: "flex-start",
              // `safe`: centred while it fits, start-aligned when it
              // does not. Plain centring pushes the inline-start
              // overflow outside the scrollable region, so at any zoom
              // past fit the left of every page was unreachable.
              justifyContent: "safe center",
            }}
          >
            {pages.map((pageNumber) => (
              <PageCanvas
                key={pageNumber}
                doc={doc}
                pageNumber={pageNumber}
                renderScale={scale}
                native={pageNativeRef.current.get(pageNumber)}
                findQuery={findQuery}
                currentOccurrence={
                  currentMatchInfo && currentMatchInfo.page === pageNumber
                    ? currentMatchInfo.occurrence
                    : null
                }
                annotations={annotationsByPage.get(pageNumber) ?? []}
                focusedAnnotationId={focusedAnnotationId}
                debugText={debugText}
                onNativeSize={applyNativeSize}
                queue={queue}
                budget={budget}
                pageRef={pageRef}
                pinchScaleRef={pinchScaleRef}
                lastScrolledTo={lastScrolledTo}
                onCreateHighlight={onCreateHighlight}
                onFollowLink={onFollowLink}
              />
            ))}
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
  native,
  findQuery,
  currentOccurrence,
  annotations,
  focusedAnnotationId,
  debugText,
  queue,
  budget,
  pageRef,
  pinchScaleRef,
  lastScrolledTo,
  onCreateHighlight,
  onFollowLink,
  onNativeSize,
}: {
  doc: PDFDocumentProxy;
  pageNumber: number;
  renderScale: number;
  native?: NativeViewport;
  findQuery: string;
  currentOccurrence: number | null;
  annotations: Annotation[];
  focusedAnnotationId: string | null;
  debugText: boolean;
  /** Serialises rendering across pages. */
  queue: RenderQueue;
  /** Shared pixel ceiling across every rendered page. */
  budget: CanvasBudget;
  /** The page in view, for queue priority. */
  pageRef: React.RefObject<number>;
  pinchScaleRef: React.RefObject<number>;
  /** Which match the reader last scrolled to, shared across pages: a
   *  virtualized list remounts them constantly, and a per-page ref
   *  would forget and scroll again on every remount. */
  lastScrolledTo: React.RefObject<string | null>;
  onCreateHighlight: (
    pageNumber: number,
    rects: Rect[],
    text: string,
  ) => void;
  /** Follow an internal link — scroll the reader to that page. */
  onFollowLink: (page: number) => void;
  /** The size pdfjs actually lays this page out at, reported once it is
   *  known. The estimate it replaces comes from the server, which may
   *  predate the CropBox/rotation fix, or from page 1 standing in for
   *  a document of mixed sizes. */
  onNativeSize?: (page: number, size: NativeViewport) => void;
}) {
  const wrapperRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const textLayerRef = useRef<HTMLDivElement>(null);
  const [textLayerVersion, setTextLayerVersion] = useState(0);
  // An unpainted canvas is a blank box, which in the dark theme reads
  // as a failure rather than as "not yet". Tracked so the placeholder
  // can say which page it is and that it is coming.
  const [painted, setPainted] = useState(false);
  // What is currently drawn. A re-run of the render effect that would
  // produce the same pixels is skipped: the list starting and stopping
  // is not a reason to redraw the page.
  const drawnKey = useRef<string | null>(null);

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

  // The PDF's own hyperlinks on this page. Independent of the canvas
  // render effect because it doesn't depend on scale — the rects come
  // back in PDF user-space and the overlay scales them itself, so a
  // zoom change re-lays-out the same links instead of re-reading them.
  const [links, setLinks] = useState<PageLink[]>([]);
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const pdfPage = await doc.getPage(pageNumber);
      try {
        if (cancelled) return;
        const found = await pageLinks(doc, pdfPage);
        if (!cancelled) setLinks(found);
      } finally {
        // Always, including the cancelled path. getPage resolves after
        // the cleanup has run, so without this every page scrolled past
        // during a drag keeps its worker-side resources.
        pdfPage.cleanup();
      }
    })().catch(() => {
      // A page whose annotations won't parse just has no links.
    });
    return () => {
      cancelled = true;
    };
  }, [doc, pageNumber]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const wrapper = wrapperRef.current;
    const textLayer = textLayerRef.current;
    if (!canvas || !wrapper || !textLayer) return;
    if (renderScale <= 0) return;

    let cancelled = false;
    let task: RenderTask | null = null;
    let pdfPage: PDFPageProxy | null = null;
    let textLayerTask: { cancel: () => void } | null = null;

    const unqueue = queue.push(
      `page-${pageNumber}`,
      // Distance from what is on screen, recomputed each time the queue
      // picks its next job.
      () => Math.abs(pageNumber - pageRef.current),
      async () => {
      if (cancelled) return;
      const wantKey = `${pageNumber}@${renderScale.toFixed(4)}`;
      if (drawnKey.current === wantKey) return;
      // getPage resolves after the cleanup has run, so the cleanup's
      // own pdfPage?.cleanup() sees null. Released here instead.
      const opened = await doc.getPage(pageNumber);
      pdfPage = opened;
      try {
      if (cancelled) return;
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      // pixelMultiplier puts more detail in the canvas's pixel
      // buffer than its CSS box demands. dpr handles HiDPI;
      // oversample handles "user has pinch-zoomed and now wants
      // crisp text". The CSS box stays at renderScale × native, so
      // the wrapper layout (and thus the virtualizer geometry) is
      // unaffected.
      //
      // Clamped, because all three multiply: renderScale is the
      // fit-width scale (~2 on a desktop container), dpr is up to 2,
      // and oversample up to MAX_OVERSAMPLE. Unclamped that reached
      // canvases of hundreds of megapixels -- past what a browser will
      // back, and a canvas it cannot back hands you a valid context
      // and paints nothing, which is the black page.
      // What pdfjs will actually lay this page out at. Compared
      // against the estimate driving the virtualizer, and reported when
      // they disagree by more than a rounding error -- a wrong estimate
      // means the scrollbar does not match the document.
      const trueNative = pdfPage.getViewport({ scale: 1 });
      if (
        onNativeSize &&
        (!native ||
          Math.abs(native.width - trueNative.width) > 1 ||
          Math.abs(native.height - trueNative.height) > 1)
      ) {
        onNativeSize(pageNumber, {
          width: trueNative.width,
          height: trueNative.height,
        });
      }

      const base = pdfPage.getViewport({ scale: renderScale });
      const budgetKey = `page-${pageNumber}`;
      const pixelMultiplier = budget.allow(
        budgetKey,
        clampPixelMultiplier(dpr, base.width, base.height),
        base.width,
        base.height,
      );
      const viewport = pdfPage.getViewport({
        scale: renderScale * pixelMultiplier,
      });

      // Drawn offscreen, then swapped in. Resizing a canvas clears it,
      // so rendering straight to the visible one blanks the page for
      // the length of the render -- which on a zoom step is a flash on
      // every page at once. Renders are serialised by the queue, so the
      // extra allocation is one canvas at a time rather than one per
      // mounted page, which is what made this too expensive before.
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
      } finally {
        if (cancelled) {
          // Let the abandoned buffer go now rather than at GC's leisure.
          offscreen.width = 0;
          offscreen.height = 0;
        }
      }
      if (cancelled) return;

      canvas.width = viewport.width;
      canvas.height = viewport.height;
      canvas.getContext("2d")?.drawImage(offscreen, 0, 0);
      offscreen.width = 0;
      offscreen.height = 0;
      drawnKey.current = wantKey;
      budget.set(budgetKey, canvas.width * canvas.height);
      setPainted(true);

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
      textLayerTask = layer;
      try {
        await layer.render();
        if (!cancelled) {
          // Only now is this page really drawn. Claiming it after the
          // canvas but before the text layer meant a re-run in between
          // was skipped by the guard, leaving the page with no text
          // layer at all -- no selection, no find highlights, and no
          // sign anything was wrong.
          drawnKey.current = wantKey;
          setTextLayerVersion((v) => v + 1);
        }
      } catch {
        // selection layer is best-effort
      }
      } finally {
        opened.cleanup();
        pdfPage = null;
      }
      },
    );

    return () => {
      cancelled = true;
      unqueue();
      task?.cancel();
      // Otherwise streamTextContent keeps flowing for a page nobody is
      // looking at any more.
      textLayerTask?.cancel();
      pdfPage?.cleanup();
    };
  }, [doc, pageNumber, renderScale, native, queue, budget, pageRef]);

  // Release the canvas when the page really goes away. Deliberately not
  // in the render effect's cleanup: that runs whenever its inputs
  // change, including when the list starts moving, and zeroing a canvas
  // there made every visible page blink on every scroll.
  useEffect(() => {
    const canvas = canvasRef.current;
    const key = `page-${pageNumber}`;
    return () => {
      budget.release(key);
      if (canvas && canvas.width > 0) {
        canvas.width = 0;
        canvas.height = 0;
      }
    };
  }, [pageNumber, budget]);

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
    if (!needle || textLayerVersion === 0) {
      // Nothing highlighted, so the next match to be drawn is worth
      // scrolling to even if it is the one we scrolled to last time --
      // searching the same word again should still take you there.
      lastScrolledTo.current = null;
      return;
    }

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

    // Only when the target actually moved. This effect also re-runs
    // whenever the text layer is rebuilt, which happens on any change
    // of render scale -- and closing the find bar resizes the scroll
    // container, so it re-rendered every page and then scrolled back to
    // the match the user had just finished with. Escaping out of a
    // search should leave you where you are reading.
    const target = `${needle}:${currentOccurrence}`;
    if (currentEl && lastScrolledTo.current !== target) {
      lastScrolledTo.current = target;
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
      {/* h-full w-full, not intrinsic size: the wrapper is sized
          from renderScale, so when the scale changes the pixels
          already drawn stretch to the new box immediately and are
          replaced by a crisp render when the queue gets to it.
          Without this the canvas kept its old size while its box
          changed, which is a visible jump on every zoom step. */}
      <canvas ref={canvasRef} className="absolute inset-0 h-full w-full" />
      {!painted && (
        <div
          className="pointer-events-none absolute inset-0 flex items-center justify-center gap-2 text-xs"
          style={{ color: "var(--color-text-muted)" }}
        >
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
          Page {pageNumber}
        </div>
      )}
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
      {native && cssH != null && links.length > 0 && (
        <LinkOverlay
          links={links}
          renderScale={renderScale}
          pageHeight={native.height}
          onFollowLink={onFollowLink}
        />
      )}
      {native && cssH != null && annotations.length > 0 && (
        <AnnotationOverlay
          annotations={annotations}
          focusedAnnotationId={focusedAnnotationId}
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

/**
 * The PDF's own hyperlinks, drawn over the page.
 *
 * Inert until Ctrl (or Cmd) is held — see `.shelf-links` in index.css
 * for why. External links open in a new tab; internal ones scroll the
 * reader.
 */
function LinkOverlay({
  links,
  renderScale,
  pageHeight,
  onFollowLink,
}: {
  links: PageLink[];
  renderScale: number;
  /** Native (scale=1) page height in PDF user-space; needed to flip
   *  the y-axis from PDF (origin bottom-left) to CSS (origin top). */
  pageHeight: number;
  onFollowLink: (page: number) => void;
}) {
  return (
    <div className="shelf-links">
      {links.map(({ rect: [x, y, w, h], page, url, label }, idx) => {
        const style = {
          left: `${x * renderScale}px`,
          top: `${(pageHeight - y - h) * renderScale}px`,
          width: `${w * renderScale}px`,
          height: `${h * renderScale}px`,
        };
        const title = `Ctrl+click to open — ${label}`;
        if (url) {
          return (
            <a
              key={idx}
              className="shelf-link"
              style={style}
              href={url}
              target="_blank"
              // noreferrer as well as noopener: an outbound link in an
              // uploaded PDF shouldn't learn which instance opened it.
              rel="noopener noreferrer"
              title={title}
              aria-label={title}
            />
          );
        }
        return (
          <button
            key={idx}
            type="button"
            className="shelf-link"
            style={style}
            title={title}
            aria-label={title}
            onClick={() => onFollowLink(page!)}
          />
        );
      })}
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
  focusedAnnotationId,
  renderScale,
  pageHeight,
}: {
  annotations: Annotation[];
  /** Ringed briefly after a deep link or a jump from the panel, so the
   *  eye lands on the passage rather than just the right page. */
  focusedAnnotationId: string | null;
  renderScale: number;
  /** Native (scale=1) page height in PDF user-space; needed to flip
   *  the y-axis from PDF (origin bottom-left) to CSS (origin top). */
  pageHeight: number;
}) {
  return (
    <div className="shelf-annotations">
      {annotations.map((a) => {
        const focused = a.id === focusedAnnotationId;
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
              className={
                "shelf-annotation-note" +
                (focused ? " shelf-annotation-focused" : "")
              }
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
              className={
                "shelf-annotation-rect" +
                (focused ? " shelf-annotation-focused" : "")
              }
              style={{
                left: `${cssX}px`,
                top: `${cssY}px`,
                width: `${w * renderScale}px`,
                height: `${h * renderScale}px`,
                backgroundColor: a.color,
                opacity: focused ? 0.65 : 0.45,
              }}
              title={a.text ?? ""}
            />
          );
        });
      })}
    </div>
  );
}
