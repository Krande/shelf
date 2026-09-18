import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
} from "react";
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useNavigate, useSearchParams } from "react-router";
import {
  ArrowDown,
  ArrowLeft,
  ArrowUp,
  ArrowUpDown,
  ChevronDown,
  ChevronRight,
  Download,
  Loader2,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  RotateCcw,
  Search,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import { canEdit, fetchMySpaces, type Space } from "@/api/spaces";
import {
  ALL_SEARCH_SCOPES,
  createItem,
  deleteItem,
  getItem,
  listItems,
  permanentDeleteItem,
  restoreItem,
  SEARCH_SCOPE_LABELS,
  updateItem,
  type Item,
  type ItemSort,
  type ItemStatus,
  type SearchScope,
  type SortDirection,
} from "@/api/items";
import {
  listCollections,
  setItemCollections,
  type Collection,
} from "@/api/collections";
import { fetchPins } from "@/api/standards";
import { createTag, listTags, setItemTags, type Tag } from "@/api/tags";
import {
  downloadItemPdfsZip,
  listAttachments,
  uploadAttachment,
} from "@/api/attachments";
import { itemTypeLabel, type ItemType } from "@/api/itemFields";
import { useDebounce } from "@/hooks/useDebounce";
import { useResizableWidth } from "@/hooks/useResizableWidth";
import AppShell from "@/components/layout/AppShell";
import ItemForm, {
  type ItemFormSubmission,
} from "@/components/library/ItemForm";
import ItemDetail from "@/components/library/ItemDetail";
import TagChips from "@/components/library/TagChips";
import CollectionRail from "@/components/library/CollectionRail";
import BulkAddToCollection from "@/components/library/BulkAddToCollection";
import BulkCopyToSpace from "@/components/library/BulkCopyToSpace";
import SearchScopePopover from "@/components/library/SearchScopePopover";
import FulltextHitsRow from "@/components/library/FulltextHitsRow";

function formatDate(s: string): string {
  return new Date(s).toLocaleString();
}

function itemTitle(item: Item): string {
  const t = item.data?.title;
  return typeof t === "string" && t.trim() ? t : "(untitled)";
}

function creatorSummary(item: Item): string {
  const cs = item.data.creators;
  if (!cs || cs.length === 0) return "";
  const first = cs[0];
  const name =
    first.name || [first.firstName, first.lastName].filter(Boolean).join(" ");
  return cs.length > 1 ? `${name} et al.` : name;
}

type View = "library" | "trash";

function primaryScopeMatch(
  item: Item,
  needle: string,
  enabled: Set<SearchScope>,
): SearchScope | null {
  if (
    enabled.has("title") &&
    typeof item.data.title === "string" &&
    item.data.title.toLowerCase().includes(needle)
  ) {
    return "title";
  }
  if (enabled.has("creators")) {
    const creators = item.data.creators ?? [];
    const blob = creators
      .map((c) =>
        [c.firstName, c.lastName, c.name].filter(Boolean).join(" "),
      )
      .join(" ")
      .toLowerCase();
    if (blob.includes(needle)) return "creators";
  }
  if (
    enabled.has("abstract") &&
    typeof item.data.abstractNote === "string" &&
    item.data.abstractNote.toLowerCase().includes(needle)
  ) {
    return "abstract";
  }
  if (
    enabled.has("extra") &&
    typeof item.data.extra === "string" &&
    item.data.extra.toLowerCase().includes(needle)
  ) {
    return "extra";
  }
  return null;
}


function SortHeader({
  label,
  column,
  activeSort,
  direction,
  onClick,
}: {
  label: string;
  column: ItemSort;
  activeSort: ItemSort;
  direction: SortDirection;
  onClick: (col: ItemSort) => void;
}) {
  const active = activeSort === column;
  const Icon = active
    ? direction === "asc"
      ? ArrowUp
      : ArrowDown
    : ArrowUpDown;
  return (
    <th
      onClick={() => onClick(column)}
      className="cursor-pointer select-none px-4 py-2 hover:opacity-80"
    >
      <span className="inline-flex items-center gap-1">
        {label}
        <Icon
          className="h-3 w-3"
          style={{
            color: active ? "var(--color-accent)" : "var(--color-text-muted)",
            opacity: active ? 1 : 0.5,
          }}
        />
      </span>
    </th>
  );
}

const RAIL_PREF_KEY = "shelf:rail-open";

function initialRailOpen(): boolean {
  if (typeof window === "undefined") return true;
  const stored = window.localStorage.getItem(RAIL_PREF_KEY);
  if (stored !== null) return stored === "true";
  // First visit: default open on tablet+, closed on phones so the
  // 224px rail doesn't eat the whole screen.
  return window.matchMedia("(min-width: 768px)").matches;
}

export default function LibraryPage() {
  const qc = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const nav = useNavigate();

  const [railOpen, setRailOpen] = useState<boolean>(initialRailOpen);
  useEffect(() => {
    window.localStorage.setItem(RAIL_PREF_KEY, String(railOpen));
  }, [railOpen]);

  // Sub-md viewports drive a different layout: the detail panel
  // takes over full-screen on row tap rather than living in a
  // side aside, since the aside is hidden under md.
  const [isMobile, setIsMobile] = useState<boolean>(() => {
    if (typeof window === "undefined") return false;
    return !window.matchMedia("(min-width: 768px)").matches;
  });
  useEffect(() => {
    const mq = window.matchMedia("(min-width: 768px)");
    const onChange = () => setIsMobile(!mq.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  const view: View =
    searchParams.get("view") === "trash" ? "trash" : "library";
  const status: ItemStatus = view === "trash" ? "trashed" : "active";

  // ?collection=<id|"unfiled"> when set; null = "All Items".
  const collectionParam = searchParams.get("collection");

  const sortParam = searchParams.get("sort");
  const sort: ItemSort =
    sortParam === "title" ||
    sortParam === "type" ||
    sortParam === "created" ||
    sortParam === "updated"
      ? sortParam
      : "updated";
  const dirParam = searchParams.get("dir");
  const direction: SortDirection = dirParam === "asc" ? "asc" : "desc";

  function applySort(next: ItemSort) {
    if (next === sort) {
      const flipped: SortDirection = direction === "asc" ? "desc" : "asc";
      if (flipped === "desc" && next === "updated") {
        searchParams.delete("dir");
      } else {
        searchParams.set("dir", flipped);
      }
    } else {
      const def: SortDirection =
        next === "title" || next === "type" ? "asc" : "desc";
      if (next === "updated") searchParams.delete("sort");
      else searchParams.set("sort", next);
      if (def === "desc") searchParams.delete("dir");
      else searchParams.set("dir", "asc");
    }
    setSearchParams(searchParams);
  }

  const spaces = useQuery({
    queryKey: ["spaces"],
    queryFn: () => fetchMySpaces(),
  });
  const personal = useMemo<Space | null>(
    () => spaces.data?.find((s) => s.is_personal) ?? spaces.data?.[0] ?? null,
    [spaces.data],
  );

  const [activeSlug, setActiveSlug] = useState<string | null>(null);
  const slug = activeSlug ?? personal?.slug ?? null;

  // A space shared read-only. Hiding the write controls is cosmetic —
  // the API returns 403 either way — but offering a button that always
  // fails is worse than not offering it.
  const activeSpace = useMemo(
    () => spaces.data?.find((s) => s.slug === slug) ?? null,
    [spaces.data, slug],
  );
  const writable = canEdit(activeSpace);

  const collections = useQuery({
    queryKey: ["collections", slug],
    queryFn: () => listCollections(slug!),
    enabled: !!slug,
  });

  // All tags for the active space. Used for: rendering tag-name
  // chips on items (from item.tag_ids), TagsInput typeahead options,
  // and filter-chip ↔ tag-id reconciliation. Cheap to keep
  // resident — tag rows are small and the count is bounded by user
  // habits, not document volume.
  const tagsQuery = useQuery({
    queryKey: ["tags", slug],
    queryFn: () => listTags(slug!),
    enabled: !!slug,
  });
  const tagsById = useMemo(() => {
    const map = new Map<string, Tag>();
    for (const t of tagsQuery.data ?? []) map.set(t.id, t);
    return map;
  }, [tagsQuery.data]);
  const tagNamesById = useCallback(
    (ids: string[]): string[] =>
      ids
        .map((id) => tagsById.get(id)?.name)
        .filter((n): n is string => typeof n === "string"),
    [tagsById],
  );

  const [searchInput, setSearchInput] = useState(searchParams.get("q") ?? "");
  const debouncedQuery = useDebounce(searchInput, 250);
  const filterTags = useMemo(
    () => searchParams.getAll("tag").filter((t) => t.trim().length > 0),
    [searchParams],
  );

  // Search scope: when no `scope=` is on the URL, all four are
  // active. Empty array means "narrowed past every scope" — which
  // the backend treats as a guaranteed no-match — so we surface that
  // explicitly instead of falling back to "all" silently.
  const searchScope = useMemo<SearchScope[]>(() => {
    const raw = searchParams.getAll("scope");
    if (raw.length === 0) return [...ALL_SEARCH_SCOPES];
    return raw.filter((s): s is SearchScope =>
      (ALL_SEARCH_SCOPES as string[]).includes(s),
    );
  }, [searchParams]);

  function setSearchScope(next: SearchScope[]) {
    searchParams.delete("scope");
    if (next.length > 0 && next.length < ALL_SEARCH_SCOPES.length) {
      for (const s of next) searchParams.append("scope", s);
    } else if (next.length === 0) {
      // Encode the user's deliberate "search nothing" as a single
      // sentinel scope=none entry so reload preserves it. The
      // useMemo above filters unknown values out → behaves like
      // "no scopes enabled", returning zero results.
      searchParams.append("scope", "none");
    }
    setSearchParams(searchParams);
  }

  useEffect(() => {
    const trimmed = debouncedQuery.trim();
    const current = searchParams.get("q") ?? "";
    if (trimmed === current) return;
    if (trimmed) searchParams.set("q", trimmed);
    else searchParams.delete("q");
    setSearchParams(searchParams, { replace: true });
  }, [debouncedQuery, searchParams, setSearchParams]);

  function setSelection(s: { view: View; collection: string | null }) {
    if (s.view === "trash") searchParams.set("view", "trash");
    else searchParams.delete("view");
    if (s.collection) searchParams.set("collection", s.collection);
    else searchParams.delete("collection");
    // Dropped in the same write as the view change rather than through
    // setSelectedId, which would follow up with a second, replacing
    // navigation and swallow this one's history entry.
    searchParams.delete("item");
    setSearchParams(searchParams);
    setCheckedIds(new Set());
  }

  const addTag = useCallback(
    (tag: string) => {
      const v = tag.trim().toLowerCase();
      if (!v) return;
      if (filterTags.includes(v)) return;
      searchParams.append("tag", v);
      setSearchParams(searchParams);
    },
    [filterTags, searchParams, setSearchParams],
  );

  const removeTag = useCallback(
    (tag: string) => {
      const remaining = filterTags.filter((t) => t !== tag);
      searchParams.delete("tag");
      for (const t of remaining) searchParams.append("tag", t);
      setSearchParams(searchParams);
    },
    [filterTags, searchParams, setSearchParams],
  );

  // Standards this space has pinned an edition of. Only used to decide
  // whether the "show all revisions" control is worth rendering — a
  // space with no pins has nothing hidden, so the toggle would be noise.
  const pins = useQuery({
    queryKey: ["pins", slug],
    queryFn: () => fetchPins(slug!),
    enabled: !!slug,
  });
  const pinnedCount = pins.data?.length ?? 0;

  // Whether to show every edition of a standard this space has pinned
  // one of. Off by default, matching the server: a project library
  // should answer "which edition do we build to", not list five. Lives
  // in the URL so the widened view survives a reload and can be linked.
  const showAllRevisions = searchParams.get("revisions") === "all";
  const toggleAllRevisions = useCallback(() => {
    if (showAllRevisions) searchParams.delete("revisions");
    else searchParams.set("revisions", "all");
    setSearchParams(searchParams);
  }, [showAllRevisions, searchParams, setSearchParams]);

  // Page size is the server's default; small enough that the first
  // chunk feels instant on mobile, large enough that scroll-to-load
  // doesn't fire constantly. The server caps at 200.
  const ITEMS_PAGE_SIZE = 100;
  const items = useInfiniteQuery({
    queryKey: [
      "items",
      slug,
      status,
      sort,
      direction,
      debouncedQuery.trim(),
      filterTags,
      collectionParam,
      searchScope,
      showAllRevisions,
    ],
    queryFn: ({ pageParam }) =>
      listItems(slug!, {
        limit: ITEMS_PAGE_SIZE,
        offset: pageParam,
        status,
        q: debouncedQuery,
        tags: filterTags,
        sort,
        direction,
        collection: collectionParam ?? undefined,
        scope: searchScope,
        revisions: showAllRevisions ? "all" : "pinned",
      }),
    initialPageParam: 0,
    getNextPageParam: (lastPage, allPages) => {
      const loaded = allPages.reduce((n, p) => n + p.items.length, 0);
      return loaded < lastPage.total ? loaded : undefined;
    },
    enabled: !!slug,
  });
  // Flat array view of the items loaded across every page so the
  // existing render + grouping code keeps working unchanged. ``total``
  // drives the header count and the "fetch more?" decision.
  const flatItems = useMemo(
    () => items.data?.pages.flatMap((p) => p.items) ?? [],
    [items.data],
  );
  const itemsTotal = items.data?.pages[0]?.total ?? null;

  // IntersectionObserver-driven infinite scroll: when the sentinel
  // below the table enters the viewport (or the scroll container's
  // viewport, since the observer uses the nearest scroll ancestor by
  // default) and we haven't already exhausted ``total``, kick off the
  // next page. ``hasNextPage`` + ``isFetchingNextPage`` from
  // useInfiniteQuery dedup overlapping triggers while a fetch is in
  // flight.
  const loadMoreRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const node = loadMoreRef.current;
    if (!node) return;
    if (!items.hasNextPage || items.isFetchingNextPage) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          items.fetchNextPage();
        }
      },
      // 600px lead so we kick off the next fetch before the user
      // actually reaches the bottom — feels seamless on a fast scroll.
      { rootMargin: "600px 0px" },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, [items.hasNextPage, items.isFetchingNextPage, items.fetchNextPage, flatItems.length]);

  // What is filed under the open collection's subcollections, shown in
  // a collapsible section below its own documents. A separate query
  // rather than a widened filter: the two listings stay distinct, and
  // nothing is fetched at all for a folder with no children.
  const openCollection =
    collectionParam && collectionParam !== "unfiled" ? collectionParam : null;
  const hasSubcollections = useMemo(
    () =>
      !!openCollection &&
      (collections.data ?? []).some((c) => c.parent_id === openCollection),
    [collections.data, openCollection],
  );
  const [subOpen, setSubOpen] = useState(false);
  // Collapsed again whenever the rail selection changes — the previous
  // folder's expansion says nothing about this one.
  useEffect(() => {
    setSubOpen(false);
  }, [openCollection]);

  const subItemsQuery = useQuery({
    queryKey: ["items", slug, "subcollections", openCollection, status],
    queryFn: () =>
      listItems(slug!, {
        limit: ITEMS_PAGE_SIZE,
        status,
        collection: openCollection!,
        collectionScope: "subcollections",
      }),
    enabled: !!slug && !!openCollection && hasSubcollections,
  });
  const subItems = subItemsQuery.data?.items ?? [];
  const subTotal = subItemsQuery.data?.total ?? 0;

  // Every item the page holds, wherever it is rendered. Id lookups go
  // through this so a row from the subcollection section resolves like
  // any other -- otherwise selecting one would re-fetch it by id and
  // the "selection vanished from the list" cleanup would clear it.
  const loadedItems = useMemo(
    () => [...flatItems, ...subItems],
    [flatItems, subItems],
  );
  // What "select all" and the bulk toolbar act on: the rows actually on
  // screen. A collapsed section is not on screen, so its items are not
  // swept into a bulk action nobody can see the scope of.
  const selectableItems = useMemo(
    () => (subOpen ? loadedItems : flatItems),
    [subOpen, loadedItems, flatItems],
  );

  // Compute which scope each item primarily matched in, so the
  // results can be grouped into "Title hits" / "Creator hits" / etc.
  // sections. Priority order matches user expectation: a hit in the
  // title outweighs a hit elsewhere. The `fulltext` bucket is the
  // fallback for items the server returned but whose match isn't on
  // any locally-readable field — i.e. the match is in PDF body text
  // we don't ship to the client.
  const groupedItems = useMemo(() => {
    const trimmed = debouncedQuery.trim().toLowerCase();
    if (!trimmed || flatItems.length === 0) {
      return null;
    }
    const enabled = new Set(searchScope);
    const groups: Record<SearchScope, Item[]> = {
      title: [],
      creators: [],
      abstract: [],
      extra: [],
      fulltext: [],
    };
    for (const it of flatItems) {
      const where = primaryScopeMatch(it, trimmed, enabled);
      if (where) {
        groups[where].push(it);
      } else if (enabled.has("fulltext")) {
        groups.fulltext.push(it);
      }
    }
    return groups;
  }, [flatItems, debouncedQuery, searchScope]);

  // Which item's detail panel is open. Kept in the URL, not component
  // state, so it survives a round trip through the reader: Back pops to
  // this entry with the panel and the filters intact. Same `?item=` the
  // landing page already deep-links to.
  const selectedId = searchParams.get("item");
  const setSelectedId = useCallback(
    (id: string | null) => {
      const next = new URLSearchParams(searchParams);
      if (id) next.set("item", id);
      else next.delete("item");
      // Replace, not push: clicking down a list of items shouldn't bury
      // the screen you arrived from under a dozen history entries.
      setSearchParams(next, { replace: true });
    },
    [searchParams, setSearchParams],
  );
  const [checkedIds, setCheckedIds] = useState<Set<string>>(new Set());

  // Width of the desktop detail pane. Null until someone drags it, so
  // the existing responsive width stays the default.
  const detailWidth = useResizableWidth("shelf.detailPanelWidth");

  // Spaces including the ones this space inherits, purely to answer
  // "may I edit this item?" — the answer depends on the caller's role in
  // the space the item actually *lives* in, which for an inherited item
  // isn't the one being browsed. Kept as its own query rather than
  // widening ["spaces"], because that one feeds the space switcher and
  // an inherited space isn't somewhere you switch to.
  const spacesWithInherited = useQuery({
    queryKey: ["spaces", "with-inherited"],
    queryFn: () => fetchMySpaces({ includeInherited: true }),
  });

  // ?item=<id> may name an item outside the current page of list results
  // — arriving from the landing-page search dropdown, or coming back
  // from the reader after the list has moved on. Fetch it directly so
  // the panel can open on it regardless.
  const deepLinkedItem = useQuery({
    queryKey: ["item", selectedId],
    queryFn: () => getItem(selectedId!),
    enabled:
      !!selectedId &&
      !!items.data &&
      !loadedItems.some((i) => i.id === selectedId),
  });

  const selected =
    loadedItems.find((i) => i.id === selectedId) ??
    (deepLinkedItem.data && deepLinkedItem.data.id === selectedId
      ? deepLinkedItem.data
      : null);

  // The space the selected item actually lives in, which for an
  // inherited item is not the one being browsed. Whether its metadata
  // and files can be changed follows from the caller's role *there* —
  // the same rule the API applies — so someone who owns the Standards
  // space edits its documents from anywhere they can see them, and a
  // subscriber who can only read gets a consistently read-only panel.
  const selectedItemSpace = useMemo(
    () =>
      selected
        ? (spacesWithInherited.data ?? []).find(
            (s) => s.id === selected.space_id,
          )
        : undefined,
    [selected, spacesWithInherited.data],
  );
  const canEditSelected = canEdit(selectedItemSpace);

  // Track which items have their full-text hit list expanded. Cleared
  // whenever the search query changes so old expansions don't leak
  // into a new search context.
  const [expandedFulltextIds, setExpandedFulltextIds] = useState<Set<string>>(
    new Set(),
  );
  useEffect(() => {
    setExpandedFulltextIds(new Set());
  }, [debouncedQuery]);
  const toggleFulltextExpanded = useCallback((id: string) => {
    setExpandedFulltextIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  // The table body, as data. Hoisted out of the JSX because arrow-key
  // navigation has to walk the rows in the order they're painted, and
  // two constructions of that order would drift apart.
  const ownEntries = useMemo(
    () =>
      groupedItems
        ? ALL_SEARCH_SCOPES.filter((s) => groupedItems[s].length > 0).flatMap(
            (s) => [
              {
                kind: "header" as const,
                scope: s,
                count: groupedItems[s].length,
              },
              ...groupedItems[s].flatMap((it) => {
                const row = { kind: "row" as const, item: it, scope: s };
                // Only the fulltext bucket gets the expansion. Other
                // groups already display their match in-band (title /
                // creators / etc.).
                if (s === "fulltext" && expandedFulltextIds.has(it.id)) {
                  return [row, { kind: "hits" as const, itemId: it.id }];
                }
                return [row];
              }),
            ],
          )
        : flatItems.map((it) => ({
            kind: "row" as const,
            item: it,
            scope: null,
          })),
    [groupedItems, flatItems, expandedFulltextIds],
  );

  // The subcollection section, appended below the folder's own rows.
  // Its rows are ordinary rows, so selection, ctrl+click and the arrow
  // keys reach them without knowing they came from somewhere else.
  const listEntries = useMemo(() => {
    if (!hasSubcollections || subTotal === 0) return ownEntries;
    return [
      ...ownEntries,
      { kind: "subheader" as const, count: subTotal, loaded: subItems.length },
      ...(subOpen
        ? subItems.map((it) => ({
            kind: "row" as const,
            item: it,
            scope: null,
          }))
        : []),
    ];
  }, [ownEntries, hasSubcollections, subTotal, subItems, subOpen]);

  /** Just the item rows, in painted order — what the arrow keys walk. */
  const navigableItems = useMemo(
    () =>
      listEntries.flatMap((e) => (e.kind === "row" ? [e.item] : [])),
    [listEntries],
  );

  useEffect(() => {
    if (!items.data) return;
    const present = new Set(loadedItems.map((i) => i.id));
    // Don't clear selectedId for deep-linked items that fell back to
    // the direct getItem fetch — they're legitimately not in the
    // current list page but still expected to remain selected.
    if (
      selectedId &&
      !present.has(selectedId) &&
      deepLinkedItem.data?.id !== selectedId &&
      !deepLinkedItem.isFetching
    ) {
      setSelectedId(null);
    }
    setCheckedIds((prev) => {
      const next = new Set([...prev].filter((id) => present.has(id)));
      return next.size === prev.size ? prev : next;
    });
  }, [items.data, selectedId, deepLinkedItem.data, deepLinkedItem.isFetching]);

  const [formMode, setFormMode] = useState<"closed" | "create" | "edit">(
    "closed",
  );

  // Item form submission also persists collection membership via a
  // follow-up PUT — keeps the create/update endpoints lean and
  // separates concerns; the membership endpoint is the source of
  // truth.
  // Resolve a list of tag names to ids, creating any tag the user
  // typed that doesn't already exist (case-insensitively). The
  // server's CITEXT unique constraint means a concurrent creator
  // could win the race; we re-fetch on 409 and pick up the row that
  // got there first instead of erroring.
  const resolveTagNamesToIds = useCallback(
    async (names: string[]): Promise<string[]> => {
      if (!slug || names.length === 0) return [];
      const existing = tagsQuery.data ?? (await listTags(slug));
      const byName = new Map<string, Tag>();
      for (const t of existing) byName.set(t.name.toLowerCase(), t);
      const ids: string[] = [];
      const created: Tag[] = [];
      for (const raw of names) {
        const name = raw.trim();
        if (!name) continue;
        const hit = byName.get(name.toLowerCase());
        if (hit) {
          if (!ids.includes(hit.id)) ids.push(hit.id);
          continue;
        }
        try {
          const t = await createTag(slug, { name });
          byName.set(t.name.toLowerCase(), t);
          created.push(t);
          ids.push(t.id);
        } catch {
          // Lost the race; refresh and try once more from cache.
          const refreshed = await listTags(slug);
          for (const t of refreshed) byName.set(t.name.toLowerCase(), t);
          const hit2 = byName.get(name.toLowerCase());
          if (hit2 && !ids.includes(hit2.id)) ids.push(hit2.id);
        }
      }
      if (created.length > 0) {
        // Push the new tag rows into the cache so the next render
        // resolves their names without an extra round-trip.
        qc.setQueryData<Tag[]>(["tags", slug], (prev) => [
          ...(prev ?? []),
          ...created,
        ]);
      }
      return ids;
    },
    [slug, tagsQuery.data, qc],
  );

  const createWithCollections = useMutation({
    mutationFn: async (payload: ItemFormSubmission) => {
      const item = await createItem(slug!, {
        item_type: payload.item_type,
        data: payload.data,
      });
      if (payload.collection_ids.length > 0) {
        await setItemCollections(item.id, payload.collection_ids);
      }
      const tagIds = await resolveTagNamesToIds(payload.tag_names);
      if (tagIds.length > 0) {
        await setItemTags(item.id, tagIds);
      }
      return item;
    },
    onSuccess: (item) => {
      qc.invalidateQueries({ queryKey: ["items", slug] });
      qc.invalidateQueries({ queryKey: ["tags", slug] });
      setFormMode("closed");
      setSelectedId(item.id);
    },
  });

  // Switching space drops the filters that are scoped to the space being
  // left. A collection id means nothing in another space, and leaving it
  // in the URL filters the new space's listing by a folder it does not
  // have -- an empty library that looks like the items are missing. The
  // selected item id goes for the same reason.
  const switchSpace = useCallback(
    (nextSlug: string) => {
      setActiveSlug(nextSlug);
      setCheckedIds(new Set());
      // One write, rather than setSelectedId() plus a second navigation.
      const next = new URLSearchParams(searchParams);
      next.delete("collection");
      next.delete("item");
      setSearchParams(next, { replace: true });
    },
    [searchParams, setSearchParams],
  );

  // Ctrl/Cmd-click a row, or Enter on it, to open the PDF directly.
  // Selecting before navigating is what makes Back work: the selection
  // is in the URL, so the history pop restores the panel. An item with
  // no PDF just stays selected.
  const openInReader = useCallback(
    async (item: Item) => {
      setSelectedId(item.id);
      try {
        const atts = await listAttachments(item.id);
        const pdf = atts.find((a) => a.content_type === "application/pdf");
        if (pdf) nav(`/reader/${encodeURIComponent(pdf.id)}`);
      } catch {
        // Nothing to add — they're on the detail panel either way.
      }
    },
    [nav, setSelectedId],
  );

  // Up/Down move the selection, Enter opens the selected PDF. Bound to
  // the window because rows have no tabindex and never hold focus; the
  // guards below keep it from stealing keys that belong to a field or a
  // focused control.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.altKey || e.ctrlKey || e.metaKey) return;
      // An open item form owns the keyboard — Enter there submits.
      if (formMode !== "closed") return;
      const el = e.target as HTMLElement | null;
      if (
        el instanceof HTMLInputElement ||
        el instanceof HTMLTextAreaElement ||
        el instanceof HTMLSelectElement ||
        el?.isContentEditable
      ) {
        return;
      }
      // Enter on a focused button or link is that control's own Enter,
      // not ours. Arrows over one are still fair game, since a button
      // does nothing with them.
      const onControl =
        el instanceof HTMLButtonElement || el instanceof HTMLAnchorElement;

      if (e.key === "Enter") {
        if (onControl) return;
        const item = navigableItems.find((i) => i.id === selectedId);
        if (!item) return;
        e.preventDefault();
        openInReader(item);
        return;
      }

      if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
      if (navigableItems.length === 0) return;
      e.preventDefault();
      const at = navigableItems.findIndex((i) => i.id === selectedId);
      let next: number;
      if (at === -1) {
        // Nothing selected yet — enter the list from the end the key
        // points at, rather than always from the top.
        next = e.key === "ArrowDown" ? 0 : navigableItems.length - 1;
      } else {
        // Clamped, not wrapped: running off the bottom of a long list
        // and landing back at the top loses your place silently.
        next = Math.min(
          Math.max(at + (e.key === "ArrowDown" ? 1 : -1), 0),
          navigableItems.length - 1,
        );
      }
      setSelectedId(navigableItems[next].id);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [navigableItems, selectedId, setSelectedId, openInReader, formMode]);

  // Keep the selected row on screen. "nearest" so a row already in view
  // isn't yanked to centre on every keypress.
  const listRef = useRef<HTMLTableSectionElement>(null);
  useEffect(() => {
    if (!selectedId) return;
    listRef.current
      ?.querySelector(`[data-item-row="${CSS.escape(selectedId)}"]`)
      ?.scrollIntoView({ block: "nearest" });
  }, [selectedId]);

  // One-tap PDF upload: mint a bare "document" item titled from the
  // filename, then attach the file. No metadata form in between — the
  // profile can be filled in afterwards from the detail panel. Accepts a
  // whole selection; each file becomes its own document.
  const pdfInput = useRef<HTMLInputElement>(null);
  // Which file of how many is in flight, for the button's label. Null
  // between batches; `useMutation`'s own isPending says nothing about
  // where in the batch we are.
  const [pdfProgress, setPdfProgress] = useState<{
    done: number;
    total: number;
  } | null>(null);

  /** Create one document item from one PDF, rolling the item back if the
   *  upload half fails. */
  async function uploadOnePdf(file: File) {
    const title = file.name.replace(/\.pdf$/i, "") || file.name;
    const item = await createItem(slug!, {
      item_type: "document",
      data: { title },
    });
    // Drop it into the collection currently in view, mirroring what
    // "New item" does — but skip the "unfiled" pseudo-collection.
    if (collectionParam && collectionParam !== "unfiled") {
      await setItemCollections(item.id, [collectionParam]);
    }
    // The item is created before the upload, so a failed upload would
    // otherwise leave an empty document behind. Roll it back on any
    // failure, then rethrow so the caller can report it.
    try {
      await uploadAttachment(item.id, file);
    } catch (e) {
      try {
        await deleteItem(item.id);
      } catch {
        // best-effort — the orphan sweep is the backstop
      }
      throw e;
    }
    return item;
  }

  const uploadPdf = useMutation({
    mutationFn: async (files: File[]) => {
      const created: Item[] = [];
      const failed: string[] = [];
      setPdfProgress({ done: 0, total: files.length });
      // Sequential: each file is three round trips plus a PUT to object
      // storage, and a parallel burst mainly makes "which one failed"
      // harder to answer.
      for (const [i, file] of files.entries()) {
        try {
          created.push(await uploadOnePdf(file));
        } catch (e) {
          // One bad file shouldn't strand the rest of the batch — the
          // others have nothing to do with it. Collect and carry on.
          failed.push(`${file.name}: ${(e as Error).message}`);
        }
        setPdfProgress({ done: i + 1, total: files.length });
      }
      return { created, failed };
    },
    onSettled: () => setPdfProgress(null),
    onSuccess: ({ created, failed }) => {
      qc.invalidateQueries({ queryKey: ["items", slug] });
      // Open the last one that landed — for a single file that's the
      // familiar "upload then look at it".
      const last = created.at(-1);
      if (last) setSelectedId(last.id);
      if (failed.length > 0) {
        window.alert(
          `${failed.length} of ${created.length + failed.length} uploads ` +
            `failed:\n\n` +
            failed.join("\n"),
        );
      }
    },
    onError: (e: Error) => window.alert(`Upload failed: ${e.message}`),
  });

  function onPickPdf(e: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(e.target.files ?? []);
    // Cleared before the upload starts so picking the same file again
    // still fires a change event.
    e.target.value = "";
    if (files.length > 0) uploadPdf.mutate(files);
  }

  // Bulk-download the PDFs of every checked document as a single ZIP.
  const downloadPdfs = useMutation({
    mutationFn: () => downloadItemPdfsZip(slug!, [...checkedIds]),
    onSuccess: ({ skipped }) => {
      if (skipped > 0) {
        window.alert(
          `${skipped} PDF${skipped === 1 ? "" : "s"} could not be fetched ` +
            "from storage and were left out (see _MISSING_FILES.txt in the " +
            "ZIP).",
        );
      }
    },
    onError: (e: Error) => window.alert(`Download failed: ${e.message}`),
  });

  const updateWithCollections = useMutation({
    mutationFn: async (payload: ItemFormSubmission) => {
      const id = selected!.id;
      const item = await updateItem(id, {
        item_type: payload.item_type,
        data: payload.data,
      });
      await setItemCollections(id, payload.collection_ids);
      const tagIds = await resolveTagNamesToIds(payload.tag_names);
      await setItemTags(id, tagIds);
      return item;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["items", slug] });
      qc.invalidateQueries({ queryKey: ["tags", slug] });
      setFormMode("closed");
    },
  });

  const trashOne = useMutation({
    mutationFn: (id: string) => deleteItem(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["items", slug] }),
  });

  const restoreOne = useMutation({
    mutationFn: (id: string) => restoreItem(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["items", slug] }),
  });

  const permanentOne = useMutation({
    mutationFn: (id: string) => permanentDeleteItem(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["items", slug] }),
  });

  const bulkTrash = useMutation({
    mutationFn: async (ids: string[]) => {
      await Promise.all(ids.map((id) => deleteItem(id)));
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["items", slug] });
      setCheckedIds(new Set());
    },
  });
  const bulkRestore = useMutation({
    mutationFn: async (ids: string[]) => {
      await Promise.all(ids.map((id) => restoreItem(id)));
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["items", slug] });
      setCheckedIds(new Set());
    },
  });
  const bulkPermanent = useMutation({
    mutationFn: async (ids: string[]) => {
      await Promise.all(ids.map((id) => permanentDeleteItem(id)));
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["items", slug] });
      setCheckedIds(new Set());
    },
  });

  function onFormSubmit(payload: ItemFormSubmission) {
    if (formMode === "edit" && selected) updateWithCollections.mutate(payload);
    else if (formMode === "create") createWithCollections.mutate(payload);
  }

  function onDeleteSelected() {
    if (!selected) return;
    if (
      view === "library" &&
      !window.confirm(`Move "${itemTitle(selected)}" to trash?`)
    ) {
      return;
    }
    trashOne.mutate(selected.id);
  }

  function onRestoreSelected() {
    if (!selected) return;
    restoreOne.mutate(selected.id);
  }

  function onPermanentSelected() {
    if (!selected) return;
    if (
      !window.confirm(
        `Permanently delete "${itemTitle(selected)}"? This cannot be undone.`,
      )
    ) {
      return;
    }
    permanentOne.mutate(selected.id);
  }

  function toggleChecked(id: string) {
    setCheckedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleAll() {
    // Only toggles across rows currently loaded — selecting "all"
    // when there are 5000 unloaded items would otherwise mean a
    // confusing "checked but not yet visible" state.
    if (selectableItems.length === 0) return;
    if (checkedIds.size === selectableItems.length) {
      setCheckedIds(new Set());
    } else {
      setCheckedIds(new Set(selectableItems.map((i) => i.id)));
    }
  }

  function bulkTrashChecked() {
    const ids = [...checkedIds];
    if (ids.length === 0) return;
    if (!window.confirm(`Move ${ids.length} item${ids.length === 1 ? "" : "s"} to trash?`)) {
      return;
    }
    bulkTrash.mutate(ids);
  }
  function bulkRestoreChecked() {
    const ids = [...checkedIds];
    if (ids.length === 0) return;
    bulkRestore.mutate(ids);
  }
  function bulkPermanentChecked() {
    const ids = [...checkedIds];
    if (ids.length === 0) return;
    if (
      !window.confirm(
        `Permanently delete ${ids.length} item${ids.length === 1 ? "" : "s"}? This cannot be undone.`,
      )
    ) {
      return;
    }
    bulkPermanent.mutate(ids);
  }

  const filtersActive =
    !!debouncedQuery.trim() ||
    filterTags.length > 0 ||
    !!collectionParam;
  const allChecked =
    selectableItems.length > 0 &&
    checkedIds.size === selectableItems.length;
  const someChecked = checkedIds.size > 0 && !allChecked;
  const bulkBusy =
    bulkTrash.isPending || bulkRestore.isPending || bulkPermanent.isPending;

  // Heading text reflects the rail selection so users know what slice
  // they're looking at.
  const headerLabel = useMemo(() => {
    if (view === "trash") return "Trash";
    if (collectionParam === "unfiled") return "Unfiled";
    if (collectionParam) {
      const c = collections.data?.find((c) => c.id === collectionParam);
      return c?.name ?? "Collection";
    }
    return personal ? personal.name : "Library";
  }, [view, collectionParam, collections.data, personal]);

  // Description strip below the title — only when a real collection is
  // selected and it has one set.
  const headerDescription = useMemo<string | null>(() => {
    if (view === "trash" || !collectionParam || collectionParam === "unfiled") {
      return null;
    }
    const c = collections.data?.find((c) => c.id === collectionParam);
    return c?.description?.trim() || null;
  }, [view, collectionParam, collections.data]);

  // Ancestor chain — root → … → direct parent. Drives the
  // "↑ A / B / C" breadcrumb above the current collection title.
  // Empty when at the library root or viewing a top-level collection.
  const ancestorChain = useMemo<Collection[]>(() => {
    if (view === "trash" || !collectionParam || collectionParam === "unfiled") {
      return [];
    }
    const byId = new Map(
      (collections.data ?? []).map((c) => [c.id, c] as const),
    );
    const chain: Collection[] = [];
    let cursor = byId.get(collectionParam)?.parent_id ?? null;
    const seen = new Set<string>();
    while (cursor && !seen.has(cursor)) {
      seen.add(cursor);
      const node = byId.get(cursor);
      if (!node) break;
      chain.unshift(node);
      cursor = node.parent_id;
    }
    return chain;
  }, [view, collectionParam, collections.data]);

  // On mobile, when a row is tapped the detail takes over the whole
  // viewport. The desktop aside (hidden md:block) is the desktop
  // path; this branch is the mobile path.
  if (isMobile && selected) {
    return (
      <AppShell>
        <div className="flex h-full flex-col">
          <div
            className="flex items-center gap-2 border-b px-3 py-2"
            style={{ borderColor: "var(--color-border)" }}
          >
            <button
              onClick={() => setSelectedId(null)}
              className="flex items-center gap-1 rounded px-2 py-1 text-xs hover:opacity-80"
              style={{ color: "var(--color-text-muted)" }}
            >
              <ArrowLeft className="h-3.5 w-3.5" />
              Back
            </button>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto">
            <ItemDetail
              item={selected}
              collections={collections.data ?? []}
              tagNames={selected ? tagNamesById(selected.tag_ids) : []}
              spaceSlug={slug}
              spaceIsOwned={activeSpace?.is_owner ?? false}
              canWrite={canEditSelected}
              homeSpaceName={selectedItemSpace?.name ?? null}
              onEdit={() => setFormMode("edit")}
              onDelete={onDeleteSelected}
              onRestore={view === "trash" ? onRestoreSelected : undefined}
              onPermanentDelete={
                view === "trash" ? onPermanentSelected : undefined
              }
              onTagClick={addTag}
              onCollectionClick={(id) =>
                setSelection({ view: "library", collection: id })
              }
              onSelectItem={setSelectedId}
            />
          </div>
        </div>

        <ItemForm
          open={formMode !== "closed"}
          title={formMode === "edit" ? "Edit Item" : "New Item"}
          slug={slug}
          initial={
            formMode === "edit" && selected
              ? {
                  item_type: selected.item_type as ItemType,
                  data: selected.data,
                  collection_ids: selected.collection_ids,
                  tag_names: tagNamesById(selected.tag_ids),
                }
              : undefined
          }
          loading={
            createWithCollections.isPending || updateWithCollections.isPending
          }
          onSubmit={onFormSubmit}
          onClose={() => setFormMode("closed")}
        />
      </AppShell>
    );
  }

  return (
    <AppShell>
      <div className="flex h-full">
        {railOpen && (
          <CollectionRail
            slug={slug}
            selection={{ view, collection: collectionParam }}
            onSelect={(s) => {
              setSelection(s);
              // On phones the rail steals the whole screen; auto-
              // close after picking so the user lands on the table.
              if (
                window.matchMedia &&
                !window.matchMedia("(min-width: 768px)").matches
              ) {
                setRailOpen(false);
              }
            }}
          />
        )}

        <div className="flex min-w-0 flex-1 flex-col">
          <div
            className="flex flex-wrap items-center gap-3 border-b px-4 py-2"
            style={{ borderColor: "var(--color-border)" }}
          >
            <button
              onClick={() => setRailOpen((v) => !v)}
              aria-label={railOpen ? "Hide collections" : "Show collections"}
              title={railOpen ? "Hide collections" : "Show collections"}
              className="rounded p-1 hover:opacity-70"
              style={{ color: "var(--color-text-muted)" }}
            >
              {railOpen ? (
                <PanelLeftClose className="h-4 w-4" />
              ) : (
                <PanelLeftOpen className="h-4 w-4" />
              )}
            </button>
            <div className="min-w-0">
              {ancestorChain.length > 0 && (
                <div
                  className="flex max-w-full items-center gap-1 text-xs"
                  style={{ color: "var(--color-text-muted)" }}
                >
                  <ArrowUp className="h-3 w-3 shrink-0" />
                  {ancestorChain.map((a, i) => (
                    <span key={a.id} className="flex min-w-0 items-center gap-1">
                      {i > 0 && <span className="shrink-0 opacity-60">/</span>}
                      <button
                        onClick={() =>
                          setSelection({
                            view: "library",
                            collection: a.id,
                          })
                        }
                        className="truncate rounded hover:opacity-80"
                        title={`Go to ${a.name}`}
                      >
                        {a.name}
                      </button>
                    </span>
                  ))}
                </div>
              )}
              <h1 className="text-base font-semibold">{headerLabel}</h1>
              {headerDescription && (
                <p
                  className="truncate text-xs italic"
                  style={{ color: "var(--color-text-muted)" }}
                  title={headerDescription}
                >
                  {headerDescription}
                </p>
              )}
              <p
                className="text-xs"
                style={{ color: "var(--color-text-muted)" }}
              >
                {itemsTotal ?? 0} item
                {itemsTotal === 1 ? "" : "s"}
                {filtersActive ? " · filtered" : ""}
                {spaces.data && spaces.data.length > 1
                  ? ` · ${spaces.data.length} spaces`
                  : ""}
              </p>
            </div>

            <div
              className="flex min-w-[220px] flex-1 items-center gap-2 rounded border px-2"
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
                value={searchInput}
                onChange={(e) => setSearchInput(e.target.value)}
                placeholder={view === "trash" ? "Search trash…" : "Search…"}
                className="flex-1 bg-transparent py-1 text-sm outline-none"
                style={{ color: "var(--color-text)" }}
              />
              {searchInput && (
                <button
                  onClick={() => setSearchInput("")}
                  aria-label="Clear search"
                  className="rounded p-0.5 hover:opacity-70"
                  style={{ color: "var(--color-text-muted)" }}
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              )}
              <SearchScopePopover
                scope={searchScope}
                onChange={setSearchScope}
              />
            </div>

            <div className="flex items-center gap-2">
              {spaces.data && spaces.data.length > 1 && (
                <select
                  value={slug ?? ""}
                  onChange={(e) => switchSpace(e.target.value)}
                  className="rounded border px-2 py-1 text-sm"
                  style={{
                    backgroundColor: "var(--color-surface)",
                    borderColor: "var(--color-border)",
                    color: "var(--color-text)",
                  }}
                >
                  {spaces.data.map((s) => (
                    <option key={s.id} value={s.slug}>
                      {s.name}
                    </option>
                  ))}
                </select>
              )}
              {view === "library" && (
                <>
                  {checkedIds.size > 0 && (
                    <button
                      disabled={!slug || downloadPdfs.isPending}
                      onClick={() => downloadPdfs.mutate()}
                      className="flex items-center gap-1 rounded border px-3 py-1.5 text-sm font-medium disabled:opacity-50"
                      style={{
                        borderColor: "var(--color-border)",
                        color: "var(--color-text)",
                      }}
                      title={`Download the PDFs of ${checkedIds.size} selected document${
                        checkedIds.size === 1 ? "" : "s"
                      } as a ZIP`}
                    >
                      {downloadPdfs.isPending ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                      ) : (
                        <Download className="h-4 w-4" />
                      )}
                      <span className="hidden sm:inline">
                        {downloadPdfs.isPending ? "Zipping…" : "Download PDFs"}
                      </span>
                      <span>({checkedIds.size})</span>
                    </button>
                  )}
                  <button
                    disabled={!slug || !writable || uploadPdf.isPending}
                    onClick={() => pdfInput.current?.click()}
                    className="flex items-center gap-1 rounded border px-3 py-1.5 text-sm font-medium disabled:opacity-50"
                    style={{
                      borderColor: "var(--color-border)",
                      color: "var(--color-text)",
                    }}
                    title="Upload one or more PDFs, one new document each"
                  >
                    {uploadPdf.isPending ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      <Upload className="h-4 w-4" />
                    )}
                    <span className="hidden sm:inline">
                      {uploadPdf.isPending
                        ? pdfProgress && pdfProgress.total > 1
                          ? // done counts finished files; the one in
                            // flight is the next, clamped so the last
                            // doesn't read "4/3".
                            `Uploading ${Math.min(
                              pdfProgress.done + 1,
                              pdfProgress.total,
                            )}/${pdfProgress.total}…`
                          : "Uploading…"
                        : "Upload PDFs"}
                    </span>
                  </button>
                  <input
                    ref={pdfInput}
                    type="file"
                    multiple
                    accept="application/pdf,.pdf"
                    onChange={onPickPdf}
                    className="hidden"
                  />
                  <button
                    disabled={!slug || !writable}
                    onClick={() => setFormMode("create")}
                    title={
                      writable
                        ? undefined
                        : "You have read-only access to this space"
                    }
                    className="flex items-center gap-1 rounded px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
                    style={{ backgroundColor: "var(--color-accent)" }}
                  >
                    <Plus className="h-4 w-4" />
                    <span className="hidden sm:inline">New item</span>
                  </button>
                </>
              )}
            </div>
          </div>

          {activeSpace && !writable && (
            <div
              className="border-b px-4 py-1.5 text-xs"
              style={{
                borderColor: "var(--color-border)",
                color: "var(--color-text-muted)",
              }}
            >
              Shared with you as a viewer — you can read and export this
              space, but not change it.
            </div>
          )}

          {pinnedCount > 0 && (
            <div
              className="flex flex-wrap items-center gap-2 border-b px-4 py-1.5 text-xs"
              style={{
                borderColor: "var(--color-border)",
                color: "var(--color-text-muted)",
              }}
            >
              <span>
                {showAllRevisions
                  ? `Showing every edition of ${pinnedCount} pinned standard${pinnedCount === 1 ? "" : "s"}.`
                  : `${pinnedCount} standard${pinnedCount === 1 ? "" : "s"} pinned to one edition; the others are hidden.`}
              </span>
              <button
                onClick={toggleAllRevisions}
                className="rounded border px-2 py-0.5 text-xs hover:opacity-80"
                style={{ borderColor: "var(--color-border)" }}
              >
                {showAllRevisions
                  ? "Show pinned only"
                  : "Show all revisions"}
              </button>
            </div>
          )}

          {filterTags.length > 0 && (
            <div
              className="flex flex-wrap items-center gap-2 border-b px-4 py-2 text-xs"
              style={{ borderColor: "var(--color-border)" }}
            >
              <span style={{ color: "var(--color-text-muted)" }}>
                Filtering by:
              </span>
              {filterTags.map((t) => (
                <span
                  key={t}
                  className="inline-flex items-center gap-1 rounded-full px-2 py-0.5"
                  style={{
                    backgroundColor:
                      "color-mix(in srgb, var(--color-accent) 15%, transparent)",
                    color: "var(--color-accent)",
                  }}
                >
                  {t}
                  <button
                    onClick={() => removeTag(t)}
                    aria-label={`Remove ${t} filter`}
                    className="rounded-full hover:opacity-70"
                  >
                    <X className="h-3 w-3" />
                  </button>
                </span>
              ))}
            </div>
          )}

          {checkedIds.size > 0 && (
            <div
              className="flex flex-wrap items-center gap-3 border-b px-4 py-2"
              style={{
                borderColor: "var(--color-border)",
                backgroundColor:
                  "color-mix(in srgb, var(--color-accent) 8%, transparent)",
              }}
            >
              <span className="text-xs">{checkedIds.size} selected</span>
              {view === "library" ? (
                <>
                  <BulkAddToCollection
                    items={loadedItems.filter((i) => checkedIds.has(i.id))}
                    collections={collections.data ?? []}
                    slug={slug}
                  />
                  <BulkCopyToSpace
                    items={loadedItems.filter((i) => checkedIds.has(i.id))}
                    slug={slug}
                  />
                  <button
                    onClick={bulkTrashChecked}
                    disabled={bulkBusy}
                    className="flex items-center gap-1 rounded px-2 py-1 text-xs hover:bg-red-500/10 disabled:opacity-50"
                    style={{ color: "var(--color-text)" }}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                    Move to trash
                  </button>
                </>
              ) : (
                <>
                  <button
                    onClick={bulkRestoreChecked}
                    disabled={bulkBusy}
                    className="flex items-center gap-1 rounded px-2 py-1 text-xs hover:opacity-70 disabled:opacity-50"
                    style={{ color: "var(--color-accent)" }}
                  >
                    <RotateCcw className="h-3.5 w-3.5" />
                    Restore
                  </button>
                  <button
                    onClick={bulkPermanentChecked}
                    disabled={bulkBusy}
                    className="flex items-center gap-1 rounded px-2 py-1 text-xs text-red-500 hover:bg-red-500/10 disabled:opacity-50"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                    Delete permanently
                  </button>
                </>
              )}
              <button
                onClick={() => setCheckedIds(new Set())}
                className="ml-auto text-xs hover:opacity-70"
                style={{ color: "var(--color-text-muted)" }}
              >
                Clear
              </button>
            </div>
          )}

          <div className="flex min-h-0 flex-1">
            <div
              className="flex-1 min-w-0 overflow-auto"
              style={{ backgroundColor: "var(--color-surface)" }}
            >
              {items.isLoading && (
                <p
                  className="p-6 text-sm"
                  style={{ color: "var(--color-text-muted)" }}
                >
                  Loading items…
                </p>
              )}
              {items.error && (
                <p className="p-6 text-sm text-red-600">
                  {(items.error as Error).message}
                </p>
              )}
              {!items.isLoading && itemsTotal === 0 && (
                <p
                  className="p-6 text-sm"
                  style={{ color: "var(--color-text-muted)" }}
                >
                  {filtersActive
                    ? "No items match the current filters."
                    : view === "trash"
                    ? "Trash is empty."
                    : "No items yet — click \"New item\" to add one."}
                </p>
              )}
              {flatItems.length > 0 && (
                <table className="w-full text-sm">
                  <thead
                    className="sticky top-0 border-b text-left text-xs uppercase tracking-wider"
                    style={{
                      backgroundColor: "var(--color-surface)",
                      color: "var(--color-text-muted)",
                      borderColor: "var(--color-border)",
                    }}
                  >
                    <tr>
                      <th className="w-8 px-2 py-2">
                        <input
                          type="checkbox"
                          checked={allChecked}
                          ref={(el) => {
                            if (el) el.indeterminate = someChecked;
                          }}
                          onChange={toggleAll}
                          aria-label="Select all"
                          className="cursor-pointer"
                        />
                      </th>
                      <SortHeader
                        label="Title"
                        column="title"
                        activeSort={sort}
                        direction={direction}
                        onClick={applySort}
                      />
                      <th className="px-4 py-2">Creator</th>
                      <SortHeader
                        label="Type"
                        column="type"
                        activeSort={sort}
                        direction={direction}
                        onClick={applySort}
                      />
                      <th className="px-4 py-2">Tags</th>
                      <SortHeader
                        label={view === "trash" ? "Trashed" : "Updated"}
                        column="updated"
                        activeSort={sort}
                        direction={direction}
                        onClick={applySort}
                      />
                    </tr>
                  </thead>
                  <tbody ref={listRef}>
                    {listEntries.map((entry) => {
                      if (entry.kind === "subheader") {
                        return (
                          <tr key="subcollections">
                            <td colSpan={6} className="px-0 py-0">
                              <button
                                type="button"
                                onClick={() => setSubOpen((v) => !v)}
                                aria-expanded={subOpen}
                                className="flex w-full items-center gap-1.5 border-t px-3 py-2 text-left text-xs uppercase tracking-widest hover:opacity-80"
                                style={{
                                  borderColor: "var(--color-border)",
                                  color: "var(--color-text-muted)",
                                }}
                              >
                                {subOpen ? (
                                  <ChevronDown className="h-3.5 w-3.5" />
                                ) : (
                                  <ChevronRight className="h-3.5 w-3.5" />
                                )}
                                In subcollections ({entry.count})
                                {entry.loaded < entry.count && subOpen && (
                                  <span className="normal-case tracking-normal">
                                    — showing the first {entry.loaded}; open
                                    the subcollection to see the rest
                                  </span>
                                )}
                              </button>
                            </td>
                          </tr>
                        );
                      }
                      if (entry.kind === "header") {
                        return (
                          <tr
                            key={`header-${entry.scope}`}
                            className="border-b"
                            style={{
                              borderColor: "var(--color-border)",
                              backgroundColor:
                                "color-mix(in srgb, var(--color-text-muted) 6%, transparent)",
                            }}
                          >
                            <td
                              colSpan={6}
                              className="px-4 py-1.5 text-xs uppercase tracking-wider"
                              style={{ color: "var(--color-text-muted)" }}
                            >
                              {SEARCH_SCOPE_LABELS[entry.scope]} ·{" "}
                              {entry.count}
                            </td>
                          </tr>
                        );
                      }
                      if (entry.kind === "hits") {
                        return (
                          <FulltextHitsRow
                            key={`hits-${entry.itemId}`}
                            itemId={entry.itemId}
                            query={debouncedQuery}
                            colSpan={6}
                            onOpen={setSelectedId}
                          />
                        );
                      }
                      const it = entry.item;
                      const isSelected = it.id === selectedId;
                      const isChecked = checkedIds.has(it.id);
                      const isFulltextRow = entry.scope === "fulltext";
                      const isExpanded = expandedFulltextIds.has(it.id);
                      return (
                        <tr
                          key={it.id}
                          data-item-row={it.id}
                          onClick={(e) => {
                            if (e.ctrlKey || e.metaKey) openInReader(it);
                            else setSelectedId(it.id);
                          }}
                          className="cursor-pointer border-b last:border-b-0 hover:opacity-90"
                          style={{
                            borderColor: "var(--color-border)",
                            backgroundColor: isSelected
                              ? "color-mix(in srgb, var(--color-accent) 12%, transparent)"
                              : undefined,
                          }}
                        >
                          <td
                            className="w-8 px-2 py-2"
                            onClick={(e) => e.stopPropagation()}
                          >
                            <input
                              type="checkbox"
                              checked={isChecked}
                              onChange={() => toggleChecked(it.id)}
                              aria-label="Select item"
                              className="cursor-pointer"
                            />
                          </td>
                          <td className="px-4 py-2">
                            <div className="flex items-center gap-1.5">
                              {isFulltextRow ? (
                                <button
                                  type="button"
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    toggleFulltextExpanded(it.id);
                                  }}
                                  aria-label={
                                    isExpanded
                                      ? "Hide PDF hits"
                                      : "Show PDF hits"
                                  }
                                  aria-expanded={isExpanded}
                                  className="rounded p-0.5 hover:opacity-70"
                                  style={{
                                    color: "var(--color-text-muted)",
                                  }}
                                >
                                  {isExpanded ? (
                                    <ChevronDown className="h-3.5 w-3.5" />
                                  ) : (
                                    <ChevronRight className="h-3.5 w-3.5" />
                                  )}
                                </button>
                              ) : (
                                // Fixed-width spacer so titles in
                                // non-fulltext groups still align with
                                // their fulltext-group siblings.
                                <span className="inline-block w-[18px]" />
                              )}
                              <span title="Ctrl+click to open the PDF">
                                {itemTitle(it)}
                              </span>
                            </div>
                          </td>
                          <td
                            className="px-4 py-2 text-xs"
                            style={{ color: "var(--color-text-muted)" }}
                          >
                            {creatorSummary(it)}
                          </td>
                          <td
                            className="px-4 py-2 text-xs"
                            style={{ color: "var(--color-text-muted)" }}
                          >
                            {itemTypeLabel(it.item_type)}
                          </td>
                          <td className="px-4 py-2">
                            <TagChips
                              tags={tagNamesById(it.tag_ids)}
                              max={3}
                              size="xs"
                              onClick={(t) => addTag(t)}
                            />
                          </td>
                          <td
                            className="px-4 py-2 text-xs"
                            style={{ color: "var(--color-text-muted)" }}
                          >
                            {formatDate(
                              view === "trash"
                                ? it.deleted_at ?? it.updated_at
                                : it.updated_at,
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              )}
              {/* Infinite-scroll sentinel. Rendered whenever there's
                  still data to fetch; observed via IntersectionObserver
                  to trigger fetchNextPage. The visible footer line is
                  optional polish — useful on slow links where it's
                  obvious work is happening. */}
              {items.hasNextPage && (
                <div
                  ref={loadMoreRef}
                  className="px-6 py-4 text-center text-xs"
                  style={{ color: "var(--color-text-muted)" }}
                >
                  {items.isFetchingNextPage ? "Loading more…" : ""}
                </div>
              )}
            </div>

            {/* Drag handle for the detail pane. Sits in the flex row
                rather than inside the pane so it doesn't scroll away
                with the content, and is a `separator` so keyboard users
                get it too. */}
            <div
              role="separator"
              aria-orientation="vertical"
              aria-label="Resize the detail panel"
              tabIndex={0}
              onPointerDown={detailWidth.onPointerDown}
              onKeyDown={detailWidth.onKeyDown}
              onDoubleClick={detailWidth.reset}
              title="Drag to resize · double-click to reset"
              className="hidden w-1 shrink-0 cursor-col-resize md:block"
              style={{
                backgroundColor: detailWidth.dragging
                  ? "var(--color-accent)"
                  : "var(--color-border)",
              }}
            />

            <aside
              className="hidden w-[360px] shrink-0 border-l md:block lg:w-[420px]"
              style={{
                borderColor: "var(--color-border)",
                backgroundColor: "var(--color-surface)",
                // Unset until dragged, so the responsive default stands.
                ...(detailWidth.width !== null
                  ? { width: detailWidth.width }
                  : {}),
              }}
            >
              <ItemDetail
                item={selected}
                collections={collections.data ?? []}
                tagNames={selected ? tagNamesById(selected.tag_ids) : []}
                spaceSlug={slug}
                spaceIsOwned={activeSpace?.is_owner ?? false}
                canWrite={canEditSelected}
                homeSpaceName={selectedItemSpace?.name ?? null}
                onEdit={() => setFormMode("edit")}
                onDelete={onDeleteSelected}
                onRestore={view === "trash" ? onRestoreSelected : undefined}
                onPermanentDelete={
                  view === "trash" ? onPermanentSelected : undefined
                }
                onTagClick={addTag}
                onCollectionClick={(id) =>
                  setSelection({ view: "library", collection: id })
                }
                onSelectItem={setSelectedId}
              />
            </aside>
          </div>
        </div>
      </div>

      <ItemForm
        open={formMode !== "closed"}
        title={formMode === "edit" ? "Edit Item" : "New Item"}
        slug={slug}
        initial={
          formMode === "edit" && selected
            ? {
                item_type: selected.item_type as ItemType,
                data: selected.data,
                collection_ids: selected.collection_ids,
                tag_names: tagNamesById(selected.tag_ids),
              }
            : undefined
        }
        loading={
          createWithCollections.isPending || updateWithCollections.isPending
        }
        onSubmit={onFormSubmit}
        onClose={() => setFormMode("closed")}
      />
    </AppShell>
  );
}
