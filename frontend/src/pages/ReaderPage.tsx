import {
  useCallback,
  useEffect,
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
  ChevronLeft,
  ChevronRight,
  Loader2,
  Search,
  X,
} from "lucide-react";
import type { PDFDocumentProxy } from "pdfjs-dist";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
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
import {
  PREF_READER_FIT,
  PREF_READER_MODE,
  PREF_READER_SPREAD,
  readScrollMode,
  usePref,
} from "@/auth/prefs";
import { useDebounce } from "@/hooks/useDebounce";
import {
  clampZoom,
  useZoomGestures,
} from "@/hooks/useZoomGestures";
import { CanvasBudget } from "@/lib/canvasBudget";
import { RenderQueue } from "@/lib/renderQueue";
import { pageRows, rowOfPage } from "@/lib/spreads";
import { PageCanvas } from "@/components/reader/PageCanvas";
import { ContinuousList } from "@/components/reader/ContinuousList";
import { HighlightsPanel } from "@/components/reader/HighlightsPanel";
import { ReaderToolbar } from "@/components/reader/ReaderToolbar";
import {
  ESTIMATE_PAGE_HEIGHT,
  PAGE_GAP,
  SCROLL_PADDING,
  SPREAD_GAP,
  type ContinuousListHandle,
  type NativeViewport,
} from "@/components/reader/types";
import { PageThumbnails } from "@/lib/pageThumbnails";
import { ReaderSidebar } from "@/components/reader/ReaderSidebar";
import type { LayerGroup } from "@/components/reader/LayersPanel";

/**
 * pdfjs's optional-content config, taken from the method that returns
 * it — the class itself is not exported from the package root.
 */
type OcConfig = Awaited<
  ReturnType<PDFDocumentProxy["getOptionalContentConfig"]>
>;

/**
 * CSS pixels per PDF point at pdf.js's scale 1, which is what its
 * viewer calls "actual size" and what 100% means in its zoom menu.
 */
const PDF_TO_CSS_UNITS = 4 / 3;

/** Page-width, but not blown up past this on a narrow document. */
const MAX_AUTO_SCALE = 1.25;

// p-4 padding on the scroll container = 16px each side, 32px total
// horizontal — pages render to fit the inner content width.


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
  const [storedMode, setStoredMode] = usePref(PREF_READER_MODE);
  const scrollMode = readScrollMode(storedMode);
  const setMode = setStoredMode;
  // Page mode shows one band at a time; the rest scroll a list of them.
  const paged = scrollMode === "page";
  const horizontal = scrollMode === "horizontal";
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
  // The editing tool in hand, named as pdf.js names them. Null is
  // reading. Draw is absent for now: ink would be a new annotation
  // kind rather than a new way to make one that already exists.
  const [tool, setTool] = useState<"highlight" | "text" | null>(null);
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
    if (paged) return;
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
  }, [paged, heightsReady, numPages, searchParams, location.key]);

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
  // Read by the page-stepping callbacks, which are defined above this
  // and should not be rebuilt every time the layout changes.
  const rowsRef = useRef(rows);
  rowsRef.current = rows;


  /**
   * The scale a row renders at.
   *
   * Per row rather than per page, because a facing pair has to fit the
   * width between them — so a spread is drawn at roughly half the scale
   * a lone page would be, which is what makes it a spread rather than
   * two pages overflowing. Fit-page measures the tallest page in the
   * row, since that is the one that has to clear the viewport.
   */
  const fitScaleFor = useCallback(
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
        if (innerH > 0) return Math.min(widthScale, innerH / maxH);
      }
      return widthScale;
    },
    [containerSize.width, containerSize.height, fit],
  );

  const rowScaleFor = useCallback(
    (pages: number[]): number => fitScaleFor(pages) * zoom,
    [fitScaleFor, zoom],
  );

  /**
   * The zoom that would put the page in view at a given size on screen,
   * where 1 is pdf.js's "actual size" — its scale 1, which is 4/3 of a
   * PDF point per CSS pixel.
   *
   * Zoom here multiplies the fit scale, so a percentage has to be
   * converted through whatever the window is currently making the page:
   * the same 100% is a different multiplier on a narrow window than on
   * a wide one, which is the point of it meaning a size.
   */
  const zoomForAbsolute = useCallback(
    (absolute: number): number => {
      const here = rows[rowOfPage(rows, page)] ?? [page];
      const fitScale = fitScaleFor(here);
      if (!(fitScale > 0)) return 1;
      return (absolute * PDF_TO_CSS_UNITS) / fitScale;
    },
    [fitScaleFor, rows, page],
  );

  /** What the current zoom works out to as a size on screen. */
  const absoluteZoom = useMemo(() => {
    const here = rows[rowOfPage(rows, page)] ?? [page];
    return (fitScaleFor(here) * zoom) / PDF_TO_CSS_UNITS;
  }, [fitScaleFor, rows, page, zoom]);

  // Rows grouped into the bands the list scrolls through. One row per
  // band everywhere except wrapped, which fits as many across as the
  // width allows.
  const bands = useMemo(() => {
    if (scrollMode !== "wrapped") return rows.map((r) => [r]);
    const out: number[][][] = [];
    let line: number[][] = [];
    let used = 0;
    const room = Math.max(1, containerSize.width - SCROLL_PADDING);
    for (const row of rows) {
      const width =
        row.reduce(
          (sum, n) => sum + (pageNativeRef.current.get(n)?.width ?? 0),
          0,
        ) *
          rowScaleFor(row) +
        (row.length - 1) * SPREAD_GAP;
      if (line.length > 0 && used + width + SPREAD_GAP > room) {
        out.push(line);
        line = [];
        used = 0;
      }
      line.push(row);
      used += width + SPREAD_GAP;
    }
    if (line.length > 0) out.push(line);
    return out;
  }, [rows, scrollMode, containerSize.width, rowScaleFor]);

  const estimateSize = useCallback(
    (index: number) => {
      const band = bands[index];
      if (!band || band.length === 0) return ESTIMATE_PAGE_HEIGHT + PAGE_GAP;
      if (horizontal) {
        // Along the scroll axis a band measures its width.
        const row = band[0];
        const natives = row.map((n) => pageNativeRef.current.get(n));
        if (natives.some((n) => !n)) return ESTIMATE_PAGE_HEIGHT + PAGE_GAP;
        const total = natives.reduce((sum, n) => sum + n!.width, 0);
        return (
          total * rowScaleFor(row) + (row.length - 1) * SPREAD_GAP + PAGE_GAP
        );
      }
      const tallest = Math.max(
        ...band.map((row) =>
          Math.max(
            ...row.map(
              (n) =>
                (pageNativeRef.current.get(n)?.height ?? 0) * rowScaleFor(row),
            ),
          ),
        ),
      );
      return (tallest || ESTIMATE_PAGE_HEIGHT) + PAGE_GAP;
    },
    [bands, horizontal, rowScaleFor],
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
      // A step is a band, not a page: with spreads on, stepping one
      // page would leave the same pair on screen with a different one
      // marked current. Both sides move, which is what the arrows
      // either side of the page number look like they should do.
      const at = rowOfPage(rowsRef.current, p);
      const n = rowsRef.current[Math.max(0, at - 1)]?.[0] ?? Math.max(1, p - 1);
      if (!paged) {
        continuousRef.current?.scrollToPage(n);
      }
      return n;
    });
  }, [paged]);

  const goNext = useCallback(() => {
    setPage((p) => {
      if (!numPages) return p;
      const at = rowOfPage(rowsRef.current, p);
      const n = Math.min(
        numPages,
        rowsRef.current[Math.min(rowsRef.current.length - 1, at + 1)]?.[0] ??
          p + 1,
      );
      if (!paged) {
        continuousRef.current?.scrollToPage(n);
      }
      return n;
    });
  }, [numPages, paged]);

  function jumpToMatch(idx: number): void {
    if (matches.length === 0) return;
    const wrapped = ((idx % matches.length) + matches.length) % matches.length;
    setCurrentMatch(wrapped);
    const target = matches[wrapped];
    setPage(target.page);
    if (!paged) {
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
      // Escape closes the find bar from anywhere, not only from the
      // field: after clicking into the document to read a hit, the bar
      // is still open and the key that should dismiss it did nothing,
      // because focus had moved off the input that was listening.
      // Checked before the input guard so it works in the field too,
      // which is where the field's own handler stops mattering.
      if (e.key === "Escape" && findOpen) {
        e.preventDefault();
        setFindOpen(false);
        setFindInput("");
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
  }, [goPrev, goNext, nav, findOpen]);

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
    // A wheel or a button leaves the named modes behind: the reader
    // asked for a size, not for "whatever fits".
    setZoomChoice("custom");
  }, []);

  // Which entry of the zoom menu is selected. "custom" is anything
  // reached by wheel or button, shown as its own percentage rather than
  // snapped to the nearest preset.
  const [zoomChoice, setZoomChoice] = useState<string>("page-width");
  const isNamedZoom =
    zoomChoice === "auto" ||
    zoomChoice === "page-fit" ||
    zoomChoice === "page-width" ||
    zoomChoice === "page-actual";

  /**
   * The zoom menu, in pdf.js's terms.
   *
   * The named modes are the two fits plus actual size; everything else
   * is a size on screen, which has to be converted through the current
   * fit scale because zoom here is a multiple of it.
   */
  const applyZoomChoice = useCallback(
    (choice: string) => {
      setZoomChoice(choice);
      switch (choice) {
        case "page-width":
          setFit("width");
          setZoom(1);
          return;
        case "page-fit":
          setFit("page");
          setZoom(1);
          return;
        case "auto":
          // Page width, but a narrow document is not blown up past
          // legibility — the same cap pdf.js puts on it.
          setFit("width");
          setZoom(
            clampZoom(Math.min(1, zoomForAbsolute(MAX_AUTO_SCALE))),
          );
          return;
        case "page-actual":
          setFit("width");
          setZoom(clampZoom(zoomForAbsolute(1)));
          return;
        default: {
          const absolute = Number(choice);
          if (!Number.isFinite(absolute)) return;
          setFit("width");
          setZoom(clampZoom(zoomForAbsolute(absolute)));
        }
      }
    },
    [zoomForAbsolute, setFit],
  );

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
    mutationFn: (input: {
      pageNumber: number;
      rects: Rect[];
      text: string;
      kind?: "highlight" | "note";
    }) =>
      createAnnotation(params.attachmentId!, {
        kind: input.kind ?? "highlight",
        page_number: input.pageNumber,
        rects: input.rects,
        text: input.text,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["annotations", params.attachmentId] });
    },
  });

  const mutateHighlight = createHighlight.mutate;
  /**
   * Leave a note at a point on a page.
   *
   * Shelf's own annotation, not one written into the PDF: it carries
   * visibility, an author and a link, which a note baked into the file
   * could not.
   */
  const mutateNote = createHighlight.mutate;
  const createNote = useCallback(
    (pageNumber: number, x: number, y: number) => {
      const text = window.prompt("Note");
      if (text === null || !text.trim()) return;
      mutateNote({
        pageNumber,
        rects: [[x, y, 0, 0]],
        text: text.trim(),
        kind: "note",
      });
    },
    [mutateNote],
  );

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
      if (!paged) {
        // Deferred past the setPage render flush so the virtualizer has
        // the current geometry. Zoom no longer enters into it: the
        // layout is the zoom, so scrollToIndex is always in the same
        // coordinates the user sees.
        setTimeout(() => {
          continuousRef.current?.scrollToPage(n);
        }, 0);
      }
    },
    [paged],
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

  /**
   * The PDF's layers, and the config the renderer draws them by.
   *
   * The config object is pdfjs's own and is mutated in place by
   * setVisibility, so it cannot be React state — nothing about it
   * changes identity. `layerVersion` is what tells the pages to draw
   * again, and it is part of each page's drawn-key so a toggle
   * invalidates what is already on screen.
   */
  const ocConfigRef = useRef<OcConfig | null>(null);
  const [layers, setLayers] = useState<LayerGroup[] | undefined>(undefined);
  const [layerVersion, setLayerVersion] = useState(0);

  useEffect(() => {
    if (!doc) return;
    let cancelled = false;
    doc
      .getOptionalContentConfig()
      .then((config) => {
        if (cancelled) return;
        ocConfigRef.current = config;
        // The config is iterable over [id, group]; there is no
        // getGroups(), and getOrder() is about display order rather
        // than membership.
        setLayers(
          [...config].map(([id, group]) => ({
            id: String(id),
            name:
              (group as { name?: string }).name?.trim() || "Unnamed layer",
            visible: (group as { visible?: boolean }).visible !== false,
          })),
        );
      })
      .catch(() => {
        if (!cancelled) setLayers([]);
      });
    return () => {
      cancelled = true;
    };
  }, [doc]);

  const toggleLayer = useCallback((id: string, visible: boolean) => {
    const config = ocConfigRef.current;
    if (!config) return;
    config.setVisibility(id, visible);
    setLayers((prev) =>
      prev?.map((l) => (l.id === id ? { ...l, visible } : l)),
    );
    setLayerVersion((v) => v + 1);
  }, []);

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
  // A thumbnail of every page seen so far, so a page returning to view
  // has something to show while its real render is queued. Cleared with
  // the document.
  const thumbnails = useRef(new PageThumbnails()).current;
  useEffect(() => {
    return () => thumbnails.clear();
  }, [doc, thumbnails]);
  // Read by the queue's priority function without re-subscribing it.
  const pageRef = useRef(page);
  pageRef.current = page;

  const currentMatchInfo = matches[currentMatch] ?? null;

  return (
    <div
      className="flex h-dvh flex-col"
      style={{ backgroundColor: "var(--color-bg)" }}
    >
      <ReaderToolbar
        navigation={{
          onBack: () => nav(-1),
          page,
          numPages,
          goPrev,
          goNext,
          setPage: goToPage,
          outlineOpen,
          setOutlineOpen,
          findOpen,
          setFindOpen,
        }}
        view={{
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
        }}
        marks={{
          tool,
          setTool,
          highlightsOpen,
          setHighlightsOpen,
          annotationCount: annotationsSorted.length,
          attachmentId: params.attachmentId,
          debugText,
          setDebugText,
          selectMode,
          setSelectMode,
          isCoarsePointer,
        }}
      />

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
              // Escape is handled at the window, so it works whether
              // or not this field has focus; it would only be a second
              // copy of the same thing here.
              if (e.key === "Enter") {
                e.preventDefault();
                jumpToMatch(currentMatch + (e.shiftKey ? -1 : 1));
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
        {/* Before the scroll area, so the drawer opens on the side the
            document is read from rather than against the far edge. */}
        {outlineOpen && (
          <ReaderSidebar
            doc={doc}
            numPages={numPages}
            currentPage={page}
            onJumpTo={jumpFromOutline}
            onClose={() => setOutlineOpen(false)}
            queue={renderQueue}
            thumbnails={thumbnails}
            layers={layers}
            onToggleLayer={toggleLayer}
          />
        )}
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
        {doc && paged && (
          <div
            ref={contentRef}
            style={{
              width: "fit-content",
              margin: "0 auto",
              display: "flex",
              gap: `${SPREAD_GAP}px`,
              alignItems: "flex-start",
            }}
          >
            {(rows[rowOfPage(rows, page)] ?? [page]).map((n) => (
              <PageCanvas
                key={n}
                doc={doc}
                pageNumber={n}
                renderScale={rowScaleFor(rows[rowOfPage(rows, page)] ?? [n])}
                native={pageNativeRef.current.get(n)}
                findQuery={findQuery}
                currentOccurrence={
                  currentMatchInfo && currentMatchInfo.page === n
                    ? currentMatchInfo.occurrence
                    : null
                }
                annotations={annotationsByPage.get(n) ?? []}
                focusedAnnotationId={focusedAnnotationId}
                debugText={debugText}
                onNativeSize={applyNativeSizeSingle}
                queue={renderQueue}
                budget={canvasBudget}
                thumbnails={thumbnails}
                ocConfigRef={ocConfigRef}
                layerVersion={layerVersion}
                tool={tool}
                onCreateNote={createNote}
                pageRef={pageRef}
                pinchScaleRef={pinchScaleRef}
                lastScrolledTo={lastScrolledTo}
                onCreateHighlight={onCreateHighlight}
                onFollowLink={goToPage}
              />
            ))}
          </div>
        )}
        {doc && !paged && (
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
            bands={bands}
            horizontal={horizontal}
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
            thumbnails={thumbnails}
            ocConfigRef={ocConfigRef}
            layerVersion={layerVersion}
            tool={tool}
            onCreateNote={createNote}
            pageRef={pageRef}
            pinchScaleRef={pinchScaleRef}
            lastScrolledTo={lastScrolledTo}
            onCreateHighlight={onCreateHighlight}
            onFollowLink={goToPage}
            onVisiblePageChange={setPage}
          />
        )}
      </div>
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
