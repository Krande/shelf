"""A query is matched against metadata word by word.

People type "EN 1993" for a title written "EN1993", and "steel design"
for "Design of steel structures". Matching the whole query as one
substring found neither. These go through the landing page's route and
the token route, since both build their predicate with `_parse_search`.
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
        f"/api/spaces/{slug}/items", json={"item_type": "document", "data": data}
    )
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def _search(client: AsyncClient, q: str, *scope: str) -> list[str]:
    params: list[tuple[str, str]] = [("q", q), *(("scope", s) for s in scope)]
    r = await client.get("/api/me/items", params=params)
    assert r.status_code == 200, r.text
    return [it["id"] for it in r.json()["items"]]


async def test_spaced_words_find_a_title_written_without_the_space(
    client: AsyncClient, slug: str
) -> None:
    joined = await _item(client, slug, title="EN1993-1-1 Design of steel")
    hyphen = await _item(client, slug, title="EN-1993 part 2")
    await _item(client, slug, title="EN 1992 concrete")

    assert set(await _search(client, "EN 1993")) == {joined, hyphen}


async def test_words_need_not_be_adjacent_or_in_order(
    client: AsyncClient, slug: str
) -> None:
    item = await _item(client, slug, title="Design of steel structures")
    assert await _search(client, "steel design") == [item]


async def test_every_word_has_to_match_somewhere(
    client: AsyncClient, slug: str
) -> None:
    await _item(client, slug, title="Design of steel structures")
    assert await _search(client, "steel timber") == []


async def test_words_may_match_in_different_fields(
    client: AsyncClient, slug: str
) -> None:
    item = await _item(
        client,
        slug,
        title="Eurocode commentary",
        creators=[{"firstName": "Ann", "lastName": "Smith"}],
    )
    assert await _search(client, "eurocode smith") == [item]
    # ...but not when the field holding one of them is scoped out.
    assert await _search(client, "eurocode smith", "title") == []


async def test_a_title_holding_every_word_ranks_first(
    client: AsyncClient, slug: str
) -> None:
    spread = await _item(
        client,
        slug,
        title="Eurocode commentary",
        creators=[{"lastName": "Smith"}],
    )
    whole = await _item(client, slug, title="Smith on Eurocode")
    assert await _search(client, "eurocode smith") == [whole, spread]


async def test_wildcard_characters_are_literal(
    client: AsyncClient, slug: str
) -> None:
    """`%` and `_` used to reach ILIKE unescaped, so `100%` matched any
    title with "100" in it."""
    literal = await _item(client, slug, title="Utilisation 100% check")
    await _item(client, slug, title="Load of 100 kN")
    assert await _search(client, "100%") == [literal]


async def test_the_token_search_matches_words_too(
    client: AsyncClient, slug: str
) -> None:
    item = await _item(client, slug, title="EN1993-1-1 Design of steel")
    r = await client.post(
        "/api/me/tokens", json={"name": "cli", "scopes": ["search"]}
    )
    token = r.json()["plaintext"]
    r = await client.get(
        "/api/v1/search",
        headers={"Authorization": f"Bearer {token}"},
        params={"q": "EN 1993"},
    )
    assert [it["id"] for it in r.json()] == [item]
