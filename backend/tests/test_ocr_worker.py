"""Phase B OCR worker — round-trip + auto-trigger tests.

We never actually run Tesseract here; ``_run_ocrmypdf`` is patched
to return canned bytes. That keeps tests fast and CI-portable while
still exercising the full glue: storage round-trip, processing-row
state transitions, and the re-extract publish.
"""

from __future__ import annotations

import uuid
from typing import Any

import obstore
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
    await client.post("/auth/dev-login", json={"email": email})
    me = (await client.get("/api/me")).json()
    return f"u-{me['id'].replace('-', '')[:8]}"


async def _make_pdf_attachment(
    client: AsyncClient, slug: str, body: bytes
) -> dict:
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
    await client.post(f"/api/attachments/{att['id']}/complete")

    from shelf.db import session_factory
    from shelf.models import Attachment

    async with session_factory() as db:
        row = await db.get(Attachment, uuid.UUID(att["id"]))
        assert row is not None
        await obstore.put_async(storage.get_store(), row.storage_key, body)
    return att


async def test_ocr_roundtrip_writes_back_and_reextracts(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: queue an OCR job, the worker swaps the PDF body
    in storage, marks ocr_status='done', and republishes extract."""
    from shelf.db import session_factory
    from shelf.models import Attachment, AttachmentProcessing
    from shelf.worker import ocr as ocr_mod

    async def fake_presign_upload(_key: str, **_: object) -> str:
        return "https://stub"

    monkeypatch.setattr(storage, "presign_upload", fake_presign_upload)

    slug = await _login(client)
    att = await _make_pdf_attachment(client, slug, b"original-pdf-bytes")

    new_bytes = b"ocrd-pdf-bytes"

    async def fake_run(_aid: uuid.UUID, _body: bytes) -> bytes:
        return new_bytes

    monkeypatch.setattr(ocr_mod, "_run_ocrmypdf_async", fake_run)

    captured_extract: list[uuid.UUID] = []
    captured_outline: list[uuid.UUID] = []

    async def fake_publish_extract(aid: uuid.UUID) -> bool:
        captured_extract.append(aid)
        return True

    async def fake_publish_outline(aid: uuid.UUID) -> bool:
        captured_outline.append(aid)
        return True

    monkeypatch.setattr(ocr_mod.queue, "publish_extract", fake_publish_extract)
    monkeypatch.setattr(ocr_mod.queue, "publish_outline", fake_publish_outline)

    await ocr_mod.ocr_attachment(att["id"])

    # 1. The original storage_key is untouched — OCR now writes to a
    #    new derivation key.
    from sqlalchemy import select

    from shelf.models import AttachmentDerivation

    async with session_factory() as db:
        row = await db.get(Attachment, uuid.UUID(att["id"]))
        assert row is not None
        res = await obstore.get_async(storage.get_store(), row.storage_key)
        assert bytes(await res.bytes_async()) != new_bytes

    # 2. A new OCR derivation row exists, blob lives at its key.
    async with session_factory() as db:
        deriv = (
            await db.execute(
                select(AttachmentDerivation).where(
                    AttachmentDerivation.attachment_id
                    == uuid.UUID(att["id"]),
                    AttachmentDerivation.kind == "ocr",
                )
            )
        ).scalar_one()
        assert deriv.parent_storage_key == row.storage_key
        assert deriv.engine.startswith("ocrmypdf/")
        res2 = await obstore.get_async(storage.get_store(), deriv.storage_key)
        assert bytes(await res2.bytes_async()) == new_bytes

    # 3. Processing row reflects the run + chains outline.
    async with session_factory() as db:
        proc = await db.get(AttachmentProcessing, uuid.UUID(att["id"]))
        assert proc is not None
        assert proc.ocr_status == "done"
        assert proc.ocr_engine is not None
        assert proc.ocr_engine.startswith("ocrmypdf/")
        assert proc.ocr_completed_at is not None
        assert proc.outline_status == "queued"

    # 4. Extract + outline both re-queued.
    assert captured_extract == [uuid.UUID(att["id"])]
    assert captured_outline == [uuid.UUID(att["id"])]


async def test_ocr_run_returning_none_means_cancelled(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If ``_run_ocrmypdf_async`` returns None (cancel poller killed
    the subprocess), the worker writes nothing to storage and the
    extract republish is skipped — the row's ocr_status stays
    'cancelled' as set by the API endpoint."""
    from datetime import UTC, datetime

    from shelf.db import session_factory
    from shelf.models import AttachmentProcessing
    from shelf.worker import ocr as ocr_mod

    async def fake_presign_upload(_key: str, **_: object) -> str:
        return "https://stub"

    monkeypatch.setattr(storage, "presign_upload", fake_presign_upload)

    async def fake_run_returns_none(
        _aid: uuid.UUID, _body: bytes
    ) -> bytes | None:
        return None  # subprocess was killed mid-run

    monkeypatch.setattr(
        ocr_mod, "_run_ocrmypdf_async", fake_run_returns_none
    )

    slug = await _login(client)
    att = await _make_pdf_attachment(client, slug, b"original")

    # Patch publish_extract *after* the upload-completion flow so
    # that publish (from mark_and_enqueue) doesn't pollute captured.
    captured: list[uuid.UUID] = []

    async def fake_publish_extract(aid: uuid.UUID) -> bool:
        captured.append(aid)
        return True

    monkeypatch.setattr(ocr_mod.queue, "publish_extract", fake_publish_extract)

    # Pre-seed status='running' to model the worker-loop's
    # mid-flight state.
    async with session_factory() as db:
        now = datetime.now(UTC)
        db.add(
            AttachmentProcessing(
                attachment_id=uuid.UUID(att["id"]),
                ocr_status="running",
                ocr_engine="ocrmypdf/x shelf/y",
                created_at=now,
                updated_at=now,
            )
        )
        await db.commit()

    await ocr_mod.ocr_attachment(att["id"])

    # Storage was NOT overwritten — the bytes at the storage key
    # are still the pre-OCR ones we wrote in _make_pdf_attachment.
    from shelf.models import Attachment

    async with session_factory() as db:
        row = await db.get(Attachment, uuid.UUID(att["id"]))
        assert row is not None
        body_res = await obstore.get_async(storage.get_store(), row.storage_key)
        body = bytes(await body_res.bytes_async())
        assert body == b"original"

    # Extract was NOT republished (cancel = no re-extract needed).
    assert captured == []


async def test_ocr_failure_marks_terminal(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``mark_failed_terminal`` is what the worker loop calls after
    redelivery cap; exercise it directly."""
    from shelf.db import session_factory
    from shelf.models import AttachmentProcessing
    from shelf.worker import ocr as ocr_mod

    async def fake_presign_upload(_key: str, **_: object) -> str:
        return "https://stub"

    monkeypatch.setattr(storage, "presign_upload", fake_presign_upload)

    slug = await _login(client)
    att = await _make_pdf_attachment(client, slug, b"x")

    await ocr_mod.mark_failed_terminal(att["id"])

    async with session_factory() as db:
        proc = await db.get(AttachmentProcessing, uuid.UUID(att["id"]))
        assert proc is not None
        assert proc.ocr_status == "failed"
        assert proc.ocr_engine is not None
        assert proc.ocr_engine.startswith("ocrmypdf/")


async def test_extract_auto_enqueues_ocr_when_needed(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A short PDF trips the chars/page heuristic; the extract worker
    should mark ``ocr_status='queued'`` AND publish to the OCR
    subject. We monkeypatch publish_ocr to capture the call."""
    from shelf.db import session_factory
    from shelf.models import AttachmentProcessing
    from shelf.worker import extract as extract_mod
    from tests.test_extraction import _build_pdf

    async def fake_presign_upload(_key: str, **_: object) -> str:
        return "https://stub"

    monkeypatch.setattr(storage, "presign_upload", fake_presign_upload)

    captured: list[uuid.UUID] = []

    async def fake_publish_ocr(aid: uuid.UUID) -> bool:
        captured.append(aid)
        return True

    monkeypatch.setattr(extract_mod.queue, "publish_ocr", fake_publish_ocr)

    slug = await _login(client)
    # Single-page short doc → chars_per_page < 100 → needs_ocr=True.
    att = await _make_pdf_attachment(client, slug, _build_pdf("hi"))

    await extract_mod.extract_attachment(att["id"])

    assert captured == [uuid.UUID(att["id"])]
    async with session_factory() as db:
        proc = await db.get(AttachmentProcessing, uuid.UUID(att["id"]))
        assert proc is not None
        assert proc.needs_ocr is True
        assert proc.ocr_status == "queued"


async def test_extract_does_not_loop_after_ocr_done(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If a previous OCR run already finished (ocr_status='done')
    and the doc still trips the heuristic, we DO NOT re-enqueue OCR
    — that's how we avoid loops on docs OCR can't fix."""
    from shelf.db import session_factory
    from shelf.models import AttachmentProcessing
    from shelf.worker import extract as extract_mod
    from tests.test_extraction import _build_pdf

    async def fake_presign_upload(_key: str, **_: object) -> str:
        return "https://stub"

    monkeypatch.setattr(storage, "presign_upload", fake_presign_upload)

    captured: list[uuid.UUID] = []

    async def fake_publish_ocr(aid: uuid.UUID) -> bool:
        captured.append(aid)
        return True

    monkeypatch.setattr(extract_mod.queue, "publish_ocr", fake_publish_ocr)

    slug = await _login(client)
    att = await _make_pdf_attachment(client, slug, _build_pdf("hi"))

    # Pre-seed ocr_status='done' before the extract runs.
    from datetime import UTC, datetime

    async with session_factory() as db:
        now = datetime.now(UTC)
        proc = AttachmentProcessing(
            attachment_id=uuid.UUID(att["id"]),
            ocr_status="done",
            ocr_engine="ocrmypdf",
            ocr_completed_at=now,
        )
        db.add(proc)
        await db.commit()

    await extract_mod.extract_attachment(att["id"])

    # Even though the heuristic still flags needs_ocr, we don't
    # re-enqueue; ocr_status stays 'done'.
    assert captured == []
    async with session_factory() as db:
        proc = await db.get(AttachmentProcessing, uuid.UUID(att["id"]))
        assert proc is not None
        assert proc.ocr_status == "done"
