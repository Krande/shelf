"""Integration tests for tags + item membership."""

from typing import Any

from httpx import AsyncClient


def _items(payload: Any) -> list[Any]:
    """Unwrap the paginated ``{items, total}`` list response."""
    return payload["items"]


async def _login(client: AsyncClient, email: str = "alice@example.com") -> str:
    r = await client.post("/auth/dev-login", json={"email": email})
    assert r.status_code == 200, r.text
    me = await client.get("/api/me")
    user_id = me.json()["id"]
    return f"u-{user_id.replace('-', '')[:8]}"


async def test_create_and_list_tags(client: AsyncClient) -> None:
    slug = await _login(client)
    r = await client.post(
        f"/api/spaces/{slug}/tags",
        json={"name": "ML", "color": "#60a5fa"},
    )
    assert r.status_code == 201, r.text
    tag = r.json()
    assert tag["name"] == "ML"
    assert tag["color"] == "#60a5fa"

    listing = await client.get(f"/api/spaces/{slug}/tags")
    assert listing.status_code == 200
    rows = listing.json()
    assert [t["id"] for t in rows] == [tag["id"]]


async def test_create_tag_name_unique_case_insensitive(client: AsyncClient) -> None:
    slug = await _login(client)
    r = await client.post(f"/api/spaces/{slug}/tags", json={"name": "Foo"})
    assert r.status_code == 201
    # CITEXT collation makes "foo" collide with "Foo".
    r = await client.post(f"/api/spaces/{slug}/tags", json={"name": "foo"})
    assert r.status_code == 409


async def test_list_tags_prefix_filter(client: AsyncClient) -> None:
    slug = await _login(client)
    for name in ("alpha", "beta", "amazing"):
        await client.post(f"/api/spaces/{slug}/tags", json={"name": name})
    rows = (
        await client.get(f"/api/spaces/{slug}/tags", params={"q": "a"})
    ).json()
    assert sorted(t["name"] for t in rows) == ["alpha", "amazing"]


async def test_update_and_delete_tag(client: AsyncClient) -> None:
    slug = await _login(client)
    tag = (
        await client.post(f"/api/spaces/{slug}/tags", json={"name": "ML"})
    ).json()

    patched = (
        await client.patch(
            f"/api/tags/{tag['id']}", json={"name": "Machine Learning"}
        )
    ).json()
    assert patched["name"] == "Machine Learning"

    r = await client.delete(f"/api/tags/{tag['id']}")
    assert r.status_code == 204
    # Subsequent fetch via list — should be empty.
    rows = (await client.get(f"/api/spaces/{slug}/tags")).json()
    assert rows == []


async def test_item_tag_membership_round_trip(client: AsyncClient) -> None:
    slug = await _login(client)
    tag = (
        await client.post(f"/api/spaces/{slug}/tags", json={"name": "ML"})
    ).json()
    item = (
        await client.post(
            f"/api/spaces/{slug}/items",
            json={"item_type": "document", "data": {"title": "paper"}},
        )
    ).json()
    assert item["tag_ids"] == []

    r = await client.put(
        f"/api/items/{item['id']}/tags", json={"tag_ids": [tag["id"]]}
    )
    assert r.status_code == 200
    assert r.json() == [tag["id"]]

    fetched = (await client.get(f"/api/items/{item['id']}")).json()
    assert fetched["tag_ids"] == [tag["id"]]

    # ?tag=name filter joins through the normalised tables.
    listing = (
        await client.get(f"/api/spaces/{slug}/items", params={"tag": "ML"})
    ).json()
    assert [r["id"] for r in _items(listing)] == [item["id"]]

    # Replace with [] strips tags from the item.
    await client.put(f"/api/items/{item['id']}/tags", json={"tag_ids": []})
    refetched = (await client.get(f"/api/items/{item['id']}")).json()
    assert refetched["tag_ids"] == []


async def test_delete_tag_unlinks_items_only(client: AsyncClient) -> None:
    slug = await _login(client)
    tag = (
        await client.post(f"/api/spaces/{slug}/tags", json={"name": "ML"})
    ).json()
    item = (
        await client.post(
            f"/api/spaces/{slug}/items",
            json={"item_type": "document", "data": {"title": "paper"}},
        )
    ).json()
    await client.put(
        f"/api/items/{item['id']}/tags", json={"tag_ids": [tag["id"]]}
    )
    await client.delete(f"/api/tags/{tag['id']}")

    refetched = (await client.get(f"/api/items/{item['id']}")).json()
    assert refetched["tag_ids"] == []


async def test_tag_endpoints_isolate_users(client: AsyncClient) -> None:
    a_slug = await _login(client, email="alice@example.com")
    a_tag = (
        await client.post(f"/api/spaces/{a_slug}/tags", json={"name": "Mine"})
    ).json()
    # Bob doesn't see Alice's tag and can't fetch it directly.
    b_slug = await _login(client, email="bob@example.com")
    rows = (await client.get(f"/api/spaces/{b_slug}/tags")).json()
    assert rows == []
    r = await client.patch(
        f"/api/tags/{a_tag['id']}", json={"name": "Bob's"}
    )
    assert r.status_code == 404
    r = await client.delete(f"/api/tags/{a_tag['id']}")
    assert r.status_code == 404


async def test_set_item_tags_rejects_cross_space(client: AsyncClient) -> None:
    a_slug = await _login(client, email="alice@example.com")
    a_tag = (
        await client.post(f"/api/spaces/{a_slug}/tags", json={"name": "X"})
    ).json()
    # Bob's item shouldn't accept Alice's tag.
    b_slug = await _login(client, email="bob@example.com")
    b_item = (
        await client.post(
            f"/api/spaces/{b_slug}/items",
            json={"item_type": "document", "data": {"title": "p"}},
        )
    ).json()
    r = await client.put(
        f"/api/items/{b_item['id']}/tags", json={"tag_ids": [a_tag["id"]]}
    )
    # Returns 404 because resolve-tag treats it as not-found for Bob.
    assert r.status_code == 404


async def test_list_items_filter_by_multiple_tags_is_and(client: AsyncClient) -> None:
    slug = await _login(client)
    t1 = (
        await client.post(f"/api/spaces/{slug}/tags", json={"name": "alpha"})
    ).json()
    t2 = (
        await client.post(f"/api/spaces/{slug}/tags", json={"name": "beta"})
    ).json()
    only_alpha = (
        await client.post(
            f"/api/spaces/{slug}/items",
            json={"item_type": "document", "data": {"title": "a"}},
        )
    ).json()
    both = (
        await client.post(
            f"/api/spaces/{slug}/items",
            json={"item_type": "document", "data": {"title": "ab"}},
        )
    ).json()
    await client.put(
        f"/api/items/{only_alpha['id']}/tags", json={"tag_ids": [t1["id"]]}
    )
    await client.put(
        f"/api/items/{both['id']}/tags", json={"tag_ids": [t1["id"], t2["id"]]}
    )

    rows = (
        await client.get(
            f"/api/spaces/{slug}/items", params=[("tag", "alpha"), ("tag", "beta")]
        )
    ).json()
    # AND across tag params: only the item that has both surfaces.
    assert [r["id"] for r in _items(rows)] == [both["id"]]
