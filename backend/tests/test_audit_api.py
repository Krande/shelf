"""Audit hooks on the token API, standards and the processing triggers.

Each test does the thing, then reads `/api/admin/audit` back as the
admin. Lookups go by action, so entries written by the cookie routes the
setup goes through (creating items, attaching files) don't get in the
way of what's being asserted.
"""

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from obstore.store import MemoryStore

from shelf.config import settings
from shelf.services import storage

from .helpers import login

ADMIN = "admin@example.com"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def memory_store(monkeypatch: pytest.MonkeyPatch) -> Any:
    storage._store = MemoryStore()

    async def fake_presign(key: str, *_: object, **__: object) -> str:
        return f"https://memory/{key}"

    monkeypatch.setattr(storage, "presign_upload", fake_presign)
    monkeypatch.setattr(storage, "presign_download", fake_presign)
    yield
    storage.reset_store()


@pytest.fixture
async def admin_id(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(settings, "admin_emails", [ADMIN])
    return await login(client, ADMIN)


async def _token(client: AsyncClient) -> str:
    r = await client.post(
        "/api/me/tokens",
        json={"name": "importer", "scopes": ["upload", "search", "download"]},
    )
    assert r.status_code == 201, r.text
    return str(r.json()["plaintext"])


@pytest.fixture
async def token(client: AsyncClient, admin_id: str) -> str:
    return await _token(client)


async def _personal(client: AsyncClient) -> dict[str, Any]:
    spaces = (await client.get("/api/me/spaces")).json()
    return dict(spaces[0])


async def _events(client: AsyncClient, action: str) -> list[dict[str, Any]]:
    r = await client.get("/api/admin/audit", params={"action": action})
    assert r.status_code == 200, r.text
    return list(r.json()["events"])


async def _upload(
    client: AsyncClient, token: str, **data: str
) -> dict[str, Any]:
    r = await client.post(
        "/api/v1/upload",
        headers=_auth(token),
        data=data,
        files={"file": ("spec.pdf", b"%PDF-1.4\nfake", "application/pdf")},
    )
    assert r.status_code == 201, r.text
    return dict(r.json())


# ── /api/v1 ─────────────────────────────────────────────────────────────────


async def test_inline_upload_creating_an_item(
    client: AsyncClient, admin_id: str, token: str
) -> None:
    body = await _upload(client, token, title="Pump datasheet")
    item, att = body["item"], body["attachment"]

    [created] = await _events(client, "item.create")
    assert created["target_type"] == "item"
    assert created["target_id"] == item["id"]
    assert created["target_label"] == "Pump datasheet"
    assert created["space_id"] == item["space_id"]
    assert created["via"] == "api"
    assert created["actor_id"] == admin_id

    [uploaded] = await _events(client, "attachment.upload")
    assert uploaded["target_type"] == "attachment"
    assert uploaded["target_id"] == att["id"]
    assert uploaded["target_label"] == "spec.pdf"
    assert uploaded["space_id"] == item["space_id"]
    assert uploaded["via"] == "api"
    assert uploaded["details"]["item_id"] == item["id"]


async def test_inline_upload_to_an_existing_item(
    client: AsyncClient, token: str
) -> None:
    first = await _upload(client, token, title="Host")
    await _upload(client, token, item_id=first["item"]["id"])
    assert len(await _events(client, "item.create")) == 1
    assert len(await _events(client, "attachment.upload")) == 2


async def test_a_refused_upload_is_not_logged(
    client: AsyncClient, token: str
) -> None:
    r = await client.post(
        "/api/v1/upload",
        headers=_auth(token),
        data={"item_id": str(uuid.uuid4())},
        files={"file": ("x.pdf", b"x", "application/pdf")},
    )
    assert r.status_code == 404
    assert await _events(client, "attachment.") == []
    assert await _events(client, "item.") == []


async def test_register_is_not_an_upload_but_complete_is(
    client: AsyncClient, token: str
) -> None:
    r = await client.post(
        "/api/v1/uploads/register",
        headers=_auth(token),
        json={
            "filename": "big.pdf",
            "content_type": "application/pdf",
            "size_bytes": 10,
            "title": "Big one",
        },
    )
    assert r.status_code == 201, r.text
    att_id = r.json()["attachment"]["id"]
    item = r.json()["item"]

    # The minted item is real; the file isn't there yet.
    [created] = await _events(client, "item.create")
    assert created["target_id"] == item["id"]
    assert created["via"] == "api"
    assert await _events(client, "attachment.upload") == []

    r = await client.post(
        f"/api/v1/uploads/{att_id}/complete", headers=_auth(token)
    )
    assert r.status_code == 200, r.text
    # Completing twice is a no-op, and logs nothing more.
    await client.post(f"/api/v1/uploads/{att_id}/complete", headers=_auth(token))

    [uploaded] = await _events(client, "attachment.upload")
    assert uploaded["target_id"] == att_id
    assert uploaded["target_label"] == "big.pdf"
    assert uploaded["space_id"] == item["space_id"]
    assert uploaded["via"] == "api"


async def test_create_item(client: AsyncClient, token: str) -> None:
    r = await client.post(
        "/api/v1/items",
        headers=_auth(token),
        json={"item_type": "standard", "data": {"title": "  Widgets  "}},
    )
    assert r.status_code == 201, r.text
    untitled = await client.post("/api/v1/items", headers=_auth(token), json={})

    events = await _events(client, "item.create")
    assert [e["target_label"] for e in events] == ["(untitled)", "Widgets"]
    assert events[1]["target_id"] == r.json()["id"]
    assert events[1]["space_id"] == r.json()["space_id"]
    assert events[1]["details"] == {"item_type": "standard"}
    assert events[0]["target_id"] == untitled.json()["id"]
    assert {e["via"] for e in events} == {"api"}


async def test_update_item_names_the_changed_fields(
    client: AsyncClient, token: str
) -> None:
    item = (
        await client.post(
            "/api/v1/items",
            headers=_auth(token),
            json={"data": {"title": "Old", "edition": "2015", "lang": "en"}},
        )
    ).json()
    url = f"/api/v1/items/{item['id']}"

    r = await client.patch(
        url,
        headers=_auth(token),
        json={
            "item_type": "standard",
            "merge": True,
            "data": {"title": "New", "lang": None, "edition": "2015"},
        },
    )
    assert r.status_code == 200, r.text
    # Same values again: nothing changes, nothing logged.
    await client.patch(
        url, headers=_auth(token), json={"merge": True, "data": {"title": "New"}}
    )

    [update] = await _events(client, "item.update")
    assert update["details"] == {"fields": ["item_type", "lang", "title"]}
    assert update["target_label"] == "New"
    assert update["target_id"] == item["id"]
    assert update["space_id"] == item["space_id"]
    assert update["via"] == "api"


async def test_a_refused_update_is_not_logged(
    client: AsyncClient, token: str
) -> None:
    r = await client.patch(
        f"/api/v1/items/{uuid.uuid4()}",
        headers=_auth(token),
        json={"data": {"title": "x"}},
    )
    assert r.status_code == 404
    assert await _events(client, "item.update") == []


async def test_set_item_revision(client: AsyncClient, token: str) -> None:
    item = (
        await client.post(
            "/api/v1/items",
            headers=_auth(token),
            json={"item_type": "standard", "data": {"title": "Widgets"}},
        )
    ).json()
    payload = {"body": "ACME", "designation": "ACME 1234", "label": "2020"}
    url = f"/api/v1/items/{item['id']}/revision"
    assert (await client.put(url, headers=_auth(token), json=payload)).status_code == 200
    # Re-filing the same edition is idempotent and not logged again.
    assert (await client.put(url, headers=_auth(token), json=payload)).status_code == 200

    [rev] = await _events(client, "item.revision")
    assert rev["target_type"] == "item"
    assert rev["target_id"] == item["id"]
    assert rev["target_label"] == "Widgets"
    assert rev["details"] == {
        "body": "ACME",
        "designation": "ACME 1234",
        "revision": "2020",
    }
    assert rev["via"] == "api"
    # The token route doesn't double-log through the web hook.
    assert await _events(client, "standard.") == []


async def test_collections_create_and_delete(
    client: AsyncClient, token: str
) -> None:
    root = await client.post(
        "/api/v1/collections", headers=_auth(token), json={"name": "FEM"}
    )
    assert root.status_code == 201, root.text
    child = await client.post(
        "/api/v1/collections",
        headers=_auth(token),
        json={"name": "Code Aster", "parent_id": root.json()["id"]},
    )
    assert child.status_code == 201, child.text
    r = await client.delete(
        f"/api/v1/collections/{child.json()['id']}", headers=_auth(token)
    )
    assert r.status_code == 204

    created = await _events(client, "collection.create")
    assert [e["target_label"] for e in created] == ["Code Aster", "FEM"]
    assert created[0]["target_id"] == child.json()["id"]
    # Named, as the web route records it.
    assert created[0]["details"] == {"parent": "FEM"}
    assert created[1]["details"] is None
    assert created[1]["space_id"] == root.json()["space_id"]
    assert {e["via"] for e in created} == {"api"}

    [deleted] = await _events(client, "collection.delete")
    assert deleted["target_type"] == "collection"
    assert deleted["target_id"] == child.json()["id"]
    assert deleted["target_label"] == "Code Aster"
    assert deleted["via"] == "api"

    # Refused: no such collection.
    r = await client.delete(
        f"/api/v1/collections/{uuid.uuid4()}", headers=_auth(token)
    )
    assert r.status_code == 404
    assert len(await _events(client, "collection.delete")) == 1


async def test_set_item_collections(client: AsyncClient, token: str) -> None:
    a = (
        await client.post(
            "/api/v1/collections", headers=_auth(token), json={"name": "A"}
        )
    ).json()
    b = (
        await client.post(
            "/api/v1/collections", headers=_auth(token), json={"name": "B"}
        )
    ).json()
    item = (
        await client.post(
            "/api/v1/items",
            headers=_auth(token),
            json={"data": {"title": "Filed"}, "collection_id": [a["id"]]},
        )
    ).json()
    url = f"/api/v1/items/{item['id']}/collections"

    r = await client.put(
        url, headers=_auth(token), json={"collection_ids": [b["id"]]}
    )
    assert r.status_code == 200, r.text
    # Unchanged membership: not logged.
    await client.put(url, headers=_auth(token), json={"collection_ids": [b["id"]]})
    # Refused: a collection that doesn't exist.
    r = await client.put(
        url, headers=_auth(token), json={"collection_ids": [str(uuid.uuid4())]}
    )
    assert r.status_code == 400

    [event] = await _events(client, "item.collections")
    assert event["target_type"] == "item"
    assert event["target_id"] == item["id"]
    assert event["target_label"] == "Filed"
    assert event["space_id"] == item["space_id"]
    assert event["details"] == {"added": ["B"], "removed": ["A"]}
    assert event["via"] == "api"


async def test_download_is_logged_once_handed_out(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, admin_id: str
) -> None:
    token = await _token(client)
    body = await _upload(client, token, title="Manual")
    att_id = body["attachment"]["id"]

    r = await client.get(
        f"/api/v1/download/{att_id}", headers=_auth(token), follow_redirects=False
    )
    assert r.status_code == 302, r.text

    # Refused: an unknown version, an unknown attachment, someone else's.
    r = await client.get(
        f"/api/v1/download/{att_id}",
        headers=_auth(token),
        params={"version": str(uuid.uuid4())},
        follow_redirects=False,
    )
    assert r.status_code == 404
    r = await client.get(
        f"/api/v1/download/{uuid.uuid4()}", headers=_auth(token), follow_redirects=False
    )
    assert r.status_code == 404
    await login(client, "eve@example.com")
    eve = await _token(client)
    r = await client.get(
        f"/api/v1/download/{att_id}", headers=_auth(eve), follow_redirects=False
    )
    assert r.status_code == 404

    await login(client, ADMIN)
    [event] = await _events(client, "attachment.download")
    assert event["actor_id"] == admin_id
    assert event["target_type"] == "attachment"
    assert event["target_id"] == att_id
    assert event["target_label"] == "spec.pdf"
    assert event["space_id"] == body["item"]["space_id"]
    assert event["details"]["version"] == "original"
    assert event["via"] == "api"


# ── Standards ───────────────────────────────────────────────────────────────


async def _standard(client: AsyncClient, slug: str, label: str) -> str:
    created = await client.post(
        f"/api/spaces/{slug}/items",
        json={"item_type": "standard", "data": {"title": f"ACME 1234:{label}"}},
    )
    assert created.status_code == 201, created.text
    item_id = str(created.json()["id"])
    r = await client.put(
        f"/api/items/{item_id}/revision",
        json={"body": "ACME", "designation": "ACME 1234", "label": label},
    )
    assert r.status_code == 200, r.text
    return item_id


async def test_standard_revision_set_and_delete(
    client: AsyncClient, admin_id: str
) -> None:
    space = await _personal(client)
    item_id = await _standard(client, space["slug"], "2020")
    # Same filing again: no entry.
    await client.put(
        f"/api/items/{item_id}/revision",
        json={"body": "ACME", "designation": "ACME 1234", "label": "2020"},
    )
    family_id = (await client.get(f"/api/items/{item_id}/revisions")).json()[
        "family"
    ]["id"]

    [linked] = await _events(client, "standard.revision.set")
    assert linked["target_type"] == "standard"
    assert linked["target_id"] == family_id
    assert linked["target_label"] == "ACME 1234"
    assert linked["space_id"] == space["id"]
    assert linked["details"]["revision"] == "2020"
    assert linked["details"]["item_id"] == item_id
    assert linked["via"] == "web"

    # Refused: someone with no access to the item.
    await login(client, "eve@example.com")
    r = await client.delete(f"/api/items/{item_id}/revision")
    assert r.status_code == 404
    await login(client, ADMIN)
    assert await _events(client, "standard.revision.delete") == []

    assert (await client.delete(f"/api/items/{item_id}/revision")).status_code == 204
    [unlinked] = await _events(client, "standard.revision.delete")
    assert unlinked["target_id"] == family_id
    assert unlinked["target_label"] == "ACME 1234"
    assert unlinked["space_id"] == space["id"]
    assert unlinked["details"]["revision"] == "2020"
    assert unlinked["details"]["item"] == "ACME 1234:2020"


async def test_standard_pin_set_and_remove(
    client: AsyncClient, admin_id: str
) -> None:
    space = await _personal(client)
    old = await _standard(client, space["slug"], "2007")
    new = await _standard(client, space["slug"], "2020")
    family_id = (await client.get(f"/api/items/{old}/revisions")).json()[
        "family"
    ]["id"]
    url = f"/api/spaces/{space['slug']}/pins/{family_id}"

    assert (await client.put(url, json={"item_id": old})).status_code == 200
    assert (await client.put(url, json={"item_id": old})).status_code == 200
    assert (await client.put(url, json={"item_id": new})).status_code == 200

    pins = await _events(client, "standard.pin.set")
    assert len(pins) == 2
    repin, first = pins
    assert first["target_type"] == "standard"
    assert first["target_id"] == family_id
    assert first["target_label"] == "ACME 1234"
    assert first["space_id"] == space["id"]
    assert first["details"] == {"revision": "2007", "item_id": old}
    assert repin["details"] == {
        "revision": "2020",
        "item_id": new,
        "previous_revision": "2007",
    }

    # Refused: not the owner.
    await login(client, "eve@example.com")
    assert (await client.delete(url)).status_code == 404
    await login(client, ADMIN)
    assert await _events(client, "standard.pin.remove") == []

    assert (await client.delete(url)).status_code == 204
    [removed] = await _events(client, "standard.pin.remove")
    assert removed["target_id"] == family_id
    assert removed["target_label"] == "ACME 1234"
    assert removed["space_id"] == space["id"]
    assert removed["details"] == {"revision": "2020"}
    assert removed["via"] == "web"


# ── Processing ──────────────────────────────────────────────────────────────


async def _pdf(
    client: AsyncClient, slug: str, content_type: str = "application/pdf"
) -> dict[str, Any]:
    item = (
        await client.post(
            f"/api/spaces/{slug}/items",
            json={"item_type": "document", "data": {"title": "T"}},
        )
    ).json()
    att = (
        await client.post(
            f"/api/items/{item['id']}/attachments",
            json={"filename": "scan.pdf", "content_type": content_type},
        )
    ).json()["attachment"]
    await client.post(f"/api/attachments/{att['id']}/complete")
    return dict(att)


@pytest.fixture
def quiet_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    from shelf.api import processing as proc_api

    async def ok(*_: object) -> bool:
        return True

    for name in ("publish_ocr", "publish_ocr_gpu", "publish_outline", "publish_extract"):
        monkeypatch.setattr(proc_api.queue, name, ok)
    monkeypatch.setattr(proc_api.storage, "restore_from_original", ok)


async def test_processing_actions(
    client: AsyncClient, admin_id: str, quiet_queue: None
) -> None:
    space = await _personal(client)
    att = await _pdf(client, space["slug"])
    base = f"/api/attachments/{att['id']}/processing"

    assert (await client.post(f"{base}/ocr")).status_code == 200
    assert (await client.post(f"{base}/ocr_gpu")).status_code == 200
    assert (await client.post(f"{base}/outline")).status_code == 200
    r = await client.post(f"{base}/cancel", json={"job": "outline"})
    assert r.json()["cancelled"] is True
    # Nothing in flight any more: a no-op, not logged.
    r = await client.post(f"{base}/cancel", json={"job": "outline"})
    assert r.json()["cancelled"] is False
    assert (await client.post(f"{base}/restore_original")).status_code == 200

    events = await _events(client, "processing.")
    assert [e["action"] for e in events] == [
        "processing.restore",
        "processing.cancel",
        "processing.outline",
        "processing.ocr",
        "processing.ocr",
    ]
    for e in events:
        assert e["target_type"] == "attachment"
        assert e["target_id"] == att["id"]
        assert e["target_label"] == "scan.pdf"
        assert e["space_id"] == space["id"]
        assert e["actor_id"] == admin_id
        assert e["via"] == "web"
    restore, cancel, outline, gpu, cpu = events
    assert cpu["details"] == {"engine": "cpu"}
    assert gpu["details"] == {"engine": "gpu"}
    assert cancel["details"] == {"job": "outline", "previous_status": "queued"}
    assert outline["details"] is None
    assert restore["details"] is None


async def test_refused_processing_is_not_logged(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    admin_id: str,
    quiet_queue: None,
) -> None:
    from shelf.api import processing as proc_api

    space = await _personal(client)
    text = await _pdf(client, space["slug"], content_type="text/plain")
    assert (
        await client.post(f"/api/attachments/{text['id']}/processing/ocr")
    ).status_code == 400

    att = await _pdf(client, space["slug"])

    async def nothing_to_restore(_key: str) -> bool:
        return False

    monkeypatch.setattr(proc_api.storage, "restore_from_original", nothing_to_restore)
    r = await client.post(f"/api/attachments/{att['id']}/processing/restore_original")
    assert r.status_code == 404

    # A non-admin is refused outright.
    await login(client, "eve@example.com")
    r = await client.post(f"/api/attachments/{att['id']}/processing/outline")
    assert r.status_code in (403, 404)

    await login(client, ADMIN)
    assert await _events(client, "processing.") == []
