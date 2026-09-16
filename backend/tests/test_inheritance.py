"""Space inheritance: one space reading another's items.

Inheriting is a read grant, so most of this file is about what it does
*not* let you do. The rules being pinned:

  * the parent has to opt in (`subscribable`)
  * the child's owner is the one who subscribes
  * everyone who can read the child can read the parent's items
  * nobody gains write access to them
  * it does not chain
"""

import pytest
from httpx import AsyncClient

from shelf.config import settings

from .helpers import login


async def _personal_slug(client: AsyncClient) -> str:
    spaces = (await client.get("/api/me/spaces")).json()
    slug = spaces[0]["slug"]
    assert isinstance(slug, str)
    return slug


async def _make_shared_space(
    client: AsyncClient, name: str, slug: str
) -> str:
    """Create a shared space as the current (admin) user."""
    r = await client.post("/api/spaces", json={"name": name, "slug": slug})
    assert r.status_code == 201, r.text
    created = r.json()
    assert isinstance(created["slug"], str)
    return str(created["slug"])


async def _add_item(client: AsyncClient, slug: str, title: str) -> str:
    r = await client.post(
        f"/api/spaces/{slug}/items",
        json={"item_type": "standard", "data": {"title": title}},
    )
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


@pytest.fixture
async def standards(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> tuple[str, str, str]:
    """An admin with a subscribable "standards" space holding one item.

    Returns (admin_id, standards_slug, item_id) with the client left
    authenticated as the admin.
    """
    monkeypatch.setattr(settings, "admin_emails", ["admin@example.com"])
    admin_id = await login(client, "admin@example.com")
    slug = await _make_shared_space(client, "Standards", "standards")
    item_id = await _add_item(client, slug, "ACME 1234")
    r = await client.patch(
        f"/api/spaces/{slug}/settings", json={"subscribable": True}
    )
    assert r.status_code == 200, r.text
    return admin_id, slug, item_id


# ── Opting in ────────────────────────────────────────────────────────────────


async def test_a_space_is_not_subscribable_by_default(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "admin_emails", ["admin@example.com"])
    await login(client, "admin@example.com")
    slug = await _make_shared_space(client, "Closed", "closed")
    assert slug not in {
        s["slug"] for s in (await client.get("/api/spaces/subscribable")).json()
    }


async def test_subscribable_spaces_are_listed_to_everyone(
    client: AsyncClient, standards: tuple[str, str, str]
) -> None:
    """Discoverability is the point: a Standards space has to be findable
    by people who hold no role in it, or nobody can subscribe."""
    _admin, slug, _item = standards
    await login(client, "nobody@example.com", link=True)
    listed = (await client.get("/api/spaces/subscribable")).json()
    assert slug in {s["slug"] for s in listed}


async def test_personal_spaces_cannot_be_opened(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await login(client, "a@example.com")
    personal = await _personal_slug(client)
    r = await client.patch(
        f"/api/spaces/{personal}/settings", json={"subscribable": True}
    )
    assert r.status_code == 400


async def test_only_the_owner_can_open_a_space(
    client: AsyncClient, standards: tuple[str, str, str]
) -> None:
    _admin, slug, _item = standards
    await login(client, "other@example.com", link=True)
    r = await client.patch(
        f"/api/spaces/{slug}/settings", json={"subscribable": False}
    )
    assert r.status_code in (403, 404)


# ── Subscribing ──────────────────────────────────────────────────────────────


async def test_personal_space_can_subscribe(
    client: AsyncClient, standards: tuple[str, str, str]
) -> None:
    """The case from the brief: a person subscribes their own shelf."""
    _admin, std_slug, item_id = standards
    await login(client, "eng@example.com", link=True)
    personal = await _personal_slug(client)

    r = await client.post(
        f"/api/spaces/{personal}/inherits", json={"parent_slug": std_slug}
    )
    assert r.status_code == 201, r.text

    listed = (await client.get(f"/api/spaces/{personal}/items")).json()
    ids = {i["id"] for i in listed["items"]}
    assert item_id in ids
    inherited = next(i for i in listed["items"] if i["id"] == item_id)
    assert inherited["is_inherited"] is True


async def test_subscribing_needs_the_parent_to_be_open(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "admin_emails", ["admin@example.com"])
    await login(client, "admin@example.com")
    closed = await _make_shared_space(client, "Closed", "closed")

    await login(client, "eng@example.com", link=True)
    personal = await _personal_slug(client)
    r = await client.post(
        f"/api/spaces/{personal}/inherits", json={"parent_slug": closed}
    )
    assert r.status_code == 403
    assert "not open" in r.json()["detail"]


async def test_only_the_child_owner_can_subscribe(
    client: AsyncClient, standards: tuple[str, str, str]
) -> None:
    """A viewer on a project space cannot decide what it inherits."""
    admin_id, std_slug, _item = standards
    project = await _make_shared_space(client, "Project", "project")

    viewer_id = await login(client, "viewer@example.com", link=True)
    await client.post("/auth/switch", json={"user_id": admin_id})
    await client.post(
        f"/api/spaces/{project}/members",
        json={"email": "viewer@example.com", "role": "viewer"},
    )
    await client.post("/auth/switch", json={"user_id": viewer_id})

    r = await client.post(
        f"/api/spaces/{project}/inherits", json={"parent_slug": std_slug}
    )
    assert r.status_code == 403


async def test_a_space_cannot_inherit_itself(
    client: AsyncClient, standards: tuple[str, str, str]
) -> None:
    _admin, std_slug, _item = standards
    r = await client.post(
        f"/api/spaces/{std_slug}/inherits", json={"parent_slug": std_slug}
    )
    assert r.status_code == 400


async def test_subscribing_twice_conflicts(
    client: AsyncClient, standards: tuple[str, str, str]
) -> None:
    _admin, std_slug, _item = standards
    await login(client, "eng@example.com", link=True)
    personal = await _personal_slug(client)
    await client.post(
        f"/api/spaces/{personal}/inherits", json={"parent_slug": std_slug}
    )
    r = await client.post(
        f"/api/spaces/{personal}/inherits", json={"parent_slug": std_slug}
    )
    assert r.status_code == 409


async def test_two_spaces_cannot_inherit_each_other(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "admin_emails", ["admin@example.com"])
    await login(client, "admin@example.com")
    a = await _make_shared_space(client, "A", "space-a")
    b = await _make_shared_space(client, "B", "space-b")
    for slug in (a, b):
        await client.patch(
            f"/api/spaces/{slug}/settings", json={"subscribable": True}
        )

    assert (
        await client.post(f"/api/spaces/{a}/inherits", json={"parent_slug": b})
    ).status_code == 201
    r = await client.post(f"/api/spaces/{b}/inherits", json={"parent_slug": a})
    assert r.status_code == 409


# ── What inheritance grants ──────────────────────────────────────────────────


async def test_inherited_items_are_read_only(
    client: AsyncClient, standards: tuple[str, str, str]
) -> None:
    _admin, std_slug, item_id = standards
    await login(client, "eng@example.com", link=True)
    personal = await _personal_slug(client)
    await client.post(
        f"/api/spaces/{personal}/inherits", json={"parent_slug": std_slug}
    )

    assert (await client.get(f"/api/items/{item_id}")).status_code == 200

    r = await client.patch(f"/api/items/{item_id}", json={"data": {"title": "x"}})
    assert r.status_code == 403
    assert "inherited" in r.json()["detail"].lower()
    assert (await client.delete(f"/api/items/{item_id}")).status_code == 403


async def test_inheritance_does_not_grant_membership(
    client: AsyncClient, standards: tuple[str, str, str]
) -> None:
    """Reading the parent's items is not being in the parent."""
    _admin, std_slug, _item = standards
    await login(client, "eng@example.com", link=True)
    personal = await _personal_slug(client)
    await client.post(
        f"/api/spaces/{personal}/inherits", json={"parent_slug": std_slug}
    )

    # Not a member: the owner-only member listing stays closed.
    assert (
        await client.get(f"/api/spaces/{std_slug}/members")
    ).status_code in (403, 404)
    # And they cannot add content to it.
    assert (
        await client.post(
            f"/api/spaces/{std_slug}/items",
            json={"item_type": "document", "data": {}},
        )
    ).status_code == 403


async def test_everyone_who_reads_the_child_reads_the_parents_items(
    client: AsyncClient, standards: tuple[str, str, str]
) -> None:
    """The grant is transitive through the child's membership — that's
    what makes a project space worth subscribing, and it's why the
    parent has to opt in."""
    admin_id, std_slug, item_id = standards
    project = await _make_shared_space(client, "Project", "project")
    await client.post(
        f"/api/spaces/{project}/inherits", json={"parent_slug": std_slug}
    )

    member_id = await login(client, "member@example.com", link=True)
    await client.post("/auth/switch", json={"user_id": admin_id})
    await client.post(
        f"/api/spaces/{project}/members",
        json={"email": "member@example.com", "role": "viewer"},
    )
    await client.post("/auth/switch", json={"user_id": member_id})

    listed = (await client.get(f"/api/spaces/{project}/items")).json()
    assert item_id in {i["id"] for i in listed["items"]}
    assert (await client.get(f"/api/items/{item_id}")).status_code == 200


async def test_inheritance_does_not_chain(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A inherits B, B inherits C — A must not see C.

    One hop is what the model promises, so a space owner can answer "who
    can see my items" from one table.
    """
    monkeypatch.setattr(settings, "admin_emails", ["admin@example.com"])
    await login(client, "admin@example.com")
    c = await _make_shared_space(client, "C", "space-c")
    deep_item = await _add_item(client, c, "Deep")
    b = await _make_shared_space(client, "B", "space-b")
    mid_item = await _add_item(client, b, "Mid")
    a = await _make_shared_space(client, "A", "space-a")
    for slug in (b, c):
        await client.patch(
            f"/api/spaces/{slug}/settings", json={"subscribable": True}
        )
    await client.post(f"/api/spaces/{b}/inherits", json={"parent_slug": c})
    await client.post(f"/api/spaces/{a}/inherits", json={"parent_slug": b})

    listed = {i["id"] for i in (await client.get(f"/api/spaces/{a}/items")).json()["items"]}
    assert mid_item in listed
    assert deep_item not in listed


async def test_collections_come_across_flagged_and_grouped(
    client: AsyncClient, standards: tuple[str, str, str]
) -> None:
    """Inheriting the items without the organisation leaves a project
    looking at a flat list of everything, which throws away most of the
    value of subscribing."""
    _admin, std_slug, item_id = standards
    coll = await client.post(
        f"/api/spaces/{std_slug}/collections", json={"name": "Structural"}
    )
    assert coll.status_code == 201, coll.text
    coll_id = coll.json()["id"]
    await client.put(
        f"/api/items/{item_id}/collections", json={"collection_ids": [coll_id]}
    )

    await login(client, "eng@example.com", link=True)
    personal = await _personal_slug(client)
    await client.post(
        f"/api/spaces/{personal}/inherits", json={"parent_slug": std_slug}
    )
    await client.post(
        f"/api/spaces/{personal}/collections", json={"name": "My own"}
    )

    listed = (await client.get(f"/api/spaces/{personal}/collections")).json()
    by_name = {c["name"]: c for c in listed}
    assert by_name["My own"]["is_inherited"] is False
    assert by_name["Structural"]["is_inherited"] is True
    # Named, so the rail can head the borrowed group with its origin.
    assert by_name["Structural"]["space_name"] == "Standards"
    # Own first, so a project's folders aren't shuffled in among borrowed
    # ones by positions that both restart at zero.
    assert listed[0]["name"] == "My own"


async def test_an_inherited_collection_filters_the_listing(
    client: AsyncClient, standards: tuple[str, str, str]
) -> None:
    """The point of bringing them across: they're a usable filter over
    items already visible here."""
    _admin, std_slug, item_id = standards
    coll_id = (
        await client.post(
            f"/api/spaces/{std_slug}/collections", json={"name": "Structural"}
        )
    ).json()["id"]
    await client.put(
        f"/api/items/{item_id}/collections", json={"collection_ids": [coll_id]}
    )

    await login(client, "eng@example.com", link=True)
    personal = await _personal_slug(client)
    await client.post(
        f"/api/spaces/{personal}/inherits", json={"parent_slug": std_slug}
    )

    filtered = (
        await client.get(f"/api/spaces/{personal}/items?collection={coll_id}")
    ).json()
    assert [i["id"] for i in filtered["items"]] == [item_id]


async def test_an_inherited_collection_is_read_only(
    client: AsyncClient, standards: tuple[str, str, str]
) -> None:
    _admin, std_slug, _item = standards
    coll_id = (
        await client.post(
            f"/api/spaces/{std_slug}/collections", json={"name": "Structural"}
        )
    ).json()["id"]

    await login(client, "eng@example.com", link=True)
    personal = await _personal_slug(client)
    await client.post(
        f"/api/spaces/{personal}/inherits", json={"parent_slug": std_slug}
    )

    renamed = await client.patch(
        f"/api/collections/{coll_id}", json={"name": "Mine now"}
    )
    assert renamed.status_code == 403
    assert (await client.delete(f"/api/collections/{coll_id}")).status_code == 403


async def test_unsubscribing_takes_the_items_away(
    client: AsyncClient, standards: tuple[str, str, str]
) -> None:
    _admin, std_slug, item_id = standards
    await login(client, "eng@example.com", link=True)
    personal = await _personal_slug(client)
    await client.post(
        f"/api/spaces/{personal}/inherits", json={"parent_slug": std_slug}
    )
    assert (await client.get(f"/api/items/{item_id}")).status_code == 200

    r = await client.delete(f"/api/spaces/{personal}/inherits/{std_slug}")
    assert r.status_code == 204
    assert (await client.get(f"/api/items/{item_id}")).status_code == 404


async def test_the_parent_owner_can_see_and_drop_subscribers(
    client: AsyncClient, standards: tuple[str, str, str]
) -> None:
    """`subscribable` is a standing offer, not a door that can't shut."""
    admin_id, std_slug, item_id = standards
    eng_id = await login(client, "eng@example.com", link=True)
    personal = await _personal_slug(client)
    await client.post(
        f"/api/spaces/{personal}/inherits", json={"parent_slug": std_slug}
    )

    await client.post("/auth/switch", json={"user_id": admin_id})
    subs = (await client.get(f"/api/spaces/{std_slug}/subscribers")).json()
    assert personal in {s["slug"] for s in subs}

    r = await client.delete(f"/api/spaces/{std_slug}/subscribers/{personal}")
    assert r.status_code == 204

    await client.post("/auth/switch", json={"user_id": eng_id})
    assert (await client.get(f"/api/items/{item_id}")).status_code == 404


async def test_closing_a_space_leaves_existing_subscribers_alone(
    client: AsyncClient, standards: tuple[str, str, str]
) -> None:
    """Deliberate: access evaporating across every project because a flag
    was toggled is a worse surprise than a stale subscription."""
    admin_id, std_slug, item_id = standards
    eng_id = await login(client, "eng@example.com", link=True)
    personal = await _personal_slug(client)
    await client.post(
        f"/api/spaces/{personal}/inherits", json={"parent_slug": std_slug}
    )

    await client.post("/auth/switch", json={"user_id": admin_id})
    await client.patch(
        f"/api/spaces/{std_slug}/settings", json={"subscribable": False}
    )

    await client.post("/auth/switch", json={"user_id": eng_id})
    assert (await client.get(f"/api/items/{item_id}")).status_code == 200


async def test_a_stranger_still_cannot_read_the_parent(
    client: AsyncClient, standards: tuple[str, str, str]
) -> None:
    """Subscribable makes a space discoverable, not public."""
    _admin, std_slug, item_id = standards
    await login(client, "stranger@example.com", link=True)
    assert (await client.get(f"/api/items/{item_id}")).status_code == 404
    assert (await client.get(f"/api/spaces/{std_slug}/items")).status_code == 404
