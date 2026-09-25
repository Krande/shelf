import { useEffect, useMemo, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { useNavigate } from "react-router";
import { useQueries, useQuery } from "@tanstack/react-query";
import {
  BookOpen,
  Check,
  ChevronDown,
  ChevronRight,
  Info,
  LibraryBig,
  Loader2,
  Search,
  Settings,
} from "lucide-react";
import { useAuth } from "@/auth/session";
import {
  ALL_SEARCH_SCOPES,
  fetchFulltextHits,
  type FulltextHitsAttachment,
  searchMyItems,
  SEARCH_SCOPE_LABELS,
  type Item,
  type SearchScope,
} from "@/api/items";
import { listAttachments } from "@/api/attachments";
import { fetchMySpaces } from "@/api/spaces";
import SearchScopePopover from "@/components/library/SearchScopePopover";
import SearchSpacePopover from "@/components/library/SearchSpacePopover";
import ShortcutsHelp from "@/components/layout/ShortcutsHelp";

/**
 * Minimal landing page — centered search box, a couple of jump-off
 * links underneath. Toggleable via the "Show landing page on sign-in"
 * setting (auth/prefs.ts). When the pref is off, App.tsx redirects
 * `/` to `/library` and this page is reachable only via direct URL.
 *
 * As the user types, a dropdown shows the top title hits. Picking one
 * opens its PDF — at the matching page when the row is a full-text
 * snippet — because a search result is a thing to read, not a record to
 * inspect. Info on each row still opens the item's detail panel in
 * /library, and Shift does the same from the keyboard. Title-only scope
 * keeps the query cheap so the dropdown feels instant.
 *
 * The search spans **every space the user can read**, not just their own
 * shelf: the question here is "where is that document", and the answer is
 * often in a space someone shared or one their shelf subscribes to. It is
 * one request (`searchMyItems`) rather than one per space, which is also
 * what keeps a document reachable two ways from being listed twice. The
 * space popover beside the field takes libraries back out of the search —
 * a ten-thousand-document Standards space is noise when you are after a
 * project drawing. Rows say which space they came from when it isn't the
 * user's own shelf.
 */
export default function HomePage() {
  const auth = useAuth();
  const nav = useNavigate();
  const [q, setQ] = useState("");
  // Same semantics as LibraryPage: empty array = "narrowed past
  // every scope" (zero results), full array = "no scope= param,
  // i.e. all fields". State here is local until submit, when it's
  // serialised onto the /library URL.
  const [scope, setScope] = useState<SearchScope[]>(() => [
    ...ALL_SEARCH_SCOPES,
  ]);
  const narrowed = useMemo(
    () =>
      scope.length === 0 || scope.length < ALL_SEARCH_SCOPES.length,
    [scope],
  );

  // Every space this search can cover: the user's own shelf, spaces
  // shared with them, and the ones those subscribe to. Same query key
  // LibraryPage uses for the wider list, so the two share a cache —
  // and deliberately not ["spaces"], which feeds the space switcher and
  // must stay a list of places you can *work*.
  const spaces = useQuery({
    queryKey: ["spaces", "with-inherited"],
    queryFn: () => fetchMySpaces({ includeInherited: true }),
  });
  const allSpaces = useMemo(() => spaces.data ?? [], [spaces.data]);
  const personal = useMemo(
    () => allSpaces.find((s) => s.is_personal) ?? allSpaces[0] ?? null,
    [allSpaces],
  );
  // null = "not narrowed", which is different from "every slug selected":
  // it survives a space being added or unshared while the page is open,
  // and keeps `space=` off the query until the user actually filters.
  const [spaceFilter, setSpaceFilter] = useState<string[] | null>(null);
  const selectedSpaces = useMemo(
    () => spaceFilter ?? allSpaces.map((s) => s.slug),
    [spaceFilter, allSpaces],
  );
  // Sent to the API only once narrowed. Sorted into space-list order by
  // the popover, so the query key is stable across click orders.
  const spacesParam = spaceFilter === null ? undefined : selectedSpaces;
  const spaceNames = useMemo(() => {
    const m = new Map<string, string>();
    for (const s of allSpaces) m.set(s.id, s.name);
    return m;
  }, [allSpaces]);

  // Enough spaces are loaded to search, and the user hasn't filtered
  // every one of them out.
  const canSearch = allSpaces.length > 0 && selectedSpaces.length > 0;

  // Live title-only suggestions. No debounce — the title-scope query
  // hits the JSONB title via ilike with a small limit, and the
  // round-trip is cheap enough that every keystroke feels snappy.
  // Trimmed query under 2 chars is skipped to avoid full-table scans.
  const trimmed = q.trim();
  const suggestions = useQuery({
    queryKey: ["home-suggest", spacesParam ?? "all", trimmed],
    queryFn: () =>
      searchMyItems({
        q: trimmed,
        spaces: spacesParam,
        scope: ["title"],
        limit: 8,
        sort: "updated",
        direction: "desc",
      }),
    enabled: canSearch && trimmed.length >= 2,
    staleTime: 30_000,
  });

  // Per-scope probes. These run alongside the title query so the
  // dropdown can show "creators is still searching, abstract found
  // 3 (here they are), fulltext found 14 (here are the top 4)" while
  // the user is still mid-keystroke. Fulltext hits a heavier index
  // than the others, so we debounce all of these by a beat to avoid
  // hammering the backend on every keystroke. Title is excluded —
  // those rows are already shown above the status strip.
  const COUNT_SCOPES: SearchScope[] = useMemo(
    () => ["creators", "abstract", "extra", "fulltext"],
    [],
  );
  // Items per scope section. Matches the backend's max page size so
  // the dropdown can show every match the API will hand us in one
  // call; the dropdown's own max-height + overflow-y-auto handles
  // long lists. Beyond 200 we surface the rest via the "see all"
  // link on the section header.
  const PER_SCOPE_LIMIT = 200;
  const [debouncedQ, setDebouncedQ] = useState("");
  useEffect(() => {
    const t = setTimeout(() => setDebouncedQ(trimmed), 250);
    return () => clearTimeout(t);
  }, [trimmed]);
  const scopeCounts = useQueries({
    queries: COUNT_SCOPES.map((s) => ({
      queryKey: [
        "home-scope",
        spacesParam ?? "all",
        debouncedQ,
        s,
        PER_SCOPE_LIMIT,
      ],
      queryFn: () =>
        searchMyItems({
          q: debouncedQ,
          spaces: spacesParam,
          scope: [s],
          limit: PER_SCOPE_LIMIT,
          sort: "updated" as const,
          direction: "desc" as const,
        }),
      enabled: canSearch && debouncedQ.length >= 2,
      staleTime: 30_000,
    })),
  });

  // -1 means "no row highlighted, focus is conceptually on the input".
  // Up/Down move through the suggestions; Left or Up-past-the-top
  // returns to -1 so the next keystroke types into the input again.
  // Declared up here (above the early return) so the hook order stays
  // stable when auth transitions from loading → authenticated.
  const [activeIndex, setActiveIndex] = useState(-1);
  const rowRefs = useRef<Array<HTMLDivElement | null>>([]);

  // Per-item expansion state for the fulltext section. Same shape as
  // LibraryPage.expandedFulltextIds — when an item is expanded we
  // fetch its per-page PDF hits and surface them as navigable rows
  // beneath the item. Right-arrow expands, left-arrow collapses.
  const [expandedFulltextIds, setExpandedFulltextIds] = useState<Set<string>>(
    () => new Set(),
  );

  // Touch-primary devices can't issue Shift+click, so a plain tap on
  // a snippet jumps straight to the PDF page — opening the library
  // detail panel and asking the user to find the right snippet again
  // would defeat the point of drilling in. Same detection ReaderPage
  // uses for its mobile UX branches.
  const isCoarsePointer = useMemo(
    () =>
      typeof window !== "undefined" &&
      typeof window.matchMedia === "function" &&
      window.matchMedia("(pointer: coarse)").matches,
    [],
  );

  // Reset highlight + expansion whenever the suggestion list changes
  // so we never point past the end of a freshly fetched result set
  // and stale per-item hit data doesn't bleed across queries.
  useEffect(() => {
    setActiveIndex(-1);
    setExpandedFulltextIds(new Set());
  }, [trimmed]);

  // Keep the active row in view even when it lives in a scope section
  // below the status strip, so ArrowDown past the visible area still
  // tracks the cursor.
  useEffect(() => {
    if (activeIndex >= 0) {
      rowRefs.current[activeIndex]?.scrollIntoView({ block: "nearest" });
    }
  });

  if (auth.status !== "authenticated") return null;

  /**
   * The library is one space at a time, so a cross-space search can only
   * hand it a space when the filter names exactly one. Otherwise it opens
   * where it always does — the personal shelf — and the dropdown stays
   * the place the wider result set lives.
   */
  function spaceParam(): string | null {
    return selectedSpaces.length === 1 ? selectedSpaces[0] : null;
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!trimmed) {
      nav("/library");
      return;
    }
    const params = new URLSearchParams();
    params.set("q", trimmed);
    const one = spaceParam();
    if (one) params.set("space", one);
    if (narrowed) {
      if (scope.length === 0) {
        // Sentinel kept in lock-step with LibraryPage's
        // setSearchScope: encodes "search nothing" so a reload
        // doesn't silently widen the query.
        params.append("scope", "none");
      } else {
        for (const s of scope) params.append("scope", s);
      }
    }
    nav(`/library?${params.toString()}`);
  }

  async function openInReader(item: Item) {
    try {
      const atts = await listAttachments(item.id);
      const pdf = atts.find((a) => a.content_type === "application/pdf");
      if (pdf) {
        nav(`/reader/${encodeURIComponent(pdf.id)}`);
        return;
      }
    } catch {
      // Fall through to the library detail panel — at least the
      // user gets to the item, even if there's no PDF attached.
    }
    nav(`/library?item=${encodeURIComponent(item.id)}`);
  }

  function openInLibrary(item: Item) {
    nav(`/library?item=${encodeURIComponent(item.id)}`);
  }

  const items = suggestions.data?.items ?? [];
  const anyScopeActivity = scopeCounts.some(
    (qr) => qr.isFetching || (qr.data?.items.length ?? 0) > 0,
  );
  const showDropdown =
    trimmed.length >= 2 &&
    (suggestions.isFetching || items.length > 0 || anyScopeActivity);

  // De-dupe scope results against title hits and against earlier
  // scopes, so the user doesn't see the same row twice when a title
  // match also happens to mention the search term in its abstract.
  // We compute per-scope data even for empty/duplicate-only scopes so
  // the status-strip chips can report the same numbers users see in
  // the section headers below (chips and sections would otherwise
  // disagree when dedupe drops rows).
  type ScopeData = {
    scope: SearchScope;
    label: string;
    items: Item[];
    /** API-reported total for the scope (pre-dedupe). */
    apiTotal: number;
    /** Items the API returned in this call (pre-dedupe). */
    fetched: number;
  };
  const scopeData = useMemo<ScopeData[]>(() => {
    const seen = new Set(items.map((it) => it.id));
    return COUNT_SCOPES.map((scope, i) => {
      const data = scopeCounts[i].data;
      const fresh: Item[] = [];
      if (data) {
        for (const it of data.items) {
          if (seen.has(it.id)) continue;
          fresh.push(it);
          seen.add(it.id);
        }
      }
      return {
        scope,
        label: SEARCH_SCOPE_LABELS[scope],
        items: fresh,
        apiTotal: data?.total ?? 0,
        fetched: data?.items.length ?? 0,
      };
    });
    // scopeCounts is a fresh array every render — use the underlying
    // data refs (which only change when a query resolves) as the
    // memo signal.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items, ...scopeCounts.map((qr) => qr.data)]);
  const sections = useMemo(
    () => scopeData.filter((s) => s.items.length > 0),
    [scopeData],
  );

  // Lazy-fetch hits for each expanded fulltext-section item. The list
  // of expanded ids is intersected with the current fulltext-section
  // items so a stale id (kept from before sections re-resolved) maps
  // to no query — its useQueries slot just disappears. Same query key
  // shape as FulltextHitsRow so the library and home views share the
  // React Query cache for free.
  const fulltextSection = useMemo(
    () => sections.find((s) => s.scope === "fulltext") ?? null,
    [sections],
  );
  const expandedItemIds = useMemo(() => {
    const ids = fulltextSection?.items.map((it) => it.id) ?? [];
    return ids.filter((id) => expandedFulltextIds.has(id));
  }, [fulltextSection, expandedFulltextIds]);
  const hitsQueries = useQueries({
    queries: expandedItemIds.map((id) => ({
      queryKey: ["fulltext-hits", id, debouncedQ],
      queryFn: () => fetchFulltextHits(id, debouncedQ),
      enabled: debouncedQ.length >= 2,
      staleTime: 60_000,
    })),
  });
  const hitsByItemId = useMemo(() => {
    const m = new Map<
      string,
      { data: FulltextHitsAttachment[] | undefined; loading: boolean }
    >();
    expandedItemIds.forEach((id, i) => {
      m.set(id, {
        data: hitsQueries[i]?.data,
        loading: hitsQueries[i]?.isFetching && !hitsQueries[i]?.data,
      });
    });
    return m;
    // hitsQueries is a fresh array every render; track the underlying
    // refs the same way scopeData does above.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    expandedItemIds,
    ...hitsQueries.map((q) => q.data),
    ...hitsQueries.map((q) => q.isFetching),
  ]);

  // Flat list the keyboard nav traverses: title rows first, then for
  // each scope section its item rows, with expanded-fulltext snippet
  // rows interleaved beneath their parent item. Section chrome (the
  // headers, scope chips, attachment-filename labels) is non-navigable.
  type ItemNavEntry = {
    kind: "item";
    item: Item;
    /** Whether this row is in the fulltext section — only those rows
     *  are expandable. */
    isFulltext: boolean;
  };
  type SnippetNavEntry = {
    kind: "snippet";
    /** The parent fulltext-section item, so Enter (no shift) still
     *  routes to the item's library detail like other rows. */
    item: Item;
    attachmentId: string;
    filename: string;
    pageNumber: number;
    snippetHtml: string;
  };
  type NavEntry = ItemNavEntry | SnippetNavEntry;
  const navEntries = useMemo<NavEntry[]>(() => {
    const entries: NavEntry[] = [];
    for (const it of items) entries.push({ kind: "item", item: it, isFulltext: false });
    for (const section of sections) {
      const isFulltextSection = section.scope === "fulltext";
      for (const it of section.items) {
        entries.push({ kind: "item", item: it, isFulltext: isFulltextSection });
        if (isFulltextSection && expandedFulltextIds.has(it.id)) {
          const hits = hitsByItemId.get(it.id)?.data;
          if (hits) {
            for (const att of hits) {
              for (const hit of att.hits) {
                entries.push({
                  kind: "snippet",
                  item: it,
                  attachmentId: att.attachment_id,
                  filename: att.filename,
                  pageNumber: hit.page_number,
                  snippetHtml: hit.snippet_html,
                });
              }
            }
          }
        }
      }
    }
    return entries;
  }, [items, sections, expandedFulltextIds, hitsByItemId]);

  function jumpToScope(s: SearchScope) {
    // This used to pre-seed the library's infinite-query cache from the
    // probe already run here, saving a round trip. It can't any more: the
    // probe spans every readable space and the cache it would seed
    // belongs to a single-space listing, so the library would render
    // other spaces' items as if they were its own. A refetch on arrival
    // is the honest trade.
    const params = new URLSearchParams();
    params.set("q", trimmed);
    params.append("scope", s);
    const one = spaceParam();
    if (one) params.set("space", one);
    nav(`/library?${params.toString()}`);
  }

  function toggleFulltextExpanded(id: string) {
    setExpandedFulltextIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function onInputKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (!showDropdown || navEntries.length === 0) return;
    const entry = activeIndex >= 0 ? navEntries[activeIndex] : null;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIndex((i) => (i + 1 >= navEntries.length ? 0 : i + 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIndex((i) => (i <= 0 ? -1 : i - 1));
    } else if (e.key === "ArrowRight") {
      // Right-arrow expands an unexpanded fulltext item. On a snippet
      // or an already-expanded item it's a no-op (no horizontal nav
      // model beyond a single level of children).
      if (entry?.kind === "item" && entry.isFulltext) {
        if (!expandedFulltextIds.has(entry.item.id)) {
          e.preventDefault();
          toggleFulltextExpanded(entry.item.id);
        }
      }
    } else if (e.key === "ArrowLeft") {
      if (entry?.kind === "snippet") {
        // Collapse the parent and hop the cursor back onto it, so the
        // expanded area disappears and the next ↑/↓ resumes through
        // the flat item list.
        e.preventDefault();
        const parentIdx = navEntries.findIndex(
          (en) => en.kind === "item" && en.item.id === entry.item.id,
        );
        if (parentIdx >= 0) setActiveIndex(parentIdx);
        toggleFulltextExpanded(entry.item.id);
      } else if (
        entry?.kind === "item" &&
        entry.isFulltext &&
        expandedFulltextIds.has(entry.item.id)
      ) {
        // Collapse, but stay on this row so the next ↑/↓ feels natural.
        e.preventDefault();
        toggleFulltextExpanded(entry.item.id);
      } else if (activeIndex >= 0) {
        e.preventDefault();
        setActiveIndex(-1);
      }
    } else if (e.key === "Enter" && activeIndex >= 0 && entry) {
      e.preventDefault();
      // Enter opens the PDF: the result is the thing to read, and
      // landing on a detail panel instead means a second click every
      // time. Shift is the way to the record.
      if (entry.kind === "snippet") {
        if (e.shiftKey) openInLibrary(entry.item);
        else
          nav(
            `/reader/${encodeURIComponent(entry.attachmentId)}?page=${entry.pageNumber}&find=${encodeURIComponent(debouncedQ)}`,
          );
      } else {
        if (e.shiftKey) openInLibrary(entry.item);
        else openInReader(entry.item);
      }
    } else if (e.key === "Escape" && activeIndex >= 0) {
      e.preventDefault();
      setActiveIndex(-1);
    }
  }

  return (
    <div
      // h-dvh + overflow-hidden pins the layout to the *current*
      // viewport height. On iOS Safari the browser chrome's show/hide
      // makes 100vh slightly larger than the visible area, which is
      // what was letting the user scroll the landing page. dvh tracks
      // the live viewport so the search input lands smack in the
      // middle and stays there.
      className="flex h-dvh flex-col overflow-hidden"
      style={{ backgroundColor: "var(--color-bg)" }}
    >
      <div className="flex flex-1 flex-col items-center justify-center px-4">
        <h1 className="mb-2 font-mono text-3xl font-medium uppercase tracking-[0.22em] sm:text-4xl">
          Shelf
        </h1>
        <p
          className="mb-6 text-sm sm:mb-8"
          style={{ color: "var(--color-text-muted)" }}
        >
          Welcome back, {auth.user.display_name}.
        </p>
        <form onSubmit={onSubmit} className="w-full max-w-xl">
          <div
            className="flex items-center gap-2 rounded-full border px-4 py-2.5 shadow-sm focus-within:shadow-md"
            style={{
              backgroundColor: "var(--color-surface)",
              borderColor: "var(--color-border)",
            }}
          >
            <Search
              className="h-4 w-4"
              style={{ color: "var(--color-text-muted)" }}
            />
            <input
              type="search"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              onKeyDown={onInputKeyDown}
              autoFocus
              placeholder="Search your spaces…"
              className="flex-1 bg-transparent text-sm outline-none"
              style={{ color: "var(--color-text)" }}
              aria-autocomplete="list"
              aria-controls="home-search-suggestions"
              aria-activedescendant={(() => {
                if (activeIndex < 0) return undefined;
                const en = navEntries[activeIndex];
                if (!en) return undefined;
                if (en.kind === "snippet") {
                  return `home-snippet-${en.attachmentId}-${en.pageNumber}`;
                }
                return `home-suggest-${en.item.id}`;
              })()}
            />
            {allSpaces.length > 1 && (
              <SearchSpacePopover
                spaces={allSpaces}
                selected={selectedSpaces}
                onChange={(next) =>
                  // Back to null — "not narrowed" — when everything is
                  // selected again, so the query drops `space=` instead of
                  // pinning today's space list into every request.
                  setSpaceFilter(
                    next.length === allSpaces.length ? null : next,
                  )
                }
              />
            )}
            <SearchScopePopover scope={scope} onChange={setScope} />
          </div>
        </form>
        {canSearch === false && allSpaces.length > 0 && (
          <p
            className="mt-3 text-xs"
            style={{ color: "var(--color-text-muted)" }}
          >
            Every space is filtered out — nothing to search.
          </p>
        )}
        {showDropdown &&
          (() => {
            // Renders a single suggestion row. flatIndex is the row's
            // position in navEntries — the unified list ArrowDown walks
            // through, spanning title hits, scope-section items, and
            // expanded fulltext-snippet children.
            const renderRow = (
              item: Item,
              flatIndex: number,
              isFulltext: boolean,
            ) => {
              const title =
                (item.data.title as string | undefined)?.trim() ||
                "(untitled)";
              const creators = (item.data.creators ?? [])
                .map(
                  (c) =>
                    c.name?.trim() ||
                    [c.firstName, c.lastName]
                      .filter(Boolean)
                      .join(" ")
                      .trim(),
                )
                .filter(Boolean)
                .slice(0, 3)
                .join(", ");
              const date = (item.data.date as string | undefined)?.slice(
                0,
                4,
              );
              // Which library this came out of, when it isn't the user's
              // own shelf. Results now span every readable space, and
              // "ACME 1234" means something different depending on whether
              // it sits in Standards or in a project someone shared.
              const from =
                personal && item.space_id !== personal.id
                  ? spaceNames.get(item.space_id)
                  : null;
              const subtitle = [creators, date, from]
                .filter(Boolean)
                .join(" · ");
              const active = flatIndex === activeIndex;
              const isExpanded =
                isFulltext && expandedFulltextIds.has(item.id);
              return (
                <div
                  key={item.id}
                  id={`home-suggest-${item.id}`}
                  role="option"
                  aria-selected={active}
                  aria-expanded={isFulltext ? isExpanded : undefined}
                  ref={(el) => {
                    rowRefs.current[flatIndex] = el;
                  }}
                  onMouseEnter={() => setActiveIndex(flatIndex)}
                  className="flex items-center gap-2 border-b px-3 py-2 last:border-b-0"
                  style={{
                    borderColor: "var(--color-border)",
                    backgroundColor: active
                      ? "color-mix(in srgb, var(--color-text) 8%, transparent)"
                      : undefined,
                  }}
                >
                  {isFulltext ? (
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        toggleFulltextExpanded(item.id);
                      }}
                      aria-label={
                        isExpanded ? "Hide PDF hits" : "Show PDF hits"
                      }
                      aria-expanded={isExpanded}
                      className="rounded p-0.5 hover:opacity-70"
                      style={{ color: "var(--color-text-muted)" }}
                    >
                      {isExpanded ? (
                        <ChevronDown className="h-3.5 w-3.5" />
                      ) : (
                        <ChevronRight className="h-3.5 w-3.5" />
                      )}
                    </button>
                  ) : (
                    // Spacer that matches the chevron's footprint so
                    // titles align across rows whether the row is in
                    // the fulltext section or not.
                    <span className="inline-block w-[22px]" />
                  )}
                  <button
                    onClick={(e) =>
                      e.shiftKey ? openInLibrary(item) : openInReader(item)
                    }
                    title="Open PDF · Shift+click for item details"
                    className="min-w-0 flex-1 text-left hover:opacity-80"
                  >
                    <div
                      className="truncate text-sm"
                      style={{ color: "var(--color-text)" }}
                    >
                      {title}
                    </div>
                    {subtitle && (
                      <div
                        className="truncate text-xs"
                        style={{ color: "var(--color-text-muted)" }}
                      >
                        {subtitle}
                      </div>
                    )}
                  </button>
                  <button
                    onClick={() => openInReader(item)}
                    title="Open PDF"
                    aria-label="Open PDF"
                    className="rounded p-1.5 hover:opacity-80"
                    style={{ color: "var(--color-text-muted)" }}
                  >
                    <BookOpen className="h-4 w-4" />
                  </button>
                  <button
                    onClick={() => openInLibrary(item)}
                    title="Open item details"
                    aria-label="Open item details"
                    className="rounded p-1.5 hover:opacity-80"
                    style={{ color: "var(--color-text-muted)" }}
                  >
                    <Info className="h-4 w-4" />
                  </button>
                </div>
              );
            };
            // Renders one PDF-snippet row beneath an expanded fulltext
            // item. Clicking one goes to that page in the reader with
            // the find bar filled in — the whole point of showing the
            // passage. Shift opens the item's record instead.
            const renderSnippet = (
              entry: SnippetNavEntry,
              flatIndex: number,
            ) => {
              const active = flatIndex === activeIndex;
              const target = `/reader/${encodeURIComponent(entry.attachmentId)}?page=${entry.pageNumber}&find=${encodeURIComponent(debouncedQ)}`;
              return (
                <div
                  key={`${entry.attachmentId}-${entry.pageNumber}-${flatIndex}`}
                  id={`home-snippet-${entry.attachmentId}-${entry.pageNumber}`}
                  role="option"
                  aria-selected={active}
                  ref={(el) => {
                    rowRefs.current[flatIndex] = el;
                  }}
                  onMouseEnter={() => setActiveIndex(flatIndex)}
                  onClick={(e) => {
                    if (e.shiftKey) openInLibrary(entry.item);
                    else nav(target);
                  }}
                  className="flex cursor-pointer items-start gap-2 border-b px-3 py-1.5 pl-11 text-sm last:border-b-0"
                  style={{
                    borderColor: "var(--color-border)",
                    backgroundColor: active
                      ? "color-mix(in srgb, var(--color-text) 8%, transparent)"
                      : "color-mix(in srgb, var(--color-text-muted) 4%, transparent)",
                  }}
                  title={
                    isCoarsePointer
                      ? "Tap to jump to this page"
                      : "Click to jump to this page · Shift+click for item details"
                  }
                >
                  <span
                    className="shrink-0 text-xs tabular-nums"
                    style={{ color: "var(--color-text-muted)" }}
                  >
                    p.{entry.pageNumber}
                  </span>
                  <span
                    className="min-w-0 flex-1"
                    // ts_headline output is escaped by Postgres before
                    // its <mark>/</mark> tags are inserted, so this is
                    // safe to render as HTML — same contract as
                    // FulltextHitsRow uses in the library.
                    dangerouslySetInnerHTML={{ __html: entry.snippetHtml }}
                  />
                </div>
              );
            };
            // Track where each scope section starts in the flat list
            // so renderRow gets the right activeIndex slot.
            let cursor = items.length;
            return (
              <div
                id="home-search-suggestions"
                role="listbox"
                // Cap the height so a slow fulltext query returning
                // many matches doesn't push the rest of the page off
                // the viewport. Internal scrollIntoView keeps the
                // keyboard cursor visible.
                className="mt-2 w-full max-w-xl overflow-y-auto overflow-x-hidden rounded-lg border shadow-sm"
                style={{
                  backgroundColor: "var(--color-surface)",
                  borderColor: "var(--color-border)",
                  maxHeight: "min(60vh, 480px)",
                }}
              >
                {items.map((item, idx) => renderRow(item, idx, false))}
            {suggestions.isFetching &&
              (suggestions.data?.items.length ?? 0) === 0 &&
              !anyScopeActivity && (
                <div
                  className="px-3 py-2 text-xs"
                  style={{ color: "var(--color-text-muted)" }}
                >
                  Searching…
                </div>
              )}
            {/* Per-scope status strip. Title results are shown above as
                rows; this strip surfaces the other scopes (creators,
                abstract, extra, fulltext) so the user can see what's
                still running in the background and where the rest of
                the matches live. Click a chip to jump to /library
                narrowed to that scope. */}
            {debouncedQ.length >= 2 && (
              <div
                className="flex flex-wrap items-center gap-1.5 border-t px-3 py-2"
                style={{
                  borderColor: "var(--color-border)",
                  backgroundColor:
                    "color-mix(in srgb, var(--color-text) 3%, transparent)",
                }}
              >
                <span
                  className="mr-1 text-[10px] font-medium uppercase tracking-wider"
                  style={{ color: "var(--color-text-muted)" }}
                >
                  Also searching
                </span>
                {COUNT_SCOPES.map((s, i) => {
                  const qr = scopeCounts[i];
                  const sd = scopeData[i];
                  const loading = qr.isFetching;
                  // Display the deduped count so the chip and the
                  // section header below tell the same story: 4 raw
                  // matches with 1 overlapping a title row reads as
                  // "3" everywhere, not "4 here, 3 there".
                  const count = sd.items.length;
                  const hasResults = !loading && count > 0;
                  const empty = !loading && count === 0;
                  // Reach back to the API total so the tooltip can
                  // explain the dedupe when a scope's raw count was
                  // larger than what we display.
                  const overlapped =
                    !loading && sd.apiTotal > count ? sd.apiTotal - count : 0;
                  return (
                    <button
                      key={s}
                      type="button"
                      onClick={() => hasResults && jumpToScope(s)}
                      disabled={!hasResults}
                      title={
                        loading
                          ? `Searching ${SEARCH_SCOPE_LABELS[s].toLowerCase()}…`
                          : count > 0
                            ? `${count} match${count === 1 ? "" : "es"} in ${SEARCH_SCOPE_LABELS[s].toLowerCase()}${
                                overlapped > 0
                                  ? ` (+${overlapped} already listed above)`
                                  : ""
                              } — click to open`
                            : sd.apiTotal > 0
                              ? `${sd.apiTotal} match${sd.apiTotal === 1 ? "" : "es"} in ${SEARCH_SCOPE_LABELS[s].toLowerCase()}, all already listed above`
                              : `No matches in ${SEARCH_SCOPE_LABELS[s].toLowerCase()}`
                      }
                      className="flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs disabled:cursor-default"
                      style={{
                        borderColor: "var(--color-border)",
                        backgroundColor: "var(--color-surface)",
                        color: hasResults
                          ? "var(--color-text)"
                          : "var(--color-text-muted)",
                        opacity: empty ? 0.55 : 1,
                      }}
                    >
                      {loading ? (
                        <Loader2 className="h-3 w-3 animate-spin" />
                      ) : (
                        <Check
                          className="h-3 w-3"
                          style={{
                            color: hasResults
                              ? "var(--color-accent)"
                              : "var(--color-text-muted)",
                          }}
                        />
                      )}
                      <span>{SEARCH_SCOPE_LABELS[s]}</span>
                      <span
                        className="tabular-nums"
                        style={{ color: "var(--color-text-muted)" }}
                      >
                        {loading ? "…" : count}
                      </span>
                    </button>
                  );
                })}
              </div>
            )}
            {/* Per-scope result sections, appended as each query
                resolves. Sections show only items not already listed
                above so the dropdown doesn't repeat itself. */}
            {sections.map((section) => {
              const isFulltextSection = section.scope === "fulltext";
              const shown = section.items.length;
              // "see all" only when the API knows about additional
              // matches we didn't fetch — i.e., the scope's apiTotal
              // outran the page we asked for. Items that overlap with
              // rows already displayed don't justify a "see all" since
              // the user already has them.
              const hasMoreInApi = section.apiTotal > section.fetched;
              return (
                <div key={section.scope}>
                  <div
                    className="flex items-center justify-between gap-2 border-t px-3 py-1.5"
                    style={{
                      borderColor: "var(--color-border)",
                      backgroundColor:
                        "color-mix(in srgb, var(--color-text) 2%, transparent)",
                    }}
                  >
                    <span
                      className="text-[10px] font-medium uppercase tracking-wider"
                      style={{ color: "var(--color-text-muted)" }}
                    >
                      In {section.label.toLowerCase()}
                    </span>
                    <button
                      type="button"
                      onClick={() => jumpToScope(section.scope)}
                      className="text-[10px] uppercase tracking-wider hover:opacity-80"
                      style={{ color: "var(--color-accent)" }}
                    >
                      {shown} match{shown === 1 ? "" : "es"}
                      {hasMoreInApi ? " · see all" : ""}
                    </button>
                  </div>
                  {section.items.map((item) => {
                    const itemFlatIndex = cursor++;
                    const expanded =
                      isFulltextSection && expandedFulltextIds.has(item.id);
                    const hitsState = isFulltextSection
                      ? hitsByItemId.get(item.id)
                      : undefined;
                    return (
                      <div key={item.id}>
                        {renderRow(item, itemFlatIndex, isFulltextSection)}
                        {expanded && hitsState?.loading && (
                          <div
                            className="border-b px-3 py-1.5 pl-11 text-xs italic"
                            style={{
                              borderColor: "var(--color-border)",
                              color: "var(--color-text-muted)",
                              backgroundColor:
                                "color-mix(in srgb, var(--color-text-muted) 4%, transparent)",
                            }}
                          >
                            Loading hits…
                          </div>
                        )}
                        {expanded &&
                          !hitsState?.loading &&
                          hitsState?.data &&
                          hitsState.data.length === 0 && (
                            <div
                              className="border-b px-3 py-1.5 pl-11 text-xs italic"
                              style={{
                                borderColor: "var(--color-border)",
                                color: "var(--color-text-muted)",
                                backgroundColor:
                                  "color-mix(in srgb, var(--color-text-muted) 4%, transparent)",
                              }}
                            >
                              No PDF hits.
                            </div>
                          )}
                        {expanded &&
                          hitsState?.data?.map((att) => (
                            <div key={att.attachment_id}>
                              {/* Attachment filename header — only worth
                                  showing when this item has more than
                                  one PDF, so single-attachment items
                                  don't burn a row on a redundant label. */}
                              {hitsState.data!.length > 1 && (
                                <div
                                  className="px-3 pl-11 pt-1 text-[10px] font-medium uppercase tracking-wider"
                                  style={{
                                    color: "var(--color-text-muted)",
                                    backgroundColor:
                                      "color-mix(in srgb, var(--color-text-muted) 4%, transparent)",
                                  }}
                                >
                                  {att.filename}
                                </div>
                              )}
                              {att.hits.map((hit) => {
                                const snippetFlatIndex = cursor++;
                                return renderSnippet(
                                  {
                                    kind: "snippet",
                                    item,
                                    attachmentId: att.attachment_id,
                                    filename: att.filename,
                                    pageNumber: hit.page_number,
                                    snippetHtml: hit.snippet_html,
                                  },
                                  snippetFlatIndex,
                                );
                              })}
                            </div>
                          ))}
                      </div>
                    );
                  })}
                </div>
              );
            })}
              </div>
            );
          })()}
        <div className="mt-6 flex items-center gap-4 text-xs">
          <button
            onClick={() => nav("/library")}
            className="flex items-center gap-1.5 hover:opacity-80"
            style={{ color: "var(--color-accent)" }}
          >
            <LibraryBig className="h-3.5 w-3.5" />
            Browse library
          </button>
          <span style={{ color: "var(--color-border)" }}>·</span>
          <button
            onClick={() => nav("/settings")}
            className="flex items-center gap-1.5 hover:opacity-80"
            style={{ color: "var(--color-text-muted)" }}
          >
            <Settings className="h-3.5 w-3.5" />
            Settings
          </button>
          <span style={{ color: "var(--color-border)" }}>·</span>
          {/* This page has its own nav rather than the app header, so
              the shortcuts reference has to be placed here too. */}
          <ShortcutsHelp />
        </div>
      </div>
    </div>
  );
}
