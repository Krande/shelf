"""Searching every space the caller can reach: /api/me/items.

The landing page asks "where is that document", which no single space can
answer — the document may live in a shared space, or in one a space of
theirs subscribes to. The rules pinned here:

  * items in shared spaces the caller is a member of are found
  * items in inherited spaces are found
  * each item appears **once**, however many ways the caller reaches it
  * `space=` narrows the search, and only to spaces they can read
  * nothing from a space they hold no role in ever appears
"""

import pytest
from httpx import AsyncClient

from shelf.config import settings

from .helpers import login


async def _personal_slug(client: AsyncClient) -> str:
    me = (await client.get("/api/me")).json()
    return f"u-{me['id'].replace('-', '')[:8]}"


async def _make_space(client: AsyncClient, name: str, slug: str) -> str:
    r = await client.post("/api/spaces", json={"name": name, "slug": slug})
    assert r.status_code == 201, r.text
    return str(r.json()["slug"])


async def _add_item(
    client: AsyncClient, slug: str, title: str, **data: object
) -> str:
    r = await client.post(
        f"/api/spaces/{slug}/items",
        json={"item_type": "standard", "data": {"title": title, **data}},
    )
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def _search(client: AsyncClient, **params: object) -> dict[str, object]:
    r = await client.get("/api/me/items", params=params)
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body, dict)
    return body


def _titles(payload: dict[str, object]) -> list[str]:
    items = payload["items"]
    assert isinstance(items, list)
    return [it["data"]["title"] for it in items]


@pytest.fixture
async def world(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> dict[str, str]:
    """Bob, with a document in each kind of space he can reach.

    An admin owns a subscribable "Standards" space and a "Projects" space
    Bob is a viewer on; Bob's own shelf subscribes to Standards, so the
    standard is reachable both directly (he is nobody in Standards) and
    through his shelf. Leaves the client authenticated as Bob.
    """
    monkeypatch.setattr(settings, "admin_emails", ["admin@example.com"])
    # Bob first, so the admin has an account to add as a member.
    await login(client, "bob@example.com")
    bob_slug = await _personal_slug(client)

    await login(client, "admin@example.com")
    standards = await _make_space(client, "Standards", "standards")
    await _add_item(client, standards, "ACME 1234 Widgets")
    assert (
        await client.patch(
            f"/api/spaces/{standards}/settings", json={"subscribable": True}
        )
    ).status_code == 200
    projects = await _make_space(client, "Projects", "projects")
    await _add_item(client, projects, "Bridge design basis")
    assert (
        await client.post(
            f"/api/spaces/{projects}/members",
            json={"email": "bob@example.com", "role": "viewer"},
        )
    ).status_code in (200, 201)
    # A space Bob holds no role in at all.
    private = await _make_space(client, "Tender", "tender")
    await _add_item(client, private, "Tender pricing")

    await login(client, "bob@example.com")
    await _add_item(client, bob_slug, "My reading notes")
    assert (
        await client.post(
            f"/api/spaces/{bob_slug}/inherits", json={"parent_slug": standards}
        )
    ).status_code in (200, 201)
    return {
        "bob": bob_slug,
        "standards": standards,
        "projects": projects,
        "tender": private,
    }


async def test_searches_every_space_the_caller_can_reach(
    client: AsyncClient, world: dict[str, str]
) -> None:
    """The point of the endpoint: one query, all three routes to a
    document — own shelf, a shared space, an inherited one."""
    found = _titles(await _search(client))
    assert sorted(found) == [
        "ACME 1234 Widgets",
        "Bridge design basis",
        "My reading notes",
    ]


async def test_finds_a_document_in_a_shared_space(
    client: AsyncClient, world: dict[str, str]
) -> None:
    assert _titles(await _search(client, q="Bridge")) == ["Bridge design basis"]


async def test_finds_a_document_in_an_inherited_space(
    client: AsyncClient, world: dict[str, str]
) -> None:
    assert _titles(await _search(client, q="ACME")) == ["ACME 1234 Widgets"]


async def test_an_inherited_document_is_listed_once(
    client: AsyncClient, world: dict[str, str]
) -> None:
    """Bob's shelf subscribes to Standards, so the standard is reachable
    twice over. Searching each space separately would report it twice —
    the whole reason this is one query."""
    body = await _search(client, q="ACME")
    assert body["total"] == 1
    assert _titles(body) == ["ACME 1234 Widgets"]


async def test_nothing_from_a_space_with_no_role(
    client: AsyncClient, world: dict[str, str]
) -> None:
    assert _titles(await _search(client, q="Tender")) == []


async def test_space_filter_narrows_to_the_named_spaces(
    client: AsyncClient, world: dict[str, str]
) -> None:
    assert _titles(await _search(client, space=world["standards"])) == [
        "ACME 1234 Widgets"
    ]
    both = await _search(
        client, space=[world["standards"], world["projects"]]
    )
    assert sorted(_titles(both)) == ["ACME 1234 Widgets", "Bridge design basis"]


async def test_space_filter_selects_where_an_item_lives(
    client: AsyncClient, world: dict[str, str]
) -> None:
    """Filtering to Bob's own shelf leaves the inherited standard out: the
    inherited space is a checkbox of its own, so unchecking it has to
    remove exactly its items."""
    assert _titles(await _search(client, space=world["bob"])) == [
        "My reading notes"
    ]


async def test_filtering_every_space_out_returns_nothing(
    client: AsyncClient, world: dict[str, str]
) -> None:
    body = await _search(client, space=[""])
    assert body == {"items": [], "total": 0}


@pytest.mark.parametrize("slug", ["no-such-space", "tender"])
async def test_a_space_the_caller_cannot_read_is_404(
    client: AsyncClient, world: dict[str, str], slug: str
) -> None:
    """Unreadable and nonexistent are the same answer, and both beat
    matching nothing quietly — which reads as "no results" and hides the
    mistake."""
    r = await client.get("/api/me/items", params={"space": slug})
    assert r.status_code == 404, r.text


async def test_scope_narrowing_applies_across_spaces(
    client: AsyncClient, world: dict[str, str]
) -> None:
    """The same `scope=` the per-space listing takes, since both are built
    from one predicate."""
    await login(client, "admin@example.com")
    await _add_item(
        client,
        world["standards"],
        "Unrelated title",
        abstractNote="mentions ACME in passing",
    )
    await login(client, "bob@example.com")

    assert sorted(_titles(await _search(client, q="ACME"))) == [
        "ACME 1234 Widgets",
        "Unrelated title",
    ]
    assert _titles(await _search(client, q="ACME", scope="title")) == [
        "ACME 1234 Widgets"
    ]


async def test_trashed_items_are_left_out_by_default(
    client: AsyncClient, world: dict[str, str]
) -> None:
    items = (await _search(client, q="notes"))["items"]
    assert isinstance(items, list)
    item_id = items[0]["id"]
    assert (await client.delete(f"/api/items/{item_id}")).status_code in (200, 204)

    assert _titles(await _search(client, q="notes")) == []
    assert _titles(await _search(client, q="notes", status="trashed")) == [
        "My reading notes"
    ]


async def test_unauthenticated_cannot_search(client: AsyncClient) -> None:
    await client.post("/auth/logout")
    assert (await client.get("/api/me/items")).status_code == 401
