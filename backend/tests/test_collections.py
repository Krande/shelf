"""Integration tests for collections + item membership."""

from httpx import AsyncClient


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
