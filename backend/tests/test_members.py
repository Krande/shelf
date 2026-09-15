"""Space membership management — /api/spaces/{slug}/members."""

import uuid

from httpx import AsyncClient

from .helpers import login


async def _owner_with_slug(client: AsyncClient) -> tuple[str, str]:
    owner_id = await login(client, "owner@example.com")
    slug = (await client.get("/api/me/spaces")).json()[0]["slug"]
    assert isinstance(slug, str)
    return owner_id, slug


async def _also_register(client: AsyncClient, email: str, owner_id: str) -> str:
    """Give `email` an account without disturbing who the client is."""
    user_id = await login(client, email, link=True)
    await client.post("/auth/switch", json={"user_id": owner_id})
    return user_id


async def test_new_space_lists_only_its_owner(client: AsyncClient) -> None:
    owner_id, slug = await _owner_with_slug(client)
    members = (await client.get(f"/api/spaces/{slug}/members")).json()
    assert len(members) == 1
    assert members[0]["user_id"] == owner_id
    assert members[0]["is_owner"] is True
    assert members[0]["role"] == "owner"


async def test_add_and_list_a_member(client: AsyncClient) -> None:
    owner_id, slug = await _owner_with_slug(client)
    other_id = await _also_register(client, "other@example.com", owner_id)

    r = await client.post(
        f"/api/spaces/{slug}/members",
        json={"email": "other@example.com", "role": "editor"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["role"] == "editor"
    assert r.json()["user_id"] == other_id

    members = (await client.get(f"/api/spaces/{slug}/members")).json()
    assert {m["user_id"] for m in members} == {owner_id, other_id}


async def test_role_defaults_to_viewer(client: AsyncClient) -> None:
    owner_id, slug = await _owner_with_slug(client)
    await _also_register(client, "other@example.com", owner_id)
    r = await client.post(
        f"/api/spaces/{slug}/members", json={"email": "other@example.com"}
    )
    assert r.status_code == 201
    assert r.json()["role"] == "viewer"


async def test_unknown_email_is_rejected(client: AsyncClient) -> None:
    _, slug = await _owner_with_slug(client)
    r = await client.post(
        f"/api/spaces/{slug}/members",
        json={"email": "nobody@example.com", "role": "viewer"},
    )
    assert r.status_code == 404
    assert "signed in" in r.json()["detail"]


async def test_cannot_add_the_owner(client: AsyncClient) -> None:
    _, slug = await _owner_with_slug(client)
    r = await client.post(
        f"/api/spaces/{slug}/members",
        json={"email": "owner@example.com", "role": "editor"},
    )
    assert r.status_code == 409


async def test_cannot_add_the_same_member_twice(client: AsyncClient) -> None:
    owner_id, slug = await _owner_with_slug(client)
    await _also_register(client, "other@example.com", owner_id)
    body = {"email": "other@example.com", "role": "viewer"}
    assert (
        await client.post(f"/api/spaces/{slug}/members", json=body)
    ).status_code == 201
    assert (
        await client.post(f"/api/spaces/{slug}/members", json=body)
    ).status_code == 409


async def test_invalid_role_rejected(client: AsyncClient) -> None:
    owner_id, slug = await _owner_with_slug(client)
    await _also_register(client, "other@example.com", owner_id)
    r = await client.post(
        f"/api/spaces/{slug}/members",
        json={"email": "other@example.com", "role": "owner"},
    )
    # Owner is not assignable: it belongs to the creator via Space.owner_id.
    assert r.status_code == 422


async def test_change_a_member_role(client: AsyncClient) -> None:
    owner_id, slug = await _owner_with_slug(client)
    other_id = await _also_register(client, "other@example.com", owner_id)
    await client.post(
        f"/api/spaces/{slug}/members",
        json={"email": "other@example.com", "role": "viewer"},
    )

    r = await client.patch(
        f"/api/spaces/{slug}/members/{other_id}", json={"role": "editor"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "editor"

    # And it takes effect: the promoted member can now write.
    await client.post("/auth/switch", json={"user_id": other_id})
    created = await client.post(
        f"/api/spaces/{slug}/items",
        json={"item_type": "document", "data": {"title": "Now allowed"}},
    )
    assert created.status_code == 201, created.text


async def test_remove_a_member_revokes_access(client: AsyncClient) -> None:
    owner_id, slug = await _owner_with_slug(client)
    other_id = await _also_register(client, "other@example.com", owner_id)
    await client.post(
        f"/api/spaces/{slug}/members",
        json={"email": "other@example.com", "role": "editor"},
    )

    await client.post("/auth/switch", json={"user_id": other_id})
    assert (await client.get(f"/api/spaces/{slug}/items")).status_code == 200

    await client.post("/auth/switch", json={"user_id": owner_id})
    r = await client.delete(f"/api/spaces/{slug}/members/{other_id}")
    assert r.status_code == 204

    await client.post("/auth/switch", json={"user_id": other_id})
    assert (await client.get(f"/api/spaces/{slug}/items")).status_code == 404


async def test_removed_member_keeps_nothing_but_loses_nothing_of_the_space(
    client: AsyncClient,
) -> None:
    """Content an editor created belongs to the space, not to them, so
    removing their access must not remove their work."""
    owner_id, slug = await _owner_with_slug(client)
    other_id = await _also_register(client, "other@example.com", owner_id)
    await client.post(
        f"/api/spaces/{slug}/members",
        json={"email": "other@example.com", "role": "editor"},
    )

    await client.post("/auth/switch", json={"user_id": other_id})
    await client.post(
        f"/api/spaces/{slug}/items",
        json={"item_type": "document", "data": {"title": "Their work"}},
    )

    await client.post("/auth/switch", json={"user_id": owner_id})
    await client.delete(f"/api/spaces/{slug}/members/{other_id}")

    titles = [
        i["data"]["title"]
        for i in (await client.get(f"/api/spaces/{slug}/items")).json()["items"]
    ]
    assert "Their work" in titles


async def test_patch_unknown_member(client: AsyncClient) -> None:
    _, slug = await _owner_with_slug(client)
    r = await client.patch(
        f"/api/spaces/{slug}/members/{uuid.uuid4()}", json={"role": "editor"}
    )
    assert r.status_code == 404


async def test_members_of_an_unknown_space(client: AsyncClient) -> None:
    await login(client, "owner@example.com")
    assert (await client.get("/api/spaces/nope/members")).status_code == 404


async def test_unauthenticated(client: AsyncClient) -> None:
    assert (await client.get("/api/spaces/anything/members")).status_code == 401
