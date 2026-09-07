"""Manual OCR/outline trigger + per-bucket admin listing.

Mirrors the extraction-admin tests in shape: register a PDF, exercise
the admin endpoint, assert state transitions on the
``attachment_processing`` row. The publish path is monkeypatched so
NATS isn't required.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient


async def _login(client: AsyncClient, email: str = "alice@example.com") -> str:
    await client.post("/auth/dev-login", json={"email": email})
    me = (await client.get("/api/me")).json()
    return f"u-{me['id'].replace('-', '')[:8]}"


async def _make_pdf_attachment(client: AsyncClient, slug: str) -> dict:
    item = (
        await client.post(
            f"/api/spaces/{slug}/items",
            json={"item_type": "document", "data": {"title": "T"}},
        )
    ).json()
    att = (
        await client.post(
            f"/api/items/{item['id']}/attachments",
            json={"filename": "p.pdf", "content_type": "application/pdf"},
        )
    ).json()["attachment"]
    # Mark uploaded so the admin scope filter picks the row up.
    await client.post(f"/api/attachments/{att['id']}/complete")
    return att


async def test_trigger_ocr_marks_queued_and_publishes(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from shelf.api import processing as proc_api
    from shelf.db import session_factory
    from shelf.models import AttachmentProcessing

    captured: list[uuid.UUID] = []

    async def fake_publish_ocr(aid: uuid.UUID) -> bool:
        captured.append(aid)
        return True

    monkeypatch.setattr(proc_api.queue, "publish_ocr", fake_publish_ocr)

    slug = await _login(client)
    att = await _make_pdf_attachment(client, slug)

    r = await client.post(f"/api/attachments/{att['id']}/processing/ocr")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["job"] == "ocr"
    assert body["enqueued"] is True
    assert body["attachment_id"] == att["id"]

    assert captured == [uuid.UUID(att["id"])]
    async with session_factory() as db:
        proc = await db.get(AttachmentProcessing, uuid.UUID(att["id"]))
        assert proc is not None
        assert proc.ocr_status == "queued"
        # ocr_engine is left null on trigger; the worker writes its
        # versioned label when the run starts.
        assert proc.ocr_engine is None


async def test_trigger_outline_marks_queued_and_publishes(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from shelf.api import processing as proc_api
    from shelf.db import session_factory
    from shelf.models import AttachmentProcessing

    captured: list[uuid.UUID] = []

    async def fake_publish_outline(aid: uuid.UUID) -> bool:
        captured.append(aid)
        return True

    monkeypatch.setattr(
        proc_api.queue, "publish_outline", fake_publish_outline
    )

    slug = await _login(client)
    att = await _make_pdf_attachment(client, slug)

    r = await client.post(
        f"/api/attachments/{att['id']}/processing/outline"
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["job"] == "outline"
    assert body["enqueued"] is True

    assert captured == [uuid.UUID(att["id"])]
    async with session_factory() as db:
        proc = await db.get(AttachmentProcessing, uuid.UUID(att["id"]))
        assert proc is not None
        assert proc.outline_status == "queued"
        # outline_engine is left null on trigger; the worker writes
        # its versioned label when the run starts.
        assert proc.outline_engine is None


async def test_trigger_ocr_isolates_users(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bob can't trigger OCR on Alice's attachment."""
    from shelf.api import processing as proc_api

    async def fake(_: uuid.UUID) -> bool:
        return True

    monkeypatch.setattr(proc_api.queue, "publish_ocr", fake)

    a_slug = await _login(client)
    a_att = await _make_pdf_attachment(client, a_slug)

    await client.post("/auth/dev-login", json={"email": "bob@example.com"})
    r = await client.post(
        f"/api/attachments/{a_att['id']}/processing/ocr"
    )
    assert r.status_code == 404


async def test_trigger_rejects_non_pdf(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from shelf.api import processing as proc_api

    async def fake(_: uuid.UUID) -> bool:
        return True

    monkeypatch.setattr(proc_api.queue, "publish_ocr", fake)

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
            json={"filename": "n.txt", "content_type": "text/plain"},
        )
    ).json()["attachment"]
    await client.post(f"/api/attachments/{att['id']}/complete")

    r = await client.post(f"/api/attachments/{att['id']}/processing/ocr")
    assert r.status_code == 400


async def test_processing_stats_counts_buckets(client: AsyncClient) -> None:
    """An attachment with no processing row counts as not_assessed; one
    with needs_ocr=true counts toward needs_ocr."""
    from datetime import UTC, datetime

    from shelf.db import session_factory
    from shelf.models import AttachmentProcessing

    slug = await _login(client)
    await _make_pdf_attachment(client, slug)
    a2 = await _make_pdf_attachment(client, slug)

    # Seed only a2 with a processing row that flags needs_ocr.
    async with session_factory() as db:
        now = datetime.now(UTC)
        db.add(
            AttachmentProcessing(
                attachment_id=uuid.UUID(a2["id"]),
                assessed_at=now,
                page_count=1,
                text_chars=10,
                needs_ocr=True,
                needs_outline=False,
                ocr_status="untouched",
                outline_status="untouched",
            )
        )
        await db.commit()

    r = await client.get("/api/me/processing/stats")
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["total_pdfs"] == 2
    assert s["assessed"] == 1
    assert s["not_assessed"] == 1
    assert s["needs_ocr"] == 1
    assert s["needs_outline"] == 0
    # both rows are in "ocr_untouched" — a1 by virtue of the outer
    # join, a2 because we wrote 'untouched' explicitly.
    assert s["ocr_untouched"] == 2


async def test_restore_original_404s_when_no_snapshot_exists(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fresh attachment with no preserved-original sibling
    returns 404 from the restore endpoint — the user shouldn't be
    able to invoke a restore that has nothing to restore from."""
    from shelf.api import processing as proc_api

    async def fake_restore(_key: str) -> bool:
        return False

    monkeypatch.setattr(
        proc_api.storage, "restore_from_original", fake_restore
    )

    slug = await _login(client)
    att = await _make_pdf_attachment(client, slug)

    r = await client.post(
        f"/api/attachments/{att['id']}/processing/restore_original"
    )
    assert r.status_code == 404
    assert "No preserved original" in r.json()["detail"]


async def test_restore_original_resets_processing_and_republishes(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When a snapshot exists, restore copies it back, resets the
    processing row's *_status fields, and republishes extract."""
    from datetime import UTC, datetime

    from shelf.api import processing as proc_api
    from shelf.db import session_factory
    from shelf.models import AttachmentProcessing

    captured_restore: list[str] = []
    captured_publish: list[uuid.UUID] = []

    async def fake_restore(key: str) -> bool:
        captured_restore.append(key)
        return True

    async def fake_publish_extract(aid: uuid.UUID) -> bool:
        captured_publish.append(aid)
        return True

    monkeypatch.setattr(
        proc_api.storage, "restore_from_original", fake_restore
    )

    slug = await _login(client)
    att = await _make_pdf_attachment(client, slug)
    # Patch publish_extract *after* the upload-complete flow so the
    # snapshot publish doesn't get counted in our captured list.
    monkeypatch.setattr(
        proc_api.queue, "publish_extract", fake_publish_extract
    )

    # Pre-seed an OCR'd row so we can verify the reset happens.
    async with session_factory() as db:
        now = datetime.now(UTC)
        db.add(
            AttachmentProcessing(
                attachment_id=uuid.UUID(att["id"]),
                ocr_status="done",
                ocr_engine="ocrmypdf/17 shelf/sha-x",
                ocr_completed_at=now,
                outline_status="failed",
                original_preserved_at=now,
            )
        )
        await db.commit()

    r = await client.post(
        f"/api/attachments/{att['id']}/processing/restore_original"
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["restored"] is True

    # Storage was asked to restore; extract was republished.
    assert len(captured_restore) == 1
    assert captured_publish == [uuid.UUID(att["id"])]

    async with session_factory() as db:
        proc = await db.get(AttachmentProcessing, uuid.UUID(att["id"]))
        assert proc is not None
        assert proc.ocr_status == "untouched"
        assert proc.outline_status == "untouched"
        assert proc.ocr_engine is None
        assert proc.ocr_completed_at is None
        # original_preserved_at must NOT be cleared — the user might
        # want to restore again after another bad worker pass.
        assert proc.original_preserved_at is not None


async def test_processing_list_filter_needs_ocr(
    client: AsyncClient,
) -> None:
    from datetime import UTC, datetime

    from shelf.db import session_factory
    from shelf.models import AttachmentProcessing

    slug = await _login(client)
    a1 = await _make_pdf_attachment(client, slug)
    a2 = await _make_pdf_attachment(client, slug)

    async with session_factory() as db:
        now = datetime.now(UTC)
        db.add(
            AttachmentProcessing(
                attachment_id=uuid.UUID(a2["id"]),
                assessed_at=now,
                page_count=10,
                needs_ocr=True,
                needs_outline=False,
            )
        )
        await db.commit()

    # bucket=needs_ocr returns only a2
    r = await client.get(
        "/api/me/processing/attachments", params={"bucket": "needs_ocr"}
    )
    assert r.status_code == 200, r.text
    rows = r.json()
    assert [row["id"] for row in rows] == [a2["id"]]

    # bucket=not_assessed returns only a1
    r = await client.get(
        "/api/me/processing/attachments", params={"bucket": "not_assessed"}
    )
    assert r.status_code == 200, r.text
    rows = r.json()
    assert [row["id"] for row in rows] == [a1["id"]]
