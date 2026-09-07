"""NATS JetStream publisher for background jobs.

The API uses this to enqueue work the worker pod consumes. Today
that's just `extract_text` over PDF attachments; the same module is
the natural home for any future job kind (OCR re-run, thumbnail,
cite-key reindex…).

Design notes:

- One JetStream stream `shelf-jobs` covers all subjects under
  `shelf.jobs.>`. WorkQueue retention so a single consumer drains
  each message exactly once and JetStream auto-purges acked work.
- A single shared connection is held in module state and re-used
  across requests. We don't pool — `nats-py` already multiplexes
  publishes over the one TCP connection.
- When `settings.nats_url` is empty, every publish is a no-op. This
  is what dev (no compose service) and tests (no NATS fixture) rely
  on — the API stays usable without the queue, the row stays
  `pending`, and the backfill script catches up later.
- Failures to publish are logged and swallowed at the call site so
  upload completion is never blocked by queue-side trouble; durable
  state lives in the database column.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import TYPE_CHECKING

from .. import config

if TYPE_CHECKING:
    from nats.aio.client import Client as NatsClient
    from nats.js import JetStreamContext

log = logging.getLogger(__name__)

STREAM_NAME = "shelf-jobs"
STREAM_SUBJECTS = ["shelf.jobs.>"]
SUBJECT_EXTRACT = "shelf.jobs.attachment.extract"
SUBJECT_OCR = "shelf.jobs.attachment.ocr"
SUBJECT_OCR_GPU = "shelf.jobs.attachment.ocr.gpu"
SUBJECT_OUTLINE = "shelf.jobs.attachment.outline"

_nc: NatsClient | None = None
_js: JetStreamContext | None = None


async def _connect() -> JetStreamContext | None:
    """Lazy-connect the shared NATS client + JetStream context.

    Returns None when no URL is configured — callers must treat that
    as "don't publish, no-op." Keeping the import inside the function
    means modules that never reach this path (most of the API) don't
    pay the nats-py import cost.
    """
    global _nc, _js
    if _js is not None:
        return _js
    url = config.settings.nats_url
    if not url:
        return None
    import nats
    from nats.js.api import RetentionPolicy, StreamConfig

    _nc = await nats.connect(url)
    _js = _nc.jetstream()
    # Idempotent stream assertion — first publisher to start wins,
    # everyone else lands on the existing stream untouched.
    try:
        await _js.add_stream(
            StreamConfig(
                name=STREAM_NAME,
                subjects=STREAM_SUBJECTS,
                retention=RetentionPolicy.WORK_QUEUE,
            )
        )
    except Exception as e:
        log.debug("stream-assert noop (likely already exists): %s", e)
    return _js


async def publish_extract(attachment_id: uuid.UUID) -> bool:
    """Enqueue a body-text extraction job for `attachment_id`.

    Returns True on a successful publish, False on no-op (no URL) or
    failure. Caller is expected not to gate user-visible work on the
    return value — the row's `extraction_status='pending'` is the
    durable signal; this just wakes the worker.
    """
    return await _publish(SUBJECT_EXTRACT, attachment_id)


async def publish_ocr(attachment_id: uuid.UUID) -> bool:
    """Enqueue an OCR (Phase B) job for `attachment_id`. Caller has
    already set `attachment_processing.ocr_status='queued'` so the
    row is the durable signal even if NATS is down."""
    return await _publish(SUBJECT_OCR, attachment_id)


async def publish_ocr_gpu(attachment_id: uuid.UUID) -> bool:
    """Enqueue a GPU-tier OCR job (olmOCR / Qwen2.5-VL-7B). Same
    durability story as ``publish_ocr``: caller has set
    ``ocr_status='queued'`` so even if NATS is down the row is the
    truth and any worker that reconnects drains the backlog."""
    return await _publish(SUBJECT_OCR_GPU, attachment_id)


async def publish_outline(attachment_id: uuid.UUID) -> bool:
    """Enqueue an outline-generation (Phase C) job. The handler is
    the GPU worker pod (Marker); if it isn't running the row's
    `outline_status='queued'` is the durable signal — JetStream
    holds the message and the next time the consumer subscribes
    it'll drain the backlog."""
    return await _publish(SUBJECT_OUTLINE, attachment_id)


async def _publish(subject: str, attachment_id: uuid.UUID) -> bool:
    js = await _connect()
    if js is None:
        return False
    payload = json.dumps({"attachment_id": str(attachment_id)}).encode("utf-8")
    try:
        await js.publish(subject, payload)
        return True
    except Exception:
        log.exception("publish %s failed for %s", subject, attachment_id)
        return False


async def close() -> None:
    """Tear down the shared connection on app shutdown."""
    global _nc, _js
    if _nc is not None:
        try:
            await _nc.drain()
        except Exception:
            log.exception("nats drain failed")
    _nc = None
    _js = None
