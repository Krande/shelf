"""Enqueue extraction jobs for PDF attachments missing text.

Run as ``python -m shelf.scripts.backfill_extract`` from inside a pod
that has SHELF_DATABASE_URL and SHELF_NATS_URL set. Used in two
spots:

1. After the first deploy of the extraction pipeline — every existing
   attachment has ``extraction_status = NULL`` and is invisible to
   the worker until something publishes a job for it.
2. Recovery — if NATS was down for an upload, the row stays
   ``pending`` with no message in the queue. Re-running this script
   re-publishes any row that hasn't reached a terminal state.

The script is idempotent: a row that was already enqueued just gets
another message, and JetStream's WORK_QUEUE retention drops the
duplicate once the worker acks the first one.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select

from ..db import session_factory
from ..models import Attachment, ExtractionStatus
from ..services import queue

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
    async with session_factory() as db:
        rows = (
            await db.execute(
                select(Attachment.id).where(
                    Attachment.content_type == "application/pdf",
                    Attachment.uploaded_at.is_not(None),
                    (
                        Attachment.extraction_status.is_(None)
                        | (Attachment.extraction_status == ExtractionStatus.pending.value)
                    ),
                )
            )
        ).scalars().all()

    log.info("found %d attachment(s) needing extraction", len(rows))
    sent = 0
    for aid in rows:
        ok = await queue.publish_extract(aid)
        if ok:
            sent += 1
        else:
            log.warning("publish failed for %s", aid)
    await queue.close()
    log.info("published %d/%d", sent, len(rows))


if __name__ == "__main__":
    asyncio.run(main())
