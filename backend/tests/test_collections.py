"""Integration tests for collections + item membership."""

import uuid

import pytest
from httpx import AsyncClient

from shelf.config import settings

from .helpers import login


async def _login(client: AsyncClient, email: str = "alice@example.com") -> str:
    r = await client.post("/auth/dev-login", json={"email": email})
    assert r.status_code == 200, r.text
    me = await client.get("/api/me")
    user_id = me.json()["id"]
    return f"u-{user_id.replace('-', '')[:8]}"


async def test_create_and_list_collections(client: AsyncClient) -> None:
    slug = await _login(client)
    r = await client.post(
        f"/api/spaces/{slug}/collections", json={"name": "Reading list"}
    )
    assert r.status_code == 201, r.text
    coll = r.json()
    assert coll["name"] == "Reading list"
    assert coll["parent_id"] is None

    listing = await client.get(f"/api/spaces/{slug}/collections")
    assert listing.status_code == 200
    rows = listing.json()
    assert [c["id"] for c in rows] == [coll["id"]]


async def test_nested_collections(client: AsyncClient) -> None:
    slug = await _login(client)
    parent = (
        await client.post(
            f"/api/spaces/{slug}/collections", json={"name": "Papers"}
        )
    ).json()
    child = (
        await client.post(
            f"/api/spaces/{slug}/collections",
            json={"name": "ML", "parent_id": parent["id"]},
        )
    ).json()
    assert child["parent_id"] == parent["id"]


async def test_item_collection_membership_round_trip(client: AsyncClient) -> None:
    slug = await _login(client)
    coll = (
        await client.post(
            f"/api/spaces/{slug}/collections", json={"name": "ML"}
        )
    ).json()
    item = (
        await client.post(
            f"/api/spaces/{slug}/items",
            json={"item_type": "document", "data": {"title": "paper"}},
        )
    ).json()
    assert item["collection_ids"] == []

    r = await client.put(
        f"/api/items/{item['id']}/collections",
        json={"collection_ids": [coll["id"]]},
    )
    assert r.status_code == 200
    assert r.json() == [coll["id"]]

    fetched = (await client.get(f"/api/items/{item['id']}")).json()
    assert fetched["collection_ids"] == [coll["id"]]

    # Filtering items by collection returns the membership.
    listing = (
        await client.get(
            f"/api/spaces/{slug}/items", params={"collection": coll["id"]}
        )
    ).json()
    assert listing["total"] == 1
    assert [r["id"] for r in listing["items"]] == [item["id"]]

    # Replacing the set with [] unfiles the item.
    await client.put(
        f"/api/items/{item['id']}/collections", json={"collection_ids": []}
    )
    refetched = (await client.get(f"/api/items/{item['id']}")).json()
    assert refetched["collection_ids"] == []


async def test_unfiled_filter(client: AsyncClient) -> None:
    slug = await _login(client)
    coll = (
        await client.post(
            f"/api/spaces/{slug}/collections", json={"name": "C"}
        )
    ).json()
    filed = (
        await client.post(
            f"/api/spaces/{slug}/items",
            json={"item_type": "document", "data": {"title": "filed"}},
        )
    ).json()
    unfiled = (
        await client.post(
            f"/api/spaces/{slug}/items",
            json={"item_type": "document", "data": {"title": "unfiled"}},
        )
    ).json()
    await client.put(
        f"/api/items/{filed['id']}/collections",
        json={"collection_ids": [coll["id"]]},
    )

    rows = (
        await client.get(
            f"/api/spaces/{slug}/items", params={"collection": "unfiled"}
        )
    ).json()
    assert rows["total"] == 1
    assert [r["id"] for r in rows["items"]] == [unfiled["id"]]


async def test_delete_collection_unlinks_items_only(client: AsyncClient) -> None:
    slug = await _login(client)
    coll = (
        await client.post(
            f"/api/spaces/{slug}/collections", json={"name": "C"}
        )
    ).json()
    item = (
        await client.post(
            f"/api/spaces/{slug}/items",
            json={"item_type": "document", "data": {"title": "T"}},
        )
    ).json()
    await client.put(
        f"/api/items/{item['id']}/collections",
        json={"collection_ids": [coll["id"]]},
    )

    r = await client.delete(f"/api/collections/{coll['id']}")
    assert r.status_code == 204

    # Item itself stays put; just the membership goes.
    fetched = (await client.get(f"/api/items/{item['id']}")).json()
    assert fetched["collection_ids"] == []


async def test_reparent_cycle_rejected(client: AsyncClient) -> None:
    slug = await _login(client)
    a = (
        await client.post(
            f"/api/spaces/{slug}/collections", json={"name": "A"}
        )
    ).json()
    b = (
        await client.post(
            f"/api/spaces/{slug}/collections",
            json={"name": "B", "parent_id": a["id"]},
        )
    ).json()
    # Reparent A under B → cycle.
    r = await client.patch(
        f"/api/collections/{a['id']}", json={"parent_id": b["id"]}
    )
    assert r.status_code == 400


async def test_collection_description_round_trip(client: AsyncClient) -> None:
    slug = await _login(client)
    coll = (
        await client.post(
            f"/api/spaces/{slug}/collections",
            json={"name": "Papers", "description": "  reading queue  "},
        )
    ).json()
    # Whitespace gets trimmed on the way in.
    assert coll["description"] == "reading queue"

    updated = (
        await client.patch(
            f"/api/collections/{coll['id']}", json={"description": "  "}
        )
    ).json()
    # Empty / whitespace-only clears the description.
    assert updated["description"] is None

    updated = (
        await client.patch(
            f"/api/collections/{coll['id']}", json={"description": "v2"}
        )
    ).json()
    assert updated["description"] == "v2"


async def test_collections_create_assigns_position(client: AsyncClient) -> None:
    slug = await _login(client)
    names = ["one", "two", "three"]
    created = []
    for name in names:
        r = await client.post(
            f"/api/spaces/{slug}/collections", json={"name": name}
        )
        created.append(r.json())
    # Insertion order = position order.
    assert [c["position"] for c in created] == [0, 1, 2]

    listing = (await client.get(f"/api/spaces/{slug}/collections")).json()
    assert [c["name"] for c in listing] == names


async def test_reorder_within_parent(client: AsyncClient) -> None:
    slug = await _login(client)
    a = (
        await client.post(
            f"/api/spaces/{slug}/collections", json={"name": "A"}
        )
    ).json()
    b = (
        await client.post(
            f"/api/spaces/{slug}/collections", json={"name": "B"}
        )
    ).json()
    c = (
        await client.post(
            f"/api/spaces/{slug}/collections", json={"name": "C"}
        )
    ).json()
    # Move C to the very front.
    r = await client.patch(
        f"/api/collections/{c['id']}", json={"position": 0}
    )
    assert r.status_code == 200, r.text
    listing = (await client.get(f"/api/spaces/{slug}/collections")).json()
    assert [row["name"] for row in listing] == ["C", "A", "B"]
    assert [row["position"] for row in listing] == [0, 1, 2]

    # Beyond-end clamps to end.
    await client.patch(
        f"/api/collections/{a['id']}", json={"position": 99}
    )
    listing = (await client.get(f"/api/spaces/{slug}/collections")).json()
    assert [row["name"] for row in listing] == ["C", "B", "A"]
    # Reference b so the linter doesn't complain about an unused name.
    assert b["id"] in [row["id"] for row in listing]


async def test_reparent_and_reposition(client: AsyncClient) -> None:
    slug = await _login(client)
    p1 = (
        await client.post(
            f"/api/spaces/{slug}/collections", json={"name": "P1"}
        )
    ).json()
    p2 = (
        await client.post(
            f"/api/spaces/{slug}/collections", json={"name": "P2"}
        )
    ).json()
    a = (
        await client.post(
            f"/api/spaces/{slug}/collections",
            json={"name": "A", "parent_id": p1["id"]},
        )
    ).json()
    b = (
        await client.post(
            f"/api/spaces/{slug}/collections",
            json={"name": "B", "parent_id": p1["id"]},
        )
    ).json()
    assert [a["position"], b["position"]] == [0, 1]
    # Move A under P2 at position 0.
    moved = (
        await client.patch(
            f"/api/collections/{a['id']}",
            json={"parent_id": p2["id"], "position": 0},
        )
    ).json()
    assert moved["parent_id"] == p2["id"]
    assert moved["position"] == 0

    listing = (await client.get(f"/api/spaces/{slug}/collections")).json()
    by_name = {c["name"]: c for c in listing}
    # B re-packed back to position 0 under P1.
    assert by_name["B"]["parent_id"] == p1["id"]
    assert by_name["B"]["position"] == 0
    assert by_name["A"]["parent_id"] == p2["id"]
    assert by_name["A"]["position"] == 0


async def test_reparent_to_root(client: AsyncClient) -> None:
    slug = await _login(client)
    p = (
        await client.post(
            f"/api/spaces/{slug}/collections", json={"name": "P"}
        )
    ).json()
    c = (
        await client.post(
            f"/api/spaces/{slug}/collections",
            json={"name": "C", "parent_id": p["id"]},
        )
    ).json()
    # parent_id explicitly null = move to root.
    moved = (
        await client.patch(
            f"/api/collections/{c['id']}", json={"parent_id": None}
        )
    ).json()
    assert moved["parent_id"] is None


async def test_delete_repacks_positions(client: AsyncClient) -> None:
    slug = await _login(client)
    created = []
    for name in ["A", "B", "C", "D"]:
        created.append(
            (
                await client.post(
                    f"/api/spaces/{slug}/collections", json={"name": name}
                )
            ).json()
        )
    # Delete B; A/C/D should renumber to 0/1/2.
    await client.delete(f"/api/collections/{created[1]['id']}")
    listing = (await client.get(f"/api/spaces/{slug}/collections")).json()
    assert [c["name"] for c in listing] == ["A", "C", "D"]
    assert [c["position"] for c in listing] == [0, 1, 2]


async def test_other_users_cannot_see_collections(client: AsyncClient) -> None:
    alice_slug = await _login(client, "alice@example.com")
    coll = (
        await client.post(
            f"/api/spaces/{alice_slug}/collections", json={"name": "private"}
        )
    ).json()

    await client.post("/auth/logout")
    await _login(client, "mallory@example.com")

    r = await client.get(f"/api/spaces/{alice_slug}/collections")
    assert r.status_code == 404
    r = await client.get(f"/api/collections/{coll['id']}")
    # No GET endpoint, but the PATCH/DELETE/list path resolves the same way.
    r = await client.delete(f"/api/collections/{coll['id']}")
    assert r.status_code == 404


async def test_collection_from_another_space_is_not_a_silent_empty_list(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stale filter must say so rather than look like an empty space.

    The library carried ?collection= across a space switch, so listing
    the target filtered by a folder only the source had. The join
    matched nothing and the response was 200 with no items, which reads
    as "your copy never arrived".
    """
    monkeypatch.setattr(settings, "admin_emails", ["admin@example.com"])
    await login(client, "admin@example.com")
    source = (
        await client.post("/api/spaces", json={"name": "Source", "slug": "src"})
    ).json()["slug"]
    target = (
        await client.post("/api/spaces", json={"name": "Target", "slug": "tgt"})
    ).json()["slug"]

    coll = (
        await client.post(
            f"/api/spaces/{source}/collections", json={"name": "Drawings"}
        )
    ).json()["id"]
    item_id = (
        await client.post(
            f"/api/spaces/{source}/items",
            json={"item_type": "document", "data": {"title": "Plan"}},
        )
    ).json()["id"]
    await client.put(
        f"/api/items/{item_id}/collections", json={"collection_ids": [coll]}
    )
    await client.post(
        f"/api/items/{item_id}/copy",
        json={"target_slug": target, "include_attachments": False},
    )

    # The copy really is there.
    plain = await client.get(f"/api/spaces/{target}/items")
    assert len(plain.json()["items"]) == 1

    # ...and the source's folder is not a filter this space accepts.
    stale = await client.get(
        f"/api/spaces/{target}/items", params={"collection": coll}
    )
    assert stale.status_code == 404, stale.text


async def test_unknown_collection_id_is_404(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "admin_emails", ["admin@example.com"])
    await login(client, "admin@example.com")
    slug = (
        await client.post("/api/spaces", json={"name": "S", "slug": "s-unknown"})
    ).json()["slug"]
    resp = await client.get(
        f"/api/spaces/{slug}/items", params={"collection": str(uuid.uuid4())}
    )
    assert resp.status_code == 404


async def test_an_inherited_collection_is_still_a_valid_filter(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The space check must not cost inheritance its folders.

    A subscriber lists the parent's items and the parent's collections
    come with them, so a collection id belonging to another space is
    legitimate here -- it just has to be a space this one inherits.
    """
    monkeypatch.setattr(settings, "admin_emails", ["admin@example.com"])
    await login(client, "admin@example.com")
    parent = (
        await client.post(
            "/api/spaces", json={"name": "Standards", "slug": "std-src"}
        )
    ).json()["slug"]
    child = (
        await client.post(
            "/api/spaces", json={"name": "Project", "slug": "proj-sub"}
        )
    ).json()["slug"]

    coll = (
        await client.post(
            f"/api/spaces/{parent}/collections", json={"name": "Piping"}
        )
    ).json()["id"]
    item_id = (
        await client.post(
            f"/api/spaces/{parent}/items",
            json={"item_type": "document", "data": {"title": "Spec"}},
        )
    ).json()["id"]
    await client.put(
        f"/api/items/{item_id}/collections", json={"collection_ids": [coll]}
    )

    await client.patch(
        f"/api/spaces/{parent}/settings", json={"subscribable": True}
    )
    sub = await client.post(
        f"/api/spaces/{child}/inherits", json={"parent_slug": parent}
    )
    assert sub.status_code in (200, 201), sub.text

    resp = await client.get(
        f"/api/spaces/{child}/items", params={"collection": coll}
    )
    assert resp.status_code == 200, resp.text
    assert [i["id"] for i in resp.json()["items"]] == [item_id]


async def _sub(client, slug: str, name: str, parent: str | None = None) -> str:
    body: dict = {"name": name}
    if parent is not None:
        body["parent_id"] = parent
    r = await client.post(f"/api/spaces/{slug}/collections", json=body)
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def _doc(client, slug: str, title: str, colls: list[str]) -> str:
    item_id = (
        await client.post(
            f"/api/spaces/{slug}/items",
            json={"item_type": "document", "data": {"title": title}},
        )
    ).json()["id"]
    r = await client.put(
        f"/api/items/{item_id}/collections", json={"collection_ids": colls}
    )
    assert r.status_code == 200, r.text
    return str(item_id)


async def test_subcollection_scope_lists_the_tree_below(
    client: AsyncClient,
) -> None:
    """The library shows a folder's own documents, then offers what is
    filed under its subcollections below them -- two listings, so this
    one reaches any depth but excludes the parent's own members."""
    slug = await _login(client)
    parent = await _sub(client, slug, "Parent")
    child = await _sub(client, slug, "Child", parent)
    grandchild = await _sub(client, slug, "Grandchild", child)
    elsewhere = await _sub(client, slug, "Unrelated")

    own = await _doc(client, slug, "Own", [parent])
    deep = await _doc(client, slug, "Deep", [grandchild])
    mid = await _doc(client, slug, "Mid", [child])
    await _doc(client, slug, "Elsewhere", [elsewhere])

    direct = await client.get(
        f"/api/spaces/{slug}/items", params={"collection": parent}
    )
    assert [i["id"] for i in direct.json()["items"]] == [own]

    subs = await client.get(
        f"/api/spaces/{slug}/items",
        params={"collection": parent, "collection_scope": "subcollections"},
    )
    assert sorted(i["id"] for i in subs.json()["items"]) == sorted([mid, deep])


async def test_subcollection_scope_does_not_repeat_an_item(
    client: AsyncClient,
) -> None:
    """Filed in two subcollections, and also in the parent.

    Without EXISTS the join would return it once per subcollection, and
    without the parent exclusion it would appear in both listings.
    """
    slug = await _login(client)
    parent = await _sub(client, slug, "Parent")
    a = await _sub(client, slug, "A", parent)
    b = await _sub(client, slug, "B", parent)

    both = await _doc(client, slug, "In both", [a, b])
    everywhere = await _doc(client, slug, "In parent too", [parent, a])

    subs = await client.get(
        f"/api/spaces/{slug}/items",
        params={"collection": parent, "collection_scope": "subcollections"},
    )
    ids = [i["id"] for i in subs.json()["items"]]
    assert ids == [both]
    assert subs.json()["total"] == 1
    assert everywhere not in ids


async def test_subcollection_scope_is_empty_without_children(
    client: AsyncClient,
) -> None:
    slug = await _login(client)
    leaf = await _sub(client, slug, "Leaf")
    await _doc(client, slug, "Only doc", [leaf])
    r = await client.get(
        f"/api/spaces/{slug}/items",
        params={"collection": leaf, "collection_scope": "subcollections"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["items"] == []
