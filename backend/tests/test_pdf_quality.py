"""Unit + integration tests for Phase A quality assessment."""

from httpx import AsyncClient

from shelf.services.pdf_quality import (
    assess_outline_need,
    assess_text_quality,
    count_pypdf_outline,
)


def test_assess_text_quality_clean_english() -> None:
    text = "Hello world. " * 200  # ~2600 chars across 4 pages = ~650/pg
    q = assess_text_quality(text, page_count=4)
    assert q.text_chars == len(text)
    assert q.page_count == 4
    assert q.chars_per_page > 100
    assert q.alpha_ratio > 0.95
    assert q.replacement_char_ratio == 0.0
    assert q.needs_ocr is False


def test_assess_text_quality_replacement_chars_flag_ocr() -> None:
    # 1.5% replacement characters trips the OCR flag.
    text = "abcde" * 200 + "�" * 20
    q = assess_text_quality(text, page_count=4)
    assert q.replacement_char_ratio > 0.01
    assert q.needs_ocr is True


def test_assess_text_quality_low_density_flags_ocr() -> None:
    # 30 chars over 10 pages = 3 chars/page → looks like a scan
    # without OCR.
    q = assess_text_quality("just a tiny stub", page_count=10)
    assert q.chars_per_page < 100
    assert q.needs_ocr is True


def test_assess_text_quality_garbage_alpha_ratio_flags_ocr() -> None:
    # Control characters / private-use glyphs simulate the broken-CID
    # encoding output we see when a font's ToUnicode map is missing.
    # alpha_ratio drops below the threshold and the heuristic flags
    # the doc for re-OCR.
    text = "\x00\x01\x02\x03\x04\x05" * 500
    q = assess_text_quality(text, page_count=2)
    assert q.alpha_ratio < 0.7
    assert q.needs_ocr is True


def test_assess_text_quality_handles_empty() -> None:
    q = assess_text_quality("", page_count=0)
    assert q.needs_ocr is False
    assert q.alpha_ratio == 0.0


def test_assess_outline_need_short_doc_skipped() -> None:
    # 20-page doc with no outline: too short to bother flagging.
    assert assess_outline_need(toc_entry_count=0, page_count=20) is False


def test_assess_outline_need_long_doc_no_toc() -> None:
    assert assess_outline_need(toc_entry_count=0, page_count=200) is True
    assert assess_outline_need(toc_entry_count=2, page_count=200) is True
    assert assess_outline_need(toc_entry_count=3, page_count=200) is False


def test_count_pypdf_outline_handles_nested_lists() -> None:
    # Mimic pypdf's nested-list shape: leaf items are `Destination`
    # stand-ins, lists nest under their parent. The counter should
    # treat any non-list entry as 1 and recurse into lists.
    class Dest:
        pass

    outline = [Dest(), Dest(), [Dest(), Dest(), [Dest()]], Dest()]
    assert count_pypdf_outline(outline) == 6
    assert count_pypdf_outline(None) == 0
    assert count_pypdf_outline([]) == 0


async def _login(client: AsyncClient) -> str:
    await client.post("/auth/dev-login", json={"email": "alice@example.com"})
    me = (await client.get("/api/me")).json()
    return f"u-{me['id'].replace('-', '')[:8]}"


async def test_processing_endpoint_returns_default_before_extraction(
    client: AsyncClient,
) -> None:
    """Polling the endpoint right after registration (before the
    worker has run) yields a 'never assessed' shape rather than
    404 — keeps the client poll loop simple."""
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
            json={"filename": "p.pdf", "content_type": "application/pdf"},
        )
    ).json()["attachment"]
    r = await client.get(f"/api/attachments/{att['id']}/processing")
    assert r.status_code == 200
    body = r.json()
    assert body["attachment_id"] == att["id"]
    assert body["assessed_at"] is None
    assert body["needs_ocr"] is False
    assert body["needs_outline"] is False
    assert body["ocr_status"] == "untouched"
    assert body["outline_status"] == "untouched"


async def test_processing_endpoint_isolates_users(client: AsyncClient) -> None:
    a_slug = await _login(client)
    a_item = (
        await client.post(
            f"/api/spaces/{a_slug}/items",
            json={"item_type": "document", "data": {"title": "T"}},
        )
    ).json()
    a_att = (
        await client.post(
            f"/api/items/{a_item['id']}/attachments",
            json={"filename": "p.pdf", "content_type": "application/pdf"},
        )
    ).json()["attachment"]
    # Switch to bob — alice's processing row 404s.
    await client.post("/auth/dev-login", json={"email": "bob@example.com"})
    r = await client.get(f"/api/attachments/{a_att['id']}/processing")
    assert r.status_code == 404
