"""Helpers tying attachment lifecycle into the extraction pipeline.

Sits between the API and the queue: callers hand in an Attachment
that's just been uploaded, this module decides whether it's worth
extracting (PDFs only, for now) and dispatches the job. The row's
`extraction_status` is the durable trigger — if NATS isn't reachable
the worker can still pick the row up later from the database.
"""

from ..models import Attachment, ExtractionStatus
from . import queue

PDF_CONTENT_TYPE = "application/pdf"


async def mark_and_enqueue(attachment: Attachment) -> None:
    """Set the row's extraction_status and publish a job when it
    makes sense.

    The caller is responsible for the surrounding `await db.commit()`.
    Doing the row mutation + publish as one helper keeps both code
    paths (SPA cookie auth + token auth bulk import) in lock-step
    so a future status value can't drift between them.
    """
    if attachment.content_type == PDF_CONTENT_TYPE:
        attachment.extraction_status = ExtractionStatus.pending.value
        await queue.publish_extract(attachment.id)
    else:
        attachment.extraction_status = ExtractionStatus.skipped.value
