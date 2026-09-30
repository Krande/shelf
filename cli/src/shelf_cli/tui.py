"""`shelf browse` — the landing page's search, in a terminal.

One list: each matching document, and under it the PDF pages its body
matched, the way the landing page groups them. Enter opens the hit in
the shelf reader at that page; `o` opens the PDF locally at that page
(see `opener.py` for how, and why that is harder than it sounds).

Kept quick on purpose, because a search box that lags behind typing is
worse than none:

* Textual is imported only by this module, and this module only by the
  `browse` command, so the rest of the CLI starts as fast as it did.
* Typing is debounced, and each new query *cancels* the request before
  it (an exclusive async worker), rather than letting stale answers race
  the current one onto the screen.
* One keep-alive connection for the session, one request per query:
  `/search?hits=N` returns every row's first pages alongside the rows.
* Answers are cached per query, so backspacing to an earlier query is
  instant. Expanding a document's full page list is one request, once.
* The list is an OptionList, which renders only the lines on screen.

The sidebar (`c`) lists spaces and their collections; picking one makes
it where searches look, and `backspace` goes back up a level. It is
fetched once, two requests side by side, and branches are only built
when opened.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, ClassVar

import httpx
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal
from textual.timer import Timer
from textual.widgets import Footer, Input, OptionList, Static, Tree
from textual.widgets.option_list import Option
from textual.widgets.tree import TreeNode

from . import opener
from .client import ApiError, AsyncShelfClient, ShelfClient
from .config import Config

PAGE_SIZE = 50
# Pages shown under each document before "N more"; the rest are one
# keypress away. Three is what fits without the list becoming all hits.
INLINE_HITS = 3
DEBOUNCE_S = 0.18
MIN_QUERY = 2
CACHE_QUERIES = 64
# Leading context kept in a list row, so the match itself is on screen
# rather than scrolled off the right edge. The preview has all of it.
ROW_CONTEXT = 24


# ── where to look ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Scope:
    """Where searches look: everywhere, one space, or one collection.

    A collection with an empty query lists what is filed *directly* in
    it, like opening a folder; with a query it searches the whole subtree,
    since "find it somewhere under FEM" is why you'd type one there.
    """

    label: str = "All spaces"
    space: str | None = None  # slug
    collection: str | None = None  # id
    parent: Scope | None = None

    @property
    def key(self) -> tuple[str | None, str | None]:
        return (self.space, self.collection)

    def search_args(self, q: str) -> dict[str, Any]:
        if self.collection:
            return {"collection": self.collection, "subtree": bool(q)}
        if self.space:
            return {"space": self.space}
        return {}


EVERYWHERE = Scope()


@dataclass
class NavEntry:
    """What a sidebar node stands for. Children are added when a node is
    first expanded, so a library with thousands of collections costs one
    request up front and nothing to draw until someone opens a branch."""

    scope: Scope
    space_id: str | None = None
    collection_id: str | None = None
    loaded: bool = False


class NavTree(Tree[NavEntry]):
    """The sidebar, with the arrow keys a file explorer has: → opens a
    branch (or steps into an open one), ← closes it (or steps out to
    the parent)."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("right", "open_branch", "Expand", show=False),
        Binding("left", "close_branch", "Collapse", show=False),
    ]

    def action_open_branch(self) -> None:
        node = self.cursor_node
        if node is None or not node.allow_expand:
            return
        if not node.is_expanded:
            node.expand()
        elif node.children:
            self.move_cursor(node.children[0])

    def action_close_branch(self) -> None:
        node = self.cursor_node
        if node is None:
            return
        if node.is_expanded and node.allow_expand:
            node.collapse()
        elif node.parent is not None:
            self.move_cursor(node.parent)


# ── rows ─────────────────────────────────────────────────────────────────


@dataclass
class Result:
    item: dict[str, Any]
    hits: list[dict[str, Any]]
    hit_count: int
    expanded: bool = False


@dataclass
class Results:
    query: str
    scope: Scope = EVERYWHERE
    rows: list[Result] = field(default_factory=list)
    exhausted: bool = False
    elapsed_ms: int = 0


@dataclass(frozen=True)
class ItemRow:
    result: Result


@dataclass(frozen=True)
class HitRow:
    result: Result
    hit: dict[str, Any]


@dataclass(frozen=True)
class MoreHitsRow:
    result: Result


@dataclass(frozen=True)
class LoadMoreRow:
    pass


Row = ItemRow | HitRow | MoreHitsRow | LoadMoreRow


def title_of(item: dict[str, Any]) -> str:
    data = item.get("data") or {}
    return str(data.get("title") or data.get("designation") or "(untitled)")


def _item_prompt(result: Result) -> Text:
    item = result.item
    data = item.get("data") or {}
    text = Text(no_wrap=True, overflow="ellipsis")
    marker = "▾ " if result.expanded else "▸ " if result.hit_count > len(result.hits) else "  "
    text.append(marker, style="dim")
    text.append(title_of(item), style="bold")
    extra = [str(data[k]) for k in ("designation", "edition") if data.get(k)]
    extra.append(item.get("item_type", ""))
    text.append("  " + " · ".join(e for e in extra if e), style="dim")
    if result.hit_count:
        pages = "page" if result.hit_count == 1 else "pages"
        text.append(f"  {result.hit_count} {pages}", style="cyan")
    return text


def _hit_prompt(hit: dict[str, Any]) -> Text:
    snippet: str = hit["snippet"]
    start, end = hit["highlight"]
    cut = max(0, start - ROW_CONTEXT)
    if cut:
        # Break at a word, so the row doesn't open on half of one.
        space = snippet.find(" ", cut, start)
        cut = space + 1 if space != -1 else cut
    shown = ("…" if cut else "") + snippet[cut:]
    offset = (1 if cut else 0) - cut

    text = Text(no_wrap=True, overflow="ellipsis")
    text.append(f"    p.{hit['page_number']:<5}", style="cyan")
    body = Text(shown, style="dim")
    body.stylize("bold reverse", start + offset, end + offset)
    text.append_text(body)
    return text


def _more_prompt(result: Result) -> Text:
    rest = result.hit_count - len(result.hits)
    return Text(f"    … {rest} more — enter to show all", style="dim italic")


def build_rows(results: Results) -> list[Row]:
    rows: list[Row] = []
    for result in results.rows:
        rows.append(ItemRow(result))
        rows.extend(HitRow(result, h) for h in result.hits)
        if result.hit_count > len(result.hits):
            rows.append(MoreHitsRow(result))
    if results.rows and not results.exhausted:
        rows.append(LoadMoreRow())
    return rows


def _prompt(row: Row) -> Text:
    match row:
        case ItemRow(result):
            return _item_prompt(result)
        case HitRow(_, hit):
            return _hit_prompt(hit)
        case MoreHitsRow(result):
            return _more_prompt(result)
        case LoadMoreRow():
            return Text("  loading more…", style="dim italic")


# ── app ──────────────────────────────────────────────────────────────────


class ShelfBrowser(App[None]):
    TITLE = "shelf"
    CSS = """
    Screen { layout: vertical; }
    #query { dock: top; margin: 0 0 1 0; }
    #body { height: 1fr; }
    #nav { width: 32; border-right: solid $panel; padding-right: 1; }
    #results { width: 3fr; border: none; }
    #preview { width: 2fr; padding: 0 1 0 2; border-left: solid $panel; }
    #status { height: 1; padding: 0 1; color: $text-muted; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("enter", "open_web", "Open in shelf", show=True, priority=False),
        Binding("o", "open_local", "Open PDF locally"),
        Binding("O", "open_local_fresh", "Re-download & open", show=False),
        Binding("space", "expand", "All matches"),
        Binding("c", "collections", "Spaces & collections"),
        Binding("backspace", "scope_up", "Up a level", show=False),
        Binding("slash", "focus_query", "Search"),
        Binding("escape", "step_back", "Back", show=False),
        Binding("q", "leave", "Quit"),
    ]

    def __init__(self, config: Config, initial_query: str = "") -> None:
        super().__init__()
        self.config = config
        self.initial_query = initial_query
        self.api = AsyncShelfClient(config.base_url, config.token)
        self._sync: ShelfClient | None = None
        self._cache: OrderedDict[tuple[tuple[str | None, str | None], str], Results] = OrderedDict()
        self._hits_cache: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self._scope = EVERYWHERE
        self._results = Results(query="")
        self._rows: list[Row] = []
        self._debounce: Timer | None = None
        # (space id, parent collection id or None) -> child collections.
        self._children: dict[tuple[str, str | None], list[dict[str, Any]]] = {}
        # None until the user toggles it; until then width decides.
        self._nav_wanted: bool | None = None
        self._nav = NavTree("All spaces", data=NavEntry(EVERYWHERE), id="nav")
        self._nav.show_root = True
        # Held rather than queried: messages can still arrive while the
        # app shuts down, after the DOM a query would search has gone.
        # Empty here; the initial query goes in on mount. An Input given a
        # value before the app is running reaches for the app to clear its
        # selection and raises NoActiveAppError (textual 8), which made
        # `shelf browse <query>` crash on start.
        self._input = Input(
            placeholder="Search titles, creators and PDF text…",
            id="query",
        )
        self._list = OptionList(id="results")
        self._preview = Static(id="preview")
        self._status_bar = Static(id="status")

    # ── layout ───────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield self._input
        with Horizontal(id="body"):
            yield self._nav
            yield self._list
            yield self._preview
        yield self._status_bar
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = self.config.base_url
        self._input.border_title = self._scope.label
        self._load_nav()
        # Silently: the search below is the one to run, and a Changed
        # event would schedule a second, debounced one for the same text.
        with self._input.prevent(Input.Changed):
            self._input.value = self.initial_query
        self._search_now(self.initial_query)
        if self.initial_query:
            self._list.focus()

    def on_resize(self) -> None:
        # A preview narrower than a sentence helps nobody.
        self._preview.display = self.size.width >= 100
        self._nav.display = (
            self._nav_wanted if self._nav_wanted is not None else self.size.width >= 130
        )

    async def on_unmount(self) -> None:
        await self.api.aclose()
        if self._sync is not None:
            self._sync.close()

    # ── searching ────────────────────────────────────────────────────────

    def on_input_changed(self, event: Input.Changed) -> None:
        if self._debounce is not None:
            self._debounce.stop()
        value = event.value
        self._debounce = self.set_timer(DEBOUNCE_S, lambda: self._search_now(value))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if self._debounce is not None:
            self._debounce.stop()
        self._search_now(event.value)
        self._list.focus()

    def _search_now(self, raw: str) -> None:
        q = raw.strip()
        if q and len(q) < MIN_QUERY:
            return  # one letter matches half the library's text
        scope = self._scope
        if q == self._results.query and scope == self._results.scope and self._rows:
            return
        key = (scope.key, q)
        if (cached := self._cache.get(key)) is not None:
            # A search still in flight is for an older query; it must not
            # land on top of this one.
            self.workers.cancel_group(self, "search")
            self._cache.move_to_end(key)
            self._show(cached, note="cached")
            return
        self._status("searching…")
        self._search(q, scope)

    @work(exclusive=True, group="search")
    async def _search(self, q: str, scope: Scope) -> None:
        started = time.perf_counter()
        try:
            rows = await self.api.search(
                q or None,
                PAGE_SIZE,
                hits=INLINE_HITS if q else 0,
                **scope.search_args(q),
            )
        except (ApiError, httpx.HTTPError) as e:
            self._status(str(e), error=True)
            return
        results = Results(
            query=q,
            scope=scope,
            rows=[_to_result(r) for r in rows],
            exhausted=len(rows) < PAGE_SIZE,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
        )
        self._remember(results)
        self._show(results)

    # ── spaces & collections ─────────────────────────────────────────────

    @work(group="nav")
    async def _load_nav(self) -> None:
        """Every space and collection, in two concurrent requests, once."""
        try:
            spaces, collections = await asyncio.gather(self.api.spaces(), self.api.collections())
        except (ApiError, httpx.HTTPError) as e:
            self._status(f"could not list spaces: {e}", error=True)
            return
        for c in collections:
            key = (str(c["space_id"]), str(c["parent_id"]) if c.get("parent_id") else None)
            self._children.setdefault(key, []).append(c)
        # A token narrowed to collections can see a child without its
        # parent; list those at the top of their space rather than lose them.
        visible = {str(c["id"]) for c in collections}
        for c in collections:
            parent = str(c["parent_id"]) if c.get("parent_id") else None
            if parent is not None and parent not in visible:
                self._children.setdefault((str(c["space_id"]), None), []).append(c)

        root = self._nav.root
        for s in spaces:
            space_id = str(s["id"])
            root.add(
                str(s["name"]),
                data=NavEntry(
                    Scope(label=str(s["name"]), space=str(s["slug"]), parent=EVERYWHERE),
                    space_id=space_id,
                ),
                allow_expand=(space_id, None) in self._children,
            )
        root.expand()

    def on_tree_node_expanded(self, event: Tree.NodeExpanded[NavEntry]) -> None:
        self._fill(event.node)

    def _fill(self, node: TreeNode[NavEntry]) -> None:
        entry = node.data
        if entry is None or entry.loaded or entry.space_id is None:
            return
        entry.loaded = True
        for c in self._children.get((entry.space_id, entry.collection_id), []):
            cid = str(c["id"])
            node.add(
                str(c["name"]),
                data=NavEntry(
                    Scope(
                        label=f"{entry.scope.label} / {c['name']}",
                        collection=cid,
                        parent=entry.scope,
                    ),
                    space_id=entry.space_id,
                    collection_id=cid,
                ),
                allow_expand=(entry.space_id, cid) in self._children,
            )

    def on_tree_node_selected(self, event: Tree.NodeSelected[NavEntry]) -> None:
        if event.node.data is not None:
            self._set_scope(event.node.data.scope)
            self._list.focus()

    def _set_scope(self, scope: Scope) -> None:
        if scope == self._scope:
            return
        self._scope = scope
        self._input.border_title = scope.label
        self._search_now(self._input.value)

    def action_collections(self) -> None:
        if self._nav.has_focus:
            self._nav_wanted = False
            self._nav.display = False
            self._list.focus()
        else:
            self._nav_wanted = True
            self._nav.display = True
            self._nav.focus()

    def action_scope_up(self) -> None:
        if self._scope.parent is not None:
            self._set_scope(self._scope.parent)

    @work(exclusive=True, group="more")
    async def _load_more(self) -> None:
        results = self._results
        if results.exhausted:
            return
        try:
            rows = await self.api.search(
                results.query or None,
                PAGE_SIZE,
                offset=len(results.rows),
                hits=INLINE_HITS if results.query else 0,
                **results.scope.search_args(results.query),
            )
        except (ApiError, httpx.HTTPError) as e:
            self._status(str(e), error=True)
            return
        if results is not self._results:
            return  # the query changed while this was in flight
        results.rows.extend(_to_result(r) for r in rows)
        results.exhausted = len(rows) < PAGE_SIZE
        self._render_rows(keep=True)

    def _remember(self, results: Results) -> None:
        key = (results.scope.key, results.query)
        self._cache[key] = results
        self._cache.move_to_end(key)
        while len(self._cache) > CACHE_QUERIES:
            self._cache.popitem(last=False)

    def _show(self, results: Results, note: str | None = None) -> None:
        self._results = results
        self._render_rows(keep=False)
        n = len(results.rows)
        more = "+" if not results.exhausted else ""
        timing = note or f"{results.elapsed_ms} ms"
        scope = results.scope
        if results.query:
            what = f"“{results.query}”" + (
                f" in {scope.label}" if scope.key != (None, None) else ""
            )
        elif scope.collection:
            what = f"filed in {scope.label}"
        elif scope.space:
            what = f"recently updated in {scope.label}"
        else:
            what = "recently updated"
        self._status(f"{n}{more} {'result' if n == 1 else 'results'} · {what} · {timing}")

    def _render_rows(self, *, keep: bool) -> None:
        option_list = self._list
        highlighted = option_list.highlighted if keep else None
        self._rows = build_rows(self._results)
        option_list.set_options(Option(_prompt(row)) for row in self._rows)
        if self._rows:
            option_list.highlighted = (
                min(highlighted, len(self._rows) - 1) if highlighted is not None else 0
            )
        else:
            self._preview.update("")

    # ── selection ────────────────────────────────────────────────────────

    def _current(self) -> Row | None:
        index = self._list.highlighted
        if index is None or not 0 <= index < len(self._rows):
            return None
        return self._rows[index]

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        row = self._current()
        if isinstance(row, LoadMoreRow):
            self._load_more()
        self._preview.update(_preview(row, self._results.query))

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        # OptionList consumes Enter itself; route it to the same action.
        self.action_open_web()

    def action_leave(self) -> None:
        self.exit()

    def action_focus_query(self) -> None:
        self._input.focus()

    def action_step_back(self) -> None:
        query = self._input
        if not query.has_focus:
            query.focus()
        elif query.value:
            query.value = ""
        else:
            self.exit()

    def on_key(self, event: Any) -> None:
        # Down from the search box moves into the results, as it does on
        # the landing page.
        if event.key == "down" and self._input.has_focus and self._rows:
            self._list.focus()
            event.stop()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        # Letters typed into the search box are text, not commands.
        if action in {
            "open_local",
            "open_local_fresh",
            "expand",
            "leave",
            "collections",
            "scope_up",
        }:
            return not self._input.has_focus
        return True

    # ── expanding ────────────────────────────────────────────────────────

    def action_expand(self) -> None:
        row = self._current()
        if isinstance(row, ItemRow | HitRow | MoreHitsRow):
            self._toggle(row.result)

    def _toggle(self, result: Result) -> None:
        if result.expanded:
            result.hits = result.hits[:INLINE_HITS]
            result.expanded = False
            self._render_rows(keep=True)
        elif result.hit_count > len(result.hits):
            self._expand(result)

    @work(group="expand")
    async def _expand(self, result: Result) -> None:
        q = self._results.query
        key = (str(result.item["id"]), q)
        hits = self._hits_cache.get(key)
        if hits is None:
            self._status("fetching pages…")
            try:
                hits = await self.api.item_hits(key[0], q)
            except (ApiError, httpx.HTTPError) as e:
                self._status(str(e), error=True)
                return
            self._hits_cache[key] = hits
        if q != self._results.query:
            return
        result.hits = hits
        result.hit_count = len(hits)
        result.expanded = True
        self._render_rows(keep=True)
        self._status(f"{len(hits)} pages in {title_of(result.item)}")

    # ── opening ──────────────────────────────────────────────────────────

    def action_open_web(self) -> None:
        if self._input.has_focus:
            return
        row = self._current()
        if isinstance(row, MoreHitsRow):
            self._toggle(row.result)
        elif isinstance(row, ItemRow | HitRow):
            self._open(row, local=False, refresh=False)

    def action_open_local(self) -> None:
        self._open_current(refresh=False)

    def action_open_local_fresh(self) -> None:
        self._open_current(refresh=True)

    def _open_current(self, *, refresh: bool) -> None:
        row = self._current()
        if isinstance(row, ItemRow | HitRow):
            self._open(row, local=True, refresh=refresh)

    @work(group="open")
    async def _open(self, row: ItemRow | HitRow, *, local: bool, refresh: bool) -> None:
        # A hit knows its page; a document row opens its first hit, or
        # page 1 of its first PDF when it matched on metadata alone.
        if isinstance(row, HitRow):
            target = row.hit
        elif row.result.hits:
            target = row.result.hits[0]
        else:
            try:
                attachments = await self.api.list_attachments(str(row.result.item["id"]))
            except (ApiError, httpx.HTTPError) as e:
                self._status(str(e), error=True)
                return
            pdfs = [a for a in attachments if a.get("content_type") == "application/pdf"]
            if not pdfs:
                self._status("this document has no PDF")
                return
            target = {
                "attachment_id": pdfs[0]["id"],
                "filename": pdfs[0]["filename"],
                "page_number": 1,
            }

        att, page = str(target["attachment_id"]), int(target["page_number"])
        query = self._results.query or None
        if not local:
            opener.open_web(self.config.base_url, att, page, query)
            self._status(f"opened p.{page} in the shelf reader")
            return
        self._status(f"downloading {target['filename']}…")
        self._open_local(att, str(target["filename"]), page, query, refresh)

    @work(thread=True, group="download")
    def _open_local(
        self, att: str, filename: str, page: int, query: str | None, refresh: bool
    ) -> None:
        # A thread, because the download and the launch both block.
        if self._sync is None:
            self._sync = ShelfClient(self.config.base_url, self.config.token)
        try:
            path = opener.fetch(self._sync, att, filename, refresh=refresh)
            opened = opener.open_local(path, page, viewer=self.config.pdf_viewer, query=query)
        except (ApiError, httpx.HTTPError) as e:
            self.call_from_thread(self._status, str(e), True)
            return
        except OSError as e:
            self.call_from_thread(self._status, f"could not open {filename}: {e}", True)
            return
        note = (
            f"opened p.{page} via {opened.how}"
            if opened.at_page
            else (f"opened via {opened.how} — no page support found, go to p.{page}")
        )
        self.call_from_thread(self._status, note)

    # ── status ───────────────────────────────────────────────────────────

    def _status(self, message: str, error: bool = False) -> None:
        # Text, not markup: an error body or a query can hold "[".
        self._status_bar.update(Text(message, style="red" if error else ""))


def _to_result(row: dict[str, Any]) -> Result:
    hits = row.get("page_hits") or []
    return Result(item=row, hits=hits, hit_count=row.get("page_hit_count") or len(hits))


def _preview(row: Row | None, query: str) -> Text:
    if row is None or isinstance(row, LoadMoreRow):
        return Text("")
    result = row.result
    item = result.item
    data = item.get("data") or {}
    out = Text()
    out.append(title_of(item) + "\n", style="bold")
    fields = [
        ("Type", item.get("item_type")),
        ("Designation", data.get("designation")),
        ("Edition", data.get("edition")),
        ("Publisher", data.get("standardBody") or data.get("publisher")),
        ("Date", data.get("date")),
        ("Creators", _creators(data.get("creators"))),
    ]
    for label, value in fields:
        if value:
            out.append(f"{label:<12}", style="dim")
            out.append(f"{value}\n")
    if result.hit_count:
        out.append(f"{'Matches':<12}", style="dim")
        out.append(f"{result.hit_count} page(s) for “{query}”\n", style="cyan")

    if isinstance(row, HitRow):
        hit = row.hit
        out.append(f"\n{hit['filename']} — page {hit['page_number']}\n", style="dim")
        body = Text(hit["snippet"])
        body.stylize("bold reverse", *hit["highlight"])
        out.append_text(body)
        out.append("\n")
    elif abstract := data.get("abstractNote"):
        out.append("\n" + str(abstract)[:800] + "\n", style="dim")
    out.append("\nenter: shelf reader   o: local PDF   space: all matches", style="dim italic")
    return out


def _creators(creators: object) -> str | None:
    if not isinstance(creators, list) or not creators:
        return None
    names = []
    for c in creators[:4]:
        if isinstance(c, dict):
            name = c.get("name") or " ".join(
                p for p in (c.get("firstName"), c.get("lastName")) if p
            )
            if name:
                names.append(str(name))
    more = f" +{len(creators) - 4}" if len(creators) > 4 else ""
    return ", ".join(names) + more if names else None


def run(config: Config, query: str = "") -> None:
    ShelfBrowser(config, query).run()
