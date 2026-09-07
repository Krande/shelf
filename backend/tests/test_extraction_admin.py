"""Per-user extraction admin endpoints.

Hits /api/me/extraction/* against a MemoryStore-backed storage and a
mocked queue publisher. Covers stats counts, listing filters,
ownership isolation, and the rescan side-effects (status reset +
publish).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import AsyncClient
from obstore.store import MemoryStore

from shelf.services import storage


@pytest.fixture(autouse=True)
def memory_store() -> Any:
    storage._store = MemoryStore()
    yield
    storage.reset_store()


async def _login(client: AsyncClient, email: str = "alice@example.com") -> str:
    r = await client.post("/auth/dev-login", json={"email": email})
    assert r.status_code == 200, r.text
    me = await client.get("/api/me")
    return f"u-{me.json()['id'].replace('-', '')[:8]}"


async def _make_pdf_attachment(
    client: AsyncClient,
    slug: str,
    title: str,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    async def fake_presign_upload(_key: str, **_: object) -> str:
        return "https://stub"

    monkeypatch.setattr(storage, "presign_upload", fake_presign_upload)

    item = (
        await client.post(
            f"/api/spaces/{slug}/items",
            json={"item_type": "document", "data": {"title": title}},
        )
    ).json()
    att = (
        await client.post(
            f"/api/items/{item['id']}/attachments",
            json={"filename": f"{title}.pdf", "content_type": "application/pdf"},
        )
    ).json()["attachment"]
    await client.post(f"/api/attachments/{att['id']}/complete")
    return att


async def _set_status(att_id: str, status_value: str | None, chars: int = 0) -> None:
    """Direct row mutation — same shorthand as the search test
    fixture; lets us seed any combination of statuses without going
    through the worker."""
    from shelf.db import session_factory
    from shelf.models import Attachment

    async with session_factory() as db:
        row = await db.get(Attachment, uuid.UUID(att_id))
        assert row is not None
        row.extraction_status = status_value
        row.text_chars = chars if chars > 0 else None
        row.extracted_at = datetime.now(UTC) if status_value else None
        await db.commit()


async def test_stats_counts_by_status(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # publish_extract is invoked by /complete; mute it so status
    # stays at whatever we set explicitly.
    from shelf.services import queue as queue_svc

    async def fake_publish(_: uuid.UUID) -> bool:
        return True

    monkeypatch.setattr(queue_svc, "publish_extract", fake_publish)

    slug = await _login(client)
    a = await _make_pdf_attachment(client, slug, "alpha", monkeypatch)
    b = await _make_pdf_attachment(client, slug, "bravo", monkeypatch)
    c = await _make_pdf_attachment(client, slug, "charlie", monkeypatch)
    d = await _make_pdf_attachment(client, slug, "delta", monkeypatch)
    await _make_pdf_attachment(client, slug, "echo", monkeypatch)
    await _set_status(a["id"], "extracted", chars=2000)
    await _set_status(b["id"], "empty")
    await _set_status(c["id"], "failed")
    await _set_status(d["id"], None)  # never enqueued
    # echo stays at 'pending' (set by /complete)

    r = await client.get("/api/me/extraction/stats")
    assert r.status_code == 200
    s = r.json()
    assert s["extracted"] == 1
    assert s["empty"] == 1
    assert s["failed"] == 1
    assert s["pending"] == 1
    assert s["missing"] == 1
    assert s["total_pdfs"] == 5


async def test_list_filter_by_missing(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from shelf.services import queue as queue_svc

    async def fake_publish(_: uuid.UUID) -> bool:
        return True

    monkeypatch.setattr(queue_svc, "publish_extract", fake_publish)

    slug = await _login(client)
    a = await _make_pdf_attachment(client, slug, "old", monkeypatch)
    await _make_pdf_attachment(client, slug, "new", monkeypatch)
    await _set_status(a["id"], None)
    # the second row stays at 'pending'

    r = await client.get(
        "/api/me/extraction/attachments", params={"status": "missing"}
    )
    assert r.status_code == 200
    rows = r.json()
    assert [row["id"] for row in rows] == [a["id"]]
    assert rows[0]["item_title"] == "old"


async def test_rescan_default_picks_pending_and_missing(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from shelf.services import queue as queue_svc

    published: list[uuid.UUID] = []

    async def fake_publish(att_id: uuid.UUID) -> bool:
        published.append(att_id)
        return True

    monkeypatch.setattr(queue_svc, "publish_extract", fake_publish)

    slug = await _login(client)
    a = await _make_pdf_attachment(client, slug, "ext", monkeypatch)
    b = await _make_pdf_attachment(client, slug, "fail", monkeypatch)
    c = await _make_pdf_attachment(client, slug, "miss", monkeypatch)
    d = await _make_pdf_attachment(client, slug, "pend", monkeypatch)
    await _set_status(a["id"], "extracted", chars=500)
    await _set_status(b["id"], "failed")
    await _set_status(c["id"], None)
    # d stays at 'pending' from /complete

    published.clear()
    r = await client.post("/api/me/extraction/rescan")
    assert r.status_code == 200
    body = r.json()
    # Default selector is pending + null. extracted + failed are
    # untouched.
    assert body["selected"] == 2
    assert body["enqueued"] == 2
    assert {str(x) for x in published} == {c["id"], d["id"]}


async def test_rescan_one_resets_status_and_publishes(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from shelf.services import queue as queue_svc

    published: list[uuid.UUID] = []

    async def fake_publish(att_id: uuid.UUID) -> bool:
        published.append(att_id)
        return True

    monkeypatch.setattr(queue_svc, "publish_extract", fake_publish)

    slug = await _login(client)
    a = await _make_pdf_attachment(client, slug, "stuck", monkeypatch)
    await _set_status(a["id"], "failed")

    published.clear()
    r = await client.post(
        f"/api/me/extraction/attachments/{a['id']}/rescan"
    )
    assert r.status_code == 200
    assert r.json() == {"selected": 1, "enqueued": 1}
    assert published == [uuid.UUID(a["id"])]

    # Status is now back to pending so the UI reflects the rescan
    # immediately.
    from shelf.db import session_factory
    from shelf.models import Attachment

    async with session_factory() as db:
        row = await db.get(Attachment, uuid.UUID(a["id"]))
        assert row is not None
        assert row.extraction_status == "pending"
        assert row.extracted_at is None


async def test_other_user_cannot_see_or_rescan(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from shelf.services import queue as queue_svc

    async def fake_publish(_: uuid.UUID) -> bool:
        return True

    monkeypatch.setattr(queue_svc, "publish_extract", fake_publish)

    alice_slug = await _login(client, "alice@example.com")
    a = await _make_pdf_attachment(client, alice_slug, "alice", monkeypatch)
    await _set_status(a["id"], None)

    await client.post("/auth/logout")
    await _login(client, "mallory@example.com")

    # Mallory has no PDFs; her stats are zeroed out.
    r = await client.get("/api/me/extraction/stats")
    assert r.status_code == 200
    assert r.json()["total_pdfs"] == 0

    # Listing under her account returns nothing — never sees Alice's row.
    r = await client.get("/api/me/extraction/attachments")
    assert r.json() == []

    # Per-row rescan on Alice's row 404s for Mallory.
    r = await client.post(
        f"/api/me/extraction/attachments/{a['id']}/rescan"
    )
    assert r.status_code == 404


async def test_rescan_one_rejects_non_pdf(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_presign_upload(_key: str, **_: object) -> str:
        return "https://stub"

    monkeypatch.setattr(storage, "presign_upload", fake_presign_upload)

    slug = await _login(client)
    item = (
        await client.post(
            f"/api/spaces/{slug}/items",
            json={"item_type": "document", "data": {"title": "x"}},
        )
    ).json()
    att = (
        await client.post(
            f"/api/items/{item['id']}/attachments",
            json={"filename": "n.txt", "content_type": "text/plain"},
        )
    ).json()["attachment"]
    await client.post(f"/api/attachments/{att['id']}/complete")

    r = await client.post(
        f"/api/me/extraction/attachments/{att['id']}/rescan"
    )
    assert r.status_code == 400
