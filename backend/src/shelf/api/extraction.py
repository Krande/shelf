"""Admin endpoints for the PDF body-text extraction pipeline.

Surfaces three things to the SPA's settings page:

* counts of attachments by extraction_status (so the user can see
  "5 PDFs need extraction"),
* a paginated listing filtered by status (so they can scan what's
  pending and what's failed),
* a "rescan" button that re-publishes extraction jobs for any rows
  in the chosen states. Both bulk and per-row variants exist.

All endpoints are scoped to attachments under spaces the caller can
reach — same auth model as the rest of /api/me. Reads span every
readable space; the rescans are writes and so span only writable ones,
since a viewer membership must not let someone queue work in a space
they can only look at.
"""

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import get_current_user
from ..auth.spaces import (
    SPACE_ROLE_EDITOR,
    readable_space_ids,
    require_space_role,
    writable_space_ids,
)
from ..db import get_session
from ..models import Attachment, ExtractionStatus, Item, Space, User
from ..services import queue

router = APIRouter(tags=["extraction"])


PDF_CONTENT_TYPE = "application/pdf"

# States that "rescan" should re-enqueue when the user hits the
# "rescan all" button. `extracted` is excluded so re-running this
# is cheap (idempotent, doesn't re-process happy rows). `null`
# represents pre-feature attachments that were never enqueued.
DEFAULT_RESCAN_STATES = {None, ExtractionStatus.pending.value}


class ExtractionStats(BaseModel):
    extracted: int
    empty: int
    failed: int
    pending: int
    skipped: int
    missing: int  # rows where extraction_status IS NULL (pre-feature)
    total_pdfs: int


class ExtractionAttachment(BaseModel):
    id: uuid.UUID
    item_id: uuid.UUID
    item_title: str | None
    filename: str
    extraction_status: str | None
    text_chars: int | None
    extracted_at: datetime | None
    uploaded_at: datetime | None
    size_bytes: int | None


class RescanRequest(BaseModel):
    """Selector for which rows to re-enqueue.

    `statuses` is the set of `extraction_status` values to include.
    Pass `null` (the JSON literal) inside the list to include rows
    that have never been touched. Default = pending + null.
    """

    statuses: list[str | None] | None = None


class RescanResult(BaseModel):
    selected: int
    enqueued: int


def _scoped_attachments_query() -> Any:
    """Base query joining attachments with the user's owned spaces."""
    return (
        select(Attachment, Item.data["title"].astext.label("item_title"))
        .join(Item, Item.id == Attachment.item_id)
        .join(Space, Space.id == Item.space_id)
    )


def _scope_filter(stmt: Any, user_id: uuid.UUID) -> Any:
    """Restrict a query to attachments in spaces the caller can read."""
    return stmt.where(
        Space.id.in_(readable_space_ids(user_id)),
        Attachment.content_type == PDF_CONTENT_TYPE,
        Attachment.uploaded_at.is_not(None),
    )


@router.get("/api/me/extraction/stats", response_model=ExtractionStats)
async def stats(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> ExtractionStats:
    """Counts of PDF attachments by extraction_status across the
    caller's spaces. Non-PDFs are ignored (they're always 'skipped')
    so the numbers reflect the search-impacting set."""
    s = ExtractionStatus
    bucket = case(
        (Attachment.extraction_status == s.extracted.value, "extracted"),
        (Attachment.extraction_status == s.empty.value, "empty"),
        (Attachment.extraction_status == s.failed.value, "failed"),
        (Attachment.extraction_status == s.pending.value, "pending"),
        (Attachment.extraction_status == s.skipped.value, "skipped"),
        else_="missing",
    )
    stmt = (
        select(bucket.label("bucket"), func.count(Attachment.id))
        .select_from(Attachment)
        .join(Item, Item.id == Attachment.item_id)
        .join(Space, Space.id == Item.space_id)
        .where(
            Space.id.in_(readable_space_ids(user.id)),
            Attachment.content_type == PDF_CONTENT_TYPE,
            Attachment.uploaded_at.is_not(None),
        )
        .group_by("bucket")
    )
    rows = (await db.execute(stmt)).all()
    counts = {row.bucket: int(row[1]) for row in rows}
    extracted = counts.get("extracted", 0)
    empty = counts.get("empty", 0)
    failed = counts.get("failed", 0)
    pending = counts.get("pending", 0)
    skipped = counts.get("skipped", 0)
    missing = counts.get("missing", 0)
    return ExtractionStats(
        extracted=extracted,
        empty=empty,
        failed=failed,
        pending=pending,
        skipped=skipped,
        missing=missing,
        total_pdfs=extracted + empty + failed + pending + skipped + missing,
    )


@router.get(
    "/api/me/extraction/attachments",
    response_model=list[ExtractionAttachment],
)
async def list_attachments(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
    extraction_status: Annotated[str | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ExtractionAttachment]:
    """Paginated list of PDF attachments, optionally filtered by status.

    Pass `status=missing` to surface rows with `extraction_status IS
    NULL` — the wire shape uses a sentinel string rather than a real
    null because Query params can't natively express "is null".
    """
    stmt = _scope_filter(_scoped_attachments_query(), user.id)
    if extraction_status:
        if extraction_status == "missing":
            stmt = stmt.where(Attachment.extraction_status.is_(None))
        else:
            stmt = stmt.where(Attachment.extraction_status == extraction_status)
    stmt = stmt.order_by(Attachment.uploaded_at.desc()).limit(limit).offset(offset)
    rows = (await db.execute(stmt)).all()
    out: list[ExtractionAttachment] = []
    for att, item_title in rows:
        out.append(
            ExtractionAttachment(
                id=att.id,
                item_id=att.item_id,
                item_title=item_title,
                filename=att.filename,
                extraction_status=att.extraction_status,
                text_chars=att.text_chars,
                extracted_at=att.extracted_at,
                uploaded_at=att.uploaded_at,
                size_bytes=att.size_bytes,
            )
        )
    return out


@router.post("/api/me/extraction/rescan", response_model=RescanResult)
async def rescan(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
    payload: RescanRequest | None = None,
) -> RescanResult:
    """Bulk-enqueue extraction jobs for matching attachments.

    Each selected row's status is reset to 'pending' inside the same
    transaction; if the publish round trip fails for some IDs the row
    state is still consistent with "asked the worker to look at this"
    and the next call picks up where this one left off.
    """
    statuses = payload.statuses if payload and payload.statuses else None
    selector = (
        set(statuses) if statuses is not None else DEFAULT_RESCAN_STATES
    )

    stmt = (
        select(Attachment.id)
        .join(Item, Item.id == Attachment.item_id)
        .join(Space, Space.id == Item.space_id)
        .where(
            # Writable, not merely readable: a rescan re-enqueues work
            # against the attachment, so a viewer membership must not
            # sweep the space in.
            Space.id.in_(writable_space_ids(user.id)),
            Attachment.content_type == PDF_CONTENT_TYPE,
            Attachment.uploaded_at.is_not(None),
        )
    )
    if None in selector:
        non_null = [s for s in selector if s is not None]
        if non_null:
            stmt = stmt.where(
                (Attachment.extraction_status.is_(None))
                | (Attachment.extraction_status.in_(non_null))
            )
        else:
            stmt = stmt.where(Attachment.extraction_status.is_(None))
    else:
        stmt = stmt.where(Attachment.extraction_status.in_(selector))

    ids = list((await db.execute(stmt)).scalars().all())
    if not ids:
        return RescanResult(selected=0, enqueued=0)

    # Reset selected rows to pending so the UI immediately reflects
    # "the worker is on this" — the row state is also the durable
    # signal the backfill script reads if NATS is unreachable.
    for aid in ids:
        att = await db.get(Attachment, aid)
        if att is None:
            continue
        att.extraction_status = ExtractionStatus.pending.value
        att.extracted_at = None
    await db.commit()

    enqueued = 0
    for aid in ids:
        if await queue.publish_extract(aid):
            enqueued += 1
    return RescanResult(selected=len(ids), enqueued=enqueued)


@router.post(
    "/api/me/extraction/attachments/{attachment_id}/rescan",
    response_model=RescanResult,
)
async def rescan_one(
    attachment_id: uuid.UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> RescanResult:
    """Re-enqueue a single attachment, regardless of its current
    status. Useful for forcing a re-extract of a 'failed' or
    'extracted' row without touching the rest."""
    att = await db.get(Attachment, attachment_id)
    if att is None or att.uploaded_at is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attachment not found")
    item = await db.get(Item, att.item_id)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attachment not found")
    space = await db.get(Space, item.space_id)
    await require_space_role(
        db, space, user.id, SPACE_ROLE_EDITOR, label="Attachment not found"
    )
    if att.content_type != PDF_CONTENT_TYPE:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Only PDF attachments support extraction",
        )

    att.extraction_status = ExtractionStatus.pending.value
    att.extracted_at = None
    await db.commit()
    enqueued = 1 if await queue.publish_extract(att.id) else 0
    return RescanResult(selected=1, enqueued=enqueued)
