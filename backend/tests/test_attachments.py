"""Integration tests for attachment registration + lifecycle.

Presign URL contents are not asserted because the local test stack
doesn't run a real S3 — we just check the row management. The
end-to-end upload + download path is exercised against Garage in
the live deployment.
"""

import pytest
from httpx import AsyncClient
from obstore.store import MemoryStore

from shelf.services import storage


@pytest.fixture(autouse=True)
def memory_store() -> None:
    """Swap the global S3 store for a MemoryStore for the duration of
    each attachment test, then restore. presign isn't supported on
    MemoryStore, so register_attachment / download endpoints are
    monkeypatched per-test rather than globally."""
    storage._store = MemoryStore()
    yield
    storage.reset_store()


async def _login(client: AsyncClient, email: str = "alice@example.com") -> str:
    r = await client.post("/auth/dev-login", json={"email": email})
    assert r.status_code == 200, r.text
    me = await client.get("/api/me")
    return f"u-{me.json()['id'].replace('-', '')[:8]}"


async def test_register_and_list_attachment(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_presign_upload(key: str, **_: object) -> str:
        return f"https://memory/upload/{key}?sig=stub"

    async def fake_presign_download(key: str, **_: object) -> str:
        return f"https://memory/download/{key}?sig=stub"

    monkeypatch.setattr(storage, "presign_upload", fake_presign_upload)
    monkeypatch.setattr(storage, "presign_download", fake_presign_download)

    slug = await _login(client)
    item = (
        await client.post(
            f"/api/spaces/{slug}/items",
            json={"item_type": "document", "data": {"title": "T"}},
        )
    ).json()

    r = await client.post(
        f"/api/items/{item['id']}/attachments",
        json={
            "filename": "paper.pdf",
            "content_type": "application/pdf",
            "size_bytes": 1234,
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["attachment"]["filename"] == "paper.pdf"
    assert body["attachment"]["item_id"] == item["id"]
    assert body["upload_url"].startswith("https://memory/upload/")
    att_id = body["attachment"]["id"]

    listing = (
        await client.get(f"/api/items/{item['id']}/attachments")
    ).json()
    assert [a["id"] for a in listing] == [att_id]

    download = (await client.get(f"/api/attachments/{att_id}/download")).json()
    assert download["url"].startswith("https://memory/download/")


async def test_delete_attachment_drops_row(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_presign_upload(key: str, **_: object) -> str:
        return "https://stub"

    monkeypatch.setattr(storage, "presign_upload", fake_presign_upload)

    slug = await _login(client)
    item = (
        await client.post(
            f"/api/spaces/{slug}/items",
            json={"item_type": "document", "data": {"title": "T"}},
        )
    ).json()
    att = (
        await client.post(
            f"/api/items/{item['id']}/attachments",
            json={"filename": "x", "content_type": "text/plain"},
        )
    ).json()["attachment"]

    r = await client.delete(f"/api/attachments/{att['id']}")
    assert r.status_code == 204
    listing = (
        await client.get(f"/api/items/{item['id']}/attachments")
    ).json()
    assert listing == []


async def test_cleanup_orphans_removes_pending_only(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import update

    from shelf.db import engine as db_engine
    from shelf.models import Attachment

    async def fake_presign_upload(_key: str, **_: object) -> str:
        return "https://stub"

    monkeypatch.setattr(storage, "presign_upload", fake_presign_upload)

    slug = await _login(client)
    item = (
        await client.post(
            f"/api/spaces/{slug}/items",
            json={"item_type": "document", "data": {"title": "T"}},
        )
    ).json()

    # Three attachments: one finished, one pending-but-recent, one
    # pending-and-old. Only the third should be reaped.
    finished = (
        await client.post(
            f"/api/items/{item['id']}/attachments",
            json={"filename": "ok.pdf", "content_type": "application/pdf"},
        )
    ).json()["attachment"]
    await client.post(f"/api/attachments/{finished['id']}/complete")

    fresh_pending = (
        await client.post(
            f"/api/items/{item['id']}/attachments",
            json={"filename": "fresh.pdf", "content_type": "application/pdf"},
        )
    ).json()["attachment"]

    old_pending = (
        await client.post(
            f"/api/items/{item['id']}/attachments",
            json={"filename": "old.pdf", "content_type": "application/pdf"},
        )
    ).json()["attachment"]
    # Backdate the old one's created_at past the default 60-min cutoff.
    async with db_engine.begin() as conn:
        await conn.execute(
            update(Attachment)
            .where(Attachment.id == __import__("uuid").UUID(old_pending["id"]))
            .values(created_at=datetime.now(UTC) - timedelta(hours=2))
        )

    r = await client.post("/api/me/attachments/cleanup-orphans")
    assert r.status_code == 200
    assert r.json()["deleted"] == 1

    listing = (
        await client.get(f"/api/items/{item['id']}/attachments")
    ).json()
    ids = {a["id"] for a in listing}
    assert finished["id"] in ids
    assert fresh_pending["id"] in ids
    assert old_pending["id"] not in ids


async def test_other_user_cannot_see_attachment(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_presign_upload(key: str, **_: object) -> str:
        return "https://stub"

    monkeypatch.setattr(storage, "presign_upload", fake_presign_upload)

    alice_slug = await _login(client, "alice@example.com")
    item = (
        await client.post(
            f"/api/spaces/{alice_slug}/items",
            json={"item_type": "document", "data": {"title": "T"}},
        )
    ).json()
    att = (
        await client.post(
            f"/api/items/{item['id']}/attachments",
            json={"filename": "x", "content_type": "text/plain"},
        )
    ).json()["attachment"]

    await client.post("/auth/logout")
    await _login(client, "mallory@example.com")

    r = await client.get(f"/api/items/{item['id']}/attachments")
    assert r.status_code == 404
    r = await client.get(f"/api/attachments/{att['id']}/download")
    assert r.status_code == 404
    r = await client.delete(f"/api/attachments/{att['id']}")
    assert r.status_code == 404
