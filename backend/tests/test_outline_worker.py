"""Phase C outline worker — derivation + chaining tests.

The font-heuristic engine itself (PyMuPDF font clustering, set_toc,
…) is exercised indirectly: ``_build_outline`` is patched out so the
test fixtures can use opaque bytes without producing a real PDF.
What we cover here is the orchestration layer — fetching the right
parent (latest OCR vs. original), writing a NEW derivation row
(never overwriting), persisting outline_json + status, and the
no-headings short-circuit.
"""

from __future__ import annotations

import uuid
from typing import Any

import obstore
import pytest
from httpx import AsyncClient
from obstore.store import MemoryStore
from sqlalchemy import select

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


async def test_outline_writes_derivation_and_persists_json(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round-trip: queue an outline job, the worker derives an
    outlined PDF at a NEW key, inserts an attachment_derivation row
    with kind='outline', and persists the nested outline_json."""
    from shelf.db import session_factory
    from shelf.models import AttachmentDerivation, AttachmentProcessing
    from shelf.worker import outline as outline_mod

    async def fake_presign_upload(_key: str, **_: object) -> str:
        return "https://stub"

    monkeypatch.setattr(storage, "presign_upload", fake_presign_upload)

    slug = await _login(client)
    att = await _make_pdf_attachment(client, slug, b"original-pdf-bytes")
    aid = uuid.UUID(att["id"])

    new_bytes = b"outlined-pdf-bytes"
    fake_outline = [
        {
            "title": "Chapter 1",
            "page": 1,
            "items": [
                {"title": "1.1 Background", "page": 3, "items": []},
            ],
        },
        {"title": "Chapter 2", "page": 10, "items": []},
    ]
    monkeypatch.setattr(
        outline_mod, "_build_outline", lambda _body: (new_bytes, fake_outline)
    )

    await outline_mod.outline_attachment(att["id"])

    # Original key untouched.
    from shelf.models import Attachment

    async with session_factory() as db:
        row = await db.get(Attachment, aid)
        assert row is not None
        res = await obstore.get_async(storage.get_store(), row.storage_key)
        assert bytes(await res.bytes_async()) == b"original-pdf-bytes"

    # Derivation row + its blob exist.
    async with session_factory() as db:
        deriv = (
            await db.execute(
                select(AttachmentDerivation).where(
                    AttachmentDerivation.attachment_id == aid,
                    AttachmentDerivation.kind == "outline",
                )
            )
        ).scalar_one()
        assert deriv.parent_storage_key == row.storage_key
        assert deriv.engine.startswith("pymupdf-fontheuristic")
        res2 = await obstore.get_async(storage.get_store(), deriv.storage_key)
        assert bytes(await res2.bytes_async()) == new_bytes

    async with session_factory() as db:
        proc = await db.get(AttachmentProcessing, aid)
        assert proc is not None
        assert proc.outline_status == "done"
        assert proc.outline_completed_at is not None
        assert proc.outline_json == fake_outline


async def test_outline_uses_latest_ocr_derivation_as_parent(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When an OCR derivation exists, outline reads from THAT key,
    not from the attachment's original. The new derivation's
    parent_storage_key reflects the OCR derivation it built on."""
    from datetime import UTC, datetime

    from shelf.db import session_factory
    from shelf.models import AttachmentDerivation
    from shelf.worker import outline as outline_mod

    async def fake_presign_upload(_key: str, **_: object) -> str:
        return "https://stub"

    monkeypatch.setattr(storage, "presign_upload", fake_presign_upload)

    slug = await _login(client)
    att = await _make_pdf_attachment(client, slug, b"original")
    aid = uuid.UUID(att["id"])

    # Plant an OCR derivation as if a previous OCR run had finished.
    ocr_key = f"items/x/attachments/{aid}/derived/ocr-deadbeef.pdf"
    await obstore.put_async(storage.get_store(), ocr_key, b"ocrd-bytes")
    async with session_factory() as db:
        from shelf.models import Attachment

        row = await db.get(Attachment, aid)
        assert row is not None
        db.add(
            AttachmentDerivation(
                attachment_id=aid,
                kind="ocr",
                storage_key=ocr_key,
                parent_storage_key=row.storage_key,
                engine="ocrmypdf/test",
                created_at=datetime.now(UTC),
            )
        )
        await db.commit()

    seen: list[bytes] = []

    def fake_build(body: bytes) -> tuple[bytes, list[dict[str, Any]]]:
        seen.append(body)
        return b"outlined", [{"title": "X", "page": 1, "items": []}]

    monkeypatch.setattr(outline_mod, "_build_outline", fake_build)

    await outline_mod.outline_attachment(att["id"])

    # Worker fed the OCR'd bytes into _build_outline (proves the
    # latest_derivation lookup wired through correctly).
    assert seen == [b"ocrd-bytes"]

    # New outline derivation rooted on the OCR key, not the original.
    async with session_factory() as db:
        deriv = (
            await db.execute(
                select(AttachmentDerivation).where(
                    AttachmentDerivation.attachment_id == aid,
                    AttachmentDerivation.kind == "outline",
                )
            )
        ).scalar_one()
        assert deriv.parent_storage_key == ocr_key


async def test_outline_no_headings_marks_done_without_derivation(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the heuristic finds no headings, the worker writes
    outline_status='done' with outline_json=[] and DOES NOT create a
    derivation row — there's no value in a no-op duplicate of the
    parent PDF."""
    from shelf.db import session_factory
    from shelf.models import AttachmentDerivation, AttachmentProcessing
    from shelf.worker import outline as outline_mod

    async def fake_presign_upload(_key: str, **_: object) -> str:
        return "https://stub"

    monkeypatch.setattr(storage, "presign_upload", fake_presign_upload)

    slug = await _login(client)
    att = await _make_pdf_attachment(client, slug, b"x")
    aid = uuid.UUID(att["id"])

    monkeypatch.setattr(
        outline_mod, "_build_outline", lambda _body: (None, [])
    )

    await outline_mod.outline_attachment(att["id"])

    async with session_factory() as db:
        proc = await db.get(AttachmentProcessing, aid)
        assert proc is not None
        assert proc.outline_status == "done"
        assert proc.outline_json == []
        # No derivation row.
        rows = (
            await db.execute(
                select(AttachmentDerivation).where(
                    AttachmentDerivation.attachment_id == aid
                )
            )
        ).scalars().all()
        assert rows == []


async def test_outline_failure_marks_terminal(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from shelf.db import session_factory
    from shelf.models import AttachmentProcessing
    from shelf.worker import outline as outline_mod

    async def fake_presign_upload(_key: str, **_: object) -> str:
        return "https://stub"

    monkeypatch.setattr(storage, "presign_upload", fake_presign_upload)

    slug = await _login(client)
    att = await _make_pdf_attachment(client, slug, b"x")

    await outline_mod.mark_failed_terminal(att["id"])

    async with session_factory() as db:
        proc = await db.get(AttachmentProcessing, uuid.UUID(att["id"]))
        assert proc is not None
        assert proc.outline_status == "failed"
        assert proc.outline_engine is not None
        assert proc.outline_engine.startswith("pymupdf-fontheuristic")


def test_normalize_toc_levels_handles_gaps_and_starts_at_one() -> None:
    """Pure-function test of the level normalizer — the bug PyMuPDF's
    set_toc trips on (gap between 3 and 5, no level-1 row) is the
    one that lost an OCR run before this work landed."""
    from shelf.worker.outline import _normalize_toc_levels

    assert _normalize_toc_levels([]) == []
    # Gap between 3 and 5 → compressed to 1, 2, 3.
    assert _normalize_toc_levels(
        [[2, "a", 1], [3, "b", 2], [5, "c", 3]]
    ) == [[1, "a", 1], [2, "b", 2], [3, "c", 3]]
    # First row above level 1 → forced to 1.
    assert _normalize_toc_levels([[3, "a", 1]]) == [[1, "a", 1]]
    # Already valid input passes through unchanged.
    assert _normalize_toc_levels(
        [[1, "a", 1], [2, "b", 2], [1, "c", 3]]
    ) == [[1, "a", 1], [2, "b", 2], [1, "c", 3]]


def test_build_nested_outline_assembles_tree() -> None:
    """Stack-based tree build: h1/h2/h2/h3/h1 → 2 top-level chapters,
    second chapter has two children, second child has one grandchild."""
    from shelf.worker.outline import _build_nested

    flat = [
        [1, "Chapter 1", 1],
        [2, "1.1 Intro", 3],
        [2, "1.2 Approach", 5],
        [3, "1.2.1 Detail", 6],
        [1, "Chapter 2", 10],
    ]
    out = _build_nested(flat)
    assert len(out) == 2
    assert out[0]["title"] == "Chapter 1"
    assert len(out[0]["items"]) == 2
    assert out[0]["items"][1]["title"] == "1.2 Approach"
    assert len(out[0]["items"][1]["items"]) == 1
    assert out[0]["items"][1]["items"][0]["title"] == "1.2.1 Detail"
    assert out[1]["title"] == "Chapter 2"


async def test_extract_auto_enqueues_outline_when_long_doc_lacks_toc(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An attachment-processing run with page_count >= 30 + toc_count
    < 3 trips the heuristic; extract.py should set
    outline_status='queued' and publish to the outline subject."""
    from shelf.db import session_factory
    from shelf.models import AttachmentProcessing
    from shelf.services import pdf_quality
    from shelf.worker import extract as extract_mod

    async def fake_presign_upload(_key: str, **_: object) -> str:
        return "https://stub"

    monkeypatch.setattr(storage, "presign_upload", fake_presign_upload)

    captured_outline: list[uuid.UUID] = []

    async def fake_publish_outline(aid: uuid.UUID) -> bool:
        captured_outline.append(aid)
        return True

    async def fake_publish_ocr(_aid: uuid.UUID) -> bool:
        return True

    monkeypatch.setattr(
        extract_mod.queue, "publish_outline", fake_publish_outline
    )
    monkeypatch.setattr(extract_mod.queue, "publish_ocr", fake_publish_ocr)

    # (page_number, text, width_pts, height_pts) — matches the shape
    # _extract_pages_and_toc returns since page dims were added.
    fake_pages = [(i, "page text " * 100, 612.0, 792.0) for i in range(1, 41)]

    def fake_extract_pages_and_toc(_body: bytes) -> tuple[list, int]:
        return fake_pages, 0

    monkeypatch.setattr(
        extract_mod, "_extract_pages_and_toc", fake_extract_pages_and_toc
    )

    slug = await _login(client)
    att = await _make_pdf_attachment(client, slug, b"placeholder")

    await extract_mod.extract_attachment(att["id"])

    assert captured_outline == [uuid.UUID(att["id"])]
    async with session_factory() as db:
        proc = await db.get(AttachmentProcessing, uuid.UUID(att["id"]))
        assert proc is not None
        assert proc.needs_outline is True
        assert proc.outline_status == "queued"

    assert pdf_quality.assess_outline_need(0, 40) is True
