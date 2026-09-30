"""Space and collection profiles: a description and default columns.

What's pinned here is storage and permission; resolving which profile
applies (collection → ancestors → space → default) happens in the SPA,
which already holds every row it needs.
"""

import pytest
from httpx import AsyncClient

from shelf.config import settings

from .helpers import login

STANDARD_COLUMNS = ["title", "field:designation", "field:edition", "type", "updated"]


@pytest.fixture
async def slug(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> str:
    """A shared space owned by an admin; client left signed in as them."""
    monkeypatch.setattr(settings, "admin_emails", ["admin@example.com"])
    await login(client, "admin@example.com")
    r = await client.post("/api/spaces", json={"name": "Standards", "slug": "standards"})
    assert r.status_code == 201, r.text
    return str(r.json()["slug"])


async def _space(client: AsyncClient, slug: str) -> dict[str, object]:
    spaces = (await client.get("/api/me/spaces")).json()
    return next(s for s in spaces if s["slug"] == slug)


# ── Spaces ───────────────────────────────────────────────────────────────────


async def test_a_space_starts_without_a_profile(client: AsyncClient, slug: str) -> None:
    space = await _space(client, slug)
    assert space["description"] is None
    assert space["columns"] is None


async def test_setting_a_space_profile(client: AsyncClient, slug: str) -> None:
    r = await client.patch(
        f"/api/spaces/{slug}/profile",
        json={"description": "  Engineering standards.  ", "columns": STANDARD_COLUMNS},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"description": "Engineering standards.", "columns": STANDARD_COLUMNS}

    space = await _space(client, slug)
    assert space["description"] == "Engineering standards."
    assert space["columns"] == STANDARD_COLUMNS


async def test_fields_are_set_independently(client: AsyncClient, slug: str) -> None:
    await client.patch(
        f"/api/spaces/{slug}/profile",
        json={"description": "Engineering standards.", "columns": STANDARD_COLUMNS},
    )
    r = await client.patch(f"/api/spaces/{slug}/profile", json={"columns": None})
    assert r.json() == {"description": "Engineering standards.", "columns": None}
    r = await client.patch(f"/api/spaces/{slug}/profile", json={"description": ""})
    assert r.json() == {"description": None, "columns": None}


async def test_an_empty_column_list_means_inherit(client: AsyncClient, slug: str) -> None:
    r = await client.patch(f"/api/spaces/{slug}/profile", json={"columns": []})
    assert r.json()["columns"] is None


async def test_repeated_columns_are_dropped(client: AsyncClient, slug: str) -> None:
    r = await client.patch(
        f"/api/spaces/{slug}/profile",
        json={"columns": ["title", "field:designation", "title"]},
    )
    assert r.json()["columns"] == ["title", "field:designation"]


@pytest.mark.parametrize("bad", ["author", "field:", "field:1st", "field:a b", "data.title"])
async def test_unknown_columns_are_refused(
    client: AsyncClient, slug: str, bad: str
) -> None:
    r = await client.patch(f"/api/spaces/{slug}/profile", json={"columns": ["title", bad]})
    assert r.status_code == 400
    assert bad in r.json()["detail"]


async def test_an_editor_may_set_the_profile(client: AsyncClient, slug: str) -> None:
    await login(client, "editor@example.com")
    await login(client, "admin@example.com")
    r = await client.post(
        f"/api/spaces/{slug}/members", json={"email": "editor@example.com", "role": "editor"}
    )
    assert r.status_code in (200, 201), r.text

    await login(client, "editor@example.com")
    r = await client.patch(f"/api/spaces/{slug}/profile", json={"columns": STANDARD_COLUMNS})
    assert r.status_code == 200, r.text


async def test_a_viewer_may_not(client: AsyncClient, slug: str) -> None:
    await login(client, "viewer@example.com")
    await login(client, "admin@example.com")
    await client.post(
        f"/api/spaces/{slug}/members", json={"email": "viewer@example.com", "role": "viewer"}
    )

    await login(client, "viewer@example.com")
    r = await client.patch(f"/api/spaces/{slug}/profile", json={"columns": STANDARD_COLUMNS})
    assert r.status_code == 403


async def test_a_stranger_is_told_nothing(client: AsyncClient, slug: str) -> None:
    await login(client, "stranger@example.com")
    r = await client.patch(f"/api/spaces/{slug}/profile", json={"description": "mine"})
    assert r.status_code == 404


# ── Collections ──────────────────────────────────────────────────────────────


async def test_a_collection_carries_its_own_columns(client: AsyncClient, slug: str) -> None:
    coll = (
        await client.post(f"/api/spaces/{slug}/collections", json={"name": "Eurocodes"})
    ).json()
    assert coll["columns"] is None

    r = await client.patch(
        f"/api/collections/{coll['id']}", json={"columns": ["title", "field:nationalAnnex"]}
    )
    assert r.status_code == 200, r.text
    assert r.json()["columns"] == ["title", "field:nationalAnnex"]

    listed = (await client.get(f"/api/spaces/{slug}/collections")).json()
    assert listed[0]["columns"] == ["title", "field:nationalAnnex"]

    # Renaming leaves the columns alone; clearing them hands the
    # collection back to its parent's.
    r = await client.patch(f"/api/collections/{coll['id']}", json={"name": "EN 199x"})
    assert r.json()["columns"] == ["title", "field:nationalAnnex"]
    r = await client.patch(f"/api/collections/{coll['id']}", json={"columns": None})
    assert r.json()["columns"] is None


async def test_a_collection_refuses_unknown_columns(client: AsyncClient, slug: str) -> None:
    coll = (
        await client.post(f"/api/spaces/{slug}/collections", json={"name": "Eurocodes"})
    ).json()
    r = await client.patch(f"/api/collections/{coll['id']}", json={"columns": ["nope"]})
    assert r.status_code == 400
