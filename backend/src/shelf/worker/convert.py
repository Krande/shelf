"""Render-to-PDF worker.

Consumes ``shelf.jobs.attachment.convert`` jobs, published when an
upload that ``services.conversion`` recognises completes.

Pipeline:

    fetch the upload → render to PDF → write it as a ``convert``
    derivation next to the upload → publish an extract job, which runs
    against the new PDF and from there triggers OCR / outline exactly as
    it would for a PDF upload.

Two renderers, picked by ``conversion.route_for``:

* office documents go to Gotenberg's LibreOffice route
  (``SHELF_GOTENBERG_URL``). LibreOffice is a few hundred MB and a
  process that likes to hang on hostile input; keeping it in its own
  container keeps it out of this image and lets it be restarted and
  sized on its own.
* images are wrapped by PyMuPDF in-process — no service needed, and the
  frames keep their pixels. They come out with no text layer, which is
  what sends them on to OCR.

The upload itself is never touched: the download button still returns
what was put in, and a re-run writes a fresh derivation beside the old.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import UTC, datetime

import httpx
import obstore

from .. import config
from ..services import conversion, queue, storage
from ._db_client import get_db

log = logging.getLogger("shelf.worker.convert")

# A 300-slide deck through LibreOffice takes a while; past this something
# is wrong with the file, not slow.
CONVERT_TIMEOUT_SECONDS = float(
    os.environ.get("SHELF_CONVERT_TIMEOUT_SECONDS", "300")
)

# How much of a failure's message to keep on the row for the uploader.
_ERROR_CHARS = 2000

_gotenberg_version: str | None = None


async def _engine_label(route: conversion.Route) -> str:
    """Versioned identifier persisted to ``convert_engine`` and the
    derivation row, like the OCR worker's ``ocrmypdf/<ver> shelf/<tag>``."""
    tag = config.settings.image_tag
    if route == "image":
        try:
            import fitz

            ver = getattr(fitz, "VersionBind", "?")
        except Exception:
            ver = "?"
        return f"pymupdf/{ver} shelf/{tag}"
    global _gotenberg_version
    if _gotenberg_version is None:
        try:
            async with httpx.AsyncClient(timeout=5.0) as c:
                r = await c.get(f"{_gotenberg_base()}/version")
            r.raise_for_status()
            _gotenberg_version = r.text.strip() or "?"
        except Exception:
            # Not cached: the next job asks again once Gotenberg answers.
            return f"gotenberg/? shelf/{tag}"
    return f"gotenberg/{_gotenberg_version} shelf/{tag}"


def _gotenberg_base() -> str:
    return config.settings.gotenberg_url.rstrip("/")


async def convert_attachment(attachment_id: str | uuid.UUID) -> None:
    """Render one attachment to PDF and chain into extraction.

    Caller is the worker loop; exceptions propagate so the loop can
    decide between nak-with-retry and ack-with-permanent-fail.
    """
    aid = uuid.UUID(str(attachment_id))
    db = get_db()
    att = await db.get_attachment(aid)
    # Raise rather than skip on both: the job is published inside the
    # request that creates or completes the row, before its commit, so a
    # fast worker can get here first. The redelivery a raise buys is
    # what lets it see the committed row.
    if att is None:
        raise LookupError(f"attachment {aid} not found")
    if att.uploaded_at is None:
        raise LookupError(f"attachment {aid} not marked uploaded yet")

    route = conversion.route_for(att.filename, att.content_type)
    if route is None:
        log.warning(
            "attachment %s (%s, %s) is not convertible; skipping",
            aid,
            att.filename,
            att.content_type,
        )
        return

    engine = await _engine_label(route)
    await db.upsert_processing(
        aid,
        {"convert_status": "running", "convert_engine": engine},
        clear=["convert_error"],
    )
    try:
        body = await _fetch_body(att.storage_key)
        if route == "image":
            ext = conversion.extension(
                att.filename
            ) or conversion.extension_for_content_type(att.content_type)
            pdf = await asyncio.to_thread(_image_to_pdf, body, ext or "")
        else:
            pdf = await _office_to_pdf(body, att.filename, att.content_type)
        if not pdf.startswith(b"%PDF"):
            raise RuntimeError("renderer returned something that isn't a PDF")
    except Exception as e:
        # Kept on the row so the uploader sees why; the status stays
        # `running` until the redelivery cap turns it `failed`.
        await db.upsert_processing(
            aid, {"convert_error": f"{type(e).__name__}: {e}"[:_ERROR_CHARS]}
        )
        raise

    new_key = storage.derived_key(att.storage_key, conversion.CONVERT_KIND)
    await _put_body(new_key, pdf)
    await db.insert_derivation(
        aid,
        kind=conversion.CONVERT_KIND,
        storage_key=new_key,
        parent_storage_key=att.storage_key,
        engine=engine,
    )
    await db.upsert_processing(
        aid,
        {
            "convert_status": "done",
            "convert_engine": engine,
            "convert_completed_at": datetime.now(UTC),
        },
        clear=["convert_error"],
    )
    await queue.publish_extract(aid)
    log.info(
        "converted %s (%s, %d → %d bytes) via %s; extract enqueued",
        aid,
        att.filename,
        len(body),
        len(pdf),
        engine,
    )


async def mark_failed_terminal(attachment_id: str | uuid.UUID) -> None:
    """Set ``convert_status='failed'`` after JetStream's redelivery cap
    is reached, and stop the row looking like extraction is still on
    its way. Best-effort — failures here just log."""
    aid = uuid.UUID(str(attachment_id))
    try:
        await get_db().upsert_processing(aid, {"convert_status": "failed"})
    except Exception:
        log.exception("convert mark_failed_terminal failed for %s", aid)
        return
    try:
        from ..db import session_factory
        from ..models import Attachment, ExtractionStatus

        async with session_factory() as s:
            row = await s.get(Attachment, aid)
            if row is not None:
                row.extraction_status = ExtractionStatus.skipped.value
                row.extracted_at = datetime.now(UTC)
                await s.commit()
    except Exception:
        log.exception("convert: resetting extraction_status failed for %s", aid)


async def _office_to_pdf(
    body: bytes, filename: str, content_type: str | None
) -> bytes:
    """POST the file to Gotenberg's LibreOffice route.

    LibreOffice picks its import filter from the file name, so the part
    is sent under a neutral name with the right extension rather than
    the uploader's (which may have no extension, or characters Gotenberg
    rejects in a multipart filename).
    """
    ext = conversion.extension(filename)
    if not ext:
        ext = conversion.extension_for_content_type(content_type) or ""
    files = {"files": (f"document{ext}", body, "application/octet-stream")}
    async with httpx.AsyncClient(timeout=CONVERT_TIMEOUT_SECONDS) as c:
        r = await c.post(
            f"{_gotenberg_base()}/forms/libreoffice/convert", files=files
        )
    if r.status_code != 200:
        raise RuntimeError(
            f"gotenberg {r.status_code}: {r.text.strip()[:500]}"
        )
    return r.content


def _image_to_pdf(body: bytes, ext: str) -> bytes:
    """One page per frame (a multi-page TIFF stays multi-page), each
    page the image's own size. PyMuPDF embeds the pixels rather than
    re-encoding a JPEG, so nothing is lost on the way to OCR."""
    import fitz

    src = fitz.open(stream=body, filetype=ext.lstrip(".") or None)
    try:
        return bytes(src.convert_to_pdf())
    finally:
        src.close()


async def _fetch_body(storage_key: str) -> bytes:
    res = await obstore.get_async(storage.get_store(), storage_key)
    buf = await res.bytes_async()
    return bytes(buf)


async def _put_body(storage_key: str, body: bytes) -> None:
    await obstore.put_async(storage.get_store(), storage_key, body)
