"""Searching a standard by its designation, and any other metadata field.

A standard is usually looked for by the code it's known by, and its title
often doesn't carry it: "Eurocode 3: Design of steel structures — Part 1-9:
Fatigue" is NS-EN 1993-1-9 only in its `designation`. That's searched by
default now. Every other metadata field (edition, DOI, publisher, …) can be
opted into as `scope=field:<name>` — never by default, so the plain search
costs no more than it did.
"""

from typing import Any

import pytest
from httpx import AsyncClient

from .helpers import get_me, login


@pytest.fixture
async def slug(client: AsyncClient) -> str:
    await login(client, "alice@example.com")
    me = await get_me(client)
    return f"u-{str(me['id']).replace('-', '')[:8]}"


async def _item(client: AsyncClient, slug: str, **data: Any) -> str:
    r = await client.post(
        f"/api/spaces/{slug}/items", json={"item_type": "standard", "data": data}
    )
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def _search(client: AsyncClient, q: str, *scope: str) -> list[str]:
    params: list[tuple[str, str]] = [("q", q), *(("scope", s) for s in scope)]
    r = await client.get("/api/me/items", params=params)
    assert r.status_code == 200, r.text
    return [it["id"] for it in r.json()["items"]]


async def test_the_designation_is_searched_by_default(
    client: AsyncClient, slug: str
) -> None:
    item = await _item(
        client,
        slug,
        title="Eurocode 3: Design of steel structures - Part 1-9: Fatigue",
        designation="NS-EN 1993-1-9",
    )
    await _item(client, slug, title="Unrelated", designation="NORSOK N-004")
    assert await _search(client, "1993-1-9") == [item]
    assert await _search(client, "EN 1993") == [item]


async def test_designation_can_be_the_only_scope(client: AsyncClient, slug: str) -> None:
    by_code = await _item(client, slug, title="Fatigue", designation="DNV-RP-C203")
    await _item(client, slug, title="About DNV-RP-C203", designation="BS 7910")
    assert await _search(client, "C203", "designation") == [by_code]


async def test_a_designation_hit_ranks_under_a_title_hit(
    client: AsyncClient, slug: str
) -> None:
    by_code = await _item(client, slug, title="Fatigue design", designation="DNV-RP-C203")
    by_title = await _item(client, slug, title="Comments on DNV-RP-C203")
    assert await _search(client, "RP-C203") == [by_title, by_code]


async def test_other_fields_are_not_searched_unless_asked(
    client: AsyncClient, slug: str
) -> None:
    item = await _item(client, slug, title="Bolts", edition="2005+NA:2009")
    assert await _search(client, "NA:2009") == []
    assert await _search(client, "NA:2009", "field:edition") == [item]


async def test_a_field_scope_joins_the_named_scopes(client: AsyncClient, slug: str) -> None:
    by_title = await _item(client, slug, title="Welding 2009")
    by_edition = await _item(client, slug, title="Bolts", edition="2009")
    found = await _search(client, "2009", "title", "field:edition")
    # Title hits first; an opted-in field ranks below the named scopes.
    assert found == [by_title, by_edition]


async def test_a_numeric_field_matches_as_text(client: AsyncClient, slug: str) -> None:
    item = await _item(client, slug, title="Thick", numberOfPages=742)
    assert await _search(client, "742", "field:numberOfPages") == [item]


async def test_a_field_with_its_own_scope_is_the_same_search(
    client: AsyncClient, slug: str
) -> None:
    item = await _item(client, slug, title="Plated", designation="NS-EN 1993-1-5")
    assert await _search(client, "1993-1-5", "field:designation") == [item]


@pytest.mark.parametrize("bad", ["author", "field:", "field:1st", "field:a b", "data.title"])
async def test_an_unknown_scope_is_refused_by_name(
    client: AsyncClient, slug: str, bad: str
) -> None:
    r = await client.get("/api/me/items", params={"q": "x", "scope": bad})
    assert r.status_code == 422
    assert bad in r.json()["detail"]


async def test_the_number_of_fields_is_capped(client: AsyncClient, slug: str) -> None:
    params = [("q", "x"), *(("scope", f"field:f{i}") for i in range(21))]
    r = await client.get("/api/me/items", params=params)
    assert r.status_code == 422


async def test_the_token_search_takes_field_scopes(client: AsyncClient, slug: str) -> None:
    item = await _item(client, slug, title="Bolts", edition="2005+NA:2009")
    token = (
        await client.post("/api/me/tokens", json={"name": "cli", "scopes": ["search"]})
    ).json()["plaintext"]
    r = await client.get(
        "/api/v1/search",
        headers={"Authorization": f"Bearer {token}"},
        params=[("q", "NA:2009"), ("scope", "field:edition")],
    )
    assert r.status_code == 200, r.text
    assert [it["id"] for it in r.json()] == [item]
