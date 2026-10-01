"""Render-to-PDF (convert) worker — routing, round-trip and failure.

Gotenberg is never called: ``_office_to_pdf`` is patched to return a
canned PDF, the same way the OCR tests stand in for Tesseract. The
image route runs for real, since PyMuPDF is in every environment.
"""

from __future__ import annotations

import contextlib
import uuid
from typing import Any

import obstore
import pytest
from httpx import AsyncClient
from obstore.store import MemoryStore

from shelf.services import conversion, queue, storage

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@pytest.fixture(autouse=True)
def memory_store(monkeypatch: pytest.MonkeyPatch) -> Any:
    storage._store = MemoryStore()

    async def fake_presign(key: str, **_: object) -> str:
        return f"https://stub/{key}"

    monkeypatch.setattr(storage, "presign_upload", fake_presign)
    monkeypatch.setattr(storage, "presign_download", fake_presign)
    yield
    storage.reset_store()


@pytest.fixture
def published(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[uuid.UUID]]:
    """Every job published, by subject short name."""
    out: dict[str, list[uuid.UUID]] = {"extract": [], "convert": []}

    def capture(name: str) -> Any:
        async def fake(aid: uuid.UUID) -> bool:
            out[name].append(aid)
            return True

        return fake

    monkeypatch.setattr(queue, "publish_extract", capture("extract"))
    monkeypatch.setattr(queue, "publish_convert", capture("convert"))
    return out


async def _login(client: AsyncClient, email: str = "alice@example.com") -> str:
    await client.post("/auth/dev-login", json={"email": email})
    me = (await client.get("/api/me")).json()
    return f"u-{me['id'].replace('-', '')[:8]}"


async def _upload(
    client: AsyncClient, slug: str, filename: str, content_type: str, body: bytes
) -> dict:
    """Register, store the bytes, complete — the SPA's upload flow."""
    from shelf.db import session_factory
    from shelf.models import Attachment

    item = (
        await client.post(
            f"/api/spaces/{slug}/items",
            json={"item_type": "document", "data": {"title": "T"}},
        )
    ).json()
    att = (
        await client.post(
            f"/api/items/{item['id']}/attachments",
            json={"filename": filename, "content_type": content_type},
        )
    ).json()["attachment"]
    async with session_factory() as db:
        row = await db.get(Attachment, uuid.UUID(att["id"]))
        assert row is not None
        await obstore.put_async(storage.get_store(), row.storage_key, body)
    r = await client.post(f"/api/attachments/{att['id']}/complete")
    assert r.status_code == 200, r.text
    return dict(r.json())


# ── routing ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("filename", "content_type", "route"),
    [
        ("report.docx", DOCX, "office"),
        # Browsers send "" or octet-stream for plenty of office files;
        # the extension decides.
        ("report.docx", "application/octet-stream", "office"),
        ("Slides.PPTX", "", "office"),
        ("old.doc", "application/msword", "office"),
        ("sheet.xlsx", "", "office"),
        ("notes.odt", "", "office"),
        ("letter.rtf", "text/rtf", "office"),
        ("scan.tiff", "", "image"),
        ("photo.JPG", "image/jpeg", "image"),
        # No extension: the declared type is all there is.
        ("upload", DOCX, "office"),
        ("upload", "image/png", "image"),
        # Already a PDF, or something shelf doesn't render.
        ("paper.pdf", "application/pdf", None),
        ("paper.pdf", "application/octet-stream", None),
        ("data.zip", "application/zip", None),
        ("upload", "application/octet-stream", None),
    ],
)
def test_route_for(filename: str, content_type: str, route: str | None) -> None:
    assert conversion.route_for(filename, content_type) == route


# ── upload → convert → extract ───────────────────────────────────────────


async def test_docx_upload_queues_conversion(
    client: AsyncClient, published: dict[str, list[uuid.UUID]]
) -> None:
    from shelf.db import session_factory
    from shelf.models import Attachment, AttachmentProcessing

    slug = await _login(client)
    att = await _upload(client, slug, "report.docx", DOCX, b"PK\x03\x04docx")
    aid = uuid.UUID(att["id"])

    assert att["pdf_status"] == "converting"
    assert published == {"extract": [], "convert": [aid]}
    async with session_factory() as db:
        row = await db.get(Attachment, aid)
        proc = await db.get(AttachmentProcessing, aid)
        assert row is not None and proc is not None
        # Pending, not skipped: text arrives once the PDF does.
        assert row.extraction_status == "pending"
        assert proc.convert_status == "queued"


async def test_pdf_upload_is_native_and_not_converted(
    client: AsyncClient, published: dict[str, list[uuid.UUID]]
) -> None:
    slug = await _login(client)
    att = await _upload(client, slug, "p.pdf", "application/pdf", b"%PDF-1.7\n")
    assert att["pdf_status"] == "native"
    assert published["convert"] == []
    assert published["extract"] == [uuid.UUID(att["id"])]


async def test_unconvertible_upload_has_no_pdf(
    client: AsyncClient, published: dict[str, list[uuid.UUID]]
) -> None:
    slug = await _login(client)
    att = await _upload(client, slug, "data.zip", "application/zip", b"PK")
    assert att["pdf_status"] is None
    assert published == {"extract": [], "convert": []}


async def test_convert_roundtrip_then_extract(
    client: AsyncClient,
    published: dict[str, list[uuid.UUID]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The worker renders the upload, stores it as a ``convert``
    derivation beside the untouched original, and chains extraction —
    which then reads the rendering's text."""
    from sqlalchemy import select

    from shelf.db import session_factory
    from shelf.models import Attachment, AttachmentDerivation, AttachmentProcessing
    from shelf.worker import convert as convert_mod
    from shelf.worker import extract as extract_mod
    from tests.test_extraction import _build_pdf

    rendered = _build_pdf("Quarterly report rendered from Word " * 4)
    seen: list[tuple[str, str | None]] = []

    async def fake_office(body: bytes, filename: str, ct: str | None) -> bytes:
        seen.append((filename, ct))
        return rendered

    monkeypatch.setattr(convert_mod, "_office_to_pdf", fake_office)

    slug = await _login(client)
    original = b"PK\x03\x04docx-bytes"
    att = await _upload(client, slug, "report.docx", DOCX, original)
    aid = uuid.UUID(att["id"])

    await convert_mod.convert_attachment(aid)

    assert seen == [("report.docx", DOCX)]
    assert published["extract"] == [aid]
    async with session_factory() as db:
        row = await db.get(Attachment, aid)
        assert row is not None
        # The upload itself is untouched.
        res = await obstore.get_async(storage.get_store(), row.storage_key)
        assert bytes(await res.bytes_async()) == original
        deriv = (
            await db.execute(
                select(AttachmentDerivation).where(
                    AttachmentDerivation.attachment_id == aid
                )
            )
        ).scalar_one()
        assert deriv.kind == "convert"
        assert deriv.parent_storage_key == row.storage_key
        assert deriv.engine.startswith("gotenberg/")
        res = await obstore.get_async(storage.get_store(), deriv.storage_key)
        assert bytes(await res.bytes_async()) == rendered
        proc = await db.get(AttachmentProcessing, aid)
        assert proc is not None
        assert proc.convert_status == "done"
        assert proc.convert_completed_at is not None
        assert proc.convert_error is None

    await extract_mod.extract_attachment(aid)
    async with session_factory() as db:
        row = await db.get(Attachment, aid)
        assert row is not None
        assert row.extraction_status == "extracted"
        assert row.text_content and "Quarterly report" in row.text_content
        # The hash is of the bytes as uploaded, not of the rendering.
        import hashlib

        assert row.sha256 == hashlib.sha256(original).hexdigest()

    # The reader gets the rendering; the listing says it can open it.
    listed = (await client.get(f"/api/items/{att['item_id']}/attachments")).json()
    assert listed[0]["pdf_status"] == "converted"
    url = (await client.get(f"/api/attachments/{aid}/download")).json()["url"]
    assert url.endswith(deriv.storage_key)
    versions = (await client.get(f"/api/attachments/{aid}/derivations")).json()
    assert versions["current_version"] == str(deriv.id)


async def test_extract_before_conversion_waits(
    client: AsyncClient, published: dict[str, list[uuid.UUID]]
) -> None:
    """An extract job that beats the conversion leaves the row pending
    rather than marking it skipped — the convert worker re-publishes."""
    from shelf.db import session_factory
    from shelf.models import Attachment
    from shelf.worker import extract as extract_mod

    slug = await _login(client)
    att = await _upload(client, slug, "report.docx", DOCX, b"PK")
    aid = uuid.UUID(att["id"])
    await extract_mod.extract_attachment(aid)
    async with session_factory() as db:
        row = await db.get(Attachment, aid)
        assert row is not None
        assert row.extraction_status == "pending"


async def test_image_converts_in_process(
    client: AsyncClient, published: dict[str, list[uuid.UUID]]
) -> None:
    """Images go through PyMuPDF, not Gotenberg: one page shaped like
    the image, no text layer (extraction then flags it for OCR)."""
    import fitz
    from sqlalchemy import select

    from shelf.db import session_factory
    from shelf.models import AttachmentDerivation
    from shelf.worker import convert as convert_mod

    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 40, 30), False)
    pix.clear_with(200)
    png = pix.tobytes("png")

    slug = await _login(client)
    att = await _upload(client, slug, "scan.png", "image/png", png)
    aid = uuid.UUID(att["id"])
    await convert_mod.convert_attachment(aid)

    async with session_factory() as db:
        deriv = (
            await db.execute(
                select(AttachmentDerivation).where(
                    AttachmentDerivation.attachment_id == aid
                )
            )
        ).scalar_one()
        assert deriv.engine.startswith("pymupdf/")
        res = await obstore.get_async(storage.get_store(), deriv.storage_key)
        pdf = bytes(await res.bytes_async())
    doc = fitz.open(stream=pdf, filetype="pdf")
    assert doc.page_count == 1
    # Sized from the image's DPI (96 when it records none), so only the
    # proportions are fixed.
    assert doc[0].rect.width / doc[0].rect.height == pytest.approx(40 / 30)


async def test_download_button_returns_the_upload(
    client: AsyncClient,
    published: dict[str, list[uuid.UUID]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``/file`` with no version is the file as uploaded — the .docx under
    its own name — even once a rendering exists."""
    from shelf.worker import convert as convert_mod
    from tests.test_extraction import _build_pdf

    async def fake_office(*_: object) -> bytes:
        return _build_pdf("x")

    monkeypatch.setattr(convert_mod, "_office_to_pdf", fake_office)
    seen: list[str] = []

    async def fake_internal(key: str, **_: object) -> str:
        seen.append(key)
        return "https://stub.invalid/x"

    monkeypatch.setattr(storage, "presign_download_internal", fake_internal)

    slug = await _login(client)
    att = await _upload(client, slug, "report.docx", DOCX, b"PK")
    await convert_mod.convert_attachment(att["id"])

    from shelf.db import session_factory
    from shelf.models import Attachment

    async with session_factory() as db:
        row = await db.get(Attachment, uuid.UUID(att["id"]))
        assert row is not None
    # The upstream fetch itself fails (no storage behind the stub URL);
    # what matters is which key was signed.
    with contextlib.suppress(Exception):
        await client.get(f"/api/attachments/{att['id']}/file")
    assert seen == [row.storage_key]


async def test_failure_keeps_reason_and_terminal_marks_failed(
    client: AsyncClient,
    published: dict[str, list[uuid.UUID]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from shelf.db import session_factory
    from shelf.models import Attachment, AttachmentProcessing
    from shelf.worker import convert as convert_mod

    async def broken(*_: object) -> bytes:
        raise RuntimeError("gotenberg 400: file is password-protected")

    monkeypatch.setattr(convert_mod, "_office_to_pdf", broken)

    slug = await _login(client)
    att = await _upload(client, slug, "locked.docx", DOCX, b"PK")
    aid = uuid.UUID(att["id"])

    with pytest.raises(RuntimeError):
        await convert_mod.convert_attachment(aid)
    await convert_mod.mark_failed_terminal(aid)

    async with session_factory() as db:
        proc = await db.get(AttachmentProcessing, aid)
        row = await db.get(Attachment, aid)
        assert proc is not None and row is not None
        assert proc.convert_status == "failed"
        assert proc.convert_error is not None
        assert "password-protected" in proc.convert_error
        assert row.extraction_status == "skipped"
    assert published["extract"] == []

    listed = (await client.get(f"/api/items/{att['item_id']}/attachments")).json()
    assert listed[0]["pdf_status"] == "failed"
    assert "password-protected" in listed[0]["convert_error"]


async def test_rescan_retries_a_failed_conversion(
    client: AsyncClient, published: dict[str, list[uuid.UUID]]
) -> None:
    from shelf.db import session_factory
    from shelf.models import AttachmentProcessing
    from shelf.worker import convert as convert_mod

    slug = await _login(client)
    att = await _upload(client, slug, "report.docx", DOCX, b"PK")
    aid = uuid.UUID(att["id"])
    await convert_mod.mark_failed_terminal(aid)

    r = await client.post(f"/api/me/extraction/attachments/{aid}/rescan")
    assert r.status_code == 200, r.text
    assert published["convert"] == [aid, aid]
    async with session_factory() as db:
        proc = await db.get(AttachmentProcessing, aid)
        assert proc is not None
        assert proc.convert_status == "queued"
