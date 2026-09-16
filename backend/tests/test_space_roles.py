"""Per-space roles: what a viewer, an editor and an owner can each do.

The permission check used to be one line repeated in nine routers. These
tests pin the behaviour of the shared helper it became, and — more
usefully — walk the actual endpoints so a router that forgets to raise
its minimum shows up here rather than in production.
"""

import pytest
from httpx import AsyncClient

from shelf.config import settings

from .helpers import get_me, login


async def _space_slug(client: AsyncClient) -> str:
    spaces = (await client.get("/api/me/spaces")).json()
    slug = spaces[0]["slug"]
    assert isinstance(slug, str)
    return slug


async def _setup(
    client: AsyncClient, role: str | None
) -> tuple[str, str, str]:
    """Owner creates a space with one item; `other` is added at `role`
    (or not added at all when None). Leaves the client authenticated as
    `other`. Returns (slug, item_id, owner_id)."""
    owner_id = await login(client, "owner@example.com")
    slug = await _space_slug(client)
    created = await client.post(
        f"/api/spaces/{slug}/items",
        json={"item_type": "document", "data": {"title": "Shared doc"}},
    )
    assert created.status_code == 201, created.text
    item_id = created.json()["id"]

    other_id = await login(client, "other@example.com", link=True)
    await client.post("/auth/switch", json={"user_id": owner_id})
    if role is not None:
        r = await client.post(
            f"/api/spaces/{slug}/members",
            json={"email": "other@example.com", "role": role},
        )
        assert r.status_code == 201, r.text
    await client.post("/auth/switch", json={"user_id": other_id})
    return slug, item_id, owner_id


# ── No membership ────────────────────────────────────────────────────────────


async def test_non_member_cannot_see_the_space(client: AsyncClient) -> None:
    slug, item_id, _ = await _setup(client, None)
    # 404, not 403: a space you have no role in should not be confirmed
    # to exist.
    assert (await client.get(f"/api/spaces/{slug}/items")).status_code == 404
    assert (await client.get(f"/api/items/{item_id}")).status_code == 404


async def test_non_member_space_is_absent_from_their_listing(
    client: AsyncClient,
) -> None:
    slug, _, _ = await _setup(client, None)
    slugs = [s["slug"] for s in (await client.get("/api/me/spaces")).json()]
    assert slug not in slugs


# ── Viewer ───────────────────────────────────────────────────────────────────


async def test_viewer_can_read(client: AsyncClient) -> None:
    slug, item_id, _ = await _setup(client, "viewer")

    listing = await client.get(f"/api/spaces/{slug}/items")
    assert listing.status_code == 200
    assert [i["data"]["title"] for i in listing.json()["items"]] == ["Shared doc"]
    assert (await client.get(f"/api/items/{item_id}")).status_code == 200


async def test_viewer_sees_the_space_in_their_listing(client: AsyncClient) -> None:
    slug, _, _ = await _setup(client, "viewer")
    spaces = {s["slug"]: s for s in (await client.get("/api/me/spaces")).json()}
    assert spaces[slug]["role"] == "viewer"
    assert spaces[slug]["is_owner"] is False


async def test_viewer_cannot_write(client: AsyncClient) -> None:
    slug, item_id, _ = await _setup(client, "viewer")

    # 403, not 404: they can see the space, so hiding it would be a lie.
    create = await client.post(
        f"/api/spaces/{slug}/items",
        json={"item_type": "document", "data": {"title": "Nope"}},
    )
    assert create.status_code == 403
    assert "editor" in create.json()["detail"]

    assert (
        await client.patch(f"/api/items/{item_id}", json={"data": {"title": "x"}})
    ).status_code == 403
    assert (await client.delete(f"/api/items/{item_id}")).status_code == 403


async def test_viewer_cannot_write_anywhere_in_the_space(
    client: AsyncClient,
) -> None:
    """Spot-check the other routers, since each had to raise its own
    minimum and a miss would be silent."""
    slug, item_id, _ = await _setup(client, "viewer")

    assert (
        await client.post(
            f"/api/spaces/{slug}/collections", json={"name": "Nope"}
        )
    ).status_code == 403
    assert (
        await client.post(f"/api/spaces/{slug}/tags", json={"name": "nope"})
    ).status_code == 403
    assert (
        await client.post(
            f"/api/items/{item_id}/attachments",
            json={
                "filename": "x.pdf",
                "content_type": "application/pdf",
                "size_bytes": 1,
            },
        )
    ).status_code == 403


async def test_viewer_can_take_notes_but_they_start_shared(
    client: AsyncClient,
) -> None:
    """Notes are the deliberate exception to "viewer cannot write".

    Annotating a document you can only read is the point of notes, so
    read access is enough to write one — see auth/visibility.py. In a
    space the caller is actually a member of, the note is shared with
    that space, which is the behaviour notes always had; only inherited
    items default to private.
    """
    _slug, item_id, _ = await _setup(client, "viewer")

    created = await client.post(
        f"/api/items/{item_id}/notes", json={"content_html": "<p>mine</p>"}
    )
    assert created.status_code == 201, created.text
    assert created.json()["visibility"] == "space"
    assert created.json()["is_mine"] is True


async def test_viewer_can_export(client: AsyncClient) -> None:
    """Export is read-only, so viewer is enough."""
    slug, _, _ = await _setup(client, "viewer")
    r = await client.get(f"/api/spaces/{slug}/export?format=bibtex")
    assert r.status_code == 200, r.text
    assert "Shared doc" in r.text


# ── Editor ───────────────────────────────────────────────────────────────────


async def test_editor_can_write_content(client: AsyncClient) -> None:
    slug, item_id, _ = await _setup(client, "editor")

    created = await client.post(
        f"/api/spaces/{slug}/items",
        json={"item_type": "document", "data": {"title": "From the editor"}},
    )
    assert created.status_code == 201, created.text

    assert (
        await client.patch(
            f"/api/items/{item_id}", json={"data": {"title": "Edited"}}
        )
    ).status_code == 200
    assert (
        await client.post(f"/api/spaces/{slug}/tags", json={"name": "ok"})
    ).status_code == 201


async def test_editor_role_reported(client: AsyncClient) -> None:
    slug, _, _ = await _setup(client, "editor")
    spaces = {s["slug"]: s for s in (await client.get("/api/me/spaces")).json()}
    assert spaces[slug]["role"] == "editor"
    assert spaces[slug]["is_owner"] is False


async def test_editor_cannot_manage_members(client: AsyncClient) -> None:
    """Filling a space with content is not the same authority as widening
    who can see it."""
    slug, _, _ = await _setup(client, "editor")
    assert (await client.get(f"/api/spaces/{slug}/members")).status_code == 403
    assert (
        await client.post(
            f"/api/spaces/{slug}/members",
            json={"email": "someone@example.com", "role": "viewer"},
        )
    ).status_code == 403


# ── Owner ────────────────────────────────────────────────────────────────────


async def test_owner_role_reported(client: AsyncClient) -> None:
    await login(client, "owner@example.com")
    spaces = (await client.get("/api/me/spaces")).json()
    assert spaces[0]["role"] == "owner"
    assert spaces[0]["is_owner"] is True


async def test_owner_keeps_access_without_a_membership_row(
    client: AsyncClient,
) -> None:
    """Space.owner_id is authoritative — there is no row to delete that
    would lock the creator out."""
    slug, _, owner_id = await _setup(client, "editor")
    await client.post("/auth/switch", json={"user_id": owner_id})

    members = (await client.get(f"/api/spaces/{slug}/members")).json()
    owner_rows = [m for m in members if m["is_owner"]]
    assert len(owner_rows) == 1
    assert owner_rows[0]["role"] == "owner"
    # The owner has no removable membership row.
    assert (
        await client.delete(f"/api/spaces/{slug}/members/{owner_id}")
    ).status_code == 404


# ── Instance admins get nothing here ─────────────────────────────────────────


async def test_instance_admin_gets_no_space_access(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Handing out roles is not the same authority as reading everyone's
    library. An admin with no membership sees a 404 like anyone else."""
    await login(client, "owner@example.com")
    slug = await _space_slug(client)

    monkeypatch.setattr(settings, "admin_emails", ["boss@example.com"])
    await login(client, "boss@example.com", link=True)
    assert (await get_me(client))["is_admin"] is True

    assert (await client.get(f"/api/spaces/{slug}/items")).status_code == 404
    assert (await client.get(f"/api/spaces/{slug}/members")).status_code == 404
