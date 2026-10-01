"""Enqueue extraction jobs for PDF attachments missing text, and
conversion jobs for uploads shelf renders to PDF but hasn't yet.

Run as ``python -m shelf.scripts.backfill_extract`` from inside a pod
that has SHELF_DATABASE_URL and SHELF_NATS_URL set. Used in two
spots:

1. After the first deploy of the extraction pipeline — every existing
   attachment has ``extraction_status = NULL`` and is invisible to
   the worker until something publishes a job for it.
2. Recovery — if NATS was down for an upload, the row stays
   ``pending`` with no message in the queue. Re-running this script
   re-publishes any row that hasn't reached a terminal state.

3. After the first deploy of the convert worker — Word, PowerPoint and
   image uploads from before it were marked ``skipped`` and never
   rendered. Any convertible upload with no rendering and no failed
   attempt is queued for conversion, which chains into extraction.

The script is idempotent: a row that was already enqueued just gets
another message, and JetStream's WORK_QUEUE retention drops the
duplicate once the worker acks the first one.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select

from ..db import session_factory
from ..models import Attachment, AttachmentProcessing, ExtractionStatus
from ..services import conversion, extraction, queue

log = logging.getLogger("shelf.backfill_extract")


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # We touch publish_extract directly rather than mark_and_enqueue —
    # we don't want to overwrite a worker-set status (e.g. `failed`)
    # for a row that has already been processed once. Selecting only
    # the "needs work" rows here is the durable filter.
    #
    # Not limited to PDFs: a converted upload whose extract message was
    # lost is pending too, and the worker reads its rendering. One that
    # isn't rendered yet is skipped by the worker and queued for
    # conversion below.
    async with session_factory() as db:
        rows = (
            await db.execute(
                select(Attachment.id).where(
                    Attachment.uploaded_at.is_not(None),
                    (
                        Attachment.extraction_status.is_(None)
                        | (Attachment.extraction_status == ExtractionStatus.pending.value)
                    ),
                )
            )
        ).scalars().all()

        # Convertible uploads: filtered in Python because "convertible"
        # is an extension table, not a column. Only non-PDF rows that
        # have neither a rendering nor a failed run.
        candidates = (
            await db.execute(
                select(Attachment)
                .outerjoin(
                    AttachmentProcessing,
                    AttachmentProcessing.attachment_id == Attachment.id,
                )
                .where(
                    Attachment.content_type != "application/pdf",
                    Attachment.uploaded_at.is_not(None),
                    AttachmentProcessing.convert_status.is_(None)
                    | AttachmentProcessing.convert_status.in_(
                        ("untouched", "queued")
                    ),
                )
            )
        ).scalars().all()
        convertible = [a for a in candidates if conversion.is_convertible(a)]
        rendered = await conversion.latest_convert_keys(
            db, [a.id for a in convertible]
        )
        to_convert = [a for a in convertible if a.id not in rendered]
        for att in to_convert:
            att.extraction_status = ExtractionStatus.pending.value
            await extraction.mark_convert_queued(db, att)
        await db.commit()
        convert_ids = [a.id for a in to_convert]

    log.info(
        "found %d attachment(s) needing extraction, %d needing conversion",
        len(rows),
        len(convert_ids),
    )
    sent = 0
    for aid in rows:
        ok = await queue.publish_extract(aid)
        if ok:
            sent += 1
        else:
            log.warning("publish failed for %s", aid)
    for aid in convert_ids:
        if await queue.publish_convert(aid):
            sent += 1
        else:
            log.warning("convert publish failed for %s", aid)
    await queue.close()
    log.info("published %d/%d", sent, len(rows) + len(convert_ids))


if __name__ == "__main__":
    asyncio.run(main())
