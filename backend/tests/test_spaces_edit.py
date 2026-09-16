"""Renaming a space: who may, and what a slug change costs.

Renaming is the one place an instance admin reaches into a space they
may hold no role in. The line being pinned here is that it stays a
*label* change — an admin who renames a space still cannot read it.
"""

import pytest
from httpx import AsyncClient

from shelf.config import settings

from .helpers import login


async def _personal_slug(client: AsyncClient) -> str:
    return str((await client.get("/api/me/spaces")).json()[0]["slug"])


@pytest.fixture
async def shared(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> tuple[str, str]:
    """An admin owning a shared space with one item.

    Returns (admin_id, slug), client left authenticated as the admin.
    """
    monkeypatch.setattr(settings, "admin_emails", ["admin@example.com"])
    admin_id = await login(client, "admin@example.com")
    r = await client.post(
        "/api/spaces", json={"name": "Standards", "slug": "standards"}
    )
    assert r.status_code == 201, r.text
    slug = str(r.json()["slug"])
    await client.post(
        f"/api/spaces/{slug}/items",
        json={"item_type": "standard", "data": {"title": "ACME 1234"}},
    )
    return admin_id, slug


# ── The owner ────────────────────────────────────────────────────────────────


async def test_the_owner_can_rename(
    client: AsyncClient, shared: tuple[str, str]
) -> None:
    _admin, slug = shared
    r = await client.patch(
        f"/api/spaces/{slug}", json={"name": "Company Standards"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Company Standards"
    assert r.json()["slug"] == slug


async def test_changing_the_slug_moves_the_space(
    client: AsyncClient, shared: tuple[str, str]
) -> None:
    _admin, slug = shared
    r = await client.patch(f"/api/spaces/{slug}", json={"slug": "eng-standards"})
    assert r.status_code == 200, r.text
    assert r.json()["slug"] == "eng-standards"

    # The new slug addresses the space, contents intact.
    listed = await client.get("/api/spaces/eng-standards/items")
    assert listed.status_code == 200
    assert len(listed.json()["items"]) == 1
    # The old one is gone, with no redirect.
    assert (await client.get(f"/api/spaces/{slug}/items")).status_code == 404


async def test_name_and_slug_change_together(
    client: AsyncClient, shared: tuple[str, str]
) -> None:
    _admin, slug = shared
    r = await client.patch(
        f"/api/spaces/{slug}", json={"name": "Eng Standards", "slug": "eng"}
    )
    assert r.status_code == 200, r.text
    assert (r.json()["name"], r.json()["slug"]) == ("Eng Standards", "eng")


async def test_an_omitted_field_is_left_alone(
    client: AsyncClient, shared: tuple[str, str]
) -> None:
    """PATCH, not PUT: sending only a name must not blank the slug."""
    _admin, slug = shared
    r = await client.patch(f"/api/spaces/{slug}", json={"name": "Renamed"})
    assert r.json()["slug"] == slug
    r = await client.patch(f"/api/spaces/{slug}", json={"slug": "renamed"})
    assert r.json()["name"] == "Renamed"


async def test_renaming_to_its_own_slug_is_fine(
    client: AsyncClient, shared: tuple[str, str]
) -> None:
    """The uniqueness check has to exclude the row being edited, or
    re-saving an unchanged form would 409."""
    _admin, slug = shared
    r = await client.patch(
        f"/api/spaces/{slug}", json={"name": "Same slug", "slug": slug}
    )
    assert r.status_code == 200, r.text


# ── Validation ───────────────────────────────────────────────────────────────


async def test_a_taken_slug_conflicts(
    client: AsyncClient, shared: tuple[str, str]
) -> None:
    _admin, slug = shared
    await client.post("/api/spaces", json={"name": "Other", "slug": "other"})
    r = await client.patch(f"/api/spaces/{slug}", json={"slug": "other"})
    assert r.status_code == 409
    assert "already exists" in r.json()["detail"]


@pytest.mark.parametrize("bad", ["Not A Slug", "-leading", "trailing-", "wi th"])
async def test_a_malformed_slug_is_rejected(
    client: AsyncClient, shared: tuple[str, str], bad: str
) -> None:
    _admin, slug = shared
    r = await client.patch(f"/api/spaces/{slug}", json={"slug": bad})
    assert r.status_code == 400


async def test_the_personal_prefix_stays_reserved(
    client: AsyncClient, shared: tuple[str, str]
) -> None:
    """Otherwise a shared space could disguise itself as a personal one
    in every listing that reads the prefix."""
    _admin, slug = shared
    r = await client.patch(f"/api/spaces/{slug}", json={"slug": "u-sneaky"})
    assert r.status_code == 400
    assert "reserved" in r.json()["detail"]


async def test_a_blank_name_is_rejected(
    client: AsyncClient, shared: tuple[str, str]
) -> None:
    _admin, slug = shared
    r = await client.patch(f"/api/spaces/{slug}", json={"name": "   "})
    assert r.status_code == 400


# ── Personal spaces ──────────────────────────────────────────────────────────


async def test_a_personal_space_can_be_renamed_by_its_owner(
    client: AsyncClient,
) -> None:
    await login(client, "a@example.com")
    personal = await _personal_slug(client)
    r = await client.patch(f"/api/spaces/{personal}", json={"name": "My shelf"})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "My shelf"
    assert r.json()["is_personal"] is True


async def test_a_personal_slug_is_fixed(client: AsyncClient) -> None:
    """`is_personal` is derived from the u- prefix, so moving the slug
    would silently reclassify the space."""
    await login(client, "a@example.com")
    personal = await _personal_slug(client)
    r = await client.patch(f"/api/spaces/{personal}", json={"slug": "ada"})
    assert r.status_code == 400
    assert "fixed" in r.json()["detail"]


async def test_an_admin_cannot_rename_someone_elses_personal_space(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "admin_emails", ["admin@example.com"])
    admin_id = await login(client, "admin@example.com")
    await login(client, "other@example.com", link=True)
    other_personal = await _personal_slug(client)
    await client.post("/auth/switch", json={"user_id": admin_id})

    r = await client.patch(
        f"/api/spaces/{other_personal}", json={"name": "Renamed by admin"}
    )
    assert r.status_code == 403
    assert "belongs to" in r.json()["detail"]


# ── The admin override ───────────────────────────────────────────────────────


async def test_an_admin_can_rename_a_shared_space_they_do_not_own(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "admin_emails", ["admin@example.com"])
    admin_id = await login(client, "admin@example.com")

    # A second admin creates and owns the space.
    monkeypatch.setattr(
        settings, "admin_emails", ["admin@example.com", "boss@example.com"]
    )
    boss_id = await login(client, "boss@example.com", link=True)
    slug = str(
        (
            await client.post("/api/spaces", json={"name": "Theirs", "slug": "theirs"})
        ).json()["slug"]
    )

    await client.post("/auth/switch", json={"user_id": admin_id})
    r = await client.patch(
        f"/api/spaces/{slug}", json={"name": "Fixed", "slug": "fixed"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Fixed"
    # Renaming it did not make them its owner, or a member.
    assert r.json()["is_owner"] is False
    assert r.json()["role"] is None
    assert boss_id != admin_id


async def test_renaming_grants_the_admin_no_access(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole basis for the exception: a label change is not a way in."""
    monkeypatch.setattr(settings, "admin_emails", ["admin@example.com"])
    admin_id = await login(client, "admin@example.com")
    monkeypatch.setattr(
        settings, "admin_emails", ["admin@example.com", "boss@example.com"]
    )
    await login(client, "boss@example.com", link=True)
    slug = str(
        (
            await client.post("/api/spaces", json={"name": "Theirs", "slug": "theirs"})
        ).json()["slug"]
    )
    await client.post(
        f"/api/spaces/{slug}/items",
        json={"item_type": "report", "data": {"title": "Confidential"}},
    )

    await client.post("/auth/switch", json={"user_id": admin_id})
    assert (await client.patch(f"/api/spaces/{slug}", json={"name": "X"})).status_code == 200
    # Still cannot read it, list its members, or add to it.
    assert (await client.get(f"/api/spaces/{slug}/items")).status_code == 404
    assert (await client.get(f"/api/spaces/{slug}/members")).status_code in (403, 404)
    assert (
        await client.post(
            f"/api/spaces/{slug}/items", json={"item_type": "report", "data": {}}
        )
    ).status_code == 404
    # And it is absent from their own space listing.
    slugs = {s["slug"] for s in (await client.get("/api/me/spaces")).json()}
    assert slug not in slugs


# ── Everyone else ────────────────────────────────────────────────────────────


async def test_an_editor_cannot_rename(
    client: AsyncClient, shared: tuple[str, str]
) -> None:
    """Filling a space is not the same as naming it."""
    admin_id, slug = shared
    editor_id = await login(client, "editor@example.com", link=True)
    await client.post("/auth/switch", json={"user_id": admin_id})
    await client.post(
        f"/api/spaces/{slug}/members",
        json={"email": "editor@example.com", "role": "editor"},
    )
    await client.post("/auth/switch", json={"user_id": editor_id})

    r = await client.patch(f"/api/spaces/{slug}", json={"name": "Nope"})
    assert r.status_code == 403
    assert "owner" in r.json()["detail"]


async def test_a_stranger_gets_a_404(
    client: AsyncClient, shared: tuple[str, str]
) -> None:
    """A space they cannot see should not be confirmed to exist."""
    _admin, slug = shared
    await login(client, "stranger@example.com", link=True)
    r = await client.patch(f"/api/spaces/{slug}", json={"name": "Nope"})
    assert r.status_code == 404


async def test_an_unknown_space_is_a_404(
    client: AsyncClient, shared: tuple[str, str]
) -> None:
    r = await client.patch("/api/spaces/no-such-space", json={"name": "X"})
    assert r.status_code == 404


async def test_anonymous_is_unauthorized(client: AsyncClient) -> None:
    r = await client.patch("/api/spaces/anything", json={"name": "X"})
    assert r.status_code == 401
