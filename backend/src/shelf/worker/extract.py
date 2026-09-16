"""PDF body-text extraction.

Runs against an attachment row that was just uploaded: fetches the
object body via obstore, extracts plain text with pypdf, and writes
the result back onto the attachment row. The Postgres-side
``tsv`` GENERATED column picks up the new ``text_content`` on commit
so search is live as soon as the transaction commits.

Heuristics:

- ``EMPTY_THRESHOLD_CHARS`` decides "looks blank → probably scanned →
  needs OCR." Set conservatively low; we'd rather have a few false
  positives that the OCR pipeline re-extracts cleanly than miss a
  scanned PDF that hides under sparse text.
- Errors during parsing → status = ``failed``. The worker bubbles the
  exception so JetStream can retry up to its cap; only after the cap
  do we permanently mark the row failed (see ``mark_failed_terminal``).
"""

from __future__ import annotations

import hashlib
import io
import logging
import uuid
from datetime import UTC, datetime

import obstore
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ..db import session_factory
from ..models import (
    Attachment,
    AttachmentDerivation,
    AttachmentPage,
    AttachmentProcessing,
    ExtractionStatus,
)
from ..services import queue, storage
from ..services.pdf_quality import (
    assess_outline_need,
    assess_text_quality,
    count_pypdf_outline,
)

log = logging.getLogger("shelf.worker.extract")

# Below this many extracted characters we treat the PDF as "no
# usable text" — typical of image-only scans. The number isn't load-
# bearing; it's the boundary for the "needs OCR" listing.
EMPTY_THRESHOLD_CHARS = 50


async def extract_attachment(attachment_id: str | uuid.UUID) -> None:
    """Run extraction for one attachment and commit the result.

    Caller is the worker loop; exceptions propagate so the loop can
    decide between nak-with-retry and ack-with-permanent-fail.
    """
    aid = uuid.UUID(str(attachment_id))
    async with session_factory() as db:
        att = await db.get(Attachment, aid)
        if att is None:
            log.warning("attachment %s not found; skipping", aid)
            return
        if att.uploaded_at is None:
            log.warning("attachment %s not marked uploaded; skipping", aid)
            return
        if att.content_type != "application/pdf":
            att.extraction_status = ExtractionStatus.skipped.value
            att.extracted_at = datetime.now(UTC)
            await db.commit()
            return

        # Extract from the most recent OCR'd derivation when one
        # exists — that's the searchable text the SPA actually shows
        # by default. Falls back to the original PDF for fresh
        # uploads (no OCR yet) and for non-OCR'd attachments.
        latest_ocr = (
            await db.execute(
                select(AttachmentDerivation.storage_key)
                .where(
                    AttachmentDerivation.attachment_id == aid,
                    AttachmentDerivation.kind == "ocr",
                )
                .order_by(AttachmentDerivation.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        source_key = latest_ocr or att.storage_key
        body = await _fetch_body(source_key)

        # Backfill the content hash while the bytes are in hand — the
        # presigned upload paths never see them, so this is the only
        # place the server can compute one for its own account.
        #
        # Only when it's absent, and only from the *original* blob: the
        # hash is defined as the bytes as uploaded, and `source_key` is
        # the OCR'd derivation whenever one exists. Hashing that would
        # quietly redefine the column the first time OCR ran.
        if att.sha256 is None:
            original = (
                body
                if latest_ocr is None
                else await _fetch_body(att.storage_key)
            )
            att.sha256 = hashlib.sha256(original).hexdigest()

        pages, toc_count = _extract_pages_and_toc(body)
        # `text_content` stays as the joined text — the search EXISTS
        # path against per-page rows is the live one, but the
        # extraction-status admin still reports `text_chars` and a few
        # downstream consumers (BibTeX export, future OCR sentinel)
        # read the concatenation directly.
        text = "\n".join(p[1] for p in pages if p[1]).strip()
        chars = len(text)

        # Replace any previous per-page rows for this attachment so a
        # rescan converges rather than appending. The cascade is the
        # delete path on attachment removal; this delete is the rescan
        # path. We now insert one row per page even if the page has
        # no extractable text, so the reader can fetch dims for every
        # page upfront — empty pages just don't contribute to FTS.
        await db.execute(
            delete(AttachmentPage).where(AttachmentPage.attachment_id == aid)
        )
        for page_number, page_text, width_pts, height_pts in pages:
            db.add(
                AttachmentPage(
                    attachment_id=aid,
                    page_number=page_number,
                    text=page_text,
                    width_pts=width_pts,
                    height_pts=height_pts,
                )
            )

        att.text_content = text or None
        att.text_chars = chars
        att.extracted_at = datetime.now(UTC)
        att.extraction_status = (
            ExtractionStatus.extracted.value
            if chars >= EMPTY_THRESHOLD_CHARS
            else ExtractionStatus.empty.value
        )

        # Phase A: persist quality assessment + downstream-worker
        # flags. UPSERT so a rescan replaces the previous assessment
        # without us having to load + edit the row.
        page_count = len(pages)
        quality = assess_text_quality(text, page_count)
        needs_outline = assess_outline_need(toc_count, page_count)
        now = datetime.now(UTC)

        # Phase B trigger: only auto-enqueue OCR when the row hasn't
        # already been processed. ``done`` / ``running`` / ``failed``
        # / ``cancelled`` all short-circuit so a re-extract doesn't
        # loop, doesn't undo a deliberate cancel, and doesn't
        # automatically retry a terminal failure — manual triggers
        # (and the explicit Cancel/Restore actions in the SPA) are
        # the user's escape hatches.
        existing = await db.get(AttachmentProcessing, aid)
        prior_ocr_status = existing.ocr_status if existing else "untouched"
        should_enqueue_ocr = quality.needs_ocr and prior_ocr_status in (
            "untouched",
            "queued",
        )
        new_ocr_status = "queued" if should_enqueue_ocr else prior_ocr_status

        # Phase C trigger: same shape as OCR.
        prior_outline_status = (
            existing.outline_status if existing else "untouched"
        )
        should_enqueue_outline = needs_outline and prior_outline_status in (
            "untouched",
            "queued",
        )
        new_outline_status = (
            "queued" if should_enqueue_outline else prior_outline_status
        )

        await db.execute(
            pg_insert(AttachmentProcessing)
            .values(
                attachment_id=aid,
                assessed_at=now,
                page_count=page_count,
                text_chars=quality.text_chars,
                replacement_char_ratio=quality.replacement_char_ratio,
                alpha_ratio=quality.alpha_ratio,
                chars_per_page=quality.chars_per_page,
                toc_entry_count=toc_count,
                needs_ocr=quality.needs_ocr,
                needs_outline=needs_outline,
                ocr_status=new_ocr_status,
                outline_status=new_outline_status,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_update(
                index_elements=[AttachmentProcessing.attachment_id],
                set_={
                    "assessed_at": now,
                    "page_count": page_count,
                    "text_chars": quality.text_chars,
                    "replacement_char_ratio": quality.replacement_char_ratio,
                    "alpha_ratio": quality.alpha_ratio,
                    "chars_per_page": quality.chars_per_page,
                    "toc_entry_count": toc_count,
                    "needs_ocr": quality.needs_ocr,
                    "needs_outline": needs_outline,
                    "ocr_status": new_ocr_status,
                    "outline_status": new_outline_status,
                    "updated_at": now,
                },
            )
        )

        await db.commit()
        if should_enqueue_ocr:
            await queue.publish_ocr(aid)
        if should_enqueue_outline:
            await queue.publish_outline(aid)
        log.info(
            "extracted %s status=%s chars=%d pages=%d toc=%d "
            "needs_ocr=%s needs_outline=%s alpha=%.2f repl=%.4f",
            aid,
            att.extraction_status,
            chars,
            sum(1 for p in pages if p[1]),
            toc_count,
            quality.needs_ocr,
            needs_outline,
            quality.alpha_ratio,
            quality.replacement_char_ratio,
        )


async def mark_failed_terminal(attachment_id: str | uuid.UUID) -> None:
    """Set ``extraction_status='failed'`` for a poison-pill row.

    Called after JetStream's redelivery cap is reached so the row
    stops looking pending. Best-effort; failures here just log.
    """
    aid = uuid.UUID(str(attachment_id))
    async with session_factory() as db:
        att = await db.get(Attachment, aid)
        if att is None:
            return
        att.extraction_status = ExtractionStatus.failed.value
        att.extracted_at = datetime.now(UTC)
        await db.commit()


async def _fetch_body(storage_key: str) -> bytes:
    res = await obstore.get_async(storage.get_store(), storage_key)
    buf = await res.bytes_async()
    return bytes(buf)


def _extract_pages_and_toc(
    body: bytes,
) -> tuple[list[tuple[int, str, float | None, float | None]], int]:
    """Return ``([(page_number, text, width_pts, height_pts), …], toc_entry_count)``.

    Page numbers are 1-based to match the reader's URL `?page=N`.
    Width/height are PDF user-space points (1 pt = 1/72 inch) at
    scale=1 — what the reader needs to seed its virtualizer height
    map without opening every page client-side. ``None`` only when
    the page mediabox is unreadable, which we keep separate from
    "page exists but empty text" so the API can still report a total
    page count.

    The TOC entry count is a flat total of all bookmarks at any
    nesting level. Used by Phase A's ``needs_outline`` flag.

    Pure-python via pypdf — no system deps. Doesn't OCR scans;
    the worker writes ``status='empty'`` for those and the OCR
    pipeline consumes the empty rows to re-extract.
    """
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(body), strict=False)
    except PdfReadError as e:
        raise RuntimeError(f"pdf parse error: {e}") from e

    out: list[tuple[int, str, float | None, float | None]] = []
    for idx, page in enumerate(reader.pages, start=1):
        try:
            t = page.extract_text() or ""
        except Exception:
            log.exception("page %d extract failed", idx)
            t = ""
        # Postgres TEXT can't store NUL bytes — pypdf occasionally
        # emits one for control sequences in odd PDFs. Strip rather
        # than fail the whole row.
        if "\x00" in t:
            t = t.replace("\x00", "")
        try:
            mb = page.mediabox
            width = float(mb.width)
            height = float(mb.height)
        except Exception:
            log.exception("page %d mediabox read failed", idx)
            width = None
            height = None
        out.append((idx, t.strip(), width, height))

    try:
        toc_count = count_pypdf_outline(reader.outline)
    except Exception:
        log.exception("outline read failed")
        toc_count = 0
    return out, toc_count
