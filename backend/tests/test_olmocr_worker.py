"""olmOCR (GPU) consumer tests — same structure as test_ocr_worker
but exercising the SUBJECT_OCR_GPU path. We never actually run
torch / Qwen here; ``_run_olmocr_async`` is patched to return canned
bytes so CI doesn't need a GPU or model weights.
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


async def test_olmocr_roundtrip_writes_derivation_and_chains_jobs(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: queue a GPU OCR job, the worker writes the OCR'd
    PDF to a NEW derivation key (NOT overwriting the original),
    inserts an attachment_derivation row, marks ocr_status='done',
    and chains both the extract and outline jobs."""
    from sqlalchemy import select

    from shelf.db import session_factory
    from shelf.models import (
        Attachment,
        AttachmentDerivation,
        AttachmentProcessing,
    )
    from shelf.worker import olmocr as olmocr_mod

    async def fake_presign_upload(_key: str, **_: object) -> str:
        return "https://stub"

    monkeypatch.setattr(storage, "presign_upload", fake_presign_upload)

    slug = await _login(client)
    att = await _make_pdf_attachment(client, slug, b"original-pdf-bytes")

    new_bytes = b"olmocr-pdf-bytes"

    async def fake_run(_aid: uuid.UUID, _body: bytes) -> bytes:
        return new_bytes

    monkeypatch.setattr(olmocr_mod, "_run_olmocr_async", fake_run)

    captured_extract: list[uuid.UUID] = []
    captured_outline: list[uuid.UUID] = []

    async def fake_publish_extract(aid: uuid.UUID) -> bool:
        captured_extract.append(aid)
        return True

    async def fake_publish_outline(aid: uuid.UUID) -> bool:
        captured_outline.append(aid)
        return True

    monkeypatch.setattr(
        olmocr_mod.queue, "publish_extract", fake_publish_extract
    )
    monkeypatch.setattr(
        olmocr_mod.queue, "publish_outline", fake_publish_outline
    )

    await olmocr_mod.olmocr_attachment(att["id"])

    # Original key untouched.
    async with session_factory() as db:
        row = await db.get(Attachment, uuid.UUID(att["id"]))
        assert row is not None
        res = await obstore.get_async(storage.get_store(), row.storage_key)
        body = bytes(await res.bytes_async())
        assert body == b"original-pdf-bytes"

    # New OCR derivation row + the OCR bytes live at its key.
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
        assert deriv.engine.startswith("olmocr/")
        res2 = await obstore.get_async(storage.get_store(), deriv.storage_key)
        assert bytes(await res2.bytes_async()) == new_bytes

    async with session_factory() as db:
        proc = await db.get(AttachmentProcessing, uuid.UUID(att["id"]))
        assert proc is not None
        assert proc.ocr_status == "done"
        assert proc.ocr_engine is not None
        assert proc.ocr_engine.startswith("olmocr/")
        assert proc.ocr_completed_at is not None
        # Outline auto-chained, marked queued so the SPA shows it.
        assert proc.outline_status == "queued"

    assert captured_extract == [uuid.UUID(att["id"])]
    assert captured_outline == [uuid.UUID(att["id"])]


async def test_olmocr_cancelled_pre_pickup_short_circuits(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If ocr_status='cancelled' before the worker picks up, we
    return without spawning the runner and without rewriting
    storage."""
    from datetime import UTC, datetime

    from shelf.db import session_factory
    from shelf.models import Attachment, AttachmentProcessing
    from shelf.worker import olmocr as olmocr_mod

    async def fake_presign_upload(_key: str, **_: object) -> str:
        return "https://stub"

    monkeypatch.setattr(storage, "presign_upload", fake_presign_upload)

    slug = await _login(client)
    att = await _make_pdf_attachment(client, slug, b"untouched-bytes")

    async with session_factory() as db:
        now = datetime.now(UTC)
        db.add(
            AttachmentProcessing(
                attachment_id=uuid.UUID(att["id"]),
                ocr_status="cancelled",
                created_at=now,
                updated_at=now,
            )
        )
        await db.commit()

    async def boom(*_: object) -> bytes:
        raise AssertionError("runner must not be invoked when cancelled")

    monkeypatch.setattr(olmocr_mod, "_run_olmocr_async", boom)

    await olmocr_mod.olmocr_attachment(att["id"])

    async with session_factory() as db:
        row = await db.get(Attachment, uuid.UUID(att["id"]))
        assert row is not None
        res = await obstore.get_async(storage.get_store(), row.storage_key)
        body = bytes(await res.bytes_async())
        assert body == b"untouched-bytes"


async def test_olmocr_failure_marks_terminal(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from shelf.db import session_factory
    from shelf.models import AttachmentProcessing
    from shelf.worker import olmocr as olmocr_mod

    async def fake_presign_upload(_key: str, **_: object) -> str:
        return "https://stub"

    monkeypatch.setattr(storage, "presign_upload", fake_presign_upload)

    slug = await _login(client)
    att = await _make_pdf_attachment(client, slug, b"x")

    await olmocr_mod.mark_failed_terminal(att["id"])

    async with session_factory() as db:
        proc = await db.get(AttachmentProcessing, uuid.UUID(att["id"]))
        assert proc is not None
        assert proc.ocr_status == "failed"
        assert proc.ocr_engine is not None
        assert proc.ocr_engine.startswith("olmocr/")


async def test_trigger_ocr_gpu_endpoint_publishes_to_gpu_subject(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hitting the trigger endpoint sets status='queued' and
    publishes to the *gpu* subject, not the cpu one."""
    from shelf.api import processing as proc_api
    from shelf.db import session_factory
    from shelf.models import AttachmentProcessing

    captured_cpu: list[uuid.UUID] = []
    captured_gpu: list[uuid.UUID] = []

    async def fake_publish_ocr(aid: uuid.UUID) -> bool:
        captured_cpu.append(aid)
        return True

    async def fake_publish_ocr_gpu(aid: uuid.UUID) -> bool:
        captured_gpu.append(aid)
        return True

    monkeypatch.setattr(proc_api.queue, "publish_ocr", fake_publish_ocr)
    monkeypatch.setattr(
        proc_api.queue, "publish_ocr_gpu", fake_publish_ocr_gpu
    )

    async def fake_presign_upload(_key: str, **_: object) -> str:
        return "https://stub"

    monkeypatch.setattr(storage, "presign_upload", fake_presign_upload)

    slug = await _login(client)
    att = await _make_pdf_attachment(client, slug, b"x")

    r = await client.post(
        f"/api/attachments/{att['id']}/processing/ocr_gpu"
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["job"] == "ocr"
    assert body["enqueued"] is True

    assert captured_cpu == []
    assert captured_gpu == [uuid.UUID(att["id"])]

    async with session_factory() as db:
        proc = await db.get(AttachmentProcessing, uuid.UUID(att["id"]))
        assert proc is not None
        assert proc.ocr_status == "queued"
