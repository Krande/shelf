"""Extraction worker + ?scope=fulltext search.

The worker logic is tested directly against a MemoryStore-backed
`storage` so the test doesn't need NATS or a real bucket. Search
tests round-trip through the API and rely on the migration's
generated `tsv` column updating on commit.
"""

from __future__ import annotations

import io
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


def _build_pdf(text: str) -> bytes:
    """Render a one-page PDF with `text` on it. pypdf reads what
    pypdf writes, so this is a self-consistent round-trip — enough
    to exercise the extract path without shipping a binary fixture
    in the repo.
    """
    from pypdf import PdfWriter
    from pypdf.generic import (
        ArrayObject,
        DictionaryObject,
        FloatObject,
        NameObject,
        NumberObject,
    )

    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    # Minimal content stream that draws `text` at (72, 720).
    escaped = (
        text.replace("\\", "\\\\")
        .replace("(", "\\(")
        .replace(")", "\\)")
    )
    cs = (
        f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET"
    ).encode("latin-1")
    from pypdf.generic import StreamObject

    stream = StreamObject()
    stream._data = cs
    # Attach a basic font so the text Tj is decodable. pypdf's writer
    # has helpers but the lowest-friction path is to inject a
    # Type1 font dict directly.
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
            # WinAnsi maps the latin-1 bytes we wrote into the
            # content stream so pypdf can decode them on read-back.
            NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
        }
    )
    font_ref = writer._add_object(font)
    resources = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {NameObject("/F1"): font_ref}
            )
        }
    )
    page[NameObject("/Resources")] = resources
    page[NameObject("/Contents")] = writer._add_object(stream)
    page[NameObject("/MediaBox")] = ArrayObject(
        [
            NumberObject(0),
            NumberObject(0),
            FloatObject(612),
            FloatObject(792),
        ]
    )
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _items(payload: Any) -> list[Any]:
    """Unwrap the paginated ``{items, total}`` list response."""
    return payload["items"]


async def _login(client: AsyncClient, email: str = "alice@example.com") -> str:
    r = await client.post("/auth/dev-login", json={"email": email})
    assert r.status_code == 200, r.text
    me = await client.get("/api/me")
    return f"u-{me.json()['id'].replace('-', '')[:8]}"


async def test_extract_attachment_writes_text_and_status(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: register attachment, drop a PDF body in the
    MemoryStore under its key, run extract, expect status=extracted
    and text_chars > threshold."""
    import obstore

    from shelf.worker import extract as extract_mod

    async def fake_presign_upload(_key: str, **_: object) -> str:
        return "https://stub"

    monkeypatch.setattr(storage, "presign_upload", fake_presign_upload)

    slug = await _login(client)
    item = (
        await client.post(
            f"/api/spaces/{slug}/items",
            json={"item_type": "document", "data": {"title": "Doc"}},
        )
    ).json()
    att = (
        await client.post(
            f"/api/items/{item['id']}/attachments",
            json={"filename": "p.pdf", "content_type": "application/pdf"},
        )
    ).json()["attachment"]
    await client.post(f"/api/attachments/{att['id']}/complete")

    # Drop a real PDF body at the storage key the registration
    # picked. Look it up off the row.
    from shelf.db import session_factory
    from shelf.models import Attachment

    async with session_factory() as db:
        row = await db.get(Attachment, uuid.UUID(att["id"]))
        assert row is not None
        key = row.storage_key

    # Stay above the EMPTY_THRESHOLD_CHARS (50) so the row lands in
    # `extracted`, not `empty`.
    body = _build_pdf(
        "hello fulltext search world. shelf reader extraction worker test body."
    )
    await obstore.put_async(storage.get_store(), key, body)

    await extract_mod.extract_attachment(att["id"])

    async with session_factory() as db:
        row = await db.get(Attachment, uuid.UUID(att["id"]))
        assert row is not None
        assert row.extraction_status == "extracted"
        assert (row.text_chars or 0) >= 10
        assert "fulltext" in (row.text_content or "")
        assert isinstance(row.extracted_at, datetime)

    # Phase A: a quality-assessment row is written alongside.
    from shelf.models import AttachmentProcessing

    async with session_factory() as db:
        proc = await db.get(AttachmentProcessing, uuid.UUID(att["id"]))
        assert proc is not None
        assert proc.assessed_at is not None
        assert proc.page_count == 1
        assert proc.text_chars and proc.text_chars > 0
        # Test PDF is short, so chars/page falls below the threshold —
        # the heuristic flags it for OCR. That's the conservative side
        # of the trade-off and is exactly what we want.
        assert isinstance(proc.needs_ocr, bool)
        # Single short page → no outline expected.
        assert proc.needs_outline is False
        assert proc.toc_entry_count == 0

    # Per-page rows are what the snippet endpoint queries against.
    from sqlalchemy import select

    from shelf.models import AttachmentPage

    async with session_factory() as db:
        pages = (
            await db.execute(
                select(AttachmentPage).where(
                    AttachmentPage.attachment_id == uuid.UUID(att["id"])
                )
            )
        ).scalars().all()
        assert len(pages) >= 1
        assert pages[0].page_number == 1
        assert "fulltext" in pages[0].text


async def test_extract_marks_skipped_for_non_pdf(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
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
    att = (
        await client.post(
            f"/api/items/{item['id']}/attachments",
            json={"filename": "n.txt", "content_type": "text/plain"},
        )
    ).json()["attachment"]
    await client.post(f"/api/attachments/{att['id']}/complete")

    from shelf.db import session_factory
    from shelf.models import Attachment
    from shelf.worker import extract as extract_mod

    await extract_mod.extract_attachment(att["id"])
    async with session_factory() as db:
        row = await db.get(Attachment, uuid.UUID(att["id"]))
        assert row is not None
        assert row.extraction_status == "skipped"


async def test_complete_publishes_extract_for_pdf(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The /complete handler should call queue.publish_extract
    exactly once for a PDF attachment, and not at all for others."""
    from shelf.services import queue as queue_svc

    calls: list[uuid.UUID] = []

    async def fake_publish(att_id: uuid.UUID) -> bool:
        calls.append(att_id)
        return True

    monkeypatch.setattr(queue_svc, "publish_extract", fake_publish)

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

    pdf = (
        await client.post(
            f"/api/items/{item['id']}/attachments",
            json={"filename": "p.pdf", "content_type": "application/pdf"},
        )
    ).json()["attachment"]
    txt = (
        await client.post(
            f"/api/items/{item['id']}/attachments",
            json={"filename": "n.txt", "content_type": "text/plain"},
        )
    ).json()["attachment"]

    await client.post(f"/api/attachments/{pdf['id']}/complete")
    await client.post(f"/api/attachments/{txt['id']}/complete")

    assert calls == [uuid.UUID(pdf["id"])]


async def test_fulltext_scope_finds_pdf_body_match(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """List items with ?q=&scope=fulltext should return items whose
    attachment body contains the term, even when the item metadata
    doesn't mention it."""
    async def fake_presign_upload(_key: str, **_: object) -> str:
        return "https://stub"

    monkeypatch.setattr(storage, "presign_upload", fake_presign_upload)

    slug = await _login(client)
    item = (
        await client.post(
            f"/api/spaces/{slug}/items",
            json={
                "item_type": "document",
                "data": {"title": "Boring metadata"},
            },
        )
    ).json()
    att = (
        await client.post(
            f"/api/items/{item['id']}/attachments",
            json={"filename": "p.pdf", "content_type": "application/pdf"},
        )
    ).json()["attachment"]
    await client.post(f"/api/attachments/{att['id']}/complete")

    # Inject one per-page row — the fulltext scope queries
    # attachment_pages.tsv now, not the legacy attachments.tsv.
    from shelf.db import session_factory
    from shelf.models import Attachment, AttachmentPage

    async with session_factory() as db:
        row = await db.get(Attachment, uuid.UUID(att["id"]))
        assert row is not None
        row.text_content = "the rare unicorn paragraph lives here"
        row.text_chars = len(row.text_content)
        row.extraction_status = "extracted"
        row.extracted_at = datetime.now(UTC)
        db.add(
            AttachmentPage(
                attachment_id=uuid.UUID(att["id"]),
                page_number=1,
                text="the rare unicorn paragraph lives here",
            )
        )
        await db.commit()

    # Default scope (no scope= param) should already include
    # fulltext and pick the item up.
    r = await client.get(
        f"/api/spaces/{slug}/items",
        params={"q": "unicorn"},
    )
    assert r.status_code == 200
    titles = [it["data"]["title"] for it in _items(r.json())]
    assert "Boring metadata" in titles

    # Narrowing to *only* fulltext also works.
    r = await client.get(
        f"/api/spaces/{slug}/items",
        params=[("q", "unicorn"), ("scope", "fulltext")],
    )
    assert [it["id"] for it in _items(r.json())] == [item["id"]]

    # Narrowing to title alone should miss it.
    r = await client.get(
        f"/api/spaces/{slug}/items",
        params=[("q", "unicorn"), ("scope", "title")],
    )
    assert _items(r.json()) == []


async def test_fulltext_hits_returns_per_page_snippets(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The expansion endpoint returns one entry per attachment with
    per-page snippets, ordered by ts_rank, with <mark> tags wrapped
    around the matched terms."""
    async def fake_presign_upload(_key: str, **_: object) -> str:
        return "https://stub"

    monkeypatch.setattr(storage, "presign_upload", fake_presign_upload)

    slug = await _login(client)
    item = (
        await client.post(
            f"/api/spaces/{slug}/items",
            json={"item_type": "document", "data": {"title": "Doc"}},
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
    from shelf.models import AttachmentPage

    async with session_factory() as db:
        db.add_all(
            [
                AttachmentPage(
                    attachment_id=uuid.UUID(att["id"]),
                    page_number=1,
                    text="introductory matter without the keyword",
                ),
                AttachmentPage(
                    attachment_id=uuid.UUID(att["id"]),
                    page_number=7,
                    text="here we discuss the rare Unicorn paragraph in depth",
                ),
                AttachmentPage(
                    attachment_id=uuid.UUID(att["id"]),
                    page_number=12,
                    text="closing notes mention unicorn briefly again",
                ),
            ]
        )
        await db.commit()

    r = await client.get(
        f"/api/items/{item['id']}/fulltext-hits",
        params={"q": "unicorn"},
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    att_entry = body[0]
    assert att_entry["attachment_id"] == att["id"]
    assert att_entry["filename"] == "p.pdf"
    pages = sorted(h["page_number"] for h in att_entry["hits"])
    # Case-insensitive match across both pages.
    assert pages == [7, 12]
    assert any("<mark>" in h["snippet_html"] for h in att_entry["hits"])


async def test_fulltext_hits_substring_matches_inside_word(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Substring search must mirror the reader's Ctrl-F: a query for
    "Test" finds "Latest" and "Testing", not just standalone words.
    Lexeme-based FTS used to miss "Latest"; substring matching is the
    fix."""
    async def fake_presign_upload(_key: str, **_: object) -> str:
        return "https://stub"

    monkeypatch.setattr(storage, "presign_upload", fake_presign_upload)

    slug = await _login(client)
    item = (
        await client.post(
            f"/api/spaces/{slug}/items",
            json={"item_type": "document", "data": {"title": "Doc"}},
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
    from shelf.models import AttachmentPage

    async with session_factory() as db:
        db.add_all(
            [
                AttachmentPage(
                    attachment_id=uuid.UUID(att["id"]),
                    page_number=5,
                    text="Latest issue of the references shall be used",
                ),
                AttachmentPage(
                    attachment_id=uuid.UUID(att["id"]),
                    page_number=7,
                    text="Part III - Chapter 4 Testing, Calibration",
                ),
            ]
        )
        await db.commit()

    r = await client.get(
        f"/api/items/{item['id']}/fulltext-hits",
        params={"q": "Test"},
    )
    assert r.status_code == 200
    pages = sorted(h["page_number"] for h in r.json()[0]["hits"])
    assert pages == [5, 7]

    # The fulltext list-filter must agree — same matcher.
    r = await client.get(
        f"/api/spaces/{slug}/items",
        params=[("q", "Test"), ("scope", "fulltext")],
    )
    assert [it["id"] for it in _items(r.json())] == [item["id"]]


async def test_fulltext_hits_escapes_html_special_chars(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The snippet HTML must escape `<`, `>`, and `&` — only the
    server-emitted <mark> tags are allowed through, so a page text
    that itself contains `<script>` can't be rendered as live HTML."""
    async def fake_presign_upload(_key: str, **_: object) -> str:
        return "https://stub"

    monkeypatch.setattr(storage, "presign_upload", fake_presign_upload)

    slug = await _login(client)
    item = (
        await client.post(
            f"/api/spaces/{slug}/items",
            json={"item_type": "document", "data": {"title": "Doc"}},
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
    from shelf.models import AttachmentPage

    async with session_factory() as db:
        db.add(
            AttachmentPage(
                attachment_id=uuid.UUID(att["id"]),
                page_number=1,
                text="hostile <script>alert(1)</script> KEYWORD trailing",
            )
        )
        await db.commit()

    r = await client.get(
        f"/api/items/{item['id']}/fulltext-hits",
        params={"q": "KEYWORD"},
    )
    assert r.status_code == 200
    snippet = r.json()[0]["hits"][0]["snippet_html"]
    assert "<script>" not in snippet
    assert "&lt;script&gt;" in snippet
    assert "<mark>KEYWORD</mark>" in snippet


async def test_fulltext_hits_other_user_404(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_presign_upload(_key: str, **_: object) -> str:
        return "https://stub"

    monkeypatch.setattr(storage, "presign_upload", fake_presign_upload)

    alice_slug = await _login(client, "alice@example.com")
    item = (
        await client.post(
            f"/api/spaces/{alice_slug}/items",
            json={"item_type": "document", "data": {"title": "Doc"}},
        )
    ).json()

    await client.post("/auth/logout")
    await _login(client, "mallory@example.com")

    r = await client.get(
        f"/api/items/{item['id']}/fulltext-hits",
        params={"q": "unicorn"},
    )
    assert r.status_code == 404
