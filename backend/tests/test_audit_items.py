"""Audit hooks on items, copies, attachments and exports.

Each test acts as an admin (so it can read `/api/admin/audit` back) in a
space of its own, then asserts on exactly what was logged.
"""

from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from httpx import AsyncClient
from obstore.store import MemoryStore

from shelf.api import attachments as attachments_api
from shelf.config import settings
from shelf.services import storage

from .helpers import login

ADMIN = "admin@example.com"


@pytest.fixture(autouse=True)
def memory_store(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    storage._store = MemoryStore()

    async def fake_presign(key: str, **_: object) -> str:
        return f"https://memory/{key}?sig=stub"

    async def fake_read_object(key: str) -> bytes:
        return f"bytes-of-{key}".encode()

    monkeypatch.setattr(storage, "presign_upload", fake_presign)
    monkeypatch.setattr(storage, "presign_download", fake_presign)
    monkeypatch.setattr(storage, "presign_download_internal", fake_presign)
    monkeypatch.setattr(storage, "read_object", fake_read_object)
    yield
    storage.reset_store()


async def _admin(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(settings, "admin_emails", [ADMIN])
    return await login(client, ADMIN, display_name="Ada Admin")


async def _space(client: AsyncClient, slug: str) -> str:
    r = await client.post("/api/spaces", json={"name": slug.title(), "slug": slug})
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def _item(client: AsyncClient, slug: str, title: str | None = "Paper") -> Any:
    data = {"title": title} if title is not None else {}
    r = await client.post(
        f"/api/spaces/{slug}/items", json={"item_type": "document", "data": data}
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _attachment(
    client: AsyncClient, item_id: str, filename: str = "paper.pdf", *, complete: bool = True
) -> Any:
    r = await client.post(
        f"/api/items/{item_id}/attachments",
        json={"filename": filename, "content_type": "application/pdf", "size_bytes": 10},
    )
    assert r.status_code == 201, r.text
    att = r.json()["attachment"]
    if complete:
        done = await client.post(f"/api/attachments/{att['id']}/complete")
        assert done.status_code == 200, done.text
        att = done.json()
    return att


async def _events(client: AsyncClient, **params: str) -> list[dict[str, Any]]:
    r = await client.get("/api/admin/audit", params=params)
    assert r.status_code == 200, r.text
    return list(r.json()["events"])


async def test_item_lifecycle_is_logged(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    admin_id = await _admin(client, monkeypatch)
    space_id = await _space(client, "lib")
    item = await _item(client, "lib", "Bridges")

    await client.patch(
        f"/api/items/{item['id']}",
        json={"data": {"title": "Bridges", "date": "2024"}},
    )
    # Saving the same data again changes nothing and logs nothing.
    await client.patch(
        f"/api/items/{item['id']}",
        json={"data": {"title": "Bridges", "date": "2024"}},
    )
    await client.patch(
        f"/api/items/{item['id']}",
        json={"item_type": "report", "data": {"title": "Bridges II", "date": "2024"}},
    )
    assert (await client.delete(f"/api/items/{item['id']}")).status_code == 204
    assert (await client.post(f"/api/items/{item['id']}/restore")).status_code == 200
    await client.delete(f"/api/items/{item['id']}")
    assert (
        await client.delete(f"/api/items/{item['id']}", params={"permanent": "true"})
    ).status_code == 204

    events = await _events(client, target_id=item["id"])
    assert [e["action"] for e in events] == [
        "item.delete",
        "item.trash",
        "item.restore",
        "item.trash",
        "item.update",
        "item.update",
        "item.create",
    ]
    for e in events:
        assert e["space_id"] == space_id
        assert e["target_type"] == "item"
        assert e["actor_id"] == admin_id
        assert e["via"] == "web"
    # The label survives the purge.
    assert events[0]["target_label"] == "Bridges II"
    assert events[-1]["target_label"] == "Bridges"
    assert events[5]["details"] == {"fields": ["date"]}
    assert events[4]["details"] == {"fields": ["item_type", "title"]}


async def test_untitled_item_label(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _admin(client, monkeypatch)
    await _space(client, "lib")
    item = await _item(client, "lib", title=None)
    [event] = await _events(client, target_id=item["id"])
    assert event["target_label"] == "(untitled)"


async def test_refused_item_writes_are_not_logged(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _admin(client, monkeypatch)
    await _space(client, "lib")
    item = await _item(client, "lib")
    att = await _attachment(client, item["id"])

    await login(client, "mallory@example.com")
    assert (await client.post("/api/spaces/lib/items", json={"item_type": "x"})).status_code == 404
    assert (
        await client.patch(f"/api/items/{item['id']}", json={"data": {"title": "x"}})
    ).status_code == 404
    assert (await client.delete(f"/api/items/{item['id']}")).status_code == 404
    assert (await client.get(f"/api/attachments/{att['id']}/download")).status_code == 404
    assert (await client.get(f"/api/attachments/{att['id']}/file")).status_code == 404
    assert (await client.delete(f"/api/attachments/{att['id']}")).status_code == 404
    assert (await client.get(f"/api/items/{item['id']}/export")).status_code == 404
    assert (await client.get("/api/spaces/lib/export")).status_code == 404
    assert (
        await client.get("/api/spaces/lib/attachments-zip", params={"item": [item["id"]]})
    ).status_code == 404

    await login(client, ADMIN)
    events = await _events(client)
    # Only the admin's own setup; nothing from the refused attempts.
    assert all(e["actor_email"] == ADMIN for e in events)
    assert [
        e["action"]
        for e in events
        if e["action"].startswith(("item.", "attachment.", "export.", "collection."))
    ] == ["attachment.upload", "item.create"]


async def test_copy_is_logged_on_the_new_item(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _admin(client, monkeypatch)
    await _space(client, "source")
    target_id = await _space(client, "target")
    item = await _item(client, "source", "Spec")

    r = await client.post(
        f"/api/items/{item['id']}/copy", json={"target_slug": "target"}
    )
    assert r.status_code == 201, r.text
    copy_id = r.json()["item_id"]

    [event] = await _events(client, action="item.copy")
    assert event["target_id"] == copy_id
    assert event["space_id"] == target_id
    assert event["target_label"] == "Spec"
    assert event["details"]["to_space"] == "target"
    assert event["details"]["from_space"] == "source"
    assert event["details"]["from_item"] == item["id"]


async def test_attachment_upload_and_delete(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _admin(client, monkeypatch)
    space_id = await _space(client, "lib")
    item = await _item(client, "lib")

    pending = await _attachment(client, item["id"], "pending.pdf", complete=False)
    # Registering alone is not an upload.
    assert await _events(client, action="attachment.") == []

    att = await _attachment(client, item["id"], "done.pdf")
    # A repeat /complete is a no-op and is not logged twice.
    await client.post(f"/api/attachments/{att['id']}/complete")
    assert (await client.delete(f"/api/attachments/{att['id']}")).status_code == 204

    events = await _events(client, action="attachment.")
    assert [e["action"] for e in events] == ["attachment.delete", "attachment.upload"]
    for e in events:
        assert e["target_type"] == "attachment"
        assert e["target_id"] == att["id"]
        assert e["target_label"] == "done.pdf"
        assert e["space_id"] == space_id
        assert e["details"]["item_id"] == item["id"]
    assert pending["id"] not in {e["target_id"] for e in events}


async def test_view_and_download_are_told_apart(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _admin(client, monkeypatch)
    space_id = await _space(client, "lib")
    item = await _item(client, "lib")
    att = await _attachment(client, item["id"])

    # /file proxies from storage; answer that GET without a network.
    real_client = httpx.AsyncClient

    class _Storage(real_client):  # type: ignore[misc,valid-type]
        def __init__(self, **kwargs: Any) -> None:
            kwargs["transport"] = httpx.MockTransport(
                lambda _req: httpx.Response(200, content=b"%PDF-1.4")
            )
            super().__init__(**kwargs)

    monkeypatch.setattr(attachments_api.httpx, "AsyncClient", _Storage)

    # The reader asks /download once per open.
    r = await client.get(f"/api/attachments/{att['id']}/download")
    assert r.status_code == 200
    # The Download button streams via /file.
    r = await client.get(f"/api/attachments/{att['id']}/file")
    assert r.status_code == 200
    assert r.content == b"%PDF-1.4"

    [view] = await _events(client, action="attachment.view")
    [download] = await _events(client, action="attachment.download")
    for e in (view, download):
        assert e["target_id"] == att["id"]
        assert e["target_label"] == "paper.pdf"
        assert e["space_id"] == space_id


async def test_failed_storage_fetch_is_not_a_download(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _admin(client, monkeypatch)
    await _space(client, "lib")
    item = await _item(client, "lib")
    att = await _attachment(client, item["id"])

    real_client = httpx.AsyncClient

    class _Broken(real_client):  # type: ignore[misc,valid-type]
        def __init__(self, **kwargs: Any) -> None:
            kwargs["transport"] = httpx.MockTransport(
                lambda _req: httpx.Response(404)
            )
            super().__init__(**kwargs)

    monkeypatch.setattr(attachments_api.httpx, "AsyncClient", _Broken)
    r = await client.get(f"/api/attachments/{att['id']}/file")
    assert r.status_code == 502
    assert await _events(client, action="attachment.download") == []


async def test_exports_are_logged(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _admin(client, monkeypatch)
    space_id = await _space(client, "lib")
    a = await _item(client, "lib", "A")
    b = await _item(client, "lib", "B")
    await _attachment(client, a["id"], "a.pdf")
    await _attachment(client, b["id"], "b.pdf")

    assert (await client.get(f"/api/items/{a['id']}/export")).status_code == 200
    [item_export] = await _events(client, action="export.item")
    assert item_export["target_id"] == a["id"]
    assert item_export["target_label"] == "A"
    assert item_export["details"] == {"format": "bibtex"}

    assert (
        await client.get("/api/spaces/lib/export", params={"format": "rdf"})
    ).status_code == 200
    [space_export] = await _events(client, action="export.space")
    assert space_export["target_type"] == "space"
    assert space_export["target_id"] == space_id
    assert space_export["target_label"] == "Lib"
    assert space_export["details"] == {"format": "rdf", "items": 2, "files": 2}

    r = await client.get(
        "/api/spaces/lib/attachments-zip", params={"item": [a["id"], b["id"]]}
    )
    assert r.status_code == 200, r.text
    [zip_event] = await _events(client, action="export.zip")
    assert zip_event["space_id"] == space_id
    assert zip_event["details"] == {"items": 2, "files": 2}

    coll = (
        await client.post("/api/spaces/lib/collections", json={"name": "Reading"})
    ).json()
    filed = await client.put(
        f"/api/items/{a['id']}/collections", json={"collection_ids": [coll["id"]]}
    )
    assert filed.status_code == 200, filed.text
    r = await client.get(
        "/api/spaces/lib/attachments-zip", params={"collection": coll["id"]}
    )
    assert r.status_code == 200, r.text
    [coll_event] = await _events(client, action="collection.download")
    assert coll_event["target_type"] == "collection"
    assert coll_event["target_id"] == coll["id"]
    assert coll_event["target_label"] == "Reading"
    assert coll_event["details"] == {"items": 1, "files": 1}


async def test_created_by_name_on_responses(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    admin_id = await _admin(client, monkeypatch)
    await _space(client, "lib")
    item = await _item(client, "lib")
    assert item["created_by"] == admin_id
    assert item["created_by_name"] == "Ada Admin"

    got = (await client.get(f"/api/items/{item['id']}")).json()
    assert got["created_by_name"] == "Ada Admin"
    listing = (await client.get("/api/spaces/lib/items")).json()["items"]
    assert [i["created_by_name"] for i in listing] == ["Ada Admin"]
    patched = (
        await client.patch(f"/api/items/{item['id']}", json={"data": {"title": "x"}})
    ).json()
    assert patched["created_by_name"] == "Ada Admin"
    await client.delete(f"/api/items/{item['id']}")
    restored = (await client.post(f"/api/items/{item['id']}/restore")).json()
    assert restored["created_by"] == admin_id
    assert restored["created_by_name"] == "Ada Admin"

    att = await _attachment(client, item["id"])
    assert att["created_by"] == admin_id
    assert att["created_by_name"] == "Ada Admin"
    one = (await client.get(f"/api/attachments/{att['id']}")).json()
    assert one["created_by_name"] == "Ada Admin"
    atts = (await client.get(f"/api/items/{item['id']}/attachments")).json()
    assert [a["created_by_name"] for a in atts] == ["Ada Admin"]
