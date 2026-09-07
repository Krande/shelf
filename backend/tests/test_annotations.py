"""Annotation lifecycle + cross-user isolation."""

import pytest
from httpx import AsyncClient
from obstore.store import MemoryStore

from shelf.services import storage


@pytest.fixture(autouse=True)
def memory_store() -> None:
    storage._store = MemoryStore()
    yield
    storage.reset_store()


async def _login(client: AsyncClient, email: str = "alice@example.com") -> str:
    r = await client.post("/auth/dev-login", json={"email": email})
    assert r.status_code == 200, r.text
    me = await client.get("/api/me")
    return f"u-{me.json()['id'].replace('-', '')[:8]}"


async def _make_attachment(
    client: AsyncClient, slug: str, monkeypatch: pytest.MonkeyPatch
) -> dict:
    async def fake_presign_upload(_key: str, **_: object) -> str:
        return "https://stub"

    monkeypatch.setattr(storage, "presign_upload", fake_presign_upload)
    item = (
        await client.post(
            f"/api/spaces/{slug}/items",
            json={"item_type": "document", "data": {"title": "T"}},
        )
    ).json()
    return (
        await client.post(
            f"/api/items/{item['id']}/attachments",
            json={"filename": "x.pdf", "content_type": "application/pdf"},
        )
    ).json()["attachment"]


async def test_create_list_update_delete(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    slug = await _login(client)
    att = await _make_attachment(client, slug, monkeypatch)

    create = await client.post(
        f"/api/attachments/{att['id']}/annotations",
        json={
            "kind": "highlight",
            "page_number": 3,
            "rects": [[10, 20, 100, 12]],
            "color": "#ffd400",
            "text": "interesting bit",
        },
    )
    assert create.status_code == 201, create.text
    body = create.json()
    assert body["kind"] == "highlight"
    assert body["page_number"] == 3
    assert body["rects"] == [[10, 20, 100, 12]]

    listing = (
        await client.get(f"/api/attachments/{att['id']}/annotations")
    ).json()
    assert [a["id"] for a in listing] == [body["id"]]

    upd = await client.patch(
        f"/api/annotations/{body['id']}",
        json={"color": "#ff0000", "text": "still interesting"},
    )
    assert upd.status_code == 200
    assert upd.json()["color"] == "#ff0000"
    assert upd.json()["text"] == "still interesting"

    r = await client.delete(f"/api/annotations/{body['id']}")
    assert r.status_code == 204
    listing = (
        await client.get(f"/api/attachments/{att['id']}/annotations")
    ).json()
    assert listing == []


async def test_create_rejects_empty_or_invalid_rects(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    slug = await _login(client)
    att = await _make_attachment(client, slug, monkeypatch)

    r = await client.post(
        f"/api/attachments/{att['id']}/annotations",
        json={"kind": "highlight", "page_number": 1, "rects": []},
    )
    assert r.status_code == 400

    r = await client.post(
        f"/api/attachments/{att['id']}/annotations",
        json={"kind": "highlight", "page_number": 1, "rects": [[1, 2, 3]]},
    )
    assert r.status_code == 400

    r = await client.post(
        f"/api/attachments/{att['id']}/annotations",
        json={"kind": "highlight", "page_number": 0, "rects": [[1, 2, 3, 4]]},
    )
    assert r.status_code == 400


async def test_other_user_cannot_see_annotations(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    alice_slug = await _login(client, "alice@example.com")
    att = await _make_attachment(client, alice_slug, monkeypatch)
    ann = (
        await client.post(
            f"/api/attachments/{att['id']}/annotations",
            json={"kind": "note", "page_number": 1, "rects": [[5, 5, 1, 1]]},
        )
    ).json()

    await client.post("/auth/logout")
    await _login(client, "mallory@example.com")

    r = await client.get(f"/api/attachments/{att['id']}/annotations")
    assert r.status_code == 404
    r = await client.patch(
        f"/api/annotations/{ann['id']}", json={"color": "#000000"}
    )
    assert r.status_code == 404
    r = await client.delete(f"/api/annotations/{ann['id']}")
    assert r.status_code == 404


async def test_annotations_cascade_on_attachment_delete(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    slug = await _login(client)
    att = await _make_attachment(client, slug, monkeypatch)
    await client.post(
        f"/api/attachments/{att['id']}/annotations",
        json={"kind": "note", "page_number": 1, "rects": [[1, 1, 1, 1]]},
    )

    # Deleting the attachment cascades the annotation row.
    r = await client.delete(f"/api/attachments/{att['id']}")
    assert r.status_code == 204

    # The attachment 404s; reaching for any annotation under it 404s
    # via the same auth path.
    r = await client.get(f"/api/attachments/{att['id']}/annotations")
    assert r.status_code == 404
