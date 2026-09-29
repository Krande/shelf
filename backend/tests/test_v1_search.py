"""Searching PDF bodies with a token.

The CLI's terminal browser is built on these: `/search` has to find
what the landing page finds, and `?hits=` has to say *which pages* in
the same request, since a terminal list shows every row's first hit at
once.
"""

import uuid
from collections.abc import Iterator

import pytest
from httpx import AsyncClient
from obstore.store import MemoryStore

from shelf.api.v1 import _plain_snippet
from shelf.config import settings
from shelf.services import storage

from .helpers import login


@pytest.fixture(autouse=True)
def memory_store() -> Iterator[None]:
    storage._store = MemoryStore()
    yield
    storage.reset_store()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def token(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(settings, "admin_emails", ["admin@example.com"])
    await login(client, "admin@example.com")
    r = await client.post(
        "/api/me/tokens",
        json={"name": "cli", "scopes": ["upload", "search", "download"]},
    )
    assert r.status_code == 201, r.text
    return str(r.json()["plaintext"])


async def _doc(
    client: AsyncClient, token: str, title: str, pages: dict[int, str]
) -> tuple[str, str]:
    """An item with one PDF whose extracted pages are `pages`. Returns
    (item id, attachment id)."""
    r = await client.post(
        "/api/v1/upload",
        headers=_auth(token),
        files={"file": (f"{title}.pdf", f"%PDF-1.7 {title}".encode(), "application/pdf")},
        data={"title": title},
    )
    assert r.status_code == 201, r.text
    body = r.json()

    from shelf.db import session_factory
    from shelf.models import AttachmentPage

    att_id = uuid.UUID(body["attachment"]["id"])
    async with session_factory() as db:
        db.add_all(
            AttachmentPage(attachment_id=att_id, page_number=n, text=text)
            for n, text in pages.items()
        )
        await db.commit()
    return str(body["item"]["id"]), str(att_id)


async def test_search_finds_pdf_body_text(client: AsyncClient, token: str) -> None:
    item_id, _ = await _doc(client, token, "Report", {3: "the Unicorn clause"})
    r = await client.get("/api/v1/search", headers=_auth(token), params={"q": "unicorn"})
    assert r.status_code == 200, r.text
    assert [it["id"] for it in r.json()] == [item_id]


async def test_title_hits_rank_above_body_hits(client: AsyncClient, token: str) -> None:
    """Same ranking as the landing page, so a query that matches
    hundreds of bodies can't bury the document named after it."""
    body_id, _ = await _doc(client, token, "Manual", {1: "see the widget guide"})
    title_id, _ = await _doc(client, token, "Widget guide", {1: "nothing here"})
    r = await client.get("/api/v1/search", headers=_auth(token), params={"q": "widget"})
    assert [it["id"] for it in r.json()] == [title_id, body_id]


async def test_scope_can_leave_the_body_out(client: AsyncClient, token: str) -> None:
    await _doc(client, token, "Manual", {1: "unicorn"})
    r = await client.get(
        "/api/v1/search",
        headers=_auth(token),
        params={"q": "unicorn", "scope": "title"},
    )
    assert r.json() == []


async def test_hits_are_only_attached_when_asked_for(client: AsyncClient, token: str) -> None:
    await _doc(client, token, "Manual", {1: "unicorn"})
    r = await client.get("/api/v1/search", headers=_auth(token), params={"q": "unicorn"})
    assert r.json()[0]["page_hits"] is None
    assert r.json()[0]["page_hit_count"] is None


async def test_hits_are_capped_per_item_but_counted_in_full(
    client: AsyncClient, token: str
) -> None:
    _, att_id = await _doc(
        client,
        token,
        "Manual",
        {1: "no match", 4: "Unicorn one", 9: "two unicorn", 12: "three UNICORN"},
    )
    r = await client.get(
        "/api/v1/search",
        headers=_auth(token),
        params={"q": "unicorn", "hits": 2},
    )
    row = r.json()[0]
    assert row["page_hit_count"] == 3
    assert [h["page_number"] for h in row["page_hits"]] == [4, 9]
    first = row["page_hits"][0]
    assert first["attachment_id"] == att_id
    assert first["filename"] == "Manual.pdf"
    start, end = first["highlight"]
    assert first["snippet"][start:end] == "Unicorn"


async def test_a_title_only_match_reports_no_hits(client: AsyncClient, token: str) -> None:
    await _doc(client, token, "Unicorn handbook", {1: "horses"})
    r = await client.get(
        "/api/v1/search",
        headers=_auth(token),
        params={"q": "unicorn", "hits": 3},
    )
    assert r.json()[0]["page_hits"] == []
    assert r.json()[0]["page_hit_count"] == 0


async def test_item_hits_returns_every_page(client: AsyncClient, token: str) -> None:
    item_id, _ = await _doc(client, token, "Manual", {n: f"page {n} unicorn" for n in range(1, 8)})
    r = await client.get(
        f"/api/v1/items/{item_id}/hits",
        headers=_auth(token),
        params={"q": "unicorn"},
    )
    assert r.status_code == 200, r.text
    assert [h["page_number"] for h in r.json()] == list(range(1, 8))


async def test_item_hits_needs_access_to_the_item(client: AsyncClient, token: str) -> None:
    item_id, _ = await _doc(client, token, "Manual", {1: "unicorn"})
    await login(client, "other@example.com", link=True)
    other = (await client.post("/api/me/tokens", json={"name": "t", "scopes": ["search"]})).json()[
        "plaintext"
    ]
    r = await client.get(
        f"/api/v1/items/{item_id}/hits",
        headers=_auth(other),
        params={"q": "unicorn"},
    )
    assert r.status_code == 404


def test_snippet_collapses_whitespace_and_keeps_word_breaks() -> None:
    snippet, (start, end) = _plain_snippet("the\n\nload   case\ttable", "load") or ("", (0, 0))
    assert snippet == "the load case table"
    assert snippet[start:end] == "load"


def test_snippet_marks_where_it_was_cut() -> None:
    text = "a" * 200 + " needle " + "b" * 200
    found = _plain_snippet(text, "NEEDLE")
    assert found is not None
    snippet, (start, end) = found
    assert snippet.startswith("…") and snippet.endswith("…")
    assert snippet[start:end] == "needle"


# ── narrowing by space and collection ────────────────────────────────────


async def _collection(client: AsyncClient, token: str, name: str, parent: str | None = None) -> str:
    body: dict[str, object] = {"name": name}
    if parent:
        body["parent_id"] = parent
    r = await client.post("/api/v1/collections", headers=_auth(token), json=body)
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def _file(client: AsyncClient, token: str, item_id: str, *collections: str) -> None:
    r = await client.put(
        f"/api/v1/items/{item_id}/collections",
        headers=_auth(token),
        json={"collection_ids": list(collections)},
    )
    assert r.status_code == 200, r.text


async def _ids(client: AsyncClient, token: str, **params: object) -> set[str]:
    r = await client.get("/api/v1/search", headers=_auth(token), params=params)
    assert r.status_code == 200, r.text
    return {it["id"] for it in r.json()}


async def test_subtree_takes_in_nested_collections(client: AsyncClient, token: str) -> None:
    fem = await _collection(client, token, "FEM")
    aster = await _collection(client, token, "Code Aster", parent=fem)
    top, _ = await _doc(client, token, "Top", {1: "x"})
    deep, _ = await _doc(client, token, "Deep", {1: "x"})
    await _doc(client, token, "Elsewhere", {1: "x"})
    await _file(client, token, top, fem)
    await _file(client, token, deep, aster)

    assert await _ids(client, token, collection=fem) == {top}
    assert await _ids(client, token, collection=fem, collection_scope="subtree") == {top, deep}


async def test_an_item_filed_twice_in_a_subtree_is_one_row(client: AsyncClient, token: str) -> None:
    """Membership used to be a join, so this came back twice and the
    duplicate took a slot from `limit`."""
    fem = await _collection(client, token, "FEM")
    aster = await _collection(client, token, "Code Aster", parent=fem)
    item, _ = await _doc(client, token, "Both", {1: "x"})
    await _file(client, token, item, fem, aster)

    r = await client.get(
        "/api/v1/search",
        headers=_auth(token),
        params={"collection": fem, "collection_scope": "subtree"},
    )
    assert [it["id"] for it in r.json()] == [item]


async def test_space_narrows_to_where_items_live(client: AsyncClient, token: str) -> None:
    mine, _ = await _doc(client, token, "Mine", {1: "x"})
    other = (await client.post("/api/spaces", json={"name": "Other", "slug": "other"})).json()
    theirs = (
        await client.post(
            f"/api/spaces/{other['slug']}/items",
            json={"item_type": "document", "data": {"title": "Theirs"}},
        )
    ).json()["id"]

    assert await _ids(client, token, space="other") == {theirs}
    assert mine in await _ids(client, token)


async def test_an_unknown_space_is_a_404(client: AsyncClient, token: str) -> None:
    r = await client.get("/api/v1/search", headers=_auth(token), params={"space": "nope"})
    assert r.status_code == 404
