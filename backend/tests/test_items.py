"""Integration tests for /api/spaces/{slug}/items and /api/items/{id}."""

from typing import Any

from httpx import AsyncClient


def _items(payload: Any) -> list[Any]:
    """Unwrap the paginated list response.

    The list endpoint returns ``{items, total}`` so the SPA can render
    infinite-scroll. Tests written before that change indexed straight
    into the list — this helper keeps them readable without sprinkling
    ``["items"]`` everywhere.
    """
    return payload["items"]


async def _login(client: AsyncClient, email: str = "alice@example.com") -> str:
    """Log in as a user, return their personal space slug."""
    r = await client.post("/auth/dev-login", json={"email": email})
    assert r.status_code == 200, r.text
    me = await client.get("/api/me")
    assert me.status_code == 200
    user_id = me.json()["id"]
    return f"u-{user_id.replace('-', '')[:8]}"


async def test_list_items_empty(client: AsyncClient) -> None:
    slug = await _login(client)
    r = await client.get(f"/api/spaces/{slug}/items")
    assert r.status_code == 200
    assert r.json() == {"items": [], "total": 0}


async def test_create_and_list_item(client: AsyncClient) -> None:
    slug = await _login(client)
    r = await client.post(
        f"/api/spaces/{slug}/items",
        json={
            "item_type": "journalArticle",
            "data": {"title": "On the structure of trees", "creators": [{"name": "Knuth"}]},
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["item_type"] == "journalArticle"
    assert body["data"]["title"] == "On the structure of trees"
    item_id = body["id"]

    listing = await client.get(f"/api/spaces/{slug}/items")
    assert listing.status_code == 200
    rows = _items(listing.json())
    assert len(rows) == 1
    assert rows[0]["id"] == item_id


async def test_get_item(client: AsyncClient) -> None:
    slug = await _login(client)
    created = (
        await client.post(
            f"/api/spaces/{slug}/items",
            json={"item_type": "document", "data": {"title": "RFC 1"}},
        )
    ).json()

    r = await client.get(f"/api/items/{created['id']}")
    assert r.status_code == 200
    assert r.json()["data"]["title"] == "RFC 1"


async def test_update_item(client: AsyncClient) -> None:
    slug = await _login(client)
    item = (
        await client.post(
            f"/api/spaces/{slug}/items",
            json={"item_type": "document", "data": {"title": "draft"}},
        )
    ).json()

    r = await client.patch(
        f"/api/items/{item['id']}",
        json={"data": {"title": "final", "tags": ["important"]}},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["data"] == {"title": "final", "tags": ["important"]}


async def test_delete_item_soft_deletes(client: AsyncClient) -> None:
    slug = await _login(client)
    item = (
        await client.post(
            f"/api/spaces/{slug}/items",
            json={"item_type": "document", "data": {"title": "trash me"}},
        )
    ).json()

    r = await client.delete(f"/api/items/{item['id']}")
    assert r.status_code == 204

    # No longer listed
    listing = _items((await client.get(f"/api/spaces/{slug}/items")).json())
    assert listing == []

    # Direct fetch returns 404 (soft-deleted is invisible)
    r = await client.get(f"/api/items/{item['id']}")
    assert r.status_code == 404


async def test_other_user_cannot_see_my_space(client: AsyncClient) -> None:
    alice_slug = await _login(client, "alice@example.com")
    await client.post(
        f"/api/spaces/{alice_slug}/items",
        json={"item_type": "document", "data": {"title": "secret"}},
    )

    # Switch user
    await client.post("/auth/logout")
    await _login(client, "mallory@example.com")

    # mallory tries to read alice's space
    r = await client.get(f"/api/spaces/{alice_slug}/items")
    assert r.status_code == 404


async def test_unauthenticated_cannot_list(client: AsyncClient) -> None:
    r = await client.get("/api/spaces/u-deadbeef/items")
    assert r.status_code == 401


async def test_search_q_filters_by_title(client: AsyncClient) -> None:
    slug = await _login(client)
    for title in ["Reading List", "Conference Notes", "reading habits"]:
        await client.post(
            f"/api/spaces/{slug}/items",
            json={"item_type": "document", "data": {"title": title}},
        )

    # Case-insensitive substring match against data->title.
    hits = _items((
        await client.get(f"/api/spaces/{slug}/items", params={"q": "READing"})
    ).json())
    titles = sorted(h["data"]["title"] for h in hits)
    assert titles == ["Reading List", "reading habits"]

    # Empty / whitespace q is treated as no filter.
    all_three = _items((
        await client.get(f"/api/spaces/{slug}/items", params={"q": "  "})
    ).json())
    assert len(all_three) == 3

    # No match returns an empty list, not an error.
    miss = _items((
        await client.get(f"/api/spaces/{slug}/items", params={"q": "nope"})
    ).json())
    assert miss == []


async def test_search_q_matches_abstract_and_creators(
    client: AsyncClient,
) -> None:
    slug = await _login(client)
    await client.post(
        f"/api/spaces/{slug}/items",
        json={
            "item_type": "journalArticle",
            "data": {
                "title": "Parallel Bayesian sampling",
                "abstractNote": "We discuss MCMC convergence diagnostics.",
                "creators": [{"firstName": "Persi", "lastName": "Diaconis"}],
            },
        },
    )
    await client.post(
        f"/api/spaces/{slug}/items",
        json={
            "item_type": "document",
            "data": {"title": "Untitled draft", "extra": "DOI: 10.1234/foo"},
        },
    )

    # Hit by abstract.
    hits = _items((
        await client.get(
            f"/api/spaces/{slug}/items", params={"q": "convergence"}
        )
    ).json())
    assert [h["data"]["title"] for h in hits] == ["Parallel Bayesian sampling"]

    # Hit by creator.
    hits = _items((
        await client.get(
            f"/api/spaces/{slug}/items", params={"q": "Diaconis"}
        )
    ).json())
    assert [h["data"]["title"] for h in hits] == ["Parallel Bayesian sampling"]

    # Hit by extra.
    hits = _items((
        await client.get(
            f"/api/spaces/{slug}/items", params={"q": "10.1234"}
        )
    ).json())
    assert [h["data"]["title"] for h in hits] == ["Untitled draft"]


async def test_status_active_excludes_trashed(client: AsyncClient) -> None:
    slug = await _login(client)
    keep = (
        await client.post(
            f"/api/spaces/{slug}/items",
            json={"item_type": "document", "data": {"title": "keep"}},
        )
    ).json()
    bin_ = (
        await client.post(
            f"/api/spaces/{slug}/items",
            json={"item_type": "document", "data": {"title": "bin"}},
        )
    ).json()
    await client.delete(f"/api/items/{bin_['id']}")

    active = _items((await client.get(f"/api/spaces/{slug}/items")).json())
    assert [a["id"] for a in active] == [keep["id"]]

    trashed = _items((
        await client.get(f"/api/spaces/{slug}/items", params={"status": "trashed"})
    ).json())
    assert [t["id"] for t in trashed] == [bin_["id"]]
    assert trashed[0]["deleted_at"] is not None

    everything = _items((
        await client.get(f"/api/spaces/{slug}/items", params={"status": "all"})
    ).json())
    assert {e["id"] for e in everything} == {keep["id"], bin_["id"]}


async def test_restore_brings_item_back(client: AsyncClient) -> None:
    slug = await _login(client)
    item = (
        await client.post(
            f"/api/spaces/{slug}/items",
            json={"item_type": "document", "data": {"title": "comeback"}},
        )
    ).json()
    await client.delete(f"/api/items/{item['id']}")
    assert _items((await client.get(f"/api/spaces/{slug}/items")).json()) == []

    restored = await client.post(f"/api/items/{item['id']}/restore")
    assert restored.status_code == 200
    assert restored.json()["deleted_at"] is None

    listing = _items((await client.get(f"/api/spaces/{slug}/items")).json())
    assert [r["id"] for r in listing] == [item["id"]]


async def test_permanent_delete_removes_trashed_row(client: AsyncClient) -> None:
    slug = await _login(client)
    item = (
        await client.post(
            f"/api/spaces/{slug}/items",
            json={"item_type": "document", "data": {"title": "burn"}},
        )
    ).json()
    # Refusal: can't perm-delete an active item.
    refusal = await client.delete(
        f"/api/items/{item['id']}", params={"permanent": "true"}
    )
    assert refusal.status_code == 400

    # Send to trash, then perm-delete.
    await client.delete(f"/api/items/{item['id']}")
    gone = await client.delete(
        f"/api/items/{item['id']}", params={"permanent": "true"}
    )
    assert gone.status_code == 204

    # Now invisible from every status filter.
    for s in ("active", "trashed", "all"):
        rows = _items((
            await client.get(f"/api/spaces/{slug}/items", params={"status": s})
        ).json())
        assert all(r["id"] != item["id"] for r in rows), s


async def test_sort_by_title_asc(client: AsyncClient) -> None:
    slug = await _login(client)
    for title in ["Charlie", "alpha", "Bravo"]:
        await client.post(
            f"/api/spaces/{slug}/items",
            json={"item_type": "document", "data": {"title": title}},
        )
    rows = _items((
        await client.get(
            f"/api/spaces/{slug}/items",
            params={"sort": "title", "direction": "asc"},
        )
    ).json())
    titles = [r["data"]["title"] for r in rows]
    # Postgres lower-case sort: alpha precedes Bravo, Bravo precedes
    # Charlie. (Default collation in the test DB is en_US.UTF-8 which
    # is case-aware; using `astext` returns the raw string so the
    # ordering is the locale's natural case-insensitive collation.)
    assert titles == sorted(titles, key=str.casefold)


async def test_sort_by_type_desc(client: AsyncClient) -> None:
    slug = await _login(client)
    for typ in ["book", "thesis", "journalArticle"]:
        await client.post(
            f"/api/spaces/{slug}/items",
            json={"item_type": typ, "data": {"title": typ}},
        )
    rows = _items((
        await client.get(
            f"/api/spaces/{slug}/items",
            params={"sort": "type", "direction": "desc"},
        )
    ).json())
    assert [r["item_type"] for r in rows] == ["thesis", "journalArticle", "book"]


async def test_filter_by_tag(client: AsyncClient) -> None:
    slug = await _login(client)
    # Tags now live in the normalised `tags` table; create + attach
    # via the dedicated endpoints.
    paper = (
        await client.post(f"/api/spaces/{slug}/tags", json={"name": "paper"})
    ).json()
    ml = (
        await client.post(f"/api/spaces/{slug}/tags", json={"name": "ml"})
    ).json()
    physics = (
        await client.post(f"/api/spaces/{slug}/tags", json={"name": "physics"})
    ).json()
    draft = (
        await client.post(f"/api/spaces/{slug}/tags", json={"name": "draft"})
    ).json()

    a = (
        await client.post(
            f"/api/spaces/{slug}/items",
            json={"item_type": "document", "data": {"title": "A"}},
        )
    ).json()
    b = (
        await client.post(
            f"/api/spaces/{slug}/items",
            json={"item_type": "document", "data": {"title": "B"}},
        )
    ).json()
    c = (
        await client.post(
            f"/api/spaces/{slug}/items",
            json={"item_type": "document", "data": {"title": "C"}},
        )
    ).json()
    await client.put(
        f"/api/items/{a['id']}/tags", json={"tag_ids": [paper["id"], ml["id"]]}
    )
    await client.put(
        f"/api/items/{b['id']}/tags", json={"tag_ids": [paper["id"], physics["id"]]}
    )
    await client.put(
        f"/api/items/{c['id']}/tags", json={"tag_ids": [draft["id"]]}
    )

    # Single tag filter
    papers = _items((
        await client.get(f"/api/spaces/{slug}/items", params={"tag": "paper"})
    ).json())
    assert sorted(p["data"]["title"] for p in papers) == ["A", "B"]

    # Multi-tag = AND across tags
    ml_papers = _items((
        await client.get(
            f"/api/spaces/{slug}/items",
            params=[("tag", "paper"), ("tag", "ml")],
        )
    ).json())
    assert [p["data"]["title"] for p in ml_papers] == ["A"]

    # Tag with no matches → empty list
    miss = _items((
        await client.get(f"/api/spaces/{slug}/items", params={"tag": "ghost"})
    ).json())
    assert miss == []
