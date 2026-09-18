"""Copying an item into another space."""

import pytest
from httpx import AsyncClient

from shelf.config import settings

from .helpers import login


async def _personal_slug(client: AsyncClient) -> str:
    return str((await client.get("/api/me/spaces")).json()[0]["slug"])


@pytest.fixture
async def two_spaces(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> tuple[str, str, str]:
    """An admin owning "source" and "target", with a tagged item in
    source. Returns (source_slug, target_slug, item_id)."""
    monkeypatch.setattr(settings, "admin_emails", ["admin@example.com"])
    await login(client, "admin@example.com")
    source = str(
        (
            await client.post("/api/spaces", json={"name": "Source", "slug": "source"})
        ).json()["slug"]
    )
    target = str(
        (
            await client.post("/api/spaces", json={"name": "Target", "slug": "target"})
        ).json()["slug"]
    )
    item_id = str(
        (
            await client.post(
                f"/api/spaces/{source}/items",
                json={
                    "item_type": "standard",
                    "data": {"title": "ACME-RP-7", "extra": "fatigue"},
                },
            )
        ).json()["id"]
    )
    tag = await client.post(
        f"/api/spaces/{source}/tags", json={"name": "fatigue", "color": "#abc"}
    )
    assert tag.status_code == 201, tag.text
    linked = await client.put(
        f"/api/items/{item_id}/tags", json={"tag_ids": [tag.json()["id"]]}
    )
    assert linked.status_code == 200, linked.text
    return source, target, item_id


async def test_copy_lands_in_the_target(
    client: AsyncClient, two_spaces: tuple[str, str, str]
) -> None:
    _source, target, item_id = two_spaces
    r = await client.post(
        f"/api/items/{item_id}/copy", json={"target_slug": target}
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["space_slug"] == target

    listed = (await client.get(f"/api/spaces/{target}/items")).json()
    assert [i["data"]["title"] for i in listed["items"]] == ["ACME-RP-7"]
    assert listed["items"][0]["id"] == body["item_id"]
    assert listed["items"][0]["is_inherited"] is False


async def test_the_original_stays_put(
    client: AsyncClient, two_spaces: tuple[str, str, str]
) -> None:
    source, target, item_id = two_spaces
    await client.post(f"/api/items/{item_id}/copy", json={"target_slug": target})
    listed = (await client.get(f"/api/spaces/{source}/items")).json()
    assert [i["id"] for i in listed["items"]] == [item_id]


async def test_the_copy_is_independent(
    client: AsyncClient, two_spaces: tuple[str, str, str]
) -> None:
    _source, target, item_id = two_spaces
    copy_id = (
        await client.post(
            f"/api/items/{item_id}/copy", json={"target_slug": target}
        )
    ).json()["item_id"]

    await client.patch(
        f"/api/items/{copy_id}", json={"data": {"title": "Edited copy"}}
    )
    original = (await client.get(f"/api/items/{item_id}")).json()
    assert original["data"]["title"] == "ACME-RP-7"


async def test_tags_come_across_by_name(
    client: AsyncClient, two_spaces: tuple[str, str, str]
) -> None:
    """Tags are space-scoped rows, so "the same tag" means the name."""
    _source, target, item_id = two_spaces
    copy_id = (
        await client.post(
            f"/api/items/{item_id}/copy", json={"target_slug": target}
        )
    ).json()["item_id"]

    target_tags = (await client.get(f"/api/spaces/{target}/tags")).json()
    assert [t["name"] for t in target_tags] == ["fatigue"]
    copy = (await client.get(f"/api/items/{copy_id}")).json()
    assert copy["tag_ids"] == [target_tags[0]["id"]]


async def test_an_existing_tag_in_the_target_is_reused(
    client: AsyncClient, two_spaces: tuple[str, str, str]
) -> None:
    _source, target, item_id = two_spaces
    existing = await client.post(
        f"/api/spaces/{target}/tags", json={"name": "Fatigue"}
    )
    assert existing.status_code == 201, existing.text

    await client.post(f"/api/items/{item_id}/copy", json={"target_slug": target})
    target_tags = (await client.get(f"/api/spaces/{target}/tags")).json()
    # Case-insensitive match — one tag, not two.
    assert len(target_tags) == 1


async def test_the_copy_joins_the_same_standard(
    client: AsyncClient, two_spaces: tuple[str, str, str]
) -> None:
    """Otherwise it looks like an unrelated PDF with a similar name."""
    _source, target, item_id = two_spaces
    await client.put(
        f"/api/items/{item_id}/revision",
        json={
            "body": "ACME",
            "designation": "ACME-RP-7",
            "label": "2019",
            "issued_on": "2019-09-01",
        },
    )
    result = (
        await client.post(
            f"/api/items/{item_id}/copy", json={"target_slug": target}
        )
    ).json()
    assert result["linked_to_standard"] is True

    revisions = (await client.get(f"/api/items/{item_id}/revisions")).json()
    assert {r["item_id"] for r in revisions["revisions"]} == {
        item_id,
        result["item_id"],
    }


async def test_notes_do_not_come_across(
    client: AsyncClient, two_spaces: tuple[str, str, str]
) -> None:
    """Notes belong to whoever wrote them, under the visibility they
    chose; republishing them into another space is a disclosure."""
    _source, target, item_id = two_spaces
    await client.post(
        f"/api/items/{item_id}/notes", json={"content_html": "<p>private-ish</p>"}
    )
    copy_id = (
        await client.post(
            f"/api/items/{item_id}/copy", json={"target_slug": target}
        )
    ).json()["item_id"]
    assert (await client.get(f"/api/items/{copy_id}/notes")).json() == []


async def test_collections_do_not_come_across(
    client: AsyncClient, two_spaces: tuple[str, str, str]
) -> None:
    source, target, item_id = two_spaces
    coll = await client.post(
        f"/api/spaces/{source}/collections", json={"name": "Structural"}
    )
    await client.put(
        f"/api/items/{item_id}/collections",
        json={"collection_ids": [coll.json()["id"]]},
    )
    copy_id = (
        await client.post(
            f"/api/items/{item_id}/copy", json={"target_slug": target}
        )
    ).json()["item_id"]
    copy = (await client.get(f"/api/items/{copy_id}")).json()
    assert copy["collection_ids"] == []


async def test_copying_into_the_same_space_is_refused(
    client: AsyncClient, two_spaces: tuple[str, str, str]
) -> None:
    source, _target, item_id = two_spaces
    r = await client.post(
        f"/api/items/{item_id}/copy", json={"target_slug": source}
    )
    assert r.status_code == 400


async def test_copying_needs_editor_on_the_target(
    client: AsyncClient, two_spaces: tuple[str, str, str]
) -> None:
    source, target, item_id = two_spaces
    admin_id = (await client.get("/api/me")).json()["id"]
    viewer_id = await login(client, "viewer@example.com", link=True)
    await client.post("/auth/switch", json={"user_id": admin_id})
    for slug, role in ((source, "viewer"), (target, "viewer")):
        await client.post(
            f"/api/spaces/{slug}/members",
            json={"email": "viewer@example.com", "role": role},
        )
    await client.post("/auth/switch", json={"user_id": viewer_id})

    r = await client.post(
        f"/api/items/{item_id}/copy", json={"target_slug": target}
    )
    assert r.status_code == 403


async def test_an_inherited_item_can_be_copied_out(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Read access is all copying needs — including read via a
    subscription."""
    monkeypatch.setattr(settings, "admin_emails", ["admin@example.com"])
    await login(client, "admin@example.com")
    std = str(
        (
            await client.post(
                "/api/spaces", json={"name": "Standards", "slug": "standards"}
            )
        ).json()["slug"]
    )
    item_id = str(
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

    await login(client, "eng@example.com", link=True)
    personal = await _personal_slug(client)
    await client.post(
        f"/api/spaces/{personal}/inherits", json={"parent_slug": std}
    )

    r = await client.post(
        f"/api/items/{item_id}/copy", json={"target_slug": personal}
    )
    assert r.status_code == 201, r.text
    # And the copy is theirs to edit, unlike the original.
    copy_id = r.json()["item_id"]
    assert (
        await client.patch(f"/api/items/{copy_id}", json={"data": {"title": "Mine"}})
    ).status_code == 200


async def test_copying_something_you_cannot_read_is_a_404(
    client: AsyncClient, two_spaces: tuple[str, str, str]
) -> None:
    _source, _target, item_id = two_spaces
    await login(client, "stranger@example.com", link=True)
    personal = await _personal_slug(client)
    r = await client.post(
        f"/api/items/{item_id}/copy", json={"target_slug": personal}
    )
    assert r.status_code == 404


async def _collection(client: AsyncClient, slug: str, name: str) -> str:
    r = await client.post(f"/api/spaces/{slug}/collections", json={"name": name})
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def test_copy_files_the_copy_under_a_target_collection(
    client: AsyncClient, two_spaces: tuple[str, str, str]
) -> None:
    """The destination folder is chosen on the target's own tree.

    Copying does not translate the source's collections, so without this
    the copy arrives unfiled and has to be found and filed by hand.
    """
    _source, target, item_id = two_spaces
    dest = await _collection(client, target, "Incoming")

    r = await client.post(
        f"/api/items/{item_id}/copy",
        json={"target_slug": target, "target_collection_id": dest},
    )
    assert r.status_code == 201, r.text
    assert r.json()["collection_id"] == dest

    listed = await client.get(
        f"/api/spaces/{target}/items", params={"collection": dest}
    )
    assert [i["id"] for i in listed.json()["items"]] == [r.json()["item_id"]]


async def test_copy_without_a_collection_lands_unfiled(
    client: AsyncClient, two_spaces: tuple[str, str, str]
) -> None:
    """Omitting it keeps the behaviour copies had before it existed."""
    _source, target, item_id = two_spaces
    r = await client.post(
        f"/api/items/{item_id}/copy", json={"target_slug": target}
    )
    assert r.status_code == 201, r.text
    assert r.json()["collection_id"] is None

    unfiled = await client.get(
        f"/api/spaces/{target}/items", params={"collection": "unfiled"}
    )
    assert r.json()["item_id"] in [i["id"] for i in unfiled.json()["items"]]


async def test_copy_rejects_a_collection_from_the_source_space(
    client: AsyncClient, two_spaces: tuple[str, str, str]
) -> None:
    """A folder id only means something in the space that owns it."""
    source, target, item_id = two_spaces
    theirs = await _collection(client, source, "Source side")

    r = await client.post(
        f"/api/items/{item_id}/copy",
        json={"target_slug": target, "target_collection_id": theirs},
    )
    assert r.status_code == 404, r.text


async def test_a_refused_collection_copies_nothing(
    client: AsyncClient, two_spaces: tuple[str, str, str]
) -> None:
    """The 404 aborts the copy rather than leaving it unfiled."""
    source, target, item_id = two_spaces
    theirs = await _collection(client, source, "Source side")

    before = len((await client.get(f"/api/spaces/{target}/items")).json()["items"])
    await client.post(
        f"/api/items/{item_id}/copy",
        json={"target_slug": target, "target_collection_id": theirs},
    )
    after = len((await client.get(f"/api/spaces/{target}/items")).json()["items"])
    assert after == before
