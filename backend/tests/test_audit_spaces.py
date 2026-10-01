"""Audit hooks for spaces, sharing, collections and tags.

Each test does a few things through the normal routes, then reads the
admin audit listing to check what was written: the right action, target,
label, space and details — and nothing for no-ops or refused requests.
"""

from typing import Any

import pytest
from httpx import AsyncClient

from shelf.config import settings

from .helpers import login

ADMIN = "admin@example.com"


async def _events(client: AsyncClient, **params: str) -> list[dict[str, Any]]:
    """Audit events, newest first. The client must be the admin."""
    r = await client.get("/api/admin/audit", params=params)
    assert r.status_code == 200, r.text
    events = r.json()["events"]
    assert isinstance(events, list)
    return events


@pytest.fixture
async def lab(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> dict[str, str]:
    """An admin owning a shared space "Lab"; the client is left as them."""
    monkeypatch.setattr(settings, "admin_emails", [ADMIN])
    admin_id = await login(client, ADMIN)
    r = await client.post("/api/spaces", json={"name": "Lab", "slug": "lab"})
    assert r.status_code == 201, r.text
    return {"admin_id": admin_id, "slug": "lab", "space_id": r.json()["id"]}


async def _item(client: AsyncClient, slug: str, title: str | None) -> str:
    data = {"title": title} if title is not None else {}
    r = await client.post(
        f"/api/spaces/{slug}/items", json={"item_type": "document", "data": data}
    )
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


# ── Spaces ───────────────────────────────────────────────────────────────────


async def test_space_create_and_rename(
    client: AsyncClient, lab: dict[str, str]
) -> None:
    created = await _events(client, action="space.create")
    assert len(created) == 1
    e = created[0]
    assert e["actor_id"] == lab["admin_id"]
    assert e["space_id"] == lab["space_id"]
    assert e["target_type"] == "space"
    assert e["target_id"] == lab["space_id"]
    assert e["target_label"] == "Lab"
    assert e["details"] == {"slug": "lab"}

    r = await client.patch("/api/spaces/lab", json={"name": "Lab 2", "slug": "lab2"})
    assert r.status_code == 200, r.text
    # Same values again: no entry.
    await client.patch("/api/spaces/lab2", json={"name": "Lab 2", "slug": "lab2"})
    # Refused (bad slug): no entry.
    r = await client.patch("/api/spaces/lab2", json={"slug": "u-nope"})
    assert r.status_code == 400

    updates = await _events(client, action="space.update")
    assert len(updates) == 1
    assert updates[0]["target_label"] == "Lab 2"
    assert updates[0]["details"] == {
        "name": ["Lab", "Lab 2"],
        "slug": ["lab", "lab2"],
    }


async def test_space_profile_update(
    client: AsyncClient, lab: dict[str, str]
) -> None:
    r = await client.patch(
        "/api/spaces/lab/profile",
        json={"description": "Test rigs", "columns": ["title", "updated"]},
    )
    assert r.status_code == 200, r.text
    # Unchanged: no entry.
    await client.patch("/api/spaces/lab/profile", json={"description": "Test rigs"})
    await client.patch("/api/spaces/lab/profile", json={"columns": ["title"]})

    updates = await _events(client, action="space.update")
    assert [u["details"] for u in updates] == [
        {"fields": ["columns"]},
        {"fields": ["description", "columns"]},
    ]
    assert updates[0]["space_id"] == lab["space_id"]
    assert updates[0]["target_label"] == "Lab"


async def test_space_settings(client: AsyncClient, lab: dict[str, str]) -> None:
    await client.patch("/api/spaces/lab/settings", json={"subscribable": True})
    await client.patch("/api/spaces/lab/settings", json={"subscribable": True})
    updates = await _events(client, action="space.update")
    assert len(updates) == 1
    assert updates[0]["details"] == {"subscribable": [False, True]}
    assert updates[0]["target_id"] == lab["space_id"]


# ── Subscriptions ────────────────────────────────────────────────────────────


async def test_subscribe_unsubscribe_and_remove_subscriber(
    client: AsyncClient, lab: dict[str, str]
) -> None:
    await client.patch("/api/spaces/lab/settings", json={"subscribable": True})
    r = await client.post("/api/spaces", json={"name": "Project", "slug": "proj"})
    project_id = r.json()["id"]

    # Refused: a space cannot inherit itself.
    r = await client.post("/api/spaces/lab/inherits", json={"parent_slug": "lab"})
    assert r.status_code == 400

    r = await client.post("/api/spaces/proj/inherits", json={"parent_slug": "lab"})
    assert r.status_code == 201, r.text
    # Refused: already subscribed.
    r = await client.post("/api/spaces/proj/inherits", json={"parent_slug": "lab"})
    assert r.status_code == 409
    r = await client.delete("/api/spaces/proj/inherits/lab")
    assert r.status_code == 204
    # Refused: nothing left to drop.
    r = await client.delete("/api/spaces/proj/inherits/lab")
    assert r.status_code == 404

    await client.post("/api/spaces/proj/inherits", json={"parent_slug": "lab"})
    r = await client.delete("/api/spaces/lab/subscribers/proj")
    assert r.status_code == 204

    events = [
        e
        for e in await _events(client, action="space.")
        if e["action"] not in ("space.create", "space.update")
    ]
    assert [e["action"] for e in events] == [
        "space.subscriber.remove",
        "space.subscribe",
        "space.unsubscribe",
        "space.subscribe",
    ]
    for e in events:
        assert e["space_id"] == project_id
        assert e["target_type"] == "space"
        assert e["target_id"] == project_id
        assert e["target_label"] == "Project"
        assert e["details"] == {
            "source_space": "Lab",
            "source_space_id": lab["space_id"],
        }


# ── Members ──────────────────────────────────────────────────────────────────


async def test_member_add_role_remove(
    client: AsyncClient, lab: dict[str, str]
) -> None:
    bob_id = await login(client, "bob@example.com", link=True)
    await login(client, ADMIN)

    r = await client.post(
        "/api/spaces/lab/members", json={"email": "bob@example.com"}
    )
    assert r.status_code == 201, r.text
    # Refused: already a member, and an unknown address.
    r = await client.post(
        "/api/spaces/lab/members", json={"email": "bob@example.com"}
    )
    assert r.status_code == 409
    r = await client.post(
        "/api/spaces/lab/members", json={"email": "ghost@example.com"}
    )
    assert r.status_code == 404

    await client.patch(f"/api/spaces/lab/members/{bob_id}", json={"role": "editor"})
    # Same role again: no entry.
    await client.patch(f"/api/spaces/lab/members/{bob_id}", json={"role": "editor"})
    r = await client.delete(f"/api/spaces/lab/members/{bob_id}")
    assert r.status_code == 204

    events = await _events(client, action="space.member.")
    assert [e["action"] for e in events] == [
        "space.member.remove",
        "space.member.role",
        "space.member.add",
    ]
    removed, role, added = events
    assert added["details"] == {"role": "viewer"}
    assert role["details"] == {"role": ["viewer", "editor"]}
    assert removed["details"] == {"role": "editor"}
    for e in events:
        assert e["space_id"] == lab["space_id"]
        assert e["target_type"] == "user"
        assert e["target_id"] == bob_id
        assert e["target_label"] == "bob@example.com"


async def test_member_changes_by_non_owner_are_not_logged(
    client: AsyncClient, lab: dict[str, str]
) -> None:
    await login(client, "eve@example.com", link=True)
    r = await client.post(
        "/api/spaces/lab/members", json={"email": "eve@example.com"}
    )
    assert r.status_code == 404
    await login(client, ADMIN)
    assert await _events(client, action="space.member.") == []


# ── Collections ──────────────────────────────────────────────────────────────


async def test_collection_lifecycle(client: AsyncClient, lab: dict[str, str]) -> None:
    a = (
        await client.post("/api/spaces/lab/collections", json={"name": "Rigs"})
    ).json()
    b = (
        await client.post("/api/spaces/lab/collections", json={"name": "Logs"})
    ).json()
    child = (
        await client.post(
            "/api/spaces/lab/collections",
            json={"name": "Old", "parent_id": a["id"]},
        )
    ).json()
    # Refused: empty name.
    r = await client.post("/api/spaces/lab/collections", json={"name": "  "})
    assert r.status_code == 400

    created = await _events(client, action="collection.create")
    assert [e["target_label"] for e in created] == ["Old", "Logs", "Rigs"]
    assert created[0]["details"] == {"parent": "Rigs"}
    assert created[2]["details"] is None
    assert created[2]["target_type"] == "collection"
    assert created[2]["target_id"] == a["id"]
    assert created[2]["space_id"] == lab["space_id"]

    # Rename + description.
    await client.patch(
        f"/api/collections/{a['id']}",
        json={"name": "Test rigs", "description": "Long text"},
    )
    # Pure reorder.
    await client.patch(f"/api/collections/{b['id']}", json={"position": 0})
    # Move to root.
    await client.patch(f"/api/collections/{child['id']}", json={"parent_id": None})
    # No-ops: same name, same position, same parent.
    await client.patch(f"/api/collections/{a['id']}", json={"name": "Test rigs"})
    await client.patch(f"/api/collections/{b['id']}", json={"position": 0})
    await client.patch(f"/api/collections/{child['id']}", json={"parent_id": None})

    updates = await _events(client, action="collection.update")
    assert [(e["target_label"], e["details"]) for e in updates] == [
        ("Old", {"parent": ["Test rigs", None]}),
        ("Logs", {"fields": ["position"]}),
        ("Test rigs", {"name": ["Rigs", "Test rigs"], "fields": ["description"]}),
    ]

    r = await client.delete(f"/api/collections/{b['id']}")
    assert r.status_code == 204
    deleted = await _events(client, action="collection.delete")
    assert len(deleted) == 1
    assert deleted[0]["target_id"] == b["id"]
    assert deleted[0]["target_label"] == "Logs"
    assert deleted[0]["space_id"] == lab["space_id"]


async def test_item_collections(client: AsyncClient, lab: dict[str, str]) -> None:
    item_id = await _item(client, "lab", "Rig manual")
    untitled = await _item(client, "lab", None)
    a = (await client.post("/api/spaces/lab/collections", json={"name": "A"})).json()
    b = (await client.post("/api/spaces/lab/collections", json={"name": "B"})).json()

    url = f"/api/items/{item_id}/collections"
    await client.put(url, json={"collection_ids": [a["id"]]})
    await client.put(url, json={"collection_ids": [b["id"]]})
    # Same set: no entry.
    await client.put(url, json={"collection_ids": [b["id"]]})
    await client.put(
        f"/api/items/{untitled}/collections", json={"collection_ids": [a["id"]]}
    )

    events = await _events(client, action="item.collections")
    assert [(e["target_label"], e["details"]) for e in events] == [
        ("(untitled)", {"added": ["A"], "removed": []}),
        ("Rig manual", {"added": ["B"], "removed": ["A"]}),
        ("Rig manual", {"added": ["A"], "removed": []}),
    ]
    assert events[1]["target_type"] == "item"
    assert events[1]["target_id"] == item_id
    assert events[1]["space_id"] == lab["space_id"]


# ── Tags ─────────────────────────────────────────────────────────────────────


async def test_tag_lifecycle(client: AsyncClient, lab: dict[str, str]) -> None:
    tag = (
        await client.post("/api/spaces/lab/tags", json={"name": "ML"})
    ).json()
    # Refused: duplicate name.
    r = await client.post("/api/spaces/lab/tags", json={"name": "ml"})
    assert r.status_code == 409

    await client.patch(
        f"/api/tags/{tag['id']}", json={"name": "Machine learning", "color": "#fff"}
    )
    # No-op.
    await client.patch(f"/api/tags/{tag['id']}", json={"name": "Machine learning"})
    r = await client.delete(f"/api/tags/{tag['id']}")
    assert r.status_code == 204

    events = await _events(client, action="tag.")
    assert [e["action"] for e in events] == ["tag.delete", "tag.update", "tag.create"]
    deleted, updated, created = events
    assert created["target_label"] == "ML"
    assert updated["details"] == {
        "name": ["ML", "Machine learning"],
        "color": [None, "#fff"],
    }
    assert deleted["target_label"] == "Machine learning"
    for e in events:
        assert e["target_type"] == "tag"
        assert e["target_id"] == tag["id"]
        assert e["space_id"] == lab["space_id"]


async def test_item_tags(client: AsyncClient, lab: dict[str, str]) -> None:
    item_id = await _item(client, "lab", "Rig manual")
    x = (await client.post("/api/spaces/lab/tags", json={"name": "x"})).json()
    y = (await client.post("/api/spaces/lab/tags", json={"name": "y"})).json()

    url = f"/api/items/{item_id}/tags"
    await client.put(url, json={"tag_ids": [x["id"], y["id"]]})
    await client.put(url, json={"tag_ids": [y["id"], x["id"]]})  # no-op
    await client.put(url, json={"tag_ids": []})

    events = await _events(client, action="item.tags")
    assert [e["details"] for e in events] == [
        {"added": [], "removed": ["x", "y"]},
        {"added": ["x", "y"], "removed": []},
    ]
    assert events[0]["target_id"] == item_id
    assert events[0]["target_label"] == "Rig manual"
    assert events[0]["space_id"] == lab["space_id"]


async def test_refused_tag_write_is_not_logged(
    client: AsyncClient, lab: dict[str, str]
) -> None:
    tag = (await client.post("/api/spaces/lab/tags", json={"name": "x"})).json()
    await login(client, "eve@example.com", link=True)
    r = await client.delete(f"/api/tags/{tag['id']}")
    assert r.status_code == 404
    await login(client, ADMIN)
    assert await _events(client, action="tag.delete") == []
