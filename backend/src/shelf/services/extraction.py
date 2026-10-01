"""Helpers tying attachment lifecycle into the extraction pipeline.

Sits between the API and the queue: callers hand in an Attachment
that's just been uploaded, this module decides what the worker should
do with it and dispatches the job. A PDF goes straight to extraction;
anything ``services.conversion`` can render goes to the convert worker
first, which chains into extraction once the PDF exists. The row's
`extraction_status` is the durable trigger — if NATS isn't reachable
the worker can still pick the row up later from the database.
"""

from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Attachment, AttachmentProcessing, ExtractionStatus
from . import conversion, queue

PDF_CONTENT_TYPE = conversion.PDF_CONTENT_TYPE


async def mark_and_enqueue(db: AsyncSession, attachment: Attachment) -> None:
    """Set the row's extraction_status and publish a job when it
    makes sense.

    The caller is responsible for the surrounding `await db.commit()`.
    Doing the row mutation + publish as one helper keeps both code
    paths (SPA cookie auth + token auth bulk import) in lock-step
    so a future status value can't drift between them.
    """
    if conversion.is_pdf(attachment.content_type, attachment.filename):
        attachment.extraction_status = ExtractionStatus.pending.value
        await queue.publish_extract(attachment.id)
    elif conversion.is_convertible(attachment):
        # Pending, not skipped: there will be text to extract once the
        # convert worker has produced the PDF and chained the job.
        attachment.extraction_status = ExtractionStatus.pending.value
        await mark_convert_queued(db, attachment)
        await queue.publish_convert(attachment.id)
    else:
        attachment.extraction_status = ExtractionStatus.skipped.value


async def mark_convert_queued(db: AsyncSession, attachment: Attachment) -> None:
    """UPSERT ``convert_status='queued'``, clearing a previous run's error."""
    # Flushed first so the processing row's FK has an attachment to point
    # at when the caller added the attachment in this same session.
    await db.flush()
    now = datetime.now(UTC)
    await db.execute(
        pg_insert(AttachmentProcessing)
        .values(
            attachment_id=attachment.id,
            convert_status="queued",
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_update(
            index_elements=[AttachmentProcessing.attachment_id],
            set_={
                "convert_status": "queued",
                "convert_error": None,
                "updated_at": now,
            },
        )
    )
