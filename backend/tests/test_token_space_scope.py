"""What an API token can reach, and how to narrow it.

Two things pinned here. First, that a token reaches everything its user
can — owned, shared *and* inherited — because a token acting as a user
that can't find what the user can find is a miserable thing to debug.
Second, that `allowed_space_ids` narrows it, and can only ever narrow.
"""

import pytest
from httpx import AsyncClient

from shelf.config import settings

from .helpers import login


async def _personal_slug(client: AsyncClient) -> str:
    return str((await client.get("/api/me/spaces")).json()[0]["slug"])


async def _space_id(client: AsyncClient, slug: str) -> str:
    spaces = (await client.get("/api/me/spaces")).json()
    return str(next(s["id"] for s in spaces if s["slug"] == slug))


async def _mint(client: AsyncClient, **over: object) -> str:
    body: dict[str, object] = {
        "name": "script",
        "scopes": ["search", "upload", "download"],
    }
    body.update(over)
    r = await client.post("/api/me/tokens", json=body)
    assert r.status_code == 201, r.text
    return str(r.json()["plaintext"])


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def world(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> dict[str, str]:
    """An engineer with three sources of items:

    * their personal space (owned)
    * a project space they're an editor on (shared)
    * a Standards space their personal shelf subscribes to (inherited)

    Leaves the client authenticated as the engineer.
    """
    monkeypatch.setattr(settings, "admin_emails", ["admin@example.com"])
    admin_id = await login(client, "admin@example.com")

    std = str(
        (
            await client.post(
                "/api/spaces", json={"name": "Standards", "slug": "standards"}
            )
        ).json()["slug"]
    )
    std_item = str(
        (
            await client.post(
                f"/api/spaces/{std}/items",
                json={"item_type": "standard", "data": {"title": "ACME 1234"}},
            )
        ).json()["id"]
    )
    await client.patch(
        f"/api/spaces/{std}/settings", json={"subscribable": True}
    )

    project = str(
        (
            await client.post(
                "/api/spaces", json={"name": "Project", "slug": "project"}
            )
        ).json()["slug"]
    )
    project_item = str(
        (
            await client.post(
                f"/api/spaces/{project}/items",
                json={"item_type": "report", "data": {"title": "Project brief"}},
            )
        ).json()["id"]
    )

    eng_id = await login(client, "eng@example.com", link=True)
    personal = await _personal_slug(client)
    await client.post(
        f"/api/spaces/{personal}/inherits", json={"parent_slug": std}
    )
    own_item = str(
        (
            await client.post(
                f"/api/spaces/{personal}/items",
                json={"item_type": "document", "data": {"title": "My notes"}},
            )
        ).json()["id"]
    )

    await client.post("/auth/switch", json={"user_id": admin_id})
    await client.post(
        f"/api/spaces/{project}/members",
        json={"email": "eng@example.com", "role": "editor"},
    )
    await client.post("/auth/switch", json={"user_id": eng_id})

    return {
        "personal": personal,
        "project": project,
        "standards": std,
        "own_item": own_item,
        "project_item": project_item,
        "std_item": std_item,
        "admin_id": admin_id,
        "eng_id": eng_id,
    }


# ── Reach ────────────────────────────────────────────────────────────────────


async def test_an_unscoped_token_sees_every_kind_of_space(
    client: AsyncClient, world: dict[str, str]
) -> None:
    """Owned, shared, and inherited. The inherited one is the case that
    was missing: /api/v1/search used to bound itself to spaces the user
    owned or belonged to, so a subscribed Standards space was invisible
    to scripts while plainly visible in the SPA."""
    token = await _mint(client)
    r = await client.get("/api/v1/search", headers=_auth(token))
    assert r.status_code == 200, r.text
    titles = {i["data"]["title"] for i in r.json()}
    assert titles == {"My notes", "Project brief", "ACME 1234"}


async def test_an_inherited_item_is_fetchable_by_token(
    client: AsyncClient, world: dict[str, str]
) -> None:
    token = await _mint(client)
    r = await client.get(
        f"/api/v1/items/{world['std_item']}/attachments", headers=_auth(token)
    )
    assert r.status_code == 200, r.text


async def test_a_token_sees_no_more_than_its_user(
    client: AsyncClient, world: dict[str, str]
) -> None:
    """A space nobody shared stays invisible."""
    token = await _mint(client)

    await client.post("/auth/switch", json={"user_id": world["admin_id"]})
    secret = str(
        (
            await client.post("/api/spaces", json={"name": "Secret", "slug": "secret"})
        ).json()["slug"]
    )
    hidden = str(
        (
            await client.post(
                f"/api/spaces/{secret}/items",
                json={"item_type": "report", "data": {"title": "Hidden"}},
            )
        ).json()["id"]
    )

    r = await client.get("/api/v1/search", headers=_auth(token))
    assert "Hidden" not in {i["data"]["title"] for i in r.json()}
    assert (
        await client.get(
            f"/api/v1/items/{hidden}/attachments", headers=_auth(token)
        )
    ).status_code == 404


# ── Narrowing ────────────────────────────────────────────────────────────────


async def test_a_space_scoped_token_sees_only_that_space(
    client: AsyncClient, world: dict[str, str]
) -> None:
    token = await _mint(
        client,
        allowed_space_ids=[await _space_id(client, world["project"])],
    )
    r = await client.get("/api/v1/search", headers=_auth(token))
    assert {i["data"]["title"] for i in r.json()} == {"Project brief"}


async def test_a_token_can_be_scoped_to_an_inherited_space(
    client: AsyncClient, world: dict[str, str]
) -> None:
    """Read-only access to just the shared Standards space is the
    obvious thing to hand a script."""
    std_id = str(
        (
            await client.get(f"/api/spaces/{world['personal']}/inherits")
        ).json()[0]["space_id"]
    )
    token = await _mint(client, allowed_space_ids=[std_id])
    r = await client.get("/api/v1/search", headers=_auth(token))
    assert {i["data"]["title"] for i in r.json()} == {"ACME 1234"}


async def test_scoping_hides_items_by_id_too(
    client: AsyncClient, world: dict[str, str]
) -> None:
    """Not just the listing — a token that knows an id must not be able
    to walk around its own allow-list."""
    token = await _mint(
        client,
        allowed_space_ids=[await _space_id(client, world["project"])],
    )
    r = await client.get(
        f"/api/v1/items/{world['own_item']}/attachments", headers=_auth(token)
    )
    assert r.status_code == 404


async def test_scoping_blocks_uploads_elsewhere(
    client: AsyncClient, world: dict[str, str]
) -> None:
    token = await _mint(
        client,
        allowed_space_ids=[await _space_id(client, world["project"])],
    )
    r = await client.post(
        "/api/v1/uploads/register",
        headers=_auth(token),
        json={
            "filename": "x.pdf",
            "content_type": "application/pdf",
            "size_bytes": 10,
            "title": "Smuggled",
            "space_slug": world["personal"],
        },
    )
    assert r.status_code == 404

    # …but its own space still works, so the 404 above is the allow-list
    # and not a broken request.
    ok = await client.post(
        "/api/v1/uploads/register",
        headers=_auth(token),
        json={
            "filename": "x.pdf",
            "content_type": "application/pdf",
            "size_bytes": 10,
            "title": "Allowed",
            "space_slug": world["project"],
        },
    )
    assert ok.status_code == 201, ok.text


async def test_an_allow_list_cannot_widen_access(
    client: AsyncClient, world: dict[str, str]
) -> None:
    """Minting against a space you can't read is refused outright."""
    await client.post("/auth/switch", json={"user_id": world["admin_id"]})
    created = await client.post(
        "/api/spaces", json={"name": "Secret", "slug": "secret"}
    )
    assert created.status_code == 201, created.text
    secret_id = str(created.json()["id"])
    await client.post("/auth/switch", json={"user_id": world["eng_id"]})

    r = await client.post(
        "/api/me/tokens",
        json={
            "name": "sneaky",
            "scopes": ["search"],
            "allowed_space_ids": [secret_id],
        },
    )
    assert r.status_code == 400
    assert "not one you can access" in r.json()["detail"]


async def test_losing_the_space_loses_the_token_reach(
    client: AsyncClient, world: dict[str, str]
) -> None:
    """The allow-list is intersected with live access, not a snapshot —
    dropping the subscription has to take the token's reach with it."""
    std_id = str(
        (
            await client.get(f"/api/spaces/{world['personal']}/inherits")
        ).json()[0]["space_id"]
    )
    token = await _mint(client, allowed_space_ids=[std_id])
    assert len((await client.get("/api/v1/search", headers=_auth(token))).json()) == 1

    await client.delete(
        f"/api/spaces/{world['personal']}/inherits/{world['standards']}"
    )
    assert (await client.get("/api/v1/search", headers=_auth(token))).json() == []


async def test_an_empty_allow_list_is_refused(
    client: AsyncClient, world: dict[str, str]
) -> None:
    """Rather than minting a token that can reach nothing at all."""
    r = await client.post(
        "/api/me/tokens",
        json={"name": "x", "scopes": ["search"], "allowed_space_ids": []},
    )
    assert r.status_code == 400
    assert "drop the field" in r.json()["detail"]


async def test_the_allow_list_round_trips_on_the_listing(
    client: AsyncClient, world: dict[str, str]
) -> None:
    project_id = await _space_id(client, world["project"])
    await _mint(client, allowed_space_ids=[project_id])
    listed = (await client.get("/api/me/tokens")).json()
    assert listed[0]["allowed_space_ids"] == [project_id]


async def test_an_unscoped_token_reports_no_allow_list(
    client: AsyncClient, world: dict[str, str]
) -> None:
    await _mint(client)
    listed = (await client.get("/api/me/tokens")).json()
    assert listed[0]["allowed_space_ids"] is None
