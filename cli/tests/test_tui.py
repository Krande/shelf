"""The terminal browser, driven headless against a fake API.

Pins the behaviour that makes it usable: hits render under their
document, a stale search never overwrites a newer one, and Enter opens
the page that is highlighted rather than the document's first.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from shelf_cli import opener, tui
from shelf_cli.config import Config


def _hit(page: int, att: str = "att-1") -> dict[str, Any]:
    snippet = f"text before unicorn on page {page}"
    start = snippet.index("unicorn")
    return {
        "attachment_id": att,
        "filename": "manual.pdf",
        "page_number": page,
        "snippet": snippet,
        "highlight": [start, start + 7],
    }


def _item(item_id: str, title: str, pages: list[int]) -> dict[str, Any]:
    return {
        "id": item_id,
        "item_type": "document",
        "data": {"title": title},
        "page_hits": [_hit(p) for p in pages[: tui.INLINE_HITS]],
        "page_hit_count": len(pages),
    }


class FakeApi:
    def __init__(self) -> None:
        self.searches: list[str | None] = []
        self.scopes: list[dict[str, Any]] = []
        self.delays: dict[str, float] = {}

    async def search(
        self, q: str | None = None, limit: int = 50, **kw: Any
    ) -> list[dict[str, Any]]:
        self.searches.append(q)
        self.scopes.append({k: v for k, v in kw.items() if k in ("space", "collection", "subtree")})
        await asyncio.sleep(self.delays.get(q or "", 0))
        if q == "unicorn":
            return [_item("i1", "Manual", [2, 5, 9, 14, 20])]
        if q == "slow":
            return [_item("stale", "Stale", [1])]
        return [_item("r1", "Recent", [])]

    async def item_hits(self, item_id: str, q: str) -> list[dict[str, Any]]:
        return [_hit(p) for p in (2, 5, 9, 14, 20)]

    async def list_attachments(self, item_id: str) -> list[dict[str, Any]]:
        return []

    async def spaces(self) -> list[dict[str, Any]]:
        return [{"id": "s1", "slug": "standards", "name": "Standards", "writable": False}]

    async def collections(self) -> list[dict[str, Any]]:
        return [
            {"id": "c-fem", "space_id": "s1", "parent_id": None, "name": "FEM"},
            {"id": "c-aster", "space_id": "s1", "parent_id": "c-fem", "name": "Code Aster"},
        ]

    async def aclose(self) -> None:
        pass


def _app() -> tuple[tui.ShelfBrowser, FakeApi]:
    app = tui.ShelfBrowser(Config(base_url="https://shelf.test", token="t"))
    fake = FakeApi()
    app.api = fake  # type: ignore[assignment]
    return app, fake


def _rows(app: tui.ShelfBrowser) -> list[str]:
    return [type(r).__name__ for r in app._rows]


def test_hits_render_under_their_document() -> None:
    async def scenario() -> None:
        app, _ = _app()
        async with app.run_test() as pilot:
            await pilot.press(*"unicorn")
            await pilot.pause(tui.DEBOUNCE_S + 0.2)
            await app.workers.wait_for_complete()
            assert _rows(app) == ["ItemRow", "HitRow", "HitRow", "HitRow", "MoreHitsRow"]

    asyncio.run(scenario())


def test_starts_with_a_query_from_the_command_line() -> None:
    # `shelf browse unicorn`: constructing the app with a query used to
    # raise NoActiveAppError before anything was on screen.
    async def scenario() -> None:
        app = tui.ShelfBrowser(Config(base_url="https://shelf.test", token="t"), "unicorn")
        fake = FakeApi()
        app.api = fake  # type: ignore[assignment]
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause(tui.DEBOUNCE_S + 0.2)
            assert app._input.value == "unicorn"
            assert _rows(app)[0] == "ItemRow"
            # Searched once — putting the query in the box doesn't count
            # as the user typing it.
            assert fake.searches == ["unicorn"]

    asyncio.run(scenario())


def test_a_stale_search_never_replaces_a_newer_one() -> None:
    async def scenario() -> None:
        app, fake = _app()
        fake.delays["slow"] = 0.5
        async with app.run_test() as pilot:
            app._search_now("slow")
            await pilot.pause(0.05)
            app._search_now("unicorn")
            # Not wait_for_complete(): it raises for the cancelled worker,
            # which is the point. Outlast the slow one instead.
            await pilot.pause(0.7)
            assert app._results.query == "unicorn"
            assert app._rows[0].result.item["id"] == "i1"  # type: ignore[union-attr]

    asyncio.run(scenario())


def test_space_expands_every_page_and_enter_opens_the_highlighted_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[tuple[str, int, str | None]] = []
    monkeypatch.setattr(
        opener, "open_web", lambda base, att, page, q: opened.append((att, page, q)) or ""
    )

    async def scenario() -> None:
        app, _ = _app()
        async with app.run_test() as pilot:
            app._search_now("unicorn")
            await app.workers.wait_for_complete()
            await pilot.press("down")  # into the list
            await pilot.press("space")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert _rows(app).count("HitRow") == 5

            await pilot.press("down", "down", "down")  # item, p2, p5, p9
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            assert opened == [("att-1", 9, "unicorn")]

    asyncio.run(scenario())


def test_repeat_queries_are_served_from_cache() -> None:
    async def scenario() -> None:
        app, fake = _app()
        async with app.run_test():
            for q in ("unicorn", "", "unicorn"):
                app._search_now(q)
                await app.workers.wait_for_complete()
            assert fake.searches.count("unicorn") == 1

    asyncio.run(scenario())


def test_an_api_error_with_brackets_is_shown_not_parsed() -> None:
    """FastAPI's 422 bodies are full of `[`; as markup they'd crash."""

    async def scenario() -> None:
        app, fake = _app()

        async def failing(*_a: Any, **_kw: Any) -> list[dict[str, Any]]:
            raise tui.ApiError(422, "[{'loc': ['query', 'q']}]", "GET", "/api/v1/search")

        fake.search = failing  # type: ignore[method-assign]
        async with app.run_test() as pilot:
            app._search_now("[unicorn")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert "422" in str(app._status_bar.render())

    asyncio.run(scenario())


def test_hit_row_keeps_the_match_on_screen() -> None:
    snippet = "x " * 60 + "unicorn tail"
    start = snippet.index("unicorn")
    text = tui._hit_prompt({"page_number": 3, "snippet": snippet, "highlight": [start, start + 7]})
    assert "unicorn" in text.plain[: 10 + tui.ROW_CONTEXT + 10]
    styled = [text.plain[s.start : s.end] for s in text.spans if "reverse" in str(s.style)]
    assert styled == ["unicorn"]


def test_the_sidebar_narrows_where_searches_look() -> None:
    """Pick a collection: its own items with no query, its whole subtree
    with one. Backspace goes back up to the space."""

    async def scenario() -> None:
        app, fake = _app()
        async with app.run_test(size=(160, 40)) as pilot:
            await app.workers.wait_for_complete()
            space = app._nav.root.children[0]
            assert str(space.label) == "Standards"
            space.expand()
            await pilot.pause()
            fem = space.children[0]
            assert fem.allow_expand  # Code Aster is below it
            fem.expand()
            await pilot.pause()
            assert [str(n.label) for n in fem.children] == ["Code Aster"]

            app._nav.focus()
            app._nav.move_cursor(fem)
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            assert fake.scopes[-1] == {"collection": "c-fem", "subtree": False}
            assert app._input.border_title == "Standards / FEM"

            app._search_now("unicorn")
            await app.workers.wait_for_complete()
            assert fake.scopes[-1] == {"collection": "c-fem", "subtree": True}

            app._list.focus()
            await pilot.press("backspace")
            await app.workers.wait_for_complete()
            assert fake.scopes[-1] == {"space": "standards"}

    asyncio.run(scenario())


def test_results_are_cached_per_scope() -> None:
    async def scenario() -> None:
        app, fake = _app()
        async with app.run_test():
            await app.workers.wait_for_complete()
            # A new scope re-runs what is in the box, so type it there.
            app._input.value = "unicorn"
            app._search_now("unicorn")
            await app.workers.wait_for_complete()
            app._set_scope(tui.Scope(label="Standards", space="standards", parent=tui.EVERYWHERE))
            await app.workers.wait_for_complete()
            assert fake.searches.count("unicorn") == 2

    asyncio.run(scenario())


def test_arrow_keys_open_and_close_sidebar_branches() -> None:
    async def scenario() -> None:
        app, _ = _app()
        async with app.run_test(size=(160, 40)) as pilot:
            await app.workers.wait_for_complete()
            nav = app._nav
            space = nav.root.children[0]
            nav.focus()
            nav.move_cursor(space)

            await pilot.press("right")  # open Standards
            assert space.is_expanded
            await pilot.press("right")  # step into it
            fem = space.children[0]
            assert nav.cursor_node is fem
            await pilot.press("right")  # open FEM
            assert fem.is_expanded and str(fem.children[0].label) == "Code Aster"

            await pilot.press("left")  # close FEM
            assert not fem.is_expanded
            await pilot.press("left")  # out to Standards
            assert nav.cursor_node is space

    asyncio.run(scenario())